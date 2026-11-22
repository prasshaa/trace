"""Career sequences, transitions, route validation and evidence classes.

Covers the scenarios required for v2: same-month internal promotions, overlapping jobs,
missing intermediate roles, ambiguous titles and incomplete dates, plus internships,
parallel activity, per-individual route validation and synthetic/empirical separation."""
import unittest

from _support import ROOT, context, emp, profile, tm


def only(ts, frm, to):
    hits = [t for t in ts if t["from_role_id"] == frm and t["to_role_id"] == to]
    if len(hits) != 1:
        raise AssertionError(f"expected exactly one {frm}->{to}, got {len(hits)}: {ts}")
    return hits[0]


class SameMonthInternalPromotion(unittest.TestCase):
    def test_promotion_in_the_same_month_is_kept_and_confirmed(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-APM", "2020-07", "2022-03"),
                                emp("SYN-900", 2, "PM-PM", "2022-03", "2024-09"),
                                emp("SYN-900", 3, "PM-SPM", "2024-09", "ongoing")])
        ts = tm.transitions(p, context())
        self.assertEqual(len(ts), 2)
        for t in ts:
            self.assertEqual(t["transition_status"], "confirmed")
            self.assertEqual(t["timing_relation"], "same_month_boundary")
            self.assertEqual(t["move_type"], "internal")
            self.assertEqual((t["gap_months_min"], t["gap_months_max"]), (0, 0))
            self.assertGreater(t["seniority_rank_change"], 0)

    def test_external_move_is_labelled_external(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2019-01", "2022-08", org="A"),
                                emp("SYN-900", 2, "PM-SPM", "2022-09", "ongoing", org="B")])
        self.assertEqual(tm.transitions(p, context())[0]["move_type"], "external")

    def test_unknown_employer_gives_unknown_move_type(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2019-01", "2022-08", org=None),
                                emp("SYN-900", 2, "PM-SPM", "2022-09", "ongoing", org="B")])
        self.assertEqual(tm.transitions(p, context())[0]["move_type"], "unknown")


class OverlappingJobs(unittest.TestCase):
    def test_multi_month_overlap_is_unconfirmed(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "ROLE-SWE", "2017-06", "2021-09"),
                                emp("SYN-900", 2, "PM-APM", "2021-07", "ongoing", org="B")])
        t = tm.transitions(p, context())[0]
        self.assertEqual(t["timing_relation"], "overlapping")
        self.assertEqual(t["transition_status"], "unconfirmed")
        self.assertIn("timing:overlapping", t["unconfirmed_reasons"])

    def test_one_month_overlap_is_not_auto_confirmed(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "ROLE-SWE", "2017-06", "2021-08"),
                                emp("SYN-900", 2, "PM-APM", "2021-07", "ongoing", org="B")])
        t = tm.transitions(p, context())[0]
        self.assertEqual((t["timing_relation"], t["transition_status"]), ("overlapping", "unconfirmed"))
        self.assertIn("timing:overlapping", t["unconfirmed_reasons"])

    def test_retired_tolerance_setting_has_no_effect(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "ROLE-SWE", "2017-06", "2021-08"),
                                emp("SYN-900", 2, "PM-APM", "2021-07", "ongoing", org="B")])
        t = tm.transitions(p, context(max_boundary_overlap_months=6))[0]
        self.assertEqual(t["transition_status"], "unconfirmed")

    def test_overlap_resolved_by_explicit_review_is_confirmed(self):
        # After explicit review the earlier role's end date is corrected; only then is it confirmed.
        p = profile("SYN-900", [emp("SYN-900", 1, "ROLE-SWE", "2017-06", "2021-07"),
                                emp("SYN-900", 2, "PM-APM", "2021-07", "ongoing", org="B")])
        t = tm.transitions(p, context())[0]
        self.assertEqual((t["timing_relation"], t["transition_status"]), ("same_month_boundary", "confirmed"))

    def test_year_precision_definite_overlap_is_unconfirmed(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "ROLE-SWE", "2015", "2019"),
                                emp("SYN-900", 2, "PM-APM", "2018-03", "ongoing", org="B")])
        self.assertEqual(tm.transitions(p, context())[0]["timing_relation"], "overlapping")

    def test_parallel_freelance_is_outside_the_chain(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "ROLE-SWE", "2017-06", "2021-06"),
                                emp("SYN-900", 2, "ROLE-CONSULT", "2020-01", "2021-06", org="F", etype="freelance",
                                    primary=False, note="parallel freelance"),
                                emp("SYN-900", 3, "PM-APM", "2021-07", "ongoing", org="B")])
        ts = tm.transitions(p, context())
        self.assertEqual([(t["from_role_id"], t["to_role_id"]) for t in ts], [("ROLE-SWE", "PM-APM")])
        self.assertEqual(ts[0]["transition_status"], "confirmed")

    def test_role_after_an_ongoing_primary_role_is_unconfirmed(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "ROLE-SWE", "2017-06", "ongoing"),
                                emp("SYN-900", 2, "PM-APM", "2021-07", "ongoing", org="B")])
        t = tm.transitions(p, context())[0]
        self.assertEqual(t["timing_relation"], "previous_role_ongoing")
        self.assertEqual(t["transition_status"], "unconfirmed")


class MissingIntermediateRoles(unittest.TestCase):
    def setUp(self):
        self.p = profile("SYN-900", [
            emp("SYN-900", 1, "ROLE-SWE", "2016-07", "2019-03"),
            emp("SYN-900", 2, "ROLE-UNKNOWN", "2019-04", "2020-12", org="B", status="unresolved", conf=0.3, title="Associate"),
            emp("SYN-900", 3, "PM-PM", "2021-01", "ongoing", org="C")])
        self.ctx = context()

    def test_unknown_role_breaks_the_chain_instead_of_bridging_it(self):
        ts = tm.transitions(self.p, self.ctx)
        pairs = [(t["from_role_id"], t["to_role_id"]) for t in ts]
        self.assertEqual(pairs, [("ROLE-SWE", "ROLE-UNKNOWN"), ("ROLE-UNKNOWN", "PM-PM")])
        self.assertNotIn(("ROLE-SWE", "PM-PM"), pairs)
        self.assertTrue(all(t["transition_status"] == "unconfirmed" for t in ts))

    def test_route_across_the_unknown_role_is_not_supported(self):
        self.assertIsNone(tm.route_observation(self.p, ["ROLE-SWE", "PM-PM"], self.ctx))
        self.assertEqual([x["role_path"] for x in tm.career_paths(self.p, self.ctx)], [["ROLE-SWE"], ["PM-PM"]])

    def test_undated_middle_role_also_breaks_the_chain(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "ROLE-PAN", "2018-01", "2019-12"),
                                emp("SYN-900", 2, "ROLE-DA", None, None, org="B"),
                                emp("SYN-900", 3, "PM-APM", "2020-02", "ongoing", org="C")])
        self.assertIsNone(tm.route_observation(p, ["ROLE-PAN", "PM-APM"], self.ctx))


class AmbiguousTitles(unittest.TestCase):
    def test_product_owner_awaiting_review_is_not_confirmed(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "ROLE-QA", "2016-01", "2019-07"),
                                emp("SYN-900", 2, "ROLE-OWNER", "2019-08", "2022-01", org="B", status="needs_review", conf=0.6),
                                emp("SYN-900", 3, "PM-PM", "2022-02", "ongoing", org="C")])
        ts = tm.transitions(p, context())
        self.assertTrue(all(t["transition_status"] == "unconfirmed" for t in ts))
        self.assertIn("to:mapping_not_confirmed", ts[0]["unconfirmed_reasons"])
        self.assertIn("from:low_mapping_confidence", ts[1]["unconfirmed_reasons"])

    def test_reviewed_product_owner_counts_as_its_own_family_not_pm(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "ROLE-OWNER", "2019-01", "2022-02"),
                                emp("SYN-900", 2, "PM-PM", "2022-03", "ongoing", org="B")])
        t = tm.transitions(p, context())[0]
        self.assertEqual(t["transition_status"], "confirmed")
        self.assertEqual((t["from_role_family"], t["to_role_family"]), ("product_ownership", "product_management"))

    def test_high_confidence_but_unconfirmed_status_is_still_excluded(self):
        rec = tm.classify_employment(emp("SYN-900", 1, "PM-PM", "2020-01", "2021-01", status="needs_review", conf=0.95), context())
        self.assertEqual(rec["exclusion_reasons"], ["mapping_not_confirmed"])

    def test_auto_mapping_below_threshold_is_excluded(self):
        rec = tm.classify_employment(emp("SYN-900", 1, "PM-PM", "2020-01", "2021-01", status="auto_high_confidence", conf=0.7), context())
        self.assertEqual(rec["exclusion_reasons"], ["low_mapping_confidence"])


class IncompleteDates(unittest.TestCase):
    def test_unknown_start_is_excluded_with_reason(self):
        rec = tm.classify_employment(emp("SYN-900", 1, "ROLE-DA", None, None), context())
        self.assertIn("unknown_start_date", rec["exclusion_reasons"])

    def test_same_year_only_boundary_is_unconfirmed_but_records_are_kept(self):
        """Regression: year-only end 2018 then year-only start 2018 -> order cannot be established."""
        p = profile("SYN-900", [emp("SYN-900", 1, "ROLE-BA", "2015", "2018"),
                                emp("SYN-900", 2, "PM-APM", "2018", "2020-03", org="B"),
                                emp("SYN-900", 3, "PM-PM", "2020-04", "ongoing", org="B")])
        ctx = context()
        seq_ids = [r["employment_id"] for r in tm.career_sequence(p, ctx)]
        self.assertEqual(seq_ids, ["SYN-900-EMP-01", "SYN-900-EMP-02", "SYN-900-EMP-03"])  # nothing discarded
        ts = tm.transitions(p, ctx)
        self.assertEqual((ts[0]["timing_relation"], ts[0]["transition_status"]), ("boundary_uncertain", "unconfirmed"))
        self.assertIn("timing:boundary_uncertain", ts[0]["unconfirmed_reasons"])
        self.assertEqual(ts[1]["transition_status"], "confirmed")       # the rest of the profile is unaffected
        self.assertIsNone(tm.route_observation(p, ["ROLE-BA", "PM-APM"], ctx))
        self.assertIsNotNone(tm.route_observation(p, ["PM-APM", "PM-PM"], ctx))

    def test_earlier_year_only_end_confirms_order_but_not_precision(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "ROLE-BA", "2015", "2017"),
                                emp("SYN-900", 2, "PM-APM", "2018", "2020-03", org="B"),
                                emp("SYN-900", 3, "PM-PM", "2020-04", "ongoing", org="B")])
        ctx = context()
        t = tm.transitions(p, ctx)[0]
        self.assertEqual((t["timing_relation"], t["transition_status"]), ("sequential", "confirmed"))
        self.assertFalse(t["gap_is_precise"])
        self.assertEqual((t["gap_months_min"], t["gap_months_max"]), (0, 22))
        obs = tm.route_observation(p, ["ROLE-BA", "PM-APM", "PM-PM"], ctx)
        self.assertFalse(obs["is_precise"])                                 # kept out of percentiles
        self.assertEqual((obs["route_months_min"], obs["route_months_max"]), (52, 63))

    def test_year_only_observations_never_reach_percentiles(self):
        ctx = context(min_dated_profiles_for_timeline=1, min_profiles_limited=1)
        people = [profile(f"SYN-9{i:02d}", [emp(f"SYN-9{i:02d}", 1, "ROLE-BA", "2015", "2018"),
                                             emp(f"SYN-9{i:02d}", 2, "PM-APM", "2019-01", "ongoing", org="B")]) for i in range(3)]
        s = tm.route_summary(people, ["ROLE-BA", "PM-APM"], "synthetic_test", ctx)
        self.assertEqual((s["supporting_profile_count"], s["dated_profile_count"]), (3, 0))
        self.assertIsNone(s["median_months"])
        self.assertIsNotNone(s["coarse_min_months"])

    def test_sequence_that_contradicts_dates_is_flagged(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2021-01", "2022-01"),
                                emp("SYN-900", 2, "PM-SPM", "2019-01", "2020-01", org="B")])
        self.assertEqual(tm.transitions(p, context())[0]["timing_relation"], "sequence_order_conflict")


class Internships(unittest.TestCase):
    def test_internship_is_excluded_from_confirmed_transitions(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-INTERN", "2018-05", "2018-07", etype="internship"),
                                emp("SYN-900", 2, "PM-APM", "2018-08", "ongoing", org="B")])
        t = tm.transitions(p, context())[0]
        self.assertEqual(t["transition_status"], "unconfirmed")
        self.assertIn("from:not_full_time_experience", t["unconfirmed_reasons"])

    def test_explicit_reclassification_includes_it(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-INTERN", "2019-07", "2020-06", etype="internship",
                                    counts=True, note="12-month full-time programme"),
                                emp("SYN-900", 2, "PM-APM", "2020-07", "ongoing")])
        self.assertEqual(tm.transitions(p, context())[0]["transition_status"], "confirmed")

    def test_founder_role_does_not_count_by_default(self):
        rec = tm.classify_employment(emp("SYN-900", 1, "ROLE-ENT", "2020-01", "2021-12", etype="founder"), context())
        self.assertIn("not_full_time_experience", rec["exclusion_reasons"])
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2016-04", "2019-12"),
                                emp("SYN-900", 2, "ROLE-ENT", "2020-01", "2021-12", org="B", etype="founder"),
                                emp("SYN-900", 3, "PM-LEAD", "2022-01", "ongoing", org="C")])
        self.assertTrue(all(t["transition_status"] == "unconfirmed" for t in tm.transitions(p, context())))
        self.assertIsNone(tm.route_observation(p, ["PM-PM", "PM-LEAD"], context()))

    def test_founder_role_counts_only_with_explicit_documented_classification(self):
        rec = tm.classify_employment(emp("SYN-900", 1, "ROLE-ENT", "2020-01", "2021-12", etype="founder",
                                         counts=True, note="Full-time founder role; reviewed."), context())
        self.assertTrue(rec["is_transition_eligible"])


class RoutesAreValidatedPerIndividual(unittest.TestCase):
    def test_halves_of_a_route_from_different_people_are_not_stitched(self):
        a = profile("SYN-901", [emp("SYN-901", 1, "ROLE-SWE", "2016-01", "2019-12"),
                                emp("SYN-901", 2, "PM-APM", "2020-01", "ongoing", org="B")])
        b = profile("SYN-902", [emp("SYN-902", 1, "PM-APM", "2018-01", "2020-12"),
                                emp("SYN-902", 2, "PM-PM", "2021-01", "ongoing")])
        s = tm.route_summary([a, b], ["ROLE-SWE", "PM-APM", "PM-PM"], "synthetic_test", context())
        self.assertEqual(s["supporting_profile_count"], 0)

    def test_repeated_role_collapses_into_one_stint(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2018-01", "2020-12", org="A"),
                                emp("SYN-900", 2, "PM-PM", "2021-01", "2022-12", org="B"),
                                emp("SYN-900", 3, "PM-SPM", "2023-01", "ongoing", org="B")])
        obs = tm.route_observation(p, ["PM-PM", "PM-SPM"], context())
        self.assertEqual(obs["route_months_min"], 60)  # first PM start (2018-01) to SPM start (2023-01)
        self.assertTrue(obs["is_precise"])

    def test_each_individual_is_counted_once(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2016-01", "2017-12"),
                                emp("SYN-900", 2, "PM-SPM", "2018-01", "2019-12"),
                                emp("SYN-900", 3, "PM-PM", "2020-01", "2021-12", org="B"),
                                emp("SYN-900", 4, "PM-SPM", "2022-01", "ongoing", org="B")])
        s = tm.route_summary([p], ["PM-PM", "PM-SPM"], "synthetic_test", context())
        self.assertEqual(s["supporting_profile_count"], 1)


class EvidenceClassesAndDataModes(unittest.TestCase):
    def people(self, n, cls="synthetic_test_fixture"):
        return [profile(f"SYN-{i:03d}" if cls == "synthetic_test_fixture" else f"REF-{i:03d}",
                        [emp(f"P{i}", 1, "PM-PM", "2018-01", "2020-12"), emp(f"P{i}", 2, "PM-SPM", "2021-01", "ongoing")],
                        dataset_class=cls) for i in range(n)]

    def test_synthetic_mode_is_always_synthetic_test_only_without_timeline(self):
        ctx = context(min_profiles_strong=1, min_profiles_moderate=1, min_profiles_limited=1, min_dated_profiles_for_timeline=1)
        s = tm.route_summary(self.people(50), ["PM-PM", "PM-SPM"], "synthetic_test", ctx)
        self.assertEqual(s["evidence_class"], "synthetic_test_only")
        self.assertFalse(s["timeline_display_allowed"])

    def test_synthetic_profiles_never_enter_empirical_mode_even_if_consent_says_yes(self):
        s = tm.route_summary(self.people(50), ["PM-PM", "PM-SPM"], "empirical", context(), consent_active=lambda p: True)
        self.assertEqual(s["supporting_profile_count"], 0)
        self.assertEqual(s["evidence_class"], "insufficient")

    def test_real_profiles_need_active_consent(self):
        real = self.people(50, "consented_real_reference")
        self.assertEqual(tm.route_summary(real, ["PM-PM", "PM-SPM"], "empirical", context(), consent_active=lambda p: False)["supporting_profile_count"], 0)
        self.assertEqual(tm.route_summary(real, ["PM-PM", "PM-SPM"], "empirical", context())["supporting_profile_count"], 0)

    def test_thresholds_drive_evidence_class_and_timeline(self):
        ctx = context()
        for n, cls, timeline in ((4, "insufficient", False), (5, "limited", False), (15, "moderate", False),
                                 (20, "moderate", True), (40, "strong", True)):
            with self.subTest(n=n):
                s = tm.route_summary(self.people(n, "consented_real_reference"), ["PM-PM", "PM-SPM"], "empirical", ctx,
                                     consent_active=lambda p: True)
                self.assertEqual(s["evidence_class"], cls)
                self.assertEqual(s["timeline_display_allowed"], timeline)
                self.assertEqual(s["median_months"] is not None, timeline)
                if timeline:
                    self.assertEqual(s["median_months"], 36)

    def test_demo_mode_respects_demo_flag(self):
        people = self.people(3)
        people[0]["synthetic_demo_allowed"] = False
        s = tm.route_summary(people, ["PM-PM", "PM-SPM"], "synthetic_demo", context())
        self.assertEqual(s["supporting_profile_count"], 2)


class SyntheticFixtureScenarios(unittest.TestCase):
    """End-to-end expectations for the edge-case fixtures shipped in data/synthetic."""

    @classmethod
    def setUpClass(cls):
        cls.ctx = tm.Context.load(ROOT)
        cls.profiles = {p["profile_id"]: p for p in tm.read_jsonl(ROOT / "data/synthetic/synthetic_profiles.jsonl")}

    def ts(self, pid):
        return tm.transitions(self.profiles[pid], self.ctx)

    def test_syn_033_same_month_promotions(self):
        ts = self.ts("SYN-033")
        self.assertEqual([t["timing_relation"] for t in ts], ["same_month_boundary"] * 2)
        self.assertTrue(all(t["transition_status"] == "confirmed" and t["move_type"] == "internal" for t in ts))

    def test_syn_034_overlap_and_parallel_freelance(self):
        ts = self.ts("SYN-034")
        self.assertEqual(only(ts, "ROLE-SWE", "PM-APM")["timing_relation"], "overlapping")
        self.assertEqual(only(ts, "PM-APM", "PM-PM")["transition_status"], "confirmed")
        self.assertFalse(any(t["from_role_id"] == "ROLE-CONSULT" or t["to_role_id"] == "ROLE-CONSULT" for t in ts))

    def test_syn_035_unresolved_intermediate(self):
        self.assertIsNone(tm.route_observation(self.profiles["SYN-035"], ["ROLE-SWE", "PM-PM"], self.ctx))

    def test_syn_036_product_owner_needs_review(self):
        self.assertTrue(all(t["transition_status"] == "unconfirmed" for t in self.ts("SYN-036")))

    def test_syn_037_year_precision(self):
        ts = self.ts("SYN-037")
        self.assertEqual(len(ts), 2)                                       # all records preserved
        self.assertEqual(only(ts, "ROLE-BA", "PM-APM")["transition_status"], "unconfirmed")  # same-year boundary
        self.assertEqual(only(ts, "PM-APM", "PM-PM")["transition_status"], "confirmed")
        self.assertIsNone(tm.route_observation(self.profiles["SYN-037"], ["ROLE-BA", "PM-APM", "PM-PM"], self.ctx))
        self.assertFalse(tm.route_observation(self.profiles["SYN-037"], ["PM-APM", "PM-PM"], self.ctx)["is_precise"])

    def test_syn_038_unknown_dates_and_internship(self):
        paths = [x["role_path"] for x in tm.career_paths(self.profiles["SYN-038"], self.ctx)]
        self.assertEqual(paths, [["ROLE-PAN", "PM-APM"]])

    def test_syn_039_career_break_gap(self):
        t = only(self.ts("SYN-039"), "ROLE-SWE", "PM-APM")
        self.assertEqual((t["gap_months_min"], t["transition_status"]), (24, "confirmed"))
        self.assertEqual(self.profiles["SYN-039"]["career_breaks"][0]["break_reason"], "education")

    def test_syn_040_explicitly_classified_founder_lead_principal(self):
        founder = next(e for e in self.profiles["SYN-040"]["employments"] if e["employment_type"] == "founder")
        self.assertTrue(founder["counts_as_full_time_experience"])
        self.assertTrue(founder["classification_note"])
        paths = [x["role_path"] for x in tm.career_paths(self.profiles["SYN-040"], self.ctx)]
        self.assertEqual(paths, [["PM-PM", "ROLE-ENT", "PM-LEAD", "PM-PRINCIPAL"]])

    def test_syn_041_reclassified_internship(self):
        self.assertEqual(only(self.ts("SYN-041"), "PM-INTERN", "PM-APM")["transition_status"], "confirmed")

    def test_synthetic_cohort_never_produces_empirical_evidence(self):
        s = tm.route_summary(list(self.profiles.values()), ["PM-PM", "PM-SPM"], "empirical", self.ctx, consent_active=lambda p: True)
        self.assertEqual(s["supporting_profile_count"], 0)


if __name__ == "__main__":
    unittest.main()
