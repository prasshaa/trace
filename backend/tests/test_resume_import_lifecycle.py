"""Resume import data lifecycle (D48, migration v2.31.0): staged drafts, identity markers, consent purposes, MIME.

Local Supabase, transient users, FICTIONAL resumes only (evaluation/resume_parser). Admin reads check what the database
holds; every action goes through the API.
"""
import io
import zipfile

import pytest

from app import db
from app.demo import consent as demo_consent
from evaluation.resume_parser.corpus import P
from evaluation.resume_parser.regression import R
from evaluation.resume_parser.render import render
from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import candidate, consent_document, make_client  # noqa: F401  (fixtures)
from tests.test_real_resume_contributions import contribution_document  # noqa: F401  (fixture)
from tests.test_resume_import_api import DOCX, PDF, complete, records, up

pytestmark = pytest.mark.integration


@pytest.fixture
def on(base_settings, consent_document, contribution_document):
    with make_client(base_settings, trace_real_resume_uploads=True) as client:
        yield client


def admin(base_settings, sql, *params):
    with db.connect(base_settings) as conn:
        return conn.execute(sql, params).fetchall()


def owner(c, h):
    return c.get("/api/v1/me", headers=h).json()["user_id"]


def markers(base_settings, uid):
    return admin(base_settings, "select key_id from trace_private.identity_markers where owner_user_id = %s", uid)


def draft_row(base_settings, uid):
    return admin(base_settings, "select import_id, identity_sealed is not null from trace_private.resume_import_drafts "
                                "where owner_user_id = %s", uid)


def contribute(c, h, decision="granted"):
    r = c.post("/api/v1/consent/contribution", headers=h, json={"decision": decision, "consent_version": demo_consent.CONTRIBUTION_VERSION})
    assert r.status_code == 200, r.text


def confirm(c, h, v, body=None, fill=True):
    """Confirm with the user's edits; fill=True also supplies what the role contract still needs (tests/…api.complete)."""
    return c.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=complete(c, h, v, body) if fill else (body or {}))


# ---------------------------------------------------------------------------------------- pending vs confirmed
def test_a_pending_import_is_never_profile_information(on, users, base_settings):
    _, h = candidate(on, users)
    v = up(on, h, render(P[0], "pdf_classic")).json()["import"]
    uid = owner(on, h)
    assert v["staged"] and records(on, h)["employments"] == []
    for t in ("trace_candidate_employments", "trace_candidate_evidence", "trace_candidate_skills", "trace_candidate_education",
              "trace_candidate_career_activities"):
        assert admin(base_settings, f"select count(*) from public.{t} where owner_user_id = %s", uid)[0][0] == 0, t
    assert admin(base_settings, "select count(*) from public.trace_resume_documents where owner_user_id = %s", uid)[0][0] == 0
    assert on.get("/api/v1/current-position", headers=h).json()["status"] != "known"   # pending data can't set a position
    assert on.get("/api/v1/proposals", headers=h).json() == []
    assert confirm(on, h, v).status_code == 200
    assert len(records(on, h)["employments"]) == 2 and draft_row(base_settings, uid) == []


def test_an_existing_profile_gets_the_same_staged_draft_not_proposals(on, users, base_settings):
    _, h = candidate(on, users)
    confirm(on, h, up(on, h, render(P[0], "pdf_classic")).json()["import"])
    before = records(on, h)["employments"]
    v = up(on, h, render(P[1], "pdf_classic")).json()["import"]
    assert v["staged"] and v["mode"] == "update" and on.get("/api/v1/proposals", headers=h).json() == []
    assert records(on, h)["employments"] == before                                  # nothing applied before confirmation
    assert {j["status"] for j in v["work"]} == {"new"}
    assert confirm(on, h, v).status_code == 200
    assert len(records(on, h)["employments"]) == len(before) + 2
    again = confirm(on, h, v)
    assert again.json()["already"] is True and len(records(on, h)["employments"]) == len(before) + 2   # no duplicates


def test_the_review_failure_pattern_reaches_the_review_screen_correctly(on, users):
    _, h = candidate(on, users)
    v = up(on, h, render(R[0], "pdf_compact")).json()["import"]
    jobs = [(j["title"], j["employer"], j["start"]["year"]) for j in v["work"]]
    assert jobs == [("Senior Product Manager", "Quillon Freight", 2025), ("Global Product Manager", "Vexa", 2021),
                    ("Associate Product Manager", "Tallyfox", 2021), ("Software Test Engineer", "Brightline Technology Solutions", 2017),
                    ("Co-Founder & Writer", "Inkpot Stories", 2016)]
    vexa = v["work"][1]
    assert not any("Tallyfox" in x["text"] for x in vexa["description"] + vexa["bullets"])
    assert v["unresolved"] == []


def test_a_role_whose_title_could_not_be_read_stays_a_role_with_its_lines(on, users):
    _, h = candidate(on, users)
    p = dict(P[0], jobs=[dict(P[0]["jobs"][0]), dict(P[0]["jobs"][1], title="Night Shift", employer="Kestrel Analytics")])
    v = up(on, h, render(p, "docx_classic"), DOCX).json()["import"]
    job = next(j for j in v["work"] if j["employer"] == "Kestrel Analytics")
    assert job["title"] is None and "title" in job["review"] and job["source"] and len(job["bullets"]) == 2
    assert v["unresolved"] == []                                                    # not parked as unplaced text
    r = confirm(on, h, v, {"work": [{"id": job["id"], "title": "Night Shift Supervisor"}]})
    assert r.status_code == 200, r.text
    added = next(e for e in records(on, h)["employments"] if e["title_raw"] == "Night Shift Supervisor")
    assert added["review"]["origin"] == "user_stated" and len(added["evidence"]) >= 2   # its lines came with it


def test_unplaced_text_can_become_a_role_once_or_be_ignored(on, users):
    _, h = candidate(on, users)
    p = dict(P[0], jobs=[dict(P[0]["jobs"][0]), dict(P[0]["jobs"][1], title="", employer="")])
    v = up(on, h, render(p, "pdf_classic")).json()["import"]
    u = next(x for x in v["unresolved"] if x["kind"] == "possible_role")
    assert u["suggested"]["start"]["year"] == 2018 and u["suggested"]["end"]["month"] == 3        # what the text supports
    role = {"title": "Data Engineer", "employer": "Kestrel Analytics", "employment_type": "full_time",
            "start": {"year": 2018, "month": 7, "precision": "month"}, "end": {"year": 2021, "month": 3, "precision": "month"},
            "description": "Built data ingestion pipelines for retail clients.", "from_unresolved": u["id"]}
    twice = confirm(on, h, v, {"added_work": [role, role]}, fill=False)
    assert twice.status_code == 422 and any(f["field"] == "from_unresolved" for f in twice.json()["fields"])
    ok = confirm(on, h, v, {"added_work": [role], "ignored": [x["id"] for x in v["unresolved"] if x["id"] != u["id"]]})
    assert ok.status_code == 200, ok.text
    assert [e["title_raw"] for e in records(on, h)["employments"]].count("Data Engineer") == 1


def test_ignored_unplaced_text_never_blocks_confirmation(on, users):
    _, h = candidate(on, users)
    p = dict(P[0], jobs=[dict(P[0]["jobs"][0]), dict(P[0]["jobs"][1], title="", employer="")])
    v = up(on, h, render(p, "pdf_classic")).json()["import"]
    assert v["unresolved"]
    r = confirm(on, h, v, {"ignored": [x["id"] for x in v["unresolved"]]})
    assert r.status_code == 200 and len(records(on, h)["employments"]) == 1        # the ignored text isn't saved


# ----------------------------------------------------------------------------------- identity markers and consent
def test_no_identity_markers_are_kept_without_contribution_consent(on, users, base_settings):
    _, h = candidate(on, users)
    uid = owner(on, h)
    v = up(on, h, render(P[0], "pdf_classic")).json()["import"]
    assert draft_row(base_settings, uid)[0][1] is False                             # nothing sealed with the draft
    assert confirm(on, h, v).status_code == 200
    assert markers(base_settings, uid) == []


def test_markers_for_a_contributor_follow_the_draft_and_the_consent(on, users, base_settings):
    _, h = candidate(on, users)
    uid = owner(on, h)
    contribute(on, h)
    v = up(on, h, render(P[0], "pdf_classic")).json()["import"]
    assert draft_row(base_settings, uid)[0][1] is True and markers(base_settings, uid) == []   # sealed, held with the draft only
    on.delete("/api/v1/resume-import", headers=h)
    assert draft_row(base_settings, uid) == [] and markers(base_settings, uid) == []   # discard: gone with the draft
    v = up(on, h, render(P[0], "pdf_classic")).json()["import"]
    assert confirm(on, h, v).status_code == 200
    assert len(markers(base_settings, uid)) == 1                                    # kept for the active contribution
    contribute(on, h, "withdrawn")
    assert markers(base_settings, uid) == []                                        # withdrawal deletes them at once
    assert len(records(on, h)["employments"]) == 2                                  # the confirmed profile stays


def test_a_failed_confirmation_leaves_no_markers_metadata_or_records(on, users, base_settings):
    _, h = candidate(on, users)
    uid = owner(on, h)
    contribute(on, h)
    v = up(on, h, render(P[0], "pdf_classic")).json()["import"]
    bad = {"work": [{"id": v["work"][0]["id"], "title": "Changed"}, {"id": "J99", "remove": True}]}
    assert confirm(on, h, v, bad, fill=False).status_code == 404
    assert markers(base_settings, uid) == [] and records(on, h)["employments"] == []
    assert admin(base_settings, "select count(*) from public.trace_resume_documents where owner_user_id = %s", uid)[0][0] == 0
    assert admin(base_settings, "select count(*) from trace_ingest.deidentified_resume_admissions where owner_user_id = %s", uid)[0][0] == 0
    assert len(draft_row(base_settings, uid)) == 1                                  # the draft is still there to fix


def test_replacing_an_import_leaves_one_draft_and_no_orphan_markers(on, users, base_settings):
    _, h = candidate(on, users)
    uid = owner(on, h)
    contribute(on, h)
    up(on, h, render(P[0], "pdf_classic"))
    up(on, h, render(P[1], "pdf_classic"), replace=True)
    assert len(draft_row(base_settings, uid)) == 1 and markers(base_settings, uid) == []


def test_withdrawing_resume_processing_deletes_the_draft_but_not_the_confirmed_profile(on, users, base_settings):
    _, h = candidate(on, users)
    uid = owner(on, h)
    confirm(on, h, up(on, h, render(P[0], "pdf_classic")).json()["import"])
    up(on, h, render(P[1], "pdf_classic"))
    doc = on.get("/api/v1/consent/resume-processing", headers=h).json()["document"]
    r = on.post("/api/v1/consent/resume-processing", headers=h, json={"decision": "withdrawn", "consent_version": doc["consent_version"]})
    assert r.status_code == 200
    assert draft_row(base_settings, uid) == [] and len(records(on, h)["employments"]) == 2
    assert up(on, h, render(P[1], "pdf_classic")).status_code == 403                 # no further processing


def test_resume_processing_consent_is_not_contribution_consent(on, users):
    _, h = candidate(on, users)
    confirm(on, h, up(on, h, render(P[0], "pdf_classic")).json()["import"])
    c = on.get("/api/v1/consent/contribution", headers=h).json()
    assert c["active"] is False                                                     # never implied by uploading or confirming


# -------------------------------------------------------------------------------------------- formats and MIME
def _sheet_zip():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", "<workbook/>")
    return b.getvalue()


@pytest.mark.parametrize("make,ctype,code", [
    (lambda: render(P[0], "pdf_classic"), DOCX, "unsupported_format"),        # a PDF sent as a Word document
    (lambda: render(P[0], "docx_classic"), PDF, "unsupported_format"),        # a Word document sent as a PDF
    (_sheet_zip, DOCX, "unsupported_format"),                                   # a ZIP that isn't a Word document
    (lambda: b"\x89PNG\r\n\x1a\n" + b"0" * 100, PDF, "unsupported_format"),    # an image with a .pdf name
    (lambda: render(P[0], "pdf_classic")[:400], PDF, "unreadable"),            # a truncated PDF
])
def test_the_format_comes_from_the_content_never_the_label(on, users, make, ctype, code):
    _, h = candidate(on, users)
    r = up(on, h, make(), ctype)
    assert r.status_code == 422 and r.json()["error"] == code, r.text
    assert on.get("/api/v1/resume-import", headers=h).json()["status"] == "none"


@pytest.mark.parametrize("layout,ctype", [("pdf_classic", PDF), ("docx_classic", DOCX)])
def test_the_recorded_mime_type_is_the_proven_format(on, users, base_settings, layout, ctype):
    _, h = candidate(on, users)
    v = up(on, h, render(P[0], layout), ctype).json()["import"]
    assert confirm(on, h, v).status_code == 200
    rows = admin(base_settings, "select mime_type, retention_until from public.trace_resume_documents where owner_user_id = %s", owner(on, h))
    assert rows == [(ctype, None)]                                                 # and no implied retention date


def test_a_role_added_in_the_review_needs_its_start_year(on, users):
    _, h = candidate(on, users)
    v = up(on, h, render(P[0], "pdf_classic")).json()["import"]
    r = confirm(on, h, v, {"added_work": [{"title": "Volunteer Tutor"}]})               # undated: would displace the current job
    assert r.status_code == 422 and records(on, h)["employments"] == []
    fields = {(f["item"], f["field"]) for f in r.json()["fields"]}
    assert {("N0", "start"), ("N0", "employer"), ("N0", "employment_type"), ("N0", "description"), ("N0", "end")} <= fields
