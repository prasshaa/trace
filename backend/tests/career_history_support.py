"""Test helpers for the candidate sequencing service.

Reads the Trace Data Foundation's taxonomy CSVs, synthetic fixtures and trace_model.py as
read-only inputs. Synthetic fixtures are test inputs only: their results are never career
evidence, and nothing here produces cohort statistics.
"""
import csv
import json
import types
from pathlib import Path
from typing import Optional

import pytest

from app.services.career_history import CandidateEmployment, RoleInfo, SequencingContext

FOUNDATION = Path(__file__).resolve().parents[2] / "data-foundation"
TRACE_MODEL = FOUNDATION / "scripts" / "trace_model.py"
SYNTHETIC_PROFILES = FOUNDATION / "data" / "synthetic" / "synthetic_profiles.jsonl"

requires_foundation = pytest.mark.skipif(
    not TRACE_MODEL.exists(), reason="data-foundation/ not present next to backend/")

DEFAULT_SETTINGS = {
    "min_profiles_limited": 5,
    "min_profiles_moderate": 15,
    "min_profiles_strong": 40,
    "min_dated_profiles_for_timeline": 20,
    "min_mapping_confidence": 0.8,
}


def _read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def foundation_context(min_mapping_confidence: Optional[float] = 0.8) -> SequencingContext:
    roles = {
        r["role_id"]: RoleInfo(
            role_family=r["role_family"],
            default_seniority_code=r["default_seniority_code"],
            is_resolved=r["is_resolved"] == "true",
            is_pm_target=r["is_pm_target"] == "true",
        )
        for r in _read_csv(FOUNDATION / "data/taxonomy/role_taxonomy.csv")
    }
    ranks = {r["seniority_code"]: (int(r["rank"]) if r["rank"] else None)
             for r in _read_csv(FOUNDATION / "data/taxonomy/seniority_levels.csv")}
    return SequencingContext(roles=roles, seniority_ranks=ranks, min_mapping_confidence=min_mapping_confidence)


def load_trace_model():
    """Load trace_model.py as an oracle without importing it (no __pycache__ is written
    into data-foundation/)."""
    module = types.ModuleType("trace_model_oracle")
    module.__file__ = str(TRACE_MODEL)
    exec(compile(TRACE_MODEL.read_text(encoding="utf-8"), str(TRACE_MODEL), "exec"), module.__dict__)
    return module


def oracle_context(tm, **overrides):
    ctx = tm.Context.load(FOUNDATION)
    ctx.settings = {**DEFAULT_SETTINGS, **overrides}
    return ctx


def synthetic_profiles():
    with open(SYNTHETIC_PROFILES, encoding="utf-8") as f:
        profiles = [json.loads(line) for line in f if line.strip()]
    for p in profiles:
        assert p["dataset_class"] == "synthetic_test_fixture", p["profile_id"]
    return profiles


# --------------------------------------------------------------------------- record builders
def _split(value, precision):
    if precision == "month":
        y, m = value.split("-")
        return int(y), int(m)
    if precision == "year":
        return int(value), None
    return None, None


def from_foundation_employment(e: dict) -> CandidateEmployment:
    """Adapt a foundation employment dict ("YYYY" / "YYYY-MM" strings) to the candidate shape."""
    sy, sm = _split(e.get("start"), e["start_precision"])
    ey, em = _split(e.get("end"), e["end_precision"])
    return CandidateEmployment(
        employment_id=e["employment_id"],
        sequence_index=e["sequence_index"],
        employer_id=e.get("employer_id"),
        title_raw=e.get("title_raw_redacted") or e["role_id"],
        role_id=e["role_id"],
        seniority_code=e.get("seniority_code"),
        mapping_status=e["mapping_status"],
        mapping_confidence=float(e["mapping_confidence"]),
        mapping_note=e.get("mapping_note"),
        employment_type=e["employment_type"],
        primary_career_sequence=e["primary_career_sequence"],
        counts_as_full_time_experience=e.get("counts_as_full_time_experience"),
        classification_note=e.get("classification_note"),
        start_year=sy, start_month=sm, start_precision=e["start_precision"],
        end_year=ey, end_month=em, end_precision=e["end_precision"],
        is_current_as_stated=e.get("is_current_as_stated", e["end_precision"] == "ongoing"),
    )


def emp_dict(pid, seq, role, start, end, *, sp=None, ep=None, org="A", etype="full_time", primary=True,
             status="confirmed", conf=1.0, counts=None, note=None, title=None):
    """Same signature and semantics as data-foundation/tests/_support.emp."""
    def prec(v):
        if v is None:
            return "unknown"
        if v == "ongoing":
            return "ongoing"
        return "year" if len(v) == 4 else "month"
    return {
        "employment_id": f"{pid}-EMP-{seq:02d}", "sequence_index": seq,
        "employer_id": f"{pid}-ORG-{org}" if org else None,
        "title_raw_redacted": title or role, "role_id": role, "seniority_code": None,
        "mapping_status": status, "mapping_confidence": conf, "mapping_note": None,
        "employment_type": etype, "primary_career_sequence": primary,
        "counts_as_full_time_experience": counts, "classification_note": note,
        "start": start, "start_precision": sp or prec(start),
        "end": None if end in (None, "ongoing") else end, "end_precision": ep or prec(end),
        "is_current_as_stated": end == "ongoing", "data_quality_flags": [],
    }


def emp(*args, **kwargs) -> CandidateEmployment:
    return from_foundation_employment(emp_dict(*args, **kwargs))
