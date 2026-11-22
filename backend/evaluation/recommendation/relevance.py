"""Whole-profile relevance PROTOTYPE (Phase 8.2), shadow / evaluation mode only.

Not imported by the application and not used for production ranking (asserted by tests). For each route that the
CURRENT engine shows, it compares the user's confirmed profile with the route's own supporting reference profiles,
dimension by dimension. There is no weight and no composite score: the output is a per-dimension breakdown of what
matched, what didn't, what isn't recorded and where evidence is insufficient.

Population: the distinct profiles observed on that exact route in the engine's evidence (own contribution already
excluded, route-level privacy already applied). Never profiles that merely share the target role.

Privacy: a reference value (an earlier role, area, industry, skill, qualification or field) is disclosed only when at
least `threshold` distinct supporting profiles share it, the same small-group minimum (5) used everywhere else. Values
the user holds that are not disclosed are reported as "not shown among supporting profiles" (never "absent"), so a rare
value cannot be confirmed by asking.

Missing data: if the user has nothing confirmed for a dimension, that dimension is "not_recorded_in_profile" and is
never compared or counted against them. Unreviewed skills are ignored.
"""
from collections import Counter
from typing import Dict, List, Optional

from app.services.candidate_profile.pathway import move_direction

DIMENSIONS = ("previous_roles", "responsibilities", "industries", "skills", "qualifications", "fields_of_study")
# Phase 8.2.1: qualification LEVEL (bachelor, master, doctorate), qualification TYPE (MBA, MSc, BTech, ...) and FIELD OF
# STUDY are separate dimensions; an MBA is a type, never a level. Used by pools that declare "dimension_set": "v2".
# The Phase 8.2 set above is unchanged (and stays the default) so the Phase 8.2 outputs remain reproducible.
DIMENSIONS_V2 = ("previous_roles", "responsibilities", "industries", "skills", "qualification_levels", "qualification_types",
                 "fields_of_study")
FACT_KEY = {"previous_roles": "earlier_roles", "responsibilities": "areas", "industries": "industries", "skills": "skills",
            "qualifications": "qualifications", "fields_of_study": "fields",
            "qualification_levels": "qualification_levels", "qualification_types": "qualification_types"}


def dimensions_for(pool: dict) -> tuple:
    return DIMENSIONS_V2 if pool.get("dimension_set") == "v2" else DIMENSIONS


def _norm(dimension: str, value) -> str:
    text = " ".join(str(value).split())
    return text if dimension == "previous_roles" else text.lower()


def user_values(user: dict, dimension: str) -> List[str]:
    """Only confirmed values (the scenario's reviewed fields). Earlier roles exclude the current role."""
    raw = {
        "previous_roles": [r for r in user.get("held_roles", []) if r != user.get("current_role")],
        "responsibilities": user.get("areas", []),
        "industries": user.get("industries", []),
        "skills": user.get("skills", []),                          # unreviewed_skills are never read
        "qualifications": user.get("qualifications", []),
        "fields_of_study": user.get("fields", []),
        "qualification_levels": user.get("qualification_levels", []),
        "qualification_types": user.get("qualification_types", []),
    }[dimension]
    return sorted({_norm(dimension, v) for v in raw if str(v).strip()})


def route_relevance(repo, evidence, route_id: str, user: dict, threshold, role_meta: Dict, dims: tuple = DIMENSIONS) -> dict:
    route = tuple(route_id.split(">"))
    supporters = {}
    for o in (evidence.observations if evidence else []):
        if tuple(o.route) == route:
            supporters.setdefault(o.provenance.profile_id, o.provenance.segment_no)
    n = len(supporters)
    if threshold is None or n < threshold:                      # never reached for a shown route; checked anyway
        return {"route_id": route_id, "state": "insufficient_route_evidence", "dimensions": {}}
    facts = [repo.profile_facts(pid, seg, route) for pid, seg in sorted(supporters.items())]
    dimension_list, dims = dims, {}
    for d in dimension_list:
        counts = Counter(v for f in facts for v in {_norm(d, x) for x in f[FACT_KEY[d]]})
        disclosed = {v: c for v, c in counts.items() if c >= threshold}
        mine = set(user_values(user, d))
        patterns = sorted(([v, c] for v, c in disclosed.items()), key=lambda x: (-x[1], x[0]))
        if not mine:
            dims[d] = {"status": "not_recorded_in_profile", "reference_patterns": patterns}
        elif not disclosed:
            dims[d] = {"status": "insufficient_reference_evidence"}
        else:
            dims[d] = {"status": "compared",
                       "matched": sorted(([v, disclosed[v]] for v in mine if v in disclosed), key=lambda x: (-x[1], x[0])),
                       "reference_not_in_profile": [p for p in patterns if p[0] not in mine],
                       "profile_not_shown_among_supporters": sorted(v for v in mine if v not in disclosed)}
    labels = [move_direction(role_meta.get(a), role_meta.get(b)) for a, b in zip(route, route[1:])]
    dims["seniority"] = ({"status": "reliable_direction", "moves": labels} if any(labels)
                         else {"status": "no_reliable_level", "moves": labels})
    return {"route_id": route_id, "state": "compared", "supporting_profiles": n, "dimensions": dims}


def differences(a: dict, b: dict, dims: tuple = DIMENSIONS) -> List[str]:
    """Dimensions whose breakdown differs between two runs of the same route (for consistency checks and comparisons)."""
    out = [d for d in (*dims, "seniority") if a.get("dimensions", {}).get(d) != b.get("dimensions", {}).get(d)]
    if a.get("supporting_profiles") != b.get("supporting_profiles"):
        out.append("population")
    return out


def describe(breakdown: dict, dims: tuple = DIMENSIONS) -> List[str]:
    """Plain sentences for one route's breakdown (evaluation report and review pack context). No score, no ranking."""
    if breakdown["state"] != "compared":
        return ["Not enough supporting profiles to compare this route."]
    lines = []
    for d in dims:
        x = breakdown["dimensions"][d]
        name = d.replace("_", " ")
        if x["status"] == "not_recorded_in_profile":
            lines.append(f"{name}: not recorded in this profile; not compared" + (
                f" (common among supporters: {', '.join(f'{v} ({c})' for v, c in x['reference_patterns'])})" if x["reference_patterns"] else ""))
        elif x["status"] == "insufficient_reference_evidence":
            lines.append(f"{name}: not enough shared detail among supporting profiles to compare")
        else:
            m = ", ".join(f"{v} ({c} of {breakdown['supporting_profiles']})" for v, c in x["matched"]) or "none"
            other = ", ".join(f"{v} ({c})" for v, c in x["reference_not_in_profile"]) or "none"
            lines.append(f"{name}: matched {m}; common among supporters but not recorded here: {other}")
    s = breakdown["dimensions"]["seniority"]
    lines.append("seniority: " + (", ".join(m or "no label" for m in s["moves"]) if s["status"] == "reliable_direction"
                                  else "no reliable level for any move"))
    return lines


def combination_audit(repo, evidence, route_id: str, threshold, dims: tuple) -> List[dict]:
    """PRIVACY AUDIT (Phase 8.2.1, evaluation only; not a disclosure rule). Each disclosed value has at least `threshold`
    supporters, but a COMBINATION of disclosed values from different dimensions may be shared by fewer. This lists
    every combination (one disclosed value per dimension, two or more dimensions) shared by more than zero and fewer than
    `threshold` of the route's supporters. Exact counts are returned for tests; reports show "fewer than <threshold>"."""
    from itertools import combinations, product
    route = tuple(route_id.split(">"))
    supporters = {}
    for o in (evidence.observations if evidence else []):
        if tuple(o.route) == route:
            supporters.setdefault(o.provenance.profile_id, o.provenance.segment_no)
    if threshold is None or len(supporters) < threshold:
        return []
    facts = [{d: {_norm(d, x) for x in repo.profile_facts(pid, seg, route)[FACT_KEY[d]]} for d in dims}
             for pid, seg in sorted(supporters.items())]
    disclosed = {}
    for d in dims:
        counts = Counter(v for f in facts for v in f[d])
        values = sorted(v for v, c in counts.items() if c >= threshold)
        if values:
            disclosed[d] = values
    risks = []
    for k in range(2, len(disclosed) + 1):
        for chosen in combinations(sorted(disclosed), k):
            for values in product(*(disclosed[d] for d in chosen)):
                n = sum(1 for f in facts if all(v in f[d] for d, v in zip(chosen, values)))
                if 0 < n < threshold:
                    risks.append({"values": [[d, v] for d, v in zip(chosen, values)], "supporters_with_all": n})
    return risks
