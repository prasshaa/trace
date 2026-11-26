"""Product Phase 4: transition plans through the API (D27-D30), with transient users and invented records only.

Each test builds a profile through the product's own entry, categorisation and role endpoints, then asks for a plan.
The reference cohort is the server's (synthetic in tests, labelled); profile-led routes never carry supporters.
"""
import json
import uuid
from datetime import datetime, timezone

import psycopg
import pytest

from app import db
from app.services.candidate_profile import OwnerContext, load_sequencing_context
from app.services.candidate_profile.repository import IDENTITY_SQL
from app.services.transition.service import TransitionPlanService
from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import candidate, consent_document, make_client  # noqa: F401  (fixtures)


@pytest.fixture
def c(base_settings, consent_document):
    with make_client(base_settings) as client:
        yield client


def month(y, m):
    return {"year": y, "month": m, "precision": "month"}


ONGOING = {"precision": "ongoing"}


def add_job(c, h, title, role_id, start, end, lines, ownership=None, confirm=True):
    """lines: [(text, areas | "none" | None, qualifiers)]"""
    cls = [None if a is None else {"none": True} if a == "none" else {"areas": a, "qualifiers": q or {}} for _, a, q in lines]
    r = c.post("/api/v1/entries/employment", headers=h, json={
        "title_raw": title, "employment_type": "full_time", "start": start, "end": end, "responsibilities": [t for t, _, _ in lines],
        "responsibility_classifications": cls, "confirm": confirm})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    body = {"role_id": role_id} if ownership is None else {"role_id": role_id, "ownership_areas": ownership}
    assert c.post(f"/api/v1/employments/{rid}/role", headers=h, json=body).status_code == 200
    return rid


def plan(c, h, target="PM-PM", contexts=()):
    q = "&".join([f"target={target}"] + [f"context={x}" for x in contexts])
    r = c.post(f"/api/v1/transition-plan?{q}", headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def routes(p):
    return {(r["route_kind"], r["bridge_role_id"]): r for r in p["routes"]}


def states(p):
    return {i["key"]: i["state"] for i in p["assessment"]["items"]}


def stage_keys(route, stage):
    return [a["template_key"] for s in route["stages"] if s["stage"] == stage for a in s["actions"]]


def all_keys(p):
    return [a["template_key"] for r in p["routes"] for s in r["stages"] for a in s["actions"]]


def posting(p, key):
    return next(x for x in p["eligibility"]["postings"] if x["source_key"] == key)


def swe(c, h):
    return add_job(c, h, "Software Engineer", "ROLE-SWE", month(2018, 1), ONGOING, [
        ("Built payment APIs for enterprise customers", ["technical_engineering"], None),
        ("Shipped weekly releases with the team", ["delivery_execution"], {"delivery_execution": {"cross_team": False}}),
        ("Organised the team offsite", "none", None)])


def data_analyst(c, h, exposure=True):
    lines = [("Built retention dashboards for the growth team", ["data_analysis"], None)]
    if exposure:
        lines.append(("Prioritised the analytics backlog with the product team", ["prioritisation_backlog"], None))
    return add_job(c, h, "Data Analyst", "ROLE-DA", month(2019, 1), ONGOING, lines)


# --------------------------------------------------------------------------- V1-V9
def test_v1_software_engineer_to_pm_bridge_beats_direct_when_core_ownership_is_missing(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    p = plan(c, h, contexts=["platform_api"])
    s = states(p)
    assert (s["C6"], s["X3"], s["C3"], s["C5"]) == ("demonstrated", "demonstrated", "transferable", "transferable")
    assert (s["C1"], s["C2"], s["C4"]) == ("gap", "gap", "gap")
    assert (p["decision"]["rule"], p["decision"]["primary"]) == ("R3", ["PM-APM"])           # V8: intermediary beats direct
    r = routes(p)
    apm, direct = r[("bridge", "PM-APM")], r[("direct", None)]
    assert apm["primary_status"] == "primary" and apm["position"] == 1 and direct["primary_status"] == "alternative"
    assert apm["feasibility"]["decision_rights_levels"] == {"C2": "partial", "C4": "partial"}           # D32: partial, never "closed"
    assert [x["role_id"] for x in apm["steps"]] == ["ROLE-SWE", "PM-APM", "PM-PM"]
    seek = [a for st in apm["stages"] for a in st["actions"] if a["template_key"] == "apply.bridge_seek"]
    assert {a["requirement_key"] for a in seek} >= {"C2", "C4"} and all("only partly" in a["action"] for a in seek)
    assert any(t["kind"] == "remaining_gaps" for t in direct["tradeoffs"])
    assert {"build.C1", "build.C2", "build.C4"} <= set(stage_keys(apm, 1))
    assert stage_keys(apm, 4)[0] == "apply.bridge_role"                                         # the bridge role first
    rep = [a for st in apm["stages"] if st["stage"] == 3 for a in st["actions"]]
    assert any("Built payment APIs" in a["action"] for a in rep)                                # their own record, repositioned
    assert r[("bridge", "ROLE-PAN")]["feasibility"]["entry"] == "not_evidenced"


def test_v2_business_analyst_to_pm_direct_route_and_no_relearning(c, users):
    _, h = candidate(c, users)
    add_job(c, h, "Business Analyst", "ROLE-BA", month(2016, 7), ONGOING, [
        ("Wrote requirements for the settlements module", ["requirements_analysis"], None),
        ("Built weekly dashboards on payment success rates", ["data_analysis"], None),
        ("Ran workshops with operations and compliance stakeholders", ["stakeholder_management"], None),
        ("Defined success metrics for the refund flow and tracked them", ["outcome_ownership"], None)])
    p = plan(c, h, contexts=["growth_analytics", "regulated_domain"])
    s = states(p)
    assert (s["C3"], s["C4"], s["C5"], s["X1"], s["C1"]) == ("demonstrated", "demonstrated", "demonstrated", "demonstrated", "transferable")
    assert (s["C2"], s["C6"], s["X4"]) == ("gap", "gap", "unknown")
    # D31/D32: no evidence of strategy at all (C2 Gap); the APM develops it partly, so it leads, direct stays the alternative
    assert (p["decision"]["rule"], p["decision"]["primary"]) == ("R3", ["PM-APM"])
    assert routes(p)[("direct", None)]["primary_status"] == "alternative"
    keys = all_keys(p)
    assert "build.X1" not in keys and "build.C4" not in keys and "build.C3" not in keys           # never relearn what is shown
    assert {"build.C2", "build.C6", "resolve.not_inferable"} <= set(keys)
    owner = routes(p)[("bridge", "ROLE-OWNER")]
    assert owner["primary_status"] == "alternative" and owner["feasibility"]["entry"] == "hypothesis"   # D34
    apm = routes(p)[("bridge", "PM-APM")]                                                  # D40: intended outcome, partial only
    assert apm["feasibility"]["decision_rights_levels"] == {"C2": "partial"} and "C2" not in apm["feasibility"]["develops_fully"]
    assert any("only partly" in r for r in apm["feasibility"]["reasons"])
    assert not any(r.startswith("Develops \"") for r in apm["feasibility"]["reasons"])


def test_v3_product_owner_to_senior_pm_goes_through_product_manager_first(c, users):
    _, h = candidate(c, users)
    add_job(c, h, "Product Owner", "ROLE-OWNER", month(2019, 3), ONGOING, [
        ("Owned the backlog for the payments team", ["prioritisation_backlog"], None),
        ("Ran discovery interviews with merchants", ["product_discovery"], None),
        ("Coached two junior product owners", ["people_leadership"], {"people_leadership": {"coaching_mentoring": True}}),
        ("Wrote user stories with acceptance criteria", ["requirements_analysis"], None)])
    p = plan(c, h, target="PM-SPM", contexts=["commercial_product"])
    s = states(p)
    assert (s["C1"], s["C3"], s["L2"]) == ("demonstrated", "demonstrated", "demonstrated")
    assert (p["decision"]["rule"], p["decision"]["primary"]) == ("R3", ["PM-PM"])           # C2 only transferable, C4 missing
    pm_first = routes(p)[("bridge", "PM-PM")]
    assert [x["role_id"] for x in pm_first["steps"]] == ["ROLE-OWNER", "PM-PM", "PM-SPM"]
    assert routes(p)[("direct", None)]["primary_status"] == "alternative"
    keys = set(all_keys(p))
    assert "build.C1" not in keys and "build.L2" not in keys and "build.C4" in keys


def test_v4_extensive_transferable_experience_with_genuine_gaps(c, users):
    _, h = candidate(c, users)
    add_job(c, h, "Engineering Manager", "ROLE-EM", month(2015, 1), ONGOING, [
        ("Managed key enterprise accounts' escalations", ["customer_account_management"], None),
        ("Coordinated delivery across three teams", ["delivery_execution"], {"delivery_execution": {"cross_team": True}}),
        ("Mentored four engineers", ["people_leadership"], {"people_leadership": {"coaching_mentoring": True}}),
        ("Reviewed system designs", ["technical_engineering"], None),
        ("Aligned sales and engineering on release scope", ["stakeholder_management"], None)])
    p = plan(c, h)
    s = states(p)
    assert (s["C1"], s["C3"]) == ("transferable", "transferable") and (s["C2"], s["C4"]) == ("gap", "gap")
    assert (p["decision"]["rule"], p["decision"]["primary"]) == ("R3", ["PM-APM"])           # no strategy or outcome evidence
    direct = routes(p)[("direct", None)]
    assert sorted(k for k in stage_keys(direct, 1)) == ["build.C2", "build.C4"]                 # build only the real gaps
    assert {"demonstrate.C1", "demonstrate.C3"} <= set(stage_keys(direct, 2))
    assert direct["feasibility"]["conditional"] and direct["primary_status"] == "alternative"
    apm = routes(p)[("bridge", "PM-APM")]                                                  # D40: intended outcome, partial only
    assert apm["primary_status"] == "primary" and apm["feasibility"]["decision_rights_levels"] == {"C2": "partial", "C4": "partial"}
    assert not {"C2", "C4"} & set(apm["feasibility"]["develops_fully"])
    assert {p_["key"] for p_ in apm["feasibility"]["partial_development"]} >= {"C2", "C4"}


def _pending_lines_job(c, h):
    """A confirmed Software Engineer job whose first line is confirmed and categorised and whose other two lines are
    still waiting for the user's review (pending)."""
    r = c.post("/api/v1/entries/employment", headers=h, json={
        "title_raw": "Software Engineer", "employment_type": "full_time", "start": month(2018, 1), "end": ONGOING,
        "responsibilities": ["Built payment APIs", "Ran sprint planning", "Fixed production incidents"],
        "responsibility_classifications": [{"areas": ["technical_engineering"], "qualifiers": {}}, None, None], "confirm": False})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    rec = next(e for e in c.get("/api/v1/profile/records", headers=h).json()["employments"] if e["id"] == rid)
    assert c.post(f"/api/v1/records/employment/{rid}/confirm", headers=h).status_code == 200
    first = next(x for x in rec["evidence"] if x["evidence_text"] == "Built payment APIs")
    assert c.post(f"/api/v1/records/evidence/{first['id']}/confirm", headers=h).status_code == 200
    assert c.post(f"/api/v1/employments/{rid}/role", headers=h, json={"role_id": "ROLE-SWE"}).status_code == 200
    return rid


def test_v5_incomplete_profile_resolves_first_and_invents_no_primary(c, users):
    # Lines still waiting for the user's REVIEW keep the plan provisional (Phase 5: categorising is no longer required).
    _, h = candidate(c, users)
    rid = _pending_lines_job(c, h)
    p = plan(c, h)
    assert "transferable" in states(p).values()                                             # some states are known...
    assert (p["decision"]["rule"], p["decision"]["primary"]) == ("R1", [])                  # ...but core ones are not
    assert all(r["primary_status"] == "not_evaluated" for r in p["routes"])
    direct = routes(p)[("direct", None)]
    assert stage_keys(direct, 0) == ["resolve.pending"] and not any(stage_keys(direct, n) for n in (1, 2, 3))
    assert stage_keys(direct, 4)                                                                # stage 4 still considered
    refs = direct["stages"][0]["actions"][0]["record_refs"]
    assert len(refs) == 2 and rid not in refs                                                   # the two lines, by id


def test_v5_confirmed_but_uncategorised_lines_are_read_and_never_block(c, users):
    # Phase 5 (D50): the same lines CONFIRMED but not categorised are read by Trace, so the plan isn't provisional.
    _, h = candidate(c, users)
    add_job(c, h, "Software Engineer", "ROLE-SWE", month(2018, 1), ONGOING,
            [("Built payment APIs", ["technical_engineering"], None), ("Ran sprint planning", None, None),
             ("Fixed production incidents", None, None)])
    p = plan(c, h)
    assert p["decision"]["rule"] != "R1" and p["assessment"]["coverage"]["complete"]
    c6 = next(i for i in p["assessment"]["items"] if i["key"] == "C6")
    assert (c6["state"], c6["basis"], c6["read_by"]) == ("demonstrated", "interpreted_evidence", ["trace"])
    assert next(i for i in p["assessment"]["items"] if i["key"] == "C2")["state"] in ("gap", "transferable")  # never invented

def test_v6_no_comparable_histories_still_gives_a_full_profile_led_plan(base_settings, consent_document, users):
    with make_client(base_settings, trace_enabled_cohorts="synthetic,consented_real") as c:
        _, h = candidate(c, users)
        swe(c, h)
        r = c.post("/api/v1/transition-plan?target=PM-PM&cohort=consented_real", headers=h)
        assert r.status_code == 201, r.text
        p = r.json()
    assert p["reference_cohort_data_mode"] == "empirical" and p["privacy_state"]["reference_pool"] == "empty"
    assert not [x for x in p["routes"] if x["route_kind"] == "observed"]
    direct = routes(p)[("direct", None)]
    assert direct["evidence_breadth_label"] == "limited_or_unavailable" and not direct["is_synthetic_test_data"]
    assert {s["stage"] for s in routes(p)[("bridge", "PM-APM")]["stages"]} == {1, 2, 3, 4}
    row = users.admin.execute("select s.eligible_profile_count, s.data_mode::text, r.evidence_class::text, r.supporting_profile_count "
                              "from public.trace_pathway_runs u join trace_ref.reference_snapshots s on s.snapshot_id = u.reference_snapshot_id "
                              "join public.trace_pathway_recommendations r using (pathway_run_id) where u.pathway_run_id = %s "
                              "and r.route_kind = 'direct'", (p["run_id"],)).fetchone()
    assert row == (0, "empirical", "insufficient", 0)                                           # an empty snapshot, nothing synthetic


def test_v7_direct_beats_intermediary_when_the_profile_already_supports_it(c, users):
    _, h = candidate(c, users)
    add_job(c, h, "Business Analyst", "ROLE-BA", month(2017, 1), ONGOING, [
        ("Ran discovery interviews with customers", ["product_discovery"], None),
        ("Owned the quarterly roadmap for the onboarding area", ["roadmap_ownership"], None),
        ("Wrote and prioritised requirements", ["requirements_analysis"], None),
        ("Defined and tracked activation metrics", ["outcome_ownership"], None),
        ("Aligned sales, support and engineering on launches", ["stakeholder_management"], None),
        ("Ran fortnightly releases", ["delivery_execution"], {"delivery_execution": {"cross_team": False}})])
    p = plan(c, h)
    assert (p["decision"]["rule"], p["decision"]["primary"]) == ("R2", ["direct"])
    assert {r["primary_status"] for r in p["routes"] if r["route_kind"] == "bridge"} == {"not_recommended"}
    direct = routes(p)[("direct", None)]
    assert not stage_keys(direct, 1) and stage_keys(direct, 3) and stage_keys(direct, 4)        # reposition-heavy, always stage 4


def test_v9_same_user_different_employer_outcomes_capability_unchanged(c, users):
    _, h = candidate(c, users)
    data_analyst(c, h)
    p1 = plan(c, h, contexts=["data_product"])
    assert posting(p1, "J11")["eligibility"]["outcome"] == "meets"                               # 5+ years adjacent + exposure
    assert posting(p1, "J5")["eligibility"]["outcome"] == "does_not_meet"                        # 3+ years of PM
    j12 = posting(p1, "J12")
    assert (j12["evidence_use"], j12["is_current_opportunity"]) == ("historical_eligibility_evidence", False)
    assert c.post("/api/v1/entries/education", headers=h, json={"qualification_title_raw": "B.Tech", "qualification_level": "bachelor",
                                                                 "field_of_study": "Computer Science", "confirm": True}).status_code == 201
    p2 = plan(c, h, contexts=["data_product"])
    pref = lambda p: posting(p, "J10")["eligibility"]["preferred"][0]["result"]["outcome"]   # noqa: E731
    assert (pref(p1), pref(p2)) == ("unclear", "meets")                                          # eligibility changed...
    assert p1["assessment"]["items"] == p2["assessment"]["items"]                                # ...capability did not
    assert p1["decision"]["rule"] == p2["decision"]["rule"] and p1["decision"]["primary"] == p2["decision"]["primary"]


def test_a_data_analyst_title_alone_does_not_meet_targets_adjacent_criterion(c, users):
    _, h = candidate(c, users)
    data_analyst(c, h, exposure=False)
    assert posting(plan(c, h), "J11")["eligibility"]["outcome"] == "does_not_meet"


# --------------------------------------------------------------------------- persistence, ownership, immutability (D30)
def test_runs_are_saved_owner_scoped_and_returned_as_stored(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    p = plan(c, h)
    assert p["status"] == "completed" and p["versions"]["action_templates"] == "pm-actions-v2"
    assert p["versions"]["bridge_catalogue"] == "pm-bridges-v2" and p["versions"]["eligibility_rules"] == "pm-eligibility-v3"
    assert c.get("/api/v1/transition-plan?target=PM-PM", headers=h).json() == p
    assert c.get(f"/api/v1/transition-plan/{p['run_id']}", headers=h).json() == p
    _, other = candidate(c, users)
    assert c.get(f"/api/v1/transition-plan/{p['run_id']}", headers=other).status_code == 404
    assert c.get("/api/v1/transition-plan?target=PM-PM", headers=other).status_code == 404
    body = json.dumps(p)
    assert "score_breakdown" not in body and "supporting_profile_count" not in body and "reference_profile_id" not in body
    row = users.admin.execute("select count(*), bool_and(score_breakdown = '{}'), bool_and(supporting_profile_count = 0) "
                              "from public.trace_pathway_recommendations where pathway_run_id = %s", (p["run_id"],)).fetchone()
    assert row[0] == len(p["routes"]) and row[1] and row[2]
    second = plan(c, h)
    assert second["run_id"] != p["run_id"] and c.get("/api/v1/transition-plan?target=PM-PM", headers=h).json()["run_id"] == second["run_id"]


def test_completed_runs_are_immutable(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    run = plan(c, h)["run_id"]
    a = users.admin
    for sql in ("update public.trace_pathway_runs set decision = '{}' where pathway_run_id = %s",
                "update public.trace_pathway_recommendations set explanation = 'x' where pathway_run_id = %s",
                "update public.trace_pathway_actions set action_text = 'x' where recommendation_id in "
                "(select recommendation_id from public.trace_pathway_recommendations where pathway_run_id = %s)",
                "insert into public.trace_pathway_recommendations (pathway_run_id, owner_user_id, data_mode, rank, evidence_class) "
                "select pathway_run_id, owner_user_id, data_mode, 99, 'synthetic_test_only' from public.trace_pathway_runs where pathway_run_id = %s"):
        with pytest.raises(psycopg.Error) as e:
            a.execute(sql, (run,))
        assert e.value.sqlstate == "TR027", sql


def _as_owner(conn, uid, sql, params=()):
    with conn.transaction():
        conn.execute("set local role trace_app_user")
        conn.execute(IDENTITY_SQL, {"sub": str(uid), "claims": json.dumps({"sub": str(uid), "role": "authenticated"})})
        return conn.execute(sql, params).fetchall()


def _payload(profile_id, **over):
    p = {"candidate_profile_id": str(profile_id), "data_mode": "synthetic_test", "target_role_id": "PM-PM", "engine_version": "t",
         "profile_version": "t", "knowledge_pack": "pm-v1.1", "source_inventory": "pm-v1", "taxonomy_version": 2,
         "bridge_catalogue_version": "pm-bridges-v1", "action_template_version": "pm-actions-v1", "eligibility_rules_version": "pm-eligibility-v1",
         "assessment": {}, "eligibility": {}, "source_versions": {}, "decision": {}, "privacy_state": {},
         "routes": [{"route_basis": "profile_led", "route_kind": "direct", "primary_status": "primary", "steps": ["PM-PM"],
                     "supporting_profile_count": 50, "score_breakdown": {"fit": 0.9}}]}
    p.update(over)
    return p


def test_the_writer_is_the_only_path_and_it_is_owner_scoped(base_settings, c, users):
    u, h = candidate(c, users)
    other, _ = candidate(c, users)
    pid = users.admin.execute("select candidate_profile_id from public.trace_candidate_profiles where owner_user_id = %s", (u,)).fetchone()[0]
    conn = db.connect(base_settings)
    try:
        run = _as_owner(conn, u, "select trace_plan.record_transition_plan(%s::jsonb)", (json.dumps(_payload(pid)),))[0][0]
        row = users.admin.execute("select supporting_profile_count, score_breakdown, evidence_class::text from public.trace_pathway_recommendations "
                                  "where pathway_run_id = %s", (run,)).fetchone()
        assert row == (0, {}, "synthetic_test_only")                                 # supplied counts and scores are ignored
        for who, payload, state in (
                (other, _payload(pid), "TR005"),                                     # another user's profile
                (u, _payload(pid, routes=[]), "TR028"),
                (u, _payload(pid, engine_version=""), "TR028"),
                (u, _payload(pid, data_mode="synthetic_demo"), "TR028"),
                (u, _payload(pid, routes=[{"route_basis": "profile_led", "route_kind": "direct", "primary_status": "primary",
                                           "evidence": [{"evidence_type": "observed_sequence", "reference_profile_id": "SYN-001",
                                                         "summary": "x"}]}]), "TR028")):
            with pytest.raises(psycopg.Error) as e:
                _as_owner(conn, who, "select trace_plan.record_transition_plan(%s::jsonb)", (json.dumps(payload),))
            assert e.value.sqlstate == state
        with pytest.raises(psycopg.Error) as e:                                       # no direct writes, even for the owner role
            _as_owner(conn, u, "insert into public.trace_pathway_runs (owner_user_id, candidate_profile_id, data_mode, target_role_id) "
                               "values (%s, %s, 'synthetic_test', 'PM-PM')", (u, pid))
        assert e.value.sqlstate == "42501"
        with pytest.raises(psycopg.Error) as e:
            with conn.transaction():
                conn.execute("set local role authenticated")
                conn.execute("select trace_plan.record_transition_plan('{}'::jsonb)")
        assert e.value.sqlstate == "42501"
        assert _as_owner(conn, u, "delete from public.trace_pathway_runs where pathway_run_id = %s returning 1", (run,)) == [(1,)]
    finally:
        conn.close()


def test_the_same_inputs_give_the_same_plan(base_settings, c, users):
    u, h = candidate(c, users)
    swe(c, h)
    pid = users.admin.execute("select candidate_profile_id from public.trace_candidate_profiles where owner_user_id = %s", (u,)).fetchone()[0]
    conn = db.connect(base_settings)
    try:
        svc, ctx = TransitionPlanService(conn), OwnerContext(owner_user_id=u, candidate_profile_id=pid)
        seq = load_sequencing_context(conn)
        at = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
        a = svc.build(ctx, seq, "PM-PM", ["platform_api"], "synthetic_test", at=at)
        b = svc.build(ctx, seq, "PM-PM", ["platform_api"], "synthetic_test", at=at)
        assert a == b
        later = svc.build(ctx, seq, "PM-PM", ["platform_api"], "synthetic_test", at=datetime(2026, 12, 31, tzinfo=timezone.utc))
        assert not any(x["is_current_opportunity"] for x in later["eligibility"]["postings"])   # past the 14-day window
        direct = next(r for r in later["routes"] if r["route_kind"] == "direct")
        assert "apply.no_verified_postings" in [x["template_key"] for x in direct["actions"]]
        assert later["decision"] == a["decision"]                                                # eligibility never moves a route
    finally:
        conn.close()


def test_targets_outside_the_pack_are_refused_and_the_current_role_is_allowed(c, users):
    # Phase 5 (D51): the current role is a valid target (the same role elsewhere); only roles without a pack are refused.
    _, h = candidate(c, users)
    add_job(c, h, "Product Manager", "PM-PM", month(2020, 1), ONGOING, [("Owned the roadmap", ["roadmap_ownership"], None)])
    same = c.post("/api/v1/transition-plan?target=PM-PM", headers=h)
    assert same.status_code == 201, same.text
    assert not [r for r in same.json()["routes"] if r["route_kind"] == "observed"]       # never a zero-move observed route
    rm = c.get(f"/api/v1/transition-plan/{same.json()['run_id']}/roadmap", headers=h).json()
    assert rm["move"]["kind"] == "same_role"
    apm = c.post("/api/v1/transition-plan?target=PM-APM", headers=h)
    assert apm.status_code == 201, apm.text                                              # APM is a real target
    apm_rm = c.get(f"/api/v1/transition-plan/{apm.json()['run_id']}/roadmap", headers=h).json()
    assert apm_rm["support"]["depth"] == "limited" and apm_rm["support"]["label"] == "Limited support"
    assert c.post("/api/v1/transition-plan?target=PM-DIR", headers=h).status_code == 422


def test_observed_routes_are_labelled_shown_beside_and_never_select(c, users):
    _, h = candidate(c, users)
    add_job(c, h, "Associate Product Manager", "PM-APM", month(2021, 1), ONGOING, [
        ("Ran discovery interviews with customers", ["product_discovery"], None)])
    p = plan(c, h)
    observed = [r for r in p["routes"] if r["route_kind"] == "observed"]
    assert observed and all(r["route_basis"] == "observed" and r["primary_status"] == "not_evaluated" and r["is_synthetic_test_data"]
                            and r["evidence_breadth_label"] == "synthetic_test_data" and not r["stages"] for r in observed)
    assert [s["role_id"] for s in observed[0]["steps"]] == ["PM-APM", "PM-PM"]
    assert routes(p)[("bridge", "PM-APM")]["primary_status"] == "not_evaluated"          # the user's own role is not a bridge
    assert p["routes"][-1]["route_kind"] == "observed"                                   # listed after the evaluated routes
    assert "observed" not in json.dumps(p["decision"]["trace"])                          # breadth is never a rule input



# --------------------------------------------------------------------------- remediation (D31, D35, D37, D38)
def test_the_outcome_metrics_question_decides_c4_through_the_api(c, users):
    _, h = candidate(c, users)
    rid = add_job(c, h, "Data Analyst", "ROLE-DA", month(2019, 1), ONGOING, [("Built retention dashboards", ["data_analysis"], None)])
    assert states(plan(c, h))["C4"] == "unknown"                                                 # not answered: Unknown (D35)
    (line,) = c.get("/api/v1/profile/records", headers=h).json()["employments"][0]["evidence"]
    for answer, expected in ((False, "transferable"), (True, "demonstrated")):
        r = c.put(f"/api/v1/evidence/{line['id']}/classification", headers=h,
                  json={"areas": ["data_analysis"], "qualifiers": {"data_analysis": {"outcome_metrics_influence": answer}}})
        assert r.status_code == 200, r.text
        assert states(plan(c, h))["C4"] == expected
    q = {a["code"]: a for a in c.get("/api/v1/taxonomy/responsibility-areas", headers=h).json()}["data_analysis"]["questions"]
    assert q["outcome_metrics_influence"].startswith("Did you define or own product outcome metrics")


def test_portfolio_scope_is_only_counted_when_the_user_states_it(c, users):
    _, h = candidate(c, users)
    add_job(c, h, "Product Owner", "ROLE-OWNER", month(2018, 1), ONGOING, [
        ("Owned the payments roadmap", ["roadmap_ownership"], None), ("Owned activation outcome metrics", ["outcome_ownership"], None),
        ("Ran discovery with merchants", ["product_discovery"], None), ("Aligned sales and engineering", ["stakeholder_management"], None)])
    plain = plan(c, h, target="PM-SPM")
    assert (plain["decision"]["rule"], plain["decision"]["primary"]) == ("R2", ["direct"])        # no title-based PM step
    r = c.post("/api/v1/transition-plan?target=PM-SPM&scope=portfolio", headers=h)
    assert r.status_code == 201 and r.json()["decision"]["primary"] == ["direct"] and r.json()["decision"]["rule"] == "R4"
    assert r.json()["constraints"]["portfolio_scope"] is True


def test_plans_share_a_dated_non_identifying_snapshot_and_old_rows_are_untouched(c, users):
    a = users.admin
    before = a.execute("select count(*) from trace_ref.reference_snapshots where state_fingerprint is null").fetchone()[0]
    _, h1 = candidate(c, users)
    _, h2 = candidate(c, users)
    swe(c, h1)
    swe(c, h2)
    r1, r2 = plan(c, h1)["run_id"], plan(c, h2)["run_id"]
    rows = a.execute("select r.reference_snapshot_id, s.created_at, s.first_observed_on, s.state_fingerprint from public.trace_pathway_runs r "
                     "join trace_ref.reference_snapshots s on s.snapshot_id = r.reference_snapshot_id where r.pathway_run_id = any(%s)",
                     ([r1, r2],)).fetchall()
    assert len({x[0] for x in rows}) == 1                                                       # one shared state, two plans
    snap_id, created, first, fingerprint = rows[0]
    assert (created.hour, created.minute, created.second, created.microsecond) == (0, 0, 0, 0) and len(fingerprint) == 64
    cols = [x[0] for x in a.execute("select column_name from information_schema.columns where table_schema = 'trace_ref' "
                                    "and table_name = 'reference_snapshots' order by ordinal_position").fetchall()]
    assert cols == ["snapshot_id", "data_mode", "settings", "eligible_profile_count", "confirmed_transition_count", "notes", "created_at",
                    "state_fingerprint", "first_observed_on"]                                     # no user, plan or profile ids
    assert a.execute("select count(*) from trace_ref.reference_snapshots where state_fingerprint is null").fetchone()[0] == before


def test_service_role_cannot_write_plan_or_snapshot_tables(base_settings, c, users):
    _, h = candidate(c, users)
    swe(c, h)
    run = plan(c, h)["run_id"]
    conn = db.connect(base_settings)
    try:
        for sql in ("insert into public.trace_pathway_runs (owner_user_id, candidate_profile_id, data_mode, target_role_id) "
                    "select owner_user_id, candidate_profile_id, data_mode, target_role_id from public.trace_pathway_runs where pathway_run_id = %(r)s",
                    "update public.trace_pathway_runs set status = 'failed' where pathway_run_id = %(r)s",
                    "delete from public.trace_pathway_runs where pathway_run_id = %(r)s",
                    "delete from public.trace_pathway_actions",
                    "truncate public.trace_pathway_recommendations cascade",
                    "insert into trace_ref.reference_snapshots (data_mode, settings, eligible_profile_count, confirmed_transition_count) "
                    "values ('empirical', '{}', 0, 0)",
                    "update trace_ref.reference_snapshots set notes = 'x'",
                    "truncate trace_ref.reference_snapshots cascade"):
            with pytest.raises(psycopg.Error) as e:
                with conn.transaction():
                    conn.execute("set local role service_role")
                    conn.execute(sql, {"r": run})
            assert e.value.sqlstate == "42501", sql
        with conn.transaction():
            conn.execute("set local role service_role")
            assert conn.execute("select count(*) from public.trace_pathway_runs where pathway_run_id = %s", (run,)).fetchone()[0] == 1
    finally:
        conn.close()


def test_actions_carry_their_labels(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    acts = [a for r in plan(c, h)["routes"] for s in r["stages"] for a in s["actions"]]
    assert any("learning_exercise" in a["labels"] for a in acts if a["kind"] == "build")
    assert all("factual_accuracy" in a["labels"] for a in acts if a["kind"] == "reposition")



# --------------------------------------------------------------------------- A5: the three Product Analyst comparisons, through the API
def test_api_customer_success_manager_gets_the_associate_pm(c, users):
    _, h = candidate(c, users)
    add_job(c, h, "Customer Success Manager", "ROLE-CS", month(2017, 1), ONGOING, [
        ("Managed renewals and escalations for enterprise accounts", ["customer_account_management"], None),
        ("Ran onboarding and fixed process gaps", ["operations_process"], None)])
    p = plan(c, h, contexts=["commercial_product"])
    s = states(p)
    assert (s["C2"], s["C4"]) == ("gap", "gap")
    assert (p["decision"]["rule"], p["decision"]["primary"]) == ("R3", ["PM-APM"])
    r = routes(p)
    apm = r[("bridge", "PM-APM")]
    assert apm["feasibility"]["decision_rights_levels"] == {"C2": "partial", "C4": "partial"}
    for part in apm["feasibility"]["partial_development"]:
        assert part["develops"] and part["incomplete"] and part["seek"]
    assert r[("direct", None)]["primary_status"] == "alternative"
    pan = r[("bridge", "ROLE-PAN")]
    assert (pan["primary_status"], pan["feasibility"]["entry"]) == ("not_recommended", "not_evidenced")
    assert r[("bridge", "ROLE-OWNER")]["primary_status"] in ("alternative", "not_recommended")


def test_api_data_scientist_without_outcome_ownership_gets_the_product_analyst(c, users):
    _, h = candidate(c, users)
    add_job(c, h, "Data Scientist", "ROLE-DS", month(2018, 1), ONGOING, [
        ("Built churn prediction models", ["technical_engineering"], None),
        ("Prioritised the modelling backlog with the analytics lead", ["prioritisation_backlog"], None),
        ("Presented model results to product and marketing", ["stakeholder_management"], None)])
    assert c.post("/api/v1/entries/skill", headers=h, json={"skill_text_raw": "Python", "confirm": True}).status_code == 201
    p = plan(c, h, contexts=["data_product"])
    s = states(p)
    assert (s["C2"], s["C4"]) == ("transferable", "gap")
    assert (p["decision"]["rule"], p["decision"]["primary"]) == ("R3", ["ROLE-PAN"])
    r = routes(p)
    pan, apm = r[("bridge", "ROLE-PAN")], r[("bridge", "PM-APM")]
    assert pan["primary_status"] == "primary" and pan["feasibility"]["decision_rights_levels"] == {"C4": "full"}
    assert pan["feasibility"]["entry"] == "evidenced" and pan["feasibility"]["progression_evidence"] == "hypothesis"
    assert apm["primary_status"] == "alternative" and apm["feasibility"]["decision_rights_levels"] == {"C4": "partial"}
    assert r[("direct", None)]["primary_status"] == "alternative"


def test_api_business_analyst_with_strategy_and_outcome_evidence_moves_directly(c, users):
    _, h = candidate(c, users)
    add_job(c, h, "Business Analyst", "ROLE-BA", month(2016, 1), ONGOING, [
        ("Wrote requirements for the payments module", ["requirements_analysis"], None),
        ("Prioritised the change backlog with operations", ["prioritisation_backlog"], None),
        ("Tracked adoption and decided the next release from it", ["outcome_ownership"], None),
        ("Interviewed operations users", ["product_discovery"], None),
        ("Ran workshops with stakeholders", ["stakeholder_management"], None),
        ("Ran fortnightly releases", ["delivery_execution"], None)])
    p = plan(c, h)
    s = states(p)
    assert (s["C2"], s["C4"]) == ("transferable", "demonstrated")
    assert (p["decision"]["rule"], p["decision"]["primary"]) == ("R2", ["direct"])
    assert {r_["primary_status"] for r_ in p["routes"] if r_["route_kind"] == "bridge"} == {"not_recommended"}


# --------------------------------------------------------------------------- D41: J12 and a current Associate PM, through the API
def test_api_a_current_apm_is_unclear_for_currently_a_product_manager(c, users):
    _, h = candidate(c, users)
    add_job(c, h, "Associate Product Manager", "PM-APM", month(2021, 1), ONGOING, [("Ran discovery interviews", ["product_discovery"], None)])
    p = plan(c, h)
    j12 = posting(p, "J12")
    assert j12["evidence_use"] == "historical_eligibility_evidence"
    current = j12["eligibility"]["required"][0]["result"]["parts"][1]
    assert current["outcome"] == "unclear" and "equivalent" in current["explanation"]
    assert p["versions"]["eligibility_rules"] == "pm-eligibility-v3"
