"""Career insights over the synthetic reference sample (Phase 5). Read-only, deterministic, no new scoring.

build_insights() is pure: given the synthetic cohort rows (already restricted by the positive allowlist in
repository.py), label maps, thresholds and the candidate's own reviewed values, it returns distributions with explicit
denominators. Rules:

  * Population. The target cohort (synthetic profiles that held the target role) is described separately only when it
    has at least min_profiles_moderate profiles; otherwise the whole synthetic sample is described and the response
    says so. Transition metrics are inherently about the target and use the profiles that moved into it.
  * Denominator. Profiles in the population with that information recorded; the rest are reported as not recorded.
    No percentage is computed over unknown values. A breakdown needs a denominator of at least min_profiles_limited.
  * Small cells. A category with fewer than min_profiles_limited profiles is never shown with its count; such
    categories are folded into "other" (shown only if that aggregate itself reaches the minimum) or omitted.
  * Comparison. in_your_profile marks categories present in the candidate's reviewed (confirmed or corrected)
    records. Absence is "not recorded", never "lacking".

Every row is re-checked to be synthetic (assert_synthetic): a row from any other dataset class is an error, not data.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set

from app.services.career_insights import models as m
from app.services.career_insights.repository import SYNTHETIC_DATASET_CLASS

REVIEWED = ("user_confirmed", "user_corrected")
UNKNOWN = {None, "", "unknown", "not_stated", "other_unknown"}

QUALIFICATION = {"bachelor": "Bachelor's degree", "master": "Master's degree", "mba": "MBA", "doctorate": "Doctorate",
                 "phd": "Doctorate", "diploma": "Diploma", "certificate": "Certificate", "secondary": "Secondary school"}
POSTGRADUATE = {"master", "mba", "doctorate", "phd"}
INDUSTRY = {"saas": "SaaS", "it_services": "IT services", "hr_tech": "HR tech", "analytics_ai": "Analytics and AI",
            "ecommerce_marketplace": "E-commerce and marketplaces", "consumer_internet": "Consumer internet",
            "banking_financial_services": "Banking and financial services", "media_entertainment": "Media and entertainment",
            "fintech": "Fintech", "enterprise_software": "Enterprise software", "edtech": "Edtech", "healthtech": "Healthtech"}
COMPANY_MODEL = {"product_company": "Product company", "it_services": "IT services firm", "non_tech_enterprise": "Non-tech enterprise",
                 "consulting": "Consulting firm", "agency": "Agency", "startup": "Startup"}
SIZE_BAND = {"1-10": "1–10 employees", "11-50": "11–50 employees", "51-200": "51–200 employees", "201-1000": "201–1,000 employees",
             "1001-5000": "1,001–5,000 employees", "5001-plus": "5,001+ employees"}
MOVE_TYPE = {"role_change": "Within the same employer", "role_and_employer_change": "With a change of employer",
             "role_change_employer_unknown": "Employer not recorded"}


class InsightsIntegrityError(RuntimeError):
    """A row outside the synthetic allowlist reached the insights service."""


ACRONYMS = {"api": "API", "apis": "APIs", "sql": "SQL", "ux": "UX", "ui": "UI", "b2b": "B2B", "b2c": "B2C", "saas": "SaaS",
            "ai": "AI", "ml": "ML", "crm": "CRM", "okr": "OKR", "okrs": "OKRs", "kpi": "KPI", "kpis": "KPIs", "gtm": "GTM"}


def humanize(code: str, known: Dict[str, str]) -> str:
    return known.get(code) or code.replace("_", " ").strip().capitalize()


def display(text: str) -> str:
    """Sentence case for lower-case taxonomy labels, keeping common acronyms."""
    if text != text.lower():
        return text
    words = [ACRONYMS.get(w, w) for w in text.split()]
    out = " ".join(words)
    return out[:1].upper() + out[1:]


@dataclass
class YourProfile:
    """The candidate's own reviewed values, as comparable codes."""
    qualification_levels: Set[str] = field(default_factory=set)
    fields_of_study: Set[str] = field(default_factory=set)             # lower-cased
    industries: Set[str] = field(default_factory=set)
    company_models: Set[str] = field(default_factory=set)
    size_bands: Set[str] = field(default_factory=set)
    roles: Set[str] = field(default_factory=set)
    areas: Set[str] = field(default_factory=set)
    skills: Set[str] = field(default_factory=set)
    institutions: Set[str] = field(default_factory=set)               # lower-cased, whitespace-normalised
    employers: Set[str] = field(default_factory=set)                  # lower-cased, whitespace-normalised


def your_profile(profile) -> YourProfile:
    """From a CandidateProfile: reviewed records only; current (not stale) evidence classifications only."""
    y = YourProfile()
    for d in profile.education:
        if d.provenance.review_status in REVIEWED:
            if d.qualification_level not in UNKNOWN:
                y.qualification_levels.add(d.qualification_level)
            if d.field_of_study and d.field_of_study.strip():
                y.fields_of_study.add(d.field_of_study.strip().lower())
            if d.institution_name and d.institution_name.strip():
                y.institutions.add(" ".join(d.institution_name.split()).lower())
    evidence = list(profile.unattached_evidence)
    for e in profile.employment_history:
        evidence += e.responsibilities + e.achievements + e.other_evidence
        if e.provenance.review_status not in REVIEWED:
            continue
        if e.role_id and e.role_id != "ROLE-UNKNOWN" and not e.mapping_is_stale:
            y.roles.add(e.role_id)
        if e.employer:
            if e.employer.employer_name and e.employer.employer_name.strip():
                y.employers.add(" ".join(e.employer.employer_name.split()).lower())
            for attr, target in (("industry_sector", y.industries), ("company_model", y.company_models),
                                 ("company_size_band", y.size_bands)):
                value = getattr(e.employer, attr)
                if value not in UNKNOWN:
                    target.add(value)
    for acts in profile.career_activities.values():
        for a in acts:
            evidence += a.achievements + a.other_evidence
    for x in evidence:
        if x.provenance.review_status in REVIEWED and x.area_classification and not x.area_classification_is_stale:
            y.areas.update(x.area_classification)
    for s in profile.skills:
        if s.provenance.review_status in REVIEWED and s.taxonomy:
            y.skills.add(s.taxonomy.skill_id)
    return y


def assert_dataset_class(rows: Iterable[dict], expected: str) -> None:
    wrong = {r.get("dataset_class") for r in rows if r.get("dataset_class") != expected}
    if wrong:
        raise InsightsIntegrityError(f"dataset classes other than {expected} reached career insights: {sorted(map(str, wrong))}")


def assert_synthetic(rows: Iterable[dict]) -> None:
    assert_dataset_class(rows, SYNTHETIC_DATASET_CLASS)


def _pct(count: int, denominator: int) -> int:
    return int(round(100 * count / denominator)) if denominator else 0


class Thresholds:
    def __init__(self, settings: Dict[str, int]):
        try:
            self.min_cell = int(settings["min_profiles_limited"])
            self.min_target_cohort = int(settings["min_profiles_moderate"])
        except KeyError as exc:                                  # thresholds are required: fail closed
            raise InsightsIntegrityError(f"analytics setting missing: {exc}") from None
        self.min_denominator = self.min_cell


def distribution(key: str, title: str, definition: str, population: str, population_label: str, members: Set[str],
                 values: Dict[str, Set[str]], label: Callable[[str], str], t: Thresholds, *, mine: Optional[Set[str]] = None,
                 multi_valued: bool = True, top: Optional[int] = None, note: Optional[str] = None) -> m.Metric:
    """values: profile -> recorded category codes (unknowns already removed). Only profiles in members count."""
    recorded = {p: v for p, v in values.items() if p in members and v}
    denominator = len(recorded)
    base = dict(key=key, title=title, definition=definition, population=population, population_label=population_label,
                population_size=len(members), denominator=denominator, not_recorded=len(members) - denominator,
                multi_valued=multi_valued, note=note)
    if denominator < t.min_denominator:
        return m.Metric(status="insufficient", **base)
    counts: Dict[str, Set[str]] = defaultdict(set)
    for p, codes in recorded.items():
        for c in codes:
            counts[c].add(p)
    eligible = sorted((c for c, ps in counts.items() if len(ps) >= t.min_cell), key=lambda c: (-len(counts[c]), label(c)))
    small = [c for c in counts if c not in eligible]
    shown, beyond = (eligible[:top], eligible[top:]) if top is not None else (eligible, [])
    other_count = None
    if not multi_valued:
        # Complementary suppression: in a single-valued breakdown the hidden total is denominator minus the shown counts.
        # If that total is 1-4, a hidden small category could be computed by subtraction, so the smallest shown
        # categories are hidden too until the hidden total reaches the minimum (or nothing is shown).
        def hidden_total():
            return denominator - sum(len(counts[c]) for c in shown)
        while shown and 0 < hidden_total() < t.min_cell:
            small.append(shown.pop())
        if small or beyond:
            others = set().union(*(counts[c] for c in small + beyond))
            other_count = len(others) if len(others) >= t.min_cell else None
    buckets = [m.Bucket(label=label(c), count=len(counts[c]), share_pct=_pct(len(counts[c]), denominator),
                        in_your_profile=None if mine is None else c in mine) for c in shown]
    status = "shown" if buckets else "all_categories_small"
    return m.Metric(status=status, buckets=buckets, other_count=other_count,
                    other_share_pct=_pct(other_count, denominator) if other_count else None, small_categories=len(small),
                    more_categories=len(beyond), **base)


def headline(key: str, label: str, definition: str, population_label: str, denominator_members: Set[str],
             members_with: Set[str], t: Thresholds, mine: Optional[bool]) -> m.Headline:
    denominator, count = len(denominator_members), len(members_with & denominator_members)
    if denominator < t.min_denominator or count < t.min_cell:
        return m.Headline(key=key, label=label, definition=definition, population_label=population_label, status="insufficient",
                          in_your_profile=mine)
    return m.Headline(key=key, label=label, definition=definition, population_label=population_label, status="shown", count=count,
                      denominator=denominator, share_pct=_pct(count, denominator), in_your_profile=mine)


def _by_profile(rows: List[dict], column: str, keep: Callable[[Optional[str]], bool] = lambda v: v not in UNKNOWN,
                transform: Callable[[str], str] = lambda v: v) -> Dict[str, Set[str]]:
    out: Dict[str, Set[str]] = defaultdict(set)
    for r in rows:
        v = r[column]
        if keep(v):
            out[r["profile_id"]].add(transform(v))
    return out


def _prior_roles(paths: List[dict], target: str, members: Set[str]) -> Dict[str, Set[str]]:
    """Distinct other roles held before the first episode in the target role (per profile, first segment reaching it)."""
    out: Dict[str, Set[str]] = {}
    for r in paths:
        p, path = r["profile_id"], list(r["role_path"] or [])
        if p in out or p not in members or target not in path:
            continue
        before = {x for x in path[:path.index(target)] if x != target}
        n = len(before)
        out[p] = {"0" if n == 0 else "1" if n == 1 else "2" if n == 2 else "3+"}
    return out


PRIOR_LABEL = {"0": "Started in this role", "1": "1 earlier role", "2": "2 earlier roles", "3+": "3 or more earlier roles"}


@dataclass(frozen=True)
class CohortSpec:
    key: str
    label: str
    noun: str                       # how its profiles are described to people: "Fictional test profiles", "Profiles"
    dataset_class: str
    title: str
    disclosure: str
    limitations: List[str]
    is_synthetic: bool


COHORTS = {
    "synthetic": CohortSpec("synthetic", "Synthetic sample", "Fictional test profiles", SYNTHETIC_DATASET_CLASS, m.SAMPLE_TITLE,
                            m.DISCLOSURE, m.LIMITATIONS, True),
    "consented_real": CohortSpec("consented_real", "Consented reference sample", "Profiles",
                                 "consented_real_reference", m.REAL_TITLE, m.REAL_DISCLOSURE,
                                 m.REAL_LIMITATIONS + [m.NOT_RECORDED_NOTE], False),
}

GRADE_BANDS = {
    "percentage": [(80, "80% or higher"), (70, "70–79%"), (60, "60–69%"), (0, "Below 60%")],
    "cgpa_10": [(9, "9.0 or higher (10-point)"), (8, "8.0–8.9 (10-point)"), (7, "7.0–7.9 (10-point)"), (0, "Below 7.0 (10-point)")],
    "gpa_4": [(3.5, "3.5 or higher (4-point)"), (3.0, "3.0–3.49 (4-point)"), (0, "Below 3.0 (4-point)")],
}


def grade_band(scale: Optional[str], value, raw: Optional[str]) -> Optional[str]:
    if scale in GRADE_BANDS and value is not None:
        for floor, label in GRADE_BANDS[scale]:
            if float(value) >= floor:
                return label
    if scale == "class_division" and raw and raw.strip():
        return raw.strip()
    return None


def _names(rows: List[dict], column: str):
    """Case-insensitive grouping of free-text names (institutions, employers, certifications); first spelling shown."""
    values: Dict[str, Set[str]] = defaultdict(set)
    labels: Dict[str, str] = {}
    for r in sorted(rows, key=lambda r: r["profile_id"]):
        v = r.get(column)
        if v and v.strip():
            k = " ".join(v.split()).lower()
            values[r["profile_id"]].add(k)
            labels.setdefault(k, " ".join(v.split()))
    return values, labels


def _complementary(headlines: List[m.Headline], t: Thresholds) -> List[m.Headline]:
    """Nested headlines (MBA is a subset of postgraduate): a 1-4 difference between them is itself a small cell, so the
    broader one is suppressed."""
    by = {h.key: h for h in headlines}
    outer, inner = by.get("postgraduate"), by.get("mba")
    if outer and inner and outer.status == inner.status == "shown" and 0 < outer.count - inner.count < t.min_cell:
        by["postgraduate"] = m.Headline(key=outer.key, label=outer.label, definition=outer.definition,
                                        population_label=outer.population_label, status="suppressed",
                                        in_your_profile=outer.in_your_profile)
    return [by[h.key] for h in headlines]


def build_insights(rows: Dict[str, List[dict]], labels: Dict[str, Dict[str, str]], settings: Dict[str, int], target_role_id: str,
                   target_label: str, mine: YourProfile, data_mode: str = "synthetic_demo",
                   cohort_key: str = "synthetic") -> m.CareerInsights:
    spec = COHORTS[cohort_key]
    assert_dataset_class(rows["profiles"], spec.dataset_class)
    t = Thresholds(settings)
    sample = {r["profile_id"] for r in rows["profiles"]}
    common = dict(data_mode=data_mode, cohort=spec.key, cohort_label=spec.label, is_synthetic=spec.is_synthetic,
                  title=spec.title, disclosure=spec.disclosure, target=m.InsightsTarget(role_id=target_role_id, label=target_label),
                  min_cell=t.min_cell, min_denominator=t.min_denominator,
                  limitations=[x.format(min_cell=t.min_cell) for x in spec.limitations])
    if len(sample) < t.min_cell:                     # nothing is described from fewer than min_cell profiles
        return m.CareerInsights(status="not_ready" if not sample else "insufficient", sections=[], **common)
    keep = {name: [r for r in rs if r["profile_id"] in sample] for name, rs in rows.items()}   # defence in depth
    role = lambda c: labels["roles"].get(c, humanize(c, {}))                                    # noqa: E731
    noun = spec.noun

    cohort = {r["profile_id"] for r in keep["employments"] if r["role_id"] == target_role_id}
    use_cohort = len(cohort) >= t.min_target_cohort
    members = cohort if use_cohort else sample
    pop = "target_cohort" if use_cohort else "whole_sample"
    pop_label = (f"{noun} that held the {target_label} role" if use_cohort else f"All {noun.lower()}")
    if use_cohort:
        scope_note = f"Patterns below describe the {len(cohort)} {noun.lower()} that held the {target_label} role."
    else:
        held = (f"only {len(cohort)}" if len(cohort) >= t.min_cell else f"fewer than {t.min_cell}")
        scope_note = (f"Not enough profiles in this sample held the {target_label} role ({held}) to describe them separately, "
                      f"so these patterns describe all {len(sample)} {noun.lower()}.")

    # --------------------------------------------------------------- background and education
    quals = _by_profile(keep["education"], "qualification_level")
    fields = _by_profile(keep["education"], "field_of_study", keep=lambda v: bool(v and v.strip()), transform=lambda v: v.strip().lower())
    field_label = {}
    for r in sorted(keep["education"], key=lambda r: r["profile_id"]):                       # the sample's own spelling
        if r["field_of_study"] and r["field_of_study"].strip():
            field_label.setdefault(r["field_of_study"].strip().lower(), r["field_of_study"].strip())
    institutions, institution_label = _names(keep["education"], "institution_name")
    grades: Dict[str, Set[str]] = defaultdict(set)
    for r in keep["education"]:
        band = grade_band(r.get("grade_scale"), r.get("grade_value"), r.get("grade_raw"))
        if band:
            grades[r["profile_id"]].add(band)
    with_education = {p for p in members if quals.get(p)}
    postgraduate = {p for p, v in quals.items() if v & POSTGRADUATE}
    mba = {p for p, v in quals.items() if "mba" in v}
    background = m.Section(key="background", title="Background and education", headlines=_complementary([
        headline("postgraduate", "Hold a postgraduate qualification", "Profiles with at least one MBA, master's or doctorate, "
                 "out of profiles with any education recorded.", pop_label, with_education, postgraduate, t,
                 bool(mine.qualification_levels & POSTGRADUATE)),
        headline("mba", "Hold an MBA", "Profiles with at least one MBA, out of profiles with any education recorded.", pop_label,
                 with_education, mba, t, "mba" in mine.qualification_levels),
    ], t), metrics=[
        distribution("qualification_level", "Qualifications held", "Profiles holding at least one qualification at each level.",
                     pop, pop_label, members, quals, lambda c: humanize(c, QUALIFICATION), t, mine=mine.qualification_levels),
        distribution("field_of_study", "Fields of study", "Profiles with at least one qualification in each field.", pop, pop_label,
                     members, fields, lambda c: field_label.get(c, c), t, mine=mine.fields_of_study, top=8),
        distribution("institution", "Universities and colleges", "Profiles with at least one qualification from each institution.",
                     pop, pop_label, members, institutions, lambda c: institution_label.get(c, c), t,
                     mine=mine.institutions, top=8),
        distribution("grades", "Recorded grades", "Profiles with at least one recorded grade in each band, per grading scale.",
                     pop, pop_label, members, grades, lambda c: c, t,
                     note="Grades are grouped by their own scale; different scales are not converted into each other."),
    ])

    # --------------------------------------------------------------- employers and industries
    industries = _by_profile(keep["employers"], "industry_sector")
    models_ = _by_profile(keep["employers"], "company_model")
    sizes = _by_profile(keep["employers"], "company_size_band")
    employer_names, employer_label = _names(keep["employers"], "employer_name")
    employers = m.Section(key="employers", title="Employers and industries", metrics=[
        distribution("industry", "Industries in their careers", "Profiles that worked at least once in each industry.", pop,
                     pop_label, members, industries, lambda c: humanize(c, INDUSTRY), t, mine=mine.industries, top=8),
        distribution("employer", "Employers", "Profiles that worked at least once at each employer.", pop, pop_label, members,
                     employer_names, lambda c: employer_label.get(c, c), t, mine=mine.employers, top=8),
        distribution("company_model", "Types of organisation", "Profiles that worked at least once at each type of organisation.",
                     pop, pop_label, members, models_, lambda c: humanize(c, COMPANY_MODEL), t, mine=mine.company_models),
        distribution("company_size", "Organisation size", "Profiles that worked at least once at an organisation of each size.",
                     pop, pop_label, members, sizes, lambda c: humanize(c, SIZE_BAND), t, mine=mine.size_bands),
    ])

    # --------------------------------------------------------------- career transitions
    into = [r for r in keep["transitions"] if r["to_role_id"] == target_role_id]
    movers = {r["profile_id"] for r in into}
    movers_label = f"{noun} with a confirmed move into {target_label}"
    from_roles = _by_profile(into, "from_role_id")
    move_types = _by_profile(into, "change_type")
    with_moves = {r["profile_id"] for r in keep["transitions"]}
    pairs: Dict[str, Set[str]] = defaultdict(set)
    for r in keep["transitions"]:
        pairs[r["profile_id"]].add(f"{r['from_role_id']}>{r['to_role_id']}")
    pair_label = lambda c: " → ".join(role(x) for x in c.split(">"))                             # noqa: E731
    transitions = m.Section(key="transitions", title="Career transitions", metrics=[
        distribution("roles_before_target", f"Roles held just before {target_label}",
                     f"Profiles by the role they held immediately before a confirmed move into {target_label}.",
                     "movers_into_target", movers_label, movers, from_roles, role, t, mine=mine.roles),
        distribution("move_type", f"How people moved into {target_label}",
                     "Profiles by whether the move into the role was with the same employer or a new one.",
                     "movers_into_target", movers_label, movers, move_types, lambda c: humanize(c, MOVE_TYPE), t),
        distribution("prior_roles", f"Different roles before first reaching {target_label}",
                     "Profiles by how many other roles they held before their first episode in the role.",
                     "target_cohort", f"{noun} that held the {target_label} role", cohort,
                     _prior_roles(keep["paths"], target_role_id, cohort), lambda c: PRIOR_LABEL[c], t, multi_valued=False),
        distribution("common_moves", "Common role-to-role moves", "Profiles that made each confirmed move between two roles.",
                     "profiles_with_moves", f"{noun} with at least one confirmed role change", with_moves, pairs,
                     pair_label, t, top=8),
    ])

    # --------------------------------------------------------------- experience and skills
    areas = _by_profile(keep["areas"], "responsibility_area")
    skills = _by_profile(keep["skills"], "skill_id")
    certifications, certification_label = _names([r for r in keep.get("activities", []) if r["activity_type"] == "certification"],
                                                 "title")
    experience = m.Section(key="experience", title="Experience and skills", metrics=[
        distribution("responsibility_areas", "Responsibility areas in their experience",
                     "Profiles with recorded experience in each responsibility area.", pop, pop_label, members, areas,
                     lambda c: labels["areas"].get(c, humanize(c, {})), t, mine=mine.areas),
        distribution("skills", "Skills represented", "Profiles listing each skill.", pop, pop_label, members, skills,
                     lambda c: display(labels["skills"].get(c, humanize(c, {}))), t, mine=mine.skills, top=10,
                     note="A frequent skill is a pattern in this sample, not a requirement for the role."),
        distribution("certifications", "Certifications", "Profiles holding each certification.", pop, pop_label, members,
                     certifications, lambda c: certification_label.get(c, c), t, top=8),
    ])

    cohort_ok = len(cohort) >= t.min_cell
    return m.CareerInsights(
        status="ready", eligible_profiles=len(sample), included_profiles=len(sample),
        scope=m.CohortScope(sample_size=len(sample), target_cohort_size=len(cohort) if cohort_ok else None,
                            target_cohort_below_min_cell=not cohort_ok, scope_used=pop, min_target_cohort=t.min_target_cohort,
                            note=scope_note),
        sections=[background, employers, transitions, experience], **common)
