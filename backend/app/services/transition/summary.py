"""Above-the-fold summary of a saved transition plan (product Phase 5: the Career Intelligence Report).

A PRESENTATION PROJECTION of one stored run, built at read time. It makes no decision of its own: the selected route
is the planner's stored decision (D27-D41); strengths and priority areas are the stored capability states, ordered only
by the approved decision-rights flag (D31) and then by requirement key; the stage previews are the selected route's own
stored actions. Nothing is scored, weighted, counted for ranking or recalculated, so the browser never has to select a
route, a strength or a gap itself.

POC iteration additions, still projections of stored data:
  * canonical stage names (Build, Demonstrate, Reposition, Hiring transition) with explanatory subtitles;
  * `development`: for a bridge route, each requirement the plan found missing, classified from the route's OWN stored
    requirement-level development (fully / partly / not developed), and a reason sentence generated from it, so the
    summary can never say more than the route card (partial development is never described as closing a gap);
  * `career_histories`: whether comparable career histories can be shown. Only empirical observed routes that passed
    the privacy minimum (a D4 label) are listed; anything else, including every synthetic route, gives the agreed
    "not enough eligible career histories" state. No counts.

Works for every saved run (no migration): it reads the same fields the report already returns.
"""
from typing import List, Optional

STAGE_TITLES = {0: "Resolve missing information first", 1: "Build", 2: "Demonstrate", 3: "Reposition", 4: "Hiring transition"}
STAGE_SUBTITLES = {0: "Profile tasks, not a stage of the plan", 1: "Build foundation", 2: "Gain practical experience",
                   3: "Present your confirmed experience effectively", 4: "Move towards the target role"}
DECISION_KIND = {"R1": "resolve_first", "R2": "direct", "R3": "bridge", "R4": "direct_conditional"}
EMPIRICAL_BANDS = ("limited", "growing", "broad", "extensive")          # D4; synthetic labels are never empirical


def selected_route(routes: List[dict]) -> Optional[dict]:
    """The route the report details: the planner's primary (the first of tied primaries, in the stored order); when no
    primary was chosen (resolve first), the direct route, which carries the resolve-first and stage 4 actions."""
    primary = [r for r in routes if r["primary_status"] in ("primary", "tied_primary")]
    if primary:
        return min(primary, key=lambda r: r["rank"])
    return next((r for r in routes if r["route_kind"] == "direct"), None)


def _item(i: dict) -> dict:
    return {"key": i["key"], "title": i["title"], "decision_rights": bool(i.get("decision_rights"))}


def _dr_first(items: List[dict]) -> List[dict]:
    return sorted(items, key=lambda i: (not i.get("decision_rights"), i["key"]))


def _quoted(titles: List[str]) -> str:
    q = [f"\"{t}\"" for t in titles]
    return q[0] if len(q) == 1 else ", ".join(q[:-1]) + " and " + q[-1]


def development(decision: dict, route: Optional[dict], titles: dict) -> Optional[dict]:
    """Requirement-level development of the selected BRIDGE route, from its stored feasibility. None otherwise."""
    if not route or route["route_kind"] != "bridge":
        return None
    support = next((s for s in (decision.get("trace") or []) if s.get("rule") == "support"), {})
    f = route.get("feasibility") or {}
    full, partly = set(f.get("develops_fully") or []), set(f.get("develops_partly") or [])
    partial_notes = {p["key"]: p for p in f.get("partial_development") or []}

    def classify(keys):
        out = {"fully": [], "partly": [], "not_developed": []}
        for k in keys:
            entry = {"key": k, "title": titles.get(k, k)}
            if k in full:
                out["fully"].append(entry)
            elif k in partly:
                p = partial_notes.get(k, {})
                out["partly"].append({**entry, "remaining": p.get("incomplete"), "seek": p.get("seek")})
            else:
                out["not_developed"].append(entry)
        return out

    core = classify(support.get("decision_rights_gaps") or [])
    practice = classify(support.get("practice_gaps") or [])
    sentences = [support["explanation"]] if support.get("explanation") else []
    t = lambda xs: _quoted([x["title"] for x in xs])          # noqa: E731
    if core["fully"]:
        sentences.append(f"This bridge role develops {t(core['fully'])} in the role itself.")
    if core["partly"]:
        sentences.append(f"It {'also ' if core['fully'] else ''}helps develop parts of {t(core['partly'])}; some aspects will "
                         "still need to be demonstrated, so the route lists what remains and what to seek.")
    if core["not_developed"]:
        sentences.append(f"It doesn't develop {t(core['not_developed'])}; your stage 2 actions cover "
                         f"{'it' if len(core['not_developed']) == 1 else 'them'}.")
    if practice["fully"]:
        sentences.append(f"It also develops {t(practice['fully'])}.")
    if practice["partly"]:
        sentences.append(f"It helps with parts of {t(practice['partly'])}.")
    return {"core": core, "practice": practice, "reason": " ".join(sentences)}


def career_histories(run: dict) -> dict:
    """Comparable career histories: shown only from empirical observed routes that passed the privacy minimum."""
    synthetic = run["data_mode"] != "empirical"
    shown = [] if synthetic else [
        {"steps": [s["title"] for s in r["steps"]], "breadth": r["evidence_breadth_label"]}
        for r in run["routes"] if r["route_kind"] == "observed" and r.get("evidence_breadth_label") in EMPIRICAL_BANDS]
    return {"status": "shown" if shown else "not_enough_evidence", "routes": shown, "synthetic_mode": synthetic}


def report_summary(run: dict) -> dict:
    routes = run["routes"]
    decision = run["decision"] or {}
    items = [i for i in (run["assessment"] or {}).get("items", []) if i["state"] != "not_applicable"]
    titles = {i["key"]: i["title"] for i in items}
    direct = next((r for r in routes if r["route_kind"] == "direct"), None)
    steps = direct["steps"] if direct else []
    target = steps[-1] if steps else {"role_id": run["target_role_id"], "title": run["target_role_id"]}
    current = steps[0] if len(steps) >= 2 else None
    chosen = selected_route(routes)
    primaries = sorted((r for r in routes if r["primary_status"] in ("primary", "tied_primary")), key=lambda r: r["rank"])
    selected = [{"position": r["rank"], "route_kind": r["route_kind"], "bridge_role_id": r["bridge_role_id"],
                 "steps": [s["title"] for s in r["steps"]]} for r in primaries]
    actions = (chosen or {}).get("actions", [])
    previews = []
    for stage in (1, 2, 3, 4):
        here = [a for a in actions if a["stage"] == stage]
        previews.append({"stage": stage, "title": STAGE_TITLES[stage], "subtitle": STAGE_SUBTITLES[stage], "action_count": len(here),
                         "first_objective": here[0]["objective"] if here else None})
    dev = development(decision, chosen if primaries else None, titles)
    return {
        "current_role": {"role_id": current["role_id"], "title": current["title"]} if current else None,
        "target_role": {"role_id": target["role_id"], "title": target["title"]},
        "decision": {"rule": decision.get("rule"), "kind": DECISION_KIND.get(decision.get("rule"), "resolve_first"),
                     "reason": dev["reason"] if dev else decision.get("explanation"), "selected": selected,
                     "detailed_route_position": chosen["rank"] if chosen else None},
        "development": {k: dev[k] for k in ("core", "practice")} if dev else None,
        "strengths": [_item(i) for i in _dr_first([i for i in items if i["state"] == "demonstrated"])],
        "priority": {
            "gaps": [_item(i) for i in _dr_first([i for i in items if i["state"] == "gap"])],
            "transferable": [_item(i) for i in _dr_first([i for i in items if i["state"] == "transferable"])],
            "unknown": [{**_item(i), "reason": i.get("reason")} for i in _dr_first([i for i in items if i["state"] == "unknown"])],
        },
        "resolve_first": any(a["stage"] == 0 for a in actions),
        "stage_previews": previews,
        "career_histories": career_histories(run),
    }
