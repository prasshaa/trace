"""Phase 6.2: identity-marker continuity during profile edits, and bounded contribution retries.

Integration tests against the local stack. Users are transient (deleted at teardown, which cascades to their markers,
queue rows, contributions and reference rows). Every name and contact detail is an invented test value. Test accounts
are email/password style: auth.users rows with an email and NO name in their metadata.
"""
import json
import threading

import pytest

from app import db
from app.config import Settings
from app.services.contributions import process_pending, processor
from app.services.contributions import worker as worker_module
from app.services.contributions.worker import ContributionWorker
PRIVATE_PAUSE_LOCK = 7_310_412_099         # the session holds the real pause lock (conftest)
from tests.test_api_foundation import base_settings, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import candidate, consent_document  # noqa: F401
from tests.test_deidentification import make_pdf
from tests.test_real_resume_contributions import (  # noqa: F401  (fixtures and helpers)
    admin_row, confirm_all, contribute, contribution_document, contribution_of, post_real, rc, ready_user, resume, state,
)

pytestmark = pytest.mark.integration

# Settings for the retry tests: three attempts, 10 s base backoff capped at 25 s.
FAST = dict(trace_contribution_max_attempts=3, trace_contribution_backoff_base_seconds=10,
            trace_contribution_backoff_max_seconds=25)
MARKERS = ("Meera", "Kulkarni", "meera", "Méera", "Kulkarnii", "example.com", "98200", "meerakulkarni", "meerak")
REFERENCE_TABLES = ("profiles", "employers", "employments", "education", "experience_evidence", "profile_skills", "activities",
                    "career_breaks")


def fast():
    return Settings(**FAST)


def reference_dump(base_settings) -> str:
    """Every consented-real reference row, as text."""
    with db.connect(base_settings) as conn:
        return json.dumps([conn.execute(f"select to_jsonb(t) from trace_ref.{t} t where profile_id like 'CRR-%%'").fetchall()
                           for t in REFERENCE_TABLES], default=str)


def eligible_ids(base_settings) -> set:
    with db.connect(base_settings) as conn:
        return {r[0] for r in conn.execute("select profile_id from trace_ref.v_eligible_profiles").fetchall()}


def reason_of(base_settings, u):
    return admin_row(base_settings, "select state, reason from trace_private.user_contribution_status where user_id = %s", u)


def request_of(base_settings, u):
    """(attempts, seconds until the next attempt, last error, terminally failed, leased) or None."""
    return admin_row(base_settings, "select attempts, extract(epoch from next_attempt_at - now()), last_error, failed_at is not null, "
                                    "locked_until is not null from trace_private.contribution_requests where user_id = %s", u)


def make_due(base_settings, u):
    with db.connect(base_settings) as admin:
        admin.execute("update trace_private.contribution_requests set next_attempt_at = now() - interval '1 second' "
                      "where user_id = %s and next_attempt_at is not null", (u,))
        admin.commit()


def expire_lease(base_settings, u):
    with db.connect(base_settings) as admin:
        admin.execute("update trace_private.contribution_requests set locked_until = now() - interval '1 second' where user_id = %s", (u,))
        admin.commit()


def events(base_settings, u):
    with db.connect(base_settings) as conn:
        return [tuple(r) for r in conn.execute("select event, attempts, error_code, actor from trace_private.contribution_queue_events "
                                               "where user_id = %s order by event_id", (u,)).fetchall()]


def correct_first_evidence(rc, h, text):
    ev = rc.get("/api/v1/profile/records", headers=h).json()["employments"][0]["evidence"][0]
    assert rc.post(f"/api/v1/records/evidence/{ev['id']}/corrections", headers=h, json={"evidence_text": text}).status_code == 201
    return ev


def included_user(rc, users, base_settings):
    u, h = ready_user(rc, users, contributor=True)
    contribute(rc, h)
    confirm_all(rc, h)
    process_pending(Settings())
    assert state(rc, h)["state"] == "eligible"
    return u, h, contribution_of(base_settings, u)


def opened_markers(base_settings, u) -> dict:
    """The user's stored markers, opened with the test session's key (the database alone cannot read them)."""
    from app.services.contributions.sealing import sealer_from
    sealed, key_id, legacy = admin_row(base_settings, "select sealed, key_id, cardinality(names) + cardinality(emails) + "
                                       "cardinality(phones) + cardinality(urls) + cardinality(handles) "
                                       "from trace_private.identity_markers where owner_user_id = %s", u)
    assert legacy == 0                                                            # never stored as plaintext
    return sealer_from(Settings()).open(u, sealed, key_id)


def account_has_no_name(base_settings, u) -> bool:
    meta = admin_row(base_settings, "select coalesce(raw_user_meta_data, '{}'::jsonb) from auth.users where id = %s", u)[0]
    return not any(meta.get(k) for k in ("full_name", "name", "preferred_username"))


# =========================================================================== 1. identity-marker continuity
EDITS = {   # edited free text -> the review reason expected
    "full_name": ("Meera Kulkarni ran the quarterly cards roadmap review", "new_identifier_in_edit"),
    "given_name_lower_case": ("Presented the cards roadmap with meera to leadership", "new_identifier_in_edit"),
    "accented_spelling": ("Méera presented the cards roadmap to leadership", "new_identifier_in_edit"),
    "near_spelling": ("Kulkarnii presented the cards roadmap to leadership", "manual_review_required"),
    "email": ("Owned the cards roadmap; write to meera.k@example.com", "new_identifier_in_edit"),
    "phone": ("Owned the cards roadmap; call +91 98200 11223 for details", "new_identifier_in_edit"),
}


@pytest.mark.parametrize("edit", sorted(EDITS))
def test_an_identifier_typed_into_an_edit_never_reaches_the_reference_pool(rc, users, base_settings, edit):
    text, expected_reason = EDITS[edit]
    u, h = candidate(rc, users)
    assert account_has_no_name(base_settings, u)                                  # email/password account, no name
    contribute(rc, h)                                                             # 1. contribution consent (markers kept, D48)
    r = post_real(rc, h, resume())                                                # 2. upload
    assert (r.status_code, r.json()["status"]) == (201, "processed")              # 3. passed de-identification
    confirm_all(rc, h)
    process_pending(Settings())
    assert state(rc, h)["state"] == "eligible"                                    # 4. eligible
    cid, pid, _ = contribution_of(base_settings, u)
    assert pid in eligible_ids(base_settings)
    correct_first_evidence(rc, h, text)                                           # 5. the name (or contact) typed into an edit
    assert state(rc, h)["state"] == "pending"
    process_pending(Settings())                                                   # 6. rebuild
    dump = reference_dump(base_settings)                                          # 7. never in the reference pool
    for value in MARKERS:
        assert value not in dump, value
    assert "cards roadmap" not in dump                                            # the edited record is not there at all
    assert pid not in eligible_ids(base_settings)
    assert admin_row(base_settings, "select count(*) from trace_ref.profiles where profile_id = %s", pid)[0] == 0
    s = state(rc, h)                                                              # 8. held for a person
    assert (s["state"], s["included"]) == ("quarantined", False)
    assert reason_of(base_settings, u) == ("quarantined", expected_reason)
    assert admin_row(base_settings, "select status, profile_id from trace_private.contributions where contribution_id = %s", cid) == ("suspended", None)
    assert contribution_of(base_settings, u) is None


def test_a_reviewer_approval_admits_only_the_redacted_text(rc, users, base_settings):
    from scripts import review_quarantine
    u, h, (cid, pid, _) = included_user(rc, users, base_settings)
    correct_first_evidence(rc, h, "Meera Kulkarni ran the quarterly cards roadmap review")
    process_pending(Settings())
    assert reason_of(base_settings, u) == ("quarantined", "new_identifier_in_edit")
    assert review_quarantine.main(["approve-contribution", str(u), "--reviewer", "Privacy reviewer"]) == 0
    process_pending(Settings())
    assert state(rc, h)["state"] == "eligible"
    _, new_pid, _ = contribution_of(base_settings, u)
    assert new_pid != pid                                                          # suspended earlier: a fresh contribution
    dump = reference_dump(base_settings)
    assert "[name removed] ran the quarterly cards roadmap review" in dump
    for value in MARKERS:
        assert value not in dump, value
    correct_first_evidence(rc, h, "Meera Kulkarni ran the yearly cards roadmap review")      # changed again: review again
    process_pending(Settings())
    assert reason_of(base_settings, u) == ("quarantined", "new_identifier_in_edit") and contribution_of(base_settings, u) is None


def test_identity_markers_are_kept_only_in_the_restricted_store(rc, users, base_settings):
    u, h = candidate(rc, users)
    contribute(rc, h)                                                             # markers are kept only for a contributor (D48)
    assert post_real(rc, h, resume()).status_code == 201
    raw = bytes(admin_row(base_settings, "select sealed from trace_private.identity_markers where owner_user_id = %s", u)[0])
    for value in (b"Meera", b"Kulkarni", b"example.com", b"98200", b"meerak", b"api-"):
        assert value not in raw                                                   # the database holds ciphertext only
    stored = opened_markers(base_settings, u)
    names, emails, phones, urls = stored["names"], stored["emails"], stored["phones"], stored["urls"]
    assert "Meera Kulkarni" in names                                              # from the resume header
    assert any(e.startswith("api-") for e in emails) and any("example.com" in e for e in emails)   # account and resume
    assert phones and any("meerak" in x for x in urls)                            # resume contact line and PDF link
    with db.connect(base_settings) as conn:
        for role in ("anon", "authenticated", "service_role", "trace_app_runtime", "trace_app_user", "trace_reference_reader"):
            assert not conn.execute("select has_table_privilege(%s, 'trace_private.identity_markers', 'select')", (role,)).fetchone()[0], role
            assert not conn.execute("select has_schema_privilege(%s, 'trace_private', 'usage')", (role,)).fetchone()[0], role
        for role, expected in (("trace_app_runtime", True), ("trace_app_user", False), ("authenticated", False), ("anon", False)):
            assert conn.execute("select has_function_privilege(%s, 'trace_contrib.store_identity_markers(bytea,text)', "
                                "'execute')", (role,)).fetchone()[0] is expected, role
    contribute(rc, h)
    confirm_all(rc, h)
    process_pending(Settings())
    cid, pid, _ = contribution_of(base_settings, u)
    report = admin_row(base_settings, "select deidentification_report::text from trace_private.contributions where contribution_id = %s", cid)[0]
    for value in ("Meera", "Kulkarni", "example.com", "98200", "meerak", "api-"):
        assert value not in report and value not in pid, value                     # never in a report or an id
    body = rc.get("/api/v1/contribution", headers=h).text + rc.get("/api/v1/profile/records", headers=h).text
    for value in ("Meera", "Kulkarni", "example.com", "98200", "meerak"):
        assert value not in body, value
    with db.connect(base_settings) as admin:                                      # erased with the account
        admin.execute("delete from auth.users where id = %s", (u,))
        admin.commit()
    assert admin_row(base_settings, "select count(*) from trace_private.identity_markers where owner_user_id = %s", u)[0] == 0


def test_another_user_cannot_write_markers_for_a_profile_they_do_not_own(rc, users, base_settings):
    import psycopg
    from app.services.candidate_profile.repository import IDENTITY_SQL
    stranger = users()                                                            # an account without any profile
    with db.connect_runtime(base_settings) as rt:
        with pytest.raises(psycopg.Error, match="own non-fixture profile"):
            with rt.transaction():
                rt.execute(IDENTITY_SQL, {"sub": str(stranger), "claims": json.dumps({"sub": str(stranger), "role": "authenticated"})})
                rt.execute("select trace_contrib.store_identity_markers(%s, %s)", (b"\x00" * 40, "0" * 16))


def test_without_recorded_markers_a_contribution_waits_for_review(rc, users, base_settings):
    u, h = ready_user(rc, users)                                                  # e.g. a profile processed before markers existed
    with db.connect(base_settings) as admin:
        admin.execute("delete from trace_private.identity_markers where owner_user_id = %s", (u,))
        admin.commit()
    contribute(rc, h)
    confirm_all(rc, h)
    process_pending(Settings())
    assert reason_of(base_settings, u) == ("quarantined", "identity_markers_missing") and contribution_of(base_settings, u) is None


def test_no_known_name_anywhere_means_review_not_admission(rc, users, base_settings):
    u, h = candidate(rc, users)
    nameless = make_pdf(["meera.k@example.com | +91 98200 11223", "EXPERIENCE", "Product Manager, Zentrix Software Apr 2019 - Present",
                         "- Owned the payments roadmap and launched UPI autopay for 2M users",
                         "EDUCATION", "B.E. Computer Engineering, College of Engineering Pune, 2016, CGPA 8.4/10"])
    contribute(rc, h)
    assert post_real(rc, h, nameless).status_code == 201
    assert opened_markers(base_settings, u)["names"] == []
    confirm_all(rc, h)
    process_pending(Settings())
    assert reason_of(base_settings, u) == ("quarantined", "identity_markers_missing") and contribution_of(base_settings, u) is None


def test_a_name_added_to_the_account_later_is_also_used(rc, users, base_settings):
    u, h, _ = included_user(rc, users, base_settings)
    with db.connect(base_settings) as admin:
        admin.execute("update auth.users set raw_user_meta_data = '{\"full_name\": \"Asha Rao\"}' where id = %s", (u,))
        admin.commit()
    correct_first_evidence(rc, h, "Asha Rao reviewed the cards roadmap each quarter")
    process_pending(Settings())
    assert reason_of(base_settings, u) == ("quarantined", "new_identifier_in_edit")
    assert "Asha" not in reference_dump(base_settings)


def test_employer_and_university_names_are_still_retained(rc, users, base_settings):
    u, h, (_, pid, _) = included_user(rc, users, base_settings)
    correct_first_evidence(rc, h, "Led payments analytics at Zentrix Software with the College of Engineering Pune alumni cell")
    process_pending(Settings())
    assert state(rc, h)["state"] == "eligible" and contribution_of(base_settings, u)[1] == pid          # no identifier: updated in place
    dump = reference_dump(base_settings)
    for kept in ("Zentrix Software", "College of Engineering Pune", "Led payments analytics", "8.4", "Northwind Logistics"):
        assert kept in dump, kept


# =========================================================================== 2. bounded retries
def boom(*a, **k):
    raise RuntimeError("processing failed for Meera Kulkarni meera.k@example.com")         # content must never be stored


def test_repeated_failures_back_off_then_stop_for_good_and_an_operator_can_retry(rc, users, base_settings, monkeypatch):
    from scripts import contribution_queue
    u, h, (cid, pid, _) = included_user(rc, users, base_settings)
    correct_first_evidence(rc, h, "Owned the cards roadmap across three markets")
    monkeypatch.setattr(processor, "process_user", boom)
    process_pending(fast())
    attempts, delay, error, failed, leased = request_of(base_settings, u)
    assert (attempts, error, failed, leased) == (1, "RuntimeError", False, False) and 8 <= delay <= 10.5      # base * 2^0
    s = state(rc, h)
    assert (s["state"], s["label"]) == ("pending", "Queued for another attempt.")
    process_pending(fast())
    assert request_of(base_settings, u)[0] == 1                                    # not due yet: backoff respected
    make_due(base_settings, u)
    process_pending(fast())
    attempts, delay, *_ = request_of(base_settings, u)
    assert attempts == 2 and 18 <= delay <= 20.5                                   # base * 2^1
    make_due(base_settings, u)
    process_pending(fast())                                                        # third and last attempt
    attempts, delay, error, failed, leased = request_of(base_settings, u)
    assert (attempts, delay, error, failed, leased) == (3, None, "RuntimeError", True, False)
    s = state(rc, h)
    assert (s["state"], s["included"]) == ("failed", False) and "several attempts" in s["label"]
    assert reason_of(base_settings, u) == ("failed", "retries_exhausted")
    assert admin_row(base_settings, "select status, profile_id from trace_private.contributions where contribution_id = %s", cid) == ("suspended", None)
    assert pid not in eligible_ids(base_settings)
    for _ in range(4):                                                             # never loops
        make_due(base_settings, u)
        process_pending(fast())
    assert request_of(base_settings, u)[0] == 3
    assert events(base_settings, u) == [("attempt_failed", 1, "RuntimeError", None), ("attempt_failed", 2, "RuntimeError", None),
                                        ("attempt_failed", 3, "RuntimeError", None), ("retries_exhausted", 3, "RuntimeError", None)]
    with db.connect(base_settings) as conn:                                        # only codes are kept, never content
        stored = json.dumps(conn.execute("select to_jsonb(r) from trace_private.contribution_requests r where user_id = %s", (u,)).fetchall()
                            + conn.execute("select to_jsonb(e) from trace_private.contribution_queue_events e where user_id = %s", (u,)).fetchall(),
                            default=str)
    assert "Meera" not in stored and "example.com" not in stored
    monkeypatch.undo()                                                             # the cause is fixed: still not retried by itself
    make_due(base_settings, u)
    process_pending(fast())
    assert state(rc, h)["state"] == "failed"
    # the explicit, protected retry
    with db.connect(base_settings) as conn:
        for role in ("trace_app_runtime", "trace_app_user", "authenticated", "anon", "service_role"):
            assert not conn.execute("select has_function_privilege(%s, 'trace_private.retry_contribution(uuid,text)', 'execute')",
                                    (role,)).fetchone()[0], role
    assert contribution_queue.main(["retry", str(u)]) == 1                         # a named operator is required
    assert contribution_queue.main(["retry", str(u), "--operator", "Queue operator"]) == 0
    assert request_of(base_settings, u)[:4] == (0, None, None, False)
    assert reason_of(base_settings, u) == ("pending", "manual_retry")
    process_pending(fast())
    assert state(rc, h)["state"] == "eligible"
    new_cid, new_pid, _ = contribution_of(base_settings, u)
    assert new_cid != cid and new_pid in eligible_ids(base_settings)
    assert request_of(base_settings, u) is None
    assert events(base_settings, u)[-1] == ("manual_retry", 0, None, "Queue operator")
    assert contribution_queue.main(["retry", str(u), "--operator", "Queue operator"]) == 1     # nothing left to retry


def test_a_profile_change_after_a_terminal_failure_starts_a_fresh_series(rc, users, base_settings, monkeypatch):
    u, h, _ = included_user(rc, users, base_settings)
    correct_first_evidence(rc, h, "Owned the cards roadmap across three markets")
    monkeypatch.setattr(processor, "process_user", boom)
    for _ in range(3):
        process_pending(fast())
        make_due(base_settings, u)
    assert request_of(base_settings, u)[3] is True
    monkeypatch.undo()
    correct_first_evidence(rc, h, "Owned the cards roadmap across four markets")   # the user's own change
    assert request_of(base_settings, u)[:4] == (0, None, None, False)
    process_pending(fast())
    assert state(rc, h)["state"] == "eligible"


def test_transient_failures_recover_and_reset_the_series(rc, users, base_settings, monkeypatch):
    u, h = ready_user(rc, users, contributor=True)
    contribute(rc, h)
    confirm_all(rc, h)
    real, calls = processor.process_user, []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) <= 2:
            raise ConnectionError("temporary")
        return real(*a, **k)
    monkeypatch.setattr(processor, "process_user", flaky)
    process_pending(fast())
    make_due(base_settings, u)
    process_pending(fast())
    assert request_of(base_settings, u)[:3][0::2] == (2, "ConnectionError")
    make_due(base_settings, u)
    process_pending(fast())
    assert state(rc, h)["state"] == "eligible" and contribution_of(base_settings, u) is not None
    assert request_of(base_settings, u) is None                                     # done: the request is gone


def test_an_expired_lease_counts_as_an_attempt_and_the_work_is_not_lost(rc, users, base_settings):
    u, h = ready_user(rc, users, contributor=True)
    contribute(rc, h)
    confirm_all(rc, h)

    def crash_mid_attempt():                                                       # a worker claims, then its process dies
        with db.connect_runtime(base_settings) as rt:
            with rt.transaction():
                got = rt.execute("select user_id from trace_contrib.claim_contribution_requests(100, 600, 3, 10, 25)").fetchall()
        assert u in {r[0] for r in got}
    crash_mid_attempt()
    assert request_of(base_settings, u)[4] is True and state(rc, h)["state"] == "processing"
    process_pending(fast())
    assert request_of(base_settings, u)[:1] == (1,) and request_of(base_settings, u)[4] is True    # leased: not taken over
    expire_lease(base_settings, u)
    process_pending(fast())
    attempts, delay, error, failed, leased = request_of(base_settings, u)
    assert (attempts, error, failed, leased) == (1, "lease_expired", False, False) and 8 <= delay <= 10.5
    assert ("lease_expired", 1, "lease_expired", None) in events(base_settings, u)
    make_due(base_settings, u)
    process_pending(fast())                                                        # recovered by the next pass
    assert state(rc, h)["state"] == "eligible" and request_of(base_settings, u) is None


def test_repeated_crashes_end_in_the_terminal_state(rc, users, base_settings):
    u, h = ready_user(rc, users)
    contribute(rc, h)
    confirm_all(rc, h)
    for _ in range(3):
        with db.connect_runtime(base_settings) as rt:
            with rt.transaction():
                rt.execute("select user_id from trace_contrib.claim_contribution_requests(100, 600, 3, 10, 25)").fetchall()
        expire_lease(base_settings, u)
        process_pending(fast())
        make_due(base_settings, u)
    assert request_of(base_settings, u)[2:4] == ("lease_expired", True)
    assert reason_of(base_settings, u) == ("failed", "retries_exhausted")
    make_due(base_settings, u)
    process_pending(fast())
    assert request_of(base_settings, u)[0] == 3                                    # never claimed again


def test_a_graceful_stop_hands_claims_back_and_a_restarted_worker_finishes_them(rc, users, base_settings, monkeypatch):
    u, h = ready_user(rc, users, contributor=True)
    contribute(rc, h)
    confirm_all(rc, h)
    stopped = threading.Event()
    stopped.set()
    counts = process_pending(fast(), stop=stopped)                                  # shutting down before starting any
    assert counts.get("released", 0) >= 1
    assert request_of(base_settings, u)[:5] == (0, None, None, False, False)       # not counted as an attempt
    assert state(rc, h)["state"] == "pending"
    assert any(e[0] == "released" for e in events(base_settings, u))
    monkeypatch.setattr(worker_module, "PAUSE_LOCK", PRIVATE_PAUSE_LOCK)             # not the session's pause lock
    worker = ContributionWorker(fast(), 1)                                          # the "restarted" process
    worker.start()
    try:
        import time
        deadline = time.time() + 20
        while time.time() < deadline and state(rc, h)["state"] != "eligible":
            time.sleep(0.5)
    finally:
        worker.stop()
    assert state(rc, h)["state"] == "eligible" and request_of(base_settings, u) is None


def test_a_stale_lease_token_cannot_change_a_request(rc, users, base_settings):
    import uuid
    u, h = ready_user(rc, users)
    contribute(rc, h)
    with db.connect_runtime(base_settings) as rt:
        with rt.transaction():
            assert rt.execute("select trace_contrib.report_contribution_failure(%s, %s, 'RuntimeError', 3, 10, 25)",
                              (u, uuid.uuid4())).fetchone()[0] == "stale"
    assert request_of(base_settings, u)[:4] == (0, None, None, False)


def test_retry_settings_are_bounded():
    from pydantic import ValidationError
    for bad in ({"trace_contribution_max_attempts": 0}, {"trace_contribution_max_attempts": 21},
                {"trace_contribution_backoff_base_seconds": 0}, {"trace_contribution_lease_seconds": 5}):
        with pytest.raises(ValidationError):
            Settings(**bad)
    assert Settings().trace_contribution_max_attempts == 5


def test_no_test_contribution_state_remains(base_settings):
    # Scoped to the transient test users (api-<uuid>@example.invalid): the local database may also hold real review
    # accounts, whose data the tests never count, change or delete.
    test_user = "in (select id from auth.users where email like 'api-%%@example.invalid')"
    with db.connect(base_settings) as conn:
        assert conn.execute("select count(*) from auth.users where email like 'api-%%@example.invalid'").fetchone()[0] == 0
        assert conn.execute(f"select count(*) from trace_ref.profiles where dataset_class = 'consented_real_reference' "
                            f"and contributor_user_id {test_user}").fetchone()[0] == 0
        assert conn.execute(f"select count(*) from trace_private.identity_markers where owner_user_id {test_user}").fetchone()[0] == 0
        assert conn.execute(f"select count(*) from trace_private.contribution_requests where user_id {test_user}").fetchone()[0] == 0


def test_an_upload_that_needed_review_keeps_needing_review_even_if_markers_miss_it(rc, users, base_settings):
    from scripts import review_quarantine
    u, h = candidate(rc, users)
    contribute(rc, h)                                                             # markers are kept only for a contributor (D48)
    assert post_real(rc, h, resume(employer2="Kulkarni & Sons Logistics")).status_code == 202
    qid = admin_row(base_settings, "select quarantine_id from trace_private.resume_quarantine where owner_user_id = %s", u)[0]
    assert review_quarantine.main(["approve-resume", str(qid), "--reviewer", "Privacy reviewer"]) == 0
    from app.services.contributions.sealing import sealer_from
    sealer = sealer_from(Settings())
    with db.connect(base_settings) as admin:                                      # markers that no longer reveal the ambiguity
        admin.execute("update trace_private.identity_markers set sealed = %s, key_id = %s where owner_user_id = %s",
                      (sealer.seal(u, {"names": ["Unrelated Person"]}), sealer.key_id, u))
        admin.commit()
    confirm_all(rc, h)
    process_pending(Settings())
    assert reason_of(base_settings, u) == ("quarantined", "carried_over_review") and contribution_of(base_settings, u) is None
