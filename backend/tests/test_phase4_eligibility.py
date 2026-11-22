"""Product Phase 4, D29: employer-specific eligibility, evaluated on the APPROVED structured criteria
(data-foundation/knowledge_packs/pm-eligibility-v3.json; D36 and the Phase 4 closure). Pure tests; invented profiles."""
import json
from pathlib import Path

import pytest

from app.services.transition.eligibility import (
    MEETS, NOT_MET, UNCLEAR, EligibilityFacts, Job, Qualification, evaluate, experience_months, posting_eligibility,
)

ROOT = Path(__file__).resolve().parents[2]
RULES = json.loads((ROOT / "data-foundation/knowledge_packs/pm-eligibility-v3.json").read_text(encoding="utf-8"))
ALIASES = {a: f for f in RULES["education_fields"] for a in f["aliases"]}
LABELS = {"product_management": "Product management", "data_analytics": "Data analytics", "consulting": "Consulting",
          "project_program_management": "Project and program management", "software_engineering": "Software engineering"}
NOW = 2026 * 12 + 10                                          # Nov 2026 as a month index


def m(y, mo=1):
    return y * 12 + mo - 1


def job(family, start, end=NOW, exposure=(), open_=(), counts=True, rid=None, start_hi=None, end_lo=None):
    return Job(record_id=rid or f"{family}-{start}", title=family, role_family=family, start_lo=start, start_hi=start_hi or start,
               end_lo=end_lo or end, end_hi=end, counts_as_experience=counts, exposure_records=tuple(exposure), open_records=tuple(open_))


def qual(level, qtype, field):
    from app.services.transition.eligibility import normalise_field
    f = ALIASES.get(normalise_field(field))
    return Qualification(level=level, qtype=qtype, field_code=f["code"] if f else None, field_category=f["category"] if f else None,
                         field_label=f["label"] if f else None, field_raw=field)


def facts(*jobs, current="__last__", quals=()):
    cur = jobs[-1].role_family if (current == "__last__" and jobs) else (None if current == "__last__" else current)
    return EligibilityFacts(jobs=tuple(jobs), current_role_family=cur, qualifications=tuple(quals))


def posting(key):
    return {"criteria": [{"text": c["text"], "importance": imp, "criterion": c["rule"]}
                         for c in RULES["criteria"] if c["source"] == key
                         for imp in [IMPORTANCE[(key, c["text"])]]]}


IMPORTANCE = {}
for _s in json.loads((ROOT / "data-foundation/knowledge_packs/pm-v1.json").read_text(encoding="utf-8"))["sources"]:
    for _c in _s.get("employer_criteria", []):
        IMPORTANCE[(_s["key"], _c["text"])] = _c["importance"]


def outcome(key, f):
    return posting_eligibility(posting(key), f, LABELS)["outcome"]


# --------------------------------------------------------------------------- months from partial dates
def test_experience_months_counts_overlaps_once_and_bounds_imprecise_dates():
    a = job("x", m(2020, 1), end=m(2020, 12))
    b = job("x", m(2020, 7), end=m(2021, 6))                       # overlaps a by 6 months
    assert experience_months([a, b]) == (18, 18)
    year_only = Job("y", "t", "x", m(2019, 1), m(2019, 12), m(2021, 1), m(2021, 12), True)   # 2019 to 2021, years only
    lo, hi = experience_months([year_only])
    assert (lo, hi) == (m(2021, 1) - m(2019, 12) + 1, m(2021, 12) - m(2019, 1) + 1) == (14, 36)
    unknown_start = Job("z", "t", "x", None, None, m(2021, 1), m(2021, 1), True)
    assert experience_months([unknown_start]) == (0, None)        # unbounded: never "does not meet"


# --------------------------------------------------------------------------- Target (J11): OR with product exposure
def test_target_adjacent_background_needs_years_and_product_exposure():
    da_title_only = facts(job("data_analytics", m(2019, 1)))                         # ~7.8 years, no exposure lines
    assert outcome("J11", da_title_only) == NOT_MET                                  # a Data Analyst title alone is not enough
    da_exposed = facts(job("data_analytics", m(2019, 1), exposure=["ev-1"]))
    assert outcome("J11", da_exposed) == MEETS
    da_open = facts(job("data_analytics", m(2019, 1), open_=["ev-2"]))
    assert outcome("J11", da_open) == UNCLEAR                                        # lines not yet confirmed or categorised
    da_short = facts(job("data_analytics", m(2023, 1), exposure=["ev-1"]))           # under 5 years
    assert outcome("J11", da_short) == NOT_MET
    pm = facts(job("product_management", m(2022, 1)))                               # ~4.8 years of PM
    assert outcome("J11", pm) == MEETS


def test_engineering_preference_is_not_a_substitute_for_pm_experience():
    eng = facts(job("software_engineering", m(2015, 1)))                            # 11+ years engineering, no PM
    res = posting_eligibility(posting("J3"), eng, LABELS)
    assert res["outcome"] == NOT_MET                                                 # the required PM years decide
    assert res["preferred"][0]["result"]["outcome"] == MEETS                         # the preference is met, and only reported


def test_more_than_a_stated_range_is_unclear_never_excluded():
    pm = facts(job("product_management", m(2014, 1)))                               # 12+ years
    r = evaluate({"type": "years_in_families", "families": ["product_management"], "min": 8, "max": 10}, pm, LABELS)
    assert r.outcome == UNCLEAR and "more than the stated range" in r.explanation


def test_imprecise_dates_give_unclear_not_a_guess():
    j = Job("pm", "PM", "product_management", m(2023, 1), m(2023, 12), NOW, NOW, True)   # started "2023" (year only)
    r = evaluate({"type": "years_in_families", "families": ["product_management"], "min": 3.5, "max": None}, facts(j), LABELS)
    assert r.outcome == UNCLEAR


def test_an_unmapped_job_can_never_support_does_not_meet():
    unmapped = Job("u", "Something", None, m(2015, 1), m(2015, 1), NOW, NOW, True)
    assert outcome("J5", facts(unmapped, current=None)) == UNCLEAR


# --------------------------------------------------------------------------- Sarvam (J12): current vs historical
def test_historical_consulting_is_not_a_current_transition():
    f = facts(job("consulting", m(2014, 1), end=m(2016, 12)), job("product_management", m(2017, 1)))
    res = posting_eligibility(posting("J12"), f, LABELS)
    leaf = res["required"][0]["result"]["parts"][2]
    assert leaf["outcome"] == MEETS and "earlier role" in leaf["explanation"]
    assert res["outcome"] == UNCLEAR                                                 # 9+ years: more than the 3-7 stated


def test_a_current_consultant_is_excluded_by_this_employer_only():
    f = facts(job("product_management", m(2018, 1), end=m(2022, 12)), job("consulting", m(2023, 1)))
    res = posting_eligibility(posting("J12"), f, LABELS)
    assert res["outcome"] == NOT_MET
    assert res["required"][0]["result"]["parts"][2]["outcome"] == NOT_MET
    assert outcome("J11", f) == MEETS                                                # another employer, its own criteria


def test_unknown_current_role_is_unclear():
    f = facts(job("product_management", m(2020, 1)), current=None)
    assert posting_eligibility(posting("J12"), f, LABELS)["required"][0]["result"]["parts"][1]["outcome"] == UNCLEAR


# --------------------------------------------------------------------------- qualifications and stated conditions
def test_qualification_or_equivalent_never_gives_does_not_meet():
    none = facts(job("product_management", m(2015, 1)))
    other = facts(job("product_management", m(2015, 1)), quals=[qual("bachelor", "BA", "History")])
    cs = facts(job("product_management", m(2015, 1)), quals=[qual("bachelor", "BTech", "Computer Science")])
    for f, expected in ((none, UNCLEAR), (other, UNCLEAR), (cs, MEETS)):
        r = posting_eligibility(posting("J9"), f, LABELS)["required"]
        edu = next(x for x in r if "Bachelor" in x["text"])
        assert edu["result"]["outcome"] == expected


def test_a_stated_condition_the_profile_cannot_show_is_unclear():
    pm = facts(job("product_management", m(2019, 1)))
    res = posting_eligibility(posting("J5"), pm, LABELS)
    assert res["outcome"] == UNCLEAR                                                 # 3+ PM years met; consumer products unknown
    assert [p["outcome"] for p in res["required"][0]["result"]["parts"]] == [MEETS, UNCLEAR]


def test_preferred_criteria_never_decide():
    pm = facts(job("product_management", m(2014, 1)))
    res = posting_eligibility(posting("J4"), pm, LABELS)
    assert {p["importance"] for p in res["preferred"]} == {"preferred"} and len(res["preferred"]) == 2
    assert res["outcome"] == UNCLEAR                                                 # decided by the required criterion alone


def test_every_stored_criterion_has_an_approved_rule():
    assert set(IMPORTANCE) == {(c["source"], c["text"]) for c in RULES["criteria"]}


@pytest.mark.parametrize("node", [
    {"type": "years_in_families", "families": ["product_management"], "min": 3, "max": None},
    {"type": "product_exposure", "families": ["data_analytics"]},
    {"type": "current_role_in_families", "families": ["product_management"]},
    {"type": "not_transitioning_from", "families": ["consulting"]},
    {"type": "total_years", "min": 8},
    {"type": "qualification", "min_level": "bachelor", "fields": ["engineering"], "or_equivalent": True},
    {"type": "stated_condition", "text": "x"},
])
def test_an_empty_profile_is_never_met_and_exclusions_need_a_known_current_role(node):
    r = evaluate(node, facts(current=None), LABELS)
    assert r.outcome in (UNCLEAR, NOT_MET)
    if node["type"] in ("current_role_in_families", "not_transitioning_from", "qualification", "stated_condition"):
        assert r.outcome == UNCLEAR


def test_a_failed_preference_never_changes_meets():
    p = {"criteria": [{"text": "3+ years PM", "importance": "required",
                       "criterion": {"type": "years_in_families", "families": ["product_management"], "min": 3, "max": None}},
                      {"text": "2+ years engineering", "importance": "preferred",
                       "criterion": {"type": "years_in_families", "families": ["software_engineering"], "min": 2, "max": None}}]}
    res = posting_eligibility(p, facts(job("product_management", m(2020, 1))), LABELS)
    assert res["preferred"][0]["result"]["outcome"] == NOT_MET and res["outcome"] == MEETS


# --------------------------------------------------------------------------- the reviewed profile as eligibility reads it
def _ev(text, areas, status="user_confirmed", stale=False):
    import uuid
    from types import SimpleNamespace as NS
    return NS(candidate_evidence_id=uuid.uuid4(), evidence_text=text, area_classification=areas, area_classification_is_stale=stale,
              provenance=NS(review_status=status))


def _emp(family, status="user_confirmed", lines=(), start=(2018, 1)):
    import uuid
    from types import SimpleNamespace as NS
    lo = m(*start)
    return NS(candidate_employment_id=uuid.uuid4(), title_raw=family, role_family=family, mapping_is_stale=False, mapping_status="confirmed",
              is_current_as_stated=True, end=NS(precision="ongoing", lo=None, hi=None), start=NS(lo=lo, hi=lo),
              counts_as_full_time_experience=True, employment_type="full_time", provenance=NS(review_status=status),
              responsibilities=list(lines), achievements=[])


def test_only_reviewed_jobs_and_current_categories_count():
    from types import SimpleNamespace as NS
    from app.services.transition.service import eligibility_facts
    stale = _ev("Prioritised the backlog", ["prioritisation_backlog"], stale=True)
    fresh = _ev("Owned the roadmap", ["roadmap_ownership"])
    profile = NS(employment_history=[_emp("product_management", status="pending_review", start=(2015, 1)),
                                     _emp("data_analytics", lines=[stale, fresh, _ev("Draft line", ["roadmap_ownership"], "pending_review")])],
                 education=[])
    f = eligibility_facts(profile, NS(status="known", role_family="data_analytics", role_id="ROLE-DA"), ["prioritisation_backlog", "roadmap_ownership"], NOW)
    assert [j.role_family for j in f.jobs] == ["data_analytics"]                          # a draft job never counts
    (j,) = f.jobs
    assert j.exposure_records == (str(fresh.candidate_evidence_id),)                     # a stale category never shows exposure
    assert set(j.open_records) == {str(stale.candidate_evidence_id), str(profile.employment_history[1].responsibilities[2].candidate_evidence_id)}
    assert j.end_lo == j.end_hi == NOW                                                   # "current" ends at the assessment date



# --------------------------------------------------------------------------- D36 corrections
def test_a_btech_meets_be_btech_whatever_the_discipline_which_is_reported_separately():
    f = facts(job("product_management", m(2015, 1)), quals=[qual("bachelor", "BTech", "Mechanical")])
    r = next(x for x in posting_eligibility(posting("J4"), f, LABELS)["preferred"] if "B.E." in x["text"])["result"]
    assert r["outcome"] == MEETS and "Mechanical engineering" in r["explanation"]


def test_education_matches_whole_values_through_aliases_never_substrings():
    from app.services.transition.eligibility import normalise_field
    assert normalise_field("Computer Science & Engineering") == "computer science and engineering"
    assert ALIASES[normalise_field("Computer Science & Engineering")]["code"] == "computer_science"
    assert normalise_field("Home Economics") not in ALIASES and normalise_field("Petroleum engineering management") not in ALIASES
    for field, expected in (("Economics", MEETS), ("Home Economics", UNCLEAR), ("EEE", MEETS), ("Electrical", MEETS)):
        f = facts(job("product_management", m(2015, 1)), quals=[qual("bachelor", "BSc", field)])
        edu = next(x for x in posting_eligibility(posting("J8"), f, LABELS)["required"] if "degree" in x["text"])
        assert edu["result"]["outcome"] == expected, field


def test_internships_with_an_unspecified_work_pattern_are_unclear_not_counted():
    intern = Job("i", "PM intern", "product_management", m(2022, 1), m(2022, 1), m(2023, 12), m(2023, 12), True, pattern_uncertain=True)
    pm = job("product_management", m(2024, 1))                                         # ~2.8 years full time
    r = evaluate({"type": "years_in_families", "families": ["product_management"], "min": 3, "max": None}, facts(intern, pm), LABELS)
    assert r.outcome == UNCLEAR and "internship" in r.explanation
    stated = Job("i", "PM intern", "product_management", m(2022, 1), m(2022, 1), m(2023, 12), m(2023, 12), True)
    assert evaluate({"type": "years_in_families", "families": ["product_management"], "min": 3, "max": None},
                    facts(stated, pm), LABELS).outcome == MEETS                         # stated as counting: counted


def test_product_owner_or_founder_years_are_unclear_against_pm_tenure_never_counted_or_rejected():
    po = facts(job("product_ownership", m(2019, 1)))
    assert outcome("J5", po) == UNCLEAR                                                 # 3+ PM years: PO years may or may not count
    founder = facts(job("entrepreneurship", m(2016, 1)), job("consulting", m(2024, 1)))
    res = posting_eligibility(posting("J7"), founder, LABELS)
    assert res["required"][0]["result"]["outcome"] in (UNCLEAR,)                         # 8-10 PM years
    short = facts(job("product_ownership", m(2025, 1)))                                # too short even if it counted
    assert outcome("J5", short) == NOT_MET
    pm_only = facts(job("product_management", m(2024, 6)))                             # neither PO nor founder: unchanged
    assert outcome("J5", pm_only) == NOT_MET


def test_target_keeps_years_and_product_exposure_together():
    exposure_short = facts(job("data_analytics", m(2023, 1), exposure=["ev"]))
    years_no_exposure = facts(job("software_engineering", m(2015, 1)))
    assert outcome("J11", exposure_short) == NOT_MET and outcome("J11", years_no_exposure) == NOT_MET
    assert outcome("J11", facts(job("software_engineering", m(2015, 1), exposure=["ev"]))) == MEETS


def test_j12_exclusions_are_current_only_and_never_reach_j10():
    became_pm = facts(job("consulting", m(2012, 1), end=m(2016, 12)), job("product_management", m(2021, 1)))
    j12 = posting_eligibility(posting("J12"), became_pm, LABELS)
    assert [p["outcome"] for p in j12["required"][0]["result"]["parts"]] == [MEETS, MEETS, MEETS] and j12["outcome"] == MEETS
    consultant_now = facts(job("product_management", m(2018, 1), end=m(2022, 12)), job("consulting", m(2023, 1)))
    assert outcome("J12", consultant_now) == NOT_MET
    j10 = posting_eligibility(posting("J10"), consultant_now, LABELS)
    assert "consult" not in json.dumps(j10).lower()                                   # J12's restriction is not applied to J10
    assert j10["required"][0]["result"]["parts"][0]["outcome"] == MEETS                # 5 years of PM: within 4-8, consulting irrelevant
    assert j10["outcome"] == UNCLEAR                                                   # only J10's own stated condition remains


def test_a_posting_without_criteria_says_so():
    res = posting_eligibility({"criteria": []}, facts(job("product_management", m(2015, 1))), LABELS)
    assert res["outcome"] == UNCLEAR and "No criteria are recorded" in res["note"]


def test_the_service_matches_fields_whole_value_and_flags_uncertain_work_patterns():
    from types import SimpleNamespace as NS
    from app.services.transition.eligibility import normalise_field
    from app.services.transition.service import eligibility_facts
    aliases = {normalise_field(a): {"code": f["code"], "label": f["label"], "category": f["category"]}
               for f in RULES["education_fields"] for a in f["aliases"]}
    intern = _emp("product_management")
    intern.employment_type, intern.counts_as_full_time_experience = "internship", None
    stated = _emp("product_management", start=(2020, 1))
    stated.employment_type, stated.counts_as_full_time_experience = "internship", True
    edu = lambda title, field: NS(provenance=NS(review_status="user_confirmed"), qualification_level="bachelor",  # noqa: E731
                                  qualification_title_raw=title, field_of_study=field)
    profile = NS(employment_history=[intern, stated], education=[edu("B.Sc", "Home Economics"), edu("B.E.", "Computer Science & Engineering")])
    f = eligibility_facts(profile, NS(status="known", role_family="product_management", role_id="PM-PM"), [], NOW, aliases)
    assert [j.pattern_uncertain for j in f.jobs] == [True, False]                       # an explicit answer is respected
    assert [(q.qtype, q.field_code) for q in f.qualifications] == [("BSc", None), ("BE", "computer_science")]



# --------------------------------------------------------------------------- Phase 4 closure: J12 and a current APM (D41)
def _j12_current_leaf(f):
    return posting_eligibility(posting("J12"), f, LABELS)["required"][0]["result"]["parts"][1]


def test_j12_current_pm_meets_current_apm_is_unclear_excluded_roles_do_not_meet():
    pm = EligibilityFacts(jobs=(job("product_management", m(2020, 1)),), current_role_family="product_management", qualifications=(),
                          current_role_id="PM-PM")
    apm = EligibilityFacts(jobs=(job("product_management", m(2020, 1)),), current_role_family="product_management", qualifications=(),
                           current_role_id="PM-APM")
    consultant = EligibilityFacts(jobs=(job("product_management", m(2018, 1), end=m(2022, 12)), job("consulting", m(2023, 1))),
                                  current_role_family="consulting", qualifications=(), current_role_id="ROLE-CONSULT")
    assert _j12_current_leaf(pm)["outcome"] == MEETS
    leaf = _j12_current_leaf(apm)
    assert leaf["outcome"] == UNCLEAR and "equivalent" in leaf["explanation"]
    assert _j12_current_leaf(consultant)["outcome"] == NOT_MET
    assert posting_eligibility(posting("J12"), consultant, LABELS)["required"][0]["result"]["parts"][2]["outcome"] == NOT_MET


def test_the_apm_rule_is_narrow_to_j12s_current_role_criterion():
    apm = EligibilityFacts(jobs=(job("product_management", m(2022, 1)),), current_role_family="product_management", qualifications=(),
                           current_role_id="PM-APM")
    j11 = posting_eligibility(posting("J11"), apm, LABELS)["required"][0]["result"]["parts"][0]
    assert j11["outcome"] == MEETS                                                     # APM years still count as PM years elsewhere
    assert not any("unconfirmed_roles" in json.dumps(c["rule"]) for c in RULES["criteria"] if c["source"] != "J12")


# --------------------------------------------------------------------------- E8: J3 "before product" ordering
J3_PREF = "A computer science or engineering degree is not mandatory; ideally 2+ years as an engineer or architect before product"


def _j3_pref(*jobs):
    f = facts(*jobs)
    return next(x for x in posting_eligibility(posting("J3"), f, LABELS)["preferred"] if x["text"] == J3_PREF)["result"]


def test_j3_counts_only_engineering_experience_before_the_first_product_role():
    before = _j3_pref(job("software_engineering", m(2016, 1), end=m(2018, 12)), job("product_management", m(2019, 1)))
    assert before["outcome"] == MEETS and "before your first" in before["explanation"]
    after = _j3_pref(job("product_management", m(2018, 1), end=m(2020, 12)), job("software_engineering", m(2021, 1)))
    assert after["outcome"] == NOT_MET and "has none" in after["explanation"]           # 5+ years of engineering, all after product
    overlap = _j3_pref(job("software_engineering", m(2017, 1), end=m(2020, 12)), job("product_management", m(2019, 1)))
    assert overlap["outcome"] == MEETS                                                 # Jan 2017 - Dec 2018 is before product
    short = _j3_pref(job("software_engineering", m(2018, 1), end=m(2020, 12)), job("product_management", m(2019, 1)))
    assert short["outcome"] == NOT_MET                                                 # only 12 months before product
    vague_pm = Job("pm", "PM", "product_management", m(2019, 1), m(2019, 12), NOW, NOW, True)   # PM start known only as "2019"
    unsure = _j3_pref(job("software_engineering", m(2017, 6), end=m(2020, 12)), vague_pm)
    assert unsure["outcome"] == UNCLEAR                                                # 19 to 30 months before product


def test_j3_ordering_is_a_preference_and_never_decides():
    after = facts(job("product_management", m(2018, 1), end=m(2020, 12)), job("software_engineering", m(2021, 1)))
    res = posting_eligibility(posting("J3"), after, LABELS)
    assert res["preferred"][0]["result"]["outcome"] == NOT_MET
    assert res["outcome"] == res["required"][0]["result"]["outcome"]                   # the outcome is the required criterion's
    long_pm = facts(job("software_engineering", m(2010, 1), end=m(2010, 12)), job("product_management", m(2020, 1)))
    res = posting_eligibility(posting("J3"), long_pm, LABELS)
    assert res["preferred"][0]["result"]["outcome"] == NOT_MET                         # only 1 year of engineering before product
    assert res["required"][0]["result"]["parts"][0]["outcome"] == MEETS and res["outcome"] == UNCLEAR   # unclear only from the stated condition
