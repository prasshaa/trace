"""Career-path sentences for peer matching.

The same shape is used for a cohort profile and for the signed-in user, so the local embedding compares like with like.
Nothing here is a keyword search over the cohort. No employer, city, or person name is included.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

COLUMNS = (
    "taxonomy_version",
    "norm_seniority_group",
    "norm_city",
    "final_background_status",
    "final_primary_prior_function",
    "final_prior_functions",
    "final_multi_function",
    "final_prev_role_function",
    "final_entry_move",
    "final_first_pm_group",
    "final_first_pm_dated",
    "final_months_to_first_pm_from_earliest_nonintern",
    "final_has_history",
    "final_dated_role_before_first_pm",
    "final_n_pre_pm_roles",
    "final_n_roles",
    "final_n_pm_roles",
    "norm_edu_has_degree",
    "norm_career_start_year",
    "has_mba",
)

LEVELS = (
    "Junior PM",
    "APM",
    "Product Manager",
    "Senior Product Manager",
    "Lead",
    "Principal/Staff/Group",
    "Head",
    "Director",
    "VP/CPO",
)

# The extract and the app have used both wordings. Stored and embedded text uses the short form.
LEVEL_ALIASES = {
    "junior pm": "Junior PM",
    "junior product manager": "Junior PM",
    "apm": "APM",
    "associate product manager": "APM",
    "product manager": "Product Manager",
    "senior product manager": "Senior Product Manager",
    "lead": "Lead",
    "principal/staff/group": "Principal/Staff/Group",
    "principal / staff / group": "Principal/Staff/Group",
    "head": "Head",
    "director": "Director",
    "vp/cpo": "VP/CPO",
    "vp / cpo": "VP/CPO",
}

ROLE_LEVEL = {
    "PM-APM": "APM",
    "PM-PM": "Product Manager",
    "PM-SPM": "Senior Product Manager",
    "PM-LEAD": "Lead",
    "PM-PRINCIPAL": "Principal/Staff/Group",
    "PM-GPM": "Principal/Staff/Group",
    "PM-HEAD": "Head",
    "PM-DIR": "Director",
    "PM-SDIR": "Director",
    "PM-VP": "VP/CPO",
    "PM-CPO": "VP/CPO",
}

_BOOL = {"true": True, "false": False, "1": True, "0": False}


class CohortFileError(ValueError):
    """The cohort file is not the approved column set. The message names the column, not a row."""


def canonical_level(value: str, *, required: bool) -> str:
    raw = " ".join((value or "").split())
    if not raw:
        if required:
            raise CohortFileError("norm_seniority_group is not a known group")
        return ""
    key = raw.lower()
    level = LEVEL_ALIASES.get(key)
    if level is None and not required:
        for alias, name in sorted(LEVEL_ALIASES.items(), key=lambda item: -len(item[0])):
            if key.startswith(alias):
                level = name
                break
    if level is None:
        if required:
            raise CohortFileError("norm_seniority_group is not a known group")
        return raw
    return level


def parse_bool(value: str, column: str) -> bool:
    key = (value or "").strip().lower()
    if key not in _BOOL:
        raise CohortFileError(f"{column} must be true or false")
    return _BOOL[key]


def parse_optional_bool(value: str, column: str) -> Optional[bool]:
    """A blank is unknown. It is not false."""
    if not (value or "").strip():
        return None
    return parse_bool(value, column)


def parse_int(value: str, column: str, *, blank_ok: bool) -> Optional[int]:
    raw = (value or "").strip()
    if not raw:
        if blank_ok:
            return None
        raise CohortFileError(f"{column} is required")
    if not re.fullmatch(r"-?\d+", raw):
        raise CohortFileError(f"{column} must be a whole number")
    return int(raw)


def parse_row(raw: dict) -> dict:
    """One cohort row. Extra or missing columns are refused. Blank optional numbers stay blank."""
    keys = tuple(raw.keys())
    if keys != COLUMNS:
        missing = [c for c in COLUMNS if c not in raw]
        extra = [c for c in raw if c not in COLUMNS]
        raise CohortFileError(f"columns must match the cohort extract (missing={missing} extra={extra})")
    level = canonical_level(raw["norm_seniority_group"], required=True)
    move = (raw["final_entry_move"] or "").strip().lower()
    if move not in ("", "internal", "external"):
        raise CohortFileError("final_entry_move must be internal, external, or blank")
    has_history = parse_bool(raw["final_has_history"], "final_has_history")
    n_pre = parse_int(raw["final_n_pre_pm_roles"], "final_n_pre_pm_roles", blank_ok=True)
    if not has_history:
        n_pre = None
    return {
        "taxonomy_version": (raw["taxonomy_version"] or "").strip(),
        "norm_seniority_group": level,
        "norm_city": (raw["norm_city"] or "").strip(),
        "final_background_status": (raw["final_background_status"] or "").strip(),
        "final_primary_prior_function": (raw["final_primary_prior_function"] or "").strip(),
        "final_prior_functions": (raw["final_prior_functions"] or "").strip(),
        "final_multi_function": parse_bool(raw["final_multi_function"], "final_multi_function"),
        "final_prev_role_function": (raw["final_prev_role_function"] or "").strip(),
        "final_entry_move": move,
        "final_first_pm_group": canonical_level(raw["final_first_pm_group"], required=False),
        "final_first_pm_dated": parse_bool(raw["final_first_pm_dated"], "final_first_pm_dated"),
        "final_months_to_first_pm_from_earliest_nonintern": parse_int(
            raw["final_months_to_first_pm_from_earliest_nonintern"],
            "final_months_to_first_pm_from_earliest_nonintern", blank_ok=True),
        "final_has_history": has_history,
        "final_dated_role_before_first_pm": parse_bool(raw["final_dated_role_before_first_pm"], "final_dated_role_before_first_pm"),
        "final_n_pre_pm_roles": n_pre,
        "final_n_roles": parse_int(raw["final_n_roles"], "final_n_roles", blank_ok=False) or 0,
        "final_n_pm_roles": parse_int(raw["final_n_pm_roles"], "final_n_pm_roles", blank_ok=False) or 0,
        "norm_edu_has_degree": parse_optional_bool(raw["norm_edu_has_degree"], "norm_edu_has_degree"),
        "norm_career_start_year": parse_int(raw["norm_career_start_year"], "norm_career_start_year", blank_ok=True),
        "has_mba": parse_bool(raw["has_mba"], "has_mba"),
    }


def cohort_path_text(row: dict) -> str:
    """The sentence that is embedded for one cohort profile. Unknown fields are left out, not written as zero."""
    parts = [f"{row['norm_seniority_group']}."]
    prior = (row.get("final_primary_prior_function") or "").strip()
    n_pre = row.get("final_n_pre_pm_roles")
    before = []
    if prior:
        before.append(prior)
    if isinstance(n_pre, int):
        before.append(f"{n_pre} roles")
    if before:
        parts.append("Before PM: " + ", ".join(before) + ".")
    move = row.get("final_entry_move") or ""
    months = row.get("final_months_to_first_pm_from_earliest_nonintern")
    if move == "internal" and months is not None:
        parts.append(f"Moved into PM internally after about {months} months.")
    elif move == "external" and months is not None:
        parts.append(f"Moved into PM from outside the employer after about {months} months.")
    elif move == "internal":
        parts.append("Moved into PM internally.")
    elif move == "external":
        parts.append("Moved into PM from outside the employer.")
    edu = []
    if row.get("norm_edu_has_degree") is True:
        edu.append("Has a degree")
    elif row.get("norm_edu_has_degree") is False:
        edu.append("No degree recorded")
    if row.get("has_mba") is True:
        edu.append("an MBA")
    elif row.get("has_mba") is False:
        edu.append("no MBA")
    if edu:
        parts.append(", ".join(edu) + ".")
    return " ".join(parts)


def near_levels(level: str) -> list:
    if level not in LEVELS:
        return []
    i = LEVELS.index(level)
    out = []
    if i > 0:
        out.append(LEVELS[i - 1])
    if i + 1 < len(LEVELS):
        out.append(LEVELS[i + 1])
    return out


_PM_TITLE = re.compile(
    r"\b(associate product manager|product manager|senior product manager|group product manager|"
    r"product owner|product lead|head of product|director of product|vp of product|chief product officer|"
    r"\bapm\b)\b",
    re.I,
)
_DESIGN_TITLE = re.compile(r"\b(designer|design)\b", re.I)


def is_product_role(title: str, role_id: str = "") -> bool:
    """A product role is a PM-family title. Product Designer is design."""
    title = title or ""
    if _DESIGN_TITLE.search(title) and not _PM_TITLE.search(title):
        return False
    if _PM_TITLE.search(title):
        return True
    return (role_id or "").startswith("PM-") and not _DESIGN_TITLE.search(title)


def _function_for_title(title: str, family: str) -> str:
    """Cohort wording, so a user path and a cohort path name the same background."""
    blob = f"{family} {title}".lower()
    if any(w in blob for w in ("data analyst", "data scientist", "analytics", "data ")):
        return "Data / Analytics / Research"
    if any(w in blob for w in ("designer", "design", " ux", "ux ")):
        return "Design / UX"
    if "research" in blob:
        return "Data / Analytics / Research"
    if any(w in blob for w in ("sales", "marketing", "growth")):
        return "Sales / Marketing"
    if any(w in blob for w in ("founder", "co-founder", "cofounder")):
        return "Founder"
    if re.search(r"\b(engineer|software|developer|qa|quality)\b", blob):
        return "Software Engineering / QA / IT"
    if any(w in blob for w in ("consult", "finance", "operation", "bank", "analyst")):
        return "Business / Consulting / Finance / Operations"
    return ""


def _year(value) -> Optional[int]:
    year = getattr(value, "year", None)
    return year if isinstance(year, int) else None


def _years_in(jobs) -> Optional[str]:
    from datetime import date
    starts = [y for y in (_year(getattr(j, "start", None)) for j in jobs) if y]
    if not starts:
        return None
    current = False
    ends = []
    for job in jobs:
        end = getattr(job, "end", None)
        if getattr(job, "is_current_as_stated", False) or getattr(end, "precision", "") == "ongoing":
            current = True
        elif _year(end):
            ends.append(_year(end))
    end_year = date.today().year if current or not ends else max(ends)
    span = end_year - min(starts)
    if span <= 0:
        return "under a year in product"
    unit = "year" if span == 1 else "years"
    return f"about {span} {unit} in product"


def _scope(title: str, evidence: Iterable[str]) -> str:
    cleaned = re.sub(
        r"(?i)\b(senior|junior|associate|lead|principal|staff|group|head|director|vp|chief|"
        r"product|manager|management|officer|apm)\b",
        " ", title or "")
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z&/-]{2,}", cleaned)]
    if words:
        return " ".join(words[:4])
    blob = " ".join(evidence).lower()
    for word in ("payments", "platform", "growth", "consumer", "marketplace", "enterprise", "mobile"):
        if word in blob:
            return word
    return ""


def user_path_text(profile, target_role_id: str, target_title: str) -> tuple:
    """(sentence, target level) from the reviewed profile. Employers are not included. Unknowns are left out."""
    target_level = ROLE_LEVEL.get(target_role_id or "", "") or canonical_level(target_title or "", required=False) or "Product Manager"
    if target_level not in LEVELS:
        target_level = "Product Manager"
    reviewed = ("user_confirmed", "user_corrected")
    jobs = [j for j in (getattr(profile, "employment_history", None) or [])
            if getattr(getattr(j, "provenance", None), "review_status", "") in reviewed]
    pm_jobs = []
    priors = []
    for job in jobs:
        title = getattr(job, "title_raw", "") or ""
        family = getattr(job, "role_family", "") or ""
        role = getattr(job, "role_id", "") or ""
        if is_product_role(title, role):
            pm_jobs.append(job)
            continue
        fn = _function_for_title(title, family)
        if fn and fn not in priors:
            priors.append(fn)
    parts = [f"Target: {target_level}."]
    if pm_jobs:
        current = pm_jobs[0]
        for job in pm_jobs:
            if getattr(job, "is_current_as_stated", False):
                current = job
                break
        level = ROLE_LEVEL.get(getattr(current, "role_id", "") or "", "") or canonical_level(getattr(current, "title_raw", ""), required=False)
        if level:
            parts.append(f"Current: {level}.")
        detail = []
        years = _years_in(pm_jobs)
        if years:
            detail.append(years)
        detail.append(f"{len(pm_jobs)} product roles")
        parts.append(", ".join(detail).capitalize() + "." if years else f"{len(pm_jobs)} product roles.")
        evidence = []
        for job in pm_jobs:
            for line in list(getattr(job, "responsibilities", []) or []) + list(getattr(job, "achievements", []) or []):
                evidence.append(getattr(line, "evidence_text", "") or "")
        scope = _scope(getattr(current, "title_raw", ""), evidence)
        if scope:
            parts.append(f"Scope: {scope}.")
    if priors:
        parts.append(f"Before PM: {', '.join(priors[:3])}, {len([j for j in jobs if j not in pm_jobs])} roles.")
    education = [e for e in (getattr(profile, "education", None) or [])
                 if getattr(getattr(e, "provenance", None), "review_status", "") in reviewed]
    titles = " ".join((getattr(e, "qualification_title_raw", "") or "") for e in education).lower()
    edu = []
    if education:
        edu.append("Has a degree")
    if "mba" in titles or "pgdm" in titles:
        edu.append("an MBA")
    elif education:
        edu.append("no MBA")
    if edu:
        parts.append(", ".join(edu) + ".")
    return " ".join(parts), target_level
