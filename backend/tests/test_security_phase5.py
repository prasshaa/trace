"""Phase 5 security regression: access control at the database, the API and the browser boundary.

Database checks run with the real client roles: `anon`, and `authenticated` with a request identity (the same
transaction-local claims Supabase sets for PostgREST), for two users A and B who each own a profile; B also holds
ingested records. Every statement that must fail is run inside a savepoint and rolled back; nothing persists except
the transient test users, which the fixtures delete.

Live Data API checks (PostgREST and Auth) run only with TRACE_TEST_SUPABASE_URL and TRACE_TEST_SUPABASE_ANON_KEY set.
"""
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import psycopg
import pytest

from app import db
from app.api.ratelimit import RateLimited, SlidingWindowLimiter, parse_rule
from app.config import Settings
from app.services.candidate_profile.repository import IDENTITY_SQL
from tests.test_api_foundation import KEY, assert_no_secrets, auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import EXTRACTOR, PDF, candidate, consent_document, demo_client, make_client, upload  # noqa: F401

pytestmark = pytest.mark.integration
WEB = Settings().trace_web_dir
OWNED = ["trace_candidate_profiles", "trace_candidate_employments", "trace_candidate_employers", "trace_candidate_education",
         "trace_candidate_evidence", "trace_candidate_skills", "trace_candidate_career_activities", "trace_candidate_career_breaks",
         "trace_candidate_corrections", "trace_resume_documents", "trace_extraction_runs", "trace_consent_events",
         "trace_pathway_runs", "trace_pathway_recommendations", "trace_pathway_route_steps", "trace_pathway_evidence",
         "trace_agent_threads", "trace_agent_messages"]
REFERENCE = ["trace_role_taxonomy", "trace_role_aliases", "trace_role_families", "trace_seniority_levels", "trace_responsibility_areas",
             "trace_skills", "trace_skill_aliases", "trace_skill_families", "trace_consent_documents", "trace_target_role_sources",
             "trace_target_role_requirements"]


# --------------------------------------------------------------------------- helpers
class Session:
    """A connection acting as a client role. Each attempt runs in a savepoint that is always rolled back."""

    def __init__(self, conn, role, uid=None):
        self.conn, self.role, self.uid = conn, role, uid

    def run(self, sql, params=None):
        with self.conn.transaction(force_rollback=True):
            self.conn.execute(f"set local role {self.role}")
            if self.uid:
                self.conn.execute(IDENTITY_SQL, {"sub": str(self.uid), "claims": json.dumps({"sub": str(self.uid), "role": "authenticated"})})
            cur = self.conn.execute(sql, params)
            return cur.fetchall() if cur.description else cur.rowcount

    def denied(self, sql, params=None):
        """The statement fails (privilege, RLS check, foreign key or trigger), or affects no rows."""
        try:
            result = self.run(sql, params)
        except psycopg.Error as exc:
            return type(exc).__name__
        return "no rows affected" if result in (0, []) else None              # rows changed/returned: NOT denied


@pytest.fixture
def world(demo_client, users, base_settings):
    """User A (profile, no records) and user B (profile with an ingested synthetic sample)."""
    a, ha = candidate(demo_client, users)
    b, hb = candidate(demo_client, users)
    assert upload(demo_client, hb, PDF["SYN-002"]).status_code == 201
    conn = db.connect(base_settings)
    conn.autocommit = False
    q = lambda sql, *p: conn.execute(sql, p).fetchone()[0]                  # noqa: E731  (table owner reads)
    ids = {"a": a, "b": b, "ha": ha, "hb": hb,
           "pa": q("select candidate_profile_id from public.trace_candidate_profiles where owner_user_id = %s", a),
           "pb": q("select candidate_profile_id from public.trace_candidate_profiles where owner_user_id = %s", b),
           "eb": q("select candidate_employment_id from public.trace_candidate_employments where owner_user_id = %s limit 1", b),
           "xb": q("select candidate_evidence_id from public.trace_candidate_evidence where owner_user_id = %s limit 1", b),
           "rb": q("select resume_id from public.trace_resume_documents where owner_user_id = %s limit 1", b),
           "empb": q("select candidate_employer_id from public.trace_candidate_employers where owner_user_id = %s limit 1", b)}
    conn.rollback()
    ids["A"], ids["B"], ids["anon"] = Session(conn, "authenticated", a), Session(conn, "authenticated", b), Session(conn, "anon")
    yield ids
    conn.close()


# --------------------------------------------------------------------------- 1. anonymous
def test_anonymous_cannot_read_or_write_private_data(world):
    anon = world["anon"]
    for t in OWNED:
        assert anon.denied(f"select * from public.{t}"), t                                   # no rows or no privilege
        assert anon.denied(f"delete from public.{t}"), t
    assert anon.denied("insert into public.trace_candidate_profiles (owner_user_id) values (%s)", (world["b"],))
    assert anon.denied("select * from trace_ref.profiles") == "InsufficientPrivilege"
    assert anon.denied("select trace_review.request_uid()") == "InsufficientPrivilege"


# --------------------------------------------------------------------------- 2-3. cross-user access
def test_user_a_cannot_read_modify_or_delete_user_b(world):
    A, w = world["A"], world
    assert A.run("select count(*) from public.trace_candidate_profiles")[0][0] == 1          # only A's own
    for t, col in (("trace_candidate_profiles", "candidate_profile_id"), ("trace_candidate_employments", "candidate_profile_id"),
                   ("trace_candidate_evidence", "candidate_profile_id"), ("trace_resume_documents", "candidate_profile_id"),
                   ("trace_candidate_corrections", "candidate_profile_id"), ("trace_extraction_runs", "candidate_profile_id")):
        assert A.run(f"select count(*) from public.{t} where {col} = %s", (w["pb"],))[0][0] == 0, t
    assert A.denied("update public.trace_candidate_employments set title_raw = 'hijacked' where candidate_employment_id = %s", (w["eb"],))
    assert A.denied("update public.trace_candidate_evidence set evidence_text = 'hijacked text' where candidate_evidence_id = %s", (w["xb"],))
    assert A.denied("delete from public.trace_candidate_employments where candidate_employment_id = %s", (w["eb"],))
    assert A.denied("delete from public.trace_resume_documents where resume_id = %s", (w["rb"],))
    assert A.denied("delete from public.trace_candidate_profiles where candidate_profile_id = %s", (w["pb"],))
    assert A.denied("select * from trace_review.correction_history_for(%s)", (w["eb"],))


def test_user_a_cannot_create_records_linked_to_user_b(world):
    A, w = world["A"], world
    attempts = [
        ("insert into public.trace_candidate_employments (owner_user_id, candidate_profile_id, sequence_index, title_raw) "
         "values (%s, %s, 99, 'x')", (w["a"], w["pb"])),                                             # B's profile, A as owner
        ("insert into public.trace_candidate_employments (owner_user_id, candidate_profile_id, sequence_index, title_raw) "
         "values (%s, %s, 99, 'x')", (w["b"], w["pb"])),                                             # posing as B
        ("insert into public.trace_candidate_evidence (owner_user_id, candidate_profile_id, candidate_employment_id, evidence_type, evidence_text) "
         "values (%s, %s, %s, 'responsibility', 'linked to B employment')", (w["a"], w["pa"], w["eb"])),   # A's profile, B's job
        ("insert into public.trace_candidate_employments (owner_user_id, candidate_profile_id, candidate_employer_id, sequence_index, title_raw) "
         "values (%s, %s, %s, 99, 'x')", (w["a"], w["pa"], w["empb"])),                                # B's employer
        ("insert into public.trace_candidate_skills (owner_user_id, candidate_profile_id, candidate_evidence_id, skill_text_raw) "
         "values (%s, %s, %s, 'x')", (w["a"], w["pa"], w["xb"])),                                      # B's evidence
        ("insert into public.trace_resume_documents (candidate_profile_id, byte_size, content_sha256, mime_type, storage_object_path) "
         "values (%s, 10, repeat('a', 64), 'application/pdf', %s)", (w["pb"], f"{w['a']}/x.pdf")),
        ("update public.trace_resume_documents set candidate_profile_id = %s where owner_user_id = %s", (w["pb"], w["a"])),
        ("insert into public.trace_agent_threads (candidate_profile_id) values (%s)", (w["pb"],)),
        ("insert into public.trace_pathway_runs (owner_user_id, candidate_profile_id) values (%s, %s)", (w["a"], w["pa"])),
        ("insert into public.trace_consent_events (user_id, purpose, status, consent_version) "
         "values (%s, 'resume_processing', 'granted', 'x')", (w["b"],)),
    ]
    for sql, params in attempts:
        assert A.denied(sql, params), sql


# --------------------------------------------------------------------------- 4. privileged fields
@pytest.mark.parametrize("sql", [
    "update public.trace_candidate_profiles set owner_user_id = gen_random_uuid()",
    "update public.trace_candidate_profiles set is_test_fixture = true",
    "update public.trace_candidate_profiles set fixture_profile_id = 'SYN-003'",
    "update public.trace_candidate_profiles set confirmation_status = 'confirmed'",
    "update public.trace_candidate_employments set review_status = 'user_confirmed'",
    "update public.trace_candidate_employments set owner_user_id = gen_random_uuid()",
    "update public.trace_candidate_employments set role_id = 'PM-DIR', mapping_status = 'confirmed'",
    "update public.trace_candidate_employments set information_origin = 'source_extracted'",
    "update public.trace_candidate_evidence set verification_status = 'verified'",
    "update public.trace_candidate_evidence set area_classification = array['roadmap_ownership']",
    "update public.trace_resume_documents set is_test_fixture = false",
    "update public.trace_consent_events set status = 'granted'",
    "delete from public.trace_consent_events",
    "update public.trace_candidate_corrections set changed_fields = '{}'",
])
def test_users_cannot_change_system_controlled_fields_of_their_own_records(world, sql):
    assert world["B"].denied(sql), sql


def test_inserts_cannot_set_system_controlled_fields(world):
    A, w = world["A"], world
    for sql in ("insert into public.trace_candidate_profiles (owner_user_id, is_test_fixture) values (%s, true)",
                "insert into public.trace_candidate_profiles (owner_user_id, fixture_profile_id) values (%s, 'SYN-001')"):
        assert A.denied(sql, (w["a"],)), sql
    assert A.denied("insert into public.trace_candidate_employments (owner_user_id, candidate_profile_id, sequence_index, title_raw, review_status) "
                    "values (%s, %s, 1, 'x', 'user_confirmed')", (w["a"], w["pa"]))
    # Phase 6: the Data API role cannot insert at all; the backend's owner role still gets system values forced.
    assert A.denied("insert into public.trace_candidate_employments (owner_user_id, candidate_profile_id, sequence_index, title_raw) "
                    "values (%s, %s, 1, 'Draft')", (w["a"], w["pa"])) == "InsufficientPrivilege"
    owner = Session(A.conn, "trace_app_user", w["a"])
    row = owner.run("insert into public.trace_candidate_employments (owner_user_id, candidate_profile_id, sequence_index, title_raw) "
                    "values (%s, %s, 1, 'Draft') returning review_status::text, information_origin::text, role_id", (w["a"], w["pa"]))
    assert row == [("pending_review", "user_stated", "ROLE-UNKNOWN")]


# --------------------------------------------------------------------------- 5. reference data
@pytest.mark.parametrize("table", REFERENCE)
def test_reference_tables_are_read_only_for_clients(world, table):
    for who in ("A", "anon"):
        s = world[who]
        assert s.denied(f"delete from public.{table}") == "InsufficientPrivilege", (who, table)
        assert s.denied(f"update public.{table} set updated_at = now()") == "InsufficientPrivilege", (who, table)
        assert s.denied(f"insert into public.{table} default values") == "InsufficientPrivilege", (who, table)
        assert s.denied(f"truncate public.{table}") == "InsufficientPrivilege", (who, table)


def test_reference_cohort_and_data_sources_are_not_reachable_by_clients(world):
    for who in ("A", "anon"):
        for t in ("trace_ref.profiles", "trace_ref.data_sources", "trace_ref.employments", "trace_ref.v_mode_profiles",
                  "trace_ref.v_eligible_profiles", "trace_ingest.approved_fixture_resumes"):
            assert world[who].denied(f"select * from {t}") == "InsufficientPrivilege", (who, t)


def test_rls_is_enabled_and_client_grants_are_the_reviewed_set(base_settings):
    with db.connect(base_settings) as conn:
        off = conn.execute("select n.nspname || '.' || c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                           "where c.relkind in ('r', 'p') and n.nspname in ('public', 'trace_ref', 'trace_ingest') "
                           "and not c.relrowsecurity").fetchall()
        assert off == []
        anon = conn.execute("select table_name, privilege_type from information_schema.role_table_grants where grantee = 'anon' "
                            "and table_schema not in ('information_schema', 'pg_catalog', 'graphql', 'graphql_public', 'storage', 'realtime', "
                            "'extensions', 'auth', 'vault', 'net', 'supabase_functions', 'pgsodium', 'cron')").fetchall()
        assert {p for _, p in anon} <= {"SELECT"} and {t for t, _ in anon} <= set(REFERENCE) - {"trace_target_role_sources",
                                                                                               "trace_target_role_requirements"}
        writes = conn.execute("select distinct table_name, privilege_type from information_schema.role_table_grants "
                              "where grantee = 'authenticated' and table_schema = 'public' and privilege_type in "
                              "('INSERT', 'UPDATE', 'TRUNCATE', 'REFERENCES', 'TRIGGER')").fetchall()
        assert writes == []                                   # writes only via column grants on owned tables + guards


# --------------------------------------------------------------------------- 6. views and definer functions
def test_exposed_views_cannot_bypass_rls(base_settings):
    with db.connect(base_settings) as conn:
        exposed = conn.execute("select c.relname, coalesce(array_to_string(c.reloptions, ','), '') from pg_class c "
                               "join pg_namespace n on n.oid = c.relnamespace where c.relkind in ('v', 'm') "
                               "and n.nspname in ('public', 'graphql_public')").fetchall()
        assert all("security_invoker=true" in opts for _, opts in exposed), exposed
        reachable = conn.execute("select n.nspname || '.' || c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                                 "where c.relkind in ('v', 'm') and n.nspname like 'trace%' and (has_table_privilege('anon', c.oid, 'select') "
                                 "or has_table_privilege('authenticated', c.oid, 'select'))").fetchall()
        assert reachable == []                                # trace_ref views: no client privilege at all


def test_security_definer_functions_are_pinned_and_not_exposed(base_settings):
    with db.connect(base_settings) as conn:
        rows = conn.execute("select n.nspname, p.proname, coalesce(array_to_string(p.proconfig, ','), ''), "
                            "has_function_privilege('anon', p.oid, 'execute'), pg_get_userbyid(p.proowner) "
                            "from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
                            "where p.prosecdef and n.nspname in ('public', 'trace_review', 'trace_util', 'trace_ingest', 'trace_ref', 'trace_private', "
                            "'trace_ops', 'trace_contrib', 'trace_plan')").fetchall()
    assert rows
    for schema, name, config, anon_exec, owner in rows:
        assert 'search_path=""' in config, name
        assert not anon_exec, name
        assert schema != "public", name                       # nothing callable through the Data API's RPC
        assert owner != "postgres" or name in ("request_uid", "enforce_profile_consent_integrity", "enforce_recommendation_thresholds",
                                               "enforce_pathway_evidence_eligibility", "eligible_real_profile_ids",
                                               "remove_contributions", "on_contribution_consent_event",
                                               # v2.31.0 (D48): consent-event trigger deleting markers / the import draft
                                               "on_consent_event_privacy",
                                               "withdraw_participant", "on_contribution_deleted",
                                               # v2.9.0 automatic contributions (trace_private helpers, trace_contrib entry points)
                                               "contribution_consent_active", "set_contribution_state", "enqueue_contribution",
                                               "on_candidate_change", "write_reference_rows", "claim_contribution_requests",
                                               "contribution_context", "contribution_identity", "record_contribution_outcome",
                                               "log_resume_processing", "quarantine_resume", "my_contribution_status",
                                               "my_resume_processing",
                                               # v2.10.0 contribution safety (marker store, bounded retries)
                                               "store_identity_markers", "suspend_contribution", "exhaust_contribution_request",
                                               "report_contribution_failure", "release_contribution_requests",
                                               # v2.11.0 marker lifecycle
                                               "forget_identity_markers",
                                               # v2.12.0 sharing preference
                                               "sharing_is_private", "exclude_from_shared_pool", "my_sharing_preference",
                                               "acknowledge_data_use_notice", "set_sharing_preference",
                                               # v2.13.0 own-contribution exclusion
                                               "my_reference_profile_ids",
                                               # v2.23.0 completed plan runs are immutable (reads the parent run's status)
                                               "guard_pathway_child"), (name, owner)


def test_resume_storage_bucket_is_private(base_settings):
    with db.connect(base_settings) as conn:
        assert conn.execute("select bool_or(public) from storage.buckets").fetchone()[0] in (False, None)


# --------------------------------------------------------------------------- 7-9. API identity and relabelling
@pytest.mark.parametrize("body", [{"owner_user_id": str(uuid.uuid4())}, {"is_test_fixture": True}, {"fixture_profile_id": "SYN-003"},
                                  {"dataset_class": "synthetic_test_fixture"}, {"confirmation_status": "confirmed"}])
def test_profile_creation_refuses_privileged_fields(demo_client, users, body):
    h = auth(token(users()))
    resp = demo_client.post("/api/v1/profile", headers=h, json=body)
    assert resp.status_code == 422 and resp.json()["error"] == "invalid_request"


def test_caller_supplied_identity_never_overrides_the_token(world, demo_client):
    w = world
    spoof = {**w["ha"], "X-User-Id": str(w["b"]), "X-Owner-User-Id": str(w["b"]), "X-Candidate-Profile-Id": str(w["pb"])}
    me = demo_client.get("/api/v1/me", headers=spoof, params={"user_id": str(w["b"]), "owner_user_id": str(w["b"])}).json()
    assert me["user_id"] == str(w["a"])
    records = demo_client.get("/api/v1/profile/records", headers=spoof, params={"candidate_profile_id": str(w["pb"])}).json()
    assert records["employments"] == []                                   # A's own (empty) profile, not B's
    for method, path, body in (("post", f"/api/v1/records/employment/{w['eb']}/confirm", None),
                               ("post", f"/api/v1/records/employment/{w['eb']}/corrections", {"title_raw": "x"}),
                               ("get", f"/api/v1/records/employment/{w['eb']}/corrections", None),
                               ("post", f"/api/v1/employments/{w['eb']}/map", None),
                               ("get", f"/api/v1/employments/{w['eb']}/role-options", None),
                               ("post", f"/api/v1/employments/{w['eb']}/role", {"role_id": "PM-PM"}),
                               ("put", f"/api/v1/evidence/{w['xb']}/classification", {"areas": ["roadmap_ownership"]})):
        resp = getattr(demo_client, method)(path, headers=w["ha"], **({"json": body} if body is not None else {}))
        assert resp.status_code == 404, (path, resp.status_code)
        assert str(w["eb"]) not in resp.text and str(w["xb"]) not in resp.text


@pytest.mark.parametrize("path, body", [
    ("/api/v1/records/employment/{id}/corrections", {"title_raw": "x", "review_status": "user_confirmed"}),
    ("/api/v1/records/employment/{id}/corrections", {"owner_user_id": "00000000-0000-0000-0000-000000000000"}),
    ("/api/v1/records/employment/{id}/corrections", {"role_id": "PM-DIR"}),
    ("/api/v1/employments/{id}/role", {"role_id": "PM-PM", "mapping_basis": "alias_match"}),
])
def test_mutation_bodies_refuse_system_fields(world, demo_client, path, body):
    resp = demo_client.post(path.format(id=world["eb"]), headers=world["hb"], json=body)
    assert resp.status_code == 422


def test_synthetic_designation_comes_only_from_the_bytes(demo_client, users, base_settings):
    u, h = candidate(demo_client, users)
    resp = demo_client.post("/api/v1/resumes?fixture_profile_id=SYN-003&is_test_fixture=false",
                            headers={**h, "Content-Type": "application/pdf", "X-Filename": "SYN-003_synthetic_resume.pdf",
                                     "X-Fixture-Id": "SYN-003", "X-Is-Test-Fixture": "false"}, content=PDF["SYN-001"])
    assert resp.status_code == 201
    with db.connect(base_settings) as conn:
        row = conn.execute("select is_test_fixture, fixture_profile_id from public.trace_candidate_profiles where owner_user_id = %s", (u,)).fetchone()
    assert row == (True, "SYN-001")


def test_unapproved_bytes_are_refused_whatever_the_name_or_type(demo_client, users):
    u, h = candidate(demo_client, users)
    fake = b"%PDF-1.4\n" + uuid.uuid4().bytes * 20
    for name in ("SYN-001_synthetic_resume.pdf", "resume.pdf"):
        assert upload(demo_client, h, fake, name=name).status_code == 422
    assert upload(demo_client, h, PDF["SYN-001"], content_type="application/x-pdf").status_code == 415
    assert upload(demo_client, h, PDF["SYN-001"], content_type="text/plain").status_code == 415


# --------------------------------------------------------------------------- 10. rate limits
def test_limiter_sliding_window():
    now = [0.0]
    lim = SlidingWindowLimiter(clock=lambda: now[0])
    rule = parse_rule("2/10")
    lim.check([("u", rule)]); lim.check([("u", rule)])
    with pytest.raises(RateLimited) as exc:
        lim.check([("u", rule)])
    assert 1 <= exc.value.retry_after <= 11
    lim.check([("other", rule)])                                         # independent key
    now[0] = 10.5
    lim.check([("u", rule)])                                             # window slid


def test_limiter_requires_every_key_and_does_not_count_refusals():
    lim = SlidingWindowLimiter(clock=lambda: 0.0)
    user, ip = parse_rule("5/60"), parse_rule("1/60")
    lim.check([("user:a", user), ("ip:1", ip)])
    with pytest.raises(RateLimited):
        lim.check([("user:b", user), ("ip:1", ip)])                      # the IP limit applies across users
    lim.check([("user:b", user), ("ip:2", ip)])                          # the refused request was not counted for user:b
    with pytest.raises(ValueError):
        parse_rule("0/60")


def test_uploads_are_rate_limited_at_the_backend(base_settings, consent_document, users):
    with make_client(base_settings, trace_demo_fixtures=True, rate_limit_upload_user="2/3600") as c:
        u, h = candidate(c, users)
        codes = [upload(c, h, b"%PDF-1.4 not approved").status_code for _ in range(3)]
        assert codes == [422, 422, 429]
        assert upload(c, h, PDF["SYN-001"]).status_code == 429           # even a valid file, before reading it


def test_mutations_are_rate_limited_per_user_and_per_ip(base_settings, consent_document, users):
    with make_client(base_settings, trace_demo_fixtures=True, rate_limit_mutation_user="3/60", rate_limit_mutation_ip="5/60") as c:
        u, h = candidate(c, users)                                       # 1 profile creation + 1 consent = 2 mutations
        assert c.post("/api/v1/consent/resume-processing", headers=h, json={"decision": "denied", "consent_version": "x"}).status_code == 409
        assert c.post("/api/v1/consent/resume-processing", headers=h, json={"decision": "denied", "consent_version": "x"}).status_code == 429
        # IP so far: profile + consent + one accepted consent attempt (the refused one is not counted) = 3 of 5
        assert c.post("/api/v1/profile", headers=auth(token(users())), json={}).status_code == 201   # IP: 4 of 5
        assert c.post("/api/v1/profile", headers=auth(token(users())), json={}).status_code == 201   # IP: 5 of 5
        assert c.post("/api/v1/profile", headers=auth(token(users())), json={}).status_code == 429   # another user, same IP


def test_pathway_is_rate_limited(base_settings, consent_document, users):
    with make_client(base_settings, trace_demo_fixtures=True, rate_limit_read_user="1/60") as c:
        u, h = candidate(c, users)
        assert c.get("/api/v1/pathway?target=PM-PM", headers=h).status_code == 200
        assert c.get("/api/v1/pathway?target=PM-PM", headers=h).status_code == 429


def test_rate_limits_can_be_disabled_by_the_server_only(base_settings, consent_document, users):
    with make_client(base_settings, trace_demo_fixtures=True, rate_limit_read_user="1/60", trace_rate_limits_enabled=False) as c:
        u, h = candidate(c, users)
        assert [c.get("/api/v1/pathway?target=PM-PM", headers=h).status_code for _ in range(3)] == [200, 200, 200]
    with make_client(base_settings, trace_demo_fixtures=True, rate_limit_read_user="1/60") as c:
        u, h = candidate(c, users)
        c.get("/api/v1/pathway?target=PM-PM", headers=h)
        resp = c.get("/api/v1/pathway?target=PM-PM&rate_limits=off", headers={**h, "X-Rate-Limit": "off"})
        assert resp.status_code == 429


def test_token_from_another_issuer_key_is_rejected(demo_client, users):
    from cryptography.hazmat.primitives.asymmetric import ec
    other = ec.generate_private_key(ec.SECP256R1())
    u = users()
    assert demo_client.get("/api/v1/me", headers=auth(token(u, key=other))).status_code == 401
    assert demo_client.get("/api/v1/me", headers=auth(token(u, role="service_role"))).status_code == 401
    assert demo_client.get("/api/v1/me", headers=auth(token(u, key=KEY))).status_code == 200


# --------------------------------------------------------------------------- demo sample endpoints
def test_sample_endpoints_need_a_token_and_demo_mode(base_settings, consent_document, users, demo_client):
    assert demo_client.get("/api/v1/demo/samples").status_code == 401
    assert demo_client.get("/api/v1/demo/samples/1/resume.pdf").status_code == 401
    with make_client(base_settings) as off:
        h = auth(token(users()))
        assert off.get("/api/v1/demo/samples", headers=h).status_code == 403
        assert off.get("/api/v1/demo/samples/1/resume.pdf", headers=h).status_code == 403


def test_samples_are_exactly_the_approved_fixtures(demo_client, users, base_settings):
    h = auth(token(users()))
    samples = demo_client.get("/api/v1/demo/samples", headers=h)
    assert samples.status_code == 200
    assert_no_secrets(samples)
    assert "SYN-" not in samples.text and "sha" not in samples.text.lower()
    with db.connect(base_settings) as conn:
        approved = dict(conn.execute("select fixture_profile_id, pdf_sha256 from trace_ingest.approved_fixture_resumes").fetchall())
    assert sorted(s["number"] for s in samples.json()) == sorted(int(f.split("-")[1]) for f in approved)
    pdf = demo_client.get("/api/v1/demo/samples/1/resume.pdf", headers=h)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.headers["cache-control"] == "no-store"
    assert hashlib.sha256(pdf.content).hexdigest() == approved["SYN-001"]
    assert demo_client.get("/api/v1/demo/samples/999/resume.pdf", headers=h).status_code == 404
    assert demo_client.get("/api/v1/demo/samples/0/resume.pdf", headers=h).status_code == 422


# --------------------------------------------------------------------------- 11. browser assets and headers
SECRET_PATTERNS = [r"service_role", r"SERVICE_ROLE", r"sb_secret_[A-Za-z0-9_-]{8,}", r"GOCSPX-", r"postgres(ql)?://", r"JWT_SECRET",
                   r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", r"BEGIN (EC |RSA )?PRIVATE KEY", r"DATABASE_URL",
                   r"password\s*[:=]\s*[\"'`][^\"'`]{4,}"]


def web_files():
    return [p for p in WEB.rglob("*") if p.is_file()]


def test_browser_assets_contain_no_privileged_credentials():
    assert web_files()
    for p in web_files():
        text = p.read_text(errors="ignore")
        for pat in SECRET_PATTERNS:
            if p.suffix == ".js" and "vendor" in p.parts and pat in (r"service_role", r"SERVICE_ROLE"):
                continue                                                  # the SDK's own identifiers, no values
            assert not re.search(pat, text), (p.name, pat)
        assert not p.name.endswith(".map") and "sourceMappingURL" not in text, p.name


def test_app_code_never_renders_html_or_calls_supabase_data_apis():
    app_js = [p for p in web_files() if p.suffix == ".js" and "vendor" not in p.parts]
    assert len(app_js) >= 8
    for p in app_js:
        text = p.read_text()
        for bad in ("innerHTML =", "innerHTML=", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                    ".from(", ".rpc(", "/rest/v1", "/storage/v1", "/functions/v1", ".storage", ".functions", "localStorage.setItem",
                    "sessionStorage.setItem"):
            assert bad not in text, (p.name, bad)
        for m in re.finditer(r"fetch\(\s*([`'\"])(.*?)\1", text):
            assert m.group(2).startswith(("/api/v1/", "${supabaseUrl")), (p.name, m.group(2))


def test_index_loads_only_local_pinned_scripts():
    html = (WEB / "index.html").read_text()
    scripts = re.findall(r"<script[^>]*src=\"([^\"]+)\"", html)
    assert scripts and all(s.startswith("/app/") for s in scripts)
    assert "<script>" not in html and "style=" not in html and "on" + "click" not in html
    vendor = WEB / "vendor" / "supabase-js-2.119.0.umd.js"
    import base64
    sri = "sha384-" + base64.b64encode(hashlib.sha384(vendor.read_bytes()).digest()).decode()
    assert f'integrity="{sri}"' in html


def test_security_headers_on_app_and_api(demo_client):
    app = demo_client.get("/app/")
    assert app.status_code == 200
    api = demo_client.get("/api/v1/config")
    for resp in (app, api):
        csp = resp.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp and "unsafe" not in csp
        assert "connect-src 'self' http://127.0.0.1:54321" in csp
        assert resp.headers["x-content-type-options"] == "nosniff" and resp.headers["referrer-policy"] == "no-referrer"
        assert resp.headers["x-frame-options"] == "DENY"
    assert api.headers["cache-control"] == "no-store"
    assert demo_client.get("/", follow_redirects=False).headers["location"] == "/app/"
    assert demo_client.get("/app/../app/config.py").status_code == 404
    assert "access-control-allow-origin" not in demo_client.get("/api/v1/config", headers={"Origin": "https://evil.example"}).headers


# --------------------------------------------------------------------------- optional: the live Supabase Data API
LIVE = os.environ.get("TRACE_TEST_SUPABASE_URL"), os.environ.get("TRACE_TEST_SUPABASE_ANON_KEY")


def _http(method, url, key, bearer=None, body=None):
    headers = {"apikey": key, "Authorization": f"Bearer {bearer or key}", "Content-Type": "application/json", "Prefer": "return=representation"}
    req = urllib.request.Request(url, method=method, headers=headers, data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


@pytest.mark.skipif(not all(LIVE), reason="set TRACE_TEST_SUPABASE_URL and TRACE_TEST_SUPABASE_ANON_KEY to run")
def test_live_data_api_enforces_the_same_boundaries(base_settings):
    url, key = LIVE
    rest = f"{url.rstrip('/')}/rest/v1"
    email, password = f"sec-{uuid.uuid4().hex[:8]}@example.invalid", uuid.uuid4().hex
    status, session = _http("POST", f"{url.rstrip('/')}/auth/v1/signup", key, body={"email": email, "password": password})
    assert status == 200 and session.get("access_token")
    user_token = session["access_token"]
    try:
        for t in ("trace_candidate_profiles", "trace_candidate_employments", "trace_consent_events", "trace_resume_documents"):
            status, rows = _http("GET", f"{rest}/{t}", key)                                          # anon: no privilege
            assert status in (401, 403) or rows == [], t
        for bearer in (None, user_token):
            assert _http("PATCH", f"{rest}/trace_role_taxonomy?role_id=eq.PM-PM", key, bearer, {"canonical_title": "x"})[0] in (401, 403)
            assert _http("POST", f"{rest}/trace_skills", key, bearer, {"skill_id": "SKL-X"})[0] in (401, 403)
            assert _http("DELETE", f"{rest}/trace_responsibility_areas?responsibility_area=eq.data_analysis", key, bearer)[0] in (401, 403)
        assert _http("GET", f"{rest}/trace_role_taxonomy?select=role_id&limit=1", key)[0] == 200                # read-only lookup
        uid = session["user"]["id"]
        # Phase 6: the Data API has NO write path on private tables at all; every write goes through the Trace API.
        assert _http("POST", f"{rest}/trace_candidate_profiles", key, user_token, {"owner_user_id": uid})[0] in (401, 403)
        with db.connect(base_settings) as admin:                         # a profile created the supported way
            admin.autocommit = True
            admin.execute("insert into public.trace_candidate_profiles (owner_user_id) values (%s)", (uid,))
        mine = f"{rest}/trace_candidate_profiles?owner_user_id=eq.{uid}"
        assert len(_http("GET", mine, key, user_token)[1]) == 1                                          # own rows readable
        for body in ({"is_test_fixture": True}, {"fixture_profile_id": "SYN-001"}, {"owner_user_id": str(uuid.uuid4())},
                     {"headline": "My headline"}):
            assert _http("PATCH", mine, key, user_token, body)[0] in (401, 403), body
        assert _http("DELETE", mine, key, user_token)[0] in (401, 403)
        profile = _http("GET", mine + "&select=candidate_profile_id", key, user_token)[1][0]["candidate_profile_id"]
        assert _http("POST", f"{rest}/trace_candidate_employments", key, user_token,
                     {"owner_user_id": uid, "candidate_profile_id": profile, "sequence_index": 1, "title_raw": "x"})[0] in (401, 403)
        assert _http("POST", f"{rest}/trace_consent_events", key, user_token,
                     {"purpose": "deidentified_shared_dataset", "status": "granted", "consent_version": "x"})[0] in (401, 403)
        assert _http("POST", f"{rest}/trace_resume_documents", key, user_token, {"candidate_profile_id": profile})[0] in (401, 403)
        for rpc in ("review_record", "designate_fixture", "consume_rate_limit", "withdraw_participant", "eligible_real_profile_ids",
                    "admit_deidentified_resume", "start_real_extraction", "record_contribution_outcome", "my_contribution_status",
                    "claim_contribution_requests", "contribution_identity", "quarantine_resume", "store_identity_markers",
                    "report_contribution_failure", "release_contribution_requests", "retry_contribution",
                    "forget_identity_markers", "purge_orphan_identity_markers", "purge_contribution_queue_events"):
            assert _http("POST", f"{rest}/rpc/{rpc}", key, user_token, {})[0] == 404, rpc
        assert _http("POST", f"{rest}/trace_candidate_profiles", key, user_token,
                     {"owner_user_id": str(uuid.uuid4())})[0] in (401, 403, 409)
        assert _http("GET", f"{rest}/v_mode_profiles", key, user_token)[0] == 404
    finally:
        with db.connect(base_settings) as conn:
            conn.autocommit = True
            conn.execute("delete from auth.users where email = %s", (email,))


def test_samples_follow_the_database_allowlist_not_the_fixture_files(demo_client, users, monkeypatch):
    from app.api import demo_routes
    real = demo_routes.approved_pair
    monkeypatch.setattr(demo_routes, "approved_pair", lambda conn, f, sha: f != "SYN-002" and real(conn, f, sha))
    h = auth(token(users()))
    numbers = [s["number"] for s in demo_client.get("/api/v1/demo/samples", headers=h).json()]
    assert 2 not in numbers and 1 in numbers
    assert demo_client.get("/api/v1/demo/samples/2/resume.pdf", headers=h).status_code == 404
    monkeypatch.setattr(demo_routes, "approved_pair", lambda conn, f, sha: False)
    assert demo_client.get("/api/v1/demo/samples", headers=h).json() == []
