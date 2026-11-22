#!/usr/bin/env python3
"""Validate every seed, taxonomy and configuration file in the Trace Data Foundation.

  python3 scripts/validate_seed_data.py            shared package + restricted local fixtures if present
  python3 scripts/validate_seed_data.py --shared   shared package only

Checks structure, references, dates, sequences, dataset separation and consent rules. It does
not and cannot verify that resume claims are true or that consent was validly obtained.
Standard library only. Exit status 1 if any check fails.
"""
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_seeds  # noqa: E402
import package_review  # noqa: E402
import trace_model as tm  # noqa: E402

ROOT = tm.ROOT
SYNTHETIC_SOURCE = "SRC-SYNTHETIC-001"
REQUIRED_SETTINGS = ("min_profiles_limited", "min_profiles_moderate", "min_profiles_strong",
                     "min_dated_profiles_for_timeline", "min_mapping_confidence")
RETIRED_SETTINGS = ("max_boundary_overlap_months",)  # overlaps are never auto-confirmed (v2.0.1)
PM_LADDER = ("PM-APM", "PM-PM", "PM-SPM", "PM-LEAD", "PM-PRINCIPAL", "PM-DIR")
EMPLOYMENT_TYPES = ("full_time", "part_time", "contract", "internship", "apprenticeship", "freelance", "founder", "volunteer", "unknown")
MAPPING_STATUSES = ("confirmed", "auto_high_confidence", "needs_review", "unresolved")
EVIDENCE_TYPES = ("responsibility", "achievement", "scope", "leadership", "skill_evidence", "other")
EVIDENCE_STATUSES = ("extracted_unverified", "participant_confirmed", "document_verified", "synthetic_curated", "rejected")
REVIEW_STATUSES = ("extracted_unverified", "participant_confirmed", "reviewer_confirmed", "synthetic_curated", "rejected")
SKILL_SOURCES = ("resume_explicit", "llm_inferred", "participant_confirmed", "synthetic_fixture")
DATASET_CLASSES = ("consented_real_reference", "uploaded_resume_fixture", "synthetic_test_fixture", "public_aggregate_reference")
INDUSTRY_SECTORS = ("fintech", "banking_financial_services", "saas", "enterprise_software", "consumer_internet",
                    "ecommerce_marketplace", "logistics", "hr_tech", "analytics_ai", "media_entertainment",
                    "manufacturing", "healthcare", "edtech", "it_services", "consulting", "agency", "other", "unknown")
COMPANY_MODELS = ("product_company", "it_services", "consulting", "agency", "global_capability_centre", "non_tech_enterprise", "hybrid", "unknown")
BUSINESS_MODELS = ("b2b", "b2c", "b2b2c", "marketplace", "internal", "mixed", "unknown")
SIZE_BANDS = ("1-10", "11-50", "51-200", "201-1000", "1001-5000", "5001-plus", "unknown")
ATTR_PROVENANCE = ("resume_stated", "participant_confirmed", "inferred_unverified", "synthetic_curated", "unknown")
QUAL_LEVELS = ("secondary", "diploma", "bachelor", "master", "mba", "doctorate", "certificate", "other", "unknown")
STUDY_MODES = ("full_time", "part_time", "executive", "online", "unknown")
COMPLETION = ("completed", "in_progress", "incomplete", "unknown")
BREAK_REASONS = ("education", "caregiving", "health", "relocation", "job_search", "sabbatical", "personal", "unspecified")
FORBIDDEN_V1_KEYS = ("consent_status", "shared_analytics_consent", "external_demo_consent", "analytics_eligible",
                     "demo_eligible", "employment_history", "is_synthetic")
IDENTIFIER_KEYS = ("name", "full_name", "email", "phone", "linkedin", "url", "address")
EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RX = re.compile(r"(?<![\d-])(\+?91[\s-]?)?[6-9]\d{9}(?!\d)")
URL_RX = re.compile(r"https?://|www\.|linkedin\.com", re.I)


class Report:
    def __init__(self):
        self.results = []  # (name, errors, checked_count)

    def add(self, name, errors, count):
        self.results.append((name, list(errors), count))

    @property
    def failed(self):
        return [r for r in self.results if r[1]]

    def errors_for(self, name):
        for n, errs, _ in self.results:
            if n == name:
                return errs
        raise KeyError(name)

    def print(self, out=sys.stdout):
        for name, errs, count in self.results:
            status = "PASS" if not errs else "FAIL"
            print(f"{status}  {name}  ({count} checked)", file=out)
            for e in errs[:25]:
                print(f"        - {e}", file=out)
            if len(errs) > 25:
                print(f"        ... {len(errs) - 25} more", file=out)
        print(f"\n{len(self.results) - len(self.failed)} passed, {len(self.failed)} failed", file=out)


# --------------------------------------------------------------------------- lookups
def check_taxonomy(lk):
    errs = []
    fam = {r["role_family"] for r in lk["families"]}
    sen = {r["seniority_code"]: r for r in lk["seniority"]}
    ranks = [r["rank"] for r in lk["seniority"] if r["rank"]]
    if len(ranks) != len(set(ranks)):
        errs.append("seniority ranks are not unique")
    roles = {}
    for r in lk["roles"]:
        if r["role_id"] in roles:
            errs.append(f"duplicate role_id {r['role_id']}")
        roles[r["role_id"]] = r
        if not re.fullmatch(r"(PM|ROLE)-[A-Z0-9]+", r["role_id"]):
            errs.append(f"bad role_id format {r['role_id']}")
        if r["role_family"] not in fam:
            errs.append(f"{r['role_id']}: unknown role_family {r['role_family']}")
        if r["default_seniority_code"] not in sen:
            errs.append(f"{r['role_id']}: unknown default_seniority_code {r['default_seniority_code']}")
        if tm.as_bool(r["is_pm_target"]) and r["role_family"] != "product_management":
            errs.append(f"{r['role_id']}: is_pm_target outside product_management")
        if tm.as_bool(r["is_pm_target"]) and not tm.as_bool(r["is_resolved"]):
            errs.append(f"{r['role_id']}: unresolved role cannot be a PM target")
    for rid in PM_LADDER:
        if rid not in roles or not tm.as_bool(roles[rid]["is_pm_target"]):
            errs.append(f"PM ladder role {rid} missing or not a PM target")
    if "ROLE-UNKNOWN" not in roles or tm.as_bool(roles["ROLE-UNKNOWN"]["is_resolved"]):
        errs.append("ROLE-UNKNOWN must exist with is_resolved=false")
    if roles.get("ROLE-OWNER", {}).get("role_family") == "product_management":
        errs.append("ROLE-OWNER must not sit in product_management (Product Owner is not PM by default)")
    ids, by_alias = set(), {}
    for a in lk["aliases"]:
        if a["alias_id"] in ids:
            errs.append(f"duplicate alias_id {a['alias_id']}")
        ids.add(a["alias_id"])
        if not re.fullmatch(r"ALS-\d{4}", a["alias_id"]):
            errs.append(f"bad alias_id {a['alias_id']}")
        if a["role_id"] not in roles:
            errs.append(f"{a['alias_id']}: unknown role {a['role_id']}")
        if a["seniority_hint_code"] and a["seniority_hint_code"] not in sen:
            errs.append(f"{a['alias_id']}: unknown seniority hint {a['seniority_hint_code']}")
        if not 0 <= float(a["default_confidence"]) <= 1:
            errs.append(f"{a['alias_id']}: confidence out of range")
        ctx_req = tm.as_bool(a["context_required"])
        if ctx_req and not a["mapping_rule"]:
            errs.append(f"{a['alias_id']}: context_required alias needs a mapping_rule")
        norm = " ".join(a["alias"].strip().lower().split())
        key = (norm, a["role_id"])
        if key in {(k, r) for k, rs in by_alias.items() for r, _ in rs}:
            errs.append(f"duplicate alias/role pair {key}")
        by_alias.setdefault(norm, []).append((a["role_id"], ctx_req))
    for norm, targets in by_alias.items():
        if len(targets) > 1 and not all(c for _, c in targets):
            errs.append(f"alias '{norm}' maps to several roles, so every mapping must be context_required")
    for must in ("product owner", "associate", "tpm", "pm"):
        if must in by_alias and not all(c for _, c in by_alias[must]):
            errs.append(f"alias '{must}' must be context_required")
    for a in lk["areas"]:
        if not re.fullmatch(r"[a-z_]+", a["responsibility_area"]):
            errs.append(f"bad responsibility_area {a['responsibility_area']}")
    return errs, len(lk["roles"]) + len(lk["aliases"]) + len(lk["seniority"]) + len(lk["families"])


def check_skills(lk):
    errs = []
    fams = {r["skill_family"] for r in lk["skill_families"]}
    ids, names = set(), set()
    for s in lk["skills"]:
        if s["skill_id"] in ids:
            errs.append(f"duplicate skill_id {s['skill_id']}")
        ids.add(s["skill_id"])
        if not re.fullmatch(r"SKL-[A-Z0-9-]+", s["skill_id"]):
            errs.append(f"bad skill_id {s['skill_id']}")
        if s["canonical_skill"] != s["canonical_skill"].lower() or s["canonical_skill"] in names:
            errs.append(f"canonical_skill must be lower-case and unique: {s['canonical_skill']}")
        names.add(s["canonical_skill"])
        if s["skill_family"] not in fams:
            errs.append(f"{s['skill_id']}: unknown family {s['skill_family']} (unclassified skills are not allowed)")
    for a in lk["skill_aliases"]:
        if a["skill_id"] not in ids:
            errs.append(f"skill alias '{a['alias']}' points to unknown skill {a['skill_id']}")
        if a["alias"] in names:
            errs.append(f"skill alias '{a['alias']}' duplicates a canonical skill")
        if a["alias"] != a["alias"].strip().lower():
            errs.append(f"skill alias '{a['alias']}' must be lower-case and trimmed")
    return errs, len(lk["skills"]) + len(lk["skill_aliases"])


def check_config(lk):
    errs = []
    s = {r["setting_key"]: float(r["value_numeric"]) for r in lk["settings"]}
    for k in REQUIRED_SETTINGS:
        if k not in s:
            errs.append(f"missing analytics setting {k}")
    if all(k in s for k in ("min_profiles_limited", "min_profiles_moderate", "min_profiles_strong")):
        if not s["min_profiles_limited"] <= s["min_profiles_moderate"] <= s["min_profiles_strong"]:
            errs.append("thresholds must satisfy limited <= moderate <= strong")
        if s["min_profiles_limited"] < 1:
            errs.append("min_profiles_limited must be at least 1")
    if "min_mapping_confidence" in s and not 0 < s["min_mapping_confidence"] <= 1:
        errs.append("min_mapping_confidence must be in (0, 1]")
    for k in RETIRED_SETTINGS:
        if k in s:
            errs.append(f"setting {k} is retired: overlapping roles must stay unconfirmed until explicitly reviewed")
    for src in lk["sources"]:
        if src["dataset_class"] == "uploaded_resume_fixture":
            errs.append(f"{src['source_id']}: uploaded-resume sources must not be in the shared package")
        if src["dataset_class"] not in DATASET_CLASSES:
            errs.append(f"{src['source_id']}: unknown dataset_class {src['dataset_class']}")
        if src["seed_target"] not in ("lookup_migration", "synthetic_seed"):
            errs.append(f"{src['source_id']}: seed_target must be lookup_migration or synthetic_seed")
        if src["seed_target"] == "lookup_migration" and src["dataset_class"] == "synthetic_test_fixture":
            errs.append(f"{src['source_id']}: synthetic sources belong in the synthetic seed, not the production migration")
    return errs, len(lk["settings"]) + len(lk["sources"])


# --------------------------------------------------------------------------- profiles
def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def _bounds_or_error(errs, where, value, precision, allow_ongoing):
    if precision not in tm.DATE_PRECISIONS:
        errs.append(f"{where}: unknown precision {precision!r}")
        return None, None
    if precision == "ongoing" and not allow_ongoing:
        errs.append(f"{where}: 'ongoing' is only valid for end dates")
        return None, None
    try:
        lo, hi = tm.bounds(value, precision)
    except ValueError as exc:
        errs.append(f"{where}: {exc}")
        return None, None
    if lo is not None and not 1950 * 12 <= lo <= 2100 * 12 + 11:
        errs.append(f"{where}: year out of range")
    return lo, hi


def check_profiles(profiles, lk, ctx, expected_class, label):
    """Return {check_name: errors} for a list of canonical-format profiles."""
    roles = {r["role_id"]: r for r in lk["roles"]}
    sen = {r["seniority_code"] for r in lk["seniority"]}
    areas = {r["responsibility_area"] for r in lk["areas"]}
    skills = {r["skill_id"] for r in lk["skills"]}
    sources = {r["source_id"]: r for r in lk["sources"]}
    aliases = {}
    for a in lk["aliases"]:
        aliases.setdefault(" ".join(a["alias"].strip().lower().split()), []).append(a)
    min_conf = ctx.setting("min_mapping_confidence", 1.0)
    out = {k: [] for k in ("schema_and_identifiers", "date_integrity", "role_taxonomy_and_seniority",
                           "profile_relationships", "complete_sequences", "data_class_and_consent")}
    seen_profiles, seen_global = set(), set()

    for p in profiles:
        pid = p.get("profile_id", "?")
        E = out["schema_and_identifiers"]
        if pid in seen_profiles:
            E.append(f"{pid}: duplicate profile_id")
        seen_profiles.add(pid)
        if not re.fullmatch(r"[A-Z]{3}-\d{3,}", pid):
            E.append(f"{pid}: bad profile_id format")
        for k in ("source_id", "dataset_class", "country_code", "resume_as_of_precision", "review_status",
                  "scope_status", "employers", "employments", "education", "career_breaks", "evidence", "skills"):
            if k not in p:
                E.append(f"{pid}: missing key {k}")
        for k in FORBIDDEN_V1_KEYS:
            if k in p:
                E.append(f"{pid}: v1 key '{k}' is not allowed; consent lives in consent events, not on profiles")
        for k in IDENTIFIER_KEYS:
            if k in p:
                E.append(f"{pid}: direct identifier field '{k}' present")
        for s in _strings(p):
            if EMAIL_RX.search(s) or PHONE_RX.search(s) or URL_RX.search(s):
                E.append(f"{pid}: possible direct identifier in text: {s[:60]!r}")
        if p.get("review_status") not in REVIEW_STATUSES:
            E.append(f"{pid}: unknown review_status {p.get('review_status')}")
        if not re.fullmatch(r"[A-Z]{2}", p.get("country_code", "")):
            E.append(f"{pid}: bad country_code")

        # --- data class and consent
        C = out["data_class_and_consent"]
        cls = p.get("dataset_class")
        if cls != expected_class:
            C.append(f"{pid}: dataset_class {cls} where {expected_class} expected ({label})")
        if cls == "synthetic_test_fixture":
            if not pid.startswith("SYN-"):
                C.append(f"{pid}: synthetic profiles must use the SYN- prefix")
            if p.get("source_id") != SYNTHETIC_SOURCE or sources.get(SYNTHETIC_SOURCE, {}).get("dataset_class") != "synthetic_test_fixture":
                C.append(f"{pid}: synthetic profile must reference {SYNTHETIC_SOURCE} registered as synthetic_test_fixture")
            if p.get("review_status") != "synthetic_curated":
                C.append(f"{pid}: synthetic profiles must have review_status synthetic_curated")
            if "NOT A REAL PERSON" not in p.get("warning", "").upper() and "SYNTHETIC" not in p.get("warning", "").upper():
                C.append(f"{pid}: synthetic warning label missing")
            if not p.get("scenario") or not p.get("test_tags"):
                C.append(f"{pid}: synthetic profiles need scenario and test_tags")
        else:
            if p.get("synthetic_demo_allowed"):
                C.append(f"{pid}: synthetic_demo_allowed is only valid for synthetic profiles")
        if cls == "uploaded_resume_fixture" and p.get("review_status") not in ("extracted_unverified", "rejected"):
            C.append(f"{pid}: uploaded fixtures cannot be marked confirmed without the subject's participation")
        for k in ("contributor_user_id", "source_consent_event_id"):
            if p.get(k):
                C.append(f"{pid}: {k} must not appear in seed files; it is set only by the contribution flow")

        # --- dates
        D = out["date_integrity"]
        as_lo, as_hi = _bounds_or_error(D, f"{pid} resume_as_of", p.get("resume_as_of"), p.get("resume_as_of_precision"), False)
        R = out["role_taxonomy_and_seniority"]
        Rel = out["profile_relationships"]
        S = out["complete_sequences"]
        employers = {}
        for emp in p.get("employers", []):
            eid = emp.get("employer_id")
            if eid in employers:
                Rel.append(f"{pid}: duplicate employer {eid}")
            employers[eid] = emp
            if not (eid or "").startswith(pid + "-ORG-"):
                Rel.append(f"{pid}: employer id {eid} is not scoped to the profile")
            for field, allowed in (("industry_sector", INDUSTRY_SECTORS), ("company_model", COMPANY_MODELS),
                                   ("business_model", BUSINESS_MODELS), ("company_size_band", SIZE_BANDS),
                                   ("attribute_provenance", ATTR_PROVENANCE)):
                if emp.get(field) not in allowed:
                    Rel.append(f"{eid}: {field}={emp.get(field)!r} not in controlled list")
        emp_ids, primaries, ongoing_primary = {}, [], 0
        for e in p.get("employments", []):
            eid = e.get("employment_id", "?")
            if eid in seen_global:
                Rel.append(f"{eid}: duplicate employment_id")
            seen_global.add(eid)
            emp_ids[eid] = e
            if not eid.startswith(pid + "-EMP-"):
                Rel.append(f"{eid}: employment id is not scoped to {pid}")
            if e.get("employer_id") is not None and e["employer_id"] not in employers:
                Rel.append(f"{eid}: employer {e['employer_id']} is not defined for {pid}")
            if not str(e.get("title_raw_redacted", "")).strip():
                E.append(f"{eid}: empty title")
            if e.get("employment_type") not in EMPLOYMENT_TYPES:
                E.append(f"{eid}: unknown employment_type {e.get('employment_type')}")
            # dates
            s_lo, s_hi = _bounds_or_error(D, f"{eid} start", e.get("start"), e.get("start_precision"), False)
            e_lo, e_hi = _bounds_or_error(D, f"{eid} end", e.get("end"), e.get("end_precision"), True)
            if (e.get("end_precision") == "ongoing") != bool(e.get("is_current_as_stated")):
                D.append(f"{eid}: end_precision 'ongoing' must match is_current_as_stated")
            if e_hi is not None and s_lo is not None and e_hi < s_lo:
                D.append(f"{eid}: end is before start")
            if as_hi is not None and s_lo is not None and s_lo > as_hi:
                D.append(f"{eid}: starts after resume_as_of")
            if as_hi is not None and e_lo is not None and e_lo > as_hi:
                D.append(f"{eid}: ends after resume_as_of")
            # roles
            rid = e.get("role_id")
            if rid not in roles:
                R.append(f"{eid}: unknown role_id {rid}")
            if e.get("seniority_code") is not None and e["seniority_code"] not in sen:
                R.append(f"{eid}: unknown seniority_code {e['seniority_code']}")
            if "role_family" in e:
                R.append(f"{eid}: role_family must not be stored; it is derived from role_id")
            ms = e.get("mapping_status")
            if ms not in MAPPING_STATUSES:
                R.append(f"{eid}: unknown mapping_status {ms}")
            conf = e.get("mapping_confidence")
            if not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
                R.append(f"{eid}: mapping_confidence must be a number in [0, 1]")
                conf = 0
            if ms == "unresolved" and rid != "ROLE-UNKNOWN":
                R.append(f"{eid}: unresolved mappings must use ROLE-UNKNOWN")
            if rid == "ROLE-UNKNOWN" and ms not in ("unresolved", "needs_review"):
                R.append(f"{eid}: ROLE-UNKNOWN must be unresolved or needs_review")
            if ms == "auto_high_confidence" and conf < min_conf:
                R.append(f"{eid}: auto_high_confidence requires mapping_confidence >= {min_conf}")
            title_key = " ".join(str(e.get("title_raw_redacted", "")).strip().lower().split())
            matches = aliases.get(title_key, [])
            if ms == "auto_high_confidence" and matches and (len(matches) > 1 or any(tm.as_bool(m["context_required"]) for m in matches)):
                R.append(f"{eid}: title '{e['title_raw_redacted']}' is a context-required alias and cannot be auto-confirmed")
            if e.get("counts_as_full_time_experience") is not None and not e.get("classification_note"):
                R.append(f"{eid}: counts_as_full_time_experience override needs a classification_note")
            if not e.get("primary_career_sequence") and not e.get("classification_note") and e.get("employment_type") not in ("founder", "freelance"):
                R.append(f"{eid}: non-primary records need a classification_note explaining the parallel activity")
            if (e.get("employment_type") == "founder" and e.get("primary_career_sequence")
                    and e.get("counts_as_full_time_experience") is None and cls == "synthetic_test_fixture"):
                R.append(f"{eid}: primary founder role needs an explicit counts_as_full_time_experience decision with a classification_note")
            if e.get("primary_career_sequence"):
                primaries.append((e, s_lo, s_hi))
                if e.get("end_precision") == "ongoing":
                    ongoing_primary += 1
        # sequences
        idx = sorted(e.get("sequence_index") for e in p.get("employments", []))
        if not p.get("employments"):
            S.append(f"{pid}: no employment records")
        elif idx != list(range(1, len(idx) + 1)):
            S.append(f"{pid}: sequence_index must be 1..n without gaps or duplicates, got {idx}")
        ordered = sorted(p.get("employments", []), key=lambda e: e.get("sequence_index", 0))
        dated = []
        for e in ordered:
            try:
                lo, hi = tm.bounds(e.get("start"), e.get("start_precision"))
            except ValueError:
                continue  # reported by date_integrity
            if lo is not None:
                dated.append((e, lo, hi))
        for (a, a_lo, a_hi), (b, b_lo, b_hi) in zip(dated, dated[1:]):
            if b_hi < a_lo:
                S.append(f"{pid}: {b['employment_id']} is sequenced after {a['employment_id']} but starts earlier")
        if ongoing_primary > 1:
            S.append(f"{pid}: {ongoing_primary} ongoing primary roles; mark concurrent roles as parallel activity")
        # education and breaks
        for d in p.get("education", []):
            did = d.get("education_id", "?")
            if not did.startswith(pid + "-EDU-"):
                Rel.append(f"{did}: education id is not scoped to {pid}")
            for field, allowed in (("qualification_level", QUAL_LEVELS), ("study_mode", STUDY_MODES), ("completion_status", COMPLETION)):
                if d.get(field) not in allowed:
                    E.append(f"{did}: {field}={d.get(field)!r} not in controlled list")
            lo, _ = _bounds_or_error(D, f"{did} start", d.get("start"), d.get("start_precision"), False)
            _, hi = _bounds_or_error(D, f"{did} end", d.get("end"), d.get("end_precision"), True)
            if lo is not None and hi is not None and hi < lo:
                D.append(f"{did}: end is before start")
        for b in p.get("career_breaks", []):
            bid = b.get("break_id", "?")
            if not bid.startswith(pid + "-BRK-"):
                Rel.append(f"{bid}: break id is not scoped to {pid}")
            if b.get("break_reason") not in BREAK_REASONS:
                E.append(f"{bid}: unknown break_reason")
            lo, _ = _bounds_or_error(D, f"{bid} start", b.get("start"), b.get("start_precision"), False)
            _, hi = _bounds_or_error(D, f"{bid} end", b.get("end"), b.get("end_precision"), True)
            if lo is not None and hi is not None and hi < lo:
                D.append(f"{bid}: end is before start")
        # evidence
        ev_ids = set()
        for x in p.get("evidence", []):
            xid = x.get("evidence_id", "?")
            if xid in ev_ids or xid in seen_global:
                Rel.append(f"{xid}: duplicate evidence_id")
            ev_ids.add(xid)
            seen_global.add(xid)
            if not xid.startswith(pid + "-"):
                Rel.append(f"{xid}: evidence id is not scoped to {pid}")
            emp_id = x.get("employment_id")
            if emp_id is not None and emp_id not in emp_ids:
                Rel.append(f"{xid}: employment {emp_id} does not belong to {pid}")
            if emp_id is not None and not xid.startswith(emp_id + "-"):
                Rel.append(f"{xid}: evidence id does not match its employment {emp_id}")
            text = str(x.get("text", ""))
            if len(text.strip()) < 10:
                Rel.append(f"{xid}: evidence text too short ({text!r}); possible character-split defect")
            if x.get("evidence_type") not in EVIDENCE_TYPES:
                E.append(f"{xid}: unknown evidence_type {x.get('evidence_type')}")
            if x.get("responsibility_area") is not None and x["responsibility_area"] not in areas:
                E.append(f"{xid}: unknown responsibility_area {x['responsibility_area']}")
            if x.get("evidence_status") not in EVIDENCE_STATUSES:
                E.append(f"{xid}: unknown evidence_status")
            if (x.get("provenance") == "synthetic_fixture") != (cls == "synthetic_test_fixture"):
                C.append(f"{xid}: provenance synthetic_fixture must be used exactly for synthetic profiles")
        for s in p.get("skills", []):
            if s.get("skill_id") not in skills:
                Rel.append(f"{pid}: unknown skill {s.get('skill_id')}")
            if s.get("employment_id") is not None and s["employment_id"] not in emp_ids:
                Rel.append(f"{pid}: skill {s.get('skill_id')} links to employment of another profile")
            if s.get("evidence_source") not in SKILL_SOURCES:
                E.append(f"{pid}: unknown skill evidence_source {s.get('evidence_source')}")
            if (s.get("evidence_source") == "synthetic_fixture") != (cls == "synthetic_test_fixture"):
                C.append(f"{pid}: skill evidence_source synthetic_fixture must be used exactly for synthetic profiles")
        # model sanity: the sequence logic must run cleanly on every profile
        try:
            tm.transitions(p, ctx)
            tm.career_paths(p, ctx)
        except Exception as exc:  # noqa: BLE001
            S.append(f"{pid}: sequence model failed: {exc}")
    return out


# --------------------------------------------------------------------------- package-level checks
def check_separation(root):
    errs = []
    shared = [root / build_seeds.SYNTHETIC_SEED, root / build_seeds.LOOKUP_MIGRATION]
    shared += sorted((root / "data").rglob("*.csv")) + sorted((root / "data").rglob("*.jsonl"))
    shared += sorted((root / "supabase").rglob("*.sql"))
    for f in shared:
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8")
        if re.search(r"\bUPL-\d{3}", text):
            errs.append(f"{f.relative_to(root)}: contains an uploaded-resume fixture id (UPL-...)")
        if "SRC-UPLOADS" in text:
            errs.append(f"{f.relative_to(root)}: references the uploaded-resume source")
        if f.suffix in (".csv", ".jsonl") and "uploaded_resume_fixture" in text:
            errs.append(f"{f.relative_to(root)}: contains dataset_class uploaded_resume_fixture")
    seed = root / build_seeds.SYNTHETIC_SEED
    if seed.exists():
        text = seed.read_text(encoding="utf-8")
        for cls in ("consented_real_reference", "public_aggregate_reference"):
            if f"'{cls}'" in text.split("do $$", 1)[-1].split("end\n$$;", 1)[-1]:
                errs.append(f"synthetic seed inserts {cls} rows")
        if "raise exception 'Refusing to load synthetic fixtures" not in text:
            errs.append("synthetic seed lacks the production-database guard")
    lookup = root / build_seeds.LOOKUP_MIGRATION
    if lookup.exists() and "synthetic_test_fixture" in lookup.read_text(encoding="utf-8"):
        errs.append("lookup migration (runs in production) contains synthetic data")
    gi = root / ".gitignore"
    if not gi.exists() or "local_only/" not in gi.read_text(encoding="utf-8"):
        errs.append(".gitignore must exclude local_only/")
    for f in (root / "supabase").rglob("*"):
        if f.is_file() and "local_only" in f.read_text(encoding="utf-8", errors="ignore") and f.suffix == ".sql":
            errs.append(f"{f.relative_to(root)}: SQL must not reference local_only files")
    return errs, len(shared)


def check_generated(root):
    try:
        stale = build_seeds.stale_artefacts(root)
    except Exception as exc:  # noqa: BLE001 - malformed sources must be reported, not crash the run
        return [f"cannot regenerate artefacts from the sources ({type(exc).__name__}: {exc}); fix the sources first"], 0
    return [f"{s} is out of date; run python3 scripts/build_seeds.py" for s in stale], 12


def check_pdfs(root, profiles):
    errs = []
    pdf_dir = root / "data/synthetic/synthetic_resume_pdfs"
    expected = {f"{p['profile_id']}_synthetic_resume.pdf" for p in profiles}
    present = {p.name for p in pdf_dir.glob("*.pdf")} if pdf_dir.exists() else set()
    for m in sorted(expected - present):
        errs.append(f"missing PDF {m}")
    for x in sorted(present - expected):
        errs.append(f"PDF without a synthetic profile: {x}")
    zpath = root / "data/synthetic/synthetic_resume_pdfs.zip"
    if not zpath.exists():
        errs.append("missing synthetic_resume_pdfs.zip")
    else:
        with zipfile.ZipFile(zpath) as z:
            names = {Path(n).name for n in z.namelist()}
            if names != expected:
                errs.append("zip contents differ from the synthetic profile set")
            for n in sorted(names & present):
                if z.read(n) != (pdf_dir / n).read_bytes():
                    errs.append(f"zip copy of {n} differs from the PDF on disk")
    for n in sorted(present):
        head = (pdf_dir / n).read_bytes()
        if not head.startswith(b"%PDF"):
            errs.append(f"{n} is not a PDF")
    return errs, len(expected)


def run_all(root=ROOT, include_local=True):
    rep = Report()
    lk = build_seeds.load_lookups(root)
    ctx = tm.Context.load(root)
    for name, fn in (("taxonomy_and_aliases", check_taxonomy), ("skills_vocabulary", check_skills),
                     ("analytics_settings_and_sources", check_config)):
        errs, n = fn(lk)
        rep.add(name, errs, n)
    profiles = tm.read_jsonl(root / build_seeds.SYNTHETIC_JSONL)
    res = check_profiles(profiles, lk, ctx, "synthetic_test_fixture", "shared synthetic seed")
    for k, v in res.items():
        rep.add(f"synthetic:{k}", v, len(profiles))
    errs, n = check_separation(root)
    rep.add("dataset_separation", errs, n)
    errs, n = check_generated(root)
    rep.add("generated_artefacts_in_sync", errs, n)
    errs, n = check_pdfs(root, profiles)
    rep.add("synthetic_resume_pdfs", errs, n)
    try:
        errs = package_review.scan(root) + package_review.verify_without_restricted(root)
    except Exception as exc:  # noqa: BLE001
        errs = [f"cannot check the shareable package ({type(exc).__name__}: {exc})"]
    rep.add("shareable_package_without_local_only", errs, 1)
    local = root / "local_only/uploaded_resume_fixtures/uploaded_resume_fixtures.jsonl"
    if include_local and local.exists():
        upl = tm.read_jsonl(local)
        lk_local = dict(lk)
        lk_local["sources"] = lk["sources"] + tm.read_csv(local.parent / "data_source.csv")
        res = check_profiles(upl, lk_local, ctx, "uploaded_resume_fixture", "restricted local fixtures")
        for k, v in res.items():
            rep.add(f"restricted_local:{k}", v, len(upl))
    return rep


def main(argv):
    rep = run_all(include_local="--shared" not in argv)
    rep.print()
    print("Note: validation checks structure, references, dates, separation and consent rules - not factual truth or consent validity.")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
