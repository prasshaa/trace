"""POC iteration after product Phase 5: target companies, company-specific research, the bridge summary, comparable
career histories at every privacy threshold, canonical stage names and saved-plan freshness.

API tests use transient users and invented records (as in Phase 4). Threshold tests use the reference-evidence fake
with consented, eligible (empirical) profiles, so the real M6 privacy chain runs: own contribution removed first, then
the route-level minimum, then the D4 label, then the report projection.
"""
import json
import re

import pytest

from app.services.reference_evidence import recommendation_evidence
from app.services.transition.feasibility import breadth_label
from app.services.transition.summary import STAGE_TITLES, career_histories, development
from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import candidate, consent_document  # noqa: F401  (fixtures)
from tests.test_phase4_transition import ONGOING, add_job, c, month, plan, routes, states, swe  # noqa: F401  (fixtures)
from tests.test_reference_evidence import REAL, FakeRepo, path
from tests.test_reference_evidence import month as rmonth

COUNT_WORDS = re.compile(r'"(observation_count|supporting_profile_count|profile_count|count|share_pct|profile_id)"')


def put(c, h, names, target="PM-PM"):
    return c.put(f"/api/v1/target-companies?target={target}", headers=h, json={"companies": names})


def research(c, h, target="PM-PM"):
    r = c.get(f"/api/v1/company-research?target={target}", headers=h)
    assert r.status_code == 200, r.text
    return {x["name"]: x for x in r.json()["companies"]}, r.json()


# --------------------------------------------------------------------------- 1-8 target-company selection
def test_select_several_companies_reorder_remove_and_reload_per_target(c, users):
    _, h = candidate(c, users)
    r = put(c, h, ["  Razorpay ", "My   Dream Startup", "razorpay", "Sarvam"])
    assert r.status_code == 200, r.text
    assert [(x["name"], x["position"], x["source"]) for x in r.json()["companies"]] == [
        ("Razorpay", 1, "researched_employer"), ("My Dream Startup", 2, "user_provided"), ("Sarvam", 3, "researched_employer")]
    assert [x["name"] for x in c.get("/api/v1/target-companies?target=PM-PM", headers=h).json()["companies"]] == [
        "Razorpay", "My Dream Startup", "Sarvam"]                                          # persists across requests
    assert c.get("/api/v1/target-companies?target=PM-SPM", headers=h).json()["companies"] == []   # per target role
    assert [x["name"] for x in put(c, h, ["Sarvam", "Razorpay"]).json()["companies"]] == ["Sarvam", "Razorpay"]  # remove + reorder
    _, other = candidate(c, users)
    assert c.get("/api/v1/target-companies?target=PM-PM", headers=other).json()["companies"] == []    # owner-scoped
    assert put(c, h, []).json()["companies"] == []                                         # skip / clear


def test_search_covers_only_researched_employers_and_says_it_is_not_a_directory(c, users):
    _, h = candidate(c, users)
    s = c.get("/api/v1/target-companies/search?target=PM-PM&q=razor", headers=h).json()
    assert [r["name"] for r in s["results"]] == ["Razorpay"] and s["results"][0]["has_posting_for_target"] is True
    assert "not a list of all companies" in s["note"] and "own choice" in s["note"]
    none = c.get("/api/v1/target-companies/search?target=PM-PM&q=Some%20Unlisted%20Co", headers=h).json()
    assert none["results"] == [] and none["exact_match"] is False
    every = {r["name"]: r for r in c.get("/api/v1/target-companies/search?target=PM-PM", headers=h).json()["results"]}
    assert every["Freshworks"]["has_posting_for_target"] is False                          # researched, but for Senior PM only
    for brand in ("Google", "Microsoft", "Amazon"):                                        # no invented "popular companies" list
        assert brand not in every


def test_invalid_company_lists_are_refused(c, users):
    _, h = candidate(c, users)
    assert put(c, h, [f"Company {i}" for i in range(21)]).status_code == 422
    assert put(c, h, ["   "]).status_code == 422
    assert put(c, h, ["x" * 101]).status_code == 422
    assert put(c, h, ["Razorpay"], target="NO-SUCH").status_code == 422


def test_company_choices_never_change_the_plan_or_trigger_a_new_run(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    p = plan(c, h, contexts=["platform_api"])
    assert put(c, h, ["Razorpay", "Sarvam", "My Dream Startup"]).status_code == 200
    latest = c.get("/api/v1/transition-plan?target=PM-PM", headers=h).json()
    assert latest["run_id"] == p["run_id"] and latest["freshness"] == {"current": True, "changed": []}   # no recomputation
    again = plan(c, h, contexts=["platform_api"])                                          # an explicit re-plan...
    for k in ("decision", "assessment", "constraints"):                                    # ...is unchanged by companies
        assert again[k] == p[k] if k != "decision" else again[k]["primary"] == p[k]["primary"]
    stage = lambda q, n: [a["template_key"] for r in q["routes"] for s in r["stages"] if s["stage"] == n for a in s["actions"]]  # noqa: E731
    for n in (1, 2, 3):
        assert stage(again, n) == stage(p, n)
    assert again["summary"] == p["summary"]


def test_skipping_companies_never_blocks_the_report(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    assert put(c, h, []).status_code == 200
    assert plan(c, h)["summary"]["stage_previews"][3]["title"] == "Hiring transition"
    companies, body = research(c, h)
    assert companies == {} and body["current_openings"] == []


# --------------------------------------------------------------------------- 9-17 employer research
def test_research_shows_only_selected_companies_each_posting_on_its_own(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    put(c, h, ["Sarvam", "Razorpay", "Freshworks", "My Dream Startup"])
    companies, body = research(c, h)
    assert list(companies) == ["Sarvam", "Razorpay", "Freshworks", "My Dream Startup"]
    for unselected in ("Paytm", "Target", "Adobe", "Wayfair"):
        assert unselected not in json.dumps(body)                                           # never arbitrary employers
    sarvam = companies["Sarvam"]
    assert sarvam["status"] == "assessed" and [p["source_key"] for p in sarvam["postings"]] == ["J10", "J12"]   # not merged
    j12 = sarvam["postings"][1]
    assert (j12["evidence_use"], j12["is_current_opportunity"]) == ("historical_eligibility_evidence", False)
    assert "outcome" not in sarvam and "eligibility" not in sarvam                          # no company-wide result
    for p in sarvam["postings"]:
        assert set(p["eligibility"]) >= {"outcome", "required", "preferred"} and p["location"]
    assert companies["Freshworks"]["status"] == "not_assessed"
    assert companies["Freshworks"]["not_assessed_reason"] == "no_relevant_posting"           # researched, no PM posting
    dream = companies["My Dream Startup"]
    assert (dream["source"], dream["status"], dream["not_assessed_reason"]) == ("user_provided", "not_assessed", "not_researched")
    assert {o["source_key"] for o in body["current_openings"]} == {"J10", "J1"}             # expired J12 never an opening


def test_research_is_assessed_from_the_confirmed_profile_now_and_not_saved(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    put(c, h, ["Paytm"])
    p = plan(c, h)
    (j5,) = research(c, h)[0]["Paytm"]["postings"]
    stored = next(x for x in p["eligibility"]["postings"] if x["source_key"] == "J5")
    assert j5["eligibility"] == stored["eligibility"]                                       # the same assessment 2
    assert c.get("/api/v1/transition-plan?target=PM-PM", headers=h).json()["run_id"] == p["run_id"]


# --------------------------------------------------------------------------- 18-22 comparable career histories
def _empirical_pool(n, route=("ROLE-QA", "PM-PM")):
    paths = [path(f"REF-{i:03d}", REAL, [(r, None if j == 0 else "role_and_employer_change") for j, r in enumerate(route)],
                  [rmonth(2015 + 2 * j, 1) for j in range(len(route))]) for i in range(n)]
    return paths, FakeRepo(paths, eligible=[p.profile_id for p in paths], threshold=5.0)


def _projected(n, exclude=()):
    paths, repo = _empirical_pool(n)
    ev = recommendation_evidence(repo, "PM-PM", "empirical", "ROLE-QA", list(exclude))
    observed = [{"route_kind": "observed", "steps": [{"title": r} for r in s.route],
                 "evidence_breadth_label": breadth_label(s.supporting_profile_count, False)} for s in ev.route_summaries]
    return career_histories({"data_mode": "empirical", "routes": observed}), paths


@pytest.mark.parametrize("n", [0, 1, 2, 3, 4])
def test_fewer_than_five_profiles_give_the_not_enough_evidence_state(n):
    h, _ = _projected(n)
    assert h == {"status": "not_enough_evidence", "routes": [], "synthetic_mode": False}   # identical for 0..4: nothing revealed


@pytest.mark.parametrize("n,band", [(5, "limited"), (9, "limited"), (10, "growing"), (24, "growing"), (25, "broad"),
                                    (99, "broad"), (100, "extensive")])
def test_d4_bands_at_their_boundaries(n, band):
    h, _ = _projected(n)
    assert h["status"] == "shown" and [r["breadth"] for r in h["routes"]] == [band]
    assert not COUNT_WORDS.search(json.dumps(h)) and "REF-" not in json.dumps(h)           # no counts or profile ids


def test_own_contribution_taking_a_route_below_five_suppresses_it():
    shown, paths = _projected(5)
    assert shown["status"] == "shown"
    h, _ = _projected(5, exclude=[paths[0].profile_id])
    assert h["status"] == "not_enough_evidence"


@pytest.mark.parametrize("n,label", [(None, None), (0, None), (4, None), (5, "limited"), (10, "growing"), (25, "broad"), (100, "extensive")])
def test_breadth_label_bands(n, label):
    assert breadth_label(n, False) == label and breadth_label(n, True) == "synthetic_test_data"


def test_synthetic_routes_never_populate_comparable_histories():
    run = {"data_mode": "synthetic_test", "routes": [{"route_kind": "observed", "steps": [{"title": "A"}, {"title": "B"}],
                                                      "evidence_breadth_label": "synthetic_test_data"}]}
    assert career_histories(run) == {"status": "not_enough_evidence", "routes": [], "synthetic_mode": True}
    run["routes"][0]["evidence_breadth_label"] = "broad"                                   # even if mislabelled
    assert career_histories(run)["routes"] == []


def test_the_current_environment_shows_the_honest_empty_state(c, users):
    _, h = candidate(c, users)
    add_job(c, h, "Associate Product Manager", "PM-APM", month(2021, 1), ONGOING, [("Ran discovery interviews", ["product_discovery"], None)])
    p = plan(c, h)
    assert any(r["route_kind"] == "observed" for r in p["routes"])                         # synthetic observed routes exist...
    assert p["summary"]["career_histories"] == {"status": "not_enough_evidence", "routes": [], "synthetic_mode": True}  # ...never shown


# --------------------------------------------------------------------------- 23-25 stages and the bridge summary
def test_canonical_stage_names_everywhere(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    p = plan(c, h)
    assert [(x["title"], x["subtitle"]) for x in p["summary"]["stage_previews"]] == [
        ("Build", "Build foundation"), ("Demonstrate", "Gain practical experience"),
        ("Reposition", "Present your confirmed experience effectively"), ("Hiring transition", "Move towards the target role")]
    assert {s["title"] for r in p["routes"] for s in r["stages"] if s["stage"]} <= set(STAGE_TITLES.values())
    assert "application-ready" not in json.dumps(p)


def test_summary_and_route_agree_on_full_versus_partial_development(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    p = plan(c, h, contexts=["platform_api"])
    s, apm = p["summary"], routes(p)[("bridge", "PM-APM")]
    dev = s["development"]
    assert [x["key"] for x in dev["core"]["partly"]] == ["C2", "C4"] and dev["core"]["fully"] == []
    f = apm["feasibility"]
    assert {x["key"] for g in dev.values() for x in g["fully"]} <= set(f["develops_fully"])
    assert {x["key"] for g in dev.values() for x in g["partly"]} <= set(f["develops_partly"])
    notes = {x["key"]: x for x in f["partial_development"]}
    for x in dev["core"]["partly"]:
        assert (x["remaining"], x["seek"]) == (notes[x["key"]]["incomplete"], notes[x["key"]]["seek"])
    reason = s["decision"]["reason"]
    assert "develops them in the role itself" not in reason
    assert "helps develop parts of" in reason and "still need to be demonstrated" in reason
    for x in dev["core"]["partly"]:                                                         # never described as fully developed
        assert f"develops \"{x['title']}\" in the role itself" not in reason


def _route(full=(), partly=()):
    return {"route_kind": "bridge", "feasibility": {"develops_fully": list(full), "develops_partly": list(partly),
                                                     "partial_development": [{"key": k, "incomplete": "rest", "seek": "ask"} for k in partly]}}


def test_development_sentences_follow_the_data_for_each_level():
    decision = {"trace": [{"rule": "support", "explanation": "Missing A, B and C.", "decision_rights_gaps": ["C2", "C4", "L1"],
                           "practice_gaps": []}]}
    titles = {"C2": "Strategy", "C4": "Outcomes", "L1": "Portfolio"}
    d = development(decision, _route(full=["C2"], partly=["C4"]), titles)
    assert "This bridge role develops \"Strategy\" in the role itself." in d["reason"]
    assert "also helps develop parts of \"Outcomes\"" in d["reason"]
    assert "It doesn't develop \"Portfolio\"" in d["reason"]
    assert [x["key"] for x in d["core"]["not_developed"]] == ["L1"]
    only_partial = development(decision, _route(partly=["C2", "C4"]), titles)["reason"]
    assert "in the role itself" not in only_partial                                         # partial never sounds complete
    assert development(decision, {"route_kind": "direct", "feasibility": {}}, titles) is None


# --------------------------------------------------------------------------- 33-34 freshness
def test_freshness_flags_a_changed_profile_and_a_new_run_is_saved(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    p = plan(c, h)
    assert p["freshness"] == {"current": True, "changed": []}
    add_job(c, h, "Support Engineer", "ROLE-SWE", month(2015, 1), month(2017, 12), [("Wrote runbooks", ["technical_engineering"], None)])
    stale = c.get("/api/v1/transition-plan?target=PM-PM", headers=h).json()
    assert stale["run_id"] == p["run_id"] and stale["freshness"] == {"current": False, "changed": ["profile_version"]}
    assert stale["decision"] == p["decision"]                                              # the saved run is never altered
    fresh = plan(c, h)
    assert fresh["run_id"] != p["run_id"] and fresh["freshness"]["current"] is True


def test_freshness_compares_every_version_and_posting_status(c, users, monkeypatch):
    _, h = candidate(c, users)
    swe(c, h)
    plan(c, h)
    import app.services.transition.service as svc
    monkeypatch.setattr(svc, "VERSIONS", {**svc.VERSIONS, "action_template_version": "pm-actions-v9"})
    monkeypatch.setattr(svc, "ENGINE_VERSION", "transition-planner-9.9.9")
    got = c.get("/api/v1/transition-plan?target=PM-PM", headers=h).json()["freshness"]
    assert got["current"] is False and set(got["changed"]) == {"action_template_version", "engine_version"}


def test_company_changes_do_not_make_a_plan_stale(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    plan(c, h)
    for names in (["Razorpay"], ["Razorpay", "Sarvam"], []):
        put(c, h, names)
        assert c.get("/api/v1/transition-plan?target=PM-PM", headers=h).json()["freshness"]["current"] is True


def test_uncovered_targets_stay_honest(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    r = c.post("/api/v1/transition-plan?target=PM-DIR", headers=h)
    assert r.status_code == 422 and r.json()["error"] == "target_not_covered"
    assert put(c, h, ["Razorpay"], target="PM-DIR").status_code == 200                    # an aspiration is still allowed
    companies, _ = research(c, h, target="PM-DIR")
    assert companies["Razorpay"]["status"] == "not_assessed"                               # no PM-DIR posting: not assessed
