"""Phase 8.1: the evaluation-only fixtures and harness. The harness runs the current engine unchanged; these tests
keep its invariants, its reproducibility and the fixtures' isolation from every real or synthetic reference pool."""
import json
import re
from pathlib import Path

import pytest

from app import db
from evaluation.recommendation.harness import FIXTURE_DIR, expand_pool, load, run_all
from evaluation.recommendation.repository import EvaluationRepository
from tests.test_api_foundation import base_settings  # noqa: F401  (fixture)

BACKEND = Path(__file__).resolve().parents[1]
REQUIRED_COVERAGE = {
    "sufficient_reference_evidence", "incomplete_reference_evidence", "own_profile_only_evidence", "tied_routes",
    "single_profile_route", "exact_minimum", "own_contribution_drops_route", "multiple_routes",
    "different_backgrounds_same_target", "insufficient_route_support", "missing_current_role",
    "incomplete_user_responsibilities", "seniority_down", "seniority_lateral", "seniority_unknown", "seniority_mixed",
    "no_route", "multi_step", "contribution_supports_others", "own_contribution_route_remains",
}


@pytest.fixture(scope="module")
def baseline():
    return run_all()


def scenario(b, sid):
    return next(s for s in b["scenarios"] if s["id"] == sid)


def test_every_automated_invariant_holds(baseline):
    failing = [(s["id"], k) for s in baseline["scenarios"] for k, v in s["invariants"].items() if not v["ok"]]
    assert failing == []
    assert REQUIRED_COVERAGE <= {c for s in baseline["scenarios"] for c in s["covers"]}


def test_the_harness_is_reproducible(baseline):
    again = run_all()
    assert json.dumps(again, sort_keys=True) == json.dumps(baseline, sort_keys=True)


def test_route_privacy_counts_one_to_six(baseline):
    s = scenario(baseline, "S08")
    shown = {r["route_id"]: r["observation_count"] for r in s["shown_routes"]}
    hidden = {d["route_id"]: d["distinct_profiles"] for d in s["suppressed_routes"]}
    assert shown == {"ROLE-QA>PM-APM": 5, "ROLE-QA>ROLE-DA>PM-APM": 6}                  # exactly the minimum, and more
    assert sorted(hidden.values()) == [1, 2, 3, 4]
    text = json.dumps(s["shown_routes"]) + json.dumps(s["explanation"])
    for route in hidden:
        assert route not in text and route.split(">")[1] not in text                     # no sequence, no step


def test_the_distinctive_single_profile_route_never_appears(baseline):
    s = scenario(baseline, "S07")
    assert [d["route_id"] for d in s["suppressed_routes"]] == ["PM-PM>ROLE-ENT>PM-LEAD"]
    assert "ROLE-ENT" not in json.dumps(s["shown_routes"]) + json.dumps(s["explanation"])     # no route, no advice
    assert len(s["shown_routes"]) == 2 and not any(r["tied"] for r in s["shown_routes"])     # two different routes


def test_repeated_observations_count_once(baseline):
    s = scenario(baseline, "S01")
    assert s["shown_routes"][0]["observation_count"] == 9                                 # 8 + EVAL-DUP-01 (two segments)


def test_own_contribution_exclusion(baseline):
    assert scenario(baseline, "S11")["shown_routes"] == []                                # 5 with own, 4 without
    assert scenario(baseline, "S11")["suppressed_routes"] == [{"route_id": "ROLE-BA>PM-PM", "distinct_profiles": 4}]
    assert scenario(baseline, "S12")["shown_routes"][0]["observation_count"] == 5          # others still count it
    assert scenario(baseline, "S13")["shown_routes"][0]["observation_count"] == 5          # 6 with own, still eligible
    assert scenario(baseline, "S14")["shown_routes"] == [] and scenario(baseline, "S14")["discovered_routes"] == []


def test_fit_states_and_limitations(baseline):
    tier = lambda sid: scenario(baseline, sid)["shown_routes"][0]                          # noqa: E731
    assert [tier(x)["tier"] for x in ("S01", "S02", "S03", "S04")] == ["aligned", "partial", "limited", "not_established"]
    assert tier("S04")["limitations"] == ["incomplete_user_profile"]
    assert tier("S05")["limitations"] == ["insufficient_reference_evidence"]
    assert tier("S06")["limitations"] == ["insufficient_reference_evidence"]
    assert tier("S13")["limitations"] == ["insufficient_reference_evidence", "incomplete_user_profile"]
    assert tier("S01")["limitations"] == []


def test_ties_are_reported_as_ties(baseline):
    s9 = scenario(baseline, "S09")["shown_routes"]
    assert [r["rank_group"] for r in s9] == [1, 1] and all(r["tied"] for r in s9)
    s10 = scenario(baseline, "S10")["shown_routes"]
    assert [r["rank_group"] for r in s10] == [1, 2] and not any(r["tied"] for r in s10)


def test_backgrounds_share_routes_and_differ_only_in_fit(baseline):
    a, b = scenario(baseline, "S16A")["shown_routes"], scenario(baseline, "S16B")["shown_routes"]
    assert [r["route_id"] for r in a] == [r["route_id"] for r in b]                       # the current engine
    assert (a[0]["tier"], b[0]["tier"]) == ("partial", "limited")


def test_seniority_directions_are_recorded_not_filtered(baseline):
    d = {sid: scenario(baseline, sid)["directions"] for sid in ("S17", "S18", "S19", "S20")}
    assert d["S17"] == {"PM-SPM>PM-PM": ["down"]} and d["S18"] == {"ROLE-OWNER>PM-PM": ["lateral"]}
    assert d["S19"] == {"PM-DIR>ROLE-ENT>PM-HEAD": ["unknown", "unknown"]}
    assert d["S20"] == {"ROLE-CONSULT>PM-APM>PM-SPM": ["down", "up"]}


# --------------------------------------------------------------------------- isolation
def test_fixture_profiles_are_namespaced_and_never_in_the_database(base_settings):
    pool, _, _ = load()
    assert pool["profiles"] and all(p["profile_id"].startswith("EVAL-") for p in pool["profiles"])
    with db.connect(base_settings) as conn:
        assert conn.execute("select count(*) from trace_ref.profiles where profile_id like 'EVAL-%%'").fetchone()[0] == 0
        assert conn.execute("select count(*) from trace_ref.profiles where dataset_class = 'synthetic_test_fixture' "
                            "and synthetic_demo_allowed").fetchone()[0] == 41
        assert conn.execute("select count(*) from trace_ref.profiles where dataset_class = 'consented_real_reference'").fetchone()[0] == 0


def test_the_harness_never_touches_a_database_or_the_app():
    src = "\n".join(p.read_text() for p in (BACKEND / "evaluation").rglob("*.py"))
    assert not re.search(r"from app import db|app\.db|psycopg|connect\(|get_settings|TestClient|requests\.|urllib", src)
    app_src = "\n".join(p.read_text() for p in (BACKEND / "app").rglob("*.py"))
    assert "evaluation" not in re.findall(r"^(?:from|import) (\w+)", app_src, re.M)          # the app never imports it
    assert "recommendation_eval" not in app_src


def test_the_evaluation_repository_refuses_other_modes_and_ids():
    pool, _, _ = load()
    repo = EvaluationRepository(pool)
    with pytest.raises(ValueError):
        repo.mode_episode_paths_containing("PM-PM", "empirical")
    bad = json.loads((FIXTURE_DIR / "pool_core.json").read_text())
    bad["route_groups"][0]["ids"] = ["SYN-001"]
    with pytest.raises(ValueError):
        EvaluationRepository(expand_pool(bad))


def test_human_review_templates_are_pending(tmp_path):
    from evaluation.recommendation.__main__ import main
    assert main(["--out", str(tmp_path)]) == 0
    rows = (tmp_path / "human_review_template.csv").read_text().splitlines()[1:]
    assert rows and all(r.count("PENDING") == 6 for r in rows)
    pairs = (tmp_path / "pairwise_review_template.csv").read_text().splitlines()[1:]
    assert pairs and all(",PENDING," in r for r in pairs)
    assert "Human relevance judgments: none yet" in (tmp_path / "baseline_report.md").read_text()
