"""Unit tests for M5 role-mapping decisions and candidate-side sequencing rules. No database."""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services.career_history import sequence_candidate_history
from app.services.candidate_profile.service import eligible_role, needs_ownership_clarification, role_mapping_options
from app.services.role_mapping import match_title
from tests.career_history_support import emp, foundation_context, requires_foundation
from tests.test_role_mapping_matcher import csv_index

pytestmark = requires_foundation

ROWS, INDEX, _ = csv_index()


def taxonomy():
    import csv
    from tests.career_history_support import FOUNDATION
    return {r["role_id"]: {"role_id": r["role_id"], "canonical_title": r["canonical_title"], "role_family": r["role_family"],
                           "is_resolved": r["is_resolved"] == "true", "active": r["active"] == "true"}
            for r in csv.DictReader(open(FOUNDATION / "data/taxonomy/role_taxonomy.csv", encoding="utf-8"))}


TAX = taxonomy()
AREAS = ["outcome_ownership", "prioritisation_backlog", "product_discovery", "roadmap_ownership"]
STATE = {"role_id": "ROLE-UNKNOWN", "mapping_status": "unresolved", "role_mapping_basis": None, "mapping_is_stale": False}


def options(title, tax=TAX):
    return role_mapping_options(uuid4(), {**STATE, "title_raw": title}, match_title(title, INDEX), tax, AREAS)


# --------------------------------------------------------------------------- options
def test_automatic_title_proposes_its_alias():
    o = options("Product Manager")
    assert (o.outcome, o.automatic_role_id, o.automatic_alias_id, str(o.automatic_confidence)) == ("automatic", "PM-PM", "ALS-0003", "0.95")
    assert [c.role_id for c in o.alias_options] == ["PM-PM"] and "PM-PM" not in [c.role_id for c in o.other_roles]


def test_decision_lists_alias_options_first_then_every_other_eligible_role():
    o = options("PM")
    assert o.outcome == "decision_required" and o.automatic_role_id is None
    assert [(c.role_id, c.source, c.alias_id) for c in o.alias_options] == [("PM-PM", "alias_option", "ALS-0004"), ("ROLE-PROJ", "alias_option", "ALS-0005")]
    eligible = sorted(r for r, t in TAX.items() if t["active"] and t["is_resolved"])
    assert sorted(c.role_id for c in o.alias_options + o.other_roles) == eligible
    assert all(c.source == "taxonomy" and c.alias_id is None for c in o.other_roles)


@pytest.mark.parametrize("title", ["Associate", "Analyst", "Assistant Manager", "Junior Analyst", "Technical Associate"])
def test_alias_with_only_role_unknown_is_unresolved(title):
    o = options(title)
    assert (o.outcome, o.alias_options) == ("unresolved", [])
    assert "ROLE-UNKNOWN" not in [c.role_id for c in o.other_roles]


def test_unmatched_title_is_unresolved_with_the_full_taxonomy():
    o = options("Product Manager - Payments")
    assert o.outcome == "unresolved" and o.other_roles and all(eligible_role(TAX[c.role_id]) for c in o.other_roles)


def test_retired_and_unresolved_roles_are_never_offered():
    tax = {**TAX, "ROLE-OPS": {**TAX["ROLE-OPS"], "active": False}}
    roles = [c.role_id for c in options("Program Management", tax).other_roles]
    assert "ROLE-OPS" not in roles and "ROLE-UNKNOWN" not in roles
    assert not eligible_role(None) and not eligible_role(TAX["ROLE-UNKNOWN"])


def test_product_owner_clarification_flags():
    o = options("Product Owner")
    flags = {c.role_id: c.requires_ownership_clarification for c in o.alias_options + o.other_roles}
    assert flags["PM-PM"] and flags["PM-DIR"] and not flags["ROLE-OWNER"] and not flags["ROLE-PROJ"]
    assert o.pm_ownership_areas == AREAS
    assert needs_ownership_clarification(match_title("Senior Product Owner", INDEX), "PM-SPM", TAX)
    assert not needs_ownership_clarification(match_title("Product Manager", INDEX), "PM-SPM", TAX)
    # Qualified Product Owner titles need the clarification too (no alias matches them).
    for title in ("Product Owner (Payments)", "PRODUCT OWNER - Growth", "Senior Product Owner, Payments", "(Product Owner)",
                  "Product Owner/Scrum Master", "Lead\u00a0Product\tOwner"):
        assert needs_ownership_clarification(match_title(title, INDEX), "PM-PM", TAX), title
        assert not needs_ownership_clarification(match_title(title, INDEX), "ROLE-OWNER", TAX), title
    for title in ("Product Ownership Lead", "Product-Owner", "Productowner", "Product Manager", "Owner, Product Analytics"):
        assert not needs_ownership_clarification(match_title(title, INDEX), "PM-PM", TAX), title


# --------------------------------------------------------------------------- candidate-side sequencing inputs
def test_confidence_may_be_missing_only_for_a_candidate_decision():
    base = emp("C", 1, "PM-PM", "2020-01", "2021-01")
    with pytest.raises(ValidationError):
        base.model_copy(update={"mapping_confidence": None}).model_validate(base.model_dump() | {"mapping_confidence": None})
    with pytest.raises(ValidationError):
        type(base).model_validate(base.model_dump() | {"mapping_basis": "candidate_selected", "mapping_status": "auto_high_confidence"})
    ok = type(base).model_validate(base.model_dump() | {"mapping_basis": "candidate_selected", "mapping_confidence": None})
    assert ok.mapping_confidence is None


def test_candidate_decision_skips_the_threshold_but_alias_matches_do_not():
    ctx = foundation_context()
    a = emp("C", 1, "PM-APM", "2019-01", "2020-01", conf=0.5, org="A")
    b = emp("C", 2, "PM-PM", "2020-02", "2021-01", org="A")
    chosen = type(a).model_validate(a.model_dump() | {"mapping_basis": "candidate_selected", "mapping_confidence": None,
                                                     "mapping_status": "confirmed", "employer_id": "A"})
    low_alias = type(a).model_validate(a.model_dump() | {"mapping_basis": "alias_match", "mapping_status": "auto_high_confidence",
                                                        "employer_id": "A"})
    h = sequence_candidate_history([chosen, b], ctx)
    assert h.records[0].is_transition_eligible and h.transitions[0].transition_status == "confirmed"
    h = sequence_candidate_history([low_alias, b], ctx)
    assert "low_mapping_confidence" in h.records[0].exclusion_reasons


def test_stale_mapping_is_a_barrier():
    ctx = foundation_context()
    a = emp("C", 1, "PM-APM", "2019-01", "2020-01")
    b = emp("C", 2, "PM-PM", "2020-02", "2021-01")
    c = emp("C", 3, "PM-SPM", "2021-02", "2022-01")
    stale = type(b).model_validate(b.model_dump() | {"mapping_basis": "alias_match", "mapping_is_stale": True, "employer_id": b.employer_id})
    h = sequence_candidate_history([a, stale, c], ctx)
    assert h.records[1].exclusion_reasons == ["mapping_stale"]
    assert [t.transition_status for t in h.transitions] == ["unconfirmed", "unconfirmed"]        # never A -> C


def test_reference_inputs_are_unchanged():
    e = emp("C", 1, "PM-PM", "2020-01", "2021-01")
    assert (e.mapping_basis, e.mapping_is_stale) == (None, False)


def test_qualified_product_owner_title_is_offered_with_clarification_but_never_mapped_automatically():
    o = options("Product Owner (Payments)")
    assert (o.outcome, o.automatic_role_id, o.alias_options) == ("unresolved", None, [])
    flags = {c.role_id: c.requires_ownership_clarification for c in o.other_roles}
    assert flags["PM-PM"] and flags["PM-SPM"] and not flags["ROLE-OWNER"]


def test_an_automatic_alias_to_product_management_for_a_product_owner_title_becomes_a_decision():
    from app.services.role_mapping import AliasIndex, AliasRow
    extra = AliasRow(alias_id="ALS-9901", alias="Product Owner Payments", alias_normalized="product owner payments", role_id="PM-PM",
                     seniority_hint_code=None, context_required=False, default_confidence=ROWS[0].default_confidence, mapping_rule=None,
                     role_default_seniority_code="mid", role_is_resolved=True, role_active=True)
    index = AliasIndex(ROWS + [extra])
    m = match_title("Product Owner Payments", index)
    assert m.outcome == "automatic"                                   # the matcher alone would map it
    o = role_mapping_options(uuid4(), {**STATE, "title_raw": "Product Owner Payments"}, m, TAX, AREAS)
    assert (o.outcome, o.automatic_role_id) == ("decision_required", None)
    assert o.alias_options[0].requires_ownership_clarification
