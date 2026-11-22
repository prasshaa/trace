"""/api/v1/resume-import: resume-first onboarding (parser v2). One import, one consolidated review, one confirmation.

GET    /api/v1/resume-import                    the caller's open import as one structured profile, or {"status": "none"}
POST   /api/v1/resume-import[?replace=true][&replace_profile=true]
                                                raw PDF or DOCX body; Content-Type must agree with the file's signature.
                                                replace_profile (D52): the reviewed import will REPLACE the confirmed
                                                profile on confirmation (one transaction); until then nothing changes.
                                                Rate limited (upload bucket), size limited before reading, parsed in an
                                                isolated process. 409 import_pending unless replace=true. Nothing about
                                                the file (bytes, name, text) is stored or echoed.
POST   /api/v1/resume-import/{id}/check         read-only: starting role + its choices + field errors for these edits
POST   /api/v1/resume-import/{id}/confirm       edits + removals + confirmation in ONE transaction (idempotent); 422
                                                review_incomplete with {"fields": [{item, field, message}]} when the
                                                role contract isn't met (nothing written)
DELETE /api/v1/resume-import                    discard the open import and earlier pasted-text suggestions; confirmed
                                                records, the profile and saved plans are untouched
Verified bearer token and the caller's own profile required for every call (owner_context). The API is token-based (no
cookies), so a cross-site request can't carry the user's credentials. Responses are no-store.
"""
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.concurrency import run_in_threadpool

from app.api.deps import db_connection, owner_context, profile_service
from app.api.ratelimit import limit
from app.api.routes import _read_limited
from app.config import Settings, get_settings
from app.services.candidate_profile import CandidateProfileService, OwnerContext
from app.services.resume_import import ConfirmRequest, ResumeImportService

router = APIRouter(prefix="/api/v1", tags=["api"])


def _svc(conn: psycopg.Connection = Depends(db_connection), service: CandidateProfileService = Depends(profile_service),
         settings: Settings = Depends(get_settings)) -> ResumeImportService:
    return ResumeImportService(conn, service, settings)


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


@router.get("/resume-import", dependencies=[Depends(limit("read"))])
def current_import(response: Response, ctx: OwnerContext = Depends(owner_context), svc: ResumeImportService = Depends(_svc)) -> Dict[str, Any]:
    _private(response)
    return svc.view(ctx)


@router.post("/resume-import", status_code=201, dependencies=[Depends(limit("upload"))])
async def upload_resume(request: Request, response: Response, replace: bool = Query(default=False),
                        replace_profile: bool = Query(default=False),
                        ctx: OwnerContext = Depends(owner_context), svc: ResumeImportService = Depends(_svc),
                        settings: Settings = Depends(get_settings)) -> Dict[str, Any]:
    _private(response)
    data = await _read_limited(request, settings.max_resume_bytes)
    out = await run_in_threadpool(svc.upload, ctx, data, request.headers.get("content-type"), replace,
                                   replace_profile)
    del data
    view = await run_in_threadpool(svc.view, ctx)
    return {"result": out["status"], "skipped": out.get("skipped") or {}, "warnings": out.get("warnings") or [], "import": view}


@router.post("/resume-import/{import_id}/confirm", dependencies=[Depends(limit("mutation"))])
def confirm_import(import_id: UUID, body: ConfirmRequest, response: Response, ctx: OwnerContext = Depends(owner_context),
                   svc: ResumeImportService = Depends(_svc)) -> Dict[str, Any]:
    _private(response)
    return svc.confirm(ctx, import_id, body)


@router.post("/resume-import/{import_id}/check", dependencies=[Depends(limit("read"))])
def check_import(import_id: UUID, body: ConfirmRequest, response: Response, ctx: OwnerContext = Depends(owner_context),
                 svc: ResumeImportService = Depends(_svc)) -> Dict[str, Any]:
    """Read-only: which role sets the starting point (with its role choices) and every field still to fix."""
    _private(response)
    return svc.check(ctx, import_id, body)


@router.delete("/resume-import", dependencies=[Depends(limit("mutation"))])
def discard_import(response: Response, ctx: OwnerContext = Depends(owner_context), svc: ResumeImportService = Depends(_svc)) -> Dict[str, Any]:
    _private(response)
    return svc.discard(ctx)
