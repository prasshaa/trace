"""Apply the peer migrations to a real Postgres with pgvector and call peer_patterns.

Skipped unless PEER_DATABASE_URL or DATABASE_URL points at that database. The default unit job
does not set it. CI sets it to the pgvector service.
"""
import json
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = [
    ROOT / "data-foundation/supabase/migrations/20261126200000_trace_peer_cohort.sql",
    ROOT / "data-foundation/supabase/migrations/20261126223000_trace_peer_pattern_quality.sql",
    ROOT / "data-foundation/supabase/migrations/20261126230000_trace_peer_rerank_pool.sql",
    ROOT / "data-foundation/supabase/migrations/20261126233000_trace_peer_aggregates_only.sql",
]
DSN = os.environ.get("PEER_DATABASE_URL") or ""

pytestmark = pytest.mark.integration


def _vector(seed: int) -> str:
    values = [0.0] * 384
    values[seed] = 1.0
    return "[" + ",".join(f"{v:.1f}" for v in values) + "]"


def _bootstrap(conn):
    conn.execute("create schema if not exists extensions")
    conn.execute("create extension if not exists vector with schema extensions")
    for role in ("anon", "authenticated", "service_role", "trace_app_user", "trace_app_runtime"):
        conn.execute(f"do $$ begin create role {role} nologin; exception when duplicate_object then null; end $$")


def _apply(conn):
    for path in MIGRATIONS:
        conn.execute(path.read_text(encoding="utf-8"))


def _load(conn):
    """150 profiles: 50 software paths and 100 consulting paths, so a switcher has a real pool of 120."""
    rows = []
    for i in range(50):
        rows.append(("s%03d" % i, "APM", "Software Engineering / QA / IT", "internal", "APM", 3, 2, True, 2014, 90, _vector(0)))
    for i in range(100):
        rows.append(("c%03d" % i, "Product Manager", "Business / Consulting / Finance / Operations", "external", "Product Manager",
                     1, 2, False, 2018, 20, _vector(1)))
    conn.execute("delete from trace_peer_cohort.profiles where cohort_version = 'india-pm-v3'")
    for key, level, prior, move, first, n_pre, n_pm, mba, year, months, vec in rows:
        conn.execute(
            """insert into trace_peer_cohort.profiles (
                 profile_key, cohort_version, norm_seniority_group, final_primary_prior_function,
                 final_entry_move, final_first_pm_group, final_n_pre_pm_roles, final_n_pm_roles,
                 final_has_history, has_mba, norm_career_start_year,
                 final_months_to_first_pm_from_earliest_nonintern, path_text, embedding)
               values (%s, 'india-pm-v3', %s, %s, %s, %s, %s, %s, true, %s, %s, %s, %s, %s::extensions.vector)""",
            (key, level, prior, move, first, n_pre, n_pm, mba, year, months, f"{level}. {prior}.", vec))


@pytest.fixture(scope="module")
def db():
    if not DSN:
        pytest.skip("PEER_DATABASE_URL is not set")
    with psycopg.connect(DSN, autocommit=True) as conn:
        _bootstrap(conn)
        _apply(conn)
        _load(conn)
        yield conn


def test_peer_patterns_runs_and_returns_aggregates_only(db):
    user = json.dumps({
        "prior": "Software Engineering / QA / IT",
        "move": "external",
        "n_pre": 3,
        "mba": False,
        "start_year": 2014,
        "in_pm": False,
        "first_pm_level": "",
        "target_level": "APM",
    })
    row = db.execute(
        "select trace_peer_cohort.peer_patterns(%s::extensions.vector, %s::jsonb, 'india-pm-v3')",
        (_vector(0), user),
    ).fetchone()
    payload = row[0]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["pool_count"] == 120
    assert payload["available"] is True
    assert len(payload["patterns"]) >= 6
    blob = json.dumps(payload)
    for hidden in ("profile_key", "path_text", "embedding", "candidates", "norm_city"):
        assert hidden not in blob
    texts = " ".join(item["text"] for item in payload["patterns"])
    assert "compared with about" in texts
    assert ".," not in texts
    assert any(phrase in texts for phrase in (
        "came from Software Engineering", "started in product as", "had an MBA",
        "middle time", "role before product", "inside the employer"))
    old = db.execute(
        "select trace_peer_cohort.peer_patterns(%s::extensions.vector, 'APM', '{}'::text[], 120, 'india-pm-v3')",
        (_vector(0),),
    ).fetchone()[0]
    if isinstance(old, str):
        old = json.loads(old)
    assert "candidates" in old
