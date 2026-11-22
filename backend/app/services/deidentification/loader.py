"""Admit a de-identified participant record into the consented real reference pool (admin identity, one transaction).

Admission needs ALL of:
  * de-identification status 'passed' (pipeline.deidentify; ambiguities approved by a named reviewer);
  * a consent attestation that is 'verified', not withdrawn, and covers every purpose the pool serves
    (deidentified_career_analysis, aggregate_research, benchmarking, recommendations);
  * a named reviewer for the record.
Otherwise the record is QUARANTINED: only the participant reference (opaque), the outcome and reason codes are logged.
No career data, no identity and no report values are stored for it.

On admission, in one transaction: the contributor (opaque participant reference, never a name/email/phone), the
attestation (basis, purposes, evidence reference, original date and notice version only when evidenced, verification),
the contribution (random id, de-identification report of counts only) and the reference rows (dataset_class
consented_real_reference, random profile id, no contributor identity on any analytical row).
"""
import json
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import psycopg

from app.services.deidentification.pipeline import PIPELINE_VERSION, Result

POOL_PURPOSES = ("deidentified_career_analysis", "aggregate_research", "benchmarking", "recommendations")
SOURCE_ID = "SRC-CONTRIB-001"


@dataclass
class Attestation:
    basis: str                                     # product_owner_attestation | signed_consent_form | recorded_verbal_consent
    purposes: Sequence[str]
    evidence_reference: str                        # where the evidence lives (non-identifying)
    attested_by: str                               # the attesting role or person (e.g. "Product owner")
    verification_status: str = "pending"           # pending | verified | rejected
    verified_by: Optional[str] = None
    verified_at: Optional[str] = None              # ISO timestamp of the verification
    original_consent_date: Optional[str] = None    # only when evidenced
    original_notice_version: Optional[str] = None  # only when evidenced


def admission_problems(result: Result, attestation: Attestation, reviewer: Optional[str]) -> List[str]:
    reasons = []
    if result.status != "passed":
        reasons.append(f"deidentification_{result.status}")
    if attestation.verification_status != "verified" or not attestation.verified_by or not attestation.verified_at:
        reasons.append("consent_not_verified")
    missing = [p for p in POOL_PURPOSES if p not in attestation.purposes]
    if missing:
        reasons.append("consent_scope_incomplete")
    if not reviewer:
        reasons.append("no_reviewer")
    return reasons


def _date(value: Optional[str], precision: Optional[str]) -> Tuple[Optional[int], Optional[int], str]:
    if precision == "ongoing":
        return None, None, "ongoing"
    if not value or precision in (None, "unknown"):
        return None, None, "unknown"
    parts = str(value).split("-")
    year = int(parts[0])
    month = int(parts[1]) if precision == "month" and len(parts) > 1 else None
    return year, month, precision


def _contributor(conn, participant_reference: str):
    row = conn.execute("select contributor_id from trace_private.contributors where participant_reference = %s",
                       (participant_reference,)).fetchone()
    if row:
        return row[0]
    return conn.execute("insert into trace_private.contributors (source_kind, participant_reference) values "
                        "('legacy_participant', %s) returning contributor_id", (participant_reference,)).fetchone()[0]


def quarantine(conn: psycopg.Connection, participant_reference: str, reasons: List[str]) -> None:
    with conn.transaction():
        contributor = _contributor(conn, participant_reference)
        conn.execute("insert into trace_private.import_log (contributor_id, outcome, reason_codes, pipeline_version) "
                     "values (%s, 'quarantined', %s, %s)", (contributor, reasons, PIPELINE_VERSION))


def admit(conn: psycopg.Connection, participant_reference: str, result: Result, attestation: Attestation,
          reviewer: Optional[str]) -> Optional[str]:
    """Admit or quarantine. Returns the new reference profile id, or None when quarantined."""
    reasons = admission_problems(result, attestation, reviewer)
    if reasons:
        quarantine(conn, participant_reference, reasons)
        return None
    rec, pid = result.record, result.profile_id
    with conn.transaction():
        contributor = _contributor(conn, participant_reference)
        conn.execute(
            "insert into trace_private.consent_attestations (contributor_id, basis, purposes, evidence_reference, attested_by, "
            "original_consent_date, original_notice_version, verification_status, verified_by, verified_at) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (contributor, attestation.basis, list(attestation.purposes), attestation.evidence_reference, attestation.attested_by,
             attestation.original_consent_date, attestation.original_notice_version, attestation.verification_status,
             attestation.verified_by, attestation.verified_at))
        contribution = conn.execute(
            "insert into trace_private.contributions (contributor_id, profile_id, status, deidentification_status, "
            "deidentification_report, pipeline_version, reviewed_by, reviewed_at, admitted_at) "
            "values (%s, %s, 'admitted', 'passed', %s, %s, %s, now(), now()) returning contribution_id",
            (contributor, pid, json.dumps(result.report), PIPELINE_VERSION, reviewer)).fetchone()[0]
        y, mth, prec = _date(rec.get("resume_as_of"), rec.get("resume_as_of_precision") or "unknown")
        conn.execute("insert into trace_ref.profiles (profile_id, source_id, dataset_class, country_code, resume_as_of_year, "
                     "resume_as_of_month, resume_as_of_precision, review_status, synthetic_demo_allowed) values "
                     "(%s, %s, 'consented_real_reference', %s, %s, %s, %s, 'reviewer_confirmed', false)",
                     (pid, SOURCE_ID, rec.get("country_code") or "IN", y, mth, prec if prec != "unknown" or y else "unknown"))
        employer_ids = {}
        for i, e in enumerate(rec.get("employers", []), 1):
            eid = f"{pid}-ORG-{i:02d}"
            employer_ids[e.get("employer_id")] = eid
            conn.execute("insert into trace_ref.employers (profile_id, employer_id, employer_name, industry_raw, industry_sector, "
                         "company_model, business_model, company_size_band, attribute_provenance) "
                         "values (%s, %s, %s, %s, %s, %s, %s, %s, 'resume_stated')",
                         (pid, eid, e.get("employer_name"), e.get("industry_raw"), e.get("industry_sector") or "unknown",
                          e.get("company_model") or "unknown", e.get("business_model") or "unknown",
                          e.get("company_size_band") or "unknown"))
        employment_ids = {}
        for e in rec.get("employments", []):
            seq = int(e["sequence_index"])
            emp_id = f"{pid}-EMP-{seq:02d}"
            employment_ids[seq] = emp_id
            sy, sm, sp = _date(e.get("start"), e.get("start_precision"))
            ey, em, ep = _date(e.get("end"), e.get("end_precision"))
            conn.execute(
                "insert into trace_ref.employments (employment_id, profile_id, sequence_index, employer_id, title_raw_redacted, "
                "role_id, seniority_code, mapping_status, mapping_confidence, mapping_note, employment_type, primary_career_sequence, "
                "counts_as_full_time_experience, classification_note, start_year, start_month, start_precision, end_year, end_month, "
                "end_precision, is_current_as_stated, extraction_status) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s, %s, %s, %s, %s, 'document_verified')",
                (emp_id, pid, seq, employer_ids.get(e.get("employer_id")), e.get("title"), e.get("role_id") or "ROLE-UNKNOWN",
                 e.get("seniority_code"), e.get("mapping_status") or "unresolved", e.get("mapping_confidence") or 0,
                 e.get("mapping_note"), e.get("employment_type") or "unknown", bool(e.get("primary_career_sequence", True)),
                 e.get("counts_as_full_time_experience"), e.get("classification_note"), sy, sm, sp, ey, em, ep,
                 bool(e.get("is_current_as_stated"))))
        for i, d in enumerate(rec.get("education", []), 1):
            sy, sm, sp = _date(d.get("start"), d.get("start_precision"))
            ey, em, ep = _date(d.get("end"), d.get("end_precision"))
            conn.execute(
                "insert into trace_ref.education (education_id, profile_id, qualification_level, qualification_title, field_of_study, "
                "institution_name, study_mode, completion_status, grade_raw, grade_scale, grade_value, start_year, start_month, "
                "start_precision, end_year, end_month, end_precision) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s)",
                (f"{pid}-EDU-{i:02d}", pid, d.get("qualification_level") or "unknown", d.get("qualification_title"),
                 d.get("field_of_study"), d.get("institution_name"), d.get("study_mode") or "unknown",
                 d.get("completion_status") or "unknown", d.get("grade_raw"), d.get("grade_scale"), d.get("grade_value"),
                 sy, sm, sp, ey, em, ep))
        for i, x in enumerate(rec.get("evidence", []), 1):
            conn.execute(
                "insert into trace_ref.experience_evidence (evidence_id, profile_id, employment_id, evidence_type, responsibility_area, "
                "evidence_text_redacted, provenance, evidence_status) values (%s, %s, %s, %s, %s, %s, 'resume_claim', 'document_verified')",
                (f"{pid}-EV-{i:02d}", pid, employment_ids.get(x.get("employment_seq")), x.get("evidence_type") or "other",
                 x.get("responsibility_area"), x.get("text")))
        for i, k in enumerate(rec.get("skills", []), 1):
            conn.execute("insert into trace_ref.profile_skills (profile_skill_id, profile_id, skill_id, skill_text_raw, employment_id, "
                         "evidence_source, verified) values (%s, %s, %s, %s, %s, 'resume_explicit', false)",
                         (f"{pid}-SKL-{i:02d}", pid, k.get("skill_id"), k.get("text"), employment_ids.get(k.get("employment_seq"))))
        for i, a in enumerate(rec.get("activities", []), 1):
            conn.execute("insert into trace_ref.activities (activity_id, profile_id, activity_type, title, organisation, "
                         "description_redacted, year) values (%s, %s, %s, %s, %s, %s, %s)",
                         (f"{pid}-ACT-{i:02d}", pid, a.get("activity_type") or "other", a.get("title"), a.get("organisation"),
                          a.get("description"), a.get("year")))
        for i, b in enumerate(rec.get("career_breaks", []), 1):
            sy, sm, sp = _date(b.get("start"), b.get("start_precision"))
            ey, em, ep = _date(b.get("end"), b.get("end_precision"))
            conn.execute("insert into trace_ref.career_breaks (break_id, profile_id, break_reason, start_year, start_month, "
                         "start_precision, end_year, end_month, end_precision) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                         (f"{pid}-BRK-{i:02d}", pid, b.get("break_reason") or "unspecified", sy, sm, sp, ey, em, ep))
        conn.execute("insert into trace_private.import_log (contributor_id, contribution_id, outcome, pipeline_version) "
                     "values (%s, %s, 'admitted', %s)", (contributor, contribution, PIPELINE_VERSION))
    return pid
