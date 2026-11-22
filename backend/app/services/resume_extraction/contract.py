"""Provider-agnostic contract for one resume extraction (M3).

An ``ExtractionResult`` describes what an extractor read from one resume, as draft candidate
records. It mirrors the candidate record types (employers, employment episodes, education, career
activities, career breaks, evidence and skills) and adds, per record, where in the document the
information was found (``source_location``) and how confident the extractor is
(``extraction_confidence``, 0 to 1).

Rules the contract enforces:

* Original values are kept as stated. Unknown stays unknown: a date the document does not give is
  ``precision = 'unknown'``, an absent field is ``None``. Nothing is normalised or inferred here.
* Extraction is not mapping. Taxonomy references (role, seniority, skill and responsibility-area ids,
  activity categories) are not part of the contract: mapping is a separate trusted process, and its
  confidence (``mapping_confidence``) is unrelated to ``extraction_confidence``.
* Extraction is not review or verification. No review status, verification status, origin or other
  trust field can be expressed; the database records every ingested record as
  ``source_extracted`` / ``pending_review`` / ``unverified``.
* ``key`` values only connect records inside one result (an employment to its employer, evidence to
  its employment, ...). They are never stored.

``ingestion_payload()`` is the exact JSON sent to ``trace_ingest.ingest_fixture_extraction``; the
database re-checks every field against its own allow-list.
"""
from decimal import Decimal
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.candidate_profile.models import ActivityType, DateInput, EmploymentType, EvidenceType

CONTRACT_VERSION = "1"
UNKNOWN = DateInput()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Located(_Strict):
    """Where the extractor found the record, and how sure it is."""
    source_location: str = Field(min_length=1, max_length=200)
    extraction_confidence: Decimal = Field(ge=0, le=1, max_digits=4, decimal_places=3)

    @field_validator("source_location")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("source_location must not be blank")
        return v


def _no_ongoing_start(v: DateInput) -> DateInput:
    if v.precision == "ongoing":
        raise ValueError("a start date cannot be 'ongoing'")
    return v


class ExtractedEmployer(_Located):
    """Employers carry no per-record provenance columns in the schema; their location and confidence
    are part of the contract but are not stored (the employer is recorded as 'resume_stated')."""
    key: str = Field(min_length=1, max_length=80)
    employer_name: Optional[str] = None
    industry_raw: Optional[str] = None
    industry_sector: str = "unknown"
    company_model: str = "unknown"
    business_model: str = "unknown"
    company_size_band: str = "unknown"


class ExtractedEmployment(_Located):
    key: str = Field(min_length=1, max_length=80)
    sequence_index: int = Field(ge=1)
    employer_key: Optional[str] = None
    title_raw: str = Field(min_length=1)
    employment_type: EmploymentType = "unknown"
    primary_career_sequence: bool = True
    counts_as_full_time_experience: Optional[bool] = None
    classification_note: Optional[str] = None
    start: DateInput = UNKNOWN
    end: DateInput = UNKNOWN

    _start = field_validator("start")(_no_ongoing_start)


class ExtractedEducation(_Located):
    key: str = Field(min_length=1, max_length=80)
    qualification_title_raw: Optional[str] = None
    qualification_level: str = "unknown"
    institution_name: Optional[str] = None
    field_of_study: Optional[str] = None
    study_mode: str = "unknown"
    completion_status: str = "unknown"
    notes: Optional[str] = None
    start: DateInput = UNKNOWN
    end: DateInput = UNKNOWN

    _start = field_validator("start")(_no_ongoing_start)


class ExtractedActivity(_Located):
    key: str = Field(min_length=1, max_length=80)
    activity_type: ActivityType
    title_raw: str = Field(min_length=1)
    organisation_raw: Optional[str] = None
    description_raw: Optional[str] = None
    contribution_raw: Optional[str] = None
    outcome_raw: Optional[str] = None
    link_url: Optional[str] = None
    status: str = "unknown"
    related_employment_key: Optional[str] = None
    credential_identifier_raw: Optional[str] = None
    start: DateInput = UNKNOWN
    end: DateInput = UNKNOWN
    expiry: DateInput = UNKNOWN

    _start = field_validator("start")(_no_ongoing_start)


class ExtractedCareerBreak(_Located):
    break_reason: str = "unspecified"
    note: Optional[str] = None
    start: DateInput = UNKNOWN
    end: DateInput = UNKNOWN

    _start = field_validator("start")(_no_ongoing_start)


class ExtractedEvidence(_Located):
    key: str = Field(min_length=1, max_length=80)
    evidence_type: EvidenceType
    evidence_text: str = Field(min_length=1)
    employment_key: Optional[str] = None
    activity_key: Optional[str] = None

    @model_validator(mode="after")
    def _one_parent(self):
        if self.employment_key and self.activity_key:
            raise ValueError("evidence attaches to at most one employment or activity")
        return self


class ExtractedSkill(_Located):
    skill_text_raw: str = Field(min_length=1)
    stated_proficiency_raw: Optional[str] = None
    employment_key: Optional[str] = None
    education_key: Optional[str] = None
    activity_key: Optional[str] = None
    evidence_key: Optional[str] = None

    @model_validator(mode="after")
    def _one_source(self):
        if sum(x is not None for x in (self.employment_key, self.education_key, self.activity_key, self.evidence_key)) > 1:
            raise ValueError("a skill links to at most one source record")
        return self


class ExtractionResult(_Strict):
    contract_version: Literal["1"] = CONTRACT_VERSION
    extractor_name: str = Field(min_length=1, max_length=64)
    extractor_version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")      # the resume document this result was read from
    fixture_profile_id: Optional[str] = Field(default=None, pattern=r"^SYN-[0-9]{3}$")
    resume_as_of: DateInput = UNKNOWN
    employers: List[ExtractedEmployer] = []
    employments: List[ExtractedEmployment] = []
    education: List[ExtractedEducation] = []
    career_activities: List[ExtractedActivity] = []
    career_breaks: List[ExtractedCareerBreak] = []
    evidence: List[ExtractedEvidence] = []
    skills: List[ExtractedSkill] = []

    @model_validator(mode="after")
    def _references_resolve(self):
        def keys(items, kind):
            seen = [i.key for i in items]
            if len(seen) != len(set(seen)):
                raise ValueError(f"{kind} keys must be unique")
            return set(seen)

        employers = keys(self.employers, "employer")
        employments = keys(self.employments, "employment")
        education = keys(self.education, "education")
        activities = keys(self.career_activities, "activity")
        evidence = keys(self.evidence, "evidence")
        seq = [e.sequence_index for e in self.employments]
        if len(seq) != len(set(seq)):
            raise ValueError("employment sequence_index values must be unique")

        def check(ref, known, what):
            if ref is not None and ref not in known:
                raise ValueError(f"unknown {what} key {ref!r}")

        for e in self.employments:
            check(e.employer_key, employers, "employer")
        for a in self.career_activities:
            check(a.related_employment_key, employments, "employment")
        for x in self.evidence:
            check(x.employment_key, employments, "employment")
            check(x.activity_key, activities, "activity")
        for s in self.skills:
            check(s.employment_key, employments, "employment")
            check(s.education_key, education, "education")
            check(s.activity_key, activities, "activity")
            check(s.evidence_key, evidence, "evidence")
        if self.resume_as_of.precision == "ongoing":
            raise ValueError("resume_as_of cannot be 'ongoing'")
        return self

    def ingestion_payload(self) -> dict:
        """The JSON document ``trace_ingest.ingest_fixture_extraction`` accepts. Dates are flattened to
        the schema's year/month/precision columns; employer location and confidence are not sent
        (the employers table has no column for them)."""
        def dates(**named: DateInput) -> Dict[str, object]:
            out: Dict[str, object] = {}
            for prefix, d in named.items():
                out.update({f"{prefix}_year": d.year, f"{prefix}_month": d.month, f"{prefix}_precision": d.precision})
            return out

        def located(r: _Located) -> Dict[str, object]:
            return {"source_location": r.source_location, "extraction_confidence": str(r.extraction_confidence)}

        payload: Dict[str, object] = {
            "contract_version": self.contract_version,
            "fixture_profile_id": self.fixture_profile_id,
            "source_sha256": self.source_sha256,
            "employers": [e.model_dump(exclude={"source_location", "extraction_confidence"}) for e in self.employers],
            "employments": [{**e.model_dump(exclude={"start", "end", "source_location", "extraction_confidence"}),
                             **dates(start=e.start, end=e.end), "is_current_as_stated": e.end.precision == "ongoing",
                             **located(e)} for e in self.employments],
            "education": [{**e.model_dump(exclude={"start", "end", "source_location", "extraction_confidence"}),
                           **dates(start=e.start, end=e.end), **located(e)} for e in self.education],
            "career_activities": [{**a.model_dump(exclude={"start", "end", "expiry", "source_location", "extraction_confidence"}),
                                   **dates(start=a.start, end=a.end, expiry=a.expiry), **located(a)}
                                  for a in self.career_activities],
            "career_breaks": [{**b.model_dump(exclude={"start", "end", "source_location", "extraction_confidence"}),
                               **dates(start=b.start, end=b.end), **located(b)} for b in self.career_breaks],
            "evidence": [{**x.model_dump(exclude={"source_location", "extraction_confidence"}), **located(x)} for x in self.evidence],
            "skills": [{**s.model_dump(exclude={"source_location", "extraction_confidence"}), **located(s)} for s in self.skills],
        }
        if self.resume_as_of.precision != "unknown":
            payload.update(dates(resume_as_of=self.resume_as_of))
        return payload
