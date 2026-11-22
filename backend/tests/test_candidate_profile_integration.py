"""Database tests for the candidate profile service (local Supabase, v2.2.0 schema).

Each test creates fresh transient synthetic users (example.invalid) and deletes them afterwards,
which cascades to all their candidate records. A separate admin connection plays the role of a
trusted backend process only where a test needs one: simulating pre-existing extracted or legacy
records, or a trusted role-mapping process (candidates can no longer write mapping fields).
"""
import threading
import time
import uuid

import psycopg
import pytest
from pydantic import ValidationError

from app import db
from app.config import get_settings
from app.services.candidate_profile import (
    ActivityCreate, ActivityUpdate, CandidateProfileService, CareerBreakCreate, CareerBreakUpdate,
    CorrectionNotAvailable, DateInput, EducationCreate, EducationUpdate, EmployerCreate, EmployerUpdate,
    EmploymentCreate, EmploymentUpdate, EvidenceCreate, EvidenceUpdate, InvalidRecord, InvalidReference,
    InvalidTransition, OwnerContext, PermissionDenied, ProfileCreate, ProfileNotFound, ProfileUpdate,
    RecordNotEditable, RecordNotFound, SkillCreate, SkillUpdate, load_sequencing_context, owner_session,
)
from app.services.candidate_profile.repository import CandidateProfileRepository

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings():
    s = get_settings()
    if s.database_url is None:
        pytest.skip("DATABASE_URL not configured")
    try:
        with db.connect(s) as c:
            if c.execute("select to_regclass('public.trace_candidate_career_activities')").fetchone()[0] is None:
                pytest.skip("v2.2.0 candidate schema not applied")
    except psycopg.OperationalError as exc:
        pytest.skip(f"local database unreachable: {type(exc).__name__}")
    return s


@pytest.fixture
def env(settings):
    admin = db.connect(settings)
    admin.autocommit = True
    users = [uuid.uuid4(), uuid.uuid4()]
    conn = db.connect(settings)
    try:
        for u in users:
            admin.execute("insert into auth.users (id, email) values (%s, %s)", (u, f"cp-test-{u}@example.invalid"))
        svc = CandidateProfileService(conn)
        ctxs = [OwnerContext(owner_user_id=u, candidate_profile_id=svc.create_profile(u, ProfileCreate(headline=f"Synthetic {i}")))
                for i, u in enumerate(users)]
        yield svc, ctxs[0], ctxs[1], admin, conn
    finally:
        conn.close()
        # Deleting the users cascades to every candidate record they own.
        admin.execute("delete from auth.users where id = any(%s)", (users,))
        admin.close()


@pytest.fixture(scope="module")
def context(settings):
    with db.connect(settings) as c:
        return load_sequencing_context(c)


def month(y, m):
    return DateInput(year=y, month=m, precision="month")


def year(y):
    return DateInput(year=y, precision="year")


ONGOING = DateInput(precision="ongoing")


def system_map(admin, employment_id, role, status="confirmed", confidence=1):
    """Test-only stand-in for a trusted role-mapping process (admin connection). Candidates cannot
    write role mapping fields through the service."""
    admin.execute("update public.trace_candidate_employments set role_id = %s, mapping_status = %s, "
                  "mapping_confidence = %s where candidate_employment_id = %s", (role, status, confidence, employment_id))


def employment(svc, ctx, seq, title, start, end, *, role=None, admin=None, etype="full_time", employer=None,
               primary=True, **kw):
    """Add a candidate-authored employment (pending_review) and, if `role` is given, map it through
    the trusted stand-in."""
    e = svc.add_employment(ctx, EmploymentCreate(
        sequence_index=seq, title_raw=title, employment_type=etype, start=start, end=end,
        candidate_employer_id=employer, primary_career_sequence=primary, **kw))
    if role is not None:
        system_map(admin, e, role)
    return e


def review_of(svc, ctx, kind, record_id):
    p = svc.get_profile(ctx)
    records = {
        "employment": [(r.candidate_employment_id, r) for r in p.employment_history],
        "education": [(r.candidate_education_id, r) for r in p.education],
        "activity": [(r.candidate_activity_id, r) for v in p.career_activities.values() for r in v],
        "evidence": [(r.candidate_evidence_id, r) for r in p.unattached_evidence]
                    + [(x.candidate_evidence_id, x) for e in p.employment_history
                       for x in e.responsibilities + e.achievements + e.other_evidence],
        "skill": [(r.candidate_skill_id, r) for r in p.skills],
        "career_break": [(r.candidate_break_id, r) for r in p.career_breaks],
    }[kind]
    return dict(records)[record_id]


# --------------------------------------------------------------------------- retrieval
def test_empty_profile_returns_every_category(env):
    svc, a, *_ = env
    p = svc.get_profile(a)
    assert p.headline == "Synthetic 0"
    assert (p.employment_history, p.education, p.skills, p.career_breaks, p.unattached_evidence) == ([], [], [], [], [])
    assert len(p.career_activities) == 12 and all(v == [] for v in p.career_activities.values())


def test_complete_profile_is_structured_by_category(env):
    svc, a, _, admin, _ = env
    er = svc.add_employer(a, EmployerCreate(employer_name="Synthetic Fintech", industry_sector="fintech"))
    e1 = employment(svc, a, 1, "Associate Product Manager", month(2019, 7), month(2021, 6), role="PM-APM", admin=admin, employer=er)
    e2 = employment(svc, a, 2, "Product Manager II", month(2021, 7), ONGOING, role="PM-PM", admin=admin, employer=er)
    svc.add_evidence(a, EvidenceCreate(evidence_type="responsibility", evidence_text="Owned the payments roadmap",
                                       responsibility_area="roadmap_ownership", candidate_employment_id=e2))
    ach = svc.add_evidence(a, EvidenceCreate(evidence_type="achievement", evidence_text="Cut checkout drop-off by 12%",
                                             candidate_employment_id=e2))
    edu = svc.add_education(a, EducationCreate(qualification_title_raw="B.Tech (CSE)", qualification_level="bachelor",
                                               institution_name="Synthetic Institute", field_of_study="Computer Science",
                                               completion_status="completed", start=year(2015), end=year(2019)))
    cert = svc.add_activity(a, ActivityCreate(activity_type="certification", title_raw="Certified Scrum Product Owner",
                                              organisation_raw="Synthetic Scrum Body", status="completed",
                                              credential_identifier_raw="SYN-001", start=month(2022, 2), expiry=month(2024, 2)))
    svc.add_activity(a, ActivityCreate(activity_type="volunteering", title_raw="Mentor", status="active",
                                       start=year(2020), end=ONGOING))
    svc.add_career_break(a, CareerBreakCreate(break_reason="education", note="Full-time study", start=month(2017, 1), end=month(2017, 6)))
    svc.add_skill(a, SkillCreate(skill_text_raw="Roadmapping", candidate_employment_id=e2))
    svc.add_skill(a, SkillCreate(skill_text_raw="Algorithms", candidate_education_id=edu))
    svc.add_skill(a, SkillCreate(skill_text_raw="Scrum", candidate_activity_id=cert))
    svc.add_skill(a, SkillCreate(skill_text_raw="Funnel analysis", candidate_evidence_id=ach))
    svc.add_evidence(a, EvidenceCreate(evidence_type="other", evidence_text="Speaks at synthetic meetups"))

    p = svc.get_profile(a)
    assert [e.candidate_employment_id for e in p.employment_history] == [e1, e2]
    pm = p.employment_history[1]
    assert (pm.title_raw, pm.role_id, pm.normalised_title, pm.employer.employer_name) == (
        "Product Manager II", "PM-PM", "Product Manager", "Synthetic Fintech")
    assert [r.evidence_text for r in pm.responsibilities] == ["Owned the payments roadmap"]
    assert [r.candidate_evidence_id for r in pm.achievements] == [ach]
    assert [s.name for s in pm.skills] == ["Roadmapping"]
    assert p.education[0].qualification_title_raw == "B.Tech (CSE)" and [s.name for s in p.education[0].skills] == ["Algorithms"]
    assert p.career_activities["certification"][0].expiry.lo_label == "2024-02"
    assert [s.name for s in p.career_activities["certification"][0].skills] == ["Scrum"]
    assert p.career_activities["volunteering"][0].end.precision == "ongoing"
    assert p.career_breaks[0].note == "Full-time study"
    assert [e.evidence_text for e in p.unattached_evidence] == ["Speaks at synthetic meetups"]
    assert {(s.skill_text_raw, s.supported_by) for s in p.skills} == {
        ("Roadmapping", "employment"), ("Algorithms", "education"), ("Scrum", "activity"), ("Funnel analysis", "evidence")}
    assert pm.achievements[0].skills[0].name == "Funnel analysis"   # skill traced to the achievement


# --------------------------------------------------------------------------- create and edit
def test_editing_a_pending_draft_keeps_its_title_mapping_and_status(env):
    svc, a, _, admin, _ = env
    e = employment(svc, a, 1, "Sr. PM (Payments)", year(2020), ONGOING, role="PM-SPM", admin=admin)
    svc.update_employment(a, e, EmploymentUpdate(end=month(2023, 3), employment_type="contract"))
    pm = svc.get_profile(a).employment_history[0]
    assert (pm.title_raw, pm.role_id, pm.normalised_title) == ("Sr. PM (Payments)", "PM-SPM", "Senior Product Manager")
    assert (pm.start.precision, pm.start.lo_label, pm.start.hi_label) == ("year", "2020-01", "2020-12")
    assert (pm.end.precision, pm.is_current_as_stated, pm.employment_type) == ("month", False, "contract")
    assert (pm.provenance.information_origin, pm.provenance.review_status) == ("user_stated", "pending_review")
    with pytest.raises(ValidationError):
        EmploymentUpdate(role_id="PM-GPM")                       # role mapping is not candidate input


def test_candidate_employment_is_unmapped_until_a_trusted_mapping(env):
    svc, a, *_ = env
    svc.add_employment(a, EmploymentCreate(sequence_index=1, title_raw="Senior Product Manager"))
    pm = svc.get_profile(a).employment_history[0]
    assert (pm.title_raw, pm.role_id, pm.mapping_status, pm.mapping_confidence) == (
        "Senior Product Manager", "ROLE-UNKNOWN", "unresolved", 0)
    with pytest.raises(ValidationError):
        EmploymentCreate(sequence_index=2, title_raw="PM", role_id="PM-PM", mapping_status="confirmed", mapping_confidence=1)
    with pytest.raises(ValidationError):
        ActivityCreate(activity_type="course", title_raw="C", category_code="analytics", category_mapping_status="confirmed")


def test_education_level_never_overwrites_the_stated_title(env):
    svc, a, *_ = env
    edu = svc.add_education(a, EducationCreate(qualification_title_raw="PGDM (Executive)"))
    svc.update_education(a, edu, EducationUpdate(qualification_level="mba", study_mode="executive"))
    e = svc.get_profile(a).education[0]
    assert (e.qualification_title_raw, e.qualification_level, e.study_mode) == ("PGDM (Executive)", "mba", "executive")
    assert (e.start.precision, e.start.lo, e.end.lo) == ("unknown", None, None)


def test_every_activity_type_is_stored_and_updatable(env):
    svc, a, *_ = env
    ids = {t: svc.add_activity(a, ActivityCreate(activity_type=t, title_raw=f"Synthetic {t}"))
           for t in ("certification", "licence", "course", "independent_project", "portfolio_project", "award",
                     "publication", "research", "volunteering", "extracurricular", "leadership")}
    svc.update_activity(a, ids["independent_project"], ActivityUpdate(
        description_raw="A budgeting app", contribution_raw="Sole developer", outcome_raw="200 synthetic users",
        link_url="https://example.invalid/app"))
    p = svc.get_profile(a)
    assert all(len(p.career_activities[t]) == 1 for t in ids)
    proj = p.career_activities["independent_project"][0]
    assert (proj.title_raw, proj.outcome_raw, proj.link_url) == ("Synthetic independent_project", "200 synthetic users",
                                                                 "https://example.invalid/app")
    with pytest.raises(InvalidRecord):
        svc.add_activity(a, ActivityCreate(activity_type="independent_project", title_raw="X", credential_identifier_raw="ID-1"))
    with pytest.raises(InvalidRecord):
        svc.add_activity(a, ActivityCreate(activity_type="course", title_raw="C", start=month(2022, 6), end=month(2021, 1)))


def test_achievements_attach_to_one_record_without_duplication(env):
    svc, a, *_ = env
    e = employment(svc, a, 1, "PM", month(2020, 1), ONGOING)
    proj = svc.add_activity(a, ActivityCreate(activity_type="independent_project", title_raw="Side app", related_employment_id=e))
    svc.add_evidence(a, EvidenceCreate(evidence_type="achievement", evidence_text="Launched pricing experiment", candidate_employment_id=e))
    svc.add_evidence(a, EvidenceCreate(evidence_type="achievement", evidence_text="Reached 200 synthetic users", candidate_activity_id=proj))
    p = svc.get_profile(a)
    assert [x.evidence_text for x in p.employment_history[0].achievements] == ["Launched pricing experiment"]
    assert [x.evidence_text for x in p.career_activities["independent_project"][0].achievements] == ["Reached 200 synthetic users"]
    assert p.career_activities["independent_project"][0].related_employment_id == e
    assert len(p.employment_history) == 1      # the project did not become an employment episode


def test_custom_skill_outside_taxonomy_and_mapped_skill(env, settings):
    svc, a, *_ = env
    with db.connect(settings) as c:
        skill_id, canonical = c.execute("select skill_id, canonical_skill from public.trace_skills order by skill_id limit 1").fetchone()
    svc.add_skill(a, SkillCreate(skill_text_raw="Mixpanel", stated_proficiency_raw="daily use for 2 years"))
    svc.add_skill(a, SkillCreate(skill_id=skill_id, skill_text_raw="what I wrote"))
    by_raw = {s.skill_text_raw: s for s in svc.get_profile(a).skills}
    assert by_raw["Mixpanel"].taxonomy is None and by_raw["Mixpanel"].stated_proficiency_raw == "daily use for 2 years"
    assert by_raw["what I wrote"].taxonomy.canonical_skill == canonical


# --------------------------------------------------------------------------- review actions
def test_candidate_authored_records_get_honest_defaults_and_review_is_separate(env):
    svc, a, *_ = env
    act = svc.add_activity(a, ActivityCreate(activity_type="course", title_raw="Analytics"))
    prov = svc.get_profile(a).career_activities["course"][0].provenance
    assert (prov.information_origin, prov.review_status, prov.verification_status, prov.extraction_confidence) == (
        "user_stated", "pending_review", "unverified", None)
    assert svc.confirm_record(a, "activity", act) == "user_confirmed"
    prov = svc.get_profile(a).career_activities["course"][0].provenance
    assert (prov.information_origin, prov.review_status, prov.verification_status) == ("user_stated", "user_confirmed", "unverified")


# One factory per reviewable record type: the same rules apply to all six.
FACTORIES = {
    "employment": (lambda svc, a: svc.add_employment(a, EmploymentCreate(sequence_index=1, title_raw="PM")),
                   lambda svc, a, i: svc.update_employment(a, i, EmploymentUpdate(title_raw="Product Manager"))),
    "education": (lambda svc, a: svc.add_education(a, EducationCreate(qualification_title_raw="MBA")),
                  lambda svc, a, i: svc.update_education(a, i, EducationUpdate(notes="edited"))),
    "activity": (lambda svc, a: svc.add_activity(a, ActivityCreate(activity_type="portfolio_project", title_raw="Site")),
                 lambda svc, a, i: svc.update_activity(a, i, ActivityUpdate(outcome_raw="edited"))),
    "evidence": (lambda svc, a: svc.add_evidence(a, EvidenceCreate(evidence_type="other", evidence_text="Synthetic claim text")),
                 lambda svc, a, i: svc.update_evidence(a, i, EvidenceUpdate(evidence_text="Edited synthetic claim"))),
    "skill": (lambda svc, a: svc.add_skill(a, SkillCreate(skill_text_raw="SQL")),
              lambda svc, a, i: svc.update_skill(a, i, SkillUpdate(stated_proficiency_raw="advanced"))),
    "career_break": (lambda svc, a: svc.add_career_break(a, CareerBreakCreate(break_reason="sabbatical")),
                     lambda svc, a, i: svc.update_career_break(a, i, CareerBreakUpdate(note="edited"))),
}


@pytest.mark.parametrize("kind", list(FACTORIES))
def test_review_actions_follow_the_transition_matrix(env, kind):
    svc, a, *_ = env
    create, edit = FACTORIES[kind]
    rid = create(svc, a)
    status = lambda: review_of(svc, a, kind, rid).provenance.review_status   # noqa: E731

    with pytest.raises(InvalidTransition):
        svc.reinstate_record(a, kind, rid)                       # only rejected records can be reinstated
    edit(svc, a, rid)                                            # a pending draft may be edited ...
    assert status() == "pending_review"                          # ... and stays pending
    assert svc.confirm_record(a, kind, rid) == "user_confirmed"
    assert svc.confirm_record(a, kind, rid) == "user_confirmed"  # idempotent
    with pytest.raises(CorrectionNotAvailable):
        edit(svc, a, rid)                                        # no correction without history
    assert svc.reject_record(a, kind, rid) == "rejected"         # a confirmed record can still be rejected
    with pytest.raises(InvalidTransition):
        svc.confirm_record(a, kind, rid)                         # never rejected -> confirmed
    with pytest.raises(RecordNotEditable):
        edit(svc, a, rid)                                        # rejected records are immutable
    assert status() == "rejected"
    assert svc.reinstate_record(a, kind, rid) == "pending_review"
    edit(svc, a, rid)                                            # reinstated: editable again, still pending
    assert status() == "pending_review"
    prov = review_of(svc, a, kind, rid).provenance
    assert (prov.information_origin, prov.verification_status) == ("user_stated", "unverified")


def test_review_actions_refuse_unknown_kinds_and_employers(env):
    svc, a, *_ = env
    er = svc.add_employer(a, EmployerCreate(employer_name="Synthetic Co"))
    with pytest.raises(InvalidRecord):
        svc.confirm_record(a, "employer", er)                    # employers have no review status
    with pytest.raises(InvalidRecord):
        svc.confirm_record(a, "profile", a.candidate_profile_id)
    with pytest.raises(RecordNotFound):
        svc.confirm_record(a, "activity", uuid.uuid4())
    assert not hasattr(svc, "set_review_status")                 # no free-form status setter


def test_pending_extracted_record_cannot_be_overwritten_without_history(env):
    svc, a, _, admin, _ = env
    act = svc.add_activity(a, ActivityCreate(activity_type="certification", title_raw="CSPO (extracted)"))
    # Simulate a trusted backend extraction (test-only, admin connection).
    admin.execute("""update public.trace_candidate_career_activities set information_origin = 'source_extracted',
                     extraction_confidence = 0.8, verification_status = 'source_document_supported',
                     source_location = 'page 1' where candidate_activity_id = %s""", (act,))
    with pytest.raises(CorrectionNotAvailable):
        svc.update_activity(a, act, ActivityUpdate(title_raw="Certified Scrum Product Owner"))
    rec = svc.get_profile(a).career_activities["certification"][0]
    assert rec.title_raw == "CSPO (extracted)"                   # the original is untouched
    # Confirming changes the review status only; every trusted field is kept.
    assert svc.confirm_record(a, "activity", act) == "user_confirmed"
    prov = svc.get_profile(a).career_activities["certification"][0].provenance
    assert (prov.information_origin, prov.review_status, prov.verification_status, str(prov.extraction_confidence),
            prov.source_location) == ("source_extracted", "user_confirmed", "source_document_supported", "0.800", "page 1")


def test_legacy_unknown_origin_is_never_upgraded(env):
    svc, a, _, admin, _ = env
    edu = svc.add_education(a, EducationCreate(qualification_title_raw="Diploma"))
    admin.execute("update public.trace_candidate_education set information_origin = null where candidate_education_id = %s", (edu,))
    with pytest.raises(CorrectionNotAvailable):
        svc.update_education(a, edu, EducationUpdate(notes="added later"))
    assert svc.confirm_record(a, "education", edu) == "user_confirmed"
    prov = svc.get_profile(a).education[0].provenance
    assert (prov.information_origin, prov.review_status) == (None, "user_confirmed")


def test_existing_user_corrected_rows_are_preserved(env):
    svc, a, _, admin, _ = env
    # A pre-existing legacy row (test-only, inserted by the table owner in its final state: since M2 no
    # role can move a record to user_corrected, and the owner cannot change review status either).
    act = admin.execute("""insert into public.trace_candidate_career_activities (owner_user_id, candidate_profile_id,
                           activity_type, title_raw, information_origin, review_status)
                           values (%s, %s, 'award', 'Synthetic award', 'source_extracted', 'user_corrected')
                           returning candidate_activity_id""", (a.owner_user_id, a.candidate_profile_id)).fetchone()[0]
    assert svc.confirm_record(a, "activity", act) == "user_corrected"     # no-op, never downgraded
    with pytest.raises(CorrectionNotAvailable):
        svc.update_activity(a, act, ActivityUpdate(title_raw="changed"))
    assert svc.reject_record(a, "activity", act) == "rejected"
    assert svc.reinstate_record(a, "activity", act) == "pending_review"


def test_employer_edits_only_while_unreviewed(env):
    svc, a, _, admin, _ = env
    er = svc.add_employer(a, EmployerCreate(employer_name="Synthetic Co"))
    svc.update_employer(a, er, EmployerUpdate(industry_sector="fintech"))              # no employment yet
    e = employment(svc, a, 1, "PM", month(2020, 1), ONGOING, employer=er)
    svc.update_employer(a, er, EmployerUpdate(company_size_band="51-200"))            # shown only on pending employment
    svc.confirm_record(a, "employment", e)
    with pytest.raises(CorrectionNotAvailable):
        svc.update_employer(a, er, EmployerUpdate(employer_name="Renamed"))           # shown on confirmed employment
    other = svc.add_employer(a, EmployerCreate(employer_name="Extracted Co"))
    admin.execute("update public.trace_candidate_employers set attribute_provenance = 'resume_stated' "
                  "where candidate_employer_id = %s", (other,))
    with pytest.raises(CorrectionNotAvailable):
        svc.update_employer(a, other, EmployerUpdate(employer_name="Renamed"))        # not candidate-entered
    emp = svc.get_profile(a).employment_history[0].employer
    assert (emp.employer_name, emp.industry_sector, emp.company_size_band) == ("Synthetic Co", "fintech", "51-200")


# --------------------------------------------------------------------------- trusted fields
def test_self_assigned_verification_and_provenance_are_rejected(env):
    svc, a, _, _, conn = env
    with pytest.raises(ValidationError):
        ActivityCreate(activity_type="course", title_raw="X", verification_status="independently_verified")
    with pytest.raises(ValidationError):
        SkillCreate(skill_text_raw="SQL", extraction_confidence=0.9)
    act = svc.add_activity(a, ActivityCreate(activity_type="course", title_raw="X"))
    # Even bypassing the service models, the owner session cannot write trusted columns.
    for sql in ("update public.trace_candidate_career_activities set verification_status = 'independently_verified' where candidate_activity_id = %s",
                "update public.trace_candidate_career_activities set information_origin = 'source_extracted' where candidate_activity_id = %s",
                "update public.trace_candidate_career_activities set extraction_run_id = gen_random_uuid() where candidate_activity_id = %s",
                "update public.trace_candidate_career_activities set source_resume_id = gen_random_uuid() where candidate_activity_id = %s"):
        with pytest.raises(PermissionDenied):
            with owner_session(conn, a.owner_user_id):
                conn.execute(sql, (act,))
    svc.confirm_record(a, "activity", act)
    svc.reject_record(a, "activity", act)
    assert svc.get_profile(a).career_activities["course"][0].provenance.verification_status == "unverified"


def test_profile_updates_cannot_set_confirmation(env):
    svc, a, *_ = env
    for field, value in (("confirmation_status", "participant_confirmed"), ("confirmed_at", "2026-11-11T00:00:00Z"),
                         ("raw_extraction_json", {}), ("extraction_status", "extracted")):
        with pytest.raises(ValidationError):
            ProfileUpdate(**{field: value})
    svc.update_profile(a, ProfileUpdate(headline="New headline"))
    p = svc.get_profile(a)
    assert (p.headline, p.confirmation_status) == ("New headline", "pending")


# --------------------------------------------------------------------------- ownership
def test_cross_owner_reads_and_writes_are_refused(env):
    svc, a, b, *_ = env
    act = svc.add_activity(a, ActivityCreate(activity_type="award", title_raw="Original title"))
    emp = employment(svc, a, 1, "PM", month(2020, 1), ONGOING)
    ev = svc.add_evidence(a, EvidenceCreate(evidence_type="achievement", evidence_text="Synthetic achievement", candidate_employment_id=emp))
    spoof = OwnerContext(owner_user_id=b.owner_user_id, candidate_profile_id=a.candidate_profile_id)
    with pytest.raises(ProfileNotFound):
        svc.get_profile(spoof)                                   # B cannot read A's profile
    with pytest.raises(ProfileNotFound):
        svc.update_activity(spoof, act, ActivityUpdate(title_raw="hijacked"))
    with pytest.raises(RecordNotFound):
        svc.update_activity(b, act, ActivityUpdate(title_raw="hijacked"))   # A's record is not in B's profile
    for action in (svc.confirm_record, svc.reject_record, svc.reinstate_record):
        with pytest.raises(ProfileNotFound):
            action(spoof, "activity", act)
        with pytest.raises(RecordNotFound):
            action(b, "activity", act)
    with pytest.raises(InvalidReference):
        svc.add_skill(b, SkillCreate(skill_text_raw="x", candidate_activity_id=act))
    with pytest.raises(InvalidReference):
        svc.add_skill(b, SkillCreate(skill_text_raw="x", candidate_evidence_id=ev))
    with pytest.raises(InvalidReference):
        svc.add_evidence(b, EvidenceCreate(evidence_type="achievement", evidence_text="Attached elsewhere", candidate_employment_id=emp))
    with pytest.raises(InvalidReference):
        svc.add_activity(b, ActivityCreate(activity_type="independent_project", title_raw="P", related_employment_id=emp))
    with pytest.raises(ProfileNotFound):
        svc.add_activity(spoof, ActivityCreate(activity_type="course", title_raw="Injected"))
    p = svc.get_profile(a)
    award = p.career_activities["award"][0]
    assert (award.title_raw, award.provenance.review_status) == ("Original title", "pending_review")
    assert len(p.skills) == 0 and len(p.career_activities["course"]) == 0
    assert svc.get_profile(b).skills == []


def test_duplicate_sequence_index_is_rejected_at_commit(env):
    svc, a, *_ = env
    employment(svc, a, 1, "PM", month(2020, 1), month(2021, 1))
    with pytest.raises(InvalidRecord):
        employment(svc, a, 1, "SPM", month(2021, 2), ONGOING)
    assert len(svc.get_profile(a).employment_history) == 1


# --------------------------------------------------------------------------- integration with career_history
def test_only_reviewed_employment_episodes_enter_sequencing(env, context):
    svc, a, _, admin, _ = env
    er_a = svc.add_employer(a, EmployerCreate(employer_name="Synthetic A"))
    er_b = svc.add_employer(a, EmployerCreate(employer_name="Synthetic B"))
    intern = employment(svc, a, 1, "Product Intern", month(2018, 5), month(2018, 7), role="PM-INTERN", admin=admin, etype="internship")
    pm_a = employment(svc, a, 2, "Product Manager", month(2019, 1), month(2021, 12), role="PM-PM", admin=admin, employer=er_a)
    side = employment(svc, a, 3, "Freelance consultant", month(2020, 1), month(2020, 6), role="ROLE-CONSULT", admin=admin,
                      etype="freelance", primary=False, counts_as_full_time_experience=False, classification_note="side work")
    pm_b = employment(svc, a, 4, "Product Manager", month(2022, 1), ONGOING, role="PM-PM", admin=admin, employer=er_b)
    supporting = [svc.add_education(a, EducationCreate(qualification_title_raw="MBA", start=month(2016, 6), end=month(2018, 4))),
                  svc.add_activity(a, ActivityCreate(activity_type="certification", title_raw="CSPO", start=month(2021, 3))),
                  svc.add_activity(a, ActivityCreate(activity_type="independent_project", title_raw="App", start=month(2020, 2))),
                  svc.add_activity(a, ActivityCreate(activity_type="volunteering", title_raw="Mentor", start=month(2019, 3)))]

    # While the candidate has not reviewed them, nothing is confirmed and no pathway is observed.
    h = svc.sequence_employment_history(a, context)
    assert all("review_pending" in r.exclusion_reasons for r in h.records)
    assert not any(t.transition_status == "confirmed" or t.is_pathway_move for t in h.transitions)
    assert h.segments == []                                      # no record is eligible for a pathway yet

    for e in (intern, pm_a, side, pm_b):
        svc.confirm_record(a, "employment", e)
    h = svc.sequence_employment_history(a, context)
    assert [r.stated.employment_id for r in h.records] == [str(intern), str(pm_a), str(side), str(pm_b)]
    step_ids = {i for seg in h.segments for st in seg.stints for i in st.employment_ids}
    assert step_ids and not step_ids & {str(x) for x in supporting}
    assert [(t.change_type, t.transition_status) for t in h.transitions] == [
        ("role_change_employer_unknown", "unconfirmed"), ("employer_change", "confirmed")]
    assert h.segments[-1].role_path == ["PM-PM", "PM-PM"]       # episode-aware: same role, new employer
    assert "Synthetic" not in h.model_dump_json()                # employer identifiers are not serialised


def test_pending_employment_is_a_barrier_and_rejected_employment_is_removed(env, context):
    svc, a, _, admin, _ = env
    ers = [svc.add_employer(a, EmployerCreate(employer_name=f"Synthetic {c}")) for c in "ABC"]
    ea = employment(svc, a, 1, "Product Analyst", month(2018, 1), month(2019, 12), role="PM-APM", admin=admin, employer=ers[0])
    eb = employment(svc, a, 2, "Product Manager", month(2020, 1), month(2021, 12), role="PM-PM", admin=admin, employer=ers[1])
    ec = employment(svc, a, 3, "Senior Product Manager", month(2022, 1), month(2023, 12), role="PM-SPM", admin=admin, employer=ers[2])
    svc.confirm_record(a, "employment", ea)
    svc.confirm_record(a, "employment", ec)

    def pairs(h):
        return [(t.from_employment_id, t.to_employment_id, t.transition_status) for t in h.transitions]

    # Company A (confirmed) -> Company B (pending) -> Company C (confirmed): never A -> C.
    h = svc.sequence_employment_history(a, context)
    assert pairs(h) == [(str(ea), str(eb), "unconfirmed"), (str(eb), str(ec), "unconfirmed")]
    assert [s.role_path for s in h.segments] == [["PM-APM"], ["PM-SPM"]]

    # Rejected: removed, so A and C become adjacent under the usual rules.
    svc.reject_record(a, "employment", eb)
    h = svc.sequence_employment_history(a, context)
    assert [r.stated.employment_id for r in h.records] == [str(ea), str(ec)]
    assert pairs(h) == [(str(ea), str(ec), "confirmed")]
    assert h.transitions[0].change_type == "role_and_employer_change"

    # Reinstated: back to pending, and the barrier returns.
    svc.reinstate_record(a, "employment", eb)
    h = svc.sequence_employment_history(a, context)
    assert pairs(h) == [(str(ea), str(eb), "unconfirmed"), (str(eb), str(ec), "unconfirmed")]


# --------------------------------------------------------------------------- concurrency: employer edit vs review
# Both sides are real service calls on separate connections in separate threads. One side is paused
# inside its open transaction, right after its locks and its write, by wrapping one repository method
# for that thread only. The test then proves the other side is blocked on a row lock (pg_stat_activity)
# before releasing the first. Without the employment-row locks in lock_employer_state the second side
# would not block, and these tests fail (checked by mutation).
PAUSED = "paused-transaction"


def pause_inside(monkeypatch, method, when=lambda *a, **k: True, expected=1):
    """Pause every thread whose name starts with PAUSED just after `method` runs, before its
    transaction commits. `reached` is set once `expected` threads are paused."""
    reached, release = threading.Event(), threading.Event()
    original = getattr(CandidateProfileRepository, method)
    arrived, guard = [], threading.Lock()

    def wrapper(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if threading.current_thread().name.startswith(PAUSED) and when(*args, **kwargs):
            with guard:
                arrived.append(threading.current_thread().name)
                if len(arrived) >= expected:
                    reached.set()
            assert release.wait(15), "test did not release the paused transaction"
        return result

    monkeypatch.setattr(CandidateProfileRepository, method, wrapper)
    return reached, release


def in_thread(fn, name=None):
    out = {}

    def run():
        try:
            out["result"] = fn()
        except Exception as exc:                                  # surfaced to the test below
            out["error"] = exc

    t = threading.Thread(target=run, name=name or "contender", daemon=True)
    t.start()
    return t, out


def blocked_on_lock(admin, pid, thread, timeout=10.0):
    """True once backend `pid` is waiting on a lock; False if the thread finished without waiting."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = admin.execute("select wait_event_type from pg_stat_activity where pid = %s", (pid,)).fetchone()
        if row and row[0] == "Lock":
            return True
        if not thread.is_alive():
            return False
        time.sleep(0.02)
    return False


@pytest.fixture
def two_services(env, settings):
    svc, a, _, admin, _ = env
    c1, c2 = db.connect(settings), db.connect(settings)
    try:
        yield CandidateProfileService(c1), c1, CandidateProfileService(c2), c2, svc, a, admin
    finally:
        c1.close()
        c2.close()


def _job_at_employer(svc, a, name="Synthetic Employer"):
    er = svc.add_employer(a, EmployerCreate(employer_name=name))
    e = employment(svc, a, 1, "PM", month(2020, 1), ONGOING, employer=er)
    return er, e


def _employer_name(svc, a):
    return svc.get_profile(a).employment_history[0].employer.employer_name


@pytest.mark.parametrize("action,expected", [("confirm_record", "user_confirmed"), ("reject_record", "rejected")])
def test_review_in_progress_blocks_employer_edit_which_then_sees_the_new_status(two_services, monkeypatch, action, expected):
    s1, c1, s2, c2, svc, a, admin = two_services
    er, e = _job_at_employer(svc, a)
    reached, release = pause_inside(monkeypatch, "review_record")
    reviewer, r_out = in_thread(lambda: getattr(s1, action)(a, "employment", e), name=PAUSED)
    assert reached.wait(10)                                      # review written, not committed
    editor, e_out = in_thread(lambda: s2.update_employer(a, er, EmployerUpdate(employer_name="Renamed")))
    try:
        assert blocked_on_lock(admin, c2.info.backend_pid, editor), "employer edit did not wait for the employment lock"
    finally:
        release.set()
    reviewer.join(10), editor.join(10)
    assert r_out == {"result": expected}
    assert isinstance(e_out.get("error"), CorrectionNotAvailable)   # rechecked under the lock
    assert _employer_name(svc, a) == "Synthetic Employer"


def test_employer_edit_in_progress_blocks_confirmation_until_it_commits(two_services, monkeypatch):
    s1, c1, s2, c2, svc, a, admin = two_services
    er, e = _job_at_employer(svc, a)
    reached, release = pause_inside(monkeypatch, "update_record", when=lambda kind, *rest: kind == "employer")
    editor, e_out = in_thread(lambda: s1.update_employer(a, er, EmployerUpdate(employer_name="Renamed")), name=PAUSED)
    assert reached.wait(10)                                      # employer written, not committed
    reviewer, r_out = in_thread(lambda: s2.confirm_record(a, "employment", e))
    try:
        assert blocked_on_lock(admin, c2.info.backend_pid, reviewer), "confirmation did not wait for the employer edit"
    finally:
        release.set()
    editor.join(10), reviewer.join(10)
    assert e_out == {"result": None} and r_out == {"result": "user_confirmed"}
    # The edit was made while the job was pending; the job was confirmed afterwards, with the new name.
    p = svc.get_profile(a)
    assert (p.employment_history[0].employer.employer_name, p.employment_history[0].provenance.review_status) == (
        "Renamed", "user_confirmed")


def test_reinstatement_in_progress_is_seen_by_the_employer_edit_after_the_lock(two_services, monkeypatch):
    s1, c1, s2, c2, svc, a, admin = two_services
    er, e = _job_at_employer(svc, a)
    svc.reject_record(a, "employment", e)
    reached, release = pause_inside(monkeypatch, "review_record")
    reviewer, r_out = in_thread(lambda: s1.reinstate_record(a, "employment", e), name=PAUSED)
    assert reached.wait(10)
    editor, e_out = in_thread(lambda: s2.update_employer(a, er, EmployerUpdate(employer_name="Renamed")))
    try:
        assert blocked_on_lock(admin, c2.info.backend_pid, editor)
    finally:
        release.set()
    reviewer.join(10), editor.join(10)
    # Before the lock the job looked rejected (edit refused); the check after the lock sees pending.
    assert r_out == {"result": "pending_review"} and e_out == {"result": None}
    assert _employer_name(svc, a) == "Renamed"


def test_pointing_a_job_at_an_employer_waits_for_an_employer_edit(two_services, monkeypatch):
    s1, c1, s2, c2, svc, a, admin = two_services
    er = svc.add_employer(a, EmployerCreate(employer_name="Synthetic Employer"))
    e = employment(svc, a, 1, "PM", month(2020, 1), ONGOING)            # not yet linked to the employer
    reached, release = pause_inside(monkeypatch, "update_record", when=lambda kind, *rest: kind == "employer")
    editor, e_out = in_thread(lambda: s1.update_employer(a, er, EmployerUpdate(employer_name="Renamed")), name=PAUSED)
    assert reached.wait(10)
    linker, l_out = in_thread(lambda: s2.update_employment(a, e, EmploymentUpdate(candidate_employer_id=er)))
    try:
        # The foreign-key check takes FOR KEY SHARE on the employer row: no job joins mid-edit.
        assert blocked_on_lock(admin, c2.info.backend_pid, linker)
    finally:
        release.set()
    editor.join(10), linker.join(10)
    assert e_out == {"result": None} and l_out == {"result": None}
    assert _employer_name(svc, a) == "Renamed"


def test_employer_edit_rules_with_several_referencing_jobs(env):
    svc, a, *_ = env
    er = svc.add_employer(a, EmployerCreate(employer_name="Synthetic Co"))
    jobs = [employment(svc, a, i, f"Role {i}", month(2018 + i, 1), month(2018 + i, 12), employer=er) for i in (1, 2, 3)]
    svc.update_employer(a, er, EmployerUpdate(employer_name="All pending"))           # every reference pending
    for action in (svc.confirm_record, svc.reject_record):
        action(a, "employment", jobs[1])
        with pytest.raises(CorrectionNotAvailable):
            svc.update_employer(a, er, EmployerUpdate(employer_name="One reviewed"))
        if action == svc.reject_record:
            svc.reinstate_record(a, "employment", jobs[1])
    svc.update_employer(a, er, EmployerUpdate(employer_name="Pending again"))
    assert {e.employer.employer_name for e in svc.get_profile(a).employment_history} == {"Pending again"}


# --------------------------------------------------------------------------- candidate-controlled fields
def test_full_time_override_cannot_make_pending_employment_eligible_or_bypass_mapping(env, context):
    svc, a, _, admin, _ = env
    founder = employment(svc, a, 1, "Co-founder", month(2019, 1), month(2021, 12), role="ROLE-ENT", admin=admin,
                         etype="founder", counts_as_full_time_experience=True, classification_note="full-time, self-reported")
    rec = svc.sequence_employment_history(a, context).records[0]
    assert rec.counts_as_full_time_experience and rec.full_time_basis == "stated_override"
    assert rec.exclusion_reasons == ["review_pending"] and not rec.is_transition_eligible
    svc.confirm_record(a, "employment", founder)
    assert svc.sequence_employment_history(a, context).records[0].is_transition_eligible   # only once confirmed
    # A confirmed override never bypasses the role-mapping rules.
    other = employment(svc, a, 2, "Founder", month(2022, 1), ONGOING, etype="founder",
                       counts_as_full_time_experience=True, classification_note="self-reported")
    system_map(admin, other, "ROLE-ENT", status="needs_review", confidence=0.6)
    svc.confirm_record(a, "employment", other)
    rec = svc.sequence_employment_history(a, context).records[1]
    assert {"mapping_not_confirmed", "low_mapping_confidence"} <= set(rec.exclusion_reasons)
    with pytest.raises(CorrectionNotAvailable):
        svc.update_employment(a, founder, EmploymentUpdate(counts_as_full_time_experience=False,
                                                           classification_note="changed after review"))


def test_parallel_flag_cannot_remove_a_pending_barrier(env, context):
    svc, a, _, admin, _ = env
    ers = [svc.add_employer(a, EmployerCreate(employer_name=f"Synthetic {c}")) for c in "ABC"]
    ea = employment(svc, a, 1, "Analyst", month(2018, 1), month(2019, 12), role="PM-APM", admin=admin, employer=ers[0])
    eb = employment(svc, a, 2, "PM", month(2020, 1), month(2021, 12), role="PM-PM", admin=admin, employer=ers[1])
    ec = employment(svc, a, 3, "SPM", month(2022, 1), month(2023, 12), role="PM-SPM", admin=admin, employer=ers[2])
    svc.confirm_record(a, "employment", ea)
    svc.confirm_record(a, "employment", ec)
    svc.update_employment(a, eb, EmploymentUpdate(primary_career_sequence=False))     # candidate declares it parallel
    h = svc.sequence_employment_history(a, context)
    b = h.records[1]
    assert (b.stated.primary_career_sequence, b.in_primary_sequence) == (False, True)  # still a barrier
    assert (str(ea), str(ec)) not in [(t.from_employment_id, t.to_employment_id) for t in h.transitions]
    svc.confirm_record(a, "employment", eb)                                             # reviewed: flag applies
    h = svc.sequence_employment_history(a, context)
    assert [(t.from_employment_id, t.to_employment_id, t.transition_status) for t in h.transitions] == [
        (str(ea), str(ec), "confirmed")]


def test_activity_status_is_a_candidate_record_attribute(env):
    svc, a, *_ = env
    cert = svc.add_activity(a, ActivityCreate(activity_type="certification", title_raw="Synthetic cert", status="expired"))
    course = svc.add_activity(a, ActivityCreate(activity_type="course", title_raw="Synthetic course", status="in_progress"))
    with pytest.raises(InvalidRecord):                             # 'expired' is for certifications and licences only
        svc.add_activity(a, ActivityCreate(activity_type="course", title_raw="X", status="expired"))
    with pytest.raises(InvalidRecord):                             # not a review or verification value
        svc.add_activity(a, ActivityCreate(activity_type="course", title_raw="Y", status="user_confirmed"))
    svc.update_activity(a, course, ActivityUpdate(status="completed"))
    rec = svc.get_profile(a).career_activities["course"][0]
    assert (rec.status, rec.provenance.review_status, rec.provenance.verification_status) == (
        "completed", "pending_review", "unverified")                # status says nothing about review or trust
    svc.confirm_record(a, "activity", cert)
    with pytest.raises(CorrectionNotAvailable):
        svc.update_activity(a, cert, ActivityUpdate(status="active"))


# --------------------------------------------------------------------------- concurrency: employer edit vs reassignment
@pytest.fixture
def connections(settings):
    opened = []

    def make():
        c = db.connect(settings)
        opened.append(c)
        return c
    yield make
    for c in opened:
        c.close()


def _employer_of(svc, a):
    return {e.candidate_employment_id: (e.employer.candidate_employer_id if e.employer else None)
            for e in svc.get_profile(a).employment_history}


def test_crossing_reassignments_and_employer_edits_do_not_deadlock(env, connections, monkeypatch):
    """Two employer edits (E1, E2) racing two crossing reassignments (X: E2 -> E1, Y: E1 -> E2). With
    an employment-first lock order this is a four-transaction deadlock cycle:
    edit E1 waits for Y, Y's reassignment waits for E2, edit E2 waits for X, X's reassignment waits for E1."""
    svc, a, _, admin, _ = env
    e1 = svc.add_employer(a, EmployerCreate(employer_name="Synthetic E1"))
    e2 = svc.add_employer(a, EmployerCreate(employer_name="Synthetic E2"))
    x = employment(svc, a, 1, "Role X", month(2018, 1), month(2019, 12), employer=e2)
    y = employment(svc, a, 2, "Role Y", month(2020, 1), month(2021, 12), employer=e1)
    svcs = {k: CandidateProfileService(connections()) for k in ("b1", "b2", "a1", "a2")}

    reached, release = pause_inside(monkeypatch, "lock_review_state", when=lambda kind, *r: kind == "employment", expected=2)
    b1, b1_out = in_thread(lambda: svcs["b1"].update_employment(a, x, EmploymentUpdate(candidate_employer_id=e1)), name=PAUSED + "-1")
    b2, b2_out = in_thread(lambda: svcs["b2"].update_employment(a, y, EmploymentUpdate(candidate_employer_id=e2)), name=PAUSED + "-2")
    assert reached.wait(10)                                       # both reassignments hold their employment row
    a1, a1_out = in_thread(lambda: svcs["a1"].update_employer(a, e1, EmployerUpdate(employer_name="Renamed E1")))
    a2, a2_out = in_thread(lambda: svcs["a2"].update_employer(a, e2, EmployerUpdate(employer_name="Renamed E2")))
    try:
        assert blocked_on_lock(admin, svcs["a1"]._conn.info.backend_pid, a1)
        assert blocked_on_lock(admin, svcs["a2"]._conn.info.backend_pid, a2)
    finally:
        release.set()
    for t in (b1, b2, a1, a2):
        t.join(20)
        assert not t.is_alive()

    # Every transaction succeeded: no deadlock victim, no lost update.
    assert (b1_out, b2_out, a1_out, a2_out) == ({"result": None},) * 4
    assert _employer_of(svc, a) == {x: e1, y: e2}
    names = {e.employer.candidate_employer_id: e.employer.employer_name for e in svc.get_profile(a).employment_history}
    assert names == {e1: "Renamed E1", e2: "Renamed E2"}
    assert {e.provenance.review_status for e in svc.get_profile(a).employment_history} == {"pending_review"}


@pytest.mark.parametrize("direction", ["onto_edited_employer", "away_from_edited_employer"])
def test_reassignment_racing_an_employer_edit_keeps_both_outcomes(env, connections, monkeypatch, direction):
    svc, a, _, admin, _ = env
    edited = svc.add_employer(a, EmployerCreate(employer_name="Synthetic Edited"))
    other = svc.add_employer(a, EmployerCreate(employer_name="Synthetic Other"))
    start, target = (other, edited) if direction == "onto_edited_employer" else (edited, other)
    job = employment(svc, a, 1, "Role", month(2020, 1), ONGOING, employer=start)
    s_move, s_edit = CandidateProfileService(connections()), CandidateProfileService(connections())

    if direction == "onto_edited_employer":
        # The employer edit is in progress; the reassignment onto that employer must wait for it.
        reached, release = pause_inside(monkeypatch, "update_record", when=lambda kind, *r: kind == "employer")
        first, first_out = in_thread(lambda: s_edit.update_employer(a, edited, EmployerUpdate(employer_name="Renamed")), name=PAUSED)
        assert reached.wait(10)
        second, second_out = in_thread(lambda: s_move.update_employment(a, job, EmploymentUpdate(candidate_employer_id=target)))
        waiter = s_move
    else:
        # The reassignment away from the employer is in progress; the employer edit must wait for it,
        # and must then no longer count the job as one of its own.
        reached, release = pause_inside(monkeypatch, "lock_review_state", when=lambda kind, *r: kind == "employment")
        first, first_out = in_thread(lambda: s_move.update_employment(a, job, EmploymentUpdate(candidate_employer_id=target)), name=PAUSED)
        assert reached.wait(10)
        second, second_out = in_thread(lambda: s_edit.update_employer(a, edited, EmployerUpdate(employer_name="Renamed")))
        waiter = s_edit
    try:
        assert blocked_on_lock(admin, waiter._conn.info.backend_pid, second)
    finally:
        release.set()
    first.join(15), second.join(15)

    assert first_out == {"result": None} and second_out == {"result": None}
    assert _employer_of(svc, a) == {job: target}
    with db.connect(get_settings()) as c:
        names = dict(c.execute("select candidate_employer_id, employer_name from public.trace_candidate_employers "
                               "where candidate_profile_id = %s", (a.candidate_profile_id,)).fetchall())
    assert names == {edited: "Renamed", other: "Synthetic Other"}


# =========================================================================== M2: database enforcement
# Data foundation v2.3.0. These tests go around the service on purpose (raw SQL inside an owner
# session) to show that the database, not only the backend, enforces the review rules.
from app.services.candidate_profile import DeletionBlocked, IdentityMismatch  # noqa: E402
from app.services.candidate_profile import repository as repository_module  # noqa: E402
from app.services.candidate_profile.review import TRANSITIONS  # noqa: E402


def _owner_sql(conn, owner, sql, params=()):
    with owner_session(conn, owner):
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else None


# --------------------------------------------------------------------------- identity
def test_owner_session_sets_one_consistent_identity(env):
    svc, a, b, _, conn = env
    with owner_session(conn, a.owner_user_id):
        row = conn.execute("select current_user, auth.uid(), auth.role(), current_setting('request.jwt.claim.sub'), "
                           "current_setting('request.jwt.claims')::jsonb ->> 'sub'").fetchone()
    assert row == ("trace_app_user", a.owner_user_id, "authenticated", str(a.owner_user_id), str(a.owner_user_id))
    with owner_session(conn, b.owner_user_id):                    # same connection, next owner
        assert conn.execute("select auth.uid()").fetchone()[0] == b.owner_user_id
    # Nothing outlives the transaction.
    assert conn.execute("select current_user, coalesce(current_setting('request.jwt.claim.sub', true), '')").fetchone() == ("postgres", "")
    conn.rollback()


def test_a_stale_session_level_claim_never_becomes_the_identity(env):
    svc, a, b, _, conn = env
    conn.autocommit = True
    conn.execute("select set_config('request.jwt.claim.sub', %s, false)", (str(b.owner_user_id),))   # left behind on a pooled connection
    conn.autocommit = False
    try:
        with owner_session(conn, a.owner_user_id):
            assert conn.execute("select auth.uid()").fetchone()[0] == a.owner_user_id
        assert svc.get_profile(a).headline == "Synthetic 0"
    finally:
        conn.autocommit = True
        conn.execute("select set_config('request.jwt.claim.sub', '', false)")
        conn.autocommit = False


def test_owner_session_refuses_to_run_with_a_mismatched_identity(env, monkeypatch):
    svc, a, b, _, conn = env
    act = svc.add_activity(a, ActivityCreate(activity_type="course", title_raw="Untouched"))
    # A future code path that sets only request.jwt.claims would let a stale claim.sub win in auth.uid().
    monkeypatch.setattr(repository_module, "IDENTITY_SQL",
                        "select set_config('request.jwt.claims', %(claims)s, true), %(sub)s::text")
    conn.autocommit = True
    conn.execute("select set_config('request.jwt.claim.sub', %s, false)", (str(b.owner_user_id),))
    conn.autocommit = False
    try:
        with pytest.raises(IdentityMismatch):
            svc.update_activity(a, act, ActivityUpdate(title_raw="changed"))
    finally:
        conn.autocommit = True
        conn.execute("select set_config('request.jwt.claim.sub', '', false)")
        conn.autocommit = False
    monkeypatch.undo()
    assert svc.get_profile(a).career_activities["course"][0].title_raw == "Untouched"


# --------------------------------------------------------------------------- every candidate operation runs in owner_session
CANDIDATE_SQL = ("trace_candidate_", "trace_resume_documents", "trace_extraction_runs", "trace_review.", "trace_ingest.")


def test_every_service_statement_on_candidate_data_runs_as_the_owner(env, context, monkeypatch):
    """Records the role and auth.uid() in force for every SQL statement that touches candidate tables
    or trace_review while the full service surface runs. Any path that bypassed owner_session would
    run as the session user (postgres) with no identity and fail this test."""
    svc, a, _, admin, conn = env
    seen = []
    original = psycopg.Cursor.execute

    def audited(self, query, params=None, **kw):
        text = query if isinstance(query, str) else str(query)
        if self.connection is conn and any(t in text for t in CANDIDATE_SQL):
            probe = self.connection.cursor()
            seen.append((original(probe, "select current_user::text, auth.uid()").fetchone(), text.split()[0].lower()))
        return original(self, query, params, **kw)

    monkeypatch.setattr(psycopg.Cursor, "execute", audited)
    er = svc.add_employer(a, EmployerCreate(employer_name="Audited Co"))
    er2 = svc.add_employer(a, EmployerCreate(employer_name="Audited Two"))
    svc.update_employer(a, er, EmployerUpdate(industry_raw="Fintech"))
    e = svc.add_employment(a, EmploymentCreate(sequence_index=1, title_raw="PM", candidate_employer_id=er))
    svc.update_employment(a, e, EmploymentUpdate(title_raw="Product Manager", candidate_employer_id=er2))
    edu = svc.add_education(a, EducationCreate(qualification_title_raw="MBA"))
    svc.update_education(a, edu, EducationUpdate(notes="n"))
    act = svc.add_activity(a, ActivityCreate(activity_type="course", title_raw="C"))
    svc.update_activity(a, act, ActivityUpdate(outcome_raw="o"))
    ev = svc.add_evidence(a, EvidenceCreate(evidence_type="other", evidence_text="Audited evidence"))
    svc.update_evidence(a, ev, EvidenceUpdate(evidence_text="Audited evidence, edited"))
    sk = svc.add_skill(a, SkillCreate(skill_text_raw="SQL"))
    svc.update_skill(a, sk, SkillUpdate(stated_proficiency_raw="advanced"))
    br = svc.add_career_break(a, CareerBreakCreate(break_reason="sabbatical"))
    svc.update_career_break(a, br, CareerBreakUpdate(note="n"))
    svc.update_profile(a, ProfileUpdate(headline="Audited"))
    for kind, rid in (("employment", e), ("education", edu), ("activity", act), ("evidence", ev), ("skill", sk), ("career_break", br)):
        svc.confirm_record(a, kind, rid)
        svc.reject_record(a, kind, rid)
        svc.reinstate_record(a, kind, rid)
    svc.get_profile(a)
    svc.sequence_employment_history(a, context)
    monkeypatch.undo()
    assert len(seen) > 40
    assert {who for who, _ in seen} == {("trace_app_user", a.owner_user_id)}
    assert {verb for _, verb in seen} >= {"insert", "update", "select"}


# The one privileged exception (local synthetic demo, Phase 2): fixture designation runs as the table owner, outside
# owner_session, because no client role may set fixture flags. It is confined to two named functions and pinned
# below: two UPDATEs, each scoped to the verified owner; no insert, delete or read of candidate rows.
PRIVILEGED_EXCEPTIONS = {"demo/fixtures.py": ("designate_fixture", "approved_pair"),
                         "services/real_resume.py": ("admit_resume",)}                 # v2.9.0


def _function_source(code, name):
    import re
    m = re.search(rf"^def {name}\(.*?(?=^\S)", code + "\nEND", flags=re.S | re.M)
    return m.group(0) if m else ""


def _outside_functions(code, names):
    for name in names:
        code = code.replace(_function_source(code, name), "")
    return code


def _assert_designation_is_narrow(src):
    """Phase 6: designation no longer runs as the table owner. Python sets the request identity and calls the one
    scoped database function; the function keeps every narrowness property the owner-run UPDATEs had."""
    import pathlib
    import re
    body = _function_source(src, "designate_fixture")
    assert "select trace_ingest.designate_fixture(%s, %s, %s, %s)" in body
    assert "IDENTITY_SQL" in body and "ctx.owner_user_id" in body and "ctx.candidate_profile_id" in body
    assert not re.search(r"\b(update|insert into|delete from)\b", body, flags=re.I)
    assert "relowner" not in body                                               # no table-owner requirement any more
    allowlist = _function_source(src, "approved_pair")
    assert re.findall(r"trace_\w+\.\w+", allowlist) == ["trace_ingest.approved_fixture_resumes"]
    root = pathlib.Path(__file__).resolve().parents[2] / "data-foundation/supabase/migrations"
    sql = (root / "20261113100000_trace_runtime_roles.sql").read_text(encoding="utf-8")
    fn = sql.split("create function trace_ingest.designate_fixture", 1)[1].split("$$;", 1)[0]
    updates = re.findall(r"update (public\.\w+) \w+\s+set (.*?)\s+where (.*?);", fn, flags=re.S)
    assert sorted(u[0] for u in updates) == ["public.trace_candidate_profiles", "public.trace_resume_documents"]
    assert all("owner_user_id = v_uid" in u[2] and "candidate_profile_id = p_candidate_profile_id" in u[2] for u in updates)
    assert all(set(re.findall(r"(\w+) =", u[1])) <= {"is_test_fixture", "fixture_profile_id"} for u in updates)
    assert "p.fixture_profile_id is null or p.fixture_profile_id = p_fixture_profile_id" in fn               # never relinked
    assert "trace_ingest.approved_fixture_resumes" in fn and "v_profiles <> 1 or v_resumes <> 1" in fn
    assert "security definer" in fn and "set search_path = ''" in fn
    assert re.search(r"grant execute on function trace_ingest\.designate_fixture\([^)]*\) to trace_app_runtime;", sql)
    assert re.search(r"revoke all on function trace_ingest\.designate_fixture\([^)]*\) from public, anon, authenticated, "
                     r"service_role, trace_app_user;", sql)


def test_candidate_sql_lives_only_in_the_owner_scoped_repository():
    """Static guard: candidate tables and trace_review appear only in repository.py, the repository
    is constructed only by owner_session, and the service never executes SQL itself."""
    import pathlib
    import re
    app = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in app.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", text, flags=re.S)
        code = "\n".join(line.split("#")[0] for line in code.splitlines())
        rel = str(path.relative_to(app))
        if rel in PRIVILEGED_EXCEPTIONS:
            code = _outside_functions(code, PRIVILEGED_EXCEPTIONS[rel])
        if path.name != "repository.py" and any(t in code for t in CANDIDATE_SQL):
            offenders.append(rel)
    assert offenders == []
    _assert_designation_is_narrow((app / "demo/fixtures.py").read_text(encoding="utf-8"))
    admit = _function_source((app / "services/real_resume.py").read_text(encoding="utf-8"), "admit_resume")
    assert re.findall(r"trace_\w+\.\w+", admit) == ["trace_ingest.admit_deidentified_resume"]
    assert "ctx.owner_user_id" in admit and "ctx.candidate_profile_id" in admit
    assert not re.search(r"\b(update|insert into|delete from)\b", admit, flags=re.I)
    repo_src = (app / "services/candidate_profile/repository.py").read_text(encoding="utf-8")
    assert repo_src.count("CandidateProfileRepository(conn") == 1          # only inside owner_session
    owner_session_src = repo_src.split("def owner_session", 1)[1].split("\nclass ", 1)[0]
    assert "CandidateProfileRepository(conn" in owner_session_src
    service_src = (app / "services/candidate_profile/service.py").read_text(encoding="utf-8")
    assert ".execute(" not in service_src and ".cursor(" not in service_src
    assert "CandidateProfileRepository" not in service_src                # repositories come only from owner_session
    # The only non-owner-session query reads shared taxonomy, never candidate data.
    ctx_src = repo_src.split("def load_sequencing_context", 1)[1]
    assert not any(t in ctx_src for t in CANDIDATE_SQL)


# --------------------------------------------------------------------------- the database is authoritative
def test_python_review_matrix_matches_the_database_function(env):
    _, _, _, admin, _ = env
    for action in ("confirm", "reject", "reinstate"):
        for status in ("pending_review", "user_confirmed", "user_corrected", "rejected"):
            expected = TRANSITIONS[action].get(status)
            try:
                got = admin.execute("select trace_review.next_review_status(%s, %s)::text", (status, action)).fetchone()[0]
                got = got if got is not None else "noop"
            except psycopg.Error as exc:
                got = exc.sqlstate
            want = "TR003" if expected is None else ("noop" if not isinstance(expected, str) else expected)
            assert got == want, (action, status)


def test_raw_sql_in_an_owner_session_cannot_bypass_review_rules(env):
    svc, a, _, _, conn = env
    e = svc.add_employment(a, EmploymentCreate(sequence_index=1, title_raw="PM"))
    svc.confirm_record(a, "employment", e)
    with pytest.raises(PermissionDenied):
        _owner_sql(conn, a.owner_user_id, "update public.trace_candidate_employments set review_status = 'pending_review' "
                                         "where candidate_employment_id = %s", (e,))
    with pytest.raises(CorrectionNotAvailable):
        _owner_sql(conn, a.owner_user_id, "update public.trace_candidate_employments set title_raw = 'Rewritten' "
                                         "where candidate_employment_id = %s", (e,))
    with pytest.raises(PermissionDenied):
        _owner_sql(conn, a.owner_user_id, "update public.trace_candidate_employments set role_id = 'PM-GPM' "
                                         "where candidate_employment_id = %s", (e,))
    with pytest.raises(PermissionDenied):
        _owner_sql(conn, a.owner_user_id, "insert into public.trace_candidate_skills (candidate_profile_id, skill_text_raw, review_status) "
                                         "values (%s, 'x', 'user_confirmed')", (a.candidate_profile_id,))
    with pytest.raises(PermissionDenied):
        _owner_sql(conn, a.owner_user_id, "update public.trace_candidate_profiles set confirmation_status = 'confirmed' "
                                         "where candidate_profile_id = %s", (a.candidate_profile_id,))
    rec = svc.get_profile(a).employment_history[0]
    assert (rec.title_raw, rec.role_id, rec.provenance.review_status) == ("PM", "ROLE-UNKNOWN", "user_confirmed")


def test_candidate_rows_carry_no_derived_legacy_trust_labels(env):
    svc, a, *_ = env
    er = svc.add_employer(a, EmployerCreate(employer_name="Synthetic Co"))
    e = svc.add_employment(a, EmploymentCreate(sequence_index=1, title_raw="PM", candidate_employer_id=er))
    svc.add_skill(a, SkillCreate(skill_text_raw="SQL", candidate_employment_id=e))
    p = svc.get_profile(a)
    assert p.employment_history[0].employer.attribute_provenance is None
    assert p.employment_history[0].entry_source == "manual_entry"
    assert p.skills[0].evidence_source is None
    assert (p.skills[0].provenance.review_status, p.skills[0].provenance.verification_status) == ("pending_review", "unverified")


# --------------------------------------------------------------------------- deletion (decision A)
def _delete(conn, owner, table, pk, rid):
    _owner_sql(conn, owner, f"delete from public.{table} where {pk} = %s", (rid,))


def test_deleting_a_parent_never_removes_or_detaches_reviewed_dependents(env):
    svc, a, _, _, conn = env
    er = svc.add_employer(a, EmployerCreate(employer_name="Synthetic Co"))
    e = svc.add_employment(a, EmploymentCreate(sequence_index=1, title_raw="PM", candidate_employer_id=er))
    svc.confirm_record(a, "employment", e)
    with pytest.raises(DeletionBlocked):
        _delete(conn, a.owner_user_id, "trace_candidate_employers", "candidate_employer_id", er)
    e2 = svc.add_employment(a, EmploymentCreate(sequence_index=2, title_raw="SPM"))
    sk = svc.add_skill(a, SkillCreate(skill_text_raw="Roadmaps", candidate_employment_id=e2))
    svc.confirm_record(a, "skill", sk)
    with pytest.raises(DeletionBlocked):
        _delete(conn, a.owner_user_id, "trace_candidate_employments", "candidate_employment_id", e2)
    act = svc.add_activity(a, ActivityCreate(activity_type="course", title_raw="Course"))
    ev = svc.add_evidence(a, EvidenceCreate(evidence_type="achievement", evidence_text="Course outcome text", candidate_activity_id=act))
    svc.confirm_record(a, "evidence", ev)
    with pytest.raises(DeletionBlocked):
        _delete(conn, a.owner_user_id, "trace_candidate_career_activities", "candidate_activity_id", act)
    edu = svc.add_education(a, EducationCreate(qualification_title_raw="MBA"))
    sk2 = svc.add_skill(a, SkillCreate(skill_text_raw="Finance", candidate_education_id=edu))
    svc.confirm_record(a, "skill", sk2)
    with pytest.raises(DeletionBlocked):
        _delete(conn, a.owner_user_id, "trace_candidate_education", "candidate_education_id", edu)
    ev2 = svc.add_evidence(a, EvidenceCreate(evidence_type="other", evidence_text="Standalone evidence"))
    sk3 = svc.add_skill(a, SkillCreate(skill_text_raw="Writing", candidate_evidence_id=ev2))
    svc.confirm_record(a, "skill", sk3)
    with pytest.raises(DeletionBlocked):
        _delete(conn, a.owner_user_id, "trace_candidate_evidence", "candidate_evidence_id", ev2)
    p = svc.get_profile(a)
    assert p.employment_history[0].employer.candidate_employer_id == er
    assert {(s.candidate_skill_id, s.supported_by_id) for s in p.skills} == {(sk, e2), (sk2, edu), (sk3, ev2)}
    assert [x.candidate_evidence_id for x in p.career_activities["course"][0].achievements] == [ev]


def test_pending_and_rejected_dependents_are_detached_intact(env):
    svc, a, _, _, conn = env
    e = svc.add_employment(a, EmploymentCreate(sequence_index=1, title_raw="PM"))
    sk = svc.add_skill(a, SkillCreate(skill_text_raw="SQL", candidate_employment_id=e))
    ev = svc.add_evidence(a, EvidenceCreate(evidence_type="achievement", evidence_text="Shipped a synthetic thing", candidate_employment_id=e))
    act = svc.add_activity(a, ActivityCreate(activity_type="independent_project", title_raw="App", related_employment_id=e))
    svc.reject_record(a, "evidence", ev)
    before = svc.get_profile(a)
    _delete(conn, a.owner_user_id, "trace_candidate_employments", "candidate_employment_id", e)
    after = svc.get_profile(a)
    assert after.employment_history == []
    s_before, s_after = before.skills[0], after.skills[0]
    assert (s_after.supported_by, s_after.provenance) == (None, s_before.provenance)
    assert s_after.model_dump(exclude={"supported_by", "supported_by_id"}) == s_before.model_dump(exclude={"supported_by", "supported_by_id"})
    ev_after = after.unattached_evidence[0]
    assert (ev_after.candidate_evidence_id, ev_after.evidence_text, ev_after.provenance.review_status) == (ev, "Shipped a synthetic thing", "rejected")
    proj = after.career_activities["independent_project"][0]
    assert (proj.candidate_activity_id, proj.related_employment_id, proj.provenance.review_status) == (act, None, "pending_review")


def test_detaching_a_duplicate_skill_fails_clearly(env):
    svc, a, _, _, conn = env
    e = svc.add_employment(a, EmploymentCreate(sequence_index=1, title_raw="PM"))
    svc.add_skill(a, SkillCreate(skill_text_raw="SQL", candidate_employment_id=e))
    svc.add_skill(a, SkillCreate(skill_text_raw="sql"))
    with pytest.raises(DeletionBlocked, match="duplicate"):
        _delete(conn, a.owner_user_id, "trace_candidate_employments", "candidate_employment_id", e)
    assert len(svc.get_profile(a).employment_history) == 1 and len(svc.get_profile(a).skills) == 2


# --------------------------------------------------------------------------- concurrency: deletion races (same lock order)
def _hold_delete(conn, owner, table, pk, rid, reached, release):
    def run():
        with owner_session(conn, owner):
            conn.execute(f"delete from public.{table} where {pk} = %s", (rid,))
            reached.set()
            assert release.wait(15)
    return run


def test_employer_delete_in_progress_then_confirmation_confirms_the_detached_job(two_services):
    s1, c1, s2, c2, svc, a, admin = two_services
    er, e = _job_at_employer(svc, a)
    reached, release = threading.Event(), threading.Event()
    deleter, d_out = in_thread(_hold_delete(c1, a.owner_user_id, "trace_candidate_employers", "candidate_employer_id", er, reached, release))
    assert reached.wait(10)
    reviewer, r_out = in_thread(lambda: s2.confirm_record(a, "employment", e))
    try:
        assert blocked_on_lock(admin, c2.info.backend_pid, reviewer), "confirmation did not wait for the employer delete"
    finally:
        release.set()
    deleter.join(10), reviewer.join(10)
    assert d_out == {"result": None} and r_out == {"result": "user_confirmed"}
    job = svc.get_profile(a).employment_history[0]
    assert (job.employer, job.provenance.review_status) == (None, "user_confirmed")


def test_confirmation_in_progress_blocks_employer_delete_which_then_refuses(two_services, monkeypatch):
    s1, c1, s2, c2, svc, a, admin = two_services
    er, e = _job_at_employer(svc, a)
    reached, release = pause_inside(monkeypatch, "review_record")
    reviewer, r_out = in_thread(lambda: s1.confirm_record(a, "employment", e), name=PAUSED)
    assert reached.wait(10)
    done, never = threading.Event(), threading.Event()
    never.set()
    deleter, d_out = in_thread(_hold_delete(c2, a.owner_user_id, "trace_candidate_employers", "candidate_employer_id", er, done, never))
    try:
        assert blocked_on_lock(admin, c2.info.backend_pid, deleter), "employer delete did not wait for the review"
    finally:
        release.set()
    reviewer.join(10), deleter.join(10)
    assert r_out == {"result": "user_confirmed"}
    assert isinstance(d_out.get("error"), DeletionBlocked)          # re-read under the lock: now confirmed
    assert svc.get_profile(a).employment_history[0].employer.candidate_employer_id == er


def test_skill_confirmation_in_progress_blocks_employment_delete_which_then_refuses(two_services, monkeypatch):
    s1, c1, s2, c2, svc, a, admin = two_services
    e = svc.add_employment(a, EmploymentCreate(sequence_index=1, title_raw="PM"))
    sk = svc.add_skill(a, SkillCreate(skill_text_raw="SQL", candidate_employment_id=e))
    reached, release = pause_inside(monkeypatch, "review_record")
    reviewer, r_out = in_thread(lambda: s1.confirm_record(a, "skill", sk), name=PAUSED)
    assert reached.wait(10)
    done, never = threading.Event(), threading.Event()
    never.set()
    deleter, d_out = in_thread(_hold_delete(c2, a.owner_user_id, "trace_candidate_employments", "candidate_employment_id", e, done, never))
    try:
        assert blocked_on_lock(admin, c2.info.backend_pid, deleter), "employment delete did not wait for the skill review"
    finally:
        release.set()
    reviewer.join(10), deleter.join(10)
    assert r_out == {"result": "user_confirmed"} and isinstance(d_out.get("error"), DeletionBlocked)
    p = svc.get_profile(a)
    assert len(p.employment_history) == 1 and p.skills[0].supported_by_id == e


def test_reassignment_in_progress_blocks_deleting_the_old_employer(two_services, monkeypatch):
    s1, c1, s2, c2, svc, a, admin = two_services
    old, e = _job_at_employer(svc, a, "Synthetic Old")
    new = svc.add_employer(a, EmployerCreate(employer_name="Synthetic New"))
    reached, release = pause_inside(monkeypatch, "lock_review_state", when=lambda kind, *r: kind == "employment")
    mover, m_out = in_thread(lambda: s1.update_employment(a, e, EmploymentUpdate(candidate_employer_id=new)), name=PAUSED)
    assert reached.wait(10)                                       # holds KEY SHARE on both employers, then the job
    done, never = threading.Event(), threading.Event()
    never.set()
    deleter, d_out = in_thread(_hold_delete(c2, a.owner_user_id, "trace_candidate_employers", "candidate_employer_id", old, done, never))
    try:
        assert blocked_on_lock(admin, c2.info.backend_pid, deleter), "employer delete did not wait for the reassignment"
    finally:
        release.set()
    mover.join(10), deleter.join(10)
    assert m_out == {"result": None} and d_out == {"result": None}
    assert _employer_of(svc, a) == {e: new}


def test_employment_delete_in_progress_then_reassignment_finds_nothing(two_services):
    s1, c1, s2, c2, svc, a, admin = two_services
    er, e = _job_at_employer(svc, a)
    other = svc.add_employer(a, EmployerCreate(employer_name="Synthetic Other"))
    reached, release = threading.Event(), threading.Event()
    deleter, d_out = in_thread(_hold_delete(c1, a.owner_user_id, "trace_candidate_employments", "candidate_employment_id", e, reached, release))
    assert reached.wait(10)
    mover, m_out = in_thread(lambda: s2.update_employment(a, e, EmploymentUpdate(candidate_employer_id=other)))
    try:
        assert blocked_on_lock(admin, c2.info.backend_pid, mover), "reassignment did not wait for the employment delete"
    finally:
        release.set()
    deleter.join(10), mover.join(10)
    assert d_out == {"result": None} and isinstance(m_out.get("error"), RecordNotFound)
    assert svc.get_profile(a).employment_history == []


def test_reassignment_holds_the_employer_rows_before_it_waits_for_the_employment(env, connections, monkeypatch):
    """Lock order inside trace_review.reassign_employment: employer rows (KEY SHARE, by id) first, then
    the employment row. While the reassignment waits for the employment row (held by a draft edit),
    an edit of the target employer must already be blocked by the reassignment's KEY SHARE. With an
    employment-first order the employer edit would not wait here, and the crossing case can deadlock."""
    svc, a, _, admin, _ = env
    e1 = svc.add_employer(a, EmployerCreate(employer_name="Synthetic E1"))
    e2 = svc.add_employer(a, EmployerCreate(employer_name="Synthetic E2"))
    x = employment(svc, a, 1, "Role X", month(2018, 1), month(2019, 12), employer=e2)
    s_hold, s_move, s_edit = (CandidateProfileService(connections()) for _ in range(3))
    reached, release = pause_inside(monkeypatch, "lock_review_state", when=lambda kind, *r: kind == "employment")
    holder, h_out = in_thread(lambda: s_hold.update_employment(a, x, EmploymentUpdate(title_raw="Role X (edited)")), name=PAUSED)
    assert reached.wait(10)                                         # the draft edit holds the employment row
    mover, m_out = in_thread(lambda: s_move.update_employment(a, x, EmploymentUpdate(candidate_employer_id=e1)))
    editor = None
    try:
        assert blocked_on_lock(admin, s_move._conn.info.backend_pid, mover), "reassignment did not wait for the employment row"
        editor, e_out = in_thread(lambda: s_edit.update_employer(a, e1, EmployerUpdate(employer_name="Renamed E1")))
        assert blocked_on_lock(admin, s_edit._conn.info.backend_pid, editor), \
            "the reassignment did not hold the employer row before waiting for the employment row"
    finally:
        release.set()
    for t in (holder, mover, editor):
        if t is not None:
            t.join(15)
            assert not t.is_alive()
    assert (h_out, m_out, e_out) == ({"result": None},) * 3
    assert _employer_of(svc, a) == {x: e1}
    assert svc.get_profile(a).employment_history[0].employer.employer_name == "Renamed E1"
