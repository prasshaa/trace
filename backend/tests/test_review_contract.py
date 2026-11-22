"""Phase 4: the review's role contract, the starting role, and confirmation reliability (D49).

Local Supabase, transient users, FICTIONAL resumes only. Confirmation reliability is tested separately from parser
accuracy: these tests prove what the review accepts, refuses and stores, not how well a resume was read.
"""
import logging

import pytest

from evaluation.resume_parser.corpus import P
from evaluation.resume_parser.regression import R
from evaluation.resume_parser.render import render
from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import candidate, consent_document, make_client  # noqa: F401  (fixtures)
from tests.test_resume_import_api import complete, records, up

pytestmark = pytest.mark.integration
M = lambda y, m: {"year": y, "month": m, "precision": "month"}          # noqa: E731
NOW = {"precision": "ongoing"}


@pytest.fixture
def on(base_settings, consent_document):
    with make_client(base_settings, trace_real_resume_uploads=True) as client:
        yield client


def staged(c, users, persona=None, layout="pdf_classic"):
    _, h = candidate(c, users)
    v = up(c, h, render(persona or P[0], layout)).json()["import"]
    return h, v


def check(c, h, v, edits):
    r = c.post(f"/api/v1/resume-import/{v['import_id']}/check", headers=h, json=edits)
    assert r.status_code == 200, r.text
    return r.json()


def fields_of(result, item):
    return {f["field"]: f["message"] for f in result["errors"] if f["item"] == item}


# ------------------------------------------------------------------------------------------------- the contract
ROLE = {"title": "Support Lead", "employer": "Example Helpdesk", "employment_type": "full_time", "start": M(2015, 1),
        "end": M(2016, 6), "description": "Led a support team of six for a retail software product."}


@pytest.mark.parametrize("change,field,message", [
    ({"employer": ""}, "employer", "Employer is required."),
    ({"employer": "   "}, "employer", "Employer is required."),
    ({"employment_type": "unknown"}, "employment_type", "Choose an employment type."),
    ({"start": {"year": 2015, "precision": "year"}}, "start", "Enter a start month and year."),
    ({"start": {"precision": "unknown"}}, "start", "Enter a start month and year."),
    ({"end": {"year": 2016, "precision": "year"}}, "end", "Enter an end month and year, or tick “I currently work here”."),
    ({"end": {"precision": "unknown"}}, "end", "Enter an end month and year, or tick “I currently work here”."),
    ({"end": M(2014, 12)}, "end", "The end date is before the start date."),
    ({"start": M(2099, 1), "end": NOW}, "start", "The start date can't be in the future."),
    ({"description": ""}, "description", None),
    ({"description": "      "}, "description", None),
    ({"description": "Did work."}, "description", None),                                  # too short to describe a role
    ({"description": "x" * 1200}, "description", None),
    ({"title": ""}, "title", "Enter the job title."),
])
def test_every_role_field_is_checked_with_a_message_for_that_field(on, users, change, field, message):
    h, v = staged(on, users)
    result = check(on, h, v, {"added_work": [{**ROLE, **change}]})
    got = fields_of(result, "N0")
    assert field in got, got
    if message:
        assert got[field] == message


def test_a_current_role_needs_no_end_date_and_a_complete_role_has_no_errors(on, users):
    h, v = staged(on, users)
    current = check(on, h, v, {"added_work": [{**ROLE, "end": NOW, "start": M(2024, 1)}]})
    assert fields_of(current, "N0") == {"role_id": "Choose the closest Trace role for your starting position."}  # now the latest
    assert fields_of(check(on, h, v, {"added_work": [ROLE]}), "N0") == {}


def test_the_same_contract_applies_to_roles_found_in_the_resume(on, users):
    h, v = staged(on, users)
    found = {f["item"] for f in check(on, h, v, {})["errors"]}
    assert {j["id"] for j in v["work"]} <= found                               # no type or description yet: each is asked
    r = on.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json={})
    assert r.status_code == 422 and r.json()["error"] == "review_incomplete"
    assert {(f["item"], f["field"]) for f in r.json()["fields"]} >= {(v["work"][0]["id"], "employment_type"),
                                                                      (v["work"][0]["id"], "description")}
    assert records(on, h)["employments"] == []                                 # nothing written
    assert on.get("/api/v1/resume-import", headers=h).json()["import_id"] == v["import_id"]


# --------------------------------------------------------------------------------------------- the starting role
def test_the_starting_role_is_the_latest_role_and_is_required_on_that_role_only(on, users):
    h, v = staged(on, users)
    latest = max(v["work"], key=lambda j: (j["start"]["year"], j["start"]["month"]))
    assert v["starting_role"] == latest["id"] and [j["id"] for j in v["work"] if j["starting"]] == [latest["id"]]
    # Phase 5 (D50): every role can be matched (optional), but only the starting role must be
    assert all(j["role"]["choices"] for j in v["work"])
    errs = check(on, h, v, {})["errors"]
    assert {f["item"] for f in errs if f["field"] == "role_id"} <= {latest["id"]}


def test_the_starting_role_moves_to_a_later_role_added_in_the_review(on, users):
    h, v = staged(on, users)
    later = {**ROLE, "title": "Principal Engineer", "employer": "Example Cloud", "start": M(2024, 3), "end": NOW}
    result = check(on, h, v, {"added_work": [later]})
    assert result["starting_role"] == "N0" and result["role"]["choices"]
    assert "role_id" in fields_of(result, "N0")                                 # asked on the new latest role ...
    assert all(f["field"] != "role_id" for f in result["errors"] if f["item"] != "N0")   # ... and nowhere else
    earlier = {**ROLE, "start": M(2012, 1), "end": M(2013, 1)}
    assert check(on, h, v, {"added_work": [earlier]})["starting_role"] == v["starting_role"]   # an older role: no move


def test_a_side_role_never_becomes_the_starting_point(on, users):
    h, v = staged(on, users)
    side = {**ROLE, "title": "Founder", "employment_type": "founder", "start": M(2024, 3), "end": NOW, "side_role": True}
    assert check(on, h, v, {"added_work": [side]})["starting_role"] == v["starting_role"]


def test_the_chosen_trace_role_is_stored_apart_from_the_job_title(on, users):
    h, v = staged(on, users)
    start = next(j for j in v["work"] if j["starting"])
    other = next(c["role_id"] for c in start["role"]["choices"] if c["role_id"] != start["role"]["suggested_role_id"])
    edits = complete(on, h, v, {"work": [{"id": start["id"], "role_id": other, "employment_type": "full_time"}]})
    assert on.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=edits).status_code == 200
    job = next(e for e in records(on, h)["employments"] if e["title_raw"] == start["title"])
    assert job["title_raw"] == start["title"] and job["mapping"]["role_id"] == other            # title kept as written


def test_a_newest_role_that_cannot_count_leaves_the_position_unknown_not_an_older_role(on, users):
    h, v = staged(on, users)
    latest = max(v["work"], key=lambda j: (j["start"]["year"], j["start"]["month"]))
    founder = {**ROLE, "title": "Founder", "employer": "Example Studio", "employment_type": "founder", "start": M(2024, 3),
               "end": NOW, "role_id": None}
    # the full-time job ended before the founder role began: the founder role is the newest main-line role
    edits = complete(on, h, v, {"work": [{"id": latest["id"], "end": M(2024, 2)}], "added_work": [founder]})
    assert on.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=edits).status_code == 200
    position = on.get("/api/v1/current-position", headers=h).json()
    assert position["status"] == "unknown" and position["title_raw"] == "Founder"            # never the older job


def test_a_founder_role_alongside_a_current_job_never_replaces_it_and_needs_no_checkbox(on, users):
    # D50: concurrent work needs no checkbox. A founder role that overlaps a current full-time job runs alongside it.
    h, v = staged(on, users)
    latest = max(v["work"], key=lambda j: (j["start"]["year"], j["start"]["month"]))
    founder = {**ROLE, "title": "Founder", "employer": "Example Studio", "employment_type": "founder", "start": M(2024, 3),
               "end": NOW}
    edits = complete(on, h, v, {"work": [{"id": latest["id"], "employment_type": "full_time", "end": NOW}], "added_work": [founder]})
    chk = check(on, h, v, edits)
    assert chk["starting_role"] == latest["id"] and chk["alongside"] == {"N0": latest["id"]}
    assert on.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=edits).status_code == 200
    position = on.get("/api/v1/current-position", headers=h).json()
    assert position["status"] == "known" and position["title_raw"] == latest["title"]
    jobs = {e["title_raw"]: e for e in records(on, h)["employments"]}
    assert jobs["Founder"]["primary_career_sequence"] is False and jobs["Founder"]["employment_type"] == "founder"


# ----------------------------------------------------------------------------------------- confirmation reliability
def test_the_review_failure_pattern_confirms_end_to_end(on, users):
    h, v = staged(on, users, R[3], "pdf_employer_first")
    assert [(j["title"], j["employer"]) for j in v["work"]][:2] == [("Clinical AI Product Manager", "Novaline (Novaline Care Labs)"),
                                                                  ("Routing Product Manager", "Pallet")]
    assert v["unresolved"] == [] and v["starting_role"] == v["work"][0]["id"]
    long_paste = "• " + " ".join(["Compared two agent designs against agreed accuracy targets."] * 20) + "\n• Cut manual checks."
    edits = complete(on, h, v, {"work": [{"id": v["work"][0]["id"], "description": long_paste}]})
    bad = on.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=edits)
    assert bad.status_code == 422 and bad.json()["fields"][0]["field"] == "description"        # the 2026-11-21 500, now a field error
    edits["work"][0]["description"] = "Agent platform for clinic billing teams; sole PM for the claims-review line."
    ok = on.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=edits)
    assert ok.status_code == 200, ok.text
    assert len(records(on, h)["employments"]) == 5
    again = on.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=edits)
    assert again.json()["already"] is True and len(records(on, h)["employments"]) == 5        # no duplicates on retry


def test_a_failure_while_saving_keeps_the_whole_draft_and_writes_nothing(on, users, monkeypatch):
    import app.services.resume_import as mod
    h, v = staged(on, users)
    edits = complete(on, h, v)

    def boom(*a, **k):
        raise RuntimeError("simulated storage failure while saving 'Designed REST APIs for the merchant settlement'")
    monkeypatch.setattr(mod.ResumeImportService, "_ingest_and_confirm", boom)
    r = on.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=edits)
    assert r.status_code == 500 and r.json()["reference"] and r.json()["reference"] in r.json()["message"]
    assert records(on, h)["employments"] == []
    assert on.get("/api/v1/resume-import", headers=h).json()["import_id"] == v["import_id"]    # the draft is intact
    monkeypatch.undo()
    assert on.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=edits).status_code == 200


def test_an_unexpected_error_never_writes_request_content_to_logs(on, users, monkeypatch, caplog, capfd):
    import app.services.resume_import as mod
    caplog.set_level(logging.DEBUG)
    h, v = staged(on, users)
    monkeypatch.setattr(mod.ResumeImportService, "check",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("Canary-Resume-Text-4417 in a value")))
    r = on.post(f"/api/v1/resume-import/{v['import_id']}/check", headers=h, json={})
    out, err = capfd.readouterr()
    assert r.status_code == 500 and "Canary-Resume-Text-4417" not in r.text
    assert "Canary-Resume-Text-4417" not in caplog.text + out + err and "ValueError" in caplog.text


def test_a_value_a_model_rejects_is_a_422_without_echoing_it(on, users, monkeypatch, caplog, capfd):
    import app.services.resume_import as mod
    from app.services.candidate_profile.entries import EmploymentEntry
    caplog.set_level(logging.DEBUG)
    h, v = staged(on, users)

    def bad(*a, **k):
        EmploymentEntry(title_raw="x", responsibilities=["Canary-Resume-Text-9903 " * 80])
    monkeypatch.setattr(mod.ResumeImportService, "check", bad)
    r = on.post(f"/api/v1/resume-import/{v['import_id']}/check", headers=h, json={})
    out, err = capfd.readouterr()
    assert r.status_code == 422 and r.json()["error"] == "invalid_input"
    assert "Canary-Resume-Text-9903" not in r.text + caplog.text + out + err


def test_resending_an_unchanged_value_is_not_a_correction(on, users):
    h, v = staged(on, users)
    job = v["work"][-1]
    edits = complete(on, h, v, {"work": [{"id": job["id"], "start": job["start"], "end": job["end"], "title": job["title"]}]})
    r = on.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=edits)
    assert r.status_code == 200, r.text
