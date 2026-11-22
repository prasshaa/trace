"""Phase 7 pre-flight: identity-marker encryption, marker lifecycle and queue-event retention.

Unit tests for the sealing module, and integration tests against the local stack (transient users; every name and
contact detail is an invented test value).
"""
import json
import uuid

import psycopg
import pytest

from app import db
from app.config import Settings
from app.services.contributions import process_pending
from app.services.contributions.sealing import (
    MarkerKeyInvalid, MarkerSealer, MarkersUnavailable, decode_key, new_key, sealer_from,
)
from tests.test_api_foundation import base_settings, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import candidate, consent_document  # noqa: F401
from tests.test_api_phase2 import make_client
from tests.test_real_resume_contributions import (  # noqa: F401  (fixtures and helpers)
    admin_row, confirm_all, contribute, contribution_document, contribution_of, post_real, rc, ready_user, resume, state,
)

MARKERS = {"names": ["Meera Kulkarni"], "emails": ["meera.k@example.com"], "phones": ["+91 98200 11223"], "urls": [], "handles": []}


# =========================================================================== sealing (unit)
def test_markers_round_trip_and_are_bound_to_their_owner():
    sealer = MarkerSealer(decode_key(new_key()))
    owner, other = uuid.uuid4(), uuid.uuid4()
    blob = sealer.seal(owner, MARKERS)
    assert b"Meera" not in blob and b"example.com" not in blob
    assert sealer.open(owner, blob, sealer.key_id)["names"] == ["Meera Kulkarni"]
    assert sealer.seal(owner, MARKERS) != blob                                   # a fresh nonce every time
    with pytest.raises(MarkersUnavailable):
        sealer.open(other, blob, sealer.key_id)                                  # moved to another user
    tampered = bytearray(blob)
    tampered[-1] ^= 1
    with pytest.raises(MarkersUnavailable):
        sealer.open(owner, bytes(tampered), sealer.key_id)
    with pytest.raises(MarkersUnavailable):
        MarkerSealer(decode_key(new_key())).open(owner, blob, sealer.key_id)    # another key
    with pytest.raises(MarkersUnavailable):
        sealer.open(owner, blob, "0" * 16)                                       # fingerprint mismatch


def test_keys_are_validated_without_ever_being_shown():
    for bad in ("", "short", "SENTINEL" * 3, new_key() + "AAAA"):
        with pytest.raises(MarkerKeyInvalid) as err:
            decode_key(bad)
        assert "SENTINEL" not in str(err.value)
    key = new_key()
    sealer = MarkerSealer(decode_key(key))
    assert key not in repr(sealer) and len(sealer.key_id) == 16
    assert sealer_from(Settings(trace_identity_marker_key=None)) is None
    s = Settings(trace_real_resume_uploads=True, trace_identity_marker_key="SENTINEL-not-a-key")
    assert s.real_resume_uploads_available is False                               # an invalid key counts as no key
    assert "SENTINEL" not in repr(s)
    assert Settings(trace_real_resume_uploads=True, trace_identity_marker_key="").real_resume_uploads_available is False
    assert Settings(trace_real_resume_uploads=False, trace_identity_marker_key=key).real_resume_uploads_available is False
    assert Settings(trace_real_resume_uploads=True, trace_identity_marker_key=key).real_resume_uploads_available is True


def test_marker_lists_are_bounded_and_normalised():
    from app.services.contributions.sealing import merge
    m = merge({"names": [" A B ", "A B", "", None]}, {"names": ["C"], "urls": ["u" * 400]}, {"names": [f"n{i}" for i in range(40)]})
    assert m["names"][:2] == ["A B", "C"] and len(m["names"]) == 16 and m["urls"] == []


# =========================================================================== real uploads need a key
def test_real_uploads_are_refused_and_not_offered_without_a_key(base_settings, consent_document, users):
    with make_client(base_settings, trace_demo_fixtures=True, trace_real_resume_uploads=True, trace_identity_marker_key=None) as c:
        assert c.get("/api/v1/config").json()["features"]["real_resume_uploads"] is False
        u, h = candidate(c, users)
        r = c.post("/api/v1/real-resumes", headers={**h, "Content-Type": "application/pdf"}, content=resume())
        assert (r.status_code, r.json()["error"]) == (403, "real_uploads_disabled")
        assert admin_row(base_settings, "select count(*) from trace_private.identity_markers where owner_user_id = %s", u)[0] == 0
    with make_client(base_settings, trace_demo_fixtures=True, trace_real_resume_uploads=True) as c:
        assert c.get("/api/v1/config").json()["features"]["real_resume_uploads"] is True


def test_a_wrong_key_at_processing_time_holds_the_contribution_for_review(rc, users, base_settings):
    u, h = ready_user(rc, users)
    contribute(rc, h)
    confirm_all(rc, h)
    process_pending(Settings(trace_identity_marker_key=new_key()))               # cannot open this user's markers
    assert admin_row(base_settings, "select state, reason from trace_private.user_contribution_status where user_id = %s", u) \
        == ("quarantined", "identity_markers_missing")
    assert contribution_of(base_settings, u) is None
    process_pending(Settings(trace_identity_marker_key=None))                     # nor without one
    assert contribution_of(base_settings, u) is None


# =========================================================================== marker lifecycle
def test_markers_are_forgotten_when_ingestion_fails_after_admission(rc, users, base_settings, monkeypatch):
    from app.services.candidate_profile import CandidateProfileService
    from app.services.candidate_profile.repository import CandidateProfileError
    u, h = candidate(rc, users)

    def broken(*a, **k):
        raise CandidateProfileError("simulated ingestion failure")
    monkeypatch.setattr(CandidateProfileService, "ingest_extraction", broken)
    r = post_real(rc, h, resume())
    assert r.status_code >= 400
    assert admin_row(base_settings, "select count(*) from trace_private.identity_markers where owner_user_id = %s", u)[0] == 0
    assert admin_row(base_settings, "select count(*) from trace_ingest.deidentified_resume_admissions where owner_user_id = %s", u)[0] == 0


def test_forgetting_never_removes_markers_an_admitted_resume_needs(rc, users, base_settings):
    from app.services.contributions.identity import forget_identity
    u, h = ready_user(rc, users, contributor=True)                                # kept only for a contributor (D48)
    with db.connect_runtime(base_settings) as rt:
        assert forget_identity(rt, u) is False
    assert admin_row(base_settings, "select count(*) from trace_private.identity_markers where owner_user_id = %s", u)[0] == 1
    contribute(rc, h, "withdrawn")                                                # no longer needed: deleted at once
    assert admin_row(base_settings, "select count(*) from trace_private.identity_markers where owner_user_id = %s", u)[0] == 0


def test_the_orphan_sweep_removes_only_unneeded_old_markers(rc, users, base_settings):
    from scripts import privacy_maintenance
    active, _ = ready_user(rc, users, contributor=True)                           # admitted resume, contributor: needed
    held, hh = candidate(rc, users)                                               # resume held for review, contributor: needed
    contribute(rc, hh)
    assert post_real(rc, hh, resume(employer2="Kulkarni & Sons Logistics")).status_code == 202
    orphan, ho = candidate(rc, users)                                             # markers with nothing that needs them
    sealer = sealer_from(Settings())
    profile = admin_row(base_settings, "select candidate_profile_id from public.trace_candidate_profiles where owner_user_id = %s", orphan)[0]
    with db.connect(base_settings) as admin:
        admin.execute("insert into trace_private.identity_markers (owner_user_id, candidate_profile_id, sealed, key_id) values (%s, %s, %s, %s)",
                      (orphan, profile, sealer.seal(orphan, MARKERS), sealer.key_id))
        admin.execute("update trace_private.identity_markers set updated_at = now() - interval '3 days' "
                      "where owner_user_id = any(%s)", ([active, held, orphan],))
        admin.commit()
        with pytest.raises(psycopg.Error, match="at least one hour"):
            admin.execute("select trace_private.purge_orphan_identity_markers(interval '5 minutes', true)")
        admin.rollback()
    assert privacy_maintenance.main(["orphan-markers", "--min-age-hours", "0", "--apply"]) == 1          # grace period enforced
    assert privacy_maintenance.main(["orphan-markers"]) == 0                                             # dry run
    assert admin_row(base_settings, "select count(*) from trace_private.identity_markers where owner_user_id = %s", orphan)[0] == 1
    assert privacy_maintenance.main(["orphan-markers", "--apply"]) == 0
    remaining = {r for r, in [(x,) for x in [active, held, orphan]]
                 if admin_row(base_settings, "select count(*) from trace_private.identity_markers where owner_user_id = %s", r)[0]}
    assert remaining == {active, held}
    with db.connect(base_settings) as admin:                                      # a young orphan is left alone (grace period)
        admin.execute("insert into trace_private.identity_markers (owner_user_id, candidate_profile_id, sealed, key_id) values (%s, %s, %s, %s)",
                      (orphan, profile, sealer.seal(orphan, MARKERS), sealer.key_id))
        admin.commit()
    assert privacy_maintenance.main(["orphan-markers", "--apply"]) == 0
    assert admin_row(base_settings, "select count(*) from trace_private.identity_markers where owner_user_id = %s", orphan)[0] == 1


def test_no_role_but_the_admin_can_run_maintenance_or_forget_for_others(base_settings):
    with db.connect(base_settings) as conn:
        for fn in ("trace_private.purge_orphan_identity_markers(interval,boolean)", "trace_private.purge_contribution_queue_events(integer,boolean)",
                   "trace_private.identity_markers_needed(uuid,uuid)"):
            for role in ("trace_app_runtime", "trace_app_user", "authenticated", "anon", "service_role"):
                assert not conn.execute("select has_function_privilege(%s, %s, 'execute')", (role, fn)).fetchone()[0], (fn, role)
        for role, expected in (("trace_app_runtime", True), ("trace_app_user", False), ("authenticated", False)):
            assert conn.execute("select has_function_privilege(%s, 'trace_contrib.forget_identity_markers()', 'execute')",
                                (role,)).fetchone()[0] is expected, role


# =========================================================================== queue-event retention
def test_queue_events_hold_no_content_and_old_ones_are_purged(rc, users, base_settings):
    from scripts import privacy_maintenance
    with db.connect(base_settings) as conn:
        cols = {r[0] for r in conn.execute("select column_name from information_schema.columns "
                                           "where table_schema = 'trace_private' and table_name = 'contribution_queue_events'").fetchall()}
    assert cols == {"event_id", "user_id", "event", "attempts", "error_code", "actor", "created_at"}
    u, h = ready_user(rc, users)
    with db.connect(base_settings) as admin:
        with pytest.raises(psycopg.errors.CheckViolation):                        # the pattern refuses messages and addresses
            admin.execute("insert into trace_private.contribution_queue_events (user_id, event, attempts, error_code) "
                          "values (%s, 'attempt_failed', 1, 'mail meera.k@example.com')", (u,))
        admin.rollback()
        admin.execute("insert into trace_private.contribution_queue_events (user_id, event, attempts, error_code, created_at) "
                      "values (%s, 'attempt_failed', 1, 'RuntimeError', now() - interval '200 days'), "
                      "(%s, 'attempt_failed', 2, 'RuntimeError', now() - interval '2 days')", (u, u))
        admin.commit()
        with pytest.raises(psycopg.Error, match="at least 7 days"):
            admin.execute("select trace_private.purge_contribution_queue_events(1, true)")
        admin.rollback()
    count = lambda: admin_row(base_settings, "select count(*) from trace_private.contribution_queue_events where user_id = %s", u)[0]  # noqa: E731
    assert privacy_maintenance.main(["queue-events"]) == 0 and count() == 2       # dry run
    assert privacy_maintenance.main(["queue-events", "--retention-days", "3"]) == 1 and count() == 2      # refused, even dry
    assert privacy_maintenance.main(["queue-events", "--retention-days", "1", "--apply"]) == 1      # below the floor: refused
    assert privacy_maintenance.main(["queue-events", "--apply"]) == 0 and count() == 1               # 90 days by default
    assert Settings().trace_queue_event_retention_days == 90


def test_unopenable_markers_hold_the_contribution_even_when_the_account_has_a_name(rc, users, base_settings):
    u, h = ready_user(rc, users)
    with db.connect(base_settings) as admin:                                     # the account name alone must not be enough
        admin.execute("update auth.users set raw_user_meta_data = '{\"full_name\": \"Asha Rao\"}' where id = %s", (u,))
        admin.commit()
    contribute(rc, h)
    confirm_all(rc, h)
    process_pending(Settings(trace_identity_marker_key=new_key()))
    assert admin_row(base_settings, "select state, reason from trace_private.user_contribution_status where user_id = %s", u) \
        == ("quarantined", "identity_markers_missing")
    assert contribution_of(base_settings, u) is None


def test_markers_are_never_stored_without_a_key(base_settings, users):
    from app.services.contributions.identity import MarkerProtectionUnavailable, store_identity
    from app.services.deidentification import Identity
    with db.connect_runtime(base_settings) as rt:
        with pytest.raises(MarkerProtectionUnavailable):
            store_identity(rt, users(), Identity(full_name="Test Person"), None)


# --------------------------------------------------------------------------- key lifecycle (D48)
def test_a_previous_key_opens_old_rows_but_never_seals():
    from app.services.contributions.sealing import MarkerSealer, MarkersUnavailable, decode_key, new_key
    old, new = decode_key(new_key()), decode_key(new_key())
    owner = uuid.uuid4()
    blob = MarkerSealer(old).seal(owner, {"names": ["Test Person"]})
    rotated = MarkerSealer(new, (old,))
    assert rotated.open(owner, blob, MarkerSealer(old).key_id)["names"] == ["Test Person"]   # recovery after a rotation
    assert rotated.key_id == MarkerSealer(new).key_id                                      # always seals with the new key
    with pytest.raises(MarkersUnavailable):
        MarkerSealer(new).open(owner, blob, MarkerSealer(old).key_id)                    # an unconfigured key: unavailable


def test_settings_read_previous_keys_and_a_restart_with_the_same_key_still_opens_rows(monkeypatch):
    from app.services.contributions.sealing import MarkerSealer, decode_key, new_key, sealer_from
    k_old, k_new = new_key(), new_key()
    owner = uuid.uuid4()
    blob = sealer_from(Settings(trace_identity_marker_key=k_old)).seal(owner, {"emails": ["x@example.invalid"]})
    again = sealer_from(Settings(trace_identity_marker_key=k_old))                          # a restart with the stable key
    assert again.open(owner, blob, MarkerSealer(decode_key(k_old)).key_id)["emails"] == ["x@example.invalid"]
    s = sealer_from(Settings(trace_identity_marker_key=k_new, trace_identity_marker_previous_keys=k_old))
    assert s.can_open(MarkerSealer(decode_key(k_old)).key_id) and "previous" not in repr(s)


def test_reseal_moves_rows_to_the_current_key_and_reports_unavailable_ones(rc, users, base_settings, monkeypatch):
    from scripts import privacy_maintenance
    from app.config import get_settings
    from app.services.contributions.sealing import MarkerSealer, decode_key, new_key, sealer_from
    current = sealer_from(Settings())
    u, h = ready_user(rc, users, contributor=True)
    lost = MarkerSealer(decode_key(new_key()))                                              # a key nobody configured
    stranger, hs = candidate(rc, users)
    profile = admin_row(base_settings, "select candidate_profile_id from public.trace_candidate_profiles where owner_user_id = %s", stranger)[0]
    with db.connect(base_settings) as admin:
        admin.execute("insert into trace_private.identity_markers (owner_user_id, candidate_profile_id, sealed, key_id) values (%s, %s, %s, %s)",
                      (stranger, profile, lost.seal(stranger, MARKERS), lost.key_id))
        admin.commit()
    rotated = new_key()
    monkeypatch.setenv("TRACE_IDENTITY_MARKER_PREVIOUS_KEYS", Settings().trace_identity_marker_key.get_secret_value())
    monkeypatch.setenv("TRACE_IDENTITY_MARKER_KEY", rotated)
    get_settings.cache_clear()
    try:
        assert privacy_maintenance.main(["reseal-markers", "--apply"]) == 0
        new_id = MarkerSealer(decode_key(rotated)).key_id
        assert admin_row(base_settings, "select key_id from trace_private.identity_markers where owner_user_id = %s", u)[0] == new_id
        assert admin_row(base_settings, "select key_id from trace_private.identity_markers where owner_user_id = %s", stranger)[0] == lost.key_id
        assert sealer_from(Settings()).open(u, bytes(admin_row(base_settings, "select sealed from trace_private.identity_markers "
                                                                           "where owner_user_id = %s", u)[0]), new_id)["names"]
    finally:
        get_settings.cache_clear()
    with db.connect(base_settings) as admin:
        admin.execute("delete from trace_private.identity_markers where owner_user_id = %s", (stranger,))
        admin.commit()
    assert current is not None
