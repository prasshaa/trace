"""Date precision, bounds, inclusive-month semantics and gap arithmetic."""
import unittest

from _support import context, emp, profile, tm


class PartialDates(unittest.TestCase):
    def test_month_precision_is_a_single_month(self):
        self.assertEqual(tm.bounds("2021-06", "month"), (2021 * 12 + 5, 2021 * 12 + 5))

    def test_year_precision_spans_january_to_december(self):
        lo, hi = tm.bounds("2019", "year")
        self.assertEqual((tm.month_label(lo), tm.month_label(hi)), ("2019-01", "2019-12"))

    def test_unknown_and_ongoing_have_no_bounds(self):
        self.assertEqual(tm.bounds(None, "unknown"), (None, None))
        self.assertEqual(tm.bounds(None, "ongoing"), (None, None))

    def test_precision_must_match_value(self):
        for value, precision in (("2021", "month"), ("2021-06", "year"), ("2021-13", "month"),
                                 ("2021-06", "unknown"), (None, "month"), ("21-06", "month")):
            with self.subTest(value=value, precision=precision):
                with self.assertRaises(ValueError):
                    tm.bounds(value, precision)


class InclusiveMonths(unittest.TestCase):
    def setUp(self):
        self.ctx = context()

    def classify(self, start, end):
        return tm.classify_employment(emp("SYN-900", 1, "PM-PM", start, end), self.ctx)

    def test_tenure_counts_first_and_last_month(self):
        # June 2021 .. March 2023 inclusive = 7 + 12 + 3 months
        self.assertEqual(tm.tenure_months(self.classify("2021-06", "2023-03")), (22, 22, False))

    def test_single_month_role_lasts_one_month(self):
        self.assertEqual(tm.tenure_months(self.classify("2021-06", "2021-06")), (1, 1, False))

    def test_year_only_tenure_is_a_range_not_a_point(self):
        mn, mx, _ = tm.tenure_months(self.classify("2019", "2020"))
        self.assertEqual((mn, mx), (2, 24))

    def test_ongoing_tenure_is_censored_at_resume_as_of(self):
        rec = self.classify("2023-07", "ongoing")
        self.assertEqual(tm.tenure_months(rec, tm.bounds("2026-11", "month")), (41, 41, True))
        self.assertEqual(tm.tenure_months(rec), (None, None, True))


class Gaps(unittest.TestCase):
    def setUp(self):
        self.ctx = context()

    def relation(self, a_end, b_start, a_start="2015-01"):
        seq = tm.career_sequence(profile("SYN-900", [emp("SYN-900", 1, "ROLE-SWE", a_start, a_end),
                                                     emp("SYN-900", 2, "PM-APM", b_start, "ongoing", org="B")]), self.ctx)
        return tm.timing_relation(seq[0], seq[1], self.ctx)

    def test_next_month_start_is_a_zero_gap(self):
        self.assertEqual(self.relation("2021-06", "2021-07"), ("sequential", 0, 0))

    def test_real_gap_counts_whole_months_between(self):
        self.assertEqual(self.relation("2021-06", "2021-09"), ("sequential", 2, 2))

    def test_same_month_boundary_is_a_zero_gap_not_dropped(self):
        self.assertEqual(self.relation("2022-03", "2022-03"), ("same_month_boundary", 0, 0))

    def test_one_month_overlap_is_overlapping_not_a_handover(self):
        self.assertEqual(self.relation("2021-08", "2021-07"), ("overlapping", None, None))

    def test_longer_overlap_is_overlapping(self):
        self.assertEqual(self.relation("2021-09", "2021-07")[0], "overlapping")

    def test_year_precision_boundary_is_uncertain_with_bounded_gap(self):
        self.assertEqual(self.relation("2018", "2018", a_start="2015"), ("boundary_uncertain", 0, 10))

    def test_year_precision_can_still_be_definitely_sequential(self):
        self.assertEqual(self.relation("2018", "2020-02", a_start="2015"), ("sequential", 13, 24))


class Percentiles(unittest.TestCase):
    def test_matches_postgres_percentile_cont(self):
        self.assertEqual(tm.percentile_cont([1, 2, 3, 4], 0.25), 1.75)
        self.assertEqual(tm.percentile_cont([1, 2, 3, 4], 0.5), 2.5)
        self.assertEqual(tm.percentile_cont([10], 0.75), 10)
        self.assertIsNone(tm.percentile_cont([], 0.5))


if __name__ == "__main__":
    unittest.main()
