"""Release (D52): update a confirmed profile from a NEW resume (replace, not merge) and clear the career profile to start
over. Local Supabase, transient users, FICTIONAL resumes rendered from the evaluation corpus; no real resume.

Replacement: the confirmed profile is untouched until the reviewed replacement is confirmed; confirmation is one
transaction (a failure after the old profile is removed rolls everything back). Clearing: an explicit confirmation,
owner-scoped, idempotent, keeps the account; another user's profile, plans and drafts are never affected.
"""
import pytest

from app import db
from app.services.resume_import import ResumeImportService
from evaluation.resume_parser.corpus import P
from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import candidate, consent_document, make_client  # noqa: F401  (fixtures)
from tests.test_resume_import_api import complete, on, pdf, records, up  # noqa: F401  (fixtures and helpers)

pytestmark = pytest.mark.integration
PHRASE = {"confirm": "clear my career profile"}
OWNER_TABLES = ("trace_candidate_employments", "trace_candidate_employers", "trace_candidate_evidence", "trace_candidate_skills",
                "trace_candidate_education", "trace_candidate_career_activities", "trace_candidate_corrections",
                "trace_resume_documents", "trace_extraction_runs", "trace_pathway_runs")


def confirmed(c, h, i):
    v = up(c, h, pdf(i)).json()["import"]
    assert c.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=complete(c, h, v)).status_code == 200
    return v


def titles(c, h):
    return sorted(e["title_raw"] for e in records(c, h)["employments"])


def owner_rows(settings, user):
    conn = db.connect(settings)
    try:
        out = {t: conn.execute(f"select count(*) from public.{t} where owner_user_id = %s", (user,)).fetchone()[0] for t in OWNER_TABLES}
        out["drafts"] = conn.execute("select count(*) from trace_private.resume_import_drafts where owner_user_id = %s", (user,)).fetchone()[0]
        out["processing_log"] = conn.execute("select count(*) from trace_private.resume_processing_log where owner_user_id = %s",
                                             (user,)).fetchone()[0]
        return out
    finally:
        conn.close()


# ------------------------------------------------------------------------------------------------- replace
def test_a_new_resume_replaces_the_confirmed_profile_only_on_confirmation(on, users):
    _, h = candidate(on, users)
    confirmed(on, h, 0)
    before = titles(on, h)
    r = on.post("/api/v1/resume-import?replace_profile=true", headers={**h, "Content-Type": "application/pdf"}, content=pdf(1))
    view = r.json()["import"]
    assert r.status_code == 201 and view["mode"] == "replace"
    assert view["replaces"]["jobs"] == len(before)                       # the page can say what will be replaced
    assert all(w["status"] == "new" for w in view["work"])                # reviewed as a new profile: nothing compared or merged
    assert titles(on, h) == before                                         # nothing changed before confirmation
    assert on.post(f"/api/v1/resume-import/{view['import_id']}/confirm", headers=h, json=complete(on, h, view)).status_code == 200
    after = titles(on, h)
    assert after == sorted(j["title"] for j in P[1]["jobs"])              # replaced, not merged
    assert not set(before) & set(after)
    assert on.get("/api/v1/resume-import", headers=h).json()["status"] == "none"


def test_a_failed_replacement_leaves_the_confirmed_profile_intact(on, users, monkeypatch):
    _, h = candidate(on, users)
    confirmed(on, h, 0)
    before = records(on, h)
    view = on.post("/api/v1/resume-import?replace_profile=true", headers={**h, "Content-Type": "application/pdf"}, content=pdf(1)).json()["import"]
    edits = complete(on, h, view)

    def boom(*a, **k):                                                    # fails AFTER the old profile was removed
        raise RuntimeError("simulated failure during ingestion")
    monkeypatch.setattr(ResumeImportService, "_ingest_and_confirm", boom)
    assert on.post(f"/api/v1/resume-import/{view['import_id']}/confirm", headers=h, json=edits).status_code == 500
    monkeypatch.undo()
    assert records(on, h) == before                                       # rolled back: the old profile is exactly as it was
    assert on.get("/api/v1/resume-import", headers=h).json()["import_id"] == view["import_id"]   # the draft is still there


def test_discarding_a_replacement_keeps_the_confirmed_profile(on, users):
    _, h = candidate(on, users)
    confirmed(on, h, 0)
    before = records(on, h)
    on.post("/api/v1/resume-import?replace_profile=true", headers={**h, "Content-Type": "application/pdf"}, content=pdf(1))
    assert on.delete("/api/v1/resume-import", headers=h).status_code == 200
    assert records(on, h) == before


def test_replace_profile_on_an_empty_profile_is_an_ordinary_import(on, users):
    _, h = candidate(on, users)
    r = on.post("/api/v1/resume-import?replace_profile=true", headers={**h, "Content-Type": "application/pdf"}, content=pdf(0))
    assert r.status_code == 201 and r.json()["import"]["mode"] == "new"


# ------------------------------------------------------------------------------------------------- clear
def test_clearing_needs_a_verified_user(on):
    assert on.get("/api/v1/profile/clear").status_code == 401
    assert on.post("/api/v1/profile/clear", json=PHRASE).status_code == 401


def test_clear_shows_its_consequences_and_needs_the_explicit_confirmation(on, users):
    _, h = candidate(on, users)
    confirmed(on, h, 2)
    p = on.get("/api/v1/profile/clear", headers=h).json()
    assert p["deletes"]["jobs"] == 3 and p["deletes"]["experience_lines"] > 0 and p["deletes"]["resume_records"] == 1
    assert set(p["keeps"]) == {"account", "consent_choices", "planning_preferences", "sharing_choice"} and not p["empty"]
    for body in ({}, {"confirm": "yes"}, {"confirm": "clear"}):
        assert on.post("/api/v1/profile/clear", headers=h, json=body).status_code == 422
    assert len(titles(on, h)) == 3                                         # nothing deleted without the confirmation


def test_clear_removes_the_career_profile_and_derived_data_keeps_the_account_and_is_idempotent(on, users, base_settings):
    u, h = candidate(on, users)
    confirmed(on, h, 2)
    plan = on.post("/api/v1/transition-plan?target=PM-SPM", headers=h)       # a saved plan that depends on the profile
    assert plan.status_code == 201, plan.text
    up(on, h, pdf(0), replace=True)                                       # plus a pending draft
    assert owner_rows(base_settings, u)["drafts"] == 1 and owner_rows(base_settings, u)["trace_pathway_runs"] >= 1
    r = on.post("/api/v1/profile/clear", headers=h, json=PHRASE)
    assert r.status_code == 200 and r.json()["status"] == "cleared" and r.json()["deleted"]["jobs"] == 3
    left = owner_rows(base_settings, u)
    assert not any(left.values()), left                                   # every owner row, the processing log included
    assert records(on, h)["employments"] == [] and on.get("/api/v1/resume-import", headers=h).json()["status"] == "none"
    again = on.post("/api/v1/profile/clear", headers=h, json=PHRASE)       # safe to retry
    assert again.status_code == 200 and again.json()["status"] == "already_clear"
    assert on.get("/api/v1/consent", headers=h).status_code in (200, 404)  # still signed in and able to call the API
    v = up(on, h, pdf(1)).json()["import"]                                 # and can start over at the upload step
    assert v["mode"] == "new"


def test_clearing_never_touches_another_users_profile_plans_or_drafts(on, users, base_settings):
    u1, h1 = candidate(on, users)
    confirmed(on, h1, 2)
    up(on, h1, pdf(0), replace=True)
    before = owner_rows(base_settings, u1)
    records_before = records(on, h1)
    _, h2 = candidate(on, users)
    confirmed(on, h2, 0)
    assert on.post("/api/v1/profile/clear", headers=h2, json=PHRASE).json()["status"] == "cleared"
    assert owner_rows(base_settings, u1) == before and records(on, h1) == records_before
    assert on.get("/api/v1/profile/clear", headers=h2).json()["empty"]
