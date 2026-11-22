"""Phase 7.2.1: zero-move routes are never career progressions; the starting point needs a user-confirmed role.

Unit tests use the episode fake of tests/test_reference_evidence.py (the foundation's semantics); integration tests use
the real API against the local database with synthetic transient users.
"""
import inspect
import re

import pytest

from app.services.candidate_profile import pathway as pathway_module
from app.services.candidate_profile.models import CurrentPosition
from app.services.candidate_profile.pathway import CandidateEvidenceSummary, RouteRole, build_pathway_result
from app.services.reference_evidence import is_zero_move, progression_routes_only
from app.services.reference_evidence import service as evidence_service
from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import PDF, candidate, consent_document, demo_client, upload  # noqa: F401  (fixtures)
from tests.test_api_phase4 import reviewed_candidate
from tests.test_reference_evidence import REAL, SYN, FakeRepo, month, mover, path, run


def career(pid, steps, cls=SYN):
    return path(pid, cls, steps, [month(2015 + i, 1) for i in range(len(steps))])


KNOWN = lambda role: CurrentPosition(status="known", explanation="ok", role_id=role, canonical_title=role,  # noqa: E731
                                     role_family="product_management", mapping_basis="candidate_selected")
NO_EVIDENCE = CandidateEvidenceSummary(classified_areas=[], supporting_evidence=[], excluded={})


def build(evidence, origin, target, titles=None):
    roles = {r for s in evidence.route_summaries for r in s.route} | {origin, target}
    return build_pathway_result(KNOWN(origin), RouteRole(role_id=target, canonical_title=(titles or {}).get(target, target)),
                                NO_EVIDENCE, {origin}, evidence, titles or {r: r for r in roles}, {}, threshold=5)


# --------------------------------------------------------------------------- 1. zero-move routes
class TestZeroMove:
    def test_same_role_at_different_employers_is_kept_as_history_but_never_a_progression(self):
        raw = run(FakeRepo([mover("REF-001")], eligible={"REF-001"}))
        assert [o.route for o in raw.observations] == [["PM-PM", "PM-PM"]]          # the record itself is preserved
        prog = progression_routes_only(raw)
        assert (prog.observations, prog.route_summaries, prog.route_step_experience, prog.has_observed_pathways) == ([], [], [], False)
        assert any("same standard role" in x for x in prog.limitations)

    def test_same_employer_repeated_same_role_records_are_one_episode_and_no_route(self):
        # The foundation merges repeated same-role records at one employer into one episode (one stint).
        raw = run(FakeRepo([career("SYN-A", [("PM-PM", None)])]), mode="synthetic_test")
        assert raw.observations == [] and progression_routes_only(raw).observations == []

    def test_a_route_that_leaves_and_returns_to_the_same_role_is_zero_move(self):
        p = career("SYN-B", [("PM-PM", None), ("PM-APM", "role_change"), ("PM-PM", "role_change")])
        raw = run(FakeRepo([p]), role="PM-PM", mode="synthetic_test", origin_role_id="PM-PM")
        assert [o.route for o in raw.observations] == [["PM-PM", "PM-APM", "PM-PM"]]
        assert progression_routes_only(raw).observations == []

    def test_genuine_transitions_are_kept_including_a_same_role_employer_change_on_the_way(self):
        p1 = career("SYN-C", [("PM-APM", None), ("PM-PM", "role_change")])
        p2 = career("SYN-D", [("PM-APM", None), ("PM-APM", "employer_change"), ("PM-PM", "role_change")])
        raw = run(FakeRepo([p1, p2]), role="PM-PM", mode="synthetic_test", origin_role_id="PM-APM")
        prog = progression_routes_only(raw)
        assert sorted(o.route for o in prog.observations) == [["PM-APM", "PM-APM", "PM-PM"], ["PM-APM", "PM-PM"]]
        assert [s.route for s in prog.route_summaries] == [s.route for s in raw.route_summaries]

    def test_different_canonical_roles_with_similar_titles_are_a_transition(self):
        p = career("SYN-E", [("PM-PM", None), ("PM-SPM", "role_change")])
        prog = progression_routes_only(run(FakeRepo([p]), role="PM-SPM", mode="synthetic_test", origin_role_id="PM-PM"))
        same_titles = {"PM-PM": "Product Manager", "PM-SPM": "Product Manager"}            # display titles may collide
        result = build(prog, "PM-PM", "PM-SPM", same_titles)
        assert [r.route_id for r in result.routes] == ["PM-PM>PM-SPM"]                     # ids decide, not titles
        assert not is_zero_move(["PM-PM", "PM-SPM"]) and is_zero_move(["PM-PM", "PM-PM"])

    def test_recommendations_alternatives_and_ranking_never_include_a_zero_move_route(self):
        # Unfiltered evidence passed straight to the pathway builder (defence in depth): the zero-move route is dropped
        # before ranking, so it can be neither the recommendation nor an alternative.
        paths = [mover("SYN-F", cls=SYN),
                 career("SYN-G", [("PM-PM", None), ("ROLE-QA", "role_change"), ("PM-PM", "role_change")])]
        raw = run(FakeRepo(paths), role="PM-PM", mode="synthetic_test", origin_role_id="PM-PM")
        assert len(raw.route_summaries) == 2
        assert build(raw, "PM-PM", "PM-PM").routes == []

    def test_no_route_result_is_cached(self):
        # There is no cache of route results to purge: evidence is read per request inside one read-only snapshot.
        for module in (evidence_service, pathway_module):
            src = inspect.getsource(module)
            assert not re.search(r"lru_cache|functools\.cache|@cache\b|cachetools", src), module.__name__


# --------------------------------------------------------------------------- 1. integration: every consumer
def test_route_evidence_and_pathways_contain_no_zero_move_route(demo_client, users, base_settings):
    from app import db
    from app.services.candidate_profile import CandidateProfileService, OwnerContext, load_sequencing_context
    u, h = reviewed_candidate(demo_client, users, "SYN-001", classify=False)
    with db.connect(base_settings) as conn:
        profile = conn.execute("select candidate_profile_id from public.trace_candidate_profiles where owner_user_id = %s", (u,)).fetchone()[0]
        conn.rollback()
        svc, ctx, context = CandidateProfileService(conn), OwnerContext(owner_user_id=u, candidate_profile_id=profile), load_sequencing_context(conn)
        current = svc.current_position(ctx, context).role_id
        same = svc.route_evidence(ctx, context, current)                                 # origin = target
        assert same.evidence.observations == [] and same.evidence.route_summaries == []
        for target in ("PM-APM", "PM-SPM", "PM-LEAD", "PM-PRINCIPAL", "PM-DIR"):
            ev = svc.route_evidence(ctx, context, target).evidence
            assert all(not is_zero_move(o.route) for o in ev.observations)
            body = demo_client.get("/api/v1/pathway", headers=h, params={"target": target}).json()
            assert all(r["route"][0]["role_id"] != r["route"][-1]["role_id"] for r in body["routes"]), target


def test_insight_transitions_never_show_a_same_role_move(demo_client, users):
    u, h = reviewed_candidate(demo_client, users, "SYN-001", classify=False)
    for target in ("PM-PM", "PM-SPM"):
        body = demo_client.get(f"/api/v1/insights?target={target}", headers=h).json()
        metrics = {m["key"]: m for s in body["sections"] for m in s["metrics"]}
        for b in metrics["common_moves"]["buckets"]:
            a, _, z = b["label"].partition(" → ")
            assert a and z and a != z, b["label"]
        assert body["target"]["label"] not in {b["label"] for b in metrics["roles_before_target"]["buckets"]}


# --------------------------------------------------------------------------- 2. user-confirmed starting point
def position(c, h):
    return c.get("/api/v1/current-position", headers=h).json()


def latest(c, h):
    return max(c.get("/api/v1/profile/records", headers=h).json()["employments"], key=lambda e: e["sequence_index"])


def auto_mapped(c, users):
    """A fixture profile whose latest job has an automatic (algorithmic) role match."""
    u, h = candidate(c, users)
    assert upload(c, h, PDF["SYN-001"]).status_code == 201
    job = latest(c, h)
    mapped = c.post(f"/api/v1/employments/{job['id']}/map", headers=h).json()
    assert mapped["result"] == "mapped_automatically"
    return u, h, job, mapped["role_id"]


def test_a_high_confidence_match_shown_and_explicitly_confirmed_is_the_starting_point(demo_client, users):
    u, h, job, role = auto_mapped(demo_client, users)
    assert demo_client.post(f"/api/v1/employments/{job['id']}/role", headers=h, json={"role_id": role}).status_code == 200
    assert demo_client.post(f"/api/v1/records/employment/{job['id']}/confirm", headers=h).status_code == 200
    p = position(demo_client, h)
    assert (p["status"], p["role_id"], p["mapping_basis"]) == ("known", role, "candidate_selected")


def test_a_confirmed_job_with_an_unconfirmed_match_is_not_a_starting_point(demo_client, users):
    u, h, job, role = auto_mapped(demo_client, users)
    demo_client.post(f"/api/v1/records/employment/{job['id']}/confirm", headers=h)    # job confirmed, role not
    p = position(demo_client, h)
    assert (p["status"], p["reason"], p["role_id"]) == ("unknown", "role_unconfirmed", None)
    assert demo_client.get("/api/v1/pathway", headers=h, params={"target": "PM-SPM"}).json()["routes"] == []


def test_a_match_not_yet_seen_or_confirmed_is_not_a_starting_point(demo_client, users):
    u, h, job, role = auto_mapped(demo_client, users)
    assert position(demo_client, h)["reason"] == "pending_review"                    # neither job nor role confirmed


def test_unresolved_and_low_confidence_matches_still_need_a_choice(demo_client, users):
    u, h, job, role = auto_mapped(demo_client, users)
    demo_client.post(f"/api/v1/records/employment/{job['id']}/corrections", headers=h, json={"title_raw": "Chief Wonder Officer"})
    demo_client.post(f"/api/v1/records/employment/{job['id']}/confirm", headers=h)
    demo_client.post(f"/api/v1/employments/{job['id']}/map", headers=h)
    p = position(demo_client, h)
    assert p["status"] == "unknown" and p["reason"] in ("stale_mapping", "unresolved_role_mapping", "low_mapping_confidence")


def test_a_corrected_standard_role_is_the_starting_point(demo_client, users):
    u, h, job, role = auto_mapped(demo_client, users)
    demo_client.post(f"/api/v1/employments/{job['id']}/role", headers=h, json={"role_id": "PM-SPM"})
    demo_client.post(f"/api/v1/records/employment/{job['id']}/confirm", headers=h)
    assert (position(demo_client, h)["status"], position(demo_client, h)["role_id"]) == ("known", "PM-SPM")


def test_editing_after_confirming_needs_a_new_confirmation(demo_client, users):
    u, h, job, role = auto_mapped(demo_client, users)
    demo_client.post(f"/api/v1/employments/{job['id']}/role", headers=h, json={"role_id": role})
    demo_client.post(f"/api/v1/records/employment/{job['id']}/confirm", headers=h)
    assert position(demo_client, h)["status"] == "known"
    demo_client.post(f"/api/v1/records/employment/{job['id']}/corrections", headers=h, json={"title_raw": "Senior Product Manager"})
    assert position(demo_client, h)["status"] == "unknown"                           # the old match is stale
    demo_client.post(f"/api/v1/employments/{job['id']}/map", headers=h)                # an automatic re-match
    demo_client.post(f"/api/v1/records/employment/{job['id']}/confirm", headers=h)
    assert position(demo_client, h)["reason"] in ("role_unconfirmed", "pending_review")  # never silently re-established
    demo_client.post(f"/api/v1/employments/{job['id']}/role", headers=h, json={"role_id": "PM-SPM"})
    demo_client.post(f"/api/v1/records/employment/{job['id']}/confirm", headers=h)
    assert (position(demo_client, h)["status"], position(demo_client, h)["role_id"]) == ("known", "PM-SPM")
    corrections = demo_client.get("/api/v1/profile/records", headers=h).json()
    assert corrections                                                                 # history is kept (records intact)
