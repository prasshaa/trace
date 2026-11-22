"""Phase 6 security: restricted runtime login, no direct client writes, connection isolation, shared rate limits,
consent tampering, contribution policy, API docs exposure and tracked secrets.

The runtime login is tested directly (its own connection), not by switching roles inside the admin connection.
"""
import json
import re
import subprocess
import threading
import uuid
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import db
from app.api.deps import _verifier
from app.config import Settings, get_settings
from app.services.candidate_profile import CandidateProfileService, OwnerContext
from app.services.candidate_profile.repository import owner_session
from tests.test_api_foundation import JWKS, auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import PDF, candidate, consent_document, demo_client, make_client, upload  # noqa: F401

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[2]
PRIVATE_TABLES = ["trace_candidate_profiles", "trace_candidate_employments", "trace_candidate_employers", "trace_candidate_education",
                  "trace_candidate_evidence", "trace_candidate_skills", "trace_candidate_career_activities", "trace_candidate_career_breaks",
                  "trace_candidate_corrections", "trace_resume_documents", "trace_extraction_runs", "trace_consent_events",
                  "trace_pathway_runs", "trace_pathway_recommendations", "trace_pathway_route_steps", "trace_pathway_evidence",
                  "trace_agent_threads", "trace_agent_messages"]


@pytest.fixture
def rt(base_settings):
    conn = db.connect_runtime(base_settings)
    yield conn
    conn.close()


def denied(conn, sql, params=None):
    try:
        with conn.transaction():
            conn.execute(sql, params)
    except psycopg.Error as exc:
        return type(exc).__name__
    return None


# --------------------------------------------------------------------------- the runtime login
def test_runtime_login_is_not_privileged(rt):
    row = rt.execute("select session_user, current_user, r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb, r.rolreplication "
                     "from pg_roles r where r.rolname = session_user").fetchone()
    assert row == ("trace_app_runtime", "trace_app_runtime", False, False, False, False, False)
    assert rt.execute("select count(*) from pg_class where relowner = 'trace_app_runtime'::regrole").fetchone()[0] == 0
    memberships = rt.execute("select r.rolname, a.inherit_option, a.set_option from pg_auth_members a join pg_roles r on r.oid = a.roleid "
                             "where a.member = 'trace_app_runtime'::regrole order by 1").fetchall()
    assert memberships == [("trace_app_user", False, True), ("trace_reference_reader", True, False)]


@pytest.mark.parametrize("sql", [
    "create table public.t6_x (id int)", "create schema t6_x", "alter table public.trace_candidate_profiles add column x int",
    "alter table public.trace_candidate_profiles disable row level security", "drop policy \"candidate profiles owner select\" on public.trace_candidate_profiles",
    "alter table trace_ref.profiles owner to trace_app_runtime", "alter role trace_app_runtime bypassrls",
    "alter role trace_app_runtime superuser", "grant postgres to trace_app_runtime", "grant service_role to trace_app_runtime",
    "set role postgres", "set role service_role", "set role authenticated", "set role trace_review_writer", "set role trace_ingest_writer",
    "set session authorization postgres", "select * from public.trace_candidate_profiles", "select * from public.trace_consent_events",
    "update trace_ref.profiles set synthetic_demo_allowed = false", "delete from trace_ref.employments", "insert into trace_ref.data_sources default values",
    "update public.trace_role_taxonomy set canonical_title = 'x'", "select * from trace_private.contributors",
    "select trace_review.review_record('employment', gen_random_uuid(), gen_random_uuid(), 'confirm')",
    "truncate trace_ops.rate_limit_counters", "alter table trace_ops.rate_limit_counters disable row level security",
])
def test_runtime_login_cannot_escalate_alter_or_bypass(rt, sql):
    assert denied(rt, sql), sql


def test_runtime_can_do_exactly_what_the_api_needs(rt):
    assert rt.execute("select count(*) from trace_ref.profiles where dataset_class = 'synthetic_test_fixture'").fetchone()[0] == 41
    assert rt.execute("select count(*) from trace_ref.v_mode_profiles where data_mode = 'synthetic_test'").fetchone()[0] == 41
    assert rt.execute("select count(*) from public.trace_role_taxonomy").fetchone()[0] > 0
    assert rt.execute("select count(*) from trace_ingest.approved_fixture_resumes").fetchone()[0] == 41
    rt.rollback()                         # idle again: the next block is a real transaction, so SET LOCAL ends with it
    with rt.transaction():
        rt.execute("set local role trace_app_user")
        assert rt.execute("select current_user").fetchone()[0] == "trace_app_user"
    assert rt.execute("select current_user").fetchone()[0] == "trace_app_runtime"
    rt.rollback()
    rt.execute("select 1")                                                    # a transaction is now open
    with pytest.raises(RuntimeError, match="no open transaction"):            # owner sessions refuse to nest
        with owner_session(rt, uuid.uuid4()):
            pass
    rt.rollback()


def test_an_admin_or_owner_login_is_refused_as_the_runtime(base_settings):
    s = Settings(database_url=base_settings.database_url, database_runtime_url=base_settings.database_url)
    with pytest.raises(db.PrivilegedRuntimeIdentity):
        db.connect_runtime(s)
    with make_client(base_settings, database_runtime_url=base_settings.database_url) as c:
        h = auth(token(uuid.uuid4()))
        r = c.get("/api/v1/me", headers=h)
        assert (r.status_code, r.json()["error"]) == (503, "database_unavailable")


# --------------------------------------------------------------------------- no direct client writes
def test_clients_hold_no_write_privilege_on_private_tables_or_writer_functions(base_settings):
    with db.connect(base_settings) as conn:
        for role in ("anon", "authenticated"):
            for t in PRIVATE_TABLES:
                for priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                    col = priv if priv in ("INSERT", "UPDATE") else "UPDATE"
                    assert not conn.execute("select has_table_privilege(%s, %s, %s) or has_any_column_privilege(%s, %s, %s)",
                                            (role, f"public.{t}", priv, role, f"public.{t}", col)).fetchone()[0], (role, t, priv)
            writers = conn.execute("select p.oid::regprocedure::text from pg_proc p where p.pronamespace in "
                                   "('trace_review'::regnamespace, 'trace_ingest'::regnamespace, 'trace_private'::regnamespace, "
                                   "'trace_ops'::regnamespace) and has_function_privilege(%s, p.oid, 'execute') "
                                   "and p.prosecdef", (role,)).fetchall()
            assert writers == [], (role, writers)
        assert not conn.execute("select pg_has_role('authenticator', 'trace_app_user', 'member')").fetchone()[0]
        assert not conn.execute("select bool_or(policyname = 'trace resumes owner upload') from pg_policies "
                                "where schemaname = 'storage'").fetchone()[0]


def test_new_definer_functions_have_no_public_execute(base_settings):
    with db.connect(base_settings) as conn:
        rows = conn.execute("select p.oid::regprocedure::text, coalesce(array_to_string(p.proacl, ','), 'DEFAULT') from pg_proc p "
                            "where p.pronamespace in ('trace_private'::regnamespace, 'trace_ops'::regnamespace) "
                            "or p.oid in ('trace_ingest.designate_fixture(uuid,uuid,text,text)'::regprocedure, "
                            "'trace_ref.eligible_real_profile_ids()'::regprocedure)").fetchall()
    for name, acl in rows:
        assert acl != "DEFAULT" and not re.search(r"(^|,)=X", acl), (name, acl)        # no PUBLIC execute


# --------------------------------------------------------------------------- connection reuse and identity
def test_reused_connection_never_carries_a_previous_identity(rt, users, demo_client):
    a, _ = candidate(demo_client, users)
    b, _ = candidate(demo_client, users)
    svc = CandidateProfileService(rt)
    ids = {u: rt.execute("select 1").fetchone() and None for u in (a, b)}
    rt.rollback()
    for u in (a, b, a):
        with owner_session(rt, u) as repo:
            who = rt.execute("select current_user, auth.uid()").fetchone()
            assert who == ("trace_app_user", u)
            assert rt.execute("select count(*) from public.trace_candidate_profiles").fetchone()[0] == 1
            assert rt.execute("select owner_user_id from public.trace_candidate_profiles").fetchone()[0] == u
        after = rt.execute("select current_user, nullif(current_setting('request.jwt.claim.sub', true), ''), "
                           "nullif(current_setting('request.jwt.claims', true), '')").fetchone()
        rt.rollback()
        assert after == ("trace_app_runtime", None, None)
    with pytest.raises(Exception):
        with owner_session(rt, a):
            raise RuntimeError("request failed half-way")
    after = rt.execute("select current_user, nullif(current_setting('request.jwt.claim.sub', true), '')").fetchone()
    rt.rollback()
    assert after == ("trace_app_runtime", None)
    assert ids is not None and svc is not None


def test_an_anonymous_request_after_an_authenticated_one_has_no_identity(demo_client, users):
    u, h = candidate(demo_client, users)
    assert demo_client.get("/api/v1/profile/records", headers=h).status_code == 200
    for path in ("/api/v1/profile/records", "/api/v1/me", "/api/v1/consent", "/api/v1/cohorts", "/api/v1/insights?target=PM-PM"):
        assert demo_client.get(path).status_code == 401


# --------------------------------------------------------------------------- shared rate limiting
def test_concurrent_requests_cannot_exceed_the_shared_limit(base_settings):
    key, accepted, errors = f"test-{uuid.uuid4().hex}:conc", [], []

    def hit():
        try:
            with db.connect_runtime(base_settings) as c:
                accepted.append(c.execute("select trace_ops.consume_rate_limit(%s, %s, %s)", ([key], [7], [3600])).fetchone()[0] == 0)
                c.commit()
        except Exception as exc:                                                 # pragma: no cover
            errors.append(type(exc).__name__)
    threads = [threading.Thread(target=hit) for _ in range(24)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and sum(accepted) == 7 and len(accepted) == 24
    with db.connect(base_settings) as admin:
        assert admin.execute("select hits from trace_ops.rate_limit_counters where key = %s", (key,)).fetchone()[0] == 7


def test_both_keys_must_have_room_and_refusals_are_not_counted(rt):
    u, ip = f"test-{uuid.uuid4().hex}:u", f"test-{uuid.uuid4().hex}:ip"
    call = lambda keys, limits: rt.execute("select trace_ops.consume_rate_limit(%s, %s, %s)", (keys, limits, [60] * len(keys))).fetchone()[0]  # noqa: E731
    assert call([u, ip], [5, 1]) == 0
    assert 1 <= call([u + "2", ip], [5, 1]) <= 60                         # the IP key is full: refused with Retry-After
    assert call([u + "2", ip + "2"], [5, 1]) == 0                         # the refused request was not counted for u2
    rt.commit()
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        call([u], [0])
    rt.rollback()


def test_the_limiter_fails_closed(base_settings, consent_document, users, monkeypatch):
    from app.api import ratelimit
    with make_client(base_settings, trace_demo_fixtures=True, trace_rate_limit_backend="memory") as c:
        u, h = candidate(c, users, grant=False) if False else (None, auth(token(users())))
        assert c.post("/api/v1/profile", headers=h, json={}).json()["error"] == "rate_limiter_unavailable"
    with make_client(base_settings, trace_demo_fixtures=True, rate_limit_mutation_user="not-a-rule") as c:
        assert c.post("/api/v1/profile", headers=auth(token(users())), json={}).status_code == 503

    def broken(conn, rules):
        raise ratelimit.RateLimiterUnavailable("db down")
    monkeypatch.setattr(ratelimit, "consume_shared", broken)
    with make_client(base_settings, trace_demo_fixtures=True) as c:
        r = c.post("/api/v1/profile", headers=auth(token(users())), json={})
        assert (r.status_code, r.json()["error"]) == (503, "rate_limiter_unavailable")


def test_clients_cannot_reach_the_counters(base_settings):
    with db.connect(base_settings) as conn:
        for role in ("anon", "authenticated", "service_role", "trace_app_user", "trace_reference_reader"):
            assert not conn.execute("select has_schema_privilege(%s, 'trace_ops', 'usage')", (role,)).fetchone()[0], role


# --------------------------------------------------------------------------- consent
@pytest.mark.parametrize("body", [
    {"decision": "granted", "consent_version": "x", "purpose": "deidentified_shared_dataset"},
    {"decision": "granted", "consent_version": "x", "user_id": str(uuid.uuid4())},
    {"decision": "granted", "consent_version": "x", "status": "granted"},
])
def test_consent_payloads_cannot_choose_purpose_owner_or_status(demo_client, users, body):
    h = auth(token(users()))
    for path in ("/api/v1/consent/resume-processing", "/api/v1/consent/contribution"):
        assert demo_client.post(path, headers=h, json=body).status_code == 422


@pytest.mark.parametrize("field", ["consent_status", "contribution_consent", "is_test_fixture", "dataset_class", "synthetic_demo_allowed"])
def test_profile_payloads_cannot_set_consent_or_dataset_fields(demo_client, users, field):
    assert demo_client.post("/api/v1/profile", headers=auth(token(users())), json={field: True}).status_code == 422


def install_contribution_doc(base_settings):
    from scripts.demo_setup import install_contribution_document
    with db.connect(base_settings) as conn:
        return install_contribution_document(conn)


def test_contribution_consent_is_separate_explicit_and_policy_controlled(base_settings, consent_document, users):
    from app.demo import consent as demo_consent
    status = install_contribution_doc(base_settings)
    try:
        with make_client(base_settings, trace_demo_fixtures=True) as c:
            u, h = candidate(c, users)                                        # resume processing granted, nothing else
            overview = c.get("/api/v1/consent", headers=h).json()
            purposes = {p["key"]: p for p in overview["purposes"]}
            assert overview["contribution_policy"] == "opt_in"
            assert purposes["personal_analysis"]["consent"]["active"] is True
            assert purposes["shared_contribution"]["consent"]["active"] is False      # never inferred
            assert purposes["shared_contribution"]["consent"]["decision"] is None
            assert purposes["shared_contribution"]["required_for_personal_pathways"] is False
            assert upload(c, h, PDF["SYN-001"]).status_code == 201                     # an upload is not contribution consent
            assert c.get("/api/v1/consent/contribution", headers=h).json()["active"] is False
            g = c.post("/api/v1/consent/contribution", headers=h,
                       json={"decision": "granted", "consent_version": demo_consent.CONTRIBUTION_VERSION}).json()
            assert g["active"] and g["purpose"] == "deidentified_shared_dataset" and g["document"]["text_verified"]
            assert c.get("/api/v1/consent/resume-processing", headers=h).json()["active"] is True     # unaffected
            w = c.post("/api/v1/consent/contribution", headers=h,
                       json={"decision": "withdrawn", "consent_version": demo_consent.CONTRIBUTION_VERSION}).json()
            assert w["active"] is False and w["decision"]["status"] == "withdrawn"
            assert c.get("/api/v1/consent/resume-processing", headers=h).json()["active"] is True     # personal use continues
        with make_client(base_settings, trace_demo_fixtures=True, trace_contribution_policy="disabled") as c:
            u, h = candidate(c, users)
            body = {"decision": "granted", "consent_version": demo_consent.CONTRIBUTION_VERSION}
            r = c.post("/api/v1/consent/contribution", headers=h, json=body)
            assert (r.status_code, r.json()["error"]) == (403, "contribution_not_offered")
            assert c.post("/api/v1/consent/contribution", headers=h, json={**body, "decision": "denied"}).status_code == 200
            assert c.get("/api/v1/consent", headers=h).json()["purposes"][1]["offered"] is False
        with make_client(base_settings, trace_demo_fixtures=True, trace_contribution_policy="required") as c:
            u, h = candidate(c, users)
            assert c.get("/api/v1/consent", headers=h).status_code == 503                      # unknown policy: fail closed
    finally:
        if status == "installed":
            with db.connect(base_settings) as conn:
                conn.autocommit = True
                conn.execute("delete from public.trace_consent_events where consent_version = %s", (demo_consent.CONTRIBUTION_VERSION,))
                conn.execute("delete from public.trace_consent_documents where consent_version = %s", (demo_consent.CONTRIBUTION_VERSION,))


# --------------------------------------------------------------------------- API surface
def test_api_docs_are_off_by_default_and_opt_in(base_settings, monkeypatch):
    from app.main import create_app
    with make_client(base_settings) as c:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert c.get(path).status_code == 404, path
    monkeypatch.setenv("TRACE_API_DOCS_ENABLED", "1")
    get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as c:
            assert c.get("/openapi.json").status_code == 200
    finally:
        monkeypatch.delenv("TRACE_API_DOCS_ENABLED")
        get_settings.cache_clear()


def test_errors_never_expose_stack_traces_or_sql(demo_client, users):
    h = auth(token(users()))
    for path in ("/api/v1/pathway?target=PM-PM'--", "/api/v1/insights?target=x&cohort=all", "/api/v1/records/employment/not-a-uuid/confirm"):
        r = demo_client.get(path, headers=h) if "records" not in path else demo_client.post(path, headers=h)
        assert r.status_code in (404, 422) and not re.search(r"Traceback|psycopg|select |relation|line \d", r.text, re.I), path


def test_no_secrets_are_tracked_in_git():
    files = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    assert not [f for f in files if re.search(r"(^|/)\.env($|\.)", f) and not f.endswith(".env.example")]
    assert not [f for f in files if f.endswith((".pem", ".key", ".p12")) or "local_only/" in f or f.endswith(".mcp.json")]
    out = subprocess.run(["git", "grep", "-I", "-n", "-E", r"sb_secret_[A-Za-z0-9_-]{8,}|GOCSPX-[A-Za-z0-9_-]{8,}|"
                          r"service_role\"?\s*[:=]\s*\"?eyJ|BEGIN (EC |RSA )?PRIVATE KEY|postgres(ql)?://[^:]+:[^@<]{8,}@"],   # <placeholders> are docs
                         cwd=REPO, capture_output=True, text=True)
    # Allowed: the well-known local Supabase default and the deliberately fake URL of the health test (port 1).
    hits = [l for l in out.stdout.splitlines() if "postgres:postgres@" not in l and "someuser:s3cret-pw@127.0.0.1:1/" not in l]
    assert hits == [], hits


def test_a_limiter_database_error_refuses_the_request():
    from app.api import ratelimit

    class Broken:
        def transaction(self):
            raise psycopg.OperationalError("connection lost")
    with pytest.raises(ratelimit.RateLimiterUnavailable):
        ratelimit.consume_shared(Broken(), [("k", (5, 60))])
