"""/api/v1, product Phase 2: manual entry, draft edits and update proposals for the caller's own profile.

Thin handlers over CandidateProfileService (EntriesMixin). Every route needs a verified bearer token; the profile comes
from the verified owner. Nothing here confirms on the user's behalf: "confirm": true is the user's own explicit "save
and confirm" (or "accept and confirm"), applied through the same review function as every confirmation.

Pasted text is parsed locally and deterministically (no external service, no AI) and never stored: only its SHA-256
and the proposed items are kept until the user decides on each one (D5, D7).
"""
import hashlib
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import owner_context, profile_service
from app.api.profile_routes import expected_version
from app.api.ratelimit import limit
from app.services.candidate_profile import CandidateProfileService, InvalidRecord, OwnerContext
from app.services.candidate_profile.entries import (
    ActivityEntry, EducationEntry, EmploymentDraftEdit, EmploymentEntry, EntryResult, LineClassification, SkillEntry,
)
from app.services.resume_extraction.real_parser import PARSER_VERSION, parse_text

router = APIRouter(prefix="/api/v1", tags=["api"])
MAX_TEXT = 20_000


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EntryView(_Body):
    kind: str
    id: UUID                                   # the caller's own new record
    created: Dict[str, int]
    confirmed: bool
    placement: Optional[str] = None
    reordered: int = 0
    note: str


def _entry(r: EntryResult) -> EntryView:
    note = ("Saved and confirmed." if r.confirmed else "Saved as a draft. Review and confirm it when you're ready.")
    if r.reordered:
        note += f" {r.reordered} later role{'s' if r.reordered != 1 else ''} moved down one place; reviewed roles keep this in their history."
    return EntryView(kind=r.kind, id=r.record_id, created=r.created, confirmed=r.confirmed, placement=r.placement,
                     reordered=r.reordered, note=note)


# --------------------------------------------------------------------------- manual entry
@router.post("/entries/employment", response_model=EntryView, status_code=201, dependencies=[Depends(limit("mutation"))])
def add_employment(entry: EmploymentEntry, ctx: OwnerContext = Depends(owner_context),
                   service: CandidateProfileService = Depends(profile_service)) -> EntryView:
    return _entry(service.add_employment_entry(ctx, entry))


@router.post("/entries/education", response_model=EntryView, status_code=201, dependencies=[Depends(limit("mutation"))])
def add_education(entry: EducationEntry, ctx: OwnerContext = Depends(owner_context),
                  service: CandidateProfileService = Depends(profile_service)) -> EntryView:
    return _entry(service.add_education_entry(ctx, entry))


@router.post("/entries/activity", response_model=EntryView, status_code=201, dependencies=[Depends(limit("mutation"))])
def add_activity(entry: ActivityEntry, ctx: OwnerContext = Depends(owner_context),
                 service: CandidateProfileService = Depends(profile_service)) -> EntryView:
    return _entry(service.add_activity_entry(ctx, entry))


@router.post("/entries/skill", response_model=EntryView, status_code=201, dependencies=[Depends(limit("mutation"))])
def add_skill(entry: SkillEntry, ctx: OwnerContext = Depends(owner_context),
              service: CandidateProfileService = Depends(profile_service)) -> EntryView:
    return _entry(service.add_skill_entry(ctx, entry))


# --------------------------------------------------------------------------- draft edits (own pending drafts only)
class EvidenceDraftEdit(_Body):
    evidence_text: Optional[str] = Field(default=None, min_length=3, max_length=1000)
    evidence_type: Optional[Literal["responsibility", "achievement"]] = None


class EditResult(_Body):
    kind: str
    status: Literal["saved"] = "saved"


@router.patch("/records/employment/{record_id}", response_model=EditResult, dependencies=[Depends(limit("mutation"))])
def edit_employment(record_id: UUID, edit: EmploymentDraftEdit, ctx: OwnerContext = Depends(owner_context),
                    service: CandidateProfileService = Depends(profile_service),
                    version: Optional[datetime] = Depends(expected_version)) -> EditResult:
    if not edit.model_fields_set:
        raise InvalidRecord("nothing to update")
    service.edit_employment_draft(ctx, record_id, edit, version)
    return EditResult(kind="employment")


@router.patch("/records/evidence/{record_id}", response_model=EditResult, dependencies=[Depends(limit("mutation"))])
def edit_evidence(record_id: UUID, edit: EvidenceDraftEdit, ctx: OwnerContext = Depends(owner_context),
                  service: CandidateProfileService = Depends(profile_service),
                  version: Optional[datetime] = Depends(expected_version)) -> EditResult:
    from app.services.candidate_profile import EvidenceUpdate
    if not edit.model_fields_set:
        raise InvalidRecord("nothing to update")
    service.update_evidence(ctx, record_id, EvidenceUpdate(**edit.model_dump(exclude_unset=True)), version)
    return EditResult(kind="evidence")


class EducationDraftEdit(_Body):
    qualification_title_raw: Optional[str] = Field(default=None, min_length=1, max_length=200)
    qualification_level: Optional[Literal["secondary", "diploma", "bachelor", "master", "mba", "doctorate", "certificate",
                                          "other", "unknown"]] = None
    institution_name: Optional[str] = Field(default=None, max_length=200)
    field_of_study: Optional[str] = Field(default=None, max_length=200)


class ActivityDraftEdit(_Body):
    title_raw: Optional[str] = Field(default=None, min_length=1, max_length=200)
    organisation_raw: Optional[str] = Field(default=None, max_length=200)
    description_raw: Optional[str] = Field(default=None, max_length=1000)


class SkillDraftEdit(_Body):
    skill_text_raw: str = Field(min_length=1, max_length=80)


def _edit(service: CandidateProfileService, kind: str, ctx: OwnerContext, record_id: UUID, edit: BaseModel, version) -> EditResult:
    from app.services import candidate_profile as cp
    if not edit.model_fields_set:
        raise InvalidRecord("nothing to update")
    model = {"education": cp.EducationUpdate, "activity": cp.ActivityUpdate, "skill": cp.SkillUpdate}[kind]
    getattr(service, f"update_{kind}")(ctx, record_id, model(**edit.model_dump(exclude_unset=True)), version)
    return EditResult(kind=kind)


@router.patch("/records/education/{record_id}", response_model=EditResult, dependencies=[Depends(limit("mutation"))])
def edit_education(record_id: UUID, edit: EducationDraftEdit, ctx: OwnerContext = Depends(owner_context),
                   service: CandidateProfileService = Depends(profile_service),
                   version: Optional[datetime] = Depends(expected_version)) -> EditResult:
    return _edit(service, "education", ctx, record_id, edit, version)


@router.patch("/records/activity/{record_id}", response_model=EditResult, dependencies=[Depends(limit("mutation"))])
def edit_activity(record_id: UUID, edit: ActivityDraftEdit, ctx: OwnerContext = Depends(owner_context),
                  service: CandidateProfileService = Depends(profile_service),
                  version: Optional[datetime] = Depends(expected_version)) -> EditResult:
    return _edit(service, "activity", ctx, record_id, edit, version)


@router.patch("/records/skill/{record_id}", response_model=EditResult, dependencies=[Depends(limit("mutation"))])
def edit_skill(record_id: UUID, edit: SkillDraftEdit, ctx: OwnerContext = Depends(owner_context),
               service: CandidateProfileService = Depends(profile_service),
               version: Optional[datetime] = Depends(expected_version)) -> EditResult:
    return _edit(service, "skill", ctx, record_id, edit, version)


# --------------------------------------------------------------------------- ordering history (D10)
class OrderEventView(_Body):
    sequence: int
    previous_position: int
    new_position: int
    review_status_at_move: str              # unchanged by the move
    cause: Literal["role_inserted", "user_reordered"]
    moved_at: datetime
    note: str


@router.get("/records/employment/{record_id}/order-events", response_model=List[OrderEventView], dependencies=[Depends(limit("read"))])
def order_events(record_id: UUID, ctx: OwnerContext = Depends(owner_context),
                 service: CandidateProfileService = Depends(profile_service)) -> List[OrderEventView]:
    """Position-only moves of one of the caller's roles. Content corrections are listed separately (.../corrections)."""
    return [OrderEventView(sequence=e["event_seq"], previous_position=e["previous_sequence_index"], new_position=e["new_sequence_index"],
                           review_status_at_move=e["review_status_at_move"], cause=e["cause"], moved_at=e["moved_at"],
                           note=("Moved down one place when you added an earlier role." if e["cause"] == "role_inserted"
                                 else "Moved when you reordered your roles."))
            for e in service.order_history(ctx, record_id)]


# --------------------------------------------------------------------------- update proposals
class TextBody(_Body):
    text: str = Field(min_length=1, max_length=MAX_TEXT)


class BatchView(_Body):
    proposed: int                    # new proposals stored now
    already_open: int                # identical proposals already waiting for a decision
    already_in_profile: int          # parsed items the profile already holds (not proposed)
    note: str


class ProposalView(_Body):
    id: UUID
    source: Literal["pasted_text", "resume_update"]
    kind: Literal["employment", "evidence", "education", "activity", "skill"]
    change: Literal["new", "possible_update"]
    summary: str
    proposed: Dict[str, Any]
    current: Optional[Dict[str, Any]] = None        # a possible update: the record's values today
    applies_to: Optional[str] = None                 # the role or qualification it attaches to or would change


class DecisionBody(_Body):
    confirm: bool = False
    # Optional (Phase 3.1): the user's own classification of the proposal's lines, in order; null = leave uncategorised
    classifications: List[Optional[LineClassification]] = Field(default_factory=list, max_length=60)


class DecisionView(_Body):
    status: Literal["accepted", "dismissed"]
    record_id: Optional[UUID] = None
    note: str


def _batch(r: dict) -> BatchView:
    if r["proposed"]:
        note = "Review each suggested change below. Nothing is added to your profile until you accept it."
    elif r["already_in_profile"] or r["already_open"]:
        note = "Nothing new was found: everything is already in your profile or waiting for your decision."
    else:
        note = "No career entries were recognised in this text."
    return BatchView(proposed=r["proposed"], already_open=r["already_open"], already_in_profile=r["already_in_profile"], note=note)


@router.post("/proposals/from-text", response_model=BatchView, status_code=201, dependencies=[Depends(limit("mutation"))])
def propose_from_text(body: TextBody, ctx: OwnerContext = Depends(owner_context),
                      service: CandidateProfileService = Depends(profile_service)) -> BatchView:
    parsed = parse_text(body.text, untitled_is_experience=True).record   # header and contact lines are never read in
    digest = hashlib.sha256(" ".join(body.text.split()).encode()).hexdigest()
    return _batch(service.propose_from_record(ctx, parsed, "pasted_text", digest, PARSER_VERSION))


def _date_label(d: Optional[dict]) -> str:
    if not d or d.get("precision") in (None, "unknown"):
        return "unknown"
    if d["precision"] == "ongoing":
        return "present"
    return f"{d['month']:02d}/{d['year']}" if d["precision"] == "month" else str(d["year"])


def _summary(kind: str, p: dict) -> str:
    if kind == "employment":
        if "title_raw" not in p:
            return "Dates: " + ", ".join(f"{k} {_date_label(v)}" for k, v in p.items())
        at = f" at {p['employer_name']}" if p.get("employer_name") else ""
        return f"{p['title_raw']}{at} ({_date_label(p.get('start'))} – {_date_label(p.get('end'))})"
    if kind == "evidence":
        return p.get("evidence_text", "")
    if kind == "education":
        if "qualification_title_raw" not in p:
            return ", ".join(f"{k.replace('_', ' ')}: {v if isinstance(v, str) else _date_label(v)}" for k, v in p.items())
        return " · ".join(x for x in (p.get("qualification_title_raw"), p.get("institution_name")) if x)
    if kind == "activity":
        return p.get("title_raw", "")
    return p.get("skill_text_raw", "")


@router.get("/proposals", response_model=List[ProposalView], dependencies=[Depends(limit("read"))])
def proposals(ctx: OwnerContext = Depends(owner_context),
              service: CandidateProfileService = Depends(profile_service)) -> List[ProposalView]:
    rows = service.open_proposals(ctx)
    if not rows:
        return []
    profile = service.get_profile(ctx)
    jobs = {e.candidate_employment_id: e for e in profile.employment_history}
    quals = {d.candidate_education_id: d for d in profile.education}
    out = []
    for r in rows:
        current, applies = None, None
        target = r["target_record_id"]
        if target in jobs:
            e = jobs[target]
            applies = e.title_raw + (f" at {e.employer.employer_name}" if e.employer and e.employer.employer_name else "")
            if r["match_state"] == "possible_update":
                current = {k: {"year": getattr(e, k).year, "month": getattr(e, k).month, "precision": getattr(e, k).precision}
                           for k in r["proposed"]}
        elif target in quals:
            d = quals[target]
            applies = d.qualification_title_raw
            current = {k: ({"year": d.end.year, "month": d.end.month, "precision": d.end.precision} if k == "end" else getattr(d, k))
                       for k in r["proposed"]}
        out.append(ProposalView(id=r["proposal_id"], source=r["source"], kind=r["record_kind"], change=r["match_state"],
                                summary=_summary(r["record_kind"], r["proposed"]), proposed=r["proposed"], current=current,
                                applies_to=applies))
    return out


@router.post("/proposals/{proposal_id}/accept", response_model=DecisionView, dependencies=[Depends(limit("mutation"))])
def accept(proposal_id: UUID, body: DecisionBody = DecisionBody(), ctx: OwnerContext = Depends(owner_context),
           service: CandidateProfileService = Depends(profile_service)) -> DecisionView:
    rid = service.accept_proposal(ctx, proposal_id, body.confirm, body.classifications)
    return DecisionView(status="accepted", record_id=rid,
                        note="Added to your profile and confirmed." if body.confirm else "Added to your profile as a draft to review.")


@router.post("/proposals/{proposal_id}/dismiss", response_model=DecisionView, dependencies=[Depends(limit("mutation"))])
def dismiss(proposal_id: UUID, ctx: OwnerContext = Depends(owner_context),
            service: CandidateProfileService = Depends(profile_service)) -> DecisionView:
    service.dismiss_proposal(ctx, proposal_id)
    return DecisionView(status="dismissed", note="Dismissed. Nothing was changed in your profile.")
