"""Phase 8.2.1: human-review readiness corrections. Evaluation fixtures only; nothing touches a database or the app."""
import csv
import io
import json
from pathlib import Path

import pytest

from evaluation.recommendation.shadow import NO_PATH, Q6, RESPONSES, review_pack, run_shadow

BACKEND = Path(__file__).resolve().parents[1]
OUTPUT = BACKEND / "evaluation" / "recommendation" / "output"


@pytest.fixture(scope="module")
def shadow():
    return run_shadow(include_internal=True)


@pytest.fixture(scope="module")
def files(shadow):
    return review_pack(shadow)


def rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def by_id(shadow, sid):
    return next(r for st in shadow["sets"] for r in st["scenarios"] if r["id"] == sid)


# --------------------------------------------------------------------------- reproducibility of earlier phases
def test_the_phase_8_2_outputs_reproduce_byte_for_byte(tmp_path):
    from evaluation.recommendation.__main__ import main
    assert main(["--out", str(tmp_path / "out"), "--reproduce-phase-8-2", str(tmp_path / "p82")]) == 0
    for f in (OUTPUT / "phase-8.2").iterdir():
        assert (tmp_path / "p82" / f.name).read_text() == f.read_text(), f.name


def test_every_invariant_holds(shadow):
    assert {k: v for k, v in shadow["invariants"].items() if not v["ok"]} == {}


# --------------------------------------------------------------------------- 1. no-path explanations
@pytest.mark.parametrize("sid,reason", [("S11", "insufficient_independent_evidence"), ("S14", "no_independent_evidence"),
                                        ("S15", "starting_point_unconfirmed"), ("S21", "no_observed_route"), ("R11", "privacy_minimum_not_met")])
def test_each_no_path_scenario_has_its_own_reason(shadow, files, sid, reason):
    assert by_id(shadow, sid)["no_path_reason"] == reason
    pack = files["review_pack.md"]
    section = pack.split(f"## {sid}\n", 1)[1].split("\n## ", 1)[0]
    assert NO_PATH[reason] in section
    for leak in ("ROLE-BA", "ROLE-HR", "PM-LEAD", "Business Analyst →", "Founder", "4 ", "3 supporting", "EVAL-"):
        assert leak not in section, (sid, leak)                       # no suppressed sequence, count or contributor


def test_no_instruction_to_edit_fixture_files(files):
    pack = files["review_pack.md"].lower()
    assert "scenario file" not in pack and "scenarios.json" not in pack and "your own review forms only" in pack


# --------------------------------------------------------------------------- 2. response options
def test_permitted_responses_are_listed_and_not_applicable_is_prefilled(files):
    pack = files["review_pack.md"]
    for q, opts in RESPONSES.items():
        assert " / ".join(opts) in pack, q
    for opt in Q6:
        assert opt in pack
    for r in rows(files["review_reviewer_A_pathways.csv"]):
        assert (r["q3_intermediate_roles_sensible"] == "not_applicable") is (r["has_intermediate_roles"] == "no")
        assert (r["q4_meaningfully_different"] == "not_applicable") is (r["pathways_in_scenario"] == "1")
    scen = {r["scenario_id"]: r for r in rows(files["review_reviewer_A_scenarios.csv"])}
    for sid in ("S11", "S14", "S15", "S21", "R11"):
        assert scen[sid]["q6_prioritised_pathway"] == "no_pathway_shown" and scen[sid]["pathways_shown"] == "none"


def test_the_procedure_document_lists_the_same_responses():
    doc = (BACKEND / "docs" / "RECOMMENDATION_HUMAN_REVIEW.md").read_text()
    for opts in RESPONSES.values():
        assert " / ".join(opts) in doc
    for opt in Q6:
        assert opt in doc
    assert "review_adjudication_scenarios.csv" in doc


# --------------------------------------------------------------------------- 3. adjudication
def test_scenario_level_adjudication_exists_and_matches_the_forms(files):
    adj = rows(files["review_adjudication_scenarios.csv"])
    assert set(adj[0]) == {"scenario_id", "reviewer_a_prioritised_pathway", "reviewer_b_prioritised_pathway", "material_difference",
                           "reviewer_c_final_decision", "reviewer_c_rationale", "missing_information_identified"}
    assert [r["scenario_id"] for r in adj] == [r["scenario_id"] for r in rows(files["review_reviewer_A_scenarios.csv"])]
    path_adj = rows(files["review_adjudication.csv"])
    assert {"q1_a", "q1_b", "q3_a", "q4_a", "q5_b", "material_disagreement_questions", "escalate_to_reviewer_c"} <= set(path_adj[0])


def test_forms_cover_all_original_scenarios_plus_r14_with_the_same_neutral_mapping(files):
    core = [s["id"] for s in json.loads((BACKEND / "tests/fixtures/recommendation_eval/scenarios.json").read_text())["scenarios"]]
    pers = [s["id"] for s in json.loads((BACKEND / "tests/fixtures/recommendation_eval/scenarios_personalisation.json").read_text())["scenarios"]]
    ids = [r["scenario_id"] for r in rows(files["review_reviewer_A_scenarios.csv"])]
    assert ids == core + pers + ["R14"] and len(ids) == 36
    assert files["review_reviewer_A_pathways.csv"] == files["review_reviewer_B_pathways.csv"]
    assert files["review_reviewer_A_scenarios.csv"] == files["review_reviewer_B_scenarios.csv"]
    assert not any(r["scenario_id"].startswith("I") for r in rows(files["review_reviewer_A_scenarios.csv"]))   # audit set excluded


# --------------------------------------------------------------------------- 4. qualification semantics
def test_r07_and_r14_isolate_their_dimensions(shadow):
    comp = {tuple(c["pair"]): c for c in shadow["comparisons"]}
    assert all(v == ["qualification_levels", "qualification_types"] for v in comp[("R01", "R07")]["relevance_differs_in"].values())
    assert all(v == ["fields_of_study"] for v in comp[("R01", "R14")]["relevance_differs_in"].values())


def test_mba_is_a_qualification_type_not_a_level(shadow):
    st = next(s for s in shadow["sets"] if s["set"] == "personalisation")
    assert "qualification_types" in st["dimensions"] and "qualifications" not in st["dimensions"]
    direct = next(x for x in by_id(shadow, "R01")["relevance"] if x["route_id"] == "PM-PM>PM-SPM")["dimensions"]
    assert direct["qualification_levels"]["matched"] == [["bachelor", 5]]
    assert ["mba", 4] not in direct["qualification_types"].get("reference_not_in_profile", [])   # 4 < 5: never shown
    assert direct["qualification_types"]["matched"] == [["btech", 5]]


# --------------------------------------------------------------------------- 5. intersection privacy audit
def risks(shadow, sid):
    return {tuple(sorted(v for _, v in x["values"])): x["supporters_with_all"] for x in by_id(shadow, sid)["_risks_exact"].get("PM-PM>PM-SPM", [])}


def test_each_value_is_disclosed_but_the_combination_is_flagged(shadow):
    dims = by_id(shadow, "I01")["relevance"][0]["dimensions"]
    assert dims["industries"]["matched"] == [["saas", 6]] and dims["skills"]["matched"] == [["java", 6]]
    assert dims["fields_of_study"]["matched"] == [["computer science", 6]]
    assert risks(shadow, "I01") == {("computer science", "java", "saas"): 4}            # pairs are shared by 5: not flagged


def test_own_exclusion_changes_the_intersection(shadow):
    assert risks(shadow, "I02") == {("computer science", "saas"): 4, ("computer science", "java"): 4, ("java", "saas"): 4,
                                    ("computer science", "java", "saas"): 3}            # own profile was in the combination
    assert risks(shadow, "I03") == {("computer science", "java", "saas"): 4}            # own profile was outside it


def test_reports_never_print_small_combination_counts(shadow):
    from evaluation.recommendation.shadow import report
    text = report(run_shadow())
    assert "computer science + industries=saas + skills=java (fewer than 5)" in text.replace("fields_of_study=", "")
    assert "(4)" not in text.split("## Privacy combination audit", 1)[1].split("## Scenario set", 1)[0]


def test_nothing_was_loaded_into_a_database(base_settings):
    from app import db
    with db.connect(base_settings) as conn:
        assert conn.execute("select count(*) from trace_ref.profiles where profile_id like 'EVAL-%%'").fetchone()[0] == 0


from tests.test_api_foundation import base_settings  # noqa: E402,F401  (fixture)
