"""Read-only access to the foundation's episode-aware evidence views and functions (v2.1.0).

Only these foundation objects are read:
  public.trace_role_taxonomy                     destination validation and role families
  trace_ref.v_career_episode_paths               confirmed episode segments (all dataset classes!) ...
  trace_ref.v_mode_profiles                      ... always joined here to restrict them to one data_mode
  trace_ref.episode_route_observations()         per-individual route matches (mode-filtered internally)
  trace_ref.episode_route_timeline_summary()     cohort counts, evidence_class, percentiles, display flags

The legacy role-code objects (v_career_paths, route_observations, route_timeline_summary) are not
used, not even as a fallback. No direct identifiers and no employer pseudonyms are selected: the
episode objects carry only pseudonymous profile_id, dataset_class, segment numbers, role ids,
change types and month indices.
"""
from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional, Protocol, Sequence

import psycopg
from psycopg.rows import dict_row

from app.services.reference_evidence.models import (
    AreaRow,
    EpisodePathRow,
    RoleRow,
    RouteObservationRow,
    RouteTimelineSummaryRow,
    StepExperienceRow,
    StepRequest,
)

ROLE_SQL = """
select t.role_id, t.canonical_title, t.role_family, t.is_pm_target, t.is_resolved, t.active,
       t.default_seniority_code as seniority_code, s.rank as seniority_rank,
       (select count(distinct t2.default_seniority_code) from public.trace_role_taxonomy t2
         where t2.role_family = t.role_family and t2.active) > 1 as family_has_levels,
       f.label as role_family_label
from public.trace_role_taxonomy t
left join public.trace_seniority_levels s on s.seniority_code = t.default_seniority_code
left join public.trace_role_families f on f.role_family = t.role_family
where t.role_id = any(%(role_ids)s::text[])
"""

# v_career_episode_paths includes every dataset class. The join to v_mode_profiles is what
# restricts it to one mode; for 'empirical' that view reads trace_ref.v_eligible_profiles
# (current consent, evaluated at query time).
MODE_EPISODE_PATHS_SQL = """
select cp.profile_id, cp.dataset_class::text as dataset_class, cp.segment_no,
       cp.role_path, cp.entry_change_types, cp.stint_start_lo, cp.stint_start_hi
from trace_ref.v_career_episode_paths cp
join trace_ref.v_mode_profiles mp
  on mp.profile_id = cp.profile_id
 and mp.data_mode = %(data_mode)s::public.trace_data_mode
where %(role_id)s = any(cp.role_path)
order by cp.profile_id, cp.segment_no
"""

EPISODE_ROUTE_OBSERVATIONS_SQL = """
select profile_id, segment_no, start_position, step_change_types,
       route_months_min, route_months_max, is_precise
from trace_ref.episode_route_observations(%(route)s::text[], %(data_mode)s::public.trace_data_mode)
"""

EPISODE_ROUTE_SUMMARY_SQL = """
select data_mode::text as data_mode, route, supporting_profile_count, dated_profile_count,
       count_display_allowed, evidence_class::text as evidence_class,
       p25_months, median_months, p75_months, coarse_min_months, coarse_max_months,
       timeline_metric, timeline_display_allowed
from trace_ref.episode_route_timeline_summary(%(route)s::text[], %(data_mode)s::public.trace_data_mode)
"""

# Responsibility areas recorded on the route steps before the destination, one row per distinct
# (profile, area). The requests name each observation's profile, segment and pre-destination stint range
# (1-based stint_no, inclusive). v_career_episode_stints includes every dataset class, so the join to
# v_mode_profiles restricts it to the requested mode, exactly as for the paths. Rejected evidence is ignored.
STEP_EXPERIENCE_SQL = """
with req as (
  select * from unnest(%(profile_ids)s::text[], %(segment_nos)s::int[], %(first_stints)s::int[], %(last_stints)s::int[])
    as r(profile_id, segment_no, first_stint, last_stint))
select distinct r.profile_id, e.responsibility_area
from req r
join trace_ref.v_mode_profiles mp
  on mp.profile_id = r.profile_id
 and mp.data_mode = %(data_mode)s::public.trace_data_mode
join trace_ref.v_career_episode_stints s
  on s.profile_id = r.profile_id and s.segment_no = r.segment_no and s.stint_no between r.first_stint and r.last_stint
join trace_ref.experience_evidence e
  on e.profile_id = s.profile_id and e.employment_id = any(s.employment_ids)
where e.responsibility_area is not null and e.evidence_status <> 'rejected'
order by 1, 2
"""

AREA_SQL = """
select responsibility_area, label, is_pm_ownership_signal
from public.trace_responsibility_areas
where responsibility_area = any(%(areas)s::text[])
"""

# The foundation's own display threshold (also used for count_display_allowed). NULL when missing.
MIN_DISPLAY_SQL = "select min_profiles_limited from trace_ref.v_settings"


class InvalidEpisodeRoute(ValueError):
    """The foundation rejected the route (SQLSTATE 22023): fewer than two steps, a null step, a
    multi-dimensional array, or an unknown or unresolved role id."""


class ReferenceEvidenceRepository(Protocol):
    def get_roles(self, role_ids: Sequence[str]) -> Dict[str, RoleRow]: ...

    def mode_episode_paths_containing(self, role_id: str, data_mode: str) -> List[EpisodePathRow]: ...

    def episode_route_observations(self, route: Sequence[str], data_mode: str) -> List[RouteObservationRow]: ...

    def episode_route_timeline_summary(self, route: Sequence[str], data_mode: str) -> Optional[RouteTimelineSummaryRow]: ...

    def route_step_experience(self, requests: Sequence[StepRequest], data_mode: str) -> List[StepExperienceRow]: ...

    def responsibility_areas(self, areas: Sequence[str]) -> Dict[str, AreaRow]: ...

    def min_display_count(self) -> Optional[float]: ...


class PostgresReferenceEvidenceRepository:
    """Runs the queries above on a caller-supplied connection. Use `reference_snapshot` so that
    every query of one retrieval sees the same eligibility state."""

    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def _rows(self, sql: str, params: dict) -> List[dict]:
        try:
            with self._conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        except psycopg.errors.InvalidParameterValue as exc:
            raise InvalidEpisodeRoute(exc.diag.message_primary or str(exc)) from exc

    def get_roles(self, role_ids: Sequence[str]) -> Dict[str, RoleRow]:
        rows = self._rows(ROLE_SQL, {"role_ids": list(role_ids)})
        return {r["role_id"]: RoleRow(**r) for r in rows}

    def mode_episode_paths_containing(self, role_id: str, data_mode: str) -> List[EpisodePathRow]:
        rows = self._rows(MODE_EPISODE_PATHS_SQL, {"role_id": role_id, "data_mode": data_mode})
        return [EpisodePathRow(**r) for r in rows]

    def episode_route_observations(self, route: Sequence[str], data_mode: str) -> List[RouteObservationRow]:
        rows = self._rows(EPISODE_ROUTE_OBSERVATIONS_SQL, {"route": list(route), "data_mode": data_mode})
        return [RouteObservationRow(**r) for r in rows]

    def episode_route_timeline_summary(self, route: Sequence[str], data_mode: str) -> Optional[RouteTimelineSummaryRow]:
        rows = self._rows(EPISODE_ROUTE_SUMMARY_SQL, {"route": list(route), "data_mode": data_mode})
        return RouteTimelineSummaryRow(**rows[0]) if rows else None

    def route_step_experience(self, requests: Sequence[StepRequest], data_mode: str) -> List[StepExperienceRow]:
        if not requests:
            return []
        rows = self._rows(STEP_EXPERIENCE_SQL, {
            "profile_ids": [q.profile_id for q in requests], "segment_nos": [q.segment_no for q in requests],
            "first_stints": [q.first_stint for q in requests], "last_stints": [q.last_stint for q in requests],
            "data_mode": data_mode})
        return [StepExperienceRow(**r) for r in rows]

    def responsibility_areas(self, areas: Sequence[str]) -> Dict[str, AreaRow]:
        return {r["responsibility_area"]: AreaRow(**r) for r in self._rows(AREA_SQL, {"areas": list(areas)})}

    def min_display_count(self) -> Optional[float]:
        rows = self._rows(MIN_DISPLAY_SQL, {})
        return None if not rows or rows[0]["min_profiles_limited"] is None else float(rows[0]["min_profiles_limited"])


@contextmanager
def reference_snapshot(conn: psycopg.Connection) -> Iterator[PostgresReferenceEvidenceRepository]:
    """A read-only REPEATABLE READ transaction: consent, eligibility and paths are read from one
    consistent snapshot, and nothing can be written."""
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        raise RuntimeError("reference_snapshot needs a connection with no open transaction")
    previous = (conn.isolation_level, conn.read_only, conn.autocommit)
    conn.autocommit = False
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    conn.read_only = True
    try:
        with conn.transaction():
            yield PostgresReferenceEvidenceRepository(conn)
    finally:
        conn.isolation_level, conn.read_only, conn.autocommit = previous
