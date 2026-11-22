"""Compose a transition plan (Phase 4): routes with their rule outcomes, four-stage actions and employer eligibility,
as one structured, deterministic payload. Pure: the same inputs always give the same payload.

Three things stay separate in the payload, as in the decision register (D4, D25, D29):
    feasibility (per route)      from the capability assessment and the bridge catalogue; decides the primary route
    evidence breadth (per route) observed routes only; a label shown beside the route; never used for ordering
    eligibility (per posting)    employer-specific; never changes a route or a capability state
Route bases are never mixed: profile_led routes (direct and curated bridges) and observed routes (privacy-safe
reference evidence of ONE cohort). A profile-led route never carries supporting profiles; synthetic observed routes
are labelled synthetic.
"""
from typing import Dict, List, Optional, Sequence

from app.services.transition.actions import ActionBuilder
from app.services.transition.feasibility import (
    ENGINE_VERSION, PROFILE_LED_BREADTH, BridgeFinding, Decision, Support, breadth_label,
)

ENTRY_EVIDENCE_TEXT = {"employer_stated": "Entry requirements stated by one employer's framework",
                       "source_stated_context": "Described by the source as an entry route (UK public-sector context)",
                       "target_plan": "Your own Product Manager plan", "hypothesis": "A curated hypothesis, not established by the source"}
PROGRESSION_TEXT = {"source_stated": "The source places this role on the level ladder to the target",
                    "hypothesis": "A curated hypothesis: no cited source describes the move to the target"}


def partials(f: BridgeFinding, support: Support, titles: Dict[str, str]) -> List[dict]:
    notes = f.bridge.get("partial_development") or {}
    return [{"key": k, "title": titles[k], **notes[k]} for k in support.gaps + support.practice_gaps
            if f.levels.get(k) == "partial" and k in notes]

STATUS_ORDER = {"primary": 0, "tied_primary": 0, "alternative": 1, "not_recommended": 2, "not_evaluated": 3}


def _titles(keys: Sequence[str], items: Dict[str, dict]) -> List[str]:
    return [items[k]["title"] for k in keys if k in items]


def compose(*, assessment: dict, support: Support, findings: List[BridgeFinding], decision: Decision, builder: ActionBuilder,
            target: dict, position: dict, postings: List[dict], observed: List[dict], data_mode: str,
            versions: dict, profile_version: str, source_ids: Dict[str, str], scope_portfolio: bool = False) -> dict:
    items = [i for i in assessment["requirements"] if i["state"] != "not_applicable"]
    by_key = {i["key"]: i for i in items}
    undetermined = support.level == "undetermined"
    resolve = builder.resolve(items)
    start = [position["role_id"]] if position.get("status") == "known" and position.get("role_id") else []

    titles = {i["key"]: i["title"] for i in items}

    def actions_for(bridge: Optional[BridgeFinding]) -> List[dict]:
        acts = list(resolve)
        if not undetermined:
            acts += builder.capability(items, develops=bridge.bridge["develops"] if bridge else (),
                                       bridge_title=bridge.bridge["bridge_title"] if bridge else None)
        acts += builder.entry(items, target["title"], postings,
                              bridge=None if bridge is None else {
                                  "bridge_title": bridge.bridge["bridge_title"], "reasons": bridge.reasons,
                                  "partials": partials(bridge, support, titles), "source_keys": bridge.bridge["source_keys"]})
        return sorted(acts, key=lambda a: a["stage"])           # stable: order within a stage is kept

    def evidence_rows(keys: Sequence[str], why: str) -> List[dict]:
        return [{"evidence_type": "role_requirement", "target_role_source_id": source_ids[k], "summary": f"{k}: {why}"}
                for k in sorted(set(keys)) if k in source_ids]

    routes = []
    # ---- direct (profile-led), always evaluated
    direct_status = ("primary" if "direct" in decision.primary else "not_evaluated" if undetermined else "alternative")
    direct_acts = actions_for(None)
    routes.append({
        "route_id": "direct", "route_kind": "direct", "route_basis": "profile_led", "primary_status": direct_status,
        "bridge_role_id": None, "steps": start + [target["role_id"]],
        "reason": decision.explanation if direct_status == "primary" else None,
        "explanation": (f"Move directly into {target['title']}. " + support.explanation),
        "feasibility": {"support": support.level, "conditional": support.level == "conditional",
                        "decision_rights": support.decision_rights, "decision_rights_gaps": support.gaps,
                        "practice_gaps": support.practice_gaps, "blocked": support.blocked,
                        "scope_dependent": support.scope_dependent, "moves": 1},
        "tradeoffs": ([{"kind": "remaining_gaps", "text": "Possible, but your confirmed profile doesn't yet show: "
                        + ", ".join(_titles(support.gaps, by_key)) + ". The stage 2 actions show how to build that evidence."}]
                      if support.gaps else [])
                     + ([{"kind": "scope", "text": "Portfolio scope (several products, or a complex, high-risk product) is assessed only "
                          "if the role you target has it."}] if support.scope_dependent else [])
                     + [{"kind": "moves", "text": "One move."}],
        "evidence_breadth_label": PROFILE_LED_BREADTH, "limitations": ["Comparable career-history evidence is limited or unavailable "
                                                                       "for this profile-led route."],
        "evidence": evidence_rows([a for x in direct_acts for a in x["source_keys"]], "cited by this route's actions"),
        "actions": direct_acts})
    # ---- curated bridges (profile-led)
    for f in findings:
        b = f.bridge
        acts = [] if f.status == "not_evaluated" else actions_for(f)
        remaining = [k for k in support.gaps if f.levels.get(k) != "full"]
        routes.append({
            "route_id": b["bridge_role_id"], "route_kind": "bridge", "route_basis": "profile_led", "primary_status": f.status,
            "bridge_role_id": b["bridge_role_id"], "steps": start + [b["bridge_role_id"], target["role_id"]],
            "reason": " ".join(f.reasons) if f.status in ("primary", "tied_primary") else None,
            "explanation": f"Move into {b['bridge_title']} first, then {target['title']}. {b['suitability_note']}",
            "feasibility": {"develops_fully": f.develops(support.gaps + support.practice_gaps, "full"),
                            "develops_partly": f.develops(support.gaps + support.practice_gaps, "partial"),
                            "decision_rights_levels": {k: f.levels[k] for k in support.gaps},
                            "partial_development": partials(f, support, titles),
                            "entry": f.entry, "entry_explanation": f.entry_explanation, "entry_records": f.entry_records,
                            "entry_evidence": b.get("entry_evidence"), "entry_evidence_text": ENTRY_EVIDENCE_TEXT.get(b.get("entry_evidence")),
                            "progression_evidence": b.get("progression_evidence"),
                            "progression_evidence_text": PROGRESSION_TEXT.get(b.get("progression_evidence")),
                            "may_be_primary": bool(b.get("may_be_primary", True)),
                            "entry_requirements_stated": b["entry_requirements_stated"],
                            "responsibilities": list(b["responsibilities"]), "develops": list(b["develops"]),
                            "partly_develops": list(b["partly_develops"]), "develops_note": b["develops_note"],
                            "already_held": f.already_held, "leads_to": target["role_id"], "reasons": f.reasons, "moves": 2},
            "tradeoffs": [{"kind": "moves", "text": "Two moves: the bridge role, then the target."},
                          {"kind": "bridge_note", "text": b["tradeoff_note"]}]
                         + ([{"kind": "remaining_gaps", "text": "Not fully developed by the bridge role: "
                              + ", ".join(_titles(remaining, by_key)) + "."}] if remaining else []),
            "evidence_breadth_label": PROFILE_LED_BREADTH,
            "limitations": ["A curated bridge role, not observed in comparable career histories."],
            "evidence": evidence_rows(b["source_keys"], "defines the bridge role's responsibilities"),
            "actions": acts})
    # ---- observed routes (one cohort; shown beside, never selecting)
    for o in observed:
        synthetic = o["is_synthetic_test_data"]
        count = o.get("observation_count")
        routes.append({
            "route_id": "observed:" + o["route_id"], "route_kind": "observed", "route_basis": "observed", "primary_status": "not_evaluated",
            "bridge_role_id": None, "steps": list(o["route"]), "reason": None,
            "explanation": ("Observed in synthetic test career histories (not real evidence)." if synthetic
                            else "Observed in consented, de-identified career histories."),
            "feasibility": {"observed_only": True},
            "tradeoffs": [{"kind": "moves", "text": f"{len(o['route']) - 1} moves as observed."}],
            "evidence_breadth_label": breadth_label(count, synthetic),
            "evidence_class": o["evidence_class"] if count is not None else "insufficient",
            "supporting_profile_count": count or 0,
            "limitations": ["How often a route was taken is not a measure of how suitable it is for you."],
            "evidence": [{"evidence_type": "observed_sequence", "target_role_source_id": None,
                          "summary": " > ".join(o["route"]) + (" (synthetic test data)" if synthetic else "")}],
            "actions": []})
    routes.sort(key=lambda r: (STATUS_ORDER[r["primary_status"]], r["route_kind"] == "observed", r["route_id"]))

    eligibility = {"note": "Employer-specific. Each posting is assessed on its own stated criteria; this never changes your routes "
                           "or capability assessment, and results are not added up across employers.",
                   "postings": postings}
    summary = {"items": [{"key": i["key"], "title": i["title"], "requirement_class": i["requirement_class"], "state": i["state"],
                          "basis": i.get("basis"), "reason": i.get("reason"), "decision_rights": bool(i.get("decision_rights")),
                          "records": sorted({s["record_id"] for s in i.get("supporting", [])}),
                          "read_by": sorted({s.get("read_by") or "you" for s in i.get("supporting", []) if s["kind"] == "evidence"}),
                          "display_state": i.get("display_state"), "needs_context": i.get("needs_context"),
                          "blocking_records": list(i.get("blocking_records", []))} for i in items],
               "support": support.level, "coverage": assessment["coverage"], "contexts": assessment["contexts"],
               "target_level": assessment["target_level"]}
    source_versions = {s["key"]: {"status": s["status"], "last_verified_at": s["last_verified_at"]}
                       for i in assessment["requirements"] for s in i.get("sources", [])}
    for p in postings:
        source_versions[p["source_key"]] = {"status": p["effective_status"], "last_verified_at": p["last_verified_at"]}
    return {
        "data_mode": data_mode, "target_role_id": target["role_id"], "engine_version": ENGINE_VERSION,
        "profile_version": profile_version, "knowledge_pack": assessment["knowledge_pack"], "source_inventory": assessment["source_inventory"],
        "taxonomy_version": assessment["taxonomy_version"], **versions,
        "constraints": {"contexts": assessment["contexts"], "portfolio_scope": bool(scope_portfolio)},
        "assessment": summary, "eligibility": eligibility, "source_versions": source_versions,
        "decision": {"rule": decision.rule, "primary": decision.primary, "explanation": decision.explanation, "trace": decision.trace,
                     "current_position": {k: position.get(k) for k in ("status", "role_id", "role_family", "reason")}},
        "privacy_state": {"own_contributions_excluded": True, "observed_routes": len(observed),
                          "observed_cohort_synthetic": data_mode != "empirical"},
        "routes": routes,
    }
