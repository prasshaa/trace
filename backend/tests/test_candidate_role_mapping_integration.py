"""Database tests for trusted role mapping and candidate role decisions (M5, data foundation v2.6.0).

Synthetic, transient users only (fixtures from test_candidate_profile_integration.py). The admin
connection (table owner) is used only to create records in states no client role may create and to
inspect rows.
"""
import threading
from decimal import Decimal

import psycopg
import pytest

from app.services.candidate_profile import (
    ClarificationRequired, EmploymentCorrection, EmploymentCreate, EmploymentUpdate, InvalidReference, MappingConflict,
    PermissionDenied, RecordNotEditable, RecordNotFound, owner_session,
)
from tests.test_candidate_corrections_integration import _extracted_employment, _sql
from tests.test_candidate_profile_integration import (  # noqa: F401  (fixtures)
    ONGOING, PAUSED, blocked_on_lock, context, env, in_thread, month, pause_inside, settings, two_services, year,
)

pytestmark = pytest.mark.integration


def _mapping(admin, e):
    return admin.execute(
        "select role_id, seniority_code, mapping_status::text, mapping_confidence, role_mapping_basis, role_mapping_alias_id, "
        "mapped_title_raw, role_mapping_ownership_areas, mapping_is_stale, review_status::text "
        "from public.trace_candidate_employments where candidate_employment_id = %s", (e,)).fetchone()


def _draft(svc, a, title, seq=1):
    return svc.add_employment(a, EmploymentCreate(sequence_index=seq, title_raw=title, employment_type="full_time",
                                                  start=month(2020, 1), end=ONGOING))


def _record(history, e):
    return next(r for r in history.records if r.stated.employment_id == str(e))


# --------------------------------------------------------------------------- automatic mapping
def test_unique_alias_maps_automatically_without_touching_review(env, context):
    svc, a, _, admin, _ = env
    e, _ = _extracted_employment(admin, a, "Product Manager")
    r = svc.map_employment(a, e)
    assert (r.result, r.role_id, r.options.automatic_alias_id) == ("mapped_automatically", "PM-PM", "ALS-0003")
    assert _mapping(admin, e) == ("PM-PM", None, "auto_high_confidence", Decimal("0.950"), "alias_match", "ALS-0003",
                                  "Product Manager", None, False, "pending_review")
    rec = svc.get_profile(a).employment_history[0]
    assert (rec.role_mapping_basis, rec.role_mapping_alias_id, rec.mapped_title_raw, rec.title_raw) == (
        "alias_match", "ALS-0003", "Product Manager", "Product Manager")
    # Mapping never bypasses review: the pending record is still a barrier.
    reasons = _record(svc.sequence_employment_history(a, context), e).exclusion_reasons
    assert "review_pending" in reasons and "mapping_not_confirmed" not in reasons
    svc.confirm_record(a, "employment", e)
    assert _record(svc.sequence_employment_history(a, context), e).is_transition_eligible


def test_seniority_hint_and_threshold_confidence(env, context):
    svc, a, _, admin, _ = env
    e, _ = _extracted_employment(admin, a, "Senior Software Engineer")
    assert svc.map_employment(a, e).role_id == "ROLE-SWE"
    assert _mapping(admin, e)[1] == "senior"
    low, _ = _extracted_employment(admin, a, "Account Manager")          # alias confidence 0.80 = min_mapping_confidence
    svc.map_employment(a, low)
    svc.confirm_record(a, "employment", low)
    rec = _record(svc.sequence_employment_history(a, context), low)
    assert rec.stated.mapping_confidence == 0.8 and "low_mapping_confidence" not in rec.exclusion_reasons


@pytest.mark.parametrize("title, outcome, alias_roles", [
    ("Product Owner", "decision_required", ["ROLE-OWNER", "PM-PM"]),
    ("PM", "decision_required", ["PM-PM", "ROLE-PROJ"]),
    ("Program Management", "decision_required", ["ROLE-PGM"]),
    ("Associate", "unresolved", []),                                   # only ROLE-UNKNOWN: treated as unresolved
    ("Product Manager (Payments)", "unresolved", []),
])
def test_context_required_and_unmatched_titles_are_not_mapped(env, title, outcome, alias_roles):
    svc, a, _, admin, _ = env
    e, _ = _extracted_employment(admin, a, title)
    r = svc.map_employment(a, e)
    assert (r.result, r.role_id) == (outcome, None)
    assert [c.role_id for c in r.options.alias_options] == alias_roles
    assert _mapping(admin, e)[:3] == ("ROLE-UNKNOWN", None, "unresolved")
    roles = [c.role_id for c in r.options.alias_options + r.options.other_roles]
    assert "ROLE-UNKNOWN" not in roles and len(roles) == len(set(roles))
    assert r.options.alias_options + r.options.other_roles and all(c.source == "taxonomy" for c in r.options.other_roles)


# --------------------------------------------------------------------------- candidate decisions
def test_candidate_selects_from_alias_options_or_the_full_taxonomy(env, context):
    svc, a, _, admin, _ = env
    e, _ = _extracted_employment(admin, a, "PM")
    assert svc.choose_role(a, e, "ROLE-PROJ") == "ROLE-PROJ"
    assert _mapping(admin, e)[:8] == ("ROLE-PROJ", None, "confirmed", None, "candidate_selected", None, "PM", None)
    u, _ = _extracted_employment(admin, a, "Chief Vibes Officer")
    assert svc.choose_role(a, u, "PM-GPM") == "PM-GPM"
    # A candidate decision has no confidence and is not held to the threshold; it applies to the own sequence.
    svc.confirm_record(a, "employment", u)
    rec = _record(svc.sequence_employment_history(a, context), u)
    assert rec.stated.mapping_confidence is None and rec.stated.mapping_basis == "candidate_selected"
    assert rec.is_transition_eligible


@pytest.mark.parametrize("role", ["ROLE-UNKNOWN", "ROLE-NOPE", "", "PM-RETIRED"])
def test_ineligible_roles_are_rejected(env, role):
    svc, a, _, admin, _ = env
    e, _ = _extracted_employment(admin, a, "Associate")
    with pytest.raises(InvalidReference):
        svc.choose_role(a, e, role)


def test_retired_role_is_rejected_by_service_and_database(env):
    svc, a, _, admin, conn = env
    e, _ = _extracted_employment(admin, a, "Associate")
    admin.execute("update public.trace_role_taxonomy set active = false where role_id = 'ROLE-OPS'")
    try:
        with pytest.raises(InvalidReference):
            svc.choose_role(a, e, "ROLE-OPS")
        with pytest.raises(InvalidReference):        # the database refuses it even when the service is bypassed
            _sql(conn, a.owner_user_id, "select trace_review.choose_employment_role(%s, %s, 'ROLE-OPS', 'Associate', null)",
                 (a.candidate_profile_id, e))
        assert "ROLE-OPS" not in [c.role_id for c in svc.role_mapping_options(a, e).other_roles]
    finally:
        admin.execute("update public.trace_role_taxonomy set active = true where role_id = 'ROLE-OPS'")


def test_product_owner_to_pm_needs_ownership_clarification(env):
    svc, a, _, admin, conn = env
    e, _ = _extracted_employment(admin, a, "Product Owner")
    opts = svc.role_mapping_options(a, e)
    flags = {c.role_id: c.requires_ownership_clarification for c in opts.alias_options + opts.other_roles}
    assert flags["PM-PM"] and flags["PM-SPM"] and not flags["ROLE-OWNER"] and not flags["ROLE-SWE"]
    assert opts.pm_ownership_areas == ["outcome_ownership", "prioritisation_backlog", "product_discovery", "roadmap_ownership"]
    with pytest.raises(ClarificationRequired):
        svc.choose_role(a, e, "PM-PM")
    with pytest.raises(ClarificationRequired):     # the database enforces it too
        _sql(conn, a.owner_user_id, "select trace_review.choose_employment_role(%s, %s, 'PM-PM', 'Product Owner', null)",
             (a.candidate_profile_id, e))
    with pytest.raises(InvalidReference):
        svc.choose_role(a, e, "PM-PM", ["stakeholder_management"])
    assert svc.choose_role(a, e, "PM-PM", ["roadmap_ownership", "product_discovery"]) == "PM-PM"
    assert _mapping(admin, e)[7] == ["product_discovery", "roadmap_ownership"]
    assert svc.choose_role(a, e, "ROLE-OWNER") == "ROLE-OWNER"
    other, _ = _extracted_employment(admin, a, "Chief Vibes Officer")
    with pytest.raises(InvalidReference):
        svc.choose_role(a, other, "PM-PM", ["roadmap_ownership"])


def test_automatic_mapping_keeps_candidate_and_external_decisions(env):
    svc, a, _, admin, _ = env
    e, _ = _extracted_employment(admin, a, "Product Manager")
    svc.map_employment(a, e)
    assert svc.choose_role(a, e, "PM-APM") == "PM-APM"                   # "None of these"
    r = svc.map_employment(a, e)
    assert (r.result, r.role_id, _mapping(admin, e)[4]) == ("candidate_decision_kept", "PM-APM", "candidate_selected")
    legacy, _ = _extracted_employment(admin, a, "Data Analyst")
    admin.execute("update public.trace_candidate_employments set role_id = 'ROLE-DS', mapping_status = 'confirmed', mapping_confidence = 1 "
                  "where candidate_employment_id = %s", (legacy,))
    assert svc.map_employment(a, legacy).result == "existing_mapping_kept"
    assert _mapping(admin, legacy)[0] == "ROLE-DS"


# --------------------------------------------------------------------------- review states and ownership
def test_review_states(env):
    svc, a, _, admin, _ = env
    pending, _ = _extracted_employment(admin, a, "Product Manager")
    confirmed, _ = _extracted_employment(admin, a, "Business Analyst")
    corrected, _ = _extracted_employment(admin, a, "Data Analyst")
    rejected, _ = _extracted_employment(admin, a, "Product Manager")
    svc.confirm_record(a, "employment", confirmed)
    svc.correct_record(a, "employment", corrected, EmploymentCorrection(title_raw="Data Scientist"))
    svc.reject_record(a, "employment", rejected)
    for e, role in ((pending, "PM-PM"), (confirmed, "ROLE-BA"), (corrected, "ROLE-DS")):
        before = _mapping(admin, e)[9]
        assert svc.map_employment(a, e).role_id == role
        assert _mapping(admin, e)[9] == before                             # review status never changes
    for op in (lambda: svc.map_employment(a, rejected), lambda: svc.choose_role(a, rejected, "PM-PM")):
        with pytest.raises(RecordNotEditable):
            op()
    svc.reinstate_record(a, "employment", rejected)
    assert svc.map_employment(a, rejected).result == "mapped_automatically"


def test_candidates_map_only_their_own_records(env):
    svc, a, b, admin, conn = env
    e, _ = _extracted_employment(admin, a, "Product Manager")
    for op in (lambda: svc.map_employment(b, e), lambda: svc.choose_role(b, e, "PM-PM"), lambda: svc.role_mapping_options(b, e)):
        with pytest.raises(RecordNotFound):
            op()
    with pytest.raises(RecordNotFound):
        _sql(conn, b.owner_user_id, "select trace_review.apply_alias_mapping(%s, %s, 'ALS-0003', 'Product Manager')",
             (a.candidate_profile_id, e))
    assert _mapping(admin, e)[0] == "ROLE-UNKNOWN"


# --------------------------------------------------------------------------- direct SQL
@pytest.mark.parametrize("sql", [
    "update public.trace_candidate_employments set role_id = 'PM-DIR', mapping_status = 'confirmed', mapping_confidence = 1 where candidate_employment_id = %s",
    "update public.trace_candidate_employments set role_mapping_basis = 'candidate_selected' where candidate_employment_id = %s",
    "update public.trace_candidate_employments set mapped_title_raw = 'x' where candidate_employment_id = %s",
    "update public.trace_candidate_employments set role_mapping_ownership_areas = '{roadmap_ownership}' where candidate_employment_id = %s",
])
def test_direct_sql_cannot_forge_mapping_fields(env, sql):
    svc, a, _, admin, conn = env
    e = _draft(svc, a, "Product Manager")
    with pytest.raises(PermissionDenied):
        _sql(conn, a.owner_user_id, sql, (e,))
    admin.execute("set role service_role")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            admin.execute(sql, (e,))
    finally:
        admin.execute("reset role")


def test_database_refuses_forged_automatic_mappings(env):
    svc, a, _, admin, conn = env
    e = _draft(svc, a, "Product Owner")
    for alias, title in (("ALS-0035", "Product Owner"), ("ALS-0003", "Product Owner")):
        with pytest.raises(MappingConflict):
            _sql(conn, a.owner_user_id, "select trace_review.apply_alias_mapping(%s, %s, %s, %s)", (a.candidate_profile_id, e, alias, title))
    with pytest.raises(MappingConflict):                                  # TR018: not the record's title
        _sql(conn, a.owner_user_id, "select trace_review.apply_alias_mapping(%s, %s, 'ALS-0003', 'Product Manager')", (a.candidate_profile_id, e))


# --------------------------------------------------------------------------- stale mappings
def test_title_correction_makes_the_mapping_stale_until_a_fresh_decision(env, context):
    svc, a, _, admin, _ = env
    e, _ = _extracted_employment(admin, a, "Product Manager")
    svc.map_employment(a, e)
    svc.confirm_record(a, "employment", e)
    assert _record(svc.sequence_employment_history(a, context), e).is_transition_eligible
    svc.correct_record(a, "employment", e, EmploymentCorrection(title_raw="Senior Product Manager"))
    rec = svc.get_profile(a).employment_history[0]
    assert (rec.mapping_is_stale, rec.role_id, rec.mapped_title_raw) == (True, "PM-PM", "Product Manager")
    s = _record(svc.sequence_employment_history(a, context), e)
    assert "mapping_stale" in s.exclusion_reasons and not s.is_transition_eligible
    r = svc.map_employment(a, e)                                       # a fresh mapping for the corrected title
    assert (r.result, r.role_id) == ("mapped_automatically", "PM-SPM")
    assert _record(svc.sequence_employment_history(a, context), e).is_transition_eligible


def test_stale_candidate_decision_needs_a_fresh_decision(env, context):
    svc, a, _, admin, _ = env
    e = _draft(svc, a, "Chief Vibes Officer")
    svc.choose_role(a, e, "PM-PM")
    svc.update_employment(a, e, EmploymentUpdate(title_raw="Head of Vibes"))       # draft edit
    assert svc.map_employment(a, e).result == "unresolved"               # no alias: not silently re-mapped
    assert _mapping(admin, e)[8] is True
    svc.confirm_record(a, "employment", e)
    assert "mapping_stale" in _record(svc.sequence_employment_history(a, context), e).exclusion_reasons
    svc.choose_role(a, e, "PM-LEAD")
    assert _record(svc.sequence_employment_history(a, context), e).is_transition_eligible


# --------------------------------------------------------------------------- concurrency
def test_title_correction_racing_a_mapping_never_maps_a_title_not_seen(two_services, monkeypatch):
    s1, c1, s2, c2, svc, a, admin = two_services
    e, _ = _extracted_employment(admin, a, "Product Manager")
    reached, release = pause_inside(monkeypatch, "lock_mapping_state")
    mapper, m_out = in_thread(lambda: s1.map_employment(a, e), name=PAUSED)
    assert reached.wait(10)
    corrector, c_out = in_thread(lambda: s2.correct_record(a, "employment", e, EmploymentCorrection(title_raw="Senior Product Manager")))
    try:
        assert blocked_on_lock(admin, c2.info.backend_pid, corrector), "the correction did not wait for the mapping"
    finally:
        release.set()
    mapper.join(10), corrector.join(10)
    assert m_out["result"].role_id == "PM-PM" and c_out == {"result": "user_corrected"}
    row = _mapping(admin, e)
    assert (row[0], row[6], row[8]) == ("PM-PM", "Product Manager", True)   # mapped for the title it saw; now stale


def test_mapping_in_progress_then_record_deletion(two_services, monkeypatch):
    s1, c1, s2, c2, svc, a, admin = two_services
    e, _ = _extracted_employment(admin, a, "Product Manager")
    reached, release = pause_inside(monkeypatch, "apply_alias_mapping")
    mapper, m_out = in_thread(lambda: s1.map_employment(a, e), name=PAUSED)
    assert reached.wait(10)
    deleter, d_out = in_thread(lambda: _sql(c2, a.owner_user_id, "delete from public.trace_candidate_employments where candidate_employment_id = %s", (e,)))
    try:
        assert blocked_on_lock(admin, c2.info.backend_pid, deleter), "the deletion did not wait for the mapping"
    finally:
        release.set()
    mapper.join(10), deleter.join(10)
    assert m_out["result"].result == "mapped_automatically" and d_out == {"result": None}
    assert _mapping(admin, e) is None


def test_deletion_in_progress_then_decision_finds_nothing(two_services):
    s1, c1, s2, c2, svc, a, admin = two_services
    e, _ = _extracted_employment(admin, a, "Product Manager")
    reached, release = threading.Event(), threading.Event()

    def hold_delete():
        with owner_session(c1, a.owner_user_id):
            c1.execute("delete from public.trace_candidate_employments where candidate_employment_id = %s", (e,))
            reached.set()
            assert release.wait(15)
    deleter, d_out = in_thread(hold_delete)
    assert reached.wait(10)
    chooser, ch_out = in_thread(lambda: s2.choose_role(a, e, "PM-SPM"))
    try:
        assert blocked_on_lock(admin, c2.info.backend_pid, chooser), "the decision did not wait for the deletion"
    finally:
        release.set()
    deleter.join(10), chooser.join(10)
    assert d_out == {"result": None} and isinstance(ch_out.get("error"), RecordNotFound)


def test_every_mapping_statement_runs_as_the_owner(env, monkeypatch):
    svc, a, _, admin, conn = env
    e, _ = _extracted_employment(admin, a, "Product Owner")
    seen = []
    original = psycopg.Cursor.execute

    def audited(self, query, params=None, **kw):
        text = query if isinstance(query, str) else str(query)
        if self.connection is conn and ("apply_alias_mapping" in text or "choose_employment_role" in text):
            seen.append(original(self.connection.cursor(), "select current_user::text, auth.uid()").fetchone())
        return original(self, query, params, **kw)

    monkeypatch.setattr(psycopg.Cursor, "execute", audited)
    svc.choose_role(a, e, "ROLE-OWNER")
    monkeypatch.undo()
    assert seen == [("trace_app_user", a.owner_user_id)]


def test_service_refuses_before_calling_the_database(env, monkeypatch):
    """The service's own checks give the error first; the database checks the same rules again."""
    from app.services.candidate_profile.repository import CandidateProfileRepository
    svc, a, _, admin, _ = env
    owner, _ = _extracted_employment(admin, a, "Product Owner")

    def never(*args, **kwargs):
        raise AssertionError("the database function must not be called")
    monkeypatch.setattr(CandidateProfileRepository, "choose_employment_role", never)
    with pytest.raises(ClarificationRequired):
        svc.choose_role(a, owner, "PM-PM")
    for role in ("ROLE-UNKNOWN", "ROLE-NOPE"):
        with pytest.raises(InvalidReference):
            svc.choose_role(a, owner, role)
    with pytest.raises(InvalidReference):
        svc.choose_role(a, owner, "PM-PM", ["stakeholder_management"])
