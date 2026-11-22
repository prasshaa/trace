"""Input and output contracts for candidate career-history sequencing.

Input mirrors public.trace_candidate_employments (same defaults and the same row-level CHECK
constraints). Output separates what the candidate stated (`stated`) from what the service
derived under the foundation rules (every other field).
"""
from typing import Dict, List, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.career_history import dates
from app.services.career_history.dates import DatePrecision

MappingStatus = Literal["confirmed", "auto_high_confidence", "needs_review", "unresolved"]
EmploymentType = Literal[
    "full_time", "part_time", "contract", "internship", "apprenticeship",
    "freelance", "founder", "volunteer", "unknown",
]
TimingRelation = Literal[
    "sequential", "same_month_boundary", "previous_role_ongoing", "timing_unknown",
    "sequence_order_conflict", "overlapping", "boundary_uncertain",
]
# trace_ref.v_episode_transitions.change_type (foundation v2.1.0, DATA_DICTIONARY.md section 6a).
ChangeType = Literal[
    "role_change", "employer_change", "role_and_employer_change", "role_change_employer_unknown",
    "no_change", "employer_unknown",
]
UNKNOWN_ROLE_ID = "ROLE-UNKNOWN"
# Candidate review states that may enter sequencing ('rejected' never does).
ReviewState = Literal["pending_review", "user_confirmed", "user_corrected"]


# --------------------------------------------------------------------------- lookups
class RoleInfo(BaseModel):
    """One row of public.trace_role_taxonomy (only the columns the rules use)."""
    model_config = ConfigDict(frozen=True)

    role_family: str
    default_seniority_code: str
    is_resolved: bool
    is_pm_target: bool


class SequencingContext(BaseModel):
    """Lookup data the rules need. Supplied by the caller; the service never queries a database.

    min_mapping_confidence = None means the setting is missing, and the rules then fail closed
    with 1.0, as trace_ref.v_settings and trace_model.Context.setting do."""
    model_config = ConfigDict(frozen=True)

    roles: Mapping[str, RoleInfo]
    seniority_ranks: Mapping[str, Optional[int]]  # seniority_code -> rank (None = not ranked)
    min_mapping_confidence: Optional[float] = None

    @property
    def effective_min_mapping_confidence(self) -> float:
        return 1.0 if self.min_mapping_confidence is None else self.min_mapping_confidence


# --------------------------------------------------------------------------- input
class PartialDateInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    year: Optional[int] = None
    month: Optional[int] = None
    precision: DatePrecision = "unknown"

    @model_validator(mode="after")
    def _valid(self):
        if self.precision == "ongoing" or not dates.valid_partial_date(self.year, self.month, self.precision):
            raise ValueError("resume_as_of must be a valid month, year or unknown partial date")
        return self


class CandidateEmployment(BaseModel):
    """One structured employment record of the candidate (public.trace_candidate_employments).

    Defaults match the table defaults, so an extracted record with no role mapping or dates is
    still accepted and kept (as ROLE-UNKNOWN / unresolved / unknown dates)."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    employment_id: str = Field(min_length=1)          # candidate_employment_id
    sequence_index: int = Field(ge=1)
    # candidate_employer_id; None = employer not known. Used only to tell whether consecutive
    # records share an employer; excluded from serialised output.
    employer_id: Optional[str] = Field(default=None, exclude=True, repr=False)
    title_raw: str
    role_id: str = UNKNOWN_ROLE_ID
    seniority_code: Optional[str] = None               # None = use the taxonomy default
    mapping_status: MappingStatus = "unresolved"
    # None only for a candidate-selected mapping (M5): a candidate decision carries no confidence.
    mapping_confidence: Optional[float] = Field(default=0.0, ge=0.0, le=1.0)
    mapping_note: Optional[str] = None
    employment_type: EmploymentType = "unknown"
    primary_career_sequence: bool = True
    counts_as_full_time_experience: Optional[bool] = None
    classification_note: Optional[str] = None
    start_year: Optional[int] = None
    start_month: Optional[int] = None
    start_precision: DatePrecision = "unknown"
    end_year: Optional[int] = None
    end_month: Optional[int] = None
    end_precision: DatePrecision = "unknown"
    is_current_as_stated: bool = False
    participant_confirmed: bool = False                # legacy column; not used by the rules
    entry_source: Literal["resume_extraction", "manual_entry"] = "resume_extraction"
    # The candidate's own review of the record (candidate-side input only). None = not applicable:
    # reference-side records and parity inputs, which follow trace_model exactly. 'pending_review'
    # holds the record in the chain as an ineligible barrier (exclusion reason review_pending).
    # Rejected records are not accepted: the caller removes them before sequencing.
    review_status: Optional[ReviewState] = None
    # Candidate-side role-mapping provenance (M5). None / False for reference-side and parity inputs.
    # 'candidate_selected' is the candidate's own decision: it applies to the candidate's own sequence and
    # is not held to min_mapping_confidence (it has no confidence). A stale mapping (its title changed after
    # mapping) is never eligible (exclusion reason mapping_stale) until a fresh mapping decision.
    mapping_basis: Optional[Literal["alias_match", "candidate_selected"]] = None
    mapping_is_stale: bool = False

    @field_validator("employer_id")
    @classmethod
    def _blank_employer_is_unknown(cls, v):
        return v if v else None

    @field_validator("title_raw")
    @classmethod
    def _title_not_blank(cls, v):
        if not v.strip():
            raise ValueError("title_raw must not be blank")
        return v

    @model_validator(mode="after")
    def _row_constraints(self):
        # The CHECK constraints of public.trace_candidate_employments.
        start = (self.start_year, self.start_month, self.start_precision)
        end = (self.end_year, self.end_month, self.end_precision)
        if self.start_precision == "ongoing" or not dates.valid_partial_date(*start):
            raise ValueError("start date: precision does not match year/month, or start is 'ongoing'")
        if not dates.valid_partial_date(*end):
            raise ValueError("end date: precision does not match year/month")
        if (self.end_precision == "ongoing") != self.is_current_as_stated:
            raise ValueError("is_current_as_stated must be true exactly when end_precision is 'ongoing'")
        if not dates.feasible_period(start, end):
            raise ValueError("end date is before start date")
        if self.mapping_status == "unresolved" and self.role_id != UNKNOWN_ROLE_ID:
            raise ValueError("mapping_status 'unresolved' requires role_id ROLE-UNKNOWN")
        if self.role_id == UNKNOWN_ROLE_ID and self.mapping_status not in ("unresolved", "needs_review"):
            raise ValueError("ROLE-UNKNOWN requires mapping_status 'unresolved' or 'needs_review'")
        if self.mapping_confidence is None and self.mapping_basis != "candidate_selected":
            raise ValueError("mapping_confidence may be None only for a candidate-selected mapping")
        if self.mapping_basis == "candidate_selected" and self.mapping_status != "confirmed":
            raise ValueError("a candidate-selected mapping is 'confirmed'")
        if self.counts_as_full_time_experience is not None and self.classification_note is None:
            raise ValueError("an explicit counts_as_full_time_experience needs a classification_note")
        return self


# --------------------------------------------------------------------------- output
class PartialDateView(BaseModel):
    """A stated partial date and the month interval it covers. Nothing is filled in:
    unknown and ongoing dates have no bounds."""
    year: Optional[int]
    month: Optional[int]
    precision: DatePrecision
    lo: Optional[int]          # earliest possible month index
    hi: Optional[int]          # latest possible month index
    lo_label: Optional[str]    # "YYYY-MM"
    hi_label: Optional[str]


class ClassifiedEmployment(BaseModel):
    """Mirrors trace_ref.v_employment_classified + v_career_sequence for one record."""
    stated: CandidateEmployment
    start: PartialDateView
    end: PartialDateView
    role_family: str
    is_pm_target: bool
    role_is_resolved: bool
    seniority_code: str
    seniority_code_source: Literal["stated", "taxonomy_default"]
    seniority_rank: Optional[int]
    counts_as_full_time_experience: bool
    full_time_basis: Literal["stated_override", "employment_type_default"]
    tenure_months_min: Optional[int]
    tenure_months_max: Optional[int]
    tenure_is_censored: bool
    exclusion_reasons: List[str]
    is_transition_eligible: bool
    in_primary_sequence: bool
    primary_position: Optional[int]       # 1-based position among primary records
    prev_employment_id: Optional[str]     # previous primary record (None for the first / parallel)


class Transition(BaseModel):
    """Mirrors trace_ref.v_role_transitions for one consecutive pair of primary records."""
    from_employment_id: str
    to_employment_id: str
    from_role_id: str
    to_role_id: str
    from_role_family: str
    to_role_family: str
    move_type: Literal["internal", "external", "unknown"]
    seniority_rank_change: Optional[int]
    timing_relation: TimingRelation
    gap_months_min: Optional[int]
    gap_months_max: Optional[int]
    gap_is_precise: bool
    transition_status: Literal["confirmed", "unconfirmed"]
    unconfirmed_reasons: List[str]
    # Episode-aware fields, mirroring trace_ref.v_episode_transitions.
    role_changed: bool
    employer_changed: Optional[bool]                  # None = an employer is not known
    change_type: ChangeType
    is_pathway_move: bool                             # confirmed and a role and/or employer change


class Stint(BaseModel):
    """One employment-episode stint: consecutive records with the same role whose employer is
    unchanged or unknown. Mirrors trace_ref.v_career_episode_stints (without employer ids)."""
    role_id: str
    entry_change_type: Optional[ChangeType]           # None for the first stint of a segment
    start_lo: Optional[int]
    start_hi: Optional[int]
    employment_ids: List[str]


class Segment(BaseModel):
    """Mirrors one row of trace_ref.v_career_episode_paths. Consecutive identical role ids are
    separate episodes entered by an employer change."""
    segment_no: int
    stints: List[Stint]
    role_path: List[str]
    entry_change_types: List[Optional[ChangeType]]


class SequenceIntegrity(BaseModel):
    """Mirrors trace_ref.v_profile_sequence_integrity."""
    employment_count: int
    max_sequence_index: Optional[int]
    has_sequence_gaps: bool
    ongoing_primary_count: int
    has_order_conflict: bool


class CandidateHistory(BaseModel):
    ruleset: str
    min_mapping_confidence_applied: float
    records: List[ClassifiedEmployment]    # every input record, in sequence_index order
    transitions: List[Transition]
    segments: List[Segment]
    integrity: SequenceIntegrity


CHANGE_TYPE_DESCRIPTIONS: Dict[str, str] = {
    "role_change": "Different role at the same employer.",
    "employer_change": "Same role at a different employer.",
    "role_and_employer_change": "Different role at a different employer.",
    "role_change_employer_unknown": "Different role; at least one employer is not stated.",
    "no_change": "Same role at the same employer (a continuation, not a move).",
    "employer_unknown": "Same role; at least one employer is not stated, so no move can be established.",
}

# Human-readable meaning of every reason code the service can emit.
REASON_DESCRIPTIONS: Dict[str, str] = {
    "review_pending": "The candidate has not reviewed this record yet. It is held in the chain as a barrier "
                      "(never skipped, so its neighbours are not treated as adjacent) and contributes no transition.",
    "parallel_activity": "Marked as parallel activity; outside the linear career chain.",
    "not_full_time_experience": "Employment type does not count as full-time experience "
                                "(only full_time and contract do, unless explicitly reclassified with a note).",
    "unresolved_role": "The role could not be resolved to a taxonomy role.",
    "mapping_not_confirmed": "Role mapping is not 'confirmed' or 'auto_high_confidence'.",
    "low_mapping_confidence": "Role mapping confidence is below min_mapping_confidence.",
    "mapping_stale": "The title changed after the role was mapped; a fresh mapping decision is needed.",
    "unknown_start_date": "Start date is unknown.",
    "timing:previous_role_ongoing": "The previous role is stated as ongoing, so the move cannot be timed.",
    "timing:timing_unknown": "The previous end or next start date is unknown.",
    "timing:sequence_order_conflict": "The next role starts before the previous role started.",
    "timing:overlapping": "The roles overlap beyond a shared boundary month; needs review.",
    "timing:boundary_uncertain": "Date precision is too coarse to establish order (e.g. same year, year-only).",
}
