"""Client views of the candidate profile for review (Phase 3). Built from the existing CandidateProfile model.

Each record carries its own id (the candidate's own record handle, needed in action URLs) and nothing else
internal: no profile, owner, resume, extraction-run, alias or employer ids. States are explicit, so a client can
tell apart pending, confirmed, corrected and rejected records; automatic, candidate-selected, unmapped and stale
role mappings; and absent, current and stale evidence classifications.
"""
from decimal import Decimal
from typing import Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.services.candidate_profile import CandidateProfile
from app.services.candidate_profile.models import EvidenceRecord, Provenance
from app.services.career_history.models import PartialDateView

REVIEW_LABELS = {"pending_review": "Pending review", "user_confirmed": "Confirmed", "user_corrected": "Corrected",
                 "rejected": "Rejected"}


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewView(_View):
    status: str                          # pending_review | user_confirmed | user_corrected | rejected
    label: str
    origin: Optional[str]                # user_stated | source_extracted | inferred | None (legacy)
    verification_status: str
    is_own_draft: bool                   # pending user_stated: edited directly, not corrected
    can_confirm: bool
    can_reject: bool
    can_reinstate: bool
    can_correct: bool
    source_location: Optional[str] = None
    version: Optional[str] = None        # optimistic concurrency: send back as If-Match with a change to this record


def review_view(p: Provenance, version=None) -> ReviewView:
    s, origin = p.review_status, p.information_origin
    draft = s == "pending_review" and origin == "user_stated"
    return ReviewView(status=s, label=REVIEW_LABELS.get(s, s), origin=origin, verification_status=p.verification_status,
                      is_own_draft=draft, can_confirm=s == "pending_review", can_reject=s != "rejected",
                      can_reinstate=s == "rejected", can_correct=s != "rejected" and not draft, source_location=p.source_location,
                      version=version.isoformat() if version else None)


class ClassificationView(_View):
    # none = not categorised yet; none_of_these = the user's explicit "None of these areas" (Phase 3.1)
    state: Literal["none", "current", "none_of_these", "stale"]
    areas: List[str] = []
    qualifiers: Dict[str, Dict[str, bool]] = {}
    basis: Optional[str] = None
    classified_text: Optional[str] = None          # the text that was classified (differs from the record's when stale)
    taxonomy_version: Optional[int] = None         # the version the user reviewed against (None: before versioning = 1)
    note: str = "Classifying evidence does not confirm it."


def classification_view(x: EvidenceRecord) -> ClassificationView:
    if x.area_classification is None:
        return ClassificationView(state="none")
    state = "stale" if x.area_classification_is_stale else "none_of_these" if x.area_classification_none else "current"
    return ClassificationView(state=state, areas=x.area_classification, qualifiers=x.area_qualifiers or {},
                              basis=x.area_classification_basis, classified_text=x.area_classified_text,
                              taxonomy_version=x.area_classification_taxonomy_version or 1)


class EvidenceView(_View):
    id: UUID
    evidence_type: str
    evidence_text: str
    review: ReviewView
    classification: ClassificationView


def evidence_view(x: EvidenceRecord) -> EvidenceView:
    return EvidenceView(id=x.candidate_evidence_id, evidence_type=x.evidence_type, evidence_text=x.evidence_text,
                        review=review_view(x.provenance, x.version), classification=classification_view(x))


class MappingView(_View):
    state: Literal["automatic", "candidate_selected", "unmapped", "set_outside_app"]
    is_stale: bool                       # the title changed after mapping: a fresh decision is needed
    role_id: Optional[str] = None
    canonical_title: Optional[str] = None
    role_family: Optional[str] = None
    mapping_status: str
    confidence: Optional[Decimal] = None           # automatic mappings only; a candidate decision has none
    mapped_title: Optional[str] = None
    ownership_areas: List[str] = []


class EmploymentView(_View):
    id: UUID
    sequence_index: int
    title_raw: str
    employer_name: Optional[str]
    employment_type: str
    primary_career_sequence: bool
    start: PartialDateView
    end: PartialDateView
    is_current_as_stated: bool
    review: ReviewView
    mapping: MappingView
    evidence: List[EvidenceView]


class OtherRecordView(_View):
    id: UUID
    kind: Literal["education", "activity", "skill", "career_break"]
    title: Optional[str]
    review: ReviewView
    # Detail of the caller's own record, for review (Phase 7). Education: level, institution, field, notes (a grade is
    # kept in notes as "Grade: ..."), end date. Activity: type, organisation, end date. None for other kinds.
    qualification_level: Optional[str] = None
    institution_name: Optional[str] = None
    field_of_study: Optional[str] = None
    notes: Optional[str] = None
    activity_type: Optional[str] = None
    organisation: Optional[str] = None
    start: Optional[PartialDateView] = None           # education (Phase 5): when it started
    end: Optional[PartialDateView] = None
    completion_status: Optional[str] = None           # education: completed | in_progress | incomplete | unknown


class ProfileRecordsView(_View):
    profile_version: str = ""                         # fingerprint of content and review state (changes with any edit)
    employments: List[EmploymentView]
    activity_evidence: List[EvidenceView]            # evidence attached to career activities
    unattached_evidence: List[EvidenceView]
    other_records: List[OtherRecordView]


def _mapping(e) -> MappingView:
    basis = e.role_mapping_basis
    state = ("automatic" if basis == "alias_match" else "candidate_selected" if basis == "candidate_selected"
             else "unmapped" if e.role_id == "ROLE-UNKNOWN" and e.mapping_status == "unresolved" else "set_outside_app")
    mapped = state != "unmapped"
    return MappingView(state=state, is_stale=e.mapping_is_stale, mapping_status=e.mapping_status,
                       role_id=e.role_id if mapped else None, canonical_title=e.normalised_title if mapped else None,
                       role_family=e.role_family if mapped else None,
                       confidence=e.mapping_confidence if basis == "alias_match" else None,
                       mapped_title=e.mapped_title_raw, ownership_areas=e.role_mapping_ownership_areas or [])


def records_view(p: CandidateProfile) -> ProfileRecordsView:
    jobs = [EmploymentView(id=e.candidate_employment_id, sequence_index=e.sequence_index, title_raw=e.title_raw,
                           employer_name=e.employer.employer_name if e.employer else None, employment_type=e.employment_type,
                           primary_career_sequence=e.primary_career_sequence, start=e.start, end=e.end,
                           is_current_as_stated=e.is_current_as_stated, review=review_view(e.provenance, e.version), mapping=_mapping(e),
                           evidence=[evidence_view(x) for x in e.responsibilities + e.achievements + e.other_evidence])
            for e in p.employment_history]
    other = ([OtherRecordView(id=d.candidate_education_id, kind="education", title=d.qualification_title_raw, review=review_view(d.provenance, d.version),
                              qualification_level=d.qualification_level, institution_name=d.institution_name,
                              field_of_study=d.field_of_study, notes=d.notes, start=d.start, end=d.end,
                              completion_status=d.completion_status)
              for d in p.education]
             + [OtherRecordView(id=a.candidate_activity_id, kind="activity", title=a.title_raw, review=review_view(a.provenance, a.version),
                                activity_type=a.activity_type, organisation=a.organisation_raw, end=a.end)
                for acts in p.career_activities.values() for a in acts]
             + [OtherRecordView(id=s.candidate_skill_id, kind="skill", title=s.skill_text_raw, review=review_view(s.provenance, s.version)) for s in p.skills]
             + [OtherRecordView(id=b.candidate_break_id, kind="career_break", title=b.break_reason, review=review_view(b.provenance, b.version))
                for b in p.career_breaks])
    activity_evidence = [evidence_view(x) for acts in p.career_activities.values() for a in acts for x in a.achievements + a.other_evidence]
    from app.services.candidate_profile.entries import profile_version
    return ProfileRecordsView(profile_version=profile_version(p), employments=jobs, activity_evidence=activity_evidence,
                              unattached_evidence=[evidence_view(x) for x in p.unattached_evidence], other_records=other)
