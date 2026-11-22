"""Career insights (Phase 5): the pure aggregation, with a fake synthetic cohort. No database."""
import json

import pytest

from app.services.career_insights import CareerInsights, InsightsIntegrityError, YourProfile, build_insights
from app.services.career_insights.service import display

SETTINGS = {"min_profiles_limited": 5, "min_profiles_moderate": 15}
LABELS = {"roles": {"PM-APM": "Associate Product Manager", "PM-PM": "Product Manager", "PM-SPM": "Senior Product Manager",
                    "ROLE-SWE": "Software Engineer"},
          "areas": {"roadmap_ownership": "Roadmap ownership", "data_analysis": "Data analysis", "outcome_ownership": "Outcome ownership"},
          "skills": {"SKL-SQL": "sql", "SKL-APIS": "apis", "SKL-ROADMAP": "product roadmap"}}


def cohort(n=20, target_holders=16, mba=6, sql=5, rare_area=3):
    """n synthetic profiles; the first target_holders held PM-PM; deterministic attributes."""
    ids = [f"SYN-{i:03d}" for i in range(1, n + 1)]
    rows = {k: [] for k in ("profiles", "employments", "education", "employers", "transitions", "paths", "areas", "skills")}
    for i, p in enumerate(ids):
        rows["profiles"].append({"profile_id": p, "dataset_class": "synthetic_test_fixture"})
        rows["employments"].append({"profile_id": p, "role_id": "PM-APM", "employer_id": f"{p}-E1"})
        if i < target_holders:
            rows["employments"].append({"profile_id": p, "role_id": "PM-PM", "employer_id": f"{p}-E1"})
            rows["transitions"].append({"profile_id": p, "from_role_id": "PM-APM", "to_role_id": "PM-PM",
                                        "change_type": "role_change" if i % 2 else "role_and_employer_change"})
            rows["paths"].append({"profile_id": p, "segment_no": 1, "role_path": ["PM-APM", "PM-PM"]})
        if i < n - 2:                                                     # two profiles have no education recorded
            rows["education"].append({"profile_id": p, "qualification_level": "bachelor", "field_of_study": "Computer Science"})
        if i < mba:
            rows["education"].append({"profile_id": p, "qualification_level": "mba", "field_of_study": "general management"})
        rows["employers"].append({"profile_id": p, "employer_id": f"{p}-E1", "industry_sector": "saas" if i % 2 else "fintech",
                                  "company_model": "product_company", "company_size_band": "unknown"})
        rows["areas"].append({"profile_id": p, "responsibility_area": "roadmap_ownership"})
        if i < rare_area:
            rows["areas"].append({"profile_id": p, "responsibility_area": "data_analysis"})
        if i < sql:
            rows["skills"].append({"profile_id": p, "skill_id": "SKL-SQL"})
    return rows


def metric(result, key):
    return next(m for s in result.sections for m in s.metrics if m.key == key)


def headline(result, key):
    return next(h for s in result.sections for h in s.headlines if h.key == key)


def all_counts(obj):
    """Every count-like integer in the serialized response."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("count", "other_count", "target_cohort_size") and isinstance(v, int):
                out.append(v)
            out += all_counts(v)
    elif isinstance(obj, list):
        for v in obj:
            out += all_counts(v)
    return out


def test_target_cohort_is_described_when_large_enough():
    r = build_insights(cohort(), LABELS, SETTINGS, "PM-PM", "Product Manager", YourProfile())
    assert isinstance(r, CareerInsights) and r.data_mode == "synthetic_demo" and r.is_synthetic is True
    assert (r.scope.sample_size, r.scope.target_cohort_size, r.scope.scope_used) == (20, 16, "target_cohort")
    q = metric(r, "qualification_level")
    assert (q.population, q.population_size, q.denominator, q.not_recorded) == ("target_cohort", 16, 16, 0)
    assert [(b.label, b.count, b.share_pct) for b in q.buckets] == [("Bachelor's degree", 16, 100), ("MBA", 6, 38)]


def test_small_target_cohort_falls_back_to_the_whole_sample_and_says_so():
    r = build_insights(cohort(target_holders=7), LABELS, SETTINGS, "PM-PM", "Product Manager", YourProfile())
    assert (r.scope.scope_used, r.scope.target_cohort_size) == ("whole_sample", 7)
    assert "Not enough profiles" in r.scope.note and "all 20 fictional test profiles" in r.scope.note
    q = metric(r, "qualification_level")
    assert (q.population, q.population_size, q.denominator, q.not_recorded) == ("whole_sample", 20, 18, 2)
    assert q.buckets[0].count == 18 and q.buckets[0].share_pct == 100            # 18 of 18 recorded, not 18 of 20


def test_target_cohort_below_the_cell_minimum_is_never_counted():
    r = build_insights(cohort(target_holders=3), LABELS, SETTINGS, "PM-PM", "Product Manager", YourProfile())
    assert r.scope.target_cohort_size is None and r.scope.target_cohort_below_min_cell
    assert "fewer than 5" in r.scope.note and " 3)" not in r.scope.note
    assert metric(r, "roles_before_target").status == "insufficient"
    assert metric(r, "prior_roles").status == "insufficient"


def test_categories_below_five_are_never_shown_individually():
    r = build_insights(cohort(rare_area=3, sql=4), LABELS, SETTINGS, "PM-PM", "Product Manager", YourProfile())
    areas = metric(r, "responsibility_areas")
    assert [b.label for b in areas.buckets] == ["Roadmap ownership"] and areas.small_categories == 1
    assert areas.other_count is None                                    # multi-valued: no misleading "other" profile count
    skills = metric(r, "skills")
    assert (skills.status, skills.denominator, skills.buckets) == ("insufficient", 4, [])         # denominator below 5
    rows = cohort(sql=4)
    rows["skills"] += [{"profile_id": f"SYN-{i:03d}", "skill_id": "SKL-APIS"} for i in range(5, 9)]  # 8 profiles, 4 + 4
    r2 = build_insights(rows, LABELS, SETTINGS, "PM-PM", "Product Manager", YourProfile())
    skills = metric(r2, "skills")
    assert (skills.status, skills.denominator, skills.buckets, skills.small_categories) == ("all_categories_small", 8, [], 2)
    for res in (r, r2):
        assert all(c >= 5 for c in all_counts(json.loads(res.model_dump_json())))


def test_single_valued_other_aggregate_only_when_it_reaches_the_minimum():
    r = build_insights(cohort(target_holders=16), LABELS, SETTINGS, "PM-PM", "Product Manager", YourProfile())
    moves = metric(r, "move_type")
    assert moves.multi_valued and {b.label for b in moves.buckets} == {"Within the same employer", "With a change of employer"}
    prior = metric(r, "prior_roles")
    assert not prior.multi_valued and [(b.label, b.count) for b in prior.buckets] == [("1 earlier role", 16)]
    assert prior.other_count is None and prior.other_share_pct is None


def test_unknown_values_are_not_recorded_rather_than_a_category():
    r = build_insights(cohort(), LABELS, SETTINGS, "PM-PM", "Product Manager", YourProfile())
    size = metric(r, "company_size")
    assert size.status == "insufficient" and size.denominator == 0 and size.not_recorded == 16 and size.buckets == []


def test_headlines_use_the_education_denominator_and_hide_small_counts():
    r = build_insights(cohort(target_holders=7, mba=6), LABELS, SETTINGS, "PM-PM", "Product Manager", YourProfile(qualification_levels={"mba"}))
    mba = headline(r, "mba")
    assert (mba.status, mba.count, mba.denominator, mba.share_pct, mba.in_your_profile) == ("shown", 6, 18, 33, True)
    small = build_insights(cohort(mba=4), LABELS, SETTINGS, "PM-PM", "Product Manager", YourProfile())
    assert headline(small, "mba").status == "insufficient" and headline(small, "mba").count is None


def test_your_profile_markers_never_claim_absence_as_lacking():
    mine = YourProfile(areas={"roadmap_ownership"}, industries={"saas"}, fields_of_study={"computer science"}, roles={"PM-APM"})
    r = build_insights(cohort(), LABELS, SETTINGS, "PM-PM", "Product Manager", mine)
    assert metric(r, "responsibility_areas").buckets[0].in_your_profile is True
    ind = {b.label: b.in_your_profile for b in metric(r, "industry").buckets}
    assert ind == {"SaaS": True, "Fintech": False}
    assert {b.label: b.in_your_profile for b in metric(r, "field_of_study").buckets}["Computer Science"] is True
    assert metric(r, "roles_before_target").buckets[0].in_your_profile is True
    assert all(b.in_your_profile is None for b in metric(r, "move_type").buckets)            # not compared
    assert "does not mean you lack" in " ".join(r.limitations)


def test_a_non_synthetic_row_is_an_error_not_data():
    rows = cohort()
    rows["profiles"].append({"profile_id": "REF-001", "dataset_class": "consented_real_reference"})
    with pytest.raises(InsightsIntegrityError):
        build_insights(rows, LABELS, SETTINGS, "PM-PM", "Product Manager", YourProfile())


def test_rows_for_profiles_outside_the_cohort_are_ignored():
    rows = cohort()
    rows["education"] += [{"profile_id": "PUB-001", "qualification_level": "doctorate", "field_of_study": "Physics"}] * 9
    r = build_insights(rows, LABELS, SETTINGS, "PM-PM", "Product Manager", YourProfile())
    assert "Doctorate" not in [b.label for b in metric(r, "qualification_level").buckets]


def test_missing_thresholds_fail_closed():
    with pytest.raises(InsightsIntegrityError):
        build_insights(cohort(), LABELS, {"min_profiles_limited": 5}, "PM-PM", "Product Manager", YourProfile())


def test_language_is_illustrative_never_predictive():
    r = build_insights(cohort(), LABELS, SETTINGS, "PM-PM", "Product Manager", YourProfile())
    assert r.disclosure == "Illustrative insights based on synthetic demo profiles. These are not real-world workforce statistics."
    text = r.model_dump_json().lower()
    for banned in ("probab", "chance", "likel", "will succeed", "guarantee", "predicts", "readiness", "salary", "percentile"):
        assert banned not in text, banned


def test_display_casing():
    assert display("product roadmap") == "Product roadmap" and display("apis") == "APIs" and display("sql") == "SQL"
    assert display("Already Cased") == "Already Cased"


def test_complementary_suppression_hides_a_derivable_small_remainder():
    rows = cohort(target_holders=16)
    rows["paths"] = [{"profile_id": f"SYN-{i:03d}", "segment_no": 1,
                      "role_path": ["PM-APM", "PM-PM"] if i <= 12 else ["PM-PM"]} for i in range(1, 17)]
    prior = metric(build_insights(rows, LABELS, SETTINGS, "PM-PM", "Product Manager", YourProfile()), "prior_roles")
    # 12 "1 earlier role" and 4 "started in this role": showing 12 of 16 would reveal the hidden 4 by subtraction.
    assert (prior.status, prior.buckets) == ("all_categories_small", [])
    assert prior.other_count == prior.denominator == 16                         # the combined total reveals nothing new
    assert prior.small_categories == 2


def test_nested_headlines_with_a_small_difference_are_suppressed_as_a_pair():
    rows = cohort(mba=6)
    rows["education"].append({"profile_id": "SYN-007", "qualification_level": "master", "field_of_study": "Statistics"})
    r = build_insights(rows, LABELS, SETTINGS, "PM-PM", "Product Manager", YourProfile())
    assert headline(r, "mba").status == "shown" and headline(r, "mba").count == 6
    pg = headline(r, "postgraduate")
    assert (pg.status, pg.count, pg.share_pct) == ("suppressed", None, None)        # 7 - 6 = 1 would be a small cell
