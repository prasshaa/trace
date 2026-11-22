"""Shared helpers for the Trace Data Foundation test-suite (standard library only)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import trace_model as tm  # noqa: E402

DEFAULT_SETTINGS = {
    "min_profiles_limited": 5,
    "min_profiles_moderate": 15,
    "min_profiles_strong": 40,
    "min_dated_profiles_for_timeline": 20,
    "min_mapping_confidence": 0.8,
}


def context(**overrides):
    """The real taxonomy and seniority ladder with optionally overridden settings."""
    ctx = tm.Context.load(ROOT)
    ctx.settings = {**DEFAULT_SETTINGS, **overrides}
    return ctx


def emp(pid, seq, role, start, end, *, sp=None, ep=None, org="A", etype="full_time", primary=True,
        status="confirmed", conf=1.0, counts=None, note=None, title=None):
    """Build one employment record. Precision is inferred from the string unless given."""
    def prec(v):
        if v is None:
            return "unknown"
        if v == "ongoing":
            return "ongoing"
        return "year" if len(v) == 4 else "month"
    start_p = sp or prec(start)
    end_p = ep or prec(end)
    return {
        "employment_id": f"{pid}-EMP-{seq:02d}", "sequence_index": seq,
        "employer_id": f"{pid}-ORG-{org}" if org else None,
        "title_raw_redacted": title or role, "role_id": role, "seniority_code": None,
        "mapping_status": status, "mapping_confidence": conf, "mapping_note": None,
        "employment_type": etype, "primary_career_sequence": primary,
        "counts_as_full_time_experience": counts, "classification_note": note,
        "start": start, "start_precision": start_p,
        "end": None if end in (None, "ongoing") else end, "end_precision": end_p,
        "is_current_as_stated": end == "ongoing", "data_quality_flags": [],
    }


def profile(pid, employments, dataset_class="synthetic_test_fixture", **extra):
    p = {"profile_id": pid, "dataset_class": dataset_class, "employments": employments,
         "review_status": "synthetic_curated" if dataset_class == "synthetic_test_fixture" else "participant_confirmed",
         "synthetic_demo_allowed": dataset_class == "synthetic_test_fixture"}
    p.update(extra)
    return p
