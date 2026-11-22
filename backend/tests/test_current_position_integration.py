"""Current position and observed route evidence from it (M5). Local Supabase only; synthetic, transient users.

The admin connection (table owner) is used only to create a mapping no client role may create (a
low-confidence mapping set outside the M5 functions) and to check that nothing is written to pathway tables.
"""
import pytest

from app.services.candidate_profile import EmploymentCorrection, EmploymentCreate, InvalidReference
from app.services.candidate_profile.models import DateInput
from tests.test_candidate_profile_integration import ONGOING, context, env, month, settings  # noqa: F401  (fixtures)

pytestmark = pytest.mark.integration


def job(svc, a, seq, title, *, start=None, end=ONGOING, etype="full_time", primary=True, confirm=True, role=None):
    e = svc.add_employment(a, EmploymentCreate(sequence_index=seq, title_raw=title, employment_type=etype, primary_career_sequence=primary,
                                               start=start or month(2018 + seq, 1), end=end))
    if role == "auto":
        m = svc.map_employment(a, e)
        assert m.result == "mapped_automatically"
        if confirm:                         # Phase 7.2.1: the user confirms the displayed match (choose_role)
            svc.choose_role(a, e, m.role_id)
    elif role:
        svc.choose_role(a, e, role)
    if confirm:
        svc.confirm_record(a, "employment", e)
    return e


def pathway_rows(admin):
    return admin.execute("select (select count(*) from public.trace_pathway_runs) + (select count(*) from public.trace_pathway_recommendations) "
                         "+ (select count(*) from public.trace_pathway_route_steps) + (select count(*) from public.trace_pathway_evidence)").fetchone()[0]


# --------------------------------------------------------------------------- known
def test_latest_reviewed_mapped_employment_is_the_current_position(env, context):
    svc, a, *_ = env
    job(svc, a, 1, "Software Engineer", end=month(2020, 12), role="auto")
    latest = job(svc, a, 2, "Associate Product Manager", role="auto")
    p = svc.current_position(a, context)
    assert (p.status, p.reason, p.role_id, p.canonical_title, p.role_family, p.seniority_code, p.mapping_basis, p.candidate_employment_id,
            p.is_current_as_stated) == ("known", None, "PM-APM", "Associate Product Manager", "product_management", "associate",
                                        "candidate_selected", latest, True)     # confirmed by the user (Phase 7.2.1)


def test_a_candidate_decision_is_eligible_for_the_own_position(env, context):
    svc, a, *_ = env
    job(svc, a, 1, "Chief Vibes Officer", role="PM-GPM")
    p = svc.current_position(a, context)
    assert (p.status, p.role_id, p.mapping_basis) == ("known", "PM-GPM", "candidate_selected")


def test_parallel_activity_is_not_the_current_position(env, context):
    svc, a, *_ = env
    job(svc, a, 1, "Product Manager", role="auto")
    job(svc, a, 2, "Data Analyst", primary=False, role="auto")
    assert svc.current_position(a, context).role_id == "PM-PM"


# --------------------------------------------------------------------------- unknown, never an older role
def test_no_employment_history(env, context):
    svc, a, *_ = env
    p = svc.current_position(a, context)
    assert (p.status, p.reason, p.role_id) == ("unknown", "no_employment_history", None)


def test_only_rejected_employment(env, context):
    svc, a, *_ = env
    e = job(svc, a, 1, "Product Manager", confirm=False, role="auto")
    svc.reject_record(a, "employment", e)
    assert svc.current_position(a, context).reason == "no_eligible_primary_employment"


def test_newer_pending_record_blocks_an_older_eligible_role(env, context):
    svc, a, *_ = env
    job(svc, a, 1, "Product Manager", end=month(2021, 12), role="auto")
    newer = job(svc, a, 2, "Senior Product Manager", role="auto", confirm=False)
    p = svc.current_position(a, context)
    assert (p.status, p.reason, p.role_id, p.candidate_employment_id) == ("unknown", "pending_review", None, newer)
    assert "review_pending" in p.exclusion_reasons
    svc.confirm_record(a, "employment", newer)
    p = svc.current_position(a, context)                      # the job is confirmed, its automatic match is not
    assert (p.status, p.reason, p.candidate_employment_id) == ("unknown", "role_unconfirmed", newer)
    svc.choose_role(a, newer, "PM-SPM")                         # the user confirms the displayed match
    assert svc.current_position(a, context).role_id == "PM-SPM"


def test_unmapped_latest_record(env, context):
    svc, a, *_ = env
    job(svc, a, 1, "Product Manager", end=month(2021, 12), role="auto")
    newer = job(svc, a, 2, "Product Owner")                               # context-required: no automatic mapping
    p = svc.current_position(a, context)
    assert (p.status, p.reason, p.candidate_employment_id) == ("unknown", "unresolved_role_mapping", newer)


def test_stale_mapping_after_a_title_correction(env, context):
    svc, a, *_ = env
    e = job(svc, a, 1, "Product Manager", role="auto")
    assert svc.current_position(a, context).role_id == "PM-PM"
    svc.correct_record(a, "employment", e, EmploymentCorrection(title_raw="Senior Product Manager"))
    p = svc.current_position(a, context)
    assert (p.status, p.reason, p.title_raw) == ("unknown", "stale_mapping", "Senior Product Manager")
    history = svc.sequence_employment_history(a, context)
    assert not history.records[0].is_transition_eligible and history.segments == []      # cannot enter transitions
    svc.map_employment(a, e)
    assert svc.current_position(a, context).reason == "role_unconfirmed"     # a re-match needs the user again
    svc.choose_role(a, e, "PM-SPM")
    assert svc.current_position(a, context).role_id == "PM-SPM"


def test_low_confidence_automatic_mapping(env, context):
    svc, a, _, admin, _ = env
    e = job(svc, a, 1, "Product Manager")
    admin.execute("update public.trace_candidate_employments set role_id = 'PM-PM', mapping_status = 'auto_high_confidence', "
                  "mapping_confidence = 0.5 where candidate_employment_id = %s", (e,))
    assert svc.current_position(a, context).reason == "low_mapping_confidence"


@pytest.mark.parametrize("kwargs, reason", [({"etype": "part_time"}, "not_full_time_experience"),
                                            ({"start": DateInput()}, "unknown_start_date")])
def test_otherwise_ineligible_latest_record(env, context, kwargs, reason):
    svc, a, *_ = env
    job(svc, a, 1, "Product Manager", end=month(2021, 12), role="auto")
    job(svc, a, 2, "Senior Product Manager", role="auto", **kwargs)
    p = svc.current_position(a, context)
    assert (p.status, p.reason, p.role_id) == ("unknown", "latest_employment_ineligible", None)
    assert reason in p.exclusion_reasons and reason in p.explanation


# --------------------------------------------------------------------------- route evidence
def test_route_evidence_from_the_current_role(env, context):
    svc, a, _, admin, _ = env
    job(svc, a, 1, "Associate Product Manager", role="auto")
    before = pathway_rows(admin)
    ev = svc.route_evidence(a, context, "PM-PM")
    r = ev.evidence
    assert ev.current_position.role_id == "PM-APM" and r.origin.role_id == "PM-APM" and r.destination.role_id == "PM-PM"
    assert r.has_observed_pathways and r.is_synthetic_test_data and r.evidence_notice.startswith("SYNTHETIC TEST DATA")
    assert all(o.route[0] == "PM-APM" and o.route[-1] == "PM-PM" and o.steps[0].entry_change_type is None for o in r.observations)
    assert all(s.evidence_class == "synthetic_test_only" and not s.timeline_display_allowed for s in r.route_summaries)
    dumped = ev.model_dump_json()
    for ident in ("SYN-", "profile_id", "employer_id", "-ORG-", "segment_no"):                 # no reference identifiers
        assert ident not in dumped, ident
    for word in ("probab", "success", "likel", "chance"):
        assert word not in ev.note.lower()
    assert pathway_rows(admin) == before                                  # read-only


def test_empty_cohort_is_reported_honestly(env, context):
    svc, a, *_ = env
    job(svc, a, 1, "HR Operations Analyst", role="auto")
    ev = svc.route_evidence(a, context, "PM-DIR")
    assert (ev.evidence.has_observed_pathways, ev.evidence.observations, ev.evidence.route_summaries) == (False, [], [])
    assert "absence of observations" in ev.note


def test_no_evidence_without_a_current_position(env, context):
    svc, a, *_ = env
    job(svc, a, 1, "Product Owner")
    ev = svc.route_evidence(a, context, "PM-PM")
    assert (ev.evidence, ev.current_position.reason) == (None, "unresolved_role_mapping")
    assert "unresolved_role_mapping" in ev.note


@pytest.mark.parametrize("target", ["ROLE-SWE", "PM-INTERN", "PM-VP", "PM-CPO", "ROLE-UNKNOWN", "PM-NOPE"])
def test_only_the_nine_pm_targets_are_supported(env, context, target):
    svc, a, *_ = env
    job(svc, a, 1, "Product Manager", role="auto")
    with pytest.raises(InvalidReference):
        svc.route_evidence(a, context, target)
