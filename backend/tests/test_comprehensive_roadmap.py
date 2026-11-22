"""Comprehensive redesign: the detailed layer of the career transition roadmap (experience themes, development plans
with verified learning resources, sequencing, stage guidance) and the returning-user entry point.

API tests use transient users and invented records only; content tests check the curated learning pack.
"""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from app.services.transition import learning
from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import candidate, consent_document  # noqa: F401  (fixtures)
from tests.test_phase4_transition import ONGOING, add_job, c, month, plan, swe  # noqa: F401  (fixtures)
from tests.test_redesign_roadmap import business_analyst, goal, lines, roadmap

PACK = learning.pack()


def _dump(name, data):
    out = os.environ.get("TRACE_REPORT_DUMP")
    if out:
        Path(out, f"{name}.json").write_text(json.dumps(data, indent=1, default=str))


# --------------------------------------------------------------------------- the learning pack itself
def test_every_resource_has_verified_facts_and_no_invented_price_or_length():
    for rid, r in PACK["resources"].items():
        for k in ("title", "provider", "type", "url", "access", "access_note", "level", "topics", "outcomes", "why", "verified_on"):
            assert r.get(k), (rid, k)
        assert urlparse(r["url"]).scheme in ("https", "http") and urlparse(r["url"]).netloc, rid
        assert r["access"] in ("free", "paid", "free_enrolment_paid_certificate"), rid
        assert not re.search(r"[$₹£€]\s?\d|\bUSD\b|\bINR\b", json.dumps(r)), rid          # no prices recorded
        if r["published_length"]:
            assert isinstance(r["published_length"]["hours"], (int, float)) and "listed" in r["published_length"]["note"]
        if "gov.uk" in r["url"]:
            assert r["licence"] == "Open Government Licence v3.0 (Crown copyright)"


def test_guides_reference_only_catalogued_resources_and_label_personal_projects():
    for key, g in PACK["guides"].items():
        for m in g["learn"]:
            assert m["method"] in PACK["methods"] and all(r in PACK["resources"] for r in m["resources"]), key
        assert g["practice"]["default"] and g["output"]["good_contains"], key
        text = json.dumps(g["practice"]["default"]).lower()
        assert ("personal project" in text or "learning exercise" in text or g["recommended_first"] == "workplace"
                or key in ("C5", "C6")), key                                                # non-work practice is always labelled
    assert all(r in PACK["resources"] for r in PACK["stage_guidance"]["4"]["resources"])


def test_pack_language_never_forecasts_or_judges():
    body = json.dumps(PACK).lower()
    for bad in ("you will become", "guarantee", "probability", "typically takes", "% of", "you lack", "not ready", "unsuitable"):
        assert bad not in body, bad


def test_internships_only_without_confirmed_employment():
    item = [{"key": "C1", "title": "Users", "state": "gap"}]
    working = learning.development_plans(item, None, "software_engineering", None, True, {}, None, {})[0]
    early = learning.development_plans(item, None, None, None, False, {}, None, {})[0]
    assert "internship" not in [a["method"] for a in working["alternatives"]]
    assert "internship" in [a["method"] for a in early["alternatives"]]


def test_study_time_is_arithmetic_on_a_published_length_only():
    r = learning._resource("coursera-uva-dpm-fundamentals", "4_6")
    assert r["study_time"].startswith("About 2–3 weeks at 4–6 hours a week") and "not a prediction" in r["study_time"]
    assert learning._resource("mom-test", "4_6")["study_time"] is None                     # no published length: nothing
    assert learning._resource("coursera-uva-dpm-fundamentals", None)["study_time"] is None


# --------------------------------------------------------------------------- experience synthesis
def test_experience_is_synthesised_into_themes_with_each_record_once(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    rm = roadmap(c, h, plan(c, h)["run_id"])
    exp = rm["experience"]
    assert exp["themes"] and exp["limitation"]
    t = exp["themes"][0]
    assert t["title"] == "Technical delivery and execution"
    assert t["summary"] == "Your experience as a Software Engineer includes delivering and releasing work with your team and building technical systems."
    for th in exp["themes"]:
        assert "Shipped weekly" not in th["summary"] and "categorised" not in th["summary"]     # no quotes, no labels
    ids = [r["id"] for r in exp["records"]]
    assert len(ids) == len(set(ids))                                                         # each record once
    assert set(ids) <= {x["id"] for x in lines(c, h).values()}


def test_rejected_records_never_feed_the_synthesis(c, users):
    _, h = candidate(c, users)
    business_analyst(c, h)
    run = plan(c, h)["run_id"]
    metric = lines(c, h)["Defined success metrics for the refund flow and tracked them"]
    assert c.post(f"/api/v1/records/evidence/{metric['id']}/reject", headers=h).status_code == 200
    exp = roadmap(c, h, run)["experience"]
    assert metric["id"] not in {r["id"] for r in exp["records"]}
    assert "owning outcomes" not in json.dumps(exp)


# --------------------------------------------------------------------------- development plans
def test_software_engineer_development_plans_are_detailed_and_personalised(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    run = plan(c, h, contexts=["platform_api"])["run_id"]
    goal(c, h, timeframe="within_12_months", weekly_hours="4_6")
    rm = roadmap(c, h, run)
    _dump("swe_12_months", rm)
    keys = [p["key"] for p in rm["development"]]
    assert keys[:2] == ["C2", "C4"] and len(keys) <= 3                                       # decision rights first
    for p in rm["development"]:
        if not p["curated"]:
            continue
        assert p["concept"] and p["subtopics"] and p["why"] and p["learn"] and p["practice"] and p["output"]["good_contains"]
        assert p["practice_for"] == "engineering" and p["practice"] == PACK["guides"][p["key"]]["practice"]["engineering"]
        assert p["recommended_first"]["reason"] and p["alternatives"]
        assert all(r["verified_on"] and r["url"] for m in p["learn"] for r in m["resources"])
        assert p["bridge_note"] and p["bridge_note"]["text"]                               # how the bridge role helps
        if p["key"] in ("C2", "C4"):
            assert p["bridge_note"]["seek"] and p["bridge_note"]["still_to_build"]             # partial development made practical
    c4 = next(p for p in rm["development"] if p["key"] == "C4")
    assert {r["id"] for m in c4["learn"] for r in m["resources"]} >= {"govuk-performance-metrics", "udacity-ab-testing"}


def test_sequence_says_what_to_start_with_what_waits_and_the_main_risk(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    run = plan(c, h)["run_id"]
    goal(c, h, timeframe="within_6_months", weekly_hours="1_3")
    s = roadmap(c, h, run)["sequence"]
    assert s["start_now"] and s["in_parallel"] and s["before_next"] and s["can_wait"]
    assert "two moves" in s["main_risk"] and "Associate Product Manager" in s["main_risk"]  # short goal + bridge route
    assert s["weekly_advice"].startswith("With about 1–3 hours a week")
    goal(c, h, timeframe="within_18_24_months")
    assert "two moves" not in roadmap(c, h, run)["sequence"]["main_risk"]


def test_stage_guidance_for_repositioning_and_hiring(c, users):
    _, h = candidate(c, users)
    swe(c, h)
    g = roadmap(c, h, plan(c, h)["run_id"])["stage_guidance"]
    assert g["3"]["case_study"] and g["3"]["never_claim"] and g["3"]["resume"]
    assert g["4"]["interviews"] and g["4"]["product_case"] and g["4"]["resources"][0]["title"] == "Cracking the PM Interview"
    assert not any(e in json.dumps(g) for e in ("Razorpay", "Sarvam", "Paytm"))


def test_business_analyst_gets_analysis_practice_and_different_priorities(c, users):
    _, h = candidate(c, users)
    business_analyst(c, h)
    rm = roadmap(c, h, plan(c, h)["run_id"])
    keys = [p["key"] for p in rm["development"]]
    assert "C4" not in keys and keys[0] == "C2"                                              # outcome metrics already shown
    assert rm["development"][0]["practice_for"] == "analysis"
    assert any(t["key"] in ("data", "requirements", "direction") for t in rm["experience"]["themes"])


# --------------------------------------------------------------------------- returning users
def test_returning_user_finds_their_latest_plan(c, users):
    _, h = candidate(c, users)
    assert c.get("/api/v1/transition-plans/latest", headers=h).status_code == 204          # "none yet" is not an error
    swe(c, h)
    plan(c, h, target="PM-SPM")
    p = plan(c, h, target="PM-PM")
    latest = c.get("/api/v1/transition-plans/latest", headers=h).json()
    assert (latest["run_id"], latest["target_role_id"]) == (p["run_id"], "PM-PM")
    _, other = candidate(c, users)
    assert c.get("/api/v1/transition-plans/latest", headers=other).status_code == 204       # never another user's
