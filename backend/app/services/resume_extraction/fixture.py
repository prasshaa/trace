"""Deterministic fixture extractor (M3): synthetic resumes only.

Trace's 41 synthetic resume PDFs (data-foundation ``synthetic_resume_pdfs.zip``) were rendered from
the structured fixtures in ``synthetic_profiles.jsonl``. This extractor identifies a PDF by its exact
SHA-256 and returns the structured fixture it was rendered from as an ``ExtractionResult``. It reads
no PDF text, calls no model or external service, and accepts nothing but those 41 documents.

What is taken from the fixture, and what is not:

* Candidate content is taken as stated in the fixture: titles, employer name as printed on the resume
  (the fixture's pseudonymous employer id), industry and employer attributes, employment type, the
  primary-career flag and full-time override, partial dates, education, career breaks, evidence text
  and skill text. Some of these structured fields are not visibly printed in the synthetic PDFs (for
  example ``employment_type`` or ``primary_career_sequence``); for fixtures, the structured record
  the PDF was rendered from is the ground truth of what the resume states.
* Not taken: taxonomy mappings (``role_id``, ``seniority_code``, ``mapping_*``, ``skill_id``,
  ``responsibility_area``) and the fixture's curation metadata (``attribute_provenance``,
  ``provenance``, ``evidence_status``, ``confidence``, ``verified``, ``data_quality_flags``,
  scenario tags). Mapping is a separate trusted process; review and verification belong to the
  candidate and to verification workflows.
* ``extraction_confidence`` is 1.000 for every record: the fixture source is exact. It says nothing
  about mapping confidence, review or verification.
* ``source_location`` names the resume section and entry the record is rendered in
  (``scripts/render_synthetic_resumes.py``), for example "Professional experience, entry 2, bullet 1".
"""
import hashlib
import json
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.services.candidate_profile.models import DateInput
from app.services.resume_extraction.contract import (
    ExtractedCareerBreak, ExtractedEducation, ExtractedEmployer, ExtractedEmployment, ExtractedEvidence,
    ExtractedSkill, ExtractionResult,
)

EXTRACTOR_NAME = "trace-fixture-extractor"
EXTRACTOR_VERSION = "fixture-1"
EXACT = Decimal("1.000")
PDF_NAME = "{profile_id}_synthetic_resume.pdf"


class UnknownResume(ValueError):
    """The document is not one of the synthetic fixture resumes."""


def _date(value: Optional[str], precision: str) -> DateInput:
    if precision == "month":
        y, m = value.split("-")
        return DateInput(year=int(y), month=int(m), precision="month")
    if precision == "year":
        return DateInput(year=int(value), precision="year")
    return DateInput(precision=precision)          # 'unknown' or 'ongoing': nothing to parse


class FixtureExtractor:
    """``FixtureExtractor(profiles_path, pdf_zip_path)``. Both files are read once; every PDF in the
    archive must belong to exactly one synthetic fixture and every fixture must have its PDF."""

    name = EXTRACTOR_NAME
    version = EXTRACTOR_VERSION

    def __init__(self, profiles_path: Path, pdf_zip_path: Path):
        with open(profiles_path, encoding="utf-8") as f:
            profiles = [json.loads(line) for line in f if line.strip()]
        self._profiles: Dict[str, dict] = {}
        for p in profiles:
            if p.get("dataset_class") != "synthetic_test_fixture":
                raise ValueError(f"{p.get('profile_id')} is not a synthetic test fixture")
            self._profiles[p["profile_id"]] = p
        self._pdfs: Dict[str, bytes] = {}
        self._by_sha: Dict[str, str] = {}
        with zipfile.ZipFile(pdf_zip_path) as z:
            for pid in self._profiles:
                data = z.read(PDF_NAME.format(profile_id=pid))
                sha = hashlib.sha256(data).hexdigest()
                if sha in self._by_sha:
                    raise ValueError(f"{pid} and {self._by_sha[sha]} share one PDF")
                self._pdfs[pid], self._by_sha[sha] = data, pid
            extra = set(z.namelist()) - {PDF_NAME.format(profile_id=p) for p in self._profiles}
            if extra:
                raise ValueError(f"unexpected files in the fixture archive: {sorted(extra)}")

    # ------------------------------------------------------------------ fixtures
    @property
    def fixture_ids(self) -> Tuple[str, ...]:
        return tuple(sorted(self._profiles))

    def resume_bytes(self, fixture_profile_id: str) -> bytes:
        return self._pdfs[fixture_profile_id]

    def fixture(self, fixture_profile_id: str) -> dict:
        return self._profiles[fixture_profile_id]

    def identify(self, document: bytes) -> str:
        sha = hashlib.sha256(document).hexdigest()
        try:
            return self._by_sha[sha]
        except KeyError:
            raise UnknownResume("not a synthetic fixture resume") from None

    # ------------------------------------------------------------------ extraction
    def extract(self, document: bytes) -> ExtractionResult:
        pid = self.identify(document)
        p = self._profiles[pid]
        employments = sorted(p["employments"], key=lambda e: e["sequence_index"])
        entry_of = {e["employment_id"]: i for i, e in enumerate(employments, 1)}
        first_entry_of_employer: Dict[str, int] = {}
        for e in employments:
            if e["employer_id"]:
                first_entry_of_employer.setdefault(e["employer_id"], entry_of[e["employment_id"]])

        employers = [ExtractedEmployer(
            key=r["employer_id"], employer_name=r["employer_id"],    # printed on the resume as the employer
            industry_raw=r.get("industry_raw"), industry_sector=r["industry_sector"], company_model=r["company_model"],
            business_model=r["business_model"], company_size_band=r["company_size_band"],
            source_location=f"Professional experience, entry {first_entry_of_employer.get(r['employer_id'], 0)}, employer line",
            extraction_confidence=EXACT) for r in p["employers"]]

        jobs = [ExtractedEmployment(
            key=e["employment_id"], sequence_index=e["sequence_index"], employer_key=e["employer_id"],
            title_raw=e["title_raw_redacted"], employment_type=e["employment_type"],
            primary_career_sequence=e["primary_career_sequence"],
            counts_as_full_time_experience=e["counts_as_full_time_experience"], classification_note=e["classification_note"],
            start=_date(e.get("start"), e["start_precision"]), end=_date(e.get("end"), e["end_precision"]),
            source_location=f"Professional experience, entry {entry_of[e['employment_id']]}",
            extraction_confidence=EXACT) for e in employments]

        bullets: Dict[str, int] = {}
        evidence: List[ExtractedEvidence] = []
        for x in p["evidence"]:
            bullets[x["employment_id"]] = bullets.get(x["employment_id"], 0) + 1
            evidence.append(ExtractedEvidence(
                key=x["evidence_id"], evidence_type=x["evidence_type"], evidence_text=x["text"],
                employment_key=x["employment_id"],
                source_location=f"Professional experience, entry {entry_of[x['employment_id']]}, bullet {bullets[x['employment_id']]}",
                extraction_confidence=EXACT))

        education = [ExtractedEducation(
            key=d["education_id"], qualification_level=d["qualification_level"], field_of_study=d.get("field_of_study"),
            study_mode=d["study_mode"], completion_status=d["completion_status"],
            start=_date(d.get("start"), d["start_precision"]), end=_date(d.get("end"), d["end_precision"]),
            source_location=f"Education, entry {i}", extraction_confidence=EXACT)
            for i, d in enumerate(p.get("education", []), 1)]

        breaks = [ExtractedCareerBreak(
            break_reason=b["break_reason"], note=b.get("note_redacted"),
            start=_date(b.get("start"), b["start_precision"]), end=_date(b.get("end"), b["end_precision"]),
            source_location=f"Career breaks, entry {i}", extraction_confidence=EXACT)
            for i, b in enumerate(p.get("career_breaks", []), 1)]

        skills = [ExtractedSkill(
            skill_text_raw=s.get("skill_text_raw") or s["skill_id"], employment_key=s.get("employment_id"),
            source_location=f"Skills, item {i}", extraction_confidence=EXACT)
            for i, s in enumerate(p.get("skills", []), 1)]

        return ExtractionResult(
            extractor_name=self.name, extractor_version=self.version, source_sha256=hashlib.sha256(document).hexdigest(),
            fixture_profile_id=pid, resume_as_of=_date(p.get("resume_as_of"), p.get("resume_as_of_precision", "unknown")),
            employers=employers, employments=jobs, education=education, career_breaks=breaks, evidence=evidence,
            skills=skills)
