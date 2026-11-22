"""API foundation (Phase 1): token verification, owner context, profile endpoints, error mapping.

Tokens are signed in the test with a throw-away EC P-256 key published to the app as a static JWKS, so every
rejection path is exercised deterministically. Users are synthetic rows in the local auth.users table and are
deleted afterwards. An optional test (TRACE_TEST_SUPABASE_SERVICE_ROLE_KEY set) uses a real local Supabase Auth
token verified through the issuer's JWKS endpoint.
"""
import json
import os
import threading
import time
import uuid

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app import db
from app.api import errors as api_errors
from app.api.deps import _verifier
from app.config import Settings, get_settings
from app.main import create_app
from app.services.candidate_profile import repository as r

pytestmark = pytest.mark.integration
ISSUER, AUDIENCE, KID = "http://127.0.0.1:54321/auth/v1", "authenticated", "test-key-1"
KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())
JWK = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(KEY.public_key()))
JWKS = json.dumps({"keys": [{**JWK, "kid": KID, "alg": "ES256", "use": "sig"}]})
SECRET_MARKERS = ("postgres:", "postgresql://", "SERVICE_ROLE", "service_role_key", "jwt_secret", "BEGIN", '"d":', "Traceback")


def token(sub=None, *, key=KEY, kid=KID, alg="ES256", exp=3600, **claims):
    body = {"sub": str(sub or uuid.uuid4()), "iss": ISSUER, "aud": AUDIENCE, "role": "authenticated",
            "exp": None if exp is None else int(time.time()) + exp, "iat": int(time.time()), **claims}
    body = {k: v for k, v in body.items() if v is not None}
    return jwt.encode(body, key, algorithm=alg, headers={"kid": kid} if kid else None)


@pytest.fixture(scope="module")
def base_settings():
    s = get_settings()
    if s.database_url is None:
        pytest.skip("DATABASE_URL not configured")
    try:
        db.check_connection(s)
    except Exception as exc:  # local stack not running
        pytest.skip(f"local database unreachable: {type(exc).__name__}")
    return s


@pytest.fixture
def client(base_settings):
    settings = Settings(database_url=base_settings.database_url, supabase_url="http://127.0.0.1:54321",
                        supabase_anon_key="public-anon-key-for-tests", supabase_jwks_json=JWKS)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    _verifier.cache_clear()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    _verifier.cache_clear()


@pytest.fixture
def users(base_settings):
    admin = db.connect(base_settings)
    admin.autocommit = True
    made = []

    def make():
        u = uuid.uuid4()
        admin.execute("insert into auth.users (id, email) values (%s, %s)", (u, f"api-{u}@example.invalid"))
        made.append(u)
        return u
    make.admin = admin
    yield make
    if made:
        admin.execute("delete from auth.users where id = any(%s)", (made,))
    admin.close()


def auth(t):
    return {"Authorization": f"Bearer {t}"}


def assert_no_secrets(resp):
    text = resp.text
    for m in SECRET_MARKERS:
        assert m not in text, m


# --------------------------------------------------------------------------- authentication
@pytest.mark.parametrize("headers", [{}, {"Authorization": ""}, {"Authorization": "Bearer"}, {"Authorization": "Basic abc"},
                                     {"Authorization": "Bearer not.a.jwt"}, {"Authorization": "Bearer abc"}])
def test_missing_or_malformed_tokens_are_401(client, headers):
    resp = client.get("/api/v1/me", headers=headers)
    assert resp.status_code == 401 and resp.headers["www-authenticate"] == "Bearer"
    assert resp.json() == {"error": "unauthorized", "message": "A valid access token is required."}


@pytest.mark.parametrize("make", [
    lambda: token(key=OTHER_KEY),                                     # invalid signature
    lambda: token(exp=-60),                                           # expired
    lambda: token(iss="http://evil.example/auth/v1"),                 # wrong issuer
    lambda: token(aud="anon"),                                        # wrong audience
    lambda: token(role="anon"),                                       # wrong role
    lambda: token(role="service_role"),
    lambda: token(role=None),                                         # no role
    lambda: token(sub="not-a-uuid"),                                  # subject is not a user id
    lambda: token(kid="unknown-kid"),                                 # unknown key
    lambda: token(kid=None),                                          # no key id
    lambda: token(exp=None),                                          # no expiry
    lambda: token(aud=None),                                          # no audience
    lambda: jwt.encode({"sub": str(uuid.uuid4()), "iss": ISSUER, "aud": AUDIENCE, "role": "authenticated",
                        "exp": int(time.time()) + 600}, "x" * 32, algorithm="HS256", headers={"kid": KID}),     # algorithm not pinned
    lambda: jwt.encode({"sub": str(uuid.uuid4()), "iss": ISSUER, "aud": AUDIENCE, "role": "authenticated",
                        "exp": int(time.time()) + 600}, None, algorithm="none", headers={"kid": KID}),          # unsigned
])
def test_invalid_tokens_are_401(client, make):
    resp = client.get("/api/v1/me", headers=auth(make()))
    assert resp.status_code == 401 and resp.json()["error"] == "unauthorized"
    assert_no_secrets(resp)


def test_valid_token_identifies_the_owner_by_sub(client, users):
    u = users()
    resp = client.get("/api/v1/me", headers=auth(token(u)))
    assert resp.status_code == 200
    assert resp.json() == {"user_id": str(u), "has_profile": False, "profile": None}


# --------------------------------------------------------------------------- profile
def test_profile_creation_and_retrieval_belong_to_the_token_subject(client, users):
    u = users()
    h = auth(token(u))
    assert client.get("/api/v1/profile", headers=h).status_code == 404
    resp = client.post("/api/v1/profile", headers=h, json={"headline": "Synthetic PM"})
    assert resp.status_code == 201
    body = resp.json()
    assert body == {"headline": "Synthetic PM", "country_code": "IN", "confirmation_status": body["confirmation_status"],
                    "resume_as_of": body["resume_as_of"]}
    assert "candidate_profile_id" not in resp.text and "owner_user_id" not in resp.text
    owner = users.admin.execute("select owner_user_id from public.trace_candidate_profiles where headline = 'Synthetic PM' "
                                "and owner_user_id = %s", (u,)).fetchone()
    assert owner == (u,)
    assert client.get("/api/v1/profile", headers=h).json()["headline"] == "Synthetic PM"
    me = client.get("/api/v1/me", headers=h).json()
    assert (me["has_profile"], me["profile"]["headline"]) == (True, "Synthetic PM")


def test_one_profile_per_user(client, users):
    u = users()
    h = auth(token(u))
    assert client.post("/api/v1/profile", headers=h, json={}).status_code == 201
    second = client.post("/api/v1/profile", headers=h, json={"headline": "Again"})
    assert second.status_code == 409 and second.json()["error"] == "profile_already_exists"
    assert users.admin.execute("select count(*) from public.trace_candidate_profiles where owner_user_id = %s", (u,)).fetchone()[0] == 1


def test_concurrent_profile_creation_creates_exactly_one(client, users):
    for _ in range(5):                                       # several rounds, so a missing lock cannot pass by luck
        u = users()
        h = auth(token(u))
        codes, gate = [], threading.Barrier(10)

        def post():
            gate.wait(10)
            codes.append(client.post("/api/v1/profile", headers=h, json={}).status_code)
        threads = [threading.Thread(target=post) for _ in range(10)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        assert sorted(codes) == [201] + [409] * 9
        assert users.admin.execute("select count(*) from public.trace_candidate_profiles where owner_user_id = %s", (u,)).fetchone()[0] == 1


def test_more_than_one_profile_is_never_chosen_silently(client, users):
    u = users()
    for _ in range(2):
        users.admin.execute("insert into public.trace_candidate_profiles (owner_user_id) values (%s)", (u,))
    for path in ("/api/v1/me", "/api/v1/profile"):
        resp = client.get(path, headers=auth(token(u)))
        assert resp.status_code == 409 and resp.json()["error"] == "profile_ambiguous"


def test_clients_cannot_supply_owner_or_profile_ids(client, users):
    a, b = users(), users()
    hb = auth(token(b))
    assert client.post("/api/v1/profile", headers=hb, json={"headline": "B's own"}).status_code == 201
    for body in ({"owner_user_id": str(b)}, {"candidate_profile_id": str(uuid.uuid4())}, {"headline": "x", "owner_user_id": str(b)}):
        resp = client.post("/api/v1/profile", headers=auth(token(a)), json=body)
        assert resp.status_code == 422, body                          # unknown fields are refused, never used
    # Query parameters and headers naming another owner are ignored: A still sees only A.
    resp = client.get(f"/api/v1/me?owner_user_id={b}&user_id={b}", headers={**auth(token(a)), "X-Owner-User-Id": str(b)})
    assert resp.json() == {"user_id": str(a), "has_profile": False, "profile": None}
    assert client.get("/api/v1/profile", headers=auth(token(a))).status_code == 404       # B's profile is not A's


# --------------------------------------------------------------------------- configuration
def test_config_exposes_only_url_anon_key_and_data_mode(client):
    resp = client.get("/api/v1/config")
    assert resp.status_code == 200
    assert resp.json() == {"supabase_url": "http://127.0.0.1:54321", "supabase_anon_key": "public-anon-key-for-tests",
                           "data_mode": "synthetic_demo", "features": {"real_resume_uploads": False, "career_research": False}}
    assert_no_secrets(resp)


@pytest.mark.parametrize("mode", ["empirical", "real", "", "SYNTHETIC_DEMO", "synthetic_test"])
def test_config_with_an_unsupported_data_mode_fails_closed(base_settings, mode):
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(database_url=base_settings.database_url, supabase_url="http://127.0.0.1:54321",
                                                              supabase_anon_key="k", trace_data_mode=mode)
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/api/v1/config")
    assert resp.status_code == 503 and resp.json()["error"] == "data_mode_unsupported"


def test_config_unconfigured_is_503(base_settings):
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(database_url=base_settings.database_url)
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/api/v1/config")
    assert resp.status_code == 503 and resp.json()["error"] == "auth_unavailable"


def test_unconfigured_issuer_rejects_everything(base_settings):
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(database_url=base_settings.database_url)
    _verifier.cache_clear()
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/api/v1/me", headers=auth(token()))
    assert resp.status_code == 503 and resp.json()["error"] == "auth_unavailable"


# --------------------------------------------------------------------------- error mapping
ERRORS = {
    "not_found": (r.RecordNotFound("employment x"), 404, "record_not_found"),
    "profile": (r.ProfileNotFound("p"), 404, "profile_not_found"),
    "consent": (r.ConsentRequired("no current consent"), 403, "consent_required"),
    "permission": (r.PermissionDenied("permission denied for table trace_candidate_profiles"), 403, "permission_denied"),
    "invalid": (r.InvalidRecord('new row for relation "trace_candidate_employments" violates check constraint "x"'), 422, "invalid_record"),
    "reference": (r.InvalidReference("not responsibility areas of the taxonomy: ['x']"), 422, "invalid_reference"),
    "conflict": (r.MappingConflict("the title changed since the mapping was proposed"), 409, "mapping_conflict"),
    "editable": (r.RecordNotEditable("a rejected record cannot be edited; reinstate it first"), 409, "record_not_editable"),
    "generic": (r.CandidateProfileError("deadlock detected"), 500, "internal_error"),
    "db": (psycopg.errors.UndefinedTable("relation trace_x does not exist"), 500, "internal_error"),
    "unreachable": (psycopg.OperationalError("connection to server at 127.0.0.1 failed: password authentication failed"), 503, "database_unavailable"),
    "unexpected": (RuntimeError("secret=abc postgresql://postgres:postgres@127.0.0.1"), 500, "internal_error"),
}


@pytest.fixture
def raising_client(base_settings):
    app = create_app()
    test = APIRouter()

    @test.get("/raise/{name}")
    def raise_(name: str):
        raise ERRORS[name][0]
    app.include_router(test)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.mark.parametrize("name", list(ERRORS))
def test_errors_map_to_safe_responses(raising_client, name):
    exc, status, code = ERRORS[name]
    resp = raising_client.get(f"/raise/{name}")
    assert resp.status_code == status and resp.json()["error"] == code
    msg = resp.json()["message"]
    assert not api_errors.UNSAFE.search(msg), msg                     # no SQL or schema detail
    assert_no_secrets(resp)
    if name in ("reference", "conflict", "editable", "consent"):
        assert msg == str(exc)                                        # authored messages pass through
    if name in ("permission", "invalid", "generic", "db", "unreachable"):
        assert msg == api_errors.DEFAULT_MESSAGE[status]
    if name == "unexpected":                                          # Phase 4: a support reference, nothing else
        ref = resp.json()["reference"]
        assert msg == f"Something went wrong on our side. Reference: {ref}" and len(ref) == 10


# --------------------------------------------------------------------------- real local Supabase Auth (optional)
@pytest.mark.skipif(not os.environ.get("TRACE_TEST_SUPABASE_SERVICE_ROLE_KEY"), reason="set TRACE_TEST_SUPABASE_SERVICE_ROLE_KEY to run")
def test_real_local_supabase_token_is_accepted_through_jwks(base_settings):
    import httpx
    api, anon, srv = os.environ["TRACE_TEST_SUPABASE_URL"], os.environ["TRACE_TEST_SUPABASE_ANON_KEY"], os.environ["TRACE_TEST_SUPABASE_SERVICE_ROLE_KEY"]
    email, pw = f"api-{uuid.uuid4().hex[:8]}@example.invalid", uuid.uuid4().hex
    u = httpx.post(f"{api}/auth/v1/admin/users", headers={"apikey": srv, "Authorization": f"Bearer {srv}"},
                   json={"email": email, "password": pw, "email_confirm": True}).json()
    try:
        t = httpx.post(f"{api}/auth/v1/token?grant_type=password", headers={"apikey": anon}, json={"email": email, "password": pw}).json()["access_token"]
        app = create_app()
        app.dependency_overrides[get_settings] = lambda: Settings(database_url=base_settings.database_url, supabase_url=api, supabase_anon_key=anon)
        _verifier.cache_clear()
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get("/api/v1/me", headers=auth(t))
            assert resp.status_code == 200 and resp.json()["user_id"] == u["id"]
            assert c.get("/api/v1/me", headers=auth(t[:-4] + "AAAA")).status_code == 401
    finally:
        httpx.delete(f"{api}/auth/v1/admin/users/{u['id']}", headers={"apikey": srv, "Authorization": f"Bearer {srv}"})
        _verifier.cache_clear()


def test_the_owner_dependency_itself_requires_the_bearer_scheme():
    """Defence in depth: even if a credential object with another scheme reached it, it is refused."""
    from fastapi.security import HTTPAuthorizationCredentials
    from app.api.auth import TokenRejected, TokenVerifier
    from app.api.deps import current_owner
    verifier = TokenVerifier(ISSUER, AUDIENCE, JWKS)
    good = token()
    assert current_owner(HTTPAuthorizationCredentials(scheme="Bearer", credentials=good), verifier)
    for scheme in ("Basic", "Token", "DPoP"):
        with pytest.raises(TokenRejected):
            current_owner(HTTPAuthorizationCredentials(scheme=scheme, credentials=good), verifier)
    with pytest.raises(TokenRejected):
        current_owner(None, verifier)
