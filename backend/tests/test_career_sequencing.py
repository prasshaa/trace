"""Unit tests for the candidate sequencing service.

Cases are ported from data-foundation/tests/test_dates.py and test_transitions.py (single-history
cases; cohort/route-summary cases are out of scope). Inputs are synthetic and prove behaviour
only; they are not career evidence.
"""
import pytest
from pydantic import ValidationError

from app.services.career_history import (
    REASON_DESCRIPTIONS,
    CandidateEmployment,
    CandidateHistoryError,
    PartialDateInput,
    sequence_candidate_history,
)
from app.services.career_history import dates
from tests.career_history_support import emp, foundation_context, requires_foundation

pytestmark = requires_foundation


@pytest.fixture(scope="module")
def ctx():
    return foundation_context()


def run(ctx, *records, resume_as_of=None):
    return sequence_candidate_history(records, ctx, resume_as_of=resume_as_of)


def pairs(history):
    return [(t.from_role_id, t.to_role_id) for t in history.transitions]


def paths(history):
    return [s.role_path for s in history.segments]


# --------------------------------------------------------------------------- partial dates
class TestPartialDates:
    def test_month_precision_is_a_single_month(self):
        assert dates.bounds(2021, 6, "month") == (2021 * 12 + 5, 2021 * 12 + 5)

    def test_year_precision_spans_january_to_december(self):
        lo, hi = dates.bounds(2019, None, "year")
        assert (dates.month_label(lo), dates.month_label(hi)) == ("2019-01", "2019-12")

    def test_unknown_and_ongoing_have_no_bounds(self):
        assert dates.bounds(None, None, "unknown") == (None, None)
        assert dates.bounds(None, None, "ongoing") == (None, None)

    @pytest.mark.parametrize("fields", [
        dict(start_year=2021, start_month=None, start_precision="month"),
        dict(start_year=2021, start_month=6, start_precision="year"),
        dict(start_year=2021, start_month=13, start_precision="month"),
        dict(start_year=2021, start_month=6, start_precision="unknown"),
        dict(start_year=None, start_month=None, start_precision="month"),
        dict(start_year=1949, start_month=None, start_precision="year"),
        dict(start_year=None, start_month=None, start_precision="ongoing"),
    ])
    def test_precision_must_match_value(self, fields):
        with pytest.raises(ValidationError):
            CandidateEmployment(employment_id="E1", sequence_index=1, title_raw="x", **fields)

    def test_dates_are_reported_as_stated_and_never_filled_in(self, ctx):
        h = run(ctx, emp("C", 1, "ROLE-DA", "2019", None))
        r = h.records[0]
        assert (r.start.precision, r.start.year, r.start.month) == ("year", 2019, None)
        assert (r.start.lo_label, r.start.hi_label) == ("2019-01", "2019-12")
        assert (r.end.precision, r.end.lo, r.end.hi, r.end.lo_label) == ("unknown", None, None, None)
        assert (r.tenure_months_min, r.tenure_months_max) == (None, None)


class TestInclusiveMonths:
    def tenure(self, ctx, start, end, resume_as_of=None):
        r = run(ctx, emp("C", 1, "PM-PM", start, end), resume_as_of=resume_as_of).records[0]
        return r.tenure_months_min, r.tenure_months_max, r.tenure_is_censored

    def test_tenure_counts_first_and_last_month(self, ctx):
        assert self.tenure(ctx, "2021-06", "2023-03") == (22, 22, False)

    def test_single_month_role_lasts_one_month(self, ctx):
        assert self.tenure(ctx, "2021-06", "2021-06") == (1, 1, False)

    def test_year_only_tenure_is_a_range_not_a_point(self, ctx):
        assert self.tenure(ctx, "2019", "2020")[:2] == (2, 24)

    def test_ongoing_tenure_is_censored_at_resume_as_of(self, ctx):
        as_of = PartialDateInput(year=2026, month=11, precision="month")
        assert self.tenure(ctx, "2023-07", "ongoing", as_of) == (41, 41, True)
        assert self.tenure(ctx, "2023-07", "ongoing") == (None, None, True)


class TestGaps:
    def relation(self, ctx, a_end, b_start, a_start="2015-01"):
        t = run(ctx, emp("C", 1, "ROLE-SWE", a_start, a_end),
                emp("C", 2, "PM-APM", b_start, "ongoing", org="B")).transitions[0]
        return t.timing_relation, t.gap_months_min, t.gap_months_max

    def test_next_month_start_is_a_zero_gap(self, ctx):
        assert self.relation(ctx, "2021-06", "2021-07") == ("sequential", 0, 0)

    def test_real_gap_counts_whole_months_between(self, ctx):
        assert self.relation(ctx, "2021-06", "2021-09") == ("sequential", 2, 2)

    def test_same_month_boundary_is_a_zero_gap_not_dropped(self, ctx):
        assert self.relation(ctx, "2022-03", "2022-03") == ("same_month_boundary", 0, 0)

    def test_one_month_overlap_is_overlapping_not_a_handover(self, ctx):
        assert self.relation(ctx, "2021-08", "2021-07") == ("overlapping", None, None)

    def test_longer_overlap_is_overlapping(self, ctx):
        assert self.relation(ctx, "2021-09", "2021-07")[0] == "overlapping"

    def test_year_precision_boundary_is_uncertain_with_bounded_gap(self, ctx):
        assert self.relation(ctx, "2018", "2018", a_start="2015") == ("boundary_uncertain", 0, 10)

    def test_year_precision_can_still_be_definitely_sequential(self, ctx):
        assert self.relation(ctx, "2018", "2020-02", a_start="2015") == ("sequential", 13, 24)


# --------------------------------------------------------------------------- ordering and preservation
class TestOrderingAndPreservation:
    def test_records_are_ordered_by_sequence_index_not_by_input_order(self, ctx):
        h = run(ctx, emp("C", 2, "PM-PM", "2021-01", "ongoing", org="B"), emp("C", 1, "ROLE-SWE", "2017-01", "2020-12"))
        assert [r.stated.sequence_index for r in h.records] == [1, 2]
        assert pairs(h) == [("ROLE-SWE", "PM-PM")]

    def test_dates_never_reorder_the_sequence(self, ctx):
        h = run(ctx, emp("C", 1, "PM-PM", "2021-01", "2022-01"), emp("C", 2, "PM-SPM", "2019-01", "2020-01", org="B"))
        assert [r.stated.role_id for r in h.records] == ["PM-PM", "PM-SPM"]
        t = h.transitions[0]
        assert (t.timing_relation, t.transition_status) == ("sequence_order_conflict", "unconfirmed")
        assert h.integrity.has_order_conflict

    def test_every_record_is_kept_including_ambiguous_parallel_and_undated(self, ctx):
        records = [
            emp("C", 1, "ROLE-SWE", "2016-07", "2019-03"),
            emp("C", 2, "ROLE-CONSULT", "2018-01", "2019-03", org="F", etype="freelance", primary=False, note="side"),
            emp("C", 3, "ROLE-UNKNOWN", None, None, org="B", status="unresolved", conf=0.0, title="Associate"),
            emp("C", 4, "ROLE-OWNER", "2020-01", "2021-12", org="D", status="needs_review", conf=0.6),
            emp("C", 5, "PM-PM", "2021-10", "ongoing", org="E"),
        ]
        h = run(ctx, *records)
        assert [r.stated.employment_id for r in h.records] == [r.employment_id for r in records]
        assert [r.stated for r in h.records] == records

    def test_minimal_extracted_record_is_accepted_with_database_defaults(self, ctx):
        h = run(ctx, CandidateEmployment(employment_id="E1", sequence_index=1, title_raw="Associate"))
        r = h.records[0]
        assert (r.stated.role_id, r.stated.mapping_status, r.role_is_resolved) == ("ROLE-UNKNOWN", "unresolved", False)
        assert set(r.exclusion_reasons) == {
            "not_full_time_experience", "unresolved_role", "mapping_not_confirmed",
            "low_mapping_confidence", "unknown_start_date"}

    def test_empty_history_is_valid(self, ctx):
        h = run(ctx)
        assert (h.records, h.transitions, h.segments) == ([], [], [])
        assert h.integrity.employment_count == 0 and not h.integrity.has_sequence_gaps


# --------------------------------------------------------------------------- transitions
class TestSameMonthAndMoves:
    def test_same_month_internal_promotions_are_kept_and_confirmed(self, ctx):
        h = run(ctx, emp("C", 1, "PM-APM", "2020-07", "2022-03"), emp("C", 2, "PM-PM", "2022-03", "2024-09"),
                emp("C", 3, "PM-SPM", "2024-09", "ongoing"))
        assert len(h.transitions) == 2
        for t in h.transitions:
            assert (t.transition_status, t.timing_relation, t.move_type) == ("confirmed", "same_month_boundary", "internal")
            assert (t.gap_months_min, t.gap_months_max, t.gap_is_precise) == (0, 0, True)
            assert t.seniority_rank_change > 0

    def test_external_and_unknown_moves(self, ctx):
        ext = run(ctx, emp("C", 1, "PM-PM", "2019-01", "2022-08", org="A"), emp("C", 2, "PM-SPM", "2022-09", "ongoing", org="B"))
        unk = run(ctx, emp("C", 1, "PM-PM", "2019-01", "2022-08", org=None), emp("C", 2, "PM-SPM", "2022-09", "ongoing", org="B"))
        assert ext.transitions[0].move_type == "external"
        assert unk.transitions[0].move_type == "unknown"

    def test_unranked_seniority_gives_no_rank_change(self, ctx):
        h = run(ctx, emp("C", 1, "PM-PM", "2016-01", "2019-12"),
                emp("C", 2, "ROLE-ENT", "2020-01", "2021-12", org="B", etype="founder", counts=True, note="reviewed"))
        assert h.records[1].seniority_rank is None
        assert h.transitions[0].seniority_rank_change is None


class TestOverlapsAndParallel:
    def test_multi_month_overlap_is_unconfirmed(self, ctx):
        t = run(ctx, emp("C", 1, "ROLE-SWE", "2017-06", "2021-09"), emp("C", 2, "PM-APM", "2021-07", "ongoing", org="B")).transitions[0]
        assert (t.timing_relation, t.transition_status) == ("overlapping", "unconfirmed")
        assert "timing:overlapping" in t.unconfirmed_reasons

    def test_one_month_overlap_is_not_auto_confirmed(self, ctx):
        t = run(ctx, emp("C", 1, "ROLE-SWE", "2017-06", "2021-08"), emp("C", 2, "PM-APM", "2021-07", "ongoing", org="B")).transitions[0]
        assert (t.timing_relation, t.transition_status) == ("overlapping", "unconfirmed")

    def test_overlap_corrected_by_review_is_confirmed(self, ctx):
        t = run(ctx, emp("C", 1, "ROLE-SWE", "2017-06", "2021-07"), emp("C", 2, "PM-APM", "2021-07", "ongoing", org="B")).transitions[0]
        assert (t.timing_relation, t.transition_status) == ("same_month_boundary", "confirmed")

    def test_year_precision_definite_overlap_is_overlapping(self, ctx):
        t = run(ctx, emp("C", 1, "ROLE-SWE", "2015", "2019"), emp("C", 2, "PM-APM", "2018-03", "ongoing", org="B")).transitions[0]
        assert t.timing_relation == "overlapping"

    def test_parallel_activity_is_kept_but_outside_the_chain(self, ctx):
        h = run(ctx, emp("C", 1, "ROLE-SWE", "2017-06", "2021-06"),
                emp("C", 2, "ROLE-CONSULT", "2020-01", "2021-06", org="F", etype="freelance", primary=False, note="parallel"),
                emp("C", 3, "PM-APM", "2021-07", "ongoing", org="B"))
        assert pairs(h) == [("ROLE-SWE", "PM-APM")]
        assert h.transitions[0].transition_status == "confirmed"
        parallel = h.records[1]
        assert not parallel.in_primary_sequence and parallel.primary_position is None
        assert "parallel_activity" in parallel.exclusion_reasons
        assert h.records[2].prev_employment_id == "C-EMP-01"

    def test_role_after_an_ongoing_primary_role_is_unconfirmed(self, ctx):
        h = run(ctx, emp("C", 1, "ROLE-SWE", "2017-06", "ongoing"), emp("C", 2, "PM-APM", "2021-07", "ongoing", org="B"))
        t = h.transitions[0]
        assert (t.timing_relation, t.transition_status) == ("previous_role_ongoing", "unconfirmed")
        assert h.integrity.ongoing_primary_count == 2


class TestMissingIntermediateRoles:
    def test_unknown_role_breaks_the_chain_instead_of_bridging_it(self, ctx):
        h = run(ctx, emp("C", 1, "ROLE-SWE", "2016-07", "2019-03"),
                emp("C", 2, "ROLE-UNKNOWN", "2019-04", "2020-12", org="B", status="unresolved", conf=0.3, title="Associate"),
                emp("C", 3, "PM-PM", "2021-01", "ongoing", org="C"))
        assert pairs(h) == [("ROLE-SWE", "ROLE-UNKNOWN"), ("ROLE-UNKNOWN", "PM-PM")]
        assert all(t.transition_status == "unconfirmed" for t in h.transitions)
        assert paths(h) == [["ROLE-SWE"], ["PM-PM"]]

    def test_undated_middle_role_also_breaks_the_chain(self, ctx):
        h = run(ctx, emp("C", 1, "ROLE-PAN", "2018-01", "2019-12"), emp("C", 2, "ROLE-DA", None, None, org="B"),
                emp("C", 3, "PM-APM", "2020-02", "ongoing", org="C"))
        assert paths(h) == [["ROLE-PAN"], ["PM-APM"]]
        assert "timing:timing_unknown" in h.transitions[0].unconfirmed_reasons
        assert "to:unknown_start_date" in h.transitions[0].unconfirmed_reasons


class TestAmbiguousTitles:
    def test_product_owner_awaiting_review_is_not_confirmed(self, ctx):
        h = run(ctx, emp("C", 1, "ROLE-QA", "2016-01", "2019-07"),
                emp("C", 2, "ROLE-OWNER", "2019-08", "2022-01", org="B", status="needs_review", conf=0.6),
                emp("C", 3, "PM-PM", "2022-02", "ongoing", org="C"))
        assert all(t.transition_status == "unconfirmed" for t in h.transitions)
        assert "to:mapping_not_confirmed" in h.transitions[0].unconfirmed_reasons
        assert "from:low_mapping_confidence" in h.transitions[1].unconfirmed_reasons

    def test_reviewed_product_owner_is_its_own_family_not_pm(self, ctx):
        t = run(ctx, emp("C", 1, "ROLE-OWNER", "2019-01", "2022-02"), emp("C", 2, "PM-PM", "2022-03", "ongoing", org="B")).transitions[0]
        assert t.transition_status == "confirmed"
        assert (t.from_role_family, t.to_role_family) == ("product_ownership", "product_management")

    def test_high_confidence_but_unconfirmed_status_is_still_excluded(self, ctx):
        r = run(ctx, emp("C", 1, "PM-PM", "2020-01", "2021-01", status="needs_review", conf=0.95)).records[0]
        assert r.exclusion_reasons == ["mapping_not_confirmed"]

    def test_auto_mapping_below_threshold_is_excluded(self, ctx):
        r = run(ctx, emp("C", 1, "PM-PM", "2020-01", "2021-01", status="auto_high_confidence", conf=0.7)).records[0]
        assert r.exclusion_reasons == ["low_mapping_confidence"]

    def test_missing_threshold_fails_closed_at_one(self):
        h = run(foundation_context(min_mapping_confidence=None),
                emp("C", 1, "PM-PM", "2020-01", "2021-01", status="auto_high_confidence", conf=0.99))
        assert h.min_mapping_confidence_applied == 1.0
        assert h.records[0].exclusion_reasons == ["low_mapping_confidence"]


class TestIncompleteDates:
    def test_unknown_start_is_excluded_with_reason(self, ctx):
        assert "unknown_start_date" in run(ctx, emp("C", 1, "ROLE-DA", None, None)).records[0].exclusion_reasons

    def test_same_year_only_boundary_is_unconfirmed_but_records_are_kept(self, ctx):
        h = run(ctx, emp("C", 1, "ROLE-BA", "2015", "2018"), emp("C", 2, "PM-APM", "2018", "2020-03", org="B"),
                emp("C", 3, "PM-PM", "2020-04", "ongoing", org="B"))
        assert len(h.records) == 3
        t0, t1 = h.transitions
        assert (t0.timing_relation, t0.transition_status) == ("boundary_uncertain", "unconfirmed")
        assert "timing:boundary_uncertain" in t0.unconfirmed_reasons
        assert t1.transition_status == "confirmed"
        assert paths(h) == [["ROLE-BA"], ["PM-APM", "PM-PM"]]

    def test_earlier_year_only_end_confirms_order_but_not_precision(self, ctx):
        h = run(ctx, emp("C", 1, "ROLE-BA", "2015", "2017"), emp("C", 2, "PM-APM", "2018", "2020-03", org="B"),
                emp("C", 3, "PM-PM", "2020-04", "ongoing", org="B"))
        t = h.transitions[0]
        assert (t.timing_relation, t.transition_status, t.gap_is_precise) == ("sequential", "confirmed", False)
        assert (t.gap_months_min, t.gap_months_max) == (0, 22)
        assert paths(h) == [["ROLE-BA", "PM-APM", "PM-PM"]]
        first = h.segments[0].stints[0]
        assert first.start_lo != first.start_hi  # year-only start stays a range


class TestFullTimeClassification:
    def test_internship_is_excluded_from_confirmed_transitions(self, ctx):
        h = run(ctx, emp("C", 1, "PM-INTERN", "2018-05", "2018-07", etype="internship"),
                emp("C", 2, "PM-APM", "2018-08", "ongoing", org="B"))
        t = h.transitions[0]
        assert t.transition_status == "unconfirmed" and "from:not_full_time_experience" in t.unconfirmed_reasons
        assert h.records[0].full_time_basis == "employment_type_default"

    def test_explicit_reclassification_includes_it(self, ctx):
        h = run(ctx, emp("C", 1, "PM-INTERN", "2019-07", "2020-06", etype="internship", counts=True, note="12-month programme"),
                emp("C", 2, "PM-APM", "2020-07", "ongoing"))
        assert h.transitions[0].transition_status == "confirmed"
        assert h.records[0].full_time_basis == "stated_override"

    def test_founder_does_not_count_by_default(self, ctx):
        h = run(ctx, emp("C", 1, "PM-PM", "2016-04", "2019-12"),
                emp("C", 2, "ROLE-ENT", "2020-01", "2021-12", org="B", etype="founder"),
                emp("C", 3, "PM-LEAD", "2022-01", "ongoing", org="C"))
        assert "not_full_time_experience" in h.records[1].exclusion_reasons
        assert all(t.transition_status == "unconfirmed" for t in h.transitions)
        assert paths(h) == [["PM-PM"], ["PM-LEAD"]]

    def test_founder_counts_only_with_explicit_documented_classification(self, ctx):
        r = run(ctx, emp("C", 1, "ROLE-ENT", "2020-01", "2021-12", etype="founder", counts=True, note="reviewed")).records[0]
        assert r.is_transition_eligible and r.full_time_basis == "stated_override"

    def test_override_without_note_is_rejected(self):
        with pytest.raises(ValidationError):
            CandidateEmployment(employment_id="E1", sequence_index=1, title_raw="Founder", role_id="ROLE-ENT",
                                mapping_status="confirmed", mapping_confidence=1, employment_type="founder",
                                counts_as_full_time_experience=True)


class TestSegments:
    def test_same_role_employer_move_is_a_separate_episode(self, ctx):
        # v2.0.2 merged PM at A and PM at B into one stint; v2.1.0 episodes keep both.
        h = run(ctx, emp("C", 1, "PM-PM", "2018-01", "2020-12", org="A"), emp("C", 2, "PM-PM", "2021-01", "2022-12", org="B"),
                emp("C", 3, "PM-SPM", "2023-01", "ongoing", org="B"))
        (seg,) = h.segments
        assert seg.role_path == ["PM-PM", "PM-PM", "PM-SPM"]
        assert seg.entry_change_types == [None, "employer_change", "role_change"]
        assert [s.employment_ids for s in seg.stints] == [["C-EMP-01"], ["C-EMP-02"], ["C-EMP-03"]]

    def test_same_role_same_employer_is_one_episode(self, ctx):
        h = run(ctx, emp("C", 1, "PM-PM", "2018-01", "2019-12", org="A"), emp("C", 2, "PM-PM", "2020-01", "2021-12", org="A"),
                emp("C", 3, "PM-SPM", "2022-01", "ongoing", org="A"))
        (seg,) = h.segments
        assert seg.role_path == ["PM-PM", "PM-SPM"]
        assert seg.stints[0].employment_ids == ["C-EMP-01", "C-EMP-02"]
        assert [t.change_type for t in h.transitions] == ["no_change", "role_change"]
        assert [t.is_pathway_move for t in h.transitions] == [False, True]

    def test_segment_numbers_count_dropped_segments(self, ctx):
        h = run(ctx, emp("C", 1, "ROLE-SWE", "2016-07", "2019-03"),
                emp("C", 2, "ROLE-UNKNOWN", "2019-04", "2020-12", org="B", status="unresolved", conf=0.3),
                emp("C", 3, "PM-PM", "2021-01", "ongoing", org="C"))
        assert [s.segment_no for s in h.segments] == [1, 3]


class TestEpisodeScenarios:
    """The four candidate scenarios from the v2.1.0 pathway definition, plus the edge cases."""

    def test_a_role_change_at_the_same_employer(self, ctx):
        h = run(ctx, emp("C", 1, "PM-APM", "2019-01", "2021-06", org="A"), emp("C", 2, "PM-PM", "2021-07", "ongoing", org="A"))
        (t,) = h.transitions
        assert (t.change_type, t.role_changed, t.employer_changed, t.is_pathway_move) == ("role_change", True, False, True)
        assert h.segments[0].role_path == ["PM-APM", "PM-PM"]

    def test_b_same_role_at_a_different_employer(self, ctx):
        h = run(ctx, emp("C", 1, "PM-PM", "2018-01", "2021-06", org="A"), emp("C", 2, "PM-PM", "2021-07", "ongoing", org="B"))
        (t,) = h.transitions
        assert (t.change_type, t.role_changed, t.employer_changed, t.is_pathway_move) == ("employer_change", False, True, True)
        assert (h.segments[0].role_path, h.segments[0].entry_change_types) == (["PM-PM", "PM-PM"], [None, "employer_change"])

    def test_c_role_and_employer_change(self, ctx):
        h = run(ctx, emp("C", 1, "PM-PM", "2018-01", "2021-06", org="A"), emp("C", 2, "PM-SPM", "2021-07", "ongoing", org="B"))
        assert h.transitions[0].change_type == "role_and_employer_change"
        assert h.segments[0].role_path == ["PM-PM", "PM-SPM"]

    def test_d_repeated_same_role_employer_moves(self, ctx):
        h = run(ctx, emp("C", 1, "PM-PM", "2016-01", "2018-12", org="A"), emp("C", 2, "PM-PM", "2019-01", "2021-12", org="B"),
                emp("C", 3, "PM-PM", "2022-01", "ongoing", org="C"))
        assert [t.change_type for t in h.transitions] == ["employer_change", "employer_change"]
        assert all(t.is_pathway_move for t in h.transitions)
        assert h.segments[0].role_path == ["PM-PM", "PM-PM", "PM-PM"]

    def test_single_episode_has_no_transition(self, ctx):
        h = run(ctx, emp("C", 1, "PM-PM", "2019-01", "ongoing"))
        assert h.transitions == [] and h.segments[0].role_path == ["PM-PM"]

    def test_unknown_employer_is_not_a_move(self, ctx):
        h = run(ctx, emp("C", 1, "PM-PM", "2018-01", "2020-12", org="A"), emp("C", 2, "PM-PM", "2021-01", "ongoing", org=None))
        (t,) = h.transitions
        assert (t.change_type, t.employer_changed, t.is_pathway_move) == ("employer_unknown", None, False)
        assert h.segments[0].role_path == ["PM-PM"]

    def test_overlapping_employer_move_is_unconfirmed_and_splits_segments(self, ctx):
        h = run(ctx, emp("C", 1, "PM-PM", "2017-06", "2021-08", org="A"), emp("C", 2, "PM-PM", "2021-07", "ongoing", org="B"))
        (t,) = h.transitions
        assert (t.change_type, t.transition_status, t.is_pathway_move) == ("employer_change", "unconfirmed", False)
        assert [s.role_path for s in h.segments] == [["PM-PM"], ["PM-PM"]]

    def test_same_year_year_only_employer_move_is_not_confirmed(self, ctx):
        h = run(ctx, emp("C", 1, "PM-PM", "2015", "2018", org="A"), emp("C", 2, "PM-PM", "2018", "ongoing", org="B"))
        (t,) = h.transitions
        assert (t.timing_relation, t.is_pathway_move) == ("boundary_uncertain", False)

    def test_parallel_work_and_unresolved_records_are_kept(self, ctx):
        h = run(ctx, emp("C", 1, "PM-PM", "2016-01", "2018-12", org="A"),
                emp("C", 2, "PM-PM", "2017-01", "2018-06", org="F", etype="freelance", primary=False, note="side"),
                emp("C", 3, "ROLE-UNKNOWN", "2019-01", "2020-12", org="B", status="unresolved", conf=0.2),
                emp("C", 4, "PM-PM", "2021-01", "ongoing", org="C"))
        assert len(h.records) == 4
        assert [(t.from_employment_id, t.to_employment_id) for t in h.transitions] == [("C-EMP-01", "C-EMP-03"), ("C-EMP-03", "C-EMP-04")]
        assert not any(t.is_pathway_move for t in h.transitions)
        assert [s.role_path for s in h.segments] == [["PM-PM"], ["PM-PM"]]

    def test_employer_ids_are_not_serialised(self, ctx):
        h = run(ctx, emp("C", 1, "PM-PM", "2018-01", "2021-06", org="A"), emp("C", 2, "PM-PM", "2021-07", "ongoing", org="B"))
        dumped = h.model_dump_json()
        assert "C-ORG-" not in dumped and "employer_id" not in dumped
        assert h.records[0].stated.employer_id == "C-ORG-A"  # still available to the service internally

    def test_every_change_type_is_described(self, ctx):
        from app.services.career_history import CHANGE_TYPE_DESCRIPTIONS
        assert set(CHANGE_TYPE_DESCRIPTIONS) == {"role_change", "employer_change", "role_and_employer_change",
                                                 "role_change_employer_unknown", "no_change", "employer_unknown"}


# --------------------------------------------------------------------------- input contract
class TestInputContract:
    def test_row_constraints(self):
        base = dict(employment_id="E1", sequence_index=1, title_raw="PM", role_id="PM-PM",
                    mapping_status="confirmed", mapping_confidence=1)
        bad = [
            dict(end_precision="ongoing"),                                         # ongoing needs is_current_as_stated
            dict(is_current_as_stated=True),                                       # and vice versa
            dict(start_year=2021, start_month=5, start_precision="month",
                 end_year=2020, end_precision="year"),                             # end before start
            dict(mapping_status="unresolved"),                                     # unresolved needs ROLE-UNKNOWN
            dict(role_id="ROLE-UNKNOWN"),                                          # ROLE-UNKNOWN needs unresolved/needs_review
            dict(mapping_confidence=1.5),
            dict(title_raw="   "),
            dict(sequence_index=0),
        ]
        for override in bad:
            with pytest.raises(ValidationError):
                CandidateEmployment(**{**base, **override})

    def test_collection_errors_are_reported_together(self, ctx):
        records = [emp("C", 1, "PM-PM", "2020-01", "2021-01"), emp("C", 1, "PM-NOPE", "2021-02", "ongoing")]
        with pytest.raises(CandidateHistoryError) as exc:
            run(ctx, *records)
        assert any("duplicate sequence_index" in p for p in exc.value.problems)
        assert any("unknown role_id" in p for p in exc.value.problems)

    def test_every_emitted_reason_is_described(self, ctx):
        h = run(ctx, emp("C", 1, "ROLE-SWE", "2017-06", "ongoing"),
                emp("C", 2, "ROLE-CONSULT", "2018-01", "2019-01", etype="freelance", primary=False, note="side"),
                emp("C", 3, "ROLE-UNKNOWN", None, None, status="unresolved", conf=0.1),
                emp("C", 4, "PM-PM", "2015-01", "2016-01"),
                emp("C", 5, "PM-SPM", "2015-06", "2016-03"),
                emp("C", 6, "ROLE-BA", "2016", "2017"), emp("C", 7, "PM-APM", "2017", "ongoing"))
        codes = {r for rec in h.records for r in rec.exclusion_reasons}
        codes |= {r.split(":", 1)[1] if r.startswith(("from:", "to:")) else r
                  for t in h.transitions for r in t.unconfirmed_reasons}
        assert codes <= set(REASON_DESCRIPTIONS)


# --------------------------------------------------------------------------- candidate review (M1)
def reviewed(e: CandidateEmployment, status) -> CandidateEmployment:
    """The same synthetic record with a candidate review status (employer_id is not serialised)."""
    return CandidateEmployment(**{**e.model_dump(), "employer_id": e.employer_id, "review_status": status})


def career(*statuses, parallel_middle=False):
    """A at org A -> B at org B -> C at org C (APM -> PM -> SPM), each with a review status."""
    base = [emp("C", 1, "PM-APM", "2018-01", "2019-12", org="A"),
            emp("C", 2, "PM-PM", "2020-01", "2021-12", org="B", primary=not parallel_middle,
                etype="freelance" if parallel_middle else "full_time"),
            emp("C", 3, "PM-SPM", "2022-01", "2023-12", org="C")]
    return [reviewed(e, s) for e, s in zip(base, statuses)]


def transition_ids(history):
    return [(t.from_employment_id, t.to_employment_id) for t in history.transitions]


class TestCandidateReviewEligibility:
    def test_all_confirmed_baseline(self, ctx):
        h = run(ctx, *career("user_confirmed", "user_confirmed", "user_confirmed"))
        assert [t.transition_status for t in h.transitions] == ["confirmed", "confirmed"]
        assert paths(h) == [["PM-APM", "PM-PM", "PM-SPM"]]

    def test_pending_record_is_a_barrier_between_confirmed_records(self, ctx):
        h = run(ctx, *career("user_confirmed", "pending_review", "user_confirmed"))
        middle = h.records[1]
        assert middle.exclusion_reasons[0] == "review_pending" and not middle.is_transition_eligible
        assert middle.in_primary_sequence
        # Never A -> C: the pending record keeps them apart.
        assert ("C-EMP-01", "C-EMP-03") not in transition_ids(h)
        assert transition_ids(h) == [("C-EMP-01", "C-EMP-02"), ("C-EMP-02", "C-EMP-03")]
        assert all(t.transition_status == "unconfirmed" and not t.is_pathway_move for t in h.transitions)
        assert "to:review_pending" in h.transitions[0].unconfirmed_reasons
        assert "from:review_pending" in h.transitions[1].unconfirmed_reasons
        # No observed pathway runs through the barrier, and the pending record is in no stint.
        assert paths(h) == [["PM-APM"], ["PM-SPM"]]
        assert "C-EMP-02" not in {i for s in h.segments for st in s.stints for i in st.employment_ids}

    def test_multiple_pending_records(self, ctx):
        a = reviewed(emp("C", 1, "PM-APM", "2016-01", "2017-12", org="A"), "user_confirmed")
        p1 = reviewed(emp("C", 2, "PM-PM", "2018-01", "2019-12", org="B"), "pending_review")
        p2 = reviewed(emp("C", 3, "PM-SPM", "2020-01", "2021-12", org="C"), "pending_review")
        c = reviewed(emp("C", 4, "PM-GPM", "2022-01", "ongoing", org="D"), "user_confirmed")
        h = run(ctx, a, p1, p2, c)
        assert transition_ids(h) == [("C-EMP-01", "C-EMP-02"), ("C-EMP-02", "C-EMP-03"), ("C-EMP-03", "C-EMP-04")]
        assert all(t.transition_status == "unconfirmed" and not t.is_pathway_move for t in h.transitions)
        assert paths(h) == [["PM-APM"], ["PM-GPM"]]          # never PM-APM -> PM-GPM

    def test_pending_at_beginning_and_end_leaves_the_confirmed_middle_intact(self, ctx):
        first = reviewed(emp("C", 1, "PM-APM", "2016-01", "2017-12", org="Z"), "pending_review")
        a = reviewed(emp("C", 2, "PM-PM", "2018-01", "2019-12", org="A"), "user_confirmed")
        c = reviewed(emp("C", 3, "PM-SPM", "2020-01", "2021-12", org="B"), "user_confirmed")
        last = reviewed(emp("C", 4, "PM-GPM", "2022-01", "ongoing", org="C"), "pending_review")
        h = run(ctx, first, a, c, last)
        status = {ids: t.transition_status for ids, t in zip(transition_ids(h), h.transitions)}
        assert status == {("C-EMP-01", "C-EMP-02"): "unconfirmed", ("C-EMP-02", "C-EMP-03"): "confirmed",
                          ("C-EMP-03", "C-EMP-04"): "unconfirmed"}
        assert paths(h) == [["PM-PM", "PM-SPM"]]

    def test_pending_record_stated_as_parallel_is_still_a_barrier(self, ctx):
        h = run(ctx, *career("user_confirmed", "pending_review", "user_confirmed", parallel_middle=True))
        assert h.records[1].in_primary_sequence and not h.records[1].stated.primary_career_sequence
        assert ("C-EMP-01", "C-EMP-03") not in transition_ids(h)
        assert paths(h) == [["PM-APM"], ["PM-SPM"]]
        # Once reviewed, the stated parallel flag applies again and A -> C are adjacent.
        h = run(ctx, *career("user_confirmed", "user_confirmed", "user_confirmed", parallel_middle=True))
        assert transition_ids(h) == [("C-EMP-01", "C-EMP-03")] and h.transitions[0].transition_status == "confirmed"

    def test_rejected_records_are_not_accepted_and_removal_makes_neighbours_adjacent(self, ctx):
        with pytest.raises(ValidationError):
            reviewed(emp("C", 2, "PM-PM", "2020-01", "2021-12"), "rejected")
        # The caller removes the rejected record; the remaining records follow the usual rules.
        a, _, c = career("user_confirmed", "user_confirmed", "user_confirmed")
        h = run(ctx, a, c)
        assert transition_ids(h) == [("C-EMP-01", "C-EMP-03")]
        assert (h.transitions[0].transition_status, h.transitions[0].change_type) == ("confirmed", "role_and_employer_change")

    def test_existing_user_corrected_records_are_eligible_like_confirmed(self, ctx):
        h = run(ctx, *career("user_corrected", "user_confirmed", "user_corrected"))
        assert [t.transition_status for t in h.transitions] == ["confirmed", "confirmed"]
        assert all("review_pending" not in r.exclusion_reasons for r in h.records)

    def test_review_does_not_relax_any_other_rule(self, ctx):
        low = reviewed(emp("C", 2, "PM-PM", "2020-01", "2021-12", org="B", status="needs_review", conf=0.6),
                       "user_confirmed")
        a, _, c = career("user_confirmed", "user_confirmed", "user_confirmed")
        h = run(ctx, a, low, c)
        assert {"mapping_not_confirmed", "low_mapping_confidence"} <= set(h.records[1].exclusion_reasons)
        assert all(t.transition_status == "unconfirmed" for t in h.transitions)

    def test_inputs_without_review_status_are_unchanged(self, ctx):
        records = [emp("C", 1, "PM-APM", "2018-01", "2019-12", org="A"),
                   emp("C", 2, "ROLE-CONSULT", "2018-06", "2019-01", org="B", etype="freelance", primary=False)]
        h = run(ctx, *records)
        assert all(r.stated.review_status is None and "review_pending" not in r.exclusion_reasons for r in h.records)
        assert [r.in_primary_sequence for r in h.records] == [True, False]
        assert "review_pending" in REASON_DESCRIPTIONS
