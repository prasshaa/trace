#!/usr/bin/env python3
"""Build a shareable review ZIP of the Trace Data Foundation, without restricted material.

  python3 scripts/package_review.py [--out PATH]

The ZIP contains exactly the files listed in manifest.json plus manifest.json itself, so
local_only/ is never included. Before writing, the script:
  1. checks that generated artefacts (including the manifest) are current;
  2. scans every shareable file for restricted identifiers and, when local_only/ is present on
     this machine, for any text copied from the restricted fixtures;
  3. after writing, extracts the ZIP to a temporary directory and confirms that the manifest and
     all generated artefacts still verify there, i.e. without local_only/.
Standard library only. Deterministic output (fixed timestamps, sorted entries).
"""
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_seeds  # noqa: E402
import trace_model as tm  # noqa: E402

ROOT = tm.ROOT
RESTRICTED_DIR = "local_only"
RESTRICTED_FIXTURES = f"{RESTRICTED_DIR}/uploaded_resume_fixtures/uploaded_resume_fixtures.jsonl"
DEFAULT_OUT = ROOT.parent / "trace-data-foundation-v2-review.zip"
ID_PATTERNS = (re.compile(r"\bUPL-\d{3}"), re.compile(r"\bturn\d+file\d+\b"))
MIN_RESTRICTED_SNIPPET = 25
TEXT_SUFFIXES = {".md", ".csv", ".jsonl", ".json", ".sql", ".py", ".txt", ".toml", ""}


def shareable_paths(root=ROOT):
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return sorted({f["path"] for f in manifest["files"]} | {"manifest.json"})


def restricted_snippets(root=ROOT):
    """Distinctive strings from the restricted fixtures (only when present on this machine)."""
    path = root / RESTRICTED_FIXTURES
    if not path.exists():
        return set()
    out = set()
    for p in tm.read_jsonl(path):
        values = [x.get("text") for x in p.get("evidence", [])]
        values += p.get("data_quality_flags", []) + p.get("extraction_notes", [])
        values += [p.get("profile_summary"), p.get("scope_status")]
        # Job titles are deliberately not scanned: generic titles legitimately appear as taxonomy aliases.
        values += [d.get("field_of_study") for d in p.get("education", [])]
        out.update(v.strip() for v in values if isinstance(v, str) and len(v.strip()) >= MIN_RESTRICTED_SNIPPET)
    return out


def scan(root=ROOT):
    """Return a list of problems that would expose restricted material in the shareable package."""
    errs = []
    paths = shareable_paths(root)
    snippets = restricted_snippets(root)
    for rel in paths:
        if rel.split("/", 1)[0] == RESTRICTED_DIR:
            errs.append(f"manifest lists restricted path {rel}")
            continue
        p = root / rel
        if not p.exists():
            errs.append(f"manifest lists missing file {rel}")
            continue
        if p.suffix not in TEXT_SUFFIXES:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for rx in ID_PATTERNS:
            m = rx.search(text)
            if m:
                errs.append(f"{rel}: contains restricted identifier {m.group(0)}")
        for snip in snippets:
            if snip in text:
                errs.append(f"{rel}: contains text copied from the restricted fixtures")
                break
        if rel == "manifest.json" and RESTRICTED_DIR + "/" in text:
            errs.append("manifest.json mentions restricted file paths")
    return errs


def verify_without_restricted(root=ROOT):
    """Copy only the shareable files to a temp dir and confirm everything still verifies."""
    with tempfile.TemporaryDirectory(prefix="trace-share-") as tmp:
        dst = Path(tmp)
        for rel in shareable_paths(root):
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / rel, target)
        return [f"{s} differs when local_only/ is absent" for s in build_seeds.stale_artefacts(dst)]


def build_zip(out, root=ROOT):
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for rel in shareable_paths(root):
            info = zipfile.ZipInfo(f"data-foundation/{rel}", date_time=(2026, 11, 11, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, (root / rel).read_bytes())


def check_zip(out):
    errs = []
    with zipfile.ZipFile(out) as z, tempfile.TemporaryDirectory(prefix="trace-zip-") as tmp:
        names = z.namelist()
        if any(n.split("/")[1] == RESTRICTED_DIR for n in names if n.count("/") >= 1):
            errs.append("ZIP contains restricted material")
        z.extractall(tmp)
        stale = build_seeds.stale_artefacts(Path(tmp) / "data-foundation")
        errs += [f"{s} does not verify inside the ZIP" for s in stale]
    return errs


def main(argv):
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else DEFAULT_OUT
    stale = build_seeds.stale_artefacts()
    if stale:
        print("Generated artefacts are out of date; run python3 scripts/build_seeds.py first:", *stale, sep="\n  ")
        return 1
    errs = scan() + verify_without_restricted()
    if errs:
        print("Refusing to package:", *errs, sep="\n  ")
        return 1
    build_zip(out)
    errs = check_zip(out)
    if errs:
        out.unlink()
        print("Packaged ZIP failed verification and was removed:", *errs, sep="\n  ")
        return 1
    print(f"Wrote {out} ({len(shareable_paths())} files, no restricted material; manifest verifies without local_only/).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
