"""Trace Data Foundation v2 - reference implementation of the career-sequence model
(and, from v2.1.0, of the episode-aware pathway objects).

This module is the executable specification of the rules implemented in SQL by
supabase/migrations/*_trace_analytics_views.sql. The validator and the unit tests
use it; keep both implementations in step (docs/DATA_DICTIONARY.md, section 6).

Standard library only (Python 3.9+).

Month arithmetic
----------------
A month is represented by an integer index: year * 12 + (month - 1).
Every partial date is stored as a closed interval [lo, hi] of month indices:
  month precision   lo == hi
  year precision    lo = January, hi = December of that year
  unknown           lo = hi = None
  ongoing           (end only) lo = hi = None; the role had not ended at resume_as_of
Employment months are inclusive: a role from 2021-06 to 2023-03 occupies both
June 2021 and March 2023.
"""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DATE_PRECISIONS = ("month", "year", "unknown", "ongoing")
# Founder roles are NOT included: they count only with an explicit, documented
# counts_as_full_time_experience = true (classification_note required).
FULL_TIME_TYPES = ("full_time", "contract")
CONFIRMABLE_MAPPING_STATUSES = ("confirmed", "auto_high_confidence")
# boundary_uncertain is NOT confirmable: e.g. a year-only end and a year-only start in the same
# year, where chronological order cannot be established. Both records are kept; only the
# transition between them stays unconfirmed. An earlier end year than start year is
# "sequential" (confirmable) but never month-precise (gap_is_precise / is_precise = False).
CONFIRMABLE_RELATIONS = ("sequential", "same_month_boundary")
PRECISE_GAP_RELATIONS = ("sequential", "same_month_boundary")
DATA_MODES = ("empirical", "synthetic_test", "synthetic_demo")


# --------------------------------------------------------------------------- dates
def parse_partial_date(value, precision):
    """Return (year, month) for a partial-date string in the given precision."""
    if precision in ("unknown", "ongoing"):
        if value not in (None, ""):
            raise ValueError(f"precision {precision} must not carry a value ({value!r})")
        return None, None
    if value in (None, ""):
        raise ValueError(f"precision {precision} requires a value")
    if precision == "year":
        if len(value) != 4 or not value.isdigit():
            raise ValueError(f"year precision expects YYYY, got {value!r}")
        return int(value), None
    if precision == "month":
        parts = value.split("-")
        if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2 or not (parts[0] + parts[1]).isdigit():
            raise ValueError(f"month precision expects YYYY-MM, got {value!r}")
        year, month = int(parts[0]), int(parts[1])
        if not 1 <= month <= 12:
            raise ValueError(f"month out of range in {value!r}")
        return year, month
    raise ValueError(f"unknown precision {precision!r}")


def month_lo(year, month, precision):
    if precision == "month":
        return year * 12 + month - 1
    if precision == "year":
        return year * 12
    return None


def month_hi(year, month, precision):
    if precision == "month":
        return year * 12 + month - 1
    if precision == "year":
        return year * 12 + 11
    return None


def bounds(value, precision):
    y, m = parse_partial_date(value, precision)
    return month_lo(y, m, precision), month_hi(y, m, precision)


def month_label(index):
    if index is None:
        return None
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def percentile_cont(values, fraction):
    """Linear-interpolation percentile, identical to PostgreSQL percentile_cont."""
    vals = sorted(values)
    if not vals:
        return None
    pos = fraction * (len(vals) - 1)
    lower = int(pos)
    upper = min(lower + 1, len(vals) - 1)
    return vals[lower] + (vals[upper] - vals[lower]) * (pos - lower)


# --------------------------------------------------------------------------- loading
def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{n}: invalid JSON: {exc}") from exc
    return rows


def as_bool(v):
    if isinstance(v, bool):
        return v
    if v in ("true", "True", "TRUE", "t", "1"):
        return True
    if v in ("false", "False", "FALSE", "f", "0", ""):
        return False
    raise ValueError(f"not a boolean: {v!r}")


class Context:
    """Lookup data needed to classify records (taxonomy, seniority, settings)."""

    def __init__(self, roles, seniority, settings):
        self.roles = roles            # role_id -> dict(role_family, default_seniority_code, is_resolved, is_pm_target)
        self.seniority = seniority    # seniority_code -> rank or None
        self.settings = settings      # setting_key -> float

    def setting(self, key, default):
        return self.settings.get(key, default)

    @classmethod
    def load(cls, root=ROOT):
        roles = {}
        for r in read_csv(root / "data/taxonomy/role_taxonomy.csv"):
            roles[r["role_id"]] = {
                "role_family": r["role_family"],
                "default_seniority_code": r["default_seniority_code"],
                "is_resolved": as_bool(r["is_resolved"]),
                "is_pm_target": as_bool(r["is_pm_target"]),
            }
        seniority = {r["seniority_code"]: (int(r["rank"]) if r["rank"] else None)
                     for r in read_csv(root / "data/taxonomy/seniority_levels.csv")}
        settings = {r["setting_key"]: float(r["value_numeric"])
                    for r in read_csv(root / "data/config/analytics_settings.csv")}
        return cls(roles, seniority, settings)


# --------------------------------------------------------------------------- classification
def classify_employment(emp, ctx):
    """Derived fields and transition-exclusion reasons for one employment record.

    Mirrors trace_ref.v_employment_classified."""
    role = ctx.roles[emp["role_id"]]
    start_lo, start_hi = bounds(emp.get("start"), emp["start_precision"])
    end_lo, end_hi = bounds(emp.get("end"), emp["end_precision"])
    seniority_code = emp.get("seniority_code") or role["default_seniority_code"]
    override = emp.get("counts_as_full_time_experience")
    counts_full_time = override if override is not None else emp["employment_type"] in FULL_TIME_TYPES
    reasons = []
    if not emp["primary_career_sequence"]:
        reasons.append("parallel_activity")
    if not counts_full_time:
        reasons.append("not_full_time_experience")
    if not role["is_resolved"]:
        reasons.append("unresolved_role")
    if emp["mapping_status"] not in CONFIRMABLE_MAPPING_STATUSES:
        reasons.append("mapping_not_confirmed")
    if float(emp["mapping_confidence"]) < ctx.setting("min_mapping_confidence", 1.0):
        reasons.append("low_mapping_confidence")
    if start_lo is None:
        reasons.append("unknown_start_date")
    return {
        "employment_id": emp["employment_id"],
        "sequence_index": emp["sequence_index"],
        "employer_id": emp.get("employer_id"),
        "role_id": emp["role_id"],
        "role_family": role["role_family"],
        "seniority_code": seniority_code,
        "seniority_rank": ctx.seniority.get(seniority_code),
        "employment_type": emp["employment_type"],
        "primary_career_sequence": emp["primary_career_sequence"],
        "start_precision": emp["start_precision"],
        "end_precision": emp["end_precision"],
        "start_lo": start_lo, "start_hi": start_hi, "end_lo": end_lo, "end_hi": end_hi,
        "counts_as_full_time_experience": counts_full_time,
        "exclusion_reasons": reasons,
        "is_transition_eligible": not reasons,
    }


def career_sequence(profile, ctx):
    """Every primary-sequence record, in sequence_index order. Nothing is dropped here:
    internships, unresolved roles, low-confidence mappings and undated roles stay in place
    so that they break, rather than bridge, the chain. Mirrors trace_ref.v_career_sequence."""
    recs = [classify_employment(e, ctx) for e in profile["employments"] if e["primary_career_sequence"]]
    return sorted(recs, key=lambda r: r["sequence_index"])


def timing_relation(prev, nxt, ctx):
    """Classify the calendar relationship between consecutive primary records.

    Returns (relation, gap_min, gap_max). Gap = whole calendar months strictly between the
    last month of `prev` and the first month of `nxt` (inclusive-month semantics), so
    2021-06 -> 2021-07 is a gap of 0 and a same-month boundary is also 0.

    Any overlap beyond the shared boundary month is 'overlapping' and never confirmable:
    it stays unconfirmed until a reviewer explicitly resolves it (corrects the dates or marks
    one role as parallel activity with a classification_note). There is no automatic
    notice-period tolerance."""
    if prev["end_precision"] == "ongoing":
        return "previous_role_ongoing", None, None
    if prev["end_lo"] is None or nxt["start_lo"] is None:
        return "timing_unknown", None, None
    if prev["start_lo"] is not None and nxt["start_hi"] < prev["start_lo"]:
        return "sequence_order_conflict", None, None
    if nxt["start_lo"] > prev["end_hi"]:
        return "sequential", nxt["start_lo"] - prev["end_hi"] - 1, nxt["start_hi"] - prev["end_lo"] - 1
    precise = prev["end_lo"] == prev["end_hi"] and nxt["start_lo"] == nxt["start_hi"]
    if precise:
        overlap = prev["end_lo"] - nxt["start_lo"]   # months beyond the shared boundary month
        if overlap == 0:
            return "same_month_boundary", 0, 0
        return "overlapping", None, None
    if prev["end_lo"] > nxt["start_hi"]:
        return "overlapping", None, None   # overlaps whatever the true months are
    return "boundary_uncertain", 0, max(0, nxt["start_hi"] - prev["end_lo"] - 1)


def transitions(profile, ctx):
    """Consecutive pairs in each individual's primary sequence. Mirrors trace_ref.v_role_transitions."""
    seq = career_sequence(profile, ctx)
    out = []
    for prev, nxt in zip(seq, seq[1:]):
        relation, gmin, gmax = timing_relation(prev, nxt, ctx)
        reasons = [f"from:{r}" for r in prev["exclusion_reasons"]] + [f"to:{r}" for r in nxt["exclusion_reasons"]]
        if relation not in CONFIRMABLE_RELATIONS:
            reasons.append(f"timing:{relation}")
        if prev["employer_id"] and nxt["employer_id"]:
            move = "internal" if prev["employer_id"] == nxt["employer_id"] else "external"
        else:
            move = "unknown"
        rank_change = None
        if prev["seniority_rank"] is not None and nxt["seniority_rank"] is not None:
            rank_change = nxt["seniority_rank"] - prev["seniority_rank"]
        gap_precise = relation in PRECISE_GAP_RELATIONS and prev["end_lo"] == prev["end_hi"] and nxt["start_lo"] == nxt["start_hi"]
        out.append({
            "profile_id": profile["profile_id"],
            "from_employment_id": prev["employment_id"], "to_employment_id": nxt["employment_id"],
            "from_role_id": prev["role_id"], "to_role_id": nxt["role_id"],
            "from_role_family": prev["role_family"], "to_role_family": nxt["role_family"],
            "move_type": move, "seniority_rank_change": rank_change,
            "timing_relation": relation, "gap_months_min": gmin, "gap_months_max": gmax, "gap_is_precise": gap_precise,
            "transition_status": "confirmed" if not reasons else "unconfirmed",
            "unconfirmed_reasons": reasons,
        })
    return out


def career_paths(profile, ctx):
    """Split the sequence into segments of consecutive confirmed transitions and collapse
    consecutive records with the same role into stints. Mirrors trace_ref.v_career_paths."""
    seq = career_sequence(profile, ctx)
    incoming = {t["to_employment_id"]: t["transition_status"] for t in transitions(profile, ctx)}
    segments, segment_no = {}, 0
    for rec in seq:
        if incoming.get(rec["employment_id"]) != "confirmed":
            segment_no += 1
        if rec["is_transition_eligible"]:
            segments.setdefault(segment_no, []).append(rec)
    paths = []
    for no in sorted(segments):
        stints = []
        for rec in segments[no]:
            if stints and stints[-1]["role_id"] == rec["role_id"]:
                stints[-1]["employment_ids"].append(rec["employment_id"])
                continue
            stints.append({"role_id": rec["role_id"], "start_lo": rec["start_lo"], "start_hi": rec["start_hi"],
                           "employment_ids": [rec["employment_id"]]})
        paths.append({"profile_id": profile["profile_id"], "segment_no": no, "stints": stints,
                      "role_path": [s["role_id"] for s in stints]})
    return paths


def collapse_route(route):
    out = []
    for r in route:
        if not out or out[-1] != r:
            out.append(r)
    return out


def route_observation(profile, route, ctx):
    """First occurrence of `route` (a list of role_ids) as a contiguous run of stints inside
    one confirmed segment of this individual's own history, or None.

    Metric route_start_to_target_start_months = start month of the target-role stint minus
    start month of the first route stint. Mirrors trace_ref.route_observations()."""
    route = collapse_route(route)
    if len(route) < 2:
        raise ValueError("a route needs at least two distinct consecutive roles")
    n = len(route)
    for path in career_paths(profile, ctx):
        rp = path["role_path"]
        for i in range(len(rp) - n + 1):
            if rp[i:i + n] == route:
                first, last = path["stints"][i], path["stints"][i + n - 1]
                mmin = max(0, last["start_lo"] - first["start_hi"])
                mmax = last["start_hi"] - first["start_lo"]
                return {
                    "profile_id": profile["profile_id"], "segment_no": path["segment_no"], "start_position": i + 1,
                    "route_months_min": mmin, "route_months_max": mmax,
                    "is_precise": first["start_lo"] == first["start_hi"] and last["start_lo"] == last["start_hi"],
                }
    return None


def profile_in_mode(profile, data_mode, consent_active=None):
    """Mirrors trace_ref.v_mode_profiles. `consent_active(profile)` must return True only when
    the contributor's latest deidentified_shared_dataset consent is granted under an approved
    consent document and the profile has been confirmed (see trace_ref.v_eligible_profiles)."""
    cls = profile["dataset_class"]
    if data_mode == "empirical":
        return (cls == "consented_real_reference"
                and profile.get("review_status") in ("participant_confirmed", "reviewer_confirmed")
                and consent_active is not None and bool(consent_active(profile)))
    if data_mode == "synthetic_test":
        return cls == "synthetic_test_fixture"
    if data_mode == "synthetic_demo":
        return cls == "synthetic_test_fixture" and bool(profile.get("synthetic_demo_allowed"))
    raise ValueError(f"unknown data_mode {data_mode!r}")


def evidence_class_for(data_mode, supporting, ctx):
    if data_mode != "empirical":
        return "synthetic_test_only"
    if supporting >= ctx.setting("min_profiles_strong", float("inf")):
        return "strong"
    if supporting >= ctx.setting("min_profiles_moderate", float("inf")):
        return "moderate"
    if supporting >= ctx.setting("min_profiles_limited", float("inf")):
        return "limited"
    return "insufficient"


def route_summary(profiles, route, data_mode, ctx, consent_active=None):
    """Cohort summary for one route. Mirrors trace_ref.route_timeline_summary()."""
    if data_mode not in DATA_MODES:
        raise ValueError(data_mode)
    obs = []
    for p in profiles:
        if profile_in_mode(p, data_mode, consent_active):
            o = route_observation(p, route, ctx)
            if o:
                obs.append(o)
    precise = [o["route_months_min"] for o in obs if o["is_precise"]]
    supporting, dated = len(obs), len(precise)
    min_dated = ctx.setting("min_dated_profiles_for_timeline", float("inf"))
    show_percentiles = dated >= min_dated
    return {
        "data_mode": data_mode,
        "route": collapse_route(route),
        "supporting_profile_count": supporting,
        "dated_profile_count": dated,
        "count_display_allowed": supporting >= ctx.setting("min_profiles_limited", float("inf")),
        "evidence_class": evidence_class_for(data_mode, supporting, ctx),
        "p25_months": percentile_cont(precise, 0.25) if show_percentiles else None,
        "median_months": percentile_cont(precise, 0.5) if show_percentiles else None,
        "p75_months": percentile_cont(precise, 0.75) if show_percentiles else None,
        "coarse_min_months": min((o["route_months_min"] for o in obs), default=None),
        "coarse_max_months": max((o["route_months_max"] for o in obs), default=None),
        "timeline_display_allowed": data_mode == "empirical" and show_percentiles,
        "profile_ids": [o["profile_id"] for o in obs],
    }


def tenure_months(rec, resume_as_of_bounds=(None, None)):
    """Inclusive tenure in months as (min, max, censored). Ongoing roles are censored at resume_as_of."""
    end_lo, end_hi, censored = rec["end_lo"], rec["end_hi"], False
    if rec["end_precision"] == "ongoing":
        end_lo, end_hi = resume_as_of_bounds
        censored = True
    if rec["start_lo"] is None or end_lo is None:
        return None, None, censored
    return max(1, end_lo - rec["start_hi"] + 1), end_hi - rec["start_lo"] + 1, censored


# --------------------------------------------------------------------------- episode-aware pathways (v2.1.0)
# Mirrors migration 20261111130000_trace_episode_pathways.sql. The unit of a pathway is an
# employment episode (role + employer). Built on transitions(), so every integrity rule above is
# inherited; only the stint-collapse rule differs from career_paths().

PATHWAY_MOVE_CHANGE_TYPES = ("role_change", "employer_change", "role_and_employer_change",
                             "role_change_employer_unknown")
# Inside a confirmed segment these do not start a new episode stint: same role, and the employer
# is unchanged or cannot be established.
CONTINUATION_CHANGE_TYPES = ("no_change", "employer_unknown")


def change_type(from_role_id, to_role_id, move_type):
    """Mirrors the change_type column of trace_ref.v_episode_transitions."""
    role_changed = from_role_id != to_role_id
    if move_type == "unknown":
        return "role_change_employer_unknown" if role_changed else "employer_unknown"
    if role_changed:
        return "role_and_employer_change" if move_type == "external" else "role_change"
    return "employer_change" if move_type == "external" else "no_change"


def episode_transitions(profile, ctx):
    """transitions() plus role_changed, employer_changed (None = unknown), change_type and
    is_pathway_move. Mirrors trace_ref.v_episode_transitions."""
    out = []
    for t in transitions(profile, ctx):
        ct = change_type(t["from_role_id"], t["to_role_id"], t["move_type"])
        out.append({
            **t,
            "role_changed": t["from_role_id"] != t["to_role_id"],
            "employer_changed": {"external": True, "internal": False}.get(t["move_type"]),
            "change_type": ct,
            "is_pathway_move": t["transition_status"] == "confirmed" and ct in PATHWAY_MOVE_CHANGE_TYPES,
        })
    return out


def career_episode_paths(profile, ctx):
    """Segments of confirmed transitions, split into employment-episode stints. Consecutive
    records collapse only on a continuation (same role, employer unchanged or unknown).
    Mirrors trace_ref.v_career_episode_stints / v_career_episode_paths."""
    seq = career_sequence(profile, ctx)
    incoming = {t["to_employment_id"]: t for t in episode_transitions(profile, ctx)}
    segments, segment_no = {}, 0
    for rec in seq:
        t = incoming.get(rec["employment_id"])
        confirmed = t is not None and t["transition_status"] == "confirmed"
        if not confirmed:
            segment_no += 1
        if rec["is_transition_eligible"]:
            segments.setdefault(segment_no, []).append((rec, t if confirmed else None))
    paths = []
    for no in sorted(segments):
        stints = []
        for rec, t in segments[no]:
            if stints and t is not None and t["change_type"] in CONTINUATION_CHANGE_TYPES:
                stints[-1]["employment_ids"].append(rec["employment_id"])
                continue
            stints.append({"role_id": rec["role_id"],
                           "entry_change_type": t["change_type"] if (stints and t is not None) else None,
                           "start_lo": rec["start_lo"], "start_hi": rec["start_hi"],
                           "employment_ids": [rec["employment_id"]]})
        paths.append({"profile_id": profile["profile_id"], "segment_no": no, "stints": stints,
                      "role_path": [s["role_id"] for s in stints],
                      "entry_change_types": [s["entry_change_type"] for s in stints]})
    return paths


def validate_episode_route(route, ctx):
    """Mirrors trace_ref.assert_valid_episode_route: at least two steps, no nulls, every step a
    known, resolved role id. The route is never collapsed (repeats mean an employer change)."""
    if route is None or isinstance(route, str) or len(route) < 2:
        raise ValueError(f"an episode route needs at least two steps (got {route!r})")
    if any(r is None for r in route):
        raise ValueError("an episode route must not contain null steps")
    bad = sorted({r for r in route if r not in ctx.roles or not ctx.roles[r]["is_resolved"]})
    if bad:
        raise ValueError(f"episode route contains unknown or unresolved role ids: {bad}")
    return list(route)


def episode_route_observation(profile, route, ctx):
    """First occurrence of `route` as consecutive episode stints inside one confirmed segment of
    this individual's history, or None. Mirrors trace_ref.episode_route_observations()."""
    route = validate_episode_route(route, ctx)
    n = len(route)
    for path in career_episode_paths(profile, ctx):
        rp = path["role_path"]
        for i in range(len(rp) - n + 1):
            if rp[i:i + n] == route:
                first, last = path["stints"][i], path["stints"][i + n - 1]
                return {
                    "profile_id": profile["profile_id"], "segment_no": path["segment_no"], "start_position": i + 1,
                    "step_change_types": path["entry_change_types"][i + 1:i + n],
                    "route_months_min": max(0, last["start_lo"] - first["start_hi"]),
                    "route_months_max": last["start_hi"] - first["start_lo"],
                    "is_precise": first["start_lo"] == first["start_hi"] and last["start_lo"] == last["start_hi"],
                }
    return None


def episode_route_summary(profiles, route, data_mode, ctx, consent_active=None):
    """Cohort summary for one episode route. Mirrors trace_ref.episode_route_timeline_summary()."""
    route = validate_episode_route(route, ctx)
    if data_mode not in DATA_MODES:
        raise ValueError(data_mode)
    obs = []
    for p in profiles:
        if profile_in_mode(p, data_mode, consent_active):
            o = episode_route_observation(p, route, ctx)
            if o:
                obs.append(o)
    precise = [o["route_months_min"] for o in obs if o["is_precise"]]
    supporting, dated = len(obs), len(precise)
    show_percentiles = dated >= ctx.setting("min_dated_profiles_for_timeline", float("inf"))
    return {
        "data_mode": data_mode,
        "route": route,
        "supporting_profile_count": supporting,
        "dated_profile_count": dated,
        "count_display_allowed": supporting >= ctx.setting("min_profiles_limited", float("inf")),
        "evidence_class": evidence_class_for(data_mode, supporting, ctx),
        "p25_months": percentile_cont(precise, 0.25) if show_percentiles else None,
        "median_months": percentile_cont(precise, 0.5) if show_percentiles else None,
        "p75_months": percentile_cont(precise, 0.75) if show_percentiles else None,
        "coarse_min_months": min((o["route_months_min"] for o in obs), default=None),
        "coarse_max_months": max((o["route_months_max"] for o in obs), default=None),
        "timeline_display_allowed": data_mode == "empirical" and show_percentiles,
        "profile_ids": [o["profile_id"] for o in obs],
    }
