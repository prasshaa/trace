"""Regression tests against the restricted uploaded-resume fixtures, when present locally.

Skipped automatically when local_only/ is absent (for example in a shared review ZIP or CI).
Expectations are derived from the data at run time, so this shared file contains no fixture
ids or resume content."""
import unittest

from _support import ROOT, tm

LOCAL = ROOT / "local_only/uploaded_resume_fixtures/uploaded_resume_fixtures.jsonl"


@unittest.skipUnless(LOCAL.exists(), "restricted local fixtures not present")
class RestrictedFixtureRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = tm.Context.load(ROOT)
        cls.profiles = tm.read_jsonl(LOCAL)

    def consecutive_primary_pairs(self, p):
        seq = sorted((e for e in p["employments"] if e["primary_career_sequence"]), key=lambda e: e["sequence_index"])
        return list(zip(seq, seq[1:]))

    def test_every_consecutive_primary_pair_is_reported(self):
        """v1 silently dropped same-boundary pairs; v2 must report every consecutive pair."""
        boundary_pairs = 0
        for p in self.profiles:
            got = {(t["from_employment_id"], t["to_employment_id"]) for t in tm.transitions(p, self.ctx)}
            for a, b in self.consecutive_primary_pairs(p):
                with self.subTest(pair=(a["employment_id"], b["employment_id"])):
                    self.assertIn((a["employment_id"], b["employment_id"]), got)
                if a.get("end") and b.get("start") and a["end"] == b["start"]:
                    boundary_pairs += 1
        self.assertGreater(boundary_pairs, 0, "fixtures should exercise same-boundary transitions")

    def test_same_month_pairs_are_classified_and_year_pairs_are_uncertain(self):
        for p in self.profiles:
            ts = {(t["from_employment_id"], t["to_employment_id"]): t for t in tm.transitions(p, self.ctx)}
            for a, b in self.consecutive_primary_pairs(p):
                if not (a.get("end") and a["end"] == b.get("start")):
                    continue
                expected = "same_month_boundary" if a["end_precision"] == "month" else "boundary_uncertain"
                with self.subTest(pair=(a["employment_id"], b["employment_id"])):
                    self.assertEqual(ts[(a["employment_id"], b["employment_id"])]["timing_relation"], expected)

    def test_parallel_records_never_appear_in_transitions(self):
        for p in self.profiles:
            parallel = {e["employment_id"] for e in p["employments"] if not e["primary_career_sequence"]}
            for t in tm.transitions(p, self.ctx):
                self.assertNotIn(t["from_employment_id"], parallel)
                self.assertNotIn(t["to_employment_id"], parallel)

    def test_unresolved_unreviewed_and_undated_records_never_confirm(self):
        for p in self.profiles:
            blocked = {e["employment_id"] for e in p["employments"]
                       if e["role_id"] == "ROLE-UNKNOWN" or e["mapping_status"] in ("needs_review", "unresolved")
                       or e["start_precision"] == "unknown"}
            for t in tm.transitions(p, self.ctx):
                if t["from_employment_id"] in blocked or t["to_employment_id"] in blocked:
                    self.assertEqual(t["transition_status"], "unconfirmed")

    def test_no_overlap_is_ever_confirmed(self):
        for p in self.profiles:
            for t in tm.transitions(p, self.ctx):
                if t["timing_relation"] == "overlapping":
                    self.assertEqual(t["transition_status"], "unconfirmed")

    def test_uploaded_fixtures_never_enter_any_cohort(self):
        for mode in ("empirical", "synthetic_test", "synthetic_demo"):
            with self.subTest(mode=mode):
                s = tm.route_summary(self.profiles, ["PM-APM", "PM-PM"], mode, self.ctx, consent_active=lambda p: True)
                self.assertEqual(s["supporting_profile_count"], 0)


if __name__ == "__main__":
    unittest.main()
