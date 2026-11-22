"""Unit tests for the candidate profile service: trust boundary of content inputs, column mapping,
profile assembly and the career_history bridge. No database."""
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services.candidate_profile import (
    SYSTEM_CONTROLLED_FIELDS, ActivityCreate, ActivityUpdate, CareerBreakCreate, CorrectionNotAvailable, DateInput,
    EducationCreate, EducationUpdate, EmployerCreate, EmployerUpdate, EmploymentCreate, EmploymentUpdate,
    EvidenceCreate, InvalidRecord, InvalidTransition, RecordNotEditable, SkillCreate, assemble_profile, check_editable,
    employment_inputs, next_review_status,
)
from app.services.candidate_profile.models import ACTIVITY_TYPES, ProfileCreate, ProfileUpdate
from app.services.candidate_profile.review import check_employer_editable
from app.services.candidate_profile.repository import PROVENANCE_COLS
from app.services.candidate_profile.service import content_columns

# Every system-controlled column: provenance, verification, review status, role and category
# mapping, profile confirmation, extraction state, legacy trust columns and ownership.
TRUSTED_FIELDS = list(SYSTEM_CONTROLLED_FIELDS)

MINIMAL = {
    EmploymentCreate: dict(sequence_index=1, title_raw="PM"),
    EducationCreate: dict(),
    ActivityCreate: dict(activity_type="course", title_raw="Course"),
    EvidenceCreate: dict(evidence_type="achievement", evidence_text="Shipped a synthetic feature"),
    SkillCreate: dict(skill_text_raw="SQL"),
    CareerBreakCreate: dict(),
    EmployerCreate: dict(),
    ProfileCreate: dict(),
    ProfileUpdate: dict(),
    EmploymentUpdate: dict(),
    ActivityUpdate: dict(),
    EducationUpdate: dict(),
    EmployerUpdate: dict(),
}


# --------------------------------------------------------------------------- trust boundary
@pytest.mark.parametrize("model", list(MINIMAL))
@pytest.mark.parametrize("field", TRUSTED_FIELDS)
def test_content_inputs_reject_trusted_fields(model, field):
    value = {"verification_status": "independently_verified", "extraction_confidence": 0.9,
             "review_status": "user_confirmed", "role_id": "PM-PM", "mapping_status": "confirmed",
             "mapping_confidence": 1, "category_mapping_status": "confirmed", "category_mapping_confidence": 1,
             "confirmation_status": "participant_confirmed", "confirmed_at": "2026-11-11T00:00:00Z",
             "participant_confirmed": True, "confidence": 1}.get(field, "x")
    with pytest.raises(ValidationError):
        model(**MINIMAL[model], **{field: value})


def test_no_content_model_declares_a_trusted_field():
    import app.services.candidate_profile.models as m
    for name in dir(m):
        cls = getattr(m, name)
        if isinstance(cls, type) and name.endswith(("Create", "Update")):
            assert not set(cls.model_fields) & set(TRUSTED_FIELDS), name


# --------------------------------------------------------------------------- shapes and dates
def test_dates_must_match_precision_and_start_cannot_be_ongoing():
    with pytest.raises(ValidationError):
        DateInput(year=2020, precision="month")
    with pytest.raises(ValidationError):
        DateInput(year=1900, precision="year")
    with pytest.raises(ValidationError):
        EmploymentCreate(sequence_index=1, title_raw="PM", start={"precision": "ongoing"})
    assert DateInput().precision == "unknown"


def test_skill_needs_id_or_text_and_at_most_one_link():
    with pytest.raises(ValidationError):
        SkillCreate()
    with pytest.raises(ValidationError):
        SkillCreate(skill_text_raw="SQL", candidate_activity_id=uuid4(), candidate_education_id=uuid4())
    assert SkillCreate(skill_text_raw="Mixpanel").skill_id is None


def test_evidence_has_at_most_one_parent():
    with pytest.raises(ValidationError):
        EvidenceCreate(evidence_type="achievement", evidence_text="Shipped a thing", candidate_employment_id=uuid4(),
                       candidate_activity_id=uuid4())


# --------------------------------------------------------------------------- column mapping
def test_create_maps_dates_and_writes_no_legacy_trust_values():
    cols = content_columns("employment", EmploymentCreate(
        sequence_index=1, title_raw="Product Manager", start=DateInput(year=2020, precision="year"),
        end=DateInput(precision="ongoing")), creating=True)
    assert (cols["start_year"], cols["start_month"], cols["start_precision"]) == (2020, None, "year")
    assert (cols["end_precision"], cols["is_current_as_stated"]) == ("ongoing", True)
    assert not set(cols) & set(PROVENANCE_COLS)
    # Since v2.3.0 nothing is derived as participant_confirmed: the database sets entry_source from the
    # origin and leaves the other legacy labels NULL. Review and verification stay separate.
    legacy = {"entry_source", "provenance", "evidence_source", "attribute_provenance", "participant_confirmed"}
    for kind, payload in (("employment", EmploymentCreate(sequence_index=1, title_raw="PM")),
                          ("skill", SkillCreate(skill_text_raw="SQL")),
                          ("evidence", EvidenceCreate(evidence_type="other", evidence_text="Some stated claim")),
                          ("employer", EmployerCreate(employer_name="Synthetic Co"))):
        assert not set(content_columns(kind, payload, creating=True)) & legacy, kind


def test_update_writes_only_the_fields_the_caller_set():
    cols = content_columns("education", EducationUpdate(qualification_level="bachelor"), creating=False)
    assert cols == {"qualification_level": "bachelor"}           # the original title is untouched
    assert content_columns("employment", EmploymentUpdate(end=DateInput(year=2022, month=3, precision="month")),
                           creating=False) == {"end_year": 2022, "end_month": 3, "end_precision": "month",
                                               "is_current_as_stated": False}
    with pytest.raises(InvalidRecord):
        content_columns("education", EducationUpdate(), creating=False)


# --------------------------------------------------------------------------- assembly
def _prov(origin="user_stated"):
    return dict(information_origin=origin, review_status="pending_review", verification_status="unverified",
                extraction_confidence=None, source_resume_id=None, extraction_run_id=None, source_location=None)


def _date(prefix, y=None, m=None, p="unknown"):
    return {f"{prefix}_year": y, f"{prefix}_month": m, f"{prefix}_precision": p}


def _empty_rows(pid):
    return {"profile": [dict(candidate_profile_id=pid, headline=None, country_code="IN", confirmation_status="pending",
                             **_date("resume_as_of"))],
            "employers": [], "employments": [], "education": [], "activities": [], "evidence": [], "skills": [],
            "career_breaks": []}


def test_empty_profile_has_every_category():
    p = assemble_profile(_empty_rows(uuid4()))
    assert (p.employment_history, p.education, p.skills, p.unattached_evidence, p.career_breaks) == ([], [], [], [], [])
    assert set(p.career_activities) == set(ACTIVITY_TYPES) and all(v == [] for v in p.career_activities.values())
    dumped = p.model_dump()
    for key in ("employment_history", "education", "career_activities", "skills", "unattached_evidence", "career_breaks"):
        assert key in dumped


def test_assembly_keeps_supporting_records_out_of_employment_and_preserves_legacy_origin():
    pid, emp, edu, act = uuid4(), uuid4(), uuid4(), uuid4()
    rows = _empty_rows(pid)
    rows["employments"] = [dict(candidate_employment_id=emp, sequence_index=1, candidate_employer_id=None, title_raw="PM II",
                                role_id="PM-PM", normalised_title="Product Manager", role_family="product_management",
                                seniority_code=None, mapping_status="confirmed", mapping_confidence=Decimal(1), mapping_note=None,
                                employment_type="full_time", primary_career_sequence=True, counts_as_full_time_experience=None,
                                classification_note=None, **_date("start", 2020, 1, "month"), **_date("end", None, None, "ongoing"),
                                is_current_as_stated=True, entry_source="manual_entry", participant_confirmed=False, **_prov())]
    rows["education"] = [dict(candidate_education_id=edu, qualification_title_raw="B.Tech (CSE)", qualification_level="bachelor",
                              institution_name=None, field_of_study=None, study_mode="unknown", completion_status="completed",
                              notes=None, **_date("start"), **_date("end"), **_prov(origin=None))]
    rows["activities"] = [dict(candidate_activity_id=act, activity_type="volunteering", title_raw="Mentor", category_code=None,
                               category_mapping_status="unresolved", category_mapping_confidence=None, organisation_raw=None,
                               description_raw=None, contribution_raw=None, outcome_raw=None, link_url=None, status="active",
                               related_employment_id=None, credential_identifier_raw=None, metadata={},
                               **_date("start", 2021, None, "year"), **_date("end", None, None, "ongoing"), **_date("expiry"), **_prov())]
    p = assemble_profile(rows)
    assert p.employment_history[0].title_raw == "PM II" and p.employment_history[0].normalised_title == "Product Manager"
    assert p.education[0].provenance.information_origin is None          # not recorded, never upgraded
    assert p.education[0].start.lo is None and p.career_activities["volunteering"][0].start.hi_label == "2021-12"
    assert [e.employment_id for e in employment_inputs(p)] == [str(emp)]


# --------------------------------------------------------------------------- candidate-owned classification is kept
def test_candidates_still_classify_their_own_records():
    for t in ACTIVITY_TYPES:                                   # project, certification, licence, course, ...
        assert ActivityCreate(activity_type=t, title_raw="Synthetic").activity_type == t
    assert ActivityUpdate(activity_type="portfolio_project", status="completed").status == "completed"
    assert EmploymentCreate(sequence_index=1, title_raw="PM", employment_type="freelance",
                            primary_career_sequence=False).employment_type == "freelance"
    assert EducationCreate(qualification_level="mba", study_mode="executive").qualification_level == "mba"


def test_candidate_employment_keeps_the_default_unmapped_role():
    cols = content_columns("employment", EmploymentCreate(sequence_index=1, title_raw="Senior PM"), creating=True)
    assert not {"role_id", "seniority_code", "mapping_status", "mapping_confidence", "mapping_note"} & set(cols)
    cols = content_columns("activity", ActivityCreate(activity_type="course", title_raw="C"), creating=True)
    assert not {"category_code", "category_mapping_status", "category_mapping_confidence"} & set(cols)


# --------------------------------------------------------------------------- review actions (pure rules)
STATUSES = ("pending_review", "user_confirmed", "user_corrected", "rejected")
EXPECTED = {   # (status, action) -> resulting status; None = no-op; "error" = InvalidTransition
    ("pending_review", "confirm"): "user_confirmed", ("user_confirmed", "confirm"): None,
    ("user_corrected", "confirm"): None, ("rejected", "confirm"): "error",
    ("pending_review", "reject"): "rejected", ("user_confirmed", "reject"): "rejected",
    ("user_corrected", "reject"): "rejected", ("rejected", "reject"): None,
    ("pending_review", "reinstate"): "error", ("user_confirmed", "reinstate"): "error",
    ("user_corrected", "reinstate"): "error", ("rejected", "reinstate"): "pending_review",
}


@pytest.mark.parametrize("status,action", list(EXPECTED))
def test_review_transition_matrix(status, action):
    expected = EXPECTED[(status, action)]
    if expected == "error":
        with pytest.raises(InvalidTransition):
            next_review_status(status, action)
    else:
        assert next_review_status(status, action) == expected


def test_no_action_produces_user_corrected_or_returns_rejected_to_confirmed():
    for status in STATUSES:
        for action in ("confirm", "reject", "reinstate"):
            try:
                target = next_review_status(status, action)
            except InvalidTransition:
                continue
            assert target != "user_corrected"
            assert not (status == "rejected" and target == "user_confirmed")
    with pytest.raises(InvalidTransition):
        next_review_status("pending_review", "unconfirm")
    with pytest.raises(InvalidTransition):
        next_review_status("pending_review", "set_status")


def test_only_unreviewed_user_stated_drafts_are_editable():
    check_editable("user_stated", "pending_review")              # allowed
    with pytest.raises(RecordNotEditable) as exc:
        check_editable("user_stated", "rejected")
    assert not isinstance(exc.value, CorrectionNotAvailable)     # reinstate first, not a correction
    for status in ("user_confirmed", "user_corrected"):
        with pytest.raises(CorrectionNotAvailable):
            check_editable("user_stated", status)
    for origin in ("source_extracted", "inferred", None):         # would overwrite an original
        with pytest.raises(CorrectionNotAvailable):
            check_editable(origin, "pending_review")


def test_employer_edits_only_while_candidate_entered_and_unreviewed():
    check_employer_editable(None, [])                                 # candidate-entered since v2.3.0
    check_employer_editable(None, ["pending_review"])
    with pytest.raises(CorrectionNotAvailable):
        check_employer_editable(None, ["user_confirmed"])
    check_employer_editable("participant_confirmed", [])             # legacy candidate-entered
    check_employer_editable("participant_confirmed", ["pending_review", "pending_review"])
    for statuses in (["user_confirmed"], ["pending_review", "rejected"], ["user_corrected"]):
        with pytest.raises(CorrectionNotAvailable):
            check_employer_editable("participant_confirmed", statuses)
    for prov in ("resume_stated", "inferred_unverified", "unknown"):
        with pytest.raises(CorrectionNotAvailable):
            check_employer_editable(prov, [])


def test_employment_inputs_drop_rejected_and_carry_review_status():
    pid = uuid4()
    rows = _empty_rows(pid)
    ids = [uuid4() for _ in range(4)]
    statuses = ["user_confirmed", "rejected", "pending_review", "user_corrected"]
    rows["employments"] = [dict(candidate_employment_id=i, sequence_index=n, candidate_employer_id=None, title_raw="PM",
                                role_id="PM-PM", normalised_title="Product Manager", role_family="product_management",
                                seniority_code=None, mapping_status="confirmed", mapping_confidence=Decimal(1),
                                mapping_note=None, employment_type="full_time", primary_career_sequence=True,
                                counts_as_full_time_experience=None, classification_note=None,
                                **_date("start", 2020, None, "year"), **_date("end", 2020, None, "year"),
                                is_current_as_stated=False, entry_source="manual_entry", participant_confirmed=False,
                                **{**_prov(), "review_status": st})
                           for n, (i, st) in enumerate(zip(ids, statuses), start=1)]
    inputs = employment_inputs(assemble_profile(rows))
    assert [e.employment_id for e in inputs] == [str(ids[0]), str(ids[2]), str(ids[3])]
    assert [e.review_status for e in inputs] == ["user_confirmed", "pending_review", "user_corrected"]
    assert not any(e.participant_confirmed for e in inputs)      # legacy flag is no longer derived


def test_the_only_status_fields_candidates_supply_are_record_attributes():
    """`status` (career activities) and `completion_status` (education) describe the candidate's own
    record. No processing, trust, review or verification status is candidate input."""
    import app.services.candidate_profile.models as m
    found = {(name, f) for name in dir(m) if name.endswith(("Create", "Update"))
             for f in getattr(m, name).model_fields if f.endswith("status")}
    assert found == {("ActivityCreate", "status"), ("ActivityUpdate", "status"),
                     ("EducationCreate", "completion_status"), ("EducationUpdate", "completion_status")}
    assert not {"status", "completion_status"} & set(SYSTEM_CONTROLLED_FIELDS)


# --------------------------------------------------------------------------- corrections (M4)
def test_correction_matrix_and_correctable_states():
    from app.services.candidate_profile.review import check_correctable
    assert {s: next_review_status(s, "correct") for s in ("pending_review", "user_confirmed", "user_corrected")} == {
        "pending_review": "user_corrected", "user_confirmed": "user_corrected", "user_corrected": "user_corrected"}
    with pytest.raises(InvalidTransition):
        next_review_status("rejected", "correct")
    for origin, status in (("source_extracted", "pending_review"), ("inferred", "pending_review"), (None, "pending_review"),
                           ("user_stated", "user_confirmed"), ("source_extracted", "user_corrected")):
        check_correctable(origin, status)
    with pytest.raises(RecordNotEditable):
        check_correctable("source_extracted", "rejected")
    with pytest.raises(CorrectionNotAvailable):
        check_correctable("user_stated", "pending_review")      # the candidate's own draft is edited instead


def test_correction_models_carry_only_correctable_content():
    from app.services.candidate_profile.models import CORRECTION_MODELS
    forbidden = set(SYSTEM_CONTROLLED_FIELDS) | {"candidate_employer_id", "related_employment_id", "candidate_employment_id",
                                                 "candidate_activity_id", "candidate_education_id", "candidate_evidence_id",
                                                 "skill_id", "responsibility_area"}
    assert set(CORRECTION_MODELS) == {"employment", "education", "activity", "evidence", "skill", "career_break"}
    for kind, model in CORRECTION_MODELS.items():
        assert not set(model.model_fields) & forbidden, kind
        with pytest.raises(ValidationError):
            model(unknown_field="x")
