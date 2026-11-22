"""Personalised pathway result (M6) against the local database: synthetic test data, transient users, read-only."""
import json
import re

import pytest

from app.services.candidate_profile import (
    ActivityCreate, EmploymentCreate, EvidenceCorrection, EvidenceCreate, InvalidReference,
)
from tests.test_candidate_profile_integration import ONGOING, context, env, month, settings  # noqa: F401  (fixtures)
from tests.test_current_position_integration import pathway_rows

pytestmark = pytest.mark.integration
BANNED = ("probab", "chance", "likel", "success", "guarantee", "predict")
LEAKS = ("SYN-", '"profile_id"', '"supporting_profile_count"', '"route_months_min"', '"route_months_max"', '"dated_profile_count"',
         '"coarse_min_months"', '"p25_months"', '"median_months"', '"timeline_metric"', '"timeline_display_allowed"', '"segment_no"',
         '"observations"', '"candidate_evidence_id"')
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def job(svc, a, seq, title, *, confirm=True, end=ONGOING):
    e = svc.add_employment(a, EmploymentCreate(sequence_index=seq, title_raw=title, employment_type="full_time",
                                               start=month(2017 + 2 * seq, 1), end=end))
    m = svc.map_employment(a, e)
    if confirm and m.result == "mapped_automatically":   # Phase 7.2.1: the user confirms the displayed match
        svc.choose_role(a, e, m.role_id)
    if confirm:
        svc.confirm_record(a, "employment", e)
    return e


def ev(svc, a, text, *, job_id=None, areas=None, confirm=True, legacy_area=None, activity=None):
    e = svc.add_evidence(a, EvidenceCreate(evidence_type="achievement", evidence_text=text, candidate_employment_id=job_id,
                                           candidate_activity_id=activity, responsibility_area=legacy_area))
    if areas:
        svc.classify_evidence(a, e, areas)
    if confirm:
        svc.confirm_record(a, "evidence", e)
    return e


def assert_client_safe(r):
    dumped = r.model_dump_json()
    assert not UUID.search(dumped), UUID.search(dumped).group(0)              # no internal database identifiers
    for leak in LEAKS:
        assert leak not in dumped, leak
    texts = json.dumps([r.interpretation_note, r.limitations, [s.text for s in r.next_steps],
                        [s.text for x in r.routes for s in x.next_steps]]).lower()
    for word in BANNED:
        assert word not in texts, word
    for x in r.routes:
        assert x.observed.is_synthetic_test_data and x.observed.evidence_class == "synthetic_test_only"
        assert x.observed.observation_count is None or x.observed.observation_count >= 5
        assert all(a.profile_count >= 5 for a in x.observed.disclosed_areas)


def test_only_reviewed_classified_current_evidence_on_eligible_employment_counts(env, context):
    svc, a, _, admin, _ = env
    old = job(svc, a, 1, "Business Analyst", confirm=False, end=month(2019, 12))       # pending: not eligible
    apm = job(svc, a, 2, "Associate Product Manager")
    ev(svc, a, "Grew activation 8%", job_id=apm, areas=["outcome_ownership", "roadmap_ownership"])
    ev(svc, a, "Owned the onboarding roadmap", job_id=apm, areas=["roadmap_ownership"])                  # duplicate area
    ev(svc, a, "Ran discovery interviews", job_id=apm, areas=["product_discovery"], confirm=False)        # pending
    rejected = ev(svc, a, "Rejected claim", job_id=apm, areas=["prioritisation_backlog"])
    svc.reject_record(a, "evidence", rejected)                                                            # rejected
    stale = ev(svc, a, "Prioritised the backlog", job_id=apm, areas=["prioritisation_backlog"])
    svc.correct_record(a, "evidence", stale, EvidenceCorrection(evidence_text="Prioritised the payments backlog"))   # stale
    ev(svc, a, "Analysed churn data", job_id=apm, legacy_area="data_analysis")                           # legacy field only
    ev(svc, a, "Owned activation metrics", job_id=apm, legacy_area="technical_engineering", areas=["outcome_ownership"])  # legacy ignored
    ev(svc, a, "Led requirements", job_id=old, areas=["requirements_analysis"])                           # ineligible employment
    act = svc.add_activity(a, ActivityCreate(activity_type="independent_project", title_raw="Side project", start=month(2022, 1)))
    ev(svc, a, "Shipped a side app", activity=act, areas=["outcome_ownership"])                           # activity, not employment

    before = pathway_rows(admin)
    r = svc.pathway_result(a, context, "PM-PM")
    c = r.candidate_evidence
    assert c.classified_areas == ["outcome_ownership", "roadmap_ownership"]
    assert sorted((s.responsibility_area, s.evidence_text) for s in c.supporting_evidence) == [
        ("outcome_ownership", "Grew activation 8%"), ("outcome_ownership", "Owned activation metrics"),
        ("roadmap_ownership", "Grew activation 8%"), ("roadmap_ownership", "Owned the onboarding roadmap")]
    assert all(s.employment_title == "Associate Product Manager" and s.employment_role_id == "PM-APM" for s in c.supporting_evidence)
    assert c.excluded == {"classification_stale": 1, "employment_not_eligible": 1, "not_attached_to_an_employment": 1, "not_classified": 1,
                          "pending_review": 1, "rejected": 1}
    assert not {"data_analysis", "requirements_analysis", "technical_engineering"} & set(c.classified_areas)
    assert {s.kind for s in r.next_steps} == {"evidence"}
    assert pathway_rows(admin) == before                                         # read-only
    assert_client_safe(r)


def test_routes_match_m5_evidence_and_disclosure_is_preserved(env, context):
    svc, a, *_ = env
    apm = job(svc, a, 1, "Associate Product Manager")
    ev(svc, a, "Grew activation 8%", job_id=apm, areas=["outcome_ownership"])
    for target in ("PM-PM", "PM-SPM"):
        r = svc.pathway_result(a, context, target)
        m5 = svc.route_evidence(a, context, target).evidence
        assert sorted(x.route_id for x in r.routes) == sorted(">".join(s.route) for s in m5.route_summaries)
        assert [x.rank for x in r.routes] == list(range(1, len(r.routes) + 1))
        exp = {">".join(x.route): x for x in m5.route_step_experience}
        for x in r.routes:
            e = exp[x.route_id]
            assert (x.observed.experience_state, x.observed.observation_count, x.observed.areas_withheld) == (e.state, e.observation_count, e.areas_withheld)
            assert x.route[0].role_id == "PM-APM" and x.route[-1].role_id == target
        assert_client_safe(r)
    pm = svc.pathway_result(a, context, "PM-PM")
    (only,) = pm.routes
    assert (only.observed.experience_state, only.observed.observation_count) == ("areas_disclosed", 13)
    assert (only.fit.tier, only.fit.reason) == ("not_established", "no_common_area_on_route")    # 5 of 13 is not common
    spm = svc.pathway_result(a, context, "PM-SPM")
    assert all(x.fit.tier == "not_established" for x in spm.routes)
    gaps = {x.route_id: [g.role_id for g in x.step_gaps] for x in spm.routes}
    assert all(set(g) <= {"PM-PM"} for g in gaps.values())
    assert [x.route_id for x in spm.routes] == sorted(gaps, key=lambda k: (len(gaps[k]), k))      # fewer missing steps first


def test_unknown_position_gives_no_routes_and_guidance(env, context):
    svc, a, *_ = env
    job(svc, a, 1, "Product Owner")                                     # context-required: no automatic role
    r = svc.pathway_result(a, context, "PM-PM")
    assert (r.current_position.reason, r.routes, r.data_mode) == ("unresolved_role_mapping", [], None)
    assert r.next_steps[0].text.startswith("Choose the role of your most recent employment")
    assert r.role_only_notice and any("unknown (unresolved_role_mapping)" in l for l in r.limitations)
    assert_client_safe(r)


def test_no_observed_route_is_honest(env, context):
    svc, a, *_ = env
    job(svc, a, 1, "HR Operations Analyst")
    r = svc.pathway_result(a, context, "PM-DIR")
    assert r.routes == [] and r.is_synthetic_test_data and any("absence of observations" in l for l in r.limitations)


@pytest.mark.parametrize("target", ["ROLE-SWE", "PM-INTERN", "PM-VP", "PM-NOPE"])
def test_only_pm_targets(env, context, target):
    svc, a, *_ = env
    job(svc, a, 1, "Product Manager")
    with pytest.raises(InvalidReference):
        svc.pathway_result(a, context, target)
