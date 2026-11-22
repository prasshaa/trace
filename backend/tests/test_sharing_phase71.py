"""Phase 7.1: the onboarding data-use notice, the sharing preference (Profile settings), its separation from the explicit
contribution permission and from the actual status, and server-chosen reference sources (no synthetic in production).

Real-resume processing is exercised end to end against the local database with invented test resumes (as in Phase 6.1).
Users are transient and deleted at teardown.
"""
import pytest

from app import db
from app.config import Settings
from app.demo import consent as demo_consent
from app.services.career_insights import load_rows
from app.services.contributions import process_pending
from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import PDF, candidate, consent_document, make_client  # noqa: F401
from tests.test_real_resume_contributions import (admin_row, confirm_all, contribution_document, contribution_of,  # noqa: F401
                                                  rc, ready_user, state)

pytestmark = pytest.mark.integration
NOTICE = "trace-data-use-notice-v2"
SHARED_VERSION = demo_consent.CONTRIBUTION_VERSION


def sharing(c, h):
    r = c.get("/api/v1/sharing", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def acknowledge(c, h, version=NOTICE):
    return c.post("/api/v1/sharing/notice", headers=h, json={"notice_version": version})


def choose(c, h, mode, confirm=None):
    body = {"mode": mode} if confirm is None else {"mode": mode, "confirm_version": confirm}
    return c.put("/api/v1/sharing", headers=h, json=body)


def consent_events(base_settings, u):
    return admin_row(base_settings, "select count(*) from public.trace_consent_events where user_id = %s "
                                    "and purpose = 'deidentified_shared_dataset'", u)[0]


# --------------------------------------------------------------------------- onboarding notice
def test_the_notice_records_the_default_preference_and_never_a_consent(rc, users, base_settings):
    u, h = candidate(rc, users)
    first = sharing(rc, h)
    assert (first["preference"], first["notice_acknowledged"], first["activation"]) == (None, False, "not_chosen")
    assert first["notice"]["version"] == NOTICE and first["notice"]["is_consent_record"] is False
    text = " ".join(first["notice"]["paragraphs"])
    assert "cannot promise complete anonymity" in text and "Your sharing preference is set to on by default" in text
    assert "won't be added to shared recommendations until you confirm sharing" in text          # the real sequence
    assert "is shared" not in text and "has been shared" not in text
    assert "anonymous" not in text.replace("anonymity", "")                     # no promise of anonymity
    assert acknowledge(rc, h, "trace-data-use-notice-v0").status_code == 409    # only the version shown
    r = acknowledge(rc, h)
    assert r.status_code == 200
    s = r.json()
    assert (s["preference"], s["preference_source"], s["notice_acknowledged"]) == ("shared", "onboarding_default", True)
    assert (s["explicit_permission"], s["activation"]) == (False, "confirmation_required")
    assert s["confirmation_notice"]["consent_version"] == SHARED_VERSION
    assert s["status"]["included"] is False
    assert consent_events(base_settings, u) == 0                                 # an acknowledgement, not a consent
    assert acknowledge(rc, h).json()["preference"] == "shared"                   # idempotent
    assert admin_row(base_settings, "select count(*) from trace_private.sharing_preference_events where user_id = %s", u)[0] == 1


def test_the_default_preference_alone_never_shares_a_profile(rc, users, base_settings):
    u, h = ready_user(rc, users)
    confirm_all(rc, h)
    acknowledge(rc, h)
    process_pending(Settings())
    s = sharing(rc, h)
    assert (s["status"]["state"], s["status"]["included"], s["activation"]) == ("not_enabled", False, "confirmation_required")
    assert contribution_of(base_settings, u) is None
    assert admin_row(base_settings, "select count(*) from trace_private.contribution_requests where user_id = %s", u)[0] == 0


# --------------------------------------------------------------------------- settings: shared, private, shared again
def test_private_withdraws_keeps_personal_use_and_shared_again_reprocesses(rc, users, base_settings):
    u, h = ready_user(rc, users, contributor=True)
    confirm_all(rc, h)
    acknowledge(rc, h)
    r = choose(rc, h, "shared", SHARED_VERSION)                                  # the explicit settings action
    assert r.status_code == 200 and r.json()["explicit_permission"] is True and r.json()["activation"] == "active"
    assert r.json()["status"]["included"] is False                               # enabled is not included
    assert r.json()["status"]["state"] in ("pending", "processing")
    process_pending(Settings())
    s = sharing(rc, h)
    assert (s["status"]["state"], s["status"]["included"]) == ("eligible", True)
    old_cid, old_pid, _ = contribution_of(base_settings, u)

    r = choose(rc, h, "private")
    assert r.status_code == 200
    s = r.json()
    assert (s["preference"], s["activation"], s["explicit_permission"]) == ("private", "private", False)
    assert (s["status"]["state"], s["status"]["included"]) == ("withdrawn", False)
    assert admin_row(base_settings, "select count(*) from trace_ref.profiles where profile_id = %s", old_pid)[0] == 0   # same request
    with db.connect(base_settings) as conn:
        assert old_pid not in {p["profile_id"] for p in load_rows(conn, "consented_real")[0]["profiles"]}
    assert admin_row(base_settings, "select status::text from public.trace_consent_events where user_id = %s and purpose = "
                                    "'deidentified_shared_dataset' order by recorded_at desc limit 1", u)[0] == "withdrawn"
    assert rc.get("/api/v1/consent/resume-processing", headers=h).json()["active"] is True              # personal use continues
    assert len(rc.get("/api/v1/profile/records", headers=h).json()["employments"]) == 2
    assert rc.get("/api/v1/pathway?target=PM-SPM", headers=h).status_code == 200

    assert acknowledge(rc, h).json()["preference"] == "private"                  # the notice never turns it back on
    edit = rc.get("/api/v1/profile/records", headers=h).json()["employments"][0]["evidence"][0]
    assert rc.post(f"/api/v1/records/evidence/{edit['id']}/corrections", headers=h,
                   json={"evidence_text": "Owned the cards roadmap across three markets"}).status_code == 201
    process_pending(Settings())
    assert contribution_of(base_settings, u) is None                             # a profile change re-adds nothing
    legacy = rc.post("/api/v1/consent/contribution", headers=h, json={"decision": "granted", "consent_version": SHARED_VERSION})
    assert legacy.status_code == 200
    assert sharing(rc, h)["explicit_permission"] is False                        # private wins over any grant
    process_pending(Settings())
    assert contribution_of(base_settings, u) is None

    r = choose(rc, h, "shared", SHARED_VERSION)                                  # a clear action again
    assert r.json()["activation"] == "active" and r.json()["status"]["state"] in ("pending", "processing")
    process_pending(Settings())
    # Re-processed from the start, but the withdrawal deleted the identity markers (D48): without them the owner's name
    # can't be checked automatically, so the fresh contribution waits for a person; nothing old is reactivated.
    assert contribution_of(base_settings, u) is None
    assert sharing(rc, h)["status"]["state"] == "quarantined" and sharing(rc, h)["status"]["included"] is False


def test_shared_without_confirmation_records_the_preference_only(rc, users, base_settings):
    u, h = ready_user(rc, users)
    confirm_all(rc, h)
    r = choose(rc, h, "shared")
    assert r.status_code == 200
    assert (r.json()["preference"], r.json()["preference_source"], r.json()["explicit_permission"]) == ("shared", "settings", False)
    assert consent_events(base_settings, u) == 0
    process_pending(Settings())
    assert contribution_of(base_settings, u) is None


def test_confirmation_needs_the_current_notice_and_the_opt_in_policy(rc, users, base_settings):
    u, h = candidate(rc, users)
    assert choose(rc, h, "shared", "some-other-version").status_code == 409
    assert choose(rc, h, "everyone").status_code == 422
    assert consent_events(base_settings, u) == 0
    with make_client(base_settings, trace_demo_fixtures=True, trace_contribution_policy="disabled") as c:
        u2, h2 = candidate(c, users)
        # release (D52): where sharing isn't offered, the notice says so and continuing records the preference private
        assert acknowledge(c, h2).status_code == 409                              # the opt-in notice isn't the current one
        assert acknowledge(c, h2, sharing(c, h2)["notice"]["version"]).status_code == 200
        s = sharing(c, h2)
        assert (s["activation"], s["confirmation_notice"], s["preference"]) == ("private", None, "private")
        r = choose(c, h2, "shared", SHARED_VERSION)
        assert (r.status_code, r.json()["error"]) == (403, "contribution_not_offered")
        assert choose(c, h2, "private").status_code == 200                       # leaving is always possible


def test_sharing_is_owner_scoped_and_needs_a_token(rc, users, base_settings):
    assert rc.get("/api/v1/sharing").status_code == 401
    assert rc.put("/api/v1/sharing", json={"mode": "private"}).status_code == 401
    assert rc.post("/api/v1/sharing/notice", json={"notice_version": NOTICE}).status_code == 401
    a, ha = candidate(rc, users)
    b, hb = candidate(rc, users)
    assert choose(rc, ha, "private").status_code == 200
    assert sharing(rc, hb)["preference"] is None                                 # someone else's choice is invisible
    assert rc.put("/api/v1/sharing", headers=hb, json={"mode": "private", "user_id": str(a)}).status_code == 422
    assert sharing(rc, ha)["preference"] == "private"


# --------------------------------------------------------------------------- the reference source is the server's
def test_synthetic_demo_reads_the_synthetic_source_by_default(base_settings, consent_document, users):
    with make_client(base_settings, trace_demo_fixtures=True) as c:
        u, h = candidate(c, users, grant=False)
        body = c.get("/api/v1/insights?target=PM-PM", headers=h).json()
        assert (body["cohort"], body["is_synthetic"], body["data_mode"]) == ("synthetic", True, "synthetic_demo")


def test_production_serves_only_real_profiles_and_never_falls_back(base_settings, consent_document, users):
    with db.connect(base_settings) as conn:
        assert len(load_rows(conn, "consented_real")[0]["profiles"]) == 0        # the real pool is empty here
    with make_client(base_settings, trace_data_mode="production", trace_enabled_cohorts="synthetic,consented_real") as c:
        u, h = candidate(c, users, grant=False)
        assert [x["cohort"] for x in c.get("/api/v1/cohorts", headers=h).json()] == ["consented_real"]
        r = c.get("/api/v1/insights?target=PM-PM", headers=h)
        body = r.json()
        assert (body["cohort"], body["status"], body["sections"], body["is_synthetic"], body["data_mode"]) == \
            ("consented_real", "not_ready", [], False, "production")
        assert "SYN-" not in r.text and "synthetic" not in body["disclosure"].lower()
        p = c.get("/api/v1/pathway?target=PM-PM", headers=h)
        assert p.status_code == 200 and p.json()["cohort"] == "consented_real" and p.json()["routes"] == []
        assert p.json()["is_synthetic_test_data"] is not True and "SYN-" not in p.text
        for path in ("/api/v1/insights?target=PM-PM&cohort=synthetic", "/api/v1/pathway?target=PM-PM&cohort=synthetic"):
            r = c.get(path, headers=h)
            assert (r.status_code, r.json()["error"]) == (403, "cohort_not_enabled"), path
        assert c.get("/api/v1/demo/samples", headers=h).status_code == 403        # QA fixtures are not available
        r = c.post("/api/v1/resumes", headers={**h, "Content-Type": "application/pdf"}, content=PDF["SYN-001"])
        assert (r.status_code, r.json()["error"]) == (403, "demo_fixtures_disabled")              # an approved fixture, refused


def test_production_refuses_synthetic_test_fixtures(base_settings, consent_document, users):
    with make_client(base_settings, trace_data_mode="production", trace_demo_fixtures=True) as c:
        assert (c.get("/api/v1/config").status_code, c.get("/api/v1/config").json()["error"]) == (503, "data_mode_unsupported")
        u, h = candidate(c, users, grant=False)
        assert c.get("/api/v1/insights?target=PM-PM", headers=h).status_code == 503
        assert c.get("/api/v1/pathway?target=PM-PM", headers=h).status_code == 503


def test_the_browser_cannot_choose_the_source_or_the_mode(base_settings, consent_document, users):
    with make_client(base_settings, trace_data_mode="production") as c:
        u, h = candidate(c, users, grant=False)
        for extra in ({"data_mode": "synthetic_demo"}, {"dataset_class": "synthetic_test_fixture"}, {"source": "synthetic"}):
            body = c.get("/api/v1/insights", params={"target": "PM-PM", **extra}, headers={**h, "X-Data-Mode": "synthetic_demo"}).json()
            assert (body["cohort"], body["is_synthetic"]) == ("consented_real", False)


def test_no_test_rows_remain(base_settings):
    with db.connect(base_settings) as conn:
        assert conn.execute("select count(*) from trace_ref.profiles where dataset_class = 'consented_real_reference'").fetchone()[0] == 0
        assert conn.execute("select count(*) from trace_private.sharing_preferences s where not exists "
                            "(select 1 from auth.users u where u.id = s.user_id)").fetchone()[0] == 0
