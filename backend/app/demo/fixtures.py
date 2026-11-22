"""Guarded synthetic fixture uploads for the local demo (Phase 2).

Only the 41 approved synthetic fixture PDFs are accepted; nothing else is represented as parsed. The flow, in order,
writes nothing until every check has passed:

  1. validate the upload (size limit, PDF signature);
  2. SHA-256 of the received bytes (computed here; no client-supplied hash, fixture id or flag is read) must be an
     approved fixture: the fixture extractor identifies it AND the database allowlist
     (trace_ingest.approved_fixture_resumes) holds exactly that (fixture id, hash) pair;
  3. TRACE_DEMO_FIXTURES must be enabled, and the actual database connection must be local (unix socket or a
     loopback address reported by the connection itself, independent of any Auth URL); unknown means refused;
  4. the owner's resume_processing consent must be active (the database re-checks at ingestion);
  5. the profile must hold no records (the database's never-merge rule re-checks).

Then: register the resume metadata as the candidate (owner session), designate in ONE privileged transaction
(designate_fixture -> trace_ingest.designate_fixture: two UPDATEs, each scoped to the request identity's own profile
or resume and required to match exactly one row; executable by the runtime login only), and ingest through the existing trace_ingest path. If designation
fails, the registered metadata row is removed. If ingestion fails, the existing run bookkeeping applies and the
designation stays consistent (profile and resume designated together, no records). There is no endpoint that
designates anything by itself.
"""
import hashlib
import ipaddress
import json
import logging
import re
from typing import Optional
from uuid import UUID

import psycopg

from app.config import Settings
from app.services.candidate_profile import CandidateProfileService, ConsentRequired, IngestionConflict, OwnerContext
from app.services.candidate_profile.repository import IDENTITY_SQL, CandidateProfileError
from app.services.resume_extraction import FixtureExtractor
from app.services.resume_extraction.fixture import UnknownResume

logger = logging.getLogger(__name__)
SYNTHETIC_NOTICE = "Synthetic test resume: a curated synthetic fixture, not a real person. It was looked up, not parsed."


class UploadTooLarge(CandidateProfileError):
    """The upload exceeds the size limit (413)."""


class UnsupportedMediaType(CandidateProfileError):
    """The upload is not a PDF (415)."""


class UnsupportedDocument(CandidateProfileError):
    """The PDF is not one of the approved synthetic fixture resumes (422). Nothing is parsed or stored."""


class DemoFixturesDisabled(CandidateProfileError):
    """Synthetic fixture uploads are disabled or not allowed here (403): TRACE_DEMO_FIXTURES is not 1, or the
    database connection is not demonstrably local, or the database holds no approved fixtures."""


class DesignationRefused(CandidateProfileError):
    """The owner's profile or resume could not be designated (409): for example the profile is already designated
    for a different fixture."""


# --------------------------------------------------------------------------- guards
def database_is_local(conn: psycopg.Connection) -> bool:
    """True only if the open connection is to a unix socket or a loopback address, as reported by the connection."""
    try:
        host, hostaddr = conn.info.host, conn.info.hostaddr
    except Exception:                                     # cannot establish locality: refuse
        return False
    if host and host.startswith("/"):
        return True
    try:
        return bool(hostaddr) and ipaddress.ip_address(hostaddr).is_loopback
    except ValueError:
        return False


def approved_pair(conn: psycopg.Connection, fixture_id: str, sha256: str) -> bool:
    """The database allowlist holds exactly this (fixture id, hash) pair. Read in its own short transaction."""
    with conn.transaction():
        row = conn.execute("select count(*) from trace_ingest.approved_fixture_resumes where fixture_profile_id = %s "
                           "and pdf_sha256 = %s", (fixture_id, sha256)).fetchone()
    return row[0] == 1


def designate_fixture(conn: psycopg.Connection, ctx: OwnerContext, resume_id: UUID, fixture_id: str, sha256: str) -> None:
    """The only privileged write: mark the authenticated owner's own profile and the resume just registered as the
    synthetic fixture, in one transaction, through trace_ingest.designate_fixture. That function (definer, owned by the
    ingest writer, executable by the runtime login only, never by trace_app_user or any client role) re-checks the
    (fixture id, hash) allowlist pair and updates exactly one profile and one resume of the request identity, never a
    profile designated for another fixture. The identity is set transaction-locally, exactly as in owner_session.
    Anything else rolls back (DesignationRefused)."""
    try:
        with conn.transaction():
            conn.execute(IDENTITY_SQL, {"sub": str(ctx.owner_user_id),
                                        "claims": json.dumps({"sub": str(ctx.owner_user_id), "role": "authenticated"})})
            conn.execute("select trace_ingest.designate_fixture(%s, %s, %s, %s)",
                         (ctx.candidate_profile_id, resume_id, fixture_id, sha256))
    except psycopg.Error as exc:
        logger.warning("fixture designation failed: %s", type(exc).__name__)
        raise DesignationRefused("this profile could not be linked to that synthetic fixture") from exc


# --------------------------------------------------------------------------- upload flow
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]")


def safe_filename(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _SAFE_NAME.sub("_", base).strip()[:120]
    return cleaned or None


def check_upload(data: bytes, content_type: Optional[str], settings: Settings) -> None:
    if len(data) > settings.max_resume_bytes:
        raise UploadTooLarge("the file is larger than the upload limit")
    if (content_type or "").split(";")[0].strip().lower() != "application/pdf" or not data.startswith(b"%PDF-"):
        raise UnsupportedMediaType("upload a PDF (Content-Type: application/pdf)")


def upload_fixture_resume(conn: psycopg.Connection, service: CandidateProfileService, extractor: FixtureExtractor,
                          settings: Settings, ctx: OwnerContext, data: bytes, content_type: Optional[str],
                          filename: Optional[str]) -> dict:
    check_upload(data, content_type, settings)
    sha256 = hashlib.sha256(data).hexdigest()                       # computed from the received bytes only
    try:
        fixture_id = extractor.identify(data)
    except UnknownResume:
        raise UnsupportedDocument("only the approved synthetic fixture resumes are supported; this file was not processed") from None
    if not settings.trace_demo_fixtures or settings.trace_data_mode != "synthetic_demo":
        raise DemoFixturesDisabled("synthetic fixture uploads are disabled (TRACE_DEMO_FIXTURES is not enabled)")
    if not database_is_local(conn):
        raise DemoFixturesDisabled("synthetic fixture uploads are only allowed against a local database")
    if not approved_pair(conn, fixture_id, sha256):
        raise DemoFixturesDisabled("this database does not approve that synthetic fixture")
    if not service.consent_state(ctx.owner_user_id)["active"]:
        raise ConsentRequired("grant consent to process your resume before uploading")
    profile = service.get_profile(ctx)
    if profile.employment_history or profile.education or profile.skills or profile.career_breaks or profile.unattached_evidence \
            or any(profile.career_activities.values()):
        raise IngestionConflict("this profile already holds records; extraction never merges into an existing profile")

    resume_id = service.register_resume(ctx, len(data), sha256, safe_filename(filename))
    try:
        designate_fixture(conn, ctx, resume_id, fixture_id, sha256)
    except CandidateProfileError:
        service.remove_resume(ctx, resume_id)                       # nothing designated, nothing left behind
        raise
    outcome = service.ingest_extraction(ctx, resume_id, extractor.extract(data))
    p = service.get_profile(ctx)
    return {"status": outcome.status, "is_synthetic_test_data": True, "notice": SYNTHETIC_NOTICE,
            "extracted": {"employments": len(p.employment_history), "education": len(p.education), "skills": len(p.skills),
                          "career_breaks": len(p.career_breaks),
                          "evidence": sum(len(j.responsibilities) + len(j.achievements) + len(j.other_evidence) for j in p.employment_history)
                          + len(p.unattached_evidence)}}
