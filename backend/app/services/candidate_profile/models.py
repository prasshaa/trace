"""Contracts for the candidate profile service (data foundation v2.2.0 candidate-side schema).

Trust boundary
--------------
* Content inputs (``*Create`` / ``*Update``) carry only what a candidate may author. They forbid
  unknown fields, so ``information_origin``, ``verification_status``, ``extraction_confidence``,
  ``source_resume_id``, ``extraction_run_id``, ``source_location``, legacy confidence/verification
  columns, ``owner_user_id`` and ``candidate_profile_id`` can never arrive through a payload.
* System-controlled classification is not candidate input either (M1). Employment role mapping
  (``role_id``, ``seniority_code``, ``mapping_status``, ``mapping_confidence``, ``mapping_note``) and
  activity taxonomy mapping (``category_code``, ``category_mapping_status``,
  ``category_mapping_confidence``) are written only by a trusted mapping process; candidate-entered
  records keep the database defaults (``ROLE-UNKNOWN`` / ``unresolved``). A candidate's own choice
  of role is deferred to a controlled operation in a later milestone. Candidates still classify their
  own records through ``activity_type``, ``employment_type``, ``qualification_level`` and similar.
* Review status, profile confirmation (``confirmation_status``, ``confirmed_at``), extraction state
  and every legacy trust column are not content either. Review changes only through the explicit
  review actions (see review.py).
* Since data foundation v2.3.0 (M2) the database refuses these columns too, for authenticated and
  service_role (column grants plus an insert guard); the models give the early, readable error.
* The owner and profile come only from a trusted :class:`OwnerContext` supplied by the caller.
* Output records expose provenance read-only. ``information_origin = None`` means the record was
  created before v2.2.0 and its origin was not recorded; it is never presented as confirmed.

Vocabularies that the database defines as CHECK lists (qualification levels, sectors, ...) are
validated by the database itself; the service maps violations to :class:`InvalidRecord` errors.
"""
from decimal import Decimal
from datetime import datetime
from typing import Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.career_history import dates
from app.services.career_history.models import PartialDateView

DatePrecision = Literal["month", "year", "unknown", "ongoing"]
EmploymentType = Literal["full_time", "part_time", "contract", "internship", "apprenticeship",
                         "freelance", "founder", "volunteer", "unknown"]
MappingStatus = Literal["confirmed", "auto_high_confidence", "needs_review", "unresolved"]
ActivityType = Literal["certification", "licence", "course", "independent_project", "portfolio_project", "award",
                       "publication", "research", "volunteering", "extracurricular", "leadership", "other"]
ACTIVITY_TYPES: tuple = ActivityType.__args__  # type: ignore[attr-defined]
InformationOrigin = Literal["user_stated", "source_extracted", "inferred"]
ReviewStatus = Literal["pending_review", "user_confirmed", "user_corrected", "rejected"]
VerificationStatus = Literal["unverified", "source_document_supported", "independently_verified"]
EvidenceType = Literal["responsibility", "achievement", "scope", "leadership", "skill_evidence", "other"]
RecordKind = Literal["employer", "employment", "education", "activity", "evidence", "skill", "career_break"]

# Columns no candidate input model may declare (tested). Written only by trusted backend processes,
# or (review_status) only through the explicit review actions.
SYSTEM_CONTROLLED_FIELDS = (
    # provenance and verification (v2.2.0)
    "information_origin", "verification_status", "review_status", "extraction_confidence",
    "source_resume_id", "extraction_run_id", "source_location",
    # employment role mapping and activity taxonomy mapping
    "role_id", "seniority_code", "mapping_status", "mapping_confidence", "mapping_note",
    "category_code", "category_mapping_status", "category_mapping_confidence",
    # profile confirmation and extraction state
    "confirmation_status", "confirmed_at", "extraction_status", "raw_extraction_json",
    # legacy self-assertable trust columns
    "participant_confirmed", "confidence", "verified", "evidence_status", "evidence_source", "provenance",
    "entry_source", "attribute_provenance",
    # ownership
    "owner_user_id", "candidate_profile_id",
)

# Legacy trust columns are no longer written by the service (v2.3.0): the database derives
# employments.entry_source = 'manual_entry' from the origin of candidate-authored rows and leaves
# evidence.provenance, skills.evidence_source and employers.attribute_provenance NULL.
MANUAL_ENTRY_SOURCE = "manual_entry"                 # trace_candidate_employments.entry_source


class OwnerContext(BaseModel):
    """Trusted identity of the caller. Never built from a record payload."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_user_id: UUID
    candidate_profile_id: UUID


# --------------------------------------------------------------------------- inputs
class _Content(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DateInput(_Content):
    """A partial date exactly as stated. Unknown stays unknown."""
    year: Optional[int] = None
    month: Optional[int] = None
    precision: DatePrecision = "unknown"

    @model_validator(mode="after")
    def _valid(self):
        if not dates.valid_partial_date(self.year, self.month, self.precision):
            raise ValueError("date precision does not match year/month (years 1950-2100)")
        return self


UNKNOWN_DATE = DateInput()


def _no_ongoing_start(v: Optional[DateInput]) -> Optional[DateInput]:
    if v is not None and v.precision == "ongoing":
        raise ValueError("a start date cannot be 'ongoing'")
    return v


class ProfileCreate(_Content):
    headline: Optional[str] = None
    country_code: str = "IN"
    resume_as_of: DateInput = UNKNOWN_DATE


class ProfileUpdate(_Content):
    headline: Optional[str] = None
    country_code: Optional[str] = None
    resume_as_of: Optional[DateInput] = None


class EmployerCreate(_Content):
    employer_name: Optional[str] = None
    industry_raw: Optional[str] = None
    industry_sector: str = "unknown"
    company_model: str = "unknown"
    business_model: str = "unknown"
    company_size_band: str = "unknown"


class EmployerUpdate(_Content):
    employer_name: Optional[str] = None
    industry_raw: Optional[str] = None
    industry_sector: Optional[str] = None
    company_model: Optional[str] = None
    business_model: Optional[str] = None
    company_size_band: Optional[str] = None


class EmploymentCreate(_Content):
    sequence_index: int = Field(ge=1)
    title_raw: str
    candidate_employer_id: Optional[UUID] = None
    employment_type: EmploymentType = "unknown"
    primary_career_sequence: bool = True
    counts_as_full_time_experience: Optional[bool] = None
    classification_note: Optional[str] = None
    start: DateInput = UNKNOWN_DATE
    end: DateInput = UNKNOWN_DATE

    @field_validator("start")
    @classmethod
    def start_not_ongoing(cls, v):
        return _no_ongoing_start(v)


class EmploymentUpdate(_Content):
    sequence_index: Optional[int] = Field(default=None, ge=1)
    title_raw: Optional[str] = None
    candidate_employer_id: Optional[UUID] = None
    employment_type: Optional[EmploymentType] = None
    primary_career_sequence: Optional[bool] = None
    counts_as_full_time_experience: Optional[bool] = None
    classification_note: Optional[str] = None
    start: Optional[DateInput] = None
    end: Optional[DateInput] = None

    @field_validator("start")
    @classmethod
    def start_not_ongoing(cls, v):
        return _no_ongoing_start(v)


class EducationCreate(_Content):
    qualification_title_raw: Optional[str] = None     # exactly as stated; never replaced by the level
    qualification_level: str = "unknown"
    institution_name: Optional[str] = None
    field_of_study: Optional[str] = None
    study_mode: str = "unknown"
    completion_status: str = "unknown"
    start: DateInput = UNKNOWN_DATE
    end: DateInput = UNKNOWN_DATE
    notes: Optional[str] = None

    @field_validator("start")
    @classmethod
    def start_not_ongoing(cls, v):
        return _no_ongoing_start(v)


class EducationUpdate(_Content):
    qualification_title_raw: Optional[str] = None
    qualification_level: Optional[str] = None
    institution_name: Optional[str] = None
    field_of_study: Optional[str] = None
    study_mode: Optional[str] = None
    completion_status: Optional[str] = None
    start: Optional[DateInput] = None
    end: Optional[DateInput] = None
    notes: Optional[str] = None

    @field_validator("start")
    @classmethod
    def start_not_ongoing(cls, v):
        return _no_ongoing_start(v)


class ActivityCreate(_Content):
    activity_type: ActivityType
    title_raw: str
    organisation_raw: Optional[str] = None
    description_raw: Optional[str] = None
    contribution_raw: Optional[str] = None
    outcome_raw: Optional[str] = None
    link_url: Optional[str] = None
    status: str = "unknown"
    related_employment_id: Optional[UUID] = None
    credential_identifier_raw: Optional[str] = None
    start: DateInput = UNKNOWN_DATE
    end: DateInput = UNKNOWN_DATE
    expiry: DateInput = UNKNOWN_DATE
    metadata: Dict[str, object] = Field(default_factory=dict)   # supplementary only

    @field_validator("start", "expiry")
    @classmethod
    def start_not_ongoing(cls, v):
        return _no_ongoing_start(v)


class ActivityUpdate(_Content):
    activity_type: Optional[ActivityType] = None
    title_raw: Optional[str] = None
    organisation_raw: Optional[str] = None
    description_raw: Optional[str] = None
    contribution_raw: Optional[str] = None
    outcome_raw: Optional[str] = None
    link_url: Optional[str] = None
    status: Optional[str] = None
    related_employment_id: Optional[UUID] = None
    credential_identifier_raw: Optional[str] = None
    start: Optional[DateInput] = None
    end: Optional[DateInput] = None
    expiry: Optional[DateInput] = None
    metadata: Optional[Dict[str, object]] = None

    @field_validator("start", "expiry")
    @classmethod
    def start_not_ongoing(cls, v):
        return _no_ongoing_start(v)


class EvidenceCreate(_Content):
    """A responsibility, achievement or other claim, attached to at most one employment episode or
    career activity (never both)."""
    evidence_type: EvidenceType
    evidence_text: str
    responsibility_area: Optional[str] = None
    candidate_employment_id: Optional[UUID] = None
    candidate_activity_id: Optional[UUID] = None

    @model_validator(mode="after")
    def _single_parent(self):
        if self.candidate_employment_id is not None and self.candidate_activity_id is not None:
            raise ValueError("evidence attaches to an employment episode or an activity, not both")
        return self


class EvidenceUpdate(_Content):
    evidence_type: Optional[EvidenceType] = None
    evidence_text: Optional[str] = None
    responsibility_area: Optional[str] = None
    candidate_employment_id: Optional[UUID] = None
    candidate_activity_id: Optional[UUID] = None

    @model_validator(mode="after")
    def _single_parent(self):
        if self.candidate_employment_id is not None and self.candidate_activity_id is not None:
            raise ValueError("evidence attaches to an employment episode or an activity, not both")
        return self


_SKILL_LINKS = ("candidate_employment_id", "candidate_education_id", "candidate_activity_id", "candidate_evidence_id")


class SkillCreate(_Content):
    """One skill claim and the single record that demonstrates it (if any). A skill outside the
    taxonomy is stored with skill_id = None and its stated text. Proficiency only as stated."""
    skill_id: Optional[str] = None
    skill_text_raw: Optional[str] = None
    stated_proficiency_raw: Optional[str] = None
    candidate_employment_id: Optional[UUID] = None
    candidate_education_id: Optional[UUID] = None
    candidate_activity_id: Optional[UUID] = None
    candidate_evidence_id: Optional[UUID] = None

    @model_validator(mode="after")
    def _shape(self):
        if self.skill_id is None and not (self.skill_text_raw or "").strip():
            raise ValueError("a skill needs a taxonomy skill_id or its stated text")
        if sum(getattr(self, k) is not None for k in _SKILL_LINKS) > 1:
            raise ValueError("a skill row links to at most one supporting record")
        return self


class SkillUpdate(_Content):
    skill_id: Optional[str] = None
    skill_text_raw: Optional[str] = None
    stated_proficiency_raw: Optional[str] = None
    candidate_employment_id: Optional[UUID] = None
    candidate_education_id: Optional[UUID] = None
    candidate_activity_id: Optional[UUID] = None
    candidate_evidence_id: Optional[UUID] = None

    @model_validator(mode="after")
    def _shape(self):
        if sum(getattr(self, k) is not None for k in _SKILL_LINKS) > 1:
            raise ValueError("a skill row links to at most one supporting record")
        return self


class CareerBreakCreate(_Content):
    break_reason: str = "unspecified"
    note: Optional[str] = None
    start: DateInput = UNKNOWN_DATE
    end: DateInput = UNKNOWN_DATE

    @field_validator("start")
    @classmethod
    def start_not_ongoing(cls, v):
        return _no_ongoing_start(v)


class CareerBreakUpdate(_Content):
    break_reason: Optional[str] = None
    note: Optional[str] = None
    start: Optional[DateInput] = None
    end: Optional[DateInput] = None

    @field_validator("start")
    @classmethod
    def start_not_ongoing(cls, v):
        return _no_ongoing_start(v)


# --------------------------------------------------------------------------- corrections (M4)
# A correction changes scalar content of a reviewed, extracted, inferred or legacy record and is kept in
# correction history (trace_review.correct_record). Links between records, taxonomy references
# (skill_id, responsibility_area), employers and every system field are not correctable. Unknown
# fields are rejected (extra="forbid"); only the fields the caller sets are sent.
class EmploymentCorrection(_Content):
    sequence_index: Optional[int] = Field(default=None, ge=1)
    title_raw: Optional[str] = None
    employment_type: Optional[EmploymentType] = None
    primary_career_sequence: Optional[bool] = None
    counts_as_full_time_experience: Optional[bool] = None
    classification_note: Optional[str] = None
    start: Optional[DateInput] = None
    end: Optional[DateInput] = None

    @field_validator("start")
    @classmethod
    def start_not_ongoing(cls, v):
        return _no_ongoing_start(v)


class EducationCorrection(_Content):
    qualification_title_raw: Optional[str] = None
    qualification_level: Optional[str] = None
    institution_name: Optional[str] = None
    field_of_study: Optional[str] = None
    study_mode: Optional[str] = None
    completion_status: Optional[str] = None
    start: Optional[DateInput] = None
    end: Optional[DateInput] = None
    notes: Optional[str] = None

    @field_validator("start")
    @classmethod
    def start_not_ongoing(cls, v):
        return _no_ongoing_start(v)


class ActivityCorrection(_Content):
    activity_type: Optional[ActivityType] = None
    title_raw: Optional[str] = None
    organisation_raw: Optional[str] = None
    description_raw: Optional[str] = None
    contribution_raw: Optional[str] = None
    outcome_raw: Optional[str] = None
    link_url: Optional[str] = None
    status: Optional[str] = None
    credential_identifier_raw: Optional[str] = None
    start: Optional[DateInput] = None
    end: Optional[DateInput] = None
    expiry: Optional[DateInput] = None
    metadata: Optional[Dict[str, object]] = None

    @field_validator("start", "expiry")
    @classmethod
    def start_not_ongoing(cls, v):
        return _no_ongoing_start(v)


class EvidenceCorrection(_Content):
    evidence_type: Optional[EvidenceType] = None
    evidence_text: Optional[str] = None


class SkillCorrection(_Content):
    skill_text_raw: Optional[str] = None
    stated_proficiency_raw: Optional[str] = None


class CareerBreakCorrection(_Content):
    break_reason: Optional[str] = None
    note: Optional[str] = None
    start: Optional[DateInput] = None
    end: Optional[DateInput] = None

    @field_validator("start")
    @classmethod
    def start_not_ongoing(cls, v):
        return _no_ongoing_start(v)


CORRECTION_MODELS = {
    "employment": EmploymentCorrection, "education": EducationCorrection, "activity": ActivityCorrection,
    "evidence": EvidenceCorrection, "skill": SkillCorrection, "career_break": CareerBreakCorrection,
}


class CorrectionEvent(BaseModel):
    """One append-only correction-history entry. The original value of a field is its previous value in
    the earliest event that changed it; the record itself holds the latest value."""
    correction_id: UUID
    correction_seq: int
    record_kind: str
    record_id: UUID
    changed_fields: List[str]
    previous_values: Dict[str, object]
    corrected_values: Dict[str, object]
    prior_review_status: ReviewStatus
    corrected_at: object
    corrected_by: UUID


# --------------------------------------------------------------------------- outputs
class Provenance(BaseModel):
    """Read-only. Origin, review and verification are independent; extraction confidence is not a
    verification or review signal. No resume excerpt is stored or returned."""
    information_origin: Optional[InformationOrigin]   # None = not recorded (created before v2.2.0)
    review_status: ReviewStatus
    verification_status: VerificationStatus
    extraction_confidence: Optional[Decimal]
    source_resume_id: Optional[UUID]
    extraction_run_id: Optional[UUID]
    source_location: Optional[str]


class SkillRef(BaseModel):
    candidate_skill_id: UUID
    name: str              # canonical taxonomy name when mapped, otherwise the stated text


class EvidenceRecord(BaseModel):
    candidate_evidence_id: UUID
    evidence_type: EvidenceType
    responsibility_area: Optional[str]
    evidence_text: str
    attached_to: Optional[Literal["employment", "activity"]]
    attached_id: Optional[UUID]
    skills: List[SkillRef]
    provenance: Provenance
    # Optimistic-concurrency version (the row's updated_at). Not provenance, not content, not a trust signal:
    # excluded from serialisation (read as an attribute by the API views only).
    version: Optional[datetime] = Field(default=None, exclude=True)
    # Candidate classification of responsibility areas (M6). A candidate assertion, never verification;
    # stale once evidence_text no longer equals the classified text.
    area_classification: Optional[List[str]] = None
    area_classification_basis: Optional[Literal["candidate_selected"]] = None
    area_classified_text: Optional[str] = None
    area_classification_is_stale: bool = False
    # Phase 3.1 (v2.18.0): the user's explicit "None of these areas"; their yes/no answers to scope questions, by area
    # (an absent answer is unanswered, never "no"); and the taxonomy version they reviewed against (None = before
    # versioning, read as version 1).
    area_classification_none: bool = False
    area_qualifiers: Dict[str, Dict[str, bool]] = {}
    area_classification_taxonomy_version: Optional[int] = None


class EmployerRecord(BaseModel):
    candidate_employer_id: UUID
    employer_name: Optional[str]
    industry_raw: Optional[str]
    industry_sector: str
    company_model: str
    business_model: str
    company_size_band: str
    attribute_provenance: Optional[str]        # NULL: candidate-entered, no source provenance recorded
    version: Optional[datetime] = Field(default=None, exclude=True)   # updated_at (optimistic concurrency)


class EmploymentRecord(BaseModel):
    candidate_employment_id: UUID
    sequence_index: int
    title_raw: str                          # original title
    role_id: str                            # normalised role (taxonomy)
    normalised_title: Optional[str]         # taxonomy canonical title for role_id
    role_family: Optional[str]
    seniority_code: Optional[str]
    mapping_status: MappingStatus
    mapping_confidence: Optional[Decimal]   # None only for a candidate-selected mapping (M5)
    mapping_note: Optional[str]
    # Role-mapping provenance (M5): how the role was chosen, from which alias, for which exact title, with
    # which PM-ownership clarification; stale when title_raw changed after the mapping.
    role_mapping_basis: Optional[Literal["alias_match", "candidate_selected"]] = None
    role_mapping_alias_id: Optional[str] = None
    mapped_title_raw: Optional[str] = None
    role_mapping_ownership_areas: Optional[List[str]] = None
    mapping_is_stale: bool = False
    employment_type: EmploymentType
    primary_career_sequence: bool
    counts_as_full_time_experience: Optional[bool]
    classification_note: Optional[str]
    start: PartialDateView
    end: PartialDateView
    is_current_as_stated: bool
    entry_source: str
    employer: Optional[EmployerRecord]
    responsibilities: List[EvidenceRecord]  # responsibility, scope and leadership evidence
    achievements: List[EvidenceRecord]
    other_evidence: List[EvidenceRecord]    # skill_evidence and other
    skills: List[SkillRef]
    provenance: Provenance
    # Optimistic-concurrency version (the row's updated_at). Not provenance, not content, not a trust signal:
    # excluded from serialisation (read as an attribute by the API views only).
    version: Optional[datetime] = Field(default=None, exclude=True)


class EducationRecord(BaseModel):
    candidate_education_id: UUID
    qualification_title_raw: Optional[str]
    qualification_level: str
    institution_name: Optional[str]
    field_of_study: Optional[str]
    study_mode: str
    completion_status: str
    start: PartialDateView
    end: PartialDateView
    notes: Optional[str]
    skills: List[SkillRef]
    provenance: Provenance
    # Optimistic-concurrency version (the row's updated_at). Not provenance, not content, not a trust signal:
    # excluded from serialisation (read as an attribute by the API views only).
    version: Optional[datetime] = Field(default=None, exclude=True)


class ActivityRecord(BaseModel):
    candidate_activity_id: UUID
    activity_type: ActivityType
    title_raw: str
    category_code: Optional[str]
    category_mapping_status: MappingStatus
    category_mapping_confidence: Optional[Decimal]
    organisation_raw: Optional[str]
    description_raw: Optional[str]
    contribution_raw: Optional[str]
    outcome_raw: Optional[str]
    link_url: Optional[str]
    status: str
    related_employment_id: Optional[UUID]
    credential_identifier_raw: Optional[str]
    start: PartialDateView
    end: PartialDateView
    expiry: PartialDateView
    metadata: Dict[str, object]
    achievements: List[EvidenceRecord]
    other_evidence: List[EvidenceRecord]
    skills: List[SkillRef]
    provenance: Provenance
    # Optimistic-concurrency version (the row's updated_at). Not provenance, not content, not a trust signal:
    # excluded from serialisation (read as an attribute by the API views only).
    version: Optional[datetime] = Field(default=None, exclude=True)


class SkillTaxonomy(BaseModel):
    skill_id: str
    canonical_skill: str
    skill_family: str


class SkillRecord(BaseModel):
    candidate_skill_id: UUID
    skill_text_raw: Optional[str]              # as stated
    taxonomy: Optional[SkillTaxonomy]          # None = not mapped to the taxonomy
    stated_proficiency_raw: Optional[str]      # only when explicitly stated; never inferred
    evidence_source: Optional[str]             # legacy v2.0 label; NULL on rows created since v2.3.0
    supported_by: Optional[Literal["employment", "education", "activity", "evidence"]]
    supported_by_id: Optional[UUID]
    provenance: Provenance
    # Optimistic-concurrency version (the row's updated_at). Not provenance, not content, not a trust signal:
    # excluded from serialisation (read as an attribute by the API views only).
    version: Optional[datetime] = Field(default=None, exclude=True)


class CareerBreakRecord(BaseModel):
    candidate_break_id: UUID
    break_reason: str
    note: Optional[str]
    start: PartialDateView
    end: PartialDateView
    provenance: Provenance
    # Optimistic-concurrency version (the row's updated_at). Not provenance, not content, not a trust signal:
    # excluded from serialisation (read as an attribute by the API views only).
    version: Optional[datetime] = Field(default=None, exclude=True)


class CandidateProfile(BaseModel):
    """The complete structured profile. Every category is always present (empty when there are no
    records). Employment history is the only input to pathway sequencing; every other category is a
    supporting record."""
    candidate_profile_id: UUID
    headline: Optional[str]
    country_code: str
    resume_as_of: PartialDateView
    confirmation_status: str
    employment_history: List[EmploymentRecord]
    education: List[EducationRecord]
    career_activities: Dict[ActivityType, List[ActivityRecord]]   # every activity type as a key
    skills: List[SkillRecord]
    unattached_evidence: List[EvidenceRecord]
    career_breaks: List[CareerBreakRecord]


# --------------------------------------------------------------------------- role mapping (M5)
class RoleChoice(BaseModel):
    """A taxonomy role a candidate may choose for an employment: active and resolved only (never
    ROLE-UNKNOWN, retired or unresolved roles)."""
    model_config = ConfigDict(frozen=True)
    role_id: str
    canonical_title: str
    role_family: str
    source: Literal["alias_option", "taxonomy"]          # alias-derived options come first
    alias_id: Optional[str] = None
    alias_confidence: Optional[Decimal] = None          # the alias's confidence, for information only
    mapping_rule: Optional[str] = None
    requires_ownership_clarification: bool = False      # Product Owner -> product management (data dictionary §9)


class RoleMappingOptions(BaseModel):
    """What the candidate sees for one employment: the automatic proposal, if any, then the alias-derived
    options, then every other eligible role ("None of these")."""
    model_config = ConfigDict(frozen=True)
    candidate_employment_id: UUID
    title_raw: str
    lookup_title: Optional[str]
    outcome: Literal["automatic", "decision_required", "unresolved"]
    reason: str
    automatic_role_id: Optional[str] = None
    automatic_alias_id: Optional[str] = None
    automatic_confidence: Optional[Decimal] = None
    current_role_id: str
    current_mapping_status: MappingStatus
    current_mapping_basis: Optional[str] = None
    mapping_is_stale: bool = False
    alias_options: List[RoleChoice] = []
    other_roles: List[RoleChoice] = []
    pm_ownership_areas: List[str] = []                  # valid answers when a clarification is required
    # Phase 5 (D50): when no alias matches exactly, a role SUGGESTED from the words of the title (matcher.suggest_role).
    # Never applied automatically: the candidate confirms it (candidate_selected) or chooses another.
    suggested_role_id: Optional[str] = None
    suggestion_basis: Optional[str] = None              # the words of the title the suggestion rests on


class RoleMappingResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    candidate_employment_id: UUID
    result: Literal["mapped_automatically", "decision_required", "unresolved", "candidate_decision_kept",
                    "existing_mapping_kept"]
    role_id: Optional[str] = None
    options: RoleMappingOptions


# --------------------------------------------------------------------------- current position (M5)
CurrentPositionReason = Literal["no_employment_history", "no_eligible_primary_employment", "pending_review",
                                "stale_mapping", "unresolved_role_mapping", "low_mapping_confidence",
                                "latest_employment_ineligible", "role_unconfirmed"]


class CurrentPosition(BaseModel):
    """Where the candidate is today, from their own sequenced employment history.

    known: the latest primary employment (by stated sequence) is transition-eligible: reviewed (confirmed
    or corrected), mapped to a resolved role (an automatic mapping at or above min_mapping_confidence, or
    the candidate's own decision), not stale, full-time by the existing rules and with a known start.
    unknown: otherwise, with the reason taken from the latest primary employment. An older eligible role
    is never used in its place."""
    model_config = ConfigDict(frozen=True)
    status: Literal["known", "unknown"]
    reason: Optional[CurrentPositionReason] = None
    explanation: str
    candidate_employment_id: Optional[UUID] = None       # the current role's record, or the blocking record
    title_raw: Optional[str] = None
    role_id: Optional[str] = None                        # known only
    canonical_title: Optional[str] = None
    role_family: Optional[str] = None
    seniority_code: Optional[str] = None
    mapping_basis: Optional[str] = None                  # 'alias_match' or 'candidate_selected' (None: set outside M5)
    is_current_as_stated: Optional[bool] = None
    exclusion_reasons: List[str] = []                    # unknown: why the latest primary employment is not eligible


class CurrentPositionEvidence(BaseModel):
    """Observed routes from the candidate's current role to a target role (read-only, synthetic test data
    only). evidence is None when the current position is unknown; nothing is inferred instead."""
    model_config = ConfigDict(frozen=True)
    current_position: CurrentPosition
    target_role_id: str
    evidence: Optional["ReferenceEvidenceResult"] = None
    note: str


from app.services.reference_evidence.models import ReferenceEvidenceResult  # noqa: E402  (forward reference)

CurrentPositionEvidence.model_rebuild()
