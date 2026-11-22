"""Product Phase 5: the Career Intelligence Report's server summary (a presentation projection of the saved plan).

The summary makes no decision of its own. These tests check, through the API with transient users and invented records,
that it repeats the planner's stored decision exactly, keeps capability states apart (Unknown is never a gap), previews
all four stages from the selected route, and carries no score of any kind.
"""
import json
import os
import re

from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import candidate, consent_document  # noqa: F401  (fixtures)
from tests.test_phase4_transition import ONGOING, _pending_lines_job, add_job, c, month, plan, routes, states, swe  # noqa: F401

SCORE_WORDS = re.compile(r'"[a-z_]*(score|percent|pct|weight|readiness|fit_pct|composite)[a-z_]*"\s*:', re.I)


def _dump(name, p):
    """Optional: TRACE_REPORT_DUMP=<dir> writes each plan for manual design review (never in normal runs)."""
    out = os.environ.get("TRACE_REPORT_DUMP")
    if out:
        with open(os.path.join(out, f"{name}.json"), "w") as fh:
            json.dump(p, fh, indent=1, default=str)


def business_analyst(c, h):
    add_job(c, h, "Business Analyst", "ROLE-BA", month(2016, 7), ONGOING, [
        ("Wrote requirements for the settlements module", ["requirements_analysis"], None),
        ("Built weekly dashboards on payment success rates", ["data_analysis"], None),
        ("Ran workshops with operations and compliance stakeholders", ["stakeholder_management"], None),
        ("Defined success metrics for the refund flow and tracked them", ["outcome_ownership"], None)])


def selected(p):
    return [(s["route_kind"], s["bridge_role_id"]) for s in p["summary"]["decision"]["selected"]]


def assert_matches_plan(p):
    """The summary repeats the stored plan; nothing in it is new."""
    s, d = p["summary"], p["decision"]
    assert s["decision"]["rule"] == d["rule"]
    if s["decision"]["kind"] == "bridge":            # POC: generated from the route's own development, never stronger
        support = next(x for x in d["trace"] if x["rule"] == "support")
        assert s["decision"]["reason"].startswith(support["explanation"])
    else:
        assert s["decision"]["reason"] == d["explanation"]
    primaries = sorted((r for r in p["routes"] if r["primary_status"] in ("primary", "tied_primary")), key=lambda r: r["position"])
    assert [x["position"] for x in s["decision"]["selected"]] == [r["position"] for r in primaries]
    st = states(p)
    for bucket, state in (("gaps", "gap"), ("transferable", "transferable"), ("unknown", "unknown")):
        assert {i["key"] for i in s["priority"][bucket]} == {k for k, v in st.items() if v == state}
    assert {i["key"] for i in s["strengths"]} == {k for k, v in st.items() if v == "demonstrated"}
    for group in (s["strengths"], *s["priority"].values()):                        # decision rights first, then by key
        assert group == sorted(group, key=lambda i: (not i["decision_rights"], i["key"]))
    assert [x["stage"] for x in s["stage_previews"]] == [1, 2, 3, 4]                # all four, never a fifth
    detailed = next(r for r in p["routes"] if r["position"] == s["decision"]["detailed_route_position"])
    for prev in s["stage_previews"]:
        acts = [a for st_ in detailed["stages"] if st_["stage"] == prev["stage"] for a in st_["actions"]]
        assert prev["action_count"] == len(acts)
        assert prev["first_objective"] == (acts[0]["objective"] if acts else None)
    assert not SCORE_WORDS.search(json.dumps(s)), "no score, percentage, weight or readiness field"


def test_software_engineer_summary_names_the_selected_bridge_and_keeps_states_apart(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    p = plan(c, h, contexts=["platform_api"])
    _dump("swe", p)
    assert_matches_plan(p)
    s = p["summary"]
    assert s["current_role"]["role_id"] == "ROLE-SWE" and s["target_role"]["role_id"] == "PM-PM"
    assert s["decision"]["kind"] == "bridge" and selected(p) == [("bridge", "PM-APM")]
    assert s["decision"]["selected"][0]["steps"][1] == "Associate Product Manager"
    assert [i["key"] for i in s["priority"]["gaps"]][:2] == ["C2", "C4"]          # decision-rights gaps lead
    assert all(i["decision_rights"] for i in s["priority"]["gaps"][:2])
    assert not s["resolve_first"]
    assert all(x["action_count"] > 0 for x in s["stage_previews"])                # a full four-stage plan
    assert s["stage_previews"][3]["title"] == "Hiring transition"


def test_business_analyst_report_differs_materially_from_the_software_engineers(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    a = plan(c, h, contexts=["platform_api"])["summary"]
    _, h2 = candidate(c, users)
    business_analyst(c, h2)
    p = plan(c, h2, contexts=["growth_analytics", "regulated_domain"])
    _dump("ba", p)
    assert_matches_plan(p)
    b = p["summary"]
    assert b["current_role"]["title"] != a["current_role"]["title"]
    assert {i["key"] for i in b["strengths"]} != {i["key"] for i in a["strengths"]}
    assert "C4" in {i["key"] for i in b["strengths"]} and "C4" in {i["key"] for i in a["priority"]["gaps"]}
    assert "X4" in {i["key"] for i in b["priority"]["unknown"]}                    # Unknown stays Unknown...
    assert "X4" not in {i["key"] for i in b["priority"]["gaps"]}                   # ...never a gap
    unknown = next(i for i in b["priority"]["unknown"] if i["key"] == "X4")
    assert unknown["reason"]                                                        # with the reason it is unknown
    assert [x["first_objective"] for x in b["stage_previews"]] != [x["first_objective"] for x in a["stage_previews"]]


def test_incomplete_profile_summary_is_resolve_first_with_no_invented_primary(c, users):
    _, h = candidate(c, users)
    _pending_lines_job(c, h)                       # Phase 5: lines waiting for review (not uncategorised ones) resolve first
    p = plan(c, h)
    _dump("incomplete", p)
    assert_matches_plan(p)
    s = p["summary"]
    assert s["decision"]["kind"] == "resolve_first" and s["decision"]["selected"] == []
    assert s["resolve_first"] is True
    assert s["decision"]["detailed_route_position"] == routes(p)[("direct", None)]["position"]
    prev = {x["stage"]: x for x in s["stage_previews"]}
    assert prev[4]["action_count"] > 0                                              # stage 4 is always present
    assert all(prev[n]["action_count"] == 0 for n in (1, 2, 3))                     # nothing invented before resolving


def test_senior_pm_summary_with_and_without_portfolio_scope(c, users):
    _, h = candidate(c, users)
    add_job(c, h, "Product Owner", "ROLE-OWNER", month(2018, 1), ONGOING, [
        ("Owned the payments roadmap", ["roadmap_ownership"], None), ("Owned activation outcome metrics", ["outcome_ownership"], None),
        ("Ran discovery with merchants", ["product_discovery"], None), ("Aligned sales and engineering", ["stakeholder_management"], None)])
    plain = plan(c, h, target="PM-SPM")
    assert_matches_plan(plain)
    assert plain["summary"]["decision"]["kind"] == "direct" and plain["summary"]["target_role"]["role_id"] == "PM-SPM"
    r = c.post("/api/v1/transition-plan?target=PM-SPM&scope=portfolio", headers=h)
    assert r.status_code == 201
    scoped = r.json()
    _dump("spm_portfolio", scoped)
    assert_matches_plan(scoped)
    assert scoped["summary"]["decision"]["kind"] == "direct_conditional"           # R4: direct, conditional on named gaps
    assert scoped["constraints"]["portfolio_scope"] is True


def test_the_summary_is_returned_as_stored_on_read(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    p = plan(c, h)
    assert c.get("/api/v1/transition-plan?target=PM-PM", headers=h).json()["summary"] == p["summary"]
    assert c.get(f"/api/v1/transition-plan/{p['run_id']}", headers=h).json()["summary"] == p["summary"]


def test_the_plan_response_has_no_score_fields_anywhere(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    p = plan(c, h)
    assert not SCORE_WORDS.search(json.dumps(p)), "no score, percentage, weight or readiness field in the plan"
