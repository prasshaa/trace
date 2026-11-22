"""/api/v1, Phase 6.1: a user's own resume, processing status and contribution status.

POST /api/v1/real-resumes          raw PDF body (Content-Type: application/pdf). The verified owner only; rate limited
                                   (upload bucket); TRACE_REAL_RESUME_UPLOADS must be on; resume_processing consent.
                                   201 {"status": "processed", ...} or 202 {"status": "awaiting_manual_review"}.
                                   Nothing about the file (bytes, name, text) is stored or returned.
GET  /api/v1/real-resumes/status   the caller's latest processing outcome (no content).
GET  /api/v1/contribution          the caller's contribution state. Never the contribution id, the reference profile
                                   id, the report or anyone else's state.
"""
from datetime import datetime
from typing import Dict, List, Literal, Optional
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.api.deps import current_owner, db_connection, owner_context, profile_service
from app.api.ratelimit import limit
from app.api.routes import _read_limited
from app.config import Settings, get_settings
from app.services.candidate_profile import CandidateProfileService, OwnerContext
from app.services.candidate_profile.repository import owner_session
from app.services.real_resume import process_real_resume

router = APIRouter(prefix="/api/v1", tags=["api"])


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RealUploadResponse(_View):
    status: Literal["processed", "awaiting_manual_review", "proposed", "unchanged"]
    extracted: Optional[Dict[str, int]] = None
    proposals: Optional[Dict[str, int]] = None       # an upload into a profile that already holds records (Phase 2)
    notice: str


class ProcessingStatus(_View):
    status: Literal["none", "processed", "awaiting_manual_review", "not_processed", "approved", "rejected"]
    reason: Optional[str] = None
    updated_at: Optional[datetime] = None


CONTRIBUTION_LABELS = {
    "not_enabled": "You have not chosen to contribute.",
    "not_eligible": "Not contributing yet.",
    "awaiting_resume": "Waiting for your resume to be processed.",
    "awaiting_review": "Waiting for you to review and confirm your profile.",
    "pending": "Queued for processing.",
    "processing": "Being processed.",
    "eligible": "Included in Trace's shared career intelligence.",
    "quarantined": "Waiting for a manual privacy review.",
    "failed": "Could not be included: it did not pass the privacy checks.",
    "withdrawn": "Withdrawn. Your contribution was removed.",
}

# A more specific label for some reasons. The state set (and so the response contract) is unchanged.
REASON_LABELS = {
    ("pending", "retry_scheduled"): "Queued for another attempt.",
    ("pending", "manual_retry"): "Queued for another attempt.",
    ("failed", "retries_exhausted"): "Could not be processed after several attempts. It will be retried after a check.",
    ("quarantined", "new_identifier_in_edit"): "Waiting for a manual privacy review: a recent edit may contain personal details.",
    ("withdrawn", "private"): "Not shared. You chose to use your career information only for your own recommendations.",
}


class ContributionStatus(_View):
    consent_active: bool
    state: Literal["not_enabled", "not_eligible", "awaiting_resume", "awaiting_review", "pending", "processing", "eligible",
                   "quarantined", "failed", "withdrawn"]
    included: bool
    label: str
    updated_at: Optional[datetime] = None


@router.post("/real-resumes", response_model=RealUploadResponse, status_code=201, dependencies=[Depends(limit("upload"))],
             responses={202: {"model": RealUploadResponse}})
async def upload_real_resume(request: Request, ctx: OwnerContext = Depends(owner_context),
                             service: CandidateProfileService = Depends(profile_service),
                             conn: psycopg.Connection = Depends(db_connection), settings: Settings = Depends(get_settings)):
    data = await _read_limited(request, settings.max_resume_bytes)
    out = await run_in_threadpool(process_real_resume, conn, service, settings, ctx, data, request.headers.get("content-type"))
    if out["status"] == "awaiting_manual_review":
        body = RealUploadResponse(status="awaiting_manual_review", notice="Your resume needs a short manual privacy review "
                                  "before its details are added to your profile. Nothing has been added yet.")
        return JSONResponse(status_code=202, content=body.model_dump())
    if out["status"] == "unchanged":
        return RealUploadResponse(status="unchanged", notice="This resume was already read for your profile. Nothing changed.")
    if out["status"] == "proposed":
        b = out["proposals"]
        return RealUploadResponse(status="proposed", proposals={k: b[k] for k in ("proposed", "already_open", "already_in_profile")},
                                  notice="Your profile was not changed. Review the suggested updates and accept the ones "
                                         "that are right." if b["proposed"] else
                                  "Your profile was not changed: nothing new was found in this resume.")
    return RealUploadResponse(status="processed", extracted=out["extracted"],
                              notice="Your details were read and personal contact details removed. Review each item.")


@router.get("/real-resumes/status", response_model=ProcessingStatus)
def processing_status(owner: UUID = Depends(current_owner), conn: psycopg.Connection = Depends(db_connection)) -> ProcessingStatus:
    with owner_session(conn, owner):
        row = conn.execute("select outcome, reason_codes, created_at from trace_contrib.my_resume_processing()").fetchone()
    if row is None:
        return ProcessingStatus(status="none")
    outcome, reasons, at = row
    status = {"admitted": "processed", "quarantined": "awaiting_manual_review", "failed": "not_processed"}.get(outcome, outcome)
    return ProcessingStatus(status=status, reason=(reasons or [None])[0], updated_at=at)


@router.get("/contribution", response_model=ContributionStatus)
def contribution_status(owner: UUID = Depends(current_owner), conn: psycopg.Connection = Depends(db_connection)) -> ContributionStatus:
    return read_contribution_status(conn, owner)


def read_contribution_status(conn: psycopg.Connection, owner: UUID) -> ContributionStatus:
    """The caller's own contribution state, from the database (never inferred). Also used by /api/v1/sharing."""
    with owner_session(conn, owner):
        row = conn.execute("select state, reason, updated_at, consent_active from trace_contrib.my_contribution_status()").fetchone()
    state, reason, at, consent = row if row else (None, None, None, False)
    if not consent and state != "withdrawn":
        shown = "not_enabled"
    elif state == "not_eligible":
        shown = {"no_processed_resume": "awaiting_resume", "no_profile": "awaiting_resume", "review_incomplete": "awaiting_review",
                 "no_reviewed_employment": "awaiting_review"}.get(reason, "not_eligible")
    else:
        shown = state or "pending"
    return ContributionStatus(consent_active=bool(consent), state=shown, included=shown == "eligible",
                              label=REASON_LABELS.get((shown, reason), CONTRIBUTION_LABELS[shown]), updated_at=at)
