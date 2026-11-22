"""Detailed, personalised guidance for the career transition roadmap (comprehensive redesign).

Pure functions over (a) the curated, versioned learning pack `content/pm-learning-v1.json` (Trace guidance plus learning
resources whose facts were checked on their own pages on a recorded date), (b) the records eligible NOW (provenance.py)
and (c) the stored plan. Nothing here changes a route, a capability state or eligibility, and nothing estimates how long
a transition takes. The only arithmetic is study time for a resource with a PUBLISHED length, divided by the weekly time
the user stated, labelled as an estimate.

  experience_themes   the user's confirmed, categorised records grouped into a few readable themes; the synthesis
                      paraphrases the user's OWN categorisation (area phrases), never the raw text, never adds claims;
                      each record is listed once
  development_plans   up to three priorities, each with what to learn, why, how (methods and verified resources), a
                      recommended first approach and alternatives, practice steps for the user's current kind of role,
                      the output to produce and how to use it
  sequence            what to start with, what runs in parallel, what must come before applying, what can wait, the
                      main risk to the user's goal, and advice for the stated weekly time
"""
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

PACK_PATH = Path(__file__).parent / "content" / "pm-learning-v1.json"
WEEKLY_HOURS = {"1_3": (1, 3), "4_6": (4, 6), "7_10": (7, 10), "more_than_10": (10, None)}
LIMITATION = ("Relevant experience can give you a useful foundation without necessarily showing full ownership of a "
              "Product Manager responsibility.")


@lru_cache(maxsize=1)
def pack() -> dict:
    return json.loads(PACK_PATH.read_text())


def _join(items: List[str]) -> str:
    items = [i for i in items if i]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def _short_provider(p: str) -> str:
    return p.split(" (")[0]


def family_group(role_family: Optional[str]) -> str:
    for group, fams in pack()["families"].items():
        if role_family in fams:
            return group
    return "default"


# --------------------------------------------------------------------------- experience you can build on
def experience_themes(evidence: Dict[str, dict], limit: int = 4) -> dict:
    p = pack()
    lines = [(rid, e) for rid, e in evidence.items() if e["kind"] == "evidence"]
    themes = []
    for t in p["themes"]:
        here = [(rid, e) for rid, e in lines if set(e.get("areas", [])) & set(t["areas"])]
        if not here:
            continue
        areas = [a for a in t["areas"] if any(a in e.get("areas", []) for _, e in here)]
        jobs = sorted({e["source"] for _, e in here if e.get("source")})
        who = f"Your experience as {_join([_article(j) + ' ' + j for j in jobs])}" if jobs else "Your confirmed experience"
        themes.append({"key": t["key"], "title": t["title"],
                       "summary": f"{who} includes {_join([p['area_phrases'][a] for a in areas])}.",
                       "relevance": t["relevance"], "record_ids": [rid for rid, _ in here]})
    themes.sort(key=lambda t: -len(t["record_ids"]))
    themes = themes[:limit]
    used = [rid for t in themes for rid in t["record_ids"]]
    records = []
    for rid in dict.fromkeys(used):                                   # each record once, in first-use order
        e = evidence[rid]
        records.append({"id": rid, "text": e["text"], "source": e.get("source")})
    return {"themes": themes, "records": records, "limitation": LIMITATION if themes else None}


# --------------------------------------------------------------------------- development plans
def _resource(rid: str, weekly: Optional[str]) -> dict:
    r = dict(pack()["resources"][rid], id=rid)
    length = r.get("published_length")
    r["study_time"] = None
    if length and weekly in WEEKLY_HOURS:
        lo, hi = WEEKLY_HOURS[weekly]
        hours = length["hours"]
        if hi is None:
            r["study_time"] = f"Under {math.ceil(hours / lo)} weeks of study at more than {lo} hours a week"
        else:
            a, b = math.ceil(hours / hi), math.ceil(hours / lo)
            r["study_time"] = (f"About {a} week{'s' if a != 1 else ''} at {lo}–{hi} hours a week" if a == b
                               else f"About {a}–{b} weeks at {lo}–{hi} hours a week")
        r["study_time"] += " (an estimate from the published length and the time you said you have; not a prediction)"
    return r


def development_plans(priorities: List[dict], route: Optional[dict], role_family: Optional[str], weekly: Optional[str],
                      has_employment: bool, requirements: Dict[str, dict], bridge_title: Optional[str],
                      partial: Dict[str, dict], fully: Optional[set] = None) -> List[dict]:
    p = pack()
    group = family_group(role_family)
    acts = (route or {}).get("actions", [])
    plans = []
    for item in priorities:
        key = item["key"]
        g = p["guides"].get(key)
        route_actions = [{"stage": a["stage"], "title": a["objective"], "what_to_do": a["action_text"], "produce": a["deliverable"],
                          "labels": list(a.get("labels") or [])}
                         for a in acts if a.get("requirement_key") == key and a["stage"] in (1, 2)]
        if not g:                                                     # no curated guide: role knowledge and the route's own actions
            plans.append({"key": key, "title": item["title"], "heading": f"Develop: {item['title']}", "state": item["state"],
                          "curated": False, "concept": requirements.get(key, {}).get("description"), "subtopics": [], "why": None,
                          "learn": [], "recommended_first": None, "alternatives": [], "practice": [], "output": None,
                          "use_output": [], "route_actions": route_actions, "bridge_note": None})
            continue
        first = g["recommended_first"]
        if first == "workplace" and not has_employment:
            first = "personal_project"
        reason = {"workplace": "Practising at work gives you real evidence, which matters more than another course.",
                  "free_learning": "Start with the free resources so you know what good looks like, then practise.",
                  "personal_project": "Without a current role to practise in, a clearly labelled personal project is the quickest way to practise."}[first]
        alts = ["workplace", "internal", "mentorship", "personal_project"] + (["internship"] if not has_employment else [])
        bridge_note = None
        if bridge_title and key in partial:
            pd = partial[key]
            bridge_note = {"text": g.get("why_bridge"), "develops": pd.get("develops"), "still_to_build": pd.get("incomplete"),
                           "seek": pd.get("seek")}
        elif bridge_title and key in (fully or set()):
            bridge_note = {"text": f"Trace's bridge-role information shows the {bridge_title} role develops this as part of the role, "
                                   "so it is a good place to build real experience of it.", "develops": None,
                           "still_to_build": None, "seek": None}
        plans.append({
            "key": key, "title": item["title"], "heading": g["heading"], "state": item["state"], "curated": True,
            "concept": g["concept"], "subtopics": g["subtopics"], "why": g["why"],
            "learn": [{"method": m["method"], "label": p["methods"][m["method"]], "summary": m["summary"],
                       "resources": [_resource(r, weekly) for r in m["resources"]]} for m in g["learn"]],
            "recommended_first": {"method": first, "label": p["methods"][first], "reason": reason},
            "alternatives": [{"method": a, "label": p["methods"][a], "text": p["alternatives"][a]} for a in alts if a != first],
            "practice": g["practice"].get(group) or g["practice"]["default"],
            "practice_for": group, "output": g["output"], "use_output": g["use_output"],
            "route_actions": route_actions, "bridge_note": bridge_note,
        })
    return plans


# --------------------------------------------------------------------------- sequence and stage guidance
def sequence(plans: List[dict], rule: Optional[str], route_steps: List[dict], timeframe: Optional[str], weekly: Optional[str],
             target_title: str) -> dict:
    p = pack()
    bridge = route_steps[1]["title"] if len(route_steps) > 2 else None
    next_role = bridge or target_title
    curated = [x for x in plans if x["curated"]]
    start, parallel, before, defer = [], [], [], []
    if rule == "R1":
        start.append({"stage": 0, "text": "Complete the profile details listed above, so Trace can assess your experience."})
    if curated:
        top = curated[0]
        res = next((r for m in top["learn"] for r in m["resources"] if r["access"] == "free"), None)
        topic = top["title"][0].lower() + top["title"][1:]
        start.append({"stage": 1, "text": f"{top['heading']}" + (f": begin with \"{res['title']}\" ({_short_provider(res['provider'])})." if res else ".")})
        start.append({"stage": 2, "text": f"Ask for a chance to practise \"{top['title']}\" at work now. Opportunities can take time "
                                          "to arrange, so start asking early."})
        if len(curated) > 1:
            parallel.append({"stage": 1, "text": f"{curated[1]['heading']}, once the first area is under way."})
        before.append({"stage": 2, "text": f"Have at least one output from real work, such as your {top['output']['name'].lower()}, before you apply for {next_role} roles."})
    start.append({"stage": 3, "text": "Describe your existing experience accurately for a product role. You can do this straight away."})
    parallel.append({"stage": 4, "text": "Talk to two or three people already doing the role about how they got in."})
    before.append({"stage": 3, "text": "Write one case study from real work."})
    defer.append({"stage": 4, "text": "Detailed interview practice can wait until you have your first real-work output."})
    if len(curated) > 2:
        defer.append({"stage": 1, "text": f"Your third priority, \"{curated[2]['title']}\", can follow the first two."})
    short = timeframe in ("within_3_months", "within_6_months")
    if rule == "R1":
        risk = "Until your profile details are complete, Trace can't confirm which route suits you, so complete them first."
    elif bridge and short:
        risk = (f"Your route has two moves. Within a short goal, plan to reach the {bridge} role and treat {target_title} as the "
                "step after it.")
    elif curated:
        risk = (f"Getting real work in {curated[0]['title'][0].lower() + curated[0]['title'][1:]} depends on opportunities you "
                "don't fully control. It is usually the slowest part, so ask early and keep a personal project as a fallback.")
    else:
        risk = "Finding the right opening can take time; start conversations with people in the role early."
    return {"start_now": start, "in_parallel": parallel, "before_next": before, "can_wait": defer, "main_risk": risk,
            "weekly_advice": p["weekly_advice"].get(weekly)}


def stage_guidance(weekly: Optional[str]) -> dict:
    g = pack()["stage_guidance"]
    four = dict(g["4"])
    four["resources"] = [_resource(r, weekly) for r in g["4"]["resources"]]
    return {"3": g["3"], "4": four}
