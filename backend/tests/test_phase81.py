"""Phase 8.1: route-level privacy, own-contribution exclusion, fit limitations, ties and role_unconfirmed guidance.

Unit tests use the foundation fake of tests/test_reference_evidence.py; integration tests use the real API and the
local database (synthetic transient users; real-resume tests use invented resumes, as in Phase 6.1).
"""
import json

import pytest

from app import db
from app.config import Settings
from app.services.candidate_profile import CandidateProfileService
from app.services.candidate_profile.models import CurrentPosition
from app.services.candidate_profile.pathway import (
    POSITION_GUIDANCE, CandidateEvidenceSummary, RouteRole, build_pathway_result, fit_for_route,
)
from app.services.contributions import process_pending
from app.services.reference_evidence import recommendation_evidence
from app.services.reference_evidence.models import AreaExperience, RouteStepExperience
from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import PDF, candidate, consent_document, demo_client, upload  # noqa: F401  (fixtures)
from tests.test_api_phase4 import TARGETS, reviewed_candidate
from tests.test_contribution_safety import correct_first_evidence
from tests.test_real_resume_contributions import (  # noqa: F401  (fixtures and helpers)
    confirm_all, contribute, contribution_document, contribution_of, rc, ready_user,
)
from tests.test_reference_evidence import SYN, FakeRepo, month, path

MODE = "synthetic_test"


def career(pid, roles):
    steps = [(r, None if i == 0 else "role_and_employer_change") for i, r in enumerate(roles)]
    return path(pid, SYN, steps, [month(2015 + 2 * i, 1) for i in range(len(roles))])


def pool(spec):
    """{route tuple: number of profiles} -> paths (one profile per career)."""
    out = []
    for route, n in spec.items():
        for i in range(n):
            out.append(career(f"SYN-{route[1][-3:]}{len(route)}{i:02d}", list(route)))
    return out


# --------------------------------------------------------------------------- 1. route-level privacy (unit)
@pytest.mark.parametrize("n,shown", [(1, False), (2, False), (3, False), (4, False), (5, True), (6, True)])
def test_a_route_is_shown_only_from_the_privacy_minimum(n, shown):
    repo = FakeRepo(pool({("ROLE-QA", "PM-PM"): n}), threshold=5.0)
    ev = recommendation_evidence(repo, "PM-PM", MODE, "ROLE-QA")
    assert bool(ev.route_summaries) is shown and bool(ev.observations) is shown and ev.has_observed_pathways is shown
    if not shown:                                       # nothing about it survives: sequence, count, experience
        assert (ev.observations, ev.route_summaries, ev.route_step_experience) == ([], [], [])


def test_a_distinctive_single_profile_route_leaves_no_trace_but_others_survive():
    repo = FakeRepo(pool({("PM-PM", "ROLE-QA", "PM-SPM"): 1, ("PM-PM", "PM-SPM"): 5}), threshold=5.0)
    ev = recommendation_evidence(repo, "PM-SPM", MODE, "PM-PM")
    assert [s.route for s in ev.route_summaries] == [["PM-PM", "PM-SPM"]]
    assert "ROLE-QA" not in ev.model_dump_json()


def test_no_threshold_setting_shows_nothing():
    assert recommendation_evidence(FakeRepo(pool({("ROLE-QA", "PM-PM"): 9}), threshold=None), "PM-PM", MODE, "ROLE-QA").route_summaries == []


def test_repeated_observations_of_one_profile_count_once():
    twice = [career("SYN-X01", ["ROLE-QA", "PM-PM"]),
             path("SYN-X01", SYN, [("ROLE-QA", None), ("PM-PM", "role_change")], [month(2022, 1), month(2023, 1)], segment_no=2)]
    repo = FakeRepo(pool({("ROLE-QA", "PM-PM"): 4}) + twice, threshold=5.0)
    ev = recommendation_evidence(repo, "PM-PM", MODE, "ROLE-QA")
    assert len({o.provenance.profile_id for o in ev.observations}) == 5 and len(ev.observations) == 5


# --------------------------------------------------------------------------- 2. own contribution (unit)
def test_the_callers_own_profile_is_removed_before_counting():
    paths = pool({("ROLE-QA", "PM-PM"): 5})
    own = paths[0].profile_id
    assert recommendation_evidence(FakeRepo(paths, threshold=5.0), "PM-PM", MODE, "ROLE-QA").route_summaries   # 5: shown
    ev = recommendation_evidence(FakeRepo(paths, threshold=5.0), "PM-PM", MODE, "ROLE-QA", [own])
    assert ev.route_summaries == [] and own not in ev.model_dump_json()                          # 4 without the caller


def test_a_route_that_stays_eligible_is_recounted_without_the_caller():
    paths = pool({("ROLE-QA", "PM-PM"): 6})
    own = paths[0].profile_id
    ev = recommendation_evidence(FakeRepo(paths, threshold=5.0), "PM-PM", MODE, "ROLE-QA", [own])
    (s,) = ev.route_summaries
    assert s.supporting_profile_count == 5 and s.p25_months is None and not s.timeline_display_allowed
    assert {o.provenance.profile_id for o in ev.observations} == {p.profile_id for p in paths[1:]}
    assert ev.route_step_experience[0].observation_count == 5


# --------------------------------------------------------------------------- 3. fit limitations (unit)
CLASSIFIED = CandidateEvidenceSummary(classified_areas=["roadmap_ownership"], supporting_evidence=[], excluded={})
NOTHING = CandidateEvidenceSummary(classified_areas=[], supporting_evidence=[], excluded={})


def exp(state, n=None, areas=()):
    return RouteStepExperience(data_mode=MODE, route=["A", "B"], pre_destination_steps=["A"], state=state, observation_count=n,
                               areas=[AreaExperience(responsibility_area=a, label=a, is_pm_ownership_signal=True, profile_count=c,
                                                     is_common=2 * c > n) for a, c in areas], areas_withheld=state == "areas_withheld")


@pytest.mark.parametrize("experience,candidate,limitations", [
    (exp("areas_disclosed", 6, [("roadmap_ownership", 6)]), NOTHING, ["incomplete_user_profile"]),
    (exp("areas_withheld", 6), CLASSIFIED, ["insufficient_reference_evidence"]),
    (exp("no_recorded_areas", 6), CLASSIFIED, ["insufficient_reference_evidence"]),
    (exp("areas_disclosed", 10, [("roadmap_ownership", 5)]), CLASSIFIED, ["insufficient_reference_evidence"]),
    (exp("suppressed_small_cohort"), CLASSIFIED, ["insufficient_route_evidence"]),
    (exp("areas_withheld", 6), NOTHING, ["insufficient_reference_evidence", "incomplete_user_profile"]),
    (exp("areas_disclosed", 6, [("roadmap_ownership", 6)]), CLASSIFIED, []),
])
def test_fit_says_whose_information_is_missing(experience, candidate, limitations):
    from app.services.candidate_profile.pathway import common_areas
    fit = fit_for_route(common_areas(experience, 5), experience, candidate)
    assert fit.limitations == limitations
    assert (fit.tier == "not_established") is bool(limitations)


# --------------------------------------------------------------------------- 5. ties (unit)
def test_equal_inputs_share_a_rank_group_and_the_order_is_only_stable():
    repo = FakeRepo(pool({("ROLE-QA", "ROLE-BA", "PM-PM"): 5, ("ROLE-QA", "PM-APM", "PM-PM"): 5}), threshold=5.0)
    ev = recommendation_evidence(repo, "PM-PM", MODE, "ROLE-QA")
    pos = CurrentPosition(status="known", explanation="x", role_id="ROLE-QA", canonical_title="QA", role_family="qa", mapping_basis="candidate_selected")
    titles = {r: r for r in ("ROLE-QA", "ROLE-BA", "PM-APM", "PM-PM")}
    tied = build_pathway_result(pos, RouteRole(role_id="PM-PM", canonical_title="PM"), NOTHING, {"ROLE-QA"}, ev, titles, {}, 5)
    assert [(r.rank, r.rank_group, r.tied) for r in tied.routes] == [(1, 1, True), (2, 1, True)]
    split = build_pathway_result(pos, RouteRole(role_id="PM-PM", canonical_title="PM"), NOTHING, {"ROLE-QA", "ROLE-BA"}, ev, titles, {}, 5)
    assert [(r.route_id, r.rank_group, r.tied) for r in split.routes] == [("ROLE-QA>ROLE-BA>PM-PM", 1, False),
                                                                        ("ROLE-QA>PM-APM>PM-PM", 2, False)]


# --------------------------------------------------------------------------- 4. guidance
def test_every_position_reason_has_specific_guidance():
    from app.services.candidate_profile.models import CurrentPositionReason
    reasons = set(CurrentPositionReason.__args__)
    assert reasons <= set(POSITION_GUIDANCE), reasons - set(POSITION_GUIDANCE)
    assert POSITION_GUIDANCE["role_unconfirmed"].startswith("Your current job is confirmed, but the standard role matched")


def test_role_unconfirmed_guidance_reaches_the_api(demo_client, users):
    from tests.test_phase721 import auto_mapped
    u, h, job, role = auto_mapped(demo_client, users)
    demo_client.post(f"/api/v1/records/employment/{job['id']}/confirm", headers=h)
    body = demo_client.get("/api/v1/pathway", headers=h, params={"target": "PM-SPM"}).json()
    assert body["current_position"]["reason"] == "role_unconfirmed"
    assert any("standard role matched to it hasn't been confirmed yet" in s["text"] for s in body["next_steps"])


# --------------------------------------------------------------------------- 1. integration: no surface leaks a suppressed route
def test_the_single_profile_founder_route_leaks_through_no_endpoint(demo_client, users, base_settings):
    u, h = reviewed_candidate(demo_client, users, "SYN-001", classify=False)          # current role Product Manager
    body = demo_client.get("/api/v1/pathway", headers=h, params={"target": "PM-LEAD"})
    assert body.status_code == 200 and body.json()["routes"] == []
    assert "ROLE-ENT" not in body.text and "Founder" not in body.text
    assert any("not an assessment of the route" in l for l in body.json()["limitations"])
    for target in TARGETS:                                                           # career insights, every target
        text = demo_client.get(f"/api/v1/insights?target={target}", headers=h).text
        assert "Founder" not in text, target
    from app.services.candidate_profile import OwnerContext, load_sequencing_context
    with db.connect(base_settings) as conn:
        profile = conn.execute("select candidate_profile_id from public.trace_candidate_profiles where owner_user_id = %s", (u,)).fetchone()[0]
        conn.rollback()
        ev = CandidateProfileService(conn).route_evidence(OwnerContext(owner_user_id=u, candidate_profile_id=profile),
                                                          load_sequencing_context(conn), "PM-LEAD").evidence
    assert ev.observations == [] and ev.route_summaries == [] and "ROLE-ENT" not in ev.model_dump_json()


def test_every_shown_synthetic_route_meets_the_minimum(demo_client, users):
    u, h = reviewed_candidate(demo_client, users, "SYN-001", classify=False)
    for target in TARGETS:
        r = demo_client.get("/api/v1/pathway", headers=h, params={"target": target})
        if r.status_code == 200:
            assert all(x["observed"]["observation_count"] is not None and x["observed"]["observation_count"] >= 5
                       for x in r.json()["routes"]), target


# --------------------------------------------------------------------------- 2. integration: own contribution
def own_ids(base_settings, u):
    with db.connect(base_settings) as conn:
        return CandidateProfileService(conn).own_reference_profile_ids(u)


def real_insights(c, h):
    return c.get("/api/v1/insights?target=PM-PM&cohort=consented_real", headers=h).json()


def test_own_contribution_is_excluded_for_its_contributor_only(rc, users, base_settings):
    outsider, ho = candidate(rc, users)
    assert own_ids(base_settings, outsider) == []                                          # 1. no contribution
    contributors = []
    for _ in range(5):
        u, h = ready_user(rc, users, contributor=True)                             # opted in before uploading (D48)
        confirm_all(rc, h)
        contributors.append((u, h))
    process_pending(Settings())
    pids = [contribution_of(base_settings, u)[1] for u, _ in contributors]
    assert all(pids) and len(set(pids)) == 5

    (u1, h1), (u2, h2), (u3, h3) = contributors[:3]
    assert own_ids(base_settings, u1) == [pids[0]]                                         # 2. included contribution
    seen_by_outsider = real_insights(rc, ho)
    assert (seen_by_outsider["status"], seen_by_outsider["eligible_profiles"]) == ("ready", 5)   # 7. independent for others
    mine = real_insights(rc, h1)
    assert mine["status"] == "insufficient" and mine["eligible_profiles"] is None           # 5. below the minimum without self
    assert pids[0] not in json.dumps(mine) and pids[0] not in json.dumps(seen_by_outsider)

    correct_first_evidence(rc, h2, "Meera Kulkarni ran the quarterly cards roadmap review")    # 3. held for manual review
    process_pending(Settings())
    assert contribution_of(base_settings, u2) is None and own_ids(base_settings, u2) == []
    contribute(rc, h3, "withdrawn")                                                          # 4. withdrawn
    assert own_ids(base_settings, u3) == []
    assert real_insights(rc, ho)["status"] == "insufficient"                                 # 3 left: nobody sees a pattern
