"""Career insights (Phase 5) against the local database and through the API.

The synthetic-only boundary is tested with sentinel reference profiles inserted inside a transaction that is always
rolled back (no lasting rows): a non-synthetic profile and a synthetic profile not approved for demos, each with
distinctive attributes, must not appear in any query or statistic.
"""
import json
import re

import pytest

from app import db
from app.services.career_insights import YourProfile, build_insights, load_rows, read_only_snapshot
from tests.test_api_foundation import assert_no_secrets, auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import candidate, consent_document, demo_client, make_client  # noqa: F401  (fixtures)
from tests.test_api_phase4 import TARGETS, reviewed_candidate

pytestmark = pytest.mark.integration
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
SENTINELS = [f"PUB-{i}" for i in range(901, 907)] + [f"SYN-{i}" for i in range(901, 907)]


def load(base_settings):
    with db.connect(base_settings) as conn:
        with read_only_snapshot(conn):
            return load_rows(conn)


def insert_sentinels(conn):
    conn.execute("insert into trace_ref.data_sources (source_id, dataset_class, source_type, source_label) "
                 "values ('SRC-SENTINEL-001', 'public_aggregate_reference', 'public_dataset', 'Sentinel non-synthetic source')")
    for pid in SENTINELS:
        synthetic = pid.startswith("SYN-")
        conn.execute("insert into trace_ref.profiles (profile_id, source_id, dataset_class, review_status, synthetic_demo_allowed, country_code) "
                     "values (%s, %s, %s::public.trace_dataset_class, %s::public.trace_review_status, false, 'IN')",
                     (pid, "SRC-SYNTHETIC-001" if synthetic else "SRC-SENTINEL-001",
                      "synthetic_test_fixture" if synthetic else "public_aggregate_reference",
                      "synthetic_curated" if synthetic else "reviewer_confirmed"))
        conn.execute("insert into trace_ref.education (education_id, profile_id, qualification_level, field_of_study) "
                     "values (%s, %s, 'doctorate', 'Sentinel Studies')", (f"{pid}-EDU-01", pid))
        conn.execute("insert into trace_ref.employers (profile_id, employer_id, industry_sector, company_model) "
                     "values (%s, %s, 'hr_tech', 'non_tech_enterprise')", (pid, f"{pid}-ORG-A"))
        conn.execute("insert into trace_ref.experience_evidence (evidence_id, profile_id, evidence_type, evidence_text_redacted, provenance, "
                     "responsibility_area, evidence_status) values (%s, %s, 'responsibility', 'sentinel evidence text', 'synthetic_fixture', "
                     "'design_research', 'synthetic_curated')", (f"{pid}-EV-01", pid))


# --------------------------------------------------------------------------- dataset boundary
def test_the_cohort_is_exactly_the_demo_approved_synthetic_profiles(base_settings):
    rows, labels, settings = load(base_settings)
    with db.connect(base_settings) as conn:
        expected = {r[0] for r in conn.execute("select profile_id from trace_ref.profiles where dataset_class = 'synthetic_test_fixture' "
                                               "and synthetic_demo_allowed").fetchall()}
        everything = conn.execute("select count(*) from trace_ref.profiles").fetchone()[0]
    assert {r["profile_id"] for r in rows["profiles"]} == expected and len(expected) == 41 == everything
    assert {r["dataset_class"] for r in rows["profiles"]} == {"synthetic_test_fixture"}
    for name, rs in rows.items():
        assert {r["profile_id"] for r in rs} <= expected, name
    assert settings == {"min_profiles_limited": 5, "min_profiles_moderate": 15}


def test_non_synthetic_and_unapproved_sentinels_are_excluded(base_settings):
    baseline_rows, labels, settings = load(base_settings)
    baseline = build_insights(baseline_rows, labels, settings, "PM-PM", "Product Manager", YourProfile())
    with db.connect(base_settings) as conn:
        try:
            insert_sentinels(conn)                                            # visible only inside this transaction
            assert conn.execute("select count(*) from trace_ref.profiles where profile_id = any(%s)", (SENTINELS,)).fetchone()[0] == 12
            rows, labels2, settings2 = load_rows(conn)
            result = build_insights(rows, labels2, settings2, "PM-PM", "Product Manager", YourProfile())
        finally:
            conn.rollback()
        assert conn.execute("select count(*) from trace_ref.profiles where profile_id = any(%s)", (SENTINELS,)).fetchone()[0] == 0
    for name, rs in rows.items():
        assert not {r["profile_id"] for r in rs} & set(SENTINELS), name
    text = result.model_dump_json()
    for marker in ("Doctorate", "Sentinel", "HR tech", "Non-tech enterprise"):
        assert marker not in text, marker
    assert result.model_dump() == baseline.model_dump()                       # identical statistics


def test_statistics_match_independent_sql(base_settings):
    rows, labels, settings = load(base_settings)
    r = build_insights(rows, labels, settings, "PM-PM", "Product Manager", YourProfile())
    with db.connect(base_settings) as conn:
        cohort = conn.execute("select count(distinct e.profile_id) from trace_ref.employments e join trace_ref.profiles p using (profile_id) "
                              "where e.role_id = 'PM-PM' and p.dataset_class = 'synthetic_test_fixture' and p.synthetic_demo_allowed").fetchone()[0]
        mba = conn.execute("select count(distinct d.profile_id) from trace_ref.education d join trace_ref.employments e using (profile_id) "
                           "where d.qualification_level = 'mba' and e.role_id = 'PM-PM'").fetchone()[0]
        with_edu = conn.execute("select count(distinct d.profile_id) from trace_ref.education d join trace_ref.employments e using (profile_id) "
                                "where e.role_id = 'PM-PM' and d.qualification_level not in ('unknown')").fetchone()[0]
        movers = conn.execute("select from_role_id, count(distinct profile_id) from trace_ref.v_episode_transitions where to_role_id = 'PM-PM' "
                              "and transition_status = 'confirmed' and role_changed group by 1 having count(distinct profile_id) >= 5").fetchall()
        all_movers = conn.execute("select count(distinct profile_id) from trace_ref.v_episode_transitions where to_role_id = 'PM-PM' "
                                  "and transition_status = 'confirmed' and role_changed").fetchone()[0]
    assert r.scope.target_cohort_size == cohort and r.scope.scope_used == "target_cohort"
    h = next(x for s in r.sections for x in s.headlines if x.key == "mba")
    assert (h.count, h.denominator, h.share_pct) == (mba, with_edu, round(100 * mba / with_edu))
    before = next(m for s in r.sections for m in s.metrics if m.key == "roles_before_target")
    assert before.denominator == all_movers
    assert {(b.label, b.count) for b in before.buckets} == {(labels["roles"][f], n) for f, n in movers}


# --------------------------------------------------------------------------- API
def scan(resp):
    assert_no_secrets(resp)
    assert not UUID_RE.search(resp.text), "identifier in response"
    assert "SYN-" not in resp.text and "PUB-" not in resp.text and "profile_id" not in resp.text
    body = resp.json()

    def counts(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("count", "other_count", "target_cohort_size") and isinstance(v, int):
                    yield v
                yield from counts(v)
        elif isinstance(o, list):
            for v in o:
                yield from counts(v)
    assert all(c >= 5 for c in counts(body)), "a count below five"


@pytest.mark.parametrize("target", TARGETS)
def test_insights_for_every_target(demo_client, users, target):
    u, h = candidate(demo_client, users, grant=False)
    resp = demo_client.get(f"/api/v1/insights?target={target}", headers=h)
    assert resp.status_code == 200
    scan(resp)
    body = resp.json()
    assert body["data_mode"] == "synthetic_demo" and body["is_synthetic"] is True
    assert body["disclosure"] == "Illustrative insights based on synthetic demo profiles. These are not real-world workforce statistics."
    assert body["title"] == "Career patterns in Trace's synthetic sample" and body["scope"]["sample_size"] == 41
    assert [s["key"] for s in body["sections"]] == ["background", "employers", "transitions", "experience"]
    for m in (m for s in body["sections"] for m in s["metrics"]):
        assert m["denominator"] + m["not_recorded"] == m["population_size"]
        if m["status"] == "shown":
            assert m["denominator"] >= 5 and m["buckets"]
            assert all(b["share_pct"] == round(100 * b["count"] / m["denominator"]) for b in m["buckets"])
        else:
            assert m["buckets"] == []
    assert resp.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("target", ["ROLE-SWE", "PM-NOPE", "pm-pm", "PM PM", "", "PM-PM'--"])
def test_invalid_targets_are_refused(demo_client, users, target):
    u, h = candidate(demo_client, users, grant=False)
    resp = demo_client.get("/api/v1/insights", params={"target": target}, headers=h)
    assert resp.status_code == 422
    if not re.fullmatch(r"[A-Z0-9-]+", target):
        assert resp.json()["error"] == "invalid_request" and (target not in resp.text or target == "")
    assert demo_client.get("/api/v1/insights", headers=h).status_code == 422


def test_insights_need_a_verified_token_and_a_profile(demo_client, users):
    assert demo_client.get("/api/v1/insights?target=PM-PM").status_code == 401
    assert demo_client.get("/api/v1/insights?target=PM-PM", headers={"Authorization": "Bearer a.b.c"}).status_code == 401
    resp = demo_client.get("/api/v1/insights?target=PM-PM", headers=auth(token(users())))
    assert (resp.status_code, resp.json()["error"]) == (404, "profile_not_found")


def test_an_unsupported_data_mode_fails_closed(base_settings, consent_document, users):
    with make_client(base_settings, trace_demo_fixtures=True, trace_data_mode="empirical") as c:
        u, h = candidate(c, users, grant=False)
        resp = c.get("/api/v1/insights?target=PM-PM", headers=h)
    assert (resp.status_code, resp.json()["error"]) == (503, "data_mode_unsupported")
    assert "SYN-" not in resp.text


def test_the_browser_cannot_choose_the_data_mode(demo_client, users):
    u, h = candidate(demo_client, users, grant=False)
    for extra in ({"data_mode": "empirical"}, {"dataset_class": "consented_real_reference"}, {"mode": "empirical"}):
        resp = demo_client.get("/api/v1/insights", params={"target": "PM-PM", **extra}, headers={**h, "X-Data-Mode": "empirical"})
        assert resp.status_code == 200 and resp.json()["data_mode"] == "synthetic_demo"


def test_your_profile_markers_come_from_your_reviewed_records(demo_client, users):
    u, h = reviewed_candidate(demo_client, users, "SYN-001")
    body = demo_client.get("/api/v1/insights?target=PM-PM", headers=h).json()
    metrics = {m["key"]: m for s in body["sections"] for m in s["metrics"]}
    mine = lambda key: {b["label"] for b in metrics[key]["buckets"] if b["in_your_profile"]}           # noqa: E731
    assert "Bachelor's degree" not in mine("qualification_level")      # education not confirmed by reviewed_candidate
    assert "Roadmap ownership" in mine("responsibility_areas")          # classified and confirmed evidence
    assert "Associate Product Manager" in mine("roles_before_target")   # a reviewed, mapped employment
    other = reviewed_candidate(demo_client, users, "SYN-001", classify=False)[1]
    body2 = demo_client.get("/api/v1/insights?target=PM-PM", headers=other).json()
    m2 = {m["key"]: m for s in body2["sections"] for m in s["metrics"]}
    assert not any(b["in_your_profile"] for b in m2["responsibility_areas"]["buckets"])      # untagged: not compared as present
    assert json.dumps(body2).count("lack that experience") == 1


def test_insights_are_rate_limited_per_user(base_settings, consent_document, users):
    with make_client(base_settings, trace_demo_fixtures=True, rate_limit_read_user="3/60") as c:
        u, h = candidate(c, users, grant=False)
        codes = [c.get("/api/v1/insights?target=PM-PM", headers=h).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        limited = c.get("/api/v1/insights?target=PM-PM", headers=h)
        assert limited.json()["error"] == "rate_limited" and int(limited.headers["retry-after"]) >= 1
        u2, h2 = candidate(c, users, grant=False)
        assert c.get("/api/v1/insights?target=PM-PM", headers=h2).status_code == 200      # another user is unaffected


def test_the_cohort_filter_is_an_explicit_positive_allowlist():
    from app.services.career_insights.repository import COHORT, QUERIES
    clause = re.sub(r"\s+", " ", COHORT)
    assert "where p.dataset_class = 'synthetic_test_fixture'::public.trace_dataset_class and p.synthetic_demo_allowed" in clause
    assert not re.search(r"<>|!=|\bnot in\b|\bis not\b|\bnot\b", clause, re.I)
    assert all(sql.startswith(COHORT) and "join cohort c on c.profile_id" in sql or name == "profiles" for name, sql in QUERIES.items())


def test_classified_but_unreviewed_evidence_is_not_yours(demo_client, users):
    from tests.test_api_phase2 import PDF, upload
    u, h = candidate(demo_client, users)
    assert upload(demo_client, h, PDF["SYN-001"]).status_code == 201
    view = demo_client.get("/api/v1/profile/records", headers=h).json()
    for e in view["employments"]:
        for x in e["evidence"]:                                            # classified, never confirmed
            assert demo_client.put(f"/api/v1/evidence/{x['id']}/classification", headers=h, json={"areas": ["roadmap_ownership"]}).status_code == 200
    body = demo_client.get("/api/v1/insights?target=PM-PM", headers=h).json()
    areas = next(m for s in body["sections"] for m in s["metrics"] if m["key"] == "responsibility_areas")
    assert not any(b["in_your_profile"] for b in areas["buckets"])
