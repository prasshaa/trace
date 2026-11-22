"""Phase 8.2: caller-specific cohort counts, seniority-direction labels, and the shadow relevance prototype.

Integration tests use the real API and the local database (transient users; invented resumes as in Phase 6.1). The
prototype tests use evaluation fixtures only; nothing is loaded into any database.
"""
import json
import re
from pathlib import Path

import pytest

from app.services.candidate_profile.pathway import move_direction
from app.services.reference_evidence.models import RoleRow
from app.config import Settings
from app.services.contributions import process_pending
from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import PDF, candidate, consent_document, demo_client, upload  # noqa: F401  (fixtures)
from tests.test_api_phase4 import reviewed_candidate
from tests.test_contribution_safety import correct_first_evidence
from tests.test_real_resume_contributions import (  # noqa: F401  (fixtures and helpers)
    confirm_all, contribute, contribution_document, contribution_of, rc, ready_user,
)

BACKEND = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- A. caller-specific cohort counts
def real_cohort(c, h):
    r = c.get("/api/v1/cohorts", headers=h)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"       # never shared through a cache
    return next(x for x in r.json() if x["cohort"] == "consented_real")


def test_cohort_counts_exclude_the_callers_own_contribution(rc, users, base_settings):
    outsider, ho = candidate(rc, users)                                              # no contribution
    assert real_cohort(rc, ho)["status"] == "not_ready"
    contributors = []
    for _ in range(5):
        u, h = ready_user(rc, users, contributor=True)                             # opted in before uploading (D48)
        confirm_all(rc, h)
        contributors.append((u, h))
    process_pending(Settings())
    assert all(contribution_of(base_settings, u) for u, _ in contributors)
    (u1, h1), (u2, h2), (u3, h3) = contributors[:3]
    assert (real_cohort(rc, ho)["status"], real_cohort(rc, ho)["eligible_profiles"]) == ("ready", 5)       # independent for others
    mine = real_cohort(rc, h1)                                                                             # included contribution
    assert (mine["status"], mine["eligible_profiles"]) == ("insufficient", None)                           # 4 without self: suppressed
    assert set(mine) == {"cohort", "label", "is_synthetic", "disclosure", "status", "eligible_profiles", "min_cell"}   # no ids
    correct_first_evidence(rc, h2, "Meera Kulkarni ran the quarterly cards roadmap review")                # held for review
    process_pending(Settings())
    assert contribution_of(base_settings, u2) is None
    assert real_cohort(rc, h2)["status"] == "insufficient" and real_cohort(rc, ho)["status"] == "insufficient"   # 4 left for all
    contribute(rc, h3, "withdrawn")                                                                        # withdrawn
    assert real_cohort(rc, h3)["status"] == "insufficient"


# --------------------------------------------------------------------------- B. seniority direction
def role(rid, family, rank, levels=True, code="x", title=None):
    return RoleRow(role_id=rid, canonical_title=title or rid, role_family=family, is_pm_target=False, is_resolved=True, active=True,
                   seniority_code=code, seniority_rank=rank, family_has_levels=levels)


APM, PM, SPM = role("PM-APM", "pm", 30), role("PM-PM", "pm", 40), role("PM-SPM", "pm", 50)


@pytest.mark.parametrize("a,b,expected", [
    (APM, SPM, "more_senior"),                                                   # upward, same family
    (SPM, PM, "more_junior"),                                                    # downward, kept and labelled
    (PM, role("PM-PM2", "pm", 40), "lateral"),                                   # same family, same reliable level
    (role("ROLE-SWE", "swe", 40, levels=False), PM, "career_track_change"),      # across families
    (role("ROLE-ENT", "ent", None, levels=False, code="founder"), PM, "career_track_change"),   # founder: other family
    (role("PM-X", "pm", None, code="unknown"), PM, None),                        # unknown seniority
    (None, PM, None),                                                            # missing metadata
    (role("ROLE-PGM", "prog", 40, levels=False, code="mid"), role("ROLE-PROJ", "prog", 40, levels=False, code="mid"), None),  # fallback mid
    (role("ROLE-UNKNOWN", "unknown", None, levels=False), PM, None),             # incomparable (unresolved family)
])
def test_direction_policy(a, b, expected):
    assert move_direction(a, b) == expected


def test_titles_and_aliases_never_change_the_direction():
    assert move_direction(role("PM-PM", "pm", 40, title="PM II"), role("PM-SPM", "pm", 50, title="Senior PM (Payments)")) == "more_senior"


def test_the_api_labels_moves_from_the_taxonomy(demo_client, users):
    u, h = reviewed_candidate(demo_client, users, "SYN-001", classify=False)
    body = demo_client.get("/api/v1/pathway", headers=h, params={"target": "PM-SPM"}).json()
    (route,) = body["routes"]
    assert route["route_id"] == "PM-PM>PM-SPM" and route["step_directions"] == ["more_senior"]
    assert route["rank"] == 1                                                     # ranking unchanged by labels


# --------------------------------------------------------------------------- D. shadow relevance prototype
@pytest.fixture(scope="module")
def shadow():
    from evaluation.recommendation.shadow_phase82 import run_shadow
    return run_shadow()


def test_every_prototype_invariant_holds(shadow):
    assert {k: v for k, v in shadow["invariants"].items() if not v["ok"]} == {}
    assert "production_output_identical_to_phase_8_1_baseline" in shadow["invariants"]


def by_id(shadow, sid):
    return next(r for st in shadow["sets"] for r in st["scenarios"] if r["id"] == sid)


def test_different_backgrounds_get_different_signals_on_the_same_routes(shadow):
    a, b = by_id(shadow, "R01"), by_id(shadow, "R02")
    assert {x["route_id"] for x in a["production"]} == {x["route_id"] for x in b["production"]}   # same routes discovered
    rel = lambda r, rid: next(x for x in r["relevance"] if x["route_id"] == rid)["dimensions"]       # noqa: E731
    assert rel(a, "PM-PM>PM-SPM")["previous_roles"]["matched"] == [["PM-APM", 9], ["ROLE-SWE", 5]]
    assert rel(b, "PM-PM>PM-SPM")["previous_roles"]["matched"] == []
    assert rel(b, "PM-PM>ROLE-PGM>PM-SPM")["previous_roles"]["matched"] == [["ROLE-BA", 6], ["ROLE-QA", 6]]
    assert rel(a, "PM-PM>PM-SPM")["seniority"]["moves"] == ["more_senior"]
    assert rel(a, "PM-PM>ROLE-PGM>PM-SPM")["seniority"]["moves"] == ["career_track_change", "career_track_change"]


def test_missing_and_thin_data_are_never_counted_against_the_user(shadow):
    r9 = by_id(shadow, "R09")["relevance"][0]["dimensions"]["responsibilities"]
    assert r9["status"] == "not_recorded_in_profile" and "matched" not in r9
    r12 = by_id(shadow, "R12")["relevance"][0]["dimensions"]
    assert {r12[d]["status"] for d in ("previous_roles", "industries", "skills", "qualifications", "fields_of_study")} == {"insufficient_reference_evidence"}
    assert by_id(shadow, "R11")["relevance"] == []                                 # suppressed route: nothing at all


def test_own_contribution_changes_the_population_not_the_ranking(shadow):
    a, own = by_id(shadow, "R01"), by_id(shadow, "R13")
    assert [x["route_id"] for x in a["production"]] == [x["route_id"] for x in own["production"]]
    d = lambda r: next(x for x in r["relevance"] if x["route_id"] == "PM-PM>PM-SPM")             # noqa: E731
    assert (d(a)["supporting_profiles"], d(own)["supporting_profiles"]) == (9, 8)
    assert ["saas", 5] in d(a)["dimensions"]["industries"]["matched"]
    assert d(own)["dimensions"]["industries"]["status"] == "insufficient_reference_evidence"    # 4 left: below the minimum


def test_the_review_pack_hides_engine_judgments_and_labels_stay_pending(shadow):
    from evaluation.recommendation.shadow_phase82 import review_pack
    files = review_pack(shadow)
    pack = files["review_pack.md"].lower()
    for word in ("rank", "aligned", "partial", "limited", "not_established", "fit ", "tier", "matched", "relevance score"):
        assert word not in pack, word
    assert "constructed test careers, not real people" in pack
    for name in ("review_reviewer_A_pathways.csv", "review_reviewer_B_pathways.csv", "review_adjudication.csv"):
        rows = files[name].splitlines()[1:]
        assert rows and all("PENDING" in r for r in rows), name
    assert files["review_reviewer_A_pathways.csv"] == files["review_reviewer_B_pathways.csv"]   # independent, identical forms


def test_the_prototype_is_not_wired_into_production():
    app_src = "\n".join(p.read_text() for p in (BACKEND / "app").rglob("*.py"))
    assert "relevance" not in re.findall(r"^(?:from|import) [\w.]*relevance", app_src, re.M)
    assert "evaluation" not in re.findall(r"^(?:from|import) (\w+)", app_src, re.M)
    assert "route_relevance" not in app_src and "profile_facts" not in app_src


def test_the_phase_8_1_baseline_is_unchanged():
    from evaluation.recommendation.harness import run_all
    stored = json.loads((BACKEND / "evaluation" / "recommendation" / "output" / "baseline.json").read_text())
    assert json.loads(json.dumps(run_all(), sort_keys=True)) == stored
    assert stored["fixture_sha256"].startswith("2f65654c48fa62ca")                  # the Phase 8.1 fixtures, untouched


def test_route_support_counts_each_profile_once_even_if_observed_twice():
    """route_support must de-duplicate by profile itself (defence in depth): the service yields one observation per
    profile today, so this is checked directly with a duplicated observation."""
    from app.services.reference_evidence import privacy_safe_routes, route_support
    from tests.test_reference_evidence import FakeRepo, run
    from tests.test_phase81 import pool
    ev = run(FakeRepo(pool({("ROLE-QA", "PM-PM"): 4}), threshold=5.0), role="PM-PM", mode="synthetic_test", origin_role_id="ROLE-QA")
    doubled = ev.model_copy(update={"observations": ev.observations + [ev.observations[0].model_copy()]})   # same profile, another object
    assert route_support(doubled) == {("ROLE-QA", "PM-PM"): 4}
    assert privacy_safe_routes(doubled, 5.0).route_summaries == []          # 5 observations, 4 profiles: still suppressed
