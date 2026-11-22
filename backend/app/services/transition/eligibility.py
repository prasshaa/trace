"""Employer-specific application eligibility (D29, assessment 2 of 3). Pure and deterministic; no database, no AI.

Each employer criterion is stored with its exact text, importance (required or preferred) and a structured rule
(trace_target_role_requirements.criterion, pm-eligibility-v1). This module evaluates that rule against the user's
REVIEWED profile and returns, per criterion and per posting, one of:

    meets           the stated condition is shown by confirmed records
    does_not_meet   confirmed records show it cannot be met (for years: even the longest reading of the dates is short)
    unclear         anything else: missing, pending or uncategorised records, imprecise dates, or a condition the
                    profile cannot show (a stated_condition, such as a product domain)

Rules (never relaxed):
  * A job title or role family alone never shows product exposure: it needs confirmed, categorised responsibility
    evidence with a product-ownership area in a role of the named families.
  * A transition exclusion ("not for people transitioning from X") applies to the CURRENT confirmed role only; having
    held X earlier never triggers it.
  * Preferred criteria are reported but never decide the posting's outcome.
  * A missing qualification record is unclear, never absence; an "or equivalent" clause never gives does_not_meet.
  * Results stay per employer. They are never counted across employers and never become capability gaps.
"""
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

REVIEWED = ("user_confirmed", "user_corrected")
MEETS, NOT_MET, UNCLEAR = "meets", "does_not_meet", "unclear"
LEVEL_RANK = {"diploma": 1, "bachelor": 2, "master": 3, "doctorate": 4}


@dataclass(frozen=True)
class Job:
    """One reviewed employment, as eligibility needs it."""
    record_id: str
    title: str
    role_family: Optional[str]                 # None when the mapping is unresolved or stale
    start_lo: Optional[int]
    start_hi: Optional[int]
    end_lo: Optional[int]                      # already resolved for "current" jobs (as_of)
    end_hi: Optional[int]
    counts_as_experience: bool                 # False only when the user said it does not count as full-time experience
    pattern_uncertain: bool = False            # D36: internship, part-time or apprenticeship with no stated work pattern
    exposure_records: Tuple[str, ...] = ()     # confirmed, categorised lines showing a product-ownership area
    open_records: Tuple[str, ...] = ()         # lines that could still show it: pending, uncategorised or stale


@dataclass(frozen=True)
class Qualification:
    level: str                                 # read-time level (D11: an MBA reads as master)
    qtype: Optional[str] = None                # read-time type from the stated title (BE, BTech, MSc, ...), or None
    field_code: Optional[str] = None           # whole-value match of the stated field through explicit aliases (D36)
    field_category: Optional[str] = None
    field_label: Optional[str] = None
    field_raw: Optional[str] = None            # as stated


@dataclass(frozen=True)
class EligibilityFacts:
    jobs: Tuple[Job, ...]
    current_role_family: Optional[str]         # None when the current position is unknown
    qualifications: Tuple[Qualification, ...]
    current_role_id: Optional[str] = None      # the confirmed current role (taxonomy id), for unconfirmed_roles


@dataclass
class Result:
    outcome: str
    explanation: str
    records: List[str] = field(default_factory=list)
    parts: List["Result"] = field(default_factory=list)
    rule: Optional[dict] = None

    def as_dict(self) -> dict:
        out = {"outcome": self.outcome, "explanation": self.explanation, "records": list(self.records)}
        if self.rule is not None:
            out["rule"] = {k: v for k, v in self.rule.items() if k not in ("all", "any", "note")}
        if self.parts:
            out["parts"] = [p.as_dict() for p in self.parts]
        return out


def normalise_field(text: Optional[str]) -> str:
    """A stated field of study as one normalised value: lower case, '&' read as 'and', punctuation removed, single spaces.
    Matching is on this WHOLE value against explicit aliases (D36), never on substrings."""
    s = (text or "").lower().replace("&", " and ")
    return " ".join("".join(ch if ch.isalnum() else " " for ch in s).split())


# --------------------------------------------------------------------------- months of experience from partial dates
def _union(intervals: Iterable[Tuple[int, int]]) -> int:
    total, cur = 0, None
    for lo, hi in sorted(intervals):
        if cur and lo <= cur[1] + 1:
            cur = (cur[0], max(cur[1], hi))
        else:
            if cur:
                total += cur[1] - cur[0] + 1
            cur = (lo, hi)
    if cur:
        total += cur[1] - cur[0] + 1
    return total


def experience_months(jobs: Sequence[Job]) -> Tuple[int, Optional[int]]:
    """(certain minimum, possible maximum) months across the jobs, overlaps counted once. Maximum None = unbounded
    (an unknown start or end date). A job whose work pattern is uncertain (D36) counts towards the maximum only."""
    certain = [(j.start_hi, j.end_lo) for j in jobs if not j.pattern_uncertain
               and j.start_hi is not None and j.end_lo is not None and j.end_lo >= j.start_hi]
    if any(j.start_lo is None or j.end_hi is None for j in jobs):
        return _union(certain), None
    return _union(certain), _union((j.start_lo, j.end_hi) for j in jobs if j.end_hi >= j.start_lo)


def _years(months: int) -> str:
    y, m = divmod(months, 12)
    return f"{y} years" + (f" {m} months" if m else "") if y else f"{m} months"


# --------------------------------------------------------------------------- leaves
def _before(jobs: Sequence[Job], later: Sequence[Job]) -> List[Job]:
    """Only the part of each job that falls before the first of the `later` jobs (E8: "before product"). Certain months
    end before the earliest possible start of a later job; possible months end before its latest possible start. An
    unknown start of a later job leaves nothing certain."""
    if not later:
        return list(jobs)
    los, his = [j.start_lo for j in later], [j.start_hi for j in later]
    certain_end = (min(los) - 1) if all(x is not None for x in los) else None
    possible_end = (min(his) - 1) if all(x is not None for x in his) else None
    out = []
    for j in jobs:
        end_lo = min(j.end_lo, certain_end) if (j.end_lo is not None and certain_end is not None) else None
        end_hi = j.end_hi if possible_end is None else (min(j.end_hi, possible_end) if j.end_hi is not None else possible_end)
        if end_hi is not None and j.start_lo is not None and end_hi < j.start_lo:
            continue                                                  # entirely after the first later role
        if end_lo is not None and j.start_hi is not None and end_lo < j.start_hi:
            end_lo = None                                             # nothing certainly before
        out.append(Job(j.record_id, j.title, j.role_family, j.start_lo, j.start_hi, end_lo, end_hi, j.counts_as_experience,
                       j.pattern_uncertain, j.exposure_records, j.open_records))
    return out


def _family_label(families: Sequence[str], labels: Dict[str, str]) -> str:
    names = [labels.get(f, f) for f in families]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def _years_leaf(node: dict, facts: EligibilityFacts, labels: Dict[str, str]) -> Result:
    total = node["type"] == "total_years"
    fams = set(node.get("families") or [])
    jobs = [j for j in facts.jobs if j.counts_as_experience and (total or j.role_family in fams)]
    what = "work experience" if total else f"experience in {_family_label(sorted(fams), labels)} roles"
    before = set(node.get("before_families") or [])
    if before:
        jobs = _before(jobs, [j for j in facts.jobs if j.role_family in before])
        what += f" before your first {_family_label(sorted(before), labels)} role"
    need = int(round(float(node["min"]) * 12))
    top = node.get("max")
    stated = f"{node['min']}" + (f"-{top}" if top is not None else "+") + " years"
    ids = [j.record_id for j in jobs]
    # A confirmed job without a confirmed role could be in these families: it can never support "does not meet".
    unmapped = [] if total else [j.record_id for j in facts.jobs if j.role_family is None and j.counts_as_experience]
    if unmapped and (not jobs or (experience_months(jobs)[1] is not None and experience_months(jobs)[1] < need)):
        return Result(UNCLEAR, f"Needs {stated} of {what}. Some of your confirmed jobs have no confirmed role, so this can't be "
                               "worked out yet.", unmapped, rule=node)
    eq_fams = set(node.get("equivalent_families") or [])
    equivalent = [j for j in facts.jobs if j.counts_as_experience and j.role_family in eq_fams] if eq_fams else []
    if not jobs and not equivalent:
        return Result(NOT_MET, f"Needs {stated} of {what}; your confirmed profile has none.", [], rule=node)
    lo, hi = experience_months(jobs)
    uncertain = [j.record_id for j in jobs if j.pattern_uncertain]
    if lo < need and equivalent:
        _, hi_all = experience_months(jobs + equivalent)
        if hi_all is None or hi_all >= need:      # D36: never counted, never rejected; the employer decides
            names = ", ".join(sorted({labels.get(j.role_family, j.role_family) for j in equivalent}))
            return Result(UNCLEAR, f"Needs {stated} of {what}. Your confirmed experience in {names} may or may not count: the employer "
                                   "decides whether it is equivalent.", ids + [j.record_id for j in equivalent], rule=node)
    if lo < need and uncertain and (hi is None or hi >= need):
        return Result(UNCLEAR, f"Needs {stated} of {what}. Some of it is an internship, part-time work or an apprenticeship with no stated "
                               "work pattern, so it can't be counted with certainty.", ids, rule=node)
    if lo >= need:
        if top is not None and lo > int(round(float(top) * 12)):
            return Result(UNCLEAR, f"Needs {stated} of {what}; you have at least {_years(lo)}, more than the stated range. The "
                                   "employer decides whether that fits.", ids, rule=node)
        return Result(MEETS, f"Needs {stated} of {what}; your confirmed jobs show at least {_years(lo)}.", ids, rule=node)
    if hi is not None and hi < need:
        return Result(NOT_MET, f"Needs {stated} of {what}; your confirmed jobs show at most {_years(hi)}.", ids, rule=node)
    return Result(UNCLEAR, f"Needs {stated} of {what}; your confirmed dates show at least {_years(lo)}"
                           + (f" and at most {_years(hi)}" if hi is not None else "") + ", so it isn't certain.", ids, rule=node)


def _exposure_leaf(node: dict, facts: EligibilityFacts, labels: Dict[str, str]) -> Result:
    fams = set(node["families"])
    jobs = [j for j in facts.jobs if j.role_family in fams]
    shown = [r for j in jobs for r in j.exposure_records]
    if shown:
        return Result(MEETS, "Product exposure is shown by confirmed responsibilities you categorised as product work "
                             "(discovery, roadmap, prioritisation or outcomes).", shown, rule=node)
    open_ = [r for j in jobs for r in j.open_records]
    if open_:
        return Result(UNCLEAR, "Product exposure needs confirmed responsibilities categorised as product work. Some lines in "
                               "these jobs are still drafts, uncategorised or changed since you categorised them.", open_, rule=node)
    if not jobs:
        return Result(NOT_MET, f"Product exposure is needed in {_family_label(sorted(fams), labels)} roles; your confirmed "
                               "profile has none.", [], rule=node)
    return Result(NOT_MET, "Product exposure needs confirmed responsibilities categorised as product work (discovery, roadmap, "
                           "prioritisation or outcomes); none of your categorised lines in these jobs shows it. A job title "
                           "alone doesn't count.", [j.record_id for j in jobs], rule=node)


def _current_leaf(node: dict, facts: EligibilityFacts, labels: Dict[str, str]) -> Result:
    fams = set(node["families"])
    names = _family_label(sorted(fams), labels)
    if facts.current_role_family is None:
        return Result(UNCLEAR, "Your current role isn't confirmed yet, so this can't be checked.", rule=node)
    if node["type"] == "current_role_in_families":
        if facts.current_role_family in fams and facts.current_role_id in set(node.get("unconfirmed_roles") or []):
            # Phase 4 closure: inside the family, but the employer has not said it treats this role as equivalent
            return Result(UNCLEAR, f"Needs a current role in {names}. Your current confirmed role is in that area, but the employer "
                                   "hasn't said it treats it as equivalent to its requirement; the employer decides.", rule=node)
        if facts.current_role_family in fams:
            return Result(MEETS, f"Needs a current role in {names}; your current confirmed role is one.", rule=node)
        return Result(NOT_MET, f"Needs a current role in {names}; your current confirmed role is "
                               f"{labels.get(facts.current_role_family, facts.current_role_family)}.", rule=node)
    if facts.current_role_family in fams:
        return Result(NOT_MET, f"This employer states the role is not for people moving now from {names}, which is your "
                               "current confirmed role.", rule=node)
    earlier = [j.record_id for j in facts.jobs if j.role_family in fams]
    return Result(MEETS, f"This employer excludes people moving now from {names}. Your current role isn't one of these"
                         + ("; an earlier role in that area doesn't count against you." if earlier else "."), earlier, rule=node)


def _qualification_leaf(node: dict, facts: EligibilityFacts, labels: Dict[str, str]) -> Result:
    """D36: a degree type (B.E./B.Tech) or the stated field matched on normalised whole values through explicit aliases.
    Never a substring. The discipline is reported separately. Missing or unrecognised gives unclear, never absence."""
    need = LEVEL_RANK.get(node.get("min_level") or "", 0)
    fields, types = set(node.get("fields") or []), set(node.get("types") or [])
    stated = " or ".join([t for t in node.get("types") or []] + [f.replace("category:", "any ") .replace("_", " ") for f in node.get("fields") or []])
    level_ok = [q for q in facts.qualifications if LEVEL_RANK.get(q.level, 0) >= need]
    by_type = [q for q in level_ok if q.qtype in types]
    by_field = [q for q in level_ok if q.field_code and (q.field_code in fields or f"category:{q.field_category}" in fields)]
    if by_type:
        q = by_type[0]
        disc = f" Discipline: {q.field_label or q.field_raw or 'not stated'} (reported, not assessed here)."
        return Result(MEETS, f"Asks for {stated}; you have a confirmed {q.qtype}.{disc}", rule=node)
    if by_field:
        return Result(MEETS, f"Asks for a qualification in {stated}; your confirmed {by_field[0].field_label} qualification matches.", rule=node)
    tail = " The employer also accepts an equivalent, which only they can judge." if node.get("or_equivalent") else ""
    if not facts.qualifications:
        return Result(UNCLEAR, f"Asks for {stated}; your profile has no confirmed qualifications.{tail}", rule=node)
    unknown = [q for q in facts.qualifications if q.field_raw and not q.field_code]
    if unknown:
        return Result(UNCLEAR, f"Asks for {stated}; Trace doesn't recognise the field \"{unknown[0].field_raw}\" exactly, so it isn't "
                               f"matched.{tail}", rule=node)
    return Result(UNCLEAR, f"Asks for {stated}; none of your confirmed qualifications states one of these.{tail}", rule=node)


def evaluate(node: dict, facts: EligibilityFacts, labels: Dict[str, str]) -> Result:
    if "all" in node or "any" in node:
        parts = [evaluate(n, facts, labels) for n in (node.get("all") or node.get("any"))]
        outs = [p.outcome for p in parts]
        if "all" in node:
            outcome = NOT_MET if NOT_MET in outs else MEETS if all(o == MEETS for o in outs) else UNCLEAR
            text = "All of these are needed."
        else:
            outcome = MEETS if MEETS in outs else NOT_MET if all(o == NOT_MET for o in outs) else UNCLEAR
            text = "Any one of these is enough."
        return Result(outcome, text, sorted({r for p in parts for r in p.records}), parts)
    kind = node["type"]
    if kind in ("years_in_families", "total_years"):
        return _years_leaf(node, facts, labels)
    if kind == "product_exposure":
        return _exposure_leaf(node, facts, labels)
    if kind in ("current_role_in_families", "not_transitioning_from"):
        return _current_leaf(node, facts, labels)
    if kind == "qualification":
        return _qualification_leaf(node, facts, labels)
    if kind == "stated_condition":
        return Result(UNCLEAR, f"Also depends on {node['text']}, which your profile can't show; the employer judges it.", rule=node)
    raise ValueError(f"unknown criterion type {kind!r}")


def posting_eligibility(posting: dict, facts: EligibilityFacts, labels: Dict[str, str]) -> dict:
    """One posting: each criterion's result, and the outcome from its REQUIRED criteria only."""
    required, preferred = [], []
    for c in posting["criteria"]:
        item = {"text": c["text"], "importance": c["importance"], "note": (c.get("criterion") or {}).get("note")}
        if c.get("criterion"):
            item["result"] = evaluate(c["criterion"], facts, labels).as_dict()
        else:
            item["result"] = Result(UNCLEAR, "This criterion has no structured form, so it isn't assessed.").as_dict()
        (required if c["importance"] == "required" else preferred).append(item)
    outs = [i["result"]["outcome"] for i in required]
    outcome = (UNCLEAR if not outs else NOT_MET if NOT_MET in outs else MEETS if all(o == MEETS for o in outs) else UNCLEAR)
    out = {"outcome": outcome, "required": required, "preferred": preferred}
    if not posting["criteria"]:
        out["note"] = "No criteria are recorded for this posting, so it can't be compared with your profile."
    elif not outs:
        out["note"] = "This posting states preferences only."
    return out
