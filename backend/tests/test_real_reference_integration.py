"""Consented real reference pool (Phase 6): admission, separation, eligibility, withdrawal, cohort APIs.

Every test that writes reference or private data does so inside one admin transaction that is ALWAYS rolled back
(deferred constraints are checked first), and `no_test_rows_remain` verifies that nothing persisted. All people and
contact details are invented test values.
"""
import json
import uuid

import psycopg
import pytest

from app import db
from app.config import Settings
from app.services.career_insights import YourProfile, build_insights, load_rows
from app.services.candidate_profile.repository import IDENTITY_SQL
from app.services.deidentification import Attestation, Identity, admit, deidentify
from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import PDF, candidate, consent_document, demo_client, make_client, upload  # noqa: F401
from tests.test_deidentification import record

pytestmark = pytest.mark.integration
VERIFIED = Attestation(basis="product_owner_attestation", purposes=["deidentified_career_analysis", "aggregate_research",
                       "benchmarking", "recommendations"], evidence_reference="Owner confirmation, consent register row 7",
                       attested_by="Product owner", verification_status="verified", verified_by="Data steward",
                       verified_at="2026-11-13T10:00:00Z")
ID = Identity(full_name="Rohan Iyer", emails=["rohan.iyer@example.com"], phones=["+91 99887 76655"])


def person(i):
    rec = record()
    rec["employers"][1]["employer_name"] = "Northwind Logistics"                 # no name part: no review needed
    return rec, Identity(full_name=f"Test Person{i} Iyer", other_names=["Meera Kulkarni"], emails=["meera.k@example.com"],
                         phones=["+91 98200 11223"], handles=["meerak"], urls=["https://meera.example.dev"])


@pytest.fixture
def tx(base_settings):
    conn = db.connect(base_settings)
    conn.autocommit = False
    conn.execute("select 1")             # open the OUTER transaction now: every later conn.transaction() is a savepoint
    assert conn.info.transaction_status == psycopg.pq.TransactionStatus.INTRANS
    yield conn
    try:
        if conn.info.transaction_status == psycopg.pq.TransactionStatus.INTRANS:
            conn.execute("set constraints all immediate")                      # deferred FKs must hold too
    finally:
        conn.rollback()
        conn.close()


def admit_people(conn, n, attestation=VERIFIED, prefix=None):
    prefix = prefix or f"T6-{uuid.uuid4().hex[:6]}"
    ids = []
    for i in range(n):
        rec, ident = person(i)
        res = deidentify(rec, ident)
        assert res.status == "passed", res.report
        ids.append(admit(conn, f"{prefix}-{i:03d}", res, attestation, reviewer="Reviewer A"))
    return ids


def eligible(conn):
    return {r[0] for r in conn.execute("select profile_id from trace_ref.v_eligible_profiles").fetchall()}


# --------------------------------------------------------------------------- admission and separation
def test_an_admitted_record_is_real_only_identity_free_and_mapped_privately(tx):
    [pid] = admit_people(tx, 1)
    row = tx.execute("select dataset_class::text, contributor_user_id, source_consent_event_id, synthetic_demo_allowed, review_status::text "
                     "from trace_ref.profiles where profile_id = %s", (pid,)).fetchone()
    assert row == ("consented_real_reference", None, None, False, "reviewer_confirmed")
    assert pid in eligible(tx)
    real, _, _ = load_rows(tx, "consented_real")
    synthetic, _, _ = load_rows(tx, "synthetic")
    assert pid in {r["profile_id"] for r in real["profiles"]}
    assert pid not in {r["profile_id"] for r in synthetic["profiles"]} and len(synthetic["profiles"]) == 41
    # No identity anywhere in the analytical rows of this profile; career detail retained.
    dump = json.dumps([tx.execute(f"select to_jsonb(t) from trace_ref.{t} t where profile_id = %s", (pid,)).fetchall()
                       for t in ("profiles", "employers", "employments", "education", "experience_evidence", "profile_skills",
                                 "activities")], default=str)
    for value in ("Meera", "Kulkarni", "Person0", "example.com", "example.dev", "98200", "meerak", "linkedin", "github"):
        assert value not in dump, value
    for kept in ("Zentrix Software", "College of Engineering Pune", "8.4 CGPA", "Certified Scrum Product Owner", "Product Manager"):
        assert kept in dump, kept
    mapping = tx.execute("select k.source_kind, k.user_id, a.verification_status, c.status, c.deidentification_status "
                         "from trace_private.contributions c join trace_private.contributors k using (contributor_id) "
                         "join trace_private.consent_attestations a using (contributor_id) where c.profile_id = %s", (pid,)).fetchone()
    assert mapping == ("legacy_participant", None, "verified", "admitted", "passed")
    report = tx.execute("select deidentification_report from trace_private.contributions where profile_id = %s", (pid,)).fetchone()[0]
    assert "Meera" not in json.dumps(report) and report["status"] == "passed"


@pytest.mark.parametrize("change, reason", [
    (dict(verification_status="pending", verified_by=None, verified_at=None), "consent_not_verified"),
    (dict(purposes=["deidentified_career_analysis", "aggregate_research"]), "consent_scope_incomplete"),
])
def test_incomplete_consent_quarantines_without_storing_career_data(tx, change, reason):
    before = tx.execute("select count(*) from trace_ref.profiles").fetchone()[0]
    att = Attestation(**{**VERIFIED.__dict__, **change})
    ref = f"T6Q-{uuid.uuid4().hex[:6]}"
    rec, ident = person(0)
    assert admit(tx, ref, deidentify(rec, ident), att, "Reviewer A") is None
    assert tx.execute("select count(*) from trace_ref.profiles").fetchone()[0] == before
    log = tx.execute("select l.outcome, l.reason_codes, l.contribution_id from trace_private.import_log l "
                     "join trace_private.contributors k using (contributor_id) where k.participant_reference = %s", (ref,)).fetchone()
    assert log[0] == "quarantined" and reason in log[1] and log[2] is None
    assert tx.execute("select count(*) from trace_private.consent_attestations a join trace_private.contributors k using (contributor_id) "
                      "where k.participant_reference = %s", (ref,)).fetchone()[0] == 0


def test_records_needing_review_or_without_reviewer_are_quarantined(tx):
    rec = record()                                                               # "Kulkarni & Sons": needs review
    res = deidentify(rec, Identity(full_name="Meera Kulkarni"))
    assert res.status == "needs_review"
    assert admit(tx, f"T6R-{uuid.uuid4().hex[:6]}", res, VERIFIED, "Reviewer A") is None
    rec2, ident = person(1)
    assert admit(tx, f"T6R-{uuid.uuid4().hex[:6]}", deidentify(rec2, ident), VERIFIED, None) is None


def test_a_real_profile_needs_a_registered_contribution(tx):
    with pytest.raises(psycopg.errors.CheckViolation), tx.transaction():
        tx.execute("insert into trace_ref.profiles (profile_id, source_id, dataset_class, country_code, review_status) "
                   "values ('CRR-000000000001', 'SRC-CONTRIB-001', 'consented_real_reference', 'IN', 'reviewer_confirmed')")


def test_participant_references_cannot_hold_contact_details(tx):
    for bad in ("someone@example.com", "x", "has space"):
        with pytest.raises(psycopg.errors.CheckViolation):
            with tx.transaction():
                tx.execute("insert into trace_private.contributors (source_kind, participant_reference) values ('legacy_participant', %s)", (bad,))


# --------------------------------------------------------------------------- eligibility and withdrawal
def test_eligibility_follows_verification_scope_and_withdrawal_immediately(tx):
    [pid] = admit_people(tx, 1)
    contributor = tx.execute("select contributor_id from trace_private.contributions where profile_id = %s", (pid,)).fetchone()[0]
    assert pid in eligible(tx)
    tx.execute("update trace_private.consent_attestations set verification_status = 'pending', verified_by = null, verified_at = null "
               "where contributor_id = %s", (contributor,))
    assert pid not in eligible(tx)
    tx.execute("update trace_private.consent_attestations set verification_status = 'verified', verified_by = 'x', verified_at = now(), "
               "purposes = array['benchmarking'] where contributor_id = %s", (contributor,))
    assert pid not in eligible(tx)
    tx.execute("update trace_private.consent_attestations set purposes = array['deidentified_career_analysis', 'aggregate_research', "
               "'benchmarking', 'recommendations'] where contributor_id = %s", (contributor,))
    assert pid in eligible(tx)
    ref = tx.execute("select participant_reference from trace_private.contributors where contributor_id = %s", (contributor,)).fetchone()[0]
    assert tx.execute("select trace_private.withdraw_participant(%s, 'Withdrawal request 2026-11-13')", (ref,)).fetchone()[0] == 1
    assert pid not in eligible(tx)
    assert tx.execute("select count(*) from trace_ref.profiles where profile_id = %s", (pid,)).fetchone()[0] == 0
    assert tx.execute("select count(*) from trace_ref.employments where profile_id = %s", (pid,)).fetchone()[0] == 0
    c = tx.execute("select status, profile_id, withdrawn_at is not null, deleted_at is not null from trace_private.contributions "
                   "where contributor_id = %s", (contributor,)).fetchone()
    assert c == ("withdrawn", None, True, True)                                  # minimal audit record kept, link removed
    assert tx.execute("select withdrawn_at is not null, withdrawal_reference from trace_private.consent_attestations "
                      "where contributor_id = %s", (contributor,)).fetchone() == (True, "Withdrawal request 2026-11-13")


def test_an_app_users_withdrawal_event_removes_their_contribution_in_the_same_transaction(users, tx):   # tx ends first
    from app.demo import consent
    u = users()
    tx.execute("insert into public.trace_consent_documents (consent_version, purpose, title, body_sha256, legal_review_status, effective_from) "
               "values ('t6-contribution-test', 'deidentified_shared_dataset', 'test notice', %s, 'approved', now() - interval '1 day')",
               (consent.CONTRIBUTION_BODY_SHA256,))

    def as_user(sql, params=()):
        with tx.transaction():
            tx.execute("set local role trace_app_user")
            tx.execute(IDENTITY_SQL, {"sub": str(u), "claims": json.dumps({"sub": str(u), "role": "authenticated"})})
            tx.execute(sql, params)
            # SET LOCAL lasts until the end of the TRANSACTION, not this savepoint: undo it explicitly.
            tx.execute("reset role")
            tx.execute("select set_config('request.jwt.claims', '', true), set_config('request.jwt.claim.sub', '', true)")
    as_user("insert into public.trace_consent_events (purpose, status, consent_version) values ('deidentified_shared_dataset', "
            "'granted', 't6-contribution-test')")
    contributor = tx.execute("insert into trace_private.contributors (source_kind, user_id) values ('app_user', %s) returning contributor_id",
                             (u,)).fetchone()[0]
    rec, ident = person(2)
    res = deidentify(rec, ident)
    tx.execute("insert into trace_private.contributions (contributor_id, profile_id, status, deidentification_status, pipeline_version, "
               "admitted_at) values (%s, %s, 'admitted', 'passed', 'test', now())", (contributor, res.profile_id))
    tx.execute("insert into trace_ref.profiles (profile_id, source_id, dataset_class, country_code, review_status) "
               "values (%s, 'SRC-CONTRIB-001', 'consented_real_reference', 'IN', 'participant_confirmed')", (res.profile_id,))
    assert res.profile_id in eligible(tx)
    as_user("insert into public.trace_consent_events (purpose, status, consent_version) values ('deidentified_shared_dataset', "
            "'withdrawn', 't6-contribution-test')")
    assert res.profile_id not in eligible(tx)
    assert tx.execute("select count(*) from trace_ref.profiles where profile_id = %s", (res.profile_id,)).fetchone()[0] == 0
    assert tx.execute("select status from trace_private.contributions where contributor_id = %s", (contributor,)).fetchone()[0] == "withdrawn"


def test_the_runtime_reader_and_clients_cannot_read_the_identity_mapping(base_settings):
    with db.connect_runtime(base_settings) as rt:
        for sql in ("select * from trace_private.contributors", "select * from trace_private.consent_attestations",
                    "select * from trace_private.contributions", "select trace_private.withdraw_participant('x', 'y')"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                rt.execute(sql)
            rt.rollback()
        for role in ("trace_app_user",):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with rt.transaction():
                    rt.execute(f"set local role {role}")
                    rt.execute("select * from trace_private.contributors")
            rt.rollback()
        assert rt.execute("select count(*) >= 0 from trace_ref.v_eligible_profiles").fetchone()[0]     # eligibility via the function
    with db.connect(base_settings) as admin:
        for role in ("anon", "authenticated", "service_role", "trace_reference_reader"):
            assert not admin.execute("select has_schema_privilege(%s, 'trace_private', 'usage')", (role,)).fetchone()[0], role


# --------------------------------------------------------------------------- cohorts and statistics
def test_the_real_cohort_is_reported_only_from_five_profiles_and_synthetic_is_unchanged(tx):
    rows0, labels, settings = load_rows(tx, "synthetic")
    synthetic_before = build_insights(rows0, labels, settings, "PM-PM", "Product Manager", YourProfile()).model_dump()
    admit_people(tx, 4)
    rows, labels, settings = load_rows(tx, "consented_real")
    small = build_insights(rows, labels, settings, "PM-PM", "Product Manager", YourProfile(), cohort_key="consented_real")
    assert (small.status, small.sections, small.eligible_profiles, small.scope) == ("insufficient", [], None, None)
    assert small.is_synthetic is False and "consented, de-identified" in small.disclosure
    admit_people(tx, 1)
    rows, labels, settings = load_rows(tx, "consented_real")
    ready = build_insights(rows, labels, settings, "PM-PM", "Product Manager", YourProfile(institutions={"college of engineering pune"}),
                           cohort_key="consented_real")
    assert ready.status == "ready" and ready.eligible_profiles == ready.included_profiles == 5 and ready.cohort == "consented_real"
    metrics = {m.key: m for s in ready.sections for m in s.metrics}
    inst = metrics["institution"]
    assert (inst.status, inst.denominator, inst.buckets[0].label, inst.buckets[0].count, inst.buckets[0].in_your_profile) == \
        ("shown", 5, "College of Engineering Pune", 5, True)
    assert metrics["employer"].buckets and metrics["grades"].buckets[0].label == "8.0–8.9 (10-point)"
    assert metrics["certifications"].buckets[0].label == "Certified Scrum Product Owner"
    text = ready.model_dump_json()
    assert "CRR-" not in text and "profile_id" not in text and "Meera" not in text
    rows1, labels, settings = load_rows(tx, "synthetic")
    assert build_insights(rows1, labels, settings, "PM-PM", "Product Manager", YourProfile()).model_dump() == synthetic_before


def test_a_users_private_profile_never_counts_as_a_reference_profile(demo_client, users, base_settings):
    with db.connect(base_settings) as conn:
        before = {k: len(v) for k, v in load_rows(conn, "synthetic")[0].items()}
        real_before = len(load_rows(conn, "consented_real")[0]["profiles"])
    u, h = candidate(demo_client, users)
    assert upload(demo_client, h, PDF["SYN-001"]).status_code == 201             # a copy of a synthetic sample
    with db.connect(base_settings) as conn:
        assert {k: len(v) for k, v in load_rows(conn, "synthetic")[0].items()} == before
        assert len(load_rows(conn, "consented_real")[0]["profiles"]) == real_before


def test_cohort_api_is_explicit_and_never_falls_back(base_settings, consent_document, users):
    with make_client(base_settings, trace_demo_fixtures=True) as c:                # default: only the synthetic cohort
        u, h = candidate(c, users, grant=False)
        assert [x["cohort"] for x in c.get("/api/v1/cohorts", headers=h).json()] == ["synthetic"]
        r = c.get("/api/v1/insights?target=PM-PM&cohort=consented_real", headers=h)
        assert (r.status_code, r.json()["error"]) == (403, "cohort_not_enabled")
        assert c.get("/api/v1/pathway?target=PM-PM&cohort=consented_real", headers=h).status_code == 403
        for bad in ("all", "combined", "both", "synthetic,consented_real", "real"):
            assert c.get(f"/api/v1/insights?target=PM-PM&cohort={bad}", headers=h).status_code == 422, bad
        default = c.get("/api/v1/insights?target=PM-PM", headers=h).json()
        assert (default["cohort"], default["is_synthetic"], default["status"]) == ("synthetic", True, "ready")
    with make_client(base_settings, trace_demo_fixtures=True, trace_enabled_cohorts="synthetic,consented_real") as c:
        u, h = candidate(c, users, grant=False)
        listed = {x["cohort"]: x for x in c.get("/api/v1/cohorts", headers=h).json()}
        assert listed["synthetic"]["eligible_profiles"] == 41 and listed["synthetic"]["status"] == "ready"
        assert listed["consented_real"]["status"] == "not_ready" and listed["consented_real"]["eligible_profiles"] is None
        real = c.get("/api/v1/insights?target=PM-PM&cohort=consented_real", headers=h).json()
        assert (real["cohort"], real["status"], real["sections"], real["is_synthetic"]) == ("consented_real", "not_ready", [], False)
        assert "synthetic" not in real["disclosure"].lower()
        path = c.get("/api/v1/pathway?target=PM-PM&cohort=consented_real", headers=h).json()
        assert path["cohort"] == "consented_real" and path["routes"] == [] and path["is_synthetic_test_data"] is not True
        synth = c.get("/api/v1/pathway?target=PM-PM&cohort=synthetic", headers=h).json()
        assert synth["cohort"] == "synthetic"


def test_no_test_rows_remain(base_settings):
    with db.connect(base_settings) as conn:
        assert conn.execute("select count(*) from trace_private.contributors where participant_reference like 'T6%'").fetchone()[0] == 0
        assert conn.execute("select count(*) from trace_ref.profiles where dataset_class = 'consented_real_reference'").fetchone()[0] == 0
        assert conn.execute("select count(*) from public.trace_consent_documents where consent_version = 't6-contribution-test'").fetchone()[0] == 0


def test_an_admitted_but_unverified_contribution_is_not_in_the_real_cohort(tx):
    [pid] = admit_people(tx, 1)
    assert pid in {r["profile_id"] for r in load_rows(tx, "consented_real")[0]["profiles"]}
    tx.execute("update trace_private.consent_attestations set verification_status = 'pending', verified_by = null, verified_at = null "
               "where contributor_id = (select contributor_id from trace_private.contributions where profile_id = %s)", (pid,))
    assert pid not in {r["profile_id"] for r in load_rows(tx, "consented_real")[0]["profiles"]}
