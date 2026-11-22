"""The validator passes on the package and catches each class of defect it is meant to catch.

Each mutation test copies the shared package (without local_only/) to a temporary directory,
injects one defect and asserts that the expected check fails."""
import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from _support import ROOT

import validate_seed_data as vsd  # noqa: E402  (scripts/ is on sys.path via _support)

JSONL = "data/synthetic/synthetic_profiles.jsonl"


class PackagePasses(unittest.TestCase):
    def test_shared_package_passes_every_check(self):
        rep = vsd.run_all(ROOT, include_local=False)
        self.assertEqual([(n, e[:3]) for n, e, _ in rep.failed], [])

    @unittest.skipUnless((ROOT / "local_only/uploaded_resume_fixtures/uploaded_resume_fixtures.jsonl").exists(),
                         "restricted local fixtures not present")
    def test_restricted_local_fixtures_pass_structural_checks(self):
        rep = vsd.run_all(ROOT, include_local=True)
        self.assertEqual([(n, e[:3]) for n, e, _ in rep.failed], [])


class Mutations(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="trace-validate-"))
        self.root = self.tmp / "pkg"
        shutil.copytree(ROOT, self.root, ignore=shutil.ignore_patterns("local_only", "__pycache__", ".DS_Store"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers
    def profiles(self):
        return [json.loads(l) for l in (self.root / JSONL).read_text(encoding="utf-8").splitlines() if l.strip()]

    def save(self, profiles):
        (self.root / JSONL).write_text("".join(json.dumps(p) + "\n" for p in profiles), encoding="utf-8")

    def mutate_profile(self, pid, fn):
        ps = self.profiles()
        fn(next(p for p in ps if p["profile_id"] == pid))
        self.save(ps)

    def edit_csv(self, rel, fn):
        path = self.root / rel
        with path.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        fields = list(rows[0].keys())
        rows = fn(rows) or rows
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)

    def assertFails(self, check, contains=None):
        errs = vsd.run_all(self.root, include_local=False).errors_for(check)
        self.assertTrue(errs, f"{check} unexpectedly passed")
        if contains:
            self.assertTrue(any(contains in e for e in errs), f"{contains!r} not in {errs[:5]}")

    # -- v1 defects
    def test_character_split_evidence_from_v1_is_caught(self):
        def split(p):
            text = p["evidence"][-1]["text"]
            emp_id = p["evidence"][-1]["employment_id"]
            p["evidence"] = p["evidence"][:-1] + [
                {**p["evidence"][-1], "evidence_id": f"{emp_id}-E{i:02d}", "text": ch} for i, ch in enumerate(text, 1)]
        self.mutate_profile("SYN-025", split)
        self.assertFails("synthetic:profile_relationships", "character-split")

    def test_stored_role_family_is_rejected(self):
        self.mutate_profile("SYN-001", lambda p: p["employments"][0].update(role_family="product_management"))
        self.assertFails("synthetic:role_taxonomy_and_seniority", "derived from role_id")

    def test_v1_consent_flags_on_profiles_are_rejected(self):
        self.mutate_profile("SYN-001", lambda p: p.update(shared_analytics_consent=False))
        self.assertFails("synthetic:schema_and_identifiers", "consent lives in consent events")

    # -- relationships
    def test_evidence_attached_to_another_profiles_employment(self):
        self.mutate_profile("SYN-001", lambda p: p["evidence"][0].update(employment_id="SYN-002-EMP-01"))
        self.assertFails("synthetic:profile_relationships", "does not belong to SYN-001")

    def test_employment_with_undefined_employer(self):
        self.mutate_profile("SYN-001", lambda p: p["employments"][0].update(employer_id="SYN-001-ORG-Z"))
        self.assertFails("synthetic:profile_relationships", "is not defined")

    def test_unknown_skill(self):
        self.mutate_profile("SYN-001", lambda p: p["skills"][0].update(skill_id="SKL-DOES-NOT-EXIST"))
        self.assertFails("synthetic:profile_relationships", "unknown skill")

    # -- taxonomy and seniority
    def test_unknown_role(self):
        self.mutate_profile("SYN-001", lambda p: p["employments"][0].update(role_id="ROLE-NOPE"))
        self.assertFails("synthetic:role_taxonomy_and_seniority", "unknown role_id")

    def test_unknown_seniority(self):
        self.mutate_profile("SYN-001", lambda p: p["employments"][0].update(seniority_code="ninja"))
        self.assertFails("synthetic:role_taxonomy_and_seniority", "unknown seniority_code")

    def test_context_required_title_cannot_be_auto_confirmed(self):
        def f(p):
            p["employments"][0].update(mapping_status="auto_high_confidence", mapping_confidence=0.9)
        self.mutate_profile("SYN-024", f)  # "Product Owner"
        self.assertFails("synthetic:role_taxonomy_and_seniority", "context-required alias")

    def test_unresolved_mapping_must_use_role_unknown(self):
        self.mutate_profile("SYN-001", lambda p: p["employments"][0].update(mapping_status="unresolved"))
        self.assertFails("synthetic:role_taxonomy_and_seniority", "ROLE-UNKNOWN")

    def test_ambiguous_alias_must_be_context_required(self):
        def f(rows):
            for r in rows:
                if r["alias_id"] == "ALS-0036":
                    r["context_required"] = "false"
        self.edit_csv("data/taxonomy/role_aliases.csv", f)
        self.assertFails("taxonomy_and_aliases", "context_required")

    def test_role_with_unknown_family(self):
        self.edit_csv("data/taxonomy/role_taxonomy.csv", lambda rows: rows[0].update(role_family="astrology"))
        self.assertFails("taxonomy_and_aliases", "unknown role_family")

    def test_unclassified_skill(self):
        self.edit_csv("data/skills/skills.csv", lambda rows: rows[0].update(skill_family="unclassified"))
        self.assertFails("skills_vocabulary", "unclassified")

    def test_primary_founder_without_explicit_classification(self):
        def f(p):
            e = next(x for x in p["employments"] if x["employment_type"] == "founder")
            e.update(counts_as_full_time_experience=None, classification_note=None)
        self.mutate_profile("SYN-040", f)
        self.assertFails("synthetic:role_taxonomy_and_seniority", "explicit counts_as_full_time_experience")

    def test_retired_overlap_tolerance_setting_is_rejected(self):
        def f(rows):
            rows.append({**rows[0], "setting_key": "max_boundary_overlap_months", "value_numeric": "1"})
            return rows
        self.edit_csv("data/config/analytics_settings.csv", f)
        self.assertFails("analytics_settings_and_sources", "retired")

    # -- dates
    def test_end_before_start(self):
        self.mutate_profile("SYN-001", lambda p: p["employments"][0].update(end="2010-01"))
        self.assertFails("synthetic:date_integrity", "end is before start")

    def test_ongoing_start_is_invalid(self):
        self.mutate_profile("SYN-001", lambda p: p["employments"][0].update(start=None, start_precision="ongoing"))
        self.assertFails("synthetic:date_integrity", "only valid for end dates")

    def test_precision_value_mismatch(self):
        self.mutate_profile("SYN-001", lambda p: p["employments"][0].update(start="2018", start_precision="month"))
        self.assertFails("synthetic:date_integrity", "YYYY-MM")

    def test_current_flag_must_match_ongoing(self):
        self.mutate_profile("SYN-001", lambda p: p["employments"][-1].update(is_current_as_stated=False))
        self.assertFails("synthetic:date_integrity", "ongoing")

    def test_role_after_resume_as_of(self):
        self.mutate_profile("SYN-001", lambda p: p.update(resume_as_of="2020-01"))
        self.assertFails("synthetic:date_integrity", "after resume_as_of")

    # -- sequences
    def test_sequence_gap(self):
        self.mutate_profile("SYN-001", lambda p: p["employments"][-1].update(sequence_index=7))
        self.assertFails("synthetic:complete_sequences", "without gaps")

    def test_sequence_out_of_chronological_order(self):
        def f(p):
            a, b = p["employments"][0], p["employments"][1]
            a["sequence_index"], b["sequence_index"] = 2, 1
        self.mutate_profile("SYN-001", f)
        self.assertFails("synthetic:complete_sequences", "starts earlier")

    def test_two_ongoing_primary_roles(self):
        def f(p):
            p["employments"][0].update(end=None, end_precision="ongoing", is_current_as_stated=True)
        self.mutate_profile("SYN-001", f)
        self.assertFails("synthetic:complete_sequences", "ongoing primary roles")

    # -- dataset classes, separation and consent
    def test_uploaded_fixture_in_shared_seed(self):
        def f(p):
            p["dataset_class"] = "uploaded_resume_fixture"
        self.mutate_profile("SYN-001", f)
        self.assertFails("synthetic:data_class_and_consent", "uploaded_resume_fixture")
        self.assertFails("dataset_separation", "uploaded_resume_fixture")

    def test_upl_identifier_in_shared_files(self):
        self.mutate_profile("SYN-001", lambda p: p.update(source_ref="UPL-" + "004"))
        self.assertFails("dataset_separation", "UPL-")

    def test_uploaded_source_in_shared_config(self):
        def f(rows):
            rows.append({**rows[0], "source_id": "SRC-UPLOADS-001", "dataset_class": "uploaded_resume_fixture"})
            return rows
        self.edit_csv("data/config/data_sources.csv", f)
        self.assertFails("analytics_settings_and_sources", "must not be in the shared package")

    def test_synthetic_demo_flag_on_real_class(self):
        def f(p):
            p["dataset_class"] = "consented_real_reference"
        self.mutate_profile("SYN-001", f)
        self.assertFails("synthetic:data_class_and_consent", "synthetic_demo_allowed")

    def test_contributor_ids_must_not_be_seeded(self):
        self.mutate_profile("SYN-001", lambda p: p.update(contributor_user_id="00000000-0000-0000-0000-000000000001"))
        self.assertFails("synthetic:data_class_and_consent", "contributor_user_id")

    def test_direct_identifier_in_text(self):
        self.mutate_profile("SYN-001", lambda p: p.update(profile_summary="Contact me at someone@example.com"))
        self.assertFails("synthetic:schema_and_identifiers", "direct identifier")

    def test_threshold_ordering(self):
        def f(rows):
            for r in rows:
                if r["setting_key"] == "min_profiles_strong":
                    r["value_numeric"] = "2"
        self.edit_csv("data/config/analytics_settings.csv", f)
        self.assertFails("analytics_settings_and_sources", "limited <= moderate <= strong")

    # -- generated artefacts
    def test_editing_source_without_rebuilding_is_detected(self):
        self.mutate_profile("SYN-001", lambda p: p.update(profile_summary="Edited without rebuilding."))
        self.assertFails("generated_artefacts_in_sync", "out of date")

    def test_missing_pdf_is_detected(self):
        (self.root / "data/synthetic/synthetic_resume_pdfs/SYN-001_synthetic_resume.pdf").unlink()
        self.assertFails("synthetic_resume_pdfs", "missing PDF")


if __name__ == "__main__":
    unittest.main()
