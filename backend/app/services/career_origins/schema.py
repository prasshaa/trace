"""The explicit output allowlist for Career Origins & Transitions.

Every object that may leave the server is declared here, field by field, with extra="forbid" at every level: anything
not listed (an identifier, a raw value, a new key) makes the artifact invalid rather than being passed through. The
suppression rules are part of the schema, so a hidden value can't be present even by mistake:

- an exact count is at least 10 (the research's "insufficient" threshold, above Trace's privacy minimum of 5);
- a banded or suppressed group carries no count, share, n, median or range, only its band label;
- a median needs n >= 10 and a quartile range n >= 30; there are no minimums, maximums or individual values;
- exact counts never exceed their denominator.

`StoredArtifact` is the file on disk: the public part plus an `internal` block (provenance, checksums, data-use status)
that is never served. `PublicArtifact` is what the API may return. Customer-facing text must not name the data source,
provider or collection method (SOURCE_TERMS).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

TAXONOMY_VERSION = "v3-final-2026-11-19"
SCHEMA_VERSION = 2
ELIGIBLE_PROFILES = 439
EXACT_MIN = 10            # below this a count is never exact
RANGE_MIN = 30            # below this no quartile range

Flag = Literal["descriptive", "indicative", "insufficient"]
Display = Literal["exact", "band", "suppressed"]
Band = Literal["5–9", "fewer than 5", "withheld so a smaller group can't be derived"]
Statement = Literal["observed_fact", "derived_pattern"]

# Customer-facing text must not identify the data source, provider, collection method or source files, and must hold
# nothing that looks like an identifier, a link or contact detail.
SOURCE_TERMS = re.compile(
    r"linked\s*in|bright\s*data|scrap|crawl|harvest|public profile|profile (?:url|link|id)|source (?:url|id)|cached|"
    r"https?://|www\.|@[a-z0-9-]+\.|\b\d{7,}\b|dataset_search|normalised_profiles|sha-?256|\.csv\b|\bsrc_|"
    r"profile_ref|norm_prior_companies|norm_institutions|final_prev_role_title|final_pre_pm_roles", re.I)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


def _hidden_is_empty(display: str, band: Optional[str], values: Dict[str, Any]) -> None:
    if display == "exact":
        if band is not None:
            raise ValueError("an exact value has no band")
    else:
        if band is None:
            raise ValueError("a hidden value needs its band label")
        if any(v is not None for v in values.values()):
            raise ValueError("a hidden value must not carry a count, share or statistic")
        if display == "suppressed" and band != "fewer than 5":
            raise ValueError("suppressed means fewer than 5")


class Bucket(_Strict):
    key: str = Field(pattern=r"^[a-z0-9_]+$")
    label: str
    flag: Flag
    display: Display
    count: Optional[int] = None
    share_pct: Optional[float] = None
    band: Optional[Band] = None
    explanation: Optional[str] = None

    @model_validator(mode="after")
    def _suppression(self):
        _hidden_is_empty(self.display, self.band, {"count": self.count, "share_pct": self.share_pct})
        if self.display == "exact" and (self.count is None or self.count < EXACT_MIN or self.share_pct is None):
            raise ValueError("an exact count must be at least 10, with its share")
        return self


class Fact(Bucket):
    denominator: int = Field(ge=EXACT_MIN)

    @model_validator(mode="after")
    def _within(self):
        if self.count is not None and self.count > self.denominator:
            raise ValueError("count exceeds its denominator")
        return self


class Metric(_Strict):
    key: str = Field(pattern=r"^[a-z0-9_]+$")
    section: Literal["origins", "transitions"]
    kind: Literal["categorical"]
    statement: Statement
    title: str
    question: str
    definition: str
    population_label: str
    denominator: int = Field(ge=EXACT_MIN)
    denominator_flag: Flag
    notes: List[str]
    buckets: List[Bucket] = Field(min_length=1)

    @model_validator(mode="after")
    def _within(self):
        if sum(b.count for b in self.buckets if b.display == "exact") > self.denominator:
            raise ValueError(f"{self.key}: counts exceed the denominator")
        return self


class _Interval(_Strict):
    flag: Flag
    display: Display
    n: Optional[int] = None
    median_months: Optional[int] = None
    iqr_months: Optional[Tuple[int, int]] = None
    band: Optional[Band] = None

    @model_validator(mode="after")
    def _suppression(self):
        _hidden_is_empty(self.display, self.band,
                         {"n": self.n, "median_months": self.median_months, "iqr_months": self.iqr_months})
        if self.display == "exact":
            if self.n is None or self.n < EXACT_MIN or self.median_months is None:
                raise ValueError("a median needs at least 10")
            if self.iqr_months is not None and self.n < RANGE_MIN:
                raise ValueError("a quartile range needs at least 30")
        return self


class Duration(_Interval):
    key: str = Field(pattern=r"^[a-z0-9_]+$")
    label: str


class Step(_Interval):
    key: str = Field(pattern=r"^[a-z0-9_]+$")
    from_: str = Field(alias="from")
    to: str


class TimeToFirstPM(_Strict):
    key: Literal["time_to_first_pm"]
    section: Literal["transitions"]
    statement: Statement
    title: str
    definition: str
    population_label: str
    overall: Duration
    by_background: List[Duration]
    notes: List[str]


class Progression(_Strict):
    key: Literal["progression"]
    section: Literal["progression"]
    statement: Statement
    title: str
    definition: str
    population_label: str
    steps: List[Step]
    notes: List[str]


class Breakdown(_Strict):
    """One dimension of the routes taken by one background group (artifact 2.1.0): same rules as a Metric's buckets."""
    key: Literal["route", "entry", "first_level"]
    title: str
    question: str
    denominator: int = Field(ge=EXACT_MIN)
    buckets: List[Bucket] = Field(min_length=2)

    @model_validator(mode="after")
    def _within(self):
        if sum(b.count for b in self.buckets if b.display == "exact") > self.denominator:
            raise ValueError(f"{self.key}: counts exceed the denominator")
        return self


class BackgroundRoutes(_Strict):
    """How people with one primary background moved into their first PM role. Only groups of 10 or more are listed."""
    key: str = Field(pattern=r"^[a-z0-9_]+$")
    label: str
    flag: Literal["descriptive", "indicative"]
    n: int = Field(ge=EXACT_MIN)
    breakdowns: List[Breakdown] = Field(min_length=1)


class RoutesByBackground(_Strict):
    key: Literal["routes_by_background"]
    section: Literal["transitions"]
    statement: Statement
    title: str
    definition: str
    population_label: str
    groups: List[BackgroundRoutes]
    not_listed: str
    notes: List[str]


class Research(_Strict):
    title: str
    label: str
    research_version: Literal["v3-final"]
    taxonomy_version: Literal["v3-final-2026-11-19"]
    eligible_profiles: Literal[439]
    sample: str
    histories_as_of: str
    disclosure: List[str] = Field(min_length=1)


class DisplayPolicy(_Strict):
    privacy_minimum: Literal[5]
    flags: Dict[Flag, str]
    suppression: str
    statistics: str


DefinitionKey = Literal["first_pm_role", "primary_background", "immediately_preceding_role", "earliest_recorded_role",
                        "internal_transition", "dated_progression"]
MetricKey = Literal["primary_background", "background_status", "preceding_function", "preceding_vs_primary",
                    "entry_move", "first_pm_level", "pre_pm_role_count"]
FactKey = Literal["multi_function", "product_ownership_any", "agile_delivery_any"]


class PublicArtifact(_Strict):
    """Everything the API may serve from the artifact; nothing else."""
    schema_version: Literal[2]
    artifact: Literal["career-origins-india-pm"]
    artifact_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    generated_at: str
    research: Research
    display_policy: DisplayPolicy
    definitions: Dict[DefinitionKey, str]
    limitations: List[str] = Field(min_length=1)
    coverage: List[Fact]
    facts: Dict[FactKey, Fact]
    metrics: Dict[MetricKey, Metric]
    time_to_first_pm: TimeToFirstPM
    progression: Progression
    routes_by_background: Optional[RoutesByBackground] = None      # artifact 2.1.0+


class Internal(_Strict):
    """Restricted provenance kept with the file for audit; never served."""
    provenance: Dict[str, Any]
    kept_separate_from: List[str]
    data_use: Dict[str, str]


class StoredArtifact(PublicArtifact):
    internal: Internal


class Observed(_Strict):
    """One observed figure in a narrative: a bucket of a background group or a progression step, as stored."""
    display: Display
    flag: Flag
    count: Optional[int] = None
    share_pct: Optional[float] = None
    denominator: Optional[int] = None
    band: Optional[Band] = None
    median_months: Optional[int] = None
    iqr_months: Optional[Tuple[int, int]] = None
    n: Optional[int] = None

    @model_validator(mode="after")
    def _suppression(self):
        _hidden_is_empty(self.display, self.band, {"count": self.count, "share_pct": self.share_pct,
                                                   "median_months": self.median_months, "n": self.n})
        if self.display == "exact" and self.count is not None and self.count < EXACT_MIN:
            raise ValueError("an exact count must be at least 10")
        if self.median_months is not None and (self.n is None or self.n < EXACT_MIN):
            raise ValueError("a median needs at least 10")
        if self.iqr_months is not None and (self.n is None or self.n < RANGE_MIN):
            raise ValueError("a quartile range needs at least 30")
        return self


class Pathway(_Strict):
    """A route observed in the research, described in plain words, with what it can and can't establish."""
    key: str = Field(pattern=r"^[a-z0-9_]+$")
    dimension: Literal["route", "entry", "first_level", "progression"]
    title: str
    description: str
    observed: Observed
    relates_to_you: Optional[str] = None
    matches_you: bool = False


class Narrative(_Strict):
    """How the research relates to this user's starting point (Phase 5): aggregate patterns only, never individuals."""
    status: Literal["group", "progression_only", "not_mapped", "no_current_role"]
    basis: str
    group_key: Optional[str] = None
    group_label: Optional[str] = None
    group_n: Optional[int] = None
    group_flag: Optional[Flag] = None
    pathways: List[Pathway] = []
    your_route: Optional[str] = None
    cannot_show: List[str] = []


class Connection(_Strict):
    """How the research relates to the caller's confirmed role family. Group figures only, from the same artifact."""
    status: Literal["mapped", "already_in_field", "not_mapped", "no_current_role"]
    text: str
    role_family_label: Optional[str] = None
    function_key: Optional[str] = None
    function_label: Optional[str] = None
    mapping_basis: Optional[str] = None
    primary_background: Optional[Fact] = None
    preceding_role: Optional[Fact] = None
    time_to_first_pm: Optional[Duration] = None
    separate_note: Optional[str] = None
    progression: Optional[List[Step]] = None
