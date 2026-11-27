"""Group patterns over the closest cohort profiles.

Counts under 5 are left out. Counts from 5 to 9 are shown as a range. Wording states what this group did.
It does not predict a move, and it does not describe one person.
"""
from __future__ import annotations

import re
from typing import Optional, Sequence

from app.services.peer_matching.text import LEVELS

K = 40
POOL = 120
MIN_LIFT = 1.2
OWN_LIFT = 1.1
BACKGROUND_WEIGHT = 0.20
MOVE_WEIGHT = 0.08
LEVEL_WEIGHT = 0.12
PRE_WEIGHT = 0.06
MBA_WEIGHT = 0.04
YEAR_WEIGHT = 0.06


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


def nearest(rows: Sequence[dict], query: Sequence[float], target_level: str, k: int = POOL) -> list:
    """Stage 1: the closest profiles by embedding only. The level argument is unused here."""
    del target_level
    scored = [(cosine(query, row["embedding"]), row) for row in rows if row.get("embedding")]
    scored.sort(key=lambda item: (-item[0], item[1].get("profile_key", "")))
    return [row for _, row in scored[: max(1, min(k, POOL))]]


def _pre_bucket(n) -> Optional[int]:
    if not isinstance(n, int):
        return None
    if n <= 0:
        return 0
    if n == 1:
        return 1
    if n <= 3:
        return 2
    return 3


def structured_score(row: dict, user: dict) -> float:
    """Stage 2. Each part is zero when that fact is unknown on either side."""
    score = 0.0
    prior = (user or {}).get("prior") or ""
    row_prior = (row.get("final_primary_prior_function") or "").strip()
    if prior and row_prior and prior == row_prior:
        score += BACKGROUND_WEIGHT
    row_move = row.get("final_entry_move") or ""
    if (user or {}).get("move") and row_move and user["move"] == row_move:
        score += MOVE_WEIGHT
    target = (user or {}).get("target_level") or ""
    level = row.get("norm_seniority_group") or ""
    if target in LEVELS and level in LEVELS:
        distance = abs(LEVELS.index(level) - LEVELS.index(target))
        score += LEVEL_WEIGHT * max(0.0, 1 - distance / 4)
    user_bucket = _pre_bucket(user.get("n_pre") if user else None)
    row_pre = row.get("final_n_pre_pm_roles") if row.get("final_has_history") else None
    row_bucket = _pre_bucket(row_pre if isinstance(row_pre, int) else None)
    if user_bucket is not None and row_bucket is not None and user_bucket == row_bucket:
        score += PRE_WEIGHT
    if user and user.get("mba") is not None and row.get("has_mba") is not None and bool(user["mba"]) == bool(row["has_mba"]):
        score += MBA_WEIGHT
    user_year = user.get("start_year") if user else None
    row_year = row.get("norm_career_start_year")
    if isinstance(user_year, int) and isinstance(row_year, int):
        score += YEAR_WEIGHT * max(0.0, 1 - abs(user_year - row_year) / 15)
    return score


def rerank(pool: Sequence[dict], query: Sequence[float], user: dict, k: int = K) -> list:
    """Stage 2: embedding similarity plus the structured score. The result is the neighbour set."""
    scored = []
    for row in pool:
        base = row.get("cosine")
        if base is None and query is not None and row.get("embedding"):
            base = cosine(query, row["embedding"])
        total = float(base or 0) + structured_score(row, user or {})
        scored.append((total, row.get("profile_key", ""), row))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [row for _, _, row in scored[: max(1, min(k, K))]]


def match_neighbours(rows: Sequence[dict], query: Sequence[float], user: dict, k: int = K) -> list:
    """Top pool by embedding, then the structured rerank."""
    return rerank(nearest(rows, query, (user or {}).get("target_level") or "", POOL), query, user, k)


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


def duration_phrase(months: float) -> str:
    """Months up to 24 stay in months. Longer durations are the nearest half year."""
    rounded = int(round(months))
    if rounded <= 24:
        unit = "month" if rounded == 1 else "months"
        return f"{rounded} {unit}"
    halves = round(months / 6.0) / 2.0
    whole = int(halves)
    number = str(whole) if abs(halves - whole) < 1e-9 else f"{whole}½"
    if number == "1":
        return "1 year"
    return f"{number} years"


def duration_span(low: float, high: float) -> str:
    """The middle half. Both ends share a unit when they are in the same one."""
    if round(low) <= 24 and round(high) <= 24:
        return f"{int(round(low))} to {duration_phrase(high)}"
    if round(low) > 24 and round(high) > 24:
        halves = round(low / 6.0) / 2.0
        whole = int(halves)
        number = str(whole) if abs(halves - whole) < 1e-9 else f"{whole}½"
        return f"{number} to {duration_phrase(high)}"
    return f"{duration_phrase(low)} to {duration_phrase(high)}"


def _share(count: int, known: int, rest: str, cohort_count: int, cohort_known: int, cohort_n: int,
           min_lift: Optional[float] = MIN_LIFT):
    """(sentence or None, lift). A base under 10 is not phrased as 'most of the N'.

    min_lift None skips the lift check. Lines about the user's own move or target level use 1.1.
    """
    if known < 10 or count < 5:
        return None, 0.0
    neighbour_rate = count / known
    cohort_rate = (cohort_count / cohort_known) if cohort_known else 0.0
    lift = (neighbour_rate / cohort_rate) if cohort_rate else (2.0 if neighbour_rate else 0.0)
    if min_lift is not None and lift + 1e-9 < min_lift:
        return None, lift
    if count < 10 and neighbour_rate < 0.5:
        return None, lift
    if count < 10:
        head = f"Most of the {known} closest PMs"
    else:
        head = f"{count} of the {known} closest PMs"
    text = f"{head} {rest.strip().rstrip('.')}"
    if cohort_known >= 10 and cohort_n >= 10:
        pct = int(round((cohort_rate * 100) / 5.0) * 5)
        text += f", compared with about {pct}% of all {cohort_n} PMs."
    elif not text.endswith("."):
        text += "."
    return text, lift


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
        n_pre = row.get("final_n_pre_pm_roles")
        if row.get("final_has_history") and isinstance(n_pre, int) and n_pre > 0:
            pre.append(n_pre)
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


def _candidates(group: dict, cohort: dict, cohort_n: int, user: Optional[dict] = None) -> list:
    """Shares that are at least 1.2 times the whole-cohort rate. Under-indexing is not promoted."""
    out = []
    user = user or {}

    def add(count, known, cohort_count, cohort_known, rest, kind, rate_count, min_lift=MIN_LIFT):
        text, lift = _share(count, known, rest, cohort_count, cohort_known, cohort_n, min_lift=min_lift)
        if not text:
            return
        out.append({"text": text, "score": lift, "count": count, "known": known, "kind": kind,
                    "rate": rate_count / known if known else 0, "cohort_rate": (cohort_count / cohort_known) if cohort_known else 0})

    user_move = user.get("move") or ""
    add(group["internal"], group["move_known"], cohort.get("internal", 0), cohort.get("move_known", 0),
        "with a recorded move moved into a product role from inside the employer.", "internal", group["internal"],
        min_lift=OWN_LIFT if user_move == "internal" else MIN_LIFT)
    add(group["external"], group["move_known"], cohort.get("external", 0), cohort.get("move_known", 0),
        "with a recorded move moved into a product role from outside the employer.", "external", group["external"],
        min_lift=OWN_LIFT if user_move == "external" else MIN_LIFT)
    target = user.get("target_level") or ""
    own = (user.get("prior") or "").strip()
    for label, count in group["priors"].items():
        if label == own:
            continue
        add(count, group["prior_known"], cohort.get("priors", {}).get(label, 0), cohort.get("prior_known", 0),
            f"with a recorded background came from {label} before a product role.", "prior", count)
    if not user.get("in_pm") and target:
        text, lift = _share(group["first"].get(target, 0), group["first_known"],
                            f"with a recorded first product level started in product as {target}.",
                            cohort.get("first", {}).get(target, 0), cohort.get("first_known", 0), cohort_n,
                            min_lift=OWN_LIFT)
        if text:
            out.append({"text": text, "score": max(lift, MIN_LIFT), "count": group["first"].get(target, 0),
                        "known": group["first_known"], "kind": "first", "rate": 1, "cohort_rate": 0})
    if user.get("mba"):
        mba_count = int(cohort.get("mba") or 0)
        if cohort_n >= 10 and mba_count >= 5:
            pct = int(round(100 * mba_count / cohort_n))
            if pct < 15:
                mba_text = f"MBAs are uncommon among these PMs: about {pct}% of all {cohort_n}."
            else:
                mba_text = f"About {pct}% of all {cohort_n} PMs have an MBA."
            out.append({"text": mba_text, "score": MIN_LIFT, "count": mba_count, "known": cohort_n, "kind": "mba",
                        "rate": mba_count / cohort_n, "cohort_rate": mba_count / cohort_n})
    else:
        text, lift = _share(group["mba"], group["mba_known"], "had an MBA.",
                            cohort.get("mba", 0), cohort.get("mba_known", 0), cohort_n, min_lift=MIN_LIFT)
        if text:
            out.append({"text": text, "score": max(lift, MIN_LIFT), "count": group["mba"], "known": group["mba_known"],
                        "kind": "mba", "rate": group["mba"] / group["mba_known"] if group["mba_known"] else 0,
                        "cohort_rate": (cohort.get("mba", 0) / cohort["mba_known"]) if cohort.get("mba_known") else 0})
    if not user.get("in_pm"):
        neighbour_pre = group.get("pre") or []
        cohort_pre = cohort.get("pre") or []
        add(sum(1 for n in neighbour_pre if n in (1, 2)), len(neighbour_pre),
            sum(1 for n in cohort_pre if n in (1, 2)), len(cohort_pre),
            "who had a role before product had 1 or 2 roles before a product role.", "roles",
            sum(1 for n in neighbour_pre if n in (1, 2)), min_lift=None)
    months = group.get("months") or []
    median = _median(months)
    if median is not None:
        sentence = (f"Among the {len(months)} closest PMs with a dated path, it took about {duration_phrase(median)} "
                    f"to reach a first PM role")
        span = _rounded_span(months)
        if span:
            sentence += f"; the middle half took {duration_span(span[0], span[1])}"
        out.append({"text": sentence + ".",
                    "score": MIN_LIFT, "count": len(months), "known": len(months), "kind": "months",
                    "rate": 1, "cohort_rate": 0})
    pre = group.get("pre") or []
    pre_med = _median(pre)
    if pre_med is not None and pre_med > 0:
        out.append({"text": (f"Among the {len(pre)} closest PMs who had a role before product, the middle number "
                             f"of those roles was about {pre_med}."),
                    "score": MIN_LIFT, "count": len(pre), "known": len(pre), "kind": "pre",
                    "rate": 1, "cohort_rate": 0})
    return out


def _rank(candidates: list) -> list:
    ordered = sorted(candidates, key=lambda item: (-item["score"], -item["count"], item["text"]))
    return ordered[:10]


def _background_name(label: str) -> str:
    short = {
        "Design / UX": "design",
        "Software Engineering / QA / IT": "software engineering",
        "Business / Consulting / Finance / Operations": "business or consulting",
        "Data / Analytics / Research": "data or analytics",
        "Sales / Marketing / Customer Success": "sales or marketing",
        "Entrepreneurship / Founders": "founding",
    }
    return short.get(label, label[:1].lower() + label[1:] if label else "that background")


def progression_lines(by_first: dict, user: Optional[dict]) -> list:
    """Where people who started at this user's first product level are now. No timing is invented."""
    user = user or {}
    if not user.get("in_pm"):
        return []
    start = user.get("first_pm_level") or ""
    target = user.get("target_level") or ""
    if start not in LEVELS or target not in LEVELS or start not in (by_first or {}):
        return []
    block = by_first[start] or {}
    n = int(block.get("n") or 0)
    reached = int((block.get("reached") or {}).get(target) or 0)
    if n < 10 or reached < 5:
        return []
    if reached < 10 and n < 20:
        if reached / n < 0.5:
            return []
        head = f"Most of the {n} cohort PMs who started as {start}"
    elif reached < 10:
        head = f"5–9 of the {n} cohort PMs who started as {start}"
    else:
        head = f"{reached} of the {n} cohort PMs who started as {start}"
    lines = [f"{head} are now {target} or above."]
    roles_n = int(block.get("pm_roles_n") or 0)
    median = block.get("pm_roles_median")
    if roles_n >= 10 and median is not None and int(round(float(median))) > 0:
        lines.append(f"The middle number of product roles along that path was about {int(round(float(median)))}.")
    return lines


def by_first_from_rows(rows: Sequence[dict]) -> dict:
    grouped: dict = {}
    for row in rows:
        start = (row.get("final_first_pm_group") or "").strip()
        level = row.get("norm_seniority_group") or ""
        if not start:
            continue
        slot = grouped.setdefault(start, {"rows": []})
        slot["rows"].append(row)
    out = {}
    for start, slot in grouped.items():
        people = slot["rows"]
        if start not in LEVELS:
            continue
        reached = {}
        for level in LEVELS:
            want = LEVELS.index(level)
            reached[level] = sum(1 for row in people if (row.get("norm_seniority_group") or "") in LEVELS
                                 and LEVELS.index(row["norm_seniority_group"]) >= want)
        roles = [row.get("final_n_pm_roles") for row in people if isinstance(row.get("final_n_pm_roles"), int) and row["final_n_pm_roles"] > 0]
        out[start] = {"n": len(people), "reached": reached, "pm_roles_n": len(roles), "pm_roles_median": _median(roles)}
    return out


def present(group: dict, cohort: Optional[dict] = None, user: Optional[dict] = None,
            by_first: Optional[dict] = None, background_counts: Optional[dict] = None) -> dict:
    """The public section. Patterns are only those well above the whole-cohort rate."""
    cohort = cohort or group
    total = int(group.get("n") or 0)
    cohort_n = int(cohort.get("n") or 0)
    if total < 5:
        return {"available": False, "reason": "too_few"}
    ranked = _rank(_candidates(group, cohort, cohort_n, user))
    extra = progression_lines(by_first or {}, user)
    patterns = [{"id": f"PEER-{i + 1}", "text": item["text"]} for i, item in enumerate(ranked)]
    for line in extra:
        if len(patterns) >= 10:
            break
        patterns.append({"id": f"PEER-{len(patterns) + 1}", "text": line})
    prior = (user or {}).get("prior") or ""
    preface = ""
    if prior and background_counts is not None and int(background_counts.get(prior) or 0) < 5:
        preface = (f"Few PMs in our data came from {_background_name(prior)}; "
                   "here is what PMs with similar experience did.")
    if not patterns and not preface:
        return {"available": False, "reason": "too_few"}
    return {
        "available": True,
        "synthetic": False,
        "neighbour_count": total,
        "headline": "PMs with paths like yours",
        "match_line": (
            f"Matched by similarity of career path. Matched on your background: {prior}."
            if prior and group.get("priors", {}).get(prior, 0) >= 10
            else "Matched by similarity of career path."
        ),
        "preface": preface,
        "disclaimer": "These are patterns across the closest profiles, not a prediction and not a guarantee.",
        "patterns": patterns,
        "takeaways": _takeaways(ranked, group.get("internal") or 0, group.get("external") or 0),
        "note": "Your profile is not in this group. A pattern shared by fewer than 5 people is left out. "
                "A pattern shared by 5 to 9 people is shown as a range.",
    }


def _reached(level: str, target: str) -> bool:
    return level in LEVELS and target in LEVELS and LEVELS.index(level) >= LEVELS.index(target)


def in_pm_lines(rows: Sequence[dict], cohort_rows: Sequence[dict], user: dict) -> list:
    """Lines for someone already in product: the level, the background mix, and years since career start."""
    if not user.get("in_pm"):
        return []
    target = user.get("target_level") or ""
    if target not in LEVELS:
        return []
    lines = []
    count = sum(1 for row in rows if _reached(row.get("norm_seniority_group") or "", target))
    cohort_count = sum(1 for row in cohort_rows if _reached(row.get("norm_seniority_group") or "", target))
    text, _lift = _share(count, len(rows), f"are at {target} or above.", cohort_count, len(cohort_rows), len(cohort_rows),
                         min_lift=OWN_LIFT)
    if text:
        lines.append(text)
    reached = [row for row in rows if _reached(row.get("norm_seniority_group") or "", target)]
    counts: dict = {}
    for row in reached:
        label = (row.get("final_primary_prior_function") or "").strip()
        if label:
            counts[label] = counts.get(label, 0) + 1
    kept = sorted(((label, n) for label, n in counts.items() if n >= 5), key=lambda item: (-item[1], item[0]))
    if len(reached) >= 5 and kept:
        if len(reached) < 10:
            if len(kept) == 1:
                lines.append("Among the closest PMs at " + target + " or above, the most common background before product was "
                             + kept[0][0] + ".")
            else:
                names = " and ".join(label for label, _n in kept)
                lines.append("Among the closest PMs at " + target + " or above, backgrounds before product include "
                             + names + ".")
        else:
            bits = ["5–9 came from " + label if n < 10 else f"{n} came from {label}" for label, n in kept]
            lines.append(f"Among the {len(reached)} closest PMs at {target} or above, {' and '.join(bits)} before product.")
    from datetime import date
    years = []
    for row in rows:
        if (row.get("norm_seniority_group") or "") != target:
            continue
        start = row.get("norm_career_start_year")
        if isinstance(start, int) and date.today().year >= start:
            years.append(date.today().year - start)
    if len(years) >= 10:
        ordered = sorted(years)
        mid = len(ordered) // 2
        median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
        if median > 0:
            lines.append(f"Among the {len(years)} closest PMs at {target}, the middle time since career start was about "
                         f"{duration_phrase(float(median) * 12)}.")
    return lines


def patterns_from_neighbours(rows: Sequence[dict], target_level: str, cohort_rows: Optional[Sequence[dict]] = None,
                             user: Optional[dict] = None) -> dict:
    """The public pattern object. No profile key, city, or raw count under 5 leaves this function."""
    cohort_rows = list(cohort_rows if cohort_rows is not None else rows)
    features = dict(user or {})
    features.setdefault("target_level", target_level)
    counts: dict = {}
    for row in cohort_rows:
        label = (row.get("final_primary_prior_function") or "").strip()
        if label:
            counts[label] = counts.get(label, 0) + 1
    view = present(stats_from_rows(rows), stats_from_rows(cohort_rows), features, by_first_from_rows(cohort_rows), counts)
    if view.get("available"):
        for line in in_pm_lines(rows, cohort_rows, features):
            if len(view["patterns"]) >= 10:
                break
            view["patterns"].append({"id": f"PEER-{len(view['patterns']) + 1}", "text": line})
    return view


def _takeaways(patterns: Sequence[dict], internal: int = 0, external: int = 0) -> list:
    """A frequent-path line only when it beats the cohort, covers about 30%, and inside moves are the majority."""
    out = []
    for item in patterns:
        if item.get("rate", 0) < 0.30 or item.get("rate", 0) <= item.get("cohort_rate", 0):
            continue
        text = item["text"]
        if "from inside the employer" in text and internal > external and len(out) < 3:
            out.append("A common step in this group was moving into product inside the same employer.")
        elif "came from Software Engineering" in text and len(out) < 3:
            out.append("Software engineering, QA or IT was a frequent path into product in this group.")
        elif "came from Business" in text and len(out) < 3:
            out.append("Business, consulting, finance or operations was a frequent path into product in this group.")
        elif "came from Design / UX" in text and len(out) < 3:
            out.append("Design was a frequent path into product in this group.")
        elif "had an MBA" in text and len(out) < 3:
            out.append("An MBA shows up in this group, and it is not described as a requirement.")
    return out[:3]


def raw_counts_hidden(view: dict) -> bool:
    """True when no published sentence contains a count that banding would have hidden."""
    if not view.get("available"):
        return True
    blob = " ".join(p["text"] for p in view.get("patterns") or [])
    return re.search(r"(?<!\d)[1-4] of the", blob) is None
