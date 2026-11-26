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


def parse_bool(value: str, column: str) -> bool:
    key = (value or "").strip().lower()
    if key not in _BOOL:
        raise CohortFileError(f"{column} must be true or false")
    return _BOOL[key]


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
    level = (raw["norm_seniority_group"] or "").strip()
    if level not in LEVELS:
        raise CohortFileError("norm_seniority_group is not a known group")
    move = (raw["final_entry_move"] or "").strip().lower()
    if move not in ("", "internal", "external"):
        raise CohortFileError("final_entry_move must be internal, external, or blank")
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
        "final_first_pm_group": (raw["final_first_pm_group"] or "").strip(),
        "final_first_pm_dated": parse_bool(raw["final_first_pm_dated"], "final_first_pm_dated"),
        "final_months_to_first_pm_from_earliest_nonintern": parse_int(
            raw["final_months_to_first_pm_from_earliest_nonintern"],
            "final_months_to_first_pm_from_earliest_nonintern", blank_ok=True),
        "final_has_history": parse_bool(raw["final_has_history"], "final_has_history"),
        "final_dated_role_before_first_pm": parse_bool(raw["final_dated_role_before_first_pm"], "final_dated_role_before_first_pm"),
        "final_n_pre_pm_roles": parse_int(raw["final_n_pre_pm_roles"], "final_n_pre_pm_roles", blank_ok=False) or 0,
        "final_n_roles": parse_int(raw["final_n_roles"], "final_n_roles", blank_ok=False) or 0,
        "final_n_pm_roles": parse_int(raw["final_n_pm_roles"], "final_n_pm_roles", blank_ok=False) or 0,
        "norm_edu_has_degree": parse_bool(raw["norm_edu_has_degree"], "norm_edu_has_degree"),
        "norm_career_start_year": parse_int(raw["norm_career_start_year"], "norm_career_start_year", blank_ok=True),
        "has_mba": parse_bool(raw["has_mba"], "has_mba"),
    }


def cohort_path_text(row: dict) -> str:
    """The sentence that is embedded for one cohort profile."""
    prior = row.get("final_primary_prior_function") or "a background that was not classified"
    n_pre = row.get("final_n_pre_pm_roles") or 0
    move = row.get("final_entry_move") or ""
    months = row.get("final_months_to_first_pm_from_earliest_nonintern")
    parts = [f"{row['norm_seniority_group']}."]
    parts.append(f"Before PM: {prior}, {n_pre} roles.")
    if move == "internal" and months is not None:
        parts.append(f"Moved into PM internally after about {months} months.")
    elif move == "external" and months is not None:
        parts.append(f"Moved into PM from outside the employer after about {months} months.")
    elif move == "internal":
        parts.append("Moved into PM internally.")
    elif move == "external":
        parts.append("Moved into PM from outside the employer.")
    degree = "Has a degree" if row.get("norm_edu_has_degree") else "No degree recorded"
    mba = "an MBA" if row.get("has_mba") else "no MBA"
    parts.append(f"{degree}, {mba}.")
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


def _function_for_title(title: str, family: str) -> str:
    blob = f"{family} {title}".lower()
    if any(w in blob for w in ("engineer", "software", "developer", "qa", "quality", "it ")):
        return "Software Engineering / QA / IT"
    if any(w in blob for w in ("consult", "analyst", "finance", "operation", "bank")):
        return "Business / Consulting / Finance / Operations"
    if any(w in blob for w in ("design", "research")):
        return "Design / Research"
    if any(w in blob for w in ("product",)):
        return ""
    return "Other / Uncertain"


def user_path_text(profile, target_role_id: str, target_title: str) -> tuple:
    """(sentence, target level) from the reviewed profile. Employers are not included."""
    target_level = ROLE_LEVEL.get(target_role_id or "", "") or (target_title or "Product Manager")
    jobs = [j for j in (getattr(profile, "employment_history", None) or [])
            if getattr(getattr(j, "provenance", None), "review_status", "") in ("user_confirmed", "user_corrected")]
    current = ""
    priors = []
    for job in jobs:
        title = getattr(job, "title_raw", "") or ""
        family = getattr(job, "role_family", "") or ""
        role = (getattr(job, "role_id", "") or "")
        if role.startswith("PM-") or "product" in title.lower():
            if not current:
                current = ROLE_LEVEL.get(role, "") or title
            continue
        fn = _function_for_title(title, family)
        if fn and fn not in priors:
            priors.append(fn)
    prior = priors[0] if priors else "a background that was not classified"
    n_pre = sum(1 for job in jobs if not (getattr(job, "role_id", "") or "").startswith("PM-")
                and "product" not in (getattr(job, "title_raw", "") or "").lower())
    education = [e for e in (getattr(profile, "education", None) or [])
                 if getattr(getattr(e, "provenance", None), "review_status", "") in ("user_confirmed", "user_corrected")]
    titles = " ".join((getattr(e, "qualification_title_raw", "") or "") for e in education).lower()
    has_degree = bool(education)
    has_mba = "mba" in titles or "pgdm" in titles
    parts = [f"Target: {target_level}."]
    if current:
        parts.append(f"Current: {current}.")
    parts.append(f"Before PM: {prior}, {n_pre} roles.")
    parts.append(f"{'Has a degree' if has_degree else 'No degree recorded'}, {'an MBA' if has_mba else 'no MBA'}.")
    return " ".join(parts), target_level if target_level in LEVELS else "Product Manager"
