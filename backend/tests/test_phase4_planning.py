"""Product Phase 4 (after the Phase 4 review, D31-D35): route selection and four-stage actions on the APPROVED content
(data-foundation/knowledge_packs/pm-v1.2.json, pm-bridges-v2.json, pm-actions-v2.json). Pure tests; invented inputs.

The assessments are computed by the real assessment rules (pm-v1.2), so every scenario exercises the whole chain:
profile -> assessment -> decision-rights gaps -> bridges -> decision."""
import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.services.capability.assess import Requirement, assess
from app.services.transition.actions import ActionBuilder
from app.services.transition.feasibility import (
    ENGINE_VERSION, breadth_label, decide, direct_support, evaluate_bridges,
)

ROOT = Path(__file__).resolve().parents[2]
KP = ROOT / "data-foundation/knowledge_packs"
PACK = json.loads((KP / "pm-v1.2.json").read_text(encoding="utf-8"))
BRIDGES = json.loads((KP / "pm-bridges-v2.json").read_text(encoding="utf-8"))
ACTIONS = json.loads((KP / "pm-actions-v2.json").read_text(encoding="utf-8"))
TITLES = {"PM-APM": "Associate Product Manager", "ROLE-PAN": "Product Analyst", "ROLE-OWNER": "Product Owner", "PM-PM": "Product Manager"}
REQS = [Requirement(key=r["key"], requirement_class=r["requirement_class"], applies_to_level=r["applies_to_level"], title=r["title"],
                    description=r["description"], level_notes=r.get("level_notes") or {}, contexts=tuple(r["contexts"]),
                    direct_areas=tuple(r["direct_areas"]), transferable_areas=tuple(r["transferable_areas"]),
                    direct_skills=tuple(r["direct_skills"]), transferable_skills=tuple(r["transferable_skills"]),
                    transferable_role_families=tuple(r["transferable_role_families"]), transfer_rationale=r.get("transfer_rationale"),
                    gap_basis=r["gap_basis"], area_conditions=r.get("area_conditions") or {}, decision_rights=bool(r.get("decision_rights")),
                    area_condition_fallback=r.get("area_condition_fallback") or {}) for r in PACK["requirements"]]
REQ = {r.key: r for r in REQS}
TEMPLATES = {t["key"]: {"stage": t["stage"], "kind": t["kind"], "objective": t["objective"], "action_text": t["action"],
                        "deliverable": t["deliverable"], "where_options": t["where"], "basis_sources": t["basis_sources"],
                        "labels": t["labels"]} for t in ACTIONS["templates"]}


def ev(text, areas, quals=None, status="user_confirmed"):
    return NS(candidate_evidence_id=uuid.uuid4(), evidence_text=text, area_classification=areas, area_classification_is_stale=False,
              area_classification_none=False, area_qualifiers=quals or {}, area_classification_taxonomy_version=3,
              provenance=NS(review_status=status))


def skill(sid):
    return NS(candidate_skill_id=uuid.uuid4(), taxonomy=NS(skill_id=sid, canonical_skill=sid.lower()), skill_text_raw=None,
              provenance=NS(review_status="user_confirmed"))


def items_for(family, lines, skills=(), level="pm", contexts=()):
    job = NS(candidate_employment_id=uuid.uuid4(), title_raw=family, role_family=family, mapping_is_stale=False, mapping_status="confirmed",
             provenance=NS(review_status="user_confirmed"), responsibilities=list(lines), achievements=[], other_evidence=[])
    profile = NS(employment_history=[job], career_activities={}, unattached_evidence=[], skills=list(skills), education=[])
    outs, _ = assess(profile, REQS, level, list(contexts))
    return [{"key": o.key, "title": REQ[o.key].title, "requirement_class": REQ[o.key].requirement_class, "state": o.state,
             "reason": o.reason, "basis": o.basis, "decision_rights": REQ[o.key].decision_rights, "transfer_rationale": o.transfer_rationale,
             "blocking_records": list(o.blocking_records), "sources": [],
             "supporting": [{"kind": s.kind, "record_id": s.record_id, "excerpt": s.excerpt} for s in o.supporting]} for o in outs]


def plan(family, lines, skills=(), target="PM-PM", contexts=(), current=None, scope=False):
    senior = target == "PM-SPM"
    it = items_for(family, lines, skills, "senior_pm" if senior else "pm", contexts)
    sup = direct_support(it, senior=senior, portfolio_scope=scope)
    bridges = [{**b, "bridge_title": TITLES[b["bridge_role_id"]]} for b in BRIDGES["bridges"] if b["target_role_id"] == target]
    roles = [{"record_id": "job", "title": family, "role_family": family}]
    f = evaluate_bridges(bridges, it, roles, current, sup, direct_support(it) if senior else None)
    d = decide(sup, f, {i["key"]: i["title"] for i in it})
    return {i["key"]: i["state"] for i in it}, sup, {x.bridge["bridge_role_id"]: x for x in f}, d


YES = {"outcome_metrics_influence": True}
CO = {"people_leadership": {"coaching_mentoring": True}}

CUSTOMER_SUCCESS = ("customer_success", [ev("Managed renewals and escalations for enterprise accounts", ["customer_account_management"]),
                                         ev("Ran onboarding and fixed process gaps", ["operations_process"])])
DATA_SCIENTIST = ("data_science", [ev("Built churn prediction models", ["technical_engineering"]),
                                   ev("Prioritised the modelling backlog with the analytics lead", ["prioritisation_backlog"]),
                                   ev("Presented model results to product and marketing", ["stakeholder_management"])], [skill("SKL-PYTHON")])
BROAD_BA = ("business_analysis", [ev("Wrote requirements", ["requirements_analysis"]), ev("Prioritised the change backlog", ["prioritisation_backlog"]),
                                  ev("Tracked adoption and decided the next release from it", ["outcome_ownership"]),
                                  ev("Interviewed operations users", ["product_discovery"]), ev("Ran workshops", ["stakeholder_management"]),
                                  ev("Ran fortnightly releases", ["delivery_execution"])])


# --------------------------------------------------------------------------- the three Product Analyst comparisons
def test_customer_success_manager_gets_the_associate_pm_bridge_with_partial_development_explained():
    states, sup, f, d = plan(*CUSTOMER_SUCCESS, contexts=["commercial_product"], current="ROLE-CS")
    assert (states["C2"], states["C4"]) == ("gap", "gap") and sup.gaps == ["C2", "C4"]
    assert (d.rule, d.primary) == ("R3", ["PM-APM"])
    apm = f["PM-APM"]
    assert apm.levels["C2"] == apm.levels["C4"] == "partial"                       # never presented as closing (D32)
    assert any("only partly" in r for r in apm.reasons) and not any(r.startswith("Develops \"") for r in apm.reasons)
    assert f["ROLE-PAN"].entry == "not_evidenced" and f["ROLE-PAN"].status == "not_recommended"


def test_data_scientist_gets_the_product_analyst_when_it_develops_the_missing_outcome_ownership():
    states, sup, f, d = plan(*DATA_SCIENTIST, contexts=["data_product"], current="ROLE-DS")
    assert (states["C2"], states["C4"]) == ("transferable", "gap") and sup.gaps == ["C4"]   # C2 carries over: fine for PM (D31)
    assert (d.rule, d.primary) == ("R3", ["ROLE-PAN"])
    assert f["ROLE-PAN"].levels["C4"] == "full" and f["ROLE-PAN"].entry == "evidenced"
    assert f["PM-APM"].levels["C4"] == "partial" and f["PM-APM"].status == "alternative"       # dominated on C4, not counted


def test_a_business_analyst_who_shows_strategy_and_outcome_evidence_moves_directly():
    states, sup, f, d = plan(*BROAD_BA, current="ROLE-BA")
    assert (states["C2"], states["C4"]) == ("transferable", "demonstrated") and sup.level == "supported"
    assert (d.rule, d.primary) == ("R2", ["direct"]) and all(x.status == "not_recommended" for x in f.values())


# --------------------------------------------------------------------------- Senior PM: evidence, never the title (D31)
def test_senior_pm_through_pm_first_only_when_strategy_or_outcome_ownership_is_not_demonstrated():
    swe = [ev("Built payment APIs", ["technical_engineering"]), ev("Shipped releases", ["delivery_execution"])]
    _, sup, f, d = plan("software_engineering", swe, target="PM-SPM", current="ROLE-SWE")
    assert sup.gaps == ["C2", "C4"] and (d.rule, d.primary) == ("R3", ["PM-PM"]) and f["PM-PM"].entry == "target_plan"
    po = [ev("Owned the backlog", ["prioritisation_backlog"]), ev("Ran discovery", ["product_discovery"]),
          ev("Aligned stakeholders", ["stakeholder_management"]), ev("Coached two product owners", ["people_leadership"], CO)]
    states, sup, _, d = plan("product_ownership", po, target="PM-SPM", current="ROLE-OWNER")
    assert states["C2"] == "transferable" and sup.gaps == ["C2", "C4"] and d.primary == ["PM-PM"]   # Transferable isn't enough at senior


def test_a_product_owner_or_founder_with_demonstrated_ownership_moves_directly_to_senior_pm():
    po = [ev("Owned the payments roadmap", ["roadmap_ownership"]), ev("Owned activation outcome metrics", ["outcome_ownership"]),
          ev("Ran discovery", ["product_discovery"]), ev("Owned the backlog", ["prioritisation_backlog"]),
          ev("Aligned stakeholders", ["stakeholder_management"]), ev("Ran releases", ["delivery_execution"]),
          ev("Coached two product owners", ["people_leadership"], CO)]
    _, sup, _, d = plan("product_ownership", po, target="PM-SPM", current="ROLE-OWNER")
    assert sup.scope_dependent == ["L1"] and (d.rule, d.primary) == ("R2", ["direct"])     # never a title-based PM step
    _, sup, f, d = plan("product_ownership", po, target="PM-SPM", current="ROLE-OWNER", scope=True)
    assert sup.gaps == ["L1"] and (d.rule, d.primary) == ("R4", ["direct"])                # PM role doesn't develop L1
    founder = [ev("Set strategy across two products", ["strategy_portfolio"], {"strategy_portfolio": {"multi_product_scope": True}}),
               ev("Owned the roadmap", ["roadmap_ownership"]), ev("Ran customer discovery", ["product_discovery"]),
               ev("Owned revenue and retention targets", ["outcome_ownership"]), ev("Pitched to the board", ["executive_communication"]),
               ev("Wrote specs", ["requirements_analysis"]), ev("Ran releases", ["delivery_execution"])]
    _, sup, _, d = plan("entrepreneurship", founder, target="PM-SPM", current="ROLE-ENT", scope=True)
    assert sup.gaps == [] and d.primary == ["direct"]


# --------------------------------------------------------------------------- D35: outcome metrics and analytics
def test_analytics_alone_never_demonstrates_outcome_ownership():
    yes = plan("data_analytics", [ev("Built product dashboards", ["data_analysis"], {"data_analysis": YES})])[0]
    no = plan("data_analytics", [ev("Built product dashboards", ["data_analysis"], {"data_analysis": {"outcome_metrics_influence": False}})])[0]
    unsure = plan("data_analytics", [ev("Built product dashboards", ["data_analysis"])])
    assert (yes["C4"], no["C4"], unsure[0]["C4"]) == ("demonstrated", "transferable", "unknown")
    assert unsure[3].rule == "R1" and unsure[3].primary == []                            # "Not sure" stays Unknown: resolve first
    skill_only = plan("software_engineering", [ev("Built APIs", ["technical_engineering"])], skills=[skill("SKL-PRODUCT-METRICS")])[0]
    assert skill_only["C4"] == "transferable"                                            # a stated skill is not ownership (D31)


# --------------------------------------------------------------------------- the rules themselves
def test_product_owner_is_alternative_only_and_hypothesis_entries_are_labelled():
    ba = [ev("Wrote requirements", ["requirements_analysis"]), ev("Ran workshops", ["stakeholder_management"]),
          ev("Tracked and decided from refund metrics", ["outcome_ownership"])]
    _, sup, f, d = plan("business_analysis", ba, current="ROLE-BA")
    assert sup.gaps == ["C2"] and f["ROLE-OWNER"].entry == "hypothesis"
    assert f["ROLE-OWNER"].status == "alternative" and "PM-APM" in d.primary and "ROLE-OWNER" not in d.primary


def test_ties_are_shown_as_ties_and_held_roles_are_not_bridges():
    da = [ev("Built churn models", ["technical_engineering"]), ev("Wrote analysis specs", ["requirements_analysis"])]
    _, sup, f, d = plan("data_science", da, contexts=["data_product"], current="ROLE-DS")
    assert sup.gaps == ["C2", "C4"] and sorted(d.primary) == ["PM-APM", "ROLE-PAN"]      # incomparable: APM C2+C4 partly, PAN C4 fully
    assert {f["PM-APM"].status, f["ROLE-PAN"].status} == {"tied_primary"}
    _, _, f, d = plan(*CUSTOMER_SUCCESS, current="PM-APM")
    assert f["PM-APM"].status == "not_evaluated" and "PM-APM" not in d.primary


def test_no_eligible_bridge_keeps_direct_as_a_conditional_route():
    lines = [ev("Built APIs", ["technical_engineering"]), ev("Owned a backlog", ["prioritisation_backlog"])]
    _, sup, f, d = plan("software_engineering", lines, target="PM-SPM", current="ROLE-SWE")
    assert d.primary == ["PM-PM"]                                                          # C2 transferable, C4 gap at senior
    _, sup, f, d = plan("product_ownership", [ev("Owned the roadmap", ["roadmap_ownership"]), ev("Owned the outcome metric", ["outcome_ownership"])],
                        target="PM-SPM", current="PM-PM", scope=True)
    assert f["PM-PM"].status == "not_evaluated" and (d.rule, d.primary) == ("R4", ["direct"])


def test_no_percentages_or_counts_drive_the_rules():
    src = (ROOT / "backend/app/services/transition/feasibility.py").read_text(encoding="utf-8")
    assert "* 2" not in src and "len(" not in src.split("def decide")[1].split("trace.append")[0]
    assert ENGINE_VERSION == "transition-planner-3.0.0"


def test_decisions_are_deterministic():
    a, b = plan(*CUSTOMER_SUCCESS)[3], plan(*CUSTOMER_SUCCESS)[3]
    assert (a.rule, a.primary, a.trace) == (b.rule, b.primary, b.trace)


def test_evidence_breadth_labels_follow_d4_and_never_show_small_counts():
    assert [breadth_label(n, False) for n in (None, 4, 5, 9, 10, 24, 25, 99, 100)] == [
        None, None, "limited", "limited", "growing", "growing", "broad", "broad", "extensive"]
    assert breadth_label(500, True) == "synthetic_test_data"


# --------------------------------------------------------------------------- actions (D28, corrected content)
def builder():
    return ActionBuilder(TEMPLATES, "pm-v1")


def test_state_logic_build_demonstrate_reposition_and_basis_specific_reposition():
    it = items_for("software_engineering", [ev("Built payment APIs", ["technical_engineering"]), ev("Shipped releases", ["delivery_execution"])],
                   skills=[skill("SKL-PRODUCT-METRICS")], contexts=["platform_api"])
    acts = builder().capability(it)
    keys = {(a["requirement_key"], a["template_key"]) for a in acts}
    assert {("C1", "build.C1"), ("C1", "demonstrate.C1")} <= keys
    assert ("C6", "reposition.demonstrated") in keys and ("C6", "build.C6") not in keys       # demonstrated: never relearn
    assert ("C4", "reposition.skill") in keys                                                 # skill evidence: its own template (T4)
    build = next(a for a in acts if a["template_key"] == "build.C1")
    assert "learning_exercise" in build["labels"] and "learning exercise" in build["deliverable"]
    demo = next(a for a in acts if a["template_key"] == "demonstrate.C1")
    assert "personal_project_possible" in demo["labels"] and "personal project" in demo["deliverable"]


def test_reposition_never_invents_ownership_research_metrics_or_outcomes():
    for key in ("reposition.demonstrated", "reposition.transferable", "reposition.skill", "reposition.role"):
        t = TEMPLATES[key]
        assert "factual_accuracy" in t["labels"]
        assert re.search(r"only|without claiming|never|not evidence", t["action_text"], re.I), key
        assert "the decision you made and the result" not in t["action_text"]


def test_stage_four_covers_the_hiring_strategy_not_only_postings():
    acts = builder().entry(items_for(*CUSTOMER_SUCCESS), "Product Manager", postings=[])
    keys = [a["template_key"] for a in acts]
    for k in ("apply.no_verified_postings", "apply.position_application", "apply.case_study", "apply.referrals",
              "apply.interview_preparation", "apply.internal_route", "apply.alternative_routes"):
        assert k in keys, k
    assert "product case" in next(a for a in acts if a["template_key"] == "apply.interview_preparation")["action"]
    posts = [{"source_key": "J11", "company_name": "Target", "is_current_opportunity": True, "last_verified_at": "2026-11-16T00:00:00",
              "eligibility": {"outcome": "unclear"}}]
    keys = [a["template_key"] for a in builder().entry(items_for(*CUSTOMER_SUCCESS), "Product Manager", posts)]
    assert "apply.current_postings" in keys and "apply.check_criteria" in keys


def test_bridge_routes_say_what_remains_and_what_to_seek():
    partial = [{"key": "C2", "title": "Product vision, strategy and roadmap", "develops": "Roadmapping methods.",
                "incomplete": "Strategic ownership.", "seek": "Ask to own a feature roadmap."}]
    acts = builder().entry(items_for(*CUSTOMER_SUCCESS), "Product Manager", [], bridge={
        "bridge_title": "Associate Product Manager", "reasons": ["Develops only partly \"Product vision, strategy and roadmap\"."],
        "partials": partial, "source_keys": ["pm-v1:F1"]})
    seek = next(a for a in acts if a["template_key"] == "apply.bridge_seek")
    assert seek["requirement_key"] == "C2" and "only partly" in seek["action"] and "Ask to own a feature roadmap." in seek["action"]


FORBIDDEN = re.compile(r"\bcourses?\b|bootcamp|certif|udemy|coursera|₹|\$|\bfees?\b|\bcost of\b|\bweeks?\b|\bdays?\b|guarantee|"
                       r"placement|\bhired\b|salary|\bwithin \d", re.I)


def test_templates_name_no_courses_costs_timelines_or_outcome_promises():
    for t in ACTIONS["templates"]:
        text = " ".join([t["objective"], t["action"], t["deliverable"]])
        assert not FORBIDDEN.search(text), (t["key"], FORBIDDEN.search(text).group(0))


@pytest.mark.parametrize("key", [r["key"] for r in PACK["requirements"]])
def test_every_requirement_has_build_and_demonstrate_templates(key):
    assert f"build.{key}" in TEMPLATES and f"demonstrate.{key}" in TEMPLATES


def _synthetic(rid, develops=(), partly=(), entry="source_stated_context", may_be_primary=True, signals=()):
    """A synthetic bridge for rule tests only (never content)."""
    return {"bridge_role_id": rid, "bridge_title": rid, "target_role_id": "PM-PM", "develops": list(develops), "partly_develops": list(partly),
            "entry_evidence": entry, "may_be_primary": may_be_primary, "entry_requirements_stated": "stated",
            "entry_signal_families": list(signals), "entry_signal_requirements": [], "suitable_backgrounds": "any",
            "partial_development": {k: {"develops": "d", "incomplete": "i", "seek": "s"} for k in partly}, "source_keys": ["pm-v1:F1"]}


def _decide_with(bridges, family="customer_success"):
    it = items_for(family, CUSTOMER_SUCCESS[1])                                             # G = {C2, C4}
    sup = direct_support(it)
    f = evaluate_bridges(bridges, it, [{"record_id": "j", "title": family, "role_family": family}], None, sup)
    return sup, {x.bridge["bridge_role_id"]: x for x in f}, decide(sup, f, {i["key"]: i["title"] for i in it})


def test_requirement_level_dominance_never_sums_levels():
    # A: C2 fully, C4 not at all; B: C4 partly only. Incomparable per requirement, so tied; a summed score would pick A.
    sup, f, d = _decide_with([_synthetic("A", develops=["C2"]), _synthetic("B", partly=["C4"])])
    assert sup.gaps == ["C2", "C4"] and sorted(d.primary) == ["A", "B"]
    sup, f, d = _decide_with([_synthetic("A", develops=["C2", "C4"]), _synthetic("B", partly=["C4"])])
    assert d.primary == ["A"] and f["B"].status == "alternative"                          # dominated on every requirement


def test_alternative_only_and_hypothesis_bridges_are_never_primary_on_their_own():
    sup, f, d = _decide_with([_synthetic("ALT", develops=["C2", "C4"], may_be_primary=False, entry="employer_stated", signals=["customer_success"])])
    assert f["ALT"].entry == "evidenced" and d.rule == "R4" and d.primary == ["direct"] and f["ALT"].status == "alternative"
    sup, f, d = _decide_with([_synthetic("HYP", develops=["C2", "C4"], entry="hypothesis", signals=["customer_success"])])
    assert f["HYP"].entry == "hypothesis" and d.primary == ["direct"] and f["HYP"].status == "alternative"
