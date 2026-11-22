"""Automatic contribution processing (Phase 6.1, hardened in 6.2). Runs on the restricted runtime login; never on the
admin identity.

process_pending() claims due requests (trace_contrib.claim_contribution_requests: lease + SKIP LOCKED, so concurrent
workers and restarts neither lose nor double-process work) and, for each, process_user():
  1. context from the database: contribution consent active, the private profile, synthetic-fixture flag, whether a
     real resume was admitted through de-identification, reviewer approvals (by record hash), and whether the upload
     itself needed manual review;
  2. the canonical record from the user's REVIEWED private profile (builder.canonical_record);
  3. de-identification again, with the account identity AND the identity markers kept from the upload
     (contributions.identity), so identifiers typed into corrections or edits are found even when the account has no
     name;
  4. a person reviews the exact content (quarantined until approved, the existing contribution suspended at once) when:
       - de-identification found an ambiguity (needs_review);
       - it REMOVED anything: the private profile was de-identified at upload, so an identifier found now was introduced
         by an edit, and nearby text may hold identifiers no rule recognises (new_identifier_in_edit);
       - no identity markers or no name are known for the user, so names cannot be detected (identity_markers_missing);
       - the upload itself needed review (carried_over_review);
  5. trace_contrib.record_contribution_outcome: eligible -> create or update the user's single contribution in place
     (idempotent on the record hash); otherwise suspend any existing one. The database re-checks consent, fixture
     status and residual identifiers itself.

Bounded retries: an error while processing is reported with its class name only
(trace_contrib.report_contribution_failure); the request is retried with exponential backoff up to
TRACE_CONTRIBUTION_MAX_ATTEMPTS, then becomes terminally 'failed' until the user changes their profile or an operator
retries it (scripts/contribution_queue.py). An attempt cut short by a crash is counted when its lease expires; a
graceful stop releases unstarted claims without counting them. Nothing here logs content.
"""
import hashlib
import json
import logging
import threading
from typing import Dict, Optional
from uuid import UUID

import psycopg

from app import db
from app.config import Settings
from app.services.candidate_profile import CandidateProfileService, OwnerContext
from app.services.contributions.builder import canonical_record
from app.services.contributions.identity import load_identity
from app.services.contributions.sealing import MarkerKeyInvalid, sealer_from
from app.services.deidentification import PIPELINE_VERSION, deidentify

logger = logging.getLogger(__name__)


def record_sha256(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _outcome(conn, user, requested_at, state, reason, record=None, sha=None, report=None) -> str:
    with conn.transaction():
        return conn.execute("select trace_contrib.record_contribution_outcome(%s, %s, %s, %s, %s, %s, %s, %s)",
                            (user, requested_at, state, reason, json.dumps(record) if record is not None else None, sha,
                             json.dumps(report) if report is not None else None, PIPELINE_VERSION)).fetchone()[0]


def review_reason(result, *, markers_recorded: bool, requires_review: bool) -> Optional[str]:
    """Why a person must review this rebuilt contribution before it may be admitted (None: no review needed)."""
    if result.status == "needs_review":
        return "manual_review_required"
    if result.status != "passed":
        return None
    if result.report.get("removed"):
        return "new_identifier_in_edit"
    if not markers_recorded or result.identity is None or not result.identity.names():
        return "identity_markers_missing"
    if requires_review:
        return "carried_over_review"
    return None


def contribution_candidate(conn: psycopg.Connection, user: UUID, profile_id, sealer=None) -> tuple:
    """(record, identity, markers_recorded, result, reason): the de-identified record this user would contribute now.
    Shared with the reviewer tool, so a reviewer approves exactly the content (hash) the processor admits."""
    profile = CandidateProfileService(conn).get_profile(OwnerContext(owner_user_id=user, candidate_profile_id=profile_id))
    record, reason = canonical_record(profile)
    if record is None:
        return None, None, False, None, reason
    identity, recorded = load_identity(conn, user, sealer)
    return record, identity, recorded, deidentify(record, identity), None


def process_user(conn: psycopg.Connection, user: UUID, requested_at, sealer=None) -> str:
    with conn.transaction():
        ctx = conn.execute("select consent_active, candidate_profile_id, is_fixture_profile, has_admitted_resume, approved_record_sha256, "
                           "requires_review from trace_contrib.contribution_context(%s)", (user,)).fetchone()
    consent, profile_id, fixture, real, approved, requires_review = ctx
    if not consent:
        return _outcome(conn, user, requested_at, "not_eligible", "consent_not_granted")
    if profile_id is None:
        return _outcome(conn, user, requested_at, "not_eligible", "no_profile")
    if fixture:
        return _outcome(conn, user, requested_at, "not_eligible", "synthetic_profile")
    if not real:
        return _outcome(conn, user, requested_at, "not_eligible", "no_processed_resume")
    record, identity, recorded, result, reason = contribution_candidate(conn, user, profile_id, sealer)
    if record is None:
        return _outcome(conn, user, requested_at, "not_eligible", reason)
    if result.status == "failed":
        return _outcome(conn, user, requested_at, "failed", "deidentification_failed", report=result.report)
    sha = record_sha256(result.record)
    why = review_reason(result, markers_recorded=recorded, requires_review=requires_review)
    if why and sha in (approved or []):                                       # a reviewer approved exactly this content
        if result.status == "needs_review":
            result = deidentify(record, identity, ambiguities_approved=True)
        why = None
    if why:
        return _outcome(conn, user, requested_at, "quarantined", why, report={**result.report, "review_reason": why})
    return _outcome(conn, user, requested_at, "eligible", None, result.record, sha, result.report)


def _report_failure(conn: psycopg.Connection, settings: Settings, user: UUID, lease_token, exc: Exception) -> str:
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        conn.rollback()
    try:
        with conn.transaction():
            return conn.execute("select trace_contrib.report_contribution_failure(%s, %s, %s, %s, %s, %s)",
                                (user, lease_token, type(exc).__name__[:60], settings.trace_contribution_max_attempts,
                                 settings.trace_contribution_backoff_base_seconds,
                                 settings.trace_contribution_backoff_max_seconds)).fetchone()[0]
    except psycopg.Error as again:                                              # counted when the lease expires
        logger.warning("contribution failure could not be recorded: %s", type(again).__name__)
        return "lease_pending"


def process_pending(settings: Settings, limit: int = 20, stop: Optional[threading.Event] = None) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    try:
        sealer = sealer_from(settings)            # None: sealed markers cannot be opened, so contributions wait for review
    except MarkerKeyInvalid:
        logger.warning("identity marker key is not valid; sealed markers cannot be opened")
        sealer = None
    with db.connect_runtime(settings) as conn:
        with conn.transaction():
            claimed = conn.execute("select user_id, requested_at, lease_token from trace_contrib.claim_contribution_requests(%s, %s, %s, %s, %s)",
                                   (limit, settings.trace_contribution_lease_seconds, settings.trace_contribution_max_attempts,
                                    settings.trace_contribution_backoff_base_seconds,
                                    settings.trace_contribution_backoff_max_seconds)).fetchall()
        for i, (user, requested_at, lease_token) in enumerate(claimed):
            if stop is not None and stop.is_set():                              # graceful stop: hand the rest back
                with conn.transaction():
                    n = conn.execute("select trace_contrib.release_contribution_requests(%s)",
                                     ([t for _, _, t in claimed[i:]],)).fetchone()[0]
                counts["released"] = n
                break
            try:
                state = process_user(conn, user, requested_at, sealer)
            except Exception as exc:
                logger.warning("contribution processing failed: %s", type(exc).__name__)
                state = _report_failure(conn, settings, user, lease_token, exc)
            counts[state] = counts.get(state, 0) + 1
    return counts
