"""Decisions D20 and D21 (presentation only).

D20: a move across role families is shown as "Moving from [source family] to [target family]", using the
taxonomy's family names supplied by the API; "A change in career direction" when a name is missing. The internal value
career_track_change, discovery, ranking and every other direction label are unchanged."""
import re
from pathlib import Path

import pytest

from app.services.candidate_profile.pathway import FamilyChange, build_pathway_result, family_change, move_direction
from app.services.reference_evidence.models import RoleRow
from tests.test_api_foundation import base_settings  # noqa: F401  (fixture)
from tests.test_pathway_result_unit import AREA_INFO, KNOWN, TARGET, candidate, evidence, exp

WEB = Path(__file__).resolve().parents[1] / "web" / "js"


def role(rid, family, rank=None, label=None, levels=True):
    return RoleRow(role_id=rid, canonical_title=rid, role_family=family, is_pm_target=family == "product_management", is_resolved=True,
                   active=True, seniority_code="x", seniority_rank=rank, family_has_levels=levels, role_family_label=label)


APM = role("PM-APM", "product_management", 30, "Product management")
PO = role("ROLE-PO", "product_ownership", None, "Product ownership", levels=False)
PM = role("PM-PM", "product_management", 40, "Product management")
SPM = role("PM-SPM", "product_management", 50, "Product management")
TITLES = {"PM-APM": "Associate Product Manager", "ROLE-PO": "Product Owner", "PM-PM": "Product Manager", "PM-SPM": "Senior Product Manager"}


def test_a_family_change_carries_both_taxonomy_names():
    assert move_direction(PO, PM) == "career_track_change"                                  # internal value unchanged
    assert family_change(PO, PM, "career_track_change") == FamilyChange(from_family="Product ownership", to_family="Product management")


@pytest.mark.parametrize("a,b", [(APM, PM), (PM, SPM)])
def test_no_family_change_is_attached_to_moves_within_a_family(a, b):
    assert family_change(a, b, move_direction(a, b)) is None


def test_a_missing_taxonomy_name_is_passed_on_as_none_for_the_fallback():
    unnamed = role("ROLE-X", "consulting", None, None, levels=False)
    assert family_change(unnamed, PM, "career_track_change") == FamilyChange(from_family=None, to_family="Product management")
    assert family_change(None, PM, "career_track_change") == FamilyChange(from_family=None, to_family="Product management")


def test_the_route_carries_one_family_change_per_move_aligned_with_its_directions():
    route = ("PM-APM", "ROLE-PO", "PM-SPM")
    ev = evidence((route, exp(route, "areas_withheld", 6, withheld=True), 6))
    result = build_pathway_result(KNOWN, TARGET, candidate(), {"PM-APM"}, ev, TITLES, AREA_INFO,
                                  role_meta={r.role_id: r for r in (APM, PO, PM, SPM)})
    (r,) = result.routes
    assert r.step_directions == ["career_track_change", "career_track_change"]
    assert [c.model_dump() if c else None for c in r.step_family_changes] == [
        {"from_family": "Product management", "to_family": "Product ownership"},
        {"from_family": "Product ownership", "to_family": "Product management"}]
    assert r.rank == 1 and r.route_id == "PM-APM>ROLE-PO>PM-SPM"                           # ranking untouched


def test_routes_within_a_family_carry_no_family_change():
    route = ("PM-APM", "PM-SPM")
    ev = evidence((route, exp(route, "areas_withheld", 6, withheld=True), 6))
    (r,) = build_pathway_result(KNOWN, TARGET, candidate(), {"PM-APM"}, ev, TITLES, AREA_INFO,
                                role_meta={r.role_id: r for r in (APM, SPM)}).routes
    assert r.step_directions == ["more_senior"] and r.step_family_changes == [None]


@pytest.mark.integration
def test_the_role_loader_returns_the_taxonomy_family_names(base_settings):
    """Every resolved family in the taxonomy has a name, and the loader returns it for each role."""
    from app import db
    from app.services.reference_evidence.repository import PostgresReferenceEvidenceRepository
    with db.connect(base_settings) as conn:
        pairs = conn.execute("select distinct on (t.role_family) t.role_id, f.label from public.trace_role_taxonomy t "
                             "join public.trace_role_families f using (role_family) where t.active and t.role_family <> 'unknown' "
                             "order by t.role_family, t.role_id").fetchall()
        roles = PostgresReferenceEvidenceRepository(conn).get_roles([rid for rid, _ in pairs])
    assert len(pairs) >= 10
    assert {rid: roles[rid].role_family_label for rid, _ in pairs} == dict(pairs)


# --------------------------------------------------------------------------- presentation (static)
def code(name):
    text = (WEB / name).read_text(encoding="utf-8")
    return re.sub(r"//[^\n]*", "", text)


def test_the_client_uses_the_api_names_and_the_approved_fallback():
    copy = code("copy.js")
    assert "`Moving from ${change.from_family} to ${change.to_family}`" in copy
    assert 'career_track_change: "A change in career direction"' in copy
    assert "Change of career track" not in copy and "career track" not in copy.lower()
    assert "Product ownership" not in copy and "Product management" not in copy                 # never a fixed phrase
    results = code("views/results.js")
    assert "familyChangeLabel((route.step_family_changes || [])[i])" in results
    assert "!d ? null" in results                                                            # unknown direction: still no label


# --------------------------------------------------------------------------- D21: contribution clarity (static)
def test_settings_never_present_a_preference_or_permission_as_a_contribution():
    s = code("views/settings.js")
    assert '"Sharing preference"' in s and '"Permission to contribute"' in s and '"Contributing now"' in s
    # "Contributing now: Yes" only for the API's eligible AND included state
    assert 'sharing.activation === "active" && sharing.status.state === "eligible" && sharing.status.included === true' in s
    assert '"No career information from your profile is currently being contributed."' in s
    assert "resume upload is switched off here" in s and "don't share anything on" in s
    assert "it doesn't contribute anything while resume upload is switched off" in s     # no "start the checks" promise here
    assert 'summary: "On"' not in s and 'summary: "Share when eligible"' in s
    for call in ("chooseSharing", "decideConsent"):                       # the page's wording changes no setting
        assert s.count(call) == {"chooseSharing": 2, "decideConsent": 0}[call]
