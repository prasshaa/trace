"""Reference cohorts (Phase 6): which one a request reads, from a strict allowlist that the server enables.

  synthetic        the 41 approved synthetic profiles (dataset_class synthetic_test_fixture AND synthetic_demo_allowed);
                   pathway evidence data mode 'synthetic_test'
  consented_real   the consented, de-identified real reference pool (trace_ref.v_eligible_profiles); pathway evidence
                   data mode 'empirical'

There is no combined or "all" cohort and no fallback: a cohort the server has not enabled is refused (403
cohort_not_enabled), an unknown value is 422, and an empty or small cohort is reported as such, never replaced by the
other.

Phase 7.1: the reference source is chosen by the server, not by users. Without a cohort parameter (the product
frontend never sends one) a request reads the data mode's source (mode.REFERENCE_SOURCE): 'synthetic' in
synthetic_demo, 'consented_real' in production. In production that is also the ONLY cohort: TRACE_ENABLED_COHORTS is
ignored and the synthetic cohort is refused, so a production request can never receive synthetic reference records.
In synthetic_demo, TRACE_ENABLED_COHORTS (default "synthetic") may add consented_real for engineering isolation tests.
"""
from typing import Dict, List, Literal, Optional

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from app.api.deps import current_owner, db_connection
from app.api.mode import REFERENCE_SOURCE, data_mode
from app.config import Settings, get_settings
from app.services.career_insights.repository import COHORT_SQL, read_only_snapshot
from app.services.career_insights.service import COHORTS
from app.services.candidate_profile import CandidateProfileService

CohortKey = Literal["synthetic", "consented_real"]
EVIDENCE_MODE: Dict[str, str] = {"synthetic": "synthetic_test", "consented_real": "empirical"}

router = APIRouter(prefix="/api/v1", tags=["api"])


class CohortNotEnabled(Exception):
    """The requested cohort is not enabled on this server (403)."""


def enabled_cohorts(settings: Settings, mode: str = "synthetic_demo") -> List[str]:
    if mode == "production":
        return [REFERENCE_SOURCE["production"]]                  # real contributed profiles only, whatever else is set
    return [c for c in (x.strip() for x in settings.trace_enabled_cohorts.split(",")) if c in COHORTS]


def requested_cohort(cohort: Optional[CohortKey] = Query(None), settings: Settings = Depends(get_settings),
                     mode: str = Depends(data_mode)) -> str:
    key = cohort or REFERENCE_SOURCE[mode]
    if key not in enabled_cohorts(settings, mode):
        raise CohortNotEnabled(key)
    return key


class CohortView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cohort: CohortKey
    label: str
    is_synthetic: bool
    disclosure: str
    status: Literal["ready", "insufficient", "not_ready"]
    eligible_profiles: Optional[int]          # None when fewer than min_cell (the exact small number is never shown)
    min_cell: int


@router.get("/cohorts", response_model=List[CohortView])
def cohorts(owner=Depends(current_owner), settings: Settings = Depends(get_settings), mode: str = Depends(data_mode),
            conn: psycopg.Connection = Depends(db_connection)) -> List[CohortView]:
    """User-facing pool sizes: the profiles that can actually support THIS caller's evidence. The caller's own
    contributed reference profiles (Phase 8.2) are excluded before counting, and suppression is applied afterwards.
    Responses are no-store (app/web.py), so a caller-specific count is never shared through a cache. Administrative
    monitoring (scripts) still reads the global pool."""
    own = CandidateProfileService(conn).own_reference_profile_ids(owner)
    out = []
    with read_only_snapshot(conn):
        min_cell = int(conn.execute("select value_numeric from trace_ref.analytics_settings "
                                    "where setting_key = 'min_profiles_limited'").fetchone()[0])
        for key in enabled_cohorts(settings, mode):
            n = conn.execute(COHORT_SQL[key] + "select count(*) from cohort where not (profile_id = any(%s::text[]))",
                             (own,)).fetchone()[0]
            spec = COHORTS[key]
            out.append(CohortView(cohort=key, label=spec.label, is_synthetic=spec.is_synthetic, disclosure=spec.disclosure,
                                  status="ready" if n >= min_cell else "not_ready" if n == 0 else "insufficient",
                                  eligible_profiles=n if n >= min_cell else None, min_cell=min_cell))
    return out
