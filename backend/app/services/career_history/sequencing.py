"""Deterministic candidate career-history sequencing.

A port of data-foundation/scripts/trace_model.py (Trace Data Foundation v2.1.0), which is the
executable specification of trace_ref.v_employment_classified, v_career_sequence,
v_role_transitions / v_episode_transitions, v_career_episode_stints / v_career_episode_paths and
v_profile_sequence_integrity. Segments follow the episode-aware rules (trace_model
career_episode_paths), not the legacy role-code collapse (trace_model career_paths).
Keep the two in step; differences are listed in backend/docs/CANDIDATE_SEQUENCING.md.

Rules in brief
- Order is sequence_index, never dates. Dates that contradict the order are flagged
  (sequence_order_conflict), not used to reorder.
- Every record is kept and classified. Parallel activity sits outside the primary chain.
- The chain is built from every primary record first; exclusions are applied afterwards, so an
  excluded record breaks the chain instead of being skipped (A -> unknown -> B gives two
  unconfirmed transitions, never A -> B).
- Candidate review (candidate-side inputs only): a record still pending the candidate's review is
  excluded (review_pending) and kept in the chain as a barrier, even if stated as parallel, so
  confirmed A -> pending B -> confirmed C never yields A -> C. Rejected records are removed by the
  caller before sequencing. Inputs without a review status (reference data, parity) are unaffected.
- No date, gap or role is invented: unknown stays None, and coarse dates give ranges.
- Episodes: a confirmed role change, employer change, or both is a pathway move. Consecutive
  records collapse into one stint only when the role is unchanged and the employer is unchanged
  or not known. So PM at A -> PM at B is two stints; PM at A -> PM at A is one.
"""
from typing import Iterable, List, Optional, Sequence, Tuple

from app.services.career_history import dates
from app.services.career_history.models import (
    CandidateEmployment,
    CandidateHistory,
    ClassifiedEmployment,
    PartialDateInput,
    PartialDateView,
    Segment,
    SequenceIntegrity,
    SequencingContext,
    Stint,
    Transition,
)

RULESET = "trace_model v2.1.0 (episode-aware)"

# Founder is deliberately absent: it counts only with an explicit, documented override.
FULL_TIME_TYPES = ("full_time", "contract")
CONFIRMABLE_MAPPING_STATUSES = ("confirmed", "auto_high_confidence")
CONFIRMABLE_RELATIONS = ("sequential", "same_month_boundary")
PRECISE_GAP_RELATIONS = ("sequential", "same_month_boundary")
PATHWAY_MOVE_CHANGE_TYPES = ("role_change", "employer_change", "role_and_employer_change",
                             "role_change_employer_unknown")
CONTINUATION_CHANGE_TYPES = ("no_change", "employer_unknown")


class CandidateHistoryError(ValueError):
    """The input cannot be sequenced as given (it would also be rejected by the database).

    Raised for structural problems only, never for ambiguity: ambiguous or incomplete records
    are always kept and classified."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


# --------------------------------------------------------------------------- public API
def sequence_candidate_history(
    employments: Iterable[CandidateEmployment],
    context: SequencingContext,
    resume_as_of: Optional[PartialDateInput] = None,
) -> CandidateHistory:
    """Classify, order and pair a candidate's employment records.

    `resume_as_of` (the date the history was stated) is used only to bound the tenure of
    ongoing roles; without it their tenure is unknown."""
    records = sorted(employments, key=lambda e: e.sequence_index)
    _check_collection(records, context)

    min_conf = context.effective_min_mapping_confidence
    as_of = (None, None) if resume_as_of is None else dates.bounds(
        resume_as_of.year, resume_as_of.month, resume_as_of.precision)

    classified = [_classify(e, context, min_conf, as_of) for e in records]
    _link_primary_chain(classified)
    primary = [c for c in classified if c.in_primary_sequence]
    transitions = [_transition(p, n) for p, n in zip(primary, primary[1:])]
    segments = _segments(primary, transitions)
    integrity = _integrity(classified, transitions)

    return CandidateHistory(
        ruleset=RULESET,
        min_mapping_confidence_applied=min_conf,
        records=classified,
        transitions=transitions,
        segments=segments,
        integrity=integrity,
    )


# --------------------------------------------------------------------------- validation
def _check_collection(records: List[CandidateEmployment], ctx: SequencingContext) -> None:
    """Cross-record and lookup checks (unique keys and foreign keys in the database)."""
    problems = []
    seen_ids, seen_seq = set(), set()
    for e in records:
        if e.employment_id in seen_ids:
            problems.append(f"duplicate employment_id {e.employment_id!r}")
        seen_ids.add(e.employment_id)
        if e.sequence_index in seen_seq:
            problems.append(f"duplicate sequence_index {e.sequence_index}")
        seen_seq.add(e.sequence_index)
        if e.role_id not in ctx.roles:
            problems.append(f"{e.employment_id}: unknown role_id {e.role_id!r}")
        elif e.seniority_code is None and ctx.roles[e.role_id].default_seniority_code not in ctx.seniority_ranks:
            problems.append(f"{e.employment_id}: taxonomy default seniority for {e.role_id!r} is not in the ladder")
        if e.seniority_code is not None and e.seniority_code not in ctx.seniority_ranks:
            problems.append(f"{e.employment_id}: unknown seniority_code {e.seniority_code!r}")
    if problems:
        raise CandidateHistoryError(problems)


# --------------------------------------------------------------------------- classification
def _date_view(year, month, precision) -> PartialDateView:
    lo, hi = dates.bounds(year, month, precision)
    return PartialDateView(year=year, month=month, precision=precision, lo=lo, hi=hi,
                           lo_label=dates.month_label(lo), hi_label=dates.month_label(hi))


def _tenure(start: PartialDateView, end: PartialDateView,
            as_of: Tuple[Optional[int], Optional[int]]) -> Tuple[Optional[int], Optional[int], bool]:
    """Inclusive tenure (min, max, censored); ongoing roles are censored at resume_as_of.
    Mirrors trace_model.tenure_months."""
    end_lo, end_hi, censored = end.lo, end.hi, False
    if end.precision == "ongoing":
        end_lo, end_hi = as_of
        censored = True
    if start.lo is None or end_lo is None:
        return None, None, censored
    return max(1, end_lo - start.hi + 1), end_hi - start.lo + 1, censored


def _classify(e: CandidateEmployment, ctx: SequencingContext, min_conf: float,
              as_of: Tuple[Optional[int], Optional[int]]) -> ClassifiedEmployment:
    """Mirrors trace_model.classify_employment."""
    role = ctx.roles[e.role_id]
    start = _date_view(e.start_year, e.start_month, e.start_precision)
    end = _date_view(e.end_year, e.end_month, e.end_precision)
    seniority_code = e.seniority_code or role.default_seniority_code
    if e.counts_as_full_time_experience is not None:
        counts_full_time, basis = e.counts_as_full_time_experience, "stated_override"
    else:
        counts_full_time, basis = e.employment_type in FULL_TIME_TYPES, "employment_type_default"

    reasons = []
    # Candidate-side only (review_status is None for reference and parity inputs).
    pending = e.review_status == "pending_review"
    if pending:
        reasons.append("review_pending")
    if not e.primary_career_sequence:
        reasons.append("parallel_activity")
    if not counts_full_time:
        reasons.append("not_full_time_experience")
    if not role.is_resolved:
        reasons.append("unresolved_role")
    if e.mapping_status not in CONFIRMABLE_MAPPING_STATUSES:
        reasons.append("mapping_not_confirmed")
    # Candidate-side (M5): a candidate's own decision has no confidence and is not held to the threshold;
    # a mapping whose title changed since it was made is never eligible.
    if e.mapping_basis != "candidate_selected" and e.mapping_confidence < min_conf:
        reasons.append("low_mapping_confidence")
    if e.mapping_is_stale:
        reasons.append("mapping_stale")
    if start.lo is None:
        reasons.append("unknown_start_date")

    t_min, t_max, censored = _tenure(start, end, as_of)
    return ClassifiedEmployment(
        stated=e,
        start=start,
        end=end,
        role_family=role.role_family,
        is_pm_target=role.is_pm_target,
        role_is_resolved=role.is_resolved,
        seniority_code=seniority_code,
        seniority_code_source="stated" if e.seniority_code else "taxonomy_default",
        seniority_rank=ctx.seniority_ranks.get(seniority_code),
        counts_as_full_time_experience=counts_full_time,
        full_time_basis=basis,
        tenure_months_min=t_min,
        tenure_months_max=t_max,
        tenure_is_censored=censored,
        exclusion_reasons=reasons,
        is_transition_eligible=not reasons,
        # An unreviewed record is a barrier even when it is stated as parallel: its own flags are
        # unreviewed too, so it must not be able to step out of the chain and make its
        # neighbours adjacent.
        in_primary_sequence=e.primary_career_sequence or pending,
        primary_position=None,
        prev_employment_id=None,
    )


def _link_primary_chain(classified: List[ClassifiedEmployment]) -> None:
    """Mirrors v_career_sequence: position and predecessor among primary records only."""
    prev_id, position = None, 0
    for c in classified:
        if not c.in_primary_sequence:
            continue
        position += 1
        c.primary_position = position
        c.prev_employment_id = prev_id
        prev_id = c.stated.employment_id


# --------------------------------------------------------------------------- transitions
def timing_relation(prev: ClassifiedEmployment, nxt: ClassifiedEmployment
                    ) -> Tuple[str, Optional[int], Optional[int]]:
    """(relation, gap_min, gap_max). Mirrors trace_model.timing_relation.

    Gap = whole calendar months strictly between the last month of `prev` and the first month
    of `nxt`, so 2021-06 -> 2021-07 is a gap of 0, as is a same-month boundary. Any overlap
    beyond the shared boundary month is 'overlapping' and never confirmable; there is no
    notice-period tolerance."""
    p_start_lo = prev.start.lo
    p_end_lo, p_end_hi = prev.end.lo, prev.end.hi
    n_start_lo, n_start_hi = nxt.start.lo, nxt.start.hi

    if prev.end.precision == "ongoing":
        return "previous_role_ongoing", None, None
    if p_end_lo is None or n_start_lo is None:
        return "timing_unknown", None, None
    if p_start_lo is not None and n_start_hi < p_start_lo:
        return "sequence_order_conflict", None, None
    if n_start_lo > p_end_hi:
        return "sequential", n_start_lo - p_end_hi - 1, n_start_hi - p_end_lo - 1
    if p_end_lo == p_end_hi and n_start_lo == n_start_hi:
        if p_end_lo == n_start_lo:
            return "same_month_boundary", 0, 0
        return "overlapping", None, None
    if p_end_lo > n_start_hi:
        return "overlapping", None, None
    return "boundary_uncertain", 0, max(0, n_start_hi - p_end_lo - 1)


def _transition(prev: ClassifiedEmployment, nxt: ClassifiedEmployment) -> Transition:
    """Mirrors trace_model.transitions for one pair."""
    relation, gmin, gmax = timing_relation(prev, nxt)
    reasons = [f"from:{r}" for r in prev.exclusion_reasons] + [f"to:{r}" for r in nxt.exclusion_reasons]
    if relation not in CONFIRMABLE_RELATIONS:
        reasons.append(f"timing:{relation}")

    p_emp, n_emp = prev.stated.employer_id, nxt.stated.employer_id
    if p_emp and n_emp:
        move = "internal" if p_emp == n_emp else "external"
    else:
        move = "unknown"

    rank_change = None
    if prev.seniority_rank is not None and nxt.seniority_rank is not None:
        rank_change = nxt.seniority_rank - prev.seniority_rank

    gap_precise = (relation in PRECISE_GAP_RELATIONS
                   and prev.end.lo == prev.end.hi and nxt.start.lo == nxt.start.hi)
    status = "unconfirmed" if reasons else "confirmed"
    role_changed = prev.stated.role_id != nxt.stated.role_id
    ct = change_type(prev.stated.role_id, nxt.stated.role_id, move)

    return Transition(
        from_employment_id=prev.stated.employment_id,
        to_employment_id=nxt.stated.employment_id,
        from_role_id=prev.stated.role_id,
        to_role_id=nxt.stated.role_id,
        from_role_family=prev.role_family,
        to_role_family=nxt.role_family,
        move_type=move,
        seniority_rank_change=rank_change,
        timing_relation=relation,
        gap_months_min=gmin,
        gap_months_max=gmax,
        gap_is_precise=gap_precise,
        transition_status=status,
        unconfirmed_reasons=reasons,
        role_changed=role_changed,
        employer_changed={"external": True, "internal": False}.get(move),
        change_type=ct,
        is_pathway_move=status == "confirmed" and ct in PATHWAY_MOVE_CHANGE_TYPES,
    )


def change_type(from_role_id: str, to_role_id: str, move_type: str) -> str:
    """Mirrors trace_model.change_type / trace_ref.v_episode_transitions.change_type."""
    role_changed = from_role_id != to_role_id
    if move_type == "unknown":
        return "role_change_employer_unknown" if role_changed else "employer_unknown"
    if role_changed:
        return "role_and_employer_change" if move_type == "external" else "role_change"
    return "employer_change" if move_type == "external" else "no_change"


# --------------------------------------------------------------------------- segments
def _segments(primary: List[ClassifiedEmployment], transitions: List[Transition]) -> List[Segment]:
    """Maximal runs joined by confirmed transitions, split into employment-episode stints.
    Consecutive records collapse only on a continuation (same role; employer unchanged or not
    known). Ineligible records start a new segment and are left out of stints. Segment numbers
    keep counting across dropped segments. Mirrors trace_model.career_episode_paths."""
    incoming = {t.to_employment_id: t for t in transitions}
    grouped, segment_no = {}, 0
    for rec in primary:
        t = incoming.get(rec.stated.employment_id)
        confirmed = t is not None and t.transition_status == "confirmed"
        if not confirmed:
            segment_no += 1
        if rec.is_transition_eligible:
            grouped.setdefault(segment_no, []).append((rec, t if confirmed else None))

    out = []
    for no in sorted(grouped):
        stints: List[Stint] = []
        for rec, t in grouped[no]:
            if stints and t is not None and t.change_type in CONTINUATION_CHANGE_TYPES:
                stints[-1].employment_ids.append(rec.stated.employment_id)
                continue
            stints.append(Stint(role_id=rec.stated.role_id,
                                entry_change_type=t.change_type if (stints and t is not None) else None,
                                start_lo=rec.start.lo, start_hi=rec.start.hi,
                                employment_ids=[rec.stated.employment_id]))
        out.append(Segment(segment_no=no, stints=stints, role_path=[s.role_id for s in stints],
                           entry_change_types=[s.entry_change_type for s in stints]))
    return out


def _integrity(classified: List[ClassifiedEmployment], transitions: List[Transition]) -> SequenceIntegrity:
    """Mirrors trace_ref.v_profile_sequence_integrity (counts every record, primary or not)."""
    count = len(classified)
    max_seq = max((c.stated.sequence_index for c in classified), default=None)
    return SequenceIntegrity(
        employment_count=count,
        max_sequence_index=max_seq,
        has_sequence_gaps=max_seq is not None and max_seq != count,
        ongoing_primary_count=sum(1 for c in classified
                                  if c.in_primary_sequence and c.end.precision == "ongoing"),
        has_order_conflict=any(t.timing_relation == "sequence_order_conflict" for t in transitions),
    )
