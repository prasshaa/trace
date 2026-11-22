"""Phase 3.1: taxonomy v2, "None of these areas", clearing, scope questions, versioned classifications and pack pm-v1.1.

Pure tests use both approved packs (pm-v1, pm-v1.1) and the real taxonomy versions. API tests use transient users.
"""
import json
from pathlib import Path

import pytest
from uuid import uuid4

from app.services.capability.assess import AreaInfo, Requirement, assess
from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import candidate, consent_document, make_client  # noqa: F401  (fixtures)
from tests.test_phase3_capability import ev, profile

ROOT = Path(__file__).resolve().parents[2]


def pack(name):
    data = json.loads((ROOT / f"data-foundation/knowledge_packs/{name}.json").read_text(encoding="utf-8"))
    return [Requirement(key=r["key"], requirement_class=r["requirement_class"], applies_to_level=r["applies_to_level"], title=r["title"],
                        description=r["description"], level_notes=r.get("level_notes") or {}, contexts=tuple(r["contexts"]),
                        direct_areas=tuple(r["direct_areas"]), transferable_areas=tuple(r["transferable_areas"]),
                        direct_skills=tuple(r["direct_skills"]), transferable_skills=tuple(r["transferable_skills"]),
                        transferable_role_families=tuple(r["transferable_role_families"]), transfer_rationale=r.get("transfer_rationale"),
                        gap_basis=r["gap_basis"], area_conditions=r.get("area_conditions") or {}) for r in data["requirements"]]


V1, V11 = pack("pm-v1"), pack("pm-v1.1")
NEW = ("ai_product_work", "data_governance_quality", "business_case_economics", "executive_communication")
QUALIFIED = {"strategy_portfolio": "multi_product_scope", "people_leadership": "coaching_mentoring", "delivery_execution": "cross_team",
             "business_case_economics": "tracks_outcomes"}
TAXONOMY = {a: AreaInfo(version=2 if a in NEW else 1, qualifier_versions={QUALIFIED[a]: 2} if a in QUALIFIED else {})
            for a in ("customer_account_management", "data_analysis", "delivery_execution", "design_research", "go_to_market_growth",
                      "operations_process", "outcome_ownership", "people_leadership", "prioritisation_backlog", "product_discovery",
                      "quality_testing", "requirements_analysis", "roadmap_ownership", "stakeholder_management", "strategy_portfolio",
                      "technical_engineering", *NEW)}
ALL_CONTEXTS = ["commercial_product", "growth_analytics", "platform_api", "data_product", "regulated_domain", "ai_product"]


def run(p, reqs=V11, level="senior_pm", contexts=ALL_CONTEXTS):
    outcomes, cov = assess(p, reqs, level, contexts, None, TAXONOMY)
    return {o.key: o for o in outcomes}, cov


# --------------------------------------------------------------------------- "None of these areas", clearing, staleness
def test_none_of_these_areas_counts_as_reviewed_and_allows_a_gap():
    p = profile([ev("Built payment APIs", ["technical_engineering"]), ev("Organised the team offsite", none=True)])
    s, cov = run(p)
    assert cov.complete and (s["C1"].state, s["C1"].reason) == ("gap", "reviewed_and_classified_without_a_match")


def test_pending_lines_block_gaps_and_are_named_and_uncategorised_lines_are_read():
    # Lines waiting for review still block a gap, and are named. Phase 5 (D50): a confirmed line the user hasn't
    # categorised (cleared, or a stale choice) is read by Trace instead of blocking; one showing nothing never counts.
    pending = ev("A pending draft", ["data_analysis"], status="pending_review")
    s, cov = run(profile([ev("Built payment APIs", ["technical_engineering"]), pending]))
    assert (s["C1"].state, s["C1"].reason) == ("unknown", "evidence_pending_review")
    assert s["C1"].blocking_records == [str(pending.candidate_evidence_id)]
    for line in (ev("Cleared line", None), ev("A stale none", none=True, stale=True)):
        s, cov = run(profile([ev("Built payment APIs", ["technical_engineering"]), line]))
        assert cov.complete and (s["C1"].state, s["C1"].reason) == ("gap", "read_without_a_match")


# --------------------------------------------------------------------------- taxonomy versions
def test_classifications_made_before_new_areas_never_support_a_gap_for_them():
    old = profile([ev("Built payment APIs", ["technical_engineering"], version=1), ev("Offsite", none=True, version=1)])
    s, _ = run(old)
    assert (s["C1"].state, s["C1"].reason) == ("gap", "reviewed_and_classified_without_a_match")    # C1 uses version-1 areas only
    for key in ("X5", "X6", "X7", "L3", "C5"):          # their mappings now include version-2 areas
        assert (s[key].state, s[key].reason) == ("unknown", "classified_before_taxonomy_update"), key
        assert len(s[key].blocking_records) == 2        # both lines, including the earlier "None" choice
    renewed = profile([ev("Built payment APIs", ["technical_engineering"], version=2), ev("Offsite", none=True, version=2)])
    s, _ = run(renewed)
    assert {s[k].state for k in ("X5", "X6", "X7", "L3", "C5")} == {"gap"}                          # re-reviewed: now determinable


def test_a_taxonomy_update_never_creates_new_gaps_for_existing_classifications():
    """Every profile classified before v2: any Gap under pm-v1.1 was already a Gap under pm-v1 (or the user re-reviewed)."""
    profiles = [
        profile([ev("Built payment APIs", ["technical_engineering"], version=1)]),
        profile([ev("Mentored analysts", ["people_leadership"], version=1), ev("x", ["delivery_execution"], version=1)]),
        profile([ev("Set portfolio strategy", ["strategy_portfolio"], version=1), ev("Offsite", none=True, version=1)]),
        profile([ev("Ran discovery", ["product_discovery"], version=1), ev("Owned activation", ["outcome_ownership"], version=1)]),
    ]
    for p in profiles:
        before, _ = run(p, reqs=V1)
        after, _ = run(p, reqs=V11)
        new_gaps = {k for k, o in after.items() if o.state == "gap" and before[k].state != "gap"}
        assert new_gaps == set(), new_gaps


# --------------------------------------------------------------------------- evidence-specific conditions (D: L1, L2, L4, C4)
@pytest.mark.parametrize("area,cond,key,line", [
    ("people_leadership", "coaching_mentoring", "L2", "Managed a team of analysts"),
    ("strategy_portfolio", "multi_product_scope", "L1", "Contributed to the roadmap discussion"),
    ("delivery_execution", "cross_team", "L4", "Delivered releases for my team"),
])
def test_generic_evidence_is_not_enough_where_a_scope_condition_applies(area, cond, key, line):
    yes = run(profile([ev(line, [area], quals={area: {cond: True}})]))[0][key]
    no = run(profile([ev(line, [area], quals={area: {cond: False}})]))[0][key]
    unanswered = run(profile([ev(line, [area])]))[0][key]
    assert yes.state == "demonstrated"
    assert (no.state, no.reason) == ("gap", "reviewed_and_classified_without_a_match")
    assert (unanswered.state, unanswered.reason) == ("unknown", "scope_question_not_answered")


def test_business_cases_transfer_to_outcome_metrics_only_with_tracked_outcomes():
    tracked = run(profile([ev("Built the pricing business case and tracked its revenue target", ["business_case_economics"],
                              quals={"business_case_economics": {"tracks_outcomes": True}})]))[0]
    model_only = run(profile([ev("Built a financial model", ["business_case_economics"],
                                 quals={"business_case_economics": {"tracks_outcomes": False}})]))[0]
    assert (tracked["C4"].state, tracked["C4"].basis) == ("transferable", "classified_evidence")
    assert "outcomes" in tracked["C4"].transfer_rationale
    assert model_only["C4"].state == "gap" and model_only["X5"].state == "demonstrated"          # still evidence of X5 itself


def test_new_areas_demonstrate_their_requirements():
    s, _ = run(profile([ev("Wrote the eval set for our chatbot", ["ai_product_work"]),
                        ev("Defined the single customer definition", ["data_governance_quality"]),
                        ev("Presented the roadmap to the CFO", ["executive_communication"])]))
    assert {k: s[k].state for k in ("X6", "X7", "L3", "C5")} == {k: "demonstrated" for k in ("X6", "X7", "L3", "C5")}


def test_domain_and_context_rules_are_unchanged():
    s, _ = run(profile([ev("Built payment APIs", ["technical_engineering"])]), contexts=[])
    assert all(s[k].state == "not_applicable" for k in ("X1", "X2", "X3", "X4", "X5", "X6", "X7"))
    s, _ = run(profile([ev("Built payment APIs", ["technical_engineering"])]))
    assert (s["X4"].state, s["X4"].reason) == ("unknown", "not_inferable_from_absence")


# --------------------------------------------------------------------------- API and database
@pytest.fixture
def c(base_settings, consent_document):
    with make_client(base_settings) as client:
        yield client


def evidence_of(c, h):
    return c.get("/api/v1/profile/records", headers=h).json()["employments"][0]["evidence"]


def test_the_taxonomy_endpoint_explains_each_area(c, users):
    _, h = candidate(c, users)
    areas = {a["code"]: a for a in c.get("/api/v1/taxonomy/responsibility-areas", headers=h).json()}
    assert len(areas) == 20 and all(a["description"] and len(a["examples"]) >= 2 for a in areas.values())
    assert areas["ai_product_work"]["introduced_in_version"] == 2 and areas["ai_product_work"]["current_taxonomy_version"] == 3   # v3: D35
    assert set(areas["people_leadership"]["questions"]) == {"coaching_mentoring"}


def test_classify_none_change_clear_through_the_api(c, users):
    _, h = candidate(c, users)
    c.post("/api/v1/entries/employment", headers=h, json={"title_raw": "Analyst", "responsibilities": ["Mentored two analysts", "Organised the offsite"],
                                                         "confirm": True})
    a, b = evidence_of(c, h)
    r = c.put(f"/api/v1/evidence/{a['id']}/classification", headers=h,
              json={"areas": ["people_leadership"], "qualifiers": {"people_leadership": {"coaching_mentoring": True}}})
    assert r.status_code == 200 and r.json()["state"] == "current"
    r = c.put(f"/api/v1/evidence/{b['id']}/classification", headers=h, json={"none": True})
    assert (r.status_code, r.json()["state"]) == (200, "none_of_these")
    a, b = evidence_of(c, h)
    assert (a["classification"]["state"], a["classification"]["qualifiers"], a["classification"]["taxonomy_version"]) == (
        "current", {"people_leadership": {"coaching_mentoring": True}}, 3)                    # taxonomy v3 (D35)
    assert (b["classification"]["state"], b["classification"]["areas"]) == ("none_of_these", [])
    assert c.put(f"/api/v1/evidence/{b['id']}/classification", headers=h, json={"none": True, "areas": ["data_analysis"]}).status_code == 422
    assert c.put(f"/api/v1/evidence/{b['id']}/classification", headers=h, json={"areas": []}).status_code == 422
    assert c.put(f"/api/v1/evidence/{a['id']}/classification", headers=h,
                 json={"areas": ["data_analysis"], "qualifiers": {"data_analysis": {"cross_team": True}}}).status_code == 422
    assert c.delete(f"/api/v1/evidence/{a['id']}/classification", headers=h).json()["state"] == "cleared"
    assert evidence_of(c, h)[0]["classification"]["state"] == "none"
    assert c.put(f"/api/v1/evidence/{a['id']}/classification", headers=h, json={"areas": ["data_analysis"]}).status_code == 200   # old body works


def test_a_pre_versioning_classification_needs_re_review_before_new_area_gaps(c, users, base_settings):
    u, h = candidate(c, users)
    c.post("/api/v1/entries/employment", headers=h, json={"title_raw": "Engineer", "responsibilities": ["Built payment APIs"], "confirm": True})
    (x,) = evidence_of(c, h)
    users.admin.execute("update public.trace_candidate_evidence set area_classification = array['technical_engineering'], "
                        "area_classification_basis = 'candidate_selected', area_classified_text = evidence_text "
                        "where candidate_evidence_id = %s", (x["id"],))                      # as written before v2.18.0 (no version)
    (x,) = evidence_of(c, h)
    assert (x["classification"]["state"], x["classification"]["taxonomy_version"]) == ("current", 1)      # shown as version 1, never rewritten
    out = c.get("/api/v1/capability-assessment?target=PM-PM&context=ai_product", headers=h).json()
    s = {r["key"]: r for r in out["requirements"]}
    assert (s["X6"]["state"], s["X6"]["reason"], s["X6"]["blocking_records"]) == ("unknown", "classified_before_taxonomy_update", [x["id"]])
    assert s["C1"]["state"] == "gap" and out["taxonomy_version"] == 3 and out["knowledge_pack"] == "pm-v1.2"
    assert c.put(f"/api/v1/evidence/{x['id']}/classification", headers=h, json={"areas": ["technical_engineering"]}).status_code == 200
    s = {r["key"]: r for r in c.get("/api/v1/capability-assessment?target=PM-PM&context=ai_product", headers=h).json()["requirements"]}
    assert s["X6"]["state"] == "gap"                                                                 # re-reviewed against v2


def test_optional_classification_while_entering_a_job_and_accepting_a_suggestion(c, users):
    _, h = candidate(c, users)
    r = c.post("/api/v1/entries/employment", headers=h, json={
        "title_raw": "Product Analyst", "responsibilities": ["Ran discovery interviews", "Ordered team lunch"],
        "responsibility_classifications": [{"areas": ["product_discovery"]}, {"none": True}], "confirm": True})
    assert r.status_code == 201
    states = {x["evidence_text"]: x["classification"]["state"] for x in evidence_of(c, h)}
    assert states == {"Ran discovery interviews": "current", "Ordered team lunch": "none_of_these"}
    c.post("/api/v1/proposals/from-text", headers=h, json={"text": "Data Analyst, Invented Co Jan 2015 - Dec 2016\n- Built a churn dashboard for the growth team\n- Wrote SQL reports for the finance team"})
    (p,) = [x for x in c.get("/api/v1/proposals", headers=h).json() if x["kind"] == "employment"]
    assert c.post(f"/api/v1/proposals/{p['id']}/accept", headers=h,
                  json={"confirm": True, "classifications": [{"areas": ["data_analysis"]}, None]}).status_code == 200
    jobs = c.get("/api/v1/profile/records", headers=h).json()["employments"]
    da = next(e for e in jobs if e["title_raw"] == "Data Analyst")
    assert sorted(x["classification"]["state"] for x in da["evidence"]) == ["current", "none"]        # the second line left uncategorised
    bad = c.post("/api/v1/entries/employment", headers=h, json={"title_raw": "X", "responsibilities": ["One line"],
                                                               "responsibility_classifications": [{"none": True, "areas": ["data_analysis"]}]})
    assert bad.status_code == 422
    assert not any(e["title_raw"] == "X" for e in c.get("/api/v1/profile/records", headers=h).json()["employments"])   # nothing half-saved


def test_classify_line_refuses_none_with_areas_before_reaching_the_database():
    from app.services.candidate_profile.entries import InvalidRecord, LineClassification, classify_line

    class Repo:
        calls = []

        def classify_evidence_v2(self, *a):
            self.calls.append(a)

    repo = Repo()
    with pytest.raises(InvalidRecord):
        classify_line(repo, uuid4(), uuid4(), "One line", LineClassification(areas=["data_analysis"], none=True))
    classify_line(repo, uuid4(), uuid4(), "One line", None)                                           # nothing chosen
    classify_line(repo, uuid4(), uuid4(), "One line", LineClassification())                           # nothing chosen
    assert repo.calls == []
    classify_line(repo, uuid4(), uuid4(), "One line", LineClassification(none=True))
    assert len(repo.calls) == 1 and repo.calls[0][2:] == ([], True, {}, "One line")
