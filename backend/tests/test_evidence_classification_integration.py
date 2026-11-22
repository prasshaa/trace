"""Candidate classification of evidence responsibility areas (M6, data foundation v2.7.0). Local Supabase only.

Synthetic, transient users only (fixtures from test_candidate_profile_integration.py). The admin connection
(table owner) is used only to create records in states no client role may create (extracted evidence) and to
inspect rows.
"""
import threading
from decimal import Decimal

import psycopg
import pytest

from app.services.candidate_profile import (
    EvidenceCorrection, EvidenceCreate, InvalidReference, PermissionDenied,
    RecordNotEditable, RecordNotFound, owner_session,
)
from tests.test_candidate_corrections_integration import _sql
from tests.test_candidate_profile_integration import (  # noqa: F401  (fixtures)
    PAUSED, blocked_on_lock, context, env, in_thread, pause_inside, settings, two_services,
)

pytestmark = pytest.mark.integration
FIXED = ("evidence_text", "evidence_type", "responsibility_area", "review_status", "verification_status", "information_origin",
         "provenance", "evidence_status", "confidence", "extraction_confidence", "source_resume_id", "extraction_run_id",
         "source_location", "candidate_employment_id", "candidate_activity_id")


def extracted_evidence(admin, a, text="Owned the payments roadmap"):
    resume = admin.execute(
        "insert into public.trace_resume_documents (owner_user_id, candidate_profile_id, storage_object_path, mime_type, byte_size) "
        "values (%s, %s, %s || '/' || gen_random_uuid() || '.pdf', 'application/pdf', 1000) returning resume_id",
        (a.owner_user_id, a.candidate_profile_id, str(a.owner_user_id))).fetchone()[0]
    return admin.execute(
        "insert into public.trace_candidate_evidence (owner_user_id, candidate_profile_id, evidence_type, evidence_text, information_origin, "
        "extraction_confidence, source_resume_id, source_location) values (%s, %s, 'responsibility', %s, 'source_extracted', %s, %s, "
        "'Experience, entry 1, bullet 1') returning candidate_evidence_id",
        (a.owner_user_id, a.candidate_profile_id, text, Decimal("0.800"), resume)).fetchone()[0]


def row(admin, e):
    cur = admin.execute("select to_jsonb(t) from public.trace_candidate_evidence t where candidate_evidence_id = %s", (e,))
    r = cur.fetchone()
    return r[0] if r else None


def record(svc, a, e):
    p = svc.get_profile(a)
    pool = p.unattached_evidence + [x for j in p.employment_history for x in j.responsibilities + j.achievements + j.other_evidence]
    return next(x for x in pool if x.candidate_evidence_id == e)


# --------------------------------------------------------------------------- classification
def test_own_extracted_evidence_is_classified_without_changing_anything_else(env):
    svc, a, _, admin, _ = env
    e = extracted_evidence(admin, a)
    before = row(admin, e)
    assert svc.classify_evidence(a, e, ["roadmap_ownership", "product_discovery", "roadmap_ownership"]) == ["product_discovery", "roadmap_ownership"]
    after = row(admin, e)
    assert {k: after[k] for k in FIXED} == {k: before[k] for k in FIXED}
    assert (after["area_classification"], after["area_classification_basis"], after["area_classified_text"], after["area_classification_is_stale"]) == (
        ["product_discovery", "roadmap_ownership"], "candidate_selected", "Owned the payments roadmap", False)
    rec = record(svc, a, e)
    assert (rec.area_classification, rec.area_classification_basis, rec.provenance.review_status, rec.provenance.verification_status,
            rec.provenance.information_origin, rec.evidence_text) == (
        ["product_discovery", "roadmap_ownership"], "candidate_selected", "pending_review", "unverified", "source_extracted", "Owned the payments roadmap")


def test_own_user_entered_evidence_is_classified(env):
    svc, a, *_ = env
    e = svc.add_evidence(a, EvidenceCreate(evidence_type="achievement", evidence_text="Grew retention by 5 points"))
    assert svc.classify_evidence(a, e, ["outcome_ownership"]) == ["outcome_ownership"]
    svc.confirm_record(a, "evidence", e)
    assert svc.classify_evidence(a, e, ["outcome_ownership", "data_analysis"]) == ["data_analysis", "outcome_ownership"]   # replaces
    assert record(svc, a, e).provenance.review_status == "user_confirmed"


def test_another_candidates_evidence_cannot_be_classified(env):
    svc, a, b, admin, conn = env
    e = extracted_evidence(admin, a)
    with pytest.raises(RecordNotFound):
        svc.classify_evidence(b, e, ["roadmap_ownership"])
    with pytest.raises(RecordNotFound):
        _sql(conn, b.owner_user_id, "select trace_review.classify_evidence_areas(%s, %s, '{roadmap_ownership}', %s)",
             (a.candidate_profile_id, e, "Owned the payments roadmap"))
    assert row(admin, e)["area_classification"] is None


def test_rejected_evidence_cannot_be_classified_until_reinstated(env):
    svc, a, _, admin, conn = env
    e = extracted_evidence(admin, a)
    svc.reject_record(a, "evidence", e)
    with pytest.raises(RecordNotEditable):
        svc.classify_evidence(a, e, ["roadmap_ownership"])
    with pytest.raises(RecordNotEditable):          # the database refuses it even when the service is bypassed
        _sql(conn, a.owner_user_id, "select trace_review.classify_evidence_areas(%s, %s, '{roadmap_ownership}', %s)",
             (a.candidate_profile_id, e, "Owned the payments roadmap"))
    assert row(admin, e)["area_classification"] is None
    svc.reinstate_record(a, "evidence", e)
    assert svc.classify_evidence(a, e, ["roadmap_ownership"]) == ["roadmap_ownership"]
    assert row(admin, e)["review_status"] == "pending_review"                  # still needs review; classification is not review


@pytest.mark.parametrize("areas", [[], ["not_an_area"], ["roadmap_ownership", None], ["ROADMAP_OWNERSHIP"], ["roadmap_ownership", "nope"]])
def test_invalid_areas_are_refused_by_service_and_database(env, areas):
    svc, a, _, admin, conn = env
    e = extracted_evidence(admin, a)
    with pytest.raises(InvalidReference):
        svc.classify_evidence(a, e, areas)
    with pytest.raises(InvalidReference):
        _sql(conn, a.owner_user_id, "select trace_review.classify_evidence_areas(%s, %s, %s::text[], %s)",
             (a.candidate_profile_id, e, areas, "Owned the payments roadmap"))
    assert row(admin, e)["area_classification"] is None


def test_duplicates_are_deterministic(env):
    svc, a, _, admin, _ = env
    e = extracted_evidence(admin, a)
    for areas in (["roadmap_ownership", "outcome_ownership", "roadmap_ownership"], ["outcome_ownership", "roadmap_ownership"],
                  ["roadmap_ownership", "outcome_ownership"]):
        assert svc.classify_evidence(a, e, areas) == ["outcome_ownership", "roadmap_ownership"]


def test_classification_leaves_sequencing_and_eligibility_unchanged(env, context):
    svc, a, _, admin, _ = env
    from app.services.candidate_profile import EmploymentCreate
    from tests.test_candidate_profile_integration import ONGOING, month
    j = svc.add_employment(a, EmploymentCreate(sequence_index=1, title_raw="Product Manager", employment_type="full_time",
                                               start=month(2020, 1), end=ONGOING))
    svc.map_employment(a, j)
    svc.confirm_record(a, "employment", j)
    e = svc.add_evidence(a, EvidenceCreate(evidence_type="responsibility", evidence_text="Owned the roadmap", candidate_employment_id=j))
    history, position = svc.sequence_employment_history(a, context).model_dump(), svc.current_position(a, context)
    svc.classify_evidence(a, e, ["roadmap_ownership"])
    assert svc.sequence_employment_history(a, context).model_dump() == history
    assert svc.current_position(a, context) == position


# --------------------------------------------------------------------------- direct SQL and permissions
@pytest.mark.parametrize("sql", [
    "update public.trace_candidate_evidence set area_classification = '{roadmap_ownership}' where candidate_evidence_id = %s",
    "update public.trace_candidate_evidence set area_classification_basis = 'candidate_selected' where candidate_evidence_id = %s",
    "update public.trace_candidate_evidence set area_classified_text = evidence_text where candidate_evidence_id = %s",
])
def test_direct_sql_cannot_forge_a_classification(env, sql):
    svc, a, _, admin, conn = env
    e = svc.add_evidence(a, EvidenceCreate(evidence_type="responsibility", evidence_text="Owned the roadmap"))
    with pytest.raises(PermissionDenied):
        _sql(conn, a.owner_user_id, sql, (e,))
    admin.execute("set role service_role")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            admin.execute(sql, (e,))
    finally:
        admin.execute("reset role")
    assert row(admin, e)["area_classification"] is None


def test_service_role_cannot_call_the_function(env):
    svc, a, _, admin, _ = env
    e = extracted_evidence(admin, a)
    admin.execute("set role service_role")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            admin.execute("select trace_review.classify_evidence_areas(%s, %s, '{roadmap_ownership}', 'Owned the payments roadmap')",
                          (a.candidate_profile_id, e))
    finally:
        admin.execute("reset role")


# --------------------------------------------------------------------------- staleness
def test_a_text_correction_makes_the_classification_stale(env):
    svc, a, _, admin, _ = env
    e = extracted_evidence(admin, a)
    svc.classify_evidence(a, e, ["roadmap_ownership"])
    svc.correct_record(a, "evidence", e, EvidenceCorrection(evidence_text="Owned the payments and billing roadmap"))
    rec = record(svc, a, e)
    assert (rec.area_classification_is_stale, rec.area_classification, rec.area_classified_text) == (
        True, ["roadmap_ownership"], "Owned the payments roadmap")
    svc.classify_evidence(a, e, ["roadmap_ownership"])
    assert record(svc, a, e).area_classification_is_stale is False


# --------------------------------------------------------------------------- concurrency
def test_concurrent_classifications_serialise(two_services, monkeypatch):
    s1, c1, s2, c2, svc, a, admin = two_services
    e = extracted_evidence(admin, a)
    reached, release = pause_inside(monkeypatch, "classify_evidence_areas")
    first, f_out = in_thread(lambda: s1.classify_evidence(a, e, ["roadmap_ownership"]), name=PAUSED)
    assert reached.wait(10)
    second, s_out = in_thread(lambda: s2.classify_evidence(a, e, ["outcome_ownership"]))
    try:
        assert blocked_on_lock(admin, c2.info.backend_pid, second), "the second classification did not wait for the first"
    finally:
        release.set()
    first.join(10), second.join(10)
    assert f_out == {"result": ["roadmap_ownership"]} and s_out == {"result": ["outcome_ownership"]}
    assert row(admin, e)["area_classification"] == ["outcome_ownership"]          # the later one wins, whole


def test_text_correction_racing_a_classification_never_classifies_a_text_not_seen(two_services, monkeypatch):
    s1, c1, s2, c2, svc, a, admin = two_services
    e = extracted_evidence(admin, a)
    reached, release = pause_inside(monkeypatch, "lock_evidence_state")
    classifier, c_out = in_thread(lambda: s1.classify_evidence(a, e, ["roadmap_ownership"]), name=PAUSED)
    assert reached.wait(10)
    corrector, k_out = in_thread(lambda: s2.correct_record(a, "evidence", e, EvidenceCorrection(evidence_text="Owned the billing roadmap")))
    try:
        assert blocked_on_lock(admin, c2.info.backend_pid, corrector), "the correction did not wait for the classification"
    finally:
        release.set()
    classifier.join(10), corrector.join(10)
    assert c_out == {"result": ["roadmap_ownership"]} and k_out == {"result": "user_corrected"}
    r = row(admin, e)
    assert (r["area_classified_text"], r["area_classification_is_stale"]) == ("Owned the payments roadmap", True)


def test_deletion_in_progress_then_classification_finds_nothing(two_services):
    s1, c1, s2, c2, svc, a, admin = two_services
    e = svc.add_evidence(a, EvidenceCreate(evidence_type="responsibility", evidence_text="Owned the roadmap"))
    reached, release = threading.Event(), threading.Event()

    def hold_delete():
        with owner_session(c1, a.owner_user_id):
            c1.execute("delete from public.trace_candidate_evidence where candidate_evidence_id = %s", (e,))
            reached.set()
            assert release.wait(15)
    deleter, d_out = in_thread(hold_delete)
    assert reached.wait(10)
    classifier, c_out = in_thread(lambda: s2.classify_evidence(a, e, ["roadmap_ownership"]))
    try:
        assert blocked_on_lock(admin, c2.info.backend_pid, classifier), "the classification did not wait for the deletion"
    finally:
        release.set()
    deleter.join(10), classifier.join(10)
    assert d_out == {"result": None} and isinstance(c_out.get("error"), RecordNotFound)


def test_every_classification_statement_runs_as_the_owner(env, monkeypatch):
    svc, a, _, admin, conn = env
    e = extracted_evidence(admin, a)
    seen = []
    original = psycopg.Cursor.execute

    def audited(self, query, params=None, **kw):
        text = query if isinstance(query, str) else str(query)
        if self.connection is conn and "classify_evidence_areas" in text:
            seen.append(original(self.connection.cursor(), "select current_user::text, auth.uid()").fetchone())
        return original(self, query, params, **kw)

    monkeypatch.setattr(psycopg.Cursor, "execute", audited)
    svc.classify_evidence(a, e, ["roadmap_ownership"])
    monkeypatch.undo()
    assert seen == [("trace_app_user", a.owner_user_id)]


def test_a_classification_for_a_text_not_seen_is_a_classification_conflict(env):
    from app.services.candidate_profile import ClassificationConflict
    svc, a, _, admin, conn = env
    e = extracted_evidence(admin, a)
    with pytest.raises(ClassificationConflict):
        with owner_session(conn, a.owner_user_id) as repo:
            repo.classify_evidence_areas(a.candidate_profile_id, e, ["roadmap_ownership"], "Owned the billing roadmap")
    assert row(admin, e)["area_classification"] is None


def test_the_service_refuses_before_calling_the_database(env, monkeypatch):
    """The service's own checks give the error first; the database checks the same rules again."""
    from app.services.candidate_profile.repository import CandidateProfileRepository
    svc, a, _, admin, _ = env
    e = extracted_evidence(admin, a)
    rejected = extracted_evidence(admin, a, "Rejected bullet")
    svc.reject_record(a, "evidence", rejected)

    def never(*args, **kwargs):
        raise AssertionError("the database function must not be called")
    monkeypatch.setattr(CandidateProfileRepository, "classify_evidence_areas", never)
    with pytest.raises(RecordNotEditable):
        svc.classify_evidence(a, rejected, ["roadmap_ownership"])
    for areas in ([], ["not_an_area"], ["roadmap_ownership", None]):
        with pytest.raises(InvalidReference):
            svc.classify_evidence(a, e, areas)
