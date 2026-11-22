"""Phase 6.1: a user's own resume, automatic contribution, withdrawal and cohort integrity, end to end.

Users are transient (deleted at teardown; deleting an account cascades to its contribution and reference rows).
All resumes, names and contact details are invented test values.
"""
import json
import re
import threading
import time
import uuid

import psycopg
import pytest

from app import db
from app.config import Settings
from app.demo import consent as demo_consent
from app.services.career_insights import load_rows
from app.services.contributions import process_pending
from app.services.contributions import worker as worker_module
PRIVATE_PAUSE_LOCK = 7_310_412_098         # the session holds the real pause lock (conftest)
from tests.test_api_foundation import assert_no_secrets, auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import PDF, candidate, consent_document, make_client, upload  # noqa: F401
from tests.test_deidentification import make_pdf

pytestmark = pytest.mark.integration
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
IDENTIFIERS = ("Meera", "Kulkarni", "example.com", "98200", "linkedin", "meerak")
RETAINED = ("Zentrix Software", "College of Engineering Pune", "8.4", "Certified Scrum Product Owner", "Product Manager",
            "Northwind Logistics", "Computer Engineering")


def resume(employer2="Northwind Logistics", extra=()):
    return make_pdf([
        "Meera Kulkarni", "meera.k@example.com | +91 98200 11223 | linkedin.com/in/meerakulkarni", "Pune, Maharashtra",
        "PROFESSIONAL SUMMARY", "Product leader with payments experience.",
        "EXPERIENCE", "Product Manager, Zentrix Software Apr 2019 - Present",
        "- Owned the payments roadmap and launched UPI autopay for 2M users",
        "- Grew activation 18% in 2022 through onboarding experiments; write to meera.k@example.com",
        "- Meera led the quarterly launch reviews with the finance team",
        f"Business Analyst | {employer2} | Jul 2016 - Mar 2019", "- Built SQL dashboards for operations leadership", *extra,
        "EDUCATION", "B.E. Computer Engineering, College of Engineering Pune, 2016, CGPA 8.4/10",
        "SKILLS", "SQL, Product discovery, Roadmapping, A/B testing",
        "CERTIFICATIONS", "Certified Scrum Product Owner - Scrum Alliance, 2020",
        "REFERENCES", "Mr Some Referee, +91 90000 00000"], author="Meera Kulkarni", uri="https://github.com/meerak")


@pytest.fixture(scope="module")
def contribution_document(base_settings):
    from scripts.demo_setup import install_contribution_document
    with db.connect(base_settings) as c:
        status = install_contribution_document(c)
    yield demo_consent.CONTRIBUTION_VERSION
    if status == "installed":
        with db.connect(base_settings) as c:
            c.autocommit = True
            try:
                c.execute("delete from public.trace_consent_documents where consent_version = %s", (demo_consent.CONTRIBUTION_VERSION,))
            except psycopg.Error:
                pass


@pytest.fixture
def rc(base_settings, consent_document, contribution_document):
    with make_client(base_settings, trace_demo_fixtures=True, trace_real_resume_uploads=True,
                     trace_enabled_cohorts="synthetic,consented_real") as c:
        yield c


def post_real(c, h, data, content_type="application/pdf"):
    return c.post("/api/v1/real-resumes", headers={**h, "Content-Type": content_type}, content=data)


def confirm_all(c, h):
    v = c.get("/api/v1/profile/records", headers=h).json()
    items = [("employment", e) for e in v["employments"]] + [("evidence", x) for e in v["employments"] for x in e["evidence"]] \
        + [("evidence", x) for x in v["unattached_evidence"] + v["activity_evidence"]] + [(o["kind"], o) for o in v["other_records"]]
    for kind, rec in items:
        if rec["review"]["can_confirm"]:
            assert c.post(f"/api/v1/records/{kind}/{rec['id']}/confirm", headers=h).status_code == 200


def contribute(c, h, decision="granted"):
    r = c.post("/api/v1/consent/contribution", headers=h, json={"decision": decision, "consent_version": demo_consent.CONTRIBUTION_VERSION})
    assert r.status_code == 200, r.text
    return r.json()


def state(c, h):
    r = c.get("/api/v1/contribution", headers=h)
    assert r.status_code == 200
    return r.json()


def admin_row(base_settings, sql, *params):
    with db.connect(base_settings) as conn:
        return conn.execute(sql, params).fetchone()


def contribution_of(base_settings, u):
    return admin_row(base_settings, "select c.contribution_id, c.profile_id, c.status from trace_private.contributions c "
                                    "join trace_private.contributors k using (contributor_id) where k.user_id = %s and c.status = 'admitted'", u)


def ready_user(rc, users, contributor=False):
    """A user with a processed resume. contributor: contribution consent is granted BEFORE the upload, the only order in
    which identity markers are kept (D48); without it the markers are used in memory and discarded."""
    u, h = candidate(rc, users)
    if contributor:
        contribute(rc, h)
    assert post_real(rc, h, resume()).status_code == 201
    return u, h


# --------------------------------------------------------------------------- upload and de-identification
def test_a_real_resume_becomes_a_private_profile_without_identifiers(rc, users, base_settings):
    u, h = candidate(rc, users)
    r = post_real(rc, h, resume())
    assert r.status_code == 201 and r.json()["status"] == "processed"
    assert r.json()["extracted"] == {"employments": 2, "education": 1, "skills": 4, "evidence": 4, "activities": 1}
    assert_no_secrets(r)
    for value in IDENTIFIERS:
        assert value not in r.text
    with db.connect(base_settings) as conn:
        dump = json.dumps([conn.execute(f"select to_jsonb(t) from public.{t} t where owner_user_id = %s", (u,)).fetchall()
                           for t in ("trace_candidate_profiles", "trace_candidate_employers", "trace_candidate_employments",
                                     "trace_candidate_education", "trace_candidate_evidence", "trace_candidate_skills",
                                     "trace_candidate_career_activities", "trace_resume_documents", "trace_extraction_runs")], default=str)
        origins = conn.execute("select distinct information_origin::text, review_status::text from public.trace_candidate_employments "
                               "where owner_user_id = %s", (u,)).fetchall()
        doc = conn.execute("select original_filename, is_test_fixture, byte_size > 0 from public.trace_resume_documents where owner_user_id = %s",
                           (u,)).fetchone()
        run = conn.execute("select provider_name, model_name, status::text from public.trace_extraction_runs where owner_user_id = %s",
                           (u,)).fetchone()
    for value in IDENTIFIERS + ("Referee", "90000"):
        assert value not in dump, value
    for kept in RETAINED + ("SQL",):                                             # free-text skills stay in the private profile
        assert kept in dump, kept
    assert origins == [("source_extracted", "pending_review")]                  # drafts for the user to review
    assert doc == (None, False, True)                                            # no filename, not a fixture, metadata only
    assert run == ("trace-resume-parser", "deterministic-deidentified", "succeeded")
    assert rc.get("/api/v1/real-resumes/status", headers=h).json()["status"] == "processed"


@pytest.mark.parametrize("data, ctype, code", [(b"%PDF-1.4 not really a pdf", "application/pdf", 422),
                                               (b"plain text", "text/plain", 415), (b"%PDF-" + b"x" * 1_100_000, "application/pdf", 413)])
def test_invalid_files_are_refused_and_nothing_is_stored(rc, users, base_settings, data, ctype, code):
    u, h = candidate(rc, users)
    assert post_real(rc, h, data, ctype).status_code == code
    assert admin_row(base_settings, "select count(*) from public.trace_resume_documents where owner_user_id = %s", u)[0] == 0


def test_a_pdf_without_career_content_is_refused(rc, users, base_settings):
    u, h = candidate(rc, users)
    r = post_real(rc, h, make_pdf(["Just a cover letter", "Dear hiring manager, thank you."]))
    assert (r.status_code, r.json()["error"]) == (422, "resume_not_processable")
    assert admin_row(base_settings, "select count(*) from public.trace_candidate_employments where owner_user_id = %s", u)[0] == 0


def test_uploads_need_a_token_the_server_switch_consent_and_an_empty_non_fixture_profile(base_settings, consent_document, users):
    with make_client(base_settings, trace_demo_fixtures=True, trace_real_resume_uploads=True) as c:
        assert c.post("/api/v1/real-resumes", content=resume(), headers={"Content-Type": "application/pdf"}).status_code == 401
        u, h = candidate(c, users, grant=False)
        assert (post_real(c, h, resume()).json()["error"]) == "consent_required"
        u, h = candidate(c, users)
        assert post_real(c, h, resume()).status_code == 201
        again = post_real(c, h, resume())                                                      # never merges (Phase 2:
        assert (again.status_code, again.json()["status"]) == (201, "unchanged")              # the same file is unchanged)
        u, h = candidate(c, users)
        assert upload(c, h, PDF["SYN-001"]).status_code == 201                                 # a synthetic demo profile
        r = post_real(c, h, resume())
        assert (r.status_code, r.json()["error"]) == (409, "fixture_profile_conflict")
    with make_client(base_settings, trace_demo_fixtures=True) as c:                            # switch off (default)
        u, h = candidate(c, users)
        assert (post_real(c, h, resume()).status_code, post_real(c, h, resume()).json()["error"]) == (403, "real_uploads_disabled")


def test_a_failed_privacy_check_stores_nothing(rc, users, base_settings, monkeypatch):
    from app.services import real_resume
    from app.services.deidentification import Result
    monkeypatch.setattr(real_resume, "deidentify", lambda *a, **k: Result(status="failed", record=None, profile_id="CRR-000000000000",
                                                                           report={"status": "failed"}))
    u, h = candidate(rc, users)
    r = post_real(rc, h, resume())
    assert (r.status_code, r.json()["error"]) == (422, "resume_not_processable")
    for t in ("trace_resume_documents", "trace_candidate_employments", "trace_candidate_evidence"):
        assert admin_row(base_settings, f"select count(*) from public.{t} where owner_user_id = %s", u)[0] == 0
    assert rc.get("/api/v1/real-resumes/status", headers=h).json()["status"] == "not_processed"


def test_an_ambiguous_resume_is_quarantined_and_never_reaches_the_profile(rc, users, base_settings):
    u, h = candidate(rc, users)
    r = post_real(rc, h, resume(employer2="Kulkarni & Sons Logistics"))           # the surname inside an employer name
    assert r.status_code == 202 and r.json()["status"] == "awaiting_manual_review"
    assert admin_row(base_settings, "select count(*) from public.trace_candidate_employments where owner_user_id = %s", u)[0] == 0
    q = admin_row(base_settings, "select status, record::text, report from trace_private.resume_quarantine where owner_user_id = %s", u)
    assert q[0] == "pending_review" and "example.com" not in q[1] and "linkedin" not in q[1]
    assert q[2]["ambiguous"]["name_part_in_retained_name"] >= 1
    assert rc.get("/api/v1/real-resumes/status", headers=h).json()["status"] == "awaiting_manual_review"


def test_one_user_cannot_admit_or_read_anothers_resume(rc, users, base_settings):
    a, ha = ready_user(rc, users)
    b, hb = candidate(rc, users)
    assert rc.get("/api/v1/real-resumes/status", headers=hb).json()["status"] == "none"
    ra = admin_row(base_settings, "select resume_id, candidate_profile_id, content_sha256 from public.trace_resume_documents where owner_user_id = %s", a)
    from app.services.candidate_profile.repository import IDENTITY_SQL
    with db.connect_runtime(base_settings) as rt:
        with pytest.raises(psycopg.Error, match="non-fixture profile of the caller"):
            with rt.transaction():
                rt.execute(IDENTITY_SQL, {"sub": str(b), "claims": json.dumps({"sub": str(b), "role": "authenticated"})})
                rt.execute("select trace_ingest.admit_deidentified_resume(%s, %s, %s, 'x')", ra)


# --------------------------------------------------------------------------- automatic contribution
def test_no_contribution_without_explicit_consent(rc, users, base_settings):
    u, h = ready_user(rc, users)
    confirm_all(rc, h)
    process_pending(Settings())
    s = state(rc, h)
    assert (s["state"], s["included"], s["consent_active"]) == ("not_enabled", False, False)
    assert contribution_of(base_settings, u) is None


def test_the_full_automatic_lifecycle(rc, users, base_settings):
    u, h = ready_user(rc, users, contributor=True)
    contribute(rc, h)
    assert state(rc, h)["state"] in ("pending", "processing")
    process_pending(Settings())
    assert state(rc, h)["state"] == "awaiting_review"                           # drafts not reviewed yet
    assert contribution_of(base_settings, u) is None
    confirm_all(rc, h)
    assert state(rc, h)["state"] == "pending"                                    # the confirmations queued it
    process_pending(Settings())
    s = state(rc, h)
    assert (s["state"], s["included"]) == ("eligible", True) and s["label"].startswith("Included")
    cid, pid, status = contribution_of(base_settings, u)
    assert status == "admitted" and re.fullmatch(r"CRR-\d{12}", pid)
    with db.connect(base_settings) as conn:
        prof = conn.execute("select dataset_class::text, contributor_user_id, synthetic_demo_allowed from trace_ref.profiles "
                            "where profile_id = %s", (pid,)).fetchone()
        dump = json.dumps([conn.execute(f"select to_jsonb(t) from trace_ref.{t} t where profile_id = %s", (pid,)).fetchall()
                           for t in ("profiles", "employers", "employments", "education", "experience_evidence", "profile_skills",
                                     "activities")], default=str)
        assert pid in {r[0] for r in conn.execute("select profile_id from trace_ref.v_eligible_profiles").fetchall()}
        real, _, _ = load_rows(conn, "consented_real")
        synthetic, _, _ = load_rows(conn, "synthetic")
    assert prof == ("consented_real_reference", None, False)
    for value in IDENTIFIERS:
        assert value not in dump, value
    for kept in RETAINED:
        assert kept in dump, kept
    assert pid in {r["profile_id"] for r in real["profiles"]}
    assert pid not in {r["profile_id"] for r in synthetic["profiles"]} and len(synthetic["profiles"]) == 41
    body = rc.get("/api/v1/contribution", headers=h).text
    assert "CRR-" not in body and not UUID_RE.search(body) and "report" not in body


def test_repeats_are_idempotent_and_edits_update_in_place(rc, users, base_settings):
    u, h = ready_user(rc, users, contributor=True)
    contribute(rc, h)
    confirm_all(rc, h)
    process_pending(Settings())
    first = contribution_of(base_settings, u)
    for _ in range(3):                                                           # repeated grants and passes
        contribute(rc, h)
        process_pending(Settings())
    assert contribution_of(base_settings, u) == first
    assert admin_row(base_settings, "select count(*) from trace_private.contributions c join trace_private.contributors k "
                                    "using (contributor_id) where k.user_id = %s", u)[0] == 1
    assert admin_row(base_settings, "select count(*) from trace_private.contribution_requests where user_id = %s", u)[0] == 0
    ev = rc.get("/api/v1/profile/records", headers=h).json()["employments"][0]["evidence"][0]
    assert rc.post(f"/api/v1/records/evidence/{ev['id']}/corrections", headers=h,
                   json={"evidence_text": "Owned the cards roadmap across three markets"}).status_code == 201
    process_pending(Settings())
    assert contribution_of(base_settings, u) == first                           # same contribution, same reference id
    joined = " ".join(admin_row(base_settings, "select array_agg(evidence_text_redacted) from trace_ref.experience_evidence "
                                               "where profile_id = %s", first[1])[0])
    assert "cards roadmap" in joined and ev["evidence_text"] not in joined      # replaced in place
    assert admin_row(base_settings, "select count(*) from trace_ref.profiles where profile_id like 'CRR-%%' and profile_id = %s",
                     first[1])[0] == 1


def test_withdrawal_excludes_immediately_and_regrant_starts_fresh(rc, users, base_settings):
    u, h = ready_user(rc, users, contributor=True)
    contribute(rc, h)
    confirm_all(rc, h)
    process_pending(Settings())
    old_cid, old_pid, _ = contribution_of(base_settings, u)
    w = contribute(rc, h, "withdrawn")
    assert w["active"] is False
    assert admin_row(base_settings, "select count(*) from trace_ref.profiles where profile_id = %s", old_pid)[0] == 0   # same transaction
    assert state(rc, h)["state"] == "withdrawn"
    assert admin_row(base_settings, "select status, profile_id from trace_private.contributions where contribution_id = %s", old_cid) == ("withdrawn", None)
    assert rc.get("/api/v1/consent/resume-processing", headers=h).json()["active"] is True              # personal use continues
    assert len(rc.get("/api/v1/profile/records", headers=h).json()["employments"]) == 2
    assert admin_row(base_settings, "select count(*) from trace_private.identity_markers where owner_user_id = %s", u)[0] == 0   # D48
    contribute(rc, h)                                                            # granted again
    assert contribution_of(base_settings, u) is None                             # nothing reactivated before processing
    process_pending(Settings())
    # The withdrawal deleted the identity markers (D48), so the fresh contribution can no longer be checked for the
    # owner's name automatically: it waits for a person instead of being admitted.
    assert state(rc, h)["state"] == "quarantined" and contribution_of(base_settings, u) is None
    assert admin_row(base_settings, "select reason from trace_private.user_contribution_status where user_id = %s", u)[0] == "identity_markers_missing"
    assert admin_row(base_settings, "select status from trace_private.contributions where contribution_id = %s", old_cid)[0] == "withdrawn"


def test_a_later_failure_suspends_the_existing_contribution(rc, users, base_settings, monkeypatch):
    u, h = ready_user(rc, users, contributor=True)
    contribute(rc, h)
    confirm_all(rc, h)
    process_pending(Settings())
    cid, pid, _ = contribution_of(base_settings, u)
    from app.services.contributions import processor
    from app.services.deidentification import Result
    monkeypatch.setattr(processor, "deidentify", lambda *a, **k: Result(status="failed", record=None, profile_id="CRR-000000000000",
                                                                         report={"status": "failed"}))
    contribute(rc, h)                                                            # requeue
    process_pending(Settings())
    assert state(rc, h)["state"] == "failed"
    assert admin_row(base_settings, "select status, profile_id from trace_private.contributions where contribution_id = %s", cid) == ("suspended", None)
    assert admin_row(base_settings, "select count(*) from trace_ref.profiles where profile_id = %s", pid)[0] == 0


def test_the_database_rechecks_consent_fixtures_and_identifiers(rc, users, base_settings):
    u, h = ready_user(rc, users)                                                 # no contribution consent
    demo, hd = candidate(rc, users)
    assert upload(rc, hd, PDF["SYN-001"]).status_code == 201
    contribute(rc, hd)                                                            # a synthetic profile with consent
    clean = {"employments": [], "employers": []}
    with db.connect_runtime(base_settings) as rt:
        call = lambda user, rec: rt.execute(  # noqa: E731
            "select trace_contrib.record_contribution_outcome(%s, now(), 'eligible', null, %s, %s, '{}', 'test')",
            (user, json.dumps(rec), "a" * 64)).fetchone()[0]
        assert call(u, clean) == "not_eligible"                                  # no consent: whatever the caller says
        assert call(demo, clean) == "not_eligible"                               # synthetic profile: never real
        rt.rollback()
    v, hv = ready_user(rc, users)
    contribute(rc, hv)
    with db.connect_runtime(base_settings) as rt:
        assert rt.execute("select trace_contrib.record_contribution_outcome(%s, now(), 'eligible', null, %s, %s, '{}', 'test')",
                          (v, json.dumps({"evidence": [{"text": "mail me at someone@example.org"}]}), "b" * 64)).fetchone()[0] == "failed"
        rt.rollback()
    process_pending(Settings())
    assert state(rc, hd)["state"] == "not_eligible" and contribution_of(base_settings, demo) is None


def test_private_profiles_never_count_and_small_real_cohorts_are_suppressed(rc, users, base_settings):
    with db.connect(base_settings) as conn:
        before = len(load_rows(conn, "consented_real")[0]["profiles"])
    u, h = ready_user(rc, users)
    confirm_all(rc, h)                                                            # reviewed, but no contribution consent
    process_pending(Settings())
    with db.connect(base_settings) as conn:
        assert len(load_rows(conn, "consented_real")[0]["profiles"]) == before
    v, hv = ready_user(rc, users)
    contribute(rc, hv)
    confirm_all(rc, hv)
    process_pending(Settings())
    body = rc.get("/api/v1/insights?target=PM-PM&cohort=consented_real", headers=hv).json()
    assert body["status"] in ("insufficient", "not_ready") and body["sections"] == [] and body["eligible_profiles"] is None
    listed = {x["cohort"]: x for x in rc.get("/api/v1/cohorts", headers=hv).json()}
    assert listed["consented_real"]["eligible_profiles"] is None


def test_contribution_functions_are_not_reachable_by_clients(base_settings):
    with db.connect(base_settings) as conn:
        for role in ("anon", "authenticated", "service_role"):
            assert not conn.execute("select has_schema_privilege(%s, 'trace_contrib', 'usage')", (role,)).fetchone()[0], role
        for fn, role, expected in (("trace_contrib.record_contribution_outcome(uuid,timestamptz,text,text,jsonb,text,jsonb,text)", "trace_app_user", False),
                                   ("trace_contrib.my_contribution_status()", "trace_app_runtime", False),
                                   ("trace_contrib.my_contribution_status()", "trace_app_user", True),
                                   ("trace_contrib.claim_contribution_requests(integer,integer,integer,integer,integer)", "trace_app_runtime", True)):
            assert conn.execute("select has_function_privilege(%s, %s, 'execute')", (role, fn)).fetchone()[0] is expected, (fn, role)


# --------------------------------------------------------------------------- queue robustness and the worker
def test_claims_are_exclusive_and_leases_expire(rc, users, base_settings):
    u, h = ready_user(rc, users)
    contribute(rc, h)
    with db.connect_runtime(base_settings) as a, db.connect_runtime(base_settings) as b:
        with a.transaction():
            got_a = {r[0] for r in a.execute("select user_id from trace_contrib.claim_contribution_requests(100, 600, 5, 30, 3600)").fetchall()}
        with b.transaction():
            got_b = {r[0] for r in b.execute("select user_id from trace_contrib.claim_contribution_requests(100, 600, 5, 30, 3600)").fetchall()}
    assert u in got_a and u not in got_b                                         # leased: no double processing
    with db.connect(base_settings) as admin:
        admin.execute("update trace_private.contribution_requests set locked_until = now() - interval '1 second' where user_id = %s", (u,))
        admin.commit()
    with db.connect_runtime(base_settings) as b:
        with b.transaction():                                                    # the expired attempt counts; backoff first
            assert u not in {r[0] for r in b.execute("select user_id from trace_contrib.claim_contribution_requests(100, 600, 5, 30, 3600)").fetchall()}
    assert admin_row(base_settings, "select count(*) from trace_private.contribution_requests where user_id = %s", u)[0] == 1   # not lost
    with db.connect(base_settings) as admin:
        admin.execute("update trace_private.contribution_requests set next_attempt_at = now() - interval '1 second' where user_id = %s", (u,))
        admin.commit()
    with db.connect_runtime(base_settings) as b:
        with b.transaction():
            assert u in {r[0] for r in b.execute("select user_id from trace_contrib.claim_contribution_requests(100, 600, 5, 30, 3600)").fetchall()}


def test_the_background_worker_drains_the_queue(rc, users, base_settings, monkeypatch):
    from app.services.contributions.worker import ContributionWorker
    u, h = ready_user(rc, users, contributor=True)
    contribute(rc, h)
    confirm_all(rc, h)
    monkeypatch.setattr(worker_module, "PAUSE_LOCK", PRIVATE_PAUSE_LOCK)             # not the session's pause lock
    worker = ContributionWorker(Settings(), 1)
    worker.start()
    try:
        deadline = time.time() + 20
        while time.time() < deadline and state(rc, h)["state"] != "eligible":
            time.sleep(0.5)
    finally:
        worker.stop()
    assert state(rc, h)["state"] == "eligible"


def test_deleting_an_account_erases_its_contribution(rc, users, base_settings):
    u, h = ready_user(rc, users, contributor=True)
    contribute(rc, h)
    confirm_all(rc, h)
    process_pending(Settings())
    _, pid, _ = contribution_of(base_settings, u)
    with db.connect(base_settings) as admin:
        admin.execute("delete from auth.users where id = %s", (u,))
        admin.commit()
        assert admin.execute("select count(*) from trace_ref.profiles where profile_id = %s", (pid,)).fetchone()[0] == 0
        assert admin.execute("select count(*) from trace_private.contributors where user_id = %s", (u,)).fetchone()[0] == 0


def test_no_test_contributions_remain(base_settings):
    with db.connect(base_settings) as conn:
        assert conn.execute("select count(*) from trace_ref.profiles where dataset_class = 'consented_real_reference'").fetchone()[0] == 0
        assert conn.execute("select count(*) from trace_private.contributors").fetchone()[0] == 0
        assert conn.execute("select count(*) from trace_private.resume_quarantine").fetchone()[0] == 0


# --------------------------------------------------------------------------- human review
def test_a_reviewer_can_approve_a_quarantined_resume(rc, users, base_settings, capsys):
    from scripts import review_quarantine
    u, h = candidate(rc, users)
    assert post_real(rc, h, resume(employer2="Kulkarni & Sons Logistics")).status_code == 202
    qid = admin_row(base_settings, "select quarantine_id from trace_private.resume_quarantine where owner_user_id = %s", u)[0]
    assert review_quarantine.main(["approve-resume", str(qid)]) == 1                       # a named reviewer is required
    assert review_quarantine.main(["approve-resume", str(qid), "--reviewer", "Privacy reviewer"]) == 0
    assert len(rc.get("/api/v1/profile/records", headers=h).json()["employments"]) == 2
    assert admin_row(base_settings, "select status, record from trace_private.resume_quarantine where quarantine_id = %s", qid) == ("approved", {})
    assert rc.get("/api/v1/real-resumes/status", headers=h).json()["status"] == "approved"
    assert review_quarantine.main(["approve-resume", str(qid), "--reviewer", "x"]) == 1                     # once only


def test_a_reviewer_approves_a_quarantined_contribution_for_exactly_that_content(rc, users, base_settings):
    from scripts import review_quarantine
    u, h = candidate(rc, users)
    assert post_real(rc, h, resume(employer2="Kulkarni & Sons Logistics")).status_code == 202
    qid = admin_row(base_settings, "select quarantine_id from trace_private.resume_quarantine where owner_user_id = %s", u)[0]
    assert review_quarantine.main(["approve-resume", str(qid), "--reviewer", "Privacy reviewer"]) == 0
    contribute(rc, h)
    confirm_all(rc, h)
    process_pending(Settings())
    assert state(rc, h)["state"] == "quarantined" and contribution_of(base_settings, u) is None   # still ambiguous: no auto-admit
    assert review_quarantine.main(["approve-contribution", str(u), "--reviewer", "Privacy reviewer"]) == 0
    process_pending(Settings())
    assert state(rc, h)["state"] == "eligible" and contribution_of(base_settings, u) is not None
    ev = rc.get("/api/v1/profile/records", headers=h).json()["employments"][0]["evidence"][0]
    rc.post(f"/api/v1/records/evidence/{ev['id']}/corrections", headers=h, json={"evidence_text": "Ran logistics analytics for regional hubs"})
    process_pending(Settings())
    assert state(rc, h)["state"] == "quarantined" and contribution_of(base_settings, u) is None   # new content: review again


def test_pending_items_hold_back_the_contribution_and_rejected_items_never_contribute(rc, users, base_settings):
    u, h = ready_user(rc, users, contributor=True)
    contribute(rc, h)
    view = rc.get("/api/v1/profile/records", headers=h).json()
    for e in view["employments"]:                                               # jobs confirmed, evidence still pending
        assert rc.post(f"/api/v1/records/employment/{e['id']}/confirm", headers=h).status_code == 200
    process_pending(Settings())
    assert state(rc, h)["state"] == "awaiting_review" and contribution_of(base_settings, u) is None
    rejected = view["employments"][0]["evidence"][0]
    assert rc.post(f"/api/v1/records/evidence/{rejected['id']}/reject", headers=h).status_code == 200
    confirm_all(rc, h)
    process_pending(Settings())
    _, pid, _ = contribution_of(base_settings, u)
    texts = admin_row(base_settings, "select array_agg(evidence_text_redacted) from trace_ref.experience_evidence where profile_id = %s", pid)[0]
    assert rejected["evidence_text"][:30] not in " ".join(texts) and len(texts) == 3


def test_a_contributions_own_ambiguity_needs_approval_of_exactly_that_content(rc, users, base_settings):
    from scripts import review_quarantine
    u, h = candidate(rc, users)
    with db.connect(base_settings) as admin:                                     # an account that carries the owner's name
        admin.execute("update auth.users set raw_user_meta_data = '{\"full_name\": \"Meera Kulkarni\"}' where id = %s", (u,))
        admin.commit()
    assert post_real(rc, h, resume(employer2="Kulkarni & Sons Logistics")).status_code == 202
    qid = admin_row(base_settings, "select quarantine_id from trace_private.resume_quarantine where owner_user_id = %s", u)[0]
    assert review_quarantine.main(["approve-resume", str(qid), "--reviewer", "Privacy reviewer"]) == 0
    contribute(rc, h)
    confirm_all(rc, h)
    process_pending(Settings())
    assert state(rc, h)["state"] == "quarantined"
    assert review_quarantine.main(["approve-contribution", str(u), "--reviewer", "Privacy reviewer"]) == 0
    process_pending(Settings())
    assert state(rc, h)["state"] == "eligible"
