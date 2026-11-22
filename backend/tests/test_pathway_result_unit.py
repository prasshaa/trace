"""Unit tests for the M6 personalised pathway result (pure; no database)."""
import json

import pytest

from app.services.candidate_profile.models import CurrentPosition
from app.services.candidate_profile.pathway import (
    CandidateEvidenceSummary, RouteRole, SupportingEvidence, build_pathway_result, common_areas, fit_for_route, rank_key,
    route_step_gaps,
)
from app.services.reference_evidence.models import (
    AreaExperience, DestinationRole, ObservationProvenance, ObservedPathway, ReferenceEvidenceResult, RouteCohortSummary,
    RouteStep, RouteStepExperience,
)

TITLES = {"PM-APM": "Associate Product Manager", "PM-PM": "Product Manager", "PM-SPM": "Senior Product Manager",
          "ROLE-BA": "Business Analyst", "PM-LEAD": "Lead Product Manager"}
LABELS = {"outcome_ownership": "Outcome ownership", "roadmap_ownership": "Roadmap ownership",
          "prioritisation_backlog": "Prioritisation and backlog", "product_discovery": "Product and customer discovery",
          "data_analysis": "Data analysis"}
AREA_INFO = {k: (v, k != "data_analysis") for k, v in LABELS.items()}
KNOWN = CurrentPosition(status="known", explanation="ok", role_id="PM-APM", canonical_title="Associate Product Manager",
                        role_family="product_management", seniority_code="associate", mapping_basis="alias_match")
TARGET = RouteRole(role_id="PM-SPM", canonical_title="Senior Product Manager")
BANNED = ("probab", "chance", "likel", "success", "guarantee", "forecasted", "will get", "predict")


def area(code, n, obs):
    return AreaExperience(responsibility_area=code, label=LABELS[code], is_pm_ownership_signal=AREA_INFO[code][1],
                          profile_count=n, is_common=2 * n > obs)


def exp(route, state, obs=None, areas=(), withheld=False):
    return RouteStepExperience(data_mode="synthetic_test", route=list(route), pre_destination_steps=list(route[:-1]), state=state,
                               observation_count=obs, areas_withheld=withheld, areas=list(areas))


def summary(route, supporting=3):
    return RouteCohortSummary(data_mode="synthetic_test", route=list(route), supporting_profile_count=supporting, dated_profile_count=supporting,
                              count_display_allowed=supporting >= 5, evidence_class="synthetic_test_only", p25_months=None,
                              median_months=None, p75_months=None, coarse_min_months=12, coarse_max_months=40,
                              timeline_metric="route_start_to_target_start_months", timeline_display_allowed=False)


def observation(route, months=37):
    steps = [RouteStep(role_id=r, role_family="product_management", entry_change_type=None if i == 0 else "role_change",
                       start_lo=24000 + i, start_hi=24000 + i, start_lo_label="2000-01", start_hi_label="2000-01", start_is_month_precise=True)
             for i, r in enumerate(route)]
    return ObservedPathway(route=list(route), steps=steps, step_change_types=["role_change"] * (len(route) - 1), route_months_min=months,
                           route_months_max=months, is_precise=True,
                           provenance=ObservationProvenance(profile_id="SYN-SECRET-9", dataset_class="synthetic_test_fixture", segment_no=1))


def evidence(*routes):
    """routes: (route, RouteStepExperience, supporting_profile_count)."""
    return ReferenceEvidenceResult(
        data_mode="synthetic_test", is_synthetic_test_data=True, evidence_notice="SYNTHETIC TEST DATA. Not real people.",
        destination=DestinationRole(role_id="PM-SPM", canonical_title="Senior Product Manager", role_family="product_management", is_pm_target=True),
        origin=DestinationRole(role_id="PM-APM", canonical_title="Associate Product Manager", role_family="product_management", is_pm_target=True),
        has_observed_pathways=bool(routes), observations=[observation(r) for r, _, _ in routes],
        route_summaries=[summary(r, n) for r, _, n in routes], route_step_experience=[e for _, e, _ in routes if e is not None],
        limitations=["Destination company cannot be filtered: x.", "Each individual contributes at most one observation."])


def candidate(*areas, excluded=None):
    sup = [SupportingEvidence(responsibility_area=a, employment_title="APM", employment_role_id="PM-APM", evidence_type="achievement",
                              evidence_text=f"Did {a}") for a in areas]
    return CandidateEvidenceSummary(classified_areas=sorted(set(areas)), supporting_evidence=sup, excluded=excluded or {})


def build(ev, cand, held=frozenset({"PM-APM"}), position=KNOWN):
    return build_pathway_result(position, TARGET, cand, set(held), ev, TITLES, AREA_INFO)


R2, R3 = ("PM-APM", "PM-SPM"), ("PM-APM", "PM-PM", "PM-SPM")
DISCLOSED = exp(R2, "areas_disclosed", 6, [area("outcome_ownership", 5, 6), area("roadmap_ownership", 6, 6), area("data_analysis", 5, 6)])


# --------------------------------------------------------------------------- common areas
def test_common_needs_disclosure_threshold_and_a_strict_majority():
    assert common_areas(DISCLOSED) == ["data_analysis", "outcome_ownership", "roadmap_ownership"]
    ten = exp(R2, "areas_disclosed", 10, [area("outcome_ownership", 5, 10), area("roadmap_ownership", 6, 10)])
    assert common_areas(ten) == ["roadmap_ownership"]                    # 5 of 10 is not more than half
    for state in ("suppressed_small_cohort", "areas_withheld", "no_recorded_areas"):
        assert common_areas(exp(R2, state, None if state == "suppressed_small_cohort" else 7, withheld=state == "areas_withheld")) == []
    assert common_areas(None) == []


def test_an_inconsistent_common_flag_is_refused():
    bad = exp(R2, "areas_disclosed", 10, [AreaExperience(responsibility_area="roadmap_ownership", label="x", is_pm_ownership_signal=True,
                                                         profile_count=5, is_common=True)])
    with pytest.raises(ValueError):
        common_areas(bad)


# --------------------------------------------------------------------------- fit tiers
@pytest.mark.parametrize("areas, tier, matched", [
    (("outcome_ownership", "roadmap_ownership", "data_analysis"), "aligned", ["data_analysis", "outcome_ownership", "roadmap_ownership"]),
    (("outcome_ownership", "product_discovery"), "partial", ["outcome_ownership"]),
    (("product_discovery",), "limited", []),
    ((), "not_established", []),
])
def test_fit_tiers(areas, tier, matched):
    f = fit_for_route(common_areas(DISCLOSED), DISCLOSED, candidate(*areas))
    assert (f.tier, f.matched_areas) == (tier, matched)
    assert {s.responsibility_area for s in f.supporting_evidence} == set(matched)
    if not areas:
        assert f.reason == "no_eligible_classified_evidence"


def test_an_empty_common_set_is_never_aligned():
    for state, reason in (("suppressed_small_cohort", "reference_evidence_suppressed_small_cohort"), ("areas_withheld", "reference_areas_withheld"),
                          ("no_recorded_areas", "reference_no_recorded_areas")):
        e = exp(R2, state, None if state == "suppressed_small_cohort" else 8, withheld=state == "areas_withheld")
        f = fit_for_route([], e, candidate("outcome_ownership", "roadmap_ownership"))
        assert (f.tier, f.reason) == ("not_established", reason)
    no_common = exp(R2, "areas_disclosed", 12, [area("outcome_ownership", 5, 12)])
    assert fit_for_route(common_areas(no_common), no_common, candidate("outcome_ownership")).reason == "no_common_area_on_route"


# --------------------------------------------------------------------------- gaps and ranking
def test_route_step_gaps_are_intermediate_roles_not_held():
    assert route_step_gaps(("PM-APM", "PM-PM", "PM-LEAD", "PM-SPM"), {"PM-APM", "PM-LEAD"}) == ["PM-PM"]
    assert route_step_gaps(("PM-APM", "PM-SPM"), set()) == []                       # ends are never gaps
    assert route_step_gaps(("PM-APM", "PM-PM", "PM-PM", "PM-SPM"), set()) == ["PM-PM"]


def test_ranking_uses_fit_then_step_gaps_then_route_id_never_frequency():
    weak = ("PM-APM", "ROLE-BA", "PM-SPM")
    ev = evidence((R2, exp(R2, "suppressed_small_cohort"), 2),                                 # rare, no fit established
                  (R3, exp(R3, "areas_disclosed", 9, [area("roadmap_ownership", 9, 9)]), 40),   # partial? (candidate has roadmap) -> aligned
                  (weak, exp(weak, "areas_disclosed", 30, [area("outcome_ownership", 30, 30)]), 500))  # most frequent, limited
    r = build(ev, candidate("roadmap_ownership"))
    assert [(x.rank, x.route_id, x.fit.tier) for x in r.routes] == [
        (1, "PM-APM>PM-PM>PM-SPM", "aligned"), (2, "PM-APM>ROLE-BA>PM-SPM", "limited"), (3, "PM-APM>PM-SPM", "not_established")]
    assert rank_key("partial", [], "Z") < rank_key("limited", [], "A") and rank_key("partial", [], "Z") < rank_key("partial", ["x"], "A")


def test_same_tier_prefers_fewer_missing_steps_then_route_id():
    a, b = ("PM-APM", "PM-PM", "PM-SPM"), ("PM-APM", "ROLE-BA", "PM-SPM")
    ev = evidence((a, exp(a, "suppressed_small_cohort"), 9), (b, exp(b, "suppressed_small_cohort"), 1), (R2, exp(R2, "suppressed_small_cohort"), 1))
    r = build(ev, candidate(), held={"PM-APM", "ROLE-BA"})
    assert [x.route_id for x in r.routes] == ["PM-APM>PM-SPM", "PM-APM>ROLE-BA>PM-SPM", "PM-APM>PM-PM>PM-SPM"]
    assert [g.role_id for g in r.routes[2].step_gaps] == ["PM-PM"] and r.routes[1].step_gaps == []


# --------------------------------------------------------------------------- assembled result
def test_result_sections_gaps_and_guidance():
    r = build(evidence((R2, DISCLOSED, 6)), candidate("outcome_ownership", "product_discovery"))
    (route,) = r.routes
    assert route.fit.tier == "partial" and [g.responsibility_area for g in route.area_gaps] == ["data_analysis", "roadmap_ownership"]
    assert all(g.note == "Not recorded in your reviewed profile." for g in route.area_gaps)
    assert route.observed.experience_state == "areas_disclosed" and route.observed.observation_count == 6
    assert [a.responsibility_area for a in route.observed.disclosed_areas] == ["data_analysis", "outcome_ownership", "roadmap_ownership"]
    assert all(s.text.startswith(("If you have", "Observed routes", "Classify", "Some ")) and s.label.startswith("Generated guidance")
               for s in route.next_steps + r.next_steps)
    assert any("measurable outcome" in s.text or "roadmap" in s.text for s in route.next_steps)
    assert r.role_only_notice == "Role-only assessment: company-specific fit is not assessed."
    assert r.is_synthetic_test_data and r.evidence_notice.startswith("SYNTHETIC TEST DATA")
    assert any("Company-specific fit" in u for u in r.unavailable_assessments)
    assert not any("Destination company" in l for l in r.limitations)


def test_all_four_disclosure_states_survive_into_the_result():
    states = {("PM-APM", "PM-SPM"): exp(R2, "suppressed_small_cohort"),
              ("PM-APM", "PM-PM", "PM-SPM"): exp(R3, "no_recorded_areas", 7),
              ("PM-APM", "ROLE-BA", "PM-SPM"): exp(("PM-APM", "ROLE-BA", "PM-SPM"), "areas_withheld", 8, withheld=True),
              ("PM-APM", "PM-LEAD", "PM-SPM"): exp(("PM-APM", "PM-LEAD", "PM-SPM"), "areas_disclosed", 5, [area("outcome_ownership", 5, 5)])}
    r = build(evidence(*[(k, v, 1) for k, v in states.items()]), candidate("outcome_ownership"))
    got = {x.route_id: (x.observed.experience_state, x.observed.observation_count, x.observed.areas_withheld, x.fit.reason) for x in r.routes}
    assert got == {"PM-APM>PM-SPM": ("suppressed_small_cohort", None, False, "reference_evidence_suppressed_small_cohort"),
                   "PM-APM>PM-PM>PM-SPM": ("no_recorded_areas", 7, False, "reference_no_recorded_areas"),
                   "PM-APM>ROLE-BA>PM-SPM": ("areas_withheld", 8, True, "reference_areas_withheld"),
                   "PM-APM>PM-LEAD>PM-SPM": ("areas_disclosed", 5, False, "all_common_areas_recorded")}
    assert any("too few reference profiles" in l for l in r.limitations)


def test_no_raw_m5_data_identifiers_or_banned_wording_leaves_the_result():
    r = build(evidence((R2, DISCLOSED, 3), (R3, exp(R3, "suppressed_small_cohort"), 4)), candidate("outcome_ownership"))
    dumped = r.model_dump_json()
    for leak in ("SYN-SECRET-9", '"profile_id"', '"supporting_profile_count"', '"route_months_min"', '"route_months_max"',
                 '"dated_profile_count"', '"coarse_min_months"', '"p25_months"', '"median_months"', '"timeline_metric"',
                 '"timeline_display_allowed"', '"segment_no"', '"start_lo"', '"observations"'):
        assert leak not in dumped, leak
    texts = json.dumps([r.interpretation_note, r.role_only_notice, r.limitations, [s.text for s in r.next_steps],
                        [s.text for x in r.routes for s in x.next_steps], [g.note for x in r.routes for g in x.area_gaps + x.step_gaps]]).lower()
    for word in BANNED:
        assert word not in texts, word
    for x in r.routes:
        assert x.observed.observation_count is None or x.observed.observation_count >= 5
        assert all(a.profile_count >= 5 for a in x.observed.disclosed_areas)


def test_unknown_position_and_no_routes():
    unknown = CurrentPosition(status="unknown", reason="pending_review", explanation="x")
    r = build(None, candidate(), position=unknown)
    assert (r.routes, r.data_mode) == ([], None)
    assert "Review your most recent employment" in r.next_steps[0].text and "unknown (pending_review)" in r.limitations[0]
    empty = build(evidence(), candidate("outcome_ownership"))
    assert empty.routes == [] and any("absence of observations" in l for l in empty.limitations)


def test_only_disclosed_routes_can_have_common_areas_even_if_areas_were_present():
    for state in ("areas_withheld", "no_recorded_areas", "suppressed_small_cohort"):
        odd = exp(R2, state, 9, [area("roadmap_ownership", 9, 9)])
        assert common_areas(odd) == [], state


def test_every_guidance_template_is_conditional():
    from app.services.candidate_profile.pathway import AREA_GUIDANCE, area_guidance, step_guidance
    texts = [area_guidance(a, LABELS[a]) for a in AREA_GUIDANCE] + [area_guidance("data_analysis", "Data analysis"), step_guidance("Product Manager")]
    assert len(texts) == len(AREA_GUIDANCE) + 2
    for t in texts:
        assert ("If you have" in t) and ("Otherwise" in t or "consider" in t), t
        for word in BANNED + ("you lack", "you need", "must", "required"):
            assert word not in t.lower(), (word, t)
