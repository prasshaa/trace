"""Phase 4: taxonomy lookups, current position and the personalised pathway through the API.

Local database only; synthetic transient users. Candidates are built through the real API: Phase 2 upload of a
synthetic fixture, then Phase 3 mapping, review and classification with the fixture's curated values.
"""
import json
import re

import pytest

from app import db
from app.services.candidate_profile import CandidateProfileService, EmploymentCreate, OwnerContext, load_sequencing_context
from app.services.candidate_profile.models import CurrentPosition
from app.services.candidate_profile.pathway import (
    CandidateEvidenceSummary, RouteRole, SupportingEvidence, build_pathway_result,
)
from tests.test_api_foundation import assert_no_secrets, auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import EXTRACTOR, candidate, consent_document, demo_client, upload, PDF  # noqa: F401  (fixtures)

pytestmark = pytest.mark.integration
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
TARGETS = ["PM-APM", "PM-PM", "PM-SPM", "PM-LEAD", "PM-PRINCIPAL", "PM-GPM", "PM-HEAD", "PM-DIR", "PM-SDIR"]
M5_RAW = ('"supporting_profile_count"', '"dated_profile_count"', '"route_months_min"', '"route_months_max"', '"observations"',
          '"coarse_min_months"', '"coarse_max_months"', '"p25_months"', '"median_months"', '"p75_months"', '"timeline_metric"',
          '"timeline_display_allowed"', '"profile_id"', '"segment_no"', '"start_lo"', '"is_precise"', '"step_change_types"',
          '"consent', '"resume_id"', '"extraction_run', '"candidate_employment_id"', '"source_resume_id"')
BANNED = ("probab", "chance", "likel", "success rate", "will succeed", "hired", "guarantee", "predict")
STATES = {"suppressed_small_cohort", "no_recorded_areas", "areas_withheld", "areas_disclosed"}


def scan(resp):
    """Leak scan for every Phase 4 response."""
    assert_no_secrets(resp)
    text = resp.text
    assert not UUID_RE.search(text), UUID_RE.search(text).group(0)
    assert "SYN-" not in text
    for raw in M5_RAW:
        assert raw not in text, raw
    assert not re.search(r"relation|constraint|violates|sqlstate|psycopg|traceback", text, re.I)


def scan_pathway(body):
    words = json.dumps([body["interpretation_note"], body["role_only_notice"], body["limitations"], body["unavailable_assessments"],
                        [s["text"] for s in body["next_steps"]], [s["text"] for r in body["routes"] for s in r["next_steps"]]]).lower()
    for w in BANNED:
        assert w not in words, w
    assert body["role_only_notice"] == "Role-only assessment: company-specific fit is not assessed."
    joined = " ".join(body["unavailable_assessments"])
    for topic in ("requirements", "Skills", "Time on route", "Company-specific fit"):
        assert topic in joined, topic
    for r in body["routes"]:
        o = r["observed"]
        assert o["experience_state"] in STATES and o["is_synthetic_test_data"] and o["evidence_class"] == "synthetic_test_only"
        assert o["observation_count"] is None or o["observation_count"] >= 5
        assert all(a["profile_count"] >= 5 for a in o["disclosed_areas"])
        if o["experience_state"] != "areas_disclosed":
            assert o["disclosed_areas"] == []
        if o["experience_state"] == "suppressed_small_cohort":
            assert o["observation_count"] is None and o["areas_withheld"] is False
        assert all(s["label"] == "Generated guidance, not observed fact." for s in r["next_steps"])
    if body["routes"]:
        assert body["is_synthetic_test_data"] and body["evidence_notice"].startswith("SYNTHETIC TEST DATA")


def reviewed_candidate(client, users, fixture="SYN-001", classify=True):
    """Upload, then map, confirm and classify every record through the API with the fixture's curated values."""
    u, h = candidate(client, users)
    assert upload(client, h, PDF[fixture]).status_code == 201
    fx = EXTRACTOR.fixture(fixture)
    by_seq = {e["sequence_index"]: e for e in fx["employments"]}
    areas = {x["text"]: x.get("responsibility_area") for x in fx["evidence"]}
    view = client.get("/api/v1/profile/records", headers=h).json()
    for e in view["employments"]:
        mapped = client.post(f"/api/v1/employments/{e['id']}/map", headers=h).json()
        if mapped["result"] == "mapped_automatically":           # Phase 7.2.1: the user confirms the displayed match
            client.post(f"/api/v1/employments/{e['id']}/role", headers=h, json={"role_id": mapped["role_id"]})
        else:
            curated = by_seq[e["sequence_index"]]
            if curated["mapping_status"] in ("confirmed", "auto_high_confidence"):
                client.post(f"/api/v1/employments/{e['id']}/role", headers=h, json={"role_id": curated["role_id"]})
        assert client.post(f"/api/v1/records/employment/{e['id']}/confirm", headers=h).status_code == 200
        for x in e["evidence"]:
            if classify and areas.get(x["evidence_text"]):
                assert client.put(f"/api/v1/evidence/{x['id']}/classification", headers=h, json={"areas": [areas[x["evidence_text"]]]}).status_code == 200
            assert client.post(f"/api/v1/records/evidence/{x['id']}/confirm", headers=h).status_code == 200
    return u, h


def service_result(base_settings, u, target):
    with db.connect(base_settings) as conn:
        profile = conn.execute("select candidate_profile_id from public.trace_candidate_profiles where owner_user_id = %s", (u,)).fetchone()[0]
        conn.rollback()
        ctx = OwnerContext(owner_user_id=u, candidate_profile_id=profile)
        return CandidateProfileService(conn).pathway_result(ctx, load_sequencing_context(conn), target)


# --------------------------------------------------------------------------- taxonomy
def test_target_roles_and_responsibility_areas(demo_client, users):
    u, h = candidate(demo_client, users, grant=False)
    roles = demo_client.get("/api/v1/taxonomy/target-roles", headers=h)
    assert roles.status_code == 200
    assert [r["role_id"] for r in roles.json()] == TARGETS
    assert all(set(r) == {"role_id", "label", "report_available"} and r["label"] for r in roles.json())   # POC: + report_available
    assert roles.json()[0] == {"role_id": "PM-APM", "label": "Associate Product Manager", "report_available": False}
    assert [r["role_id"] for r in roles.json() if r["report_available"]] == ["PM-PM", "PM-SPM"]
    areas = demo_client.get("/api/v1/taxonomy/responsibility-areas", headers=h)
    assert areas.status_code == 200 and len(areas.json()) == 20          # taxonomy v2 (Phase 3.1): 16 + 4 new areas
    assert all(set(a) == {"code", "label", "is_pm_ownership_signal", "description", "examples", "questions",
                          "introduced_in_version", "current_taxonomy_version"} for a in areas.json())
    assert sorted(a["code"] for a in areas.json() if a["is_pm_ownership_signal"]) == [
        "outcome_ownership", "prioritisation_backlog", "product_discovery", "roadmap_ownership"]
    scan(roles), scan(areas)


# --------------------------------------------------------------------------- authentication and profile
@pytest.mark.parametrize("path", ["/api/v1/taxonomy/target-roles", "/api/v1/taxonomy/responsibility-areas", "/api/v1/current-position",
                                  "/api/v1/pathway?target=PM-PM"])
def test_authentication_and_profile_are_required(demo_client, users, path):
    assert demo_client.get(path).status_code == 401
    assert demo_client.get(path, headers={"Authorization": "Bearer x.y.z"}).status_code == 401
    resp = demo_client.get(path, headers=auth(token(users())))
    assert (resp.status_code, resp.json()["error"]) == (404, "profile_not_found")
    scan(resp)


# --------------------------------------------------------------------------- current position
def test_current_position_unknown_then_known(demo_client, users):
    u, h = candidate(demo_client, users)
    empty = demo_client.get("/api/v1/current-position", headers=h)
    assert empty.json()["status"] == "unknown" and empty.json()["reason"] == "no_employment_history"
    assert upload(demo_client, h, PDF["SYN-001"]).status_code == 201
    pending = demo_client.get("/api/v1/current-position", headers=h)
    body = pending.json()
    assert (body["status"], body["reason"], body["role_id"]) == ("unknown", "pending_review", None)
    assert body["explanation"] and "review_pending" in body["exclusion_reasons"]
    scan(pending)


def test_current_position_reasons_are_preserved(demo_client, users, base_settings):
    u, h = candidate(demo_client, users, grant=False)
    with db.connect(base_settings) as conn:
        profile = conn.execute("select candidate_profile_id from public.trace_candidate_profiles where owner_user_id = %s", (u,)).fetchone()[0]
        conn.rollback()
        e = CandidateProfileService(conn).add_employment(OwnerContext(owner_user_id=u, candidate_profile_id=profile),
                                                         EmploymentCreate(sequence_index=1, title_raw="Product Owner", employment_type="full_time"))
    demo_client.post(f"/api/v1/records/employment/{e}/confirm", headers=h)
    body = demo_client.get("/api/v1/current-position", headers=h).json()
    assert (body["status"], body["reason"]) == ("unknown", "unresolved_role_mapping")
    demo_client.post(f"/api/v1/employments/{e}/role", headers=h, json={"role_id": "ROLE-OWNER"})
    body = demo_client.get("/api/v1/current-position", headers=h).json()
    assert body["reason"] in ("latest_employment_ineligible",) and "unknown_start_date" in body["exclusion_reasons"]
    pw = demo_client.get("/api/v1/pathway?target=PM-PM", headers=h)
    assert pw.status_code == 200 and pw.json()["routes"] == [] and pw.json()["current_position"]["reason"] == body["reason"]
    assert any("unknown" in l for l in pw.json()["limitations"])
    scan(pw), scan_pathway(pw.json())


# --------------------------------------------------------------------------- pathway
@pytest.mark.parametrize("target", ["ROLE-SWE", "PM-INTERN", "PM-VP", "PM-CPO", "PM-NOPE", "pm-pm", "", "PM PM", "X" * 60])
def test_invalid_and_unsupported_targets(demo_client, users, target):
    u, h = candidate(demo_client, users, grant=False)
    resp = demo_client.get("/api/v1/pathway", headers=h, params={"target": target})
    assert resp.status_code == 422 and resp.json()["error"] in ("invalid_reference", "invalid_request")
    scan(resp)
    assert demo_client.get("/api/v1/pathway", headers=h).json()["error"] == "invalid_request"          # target is required


def test_full_journey_every_target_matches_the_service(demo_client, users, base_settings):
    u, h = reviewed_candidate(demo_client, users, "SYN-001")
    pos = demo_client.get("/api/v1/current-position", headers=h)
    assert pos.json()["status"] == "known" and pos.json()["role_id"] == "PM-PM"
    scan(pos)
    seen_routes = 0
    for target in TARGETS:
        resp = demo_client.get("/api/v1/pathway", headers=h, params={"target": target})
        assert resp.status_code == 200, target                     # Phase 5 (D51): the current role is a valid target
        if target == "PM-PM":
            assert resp.json()["routes"] == []                       # ...with no zero-move route
            continue
        scan(resp)
        body = resp.json()
        scan_pathway(body)
        expected = service_result(base_settings, u, target)
        assert [(r["rank"], r["route_id"], r["fit"]["tier"], r["fit"]["reason"], [g["role_id"] for g in r["step_gaps"]],
                 [g["responsibility_area"] for g in r["area_gaps"]], r["observed"]["experience_state"], r["observed"]["observation_count"])
                for r in body["routes"]] == [
            (r.rank, r.route_id, r.fit.tier, r.fit.reason, [g.role_id for g in r.step_gaps], [g.responsibility_area for g in r.area_gaps],
             r.observed.experience_state, r.observed.observation_count) for r in expected.routes], target
        assert body["candidate_evidence"]["classified_areas"] == expected.candidate_evidence.classified_areas
        assert body["target"]["role_id"] == target and body["current_position"]["role_id"] == "PM-PM"
        seen_routes += len(body["routes"])
        if not body["routes"]:
            assert any("absence of observations" in l for l in body["limitations"])
    assert seen_routes > 0


def test_routes_disclosure_and_ownership_across_candidates(demo_client, users):
    a, ha = reviewed_candidate(demo_client, users, "SYN-001")
    b, hb = reviewed_candidate(demo_client, users, "SYN-002", classify=False)
    states = set()
    for h in (ha, hb):
        for target in ("PM-APM", "PM-SPM", "PM-LEAD"):                # never the current role (Phase 7.2)
            body = demo_client.get("/api/v1/pathway", headers=h, params={"target": target}).json()
            scan_pathway(body)
            states |= {r["observed"]["experience_state"] for r in body["routes"]}
    # Phase 8.1: a route below the privacy minimum is never returned, so no returned route is suppressed.
    assert states <= STATES and "suppressed_small_cohort" not in states
    eb = demo_client.get("/api/v1/pathway", headers=hb, params={"target": "PM-SPM"}).json()["candidate_evidence"]
    ea = demo_client.get("/api/v1/pathway", headers=ha, params={"target": "PM-SPM"}).json()["candidate_evidence"]
    assert eb["classified_areas"] == [] and ea["classified_areas"]                  # each sees only their own evidence
    assert not set(s["evidence_text"] for s in ea["supporting_evidence"]) & set(
        x["text"] for x in EXTRACTOR.fixture("SYN-002")["evidence"])


def test_every_disclosure_state_passes_through_the_api_unchanged(demo_client, users, monkeypatch):
    """The four states (only three occur in the synthetic data) are preserved exactly by the API response."""
    from app.services.candidate_profile import CandidateProfileService
    from tests.test_pathway_result_unit import AREA_INFO, TITLES, area, evidence, exp
    routes = {("PM-APM", "PM-SPM"): exp(("PM-APM", "PM-SPM"), "suppressed_small_cohort"),
              ("PM-APM", "PM-PM", "PM-SPM"): exp(("PM-APM", "PM-PM", "PM-SPM"), "no_recorded_areas", 7),
              ("PM-APM", "ROLE-BA", "PM-SPM"): exp(("PM-APM", "ROLE-BA", "PM-SPM"), "areas_withheld", 8, withheld=True),
              ("PM-APM", "PM-LEAD", "PM-SPM"): exp(("PM-APM", "PM-LEAD", "PM-SPM"), "areas_disclosed", 6,
                                                    [area("outcome_ownership", 5, 6), area("roadmap_ownership", 6, 6)])}
    known = CurrentPosition(status="known", explanation="ok", role_id="PM-APM", canonical_title="Associate Product Manager",
                            candidate_employment_id="00000000-0000-4000-8000-000000000001")
    cand = CandidateEvidenceSummary(classified_areas=["outcome_ownership"], excluded={"pending_review": 1}, supporting_evidence=[
        SupportingEvidence(responsibility_area="outcome_ownership", employment_title="APM", employment_role_id="PM-APM",
                           evidence_type="achievement", evidence_text="Grew activation")])
    crafted = build_pathway_result(known, RouteRole(role_id="PM-SPM", canonical_title="Senior Product Manager"), cand, {"PM-APM"},
                                   evidence(*[(k, v, 2) for k, v in routes.items()]), TITLES, AREA_INFO)
    monkeypatch.setattr(CandidateProfileService, "pathway_result", lambda self, ctx, context, target, **kw: crafted)
    u, h = candidate(demo_client, users, grant=False)
    resp = demo_client.get("/api/v1/pathway", headers=h, params={"target": "PM-SPM"})
    assert resp.status_code == 200
    scan(resp)
    body = resp.json()
    scan_pathway(body)
    got = {r["route_id"]: (r["observed"]["experience_state"], r["observed"]["observation_count"], r["observed"]["areas_withheld"],
                           [a["responsibility_area"] for a in r["observed"]["disclosed_areas"]], r["fit"]["tier"]) for r in body["routes"]}
    assert got == {"PM-APM>PM-SPM": ("suppressed_small_cohort", None, False, [], "not_established"),
                   "PM-APM>PM-PM>PM-SPM": ("no_recorded_areas", 7, False, [], "not_established"),
                   "PM-APM>ROLE-BA>PM-SPM": ("areas_withheld", 8, True, [], "not_established"),
                   "PM-APM>PM-LEAD>PM-SPM": ("areas_disclosed", 6, False, ["outcome_ownership", "roadmap_ownership"], "partial")}
    assert [r["route_id"] for r in body["routes"]] == [r.route_id for r in crafted.routes]       # ranking preserved
    assert "00000000-0000-4000-8000-000000000001" not in resp.text                             # position id dropped


def test_reference_evidence_failures_are_generic(demo_client, users, monkeypatch):
    from app.services.candidate_profile import CandidateProfileService
    from app.services.reference_evidence import EvidenceIntegrityError
    u, h = candidate(demo_client, users, grant=False)

    def broken(self, ctx, context, target, **kw):
        raise EvidenceIntegrityError("episode_route_observations disagrees with v_career_episode_paths for SYN-007")
    monkeypatch.setattr(CandidateProfileService, "pathway_result", broken)
    resp = demo_client.get("/api/v1/pathway", headers=h, params={"target": "PM-PM"})
    assert (resp.status_code, resp.json()) == (500, {"error": "internal_error", "message": "An unexpected error occurred."})
    scan(resp)


def test_no_observed_routes_is_honest(demo_client, users, base_settings):
    u, h = candidate(demo_client, users, grant=False)
    from tests.test_candidate_profile_integration import ONGOING, month
    with db.connect(base_settings) as conn:
        profile = conn.execute("select candidate_profile_id from public.trace_candidate_profiles where owner_user_id = %s", (u,)).fetchone()[0]
        conn.rollback()
        e = CandidateProfileService(conn).add_employment(OwnerContext(owner_user_id=u, candidate_profile_id=profile),
                                                         EmploymentCreate(sequence_index=1, title_raw="HR Operations Analyst",
                                                                          employment_type="full_time", start=month(2020, 1), end=ONGOING))
    mapped = demo_client.post(f"/api/v1/employments/{e}/map", headers=h).json()
    assert mapped["result"] == "mapped_automatically"
    demo_client.post(f"/api/v1/employments/{e}/role", headers=h, json={"role_id": mapped["role_id"]})   # the user confirms it
    demo_client.post(f"/api/v1/records/employment/{e}/confirm", headers=h)
    resp = demo_client.get("/api/v1/pathway", headers=h, params={"target": "PM-DIR"})
    body = resp.json()
    assert body["current_position"]["role_id"] == "ROLE-HR" and body["routes"] == []
    assert any("absence of observations" in l for l in body["limitations"]) and body["is_synthetic_test_data"]
    scan(resp), scan_pathway(body)
