"""Phase 3: review, corrections, role mapping and evidence classification through the API.

Local database only; synthetic transient users; records come from the real Phase 2 upload of synthetic fixtures, or
(for states ingestion cannot produce, such as the candidate's own draft) from the candidate service as that owner.
"""
import re
import uuid

import pytest

from app import db
from app.services.candidate_profile import CandidateProfileService, EmploymentCreate, OwnerContext
from tests.test_api_foundation import assert_no_secrets, auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import PDF, candidate, consent_document, demo_client, upload  # noqa: F401  (fixtures)

pytestmark = pytest.mark.integration
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
KINDS = ("employment", "education", "activity", "evidence", "skill", "career_break")


def ingested(client, users, fixture="SYN-001"):
    u, h = candidate(client, users)
    assert upload(client, h, PDF[fixture]).status_code == 201
    return u, h


def records(client, h):
    resp = client.get("/api/v1/profile/records", headers=h)
    assert resp.status_code == 200
    return resp.json()


def job(view, i=0):
    return view["employments"][i]


def own_draft(base_settings, u, title):
    """A pending user_stated employment (the candidate's own draft), created as that owner through the service."""
    with db.connect(base_settings) as conn:
        profile = conn.execute("select candidate_profile_id from public.trace_candidate_profiles where owner_user_id = %s", (u,)).fetchone()[0]
        conn.rollback()
        svc = CandidateProfileService(conn)
        return svc.add_employment(OwnerContext(owner_user_id=u, candidate_profile_id=profile),
                                  EmploymentCreate(sequence_index=50, title_raw=title, employment_type="full_time"))


def assert_safe(resp, allowed_ids=()):
    assert_no_secrets(resp)
    assert not re.search(r"relation|constraint|violates|sqlstate|psycopg|trace_\w+\.", resp.text, re.I)
    assert set(UUID_RE.findall(resp.text)) <= {str(i) for i in allowed_ids}, "unexpected identifier in response"


# --------------------------------------------------------------------------- the journey
def test_upload_review_correct_map_and_classify_journey(demo_client, users):
    u, h = ingested(demo_client, users)
    v = records(demo_client, h)
    ids = {e["id"] for e in v["employments"]} | {x["id"] for e in v["employments"] for x in e["evidence"]} | {r["id"] for r in v["other_records"]}
    assert_safe(demo_client.get("/api/v1/profile/records", headers=h), ids)            # only the candidate's own record handles
    first = job(v)
    assert (first["review"]["status"], first["review"]["label"], first["review"]["origin"]) == ("pending_review", "Pending review", "source_extracted")
    assert first["review"]["can_confirm"] and first["review"]["can_correct"] and not first["review"]["is_own_draft"]
    assert first["mapping"]["state"] == "unmapped" and not first["mapping"]["is_stale"]
    assert all(x["classification"]["state"] == "none" for x in first["evidence"])

    # map: automatic where the alias table allows it
    mapped = demo_client.post(f"/api/v1/employments/{first['id']}/map", headers=h)
    assert mapped.status_code == 200 and mapped.json()["result"] == "mapped_automatically"
    assert "candidate_employment_id" not in mapped.text
    assert job(records(demo_client, h))["mapping"]["state"] == "automatic"

    # review the employment
    c = demo_client.post(f"/api/v1/records/employment/{first['id']}/confirm", headers=h)
    assert c.json() == {"kind": "employment", "status": "user_confirmed", "label": "Confirmed"}

    # classify evidence: it stays pending (classification is not review)
    ev = first["evidence"][0]
    cl = demo_client.put(f"/api/v1/evidence/{ev['id']}/classification", headers=h, json={"areas": ["technical_engineering", "technical_engineering"]})
    assert cl.status_code == 200 and cl.json()["areas"] == ["technical_engineering"] and "does not confirm" in cl.json()["note"]
    ev_now = job(records(demo_client, h))["evidence"][0]
    assert ev_now["classification"]["state"] == "current" and ev_now["review"]["status"] == "pending_review"
    assert ev_now["review"]["verification_status"] == ev["review"]["verification_status"]

    # correct the title: the record becomes corrected, history keeps the original, the mapping goes stale
    corr = demo_client.post(f"/api/v1/records/employment/{first['id']}/corrections", headers=h, json={"title_raw": "Senior Software Engineer"})
    assert corr.status_code == 201 and corr.json()["status"] == "user_corrected"
    hist = demo_client.get(f"/api/v1/records/employment/{first['id']}/corrections", headers=h)
    assert hist.status_code == 200
    (event,) = hist.json()
    assert (event["changed_fields"], event["previous_values"], event["corrected_values"], event["prior_review_status"]) == (
        ["title_raw"], {"title_raw": first["title_raw"]}, {"title_raw": "Senior Software Engineer"}, "user_confirmed")
    assert_safe(hist)
    now = job(records(demo_client, h))
    assert (now["review"]["label"], now["mapping"]["state"], now["mapping"]["is_stale"], now["mapping"]["mapped_title"]) == (
        "Corrected", "automatic", True, first["title_raw"])
    assert demo_client.post(f"/api/v1/employments/{first['id']}/map", headers=h).json()["role_id"] == "ROLE-SWE"
    assert job(records(demo_client, h))["mapping"]["is_stale"] is False

    # correct the evidence text: its classification goes stale and says which text it described
    demo_client.post(f"/api/v1/records/evidence/{ev['id']}/corrections", headers=h, json={"evidence_text": "Built payment APIs"})
    stale = job(records(demo_client, h))["evidence"][0]["classification"]
    assert (stale["state"], stale["classified_text"]) == ("stale", ev["evidence_text"])


# --------------------------------------------------------------------------- review transitions
def test_every_review_transition_follows_the_service_matrix(demo_client, users):
    """Every (state, action) pair: valid transitions and no-ops answer 200 with the matrix's state; anything the
    matrix does not allow answers 409 invalid_transition and changes nothing."""
    from app.services.candidate_profile.review import TRANSITIONS, _NOOP
    u, h = ingested(demo_client, users)
    rid = job(records(demo_client, h))["id"]
    url = lambda a: f"/api/v1/records/employment/{rid}/{a}"
    counter = iter(range(1000))

    def status():
        return job(records(demo_client, h))["review"]["status"]

    def bring_to(state):
        if status() != "rejected":
            demo_client.post(url("reject"), headers=h)
        demo_client.post(url("reinstate"), headers=h)                     # now pending_review
        if state == "user_confirmed":
            demo_client.post(url("confirm"), headers=h)
        elif state == "user_corrected":
            demo_client.post(url("corrections"), headers=h, json={"title_raw": f"Corrected title {next(counter)}"})
        elif state == "rejected":
            demo_client.post(url("reject"), headers=h)
        assert status() == state

    for state in ("pending_review", "user_confirmed", "user_corrected", "rejected"):
        for action in ("confirm", "reject", "reinstate"):
            bring_to(state)
            expected = TRANSITIONS[action].get(state)
            resp = demo_client.post(url(action), headers=h)
            if expected is None:
                assert (resp.status_code, resp.json()["error"]) == (409, "invalid_transition"), (state, action)
                assert status() == state
            else:
                target = state if expected is _NOOP else expected
                assert (resp.status_code, resp.json()["status"]) == (200, target), (state, action)
                assert status() == target
            assert_safe(resp)


@pytest.mark.parametrize("kind", ["employer", "profile", "nope", "EMPLOYMENT"])
def test_unsupported_kinds_are_refused(demo_client, users, kind):
    u, h = ingested(demo_client, users)
    rid = job(records(demo_client, h))["id"]
    resp = demo_client.post(f"/api/v1/records/{kind}/{rid}/confirm", headers=h)
    assert resp.status_code == 422 and resp.json()["error"] == "invalid_request"
    assert "input" not in resp.json()


def test_every_kind_can_be_reviewed(demo_client, users):
    u, h = ingested(demo_client, users)
    v = records(demo_client, h)
    seen = {"employment": job(v)["id"], "evidence": job(v)["evidence"][0]["id"]}
    seen.update({r["kind"]: r["id"] for r in v["other_records"]})
    for kind, rid in seen.items():
        assert demo_client.post(f"/api/v1/records/{kind}/{rid}/confirm", headers=h).json()["status"] == "user_confirmed", kind
    assert {"employment", "evidence", "education", "skill"} <= set(seen)


# --------------------------------------------------------------------------- ownership isolation
def test_another_candidates_records_are_404_on_every_endpoint(demo_client, users):
    a, ha = ingested(demo_client, users)
    v = records(demo_client, ha)
    emp, ev = job(v)["id"], job(v)["evidence"][0]["id"]
    b, hb = ingested(demo_client, users, "SYN-002")
    calls = [("post", f"/api/v1/records/employment/{emp}/confirm", None), ("post", f"/api/v1/records/employment/{emp}/reject", None),
             ("post", f"/api/v1/records/employment/{emp}/reinstate", None),
             ("post", f"/api/v1/records/employment/{emp}/corrections", {"title_raw": "Hijacked"}),
             ("get", f"/api/v1/records/employment/{emp}/corrections", None), ("get", f"/api/v1/employments/{emp}/role-options", None),
             ("post", f"/api/v1/employments/{emp}/map", None), ("post", f"/api/v1/employments/{emp}/role", {"role_id": "PM-PM"}),
             ("put", f"/api/v1/evidence/{ev}/classification", {"areas": ["roadmap_ownership"]}),
             ("post", f"/api/v1/records/evidence/{ev}/confirm", None)]
    for method, url, body in calls:
        resp = getattr(demo_client, method)(url, headers=hb, **({"json": body} if body is not None else {}))
        assert (resp.status_code, resp.json()["error"]) == (404, "record_not_found"), url
        assert_safe(resp)
    after = records(demo_client, ha)
    assert job(after)["review"]["status"] == "pending_review" and job(after)["mapping"]["state"] == "unmapped"
    assert job(after)["evidence"][0]["classification"]["state"] == "none"


def test_endpoints_need_a_profile_and_a_token(demo_client, users):
    rid = uuid.uuid4()
    paths = [("post", f"/api/v1/records/employment/{rid}/confirm"), ("get", f"/api/v1/records/employment/{rid}/corrections"),
             ("get", f"/api/v1/employments/{rid}/role-options"), ("post", f"/api/v1/employments/{rid}/map"),
             ("get", "/api/v1/profile/records")]
    for method, url in paths:
        assert getattr(demo_client, method)(url).status_code == 401
        bad = getattr(demo_client, method)(url, headers={"Authorization": "Bearer not.a.token"})
        assert bad.status_code == 401
    assert demo_client.put(f"/api/v1/evidence/{rid}/classification", json={"areas": ["x"]}).status_code == 401
    no_profile = auth(token(users()))
    assert demo_client.get("/api/v1/profile/records", headers=no_profile).json()["error"] == "profile_not_found"


# --------------------------------------------------------------------------- corrections
def test_correction_errors_and_append_only_history(demo_client, users, base_settings):
    u, h = ingested(demo_client, users)
    rid = job(records(demo_client, h))["id"]
    url = f"/api/v1/records/employment/{rid}/corrections"
    bad = demo_client.post(url, headers=h, json={"role_id": "PM-DIR"})
    assert (bad.status_code, bad.json()["error"]) == (422, "invalid_record") and "role_id" in bad.json()["message"]
    same = demo_client.post(url, headers=h, json={"title_raw": job(records(demo_client, h))["title_raw"]})
    assert (same.status_code, same.json()["error"]) == (422, "invalid_reference")              # changes nothing
    for title in ("First correction", "Second correction"):
        assert demo_client.post(url, headers=h, json={"title_raw": title}).status_code == 201
    hist = demo_client.get(url, headers=h).json()
    assert [e["sequence"] for e in hist] == sorted(e["sequence"] for e in hist) and len(hist) == 2
    assert hist[1]["previous_values"] == {"title_raw": "First correction"} and hist[0]["prior_review_status"] == "pending_review"
    assert demo_client.get(f"/api/v1/records/education/{rid}/corrections", headers=h).status_code == 404       # wrong kind
    demo_client.post(f"/api/v1/records/employment/{rid}/reject", headers=h)
    rej = demo_client.post(url, headers=h, json={"title_raw": "Third"})
    assert (rej.status_code, rej.json()["error"]) == (409, "record_not_editable")
    draft = own_draft(base_settings, u, "My own draft")
    d = demo_client.post(f"/api/v1/records/employment/{draft}/corrections", headers=h, json={"title_raw": "Edited"})
    assert (d.status_code, d.json()["error"]) == (409, "correction_not_available")
    dv = next(e for e in records(demo_client, h)["employments"] if e["id"] == str(draft))["review"]
    assert dv["is_own_draft"] and not dv["can_correct"] and dv["can_confirm"]                  # drafts are edited, not corrected
    assert len(demo_client.get(url, headers=h).json()) == 2                                  # refused attempts appended nothing


# --------------------------------------------------------------------------- role mapping
def test_context_required_titles_need_a_decision_and_product_owner_clarification(demo_client, users, base_settings):
    u, h = ingested(demo_client, users)
    po = own_draft(base_settings, u, "Product Owner (Payments)")
    opts = demo_client.get(f"/api/v1/employments/{po}/role-options", headers=h)
    assert opts.status_code == 200 and "candidate_employment_id" not in opts.text
    o = opts.json()
    assert o["outcome"] in ("decision_required", "unresolved") and o["automatic_role_id"] is None
    flags = {c["role_id"]: c["requires_ownership_clarification"] for c in o["alias_options"] + o["other_roles"]}
    assert flags["PM-PM"] and not flags["ROLE-OWNER"] and "ROLE-UNKNOWN" not in flags
    assert demo_client.post(f"/api/v1/employments/{po}/map", headers=h).json()["result"] in ("decision_required", "unresolved")
    no_areas = demo_client.post(f"/api/v1/employments/{po}/role", headers=h, json={"role_id": "PM-PM"})
    assert (no_areas.status_code, no_areas.json()["error"]) == (422, "clarification_required")
    unknown = demo_client.post(f"/api/v1/employments/{po}/role", headers=h, json={"role_id": "ROLE-UNKNOWN"})
    assert (unknown.status_code, unknown.json()["error"]) == (422, "invalid_reference")
    ok = demo_client.post(f"/api/v1/employments/{po}/role", headers=h, json={"role_id": "PM-PM", "ownership_areas": ["roadmap_ownership"]})
    assert ok.json() == {"role_id": "PM-PM", "mapping_basis": "candidate_selected"}
    view = next(e for e in records(demo_client, h)["employments"] if e["id"] == str(po))["mapping"]
    assert (view["state"], view["role_id"], view["confidence"], view["ownership_areas"]) == ("candidate_selected", "PM-PM", None, ["roadmap_ownership"])
    kept = demo_client.post(f"/api/v1/employments/{po}/map", headers=h).json()
    assert kept["result"] in ("candidate_decision_kept", "unresolved", "decision_required")
    assert next(e for e in records(demo_client, h)["employments"] if e["id"] == str(po))["mapping"]["state"] == "candidate_selected"


def test_extra_fields_in_role_choice_are_refused(demo_client, users):
    u, h = ingested(demo_client, users)
    rid = job(records(demo_client, h))["id"]
    resp = demo_client.post(f"/api/v1/employments/{rid}/role", headers=h, json={"role_id": "PM-PM", "mapping_confidence": 1})
    assert resp.status_code == 422 and resp.json()["fields"] == ["mapping_confidence"]


# --------------------------------------------------------------------------- classification
def test_classification_rules(demo_client, users):
    u, h = ingested(demo_client, users)
    ev = job(records(demo_client, h))["evidence"][0]
    url = f"/api/v1/evidence/{ev['id']}/classification"
    for body, code, error in (({"areas": []}, 422, "invalid_reference"), ({"areas": ["not_an_area"]}, 422, "invalid_reference"),
                              ({"areas": ["roadmap_ownership"], "basis": "verified"}, 422, "invalid_request")):
        resp = demo_client.put(url, headers=h, json=body)
        assert (resp.status_code, resp.json()["error"]) == (code, error), body
    demo_client.post(f"/api/v1/records/evidence/{ev['id']}/reject", headers=h)
    rej = demo_client.put(url, headers=h, json={"areas": ["roadmap_ownership"]})
    assert (rej.status_code, rej.json()["error"]) == (409, "record_not_editable")
    demo_client.post(f"/api/v1/records/evidence/{ev['id']}/reinstate", headers=h)
    assert demo_client.put(url, headers=h, json={"areas": ["roadmap_ownership"]}).status_code == 200
    after = job(records(demo_client, h))["evidence"][0]
    assert (after["review"]["status"], after["classification"]["state"]) == ("pending_review", "current")
