"""The local demo setup script: locality refusals, idempotency, labelled users, no consent on anyone's behalf.

Auth admin calls go to an in-memory fake (no keys involved); one optional test (TRACE_TEST_SUPABASE_SERVICE_ROLE_KEY
set) runs against the real local Supabase Auth with throw-away emails and removes them afterwards.
"""
import os
import uuid
from types import SimpleNamespace

import pytest

from app import db
from app.config import Settings
from app.demo import consent
from scripts import demo_setup as setup
from tests.test_api_foundation import base_settings  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration


class FakeAuth:
    def __init__(self, existing=()):
        self.users = {e: str(uuid.uuid4()) for e in existing}
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path.split("?")[0], body))
        if method == "GET":
            return {"users": [{"email": e, "id": i} for e, i in self.users.items()]}
        if method == "POST":
            self.users[body["email"]] = str(uuid.uuid4())
        return {}


# --------------------------------------------------------------------------- locality
@pytest.mark.parametrize("url", [None, "", "https://abcdefgh.supabase.co", "http://10.0.0.5:54321", "http://db.example.com",
                                 "http://127.0.0.1.evil.example:54321"])
def test_non_local_auth_urls_are_refused(base_settings, url):
    with db.connect(base_settings) as conn:
        with pytest.raises(setup.SetupRefused, match="SUPABASE_URL"):
            setup.require_local(url, conn)


def test_a_non_local_database_is_refused(monkeypatch):
    monkeypatch.setattr(setup, "database_is_local", lambda conn: False)
    with pytest.raises(setup.SetupRefused, match="database connection"):
        setup.require_local("http://127.0.0.1:54321", SimpleNamespace())


def test_the_local_stack_is_accepted(base_settings):
    with db.connect(base_settings) as conn:
        setup.require_local("http://127.0.0.1:54321", conn)
        setup.require_local("http://localhost:54321", conn)


def test_main_refuses_before_touching_anything(base_settings, monkeypatch, capsys):
    called = []
    monkeypatch.setattr(setup, "get_settings", lambda: Settings(database_url=base_settings.database_url, supabase_url="https://x.supabase.co"))
    monkeypatch.setattr(setup, "http_client", lambda *a: called.append(a))
    monkeypatch.setattr(setup, "install_consent_document", lambda conn: called.append("doc"))
    assert setup.main(["--users", "1"], environ={"SUPABASE_SERVICE_ROLE_KEY": "k", "TRACE_DEMO_PASSWORD": "password1"}) == 1
    assert called == [] and "refused" in capsys.readouterr().err


# --------------------------------------------------------------------------- consent document
def test_consent_document_install_is_idempotent_and_never_overwrites(base_settings):
    with db.connect(base_settings) as conn:
        before = conn.execute("select count(*) from public.trace_consent_documents where consent_version = %s", (consent.VERSION,)).fetchone()[0]
        conn.rollback()
        first = setup.install_consent_document(conn)
        assert first in ("installed", "present") and setup.install_consent_document(conn) == "present"
        row = conn.execute("select title, body_sha256, legal_review_status, purpose::text from public.trace_consent_documents "
                           "where consent_version = %s", (consent.VERSION,)).fetchone()
        conn.rollback()
        assert row == (consent.TITLE, consent.BODY_SHA256, "approved", "resume_processing") and "not reviewed by a lawyer" in row[0]
        with conn.transaction():                                                 # tampered document: refused, not changed
            conn.execute("update public.trace_consent_documents set title = 'Tampered' where consent_version = %s", (consent.VERSION,))
            with pytest.raises(setup.SetupRefused, match="different content"):
                setup.install_consent_document(conn)
            conn.execute("update public.trace_consent_documents set title = %s where consent_version = %s", (consent.TITLE, consent.VERSION))
        if before == 0 and first == "installed":
            with conn.transaction():
                conn.execute("delete from public.trace_consent_documents where consent_version = %s", (consent.VERSION,))


# --------------------------------------------------------------------------- users
def test_demo_users_are_created_labelled_then_updated_on_rerun():
    fake = FakeAuth()
    assert setup.ensure_demo_users(fake, "password1", 2) == [("demo-candidate-1@example.invalid", "created"),
                                                               ("demo-candidate-2@example.invalid", "created")]
    created = [c for c in fake.calls if c[0] == "POST"]
    assert all(c[2]["user_metadata"] == setup.METADATA and c[2]["email_confirm"] is True for c in created)
    assert setup.ensure_demo_users(fake, "password1", 2) == [("demo-candidate-1@example.invalid", "updated"),
                                                               ("demo-candidate-2@example.invalid", "updated")]
    assert len(fake.users) == 2                                                  # rerun creates nothing new
    assert not any("consent" in c[1] for c in fake.calls)                         # never touches consent


def test_a_weak_or_missing_password_is_refused():
    for pw in (None, "", "short"):
        with pytest.raises(setup.SetupRefused):
            setup.ensure_demo_users(FakeAuth(), pw, 1)


def test_admin_errors_never_echo_keys_or_bodies():
    import urllib.error
    call = setup.http_client("http://127.0.0.1:1", "super-secret-key-value")
    with pytest.raises(Exception) as err:
        call("GET", "/auth/v1/admin/users", None)
    assert "super-secret-key-value" not in str(err.value)


# --------------------------------------------------------------------------- optional: real local Supabase Auth
@pytest.mark.skipif(not os.environ.get("TRACE_TEST_SUPABASE_SERVICE_ROLE_KEY"), reason="set TRACE_TEST_SUPABASE_SERVICE_ROLE_KEY to run")
def test_real_setup_run_is_idempotent_and_records_no_consent(base_settings, monkeypatch, capsys):
    tag = uuid.uuid4().hex[:6]
    monkeypatch.setattr(setup, "EMAIL", f"setup-test-{tag}-{{}}@example.invalid")
    api = os.environ["TRACE_TEST_SUPABASE_URL"]
    monkeypatch.setattr(setup, "get_settings", lambda: Settings(database_url=base_settings.database_url, supabase_url=api))
    env = {"SUPABASE_SERVICE_ROLE_KEY": os.environ["TRACE_TEST_SUPABASE_SERVICE_ROLE_KEY"], "TRACE_DEMO_PASSWORD": uuid.uuid4().hex}
    try:
        assert setup.main(["--users", "2"], environ=env) == 0
        assert setup.main(["--users", "2"], environ=env) == 0
        out = capsys.readouterr().out
        assert out.count("created") == 2 and out.count("updated") == 2 and env["TRACE_DEMO_PASSWORD"] not in out
        assert env["SUPABASE_SERVICE_ROLE_KEY"] not in out
        with db.connect(base_settings) as conn:
            ids = [r[0] for r in conn.execute("select id from auth.users where email like %s", (f"setup-test-{tag}-%",)).fetchall()]
            assert len(ids) == 2
            assert conn.execute("select count(*) from public.trace_consent_events where user_id = any(%s)", (ids,)).fetchone()[0] == 0
            assert conn.execute("select count(*) from public.trace_candidate_profiles where owner_user_id = any(%s)", (ids,)).fetchone()[0] == 0
    finally:
        with db.connect(base_settings) as conn:
            conn.autocommit = True
            conn.execute("delete from auth.users where email like %s", (f"setup-test-{tag}-%",))
