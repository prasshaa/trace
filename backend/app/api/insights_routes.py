"""/api/v1, Phase 5: career insights over the synthetic reference sample.

GET /api/v1/insights?target=<role_id>[&cohort=synthetic|consented_real]: read-only distributions (education, employers
and industries, transitions, responsibility areas and skills) over ONE reference cohort (default synthetic; never
combined), with denominators and small-cell suppression, plus
markers for categories present in the caller's own reviewed records. Verified bearer token and own profile required.
The target must be one of the nine PM targets. Rate limited (read bucket).
"""
import psycopg
from fastapi import APIRouter, Depends, Query

from app.api.cohorts import requested_cohort
from app.api.deps import db_connection, owner_context, profile_service
from app.api.mode import data_mode
from app.api.ratelimit import limit
from app.services.candidate_profile import CandidateProfileService, OwnerContext
from app.services.candidate_profile.repository import InvalidReference
from app.services.career_insights import CareerInsights, build_insights, load_rows, read_only_snapshot, your_profile
from app.services.career_insights.repository import without_profiles

router = APIRouter(prefix="/api/v1", tags=["api"])


@router.get("/insights", response_model=CareerInsights, dependencies=[Depends(limit("read"))])
def insights(target: str = Query(min_length=1, max_length=40, pattern=r"^[A-Z0-9-]+$"), mode: str = Depends(data_mode),
             cohort: str = Depends(requested_cohort),
             ctx: OwnerContext = Depends(owner_context), service: CandidateProfileService = Depends(profile_service),
             conn: psycopg.Connection = Depends(db_connection)) -> CareerInsights:
    targets = {r["role_id"]: r["canonical_title"] for r in service.target_roles(ctx)}
    if target not in targets:
        raise InvalidReference(f"{target!r} is not one of the PM target roles")
    mine = your_profile(service.get_profile(ctx))
    own = service.own_reference_profile_ids(ctx.owner_user_id)      # never counted as evidence for the caller
    with read_only_snapshot(conn):
        rows, labels, settings = load_rows(conn, cohort)
    rows = without_profiles(rows, own)
    return build_insights(rows, labels, settings, target, targets[target], mine, data_mode=mode, cohort_key=cohort)
