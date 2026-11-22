"""/api/v1/profile/clear: clear my career profile and start over (D52).

GET  /api/v1/profile/clear    what clearing would delete (owner's own counts) and what is kept. Read-only.
POST /api/v1/profile/clear    {"confirm": "clear my career profile"}: delete the career profile and everything derived
                              from it in one owner transaction, then create a fresh empty profile. 422 without the exact
                              confirmation (nothing deleted). Idempotent: a retry after success returns already_clear.
Verified bearer token and the caller's own profile (owner_context); every statement is owner-scoped under RLS.
"""
from typing import Any, Dict

import psycopg
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import db_connection, owner_context
from app.api.ratelimit import limit
from app.services.candidate_profile import OwnerContext
from app.services.profile_reset import ProfileResetService

router = APIRouter(prefix="/api/v1", tags=["api"])


class ClearRequest(BaseModel):
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
