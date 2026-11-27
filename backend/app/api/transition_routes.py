"""/api/v1, product Phase 4: the caller's transition plan (saved, append-only runs; D27-D30).

    POST /transition-plan?target=PM-PM[&context=...]   build and save a new run; returns it as stored (201)
    GET  /transition-plan?target=PM-PM                  the caller's latest saved run for that target (404 if none)
    GET  /transition-plan/{run_id}                       one of the caller's own runs (404 for anyone else's)

Structured data for the later Career Intelligence Report: routes with their rule outcomes, actions by stage,
employer eligibility per posting, the assessment summary and full provenance. The browser renders; it never counts,
ranks or labels. No scores (score_breakdown is always empty and not returned), no supporting-profile counts (observed
routes carry an evidence-breadth label), no reference profile ids. The existing /pathway endpoint is unchanged.
"""
import datetime as _dt
from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Path, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.capability_routes import Context
from app.api.cohorts import EVIDENCE_MODE, requested_cohort
from app.api.deps import db_connection, owner_context
from app.api.pathway_routes import sequencing_context
from app.api.ratelimit import limit
from app.services.candidate_profile import OwnerContext
from app.services.career_history import SequencingContext
from app.services.transition.service import TransitionPlanService
from app.services.transition.summary import STAGE_SUBTITLES, STAGE_TITLES, report_summary

router = APIRouter(prefix="/api/v1", tags=["api"])
TARGET = Query(min_length=1, max_length=40, pattern=r"^[A-Z0-9-]+$")
# Canonical stage names (POC iteration): the product framework's Build, Demonstrate, Reposition, Hiring transition, each
# with an explanatory subtitle. Stage 0 holds profile tasks and is never presented as a stage of the plan.
STAGES = STAGE_TITLES


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionView(_View):
    stage: int
    kind: Literal["resolve", "build", "demonstrate", "reposition", "apply"]
    template_key: str
    requirement_key: Optional[str] = None
    objective: str
    action: str
    why_for_you: str
    deliverable: str
    where: List[str]
    source_keys: List[str]
    record_refs: List[str]                       # the caller's own records the action refers to
    labels: List[str] = []                       # learning_exercise, personal_project_possible, factual_accuracy, trace_curated


class StageView(_View):
    stage: int
    title: str
    subtitle: str
    actions: List[ActionView]


class StepView(_View):
    role_id: str
    title: str


class RouteEvidenceView(_View):
    kind: str
    summary: str
    source_key: Optional[str] = None
    publisher: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    attribution: Optional[str] = None


class RouteView(_View):
    position: int                                # stable display order (primary first); not a score
    route_kind: Literal["direct", "bridge", "observed"]
    route_basis: Literal["profile_led", "observed"]
    primary_status: Literal["primary", "tied_primary", "alternative", "not_recommended", "not_evaluated"]
    bridge_role_id: Optional[str] = None
    steps: List[StepView]
    reason: Optional[str] = None
    explanation: Optional[str] = None
    feasibility: Dict[str, Any]
    tradeoffs: List[Dict[str, Any]]
    evidence_breadth_label: Optional[str] = None
    is_synthetic_test_data: bool
    limitations: List[str]
    evidence: List[RouteEvidenceView]
    stages: List[StageView]


class RoleRef(_View):
    role_id: str
    title: str


class SummaryItem(_View):
    key: str
    title: str
    decision_rights: bool
    reason: Optional[str] = None                 # unknown items: why (never treated as a gap)


class SelectedRoute(_View):
    position: int
    route_kind: Literal["direct", "bridge", "observed"]
    bridge_role_id: Optional[str] = None
    steps: List[str]


class SummaryDecision(_View):
    rule: Optional[str] = None
    kind: Literal["resolve_first", "direct", "bridge", "direct_conditional"]
    reason: Optional[str] = None
    selected: List[SelectedRoute]                # empty when the planner chose no primary (resolve first)
    detailed_route_position: Optional[int] = None


class SummaryPriority(_View):
    gaps: List[SummaryItem]
    transferable: List[SummaryItem]
    unknown: List[SummaryItem]


class StagePreview(_View):
    stage: int
    title: str
    subtitle: str
    action_count: int
    first_objective: Optional[str] = None


class DevelopmentItem(_View):
    key: str
    title: str
    remaining: Optional[str] = None             # partly developed: what the bridge leaves incomplete
    seek: Optional[str] = None                  # partly developed: the responsibility to seek


class DevelopmentGroup(_View):
    fully: List[DevelopmentItem]
    partly: List[DevelopmentItem]
    not_developed: List[DevelopmentItem]


class Development(_View):
    """The selected bridge route's own requirement-level development, for the missing requirements (core = decision rights)."""
    core: DevelopmentGroup
    practice: DevelopmentGroup


class HistoryRoute(_View):
    steps: List[str]
    breadth: Literal["limited", "growing", "broad", "extensive"]


class CareerHistories(_View):
    """Empirical routes past the privacy minimum only (D4 label, never a count); synthetic data never appears here."""
    status: Literal["shown", "not_enough_evidence"]
    routes: List[HistoryRoute]
    synthetic_mode: bool


class Freshness(_View):
    current: bool
    changed: List[str]


class ReportSummary(_View):
    """Product Phase 5: a presentation projection of the stored plan (no decision of its own; see summary.py)."""
    current_role: Optional[RoleRef] = None
    target_role: RoleRef
    decision: SummaryDecision
    strengths: List[SummaryItem]
    priority: SummaryPriority
    resolve_first: bool
    stage_previews: List[StagePreview]
    development: Optional[Development] = None
    career_histories: CareerHistories


class PlanView(_View):
    run_id: UUID
    target_role_id: str
    status: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    reference_cohort_data_mode: str
    versions: Dict[str, Any]
    constraints: Dict[str, Any]
    decision: Dict[str, Any]
    assessment: Dict[str, Any]
    eligibility: Dict[str, Any]
    source_versions: Dict[str, Any]
    privacy_state: Dict[str, Any]
    routes: List[RouteView]
    summary: ReportSummary
    freshness: Optional[Freshness] = None       # GET latest only: whether every decision input is still current
    separation_note: str = ("Three separate views: whether a route suits you (from your confirmed experience), how much observed "
                            "career-history evidence exists (shown as a label, never used to choose), and each employer's stated "
                            "criteria (never changes your routes).")


def plan_view(run: dict) -> PlanView:
    synthetic = run["data_mode"] != "empirical"
    routes = []
    for r in run["routes"]:
        stages = {}
        for a in r["actions"]:
            stages.setdefault(a["stage"], []).append(ActionView(
                stage=a["stage"], kind=a["kind"], template_key=a["template_key"], requirement_key=a["requirement_key"],
                objective=a["objective"], action=a["action_text"], why_for_you=a["why_for_you"], deliverable=a["deliverable"],
                where=list(a["where_options"]), source_keys=list(a["source_keys"]), record_refs=list(a["record_refs"]),
                labels=list(a.get("labels") or [])))
        routes.append(RouteView(
            position=r["rank"], route_kind=r["route_kind"], route_basis=r["route_basis"], primary_status=r["primary_status"],
            bridge_role_id=r["bridge_role_id"], steps=[StepView(**s) for s in r["steps"]], reason=r["recommendation_reason"],
            explanation=r["explanation"], feasibility=r["feasibility"], tradeoffs=r["tradeoffs"],
            evidence_breadth_label=r["evidence_breadth_label"], is_synthetic_test_data=synthetic and r["route_basis"] == "observed",
            limitations=list(r["limitations"]),
            evidence=[RouteEvidenceView(kind=e["evidence_type"], summary=e["evidence_summary"], source_key=e["source_key"],
                                        publisher=e["company_name"], title=e["role_title"], url=e["source_url"], attribution=e["attribution"])
                      for e in r["evidence"]],
            stages=[StageView(stage=s, title=STAGES[s], subtitle=STAGE_SUBTITLES[s], actions=stages[s]) for s in sorted(stages)]))
    return PlanView(
        run_id=run["pathway_run_id"], target_role_id=run["target_role_id"], status=run["status"], created_at=run["created_at"],
        completed_at=run["completed_at"], reference_cohort_data_mode=run["data_mode"],
        versions={"engine": run["engine_version"], "profile_version": run["profile_version"], "knowledge_pack": run["knowledge_pack"],
                  "source_inventory": run["source_inventory"], "taxonomy_version": run["taxonomy_version"],
                  "bridge_catalogue": run["bridge_catalogue_version"], "action_templates": run["action_template_version"],
                  "eligibility_rules": run["eligibility_rules_version"]},
        constraints=run["constraints_json"], decision=run["decision"], assessment=run["assessment"], eligibility=run["eligibility"],
        source_versions=run["source_versions"], privacy_state=run["privacy_state"], routes=routes,
        summary=ReportSummary(**report_summary(run)))


def _with_freshness(service: "TransitionPlanService", ctx: OwnerContext, run: dict, cohort: str) -> "PlanView":
    """Every plan response says whether the run still reflects all current decision inputs (the run is never altered)."""
    view = plan_view(run)
    view.freshness = Freshness(**service.freshness(ctx, run, EVIDENCE_MODE[cohort]))
    return view


def _service(conn: psycopg.Connection = Depends(db_connection)) -> TransitionPlanService:
    return TransitionPlanService(conn)


@router.post("/transition-plan", response_model=PlanView, status_code=201, dependencies=[Depends(limit("mutation"))])
def create_plan(response: Response, target: str = TARGET, context: List[Context] = Query(default=[]),
                scope: Optional[Literal["portfolio"]] = Query(default=None),
                ctx: OwnerContext = Depends(owner_context), service: TransitionPlanService = Depends(_service),
                sequencing: SequencingContext = Depends(sequencing_context), cohort: str = Depends(requested_cohort)) -> PlanView:
    response.headers["Cache-Control"] = "no-store"
    # scope=portfolio: the user states that the target role has portfolio scope (D31: only then does L1 count for routes)
    run = service.create_plan(ctx, sequencing, target, context, EVIDENCE_MODE[cohort], portfolio_scope=scope == "portfolio")
    return _with_freshness(service, ctx, run, cohort)


@router.get("/transition-plan", response_model=PlanView, dependencies=[Depends(limit("read"))])
def latest_plan(response: Response, target: str = TARGET, ctx: OwnerContext = Depends(owner_context),
                service: TransitionPlanService = Depends(_service), cohort: str = Depends(requested_cohort)) -> PlanView:
    """The latest saved run, with `freshness`: whether the confirmed profile, the reference data mode, every knowledge and
    rule version and every posting status are unchanged since it was made. A stale run is never altered; the client saves
    a new run (POST) with the same target and stated constraints."""
    response.headers["Cache-Control"] = "no-store"
    return _with_freshness(service, ctx, service.latest(ctx, target), cohort)


@router.get("/transition-plan/{run_id}", response_model=PlanView, dependencies=[Depends(limit("read"))])
def get_plan(response: Response, run_id: UUID = Path(), ctx: OwnerContext = Depends(owner_context), cohort: str = Depends(requested_cohort),
             service: TransitionPlanService = Depends(_service)) -> PlanView:
    response.headers["Cache-Control"] = "no-store"
    return _with_freshness(service, ctx, service.plan(ctx, run_id), cohort)


# --------------------------------------------------------------------------- the career transition roadmap (redesign)
Timeframe = Literal["within_3_months", "within_6_months", "within_12_months", "within_18_24_months", "flexible"]
WeeklyHours = Literal["1_3", "4_6", "7_10", "more_than_10", "varies"]


class GoalView(_View):
    """The user's planning goal for a target role: an ambition, never a forecast and never a plan input."""
    target_role_id: str
    timeframe: Optional[Timeframe] = None
    target_date: Optional[date] = None
    weekly_hours: Optional[WeeklyHours] = None
    checkpoint_dates: Dict[Literal["1", "2", "3", "4"], date] = {}
    goal_set_on: Optional[date] = None
    updated_at: Optional[datetime] = None


class GoalUpdate(_View):
    timeframe: Optional[Timeframe] = None
    target_date: Optional[date] = None
    weekly_hours: Optional[WeeklyHours] = None
    checkpoint_dates: Dict[Literal["1", "2", "3", "4"], date] = {}


class EvidenceRef(_View):
    kind: Literal["evidence", "skill", "role"]
    text: str
    source: Optional[str] = None                 # the confirmed job (or activity) the line belongs to
    categorised_as: List[str]                    # the user's categorisation, or what Trace's reading clearly shows
    areas: List[str] = []
    related_areas: List[str] = []                # Trace's reading: related but not clear (needs more context)
    why: Dict[str, Dict[str, str]] = {}          # Trace's reading: {area label: {cue, why}}
    basis: Literal["confirmed_line_categorised_by_you", "read_by_trace", "confirmed_skill", "confirmed_role"]


class Strength(_View):
    key: str
    title: str
    basis: Optional[str] = None
    evidence: List[EvidenceRef]


class Related(_View):
    key: str
    title: str
    evidence: List[EvidenceRef]
    carries_over: Optional[str] = None
    does_not_establish: str


class Focus(_View):
    key: str
    title: str
    state: Literal["gap", "transferable"]
    why_it_matters: Optional[str] = None
    how: Optional[str] = None
    where: List[str]


class NotAssessed(_View):
    key: str
    title: str
    reason: Optional[str] = None


class RouteStep(_View):
    title: str
    position: Literal["current", "intermediate", "target"]


class Recommendation(_View):
    kind: Literal["resolve_first", "direct", "bridge", "direct_conditional"]
    headline: str
    route: List[RouteStep]
    reasons: List[str]
    provides: Optional[str] = None
    tradeoff: Optional[str] = None
    why_path: List[str]


class RoadmapAction(_View):
    stage: int
    kind: str
    template_key: str
    requirement_key: Optional[str] = None
    title: str
    what_to_do: str
    why: Optional[str] = None
    where: List[str]
    produce: Optional[str] = None
    keep: Optional[str] = None                   # Trace-curated guidance on the evidence to retain
    labels: List[str]
    source_keys: List[str]


class RoadmapStage(_View):
    stage: int
    title: str
    subtitle: str
    highlighted: List[RoadmapAction]
    more: List[RoadmapAction]
    empty_note: Optional[str] = None


class Checkpoint(_View):
    stage: int
    title: str
    have: List[str]
    date: Optional[_dt.date] = None              # only ever a date the USER set
    after_goal: bool


class GoalWindow(_View):
    from_: date = Field(alias="from")
    to: date


class DurationEvidence(_View):
    status: Literal["not_available"]
    reason: Literal["synthetic_data", "no_approved_duration_evidence"]


class Timeline(_View):
    timeframe: Optional[Timeframe] = None
    timeframe_label: Optional[str] = None
    target_date: Optional[date] = None
    goal_window: Optional[GoalWindow] = None
    weekly_hours: Optional[WeeklyHours] = None
    weekly_label: Optional[str] = None
    highlight_per_stage: int
    note: str
    duration_evidence: DurationEvidence


class Alternative(_View):
    steps: List[str]
    tradeoff: str


class ResolveTask(_View):
    title: str
    what_to_do: str
    why: Optional[str] = None


class RoadmapView(_View):
    run_id: UUID
    target_role_id: str
    current_role: Optional[str] = None
    target_role: str
    recommendation: Recommendation
    build_on: List[Strength]
    focus: List[Focus]
    related: List[Related]
    not_assessed: List[NotAssessed]
    resolve_first: List[ResolveTask]
    stages: List[RoadmapStage]
    checkpoints: List[Checkpoint]
    timeline: Timeline
    alternatives: List[Alternative]
    career_histories: CareerHistories
    data_mode: str
    # the detailed layer (learning pack pm-learning-v1; see learning.py): curated guidance and verified resources
    experience: Dict[str, Any]
    development: List[Dict[str, Any]]
    sequence: Dict[str, Any]
    stage_guidance: Dict[str, Any]
    learning_version: str
    # Phase 5 (D50, D51): each capability's plain-language state with the user's records behind it, and the kind of move
    capabilities: List[Dict[str, Any]] = []
    move: Dict[str, Any] = {}
    readiness: Dict[str, Any] = {}
    next_actions: List[Dict[str, Any]] = []
    # deep: the four assessed moves. limited: evidence and positioning, never a capability status.
    support: Dict[str, Any] = {}
    # Present when the AI demo path ran or explained why it did not. Absent on older saved responses only.
    ai: Dict[str, Any] = {}
    # Group patterns from the closest de-identified career paths. Empty unless TRACE_PEER_MATCHING=1.
    peers: Dict[str, Any] = {}


def _goal_view(target: str, g: Optional[dict]) -> GoalView:
    return GoalView(target_role_id=target, **(g or {}))


@router.get("/transition-goal", response_model=GoalView, dependencies=[Depends(limit("read"))])
def get_goal(response: Response, target: str = TARGET, ctx: OwnerContext = Depends(owner_context),
             service: TransitionPlanService = Depends(_service)) -> GoalView:
    response.headers["Cache-Control"] = "no-store"
    return _goal_view(target, service.goal(ctx, target))


@router.put("/transition-goal", response_model=GoalView, dependencies=[Depends(limit("mutation"))])
def put_goal(payload: GoalUpdate, response: Response, target: str = TARGET, ctx: OwnerContext = Depends(owner_context),
             service: TransitionPlanService = Depends(_service)) -> GoalView:
    """Set the planning goal. Never creates a plan run and never changes a route or capability state."""
    response.headers["Cache-Control"] = "no-store"
    dates = {k: v.isoformat() for k, v in payload.checkpoint_dates.items()}
    return _goal_view(target, service.set_goal(ctx, target, payload.timeframe, payload.target_date, payload.weekly_hours, dates))


@router.get("/ai-narrative", dependencies=[Depends(limit("read"))])
def ai_narrative(response: Response, target: str = TARGET, ctx: OwnerContext = Depends(owner_context)) -> Dict[str, Any]:
    """The in-progress or finished model reading for this target. Pending means the call is still running."""
    from app.services.ai_assessment.jobs import lookup
    response.headers["Cache-Control"] = "no-store"
    found = lookup(str(ctx.owner_user_id), target)
    return found or {"status": "not_started"}


@router.get("/transition-plan/{run_id}/roadmap", response_model=RoadmapView, response_model_by_alias=True,
            dependencies=[Depends(limit("read"))])
def get_roadmap(response: Response, run_id: UUID = Path(), ctx: OwnerContext = Depends(owner_context),
                service: TransitionPlanService = Depends(_service)) -> RoadmapView:
    response.headers["Cache-Control"] = "no-store"
    r = service.roadmap(ctx, run_id)
    w = r["timeline"]["goal_window"]
    if w:
        r["timeline"]["goal_window"] = {"from": w["from"], "to": w["to"]}
    return RoadmapView(**r)



class LatestPlanRef(_View):
    run_id: UUID
    target_role_id: str
    created_at: datetime


@router.get("/transition-plans/latest", response_model=LatestPlanRef, dependencies=[Depends(limit("read"))],
            responses={204: {"description": "No saved plan yet"}})
def latest_any_plan(response: Response, ctx: OwnerContext = Depends(owner_context),
                    service: TransitionPlanService = Depends(_service)):
    """The caller's most recent saved plan for any target: where a returning user lands. 204 when there is none (asked on
    every page load, so 'no plan yet' is a normal answer, not an error)."""
    from app.services.transition.service import PlanNotFound
    try:
        r = service.latest_any(ctx)
    except PlanNotFound:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.headers["Cache-Control"] = "no-store"
    return LatestPlanRef(run_id=r["pathway_run_id"], target_role_id=r["target_role_id"], created_at=r["created_at"])
