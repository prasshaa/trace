"""Phase 7.2: the starting point and the self-target guard, through the real API (local database, synthetic transient
users built from an approved fixture). The deterministic engine is unchanged; the guard compares canonical role ids."""
import pytest

from app import db
from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import PDF, candidate, consent_document, demo_client, upload  # noqa: F401  (fixtures)
from tests.test_api_phase4 import TARGETS, reviewed_candidate, scan

pytestmark = pytest.mark.integration


def position(c, h):
    return c.get("/api/v1/current-position", headers=h).json()


def pathway(c, h, target):
    return c.get("/api/v1/pathway", headers=h, params={"target": target})


def latest_job(c, h):
    """The most recent job (highest sequence index) from the caller's own records."""
    return max(c.get("/api/v1/profile/records", headers=h).json()["employments"], key=lambda e: e["sequence_index"])


def uploaded(c, users, fixture="SYN-001"):
    u, h = candidate(c, users)
    assert upload(c, h, PDF[fixture]).status_code == 201
    return u, h


# --------------------------------------------------------------------------- starting point
def test_no_current_employment_means_no_pathway(demo_client, users):
    u, h = candidate(demo_client, users)
    assert position(demo_client, h)["reason"] == "no_employment_history"
    body = pathway(demo_client, h, "PM-SPM").json()
    assert body["current_position"]["status"] == "unknown" and body["routes"] == []


def test_an_unconfirmed_current_job_means_no_pathway(demo_client, users):
    u, h = uploaded(demo_client, users)
    assert (position(demo_client, h)["status"], position(demo_client, h)["reason"]) == ("unknown", "pending_review")
    assert pathway(demo_client, h, "PM-SPM").json()["routes"] == []


def test_an_unresolved_standard_role_means_no_pathway(demo_client, users):
    u, h = uploaded(demo_client, users)
    job = latest_job(demo_client, h)
    assert demo_client.post(f"/api/v1/records/employment/{job['id']}/confirm", headers=h).status_code == 200
    p = position(demo_client, h)
    if p["status"] == "known":                       # the automatic match resolved it: correct the title to an unknown one
        assert demo_client.post(f"/api/v1/records/employment/{job['id']}/corrections", headers=h,
                                json={"title_raw": "Chief Wonder Officer"}).status_code == 201
        demo_client.post(f"/api/v1/records/employment/{job['id']}/confirm", headers=h)
        p = position(demo_client, h)
    assert p["status"] == "unknown" and p["reason"] in ("unresolved_role_mapping", "stale_mapping", "pending_review")
    assert pathway(demo_client, h, "PM-SPM").json()["routes"] == []


def test_confirming_only_the_current_job_and_role_is_enough(demo_client, users):
    u, h = uploaded(demo_client, users)
    job = latest_job(demo_client, h)
    demo_client.post(f"/api/v1/employments/{job['id']}/map", headers=h)
    assert demo_client.post(f"/api/v1/employments/{job['id']}/role", headers=h, json={"role_id": "PM-PM"}).status_code == 200
    assert demo_client.post(f"/api/v1/records/employment/{job['id']}/confirm", headers=h).status_code == 200
    p = position(demo_client, h)
    assert (p["status"], p["role_id"]) == ("known", "PM-PM")
    records = demo_client.get("/api/v1/profile/records", headers=h).json()
    unconfirmed = [x for e in records["employments"] for x in e["evidence"] if x["review"]["can_confirm"]] + \
                  [o for o in records["other_records"] if o["review"]["can_confirm"]]
    assert unconfirmed                                                   # other details are still unreviewed
    assert pathway(demo_client, h, "PM-SPM").status_code == 200


def test_a_corrected_standard_role_becomes_the_starting_point(demo_client, users):
    u, h = reviewed_candidate(demo_client, users, "SYN-001", classify=False)
    job = latest_job(demo_client, h)
    assert demo_client.post(f"/api/v1/employments/{job['id']}/role", headers=h, json={"role_id": "PM-SPM"}).status_code == 200
    demo_client.post(f"/api/v1/records/employment/{job['id']}/confirm", headers=h)
    p = position(demo_client, h)
    assert (p["status"], p["role_id"]) == ("known", "PM-SPM")


# --------------------------------------------------------------------------- self-target guard
def test_the_current_role_is_a_valid_target_without_a_zero_move_route(demo_client, users):
    # Phase 5 (D51): the same role at another company is a real move; it's allowed, and no zero-move route is built.
    u, h = reviewed_candidate(demo_client, users, "SYN-001", classify=False)
    assert position(demo_client, h)["role_id"] == "PM-PM"
    r = pathway(demo_client, h, "PM-PM")
    assert r.status_code == 200 and r.json()["routes"] == []
    scan(r)


def test_differently_worded_titles_of_one_role_are_the_same_role(demo_client, users):
    u, h = reviewed_candidate(demo_client, users, "SYN-001", classify=False)
    job = latest_job(demo_client, h)
    # The same standard role under a differently worded title: compared by role id, never by title.
    assert demo_client.post(f"/api/v1/records/employment/{job['id']}/corrections", headers=h,
                            json={"title_raw": "Product Manager - Payments Platform"}).status_code == 201
    demo_client.post(f"/api/v1/employments/{job['id']}/role", headers=h, json={"role_id": "PM-PM"})
    demo_client.post(f"/api/v1/records/employment/{job['id']}/confirm", headers=h)
    p = position(demo_client, h)
    assert (p["status"], p["role_id"]) == ("known", "PM-PM") and p["title_raw"] != "Product Manager"
    r = pathway(demo_client, h, "PM-PM")
    assert r.status_code == 200 and r.json()["routes"] == []                # same role: no self-transition route


def test_a_stored_target_that_becomes_the_current_role_still_opens(demo_client, users):
    u, h = reviewed_candidate(demo_client, users, "SYN-001", classify=False)
    assert pathway(demo_client, h, "PM-SPM").status_code == 200        # chosen while the current role was PM-PM
    job = latest_job(demo_client, h)
    demo_client.post(f"/api/v1/employments/{job['id']}/role", headers=h, json={"role_id": "PM-SPM"})
    demo_client.post(f"/api/v1/records/employment/{job['id']}/confirm", headers=h)
    r = pathway(demo_client, h, "PM-SPM")
    assert r.status_code == 200 and r.json()["routes"] == []
    assert pathway(demo_client, h, "PM-PM").status_code == 200         # a step back is a valid target too


def test_no_pathway_ever_contains_a_self_transition(demo_client, users):
    u, h = reviewed_candidate(demo_client, users, "SYN-001")
    current = position(demo_client, h)["role_id"]
    for target in TARGETS:
        r = pathway(demo_client, h, target)
        assert r.status_code == 200
        for route in r.json()["routes"]:
            ids = [x["role_id"] for x in route["route"]]
            assert len(ids) >= 2 and ids[0] != ids[-1] and ids[-1] == target, ids
        if target == current:
            assert r.json()["routes"] == []


def test_insights_still_explore_the_current_role(demo_client, users):
    u, h = reviewed_candidate(demo_client, users, "SYN-001", classify=False)
    body = demo_client.get("/api/v1/insights?target=PM-PM", headers=h).json()
    assert body["target"]["role_id"] == "PM-PM" and body["status"] == "ready"


def test_the_sample_caution_is_the_existing_setting(demo_client, users, base_settings):
    u, h = reviewed_candidate(demo_client, users, "SYN-001", classify=False)
    with db.connect(base_settings) as conn:
        moderate = int(conn.execute("select value_numeric from trace_ref.analytics_settings "
                                    "where setting_key = 'min_profiles_moderate'").fetchone()[0])
    assert pathway(demo_client, h, "PM-SPM").json()["sample_caution_below"] == moderate
    assert demo_client.get("/api/v1/insights?target=PM-SPM", headers=h).json()["scope"]["min_target_cohort"] == moderate
