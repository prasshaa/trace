"""Controlled import of consented real reference profiles from a restricted staging folder (admin, local only).

    python -m scripts.import_real_reference [--staging DIR] [--dry-run]

Staging (default: data-foundation/local_only/real_reference, git-ignored) must be readable by its owner only (mode 700).
It holds manifest.json and, per participant, the structured career record (JSON, the canonical import format) and
optionally the original PDF. manifest.json:

  {"participants": [{
      "participant_reference": "P-2026-001",            opaque code from the product owner's own consent records
      "record": "P-2026-001.json", "pdf": "P-2026-001.pdf",
      "identity": {"full_name": "...", "other_names": [], "emails": [], "phones": [], "handles": [], "urls": [],
                   "address_lines": []},               used in memory only, never stored or printed
      "consent": {"basis": "product_owner_attestation", "purposes": [...], "evidence_reference": "...",
                  "attested_by": "Product owner", "verification_status": "verified", "verified_by": "...",
                  "verified_at": "2026-11-13T10:00:00Z", "original_consent_date": null, "original_notice_version": null},
      "review": {"reviewer": "...", "ambiguities_approved": false}}]}

Every record is de-identified (pipeline.deidentify) and then admitted or quarantined (loader.admit). Output is counts
and reason codes only: no names, contact details, file names or career text. Nothing is copied out of staging, no
temporary files are written, and source files are never deleted by this script (retention is an operator decision
after verification; see docs/REAL_REFERENCE_DATA.md). Refuses a non-local database.
"""
import argparse
import json
import stat
import sys
from collections import Counter
from pathlib import Path

import psycopg

from app.config import BACKEND_DIR, get_settings
from app.demo.fixtures import database_is_local
from app.services.deidentification import Attestation, Identity, admission_problems, admit, deidentify, quarantine

DEFAULT_STAGING = BACKEND_DIR.parent / "data-foundation" / "local_only" / "real_reference"


def check_staging(path: Path) -> str:
    if not path.is_dir():
        return "missing"
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        return "too_open"
    if not (path / "manifest.json").is_file():
        return "no_manifest"
    return "ok"


def run(conn, staging: Path, dry_run: bool) -> Counter:
    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    outcome = Counter()
    for entry in manifest.get("participants", []):
        ref = entry["participant_reference"]
        record_path = staging / entry["record"]
        if not record_path.is_file():
            outcome["missing_record"] += 1
            if not dry_run:
                quarantine(conn, ref, ["source_record_missing"])
            continue
        record = json.loads(record_path.read_text(encoding="utf-8"))
        pdf = (staging / entry["pdf"]).read_bytes() if entry.get("pdf") and (staging / entry["pdf"]).is_file() else None
        identity = Identity(**entry.get("identity", {}))
        review = entry.get("review", {})
        result = deidentify(record, identity, pdf, ambiguities_approved=bool(review.get("ambiguities_approved")))
        attestation = Attestation(**entry["consent"])
        problems = admission_problems(result, attestation, review.get("reviewer"))
        outcome[f"deidentification_{result.status}"] += 1
        if dry_run:
            outcome["would_admit" if not problems else "would_quarantine"] += 1
            for p in problems:
                outcome[f"reason:{p}"] += 1
            continue
        pid = admit(conn, ref, result, attestation, review.get("reviewer"))
        outcome["admitted" if pid else "quarantined"] += 1
        for p in problems:
            outcome[f"reason:{p}"] += 1
    return outcome


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--staging", type=Path, default=DEFAULT_STAGING)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    state = check_staging(args.staging)
    if state != "ok":
        print({"missing": "staging folder not found: no source files to import",
               "too_open": "refused: the staging folder must be readable by its owner only (chmod 700)",
               "no_manifest": "refused: manifest.json is missing"}[state], file=sys.stderr)
        return 1
    settings = get_settings()
    with psycopg.connect(settings.database_url.get_secret_value(), autocommit=True) as conn:
        if not database_is_local(conn):
            print("refused: the database connection is not local", file=sys.stderr)
            return 1
        outcome = run(conn, args.staging, args.dry_run)
    print(json.dumps(dict(sorted(outcome.items())), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
