"""Database tests for corrections with append-only correction history (M4, data foundation v2.5.0).

Synthetic, transient users only (see test_candidate_profile_integration.py for the fixtures). The admin
connection (table owner) is used only to create records in states no client role may create (extracted,
inferred, legacy origin) and to show that even the table owner cannot rewrite history.
"""
import threading
from decimal import Decimal

import psycopg
import pytest
from pydantic import ValidationError

from app.services.candidate_profile import (
    ActivityCorrection, ActivityCreate, CareerBreakCorrection, CareerBreakCreate, CorrectionHistoryViolation,
    CorrectionNotAvailable, DeletionBlocked, EducationCorrection, EducationCreate, EmploymentCorrection, EmploymentCreate,
    EmploymentUpdate, EvidenceCorrection, EvidenceCreate, InvalidRecord, InvalidReference, PermissionDenied, RecordNotEditable,
    RecordNotFound, SkillCorrection, SkillCreate, owner_session,
)
from tests.test_candidate_profile_integration import (  # noqa: F401  (fixtures)
    ONGOING, PAUSED, blocked_on_lock, context, env, in_thread, month, pause_inside, settings, two_services, year,
)

pytestmark = pytest.mark.integration


def _sql(conn, owner, sql, params=()):
    with owner_session(conn, owner):
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _row(admin, table, pk, rid):
    cur = admin.execute(f"select to_jsonb(t) from public.{table} t where {pk} = %s", (rid,))
    r = cur.fetchone()
    return r[0] if r else None


def _history(admin, rid):
    return admin.execute("select changed_fields, previous_values, corrected_values, prior_review_status::text "
                         "from public.trace_candidate_corrections where record_id = %s order by correction_seq", (rid,)).fetchall()


# kind -> (table, pk, create(svc, ctx) -> id, correction covering every correctable field, expected column values)
CASES = {
    "employment": ("trace_candidate_employments", "candidate_employment_id",
                   lambda svc, a: svc.add_employment(a, EmploymentCreate(sequence_index=1, title_raw="PM", employment_type="full_time",
                                                                          start=year(2018), end=ONGOING)),
                   EmploymentCorrection(sequence_index=7, title_raw="Senior PM", employment_type="contract", primary_career_sequence=False,
                                        counts_as_full_time_experience=True, classification_note="Full-time contract",
                                        start=month(2019, 2), end=month(2020, 3)),
                   {"sequence_index": 7, "title_raw": "Senior PM", "employment_type": "contract", "primary_career_sequence": False,
                    "counts_as_full_time_experience": True, "classification_note": "Full-time contract", "start_year": 2019,
                    "start_month": 2, "start_precision": "month", "end_year": 2020, "end_month": 3, "end_precision": "month",
                    "is_current_as_stated": False}),
    "education": ("trace_candidate_education", "candidate_education_id",
                  lambda svc, a: svc.add_education(a, EducationCreate(qualification_title_raw="BSc", qualification_level="bachelor",
                                                                      institution_name="Synthetic A", field_of_study="CS", study_mode="full_time",
                                                                      completion_status="completed", start=year(2010), end=year(2013), notes="n1")),
                  EducationCorrection(qualification_title_raw="MSc", qualification_level="master", institution_name="Synthetic B",
                                      field_of_study="Maths", study_mode="part_time", completion_status="in_progress",
                                      start=month(2014, 6), end=month(2016, 5), notes="n2"),
                  {"qualification_title_raw": "MSc", "qualification_level": "master", "institution_name": "Synthetic B",
                   "field_of_study": "Maths", "study_mode": "part_time", "completion_status": "in_progress", "start_year": 2014,
                   "start_month": 6, "start_precision": "month", "end_year": 2016, "end_month": 5, "end_precision": "month", "notes": "n2"}),
    "activity": ("trace_candidate_career_activities", "candidate_activity_id",
                 lambda svc, a: svc.add_activity(a, ActivityCreate(activity_type="course", title_raw="Course A", organisation_raw="Org A",
                                                                   description_raw="d1", contribution_raw="c1", outcome_raw="o1",
                                                                   link_url="https://example.invalid/a", status="completed",
                                                                   start=year(2020), end=year(2021), metadata={"a": 1})),
                 ActivityCorrection(activity_type="certification", title_raw="Cert B", organisation_raw="Org B", description_raw="d2",
                                    contribution_raw="c2", outcome_raw="o2", link_url="https://example.invalid/b", status="active",
                                    credential_identifier_raw="SYN-CRED-2", start=month(2021, 3), end=month(2022, 2),
                                    expiry=month(2026, 1), metadata={"b": 2}),
                 {"activity_type": "certification", "title_raw": "Cert B", "organisation_raw": "Org B", "description_raw": "d2",
                  "contribution_raw": "c2", "outcome_raw": "o2", "link_url": "https://example.invalid/b", "status": "active",
                  "credential_identifier_raw": "SYN-CRED-2", "start_year": 2021, "start_month": 3, "start_precision": "month",
                  "end_year": 2022, "end_month": 2, "end_precision": "month", "expiry_year": 2026, "expiry_month": 1,
                  "expiry_precision": "month", "metadata": {"b": 2}}),
    "evidence": ("trace_candidate_evidence", "candidate_evidence_id",
                 lambda svc, a: svc.add_evidence(a, EvidenceCreate(evidence_type="responsibility", evidence_text="Owned a synthetic roadmap")),
                 EvidenceCorrection(evidence_type="achievement", evidence_text="Shipped a synthetic roadmap"),
                 {"evidence_type": "achievement", "evidence_text": "Shipped a synthetic roadmap"}),
    "skill": ("trace_candidate_skills", "candidate_skill_id",
              lambda svc, a: svc.add_skill(a, SkillCreate(skill_text_raw="SQL", stated_proficiency_raw="basic")),
              SkillCorrection(skill_text_raw="PostgreSQL", stated_proficiency_raw="advanced"),
              {"skill_text_raw": "PostgreSQL", "stated_proficiency_raw": "advanced"}),
    "career_break": ("trace_candidate_career_breaks", "candidate_break_id",
                     lambda svc, a: svc.add_career_break(a, CareerBreakCreate(break_reason="education", note="MBA",
                                                                             start=year(2015), end=year(2016))),
                     CareerBreakCorrection(break_reason="sabbatical", note="Travel", start=month(2016, 1), end=month(2017, 9)),
                     {"break_reason": "sabbatical", "note": "Travel", "start_year": 2016, "start_month": 1, "start_precision": "month",
                      "end_year": 2017, "end_month": 9, "end_precision": "month"}),
}


# --------------------------------------------------------------------------- every kind, every correctable field
@pytest.mark.parametrize("kind", list(CASES))
def test_every_correctable_field_of_every_record_type(env, kind):
    svc, a, _, admin, _ = env
    table, pk, create, correction, expected = CASES[kind]
    rid = create(svc, a)
    assert svc.confirm_record(a, kind, rid) == "user_confirmed"
    before = _row(admin, table, pk, rid)
    assert svc.correct_record(a, kind, rid, correction) == "user_corrected"
    after = _row(admin, table, pk, rid)
    assert {k: after[k] for k in expected} == expected
    assert after["review_status"] == "user_corrected"
    unchanged = set(before) - set(expected) - {"review_status", "updated_at", "start_lo", "start_hi", "end_lo", "end_hi"}
    assert {k: after[k] for k in unchanged} == {k: before[k] for k in unchanged}    # provenance, identity, links, mapping
    [(fields, prev, corr, prior)] = _history(admin, rid)
    assert sorted(fields) == sorted(expected) and prior == "user_confirmed"
    assert prev == {k: before[k] for k in expected} and corr == expected
    events = svc.correction_history(a, kind, rid)
    assert [(e.record_kind, e.record_id, e.corrected_by) for e in events] == [(kind, rid, a.owner_user_id)]


# --------------------------------------------------------------------------- extracted records: original values stay recoverable
def _extracted_employment(admin, a, title, origin="source_extracted"):
    """A record no client role may create: an extracted (or inferred, or legacy) employment with its source."""
    resume = admin.execute(
        "insert into public.trace_resume_documents (owner_user_id, candidate_profile_id, storage_object_path, mime_type, byte_size) "
        "values (%s, %s, %s || '/' || gen_random_uuid() || '.pdf', 'application/pdf', 1000) returning resume_id",
        (a.owner_user_id, a.candidate_profile_id, str(a.owner_user_id))).fetchone()[0]
    return admin.execute(
        "insert into public.trace_candidate_employments (owner_user_id, candidate_profile_id, sequence_index, title_raw, employment_type, "
        "start_year, start_month, start_precision, information_origin, extraction_confidence, source_resume_id, source_location) "
        "values (%s, %s, (select coalesce(max(sequence_index), 0) + 1 from public.trace_candidate_employments where candidate_profile_id = %s), "
        "%s, 'full_time', 2021, 7, 'month', %s, %s, %s, 'Professional experience, entry 2') returning candidate_employment_id",
        (a.owner_user_id, a.candidate_profile_id, a.candidate_profile_id, title, origin,
         Decimal("0.800") if origin != "user_stated" else None, resume)).fetchone()[0], resume


def test_repeated_corrections_keep_every_transition_and_the_original(env):
    svc, a, _, admin, _ = env
    e, resume = _extracted_employment(admin, a, "Associate Product Manager")
    assert svc.correct_record(a, "employment", e, EmploymentCorrection(title_raw="Product Manager")) == "user_corrected"
    assert svc.correct_record(a, "employment", e, EmploymentCorrection(title_raw="Senior Product Manager")) == "user_corrected"
    rec = svc.get_profile(a).employment_history[0]
    assert rec.title_raw == "Senior Product Manager"                                   # the latest value
    assert (rec.provenance.information_origin, rec.provenance.review_status, rec.provenance.source_resume_id,
            str(rec.provenance.extraction_confidence), rec.provenance.source_location) == (
        "source_extracted", "user_corrected", resume, "0.800", "Professional experience, entry 2")   # provenance kept
    events = svc.correction_history(a, "employment", e)
    assert [(ev.changed_fields, ev.previous_values, ev.corrected_values, ev.prior_review_status) for ev in events] == [
        (["title_raw"], {"title_raw": "Associate Product Manager"}, {"title_raw": "Product Manager"}, "pending_review"),
        (["title_raw"], {"title_raw": "Product Manager"}, {"title_raw": "Senior Product Manager"}, "user_corrected")]
    assert events[0].previous_values["title_raw"] == "Associate Product Manager"       # the original extracted value


@pytest.mark.parametrize("origin", ["source_extracted", "inferred", None])
def test_pending_extracted_inferred_and_legacy_records_can_be_corrected(env, origin):
    svc, a, _, admin, _ = env
    e, _ = _extracted_employment(admin, a, "Analyst", origin=origin or "source_extracted")
    if origin is None:
        admin.execute("update public.trace_candidate_employments set information_origin = null, extraction_confidence = null "
                      "where candidate_employment_id = %s", (e,))
    assert svc.correct_record(a, "employment", e, EmploymentCorrection(title_raw="Product Analyst")) == "user_corrected"
    assert _history(admin, e)[0][3] == "pending_review"


def test_corrected_records_can_be_rejected_and_reinstated_with_history_kept(env, context):
    svc, a, _, admin, conn = env
    e, _ = _extracted_employment(admin, a, "PM")
    svc.correct_record(a, "employment", e, EmploymentCorrection(title_raw="Product Manager"))
    assert svc.reject_record(a, "employment", e) == "rejected"
    with pytest.raises(RecordNotEditable):                     # the service refuses ...
        svc.correct_record(a, "employment", e, EmploymentCorrection(title_raw="Lead PM"))
    with pytest.raises(RecordNotEditable):                     # ... and so does the database (TR001)
        _sql(conn, a.owner_user_id, "select trace_review.correct_record('employment', %s, %s, '{\"title_raw\": \"Lead PM\"}')",
             (a.candidate_profile_id, e))
    assert len(_history(admin, e)) == 1
    assert svc.reinstate_record(a, "employment", e) == "pending_review"
    assert svc.get_profile(a).employment_history[0].title_raw == "Product Manager"    # values survive rejection
    svc.correct_record(a, "employment", e, EmploymentCorrection(title_raw="Lead PM"))
    assert [h[3] for h in _history(admin, e)] == ["pending_review", "pending_review"]
    assert [h[1]["title_raw"] for h in _history(admin, e)] == ["PM", "Product Manager"]
    # user_corrected is eligible like confirmed; the corrected record is what sequencing sees
    rec = svc.sequence_employment_history(a, context).records[0]
    assert "review_pending" not in rec.exclusion_reasons and rec.stated.title_raw == "Lead PM"


def test_pending_user_stated_drafts_are_edited_without_history(env):
    svc, a, _, admin, conn = env
    e = svc.add_employment(a, EmploymentCreate(sequence_index=1, title_raw="PM"))
    svc.update_employment(a, e, EmploymentUpdate(title_raw="Product Manager"))
    assert svc.get_profile(a).employment_history[0].provenance.review_status == "pending_review"
    with pytest.raises(CorrectionNotAvailable):
        svc.correct_record(a, "employment", e, EmploymentCorrection(title_raw="Senior PM"))
    with pytest.raises(CorrectionNotAvailable):
        _sql(conn, a.owner_user_id, "select trace_review.correct_record('employment', %s, %s, '{\"title_raw\": \"Senior PM\"}')",
             (a.candidate_profile_id, e))
    assert _history(admin, e) == [] and svc.correction_history(a, "employment", e) == []
    svc.confirm_record(a, "employment", e)
    with pytest.raises(CorrectionNotAvailable):                # confirmed: no plain edit, only a correction
        svc.update_employment(a, e, EmploymentUpdate(title_raw="Senior PM"))


# --------------------------------------------------------------------------- forged and unknown fields
def test_protected_and_unknown_fields_are_rejected(env):
    svc, a, _, admin, conn = env
    e, _ = _extracted_employment(admin, a, "PM")
    for field in ("review_status", "role_id", "mapping_confidence", "candidate_employer_id", "information_origin",
                  "verification_status", "source_resume_id", "owner_user_id", "updated_at"):
        with pytest.raises(ValidationError):
            EmploymentCorrection(**{field: "x"})
    for model, field in ((SkillCorrection, "skill_id"), (EvidenceCorrection, "responsibility_area"),
                         (EvidenceCorrection, "candidate_employment_id"), (ActivityCorrection, "related_employment_id")):
        with pytest.raises(ValidationError):
            model(**{field: "x"})
    with pytest.raises(InvalidRecord):
        svc.correct_record(a, "employment", e, SkillCorrection(skill_text_raw="x"))     # wrong model for the kind
    with pytest.raises(InvalidRecord):
        svc.correct_record(a, "employer", e, EmploymentCorrection(title_raw="x"))       # employers are not correctable
    with pytest.raises(InvalidRecord):
        svc.correct_record(a, "employment", e, EmploymentCorrection())                  # nothing to correct
    for forged in ('{"review_status": "user_confirmed"}', '{"role_id": "PM-PM"}', '{"source_resume_id": null}',
                   '{"extraction_confidence": 1}', '{"title_raw": "PM2", "verification_status": "independently_verified"}',
                   '{"candidate_employer_id": null}', '{}', '{"title_raw": "PM"}'):
        with pytest.raises(InvalidReference):                                               # TR006 from the database
            _sql(conn, a.owner_user_id, "select trace_review.correct_record('employment', %s, %s, %s::jsonb)",
                 (a.candidate_profile_id, e, forged))
    assert _history(admin, e) == []
    assert _row(admin, "trace_candidate_employments", "candidate_employment_id", e)["review_status"] == "pending_review"


def test_direct_sql_cannot_correct_or_rewrite_history(env):
    svc, a, _, admin, conn = env
    e, _ = _extracted_employment(admin, a, "PM")
    svc.correct_record(a, "employment", e, EmploymentCorrection(title_raw="Product Manager"))
    for sql in ("update public.trace_candidate_employments set title_raw = 'Forged' where candidate_employment_id = %s",):
        with pytest.raises(CorrectionNotAvailable):
            _sql(conn, a.owner_user_id, sql, (e,))
    for sql in ("update public.trace_candidate_employments set review_status = 'user_corrected' where candidate_employment_id = %s",
                "update public.trace_candidate_corrections set corrected_values = '{}' where record_id = %s",
                "delete from public.trace_candidate_corrections where record_id = %s",
                "insert into public.trace_candidate_corrections (owner_user_id, candidate_profile_id, record_kind, record_id, changed_fields, "
                "previous_values, corrected_values, prior_review_status, corrected_by) select owner_user_id, candidate_profile_id, 'employment', "
                "candidate_employment_id, '{title_raw}', '{}', '{}', 'user_confirmed', owner_user_id "
                "from public.trace_candidate_employments where candidate_employment_id = %s"):
        with pytest.raises(PermissionDenied):
            _sql(conn, a.owner_user_id, sql, (e,))
    # Not even the table owner can write, rewrite or remove history (TR016).
    for sql in ("insert into public.trace_candidate_corrections (owner_user_id, candidate_profile_id, record_kind, record_id, changed_fields, "
                "previous_values, corrected_values, prior_review_status, corrected_by) select owner_user_id, candidate_profile_id, 'employment', "
                "candidate_employment_id, '{title_raw}', '{\"title_raw\": \"Forged\"}', '{\"title_raw\": \"Product Manager\"}', "
                "'user_confirmed', owner_user_id from public.trace_candidate_employments where candidate_employment_id = %s",
"update public.trace_candidate_corrections set corrected_values = '{}' where record_id = %s",
                "delete from public.trace_candidate_corrections where record_id = %s"):
        with pytest.raises(psycopg.Error) as err:
            admin.execute(sql, (e,))
        assert err.value.sqlstate == "TR016"
    with pytest.raises(psycopg.Error) as err:
        admin.execute("truncate public.trace_candidate_corrections")
    assert err.value.sqlstate == "TR016"
    assert len(_history(admin, e)) == 1


def test_history_goes_only_with_its_record_profile_or_account(env):
    svc, a, b, admin, conn = env
    e, _ = _extracted_employment(admin, a, "PM")
    skill = svc.add_skill(a, SkillCreate(skill_text_raw="SQL", candidate_employment_id=e))
    svc.confirm_record(a, "skill", skill)
    svc.correct_record(a, "skill", skill, SkillCorrection(skill_text_raw="PostgreSQL"))
    with pytest.raises(DeletionBlocked):                     # a corrected dependent is reviewed: M2 deletion guard
        _sql(conn, a.owner_user_id, "delete from public.trace_candidate_employments where candidate_employment_id = %s", (e,))
    svc.correct_record(a, "employment", e, EmploymentCorrection(title_raw="Product Manager"))
    _sql(conn, a.owner_user_id, "delete from public.trace_candidate_skills where candidate_skill_id = %s", (skill,))
    assert _history(admin, skill) == [] and len(_history(admin, e)) == 1          # record deletion takes its history
    other = svc.add_education(b, EducationCreate(qualification_title_raw="BSc"))
    svc.confirm_record(b, "education", other)
    svc.correct_record(b, "education", other, EducationCorrection(qualification_title_raw="BSc (Hons)"))
    _sql(conn, a.owner_user_id, "delete from public.trace_candidate_profiles where candidate_profile_id = %s", (a.candidate_profile_id,))
    assert _history(admin, e) == []                                                  # profile deletion takes the rest
    assert len(_history(admin, other)) == 1                                          # another candidate's history is untouched
    with pytest.raises(RecordNotFound):
        svc.correct_record(b, "education", e, EducationCorrection(notes="x"))


def test_candidates_see_only_their_own_history(env):
    svc, a, b, admin, conn = env
    e, _ = _extracted_employment(admin, a, "PM")
    svc.correct_record(a, "employment", e, EmploymentCorrection(title_raw="Product Manager"))
    assert _sql(conn, b.owner_user_id, "select count(*) from public.trace_candidate_corrections")[0][0] == 0
    assert svc.correction_history(b, "employment", e) == []
    with pytest.raises(RecordNotFound):
        svc.correct_record(b, "employment", e, EmploymentCorrection(title_raw="Hijacked"))


# --------------------------------------------------------------------------- concurrency
def test_concurrent_corrections_serialise_and_chain_their_history(two_services, monkeypatch):
    s1, c1, s2, c2, svc, a, admin = two_services
    e, _ = _extracted_employment(admin, a, "PM")
    reached, release = pause_inside(monkeypatch, "correct_record")
    first, f_out = in_thread(lambda: s1.correct_record(a, "employment", e, EmploymentCorrection(title_raw="Product Manager")), name=PAUSED)
    assert reached.wait(10)
    second, s_out = in_thread(lambda: s2.correct_record(a, "employment", e, EmploymentCorrection(title_raw="Senior Product Manager")))
    try:
        assert blocked_on_lock(admin, c2.info.backend_pid, second), "the second correction did not wait for the first"
    finally:
        release.set()
    first.join(10), second.join(10)
    assert f_out == {"result": "user_corrected"} and s_out == {"result": "user_corrected"}
    assert [(h[1]["title_raw"], h[2]["title_raw"], h[3]) for h in _history(admin, e)] == [
        ("PM", "Product Manager", "pending_review"), ("Product Manager", "Senior Product Manager", "user_corrected")]


def test_correction_in_progress_then_record_deletion_removes_record_and_history(two_services, monkeypatch):
    s1, c1, s2, c2, svc, a, admin = two_services
    e, _ = _extracted_employment(admin, a, "PM")
    reached, release = pause_inside(monkeypatch, "correct_record")
    corrector, c_out = in_thread(lambda: s1.correct_record(a, "employment", e, EmploymentCorrection(title_raw="Product Manager")), name=PAUSED)
    assert reached.wait(10)
    deleter, d_out = in_thread(lambda: _sql(c2, a.owner_user_id,
                                            "delete from public.trace_candidate_employments where candidate_employment_id = %s", (e,)))
    try:
        assert blocked_on_lock(admin, c2.info.backend_pid, deleter), "the deletion did not wait for the correction"
    finally:
        release.set()
    corrector.join(10), deleter.join(10)
    assert c_out == {"result": "user_corrected"} and d_out == {"result": None}
    assert _row(admin, "trace_candidate_employments", "candidate_employment_id", e) is None and _history(admin, e) == []


def test_deletion_in_progress_then_correction_finds_nothing(two_services):
    s1, c1, s2, c2, svc, a, admin = two_services
    e, _ = _extracted_employment(admin, a, "PM")
    reached, release = threading.Event(), threading.Event()

    def hold_delete():
        with owner_session(c1, a.owner_user_id):
            c1.execute("delete from public.trace_candidate_employments where candidate_employment_id = %s", (e,))
            reached.set()
            assert release.wait(15)
    deleter, d_out = in_thread(hold_delete)
    assert reached.wait(10)
    corrector, c_out = in_thread(lambda: s2.correct_record(a, "employment", e, EmploymentCorrection(title_raw="Product Manager")))
    try:
        assert blocked_on_lock(admin, c2.info.backend_pid, corrector), "the correction did not wait for the deletion"
    finally:
        release.set()
    deleter.join(10), corrector.join(10)
    assert d_out == {"result": None} and isinstance(c_out.get("error"), RecordNotFound)
    assert _history(admin, e) == []


def test_every_correction_statement_runs_as_the_owner(env, monkeypatch):
    svc, a, _, admin, conn = env
    e, _ = _extracted_employment(admin, a, "PM")
    seen = []
    original = psycopg.Cursor.execute

    def audited(self, query, params=None, **kw):
        text = query if isinstance(query, str) else str(query)
        if self.connection is conn and ("correct_record" in text or "trace_candidate_corrections" in text):
            seen.append(original(self.connection.cursor(), "select current_user::text, auth.uid()").fetchone())
        return original(self, query, params, **kw)

    monkeypatch.setattr(psycopg.Cursor, "execute", audited)
    svc.correct_record(a, "employment", e, EmploymentCorrection(title_raw="Product Manager"))
    svc.correction_history(a, "employment", e)
    monkeypatch.undo()
    assert len(seen) == 2 and set(seen) == {("trace_app_user", a.owner_user_id)}
