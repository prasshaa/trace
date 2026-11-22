"""A user's own resume (Phase 6.1): validate, parse locally, de-identify, then ingest into THEIR private profile.

Order, nothing written until every check has passed:
  1. size limit and PDF signature (check_upload); real uploads enabled on this server (TRACE_REAL_RESUME_UPLOADS);
  2. current resume_processing consent; a non-fixture profile with no records (ingestion never merges);
  3. parse in memory (real_parser), de-identify (deidentification.deidentify) with the header identity and the
     account's own identity (email and name from Supabase Auth, read through a runtime-only function); unless that
     failed, keep the identity used in the restricted marker store (contributions.identity) for later rebuilds;
  4. passed:       register resume metadata (size and hash only; no filename, no bytes), record the admission
                   (trace_ingest.admit_deidentified_resume), ingest through the shared ingestion path (pending drafts);
     needs_review: hold the de-identified record in trace_private.resume_quarantine for a reviewer; nothing reaches
                   the candidate tables;
     failed:       nothing is stored except the outcome and reason codes.
The original bytes are never stored or written to disk, and no content is logged.

A profile that already holds records (product Phase 2): ingestion still never merges. The same checks run (switch,
consent, size and signature, local parse, de-identification), then the de-identified record is compared with the profile
and becomes UPDATE PROPOSALS that the user accepts or dismisses one by one; nothing enters the profile on its own. The
same file again (same SHA-256) is reported as unchanged. A version that needs a manual privacy review is not proposed
(nothing is stored).
"""
import hashlib
import json
import logging
from typing import Optional

import psycopg

from app.config import Settings
from app.demo.fixtures import check_upload
from app.services.candidate_profile import CandidateProfileService, ConsentRequired, OwnerContext
from app.services.candidate_profile.models import DateInput
from app.services.candidate_profile.repository import IDENTITY_SQL, CandidateProfileError
from app.services.contributions.identity import forget_identity, load_identity, store_identity
from app.services.contributions.sealing import sealer_from
from app.services.deidentification import Identity, deidentify
from app.services.resume_extraction.contract import (
    ExtractedActivity, ExtractedEducation, ExtractedEmployer, ExtractedEmployment, ExtractedEvidence, ExtractedSkill,
    ExtractionResult,
)
from app.services.resume_extraction.real_parser import PARSER_VERSION, parse_resume

logger = logging.getLogger(__name__)
CONFIDENCE = "0.600"
ACTIVITY = {"certification": "certification", "project": "independent_project", "award": "award"}


class RealUploadsDisabled(CandidateProfileError):
    """Real resume processing is not enabled on this server (403)."""


class ResumeNotProcessable(CandidateProfileError):
    """The resume could not be read or safely de-identified (422). Nothing was stored."""


class FixtureProfileConflict(CandidateProfileError):
    """A synthetic demo profile cannot take a real resume (409)."""


def as_identity(conn: psycopg.Connection, owner_user_id, header: dict, sealer=None) -> Identity:
    """The account identity and any markers already kept, extended with the resume header's name and contact details."""
    known, _ = load_identity(conn, owner_user_id, sealer)
    names = [*known.names(), *header.get("names", [])]
    return Identity(full_name=names[0] if names else "", other_names=names[1:], emails=[*known.emails, *header.get("emails", [])],
                    phones=[*known.phones, *header.get("phones", [])], urls=[*known.urls, *header.get("urls", [])],
                    handles=list(known.handles))


def store_identity_if_contributing(conn: psycopg.Connection, owner_user_id, identity, sealer) -> bool:
    """Seal and keep the owner's identity markers only while their contribution consent is active (D48). Returns
    whether they were stored. Without that consent they are used in memory for this upload and then discarded."""
    with conn.transaction():
        row = conn.execute("select consent_active from trace_contrib.contribution_context(%s)", (owner_user_id,)).fetchone()
    if not (row and row[0]):
        return False
    store_identity(conn, owner_user_id, identity, sealer)
    return True


def _owner_call(conn: psycopg.Connection, owner_user_id, sql: str, params: tuple) -> None:
    """A runtime-only function, called with the verified owner as the transaction-local request identity."""
    with conn.transaction():
        conn.execute(IDENTITY_SQL, {"sub": str(owner_user_id), "claims": json.dumps({"sub": str(owner_user_id), "role": "authenticated"})})
        conn.execute(sql, params)


def admit_resume(conn: psycopg.Connection, ctx: OwnerContext, resume_id, sha256: str, pipeline_version: str) -> None:
    """Record that de-identification passed for exactly this resume of the verified owner's own profile. The only
    trace_ingest call outside the owner-scoped repository (runtime-only database function; identity-scoped)."""
    _owner_call(conn, ctx.owner_user_id, "select trace_ingest.admit_deidentified_resume(%s, %s, %s, %s)",
                (ctx.candidate_profile_id, resume_id, sha256, pipeline_version))


def _d(value: Optional[str], precision: Optional[str]) -> DateInput:
    if precision == "ongoing":
        return DateInput(precision="ongoing")
    if not value or precision in (None, "unknown"):
        return DateInput()
    parts = value.split("-")
    return DateInput(year=int(parts[0]), month=int(parts[1]) if precision == "month" else None, precision=precision)


def to_extraction(record: dict, sha256: str) -> ExtractionResult:
    jobs = record.get("employments", [])
    employers = [ExtractedEmployer(key=e["employer_id"], employer_name=e.get("employer_name"), source_location="Experience, employer line",
                                   extraction_confidence=CONFIDENCE) for e in record.get("employers", [])]
    employments = [ExtractedEmployment(key=f"J{j['sequence_index']}", sequence_index=j["sequence_index"], employer_key=j.get("employer_id"),
                                       title_raw=j["title"], start=_d(j.get("start"), j.get("start_precision")),
                                       end=_d(j.get("end"), j.get("end_precision")),
                                       source_location=f"Experience, entry {j['sequence_index']}", extraction_confidence=CONFIDENCE)
                   for j in jobs if j.get("title")]
    seqs = {e.sequence_index for e in employments}
    evidence = [ExtractedEvidence(key=f"X{i}", evidence_type=x.get("evidence_type", "responsibility"), evidence_text=x["text"],
                                  employment_key=f"J{x['employment_seq']}" if x.get("employment_seq") in seqs else None,
                                  source_location=f"Experience, entry {x.get('employment_seq')}, bullet {i}",
                                  extraction_confidence=CONFIDENCE)
                for i, x in enumerate(record.get("evidence", []), 1) if x.get("text")]
    education = [ExtractedEducation(key=f"D{i}", qualification_title_raw=d.get("qualification_title"),
                                    qualification_level=d.get("qualification_level") or "unknown", institution_name=d.get("institution_name"),
                                    field_of_study=d.get("field_of_study"), notes=f"Grade: {d['grade_raw']}" if d.get("grade_raw") else None,
                                    end=_d(d.get("end"), d.get("end_precision")), source_location=f"Education, entry {i}",
                                    extraction_confidence=CONFIDENCE)
                 for i, d in enumerate(record.get("education", []), 1)]
    activities = [ExtractedActivity(key=f"A{i}", activity_type=ACTIVITY.get(a.get("activity_type"), "other"), title_raw=a["title"],
                                    organisation_raw=a.get("organisation"), description_raw=a.get("description"),
                                    end=DateInput(year=a["year"], precision="year") if a.get("year") else DateInput(),
                                    source_location=f"{a.get('activity_type', 'Other').capitalize()}s, entry {i}",
                                    extraction_confidence=CONFIDENCE)
                  for i, a in enumerate(record.get("activities", []), 1) if a.get("title")]
    skills = [ExtractedSkill(skill_text_raw=k["text"], source_location=f"Skills, item {i}", extraction_confidence=CONFIDENCE)
              for i, k in enumerate(record.get("skills", []), 1) if k.get("text")]
    return ExtractionResult(extractor_name="trace-resume-parser", extractor_version=PARSER_VERSION, source_sha256=sha256,
                            employers=employers, employments=employments, education=education, career_activities=activities,
                            evidence=evidence, skills=skills)


def process_real_resume(conn: psycopg.Connection, service: CandidateProfileService, settings: Settings, ctx: OwnerContext,
                        data: bytes, content_type: Optional[str]) -> dict:
    check_upload(data, content_type, settings)
    if not settings.real_resume_uploads_available:        # switched on AND an identity-marker key configured
        raise RealUploadsDisabled("processing your own resume is not enabled on this server yet")
    sealer = sealer_from(settings)
    if not service.consent_state(ctx.owner_user_id)["active"]:
        raise ConsentRequired("grant consent to process your resume before uploading")
    with conn.transaction():
        row = conn.execute("select is_fixture_profile from trace_contrib.contribution_context(%s)", (ctx.owner_user_id,)).fetchone()
    if row and row[0]:
        raise FixtureProfileConflict("this profile holds a synthetic sample; it cannot take your own resume")
    profile = service.get_profile(ctx)
    updating = bool(profile.employment_history or profile.education or profile.skills or profile.career_breaks
                    or profile.unattached_evidence or any(profile.career_activities.values()))
    sha256 = hashlib.sha256(data).hexdigest()
    if updating and service.source_seen(ctx, sha256):
        return {"status": "unchanged", "extracted": None}
    try:
        parsed = parse_resume(data)
    except Exception as exc:                                              # unreadable PDF: no detail, no content
        logger.info("resume could not be read: %s", type(exc).__name__)
        _owner_call(conn, ctx.owner_user_id, "select trace_contrib.log_resume_processing('failed', array['unreadable'])", ())
        raise ResumeNotProcessable("this PDF could not be read; try exporting it again as a text-based PDF") from None
    if not parsed.record["employments"] and not parsed.record["education"]:
        _owner_call(conn, ctx.owner_user_id, "select trace_contrib.log_resume_processing('failed', array['no_career_content'])", ())
        raise ResumeNotProcessable("no work experience or education could be found in this PDF")
    identity = as_identity(conn, ctx.owner_user_id, parsed.identity, sealer)
    result = deidentify(parsed.record, identity, data)
    if result.status == "failed":
        _owner_call(conn, ctx.owner_user_id, "select trace_contrib.log_resume_processing('failed', array['deidentification_failed'])", ())
        raise ResumeNotProcessable("this resume could not be safely de-identified, so nothing was stored")
    # The identity used here (account, header, PDF) is kept in the restricted marker store ONLY for an owner whose
    # contribution consent is active (D48): later contribution rebuilds use it to find these identifiers again in edited
    # text. It is stored after the records it protects, so a failure leaves no markers behind.
    if updating:
        if result.status == "needs_review":
            raise ResumeNotProcessable("this version of your resume could not be checked for personal details automatically, "
                                       "so nothing was stored; add any new details by hand instead")
        batch = service.propose_from_record(ctx, result.record, "resume_update", sha256,
                                            f"{PARSER_VERSION}+{result.report['pipeline_version']}")
        store_identity_if_contributing(conn, ctx.owner_user_id, result.identity or identity, sealer)
        return {"status": "proposed", "extracted": None, "proposals": batch}
    if result.status == "needs_review":
        _owner_call(conn, ctx.owner_user_id, "select trace_contrib.quarantine_resume(%s, %s, %s, %s)",
                    (ctx.candidate_profile_id, sha256, json.dumps(result.record), json.dumps({**result.report, "byte_size": len(data)})))
        store_identity_if_contributing(conn, ctx.owner_user_id, result.identity or identity, sealer)
        return {"status": "awaiting_manual_review", "extracted": None}
    extraction = to_extraction(result.record, sha256)
    resume_id = service.register_resume(ctx, len(data), sha256, None)                 # no filename: it may carry a name
    try:
        admit_resume(conn, ctx, resume_id, sha256, result.report["pipeline_version"])
        service.ingest_extraction(ctx, resume_id, extraction, real=True)
    except (CandidateProfileError, psycopg.Error):
        service.remove_resume(ctx, resume_id)                       # its admission goes with it (cascade)
        forget_identity(conn, ctx.owner_user_id)                    # markers no admitted or held resume needs
        raise
    _owner_call(conn, ctx.owner_user_id, "select trace_contrib.log_resume_processing('admitted', '{}')", ())
    store_identity_if_contributing(conn, ctx.owner_user_id, result.identity or identity, sealer)
    p = service.get_profile(ctx)
    return {"status": "processed", "extracted": {
        "employments": len(p.employment_history), "education": len(p.education), "skills": len(p.skills),
        "evidence": sum(len(j.responsibilities) + len(j.achievements) + len(j.other_evidence) for j in p.employment_history)
        + len(p.unattached_evidence), "activities": sum(len(v) for v in p.career_activities.values())}}
