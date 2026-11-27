"""/api/v1, Phase 6: the two consent purposes, kept separate.

  personal_analysis    (purpose resume_processing)            Trace analyses the caller's own profile for them.
                       Existing endpoints: GET/POST /api/v1/consent/resume-processing.
  shared_contribution  (purpose deidentified_shared_dataset)  A de-identified copy of the caller's career history may
                       join the shared reference pool. GET/POST /api/v1/consent/contribution.

GET /api/v1/consent shows both side by side. Each decision is the caller's own explicit action on the current
document version; none is inferred from an upload, from the other purpose or from terms. The contribution policy is
server-controlled (TRACE_CONTRIBUTION_POLICY): "opt_in" (default), "disabled" (the notice records private; a grant
is refused), or "anonymous" (the sign-up notice records shared use to improve recommendations, and a grant is still
refused, so nothing is queued for a shared pool). Denial and withdrawal are always accepted. Any other value fails
closed. Withdrawal excludes and deletes the caller's contributed reference profiles in the same transaction.
"""
from typing import List, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.api.deps import current_owner, profile_service
from app.api.ratelimit import limit
from app.api.routes import ConsentDecision, ConsentResponse, _consent_view
from app.config import Settings, get_settings
from app.services.candidate_profile import CandidateProfileService
from app.services.candidate_profile.repository import CandidateProfileError

router = APIRouter(prefix="/api/v1", tags=["api"])
CONTRIBUTION = "deidentified_shared_dataset"
POLICIES = ("opt_in", "disabled", "anonymous")


class ContributionPolicyUnavailable(Exception):
    """The configured contribution policy is unknown (503). Nothing is offered or recorded."""


class ContributionNotOffered(CandidateProfileError):
    """Shared contribution is disabled by policy: a grant is refused (403)."""


def contribution_policy(settings: Settings = Depends(get_settings)) -> str:
    if settings.trace_contribution_policy not in POLICIES:
        raise ContributionPolicyUnavailable("unsupported contribution policy")
    return settings.trace_contribution_policy


class PurposeView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: Literal["personal_analysis", "shared_contribution"]
    title: str
    summary: str
    required_for_personal_pathways: bool
    offered: bool
    consent: ConsentResponse


class ConsentOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contribution_policy: Literal["opt_in", "disabled", "anonymous"]
    purposes: List[PurposeView]
    note: str = "Each purpose is a separate decision. Neither is implied by the other, by uploading or by any terms."


@router.get("/consent", response_model=ConsentOverview)
def consent_overview(owner: UUID = Depends(current_owner), service: CandidateProfileService = Depends(profile_service),
                     policy: str = Depends(contribution_policy)) -> ConsentOverview:
    return ConsentOverview(contribution_policy=policy, purposes=[
        PurposeView(key="personal_analysis", title="Analyse my profile for me",
                    summary="Trace processes your resume and profile to build your own pathways and insights.",
                    required_for_personal_pathways=True, offered=True,
                    consent=_consent_view(service.consent_state(owner, "resume_processing"), "resume_processing")),
        PurposeView(key="shared_contribution", title="Contribute my de-identified career history",
                    summary="A de-identified copy of your career history helps build shared career statistics and "
                            "recommendations for others. Optional.",
                    required_for_personal_pathways=False, offered=policy == "opt_in",
                    consent=_consent_view(service.consent_state(owner, CONTRIBUTION), CONTRIBUTION)),
    ])


@router.get("/consent/contribution", response_model=ConsentResponse)
def get_contribution(owner: UUID = Depends(current_owner), service: CandidateProfileService = Depends(profile_service),
                     policy: str = Depends(contribution_policy)) -> ConsentResponse:
    return _consent_view(service.consent_state(owner, CONTRIBUTION), CONTRIBUTION)


@router.post("/consent/contribution", response_model=ConsentResponse, dependencies=[Depends(limit("mutation"))])
def post_contribution(payload: ConsentDecision, owner: UUID = Depends(current_owner),
                      service: CandidateProfileService = Depends(profile_service),
                      policy: str = Depends(contribution_policy)) -> ConsentResponse:
    """The caller's own explicit decision for the current contribution notice. A grant needs the opt-in policy."""
    if payload.decision == "granted" and policy != "opt_in":
        raise ContributionNotOffered("contributing to the shared pool is not offered at the moment")
    return _consent_view(service.record_consent(owner, payload.decision, payload.consent_version, CONTRIBUTION), CONTRIBUTION)
