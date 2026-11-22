"""Integration tests against the local Supabase database (read-only).

Uses only the 41 synthetic fixtures already seeded locally. Nothing is inserted. Synthetic
results prove behaviour only and are never empirical evidence. Skipped without DATABASE_URL.
"""
import pytest

from app import db
from app.config import get_settings
from app.services.reference_evidence import EvidenceRequest, reference_snapshot, retrieve_reference_evidence

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def conn():
    settings = get_settings()
    if settings.database_url is None:
        pytest.skip("DATABASE_URL not configured")
    try:
        c = db.connect(settings)
    except Exception as exc:  # local stack not running
        pytest.skip(f"local database unreachable: {type(exc).__name__}")
    with c:
        yield c


def retrieve(conn, role, mode):
    with reference_snapshot(conn) as repo:
        return retrieve_reference_evidence(EvidenceRequest(destination_role_id=role, data_mode=mode), repo)


def test_synthetic_mode_returns_only_labelled_synthetic_evidence(conn):
    r = retrieve(conn, "PM-PM", "synthetic_test")
    assert r.has_observed_pathways and r.is_synthetic_test_data
    assert {o.provenance.dataset_class for o in r.observations} == {"synthetic_test_fixture"}
    assert all(o.provenance.profile_id.startswith("SYN-") for o in r.observations)
    assert all(o.route[-1] == "PM-PM" and len(o.route) >= 2 for o in r.observations)
    assert len({o.provenance.profile_id for o in r.observations}) == len(r.observations)  # one per person
    for s in r.route_summaries:
        assert (s.data_mode, s.evidence_class, s.timeline_display_allowed) == ("synthetic_test", "synthetic_test_only", False)
        assert s.supporting_profile_count >= sum(o.route == s.route for o in r.observations)


def test_known_fixture_route_and_precision(conn):
    r = retrieve(conn, "PM-PM", "synthetic_test")
    swe = [o for o in r.observations if o.provenance.profile_id == "SYN-001"]
    assert [o.route for o in swe] == [["ROLE-SWE", "PM-APM", "PM-PM"]]
    assert (swe[0].route_months_min, swe[0].route_months_max, swe[0].is_precise) == (60, 60, True)
    # SYN-037 has a year-only first role; any observation for it must keep the range.
    for o in r.observations:
        for s in o.steps:
            assert s.start_is_month_precise == (s.start_lo == s.start_hi)


def test_empirical_mode_is_empty_and_never_falls_back(conn):
    r = retrieve(conn, "PM-PM", "empirical")
    assert (r.has_observed_pathways, r.observations, r.route_summaries) == (False, [], [])
    assert r.data_mode == "empirical" and not r.is_synthetic_test_data


def test_exact_role_matching_on_real_views(conn):
    apm = retrieve(conn, "PM-APM", "synthetic_test")
    assert apm.observations and all(o.route[-1] == "PM-APM" for o in apm.observations)
    assert all("PM-PM" not in o.route for o in apm.observations)


def test_no_identifiers_in_serialised_output(conn):
    dumped = retrieve(conn, "PM-PM", "synthetic_test").model_dump_json()
    assert "SYN-" not in dumped and "profile_id" not in dumped


def test_snapshot_is_read_only(conn):
    import psycopg
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        with reference_snapshot(conn):
            conn.execute("create temp table should_fail (x int)")


# --------------------------------------------------------------------------- episode-aware (v2.1.0)
PREVIOUSLY_MISSED = {
    "SYN-018": "PM-PM", "SYN-019": "PM-PM", "SYN-025": "PM-PM", "SYN-026": "PM-PM",
    "SYN-027": "PM-PM", "SYN-029": "PM-PM", "SYN-030": "PM-PM", "SYN-032": "PM-DIR",
}


@pytest.mark.parametrize("pid,role", sorted(PREVIOUSLY_MISSED.items()))
def test_previously_missed_fixtures_are_retrieved(conn, pid, role):
    r = retrieve(conn, role, "synthetic_test")
    obs = [o for o in r.observations if o.provenance.profile_id == pid]
    assert len(obs) == 1, pid
    o = obs[0]
    assert o.route == [role, role]
    assert o.step_change_types == ["employer_change"]
    assert [s.entry_change_type for s in o.steps] == [None, "employer_change"]
    assert o.is_precise is True and o.route_months_min > 0
    summary = next(s for s in r.route_summaries if s.route == [role, role])
    assert (summary.evidence_class, summary.timeline_display_allowed) == ("synthetic_test_only", False)


def test_same_role_summary_is_not_collapsed(conn):
    r = retrieve(conn, "PM-PM", "synthetic_test")
    (s,) = [s for s in r.route_summaries if s.route == ["PM-PM", "PM-PM"]]
    assert s.supporting_profile_count == 7  # SYN-018, 019, 025, 026, 027, 029, 030
    assert s.supporting_profile_count >= sum(o.route == ["PM-PM", "PM-PM"] for o in r.observations)


def test_step_change_types_match_the_foundation_view(conn):
    r = retrieve(conn, "PM-PM", "synthetic_test")
    with conn.cursor() as cur:
        for o in r.observations:
            cur.execute("select entry_change_types from trace_ref.v_career_episode_paths where profile_id = %s and segment_no = %s",
                        (o.provenance.profile_id, o.provenance.segment_no))
            (entry,) = cur.fetchone()
            assert [s.entry_change_type for s in o.steps] == entry[:len(o.steps)]
    conn.rollback()


def test_role_and_employer_changes_are_distinguished(conn):
    kinds = {c for o in retrieve(conn, "PM-PM", "synthetic_test").observations for c in o.step_change_types}
    assert {"role_change", "employer_change", "role_and_employer_change"} <= kinds


def test_empirical_episode_retrieval_is_empty_without_fallback(conn):
    for role in ("PM-PM", "PM-DIR"):
        r = retrieve(conn, role, "empirical")
        assert (r.observations, r.route_summaries, r.is_synthetic_test_data) == ([], [], False)


@pytest.mark.parametrize("route", [["PM-PM"], ["PM-PM", "PM-NOPE"], ["ROLE-UNKNOWN", "PM-PM"], ["PM-PM", None]])
def test_repository_rejects_invalid_routes(conn, route):
    from app.services.reference_evidence import InvalidEpisodeRoute
    for call in ("episode_route_observations", "episode_route_timeline_summary"):
        with pytest.raises(InvalidEpisodeRoute):
            with reference_snapshot(conn) as repo:
                getattr(repo, call)(route, "synthetic_test")


def test_no_employer_pseudonyms_in_output(conn):
    for role in ("PM-PM", "PM-DIR", "PM-SPM"):
        dumped = retrieve(conn, role, "synthetic_test").model_dump_json()
        assert "-ORG-" not in dumped and "employer_id" not in dumped


# --------------------------------------------------------------------------- route step experience (M6)
def _expected_step_experience(conn, r):
    """Independent recomputation: for each observation, the areas recorded on its stints before the destination,
    read row by row from the reference tables, then counted once per profile per route."""
    expected = {}
    for o in r.observations:
        pid, seg = o.provenance.profile_id, o.provenance.segment_no
        stints = conn.execute("select stint_no, role_id, employment_ids from trace_ref.v_career_episode_stints "
                              "where profile_id = %s and segment_no = %s order by stint_no", (pid, seg)).fetchall()
        start = next(i for i in range(len(stints)) if [s[1] for s in stints[i:i + len(o.route)]] == o.route
                     and stints[i][0] == stints[0][0] + i)
        emp_ids = [e for s in stints[start:start + len(o.route) - 1] for e in s[2]]
        areas = {a for (a,) in conn.execute("select responsibility_area from trace_ref.experience_evidence where profile_id = %s "
                                            "and employment_id = any(%s) and responsibility_area is not null "
                                            "and evidence_status <> 'rejected'", (pid, emp_ids)).fetchall()}
        route = expected.setdefault(tuple(o.route), {"profiles": 0, "areas": {}})
        route["profiles"] += 1
        for a in areas:
            route["areas"][a] = route["areas"].get(a, 0) + 1
    return expected


@pytest.mark.parametrize("origin", [None, "PM-APM"])
def test_step_experience_matches_an_independent_recomputation(conn, origin):
    with reference_snapshot(conn) as repo:
        r = retrieve_reference_evidence(EvidenceRequest(destination_role_id="PM-PM", data_mode="synthetic_test", origin_role_id=origin), repo)
        expected = _expected_step_experience(conn, r)
    assert r.is_synthetic_test_data and {x.data_mode for x in r.route_step_experience} == {"synthetic_test"}
    assert [tuple(x.route) for x in r.route_step_experience] == sorted(expected)
    for x in r.route_step_experience:
        exp = expected[tuple(x.route)]
        n, areas = exp["profiles"], exp["areas"]
        assert x.pre_destination_steps == x.route[:-1]
        if n < 5:
            assert (x.state, x.observation_count, x.areas, x.areas_withheld) == ("suppressed_small_cohort", None, [], False)
            continue
        listed = {a: c for a, c in areas.items() if c >= 5}
        assert x.observation_count == n and x.areas_withheld == (len(listed) < len(areas))
        assert x.state == ("no_recorded_areas" if not areas else "areas_disclosed" if listed else "areas_withheld")
        assert [(a.responsibility_area, a.profile_count, a.is_common) for a in x.areas] == [
            (a, c, 2 * c > n) for a, c in sorted(listed.items())]
    if origin == "PM-APM":
        (apm,) = r.route_step_experience
        assert (apm.state, apm.observation_count, apm.areas_withheld) == ("areas_disclosed", 13, True)
        assert [(a.responsibility_area, a.profile_count, a.is_common) for a in apm.areas] == [
            ("outcome_ownership", 5, False), ("prioritisation_backlog", 5, False)]


def test_step_experience_never_exposes_identifiers_small_routes_or_small_counts(conn):
    for dest in ("PM-PM", "PM-SPM", "PM-LEAD", "PM-DIR"):
        with reference_snapshot(conn) as repo:
            r = retrieve_reference_evidence(EvidenceRequest(destination_role_id=dest, data_mode="synthetic_test"), repo)
        dumped = "".join(x.model_dump_json() for x in r.route_step_experience)
        assert "SYN-" not in dumped and "profile_id" not in dumped and "months" not in dumped
        for x in r.route_step_experience:
            if x.state == "suppressed_small_cohort":
                assert x.observation_count is None and x.areas == [] and not x.areas_withheld
            else:
                assert x.observation_count >= 5 and all(a.profile_count >= 5 for a in x.areas)


def test_step_experience_is_empty_in_empirical_mode(conn):
    with reference_snapshot(conn) as repo:
        r = retrieve_reference_evidence(EvidenceRequest(destination_role_id="PM-PM", data_mode="empirical"), repo)
    assert (r.observations, r.route_step_experience) == ([], [])          # no eligible real profiles: honestly empty


def test_step_experience_query_is_mode_isolated_by_itself(conn):
    """Defence in depth: even if a synthetic profile were requested in empirical mode, the query returns nothing."""
    from app.services.reference_evidence.models import StepRequest
    req = [StepRequest(profile_id="SYN-001", segment_no=1, first_stint=1, last_stint=3)]
    with reference_snapshot(conn) as repo:
        assert repo.route_step_experience(req, "synthetic_test")            # the profile has recorded areas ...
        assert repo.route_step_experience(req, "empirical") == []           # ... but never in another mode


def test_step_experience_sql_keeps_mode_join_and_ignores_rejected_evidence():
    from app.services.reference_evidence.repository import STEP_EXPERIENCE_SQL
    assert "join trace_ref.v_mode_profiles mp" in STEP_EXPERIENCE_SQL and "mp.data_mode = %(data_mode)s" in STEP_EXPERIENCE_SQL
    assert "e.evidence_status <> 'rejected'" in STEP_EXPERIENCE_SQL and "select distinct r.profile_id, e.responsibility_area" in STEP_EXPERIENCE_SQL
