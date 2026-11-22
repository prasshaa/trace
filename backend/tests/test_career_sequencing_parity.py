"""Parity: the service must give the same answers as data-foundation/scripts/trace_model.py
(v2.1.0: legacy transition rules plus the episode-aware transitions and segments).

trace_model.py is loaded read-only as an oracle. Inputs are the 41 synthetic fixtures and
seeded random histories; these prove rule parity only and are never career evidence.
"""
import random

import pytest
from pydantic import ValidationError

from app.services.career_history import PartialDateInput, sequence_candidate_history
from tests.career_history_support import (
    emp_dict,
    foundation_context,
    from_foundation_employment,
    load_trace_model,
    oracle_context,
    requires_foundation,
    synthetic_profiles,
)

pytestmark = requires_foundation


@pytest.fixture(scope="module")
def tm():
    return load_trace_model()


@pytest.fixture(scope="module")
def octx(tm):
    return oracle_context(tm)


@pytest.fixture(scope="module")
def ctx():
    return foundation_context()


def resume_as_of_input(profile):
    value, precision = profile.get("resume_as_of"), profile.get("resume_as_of_precision", "unknown")
    if precision == "month":
        y, m = value.split("-")
        return PartialDateInput(year=int(y), month=int(m), precision="month")
    if precision == "year":
        return PartialDateInput(year=int(value), precision="year")
    return None


def assert_parity(tm, octx, ctx, profile):
    as_of = resume_as_of_input(profile)
    ours = sequence_candidate_history(
        [from_foundation_employment(e) for e in profile["employments"]], ctx, resume_as_of=as_of)
    pid = profile["profile_id"]

    # Classification of every record (primary or not), as trace_model.classify_employment.
    as_of_bounds = tm.bounds(profile.get("resume_as_of"), profile.get("resume_as_of_precision", "unknown")) \
        if as_of else (None, None)
    by_id = {r.stated.employment_id: r for r in ours.records}
    assert len(by_id) == len(profile["employments"]), pid
    for e in profile["employments"]:
        exp = tm.classify_employment(e, octx)
        got = by_id[e["employment_id"]]
        assert (got.start.lo, got.start.hi, got.end.lo, got.end.hi) == \
            (exp["start_lo"], exp["start_hi"], exp["end_lo"], exp["end_hi"]), pid
        assert got.exclusion_reasons == exp["exclusion_reasons"], pid
        assert got.is_transition_eligible == exp["is_transition_eligible"], pid
        assert (got.seniority_code, got.seniority_rank) == (exp["seniority_code"], exp["seniority_rank"]), pid
        assert got.counts_as_full_time_experience == exp["counts_as_full_time_experience"], pid
        assert got.role_family == exp["role_family"], pid
        assert (got.tenure_months_min, got.tenure_months_max, got.tenure_is_censored) == \
            tm.tenure_months(exp, as_of_bounds), pid

    # Primary sequence order.
    assert [r.stated.employment_id for r in ours.records if r.in_primary_sequence] == \
        [r["employment_id"] for r in tm.career_sequence(profile, octx)], pid

    # Transitions, field by field: every legacy field of trace_model.transitions (unchanged
    # rules) plus the v2.1.0 episode fields of trace_model.episode_transitions.
    legacy = tm.transitions(profile, octx)
    expected = tm.episode_transitions(profile, octx)
    assert len(ours.transitions) == len(expected) == len(legacy), pid
    for got, exp, old in zip(ours.transitions, expected, legacy):
        dumped = got.model_dump()
        assert {k: dumped[k] for k in old if k != "profile_id"} == {k: v for k, v in old.items() if k != "profile_id"}, pid
        assert dumped == {k: v for k, v in exp.items() if k != "profile_id"}, pid

    # Segments and stints follow the episode-aware rules (trace_model.career_episode_paths).
    exp_paths = tm.career_episode_paths(profile, octx)
    got_paths = [{"segment_no": s.segment_no, "role_path": s.role_path, "entry_change_types": s.entry_change_types,
                  "stints": [st.model_dump() for st in s.stints]} for s in ours.segments]
    assert got_paths == [{"segment_no": p["segment_no"], "role_path": p["role_path"],
                          "entry_change_types": p["entry_change_types"], "stints": p["stints"]}
                         for p in exp_paths], pid


def test_all_synthetic_fixtures_match_trace_model(tm, octx, ctx):
    profiles = synthetic_profiles()
    assert len(profiles) == 41
    for p in profiles:
        assert_parity(tm, octx, ctx, p)


# --------------------------------------------------------------------------- randomised parity
ROLES = ["ROLE-SWE", "ROLE-BA", "ROLE-OWNER", "PM-INTERN", "PM-APM", "PM-PM", "PM-SPM", "PM-LEAD", "ROLE-ENT"]
TYPES = ["full_time", "full_time", "full_time", "contract", "internship", "founder", "freelance", "part_time", "unknown"]


def _random_date(rng, allow_ongoing):
    kinds = ["month"] * 5 + ["year"] * 3 + ["unknown"] + (["ongoing"] if allow_ongoing else [])
    kind = rng.choice(kinds)
    year = rng.randint(2014, 2020)
    if kind == "month":
        return f"{year}-{rng.randint(1, 12):02d}"
    if kind == "year":
        return str(year)
    return None if kind == "unknown" else "ongoing"


def _random_profile(rng, n):
    pid = f"SYN-9{n:03d}"
    emps, seq = [], 0
    while len(emps) < rng.randint(1, 5):
        seq += 1
        role = rng.choice(ROLES + ["ROLE-UNKNOWN"])
        status = rng.choice(["confirmed", "confirmed", "auto_high_confidence", "needs_review"])
        if role == "ROLE-UNKNOWN":
            status = rng.choice(["unresolved", "needs_review"])
        counts = rng.choice([None, None, None, True, False])
        e = emp_dict(pid, seq, role, _random_date(rng, False), _random_date(rng, True),
                     org=rng.choice(["A", "B", "C", None]), etype=rng.choice(TYPES),
                     primary=rng.random() > 0.15, status=status,
                     conf=rng.choice([1.0, 0.95, 0.8, 0.79, 0.5, 0.0]),
                     counts=counts, note="reviewed" if counts is not None else None)
        try:
            from_foundation_employment(e)
        except ValidationError:
            seq -= 1  # infeasible period (end before start): the database would reject it too
            continue
        emps.append(e)
    rng.shuffle(emps)  # input order must not matter
    return {"profile_id": pid, "employments": emps,
            "resume_as_of": rng.choice(["2026-11", "2026", None]),
            "resume_as_of_precision": None}


def _label(m):
    return f"{m // 12:04d}-{m % 12 + 1:02d}"


def _clean_profile(rng, n):
    """Mostly chronological, confirmable histories (so confirmed moves of every change type occur),
    with occasional perturbations: overlaps, year-only dates, unknown employers, parallel work."""
    pid = f"SYN-8{n:03d}"
    month = rng.randint(2012 * 12, 2016 * 12)
    emps = []
    for seq in range(1, rng.randint(2, 6) + 1):
        start = month + rng.choice([0, 1, 1, 1, 2, 4]) - (1 if seq > 1 and rng.random() < 0.05 else 0)
        end = start + rng.randint(6, 40)
        last = seq > 1 and rng.random() < 0.3
        year_only = rng.random() < 0.08
        s_val = str(start // 12) if year_only else _label(start)
        e_val = "ongoing" if last else (str(end // 12) if year_only else _label(end))
        parallel = seq > 1 and rng.random() < 0.08
        emps.append(emp_dict(pid, seq, rng.choice(["ROLE-SWE", "PM-APM", "PM-PM", "PM-PM", "PM-SPM"]), s_val, e_val,
                             org=rng.choice(["A", "A", "B", "C", "D", None]) if rng.random() < 0.9 else None,
                             etype="freelance" if parallel else "full_time", primary=not parallel,
                             note="parallel" if parallel else None))
        if last:
            break
        if not parallel:
            month = end
    for e in emps:
        try:
            from_foundation_employment(e)
        except ValidationError:
            return None
    rng.shuffle(emps)
    return {"profile_id": pid, "employments": emps, "resume_as_of": "2026-11", "resume_as_of_precision": "month"}


def test_randomised_histories_match_trace_model(tm, octx, ctx):
    rng = random.Random(20261111)
    relations, changes, collapsed_moves = set(), set(), 0
    profiles = [_random_profile(rng, n) for n in range(2000)]
    for p in profiles:
        ra = p["resume_as_of"]
        p["resume_as_of_precision"] = "unknown" if ra is None else ("year" if len(ra) == 4 else "month")
    clean_rng = random.Random(20261112)
    profiles += [p for p in (_clean_profile(clean_rng, n) for n in range(2000)) if p is not None]
    assert len(profiles) > 3500
    for p in profiles:
        assert_parity(tm, octx, ctx, p)
        ets = tm.episode_transitions(p, octx)
        relations |= {t["timing_relation"] for t in ets}
        changes |= {(t["change_type"], t["transition_status"]) for t in ets}
        collapsed_moves += sum(1 for x in tm.career_episode_paths(p, octx)
                               for a, b in zip(x["role_path"], x["role_path"][1:]) if a == b)
    # The generator must have exercised every timing relation and every change type, confirmed
    # and unconfirmed, and produced repeated-role episodes (same-role employer moves).
    assert relations == {"sequential", "same_month_boundary", "previous_role_ongoing", "timing_unknown",
                         "sequence_order_conflict", "overlapping", "boundary_uncertain"}
    all_types = {"role_change", "employer_change", "role_and_employer_change", "role_change_employer_unknown",
                 "no_change", "employer_unknown"}
    assert {c for c, s in changes if s == "confirmed"} == all_types
    assert {c for c, s in changes if s == "unconfirmed"} == all_types
    assert collapsed_moves > 0
