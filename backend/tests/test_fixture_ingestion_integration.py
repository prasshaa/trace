"""Database tests for extracted-draft ingestion (M3, data foundation v2.4.0). Local Supabase only.

Every user, profile, resume and consent record here is synthetic and transient: test users are
created per test and deleted afterwards (cascading to everything they own), and the test consent
document is removed at the end of the module. The only extracted content is Trace's 41 synthetic
fixture resumes.

An admin connection (table owner) is used only for test setup that no client role may perform:
designating a profile and a resume as synthetic test fixtures and adding the test consent document.
Ingestion always runs through trace_ingest, and role mapping through the trusted M5 functions
(map_employment, choose_role), inside owner_session.
"""
import copy
import hashlib
import json
import uuid
from collections import Counter
from decimal import Decimal

import psycopg
import pytest
from pydantic import ValidationError

from app import db
from app.services.candidate_profile.repository import TargetIsCurrentRole
from app.config import get_settings
from app.services.candidate_profile import (
    CandidateProfileService, ConsentRequired, CorrectionNotAvailable, EmploymentCreate, EmploymentUpdate,
    IngestionConflict, IngestionNotAllowed, InvalidRecord, InvalidReference, OwnerContext, PermissionDenied,
    ProfileCreate, RecordNotFound, load_sequencing_context, owner_session,
)
from app.services.resume_extraction import ExtractedActivity, ExtractedEvidence, ExtractedSkill, FixtureExtractor
from tests.career_history_support import FOUNDATION, load_trace_model, oracle_context
from tests.test_pathway_result_integration import assert_client_safe

pytestmark = pytest.mark.integration
SYNTHETIC = FOUNDATION / "data" / "synthetic"
PM_TARGETS = ("PM-APM", "PM-PM", "PM-SPM", "PM-LEAD", "PM-PRINCIPAL", "PM-GPM", "PM-HEAD", "PM-DIR", "PM-SDIR")


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def settings():
    s = get_settings()
    if s.database_url is None:
        pytest.skip("DATABASE_URL not configured")
    try:
        with db.connect(s) as c:
            if c.execute("select to_regnamespace('trace_ingest')").fetchone()[0] is None:
                pytest.skip("v2.4.0 ingestion schema not applied")
    except psycopg.OperationalError as exc:
        pytest.skip(f"local database unreachable: {type(exc).__name__}")
    return s


@pytest.fixture(scope="module")
def extractor():
    return FixtureExtractor(SYNTHETIC / "synthetic_profiles.jsonl", SYNTHETIC / "synthetic_resume_pdfs.zip")


@pytest.fixture(scope="module")
def consent_versions(settings):
    """An approved and a draft resume_processing consent document, synthetic and test-only."""
    tag = uuid.uuid4().hex[:8]
    approved, draft, retired = f"pgtest-m3-{tag}-approved", f"pgtest-m3-{tag}-draft", f"pgtest-m3-{tag}-retired"
    with db.connect(settings) as admin:
        admin.autocommit = True
        for version, status, retired_at in ((approved, "approved", None), (draft, "draft_not_reviewed", None),
                                            (retired, "approved", "now()")):
            admin.execute(
                "insert into public.trace_consent_documents (consent_version, purpose, title, body_sha256, legal_review_status, "
                "effective_from, retired_at) values (%s, 'resume_processing', 'Synthetic test consent (not legal text)', %s, %s, "
                f"now() - interval '1 day', {retired_at or 'null'})",
                (version, hashlib.sha256(version.encode()).hexdigest(), status))
    yield {"approved": approved, "draft": draft, "retired": retired}
    with db.connect(settings) as admin:
        admin.autocommit = True
        admin.execute("delete from public.trace_consent_documents where consent_version in (%s, %s, %s)", (approved, draft, retired))


@pytest.fixture
def env(settings):
    admin = db.connect(settings)
    admin.autocommit = True
    conn = db.connect(settings)
    users = []
    try:
        yield CandidateProfileService(conn), admin, conn, users
    finally:
        conn.close()
        if users:
            admin.execute("delete from auth.users where id = any(%s)", (users,))
        admin.close()


@pytest.fixture(scope="module")
def context(settings):
    with db.connect(settings) as c:
        return load_sequencing_context(c)


def provision(env, extractor, fixture_id, consent_versions, *, consent="approved", fixture_profile=True, fixture_resume=True,
              document=None):
    """A synthetic user with a fixture profile, the fixture's resume and (optionally) a consent grant.
    The candidate creates the profile, uploads the resume metadata and records consent through the
    owner session; only the test-fixture designation is set by the table owner (no client role can)."""
    svc, admin, conn, users = env
    user = uuid.uuid4()
    users.append(user)
    admin.execute("insert into auth.users (id, email) values (%s, %s)", (user, f"m3-{user}@example.invalid"))
    profile = svc.create_profile(user, ProfileCreate(headline="Synthetic fixture candidate"))
    data = document if document is not None else extractor.resume_bytes(fixture_id)
    resume = uuid.uuid4()
    with owner_session(conn, user):
        conn.execute("insert into public.trace_resume_documents (candidate_profile_id, storage_object_path, original_filename, "
                     "mime_type, byte_size, content_sha256) values (%s, %s, %s, 'application/pdf', %s, %s)",
                     (profile, f"{user}/{resume}.pdf", f"{fixture_id}_synthetic_resume.pdf", len(data),
                      hashlib.sha256(data).hexdigest()))
        resume = conn.execute("select resume_id from public.trace_resume_documents where owner_user_id = %s", (user,)).fetchone()[0]
        if consent:
            conn.execute("insert into public.trace_consent_events (purpose, status, consent_version) "
                         "values ('resume_processing', 'granted', %s)", (consent_versions["approved"],))
    if fixture_profile:
        admin.execute("update public.trace_candidate_profiles set is_test_fixture = true, fixture_profile_id = %s "
                      "where candidate_profile_id = %s", (fixture_id, profile))
    if fixture_resume:
        admin.execute("update public.trace_resume_documents set is_test_fixture = true where resume_id = %s", (resume,))
    return OwnerContext(owner_user_id=user, candidate_profile_id=profile), resume


def run_row(admin, run_id):
    return admin.execute("select status, error_code, result_sha256, provider_name, model_name, prompt_version, consent_event_id "
                         "from public.trace_extraction_runs where extraction_run_id = %s", (run_id,)).fetchone()


def record_count(admin, profile):
    return admin.execute(
        "select (select count(*) from public.trace_candidate_employers where candidate_profile_id = %(p)s)"
        " + (select count(*) from public.trace_candidate_employments where candidate_profile_id = %(p)s)"
        " + (select count(*) from public.trace_candidate_education where candidate_profile_id = %(p)s)"
        " + (select count(*) from public.trace_candidate_career_activities where candidate_profile_id = %(p)s)"
        " + (select count(*) from public.trace_candidate_career_breaks where candidate_profile_id = %(p)s)"
        " + (select count(*) from public.trace_candidate_evidence where candidate_profile_id = %(p)s)"
        " + (select count(*) from public.trace_candidate_skills where candidate_profile_id = %(p)s)",
        {"p": profile}).fetchone()[0]


def _date(view):
    return (view.year, view.month, view.precision)


def _fixture_date(value, precision):
    if precision == "month":
        y, m = value.split("-")
        return (int(y), int(m), "month")
    if precision == "year":
        return (int(value), None, "year")
    return (None, None, precision)


def assert_matches_fixture(p, fx, resume, run_id):
    """Every extracted record equals its fixture source, with extracted-draft provenance."""
    def prov(rec, location):
        pv = rec.provenance
        assert (pv.information_origin, pv.review_status, pv.verification_status) == ("source_extracted", "pending_review", "unverified")
        assert (pv.extraction_confidence, pv.source_resume_id, pv.extraction_run_id, pv.source_location) == (
            Decimal("1.000"), resume, run_id, location)

    employers = {e["employer_id"]: e for e in fx["employers"]}
    jobs = sorted(fx["employments"], key=lambda e: e["sequence_index"])
    assert len(p.employment_history) == len(jobs)
    for i, (got, e) in enumerate(zip(p.employment_history, jobs), 1):
        assert (got.sequence_index, got.title_raw, got.employment_type, got.primary_career_sequence,
                got.counts_as_full_time_experience, got.classification_note, got.is_current_as_stated) == (
            e["sequence_index"], e["title_raw_redacted"], e["employment_type"], e["primary_career_sequence"],
            e["counts_as_full_time_experience"], e["classification_note"], e["is_current_as_stated"])
        assert _date(got.start) == _fixture_date(e.get("start"), e["start_precision"])
        assert _date(got.end) == _fixture_date(e.get("end"), e["end_precision"])
        # Extraction is not mapping: the role stays unmapped until a trusted mapping process runs.
        assert (got.role_id, got.seniority_code, got.mapping_status, got.mapping_confidence) == ("ROLE-UNKNOWN", None, "unresolved", 0)
        assert got.entry_source == "resume_extraction"
        er = employers[e["employer_id"]]
        assert (got.employer.employer_name, got.employer.industry_raw, got.employer.industry_sector, got.employer.company_model,
                got.employer.business_model, got.employer.company_size_band, got.employer.attribute_provenance) == (
            e["employer_id"], er["industry_raw"], er["industry_sector"], er["company_model"], er["business_model"],
            er["company_size_band"], "resume_stated")
        prov(got, f"Professional experience, entry {i}")
        texts = sorted(x.evidence_text for x in got.responsibilities + got.achievements + got.other_evidence)
        assert texts == sorted(x["text"] for x in fx["evidence"] if x["employment_id"] == e["employment_id"])
        for x in got.responsibilities + got.achievements + got.other_evidence:
            assert x.provenance.information_origin == "source_extracted" and x.provenance.source_location.startswith(
                f"Professional experience, entry {i}, bullet ")
    assert len({e.employer.candidate_employer_id for e in p.employment_history}) == len(fx["employers"])
    key = lambda t: tuple(str(x) for x in t)     # noqa: E731  (the profile orders education by date, not by resume entry)
    assert sorted(((d.qualification_level, d.field_of_study, d.study_mode, d.completion_status, _date(d.start), _date(d.end),
                    d.provenance.source_location) for d in p.education), key=key) == sorted((
        (d["qualification_level"], d.get("field_of_study"), d["study_mode"], d["completion_status"],
         _fixture_date(d.get("start"), d["start_precision"]), _fixture_date(d.get("end"), d["end_precision"]), f"Education, entry {i}")
        for i, d in enumerate(fx["education"], 1)), key=key)
    for d in p.education:
        prov(d, d.provenance.source_location)
    assert [(b.break_reason, b.note, _date(b.start), _date(b.end)) for b in p.career_breaks] == [
        (b["break_reason"], b.get("note_redacted"), _fixture_date(b.get("start"), b["start_precision"]),
         _fixture_date(b.get("end"), b["end_precision"])) for b in fx["career_breaks"]]
    assert sorted(s.skill_text_raw for s in p.skills) == sorted(s["skill_text_raw"] for s in fx["skills"])
    for s in p.skills:
        assert (s.taxonomy, s.evidence_source, s.supported_by) == (None, "resume_explicit", None)
        assert s.provenance.information_origin == "source_extracted" and s.provenance.source_location.startswith("Skills, item ")
    assert all(v == [] for v in p.career_activities.values()) and p.unattached_evidence == []
    assert _date(p.resume_as_of) == _fixture_date(fx["resume_as_of"], fx["resume_as_of_precision"])


# The mapping differs from the fixtures' curated labels only here (asserted exactly, so nothing is hidden):
# * SYN-011 "Project Manager": the alias table gives ROLE-PROJ, the fixture is curated as ROLE-PGM. Kept
#   as a documented discrepancy pending source verification; neither the fixture nor the alias is changed.
# * curated needs_review / unresolved records: the simulated candidate makes no decision, so they stay
#   unmapped (ROLE-UNKNOWN) and remain barriers.
MAPPING_DIFFERENCES = {
    "SYN-011-EMP-01": ("Project Manager", "ROLE-PROJ", "ROLE-PGM"),
    "SYN-034-EMP-02": ("Product Consultant (Freelance)", "ROLE-UNKNOWN", "ROLE-CONSULT"),
    "SYN-036-EMP-02": ("Product Owner", "ROLE-UNKNOWN", "ROLE-OWNER"),
}


def map_like_a_candidate(svc, ctx, p, fx):
    """Real trusted mapping. map_employment for every employment; where no automatic mapping exists the
    candidate decides with the curated role when the curation is confirmed (with a PM-ownership area if the
    rule requires one) and makes no decision otherwise. Returns employment_id -> how it was mapped."""
    by_seq = {e["sequence_index"]: e for e in fx["employments"]}
    how = {}
    for rec in p.employment_history:
        e = by_seq[rec.sequence_index]
        r = svc.map_employment(ctx, rec.candidate_employment_id)
        if r.result == "mapped_automatically":
            how[e["employment_id"]] = "automatic"
        elif e["mapping_status"] in ("confirmed", "auto_high_confidence"):
            choice = next(c for c in r.options.alias_options + r.options.other_roles if c.role_id == e["role_id"])
            svc.choose_role(ctx, rec.candidate_employment_id, e["role_id"],
                            ["roadmap_ownership"] if choice.requires_ownership_clarification else None)
            how[e["employment_id"]] = "candidate_selected"
        else:
            assert r.result in ("decision_required", "unresolved"), e["employment_id"]
            how[e["employment_id"]] = "undecided"
    return how


def as_mapped(fx, p):
    """The fixture with the mapping the trusted process actually stored, for the trace_model oracle. A
    candidate decision has no confidence and is not held to the threshold; trace_model has no such notion,
    so it is given confidence 1.0, which is equivalent."""
    rows = {r.sequence_index: r for r in p.employment_history}
    out = copy.deepcopy(fx)
    for e in out["employments"]:
        r = rows[e["sequence_index"]]
        e.update(role_id=r.role_id, seniority_code=r.seniority_code, mapping_status=r.mapping_status,
                 mapping_confidence=1.0 if r.mapping_confidence is None else float(r.mapping_confidence))
    return out


def expected_position(tm, octx, fx):
    """Oracle: the latest primary employment of trace_model's own career sequence decides."""
    seq = tm.career_sequence(fx, octx)
    if not seq:
        return ("unknown", None)
    emp = next(e for e in fx["employments"] if e["employment_id"] == seq[-1]["employment_id"])
    last = tm.classify_employment(emp, octx)
    return ("known", emp["role_id"]) if last["is_transition_eligible"] else ("unknown", None)


def assert_sequencing_matches_reference(tm, octx, history, p, fx):
    to_fixture = {str(e.candidate_employment_id): j["employment_id"]
                  for e, j in zip(p.employment_history, sorted(fx["employments"], key=lambda e: e["sequence_index"]))}
    pid = fx["profile_id"]
    as_of = tm.bounds(fx.get("resume_as_of"), fx.get("resume_as_of_precision", "unknown"))
    by_id = {to_fixture[r.stated.employment_id]: r for r in history.records}
    for e in fx["employments"]:
        exp, got = tm.classify_employment(e, octx), by_id[e["employment_id"]]
        assert (got.start.lo, got.start.hi, got.end.lo, got.end.hi) == (exp["start_lo"], exp["start_hi"], exp["end_lo"], exp["end_hi"]), pid
        assert got.exclusion_reasons == exp["exclusion_reasons"], pid
        assert got.is_transition_eligible == exp["is_transition_eligible"], pid
        assert (got.seniority_code, got.seniority_rank, got.role_family) == (exp["seniority_code"], exp["seniority_rank"], exp["role_family"]), pid
        assert got.counts_as_full_time_experience == exp["counts_as_full_time_experience"], pid
        assert (got.tenure_months_min, got.tenure_months_max, got.tenure_is_censored) == tm.tenure_months(exp, as_of), pid
    assert [to_fixture[r.stated.employment_id] for r in history.records if r.in_primary_sequence] == \
        [r["employment_id"] for r in tm.career_sequence(fx, octx)], pid
    expected = tm.episode_transitions(fx, octx)
    assert len(history.transitions) == len(expected), pid
    for got, exp in zip(history.transitions, expected):
        d = got.model_dump()
        d["from_employment_id"], d["to_employment_id"] = to_fixture[d["from_employment_id"]], to_fixture[d["to_employment_id"]]
        assert d == {k: v for k, v in exp.items() if k != "profile_id"}, pid
    got_paths = [{"segment_no": s.segment_no, "role_path": s.role_path, "entry_change_types": s.entry_change_types,
                  "stints": [{**st.model_dump(), "employment_ids": [to_fixture[i] for i in st.employment_ids]} for st in s.stints]}
                 for s in history.segments]
    assert got_paths == [{"segment_no": x["segment_no"], "role_path": x["role_path"], "entry_change_types": x["entry_change_types"],
                          "stints": x["stints"]} for x in tm.career_episode_paths(fx, octx)], pid


# --------------------------------------------------------------------------- all 41 fixtures, end to end
def test_all_41_fixtures_ingest_review_and_sequence_like_the_reference_model(env, extractor, consent_versions, context):
    svc, admin, conn, users = env
    plan_rows = ("select (select count(*) from public.trace_pathway_runs) + (select count(*) from public.trace_pathway_recommendations) "
                 "+ (select count(*) from public.trace_pathway_route_steps)")
    plans_before = admin.execute(plan_rows).fetchone()[0]          # other accounts' saved plans (never counted as ours)
    tm = load_trace_model()
    octx = oracle_context(tm)
    assert len(extractor.fixture_ids) == 41
    bases, differences, positions, evidence = {}, {}, {}, {}
    tiers, states, results, refused = {}, {}, 0, 0
    for fixture_id in extractor.fixture_ids:
        fx = extractor.fixture(fixture_id)
        ctx, resume = provision(env, extractor, fixture_id, consent_versions)
        result = extractor.extract(extractor.resume_bytes(fixture_id))
        outcome = svc.ingest_extraction(ctx, resume, result)
        assert outcome.status == "succeeded", fixture_id
        status, error, sha, provider, model, version, consent_event = run_row(admin, outcome.extraction_run_id)
        assert (status, error, provider, model, version) == ("succeeded", None, "trace-fixture-extractor", "deterministic-fixture", "fixture-1")
        assert sha and consent_event, fixture_id
        assert admin.execute("select p.extraction_status, r.parse_status from public.trace_candidate_profiles p "
                             "join public.trace_resume_documents r on r.candidate_profile_id = p.candidate_profile_id "
                             "where p.candidate_profile_id = %s", (ctx.candidate_profile_id,)).fetchone() == ("extracted", "parsed")
        p = svc.get_profile(ctx)
        assert_matches_fixture(p, fx, resume, outcome.extraction_run_id)

        # Pending extracted drafts are barriers: nothing is eligible before the candidate reviews them.
        assert all("review_pending" in r.exclusion_reasons for r in svc.sequence_employment_history(ctx, context).records)
        for e in p.employment_history:
            assert svc.confirm_record(ctx, "employment", e.candidate_employment_id) == "user_confirmed"
        how = map_like_a_candidate(svc, ctx, p, fx)
        bases.update(how)
        p = svc.get_profile(ctx)
        for rec, e in zip(p.employment_history, sorted(fx["employments"], key=lambda e: e["sequence_index"])):
            assert rec.provenance.review_status == "user_confirmed" and rec.title_raw == e["title_raw_redacted"]   # mapping is not review
            assert rec.role_mapping_basis == {"automatic": "alias_match", "candidate_selected": "candidate_selected", "undecided": None}[how[e["employment_id"]]]
            if rec.role_id != e["role_id"]:
                differences[e["employment_id"]] = (rec.title_raw, rec.role_id, e["role_id"])
        mapped = as_mapped(fx, p)
        history = svc.sequence_employment_history(ctx, context)
        assert_sequencing_matches_reference(tm, octx, history, p, mapped)
        for rec in p.employment_history:                                  # undecided records stay barriers
            if rec.role_mapping_basis is None:
                assert "unresolved_role" in next(r for r in history.records if r.stated.employment_id == str(rec.candidate_employment_id)).exclusion_reasons

        position = svc.current_position(ctx, context)
        if position.reason == "role_unconfirmed":                          # Phase 7.2.1: an automatic match alone
            latest = next(r for r in p.employment_history if r.candidate_employment_id == position.candidate_employment_id)
            assert how[next(e["employment_id"] for e in fx["employments"] if e["sequence_index"] == latest.sequence_index)] == "automatic", fixture_id     # is no starting point
            svc.choose_role(ctx, latest.candidate_employment_id, latest.role_id)               # the user confirms it
            position = svc.current_position(ctx, context)
        assert (position.status, position.role_id) == expected_position(tm, octx, mapped), fixture_id
        positions[fixture_id] = position
        if position.status == "known":
            for target in PM_TARGETS:
                ev = svc.route_evidence(ctx, context, target)
                r = ev.evidence
                assert r.is_synthetic_test_data and r.data_mode == "synthetic_test" and r.evidence_notice.startswith("SYNTHETIC TEST DATA")
                assert r.origin.role_id == position.role_id and r.destination.role_id == target
                assert all(o.route[0] == position.role_id and o.route[-1] == target and len(o.route) >= 2 for o in r.observations)
                assert {tuple(s.route) for s in r.route_summaries} == {tuple(o.route) for o in r.observations}
                assert all(s.evidence_class == "synthetic_test_only" and not s.timeline_display_allowed for s in r.route_summaries)
                assert r.has_observed_pathways == bool(r.observations)
                assert "probab" not in ev.note.lower() and "success" not in ev.note.lower()
                evidence[(position.role_id, target)] = len(r.observations)

        # M6: the candidate classifies and reviews their extracted evidence with the curated areas (real function),
        # then the pathway result is checked against an independent oracle for every PM target.
        jobs = {rec.sequence_index: rec for rec in p.employment_history}
        fx_jobs = {e["employment_id"]: e for e in fx["employments"]}
        for x in fx["evidence"]:
            rec = jobs[fx_jobs[x["employment_id"]]["sequence_index"]]
            ev_rec = next(y for y in rec.responsibilities + rec.achievements + rec.other_evidence if y.evidence_text == x["text"])
            if x.get("responsibility_area"):
                svc.classify_evidence(ctx, ev_rec.candidate_evidence_id, [x["responsibility_area"]])
            svc.confirm_record(ctx, "evidence", ev_rec.candidate_evidence_id)
        eligible = {e["employment_id"] for e in tm.career_sequence(mapped, octx)
                    if tm.classify_employment(next(m for m in mapped["employments"] if m["employment_id"] == e["employment_id"]), octx)["is_transition_eligible"]}
        oracle_areas = {x["responsibility_area"] for x in fx["evidence"] if x.get("responsibility_area") and x["employment_id"] in eligible}
        held = {m["role_id"] for m in mapped["employments"] if m["employment_id"] in eligible}
        for target in PM_TARGETS:
            if position.status == "known" and target == position.role_id:     # Phase 5 (D51): allowed, never a zero-move route
                assert svc.pathway_result(ctx, context, target).routes == []
                refused += 1
                continue
            pr = svc.pathway_result(ctx, context, target)
            assert_client_safe(pr)
            assert pr.candidate_evidence.classified_areas == sorted(oracle_areas), fixture_id
            if position.status != "known":
                assert pr.routes == []
                continue
            m5 = svc.route_evidence(ctx, context, target).evidence
            assert sorted(x.route_id for x in pr.routes) == sorted(">".join(s.route) for s in m5.route_summaries), (fixture_id, target)
            exp = {">".join(x.route): x for x in m5.route_step_experience}
            expected_order = []
            for x in pr.routes:
                e = exp[x.route_id]
                common = sorted(a.responsibility_area for a in e.areas if e.state == "areas_disclosed" and e.observation_count >= 5
                                and a.profile_count >= 5 and 2 * a.profile_count > e.observation_count)
                matched = sorted(set(common) & oracle_areas)
                tier = ("not_established" if not common or not oracle_areas else "aligned" if len(matched) == len(common)
                        else "partial" if matched else "limited")
                steps = [r for r in dict.fromkeys(r.role_id for r in x.route[1:-1]) if r not in held]
                assert (x.fit.tier, x.fit.matched_areas, [g.role_id for g in x.step_gaps], x.observed.experience_state) == (
                    tier, matched, steps, e.state), (fixture_id, target, x.route_id)
                expected_order.append(({"aligned": 0, "partial": 1, "limited": 2, "not_established": 3}[tier], len(steps), x.route_id))
                tiers[tier] = tiers.get(tier, 0) + 1
                states[e.state] = states.get(e.state, 0) + 1
            assert [x.route_id for x in pr.routes] == [k[2] for k in sorted(expected_order)], (fixture_id, target)
            results += 1

    assert differences == MAPPING_DIFFERENCES
    assert len(bases) == 106 and len(positions) == 41
    assert admin.execute(plan_rows).fetchone()[0] == plans_before                 # read-only: this test wrote no plan rows
    print("\nM5 fixture summary:", dict(Counter(bases.values())), dict(Counter(p.status for p in positions.values())),
          dict(Counter(p.reason for p in positions.values() if p.reason)),
          {"position-target pairs": len(evidence), "with observed routes": sum(1 for n in evidence.values() if n)},
          "current roles:", dict(Counter(p.role_id for p in positions.values() if p.role_id)),
          "routes:", {f"{a}->{b}": n for (a, b), n in sorted(evidence.items()) if n},
          "pathway results:", results, "route fit tiers:", tiers, "experience states:", states)
    assert results + refused == 41 * len(PM_TARGETS) and refused == 41        # one refused self-target per fixture


# --------------------------------------------------------------------------- review reuses M1/M2
def test_extracted_drafts_are_reviewed_with_the_existing_actions(env, extractor, consent_versions):
    svc, admin, conn, _ = env
    ctx, resume = provision(env, extractor, "SYN-001", consent_versions)
    svc.ingest_extraction(ctx, resume, extractor.extract(extractor.resume_bytes("SYN-001")))
    jobs = svc.get_profile(ctx).employment_history
    e0, e1 = jobs[0].candidate_employment_id, jobs[1].candidate_employment_id
    with pytest.raises(CorrectionNotAvailable):           # correcting an extracted value is M4
        svc.update_employment(ctx, e0, EmploymentUpdate(title_raw="Senior Software Engineer"))
    assert svc.confirm_record(ctx, "employment", e0) == "user_confirmed"
    assert svc.reject_record(ctx, "employment", e1) == "rejected"
    assert svc.reinstate_record(ctx, "employment", e1) == "pending_review"
    skill = svc.get_profile(ctx).skills[0].candidate_skill_id
    assert svc.confirm_record(ctx, "skill", skill) == "user_confirmed"
    jobs = {j.candidate_employment_id: j for j in svc.get_profile(ctx).employment_history}
    assert (jobs[e0].title_raw, jobs[e0].provenance.information_origin, jobs[e0].provenance.verification_status) == (
        "Software Engineer", "source_extracted", "unverified")         # review never changes origin or verification


def test_activities_evidence_and_skill_links_are_ingested(env, extractor, consent_versions):
    svc, *_ = env
    ctx, resume = provision(env, extractor, "SYN-002", consent_versions)
    base = extractor.extract(extractor.resume_bytes("SYN-002"))
    loc = {"extraction_confidence": Decimal("0.900")}
    result = base.model_copy(update={
        "career_activities": [ExtractedActivity(key="ACT-1", activity_type="independent_project", title_raw="Synthetic side project",
                                                related_employment_key=base.employments[0].key,
                                                source_location="Projects, entry 1", **loc)],
        "evidence": list(base.evidence) + [ExtractedEvidence(key="ACT-1-E1", evidence_type="achievement",
                                                             evidence_text="Shipped a synthetic budgeting app", activity_key="ACT-1",
                                                             source_location="Projects, entry 1, bullet 1", **loc)],
        "skills": list(base.skills) + [ExtractedSkill(skill_text_raw="Prototyping", evidence_key="ACT-1-E1",
                                                      source_location="Projects, entry 1, bullet 1", **loc)]})
    svc.ingest_extraction(ctx, resume, type(base).model_validate(result.model_dump()))
    p = svc.get_profile(ctx)
    act = p.career_activities["independent_project"][0]
    assert (act.title_raw, act.related_employment_id, act.provenance.extraction_confidence, act.category_code) == (
        "Synthetic side project", p.employment_history[0].candidate_employment_id, Decimal("0.900"), None)
    assert [x.evidence_text for x in act.achievements] == ["Shipped a synthetic budgeting app"]
    assert act.achievements[0].skills[0].name == "Prototyping"


# --------------------------------------------------------------------------- forged fields
def test_forged_review_mapping_and_provenance_fields_are_rejected(env, extractor, consent_versions):
    svc, admin, conn, _ = env
    ctx, resume = provision(env, extractor, "SYN-003", consent_versions)
    result = extractor.extract(extractor.resume_bytes("SYN-003"))
    raw = result.model_dump()
    for forged in ({"review_status": "user_confirmed"}, {"role_id": "PM-PM"}, {"information_origin": "user_stated"},
                   {"verification_status": "independently_verified"}):
        bad = json.loads(json.dumps(raw, default=str))
        bad["employments"][0].update(forged)
        with pytest.raises(ValidationError):                         # the contract forbids them ...
            type(result).model_validate(bad)
    with owner_session(conn, ctx.owner_user_id) as repo:
        run = repo.start_extraction(ctx.candidate_profile_id, resume, result.extractor_version)
    payload = result.ingestion_payload()
    for where, forged in (("employments", {"review_status": "user_confirmed"}), ("employments", {"role_id": "PM-PM"}),
                          ("employments", {"mapping_confidence": 1}), ("skills", {"skill_id": "SKL-SQL"}),
                          ("skills", {"verified": True}), ("employers", {"attribute_provenance": "participant_confirmed"}),
                          ("evidence", {"responsibility_area": "roadmap_ownership"}), (None, {"information_origin": "user_stated"})):
        bad = json.loads(json.dumps(payload))
        (bad[where][0] if where else bad).update(forged)
        with pytest.raises(InvalidReference):                        # ... and so does the database (TR006)
            with owner_session(conn, ctx.owner_user_id) as repo:
                repo.ingest_extraction(run, bad)
    assert record_count(admin, ctx.candidate_profile_id) == 0
    # Ordinary candidate SQL cannot create extracted records either (M2 column grants and guard).
    with pytest.raises(PermissionDenied):
        with owner_session(conn, ctx.owner_user_id):
            conn.execute("insert into public.trace_candidate_employments (candidate_profile_id, sequence_index, title_raw, "
                         "information_origin) values (%s, 1, 'Forged', 'source_extracted')", (ctx.candidate_profile_id,))


# --------------------------------------------------------------------------- consent
def _runs(admin, profile):
    return admin.execute("select status, error_code from public.trace_extraction_runs where candidate_profile_id = %s "
                         "order by created_at", (profile,)).fetchall()


def test_ingestion_requires_current_approved_resume_processing_consent(env, extractor, consent_versions):
    svc, admin, conn, _ = env
    result = extractor.extract(extractor.resume_bytes("SYN-004"))

    ctx, resume = provision(env, extractor, "SYN-004", consent_versions, consent=None)          # never asked
    with pytest.raises(ConsentRequired):
        svc.ingest_extraction(ctx, resume, result)
    assert _runs(admin, ctx.candidate_profile_id) == [] and record_count(admin, ctx.candidate_profile_id) == 0

    ctx, resume = provision(env, extractor, "SYN-004", consent_versions)                         # granted, then denied
    with owner_session(conn, ctx.owner_user_id):
        conn.execute("insert into public.trace_consent_events (purpose, status, consent_version) values "
                     "('resume_processing', 'denied', %s)", (consent_versions["approved"],))
    with pytest.raises(ConsentRequired):
        svc.ingest_extraction(ctx, resume, result)

    for version in ("draft", "retired"):                                                         # not an approved, current text
        ctx, resume = provision(env, extractor, "SYN-004", consent_versions, consent=None)
        admin.execute("insert into public.trace_consent_events (user_id, purpose, status, consent_version) "
                      "values (%s, 'resume_processing', 'granted', %s)", (ctx.owner_user_id, consent_versions[version]))
        with pytest.raises(ConsentRequired):
            svc.ingest_extraction(ctx, resume, result)
        assert record_count(admin, ctx.candidate_profile_id) == 0

    # Withdrawn between the start of the run and ingestion: refused at ingestion, run failed, nothing written.
    ctx, resume = provision(env, extractor, "SYN-004", consent_versions)
    with owner_session(conn, ctx.owner_user_id) as repo:
        run = repo.start_extraction(ctx.candidate_profile_id, resume, result.extractor_version)
    with owner_session(conn, ctx.owner_user_id):
        conn.execute("insert into public.trace_consent_events (purpose, status, consent_version) values "
                     "('resume_processing', 'withdrawn', %s)", (consent_versions["approved"],))
    with pytest.raises(ConsentRequired):
        with owner_session(conn, ctx.owner_user_id) as repo:
            repo.ingest_extraction(run, result.ingestion_payload())
    with owner_session(conn, ctx.owner_user_id) as repo:
        assert repo.fail_extraction(run, "consent_required") == "failed"
    assert _runs(admin, ctx.candidate_profile_id) == [("failed", "consent_required")]
    assert record_count(admin, ctx.candidate_profile_id) == 0


# --------------------------------------------------------------------------- fixture scope and ownership
def test_only_synthetic_fixture_profiles_resumes_and_results_are_accepted(env, extractor, consent_versions):
    svc, admin, conn, _ = env
    result = extractor.extract(extractor.resume_bytes("SYN-005"))
    for flags in ({"fixture_profile": False}, {"fixture_resume": False}):
        ctx, resume = provision(env, extractor, "SYN-005", consent_versions, **flags)
        with pytest.raises(IngestionNotAllowed):
            svc.ingest_extraction(ctx, resume, result)
        assert _runs(admin, ctx.candidate_profile_id) == [] and record_count(admin, ctx.candidate_profile_id) == 0

    ctx, resume = provision(env, extractor, "SYN-005", consent_versions)
    other = extractor.extract(extractor.resume_bytes("SYN-006"))                 # another fixture's result
    with pytest.raises(IngestionNotAllowed):
        svc.ingest_extraction(ctx, resume, other)
    tampered = result.model_copy(update={"source_sha256": "0" * 64})              # not this resume's document
    with pytest.raises(IngestionNotAllowed):
        svc.ingest_extraction(ctx, resume, tampered)
    assert [s for s, _ in _runs(admin, ctx.candidate_profile_id)] == ["failed", "failed"]
    assert record_count(admin, ctx.candidate_profile_id) == 0

    b_ctx, b_resume = provision(env, extractor, "SYN-005", consent_versions)
    with pytest.raises(RecordNotFound):                                           # another user's resume
        svc.ingest_extraction(ctx, b_resume, result)
    with pytest.raises(RecordNotFound):                                           # another user's run
        with owner_session(conn, ctx.owner_user_id) as repo:
            repo.ingest_extraction(admin.execute("select gen_random_uuid()").fetchone()[0], result.ingestion_payload())
    assert record_count(admin, b_ctx.candidate_profile_id) == 0


def test_the_database_allowlist_is_exactly_the_41_fixture_pdfs(env, extractor):
    """The approved (fixture id, PDF SHA-256) pairs in the database equal the PDF archive, pair for pair."""
    _, admin, _, _ = env
    approved = dict(admin.execute("select fixture_profile_id, pdf_sha256 from trace_ingest.approved_fixture_resumes").fetchall())
    expected = {fid: hashlib.sha256(extractor.resume_bytes(fid)).hexdigest() for fid in extractor.fixture_ids}
    assert len(expected) == 41 and approved == expected


def test_only_the_exact_approved_fixture_pdf_pair_is_accepted(env, extractor, consent_versions):
    svc, admin, conn, _ = env
    # The profile is SYN-012, but the resume is SYN-013's approved PDF: a real fixture hash, wrong pair.
    ctx, resume = provision(env, extractor, "SYN-012", consent_versions, document=extractor.resume_bytes("SYN-013"))
    for result in (extractor.extract(extractor.resume_bytes("SYN-012")), extractor.extract(extractor.resume_bytes("SYN-013"))):
        with pytest.raises(IngestionNotAllowed):
            svc.ingest_extraction(ctx, resume, result)
    assert _runs(admin, ctx.candidate_profile_id) == [] and record_count(admin, ctx.candidate_profile_id) == 0
    # An arbitrary document, designated as a fixture by the table owner, is still not an approved PDF.
    arbitrary = b"%PDF-1.4 not one of the synthetic fixtures"
    ctx, resume = provision(env, extractor, "SYN-012", consent_versions, document=arbitrary)
    forged = extractor.extract(extractor.resume_bytes("SYN-012")).model_copy(update={"source_sha256": hashlib.sha256(arbitrary).hexdigest()})
    with pytest.raises(IngestionNotAllowed):
        svc.ingest_extraction(ctx, resume, forged)
    assert _runs(admin, ctx.candidate_profile_id) == [] and record_count(admin, ctx.candidate_profile_id) == 0
    # The exact pair is accepted.
    ctx, resume = provision(env, extractor, "SYN-012", consent_versions)
    assert svc.ingest_extraction(ctx, resume, extractor.extract(extractor.resume_bytes("SYN-012"))).status == "succeeded"


# --------------------------------------------------------------------------- idempotency and conflicts
def test_reingestion_is_idempotent_and_never_merges(env, extractor, consent_versions):
    svc, admin, conn, _ = env
    ctx, resume = provision(env, extractor, "SYN-007", consent_versions)
    result = extractor.extract(extractor.resume_bytes("SYN-007"))
    first = svc.ingest_extraction(ctx, resume, result)
    n = record_count(admin, ctx.candidate_profile_id)
    second = svc.ingest_extraction(ctx, resume, extractor.extract(extractor.resume_bytes("SYN-007")))
    assert second == first and record_count(admin, ctx.candidate_profile_id) == n
    changed = result.model_copy(update={"skills": result.skills[:-1]})
    with pytest.raises(IngestionConflict):
        svc.ingest_extraction(ctx, resume, changed)
    assert record_count(admin, ctx.candidate_profile_id) == n
    assert [s for s, _ in _runs(admin, ctx.candidate_profile_id)] == ["succeeded"]

    # A profile that already holds records is never merged into.
    ctx, resume = provision(env, extractor, "SYN-007", consent_versions)
    svc.add_employment(ctx, EmploymentCreate(sequence_index=1, title_raw="Manually entered"))
    with pytest.raises(IngestionConflict):
        svc.ingest_extraction(ctx, resume, result)
    assert record_count(admin, ctx.candidate_profile_id) == 1
    assert _runs(admin, ctx.candidate_profile_id) == [("failed", "ingestion_conflict")]


# --------------------------------------------------------------------------- atomicity
def test_a_failed_ingestion_leaves_nothing_behind_and_can_be_retried(env, extractor, consent_versions):
    svc, admin, conn, _ = env
    ctx, resume = provision(env, extractor, "SYN-008", consent_versions)
    good = extractor.extract(extractor.resume_bytes("SYN-008"))
    # The last evidence row violates a CHECK (text shorter than 10 characters), after employers,
    # employments, education and earlier evidence were inserted in the same statement.
    broken = good.model_copy(update={"evidence": list(good.evidence[:-1]) + [good.evidence[-1].model_copy(update={"evidence_text": "Too short"})]})
    with pytest.raises(InvalidRecord):
        svc.ingest_extraction(ctx, resume, broken)
    assert record_count(admin, ctx.candidate_profile_id) == 0
    assert _runs(admin, ctx.candidate_profile_id) == [("failed", "invalid_record")]
    assert admin.execute("select p.extraction_status, r.parse_status from public.trace_candidate_profiles p join "
                         "public.trace_resume_documents r on r.candidate_profile_id = p.candidate_profile_id "
                         "where p.candidate_profile_id = %s", (ctx.candidate_profile_id,)).fetchone() == ("failed", "failed")
    retry = svc.ingest_extraction(ctx, resume, good)                 # a new run; the failed one stays failed
    assert retry.status == "succeeded"
    assert [s for s, _ in _runs(admin, ctx.candidate_profile_id)] == ["failed", "succeeded"]
    assert record_count(admin, ctx.candidate_profile_id) > 0


# --------------------------------------------------------------------------- owner_session
def test_every_ingestion_statement_runs_as_the_owner(env, extractor, consent_versions, monkeypatch):
    svc, admin, conn, _ = env
    ctx, resume = provision(env, extractor, "SYN-009", consent_versions)
    seen = []
    original = psycopg.Cursor.execute

    def audited(self, query, params=None, **kw):
        text = query if isinstance(query, str) else str(query)
        if self.connection is conn and "trace_ingest." in text:
            seen.append(original(self.connection.cursor(), "select current_user::text, auth.uid()").fetchone())
        return original(self, query, params, **kw)

    monkeypatch.setattr(psycopg.Cursor, "execute", audited)
    svc.ingest_extraction(ctx, resume, extractor.extract(extractor.resume_bytes("SYN-009")))
    monkeypatch.undo()
    assert seen and set(seen) == {("trace_app_user", ctx.owner_user_id)}
