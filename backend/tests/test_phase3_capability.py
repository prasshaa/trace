"""Product Phase 3: target-role capability assessment, posting eligibility metadata and the approved pm-v1 pack.

Pure tests use the APPROVED mapping from data-foundation/knowledge_packs/pm-v1.1.json, so they check the real rules.
API tests create transient users through the product's own manual-entry and review endpoints; all values are invented.
"""
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.services.capability.assess import Requirement, assess, interpret_qualification
from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import candidate, consent_document, make_client  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parents[2]
PACK = json.loads((ROOT / "data-foundation/knowledge_packs/pm-v1.1.json").read_text(encoding="utf-8"))
REQS = [Requirement(key=r["key"], requirement_class=r["requirement_class"], applies_to_level=r["applies_to_level"], title=r["title"],
                    description=r["description"], level_notes=r.get("level_notes") or {}, contexts=tuple(r["contexts"]),
                    direct_areas=tuple(r["direct_areas"]), transferable_areas=tuple(r["transferable_areas"]),
                    direct_skills=tuple(r["direct_skills"]), transferable_skills=tuple(r["transferable_skills"]),
                    transferable_role_families=tuple(r["transferable_role_families"]), transfer_rationale=r.get("transfer_rationale"),
                    gap_basis=r["gap_basis"], area_conditions=r.get("area_conditions") or {}) for r in PACK["requirements"]]


# --------------------------------------------------------------------------- pure fixtures (duck-typed profile)
def ev(text, areas=None, status="user_confirmed", stale=False, none=False, quals=None, version=2):
    """areas=None: unclassified; none=True: the explicit "None of these areas" (an empty area list)."""
    return NS(candidate_evidence_id=uuid.uuid4(), evidence_text=text, area_classification=[] if none else areas,
              area_classification_is_stale=stale, area_classification_none=none, area_qualifiers=quals or {},
              area_classification_taxonomy_version=version if (areas is not None or none) else None,
              provenance=NS(review_status=status))


def skill(sid=None, text=None, status="user_confirmed"):
    return NS(candidate_skill_id=uuid.uuid4(), taxonomy=NS(skill_id=sid, canonical_skill=sid.lower()) if sid else None,
              skill_text_raw=text, provenance=NS(review_status=status))


def job(family="software_engineering", status="user_confirmed", evidence=()):
    return NS(candidate_employment_id=uuid.uuid4(), title_raw="Role", role_family=family, mapping_is_stale=False, mapping_status="confirmed",
              provenance=NS(review_status=status), responsibilities=list(evidence), achievements=[], other_evidence=[])


def profile(evidence=(), skills=(), jobs=None):
    jobs = jobs if jobs is not None else [job(evidence=evidence)]
    return NS(employment_history=jobs, career_activities={}, unattached_evidence=[], skills=list(skills), education=[])


def states(p, level="pm", contexts=(), names=None):
    outcomes, cov = assess(p, REQS, level, contexts, names)
    return {o.key: o for o in outcomes}, cov


# --------------------------------------------------------------------------- the four states
def test_demonstrated_from_reviewed_classified_evidence():
    s, _ = states(profile([ev("Ran customer interviews", ["product_discovery"])]))
    assert (s["C1"].state, s["C1"].basis) == ("demonstrated", "classified_evidence")
    assert s["C1"].supporting[0].matched == ["product_discovery"] and s["C1"].supporting[0].excerpt == "Ran customer interviews"


def test_demonstrated_from_a_confirmed_skill_is_labelled_as_such():
    s, _ = states(profile([ev("x", ["technical_engineering"])], skills=[skill("SKL-USER-RESEARCH")]))
    assert (s["C1"].state, s["C1"].basis) == ("demonstrated", "confirmed_skill")


def test_transferable_needs_the_curated_mapping_and_returns_its_rationale():
    s, _ = states(profile([ev("Managed key enterprise accounts", ["customer_account_management"])]))
    assert (s["C1"].state, s["C1"].basis) == ("transferable", "classified_evidence")
    assert "Account management" in s["C1"].transfer_rationale


def test_transferable_from_role_history_only_where_mapped():
    p = profile([ev("Built APIs", ["delivery_execution"])], jobs=[job("software_engineering", evidence=[ev("Built APIs", ["delivery_execution"])])])
    s, _ = states(p, contexts=["platform_api"])
    assert (s["X3"].state, s["X3"].basis) == ("transferable", "role_history")     # engineering role -> technical depth (mapped)
    assert s["C1"].state == "gap"                                                  # an engineering role says nothing about discovery


def test_gap_only_when_the_reviewed_profile_is_fully_classified():
    complete = profile([ev("Built payment APIs", ["technical_engineering"])])
    s, cov = states(complete)
    assert cov.complete and (s["C1"].state, s["C1"].reason) == ("gap", "reviewed_and_classified_without_a_match")
    pending = profile([ev("Built payment APIs", ["technical_engineering"]), ev("Draft", None, status="pending_review")])
    s, cov = states(pending)
    assert (s["C1"].state, s["C1"].reason, cov.complete) == ("unknown", "evidence_pending_review", False)
    # Phase 5 (D50): a confirmed line the user hasn't categorised is READ by Trace, so it no longer blocks the assessment.
    # A line that shows nothing relevant still never counts; one that does counts, with its cue kept.
    unclassified = profile([ev("Built payment APIs", ["technical_engineering"]), ev("Talked to users", None)])
    s, cov = states(unclassified)
    assert cov.complete and (s["C1"].state, s["C1"].reason) == ("gap", "read_without_a_match")
    s, _ = states(profile([ev("Built payment APIs", ["technical_engineering"]), ev("Interviewed 12 shop owners about stock-outs", None)]))
    assert (s["C1"].state, s["C1"].basis) == ("demonstrated", "interpreted_evidence")
    assert s["C1"].supporting[0].read_by == "trace" and s["C1"].supporting[0].cue
    s, _ = states(profile([]))
    assert (s["C1"].state, s["C1"].reason) == ("unknown", "no_reviewed_evidence")


def test_absence_never_makes_a_gap_where_it_is_not_inferable():
    s, cov = states(profile([ev("Built payment APIs", ["technical_engineering"])]), contexts=["regulated_domain"], level="senior_pm")
    assert cov.complete
    assert (s["X4"].state, s["X4"].reason) == ("unknown", "not_inferable_from_absence")      # domain stays skill-based (pm-v1.1)


def test_unreviewed_rejected_and_stale_records_never_count():
    s, _ = states(profile([ev("Ran interviews", ["product_discovery"], status="pending_review")]))
    assert s["C1"].state == "unknown"
    s, _ = states(profile([ev("Ran interviews", ["product_discovery"], status="rejected"), ev("x", ["technical_engineering"])]))
    assert s["C1"].state == "gap"                                                  # rejected evidence ignored entirely
    s, _ = states(profile([ev("Ran interviews", ["product_discovery"], stale=True)]))
    # text changed after classifying: the stale categorisation is never used. Phase 5 (D50): Trace reads the CURRENT text,
    # which doesn't show discovery work on its own, so the capability is not demonstrated (and never from the old choice)
    assert (s["C1"].state, s["C1"].reason) == ("gap", "read_without_a_match")
    s, _ = states(profile([ev("x", ["technical_engineering"])], skills=[skill("SKL-USER-RESEARCH", status="pending_review")]))
    assert s["C1"].state == "gap"


def test_context_dependent_requirements_apply_only_in_a_selected_context():
    p = profile([ev("Built payment APIs", ["technical_engineering"])])
    s, _ = states(p)
    assert all(s[k].state == "not_applicable" and s[k].reason == "not_in_selected_context" for k in ("X1", "X2", "X3", "X4", "X5", "X6", "X7"))
    s, _ = states(p, contexts=["platform_api"])
    assert s["X3"].state == "demonstrated" and s["X1"].state == "gap" and s["X2"].state == "not_applicable"


def test_senior_differentiators_only_for_a_senior_target():
    p = profile([ev("Led a team of analysts", ["people_leadership"])])
    s, _ = states(p, level="pm")
    assert not any(k.startswith("L") for k in s)
    s, _ = states(p, level="senior_pm")
    assert {k for k in s if k.startswith("L")} == {"L1", "L2", "L3", "L4"}
    assert (s["L2"].state, s["L2"].reason) == ("unknown", "scope_question_not_answered")    # generic leadership is not enough
    coached = profile([ev("Mentored two junior PMs", ["people_leadership"], quals={"people_leadership": {"coaching_mentoring": True}})])
    assert states(coached, level="senior_pm")[0]["L2"].state == "demonstrated"


def test_typed_skills_count_only_on_an_exact_taxonomy_name():
    names = {"sql": ("SKL-SQL", "sql"), "user research": ("SKL-USER-RESEARCH", "user research")}
    p = profile([ev("x", ["technical_engineering"])], skills=[skill(text="User  Research"), skill(text="research of users")])
    s, _ = states(p, names=names)
    assert (s["C1"].state, s["C1"].basis) == ("demonstrated", "confirmed_skill") and len(s["C1"].supporting) == 1
    near = profile([ev("x", ["technical_engineering"])], skills=[skill(text="Advanced SQL reporting")])
    s, _ = states(near, contexts=["growth_analytics"], names=names)
    assert s["X1"].state == "gap"                                                  # a near-match is not the skill "sql"


def test_no_scores_weights_or_percentages_anywhere_in_the_result():
    s, _ = states(profile([ev("Ran interviews", ["product_discovery"])]), level="senior_pm", contexts=list({c for r in REQS for c in r.contexts}))
    for o in s.values():
        assert set(vars(o)) == {"key", "state", "basis", "reason", "supporting", "transfer_rationale", "blocking_records",
                                "needs_context"}
    src = (ROOT / "backend/app/services/capability/assess.py").read_text() + (ROOT / "backend/app/services/capability/service.py").read_text()
    assert not re.search(r"\b(score|weight|percent|probabilit|readiness)\w*\s*[=:]", src, re.I)


def test_unknown_contexts_are_refused():
    with pytest.raises(ValueError):
        assess(profile([]), REQS, "pm", ["consulting"])


@pytest.mark.parametrize("stored,title,expected", [
    ("mba", "MBA", ("master", "MBA")),                 # D11: MBA is a type at master's level
    ("mba", "PGDM", ("master", "MBA")),
    ("master", "M.Tech Computer Science", ("master", "MTech")),
    ("bachelor", "B.Tech", ("bachelor", "BTech")),
    ("bachelor", "Some degree", ("bachelor", None)),   # never guessed
    (None, None, ("unknown", None)),
])
def test_mba_is_read_as_a_masters_level_qualification_type(stored, title, expected):
    assert interpret_qualification(stored, title) == expected


def test_the_pack_mapping_supports_every_core_capability_from_the_taxonomy():
    for r in REQS:
        if r.requirement_class == "core":
            assert r.direct_areas and r.gap_basis == "reviewed_classified_evidence", r.key
        if r.transferable_areas or r.transferable_skills or r.transferable_role_families:
            assert r.transfer_rationale, r.key


# --------------------------------------------------------------------------- API, two materially different users
@pytest.fixture
def c(base_settings, consent_document):
    with make_client(base_settings) as client:
        yield client


def build(c, h, title, employer, lines, skills=()):
    """Manual entry, then the user's own classification and confirmation of every record (the product flow)."""
    assert c.post("/api/v1/entries/employment", headers=h, json={"title_raw": title, "employer_name": employer,
                  "employment_type": "full_time", "start": {"year": 2020, "precision": "year"}, "end": {"precision": "ongoing"},
                  "responsibilities": [t for t, _ in lines], "skills": list(skills), "confirm": True}).status_code == 201
    view = c.get("/api/v1/profile/records", headers=h).json()
    areas = dict(lines)
    for x in view["employments"][0]["evidence"]:
        assert c.put(f"/api/v1/evidence/{x['id']}/classification", headers=h, json={"areas": areas[x["evidence_text"]]}).status_code == 200


def assess_api(c, h, target="PM-PM", contexts=()):
    q = f"/api/v1/capability-assessment?target={target}" + "".join(f"&context={x}" for x in contexts)
    r = c.get(q, headers=h)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()


SWE = ("Software Engineer", "Invented SaaS Co", [("Built and maintained payment APIs used by enterprise customers", ["technical_engineering"]),
                                                  ("Shipped fortnightly releases and handled production incidents", ["delivery_execution"])], ["APIs"])
BA = ("Business Analyst", "Invented Fintech Ltd", [("Gathered requirements from merchants for settlement features", ["requirements_analysis"]),
                                                    ("Ran workshops with operations and finance stakeholders", ["stakeholder_management"]),
                                                    ("Built SQL reports on reconciliation failures for leadership", ["data_analysis"])], ["SQL"])


def test_two_different_backgrounds_get_materially_different_assessments(c, users):
    _, hs = candidate(c, users)
    _, hb = candidate(c, users)
    build(c, hs, *SWE)
    build(c, hb, *BA)
    s = {r["key"]: r for r in assess_api(c, hs, contexts=["platform_api"])["requirements"]}
    b = {r["key"]: r for r in assess_api(c, hb, contexts=["platform_api"])["requirements"]}
    assert {k: s[k]["state"] for k in s} == {
        "C1": "gap", "C2": "gap", "C3": "transferable", "C4": "gap", "C5": "transferable", "C6": "demonstrated",
        "X1": "gap", "X2": "not_applicable", "X3": "demonstrated", "X4": "not_applicable", "X5": "not_applicable",
        "X6": "not_applicable", "X7": "not_applicable"}
    assert {k: b[k]["state"] for k in b} == {
        # pm-v1.2 (D35): SQL reports with the outcome-metrics question unanswered, plus a SQL skill, carry over to C4 but don't demonstrate it
        "C1": "transferable", "C2": "gap", "C3": "demonstrated", "C4": "transferable", "C5": "demonstrated", "C6": "gap",
        "X1": "demonstrated", "X2": "not_applicable", "X3": "gap", "X4": "not_applicable", "X5": "not_applicable",
        "X6": "not_applicable", "X7": "not_applicable"}
    assert s["X3"]["supporting"][0]["matched"] == ["Technical engineering"]           # taxonomy labels, the user's own record
    assert b["C1"]["transfer_rationale"] and b["C1"]["basis"] == "classified_evidence"
    fin = {r["key"]: r for r in assess_api(c, hb, contexts=["regulated_domain"])["requirements"]}
    assert fin["X4"]["state"] == "not_applicable" or fin["X4"]["supporting"] == [] or all(
        not m.startswith("SKL-") for sup in fin["X4"]["supporting"] for m in sup["matched"])
    build_skill = c.post("/api/v1/entries/skill", headers=hb, json={"skill_text_raw": "Fintech", "confirm": True})
    assert build_skill.status_code == 201
    fin = {r["key"]: r for r in assess_api(c, hb, contexts=["regulated_domain"])["requirements"]}
    assert (fin["X4"]["state"], fin["X4"]["basis"], fin["X4"]["supporting"][0]["matched"]) == ("demonstrated", "confirmed_skill", ["fintech"])


def test_unknown_until_the_user_reviews_and_classifies(c, users):
    _, h = candidate(c, users)
    assert all(r["state"] == "unknown" and r["reason"] == "no_reviewed_evidence"
               for r in assess_api(c, h)["requirements"] if r["requirement_class"] == "core")
    c.post("/api/v1/entries/employment", headers=h, json={"title_raw": "Analyst", "responsibilities": ["Wrote weekly reports"], "confirm": True})
    out = assess_api(c, h)
    # Phase 5 (D50): the confirmed line is read by Trace. It shows none of the core capabilities, which is shown as
    # "not yet demonstrated in the available evidence", never as unknown-until-categorised and never as a deficit.
    assert out["coverage"]["complete"] and out["coverage"]["unclassified_reviewed_evidence"] == 1
    core = [r for r in out["requirements"] if r["requirement_class"] == "core"]
    assert not [r for r in core if r["state"] == "demonstrated"]
    assert {r["display_state"] for r in core} <= {"not_yet_demonstrated", "needs_context"}


def test_senior_target_adds_differentiators_and_other_targets_are_not_covered(c, users):
    _, h = candidate(c, users)
    build(c, h, *BA)
    keys = [r["key"] for r in assess_api(c, h, target="PM-SPM")["requirements"]]
    assert {"L1", "L2", "L3", "L4"} <= set(keys)
    assert not any(k.startswith("L") for k in [r["key"] for r in assess_api(c, h)["requirements"]])
    r = c.get("/api/v1/capability-assessment?target=PM-LEAD", headers=h)
    assert (r.status_code, r.json()["error"]) == (422, "target_not_covered")
    assert c.get("/api/v1/capability-assessment?target=PM-PM&context=consulting", headers=h).status_code == 422


def test_provenance_every_requirement_is_attributed_and_only_own_records_are_shown(c, users):
    _, ha = candidate(c, users)
    _, hb = candidate(c, users)
    build(c, ha, *SWE)
    build(c, hb, *BA)
    a = assess_api(c, ha, target="PM-SPM", contexts=["platform_api", "data_product", "ai_product", "commercial_product",
                                                     "regulated_domain", "growth_analytics"])
    own = {x["id"] for e in c.get("/api/v1/profile/records", headers=ha).json()["employments"] for x in e["evidence"]}
    for r in a["requirements"]:
        assert r["sources"], r["key"]
        for s in r["sources"]:
            assert s["url"].startswith("https://") and s["attribution"] and s["stance"] in ("supports", "qualifies", "disagrees", "silent")
            assert "sfia" not in s["url"].lower()
        for sup in r["supporting"]:
            if sup["kind"] == "evidence":
                assert sup["record_id"] in own                                        # never another user's record
    j12 = next(s for r in a["requirements"] for s in r["sources"] if s["key"] == "J12")
    assert j12["status"] == "expired"                                                 # dated evidence, never current
    text = json.dumps(a)
    for leak in ("SYN-", "EVAL-", "entry_background_stance", "min_years", "excluded_backgrounds", "Invented Fintech"):
        assert leak not in text, leak                                                 # no synthetic data, no employer criteria, no other user


def test_mba_is_interpreted_without_rewriting_the_stored_record(c, users):
    u, h = candidate(c, users)
    assert c.post("/api/v1/entries/education", headers=h, json={"qualification_title_raw": "MBA", "qualification_level": "mba",
                                                                "field_of_study": "Business", "confirm": True}).status_code == 201
    (ed,) = assess_api(c, h)["education"]
    assert (ed["stored_level"], ed["level"], ed["qualification_type"]) == ("mba", "master", "MBA")
    assert users.admin.execute("select qualification_level from public.trace_candidate_education where owner_user_id = %s",
                               (u,)).fetchone()[0] == "mba"                            # unchanged in the database


def test_posting_metadata_is_separate_dated_and_revalidated(c, users, base_settings):
    _, h = candidate(c, users)
    r = c.get("/api/v1/knowledge/postings", headers=h)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    posts = {p["source_key"]: p for p in r.json()["postings"]}
    assert len(posts) == 12 and all(p["last_verified_at"] for p in posts.values())
    assert (posts["J12"]["is_current_opportunity"], posts["J12"]["evidence_use"]) == (False, "historical_eligibility_evidence")
    assert posts["J12"]["entry_background_stance"] == "excludes_specified_transitions"
    assert posts["J11"]["entry_background_stance"] == "accepts_adjacent" and posts["J11"]["accepted_backgrounds"]
    assert {k: p["entry_background_stance"] for k, p in posts.items() if k in ("J1", "J6")} == {"J1": "not_specified", "J6": "not_specified"}
    from app import db
    from app.services.capability import CapabilityService
    from app.services.candidate_profile import CandidateProfileService, OwnerContext
    owner = uuid.UUID(json.loads(__import__("base64").urlsafe_b64decode(h["Authorization"].split(".")[1] + "=="))["sub"])
    with db.connect_runtime(base_settings) as conn:
        ctx = OwnerContext(owner_user_id=owner, candidate_profile_id=CandidateProfileService(conn).owner_profile_id(owner))
        fresh = {p["source_key"]: p for p in CapabilityService(conn).postings(ctx, at=datetime(2026, 11, 20, tzinfo=timezone.utc))}
        stale = {p["source_key"]: p for p in CapabilityService(conn).postings(ctx, at=datetime(2026, 12, 1, tzinfo=timezone.utc))}
    assert sum(p["is_current_opportunity"] for p in fresh.values()) == 11                       # verified within 14 days
    assert not any(p["is_current_opportunity"] for p in stale.values())                        # D26: older than 14 days
    assert {p["evidence_use"] for k, p in stale.items() if k != "J12"} == {"requires_revalidation"}
    assert stale["J12"]["evidence_use"] == "historical_eligibility_evidence"                   # never confused with a stale one


def test_the_assessment_reads_no_reference_profiles_and_no_evaluation_code():
    src = "\n".join(p.read_text() for p in (ROOT / "backend/app/services/capability").glob("*.py"))
    assert "trace_ref." not in src and "evaluation" not in re.findall(r"^(?:from|import) (\w+)", src, re.M)
    assert "anthropic" not in src.lower() and "openai" not in src.lower() and "embedding" not in src.lower()
