"""Candidate profile service: one consistent way to read and write a candidate's structured profile.

* Every operation takes a trusted :class:`OwnerContext` and runs in an owner session (see
  repository.owner_session), so the database enforces ownership and column privileges.
* Content operations accept only candidate-authored content. Provenance and verification are never
  accepted from a payload; candidate-authored records get the honest database defaults:
  information_origin = 'user_stated', review_status = 'pending_review',
  verification_status = 'unverified', no extraction confidence, no source references.
* Review status changes only through the explicit actions confirm_record, reject_record and
  reinstate_record, which call trace_review.review_record (the M1 matrix, mirrored in review.py).
  There is no free-form status setter and no "unconfirm".
* Editing is allowed only for the candidate's own unreviewed draft (user_stated, pending_review) and
  never changes its review status. Corrections of reviewed, extracted, inferred or legacy records are
  refused (CorrectionNotAvailable) until structured correction history exists.
* Nothing here can set verification, origin, source references, role or category mapping, or
  profile confirmation.
* Employment history is the only input to career sequencing; supporting records never are. Rejected
  employment is removed before sequencing; pending employment stays in the chain as a barrier.
* Since data foundation v2.3.0 (M2) the database enforces all of the above for every role except the
  table owner: column grants, guard triggers and the trace_review functions. The checks here run
  first and give clearer errors; the database checks are authoritative.
"""
import re
from typing import TYPE_CHECKING, Dict, List, Optional
from uuid import UUID, uuid4

import psycopg
from pydantic import BaseModel

from app.services.career_history import (
    CandidateEmployment,
    CandidateHistory,
    PartialDateInput,
    SequencingContext,
    sequence_candidate_history,
)
from app.services.career_history import dates
from app.services.career_history.models import PartialDateView
from app.services.candidate_profile.models import (
    ACTIVITY_TYPES,
    CORRECTION_MODELS,
    ActivityCreate,
    ActivityRecord,
    ActivityUpdate,
    CandidateProfile,
    CareerBreakCreate,
    CareerBreakRecord,
    CareerBreakUpdate,
    CorrectionEvent,
    DateInput,
    EducationCreate,
    EducationRecord,
    EducationUpdate,
    EmployerCreate,
    EmployerRecord,
    EmployerUpdate,
    EmploymentCreate,
    EmploymentRecord,
    EmploymentUpdate,
    EvidenceCreate,
    EvidenceRecord,
    EvidenceUpdate,
    OwnerContext,
    ProfileCreate,
    ProfileUpdate,
    Provenance,
    CurrentPosition,
    CurrentPositionEvidence,
    RoleChoice,
    RoleMappingOptions,
    RoleMappingResult,
    SkillCreate,
    SkillRecord,
    SkillRef,
    SkillTaxonomy,
    SkillUpdate,
)
from app.services.candidate_profile.repository import (
    PROVENANCE_COLS,
    TABLES,
    CandidateProfileError,
    ClarificationRequired,
    InvalidRecord,
    InvalidReference,
    ConsentConflict,
    ProfileAlreadyExists,
    ProfileAmbiguous,
    ProfileNotFound,
    RecordChanged,
    RecordNotEditable,
    TargetIsCurrentRole,
    owner_session,
)
from app.services.candidate_profile.entries import EntriesMixin
from app.services.candidate_profile.review import (
    ReviewAction,
    check_correctable,
    check_editable,
    check_employer_editable,
)

from app.services.reference_evidence import recommendation_evidence, reference_snapshot
from app.services.role_mapping import AliasIndex, PostgresAliasRepository, TitleMatch, is_product_owner_title, match_title
from app.services.role_mapping.matcher import suggest_role

if TYPE_CHECKING:                                   # imported for typing only (avoids an import cycle)
    from app.services.candidate_profile.pathway import PathwayResult
    from app.services.resume_extraction.contract import ExtractionResult

DATE_FIELDS = ("start", "end", "expiry", "resume_as_of")
RESPONSIBILITY_TYPES = ("responsibility", "scope", "leadership")
OTHER_EVIDENCE_TYPES = ("skill_evidence", "other")


# --------------------------------------------------------------------------- input -> columns
def content_columns(kind: str, payload: BaseModel, *, creating: bool) -> dict:
    """Map a content model to table columns. Only fields the caller set are written on update.
    Partial dates expand to their year/month/precision columns; nothing else is added. Legacy trust
    columns (entry_source, provenance, evidence_source, attribute_provenance) are never written: the
    database records origin itself and leaves the legacy labels NULL for candidate-authored rows."""
    fields = payload.model_fields_set if not creating else set(type(payload).model_fields)
    values: dict = {}
    for name in fields:
        value = getattr(payload, name)
        if name in DATE_FIELDS:
            d: DateInput = value
            prefix = name
            values.update({f"{prefix}_year": d.year, f"{prefix}_month": d.month, f"{prefix}_precision": d.precision})
            if kind == "employment" and name == "end":
                values["is_current_as_stated"] = d.precision == "ongoing"
        else:
            values[name] = value
    if not values:
        raise InvalidRecord("nothing to update")
    return values


# --------------------------------------------------------------------------- rows -> profile
def _date(row: dict, prefix: str) -> PartialDateView:
    y, m, p = row[f"{prefix}_year"], row[f"{prefix}_month"], row[f"{prefix}_precision"]
    lo, hi = dates.bounds(y, m, p)
    return PartialDateView(year=y, month=m, precision=p, lo=lo, hi=hi,
                           lo_label=dates.month_label(lo), hi_label=dates.month_label(hi))


def _prov(row: dict) -> Provenance:
    return Provenance(**{c: row[c] for c in PROVENANCE_COLS})


def check_version(current, expected) -> None:
    """Optimistic concurrency. ``expected`` is the version the caller read (None: no check, for callers that predate
    versions). Compared AFTER the row lock is held, so a concurrent change either committed before (mismatch,
    RecordChanged) or waits for this transaction."""
    if expected is None:
        return
    if current is None or current != expected:
        raise RecordChanged("this record changed since you loaded it; reload and try again")


def assemble_profile(rows: Dict[str, List[dict]]) -> CandidateProfile:
    """Pure function: database rows -> CandidateProfile. Every category is present; nothing is
    filled in; original text, unresolved mappings and provenance are carried through unchanged."""
    p = rows["profile"][0]
    skill_refs: Dict[tuple, List[SkillRef]] = {}
    skills: List[SkillRecord] = []
    for r in rows["skills"]:
        link = next(((kind, r[col]) for kind, col in (("employment", "candidate_employment_id"),
                                                      ("education", "candidate_education_id"),
                                                      ("activity", "candidate_activity_id"),
                                                      ("evidence", "candidate_evidence_id")) if r[col] is not None),
                    (None, None))
        taxonomy = (SkillTaxonomy(skill_id=r["skill_id"], canonical_skill=r["canonical_skill"], skill_family=r["skill_family"])
                    if r["skill_id"] else None)
        skills.append(SkillRecord(candidate_skill_id=r["candidate_skill_id"], skill_text_raw=r["skill_text_raw"],
                                  taxonomy=taxonomy, stated_proficiency_raw=r["stated_proficiency_raw"],
                                  evidence_source=r["evidence_source"], supported_by=link[0], supported_by_id=link[1],
                                  provenance=_prov(r), version=r.get("updated_at")))
        if link[0]:
            name = r["canonical_skill"] or r["skill_text_raw"]
            skill_refs.setdefault(link, []).append(SkillRef(candidate_skill_id=r["candidate_skill_id"], name=name))

    evidence_by_parent: Dict[tuple, List[EvidenceRecord]] = {}
    unattached: List[EvidenceRecord] = []
    for r in rows["evidence"]:
        parent = (("employment", r["candidate_employment_id"]) if r["candidate_employment_id"]
                  else ("activity", r["candidate_activity_id"]) if r["candidate_activity_id"] else (None, None))
        rec = EvidenceRecord(candidate_evidence_id=r["candidate_evidence_id"], evidence_type=r["evidence_type"],
                             responsibility_area=r["responsibility_area"], evidence_text=r["evidence_text"],
                             attached_to=parent[0], attached_id=parent[1],
                             skills=skill_refs.get(("evidence", r["candidate_evidence_id"]), []), provenance=_prov(r), version=r.get("updated_at"),
                             area_classification=r.get("area_classification"), area_classification_basis=r.get("area_classification_basis"),
                             area_classified_text=r.get("area_classified_text"),
                             area_classification_is_stale=bool(r.get("area_classification_is_stale")),
                             area_classification_none=bool(r.get("area_classification_none")),
                             area_qualifiers=r.get("area_qualifiers") or {},
                             area_classification_taxonomy_version=r.get("area_classification_taxonomy_version"))
        (evidence_by_parent.setdefault(parent, []) if parent[0] else unattached).append(rec)

    employers = {r["candidate_employer_id"]: EmployerRecord(**r) for r in rows["employers"]}

    def split(parent):
        ev = evidence_by_parent.get(parent, [])
        return ([e for e in ev if e.evidence_type in RESPONSIBILITY_TYPES],
                [e for e in ev if e.evidence_type == "achievement"],
                [e for e in ev if e.evidence_type in OTHER_EVIDENCE_TYPES])

    employment = []
    for r in rows["employments"]:
        resp, ach, other = split(("employment", r["candidate_employment_id"]))
        employment.append(EmploymentRecord(
            candidate_employment_id=r["candidate_employment_id"], sequence_index=r["sequence_index"],
            title_raw=r["title_raw"], role_id=r["role_id"], normalised_title=r["normalised_title"],
            role_family=r["role_family"], seniority_code=r["seniority_code"], mapping_status=r["mapping_status"],
            mapping_confidence=r["mapping_confidence"], mapping_note=r["mapping_note"],
            role_mapping_basis=r.get("role_mapping_basis"), role_mapping_alias_id=r.get("role_mapping_alias_id"),
            mapped_title_raw=r.get("mapped_title_raw"), role_mapping_ownership_areas=r.get("role_mapping_ownership_areas"),
            mapping_is_stale=bool(r.get("mapping_is_stale")),
            employment_type=r["employment_type"], primary_career_sequence=r["primary_career_sequence"],
            counts_as_full_time_experience=r["counts_as_full_time_experience"],
            classification_note=r["classification_note"], start=_date(r, "start"), end=_date(r, "end"),
            is_current_as_stated=r["is_current_as_stated"], entry_source=r["entry_source"],
            employer=employers.get(r["candidate_employer_id"]), responsibilities=resp, achievements=ach,
            other_evidence=other, skills=skill_refs.get(("employment", r["candidate_employment_id"]), []),
            provenance=_prov(r), version=r.get("updated_at")))

    education = [EducationRecord(
        **{k: r[k] for k in ("candidate_education_id", "qualification_title_raw", "qualification_level", "institution_name",
                             "field_of_study", "study_mode", "completion_status", "notes")},
        start=_date(r, "start"), end=_date(r, "end"),
        skills=skill_refs.get(("education", r["candidate_education_id"]), []), provenance=_prov(r), version=r.get("updated_at"))
        for r in rows["education"]]

    activities: Dict[str, List[ActivityRecord]] = {t: [] for t in ACTIVITY_TYPES}
    for r in rows["activities"]:
        ev = evidence_by_parent.get(("activity", r["candidate_activity_id"]), [])
        ach = [e for e in ev if e.evidence_type == "achievement"]
        other = [e for e in ev if e.evidence_type != "achievement"]
        activities[r["activity_type"]].append(ActivityRecord(
            **{k: r[k] for k in ("candidate_activity_id", "activity_type", "title_raw", "category_code",
                                 "category_mapping_status", "category_mapping_confidence", "organisation_raw",
                                 "description_raw", "contribution_raw", "outcome_raw", "link_url", "status",
                                 "related_employment_id", "credential_identifier_raw", "metadata")},
            start=_date(r, "start"), end=_date(r, "end"), expiry=_date(r, "expiry"), achievements=ach,
            other_evidence=other, skills=skill_refs.get(("activity", r["candidate_activity_id"]), []),
            provenance=_prov(r), version=r.get("updated_at")))

    breaks = [CareerBreakRecord(candidate_break_id=r["candidate_break_id"], break_reason=r["break_reason"], note=r["note"],
                                start=_date(r, "start"), end=_date(r, "end"), provenance=_prov(r), version=r.get("updated_at"))
              for r in rows["career_breaks"]]

    return CandidateProfile(
        candidate_profile_id=p["candidate_profile_id"], headline=p["headline"], country_code=p["country_code"],
        resume_as_of=_date(p, "resume_as_of"), confirmation_status=p["confirmation_status"],
        employment_history=employment, education=education, career_activities=activities, skills=skills,
        unattached_evidence=unattached, career_breaks=breaks)


# --------------------------------------------------------------------------- career_history bridge
def employment_inputs(profile: CandidateProfile) -> List[CandidateEmployment]:
    """The profile's employment episodes as career_history inputs. Only employment_history is used:
    education, activities (certifications, projects, volunteering, ...), skills and breaks never
    enter an employment sequence. The candidate's own employer id is used only to compare
    consecutive episodes and is not serialised by career_history.

    Review eligibility: a rejected record is removed (the candidate said it is inaccurate, so its
    neighbours may become adjacent). Every other record keeps its review status; career_history holds
    pending records in the chain as ineligible barriers, so they are never skipped."""
    out = []
    for e in profile.employment_history:
        if e.provenance.review_status == "rejected":
            continue
        out.append(CandidateEmployment(
            employment_id=str(e.candidate_employment_id), sequence_index=e.sequence_index,
            employer_id=str(e.employer.candidate_employer_id) if e.employer else None,
            title_raw=e.title_raw, role_id=e.role_id, seniority_code=e.seniority_code,
            mapping_status=e.mapping_status,
            mapping_confidence=None if e.mapping_confidence is None else float(e.mapping_confidence),
            mapping_basis=e.role_mapping_basis, mapping_is_stale=e.mapping_is_stale,
            mapping_note=e.mapping_note, employment_type=e.employment_type,
            primary_career_sequence=e.primary_career_sequence,
            counts_as_full_time_experience=e.counts_as_full_time_experience,
            classification_note=e.classification_note,
            start_year=e.start.year, start_month=e.start.month, start_precision=e.start.precision,
            end_year=e.end.year, end_month=e.end.month, end_precision=e.end.precision,
            is_current_as_stated=e.is_current_as_stated,
            entry_source=e.entry_source,
            review_status=e.provenance.review_status))
    return out


def resume_as_of_input(profile: CandidateProfile) -> Optional[PartialDateInput]:
    d = profile.resume_as_of
    return PartialDateInput(year=d.year, month=d.month, precision=d.precision) if d.precision != "unknown" else None


# --------------------------------------------------------------------------- role mapping options (M5)
OWNERSHIP_FAMILY, PM_FAMILY = "product_ownership", "product_management"


def eligible_role(role: Optional[dict]) -> bool:
    """A role a candidate may choose: active and resolved (never ROLE-UNKNOWN, retired or unresolved)."""
    return bool(role) and role["active"] and role["is_resolved"]


def needs_ownership_clarification(match: TitleMatch, role_id: str, taxonomy: Dict[str, dict]) -> bool:
    """Data dictionary §9: a Product Owner-family title (by its words, qualified or not, or because its
    aliases include a product-ownership role) maps to a product-management role only with PM-ownership
    responsibility areas. The database applies the same rule (trace_review.choose_employment_role)."""
    role = taxonomy.get(role_id)
    return (role is not None and role["role_family"] == PM_FAMILY
            and (is_product_owner_title(match.original_title)
                 or any(taxonomy.get(o.role_id, {}).get("role_family") == OWNERSHIP_FAMILY for o in match.options)))


def role_mapping_options(employment_id: UUID, state: dict, match: TitleMatch, taxonomy: Dict[str, dict],
                         pm_areas: List[str], suggestion=None) -> RoleMappingOptions:
    """Pure: the matcher's proposal turned into the candidate's choices. Alias-derived options come first,
    then every other eligible role. A context-required title whose only options are ineligible (for example
    'Associate' -> ROLE-UNKNOWN) is unresolved."""
    def choice(role_id: str, source: str, option=None) -> RoleChoice:
        t = taxonomy[role_id]
        return RoleChoice(role_id=role_id, canonical_title=t["canonical_title"], role_family=t["role_family"], source=source,
                          alias_id=option.alias_id if option else None,
                          alias_confidence=option.default_confidence if option else None,
                          mapping_rule=option.mapping_rule if option else None,
                          requires_ownership_clarification=needs_ownership_clarification(match, role_id, taxonomy))

    alias_options = [choice(o.role_id, "alias_option", o) for o in match.options if eligible_role(taxonomy.get(o.role_id))]
    listed = {c.role_id for c in alias_options}
    other = [choice(r, "taxonomy") for r in sorted(taxonomy) if eligible_role(taxonomy[r]) and r not in listed]
    if match.outcome == "automatic":
        outcome, reason = "automatic", match.reason
    elif match.outcome == "context_required" and alias_options:
        outcome, reason = "decision_required", match.reason
    elif match.outcome == "context_required":
        outcome, reason = "unresolved", "the alias offers no eligible role; choose from the taxonomy"
    else:
        outcome, reason = "unresolved", match.reason
    if outcome == "automatic" and needs_ownership_clarification(match, match.proposed_role_id, taxonomy):
        # Never automatic (the database refuses it too, TR020): the candidate decides, with the clarification.
        outcome, reason = "decision_required", "a product-ownership title needs a decision with PM-ownership areas"
    auto = outcome == "automatic"
    suggested = None
    if outcome == "unresolved" and suggestion is not None and eligible_role(taxonomy.get(suggestion[0].role_id)):
        suggested = suggestion
        row = suggestion[0]
        alias_options = [choice(row.role_id, "alias_option", row)] + [c for c in alias_options if c.role_id != row.role_id]
        other = [c for c in other if c.role_id != row.role_id]
    return RoleMappingOptions(
        candidate_employment_id=employment_id, title_raw=state["title_raw"], lookup_title=match.lookup_title,
        outcome=outcome, reason=reason,
        automatic_role_id=match.proposed_role_id if auto else None,
        automatic_alias_id=match.matched_alias_ids[0] if auto else None,
        automatic_confidence=match.mapping_confidence if auto else None,
        current_role_id=state["role_id"], current_mapping_status=state["mapping_status"],
        current_mapping_basis=state["role_mapping_basis"], mapping_is_stale=state["mapping_is_stale"],
        alias_options=alias_options, other_roles=other, pm_ownership_areas=pm_areas,
        suggested_role_id=suggested[0].role_id if suggested else None, suggestion_basis=suggested[1] if suggested else None)


# --------------------------------------------------------------------------- current position (M5)
EVIDENCE_MODE = "synthetic_test"        # the only cohort with data; every result is labelled synthetic

_UNKNOWN = [  # (exclusion reason on the latest primary employment, reason code, explanation), in priority order
    ("review_pending", "pending_review", "The most recent employment has not been reviewed yet. Confirm or correct it first."),
    ("mapping_stale", "stale_mapping", "The most recent employment's title changed after its role was mapped. Map it again."),
    ("unresolved_role", "unresolved_role_mapping", "The most recent employment has no confirmed taxonomy role. Map it or choose a role."),
    ("mapping_not_confirmed", "unresolved_role_mapping", "The most recent employment has no confirmed taxonomy role. Map it or choose a role."),
    ("low_mapping_confidence", "low_mapping_confidence",
     "The most recent employment's automatic role mapping is below the confidence threshold. Choose its role."),
]


def derive_current_position(profile: CandidateProfile, history: CandidateHistory) -> CurrentPosition:
    """Pure. The latest primary employment (stated order) decides: if it is transition-eligible it is the
    current position; otherwise the position is unknown, with the reason taken from that record. An older
    eligible role is never used instead, because a newer primary employment makes it uncertain."""
    if not profile.employment_history:
        return CurrentPosition(status="unknown", reason="no_employment_history", explanation="No employment history is recorded.")
    primary = sorted((r for r in history.records if r.in_primary_sequence), key=lambda r: r.primary_position)
    if not primary:
        return CurrentPosition(status="unknown", reason="no_eligible_primary_employment",
                               explanation="No employment is in the primary career sequence (every record is rejected or parallel).")
    latest = primary[-1]
    record = next(e for e in profile.employment_history if str(e.candidate_employment_id) == latest.stated.employment_id)
    base = dict(candidate_employment_id=record.candidate_employment_id, title_raw=record.title_raw,
                is_current_as_stated=record.is_current_as_stated)
    if latest.is_transition_eligible:
        return CurrentPosition(status="known", explanation="The most recent primary employment is reviewed, mapped and eligible.",
                               role_id=record.role_id, canonical_title=record.normalised_title, role_family=latest.role_family,
                               seniority_code=latest.seniority_code, mapping_basis=record.role_mapping_basis, **base)
    reasons = list(latest.exclusion_reasons)
    reason, text = next(((code, t) for r, code, t in _UNKNOWN if r in reasons),
                        ("latest_employment_ineligible",
                         "The most recent primary employment is not eligible (" + ", ".join(reasons) + "), so the current role cannot be established."))
    return CurrentPosition(status="unknown", reason=reason, explanation=text, exclusion_reasons=reasons, **base)


def user_grounded_position(position: CurrentPosition) -> CurrentPosition:
    """Phase 7.2.1: the starting point must rest on the user's own confirmation of the standard role, not on the
    matcher's confidence. A known position stands only when the role basis is 'candidate_selected' (the user chose or
    explicitly confirmed it through choose_role); an automatic alias match, or a mapping set outside the mapping
    functions, leaves it unknown with reason 'role_unconfirmed' until the user confirms the role. An automatic re-match
    later replaces the basis and so withdraws the confirmation; a title edit makes the mapping stale (already excluded).
    The sequencing engine and its eligibility rules are unchanged; only the product's starting point is gated."""
    if position.status != "known" or position.mapping_basis == "candidate_selected":
        return position
    return CurrentPosition(status="unknown", reason="role_unconfirmed",
                           explanation="The role matched to the most recent employment has not been confirmed yet. Confirm or choose its role.",
                           candidate_employment_id=position.candidate_employment_id, title_raw=position.title_raw,
                           is_current_as_stated=position.is_current_as_stated, exclusion_reasons=["role_not_user_confirmed"])


# --------------------------------------------------------------------------- service
class IngestionOutcome(BaseModel):
    extraction_run_id: UUID
    status: str


class CandidateProfileService(EntriesMixin):
    """Owner-scoped operations on a candidate's structured career profile. ``conn`` is a psycopg
    connection; each call is one owner-session transaction."""

    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    # ------------------------------------------------------------------ profile
    def create_profile(self, owner_user_id: UUID, payload: ProfileCreate) -> UUID:
        with owner_session(self._conn, owner_user_id) as repo:
            return repo.insert_profile(content_columns("profile", payload, creating=True))

    def owner_profile_id(self, owner_user_id: UUID) -> Optional[UUID]:
        """The owner's single candidate profile, or None. More than one raises ProfileAmbiguous (never chosen
        silently). Runs as the owner, so RLS limits it to the owner's own profiles."""
        with owner_session(self._conn, owner_user_id) as repo:
            ids = repo.owner_profile_ids()
        if len(ids) > 1:
            raise ProfileAmbiguous("more than one candidate profile exists for this account")
        return ids[0] if ids else None

    def create_owner_profile(self, owner_user_id: UUID, payload: ProfileCreate) -> UUID:
        """Create the owner's profile only if they have none (ProfileAlreadyExists otherwise). A per-owner advisory
        lock in the same owner-session transaction makes concurrent requests for one owner create at most one."""
        with owner_session(self._conn, owner_user_id) as repo:
            repo.lock_owner_profiles()
            if repo.owner_profile_ids():
                raise ProfileAlreadyExists("a candidate profile already exists for this account")
            return repo.insert_profile(content_columns("profile", payload, creating=True))

    # ------------------------------------------------------------------ consent and resume metadata (Phase 2)
    def consent_state(self, owner_user_id: UUID, purpose: str = "resume_processing") -> dict:
        """The current approved document for the purpose (or None) and the owner's latest decision. active is true
        only when that decision is 'granted' for an approved, effective, unretired document (the ingestion check)."""
        with owner_session(self._conn, owner_user_id) as repo:
            docs = repo.consent_documents(purpose)
            latest = repo.latest_consent_event(purpose)
        approved = {d["consent_version"] for d in docs}
        return {"document": docs[0] if docs else None, "latest": latest,
                "active": bool(latest and latest["status"] == "granted" and latest["consent_version"] in approved)}

    def record_consent(self, owner_user_id: UUID, decision: str, consent_version: str, purpose: str = "resume_processing") -> dict:
        """Append the owner's own explicit decision (granted, denied or withdrawn) for the current approved document.
        The version must be the document the owner was shown (ConsentConflict otherwise). Never inferred."""
        if decision not in ("granted", "denied", "withdrawn"):
            raise InvalidReference("decision must be granted, denied or withdrawn")
        with owner_session(self._conn, owner_user_id) as repo:
            docs = repo.consent_documents(purpose)
            if not docs or docs[0]["consent_version"] != consent_version:
                raise ConsentConflict("this consent document is not the current approved version")
            repo.insert_consent_event(purpose, decision, consent_version)
        return self.consent_state(owner_user_id, purpose)

    def register_resume(self, ctx: OwnerContext, byte_size: int, sha256: str, filename: Optional[str]) -> UUID:
        """Record resume metadata only (the bytes are not stored): size and the server-computed SHA-256."""
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            if not repo.profile_exists(ctx.candidate_profile_id):
                raise ProfileNotFound(str(ctx.candidate_profile_id))
            placeholder = f"{ctx.owner_user_id}/{uuid4()}.pdf"           # no object is uploaded (metadata only)
            return repo.insert_resume(ctx.candidate_profile_id, placeholder, filename, byte_size, sha256)

    def remove_resume(self, ctx: OwnerContext, resume_id: UUID) -> None:
        """Delete one of the owner's resume metadata rows (used to undo a registration that could not proceed)."""
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            repo.delete_resume(ctx.candidate_profile_id, resume_id)

    def update_profile(self, ctx: OwnerContext, payload: ProfileUpdate) -> None:
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            repo.update_profile(ctx.candidate_profile_id, content_columns("profile", payload, creating=False))

    def get_profile(self, ctx: OwnerContext) -> CandidateProfile:
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            rows = repo.fetch_profile_rows(ctx.candidate_profile_id)
        if rows is None:
            raise ProfileNotFound(str(ctx.candidate_profile_id))
        return assemble_profile(rows)

    # ------------------------------------------------------------------ records
    def _create(self, kind: str, ctx: OwnerContext, payload: BaseModel) -> UUID:
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            if not repo.profile_exists(ctx.candidate_profile_id):
                raise ProfileNotFound(str(ctx.candidate_profile_id))
            return repo.insert_record(kind, ctx.candidate_profile_id, content_columns(kind, payload, creating=True))

    def _update(self, kind: str, ctx: OwnerContext, record_id: UUID, payload: BaseModel, expected_version=None) -> None:
        """Edit the candidate's own unreviewed draft. The row is locked and checked first; the
        review status is never changed by an edit."""
        values = content_columns(kind, payload, creating=False)
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            if not repo.profile_exists(ctx.candidate_profile_id):
                raise ProfileNotFound(str(ctx.candidate_profile_id))
            if kind == "employer":
                state = repo.lock_employer_state(ctx.candidate_profile_id, record_id)
                check_version(state["updated_at"], expected_version)
                check_employer_editable(state["attribute_provenance"], state["employment_statuses"])
            else:
                if kind == "employment" and "candidate_employer_id" in values:
                    if expected_version is not None:          # the reassignment function locks employers first
                        raise InvalidRecord("an employer reassignment cannot be combined with an expected version")
                    # Reassignment runs in the database function: employer rows (old and new, by id)
                    # before the employment row, the same order as an employer edit.
                    repo.reassign_employment(ctx.candidate_profile_id, record_id, values.pop("candidate_employer_id"))
                state = repo.lock_review_state(kind, ctx.candidate_profile_id, record_id)
                check_version(state["updated_at"], expected_version)
                check_editable(state["information_origin"], state["review_status"])
            if values:
                repo.update_record(kind, ctx.candidate_profile_id, record_id, values)

    def add_employer(self, ctx: OwnerContext, payload: EmployerCreate) -> UUID:
        return self._create("employer", ctx, payload)

    def update_employer(self, ctx: OwnerContext, record_id: UUID, payload: EmployerUpdate, expected_version=None) -> None:
        self._update("employer", ctx, record_id, payload, expected_version)

    def add_employment(self, ctx: OwnerContext, payload: EmploymentCreate) -> UUID:
        return self._create("employment", ctx, payload)

    def update_employment(self, ctx: OwnerContext, record_id: UUID, payload: EmploymentUpdate, expected_version=None) -> None:
        self._update("employment", ctx, record_id, payload, expected_version)

    def add_education(self, ctx: OwnerContext, payload: EducationCreate) -> UUID:
        return self._create("education", ctx, payload)

    def update_education(self, ctx: OwnerContext, record_id: UUID, payload: EducationUpdate, expected_version=None) -> None:
        self._update("education", ctx, record_id, payload, expected_version)

    def add_activity(self, ctx: OwnerContext, payload: ActivityCreate) -> UUID:
        return self._create("activity", ctx, payload)

    def update_activity(self, ctx: OwnerContext, record_id: UUID, payload: ActivityUpdate, expected_version=None) -> None:
        self._update("activity", ctx, record_id, payload, expected_version)

    def add_evidence(self, ctx: OwnerContext, payload: EvidenceCreate) -> UUID:
        return self._create("evidence", ctx, payload)

    def update_evidence(self, ctx: OwnerContext, record_id: UUID, payload: EvidenceUpdate, expected_version=None) -> None:
        self._update("evidence", ctx, record_id, payload, expected_version)

    def add_skill(self, ctx: OwnerContext, payload: SkillCreate) -> UUID:
        return self._create("skill", ctx, payload)

    def update_skill(self, ctx: OwnerContext, record_id: UUID, payload: SkillUpdate, expected_version=None) -> None:
        self._update("skill", ctx, record_id, payload, expected_version)

    def add_career_break(self, ctx: OwnerContext, payload: CareerBreakCreate) -> UUID:
        return self._create("career_break", ctx, payload)

    def update_career_break(self, ctx: OwnerContext, record_id: UUID, payload: CareerBreakUpdate, expected_version=None) -> None:
        self._update("career_break", ctx, record_id, payload, expected_version)

    # ------------------------------------------------------------------ review actions
    def confirm_record(self, ctx: OwnerContext, kind: str, record_id: UUID, expected_version=None) -> str:
        """pending_review -> user_confirmed. Already confirmed or (legacy) corrected: no change.
        Rejected: refused (reinstate first). Returns the resulting review status."""
        return self._review(ctx, kind, record_id, "confirm", expected_version)

    def reject_record(self, ctx: OwnerContext, kind: str, record_id: UUID, expected_version=None) -> str:
        """pending_review, user_confirmed or user_corrected -> rejected. The record is kept but is
        excluded from sequencing immediately. Returns the resulting review status."""
        return self._review(ctx, kind, record_id, "reject", expected_version)

    def reinstate_record(self, ctx: OwnerContext, kind: str, record_id: UUID, expected_version=None) -> str:
        """rejected -> pending_review (never straight to confirmed). Returns the resulting status."""
        return self._review(ctx, kind, record_id, "reinstate", expected_version)

    def _review(self, ctx: OwnerContext, kind: str, record_id: UUID, action: ReviewAction, expected_version=None) -> str:
        """Apply one review action through trace_review.review_record. Changes review_status only;
        origin, verification, extraction confidence and source references are never touched."""
        if kind not in TABLES or not TABLES[kind][2]:
            raise InvalidRecord(f"{kind!r} records have no review status")
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            if not repo.profile_exists(ctx.candidate_profile_id):
                raise ProfileNotFound(str(ctx.candidate_profile_id))
            if expected_version is not None:        # the same row lock the review function takes, held first
                check_version(repo.lock_review_state(kind, ctx.candidate_profile_id, record_id)["updated_at"], expected_version)
            return repo.review_record(kind, ctx.candidate_profile_id, record_id, action)

    # ------------------------------------------------------------------ corrections (M4)
    def correct_record(self, ctx: OwnerContext, kind: str, record_id: UUID, changes: BaseModel, expected_version=None) -> str:
        """Correct scalar content of a reviewed, extracted, inferred or legacy record (M4). The record
        keeps the latest value and becomes user_corrected; the previous values are appended to
        correction history in the same statement (trace_review.correct_record). Rejected records must
        be reinstated first; the candidate's own pending user_stated drafts are edited instead
        (update_*). Source and extraction provenance never change."""
        model = CORRECTION_MODELS.get(kind)
        if model is None:
            raise InvalidRecord(f"{kind!r} records cannot be corrected")
        if not isinstance(changes, model):
            raise InvalidRecord(f"a {kind} correction must be a {model.__name__}")
        values = content_columns(kind, changes, creating=False)
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            if not repo.profile_exists(ctx.candidate_profile_id):
                raise ProfileNotFound(str(ctx.candidate_profile_id))
            state = repo.lock_review_state(kind, ctx.candidate_profile_id, record_id)
            check_version(state["updated_at"], expected_version)
            check_correctable(state["information_origin"], state["review_status"])
            return repo.correct_record(kind, ctx.candidate_profile_id, record_id, values)

    def require_record(self, ctx: OwnerContext, kind: str, record_id: UUID) -> None:
        """RecordNotFound unless the record exists in the owner's profile (owner session, RLS). Lets a read that would
        otherwise return an empty result (such as correction history) answer 404 for someone else's record."""
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            repo.lock_review_state(kind, ctx.candidate_profile_id, record_id)

    def correction_history(self, ctx: OwnerContext, kind: str, record_id: UUID) -> List[CorrectionEvent]:
        """The record's correction events, oldest first. Empty for a record never corrected."""
        if kind not in CORRECTION_MODELS:
            raise InvalidRecord(f"{kind!r} records have no correction history")
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            return [CorrectionEvent(**r) for r in repo.correction_history(ctx.candidate_profile_id, kind, record_id)]

    # ------------------------------------------------------------------ lookups (API Phase 4)
    def target_roles(self, ctx: OwnerContext) -> List[dict]:
        """The supported PM target roles (role_id, canonical_title), read through the owner session."""
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            return repo.target_roles()

    def responsibility_areas(self, ctx: OwnerContext) -> List[dict]:
        """The responsibility-area taxonomy (code, label, PM-ownership signal), read through the owner session."""
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            return repo.responsibility_area_rows()

    def taxonomy(self, ctx: OwnerContext) -> List[dict]:
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            return repo.taxonomy_rows()

    def classify_evidence_v2(self, ctx: OwnerContext, evidence_id: UUID, areas: List[str], none: bool = False,
                             qualifiers: Optional[dict] = None, expected_version=None) -> List[str]:
        """Phase 3.1: the user's own classification: responsibility areas (with optional yes/no scope answers) or an
        explicit "None of these areas". Never inferred. Records the taxonomy version reviewed against."""
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            if not repo.profile_exists(ctx.candidate_profile_id):
                raise ProfileNotFound(str(ctx.candidate_profile_id))
            state = repo.lock_evidence_state(ctx.candidate_profile_id, evidence_id)
            check_version(state["updated_at"], expected_version)
            if state["review_status"] == "rejected":
                raise RecordNotEditable("rejected evidence cannot be classified; reinstate it first")
            if none and (areas or qualifiers):
                raise InvalidReference('"None of these areas" cannot be combined with areas')
            if not none and not areas:
                raise InvalidReference('choose at least one responsibility area, or "None of these areas"')
            unknown = sorted(set(areas or []) - set(repo.responsibility_area_codes()))
            if unknown:
                raise InvalidReference(f"not responsibility areas of the taxonomy: {unknown}")
            return repo.classify_evidence_v2(ctx.candidate_profile_id, evidence_id, list(areas or []), none, qualifiers or {},
                                             state["evidence_text"])

    def clear_evidence_classification(self, ctx: OwnerContext, evidence_id: UUID, expected_version=None) -> None:
        """Back to unclassified (the user's choice). Unclassified experience is never counted as missing."""
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            if not repo.profile_exists(ctx.candidate_profile_id):
                raise ProfileNotFound(str(ctx.candidate_profile_id))
            state = repo.lock_evidence_state(ctx.candidate_profile_id, evidence_id)
            check_version(state["updated_at"], expected_version)
            repo.clear_evidence_classification(ctx.candidate_profile_id, evidence_id)

    # ------------------------------------------------------------------ current position and route evidence (M5)
    def current_position(self, ctx: OwnerContext, context: SequencingContext) -> CurrentPosition:
        """Where the candidate is today, from their own sequenced employment history (derive_current_position)."""
        profile = self.get_profile(ctx)
        history = sequence_candidate_history(employment_inputs(profile), context, resume_as_of=resume_as_of_input(profile))
        return user_grounded_position(derive_current_position(profile, history))

    def route_evidence(self, ctx: OwnerContext, context: SequencingContext, target_role_id: str) -> CurrentPositionEvidence:
        """Observed routes from the candidate's current role to one of the PM target roles: read-only reference
        evidence (retrieve_reference_evidence with an origin role), synthetic test data only. Nothing is written.
        When the current position is unknown, no evidence is returned and the reason is."""
        position = self.current_position(ctx, context)
        own = self.own_reference_profile_ids(ctx.owner_user_id)
        with reference_snapshot(self._conn) as repo:
            target = repo.get_roles([target_role_id]).get(target_role_id)
            if target is None or not target.is_pm_target:
                raise InvalidReference(f"{target_role_id!r} is not one of the PM target roles")
            if position.status != "known":
                return CurrentPositionEvidence(current_position=position, target_role_id=target_role_id,
                                               note=f"No route evidence: the current position is unknown ({position.reason}).")
            # The caller's own contribution excluded, career progressions only, route-level privacy (Phases 7.2.1, 8.1).
            evidence = recommendation_evidence(repo, target_role_id, EVIDENCE_MODE, position.role_id, own)
        note = (f"Synthetic test fixtures with an observed route from {position.role_id} to {target_role_id}. They describe "
                "what those fixture profiles did; they are not real people and not a forecast."
                if evidence.has_observed_pathways else
                f"No observed route from {position.role_id} to {target_role_id} in the synthetic test data. This is an absence of "
                "observations, not an assessment of the route.")
        return CurrentPositionEvidence(current_position=position, target_role_id=target_role_id, evidence=evidence, note=note)

    def own_reference_profile_ids(self, owner_user_id: UUID) -> List[str]:
        """The reference profile ids of the caller's own contributions (trace_contrib.my_reference_profile_ids, owner
        role, request identity). Used server-side only, to exclude them from the caller's own evidence and comparisons;
        never returned to a client. Empty when the caller has contributed nothing (or withdrew)."""
        with owner_session(self._conn, owner_user_id) as repo:
            return repo.own_reference_profile_ids()

    # ------------------------------------------------------------------ personalised pathway result (M6)
    def pathway_result(self, ctx: OwnerContext, context: SequencingContext, target_role_id: str,
                       evidence_mode: str = EVIDENCE_MODE) -> "PathwayResult":
        """Read-only, deterministic pathway result for one of the nine PM target roles (pathway.build_pathway_result):
        the current position, observed routes from it (synthetic test data), the safely disclosed experience on each
        route, the candidate's fit from their own reviewed, classified evidence, gaps and generated guidance. Nothing
        is written; no probability, requirement or company fit is assessed."""
        from app.services.candidate_profile.pathway import (
            RouteRole, build_pathway_result, eligible_candidate_evidence,
        )
        profile = self.get_profile(ctx)
        history = sequence_candidate_history(employment_inputs(profile), context, resume_as_of=resume_as_of_input(profile))
        position = user_grounded_position(derive_current_position(profile, history))
        candidate = eligible_candidate_evidence(profile, history)
        held = {r.stated.role_id for r in history.records if r.in_primary_sequence and r.is_transition_eligible}
        own = self.own_reference_profile_ids(ctx.owner_user_id)
        with reference_snapshot(self._conn) as repo:
            target = repo.get_roles([target_role_id]).get(target_role_id)
            if target is None or not target.is_pm_target:
                raise InvalidReference(f"{target_role_id!r} is not one of the PM target roles")
            # Phase 5 (D51): the current role is a valid target (the same role elsewhere, or a step back). Zero-move routes
            # are never built from the evidence (build_pathway_result drops them), so such a target has no observed
            # routes here, and the plan says what the research can and cannot show about it.
            evidence = None
            if position.status == "known":
                evidence = recommendation_evidence(repo, target_role_id, evidence_mode, position.role_id, own)
            roles = {r for s in (evidence.route_summaries if evidence else []) for r in s.route} | {target_role_id}
            role_meta = repo.get_roles(sorted(roles))
            titles = {k: v.canonical_title for k, v in role_meta.items()}
            threshold = repo.min_display_count()
        area_info = {a.responsibility_area: (a.label, a.is_pm_ownership_signal)
                     for x in (evidence.route_step_experience if evidence else []) for a in x.areas}
        return build_pathway_result(position, RouteRole(role_id=target_role_id, canonical_title=titles[target_role_id]), candidate,
                                    held, evidence, titles, area_info,
                                    threshold=int(threshold) if threshold is not None else 10 ** 9,      # no setting: nothing common
                                    role_meta=role_meta)

    # ------------------------------------------------------------------ evidence classification (M6)
    def classify_evidence(self, ctx: OwnerContext, evidence_id: UUID, areas: List[str], expected_version=None) -> List[str]:
        """The candidate classifies one of their evidence records (extracted or entered) with responsibility areas,
        through trace_review.classify_evidence_areas. A candidate assertion (basis candidate_selected), not
        verification: review status, verification, provenance, text and employments never change. Rejected
        evidence is refused (reinstate it first). Duplicates are removed; the stored list is sorted."""
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            if not repo.profile_exists(ctx.candidate_profile_id):
                raise ProfileNotFound(str(ctx.candidate_profile_id))
            state = repo.lock_evidence_state(ctx.candidate_profile_id, evidence_id)
            check_version(state["updated_at"], expected_version)
            if state["review_status"] == "rejected":
                raise RecordNotEditable("rejected evidence cannot be classified; reinstate it first")
            if not areas:
                raise InvalidReference("a classification names at least one responsibility area")
            unknown = sorted({a for a in areas if a is None} | (set(areas) - set(repo.responsibility_area_codes()) - {None}), key=str)
            if unknown:
                raise InvalidReference(f"not responsibility areas of the taxonomy: {unknown}")
            return repo.classify_evidence_areas(ctx.candidate_profile_id, evidence_id, list(areas), state["evidence_text"])

    # ------------------------------------------------------------------ role mapping (M5)
    def _alias_index(self) -> AliasIndex:
        return AliasIndex(PostgresAliasRepository(self._conn).alias_rows())

    def role_mapping_options(self, ctx: OwnerContext, employment_id: UUID) -> RoleMappingOptions:
        """The automatic proposal for the employment's current title (if any), the alias-derived options and
        every other eligible role. Reads only; changes nothing."""
        index = self._alias_index()
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            if not repo.profile_exists(ctx.candidate_profile_id):
                raise ProfileNotFound(str(ctx.candidate_profile_id))
            state = repo.lock_mapping_state(ctx.candidate_profile_id, employment_id)
            return role_mapping_options(employment_id, state, match_title(state["title_raw"], index),
                                        repo.taxonomy_roles(), repo.pm_ownership_areas(), suggest_role(state["title_raw"], index))

    def role_options_for_title(self, ctx: OwnerContext, title_raw: str) -> RoleMappingOptions:
        """The same choices as role_mapping_options, for a title that is not a record yet (a staged resume import, D48).
        Reads only."""
        return self.role_options_for_titles(ctx, [title_raw])[title_raw]

    def role_options_for_titles(self, ctx: OwnerContext, titles: List[str]) -> Dict[str, RoleMappingOptions]:
        """role_options_for_title for several titles, reading the alias index and the taxonomy once."""
        index = self._alias_index()
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            taxonomy, areas = repo.taxonomy_roles(), repo.pm_ownership_areas()
        out = {}
        for title_raw in dict.fromkeys(titles):
            unmapped = {"title_raw": title_raw, "role_id": "ROLE-UNKNOWN", "mapping_status": "unresolved", "role_mapping_basis": None,
                        "mapping_is_stale": False}
            out[title_raw] = role_mapping_options(UUID(int=0), unmapped, match_title(title_raw, index), taxonomy, areas,
                                                  suggest_role(title_raw, index))
        return out

    def map_employment(self, ctx: OwnerContext, employment_id: UUID) -> RoleMappingResult:
        """Apply the automatic alias mapping for the employment's current title when there is one
        (trace_review.apply_alias_mapping). Context-required and unmatched titles are not mapped: the
        result lists the options for a candidate decision (choose_role). A current candidate decision, or a
        mapping set outside the mapping functions, is kept. Review status never changes."""
        index = self._alias_index()
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            if not repo.profile_exists(ctx.candidate_profile_id):
                raise ProfileNotFound(str(ctx.candidate_profile_id))
            state = repo.lock_mapping_state(ctx.candidate_profile_id, employment_id)
            if state["review_status"] == "rejected":
                raise RecordNotEditable("a rejected record cannot be mapped; reinstate it first")
            options = role_mapping_options(employment_id, state, match_title(state["title_raw"], index),
                                           repo.taxonomy_roles(), repo.pm_ownership_areas())
            if options.outcome != "automatic":
                return RoleMappingResult(candidate_employment_id=employment_id, result=options.outcome, options=options)
            if state["role_mapping_basis"] == "candidate_selected" and not state["mapping_is_stale"]:
                return RoleMappingResult(candidate_employment_id=employment_id, result="candidate_decision_kept",
                                         role_id=state["role_id"], options=options)
            if state["role_mapping_basis"] is None and (state["role_id"] != "ROLE-UNKNOWN" or state["mapping_status"] != "unresolved"):
                return RoleMappingResult(candidate_employment_id=employment_id, result="existing_mapping_kept",
                                         role_id=state["role_id"], options=options)
            role = repo.apply_alias_mapping(ctx.candidate_profile_id, employment_id, options.automatic_alias_id, state["title_raw"])
            return RoleMappingResult(candidate_employment_id=employment_id, result="mapped_automatically", role_id=role,
                                     options=options)

    def choose_role(self, ctx: OwnerContext, employment_id: UUID, role_id: str,
                    ownership_areas: Optional[List[str]] = None) -> str:
        """The candidate's own role decision (candidate_selected) from any active, resolved role. A
        product-ownership title mapped to a product-management role needs at least one PM-ownership
        responsibility area. Applies to the candidate's own sequence only; review status never changes."""
        index = self._alias_index()
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            if not repo.profile_exists(ctx.candidate_profile_id):
                raise ProfileNotFound(str(ctx.candidate_profile_id))
            state = repo.lock_mapping_state(ctx.candidate_profile_id, employment_id)
            if state["review_status"] == "rejected":
                raise RecordNotEditable("a rejected record cannot be mapped; reinstate it first")
            taxonomy = repo.taxonomy_roles()
            if not eligible_role(taxonomy.get(role_id)):
                raise InvalidReference(f"{role_id!r} is not an active, resolved taxonomy role")
            areas = sorted(set(ownership_areas)) if ownership_areas else None
            required = needs_ownership_clarification(match_title(state["title_raw"], index), role_id, taxonomy)
            if areas and not required:
                raise InvalidReference("ownership areas apply only to a product-ownership title mapped to a product-management role")
            if areas and not set(areas) <= set(repo.pm_ownership_areas()):
                raise InvalidReference("ownership areas must be PM-ownership responsibility areas")
            if required and not areas:
                raise ClarificationRequired("mapping a product-ownership title to a product-management role needs at least "
                                            "one PM-ownership responsibility area")
            return repo.choose_employment_role(ctx.candidate_profile_id, employment_id, role_id, state["title_raw"], areas)

    # ------------------------------------------------------------------ extraction ingestion (M3)
    def ingest_extraction(self, ctx: OwnerContext, resume_id: UUID, result: "ExtractionResult", real: bool = False) -> IngestionOutcome:
        """Turn one extraction result into draft records of the owner's profile.

        Three owner-session transactions, all through trace_ingest functions (no other write path):
        1. start: ownership, fixture scope and consent are checked; the resume's run is returned
           (a new 'queued' run, or the existing one: re-ingestion is idempotent);
        2. ingest: every record is inserted as source_extracted / pending_review / unverified in one
           statement, or none is; the run becomes 'succeeded';
        3. on failure only: the run is marked 'failed' with a short error code, and the error is raised.

        The candidate then reviews the drafts with the existing actions (confirm_record,
        reject_record, reinstate_record). Correcting an extracted value needs correction history (M4)."""
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            run_id = repo.start_extraction(ctx.candidate_profile_id, resume_id, result.extractor_version, real=real)
        try:
            with owner_session(self._conn, ctx.owner_user_id) as repo:
                status = repo.ingest_extraction(run_id, result.ingestion_payload())
        except CandidateProfileError as exc:
            code = re.sub(r"(?<!^)(?=[A-Z])", "_", type(exc).__name__).lower()[:40]
            try:
                with owner_session(self._conn, ctx.owner_user_id) as repo:
                    repo.fail_extraction(run_id, code)
            except CandidateProfileError:
                pass        # the run already succeeded or failed; the original error is what matters
            raise
        return IngestionOutcome(extraction_run_id=run_id, status=status)

    def extraction_run(self, ctx: OwnerContext, run_id: UUID) -> Optional[dict]:
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            return repo.extraction_run(run_id)

    # ------------------------------------------------------------------ sequencing
    def sequence_employment_history(self, ctx: OwnerContext, context: SequencingContext) -> CandidateHistory:
        """Episode-aware sequencing of the candidate's employment episodes only (career_history)."""
        profile = self.get_profile(ctx)
        return sequence_candidate_history(employment_inputs(profile), context, resume_as_of=resume_as_of_input(profile))
