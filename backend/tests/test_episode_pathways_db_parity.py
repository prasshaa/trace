"""Parity: the foundation's episode-aware SQL objects (migration 20261111130000) give the same
answers as their Python specification in data-foundation/scripts/trace_model.py.

Read-only against the local database; uses only the 41 seeded synthetic fixtures, which prove
rule parity and are never career evidence. Skipped without a local database.
"""
import psycopg
import pytest

from app import db
from app.config import get_settings
from tests.career_history_support import load_trace_model, oracle_context, requires_foundation, synthetic_profiles

pytestmark = [pytest.mark.integration, requires_foundation]


@pytest.fixture(scope="module")
def conn():
    settings = get_settings()
    if settings.database_url is None:
        pytest.skip("DATABASE_URL not configured")
    try:
        c = db.connect(settings)
    except Exception as exc:
        pytest.skip(f"local database unreachable: {type(exc).__name__}")
    c.read_only = True
    c.autocommit = True
    with c:
        if not c.execute("select to_regprocedure('trace_ref.episode_route_observations(text[], public.trace_data_mode)')").fetchone()[0]:
            pytest.skip("episode pathway migration not applied to this database")
        yield c


@pytest.fixture(scope="module")
def tm():
    return load_trace_model()


@pytest.fixture(scope="module")
def octx(tm):
    return oracle_context(tm)


@pytest.fixture(scope="module")
def profiles():
    return synthetic_profiles()


def rows(conn, sql, *params):
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql, params or None)
        return cur.fetchall()


def test_episode_transitions_match(conn, tm, octx, profiles):
    got = {(r["from_employment_id"], r["to_employment_id"]): r for r in rows(
        conn, "select * from trace_ref.v_episode_transitions where dataset_class = 'synthetic_test_fixture'")}
    expected = [t for p in profiles for t in tm.episode_transitions(p, octx)]
    assert len(got) == len(expected) == 63
    for t in expected:
        r = got[(t["from_employment_id"], t["to_employment_id"])]
        for k in ("change_type", "is_pathway_move", "role_changed", "employer_changed", "transition_status",
                  "timing_relation", "move_type"):
            assert r[k] == t[k], (t["from_employment_id"], k)


def test_episode_paths_match(conn, tm, octx, profiles):
    got = {(r["profile_id"], r["segment_no"]): r for r in rows(
        conn, "select * from trace_ref.v_career_episode_paths where dataset_class = 'synthetic_test_fixture'")}
    expected = {(p["profile_id"], x["segment_no"]): x for p in profiles for x in tm.career_episode_paths(p, octx)}
    assert set(got) == set(expected)
    for key, x in expected.items():
        r = got[key]
        assert r["role_path"] == x["role_path"], key
        assert r["entry_change_types"] == x["entry_change_types"], key
        assert r["stint_start_lo"] == [s["start_lo"] for s in x["stints"]], key
        assert r["stint_start_hi"] == [s["start_hi"] for s in x["stints"]], key


def all_routes(tm, octx, profiles):
    routes = set()
    for p in profiles:
        for x in tm.career_episode_paths(p, octx):
            rp = x["role_path"]
            for i in range(len(rp)):
                for j in range(i + 2, len(rp) + 1):
                    routes.add(tuple(rp[i:j]))
    return sorted(routes)


def test_route_observations_and_summaries_match_for_every_observed_route(conn, tm, octx, profiles):
    routes = all_routes(tm, octx, profiles)
    assert ("PM-PM", "PM-PM") in routes and ("PM-DIR", "PM-DIR") in routes
    for route in routes:
        for mode in ("synthetic_test", "empirical"):
            got = {r["profile_id"]: r for r in rows(
                conn, "select * from trace_ref.episode_route_observations(%s::text[], %s::public.trace_data_mode)", list(route), mode)}
            expected = {}
            if mode == "synthetic_test":
                for p in profiles:
                    o = tm.episode_route_observation(p, list(route), octx)
                    if o:
                        expected[p["profile_id"]] = o
            assert set(got) == set(expected), (route, mode)
            for pid, o in expected.items():
                r = got[pid]
                for k in ("segment_no", "start_position", "step_change_types", "route_months_min", "route_months_max", "is_precise"):
                    assert r[k] == o[k], (route, pid, k)

            (s,) = rows(conn, "select * from trace_ref.episode_route_timeline_summary(%s::text[], %s::public.trace_data_mode)",
                        list(route), mode)
            e = tm.episode_route_summary(profiles, list(route), mode, octx, consent_active=lambda p: True)
            for k in ("route", "supporting_profile_count", "dated_profile_count", "count_display_allowed",
                      "evidence_class", "coarse_min_months", "coarse_max_months", "timeline_display_allowed"):
                assert s[k] == e[k], (route, mode, k)
            for k in ("p25_months", "median_months", "p75_months"):
                assert (s[k] is None) == (e[k] is None) and (s[k] is None or float(s[k]) == pytest.approx(e[k])), (route, k)


@pytest.mark.parametrize("route", [["PM-PM"], ["PM-PM", "PM-NOPE"], ["ROLE-UNKNOWN", "PM-PM"]])
def test_invalid_routes_are_rejected_by_both(conn, tm, octx, profiles, route):
    with pytest.raises(ValueError):
        tm.episode_route_summary(profiles, route, "synthetic_test", octx)
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        conn.execute("select * from trace_ref.episode_route_timeline_summary(%s::text[], 'synthetic_test')", (route,))
