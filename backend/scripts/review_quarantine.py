"""Human review of quarantined items (admin, local). Ambiguous cases are never admitted automatically.

    python -m scripts.review_quarantine list
    python -m scripts.review_quarantine show-resume <quarantine id>          the held, already de-identified record
    python -m scripts.review_quarantine approve-resume <quarantine id> --reviewer "<name>"
    python -m scripts.review_quarantine reject-resume <quarantine id> --reviewer "<name>"
    python -m scripts.review_quarantine show-contribution <user id>         the de-identified record that would be contributed
    python -m scripts.review_quarantine approve-contribution <user id> --reviewer "<name>"

Approving a resume ingests the held record into the owner's private profile through the same admission and ingestion
path as an upload (as the restricted runtime with the owner's identity), then clears the held content. Approving a
contribution records approval for exactly the content shown (its hash) and queues processing; any later change to the
profile needs a new review. Rejecting clears the held content. Refuses a non-local database.
"""
import argparse
import json
import sys
from uuid import UUID

import psycopg

from app import db
from app.config import get_settings
from app.demo.fixtures import database_is_local
from app.services.candidate_profile import CandidateProfileService, OwnerContext
from app.services.contributions.processor import contribution_candidate, record_sha256
from app.services.contributions.sealing import sealer_from
from app.services.deidentification import deidentify
from app.services.real_resume import admit_resume, to_extraction


def _contribution_preview(rt, user: UUID, sealer=None):
    """Exactly what the processor would contribute now (same builder, same identity markers, same hash)."""
    with rt.transaction():
        ctx = rt.execute("select candidate_profile_id from trace_contrib.contribution_context(%s)", (user,)).fetchone()
    if ctx is None or ctx[0] is None:
        return None, None, "no_profile"
    record, identity, _, result, reason = contribution_candidate(rt, user, ctx[0], sealer)
    if record is None:
        return None, None, reason
    if result.status == "failed":
        return None, None, "failed"
    if result.status == "needs_review":
        result = deidentify(record, identity, ambiguities_approved=True)
    return result.record, record_sha256(result.record), result.status


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["list", "show-resume", "approve-resume", "reject-resume", "show-contribution", "approve-contribution"])
    ap.add_argument("target", nargs="?")
    ap.add_argument("--reviewer")
    a = ap.parse_args(argv)
    settings = get_settings()
    with db.connect(settings) as admin, db.connect_runtime(settings) as rt:
        if not database_is_local(admin):
            print("refused: the database connection is not local", file=sys.stderr)
            return 1
        if a.action == "list":
            rows = admin.execute("select quarantine_id, created_at, report -> 'ambiguous' from trace_private.resume_quarantine "
                                 "where status = 'pending_review' order by created_at").fetchall()
            users = admin.execute("select user_id, updated_at, reason from trace_private.user_contribution_status "
                                  "where state = 'quarantined'").fetchall()
            print(json.dumps({"resumes": [{"id": str(r[0]), "since": r[1].isoformat(), "ambiguous": r[2]} for r in rows],
                              "contributions": [{"user": str(u[0]), "since": u[1].isoformat(), "reason": u[2]} for u in users]}, indent=2))
            return 0
        if a.action in ("approve-resume", "reject-resume", "approve-contribution") and not (a.reviewer and a.reviewer.strip()):
            print("refused: --reviewer is required", file=sys.stderr)
            return 1
        if a.action in ("show-contribution", "approve-contribution") and sealer_from(settings) is None and admin.execute(
                "select exists (select 1 from trace_private.identity_markers where owner_user_id = %s and sealed is not null)",
                (UUID(a.target),)).fetchone()[0]:
            admin.rollback()
            print("refused: this user's identity markers are sealed; set TRACE_IDENTITY_MARKER_KEY to review the exact content",
                  file=sys.stderr)
            return 1
        if a.action == "show-contribution":
            record, sha, status = _contribution_preview(rt, UUID(a.target), sealer_from(settings))
            print(json.dumps({"status": status, "record_sha256": sha, "record": record}, indent=2))
            return 0
        if a.action == "approve-contribution":
            user = UUID(a.target)
            record, sha, status = _contribution_preview(rt, user, sealer_from(settings))
            if sha is None:
                print(f"refused: nothing to approve ({status})", file=sys.stderr)
                return 1
            with admin.transaction():
                admin.execute("insert into trace_private.contribution_review_approvals (user_id, record_sha256, approved_by) "
                              "values (%s, %s, %s) on conflict do nothing", (user, sha, a.reviewer))
                admin.execute("select trace_private.enqueue_contribution(%s)", (user,))                # a fresh request
            print("approved; processing queued")
            return 0
        row = admin.execute("select owner_user_id, candidate_profile_id, content_sha256, record, report, status "
                            "from trace_private.resume_quarantine where quarantine_id = %s", (a.target,)).fetchone()
        admin.rollback()
        if row is None or row[5] != "pending_review":
            print("refused: no pending quarantined resume with that id", file=sys.stderr)
            return 1
        owner, profile_id, sha, record, report, _ = row
        if a.action == "show-resume":
            print(json.dumps(record, indent=2))
            return 0
        if a.action == "approve-resume":
            ctx = OwnerContext(owner_user_id=owner, candidate_profile_id=profile_id)
            service = CandidateProfileService(rt)
            resume_id = service.register_resume(ctx, int(report.get("byte_size") or 1), sha, None)
            try:
                admit_resume(rt, ctx, resume_id, sha, report.get("pipeline_version", "reviewed"))
                service.ingest_extraction(ctx, resume_id, to_extraction(record, sha), real=True)
            except Exception:
                service.remove_resume(ctx, resume_id)
                raise
        outcome = "approved" if a.action == "approve-resume" else "rejected"
        with admin.transaction():
            admin.execute("update trace_private.resume_quarantine set status = %s, reviewed_by = %s, reviewed_at = now(), "
                          "record = '{}'::jsonb where quarantine_id = %s", (outcome, a.reviewer, a.target))
            admin.execute("insert into trace_private.resume_processing_log (owner_user_id, outcome) values (%s, %s)", (owner, outcome))
        print(f"{outcome}; held content cleared")
        return 0


if __name__ == "__main__":
    sys.exit(main())
