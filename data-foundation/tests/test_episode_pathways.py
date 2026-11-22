"""Episode-aware pathways (v2.1.0): role and employer moves, continuations, integrity rules,
route validation and mode separation. Mirrors migration 20261111130000_trace_episode_pathways.sql.

Synthetic inputs prove behaviour only; they are never career evidence."""
import unittest

from _support import ROOT, context, emp, profile, tm

PREVIOUSLY_MISSED = {  # same-role employer moves that v2.0.2 stints merged away
    "SYN-018": "PM-PM", "SYN-019": "PM-PM", "SYN-025": "PM-PM", "SYN-026": "PM-PM",
    "SYN-027": "PM-PM", "SYN-029": "PM-PM", "SYN-030": "PM-PM", "SYN-032": "PM-DIR",
}


def paths(p, ctx):
    return [x["role_path"] for x in tm.career_episode_paths(p, ctx)]


def change_types(p, ctx):
    return [t["change_type"] for t in tm.episode_transitions(p, ctx)]


class ChangeTypes(unittest.TestCase):
    def test_all_six_combinations(self):
        cases = {
            ("PM-APM", "PM-PM", "internal"): "role_change",
            ("PM-PM", "PM-PM", "external"): "employer_change",
            ("PM-PM", "PM-SPM", "external"): "role_and_employer_change",
            ("PM-PM", "PM-PM", "internal"): "no_change",
            ("PM-PM", "PM-SPM", "unknown"): "role_change_employer_unknown",
            ("PM-PM", "PM-PM", "unknown"): "employer_unknown",
        }
        for (a, b, move), expected in cases.items():
            with self.subTest(a=a, b=b, move=move):
                self.assertEqual(tm.change_type(a, b, move), expected)

    def test_flags_distinguish_role_and_employer(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2018-01", "2020-12", org="A"),
                                emp("SYN-900", 2, "PM-PM", "2021-01", "ongoing", org=None)])
        t = tm.episode_transitions(p, context())[0]
        self.assertEqual((t["role_changed"], t["employer_changed"], t["change_type"]), (False, None, "employer_unknown"))
        self.assertFalse(t["is_pathway_move"])


class Scenarios(unittest.TestCase):
    def setUp(self):
        self.ctx = context()

    def test_1_role_change_within_same_employer(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-APM", "2019-01", "2021-06", org="A"),
                                emp("SYN-900", 2, "PM-PM", "2021-07", "ongoing", org="A")])
        self.assertEqual(change_types(p, self.ctx), ["role_change"])
        self.assertEqual(paths(p, self.ctx), [["PM-APM", "PM-PM"]])
        self.assertEqual(tm.episode_route_observation(p, ["PM-APM", "PM-PM"], self.ctx)["step_change_types"], ["role_change"])

    def test_2_same_role_different_employer(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2018-01", "2021-06", org="A"),
                                emp("SYN-900", 2, "PM-PM", "2021-07", "ongoing", org="B")])
        self.assertEqual(change_types(p, self.ctx), ["employer_change"])
        self.assertEqual(paths(p, self.ctx), [["PM-PM", "PM-PM"]])
        obs = tm.episode_route_observation(p, ["PM-PM", "PM-PM"], self.ctx)
        self.assertEqual((obs["step_change_types"], obs["route_months_min"], obs["is_precise"]), (["employer_change"], 42, True))
        # The v2.0.2 objects are unchanged: one merged stint, no route.
        self.assertEqual([x["role_path"] for x in tm.career_paths(p, self.ctx)], [["PM-PM"]])

    def test_3_role_and_employer_change(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2018-01", "2021-06", org="A"),
                                emp("SYN-900", 2, "PM-SPM", "2021-07", "ongoing", org="B")])
        self.assertEqual(change_types(p, self.ctx), ["role_and_employer_change"])
        self.assertEqual(paths(p, self.ctx), [["PM-PM", "PM-SPM"]])

    def test_5_multiple_same_role_employer_moves(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2016-01", "2018-12", org="A"),
                                emp("SYN-900", 2, "PM-PM", "2019-01", "2021-12", org="B"),
                                emp("SYN-900", 3, "PM-PM", "2022-01", "ongoing", org="C")])
        self.assertEqual(paths(p, self.ctx), [["PM-PM", "PM-PM", "PM-PM"]])
        obs = tm.episode_route_observation(p, ["PM-PM", "PM-PM", "PM-PM"], self.ctx)
        self.assertEqual(obs["step_change_types"], ["employer_change", "employer_change"])
        self.assertEqual(obs["route_months_min"], 72)
        # The first two-step occurrence is used once per individual.
        self.assertEqual(tm.episode_route_observation(p, ["PM-PM", "PM-PM"], self.ctx)["start_position"], 1)

    def test_destination_role_first_then_employer_change(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2019-01", "2021-12", org="A"),
                                emp("SYN-900", 2, "PM-PM", "2022-01", "ongoing", org="B")])
        self.assertIsNotNone(tm.episode_route_observation(p, ["PM-PM", "PM-PM"], self.ctx))

    def test_single_episode_is_not_a_pathway(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2019-01", "ongoing")])
        self.assertEqual(paths(p, self.ctx), [["PM-PM"]])
        self.assertEqual(tm.episode_transitions(p, self.ctx), [])
        self.assertIsNone(tm.episode_route_observation(p, ["PM-PM", "PM-PM"], self.ctx))

    def test_repeated_role_same_employer_is_a_continuation(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2018-01", "2019-12", org="A"),
                                emp("SYN-900", 2, "PM-PM", "2020-01", "2021-12", org="A"),
                                emp("SYN-900", 3, "PM-SPM", "2022-01", "ongoing", org="A")])
        self.assertEqual(change_types(p, self.ctx), ["no_change", "role_change"])
        (path,) = tm.career_episode_paths(p, self.ctx)
        self.assertEqual(path["role_path"], ["PM-PM", "PM-SPM"])
        self.assertEqual(path["stints"][0]["employment_ids"], ["SYN-900-EMP-01", "SYN-900-EMP-02"])
        self.assertIsNone(tm.episode_route_observation(p, ["PM-PM", "PM-PM"], self.ctx))

    def test_repeated_role_with_unknown_employer_is_not_a_move(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2018-01", "2020-12", org="A"),
                                emp("SYN-900", 2, "PM-PM", "2021-01", "ongoing", org=None)])
        self.assertEqual(paths(p, self.ctx), [["PM-PM"]])

    def test_repeated_role_codes_with_different_employers_are_kept(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2016-01", "2017-12", org="A"),
                                emp("SYN-900", 2, "PM-SPM", "2018-01", "2019-12", org="A"),
                                emp("SYN-900", 3, "PM-SPM", "2020-01", "2021-12", org="B"),
                                emp("SYN-900", 4, "PM-SPM", "2022-01", "ongoing", org="B")])
        (path,) = tm.career_episode_paths(p, self.ctx)
        self.assertEqual(path["role_path"], ["PM-PM", "PM-SPM", "PM-SPM"])
        self.assertEqual(path["entry_change_types"], [None, "role_change", "employer_change"])
        self.assertEqual(path["stints"][2]["employment_ids"], ["SYN-900-EMP-03", "SYN-900-EMP-04"])


class IntegrityRulesAreInherited(unittest.TestCase):
    def setUp(self):
        self.ctx = context()

    def test_overlapping_employer_move_is_unconfirmed_and_breaks_the_route(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2017-06", "2021-08", org="A"),
                                emp("SYN-900", 2, "PM-PM", "2021-07", "ongoing", org="B")])
        (t,) = tm.episode_transitions(p, self.ctx)
        self.assertEqual((t["change_type"], t["transition_status"], t["is_pathway_move"]),
                         ("employer_change", "unconfirmed", False))
        self.assertEqual(paths(p, self.ctx), [["PM-PM"], ["PM-PM"]])
        self.assertIsNone(tm.episode_route_observation(p, ["PM-PM", "PM-PM"], self.ctx))

    def test_same_year_year_only_boundary_is_not_a_move(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2015", "2018", org="A"),
                                emp("SYN-900", 2, "PM-PM", "2018", "ongoing", org="B")])
        (t,) = tm.episode_transitions(p, self.ctx)
        self.assertEqual((t["timing_relation"], t["transition_status"]), ("boundary_uncertain", "unconfirmed"))
        self.assertIsNone(tm.episode_route_observation(p, ["PM-PM", "PM-PM"], self.ctx))

    def test_year_only_move_is_confirmed_but_imprecise(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2015", "2017", org="A"),
                                emp("SYN-900", 2, "PM-PM", "2018", "ongoing", org="B")])
        obs = tm.episode_route_observation(p, ["PM-PM", "PM-PM"], self.ctx)
        self.assertFalse(obs["is_precise"])

    def test_previous_role_ongoing_is_not_a_move(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2017-06", "ongoing", org="A"),
                                emp("SYN-900", 2, "PM-PM", "2021-07", "ongoing", org="B")])
        self.assertEqual(tm.episode_transitions(p, self.ctx)[0]["transition_status"], "unconfirmed")
        self.assertIsNone(tm.episode_route_observation(p, ["PM-PM", "PM-PM"], self.ctx))

    def test_parallel_work_stays_outside_the_chain(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2017-06", "2021-06", org="A"),
                                emp("SYN-900", 2, "PM-PM", "2020-01", "2021-06", org="F", etype="freelance",
                                    primary=False, note="parallel consulting"),
                                emp("SYN-900", 3, "PM-PM", "2021-07", "ongoing", org="B")])
        ts = tm.episode_transitions(p, self.ctx)
        self.assertEqual([(t["from_employment_id"], t["to_employment_id"]) for t in ts], [("SYN-900-EMP-01", "SYN-900-EMP-03")])
        self.assertEqual(paths(p, self.ctx), [["PM-PM", "PM-PM"]])

    def test_unresolved_intermediate_is_never_bridged(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2016-07", "2019-03", org="A"),
                                emp("SYN-900", 2, "ROLE-UNKNOWN", "2019-04", "2020-12", org="B", status="unresolved", conf=0.3),
                                emp("SYN-900", 3, "PM-PM", "2021-01", "ongoing", org="C")])
        self.assertEqual(paths(p, self.ctx), [["PM-PM"], ["PM-PM"]])
        self.assertIsNone(tm.episode_route_observation(p, ["PM-PM", "PM-PM"], self.ctx))

    def test_unclassified_founder_breaks_the_chain(self):
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2016-04", "2019-12", org="A"),
                                emp("SYN-900", 2, "ROLE-ENT", "2020-01", "2021-12", org="B", etype="founder"),
                                emp("SYN-900", 3, "PM-PM", "2022-01", "ongoing", org="C")])
        self.assertIsNone(tm.episode_route_observation(p, ["PM-PM", "PM-PM"], self.ctx))


class RouteValidation(unittest.TestCase):
    def test_invalid_routes_raise_instead_of_returning_empty(self):
        ctx = context()
        p = profile("SYN-900", [emp("SYN-900", 1, "PM-PM", "2018-01", "2021-06", org="A"),
                                emp("SYN-900", 2, "PM-PM", "2021-07", "ongoing", org="B")])
        for route in (None, [], ["PM-PM"], ["PM-PM", None], ["PM-PM", "PM-NOPE"], ["ROLE-UNKNOWN", "PM-PM"], "PM-PM"):
            with self.subTest(route=route):
                with self.assertRaises(ValueError):
                    tm.episode_route_observation(p, route, ctx)
                with self.assertRaises(ValueError):
                    tm.episode_route_summary([p], route, "synthetic_test", ctx)

    def test_valid_repeated_route_is_not_collapsed(self):
        s = tm.episode_route_summary([], ["PM-PM", "PM-PM"], "synthetic_test", context())
        self.assertEqual(s["route"], ["PM-PM", "PM-PM"])
        self.assertEqual(s["supporting_profile_count"], 0)


class ModeSeparation(unittest.TestCase):
    def mover(self, pid, cls):
        return profile(pid, [emp(pid, 1, "PM-PM", "2018-01", "2020-12", org="A"),
                             emp(pid, 2, "PM-PM", "2021-01", "ongoing", org="B")], dataset_class=cls)

    def test_synthetic_mode_is_synthetic_test_only(self):
        ctx = context(min_profiles_limited=1, min_dated_profiles_for_timeline=1)
        s = tm.episode_route_summary([self.mover("SYN-901", "synthetic_test_fixture")], ["PM-PM", "PM-PM"], "synthetic_test", ctx)
        self.assertEqual((s["supporting_profile_count"], s["evidence_class"], s["timeline_display_allowed"]),
                         (1, "synthetic_test_only", False))

    def test_empirical_mode_never_reads_synthetic_and_needs_consent(self):
        people = [self.mover("SYN-901", "synthetic_test_fixture"), self.mover("REF-901", "consented_real_reference")]
        ctx = context()
        s = tm.episode_route_summary(people, ["PM-PM", "PM-PM"], "empirical", ctx, consent_active=lambda p: True)
        self.assertEqual(s["profile_ids"], ["REF-901"])
        self.assertEqual(s["evidence_class"], "insufficient")
        none = tm.episode_route_summary(people, ["PM-PM", "PM-PM"], "empirical", ctx, consent_active=lambda p: False)
        self.assertEqual(none["supporting_profile_count"], 0)


class SyntheticFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = tm.Context.load(ROOT)
        cls.profiles = {p["profile_id"]: p for p in tm.read_jsonl(ROOT / "data/synthetic/synthetic_profiles.jsonl")}

    def test_previously_missed_fixtures_are_now_pathways(self):
        for pid, role in PREVIOUSLY_MISSED.items():
            with self.subTest(pid=pid):
                p = self.profiles[pid]
                self.assertEqual([x["role_path"] for x in tm.career_paths(p, self.ctx)], [[role]])  # v2.0.2 unchanged
                obs = tm.episode_route_observation(p, [role, role], self.ctx)
                self.assertIsNotNone(obs)
                self.assertEqual(obs["step_change_types"], ["employer_change"])

    def test_episode_and_role_paths_agree_when_no_same_role_moves(self):
        for pid, p in self.profiles.items():
            if pid in PREVIOUSLY_MISSED:
                continue
            with self.subTest(pid=pid):
                ep = [x["role_path"] for x in tm.career_episode_paths(p, self.ctx)]
                rp = [x["role_path"] for x in tm.career_paths(p, self.ctx)]
                self.assertEqual(ep, rp)

    def test_every_transition_is_preserved_and_classified(self):
        for pid, p in self.profiles.items():
            with self.subTest(pid=pid):
                plain, episode = tm.transitions(p, self.ctx), tm.episode_transitions(p, self.ctx)
                self.assertEqual(len(episode), len(plain))
                for e, t in zip(episode, plain):
                    self.assertEqual({k: v for k, v in e.items() if k in t}, t)
                    self.assertIn(e["change_type"], ("role_change", "employer_change", "role_and_employer_change",
                                                     "no_change", "role_change_employer_unknown", "employer_unknown"))

    def test_synthetic_cohort_never_produces_empirical_evidence(self):
        s = tm.episode_route_summary(list(self.profiles.values()), ["PM-PM", "PM-PM"], "empirical", self.ctx,
                                     consent_active=lambda p: True)
        self.assertEqual(s["supporting_profile_count"], 0)
        syn = tm.episode_route_summary(list(self.profiles.values()), ["PM-PM", "PM-PM"], "synthetic_test", self.ctx)
        self.assertEqual(sorted(syn["profile_ids"]), sorted(p for p, r in PREVIOUSLY_MISSED.items() if r == "PM-PM"))


if __name__ == "__main__":
    unittest.main()
