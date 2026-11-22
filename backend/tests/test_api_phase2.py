"""Phase 2: explicit consent, guarded synthetic fixture designation, resume upload and ingestion through the API.

Local database only. Users are synthetic auth.users rows (deleted afterwards); tokens are signed with the test key
from test_api_foundation. The demo consent document is installed if missing and removed again if this module
installed it.
"""
import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db
from app.api.deps import _verifier
from app.config import Settings, get_settings
from app.demo import consent as demo_consent
from app.demo import fixtures as demo
from app.main import create_app
from app.services.candidate_profile import OwnerContext
from app.services.resume_extraction import FixtureExtractor
from scripts.demo_setup import install_consent_document
from tests.test_api_foundation import JWKS, assert_no_secrets, auth, base_settings, token, users  # noqa: F401  (fixtures)

pytestmark = pytest.mark.integration
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SYN_DIR = Settings().trace_fixture_dir
EXTRACTOR = FixtureExtractor(SYN_DIR / "synthetic_profiles.jsonl", SYN_DIR / "synthetic_resume_pdfs.zip")
PDF = {f: EXTRACTOR.resume_bytes(f) for f in ("SYN-001", "SYN-002")}


@pytest.fixture(scope="module")
def consent_document(base_settings):
    with db.connect(base_settings) as c:
        status = install_consent_document(c)
    yield demo_consent.VERSION
    if status == "installed":
        with db.connect(base_settings) as c:
            c.autocommit = True
            try:
                c.execute("delete from public.trace_consent_documents where consent_version = %s", (demo_consent.VERSION,))
            except Exception:
                pass


def make_client(base_settings, **overrides):
    settings = Settings(database_url=base_settings.database_url, supabase_url="http://127.0.0.1:54321",
                        supabase_anon_key="public-anon-key-for-tests", supabase_jwks_json=JWKS, **overrides)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    _verifier.cache_clear()
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def demo_client(base_settings, consent_document):
    with make_client(base_settings, trace_demo_fixtures=True) as c:
        yield c


def candidate(client, users, *, grant=True):
    u = users()
    h = auth(token(u))
    assert client.post("/api/v1/profile", headers=h, json={}).status_code == 201
    if grant:
        assert client.post("/api/v1/consent/resume-processing", headers=h,
                           json={"decision": "granted", "consent_version": demo_consent.VERSION}).json()["active"]
    return u, h


def upload(client, h, data, content_type="application/pdf", name="SYN-001_synthetic_resume.pdf"):
    return client.post("/api/v1/resumes", headers={**h, "Content-Type": content_type, "X-Filename": name}, content=data)


def state(admin, u):
    p = admin.execute("select is_test_fixture, fixture_profile_id, candidate_profile_id from public.trace_candidate_profiles "
                      "where owner_user_id = %s", (u,)).fetchone()
    resumes = admin.execute("select is_test_fixture, content_sha256 from public.trace_resume_documents where owner_user_id = %s "
                            "order by created_at", (u,)).fetchall()
    records = admin.execute("select count(*) from public.trace_candidate_employments where owner_user_id = %s", (u,)).fetchone()[0]
    return p, resumes, records


def assert_clean(resp):
    assert_no_secrets(resp)
    assert not UUID_RE.search(resp.text), "internal identifier in response"
    assert "SYN-" not in resp.text and "sha256" not in resp.text.lower()


# --------------------------------------------------------------------------- consent
def test_consent_is_explicit_and_can_be_withdrawn(demo_client, users):
    u = users()
    h = auth(token(u))
    first = demo_client.get("/api/v1/consent/resume-processing", headers=h)
    assert first.status_code == 200
    body = first.json()
    assert (body["active"], body["decision"]) == (False, None)                 # nothing granted on the user's behalf
    assert body["document"]["consent_version"] == demo_consent.VERSION and body["document"]["text_verified"]
    assert body["document"]["text"] == demo_consent.BODY and "not reviewed by a lawyer" in body["document"]["title"]
    granted = demo_client.post("/api/v1/consent/resume-processing", headers=h, json={"decision": "granted", "consent_version": demo_consent.VERSION})
    assert granted.status_code == 200 and granted.json()["active"] and granted.json()["decision"]["status"] == "granted"
    withdrawn = demo_client.post("/api/v1/consent/resume-processing", headers=h, json={"decision": "withdrawn", "consent_version": demo_consent.VERSION})
    assert withdrawn.json()["active"] is False and withdrawn.json()["decision"]["status"] == "withdrawn"
    assert users.admin.execute("select array_agg(status::text order by recorded_at) from public.trace_consent_events where user_id = %s",
                               (u,)).fetchone()[0] == ["granted", "withdrawn"]
    assert_no_secrets(withdrawn)


@pytest.mark.parametrize("body, code", [({"decision": "granted", "consent_version": "some-other-version"}, 409),
                                        ({"decision": "maybe", "consent_version": demo_consent.VERSION}, 422),
                                        ({"decision": "granted"}, 422),
                                        ({"decision": "granted", "consent_version": demo_consent.VERSION, "user_id": str(uuid.uuid4())}, 422)])
def test_invalid_consent_requests_are_refused(demo_client, users, body, code):
    u = users()
    resp = demo_client.post("/api/v1/consent/resume-processing", headers=auth(token(u)), json=body)
    assert resp.status_code == code
    assert users.admin.execute("select count(*) from public.trace_consent_events where user_id = %s", (u,)).fetchone()[0] == 0


def test_consent_is_per_user(demo_client, users):
    a, ha = candidate(demo_client, users)
    b = users()
    assert demo_client.get("/api/v1/consent/resume-processing", headers=auth(token(b))).json()["active"] is False


def test_consent_requires_authentication(demo_client):
    assert demo_client.get("/api/v1/consent/resume-processing").status_code == 401


# --------------------------------------------------------------------------- upload: refusals that write nothing
def test_upload_without_consent_writes_nothing(demo_client, users):
    u, h = candidate(demo_client, users, grant=False)
    resp = upload(demo_client, h, PDF["SYN-001"])
    assert resp.status_code == 403 and resp.json()["error"] == "consent_required"
    p, resumes, records = state(users.admin, u)
    assert (p[0], p[1], resumes, records) == (False, None, [], 0)


def test_upload_after_withdrawal_is_refused(demo_client, users):
    u, h = candidate(demo_client, users)
    demo_client.post("/api/v1/consent/resume-processing", headers=h, json={"decision": "withdrawn", "consent_version": demo_consent.VERSION})
    assert upload(demo_client, h, PDF["SYN-001"]).json()["error"] == "consent_required"
    assert state(users.admin, u)[1] == []


@pytest.mark.parametrize("data, ctype, code, error", [
    (PDF["SYN-001"] + b"\n%modified", "application/pdf", 422, "unsupported_document"),        # modified approved PDF
    (b"%PDF-1.4\n1 0 obj << >> endobj\n%%EOF", "application/pdf", 422, "unsupported_document"),  # arbitrary PDF
    (b"hello, not a pdf", "application/pdf", 415, "unsupported_media_type"),                   # not a PDF
    (PDF["SYN-001"], "text/plain", 415, "unsupported_media_type"),                              # wrong media type
    (b"", "application/pdf", 415, "unsupported_media_type"),                                    # empty
])
def test_unapproved_or_invalid_uploads_write_nothing(demo_client, users, data, ctype, code, error):
    u, h = candidate(demo_client, users)
    resp = upload(demo_client, h, data, ctype)
    assert (resp.status_code, resp.json()["error"]) == (code, error)
    p, resumes, records = state(users.admin, u)
    assert (p[0], resumes, records) == (False, [], 0)
    assert_clean(resp)


def test_oversized_upload_is_refused(base_settings, consent_document, users):
    with make_client(base_settings, trace_demo_fixtures=True, max_resume_bytes=1000) as c:
        u, h = candidate(c, users)
        resp = upload(c, h, b"%PDF-" + b"x" * 5000)
        assert (resp.status_code, resp.json()["error"]) == (413, "upload_too_large")
        assert state(users.admin, u)[1] == []


def test_demo_mode_disabled_designates_nothing(base_settings, consent_document, users):
    with make_client(base_settings) as c:                                     # TRACE_DEMO_FIXTURES defaults to off
        u, h = candidate(c, users)
        resp = upload(c, h, PDF["SYN-001"])
        assert (resp.status_code, resp.json()["error"]) == (403, "demo_fixtures_disabled")
        p, resumes, records = state(users.admin, u)
        assert (p[0], resumes, records) == (False, [], 0)


def test_demo_mode_against_a_non_local_database_is_refused(demo_client, users, monkeypatch):
    monkeypatch.setattr(demo, "database_is_local", lambda conn: False)
    u, h = candidate(demo_client, users)
    resp = upload(demo_client, h, PDF["SYN-001"])
    assert (resp.status_code, resp.json()["error"]) == (403, "demo_fixtures_disabled")
    assert state(users.admin, u)[1] == []


def test_upload_needs_a_profile_and_a_token(demo_client, users):
    assert upload(demo_client, {}, PDF["SYN-001"]).status_code == 401
    u = users()
    assert upload(demo_client, auth(token(u)), PDF["SYN-001"]).status_code == 404


# --------------------------------------------------------------------------- locality check
@pytest.mark.parametrize("host, hostaddr, local", [("127.0.0.1", "127.0.0.1", True), ("localhost", "::1", True),
                                                  ("/var/run/postgresql", "", True), ("db.example.com", "10.0.0.5", False),
                                                  ("127.0.0.1", "", False), ("127.0.0.1", None, False),
                                                  ("db.internal", "192.168.1.10", False), ("db", "not-an-ip", False)])
def test_database_locality_is_read_from_the_connection(host, hostaddr, local):
    assert demo.database_is_local(SimpleNamespace(info=SimpleNamespace(host=host, hostaddr=hostaddr))) is local


def test_unknown_locality_fails_closed():
    class Broken:
        @property
        def info(self):
            raise RuntimeError("no info")
    assert demo.database_is_local(Broken()) is False


# --------------------------------------------------------------------------- success and consistency
def test_synthetic_fixture_is_ingested_through_the_api(demo_client, users):
    u, h = candidate(demo_client, users)
    resp = upload(demo_client, h, PDF["SYN-001"])
    assert resp.status_code == 201, resp.text
    body = resp.json()
    fx = EXTRACTOR.fixture("SYN-001")
    assert body["status"] == "succeeded" and body["is_synthetic_test_data"] and "not parsed" in body["notice"]
    assert body["extracted"]["employments"] == len(fx["employments"]) and body["extracted"]["evidence"] == len(fx["evidence"])
    p, resumes, records = state(users.admin, u)
    import hashlib
    assert (p[0], p[1]) == (True, "SYN-001") and resumes == [(True, hashlib.sha256(PDF["SYN-001"]).hexdigest())]
    assert records == len(fx["employments"])
    assert users.admin.execute("select count(*) from public.trace_candidate_employments where owner_user_id = %s and review_status <> 'pending_review'",
                               (u,)).fetchone()[0] == 0                               # drafts, not reviewed
    assert_clean(resp)
    again = upload(demo_client, h, PDF["SYN-001"])                                    # never merges
    assert (again.status_code, again.json()["error"]) == (409, "ingestion_conflict")
    assert len(state(users.admin, u)[1]) == 1


def test_designation_only_touches_the_callers_own_rows(base_settings, users):
    a, b = users(), users()
    admin = users.admin
    pa = admin.execute("insert into public.trace_candidate_profiles (owner_user_id) values (%s) returning candidate_profile_id", (a,)).fetchone()[0]
    pb = admin.execute("insert into public.trace_candidate_profiles (owner_user_id) values (%s) returning candidate_profile_id", (b,)).fetchone()[0]
    import hashlib
    sha = hashlib.sha256(PDF["SYN-001"]).hexdigest()
    ra = admin.execute("insert into public.trace_resume_documents (owner_user_id, candidate_profile_id, storage_object_path, mime_type, byte_size, "
                       "content_sha256) values (%s, %s, %s, 'application/pdf', 1, %s) returning resume_id", (a, pa, f"{a}/test.pdf", sha)).fetchone()[0]
    with db.connect(base_settings) as conn:
        for ctx, resume in ((OwnerContext(owner_user_id=b, candidate_profile_id=pb), ra),       # B naming A's resume
                            (OwnerContext(owner_user_id=b, candidate_profile_id=pa), ra)):      # B naming A's profile
            with pytest.raises(demo.DesignationRefused):
                demo.designate_fixture(conn, ctx, resume, "SYN-001", sha)
    assert admin.execute("select bool_or(is_test_fixture) from public.trace_candidate_profiles where owner_user_id = any(%s)", ([a, b],)).fetchone()[0] is False
    assert admin.execute("select is_test_fixture from public.trace_resume_documents where resume_id = %s", (ra,)).fetchone()[0] is False


def test_a_profile_linked_to_another_fixture_is_never_relinked(demo_client, users):
    u, h = candidate(demo_client, users)
    users.admin.execute("update public.trace_candidate_profiles set is_test_fixture = true, fixture_profile_id = 'SYN-002' where owner_user_id = %s", (u,))
    resp = upload(demo_client, h, PDF["SYN-001"])
    assert (resp.status_code, resp.json()["error"]) == (409, "designation_refused")
    p, resumes, records = state(users.admin, u)
    assert (p[1], resumes, records) == ("SYN-002", [], 0)                         # registration undone, nothing changed


def test_designation_failure_leaves_nothing_behind(demo_client, users, monkeypatch):
    def boom(*args, **kwargs):
        raise demo.DesignationRefused("simulated")
    monkeypatch.setattr(demo, "designate_fixture", boom)
    u, h = candidate(demo_client, users)
    assert upload(demo_client, h, PDF["SYN-001"]).status_code == 409
    p, resumes, records = state(users.admin, u)
    assert (p[0], p[1], resumes, records) == (False, None, [], 0)


def test_ingestion_failure_leaves_a_consistent_designation(demo_client, users, monkeypatch):
    from app.services.candidate_profile import CandidateProfileService
    u, h = candidate(demo_client, users, grant=False)
    monkeypatch.setattr(CandidateProfileService, "consent_state", lambda self, owner, purpose="resume_processing": {"active": True})
    resp = upload(demo_client, h, PDF["SYN-001"])                                     # the database's own consent check refuses
    assert (resp.status_code, resp.json()["error"]) == (403, "consent_required")
    p, resumes, records = state(users.admin, u)
    assert (p[0], p[1], [r[0] for r in resumes], records) == (True, "SYN-001", [True], 0)   # designated together, no records


def test_the_database_allowlist_is_checked_independently_of_the_extractor(base_settings, consent_document, users):
    """If the extractor and the database allowlist ever disagreed, the database allowlist wins: nothing is written."""
    from app.api.routes import fixture_extractor

    class Mislabelled:
        def identify(self, data):
            return "SYN-001"                                                  # claims every PDF is SYN-001

        def extract(self, data):
            raise AssertionError("must not be reached")
    with make_client(base_settings, trace_demo_fixtures=True) as c:
        c.app.dependency_overrides[fixture_extractor] = lambda: Mislabelled()
        u, h = candidate(c, users)
        resp = upload(c, h, PDF["SYN-002"])                                     # SYN-002's bytes labelled SYN-001
        assert (resp.status_code, resp.json()["error"]) == (403, "demo_fixtures_disabled")
        p, resumes, records = state(users.admin, u)
        assert (p[0], resumes, records) == (False, [], 0)


def test_the_size_limit_holds_without_a_content_length(base_settings, consent_document, users):
    with make_client(base_settings, trace_demo_fixtures=True, max_resume_bytes=1000) as c:
        u, h = candidate(c, users)

        def chunks():
            yield b"%PDF-"
            for _ in range(10):
                yield b"x" * 500
        resp = c.post("/api/v1/resumes", headers={**h, "Content-Type": "application/pdf"}, content=chunks())
        assert (resp.status_code, resp.json()["error"]) == (413, "upload_too_large")
        assert state(users.admin, u)[1] == []


def test_the_upload_reader_stops_reading_once_the_limit_is_exceeded():
    import asyncio
    from app.api.routes import _read_limited
    from app.demo.fixtures import UploadTooLarge
    consumed = []

    class FakeRequest:
        headers = {}

        async def stream(self):
            for i in range(100):
                consumed.append(i)
                yield b"x" * 400
    with pytest.raises(UploadTooLarge):
        asyncio.run(_read_limited(FakeRequest(), 1000))
    assert len(consumed) == 3                                                     # 1200 bytes > 1000: stopped there

    class Declared(FakeRequest):
        headers = {"content-length": "5000"}
    consumed.clear()
    with pytest.raises(UploadTooLarge):
        asyncio.run(_read_limited(Declared(), 1000))
    assert consumed == []                                                          # refused before reading anything
