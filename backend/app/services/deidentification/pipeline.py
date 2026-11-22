"""De-identification of one participant's career record before it may enter the consented real reference pool.

Input (restricted staging only): the structured career record (the canonical import format below), the owner's known
identity, and optionally the original PDF. Output: a de-identified record, a status and a report that contains
categories, counts and checklist results only (never a matched value, a name or a filename).

Steps
  1. Inspect the PDF (if given) in memory: document metadata, link annotations, text and images. Emails, phones and
     links found there are added to the known identity so they are also removed from the structured record; the PDF
     itself, its metadata and its filename are never stored.
  2. Keep only the allowed career fields (FIELDS). Anything else (name, email, phone, address, links, photo,
     references, ...) is dropped and counted.
  3. Redact direct identifiers from EVERY retained text value (detector.redact), protecting the record's own employer
     and institution names.
  4. Scan the output again (detector.residual). Any residual direct identifier -> 'failed'. Ambiguities (for example a
     name part that is also an employer word) -> 'needs_review' unless a person approved them.
  5. Generate a random reference profile id. Never derived from the person, their email or the file.
Retained on purpose: employers, universities and colleges, qualifications, grades, dates, titles, industries,
responsibilities, skills, achievements, certifications and projects.
"""
import io
import re
import secrets
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.services.deidentification.detector import EMAIL, URL, BARE_PROFILE, PHONE_CANDIDATE, Findings, Identity, redact, residual

PIPELINE_VERSION = "trace-deid-1.0"

# The canonical import format: which keys are kept, per section. Everything else is dropped.
FIELDS: Dict[str, set] = {
    "_root": {"resume_as_of", "resume_as_of_precision", "country_code", "employers", "employments", "education", "evidence",
              "skills", "activities", "career_breaks", "unresolved"},
    "employers": {"employer_id", "employer_name", "industry_raw", "industry_sector", "company_model", "business_model",
                  "company_size_band"},
    "employments": {"sequence_index", "employer_id", "title", "role_id", "seniority_code", "mapping_status",
                    "mapping_confidence", "mapping_note", "employment_type", "primary_career_sequence",
                    "counts_as_full_time_experience", "classification_note", "start", "start_precision", "end",
                    "end_precision", "is_current_as_stated", "source_text", "review_flags"},
    "education": {"qualification_level", "qualification_title", "field_of_study", "institution_name", "study_mode",
                  "completion_status", "grade_raw", "grade_scale", "grade_value", "start", "start_precision", "end",
                  "end_precision"},
    "evidence": {"employment_seq", "evidence_type", "text", "responsibility_area", "evidence_role"},
    "skills": {"skill_id", "text", "employment_seq"},
    "activities": {"activity_type", "title", "organisation", "description", "year"},
    "career_breaks": {"break_reason", "start", "start_precision", "end", "end_precision"},
    # Resume import only: text the parser could not place, shown to the owner for review. Redacted like any other text.
    "unresolved": {"unresolved_kind", "section", "text", "title", "employer", "start", "start_precision", "end", "end_precision",
                   "description"},
}
# Text values that are categorical codes, dates or numbers: validated, not redacted.
NOT_TEXT = {"resume_as_of", "resume_as_of_precision", "country_code", "employer_id", "industry_sector", "company_model",
            "business_model", "company_size_band", "sequence_index", "role_id", "seniority_code", "mapping_status",
            "mapping_confidence", "employment_type", "primary_career_sequence", "counts_as_full_time_experience", "start",
            "start_precision", "end", "end_precision", "is_current_as_stated", "qualification_level", "study_mode",
            "completion_status", "grade_scale", "grade_value", "employment_seq", "evidence_type", "responsibility_area",
            "skill_id", "activity_type", "year", "break_reason", "unresolved_kind", "section", "evidence_role"}
PROTECTED = {("employers", "employer_name"), ("education", "institution_name")}


@dataclass
class PdfInspection:
    readable: bool
    pages: int = 0
    images: int = 0
    metadata_fields_with_identity: int = 0
    metadata_fields_present: int = 0
    link_annotations: int = 0
    found_emails: List[str] = field(default_factory=list)       # in memory only; used to extend the identity
    found_phones: List[str] = field(default_factory=list)
    found_urls: List[str] = field(default_factory=list)
    text_chars: int = 0


def inspect_pdf(data: bytes, identity: Identity) -> PdfInspection:
    """In memory only. Nothing from the PDF is written anywhere."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = list(reader.pages)
    except Exception:
        return PdfInspection(readable=False)
    out = PdfInspection(readable=True, pages=len(pages))
    meta = reader.metadata or {}
    tokens = identity.name_tokens() | {e.lower() for e in identity.emails}
    for key, value in dict(meta).items():
        if value is None or not str(value).strip():
            continue
        out.metadata_fields_present += 1
        v = str(value).lower()
        if any(t in v for t in tokens) or EMAIL.search(str(value)):
            out.metadata_fields_with_identity += 1
    text_parts = []
    for page in pages:
        try:
            text_parts.append(page.extract_text() or "")
        except Exception:
            pass
        try:
            out.images += len(page.images)
        except Exception:
            pass
        for annot in (page.get("/Annots") or []):
            try:
                a = annot.get_object()
                uri = a.get("/A", {}).get("/URI") if a.get("/A") else None
            except Exception:
                uri = None
            if uri:
                out.link_annotations += 1
                out.found_urls.append(str(uri))
    text = "\n".join(text_parts)
    out.text_chars = len(text)
    out.found_emails = sorted(set(EMAIL.findall(text)))
    out.found_phones = sorted({m.group(1).strip() for m in PHONE_CANDIDATE.finditer(text)
                               if 10 <= sum(c.isdigit() for c in m.group(1)) <= 13})
    out.found_urls += [m.group(0) for m in URL.finditer(text)] + [m.group(0) for m in BARE_PROFILE.finditer(text)]
    return out


@dataclass
class Result:
    status: str                       # passed | needs_review | failed
    record: Optional[dict]            # de-identified record (None when failed)
    profile_id: str
    report: dict                      # counts and checklist only
    # The owner's identity as extended from the PDF: in memory only, for the restricted marker store. Never serialised.
    identity: Optional[Identity] = field(default=None, repr=False, compare=False)


def new_reference_profile_id() -> str:
    """A random, non-identifying id in the reference format ('CRR-' + 12 random digits)."""
    return "CRR-" + "".join(str(secrets.randbelow(10)) for _ in range(12))


def _protected_terms(record: dict) -> List[str]:
    return [r.get("employer_name") or "" for r in record.get("employers", [])] + \
           [r.get("institution_name") or "" for r in record.get("education", [])]


def deidentify(record: dict, identity: Identity, pdf: Optional[bytes] = None, ambiguities_approved: bool = False) -> Result:
    identity = Identity(**{k: list(v) if isinstance(v, (list, tuple)) else v for k, v in identity.__dict__.items()})
    inspection = inspect_pdf(pdf, identity) if pdf is not None else None
    if inspection and inspection.readable:
        identity.emails = sorted(set(identity.emails) | set(inspection.found_emails))
        identity.phones = sorted(set(identity.phones) | set(inspection.found_phones))
        identity.urls = sorted(set(identity.urls) | set(inspection.found_urls))
    findings, dropped = Findings(), 0
    src = deepcopy(record)
    out: dict = {}
    for key, value in src.items():
        if key not in FIELDS["_root"]:
            dropped += 1
            continue
        out[key] = value
    protected = _protected_terms(out)
    for section in [k for k in FIELDS if k != "_root"]:
        rows = out.get(section) or []
        clean_rows = []
        for row in rows:
            clean = {}
            for k, v in row.items():
                if k not in FIELDS[section]:
                    dropped += 1
                    continue
                if isinstance(v, str) and k not in NOT_TEXT:
                    if (section, k) in PROTECTED:
                        v, f = redact(v, Identity(emails=identity.emails, phones=identity.phones, urls=identity.urls,
                                                  handles=identity.handles), ())
                        # A retained employer or institution name that contains one of the owner's name parts (a family
                        # business, an eponymous school) is kept but is a strong quasi-identifier: a person decides.
                        hits = sum(1 for t in identity.name_tokens() if re.search(rf"(?<![\w]){re.escape(t)}(?![\w])", v, re.I))
                        if hits:
                            f.add("name_part_in_retained_name", hits, ambiguous=True)
                    else:
                        v, f = redact(v, identity, protected)
                    findings.merge(f)
                clean[k] = v
            clean_rows.append(clean)
        if section in out:
            out[section] = clean_rows
    # Second scan over every retained text value.
    leftover = Findings()
    for section in [k for k in FIELDS if k != "_root"]:
        for row in out.get(section) or []:
            for k, v in row.items():
                if not isinstance(v, str) or k in NOT_TEXT:
                    continue
                if (section, k) in PROTECTED:                  # names kept; contact details and links still not allowed
                    leftover.merge(residual(v, Identity(emails=identity.emails, phones=identity.phones, urls=identity.urls,
                                                        handles=identity.handles)))
                else:
                    leftover.merge(residual(v, identity, protected))
    direct_residual = leftover.total_removed
    ambiguous = sum(findings.ambiguous.values()) + sum(leftover.ambiguous.values())
    status = "failed" if direct_residual else ("needs_review" if ambiguous and not ambiguities_approved else "passed")
    checklist = {
        "no_emails_remaining": not leftover.removed.get("email"), "no_phones_remaining": not leftover.removed.get("phone"),
        "no_links_remaining": not any(leftover.removed.get(c) for c in ("link_linkedin", "link_github", "link_social", "link_other")),
        "no_handles_remaining": not leftover.removed.get("handle"), "no_id_numbers_remaining": not leftover.removed.get("id_number"),
        "no_known_names_remaining": not leftover.removed.get("name"), "no_addresses_remaining": not leftover.removed.get("address"),
        "pdf_metadata_not_stored": True, "source_filename_not_stored": True, "source_text_not_stored": True,
        "ambiguities_reviewed": ambiguous == 0 or ambiguities_approved,
    }
    report = {
        "pipeline_version": PIPELINE_VERSION, "status": status, "removed": dict(sorted(findings.removed.items())),
        "ambiguous": dict(sorted({**findings.ambiguous, **leftover.ambiguous}.items())), "residual_direct_identifiers": direct_residual,
        "fields_dropped": dropped, "checklist": checklist,
        "pdf": None if inspection is None else {
            "readable": inspection.readable, "pages": inspection.pages, "images_not_stored": inspection.images,
            "metadata_fields_present": inspection.metadata_fields_present,
            "metadata_fields_with_identity": inspection.metadata_fields_with_identity,
            "link_annotations": inspection.link_annotations},
    }
    return Result(status=status, record=None if status == "failed" else out, profile_id=new_reference_profile_id(), report=report,
                  identity=identity)


def report_has_no_values(report: dict, identity: Identity) -> bool:
    """True when no identity value appears anywhere in the report (a self-check used before storing it)."""
    text = repr(report).lower()
    values = [*identity.names(), *identity.emails, *identity.phones, *identity.handles, *identity.urls, *identity.address_lines]
    return not any(v and v.strip() and v.strip().lower() in text for v in values) and not re.search(EMAIL, text)
