"""/api/v1/profile/clear, /api/v1/account/export and /api/v1/account: the owner's own data (D52).

GET  /api/v1/profile/clear    what clearing would delete (owner's own counts) and what is kept. Read-only.
POST /api/v1/profile/clear    {"confirm": "clear my career profile"}: delete the career profile and everything derived
                              from it in one owner transaction, then create a fresh empty profile. 422 without the exact
                              confirmation (nothing deleted). Idempotent: a retry after success returns already_clear.
Verified bearer token and the caller's own profile (owner_context); every statement is owner-scoped under RLS.
"""
import json
from typing import Any, Dict
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import current_owner, db_connection, owner_context
from app.api.ratelimit import limit
from app.services.candidate_profile import OwnerContext
from app.services.profile_reset import ProfileResetService

router = APIRouter(prefix="/api/v1", tags=["api"])


class ClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: str = Field(default="", max_length=60)


class DeleteAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: str = Field(default="", max_length=60)


def _svc(conn: psycopg.Connection = Depends(db_connection)) -> ProfileResetService:
    return ProfileResetService(conn)


@router.get("/profile/clear", dependencies=[Depends(limit("read"))])
def clear_preview(response: Response, ctx: OwnerContext = Depends(owner_context),
                  svc: ProfileResetService = Depends(_svc)) -> Dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    return svc.preview(ctx)


@router.post("/profile/clear", dependencies=[Depends(limit("mutation"))])
def clear_profile(body: ClearRequest, response: Response, ctx: OwnerContext = Depends(owner_context),
                  svc: ProfileResetService = Depends(_svc)) -> Dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    return svc.clear(ctx, body.confirm)


@router.get("/account/export", dependencies=[Depends(limit("read"))])
def export_account(ctx: OwnerContext = Depends(owner_context), svc: ProfileResetService = Depends(_svc)) -> Response:
    """A JSON download of the caller's own profile. Owner-scoped; no other account is readable."""
    payload = svc.export(ctx)
    body = json.dumps(payload, indent=2, ensure_ascii=False).encode()
    return Response(content=body, media_type="application/json", headers={
        "Content-Disposition": 'attachment; filename="trace-data.json"',
        "Cache-Control": "private, no-store",
    })


@router.post("/account", dependencies=[Depends(limit("mutation"))])
def delete_account(body: DeleteAccountRequest, response: Response, owner: UUID = Depends(current_owner),
                   svc: ProfileResetService = Depends(_svc)) -> Dict[str, Any]:
    """Delete the caller's account. A career profile is not required. 422 without the exact phrase."""
    response.headers["Cache-Control"] = "private, no-store"
    return svc.delete_account(owner, body.confirm)
