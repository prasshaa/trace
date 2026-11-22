"""/api/v1, Phase 7.1: how a user's career information is used (Profile settings -> Data & privacy).

GET  /api/v1/sharing          the onboarding data-use notice, the caller's sharing preference and, separately, the
                              actual state of their profile in the shared pool.
POST /api/v1/sharing/notice   Continue on the onboarding data-use notice: records the default preference ('shared')
                              when the caller has none yet. Never overrides an earlier choice.
PUT  /api/v1/sharing          a choice in Profile settings: {"mode": "shared" | "private", "confirm_version": ...}.

Three things are kept apart and never inferred from one another:
  preference           what the user wants ('shared' is the product default after the onboarding notice).
  explicit permission  the existing contribution consent (purpose deidentified_shared_dataset): an explicit 'granted'
                       decision under the current approved notice. Processing for the shared pool still REQUIRES it.
                       Acknowledging the onboarding notice never creates it. Choosing 'shared' in settings with
                       confirm_version = the current notice version records it (an explicit settings action, offered only
                       under the opt-in policy with a verified notice).
  status               what actually happened to the profile (queued, held for review, included, ...), from the database.
Choosing 'private' records a withdrawal of any active permission and excludes the profile from the shared pool in the
same transaction (the database also refuses to process a private user). The private profile and personal pathway are
untouched. Whether the default preference may itself authorise processing is a legal decision that has not been made;
until it is, it does not (see docs/SHARING_PREFERENCE.md).
"""
from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.consent_routes import CONTRIBUTION, ContributionNotOffered, contribution_policy
from app.api.deps import current_owner, db_connection, profile_service
from app.api.ratelimit import limit
from app.api.real_resume_routes import ContributionStatus, read_contribution_status
from app.api.routes import ConsentDocumentView, _document_view
from app.services.candidate_profile import CandidateProfileService
from app.services.candidate_profile.repository import ConsentConflict, owner_session

router = APIRouter(prefix="/api/v1", tags=["api"])

# v2 (Phase 7.2): the last paragraph now says what the default preference does and does not do. Preferences recorded
# under v1 keep their recorded version; nothing is re-shown or re-recorded for them.
NOTICE_VERSION = "trace-data-use-notice-v2"
NOTICE_TITLE = "Your experience helps make Trace better."
NOTICE_PARAGRAPHS = (
    "Trace uses your career information to understand your experience and recommend paths for you. It can also use "
    "career details from your profile to improve recommendations for other people.",
    "Before career details are added to the shared pool, we remove personal details such as your name, email address "
    "and phone number. We keep relevant professional information, such as your employers, job titles, education and "
    "experience, to understand career patterns.",
    "Other users see patterns across groups of profiles, not your individual profile. Some combinations of career "
    "details may still make a profile recognisable, so we cannot promise complete anonymity.",
    "Your sharing preference is set to on by default. Your career details won't be added to shared recommendations until "
    "you confirm sharing in Profile settings and your profile passes the required checks. You can change your "
    "preference to private recommendations anytime.",
)

# Release (D52): where sharing with other users is not offered (TRACE_CONTRIBUTION_POLICY=disabled, the public site), the
# onboarding notice says so, and continuing records the preference 'private' (never the 'shared' default).
PRIVATE_NOTICE_VERSION = "trace-data-use-notice-private-v1"
PRIVATE_NOTICE_TITLE = "Your career information stays private."
PRIVATE_NOTICE_PARAGRAPHS = (
    "Trace uses your career information only to understand your experience and build your own career plan.",
    "Your profile and your plans are private to you. Other users can't see them, and nothing from your profile is added "
    "to any shared pool. Sharing career patterns with other users isn't offered on this site.",
    "You can update your profile from a new resume, or clear it, at any time from your profile page.",
)


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DataUseNotice(_View):
    version: str
    title: str
    paragraphs: List[str]
    is_consent_record: bool = False          # acknowledging it records a preference, never a consent decision


class SharingView(_View):
    notice: DataUseNotice
    notice_acknowledged: bool                # a preference exists (the onboarding notice was seen, or a choice made)
    preference: Optional[Literal["shared", "private"]] = None
    preference_source: Optional[Literal["onboarding_default", "settings"]] = None
    preference_updated_at: Optional[datetime] = None
    explicit_permission: bool                # an active explicit contribution permission (and not private)
    activation: Literal["not_chosen", "private", "active", "confirmation_required", "not_offered"]
    confirmation_notice: Optional[ConsentDocumentView] = None    # the notice confirm_version must match, when offered
    status: ContributionStatus               # the actual state in the shared pool; "included" only when it is


class NoticeAcknowledgement(_View):
    notice_version: str = Field(min_length=1, max_length=80)


class SharingChoice(_View):
    mode: Literal["shared", "private"]
    confirm_version: Optional[str] = Field(default=None, min_length=1, max_length=120)


def _notice(policy: str = "opt_in") -> DataUseNotice:
    if policy == "disabled":
        return DataUseNotice(version=PRIVATE_NOTICE_VERSION, title=PRIVATE_NOTICE_TITLE, paragraphs=list(PRIVATE_NOTICE_PARAGRAPHS))
    return DataUseNotice(version=NOTICE_VERSION, title=NOTICE_TITLE, paragraphs=list(NOTICE_PARAGRAPHS))


def _view(conn: psycopg.Connection, owner: UUID, service: CandidateProfileService, policy: str) -> SharingView:
    with owner_session(conn, owner):
        row = conn.execute("select mode, source, updated_at from trace_contrib.my_sharing_preference()").fetchone()
    mode, source, updated = row if row else (None, None, None)
    state = service.consent_state(owner, CONTRIBUTION)
    doc = _document_view(state["document"], CONTRIBUTION)
    offered = policy == "opt_in" and doc is not None and doc.text_verified
    status = read_contribution_status(conn, owner)
    permission = bool(status.consent_active)
    if mode is None:
        activation = "not_chosen"
    elif mode == "private":
        activation = "private"
    elif permission:
        activation = "active"
    else:
        activation = "confirmation_required" if offered else "not_offered"
    return SharingView(notice=_notice(policy), notice_acknowledged=mode is not None, preference=mode, preference_source=source,
                       preference_updated_at=updated, explicit_permission=permission, activation=activation,
                       confirmation_notice=doc if offered else None, status=status)


@router.get("/sharing", response_model=SharingView)
def get_sharing(owner: UUID = Depends(current_owner), conn: psycopg.Connection = Depends(db_connection),
                service: CandidateProfileService = Depends(profile_service), policy: str = Depends(contribution_policy)) -> SharingView:
    return _view(conn, owner, service, policy)


@router.post("/sharing/notice", response_model=SharingView, dependencies=[Depends(limit("mutation"))])
def acknowledge_notice(payload: NoticeAcknowledgement, owner: UUID = Depends(current_owner),
                       conn: psycopg.Connection = Depends(db_connection),
                       service: CandidateProfileService = Depends(profile_service),
                       policy: str = Depends(contribution_policy)) -> SharingView:
    """Continue on the onboarding data-use notice the caller was shown. Records the default only if no preference exists."""
    current = _notice(policy).version
    if payload.notice_version != current:
        raise ConsentConflict("this data-use notice is not the current version")
    with owner_session(conn, owner):
        conn.execute("select trace_contrib.acknowledge_data_use_notice(%s)", (current,))
        if policy == "disabled":                       # sharing isn't offered: the recorded preference is private
            conn.execute("select trace_contrib.set_sharing_preference('private')")
    return _view(conn, owner, service, policy)


@router.put("/sharing", response_model=SharingView, dependencies=[Depends(limit("mutation"))])
def choose_sharing(payload: SharingChoice, owner: UUID = Depends(current_owner), conn: psycopg.Connection = Depends(db_connection),
                   service: CandidateProfileService = Depends(profile_service),
                   policy: str = Depends(contribution_policy)) -> SharingView:
    """The caller's own choice in Profile settings. Each request is one transaction."""
    if payload.mode == "shared" and payload.confirm_version is not None and policy != "opt_in":
        raise ContributionNotOffered("contributing to the shared pool is not offered at the moment")
    with owner_session(conn, owner) as repo:
        latest = repo.latest_consent_event(CONTRIBUTION)
        if payload.mode == "private":
            if latest and latest["status"] == "granted":
                # The explicit permission is withdrawn too, so the consent history matches the setting.
                repo.insert_consent_event(CONTRIBUTION, "withdrawn", latest["consent_version"])
            conn.execute("select trace_contrib.set_sharing_preference('private')")
        else:
            conn.execute("select trace_contrib.set_sharing_preference('shared')")
            if payload.confirm_version is not None:
                docs = repo.consent_documents(CONTRIBUTION)
                if not docs or docs[0]["consent_version"] != payload.confirm_version:
                    raise ConsentConflict("this contribution notice is not the current approved version")
                shown = _document_view(docs[0], CONTRIBUTION)
                if not shown.text_verified:
                    raise ConsentConflict("the contribution notice text could not be verified")
                if not (latest and latest["status"] == "granted" and latest["consent_version"] == payload.confirm_version):
                    repo.insert_consent_event(CONTRIBUTION, "granted", payload.confirm_version)
    return _view(conn, owner, service, policy)
