"""Group patterns over the closest cohort profiles.

Counts under 5 are left out. Counts from 5 to 9 are shown as a range. Wording states what this group did.
It does not predict a move, and it does not describe one person.
"""
from __future__ import annotations

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


def level_bonus(profile_level: str, target_level: str) -> float:
    if profile_level not in LEVELS or target_level not in LEVELS:
        return 0.0
    distance = abs(LEVELS.index(profile_level) - LEVELS.index(target_level))
    if distance == 0:
        return 0.15
    if distance == 1:
        return 0.08
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


def _of(display: Optional[str], total: int, rest: str) -> Optional[str]:
    if not display:
        return None
    return f"{display} of the {total} closest PMs {rest}"


def patterns_from_neighbours(rows: Sequence[dict], target_level: str) -> dict:
    """The public pattern object. No profile key, city, or raw count under 5 leaves this function."""
    total = len(rows)
    if total < 5:
        return {"available": False, "reason": "too_few"}
    internal = sum(1 for r in rows if r.get("final_entry_move") == "internal")
    external = sum(1 for r in rows if r.get("final_entry_move") == "external")
    priors: dict = {}
    for row in rows:
        label = (row.get("final_primary_prior_function") or "").strip()
        if label:
            priors[label] = priors.get(label, 0) + 1
    months = [r["final_months_to_first_pm_from_earliest_nonintern"] for r in rows
              if isinstance(r.get("final_months_to_first_pm_from_earliest_nonintern"), int)]
    pre = [r.get("final_n_pre_pm_roles") or 0 for r in rows]
    mba = sum(1 for r in rows if r.get("has_mba"))
    degree = sum(1 for r in rows if r.get("norm_edu_has_degree"))
    first: dict = {}
    for row in rows:
        label = (row.get("final_first_pm_group") or "").strip()
        if label:
            first[label] = first.get(label, 0) + 1
    at_target = sum(1 for r in rows if r.get("norm_seniority_group") == target_level)
    patterns = []

    def add(text: Optional[str]) -> None:
        if text:
            patterns.append({"id": f"PEER-{len(patterns) + 1}", "text": text})

    add(_of(band_count(internal), total, "moved into a product role from inside the employer."))
    add(_of(band_count(external), total, "moved into a product role from outside the employer."))
    for label, count in sorted(priors.items(), key=lambda item: (-item[1], item[0]))[:3]:
        add(_of(band_count(count), total, f"came from {label} before a product role."))
    median = _median(months)
    if median is not None:
        add(f"Among the {total} closest PMs with a dated path, the middle time to a first product role was about {median} months.")
        span = _rounded_span(months)
        if span:
            add(f"Most of those dated paths took about {span[0]} to {span[1]} months.")
    pre_med = _median(pre)
    if pre_med is not None:
        add(f"Among the {total} closest PMs, the middle number of roles before a product role was about {pre_med}.")
    add(_of(band_count(mba), total, "had an MBA."))
    add(_of(band_count(degree), total, "had a degree."))
    if first:
        label, count = sorted(first.items(), key=lambda item: (-item[1], item[0]))[0]
        add(_of(band_count(count), total, f"started in product as {label}."))
    add(_of(band_count(at_target), total, f"are at {target_level}."))
    takeaways = _takeaways(patterns)
    return {
        "available": True,
        "synthetic": False,
        "neighbour_count": total,
        "headline": "PMs with paths like yours",
        "match_line": "Matched by similarity of career path.",
        "disclaimer": "These are patterns across the closest profiles, not a prediction and not a guarantee.",
        "patterns": patterns[:8],
        "takeaways": takeaways,
        "note": "Your profile is not in this group. A pattern shared by fewer than 5 people is left out. "
                "A pattern shared by 5 to 9 people is shown as a range.",
    }


def _takeaways(patterns: Sequence[dict]) -> list:
    """Up to three short lines, using words already in the patterns. No new figure."""
    out = []
    for item in patterns:
        text = item["text"]
        if "moved into a product role from inside" in text and len(out) < 3:
            out.append("A common step in this group was moving into product inside the same employer.")
        elif "came from Software Engineering" in text and len(out) < 3:
            out.append("Software engineering, QA or IT was a frequent path into product in this group.")
        elif "came from Business" in text and len(out) < 3:
            out.append("Business, consulting, finance or operations was a frequent path into product in this group.")
        elif "had an MBA" in text and len(out) < 3:
            out.append("An MBA shows up in this group, and it is not described as a requirement.")
        elif "middle time" in text and len(out) < 3:
            out.append("The time to a first product role, for those with dates, is stated as a middle value for the group.")
    return out[:3]


def raw_counts_hidden(view: dict) -> bool:
    """True when no published sentence contains a count that banding would have hidden."""
    if not view.get("available"):
        return True
    blob = " ".join(p["text"] for p in view.get("patterns") or [])
    return re.search(r"(?<!\d)[1-4] of the", blob) is None
