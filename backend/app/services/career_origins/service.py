"""Load and check the Career Origins & Transitions artifact, and connect it to a user's confirmed current role family.

The artifact is built offline by scripts/build_career_origins.py. Here it is only read and checked; nothing is counted,
ranked or re-derived. A missing, changed or unexpected artifact is reported as unavailable, never replaced by other data.

Checking is an allowlist (schema.StoredArtifact: every field declared, extra="forbid", suppression rules built in), then
two text scans: no identifier-like content anywhere in the file (LEAK), and no source, provider or collection-method
wording in the part that is served (schema.SOURCE_TERMS). Only `public()` is ever returned to a client; the file's
`internal` provenance block never is. Validation failures are logged by location only, never with the offending value.

The connection uses ONE input: the role family of the user's confirmed current position, as stored with their saved
plan. It maps that family to a research function only where the research taxonomy's own definition covers the family's
titles (FAMILY_TO_FUNCTION). No similarity score, weight or probability is produced, and the plan is not changed.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from app.services.candidate_profile.repository import RecordNotFound
from app.services.career_origins.schema import (
    ELIGIBLE_PROFILES, SCHEMA_VERSION, SOURCE_TERMS, TAXONOMY_VERSION, Connection, PublicArtifact, StoredArtifact,
)

log = logging.getLogger(__name__)
ARTIFACT_PATH = Path(__file__).resolve().parent / "content" / "india-pm-origins-v3.json"

# Trace role family -> research function key, where the research taxonomy's definition explicitly covers the family's
# titles (background_taxonomy.md, v3). Families without a defensible match are left out on purpose: customer support,
# engineering management, human resources and 'unknown' are not named by any research function. Product management is
# handled separately (the user is already in the field), and Agile Delivery has no Trace family.
FAMILY_TO_FUNCTION = {
    "software_engineering": "software_qa_it",
    "qa_testing": "software_qa_it",
    "data_analytics": "data_analytics",
    "data_science": "data_analytics",
    "product_analytics": "data_analytics",          # "product analyst" is named in the research definition
    "business_analysis": "business_ops",
    "consulting": "business_ops",
    "finance": "business_ops",
    "operations": "business_ops",
    "project_program_management": "business_ops",   # "programme/project manager" is named in the research definition
    "sales": "sales_marketing_cs",
    "marketing_growth": "sales_marketing_cs",
    "customer_success": "sales_marketing_cs",
    "design_ux": "design_ux",
    "entrepreneurship": "founders",
    "product_ownership": "product_ownership",
}
FAMILY_LABEL = {
    "software_engineering": "software engineering", "qa_testing": "QA and testing", "data_analytics": "data analytics",
    "data_science": "data science", "product_analytics": "product analytics", "business_analysis": "business analysis",
    "consulting": "consulting", "finance": "finance", "operations": "operations",
    "project_program_management": "project and program management", "sales": "sales and account management",
    "marketing_growth": "marketing and growth", "customer_success": "customer success", "design_ux": "design and UX",
    "entrepreneurship": "entrepreneurship", "product_ownership": "product ownership",
    "product_management": "product management", "customer_support": "customer and technical support",
    "engineering_management": "engineering management", "human_resources": "human resources",
}

# Anything that looks like an identifier, a URL or contact detail must never be in the artifact.
LEAK = re.compile(r"https?://|www\.|linkedin|@[a-z0-9-]+\.|\b\d{7,}\b|profile_ref|src_|norm_prior_companies|"
                  r"norm_institutions|final_prev_role_title|final_pre_pm_roles", re.I)


class CareerOriginsDisabled(RecordNotFound):
    """The feature is off (TRACE_CAREER_ORIGINS is not set): the endpoint doesn't exist for clients."""


class ArtifactUnavailable(Exception):
    """The artifact is missing, unreadable or not the expected version."""


def check(artifact: dict) -> dict:
    """Validate the stored artifact against the allowlist schema and the text scans. Returns it unchanged, or raises."""
    if not isinstance(artifact, dict) or artifact.get("schema_version") != SCHEMA_VERSION:
        raise ArtifactUnavailable("unexpected schema version")
    research = artifact.get("research") or {}
    if research.get("taxonomy_version") != TAXONOMY_VERSION or research.get("eligible_profiles") != ELIGIBLE_PROFILES:
        raise ArtifactUnavailable("not the v3 final research cohort")
    try:
        StoredArtifact.model_validate(artifact)
    except ValidationError as exc:
        where = sorted({".".join(str(p) for p in e["loc"][:3]) for e in exc.errors(include_input=False)})[:5]
        log.warning("career origins artifact rejected by the output schema at %s", where)
        raise ArtifactUnavailable("the artifact doesn't match the output schema: " + ", ".join(where)) from None
    if LEAK.search(json.dumps(artifact, ensure_ascii=False)):
        log.warning("career origins artifact rejected: identifier-like content")
        raise ArtifactUnavailable("the artifact contains identifier-like content")
    if SOURCE_TERMS.search(json.dumps({k: v for k, v in artifact.items() if k != "internal"}, ensure_ascii=False)):
        log.warning("career origins artifact rejected: source wording in served text")
        raise ArtifactUnavailable("the served part names the data source or collection method")
    return artifact


def public(artifact: dict) -> dict:
    """The only part of the artifact a client may receive: the allowlisted public fields, without `internal`."""
    return PublicArtifact.model_validate({k: v for k, v in artifact.items() if k != "internal"}).model_dump(by_alias=True)


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict:
    return check(json.loads(Path(path).read_text(encoding="utf-8")))


def load_artifact(path: Path = ARTIFACT_PATH) -> dict:
    try:
        return _load(str(path), path.stat().st_mtime)
    except (OSError, ValueError) as exc:
        raise ArtifactUnavailable("the research artifact can't be read") from exc


def _find(items, key):
    return next((x for x in items if x["key"] == key), None)


def connection(artifact: dict, position: Optional[dict]) -> dict:
    """See _connection. Validated against the Connection allowlist: it can only repeat the artifact's own group figures."""
    return Connection.model_validate(_connection(artifact, position)).model_dump(by_alias=True, exclude_none=True)


def _connection(artifact: dict, position: Optional[dict]) -> dict:
    """How the research relates to the user's confirmed current role, by role family only. Always says what it is: a
    description of other people's histories, not a statement about the user."""
    position = position or {}
    family = position.get("role_family") if position.get("status") == "known" else None
    if not family:
        return {"status": "no_current_role",
                "text": "Your plan doesn't have a confirmed current role, so the research isn't connected to your background. "
                        "Confirm your current role in your profile to see the closest research group, if there is one."}
    label = FAMILY_LABEL.get(family, family.replace("_", " "))
    if family == "product_management":
        return {"status": "already_in_field", "role_family_label": label,
                "text": "Your confirmed current role is already in product management, so the most relevant part of the "
                        "research is how people moved between product-management levels.",
                "progression": artifact["progression"]["steps"]}
    key = FAMILY_TO_FUNCTION.get(family)
    if key is None:
        return {"status": "not_mapped", "role_family_label": label,
                "text": f"The research's job-title groups don't include one that clearly covers {label}, so Trace doesn't "
                        "connect your background to one. The findings for the whole sample still apply as general context."}
    m = artifact["metrics"]
    primary = _find(m["primary_background"]["buckets"], key)
    return {
        "status": "mapped", "role_family_label": label, "function_key": key, "function_label": primary["label"],
        "mapping_basis": f"Your confirmed current role is in {label}, which the research groups under "
                         f"'{primary['label']}' by job title.",
        "primary_background": {**primary, "denominator": m["primary_background"]["denominator"]},
        "preceding_role": {**_find(m["preceding_function"]["buckets"], key), "denominator": m["preceding_function"]["denominator"]},
        "time_to_first_pm": _find(artifact["time_to_first_pm"]["by_background"], key),
        "separate_note": ("Product Ownership is kept separate from product management in the research; a Product Owner "
                          "title is not counted as a PM role.") if key == "product_ownership" else None,
        "text": "This describes people in the research sample whose earlier titles fall in the same group. It isn't a "
                "prediction for you, and it doesn't change your plan.",
    }


# ------------------------------------------------------------------------------------------- the plan's narrative (Phase 5)
LEVEL_OF_ROLE = {"PM-APM": "Associate Product Manager", "PM-PM": "Product Manager", "PM-SPM": "Senior Product Manager",
                 "PM-LEAD": "Lead", "PM-PRINCIPAL": "Principal / Staff / Group", "PM-GPM": "Principal / Staff / Group",
                 "PM-DIR": "Director", "PM-HEAD": "Head"}
FIRST_LEVEL_OF_ROLE = {"PM-APM": "associate", "PM-PM": "product_manager"}
PATHWAY_TEXT = {
    ("route", "same"): ("Straight from their main background",
                        "The role just before their first PM role was in the same function as most of their earlier work."),
    ("route", "different"): ("After a role in a different function",
                             "They moved into product management after a role in another function (an intermediate step)."),
    ("entry", "internal"): ("Within the same employer", "Their first PM role was with the employer they were already at."),
    ("entry", "external"): ("With a new employer", "Their first PM role meant joining a different employer."),
    ("first_level", "associate"): ("Starting as an Associate or junior PM", "Their first PM title was at associate or junior level."),
    ("first_level", "product_manager"): ("Starting as a Product Manager", "Their first PM title was Product Manager."),
    ("first_level", "senior"): ("Starting at Senior PM or above", "Their first PM title was senior: usually after a long earlier career."),
}
CANNOT_SHOW = [
    "The research has no industry or company-type information, so it can't say anything about particular companies or kinds of company.",
    "It shows how often something happened in this sample, not how likely it is for you, and not why it happened.",
]


def _observed(bucket: dict, denominator: int) -> dict:
    o = {"display": bucket["display"], "flag": bucket["flag"]}
    if bucket["display"] == "exact":
        o.update(count=bucket["count"], share_pct=bucket["share_pct"], denominator=denominator)
    else:
        o.update(band=bucket["band"], denominator=denominator)
    return o


def _step_observed(step: dict) -> dict:
    o = {"display": step["display"], "flag": step["flag"]}
    if step["display"] == "exact":
        o.update(n=step["n"], median_months=step["median_months"])
        if step.get("iqr_months"):
            o["iqr_months"] = step["iqr_months"]
    else:
        o["band"] = step["band"]
    return o


def own_route(profile) -> Optional[dict]:
    """The user's OWN route into product management, from their confirmed history, in the research's dimensions only:
    main background function, whether the role just before their first PM role was in that function, whether it was
    with the same employer, and the level of the first PM role. Computed for the response; never stored."""
    reviewed = ("user_confirmed", "user_corrected")
    jobs = sorted((e for e in profile.employment_history if e.provenance.review_status in reviewed and e.primary_career_sequence
                   and e.role_family and e.role_family != "unknown" and not e.mapping_is_stale and e.mapping_status != "unresolved"
                   and getattr(e, "employment_type", None) != "internship"), key=lambda e: e.sequence_index)
    first = next((i for i, e in enumerate(jobs) if e.role_family == "product_management"), None)
    if not first:
        return None                                   # no PM role yet, or nothing confirmed before it
    before = jobs[:first]
    functions = [FAMILY_TO_FUNCTION.get(e.role_family) for e in before]
    known = [f for f in functions if f]
    if not known:
        return None
    main = max(dict.fromkeys(known), key=known.count)
    prev = before[-1]
    emp = lambda e: " ".join(((e.employer.employer_name if e.employer else "") or "").lower().split())   # noqa: E731
    pm = jobs[first]
    return {"background": main, "route": "same" if functions[-1] == main else ("different" if functions[-1] else None),
            "entry": ("internal" if emp(prev) and emp(prev) == emp(pm) else "external") if emp(prev) and emp(pm) else None,
            "first_level": FIRST_LEVEL_OF_ROLE.get(pm.role_id, "senior" if pm.role_id in LEVEL_OF_ROLE else None)}


def narrative(artifact: dict, position: Optional[dict], target_role_id: str, mine: Optional[dict] = None,
              plan_route: Optional[List[str]] = None) -> dict:
    """See Narrative. Groups are chosen by role function only, the one dimension the research and Trace share; nothing
    is matched on attributes the research doesn't hold. Pathways are the observed variants of that group (and, for
    someone already in product management, the steps between PM levels), each with its figure exactly as stored."""
    position = position or {}
    family = position.get("role_family") if position.get("status") == "known" else None
    if not family:
        return {"status": "no_current_role", "basis": "Your plan doesn't have a confirmed current role yet, so the research "
                "can't be connected to your starting point.", "cannot_show": CANNOT_SHOW}
    groups = {g["key"]: g for g in (artifact.get("routes_by_background") or {}).get("groups", [])}
    in_pm = family == "product_management"
    key = (mine or {}).get("background") if in_pm else FAMILY_TO_FUNCTION.get(family)
    pathways, cannot = [], list(CANNOT_SHOW)
    plan_route = plan_route or []
    if in_pm:
        steps = {s["key"]: s for s in artifact["progression"]["steps"]}
        cur, tgt = LEVEL_OF_ROLE.get(position.get("role_id")), LEVEL_OF_ROLE.get(target_role_id)
        if cur and tgt and cur != tgt:
            k = f"{FUNCTION_SLUG(cur)}__{FUNCTION_SLUG(tgt)}"
            if k in steps:
                pathways.append({"key": "step_" + k, "dimension": "progression", "title": f"{cur} to {tgt}",
                                 "description": "People in the sample who held both titles: the time between the first "
                                                f"dated {cur} role and the first dated {tgt} role.",
                                 "observed": _step_observed(steps[k]), "matches_you": True,
                                 "relates_to_you": "This is the step your plan is about."})
            else:
                cannot.append(f"The research doesn't record a {cur} to {tgt} step directly.")
        elif cur and tgt == cur:
            cannot.append("The research records moves between product-management levels, not moves between companies at "
                          "the same level, so it can't describe a same-role move.")
    group = groups.get(key) if key else None
    if group:
        for b in group["breakdowns"]:
            for bucket in b["buckets"]:
                text = PATHWAY_TEXT.get((b["key"], bucket["key"]))
                if not text:
                    continue
                match = bool(mine) and in_pm and (mine.get(b["key"]) == bucket["key"])
                relates = None
                if match:
                    relates = "This matches your own route into product management."
                elif not in_pm and b["key"] == "first_level" and bucket["key"] == "associate" and "PM-APM" in plan_route[1:-1]:
                    relates = "Your plan's recommended route goes through an Associate PM role first."
                elif not in_pm and b["key"] == "route" and bucket["key"] == "same" and len(plan_route) == 2:
                    relates = "Your plan's recommended route is a direct move."
                pathways.append({"key": f"{b['key']}_{bucket['key']}", "dimension": b["key"], "title": text[0],
                                 "description": text[1], "observed": _observed(bucket, b["denominator"]),
                                 "relates_to_you": relates, "matches_you": match})
        label = FAMILY_LABEL.get(family, family.replace("_", " "))
        basis = (f"Your earlier career before product management was mainly in {group['label']}, by job title. These are "
                 f"the routes people in the research with that main background took into their first PM role."
                 if in_pm else
                 f"Your confirmed current role is in {label}, which the research groups under '{group['label']}' by job "
                 "title. These are the routes people with that main background took into their first PM role.")
        your = None
        if mine and in_pm:
            bits = [PATHWAY_TEXT[(d, mine[d])][0].lower() for d in ("route", "entry", "first_level") if mine.get(d) and (d, mine[d]) in PATHWAY_TEXT]
            your = ("Your own move into product management, from your confirmed history: " + "; ".join(bits) + ".") if bits else None
        return {"status": "group", "basis": basis, "group_key": group["key"], "group_label": group["label"],
                "group_n": group["n"], "group_flag": group["flag"], "pathways": pathways, "your_route": your,
                "cannot_show": cannot}
    if pathways or in_pm:
        return {"status": "progression_only", "basis": "You're already in product management, so the most relevant part of "
                "the research is how people moved between product-management levels.", "pathways": pathways,
                "cannot_show": cannot + ["Your earlier background isn't one the research breaks down (fewer than 10 "
                                         "people, or not classified), so routes into product management aren't shown."]}
    label = FAMILY_LABEL.get(family, family.replace("_", " "))
    return {"status": "not_mapped", "basis": f"The research doesn't break down a group that clearly covers {label} "
            "(it needs 10 or more people with that background), so it isn't connected to your starting point.",
            "cannot_show": cannot}


def FUNCTION_SLUG(s: str) -> str:  # noqa: N802  (same slug rule as the builder)
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
