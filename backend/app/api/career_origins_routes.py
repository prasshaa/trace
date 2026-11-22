"""/api/v1/career-origins: Career Origins & Transitions (internal preview, off by default).

GET /api/v1/career-origins[?plan=<run id>]: read-only aggregates from the frozen India PM research cohort (v3 final),
with definitions, denominators, sample-size flags, suppression and limitations. With ?plan=, the caller's own saved plan
supplies the role family of their confirmed current position, used only to point at the matching research group.
The research never changes the plan, its ranking or its route decision.

404 unless TRACE_CAREER_ORIGINS=1. Verified bearer token and own profile required; rate limited (read bucket). An
unreadable or unexpected artifact gives status "unavailable" and nothing else: never a substitute data source.

The response is an explicit allowlist (app/services/career_origins/schema.py), typed to the field, extra="forbid".
Null fields are dropped, so a banded or suppressed group carries its band label and nothing else. The only parameter
is `plan` (a UUID of the caller's own run); it selects which precomputed group is pointed at and never filters,
recounts or narrows the aggregates, so no request can produce a figure the artifact doesn't already show.
The artifact's internal provenance block is never served. Cache-Control: private, no-store.
"""
from typing import Dict, List, Literal, Optional
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict

from app.api.deps import db_connection, owner_context
from app.api.ratelimit import limit
from app.config import Settings, get_settings
from app.services.candidate_profile import OwnerContext
from app.services.career_origins import (
    ARTIFACT_PATH, ArtifactUnavailable, CareerOriginsDisabled, connection, load_artifact, public,
)
from app.services.career_origins.schema import (
    Connection, DefinitionKey, DisplayPolicy, Fact, FactKey, Metric, MetricKey, Narrative, Progression, Research,
    RoutesByBackground, TimeToFirstPM,
)
from app.services.career_origins.service import narrative, own_route
from app.services.candidate_profile import CandidateProfileService
from app.services.transition.service import TransitionPlanService

router = APIRouter(prefix="/api/v1", tags=["api"])


class CareerOriginsView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["available", "unavailable"]
    research: Optional[Research] = None
    display_policy: Optional[DisplayPolicy] = None
    definitions: Optional[Dict[DefinitionKey, str]] = None
    limitations: List[str] = []
    coverage: List[Fact] = []
    facts: Dict[FactKey, Fact] = {}
    metrics: Dict[MetricKey, Metric] = {}
    time_to_first_pm: Optional[TimeToFirstPM] = None
    progression: Optional[Progression] = None
    routes_by_background: Optional[RoutesByBackground] = None
    artifact_version: Optional[str] = None
    generated_at: Optional[str] = None
    connection: Optional[Connection] = None
    narrative: Optional[Narrative] = None           # Phase 5: how the research relates to this plan's starting point


def enabled(settings: Settings = Depends(get_settings)) -> Settings:
    if not settings.trace_career_origins:
        raise CareerOriginsDisabled("this feature isn't available")
    return settings


@router.get("/career-origins", response_model=CareerOriginsView, response_model_exclude_none=True,
            dependencies=[Depends(limit("read"))])
def career_origins(response: Response, plan: Optional[UUID] = Query(default=None), settings: Settings = Depends(enabled),
                   ctx: OwnerContext = Depends(owner_context),
                   conn: psycopg.Connection = Depends(db_connection)) -> CareerOriginsView:
    response.headers["Cache-Control"] = "private, no-store"
    try:
        a = load_artifact(settings.trace_career_origins_artifact or ARTIFACT_PATH)
    except ArtifactUnavailable:
        return CareerOriginsView(status="unavailable")
    link = story = None
    if plan is not None:
        run = TransitionPlanService(conn).plan(ctx, plan)                   # the caller's own run, or 404
        position = (run.get("decision") or {}).get("current_position")
        link = connection(a, position)
        chosen = next((r for r in run["routes"] if r["primary_status"] in ("primary", "tied_primary")), None)
        route = [x.get("role_id") if isinstance(x, dict) else x for x in (chosen or {}).get("steps", [])]
        mine = own_route(CandidateProfileService(conn).get_profile(ctx))  # the caller's own confirmed history; not stored
        story = Narrative.model_validate(narrative(a, position, run["target_role_id"], mine, route))
    p = public(a)                                                           # never the internal block
    return CareerOriginsView(
        status="available", research=p["research"], display_policy=p["display_policy"], definitions=p["definitions"],
        limitations=p["limitations"], coverage=p["coverage"], facts=p["facts"], metrics=p["metrics"],
        time_to_first_pm=p["time_to_first_pm"], progression=p["progression"],
        routes_by_background=p.get("routes_by_background"), artifact_version=p["artifact_version"],
        generated_at=p["generated_at"], connection=link, narrative=story)
