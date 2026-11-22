"""Read-only access to the synthetic reference sample for career insights.

COHORT BOUNDARIES. Every query of the synthetic cohort starts from the same explicit positive allowlist:

    trace_ref.profiles p WHERE p.dataset_class = 'synthetic_test_fixture' AND p.synthetic_demo_allowed

(the definition of data mode 'synthetic_demo' in trace_ref.v_mode_profiles, written out so it cannot drift). Nothing
"not real" or "not consented" is ever inferred: consented real references, public aggregates, uploaded fixtures,
synthetic profiles not approved for demos and any future dataset class are excluded because they do not match.
Rows are read in one REPEATABLE READ, READ ONLY transaction (the reference snapshot pattern).
"""
from contextlib import contextmanager
from typing import Dict, Iterator, List, Tuple

import psycopg
from psycopg.rows import dict_row

SYNTHETIC_DATASET_CLASS = "synthetic_test_fixture"

COHORT = """
with cohort as (
  select p.profile_id, p.dataset_class::text as dataset_class
  from trace_ref.profiles p
  where p.dataset_class = 'synthetic_test_fixture'::public.trace_dataset_class
    and p.synthetic_demo_allowed
)
"""

# The consented real reference pool: only profiles that trace_ref.v_eligible_profiles admits now (an admitted,
# de-identified contribution with a current, verified consent; withdrawn contributions drop out immediately), and only
# that dataset class. The identity mapping is never read here.
REAL_COHORT = """
with cohort as (
  select p.profile_id, p.dataset_class::text as dataset_class
  from trace_ref.v_eligible_profiles p
  where p.dataset_class = 'consented_real_reference'::public.trace_dataset_class
)
"""

_BODIES = {
    "profiles": "select profile_id, dataset_class from cohort",
    "employments": """
        select e.profile_id, e.role_id, e.employer_id from trace_ref.employments e join cohort c on c.profile_id = e.profile_id""",
    "education": """
        select d.profile_id, d.qualification_level, d.field_of_study, d.institution_name, d.grade_scale, d.grade_value, d.grade_raw
        from trace_ref.education d join cohort c on c.profile_id = d.profile_id""",
    "employers": """
        select r.profile_id, r.employer_id, r.industry_sector, r.company_model, r.company_size_band, r.employer_name
        from trace_ref.employers r join cohort c on c.profile_id = r.profile_id""",
    # Confirmed role changes only (the same transitions the route evidence uses); employer-only moves are not role moves.
    "transitions": """
        select t.profile_id, t.from_role_id, t.to_role_id, t.change_type
        from trace_ref.v_episode_transitions t join cohort c on c.profile_id = t.profile_id
        where t.transition_status = 'confirmed' and t.role_changed""",
    "paths": """
        select x.profile_id, x.segment_no, x.role_path from trace_ref.v_career_episode_paths x
        join cohort c on c.profile_id = x.profile_id order by x.profile_id, x.segment_no""",
    "areas": """
        select v.profile_id, v.responsibility_area from trace_ref.experience_evidence v join cohort c on c.profile_id = v.profile_id
        where v.responsibility_area is not null and v.evidence_status <> 'rejected'::public.trace_evidence_status""",
    "skills": """
        select s.profile_id, s.skill_id from trace_ref.profile_skills s join cohort c on c.profile_id = s.profile_id
        where s.skill_id is not null""",
    "activities": """
        select a.profile_id, a.activity_type, a.title from trace_ref.activities a join cohort c on c.profile_id = a.profile_id""",
}
COHORT_SQL = {"synthetic": COHORT, "consented_real": REAL_COHORT}
QUERIES_BY_COHORT = {key: {name: cte + body for name, body in _BODIES.items()} for key, cte in COHORT_SQL.items()}
QUERIES = QUERIES_BY_COHORT["synthetic"]

LABELS = {
    "roles": "select role_id, canonical_title from public.trace_role_taxonomy",
    "areas": "select responsibility_area, label from public.trace_responsibility_areas",
    "skills": "select skill_id, canonical_skill from public.trace_skills",
    "settings": "select setting_key, value_numeric from trace_ref.analytics_settings "
                "where setting_key in ('min_profiles_limited', 'min_profiles_moderate')",
}


def load_rows(conn: psycopg.Connection, cohort: str = "synthetic") -> Tuple[Dict[str, List[dict]], Dict[str, Dict[str, str]], Dict[str, int]]:
    """Every query of ONE cohort on the given connection (the caller owns the transaction). Read-only SQL; the cohort
    key selects fixed SQL from an allowlist (KeyError otherwise), nothing from the request is interpolated."""
    rows: Dict[str, List[dict]] = {}
    with conn.cursor(row_factory=dict_row) as cur:
        for name, sql in QUERIES_BY_COHORT[cohort].items():
            rows[name] = cur.execute(sql).fetchall()
        labels = {k: {r[0]: r[1] for r in conn.execute(LABELS[k]).fetchall()} for k in ("roles", "areas", "skills")}
        settings = {k: int(v) for k, v in conn.execute(LABELS["settings"]).fetchall()}
    return rows, labels, settings


def without_profiles(rows: Dict[str, List[dict]], profile_ids) -> Dict[str, List[dict]]:
    """The cohort rows without the given reference profiles (Phase 8.1: the caller's own contribution). Applied before
    any statistic is computed, so denominators, counts and small-cell suppression are all computed without them."""
    excluded = set(profile_ids)
    if not excluded:
        return rows
    return {name: [r for r in rs if r["profile_id"] not in excluded] for name, rs in rows.items()}


@contextmanager
def read_only_snapshot(conn: psycopg.Connection) -> Iterator[psycopg.Connection]:
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        raise RuntimeError("read_only_snapshot needs a connection with no open transaction")
    previous = (conn.isolation_level, conn.read_only, conn.autocommit)
    conn.autocommit = False
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    conn.read_only = True
    try:
        with conn.transaction():
            yield conn
    finally:
        conn.isolation_level, conn.read_only, conn.autocommit = previous
