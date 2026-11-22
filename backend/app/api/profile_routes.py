"""/api/v1, Phase 3: review, corrections, role mapping and evidence classification of the caller's own records.

Thin handlers over CandidateProfileService. Every route needs a verified bearer token; the profile comes from the
verified owner (owner_context); every operation runs in an owner session, so a record that is not the caller's is
404 (RecordNotFound), never revealed. Business rules (transitions, correction history, mapping, Product Owner
clarification, classification) stay in the services and the database.
"""
from datetime import datetime
from typing import Dict, List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.api.deps import owner_context, profile_service
from app.api.ratelimit import limit
from app.api.views import REVIEW_LABELS, ProfileRecordsView, records_view
from app.services.candidate_profile import CORRECTION_MODELS, CandidateProfileService, InvalidRecord, OwnerContext
from app.services.candidate_profile.models import RoleMappingOptions, RoleMappingResult

router = APIRouter(prefix="/api/v1", tags=["api"])
ReviewKind = Literal["employment", "education", "activity", "evidence", "skill", "career_break"]
NO_IDS = {"candidate_employment_id": True}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


def expected_version(if_match: Optional[str] = Header(default=None, alias="If-Match")) -> Optional[datetime]:
    """Optimistic concurrency (Phase 2): the record version the client read (ReviewView.version), sent as If-Match.
    Absent: no check (clients that predate versions). A stale version is refused with 409 record_changed."""
    if if_match is None or not if_match.strip():
        return None
    try:
        value = datetime.fromisoformat(if_match.strip().strip('"').replace("Z", "+00:00"))
    except ValueError:
        raise InvalidRecord("If-Match must be a record version as returned by the API") from None
    if value.tzinfo is None:
        raise InvalidRecord("If-Match must be a record version as returned by the API")
    return value


# --------------------------------------------------------------------------- read
@router.get("/profile/records", response_model=ProfileRecordsView)
def profile_records(ctx: OwnerContext = Depends(owner_context),
                    service: CandidateProfileService = Depends(profile_service)) -> ProfileRecordsView:
    return records_view(service.get_profile(ctx))


# --------------------------------------------------------------------------- review actions
class ReviewResult(_Body):
    kind: str
    status: str
    label: str


def _review(action, kind: str, record_id: UUID, ctx: OwnerContext, version: Optional[datetime] = None) -> ReviewResult:
    status = action(ctx, kind, record_id, version)
    return ReviewResult(kind=kind, status=status, label=REVIEW_LABELS.get(status, status))


@router.post("/records/{kind}/{record_id}/confirm", response_model=ReviewResult, dependencies=[Depends(limit("mutation"))])
def confirm(kind: ReviewKind, record_id: UUID, ctx: OwnerContext = Depends(owner_context),
            service: CandidateProfileService = Depends(profile_service),
            version: Optional[datetime] = Depends(expected_version)) -> ReviewResult:
    return _review(service.confirm_record, kind, record_id, ctx, version)


@router.post("/records/{kind}/{record_id}/reject", response_model=ReviewResult, dependencies=[Depends(limit("mutation"))])
def reject(kind: ReviewKind, record_id: UUID, ctx: OwnerContext = Depends(owner_context),
           service: CandidateProfileService = Depends(profile_service),
           version: Optional[datetime] = Depends(expected_version)) -> ReviewResult:
    return _review(service.reject_record, kind, record_id, ctx, version)


@router.post("/records/{kind}/{record_id}/reinstate", response_model=ReviewResult, dependencies=[Depends(limit("mutation"))])
def reinstate(kind: ReviewKind, record_id: UUID, ctx: OwnerContext = Depends(owner_context),
              service: CandidateProfileService = Depends(profile_service),
              version: Optional[datetime] = Depends(expected_version)) -> ReviewResult:
    return _review(service.reinstate_record, kind, record_id, ctx, version)


# --------------------------------------------------------------------------- corrections
class CorrectionView(_Body):
    sequence: int
    changed_fields: List[str]
    previous_values: Dict[str, object]
    corrected_values: Dict[str, object]
    prior_review_status: str
    corrected_at: datetime


@router.post("/records/{kind}/{record_id}/corrections", response_model=ReviewResult, status_code=201, dependencies=[Depends(limit("mutation"))])
def correct(kind: ReviewKind, record_id: UUID, changes: dict = Body(...), ctx: OwnerContext = Depends(owner_context),
            service: CandidateProfileService = Depends(profile_service),
            version: Optional[datetime] = Depends(expected_version)) -> ReviewResult:
    """Body: the kind's correction model (EmploymentCorrection, ...): scalar content only, unknown fields refused."""
    try:
        model = CORRECTION_MODELS[kind].model_validate(changes)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) or "body" for e in exc.errors()})
        raise InvalidRecord(f"invalid correction fields: {', '.join(fields)}") from None
    status = service.correct_record(ctx, kind, record_id, model, version)
    return ReviewResult(kind=kind, status=status, label=REVIEW_LABELS.get(status, status))


@router.get("/records/{kind}/{record_id}/corrections", response_model=List[CorrectionView])
def corrections(kind: ReviewKind, record_id: UUID, ctx: OwnerContext = Depends(owner_context),
                service: CandidateProfileService = Depends(profile_service)) -> List[CorrectionView]:
    service.require_record(ctx, kind, record_id)                     # someone else's record: 404, not an empty list
    return [CorrectionView(sequence=e.correction_seq, changed_fields=e.changed_fields, previous_values=e.previous_values,
                           corrected_values=e.corrected_values, prior_review_status=e.prior_review_status,
                           corrected_at=e.corrected_at) for e in service.correction_history(ctx, kind, record_id)]


# --------------------------------------------------------------------------- role mapping
class RoleChoiceBody(_Body):
    role_id: str = Field(min_length=1, max_length=40)
    ownership_areas: Optional[List[str]] = Field(default=None, max_length=16)


class RoleChoiceResult(_Body):
    role_id: str
    mapping_basis: Literal["candidate_selected"] = "candidate_selected"


@router.get("/employments/{employment_id}/role-options", response_model=RoleMappingOptions, response_model_exclude=NO_IDS)
def role_options(employment_id: UUID, ctx: OwnerContext = Depends(owner_context),
                 service: CandidateProfileService = Depends(profile_service)) -> RoleMappingOptions:
    return service.role_mapping_options(ctx, employment_id)


@router.post("/employments/{employment_id}/map", response_model=RoleMappingResult,
             response_model_exclude={"candidate_employment_id": True, "options": NO_IDS}, dependencies=[Depends(limit("mutation"))])
def map_role(employment_id: UUID, ctx: OwnerContext = Depends(owner_context),
             service: CandidateProfileService = Depends(profile_service)) -> RoleMappingResult:
    return service.map_employment(ctx, employment_id)


@router.post("/employments/{employment_id}/role", response_model=RoleChoiceResult, dependencies=[Depends(limit("mutation"))])
def choose_role(employment_id: UUID, payload: RoleChoiceBody, ctx: OwnerContext = Depends(owner_context),
                service: CandidateProfileService = Depends(profile_service)) -> RoleChoiceResult:
    return RoleChoiceResult(role_id=service.choose_role(ctx, employment_id, payload.role_id, payload.ownership_areas))


# --------------------------------------------------------------------------- evidence classification
class ClassificationBody(_Body):
    """Phase 3.1: areas with optional yes/no scope answers ({"<area>": {"<question>": true|false}}), or none=true for the
    user's explicit "None of these areas". An unanswered question is never read as "no"."""
    areas: List[str] = Field(default_factory=list, max_length=32)
    none: bool = False
    qualifiers: Dict[str, Dict[str, bool]] = Field(default_factory=dict)


class ClassificationResult(_Body):
    areas: List[str]
    none: bool = False
    basis: Literal["candidate_selected"] = "candidate_selected"
    state: Literal["current", "none_of_these", "cleared"] = "current"
    note: str = "Classifying evidence does not confirm it; review it separately."


@router.put("/evidence/{evidence_id}/classification", response_model=ClassificationResult, dependencies=[Depends(limit("mutation"))])
def classify(evidence_id: UUID, payload: ClassificationBody, ctx: OwnerContext = Depends(owner_context),
             service: CandidateProfileService = Depends(profile_service),
             version: Optional[datetime] = Depends(expected_version)) -> ClassificationResult:
    areas = service.classify_evidence_v2(ctx, evidence_id, payload.areas, payload.none, payload.qualifiers, version)
    return ClassificationResult(areas=areas, none=payload.none, state="none_of_these" if payload.none else "current")


@router.delete("/evidence/{evidence_id}/classification", response_model=ClassificationResult, dependencies=[Depends(limit("mutation"))])
def clear_classification(evidence_id: UUID, ctx: OwnerContext = Depends(owner_context),
                         service: CandidateProfileService = Depends(profile_service),
                         version: Optional[datetime] = Depends(expected_version)) -> ClassificationResult:
    """Phase 3.1: back to unclassified. Unclassified experience is never treated as missing."""
    service.clear_evidence_classification(ctx, evidence_id, version)
    return ClassificationResult(areas=[], state="cleared", note="Cleared. This experience is now uncategorised.")
