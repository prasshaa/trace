"""Contracts for reference career-history evidence retrieval (episode-aware, foundation v2.1.0).

A route is a sequence of employment episodes (role + employer). Consecutive identical role ids
mean an employer change. Individual observations and cohort summaries are separate types. Every value that the
foundation computes (route months, precision, counts, evidence_class, percentiles, display
flags) is passed through unchanged; nothing is re-derived here.
"""
from decimal import Decimal
from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

# Exact values of public.trace_data_mode and public.trace_evidence_class.
DataMode = Literal["empirical", "synthetic_test", "synthetic_demo"]
EvidenceClass = Literal["strong", "moderate", "limited", "insufficient", "synthetic_test_only"]
SUPPORTED_MODES = ("empirical", "synthetic_test")

# The dataset class each supported mode may read (trace_ref.v_mode_profiles).
EXPECTED_DATASET_CLASS = {
    "empirical": "consented_real_reference",
    "synthetic_test": "synthetic_test_fixture",
}

TIMELINE_METRIC = "route_start_to_target_start_months"  # DATA_DICTIONARY.md section 7

# Exact values of trace_ref.v_episode_transitions.change_type that start a new episode stint
# (DATA_DICTIONARY.md section 6a). Continuations (no_change, employer_unknown) never appear in a route.
ChangeType = Literal["role_change", "employer_change", "role_and_employer_change", "role_change_employer_unknown"]

INTERPRETATION_NOTE = (
    "Observations describe de-identified people who were observed to follow a route into the "
    "destination role. Their number is evidence quantity, not a probability that any individual "
    "will succeed on that route."
)
SYNTHETIC_NOTICE = (
    "SYNTHETIC TEST DATA. These are synthetic test fixtures, not real people or career histories. "
    "They are not evidence about real career transitions."
)
EMPIRICAL_NOTICE = (
    "Empirical evidence from consented, confirmed, de-identified reference profiles that are "
    "eligible at the time of retrieval (trace_ref.v_eligible_profiles)."
)


# --------------------------------------------------------------------------- request
class EvidenceRequest(BaseModel):
    """data_mode has no default: the caller must choose it explicitly."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    destination_role_id: str = Field(min_length=1)       # public.trace_role_taxonomy.role_id
    data_mode: DataMode
    destination_company: Optional[str] = None            # not supported by the schema; see service
    # Optional origin (M5): only routes that start in this role (a stint of it) and reach the destination
    # later in the same confirmed segment. None = every route into the destination (unchanged behaviour).
    origin_role_id: Optional[str] = Field(default=None, min_length=1)
    # Phase 8.1: reference profiles excluded before anything is computed (the caller's own contribution, so it never
    # counts as independent evidence for them). Server-side only; never echoed in any response.
    exclude_profile_ids: Tuple[str, ...] = ()


# --------------------------------------------------------------------------- repository rows
class RoleRow(BaseModel):
    role_id: str
    canonical_title: str
    role_family: str
    is_pm_target: bool
    is_resolved: bool
    active: bool
    # Phase 8.2 (presentation only): the role's taxonomy seniority and whether its family defines more than one level.
    # A level is reliable only in a level-structured family; elsewhere 'mid' is just the default for an unqualified title.
    seniority_code: Optional[str] = None
    seniority_rank: Optional[int] = None
    family_has_levels: bool = False
    # Decision D20 (presentation only): the taxonomy's human-readable family name (trace_role_families.label).
    role_family_label: Optional[str] = None


class EpisodePathRow(BaseModel):
    """One row of trace_ref.v_career_episode_paths, already restricted by v_mode_profiles."""
    profile_id: str
    dataset_class: str
    segment_no: int
    role_path: List[str]
    entry_change_types: List[Optional[ChangeType]]   # first element is always None
    stint_start_lo: List[int]
    stint_start_hi: List[int]


class RouteObservationRow(BaseModel):
    """One row of trace_ref.episode_route_observations(route, data_mode)."""
    profile_id: str
    segment_no: int
    start_position: int
    step_change_types: List[ChangeType]               # one per step after the first
    route_months_min: int
    route_months_max: int
    is_precise: bool


class RouteTimelineSummaryRow(BaseModel):
    """The single row of trace_ref.episode_route_timeline_summary(route, data_mode)."""
    data_mode: DataMode
    route: List[str]
    supporting_profile_count: int
    dated_profile_count: int
    count_display_allowed: bool
    evidence_class: EvidenceClass
    p25_months: Optional[Decimal]
    median_months: Optional[Decimal]
    p75_months: Optional[Decimal]
    coarse_min_months: Optional[int]
    coarse_max_months: Optional[int]
    timeline_metric: str
    timeline_display_allowed: bool


class StepRequest(BaseModel):
    """One observation's pre-destination stints (1-based stint_no, inclusive). Internal only."""
    model_config = ConfigDict(frozen=True)
    profile_id: str
    segment_no: int
    first_stint: int
    last_stint: int


class StepExperienceRow(BaseModel):
    """One distinct (reference profile, responsibility area) recorded before the destination. Internal only."""
    profile_id: str
    responsibility_area: str


class AreaRow(BaseModel):
    responsibility_area: str
    label: str
    is_pm_ownership_signal: bool


# --------------------------------------------------------------------------- result
class DestinationRole(BaseModel):
    role_id: str
    canonical_title: str
    role_family: str
    is_pm_target: bool


class RouteStep(BaseModel):
    """One employment episode of an observed route. Start is the stated interval; nothing is
    filled in. entry_change_type says how the person moved into this episode (None for the first).
    No employer identifier is carried."""
    role_id: str
    role_family: str
    entry_change_type: Optional[ChangeType]
    start_lo: int
    start_hi: int
    start_lo_label: str
    start_hi_label: str
    start_is_month_precise: bool


class ObservationProvenance(BaseModel):
    """Internal traceability only (e.g. for trace_pathway_evidence.reference_profile_id).
    Excluded from serialisation; never send it to a client."""
    profile_id: str
    dataset_class: str
    segment_no: int


class ObservedPathway(BaseModel):
    """One individual's observed route into the destination role (individual level)."""
    level: Literal["individual_observation"] = "individual_observation"
    route: List[str]
    steps: List[RouteStep]
    # From trace_ref.episode_route_observations, unchanged.
    step_change_types: List[ChangeType]
    timeline_metric: str = TIMELINE_METRIC
    route_months_min: int
    route_months_max: int
    is_precise: bool
    provenance: ObservationProvenance = Field(exclude=True, repr=False)


class RouteCohortSummary(BaseModel):
    """trace_ref.episode_route_timeline_summary for one observed route (cohort level), unchanged.

    supporting_profile_count counts every profile in this mode whose confirmed segment contains
    the route anywhere, so it can exceed the number of observations whose full route is exactly
    this one. Percentiles are null unless the foundation computed them."""
    level: Literal["cohort_summary"] = "cohort_summary"
    data_mode: DataMode
    route: List[str]
    supporting_profile_count: int
    dated_profile_count: int
    count_display_allowed: bool
    evidence_class: EvidenceClass
    p25_months: Optional[Decimal]
    median_months: Optional[Decimal]
    p75_months: Optional[Decimal]
    coarse_min_months: Optional[int]
    coarse_max_months: Optional[int]
    timeline_metric: str
    timeline_display_allowed: bool


StepExperienceState = Literal["suppressed_small_cohort", "no_recorded_areas", "areas_withheld", "areas_disclosed"]


class AreaExperience(BaseModel):
    """A responsibility area recorded before the destination by at least the display threshold of distinct
    reference profiles (min_profiles_limited, 5 by default). Areas below the threshold are never listed.
    is_common: also recorded by more than half of the route's observations."""
    responsibility_area: str
    label: str
    is_pm_ownership_signal: bool
    profile_count: int
    is_common: bool


class RouteStepExperience(BaseModel):
    """Experience recorded by the reference profiles observed on one route, on the steps before the destination
    (the destination step is excluded). Counted once per distinct profile; observation_count is the denominator.

    state:
      suppressed_small_cohort  fewer observations than the display threshold (or no threshold setting): nothing
                               about the route's areas is disclosed, not even whether any were recorded;
      no_recorded_areas        enough observations, and none of them recorded an area (genuinely empty);
      areas_withheld           enough observations and areas were recorded, but each by fewer profiles than the
                               threshold: none is listed;
      areas_disclosed          at least one area reached the threshold; only those are listed, and areas_withheld
                               says whether others were recorded below it.
    No identifiers, dates or timelines. Areas are listed in code order, never by count."""
    level: Literal["cohort_summary"] = "cohort_summary"
    data_mode: DataMode
    route: List[str]
    pre_destination_steps: List[str]               # route without its last (destination) step
    state: StepExperienceState
    observation_count: Optional[int] = None        # None when suppressed_small_cohort
    areas_withheld: bool = False                   # areas recorded below the threshold were not listed
    areas: List[AreaExperience] = []


class ReferenceEvidenceResult(BaseModel):
    data_mode: DataMode
    is_synthetic_test_data: bool
    evidence_notice: str
    interpretation_note: str = INTERPRETATION_NOTE
    destination: DestinationRole
    origin: Optional[DestinationRole] = None              # set when the request named an origin role
    has_observed_pathways: bool
    observations: List[ObservedPathway]
    route_summaries: List[RouteCohortSummary]
    route_step_experience: List[RouteStepExperience] = []
    limitations: List[str]
