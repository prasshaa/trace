"""/api/v1, Phase 4: taxonomy lookups, current position and the personalised pathway result.

Thin handlers over CandidateProfileService. Every route needs a verified bearer token and the caller's own profile.
Responses are explicit API models: the current position never carries a record id, and the pathway response is
built field by field from the M6 PathwayResult, whose route models were designed for clients (no reference ids,
individual observations, supporting_profile_count, counts below five or timelines). Pathway logic is not touched.
"""
from typing import Dict, List, Optional

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from app.api.cohorts import EVIDENCE_MODE, requested_cohort
from app.api.deps import db_connection, owner_context, profile_service
from app.api.ratelimit import limit
from app.services.candidate_profile import CandidateProfileService, OwnerContext, load_sequencing_context
from app.services.candidate_profile.models import CurrentPosition
from app.services.candidate_profile.pathway import CandidateEvidenceSummary, NextStep, PathwayRoute, RouteRole
from app.services.career_history import SequencingContext

router = APIRouter(prefix="/api/v1", tags=["api"])


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- taxonomy
class TargetRole(_View):
    role_id: str
    label: str
    report_available: bool = False      # POC: a Career Intelligence Report exists for this target (an approved pack covers it)


class ResponsibilityArea(_View):
    code: str
    label: str
    is_pm_ownership_signal: bool                 # used for the Product Owner clarification
    # Phase 3.1: plain-language definition, examples, optional yes/no scope questions, and versions
    description: Optional[str] = None
    examples: List[str] = []
    questions: Dict[str, str] = {}
    introduced_in_version: int = 1
    current_taxonomy_version: int = 1


@router.get("/taxonomy/target-roles", response_model=List[TargetRole])
def target_roles(ctx: OwnerContext = Depends(owner_context), service: CandidateProfileService = Depends(profile_service)):
    from app.services.capability.service import TARGET_LEVELS
    return [TargetRole(role_id=r["role_id"], label=r["canonical_title"], report_available=r["role_id"] in TARGET_LEVELS)
            for r in service.target_roles(ctx)]


@router.get("/taxonomy/responsibility-areas", response_model=List[ResponsibilityArea])
def responsibility_areas(ctx: OwnerContext = Depends(owner_context), service: CandidateProfileService = Depends(profile_service)):
    return [ResponsibilityArea(code=r["responsibility_area"], label=r["label"], is_pm_ownership_signal=r["is_pm_ownership_signal"],
                               description=r["description"], examples=list(r["examples"] or []),
                               questions={k: v["question"] for k, v in (r["qualifiers"] or {}).items()},
                               introduced_in_version=r["introduced_in_version"], current_taxonomy_version=r["current_version"])
            for r in service.taxonomy(ctx)]


# --------------------------------------------------------------------------- current position
class PositionView(_View):
    """The current position, or why it is unknown. No record identifier."""
    status: str
    reason: Optional[str] = None
    explanation: str
    title_raw: Optional[str] = None
    role_id: Optional[str] = None
    canonical_title: Optional[str] = None
    role_family: Optional[str] = None
    seniority_code: Optional[str] = None
    mapping_basis: Optional[str] = None
    is_current_as_stated: Optional[bool] = None
    exclusion_reasons: List[str] = []


def position_view(p: CurrentPosition) -> PositionView:
    return PositionView(**p.model_dump(exclude={"candidate_employment_id"}))


def sequencing_context(conn: psycopg.Connection = Depends(db_connection)) -> SequencingContext:
    return load_sequencing_context(conn)                 # shared taxonomy and settings only; read per request


@router.get("/current-position", response_model=PositionView)
def current_position(ctx: OwnerContext = Depends(owner_context), service: CandidateProfileService = Depends(profile_service),
                     context: SequencingContext = Depends(sequencing_context)) -> PositionView:
    return position_view(service.current_position(ctx, context))


# --------------------------------------------------------------------------- pathway
class PathwayResponse(_View):
    cohort: str = "synthetic"                   # the one reference cohort the observed route evidence came from
    """The personalised pathway result. OBSERVED evidence (routes[].observed, synthetic and labelled), CANDIDATE FIT
    (routes[].fit, from the candidate's own reviewed evidence) and GENERATED guidance (next_steps) are separate."""
    current_position: PositionView
    target: RouteRole
    role_only_notice: str
    data_mode: Optional[str] = None
    is_synthetic_test_data: Optional[bool] = None
    evidence_notice: Optional[str] = None
    interpretation_note: str
    candidate_evidence: CandidateEvidenceSummary
    routes: List[PathwayRoute]
    next_steps: List[NextStep]
    limitations: List[str]
    unavailable_assessments: List[str]
    # Phase 7.2: the existing analytics setting min_profiles_moderate (the size below which a target group is not
    # described on its own). Observed counts below it are shown with a small-sample caution. It is not a reliability
    # measure and no new threshold: None when the setting is absent.
    sample_caution_below: Optional[int] = None


def _moderate_sample(conn: psycopg.Connection) -> Optional[int]:
    from app.services.career_insights.repository import read_only_snapshot
    with read_only_snapshot(conn):
        row = conn.execute("select value_numeric from trace_ref.analytics_settings where setting_key = 'min_profiles_moderate'").fetchone()
    return int(row[0]) if row and row[0] is not None else None


@router.get("/pathway", response_model=PathwayResponse, dependencies=[Depends(limit("read"))])
def pathway(target: str = Query(min_length=1, max_length=40, pattern=r"^[A-Z0-9-]+$"),
            ctx: OwnerContext = Depends(owner_context), service: CandidateProfileService = Depends(profile_service),
            context: SequencingContext = Depends(sequencing_context), cohort: str = Depends(requested_cohort),
            conn: psycopg.Connection = Depends(db_connection)) -> PathwayResponse:
    # Personal fit always comes from the caller's own reviewed profile; the observed route evidence comes from exactly
    # one reference cohort (synthetic_test or empirical data mode), never both.
    r = service.pathway_result(ctx, context, target, evidence_mode=EVIDENCE_MODE[cohort])   # 422 unless a PM target
    return PathwayResponse(cohort=cohort, current_position=position_view(r.current_position), target=r.target, role_only_notice=r.role_only_notice,
                           data_mode=r.data_mode, is_synthetic_test_data=r.is_synthetic_test_data, evidence_notice=r.evidence_notice,
                           interpretation_note=r.interpretation_note, candidate_evidence=r.candidate_evidence, routes=r.routes,
                           next_steps=r.next_steps, limitations=r.limitations, unavailable_assessments=r.unavailable_assessments,
                           sample_caution_below=_moderate_sample(conn))
