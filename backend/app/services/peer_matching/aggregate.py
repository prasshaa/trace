"""Group patterns over the closest cohort profiles.

Counts under 5 are left out. Counts from 5 to 9 are shown as a range. Wording states what this group did.
It does not predict a move, and it does not describe one person.
"""
from __future__ import annotations

import math
import re
from typing import Optional, Sequence

from app.services.peer_matching.text import LEVELS

K = 40


def band_count(n: int) -> Optional[str]:
    """None when the count is too small to show. '5–9' for a small group. The exact count from 10 up."""
    if n < 5:
        return None
    if n < 10:
        return "5–9"
    return str(n)


# Path texts are a template, so cosine already sits in a narrow band. A large bonus would
# turn the search into a level filter. Same level is a small nudge; an adjacent level is smaller.
SAME_LEVEL_BONUS = 0.03
ADJACENT_LEVEL_BONUS = 0.02


def level_bonus(profile_level: str, target_level: str) -> float:
    if profile_level not in LEVELS or target_level not in LEVELS:
        return 0.0
    distance = abs(LEVELS.index(profile_level) - LEVELS.index(target_level))
    if distance == 0:
        return SAME_LEVEL_BONUS
    if distance == 1:
        return ADJACENT_LEVEL_BONUS
    return 0.0


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return float(sum(x * y for x, y in zip(a, b)))


def nearest(rows: Sequence[dict], query: Sequence[float], target_level: str, k: int = K) -> list:
    """The k closest profiles. Vector similarity leads. A nearby seniority group is a soft bonus, not a filter."""
    scored = []
    for row in rows:
        score = cosine(query, row["embedding"]) + level_bonus(row["norm_seniority_group"], target_level)
        scored.append((score, row))
    scored.sort(key=lambda item: (-item[0], item[1].get("profile_key", "")))
    return [row for _, row in scored[: max(1, min(k, K))]]


def _rounded_span(values: Sequence[int]) -> Optional[tuple]:
    """A rounded middle half. Exact shortest and longest paths are not published."""
    if len(values) < 10:
        return None
    ordered = sorted(values)
    def at(q: float) -> int:
        pos = (len(ordered) - 1) * q
        lo = int(pos)
        hi = min(lo + 1, len(ordered) - 1)
        raw = ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)
        return int(round(raw / 6.0) * 6)
    low, high = at(0.25), at(0.75)
    if high <= low:
        return None
    return low, high


def _median(values: Sequence[int]) -> Optional[int]:
    if len(values) < 10:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return int(ordered[mid])
    return int(round((ordered[mid - 1] + ordered[mid]) / 2))


def _shown(n: int) -> Optional[str]:
    return band_count(n)


def _share(count: int, known: int, rest: str) -> Optional[str]:
    """A share over the profiles that have the field. Both the count and that denominator respect the minimum of 5."""
    if known < 5 or count < 5:
        return None
    count_text = _shown(count)
    known_text = _shown(known) if known < 10 else str(known)
    if not count_text or not known_text:
        return None
    return f"{count_text} of the {known_text} closest PMs {rest}"


def _rate(count: int, known: int) -> Optional[float]:
    if known <= 0:
        return None
    return count / known


def _lift(neighbour: Optional[float], cohort: Optional[float]) -> float:
    if neighbour is None:
        return 0.0
    if not cohort:
        return 2.0 if neighbour else 0.0
    if neighbour <= 0:
        return 0.0
    return neighbour / cohort


def stats_from_rows(rows: Sequence[dict]) -> dict:
    """Counts over known values only. A blank prior, move, month, pre-PM count or degree is skipped."""
    priors: dict = {}
    first: dict = {}
    months = []
    pre = []
    internal = external = 0
    mba = degree_yes = 0
    mba_known = degree_known = 0
    for row in rows:
        move = row.get("final_entry_move") or ""
        if move == "internal":
            internal += 1
        elif move == "external":
            external += 1
        label = (row.get("final_primary_prior_function") or "").strip()
        if label:
            priors[label] = priors.get(label, 0) + 1
        if isinstance(row.get("final_months_to_first_pm_from_earliest_nonintern"), int):
            months.append(row["final_months_to_first_pm_from_earliest_nonintern"])
        if row.get("final_has_history") and isinstance(row.get("final_n_pre_pm_roles"), int):
            pre.append(row["final_n_pre_pm_roles"])
        if row.get("has_mba") is not None:
            mba_known += 1
            if row.get("has_mba"):
                mba += 1
        if row.get("norm_edu_has_degree") is not None:
            degree_known += 1
            if row.get("norm_edu_has_degree"):
                degree_yes += 1
        started = (row.get("final_first_pm_group") or "").strip()
        if started:
            first[started] = first.get(started, 0) + 1
    return {
        "n": len(rows),
        "internal": internal,
        "external": external,
        "move_known": internal + external,
        "priors": priors,
        "prior_known": sum(priors.values()),
        "months": months,
        "pre": pre,
        "mba": mba,
        "mba_known": mba_known,
        "degree": degree_yes,
        "degree_known": degree_known,
        "first": first,
        "first_known": sum(first.values()),
    }


def _candidates(group: dict, cohort: dict) -> list:
    """One candidate pattern per share, scored by how far it sits from the whole cohort."""
    out = []

    def add(count, known, cohort_count, cohort_known, text, kind, raw):
        if not text:
            return
        score = abs(math.log(_lift(_rate(count, known), _rate(cohort_count, cohort_known)) or 1.0))
        out.append({"text": text, "score": score, "count": count, "kind": kind, "raw": raw})

    add(group["internal"], group["move_known"], cohort.get("internal", 0), cohort.get("move_known", 0),
        _share(group["internal"], group["move_known"], "with a recorded move moved into a product role from inside the employer."),
        "internal", group["internal"])
    add(group["external"], group["move_known"], cohort.get("external", 0), cohort.get("move_known", 0),
        _share(group["external"], group["move_known"], "with a recorded move moved into a product role from outside the employer."),
        "external", group["external"])
    for label, count in group["priors"].items():
        add(count, group["prior_known"], cohort.get("priors", {}).get(label, 0), cohort.get("prior_known", 0),
            _share(count, group["prior_known"], f"with a recorded background came from {label} before a product role."),
            "prior", count)
    for label, count in group["first"].items():
        add(count, group["first_known"], cohort.get("first", {}).get(label, 0), cohort.get("first_known", 0),
            _share(count, group["first_known"], f"with a recorded first product level started in product as {label}."),
            "first", count)
    add(group["mba"], group["mba_known"], cohort.get("mba", 0), cohort.get("mba_known", 0),
        _share(group["mba"], group["mba_known"], "had an MBA."), "mba", group["mba"])
    add(group["degree"], group["degree_known"], cohort.get("degree", 0), cohort.get("degree_known", 0),
        _share(group["degree"], group["degree_known"], "with a recorded degree had a degree."), "degree", group["degree"])
    months = group.get("months") or []
    cohort_months = cohort.get("months") or []
    median = _median(months)
    if median is not None:
        base = _median(cohort_months)
        gap = abs(median - base) / max(base or 1, 1) if base is not None else 0.5
        text = (f"Among the {len(months)} closest PMs with a dated path, the middle time to a first product role "
                f"was about {median} months.")
        out.append({"text": text, "score": gap, "count": len(months), "kind": "months", "raw": median})
        span = _rounded_span(months)
        if span:
            out.append({"text": f"Most of those dated paths took about {span[0]} to {span[1]} months.",
                        "score": gap, "count": len(months), "kind": "span", "raw": span[0]})
    pre = group.get("pre") or []
    pre_med = _median(pre)
    if pre_med is not None:
        base = _median(cohort.get("pre") or [])
        gap = abs(pre_med - base) / max(base or 1, 1) if base is not None else 0.5
        out.append({"text": (f"Among the {len(pre)} closest PMs with roles recorded before product, the middle number "
                             f"of those roles was about {pre_med}."),
                    "score": gap, "count": len(pre), "kind": "pre", "raw": pre_med})
    return out


def _rank(candidates: list) -> list:
    """Up to 10 patterns. Distinctive ones lead. A near-match to the whole cohort is kept only to fill the section."""
    ordered = sorted(candidates, key=lambda item: (-item["score"], -item["count"], item["text"]))
    distinctive = [item for item in ordered if item["score"] >= 0.15]
    chosen = distinctive[:10] if len(distinctive) >= 3 else ordered[:10]
    return [{"id": f"PEER-{i + 1}", "text": item["text"], "kind": item["kind"]} for i, item in enumerate(chosen)]


def present(group: dict, cohort: Optional[dict] = None) -> dict:
    """The public section. `cohort` is the same counts for every loaded profile, used only to rank."""
    cohort = cohort or group
    total = int(group.get("n") or 0)
    if total < 5:
        return {"available": False, "reason": "too_few"}
    patterns = _rank(_candidates(group, cohort))
    if not patterns:
        return {"available": False, "reason": "too_few"}
    return {
        "available": True,
        "synthetic": False,
        "neighbour_count": total,
        "headline": "PMs with paths like yours",
        "match_line": "Matched by similarity of career path.",
        "disclaimer": "These are patterns across the closest profiles, not a prediction and not a guarantee.",
        "patterns": patterns,
        "takeaways": _takeaways(patterns, group.get("internal") or 0, group.get("external") or 0),
        "note": "Your profile is not in this group. A pattern shared by fewer than 5 people is left out. "
                "A pattern shared by 5 to 9 people is shown as a range.",
    }


def patterns_from_neighbours(rows: Sequence[dict], target_level: str, cohort_rows: Optional[Sequence[dict]] = None) -> dict:
    """The public pattern object. No profile key, city, or raw count under 5 leaves this function."""
    del target_level
    return present(stats_from_rows(rows), stats_from_rows(cohort_rows if cohort_rows is not None else rows))


def _takeaways(patterns: Sequence[dict], internal: int, external: int) -> list:
    """Up to three short lines. The inside-move line is used only when inside moves are the larger group."""
    out = []
    shown = " ".join(item["text"] for item in patterns)
    if internal > external and "from inside the employer" in shown and len(out) < 3:
        out.append("A common step in this group was moving into product inside the same employer.")
    if "came from Software Engineering" in shown and len(out) < 3:
        out.append("Software engineering, QA or IT was a frequent path into product in this group.")
    if "came from Business" in shown and len(out) < 3:
        out.append("Business, consulting, finance or operations was a frequent path into product in this group.")
    if "came from Design / UX" in shown and len(out) < 3:
        out.append("Design was a frequent path into product in this group.")
    if "came from Data / Analytics" in shown and len(out) < 3:
        out.append("Data, analytics or research was a frequent path into product in this group.")
    if "had an MBA" in shown and len(out) < 3:
        out.append("An MBA shows up in this group, and it is not described as a requirement.")
    if "middle time" in shown and len(out) < 3:
        out.append("The time to a first product role, for those with dates, is stated as a middle value for the group.")
    return out[:3]


def raw_counts_hidden(view: dict) -> bool:
    """True when no published sentence contains a count that banding would have hidden."""
    if not view.get("available"):
        return True
    blob = " ".join(p["text"] for p in view.get("patterns") or [])
    return re.search(r"(?<!\d)[1-4] of the", blob) is None
