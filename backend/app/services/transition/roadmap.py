"""The career transition roadmap (product redesign): a PRESENTATION PROJECTION of one saved plan.

    build_roadmap(run, evidence, requirements, goal, today)

It decides nothing about routes, capability or eligibility: the selected route is the planner's stored decision
(D27-D41), the capability states are the stored ones, and nothing here is scored, weighted or estimated. It selects
and words what the user sees:

  recommendation   the selected next move, the route, one or two reasons drawn from the stored decision and the route's
                   own development data, what the route offers (curated), the main trade-off, and a plain-language
                   "why this path" disclosure
  build_on         at most two strengths (stored Demonstrated), each ONLY with a record that is eligible now (provenance)
  focus            at most two experiences to develop (stored Gap; decision-rights first, D31), each with why it matters
                   (approved role knowledge) and where it could be built (the route's own actions)
  related          at most two Transferable items: what carries over, from which eligible record, and what it does NOT
                   establish
  not_assessed     Unknown items, with the reason; never a deficit
  stages           the four canonical stages; per stage a few actions chosen by a fixed presentation order (the focus
                   requirements first; at most 1, 2 or 3 highlighted according to the user's stated weekly time), the
                   rest one click away. Employer-posting actions are not part of the roadmap (employer matching is
                   retired from the product)
  checkpoints      one per stage that has actions, with what the user should have by then; dates only if the USER set
                   them. The goal date is the user's planning target, never a forecast
  timeline         the user's goal; duration evidence is reported as unavailable (saved plans carry no approved
                   empirical duration, and fictional test data can never support one)

Personal statements use only `evidence` (records eligible NOW, see provenance.py); a stored claim or action whose records
are no longer eligible is dropped rather than repeated.
"""
from datetime import date
from typing import Dict, List, Optional

from app.services.transition import learning
from app.services.transition.summary import STAGE_SUBTITLES, STAGE_TITLES, career_histories, development, selected_route

EMPLOYER_POSTING_ACTIONS = {"apply.current_postings", "apply.check_criteria", "apply.no_verified_postings"}
STAGE4_ORDER = ["apply.bridge_role", "apply.bridge_seek", "apply.position_application", "apply.case_study",
                "apply.interview_preparation", "apply.internal_route", "apply.referrals", "apply.alternative_routes"]
HIGHLIGHT = {"1_3": 1, "4_6": 2, "7_10": 3, "more_than_10": 3, "varies": 2, None: 3}
TIMEFRAME_MONTHS = {"within_3_months": (3, 3), "within_6_months": (6, 6), "within_12_months": (12, 12),
                    "within_18_24_months": (18, 24), "flexible": None}
TIMEFRAME_LABEL = {"within_3_months": "Within 3 months", "within_6_months": "Within 6 months",
                   "within_12_months": "Within 12 months", "within_18_24_months": "Within 18–24 months",
                   "flexible": "Flexible / not sure yet"}
WEEKLY_LABEL = {"1_3": "1–3 hours a week", "4_6": "4–6 hours a week", "7_10": "7–10 hours a week",
                "more_than_10": "More than 10 hours a week", "varies": "It varies / not sure"}
CHECKPOINTS = {1: "Foundations in place", 2: "Evidence from real work", 3: "Your story is ready", 4: "Moving into the role"}
# Trace-curated guidance on what to keep (labelled as Trace guidance in the interface; never a claim about the user).
KEEP = {
    "build": "Your notes and the output, labelled as a learning exercise. It isn't professional experience.",
    "demonstrate": "The output, the date, who reviewed it and what happened. Add it to your Trace profile once it's real "
                   "work; label a personal project as a personal project.",
    "reposition": "The rewritten line next to the confirmed record it describes, so every claim can be traced back.",
    "apply": "A dated record of applications, conversations and what you learned from each.",
    "seek": "What you owned, the decisions you made and what happened, once you hold the responsibility. Add it to your "
            "Trace profile.",
}


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    days = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(year, month, min(d.day, days[month - 1]))


def _quoted(titles: List[str]) -> str:
    return titles[0] if len(titles) == 1 else ", ".join(titles[:-1]) + " and " + titles[-1]


def lower_first(s: str) -> str:
    return s[0].lower() + s[1:] if s else s


def _a(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def _evidence(item: dict, evidence: Dict[str, dict], limit: int = 2) -> List[dict]:
    return [evidence[r] for r in item.get("records", []) if r in evidence][:limit]


def _dr_first(items: List[dict]) -> List[dict]:
    return sorted(items, key=lambda i: (not i.get("decision_rights"), i["key"]))


STATE_TEXT = {
    "evidence_found": "Evidence found in your confirmed experience",
    "needs_context": "Related experience found; it needs more context to show the whole capability",
    "not_yet_demonstrated": "Not yet demonstrated in the available evidence",
    "cannot_assess_yet": "Can't assess yet",
}


# Scope-question key -> the responsibility area it belongs to (taxonomy v2/v3). An answer is stored on that area.
QUALIFIER_AREA = {
    "outcome_metrics_influence": "data_analysis",
    "multi_product_scope": "strategy_portfolio",
    "cross_team": "delivery_execution",
    "coaching_mentoring": "people_leadership",
    "tracks_outcomes": "business_case_economics",
}


def capabilities(items: List[dict], evidence: Dict[str, dict], requirements: Dict[str, dict]) -> List[dict]:
    """Each assessed capability with its plain-language state and the user's own records behind it (only records
    eligible now: confirmed, and categorised by the user or read by Trace). "Not yet demonstrated in the available
    evidence" never means the person lacks the experience."""
    out = []
    for i in items:
        state = i.get("display_state") or ("evidence_found" if i["state"] == "demonstrated" else "needs_context"
                                           if i["state"] == "transferable" else "not_yet_demonstrated" if i["state"] == "gap"
                                           else "cannot_assess_yet")
        ev = []
        for rid in i.get("records", []):
            e = evidence.get(rid)
            if not e:
                continue
            ev.append({"id": rid, "kind": e["kind"], "text": e["text"], "source": e.get("source"), "basis": e.get("basis"),
                       "shows": e.get("categorised_as") or [], "related": e.get("related_areas") or [],
                       "why": [{"area": a, **w} for a, w in (e.get("why") or {}).items()]})
        if state in ("evidence_found", "needs_context") and not ev:
            state = "not_yet_demonstrated" if i["state"] != "unknown" else "cannot_assess_yet"
        nc = i.get("needs_context") or {}
        questions = nc.get("questions", []) if state == "needs_context" else []
        asks = []
        if nc.get("kind") == "scope_question" and state == "needs_context":
            line = next((e for e in ev if e["kind"] == "evidence"), None)
            for key, text in zip(nc.get("question_keys") or [], questions):
                area = QUALIFIER_AREA.get(key)
                areas = list((line or {}).get("areas") or [])
                if area and area not in areas:
                    areas.append(area)
                asks.append({"key": key, "text": text, "area": area, "areas": areas,
                             "evidence_id": line["id"] if line else None})
        out.append({"key": i["key"], "title": i["title"], "state": state, "state_text": STATE_TEXT[state],
                    "decision_rights": bool(i.get("decision_rights")), "description": requirements.get(i["key"], {}).get("description"),
                    "evidence": ev[:12], "evidence_count": len(ev),
                    "questions": questions, "asks": asks,
                    "context_kind": nc.get("kind") if state == "needs_context" else None})
    order = {"evidence_found": 0, "needs_context": 1, "not_yet_demonstrated": 2, "cannot_assess_yet": 3}
    return sorted(out, key=lambda c: (not c["decision_rights"], order[c["state"]], c["key"]))


# What "done" looks like and how to do it at work, per capability (curated by Trace; never a claim about the user).
CAPABILITY_PRACTICE = {
    "C1": ("talk to the people who use the product and turn what you learn into a decision",
           "notes from at least five user conversations and the decision they changed"),
    "C2": ("own the plan for one product area: what to build next, in what order, and why",
           "the roadmap or plan you owned, with the reasoning for its order"),
    "C3": ("turn a messy problem into a clear requirement and a priority call",
           "a requirements document and the trade-off you made"),
    "C4": ("choose how success will be measured for something you ship, and use the result to decide what happens next",
           "the success measure you chose, the result, and the decision it led to"),
    "C5": ("lead a decision that needs several functions to agree",
           "a short note of who you aligned, on what, and the outcome"),
    "C6": ("take one piece of work from idea to release and follow it after launch",
           "the release plan and what you learned after launch"),
    "L1": ("take responsibility for more than one product area, or one with real risk",
           "the scope you owned and the decisions across it"),
    "L2": ("coach or mentor another product person through a piece of work",
           "what you coached and how their work changed"),
    "L3": ("present a recommendation to senior leaders and get a decision",
           "the recommendation and the decision it got"),
    "L4": ("plan work that depends on several teams, and keep it on track",
           "the cross-team plan and how dependencies were handled"),
}


def _clip(text: str, n: int = 90) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def apply_support_depth(roadmap: dict, depth: str, target_title: Optional[str]) -> None:
    """Label the move. Limited support keeps the evidence and a positioning note, and never a capability status."""
    title = target_title or "this role"
    if depth == "deep":
        roadmap["support"] = {"depth": "deep", "label": "Supported move",
                              "text": f"Trace can assess this move toward {title} from your confirmed experience."}
        return
    evidence = []
    for c in roadmap.get("capabilities") or []:
        c["state"] = "positioning"
        c["state_text"] = "Positioning"
        for e in c.get("evidence") or []:
            if e.get("kind") == "evidence" and len(evidence) < 4:
                evidence.append({"text": e.get("text"), "source": e.get("source")})
    roadmap["support"] = {
        "depth": "limited", "label": "Limited support",
        "text": (f"Limited support for a move to {title}. Trace shows the experience that positions you for it. "
                 "It does not assign a capability status on this move."),
    }
    roadmap["next_actions"] = [{
        "key": "positioning", "capability": "How to talk about this move", "kind": "reposition",
        "timing": "before_applying", "decision_rights": False,
        "action": f"Write a short note on why your experience is relevant to {title}, using your own lines.",
        "why": "This move is outside the paths Trace assesses in full. The note is positioning, not a judgement of capability.",
        "builds_on": evidence,
        "keep": "The lines from your profile you would actually use.",
        "how": "One page: the problem, what you did, and what it shows for the role you want.",
        "done_when": "You can explain the move in a few minutes without claiming a status Trace has not assessed.",
    }]


def next_actions(caps: List[dict], move: Optional[dict], target_title: str, rule: Optional[str], route_steps: List[dict]) -> List[dict]:
    """Specific next actions from the plan's own assessment (Phase 5). One per capability that matters, in four kinds:
    reposition (evidence found: show it for the target role), demonstrate (related evidence: make it convincing), expand
    (the scope the target needs), build (not yet demonstrated). Each names the action, why it matters for the target,
    the user's own evidence it builds on (with the job it came from), how to do it at work, and what done looks like.
    Never generic advice for a capability the user has already shown, and never a claim the user lacks something."""
    out = []
    used = set()                                       # each action quotes a different line where it can
    bridge = route_steps[1]["title"] if len(route_steps) > 2 else None
    for c in caps:
        key, title = c["key"], c["title"]
        practice = CAPABILITY_PRACTICE.get(key)
        if not practice:
            continue
        doing, keep = practice
        ev = c.get("evidence") or []
        lines = [e for e in ev if e["kind"] == "evidence"]
        first = next((e for e in lines if e["id"] not in used), lines[0] if lines else None)
        if first:
            used.add(first["id"])
        where = f" at {first['source']}" if first and first.get("source") else ""
        quote = f"“{_clip(first['text']).rstrip('.;:')}”" if first else None
        if c["state"] == "evidence_found":
            kind, timing = "reposition", "before_applying"
            action = (f"Turn your work{where} into a short case study that shows {title[0].lower() + title[1:]}"
                      + (f", starting from {quote}." if quote else "."))
            why = f"{target_title} roles look for {title[0].lower() + title[1:]}; your confirmed experience already shows it."
            how = "Write the problem, what you decided and why, and the result, in your own words; keep it to one page."
            done = "A one-page case study you can talk through in five minutes, with the result stated plainly."
        elif c["state"] == "needs_context" and c.get("context_kind") == "scope_question":
            kind, timing = "expand", "in_parallel"
            q = (c.get("questions") or [None])[0]
            action = (f"Check whether your experience already covers this: {q} If it does, add that to the line in your "
                      "profile; if not, look for a chance to " + doing + ".") if q else f"Look for a chance to {doing}."
            why = f"Your confirmed experience is related to {title[0].lower() + title[1:]}, but it doesn't say whether it reached the scope this role needs."
            how = f"In your current role, ask to {doing}."
            done = f"Your profile states the scope plainly, or you have {keep}."
        elif c["state"] == "needs_context":
            kind, timing = "demonstrate", "start_now"
            action = (f"Build on your related work{where}" + (f" ({quote})" if quote else "") + f": next time, {doing}.")
            why = f"It's related to {title[0].lower() + title[1:]}, but on its own it doesn't show the whole capability."
            how = f"Pick one piece of work in the next few months where you can {doing}."
            done = f"You have {keep}."
        elif c["state"] == "not_yet_demonstrated":
            kind, timing = "build", "start_now"
            action = (f"Ask for one piece of work where you {doing}."
                      + (f" The {bridge} role on your route develops this as part of the role." if bridge else ""))
            why = (f"{target_title} roles look for {title[0].lower() + title[1:]}, and it isn't shown in the experience "
                   "you've confirmed so far. That's about the evidence available, not your ability.")
            how = f"In your current role, or a clearly labelled personal project if that isn't possible: {doing}."
            done = f"You have {keep}."
        else:
            continue
        out.append({"key": key, "capability": title, "kind": kind, "timing": timing, "action": action, "why": why,
                    "builds_on": [{"text": e["text"], "source": e.get("source")} for e in ([first] if first else []) + [x for x in lines if x is not first][:1]],
                    "keep": keep[0].upper() + keep[1:], "how": how, "done_when": done, "decision_rights": c["decision_rights"]})
    rank = {"build": 0, "demonstrate": 1, "expand": 2, "reposition": 3}
    out.sort(key=lambda a: (rank[a["kind"]], not a["decision_rights"], a["key"]))
    hiring = {"key": "hiring", "capability": None, "kind": "hiring", "timing": "hiring_transition",
              "action": ("Talk to two or three people doing the role you want about how they made the move"
                         + (", and how the role differs between companies." if (move or {}).get("kind") in ("same_role", "lateral") else ".")),
              "why": "Hiring for product roles leans on how clearly you can explain your decisions and their results.",
              "builds_on": [], "keep": "Notes from those conversations and the questions they raised",
              "how": "Ask for 20 minutes; bring one of your case studies and ask what they'd want to see.",
              "done_when": "You can explain, in your own words, why you're making this move and what you'd bring.",
              "decision_rights": False}
    return out[:6] + [hiring]


def build_roadmap(run: dict, evidence: Dict[str, dict], requirements: Dict[str, dict], goal: Optional[dict],
                  today: date) -> dict:
    routes, decision = run["routes"], run["decision"] or {}
    items = [i for i in (run["assessment"] or {}).get("items", []) if i["state"] != "not_applicable"]
    by_state = {s: _dr_first([i for i in items if i["state"] == s]) for s in ("demonstrated", "transferable", "gap", "unknown")}
    chosen = selected_route(routes)
    primaries = sorted((r for r in routes if r["primary_status"] in ("primary", "tied_primary")), key=lambda r: r["rank"])
    direct = next((r for r in routes if r["route_kind"] == "direct"), None)
    steps = (direct or chosen or {}).get("steps", [])
    current, target = (steps[0] if len(steps) >= 2 else None), (steps[-1] if steps else {"title": run["target_role_id"]})
    req = lambda k: requirements.get(k, {})                                           # noqa: E731

    # ---- what the user can build on: stored Demonstrated, ONLY with records eligible now
    build_on = []
    for i in by_state["demonstrated"]:
        ev = _evidence(i, evidence)
        if ev:
            build_on.append({"key": i["key"], "title": i["title"], "basis": i.get("basis"), "evidence": ev})
        if len(build_on) == 2:
            break
    # ---- related experience: stored Transferable, with what it does and does not establish
    related = []
    for i in by_state["transferable"]:
        ev = _evidence(i, evidence)
        if not ev:
            continue
        related.append({"key": i["key"], "title": i["title"], "evidence": ev,
                        "carries_over": req(i["key"]).get("transfer_rationale"),
                        "does_not_establish": f"On its own, this doesn't establish \"{i['title']}\"."})
        if len(related) == 2:
            break
    # ---- what to focus on: stored Gaps (decision rights first); where to build them comes from the route's actions
    acts = (chosen or {}).get("actions", [])
    focus_items = by_state["gap"][:2] or [i for i in by_state["transferable"] if i.get("decision_rights")][:2]
    focus = []
    for i in focus_items:
        demo = next((a for a in acts if a["stage"] == 2 and a.get("requirement_key") == i["key"]), None)
        focus.append({"key": i["key"], "title": i["title"], "state": i["state"],
                      "why_it_matters": req(i["key"]).get("description"),
                      "how": demo["objective"] if demo else None, "where": list(demo["where_options"]) if demo else []})
    focus_keys = [f["key"] for f in focus]
    not_assessed = [{"key": i["key"], "title": i["title"], "reason": i.get("reason")} for i in by_state["unknown"]]

    # ---- the recommended next move
    titles = {i["key"]: i["title"] for i in items}
    dev = development(decision, chosen if primaries else None, titles)
    rule = decision.get("rule")
    f = (chosen or {}).get("feasibility") or {}
    reasons: List[str] = []
    if build_on:
        reasons.append(f"Your confirmed experience in {build_on[0]['title'][0].lower() + build_on[0]['title'][1:]} gives you a foundation to build on.")
    elif related:
        reasons.append(f"Your recorded experience is relevant to {related[0]['title'][0].lower() + related[0]['title'][1:]}.")
    provides = tradeoff = None
    if rule == "R3" and chosen:
        bridge = chosen["steps"][1]["title"] if len(chosen["steps"]) > 2 else "bridge"
        tied = [r["steps"][1]["title"] for r in primaries if len(r["steps"]) > 2]
        headline = (f"Move into {_a(bridge)} {bridge} role first." if len(tied) <= 1
                    else "Move into a bridge role first: " + " or ".join(tied) + ".")
        core = dev["core"] if dev else {"fully": [], "partly": [], "not_developed": []}
        if core["fully"]:
            reasons.append(f"{_a(bridge).capitalize()} {bridge} role develops {_quoted([chr(34) + x['title'] + chr(34) for x in core['fully']])} as part of the role.")
        elif core["partly"]:
            reasons.append(f"{_a(bridge).capitalize()} {bridge} role may give you opportunities to develop parts of "
                           f"{_quoted([chr(34) + x['title'] + chr(34) for x in core['partly']])}; some aspects you'll still need to seek and show yourself.")
        # the curated description, without its first sentence when that only restates the move ("Move into X first, ...")
        provides = chosen.get("explanation") or ""
        if provides.startswith("Move into") and ". " in provides:
            provides = provides.split(". ", 1)[1]
        provides = provides or None
        tradeoff = next((t["text"] for t in chosen.get("tradeoffs", []) if t["kind"] == "bridge_note"), None)
        moves = next((t["text"] for t in chosen.get("tradeoffs", []) if t["kind"] == "moves"), None)
        tradeoff = " ".join(x for x in (moves, tradeoff) if x) or None
    elif rule == "R2":
        headline = f"Move directly into {_a(target['title'])} {target['title']} role."
        dr = [f"\"{i['title']}\"" for i in by_state["demonstrated"] if i.get("decision_rights") and _evidence(i, evidence)]
        if dr:
            reasons.append(f"Your confirmed experience already covers the product decisions this role centres on: {_quoted(dr)}.")
    elif rule == "R4":
        headline = f"Aim directly for {_a(target['title'])} {target['title']} role, building evidence as you go."
        if focus:
            reasons.append(f"No bridge role clearly helps here, so the roadmap builds evidence of "
                           f"{_quoted([chr(34) + x['title'] + chr(34) for x in focus])} on the way.")
        tradeoff = next((t["text"] for t in (chosen or {}).get("tradeoffs", []) if t["kind"] == "scope"), None)
    else:
        headline = "First, complete a few details in your profile."
        reasons = ["Trace can't recommend a route until it can assess the product decisions this role centres on from "
                   "your confirmed profile."]
    route_steps = []
    if chosen:
        for n, s in enumerate(chosen["steps"]):
            route_steps.append({"title": s["title"], "position": "current" if n == 0 else "target" if n == len(chosen["steps"]) - 1 else "intermediate"})
    why_path = []
    if dev:
        for x in dev["core"]["fully"] + dev["practice"]["fully"]:
            why_path.append(f"{x['title']}: the {route_steps[1]['title'] if len(route_steps) > 2 else 'bridge'} role develops this.")
        for x in dev["core"]["partly"] + dev["practice"]["partly"]:
            why_path.append(f"{x['title']}: the role develops part of it. Still to build: {x['remaining']}" if x.get("remaining")
                            else f"{x['title']}: the role develops part of it.")
        for x in dev["core"]["not_developed"]:
            why_path.append(f"{x['title']}: the role doesn't develop this, so the roadmap builds it separately.")
        if f.get("entry_evidence_text"):
            why_path.append(f"Getting in: {f['entry_evidence_text']}.")
    elif rule in ("R2", "R4"):
        for i in [x for x in items if x.get("decision_rights")]:
            shown = i["state"] == "demonstrated" and _evidence(i, evidence)
            why_path.append(f"{i['title']}: " + ("shown in your confirmed experience." if shown
                            else "not yet established from your confirmed profile; the roadmap builds evidence of it."
                            if i["state"] == "gap" else "not assessed yet from the information available."))

    # ---- other genuinely possible routes (alternatives only; excluded routes are not shown)
    alternatives = []
    for r in sorted(routes, key=lambda r: r["rank"]):
        if r is chosen or r["route_kind"] == "observed" or r["primary_status"] not in ("alternative", "tied_primary"):
            continue
        rf = r.get("feasibility") or {}
        if r["route_kind"] == "direct":
            gaps = [f"\"{titles.get(k, k)}\"" for k in rf.get("decision_rights_gaps") or []]
            trade = ("One move; you'd need to build evidence of " + _quoted(gaps) + " on your own.") if gaps else "One move."
        else:
            trade = next((t["text"] for t in r.get("tradeoffs", []) if t["kind"] == "bridge_note"), None) or "Two moves."
        alternatives.append({"steps": [s["title"] for s in r["steps"]], "tradeoff": trade})

    # ---- the four stages: a few highlighted actions per stage, chosen by a fixed presentation order
    weekly = (goal or {}).get("weekly_hours")
    n_highlight = HIGHLIGHT.get(weekly, 3)
    strengths_keys = [b["key"] for b in build_on] + [r_["key"] for r_ in related]
    partials = {p["key"]: p for p in f.get("partial_development") or []}
    bridge_title = route_steps[1]["title"] if len(route_steps) > 2 else None

    def order(a):
        k = a.get("requirement_key")
        if a["stage"] == 4:
            base = STAGE4_ORDER.index(a["template_key"]) if a["template_key"] in STAGE4_ORDER else len(STAGE4_ORDER)
            return (base, focus_keys.index(k) if k in focus_keys else 9)
        pref = strengths_keys if a["stage"] == 3 else focus_keys
        return (pref.index(k) if k in pref else len(pref), 0)

    def view(a):
        kind = "seek" if a["template_key"] == "apply.bridge_seek" else a["kind"]
        out = {"stage": a["stage"], "kind": a["kind"], "template_key": a["template_key"], "requirement_key": a.get("requirement_key"),
               "title": a["objective"], "what_to_do": a["action_text"], "why": a["why_for_you"], "where": list(a["where_options"]),
               "produce": a["deliverable"], "keep": KEEP.get(kind), "labels": list(a.get("labels") or []),
               "source_keys": list(a["source_keys"])}
        p = partials.get(a.get("requirement_key")) if kind == "seek" else None
        if p:                                        # make "seek" concrete: what to do, where, what it develops, what remains
            out.update({"title": f"Grow {p['title'][0].lower() + p['title'][1:]} in the {bridge_title or 'bridge'} role",
                        "what_to_do": p["seek"],
                        "why": f"The role develops {p['develops'][0].lower() + p['develops'][1:]} Still to build: {p['incomplete']}"})
        return out

    stages = []
    for n in (1, 2, 3, 4):
        # An action that quotes or relies on the user's records is shown only while EVERY such record is eligible now
        # (a line rejected, edited or un-categorised since the plan was saved is never quoted back).
        here = sorted([a for a in acts if a["stage"] == n and a["template_key"] not in EMPLOYER_POSTING_ACTIONS
                       and all(str(r) in evidence for r in (a.get("record_refs") or []))], key=order)
        views = [view(a) for a in here]
        empty = None
        if not views:
            empty = ("Opens once you've completed the profile details above." if rule == "R1" and n < 4 else
                     "Nothing to learn first: your confirmed profile doesn't leave a priority here." if n == 1 else
                     "Nothing to demonstrate first for this route." if n == 2 else "Nothing for this stage yet.")
        stages.append({"stage": n, "title": STAGE_TITLES[n], "subtitle": STAGE_SUBTITLES[n], "highlighted": views[:n_highlight],
                       "more": views[n_highlight:], "empty_note": empty})

    # ---- timeline: the user's goal and checkpoints; never a forecast
    timeframe = (goal or {}).get("timeframe")
    target_date = (goal or {}).get("target_date")
    set_on = (goal or {}).get("goal_set_on") or today
    window = None
    if target_date:
        window = {"from": target_date, "to": target_date}
    elif timeframe and TIMEFRAME_MONTHS.get(timeframe):
        lo, hi = TIMEFRAME_MONTHS[timeframe]
        window = {"from": _add_months(set_on, lo), "to": _add_months(set_on, hi)}
    user_dates = (goal or {}).get("checkpoint_dates") or {}
    # ---- the detailed layer: experience themes, development plans, sequence and stage guidance (learning pack)
    priorities = (by_state["gap"] + [i for i in by_state["transferable"] if i.get("decision_rights")])[:3]
    has_employment = any(e["kind"] == "role" or e.get("source") for e in evidence.values())
    role_family = ((decision.get("current_position") or {}).get("role_family"))
    plans = learning.development_plans(priorities, chosen, role_family, weekly, has_employment, requirements, bridge_title, partials,
                                       set(f.get("develops_fully") or []))
    seq = learning.sequence(plans, rule, route_steps, timeframe, weekly, target["title"])
    curated = [x for x in plans if x["curated"]]
    have = {1: [f"Notes and exercises for: {lower_first(x['heading'])} (learning, not professional experience)" for x in curated],
            2: [f"Your {x['output']['name'].lower()}, from real work or a clearly labelled personal project" for x in curated],
            3: ["Your existing experience described accurately for a product role", "One case study from real work"],
            4: [f"Applications to {bridge_title} roles" if bridge_title else f"Applications for {target['title']} roles",
                "Practice with the main types of product interview question"]}
    checkpoints = []
    for s in stages:
        shown = s["highlighted"]
        if not shown and not have[s["stage"]]:
            continue
        if rule == "R1" and s["stage"] < 4:
            continue
        d = user_dates.get(str(s["stage"]))
        checkpoints.append({"stage": s["stage"], "title": CHECKPOINTS[s["stage"]] if not (s["stage"] == 4 and bridge_title)
                            else f"Moving into {_a(bridge_title)} {bridge_title} role",
                            "have": (have[s["stage"]] or [a["produce"] for a in shown if a["produce"]])[:3], "date": d,
                            "after_goal": bool(d and window and date.fromisoformat(d) > window["to"])})
    if not goal or not (timeframe or target_date):
        note = "No timeframe set. The roadmap is a sequence; you can add a goal date at any time."
    elif timeframe == "flexible" and not target_date:
        note = "Your timeframe is flexible, so the roadmap is a sequence without dates. You can add checkpoint dates yourself."
    else:
        note = ("Your goal is the planning target. These checkpoints are suggested stages in order, not a prediction of when "
                "you will secure the role. Add your own dates to each checkpoint if it helps you plan.")
    timeline = {
        "timeframe": timeframe, "timeframe_label": TIMEFRAME_LABEL.get(timeframe), "target_date": target_date,
        "goal_window": window, "weekly_hours": weekly, "weekly_label": WEEKLY_LABEL.get(weekly),
        "highlight_per_stage": n_highlight, "note": note,
        "duration_evidence": {"status": "not_available",
                              "reason": "synthetic_data" if run["data_mode"] != "empirical" else "no_approved_duration_evidence"},
    }
    return {
        "run_id": str(run["pathway_run_id"]), "target_role_id": run["target_role_id"],
        "current_role": current["title"] if current else None, "target_role": target["title"],
        "recommendation": {"kind": {"R1": "resolve_first", "R2": "direct", "R3": "bridge", "R4": "direct_conditional"}.get(rule, "resolve_first"),
                           "headline": headline, "route": route_steps, "reasons": reasons[:2], "provides": provides,
                           "tradeoff": tradeoff, "why_path": why_path},
        "build_on": build_on, "focus": focus, "related": related, "not_assessed": not_assessed,
        "resolve_first": [{"title": a["objective"], "what_to_do": a["action_text"], "why": a["why_for_you"]}
                          for a in acts if a["stage"] == 0],
        "stages": stages, "checkpoints": checkpoints, "timeline": timeline, "alternatives": alternatives,
        "career_histories": career_histories(run), "data_mode": run["data_mode"],
        "capabilities": (caps := capabilities(items, evidence, requirements)),
        "next_actions": next_actions(caps, None, target["title"], rule, route_steps),
        "experience": learning.experience_themes(evidence), "development": plans, "sequence": seq,
        "stage_guidance": learning.stage_guidance(weekly), "learning_version": learning.pack()["version"],
    }
