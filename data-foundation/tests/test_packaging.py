"""The shareable package is self-consistent without local_only/ and exposes no restricted material."""
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from _support import ROOT

import build_seeds  # noqa: E402
import package_review  # noqa: E402


class ShareablePackage(unittest.TestCase):
    def test_manifest_lists_no_restricted_paths_or_names(self):
        text = (ROOT / "manifest.json").read_text(encoding="utf-8")
        manifest = json.loads(text)
        self.assertFalse(any(f["path"].startswith("local_only/") for f in manifest["files"]))
        self.assertNotIn("local_only/", text)
        self.assertFalse(manifest["restricted_material"]["included"])

    def test_manifest_never_lists_supabase_local_state(self):
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse([f["path"] for f in manifest["files"]
                          if f["path"].startswith(("supabase/.temp/", "supabase/.branches/"))])
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "pkg"
            shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store", ".temp", ".branches"))
            (dst / "supabase" / ".temp" / "start-secrets").mkdir(parents=True)
            (dst / "supabase" / ".temp" / "start-secrets" / "docker.env").write_text("SECRET=x", encoding="utf-8")
            (dst / "supabase" / ".branches").mkdir()
            (dst / "supabase" / ".branches" / "_current_branch").write_text("main", encoding="utf-8")
            self.assertEqual(build_seeds.stale_artefacts(dst), [])

    def test_manifest_is_identical_with_and_without_local_only(self):
        self.assertEqual(package_review.verify_without_restricted(ROOT), [])

    def test_manifest_does_not_change_when_local_only_content_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "pkg"
            shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
            (dst / "local_only" / "extra-restricted-file.txt").parent.mkdir(exist_ok=True)
            (dst / "local_only" / "extra-restricted-file.txt").write_text("restricted", encoding="utf-8")
            self.assertEqual(build_seeds.stale_artefacts(dst), [])

    def test_no_restricted_identifiers_or_content_in_shared_files(self):
        self.assertEqual(package_review.scan(ROOT), [])

    def test_scan_detects_restricted_identifiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "pkg"
            shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns("local_only", "__pycache__", ".DS_Store"))
            with (dst / "docs" / "DATA_DICTIONARY.md").open("a", encoding="utf-8") as f:
                f.write("\nleak: " + "UPL-" + "001\n")
            self.assertTrue(any("restricted identifier" in e for e in package_review.scan(dst)))

    @unittest.skipUnless((ROOT / package_review.RESTRICTED_FIXTURES).exists(), "restricted local fixtures not present")
    def test_scan_detects_copied_restricted_text(self):
        snippet = sorted(package_review.restricted_snippets(ROOT))[0]
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "pkg"
            shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
            with (dst / "README.md").open("a", encoding="utf-8") as f:
                f.write("\n" + snippet + "\n")
            self.assertTrue(any("copied from the restricted fixtures" in e for e in package_review.scan(dst)))

    def test_review_zip_excludes_local_only_and_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "review.zip"
            package_review.build_zip(out, ROOT)
            with zipfile.ZipFile(out) as z:
                names = z.namelist()
            self.assertFalse(any("/local_only/" in n for n in names))
            self.assertEqual(len(names), len(package_review.shareable_paths(ROOT)))
            self.assertEqual(package_review.check_zip(out), [])


if __name__ == "__main__":
    unittest.main()
