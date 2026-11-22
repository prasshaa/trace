"""/api/v1. Phase 1: configuration, the authenticated account and its candidate profile. Phase 2: consent and
synthetic fixture resume upload."""
from datetime import datetime
from functools import lru_cache
from typing import Dict, Literal, Optional
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import AuthUnavailable
from app.api.deps import current_owner, db_connection, owner_context, profile_service
from app.api.mode import data_mode
from app.api.ratelimit import limit
from app.config import Settings, get_settings
from app.demo import consent as demo_consent
from app.demo.fixtures import UploadTooLarge, upload_fixture_resume
from app.services.resume_extraction import FixtureExtractor
from app.services.candidate_profile import CandidateProfileService, OwnerContext, ProfileCreate
from app.services.career_history.models import PartialDateView

router = APIRouter(prefix="/api/v1", tags=["api"])


class ConfigResponse(BaseModel):
    """Public client configuration: the Supabase URL, the anon key (public by design) and the data mode. Nothing else."""
    model_config = ConfigDict(extra="forbid")
    supabase_url: str
    supabase_anon_key: str
    data_mode: str                                   # server-controlled (TRACE_DATA_MODE); read-only for clients
    features: "ClientFeatures"


class ClientFeatures(BaseModel):
    """Server-controlled switches the client needs to present the right options. Read-only; no client can change them."""
    model_config = ConfigDict(extra="forbid")
    real_resume_uploads: bool                        # TRACE_REAL_RESUME_UPLOADS on AND an identity-marker key configured
    career_research: bool = False                    # TRACE_CAREER_ORIGINS (D46): off unless its release gates are cleared


class ProfileSummary(BaseModel):
    """The candidate's profile header. No internal identifiers."""
    model_config = ConfigDict(extra="forbid")
    headline: Optional[str]
    country_code: str
    resume_as_of: PartialDateView
    confirmation_status: str


ConfigResponse.model_rebuild()


class MeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID                                    # the verified token subject (the caller's own account)
    has_profile: bool
    profile: Optional[ProfileSummary] = None


def _summary(service: CandidateProfileService, ctx: OwnerContext) -> ProfileSummary:
    p = service.get_profile(ctx)
    return ProfileSummary(headline=p.headline, country_code=p.country_code, resume_as_of=p.resume_as_of,
                          confirmation_status=p.confirmation_status)


@router.get("/config", response_model=ConfigResponse)
def config(settings: Settings = Depends(get_settings), mode: str = Depends(data_mode)) -> ConfigResponse:
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise AuthUnavailable("client configuration is not set")
    return ConfigResponse(supabase_url=settings.supabase_url, supabase_anon_key=settings.supabase_anon_key, data_mode=mode,
                          features=ClientFeatures(real_resume_uploads=settings.real_resume_uploads_available,
                                                  career_research=settings.trace_career_origins))


@router.get("/me", response_model=MeResponse)
def me(owner: UUID = Depends(current_owner), service: CandidateProfileService = Depends(profile_service)) -> MeResponse:
    profile_id = service.owner_profile_id(owner)
    if profile_id is None:
        return MeResponse(user_id=owner, has_profile=False)
    return MeResponse(user_id=owner, has_profile=True,
                      profile=_summary(service, OwnerContext(owner_user_id=owner, candidate_profile_id=profile_id)))


@router.post("/profile", response_model=ProfileSummary, status_code=status.HTTP_201_CREATED, dependencies=[Depends(limit("mutation"))])
def create_profile(payload: ProfileCreate, owner: UUID = Depends(current_owner),
                   service: CandidateProfileService = Depends(profile_service)) -> ProfileSummary:
    profile_id = service.create_owner_profile(owner, payload)
    return _summary(service, OwnerContext(owner_user_id=owner, candidate_profile_id=profile_id))


@router.get("/profile", response_model=ProfileSummary)
def get_profile(ctx: OwnerContext = Depends(owner_context),
                service: CandidateProfileService = Depends(profile_service)) -> ProfileSummary:
    return _summary(service, ctx)


# --------------------------------------------------------------------------- consent (Phase 2)
class ConsentDocumentView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent_version: str
    title: str
    text: Optional[str] = None                       # shown only when it matches the installed document's hash
    text_verified: bool


class ConsentDecisionView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    consent_version: str
    recorded_at: datetime


class ConsentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: Literal["resume_processing", "deidentified_shared_dataset"] = "resume_processing"
    document: Optional[ConsentDocumentView] = None   # the current approved document, or None
    decision: Optional[ConsentDecisionView] = None   # the caller's latest decision, or None
    active: bool                                     # a current grant under an approved document


class ConsentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["granted", "denied", "withdrawn"]
    consent_version: str = Field(min_length=1, max_length=120)


def _document_view(doc: Optional[dict], purpose: str) -> Optional[ConsentDocumentView]:
    """The document's text is shown only when the installed document's hash matches a text this server holds."""
    if not doc:
        return None
    known = demo_consent.KNOWN.get(doc["consent_version"])
    verified = known is not None and known[0] == purpose and doc["body_sha256"] == known[2]
    body = known[1] if known else None
    return ConsentDocumentView(consent_version=doc["consent_version"], title=doc["title"], text=body if verified else None,
                               text_verified=verified)


def _consent_view(state: dict, purpose: str = "resume_processing") -> ConsentResponse:
    latest = state["latest"]
    return ConsentResponse(purpose=purpose, document=_document_view(state["document"], purpose), active=state["active"],
                           decision=ConsentDecisionView(**latest) if latest else None)


@router.get("/consent/resume-processing", response_model=ConsentResponse)
def get_consent(owner: UUID = Depends(current_owner), service: CandidateProfileService = Depends(profile_service)) -> ConsentResponse:
    return _consent_view(service.consent_state(owner))


@router.post("/consent/resume-processing", response_model=ConsentResponse, dependencies=[Depends(limit("mutation"))])
def post_consent(payload: ConsentDecision, owner: UUID = Depends(current_owner),
                 service: CandidateProfileService = Depends(profile_service)) -> ConsentResponse:
    """The caller's own explicit decision for the current approved document. Never granted on anyone's behalf."""
    return _consent_view(service.record_consent(owner, payload.decision, payload.consent_version))


# --------------------------------------------------------------------------- synthetic resume upload (Phase 2)
class UploadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    is_synthetic_test_data: bool
    notice: str
    extracted: Dict[str, int]


@lru_cache(maxsize=2)
def _extractor(directory: str) -> FixtureExtractor:
    from pathlib import Path
    d = Path(directory)
    return FixtureExtractor(d / "synthetic_profiles.jsonl", d / "synthetic_resume_pdfs.zip")


def fixture_extractor(settings: Settings = Depends(get_settings)) -> FixtureExtractor:
    return _extractor(str(settings.trace_fixture_dir))


async def _read_limited(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise UploadTooLarge("the file is larger than the upload limit")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise UploadTooLarge("the file is larger than the upload limit")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/resumes", response_model=UploadResponse, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(limit("upload"))])
async def upload_resume(request: Request, ctx: OwnerContext = Depends(owner_context),
                        service: CandidateProfileService = Depends(profile_service),
                        conn: psycopg.Connection = Depends(db_connection), settings: Settings = Depends(get_settings),
                        extractor: FixtureExtractor = Depends(fixture_extractor)) -> UploadResponse:
    """Raw PDF body (Content-Type: application/pdf; optional X-Filename). Only approved synthetic fixtures, only in
    local demo mode, only with active consent. The bytes are not stored."""
    data = await _read_limited(request, settings.max_resume_bytes)
    result = await run_in_threadpool(upload_fixture_resume, conn, service, extractor, settings, ctx, data,
                                     request.headers.get("content-type"), request.headers.get("x-filename"))
    return UploadResponse(**result)
