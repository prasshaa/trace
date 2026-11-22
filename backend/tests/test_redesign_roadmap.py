"""Product redesign: the career transition roadmap (GET /transition-plan/{id}/roadmap) and the planning goal
(GET/PUT /transition-goal). Transient users and invented records only, through the product's own endpoints.

The roadmap is a presentation projection of one saved plan: it never changes the route, capability or eligibility,
never creates a run, says nothing personal without a record that is eligible NOW, and never forecasts a duration.
"""
import json
import re
from datetime import date, datetime, timedelta, timezone

from app.services.transition.roadmap import _add_months
from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import candidate, consent_document  # noqa: F401  (fixtures)
from tests.test_phase4_transition import ONGOING, add_job, c, month, plan, swe  # noqa: F401  (fixtures)

# Goals are anchored to the database's day (UTC), as the goal functions define it; local midnight must not flip the test.
def db_today() -> date:
    return datetime.now(timezone.utc).date()


DEFICIT = re.compile(r"\byou lack\b|\byou don't have\b|\bnot ready\b|\bunsuitable\b|\byou cannot\b|\byou can't do\b", re.I)
EMPLOYERS = ("Razorpay", "Sarvam", "Paytm", "Target ", "Adobe", "Freshworks", "Wayfair")


def business_analyst(c, h):
    return add_job(c, h, "Business Analyst", "ROLE-BA", month(2016, 7), ONGOING, [
        ("Wrote requirements for the settlements module", ["requirements_analysis"], None),
        ("Built weekly dashboards on payment success rates", ["data_analysis"], None),
        ("Ran workshops with operations and compliance stakeholders", ["stakeholder_management"], None),
        ("Defined success metrics for the refund flow and tracked them", ["outcome_ownership"], None)])


def roadmap(c, h, run_id):
    r = c.get(f"/api/v1/transition-plan/{run_id}/roadmap", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def goal(c, h, target="PM-PM", **body):
    return c.put(f"/api/v1/transition-goal?target={target}", headers=h, json=body)


def lines(c, h):
    return {x["evidence_text"]: x for e in c.get("/api/v1/profile/records", headers=h).json()["employments"] for x in e["evidence"]}


# --------------------------------------------------------------------------- A. evidence correctness
def test_every_personal_claim_points_at_an_eligible_confirmed_record(c, users):
    _, h = candidate(c, users)
    business_analyst(c, h)
    p = plan(c, h, contexts=["growth_analytics"])
    rm = roadmap(c, h, p["run_id"])
    confirmed = set(lines(c, h))
    assert rm["build_on"], "a BA with categorised lines has something to build on"
    for claim in rm["build_on"] + rm["related"]:
        assert claim["evidence"], claim["key"]
        for ev in claim["evidence"]:
            assert ev["basis"] in ("confirmed_line_categorised_by_you", "confirmed_skill", "confirmed_role")
            if ev["kind"] == "evidence":
                assert ev["text"] in confirmed and ev["source"] == "Business Analyst" and ev["categorised_as"]
    assert len(rm["build_on"]) <= 2 and len(rm["focus"]) <= 2 and len(rm["related"]) <= 2      # no long lists
    assert "verified" not in json.dumps(rm["build_on"]).lower()                                # categorisation is the user's own


def test_rejected_or_changed_records_never_support_a_claim(c, users):
    _, h = candidate(c, users)
    business_analyst(c, h)
    p = plan(c, h)
    before = {b["key"]: b for b in roadmap(c, h, p["run_id"])["build_on"]}
    assert "C4" in before
    metric = lines(c, h)["Defined success metrics for the refund flow and tracked them"]
    assert c.post(f"/api/v1/records/evidence/{metric['id']}/reject", headers=h).status_code == 200
    after = roadmap(c, h, p["run_id"])                                                       # the same stored run
    assert "Defined success metrics" not in json.dumps(after)                                # rejected: never quoted
    assert "C4" not in {b["key"] for b in after["build_on"]}                                 # and no claim without it
    req = lines(c, h)["Wrote requirements for the settlements module"]
    r = c.post(f"/api/v1/records/evidence/{req['id']}/corrections", headers=h, json={"evidence_text": "Wrote the settlements specs"})
    assert r.status_code in (200, 201), r.text
    # changed text: the user's earlier categorisation is stale and never shown as theirs. Phase 5 (D50): the corrected,
    # confirmed text may be read by Trace, and is then labelled as Trace's reading, with the words it rests on.
    refs = [e for e in json.loads(json.dumps(roadmap(c, h, p["run_id"])))["build_on"] for e in e["evidence"]
            if e["text"] == "Wrote the settlements specs"]
    assert all(e["basis"] == "read_by_trace" and e["why"] for e in refs)


def test_pending_lines_never_support_a_claim(c, users):
    _, h = candidate(c, users)
    add_job(c, h, "Business Analyst", "ROLE-BA", month(2016, 7), ONGOING,
            [("Defined success metrics for the refund flow", ["outcome_ownership"], None)], confirm=False)
    p = plan(c, h)
    rm = roadmap(c, h, p["run_id"])
    assert rm["build_on"] == [] and "Defined success metrics" not in json.dumps(rm)
    assert rm["recommendation"]["kind"] == "resolve_first" and rm["resolve_first"]


def test_missing_information_is_never_a_deficit_and_unknown_stays_separate(c, users):
    _, h = candidate(c, users)
    business_analyst(c, h)
    rm = roadmap(c, h, plan(c, h, contexts=["regulated_domain"])["run_id"])
    assert not DEFICIT.search(json.dumps(rm))
    assert {x["key"] for x in rm["not_assessed"]} == {"X4"}                                  # Unknown is reported apart
    assert "X4" not in {x["key"] for x in rm["focus"]}                                       # ...never as something to fix


# --------------------------------------------------------------------------- B. recommendation and stages
def test_software_engineer_gets_a_short_bridge_recommendation(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    p = plan(c, h, contexts=["platform_api"])
    rec = roadmap(c, h, p["run_id"])["recommendation"]
    assert rec["headline"] == "Move into an Associate Product Manager role first."
    assert [s["position"] for s in rec["route"]] == ["current", "intermediate", "target"]
    assert 1 <= len(rec["reasons"]) <= 2 and rec["tradeoff"] and rec["provides"]
    assert any("parts of" in r and "seek and show yourself" in r for r in rec["reasons"])    # partial is never "closes"
    assert not any("develops them in the role itself" in r for r in rec["reasons"])
    assert rec["why_path"] and all(len(x) < 400 for x in rec["why_path"])


def test_stages_are_short_relevant_and_seek_is_concrete(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    rm = roadmap(c, h, plan(c, h)["run_id"])
    assert [s["title"] for s in rm["stages"]] == ["Build", "Demonstrate", "Reposition", "Hiring transition"]
    for s in rm["stages"]:
        assert len(s["highlighted"]) <= 3
        for a in s["highlighted"] + s["more"]:
            assert a["template_key"] not in ("apply.current_postings", "apply.check_criteria", "apply.no_verified_postings")
            assert "Seek this responsibility" not in a["what_to_do"] + a["title"]
            assert a["keep"] and a["what_to_do"]
    seek = [a for s in rm["stages"] for a in s["highlighted"] + s["more"] if a["template_key"] == "apply.bridge_seek"]
    assert seek and all(a["title"].startswith("Grow ") and a["where"] and a["produce"] for a in seek)
    focus = [f["key"] for f in rm["focus"]]
    assert [a["requirement_key"] for a in rm["stages"][0]["highlighted"]][:len(focus)] == focus   # focus first


def test_only_genuine_alternatives_are_offered(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    p = plan(c, h)
    rm = roadmap(c, h, p["run_id"])
    excluded = {tuple(s["title"] for s in r["steps"]) for r in p["routes"] if r["primary_status"] == "not_recommended"}
    shown = {tuple(a["steps"]) for a in rm["alternatives"]}
    assert shown and not (shown & excluded) and all(a["tradeoff"] for a in rm["alternatives"])


def test_direct_route_roadmap_has_no_employer_posting_actions(c, users):
    _, h = candidate(c, users)
    add_job(c, h, "Business Analyst", "ROLE-BA", month(2017, 1), ONGOING, [
        ("Ran discovery interviews with customers", ["product_discovery"], None),
        ("Owned the quarterly roadmap for the onboarding area", ["roadmap_ownership"], None),
        ("Wrote and prioritised requirements", ["requirements_analysis"], None),
        ("Defined and tracked activation metrics", ["outcome_ownership"], None),
        ("Aligned sales, support and engineering on launches", ["stakeholder_management"], None),
        ("Ran fortnightly releases", ["delivery_execution"], {"delivery_execution": {"cross_team": False}})])
    p = plan(c, h)
    assert p["decision"]["rule"] == "R2"
    stored = [a["template_key"] for r in p["routes"] for s in r["stages"] for a in s["actions"]]
    assert "apply.current_postings" in stored                                                # the stored plan still has it...
    rm = roadmap(c, h, p["run_id"])
    assert rm["recommendation"]["headline"] == "Move directly into a Product Manager role."
    body = json.dumps(rm)
    assert "apply.current_postings" not in body and not any(e in body for e in EMPLOYERS)   # ...the roadmap never shows it


# --------------------------------------------------------------------------- C. goal and timeline
def test_goal_persists_and_organises_the_roadmap_without_changing_the_plan(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    p = plan(c, h)
    before = roadmap(c, h, p["run_id"])
    assert before["timeline"]["timeframe"] is None and "No timeframe set" in before["timeline"]["note"]
    r = goal(c, h, timeframe="within_12_months", weekly_hours="1_3")
    assert r.status_code == 200 and (r.json()["timeframe"], r.json()["weekly_hours"]) == ("within_12_months", "1_3")
    assert c.get("/api/v1/transition-goal?target=PM-PM", headers=h).json()["timeframe"] == "within_12_months"   # reloads
    after = roadmap(c, h, p["run_id"])
    t = after["timeline"]
    assert t["goal_window"] == {"from": _add_months(db_today(), 12).isoformat(), "to": _add_months(db_today(), 12).isoformat()}
    assert "not a prediction" in t["note"] and t["duration_evidence"] == {"status": "not_available", "reason": "synthetic_data"}
    assert t["highlight_per_stage"] == 1 and all(len(s["highlighted"]) <= 1 for s in after["stages"])
    for k in ("recommendation", "build_on", "focus", "related", "not_assessed"):
        assert after[k] == before[k]                                                          # the plan's content is unchanged
    latest = c.get("/api/v1/transition-plan?target=PM-PM", headers=h).json()
    assert latest["run_id"] == p["run_id"] and latest["freshness"]["current"] is True        # no new run, still current


def test_flexible_and_specific_dates_and_user_checkpoints(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    run = plan(c, h)["run_id"]
    assert goal(c, h, timeframe="flexible").status_code == 200
    t = roadmap(c, h, run)["timeline"]
    assert t["goal_window"] is None and "flexible" in t["note"]
    target = (db_today() + timedelta(days=200)).isoformat()
    late = (db_today() + timedelta(days=260)).isoformat()
    assert goal(c, h, target_date=target, checkpoint_dates={"1": (db_today() + timedelta(days=60)).isoformat(), "4": late}).status_code == 200
    rm = roadmap(c, h, run)
    assert rm["timeline"]["goal_window"] == {"from": target, "to": target}
    cps = {cp["stage"]: cp for cp in rm["checkpoints"]}
    assert cps[1]["date"] and not cps[1]["after_goal"] and cps[4]["after_goal"] is True     # flagged, never refused
    assert cps[2]["date"] is None                                                            # Trace never invents a date
    assert goal(c, h, timeframe="within_2_weeks").status_code == 422
    assert goal(c, h, target_date=(db_today() - timedelta(days=1)).isoformat()).status_code == 422
    assert goal(c, h, target="NO-SUCH", timeframe="flexible").status_code == 422


def test_no_duration_or_success_forecast_anywhere(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    run = plan(c, h)["run_id"]
    goal(c, h, timeframe="within_6_months")
    body = json.dumps(roadmap(c, h, run)).lower()
    for bad in ("typically takes", "on average", "usually takes", "% ", "probability", "likely to", "you will become"):
        assert bad not in body, bad


# --------------------------------------------------------------------------- D/E. employer matching retired; histories
def test_roadmap_never_carries_employer_matching_and_research_is_preserved(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    c.put("/api/v1/target-companies?target=PM-PM", headers=h, json={"companies": ["Sarvam", "Razorpay"]})   # dormant API kept
    p = plan(c, h)
    body = json.dumps(roadmap(c, h, p["run_id"]))
    assert not any(e in body for e in EMPLOYERS) and "required criteria" not in body.lower()
    assert c.get("/api/v1/company-research?target=PM-PM", headers=h).status_code == 200    # data and endpoint preserved
    assert p["eligibility"]["postings"]                                                    # stored research untouched
    assert p["decision"]["trace"] and "eligib" not in json.dumps(p["decision"]["trace"])   # no route depends on employers


def test_histories_show_the_honest_state_in_the_roadmap(c, users):
    _, h = candidate(c, users)
    add_job(c, h, "Associate Product Manager", "PM-APM", month(2021, 1), ONGOING, [("Ran discovery interviews", ["product_discovery"], None)])
    rm = roadmap(c, h, plan(c, h)["run_id"])
    assert rm["career_histories"] == {"status": "not_enough_evidence", "routes": [], "synthetic_mode": True}


def test_roadmaps_are_owner_scoped(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    run = plan(c, h)["run_id"]
    _, other = candidate(c, users)
    assert c.get(f"/api/v1/transition-plan/{run}/roadmap", headers=other).status_code == 404
    assert c.get("/api/v1/transition-goal?target=PM-PM", headers=other).json()["timeframe"] is None
