"""Response contracts for career insights over the synthetic reference sample (Phase 5).

Every statistic carries its population, denominator, number of profiles with nothing recorded, and a definition, so the
client can show a count and a percentage together and say exactly what was measured. Counts below the minimum cell
size are never present: such categories are folded into an aggregate or omitted, and the response says so.
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict

DISCLOSURE = ("Illustrative insights based on synthetic demo profiles. These are not real-world workforce statistics.")
SAMPLE_TITLE = "Career patterns in Trace's synthetic sample"
REAL_DISCLOSURE = ("Aggregate insights from Trace's consented, de-identified reference profiles. This is a small sample of "
                   "real career histories, not a representative survey of the workforce.")
REAL_TITLE = "Career patterns in Trace's consented reference sample"
REAL_LIMITATIONS = [
    "These profiles are real career histories shared with consent and de-identified before analysis. The sample is small and "
    "not representative of any wider population.",
    "Percentages describe this sample only. They do not predict what will happen to anyone.",
    "Categories shared by fewer than {min_cell} profiles are not shown individually, and no individual profile is ever shown.",
    "Durations and time between roles are not shown.",
    "A skill or responsibility that appears often in this sample is a pattern, not a requirement for the role.",
]
NOT_RECORDED_NOTE = "Not recorded in your reviewed profile does not mean you lack that experience."
LIMITATIONS = [
    "Every profile in this sample is synthetic: fictional people, employers and careers created to demonstrate Trace.",
    "Percentages describe this small synthetic sample only. They do not predict what will happen to anyone.",
    "Your sample profile is itself one of the synthetic profiles in this sample.",
    "Categories shared by fewer than {min_cell} profiles are not shown individually.",
    "Durations and time between roles are not shown: too few profiles have precise enough dates.",
    "A skill or responsibility that appears often in this sample is a pattern, not a requirement for the role.",
    NOT_RECORDED_NOTE,
]


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Bucket(_View):
    label: str
    count: int                                   # profiles; always >= min_cell
    share_pct: int                               # round(100 * count / denominator)
    in_your_profile: Optional[bool] = None       # None: not compared for this metric


class Metric(_View):
    key: str
    title: str
    definition: str                              # what one profile counts for
    population: Literal["target_cohort", "whole_sample", "movers_into_target", "profiles_with_moves"]
    population_label: str
    population_size: int                         # profiles in the population
    denominator: int                             # profiles in the population with this information recorded
    not_recorded: int                            # population_size - denominator
    multi_valued: bool                           # a profile can count in more than one category
    status: Literal["shown", "insufficient", "all_categories_small"]
    buckets: List[Bucket] = []
    other_count: Optional[int] = None            # single-valued metrics only: profiles in categories not shown, when
                                                 # that aggregate itself reaches min_cell
    other_share_pct: Optional[int] = None
    small_categories: int = 0                    # categories not shown because fewer than min_cell profiles share each
    more_categories: int = 0                     # further categories with enough profiles, beyond the top shown
    note: Optional[str] = None


class Headline(_View):
    key: str
    label: str
    definition: str
    population_label: str
    status: Literal["shown", "insufficient", "suppressed"]      # suppressed: hidden to prevent derivation by subtraction
    count: Optional[int] = None
    denominator: Optional[int] = None
    share_pct: Optional[int] = None
    in_your_profile: Optional[bool] = None


class Section(_View):
    key: Literal["background", "employers", "transitions", "experience"]
    title: str
    headlines: List[Headline] = []
    metrics: List[Metric]


class InsightsTarget(_View):
    role_id: str
    label: str


class CohortScope(_View):
    sample_size: Optional[int]                   # eligible profiles in the cohort; None when below min_cell
    target_cohort_size: Optional[int]            # of which held the target role; None when below min_cell
    target_cohort_below_min_cell: bool
    scope_used: Literal["target_cohort", "whole_sample"]
    min_target_cohort: int                       # target cohort size needed to describe it separately
    note: str


Cohort = Literal["synthetic", "consented_real"]


class CareerInsights(_View):
    title: str = SAMPLE_TITLE
    data_mode: Literal["synthetic_demo", "production"]
    cohort: Cohort = "synthetic"                 # the one source cohort of every number below; never combined
    cohort_label: str = "Synthetic sample"
    status: Literal["ready", "insufficient", "not_ready"] = "ready"   # not_ready: no eligible profiles yet
    eligible_profiles: Optional[int] = None      # profiles eligible in the cohort now (None when below min_cell)
    included_profiles: Optional[int] = None      # profiles actually used for these numbers (None when below min_cell)
    is_synthetic: bool = True
    disclosure: str = DISCLOSURE
    target: InsightsTarget
    scope: Optional[CohortScope] = None
    min_cell: int
    min_denominator: int
    sections: List[Section]
    your_profile_basis: str = "Compared with your reviewed (confirmed or corrected) records only."
    limitations: List[str]
