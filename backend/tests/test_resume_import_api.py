"""Resume-first onboarding API (/api/v1/resume-import): upload, consolidated review, one confirmation, replace, discard.

Local Supabase, transient users, FICTIONAL resumes rendered from the evaluation corpus (evaluation/resume_parser). A
synthetic canary in the resume's contact line proves contact details never reach stored records, responses or logs.
Since D48 an unconfirmed import is a private staged draft (trace_private.resume_import_drafts): nothing reaches the
profile tables until the user confirms, for a new profile and an existing one alike.
"""
import io
import logging
import os
import tempfile
import zipfile

import pytest

from evaluation.resume_parser.corpus import P
from evaluation.resume_parser.render import render
from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import candidate, consent_document, make_client  # noqa: F401  (fixtures)

pytestmark = pytest.mark.integration
PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
def on(base_settings, consent_document):
    with make_client(base_settings, trace_real_resume_uploads=True) as client:
        yield client


@pytest.fixture
def off(base_settings, consent_document):
    with make_client(base_settings) as client:
        yield client


def pdf(i=0, layout="pdf_classic", canary=None):
    p = dict(P[i])
    if canary:
        p["name"] = canary
    return render(p, layout)


def up(c, h, data, ctype=PDF, replace=False):
    return c.post("/api/v1/resume-import" + ("?replace=true" if replace else ""), headers={**h, "Content-Type": ctype}, content=data)


def complete(c, h, v, edits=None):
    """The user's edits plus what the contract still needs (asked from the server's read-only check): an employment type,
    a description, the closest Trace role on the starting role, and education dates (Phase 5). Never changes a given
    value. Re-checks until nothing new is needed: an answer can move the starting role (D50: a founder or part-time role
    that overlaps a full-time one runs alongside it, so the full-time role becomes the starting point)."""
    edits = {"work": [], **(edits or {})}
    for _ in range(4):
        if not _fill(c, h, v, edits):
            break
    return edits


def _fill(c, h, v, edits) -> bool:
    by_id = {e["id"]: e for e in edits["work"]}
    edu = {e["id"]: e for e in edits.setdefault("education", [])}
    res = c.post(f"/api/v1/resume-import/{v['import_id']}/check", headers=h, json=edits)
    if res.status_code != 200:                                  # already confirmed (no draft), or a foreign id
        return False
    chk = res.json()
    changed = False
    for f in chk["errors"]:
        if f["item"].startswith("D"):                           # Phase 5: education start and completion month and year
            d = edu.setdefault(f["item"], {"id": f["item"]})
            parsed = next((x for x in v.get("education", []) if x["id"] == f["item"]), {})
            end_year = (parsed.get("end") or {}).get("year") or 2016
            want = {"start": ("start", {"year": (parsed.get("start") or {}).get("year") or end_year - 4, "month": 7, "precision": "month"}),
                    "end": ("end", {"year": end_year, "month": 5, "precision": "month"}),
                    "institution": ("institution", "Example University"), "qualification": ("qualification", "B.Sc")}.get(f["field"])
            if want and want[0] not in d:
                d[want[0]] = want[1]
                changed = True
            continue
        if f["item"].startswith("N"):
            a = edits.get("added_work", [])[int(f["item"][1:])]
            if f["field"] == "role_id" and chk["role"] and not a.get("role_id"):
                a["role_id"] = chk["role"]["suggested_role_id"] or chk["role"]["choices"][0]["role_id"]
                changed = True
            continue
        if not f["item"].startswith("J"):
            continue
        e = by_id.setdefault(f["item"], {"id": f["item"]})
        before = dict(e)
        if f["field"] == "employment_type":
            e.setdefault("employment_type", "full_time")
        elif f["field"] == "description":
            e.setdefault("description", "Responsible for planning and delivering the team's work with stakeholders.")
        elif f["field"] == "role_id" and chk["role"]:
            e.setdefault("role_id", chk["role"]["suggested_role_id"] or chk["role"]["choices"][0]["role_id"])
        changed = changed or e != before
    edits["work"] = list(by_id.values()) + [e for e in edits["work"] if e["id"] not in by_id]
    edits["education"] = list(edu.values())
    return changed


def records(c, h):
    return c.get("/api/v1/profile/records", headers=h).json()


def all_statuses(c, h):
    r = records(c, h)
    out = [e["review"]["status"] for e in r["employments"]] + [x["review"]["status"] for e in r["employments"] for x in e["evidence"]]
    out += [x["review"]["status"] for x in r.get("other_records", [])]
    return out


# ------------------------------------------------------------------------------------------------- access and switches
def test_upload_is_refused_when_the_server_switch_is_off(off, users):
    _, h = candidate(off, users)
    r = up(off, h, pdf())
    assert r.status_code == 403 and r.json()["error"] == "real_uploads_disabled"
    assert off.get("/api/v1/resume-import", headers=h).json()["status"] == "none"


def test_every_endpoint_needs_a_verified_user(on):
    assert on.get("/api/v1/resume-import").status_code == 401
    assert on.post("/api/v1/resume-import", content=b"%PDF-1.4", headers={"Content-Type": PDF}).status_code == 401
    assert on.delete("/api/v1/resume-import").status_code == 401
    assert on.post("/api/v1/resume-import/00000000-0000-0000-0000-000000000000/confirm", json={}).status_code == 401
    assert on.post("/api/v1/resume-import", content=b"%PDF-1.4", headers={"Content-Type": PDF,
                   "Authorization": "Bearer forged"}).status_code == 401


def test_consent_is_required(on, users):
    _, h = candidate(on, users, grant=False)
    assert up(on, h, pdf()).status_code == 403


# ------------------------------------------------------------------------------------------------------ file checks
def _encrypted_pdf():
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter()
    for page in PdfReader(io.BytesIO(pdf())).pages:
        w.add_page(page)
    w.encrypt("secret-password")
    b = io.BytesIO()
    w.write(b)
    return b.getvalue()


def _traversal_docx():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
        z.writestr("../../evil.txt", "x")
    return b.getvalue()


@pytest.mark.parametrize("data,ctype,status,code", [
    (b"", PDF, 422, "empty"),
    (b"hello, this is plain text", PDF, 422, "unsupported_format"),
    (b"%PDF-1.4 this is not really a pdf", DOCX, 422, "unsupported_format"),     # declared type disagrees with signature
    ("docx_as_pdf", PDF, 422, "unsupported_format"),
    (b"%PDF-1.4\n%garbage that is not a valid pdf structure", PDF, 422, "unreadable"),
    ("encrypted", PDF, 422, "encrypted"),
    ("traversal", DOCX, 422, "unsafe_document"),
    (b"%PDF-" + b"0" * (1_048_576 + 10), PDF, 413, None),
])
def test_bad_files_are_refused_with_a_helpful_message_and_nothing_is_stored(on, users, data, ctype, status, code):
    _, h = candidate(on, users)
    data = {"encrypted": _encrypted_pdf, "traversal": _traversal_docx,
            "docx_as_pdf": lambda: render(P[0], "docx_classic")}.get(data, lambda: data)() if isinstance(data, str) else data
    r = up(on, h, data, ctype)
    assert r.status_code == status, r.text
    if code:
        assert r.json()["error"] == code
    assert len(r.json()["message"]) < 300
    assert on.get("/api/v1/resume-import", headers=h).json()["status"] == "none"
    assert records(on, h)["employments"] == []


# --------------------------------------------------------------------------------------------- upload and review
@pytest.mark.parametrize("layout,ctype", [("pdf_classic", PDF), ("pdf_two_column", PDF), ("docx_classic", DOCX)])
def test_upload_gives_one_structured_reviewable_profile(on, users, layout, ctype):
    _, h = candidate(on, users)
    r = up(on, h, render(P[2], layout), ctype)
    assert r.status_code == 201, r.text
    v = r.json()["import"]
    assert r.json()["result"] == "imported" and v["status"] == "ready"
    jobs = [(j["title"], j["employer"]) for j in v["work"]]
    assert jobs == [("Product Manager", "Lumen Health Technologies"), ("Associate Product Manager", "Lumen Health Technologies"),
                    ("Product Analyst", "Vertex Commerce")]
    assert v["work"][0]["start"] == {"year": 2022, "month": 2, "precision": "month"} and v["work"][0]["current"] is True
    assert len(v["work"][0]["bullets"]) == 3                                         # whole bullets, not wrapped fragments
    names = {s["name"] for g in v["skills"] for s in g["items"]}
    assert {"SQL", "Mixpanel", "Figma"} <= names and not any(n.endswith(")") or n == "F1)" for n in names)
    assert len(names) == len({n.lower() for n in names})                            # de-duplicated
    assert v["education"][0]["institution"] == "Fictional University of Engineering"
    assert v["staged"] is True and v["mode"] == "new"
    assert records(on, h)["employments"] == [] and all_statuses(on, h) == []         # nothing in the profile before confirming


def test_contact_details_never_reach_records_responses_or_logs(on, users, caplog):
    caplog.set_level(logging.DEBUG)
    _, h = candidate(on, users)
    canary = "Zebulon Canaryperson"
    r = up(on, h, pdf(0, canary=canary))
    assert r.status_code == 201
    body = r.text + on.get("/api/v1/resume-import", headers=h).text + on.get("/api/v1/profile/records", headers=h).text
    for leak in ("Zebulon", "Canaryperson", "zebulon@example.invalid", "90000 00000"):
        assert leak not in body and leak not in caplog.text
    assert "Designed REST APIs" not in caplog.text                                   # no resume content in logs


def test_the_original_file_is_not_kept(on, users, base_settings):
    _, h = candidate(on, users)
    tmp = tempfile.gettempdir()
    before = set(os.listdir(tmp))
    data = render(P[0], "docx_classic")
    v = up(on, h, data, DOCX).json()["import"]
    after = set(os.listdir(tmp))
    assert not [f for f in after - before if f.lower().endswith((".pdf", ".docx"))]
    import psycopg
    with psycopg.connect(base_settings.database_url.get_secret_value() if hasattr(base_settings.database_url, "get_secret_value") else base_settings.database_url) as conn:
        draft = conn.execute("select draft::text, mime_type, format, byte_size, identity_sealed from trace_private.resume_import_drafts "
                             "where import_id = %s", (v["import_id"],)).fetchone()
        assert draft[1] == DOCX and draft[2] == "docx" and draft[3] == len(data) and draft[4] is None
        assert "example.invalid" not in draft[0] and "90000" not in draft[0]         # de-identified; no file bytes or name
        assert on.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=complete(on, h, v)).status_code == 200
        cols = conn.execute("""select original_filename, storage_object_path, mime_type, retention_until from public.trace_resume_documents
                               where storage_object_path like %s""", (f"%/{v['import_id']}.docx",)).fetchone()
        objects = conn.execute("select count(*) from storage.objects where bucket_id = 'trace-resumes'").fetchone()[0]
        left = conn.execute("select count(*) from trace_private.resume_import_drafts where import_id = %s", (v["import_id"],)).fetchone()[0]
    assert cols[0] is None and objects == 0                                          # no filename, no stored object
    assert cols[2] == DOCX and cols[3] is None                                       # the proven format; no implied retention date
    assert left == 0                                                                 # the draft is gone after confirmation


# ---------------------------------------------------------------------------------- replace, confirm, discard, merge
def test_an_open_import_must_be_replaced_explicitly_and_replacement_leaves_no_duplicates(on, users):
    _, h = candidate(on, users)
    first = up(on, h, pdf(0)).json()["import"]
    r = up(on, h, pdf(1))
    assert r.status_code == 409 and r.json()["error"] == "import_pending"
    again = up(on, h, pdf(0), replace=True)
    assert again.json()["result"] == "unchanged"                                    # the same file: idempotent
    second = up(on, h, pdf(1), replace=True).json()["import"]
    assert second["import_id"] != first["import_id"]
    assert [j["title"] for j in second["work"]] == ["Business Analyst", "Associate Consultant"]
    assert on.get("/api/v1/resume-import", headers=h).json()["import_id"] == second["import_id"]   # one draft only
    assert records(on, h)["employments"] == []                                       # and nothing in the profile


def test_one_confirmation_saves_edits_atomically_and_is_idempotent(on, users):
    _, h = candidate(on, users)
    v = up(on, h, pdf(2)).json()["import"]
    pm, apm, analyst = v["work"]
    skill = next(s for g in v["skills"] for s in g["items"] if s["name"] == "Figma")
    edits = {"work": [{"id": pm["id"], "title": "Product Manager II", "bullets": [{"id": pm["bullets"][0]["id"], "remove": True}]},
                      {"id": analyst["id"], "remove": True},
                      {"id": apm["id"], "employer": "Lumen Health"}],
             "skills": [{"id": skill["id"], "remove": True}]}
    edits = complete(on, h, v, edits)
    r = on.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=edits)
    assert r.status_code == 200 and r.json() == {"status": "confirmed", "already": False}, r.text
    rec = records(on, h)
    titles = {e["title_raw"]: e for e in rec["employments"]}
    assert set(titles) == {"Product Manager II", "Associate Product Manager"}
    assert titles["Product Manager II"]["review"]["status"] == "user_corrected"
    assert titles["Associate Product Manager"]["review"]["origin"] == "user_stated"   # rewritten by the user
    assert titles["Associate Product Manager"]["employer_name"] == "Lumen Health"
    assert "pending_review" not in all_statuses(on, h)
    assert on.get("/api/v1/resume-import", headers=h).json()["status"] == "none"
    again = on.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=edits)
    assert again.status_code == 200 and again.json()["already"] is True
    assert len(records(on, h)["employments"]) == 2                                  # no duplicates on retry


def test_a_bad_edit_rolls_back_the_whole_confirmation(on, users):
    _, h = candidate(on, users)
    v = up(on, h, pdf(2)).json()["import"]
    bad = {"work": [{"id": v["work"][0]["id"], "title": "Changed"}, {"id": "00000000-0000-0000-0000-000000000001", "remove": True}]}
    assert on.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=bad).status_code == 404
    assert records(on, h)["employments"] == []                                       # nothing half-saved
    assert on.get("/api/v1/resume-import", headers=h).json()["import_id"] == v["import_id"]        # the draft is still there


def test_discard_clears_only_unconfirmed_import_data_and_survives_sign_in_again(on, users):
    _, h = candidate(on, users)
    v = up(on, h, pdf(0)).json()["import"]
    assert on.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=complete(on, h, v)).status_code == 200
    confirmed = records(on, h)["employments"]
    plan = on.post("/api/v1/transition-plan?target=PM-PM", headers=h)
    r = up(on, h, pdf(1))                                                            # a second resume into a confirmed profile
    assert r.status_code == 201 and r.json()["import"]["status"] == "ready"
    assert r.json()["import"]["mode"] == "update" and records(on, h)["employments"] == confirmed
    d = on.delete("/api/v1/resume-import", headers=h)
    assert d.status_code == 200 and d.json()["drafts"] == 1
    assert on.get("/api/v1/resume-import", headers=h).json()["status"] == "none"   # gone (server state, not the browser)
    assert [e["id"] for e in records(on, h)["employments"]] == [e["id"] for e in confirmed]
    if plan.status_code in (200, 201):
        assert on.get("/api/v1/transition-plan?target=PM-PM", headers=h).status_code == 200            # saved plan kept
    assert on.delete("/api/v1/resume-import", headers=h).status_code == 200           # idempotent


def test_a_confirmed_profile_is_never_overwritten_and_duplicates_are_skipped(on, users):
    _, h = candidate(on, users)
    v = up(on, h, pdf(0)).json()["import"]
    assert on.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=complete(on, h, v)).status_code == 200
    before = records(on, h)
    r = up(on, h, pdf(0, layout="docx_classic"), DOCX)                               # the same history, another file
    assert r.status_code == 201 and r.json()["result"] == "nothing_new" and r.json()["skipped"]["already_in_profile"] > 0
    assert records(on, h)["employments"] == before["employments"]
    assert on.get("/api/v1/resume-import", headers=h).json()["status"] == "none"


def test_a_newer_resume_updates_a_confirmed_profile_only_through_one_explicit_review(on, users):
    _, h = candidate(on, users)
    v = up(on, h, pdf(0)).json()["import"]
    assert on.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=complete(on, h, v)).status_code == 200
    before = {e["id"]: e for e in records(on, h)["employments"]}
    newer = dict(P[0])
    newer["jobs"] = [dict(P[0]["jobs"][0], title="Staff Software Engineer", employer="Nimbus Payments Pvt Ltd", start="2024-01",
                          bullets=["Set the technical direction for the payouts platform across three teams."]),
                     dict(P[0]["jobs"][0]), dict(P[0]["jobs"][1], start="2018-08")]
    newer["jobs"][1]["end"] = "2023-12"
    r = up(on, h, render(newer, "pdf_classic"))
    view = r.json()["import"]
    assert r.status_code == 201 and view["mode"] == "update"
    state = {j["title"]: j["status"] for j in view["work"]}
    assert [t for t, s in state.items() if s == "new"] == ["Staff Software Engineer"]
    assert {t for t, s in state.items() if s == "update"} == {"Senior Software Engineer", "Software Engineer"}   # visible, not silent
    assert {e["id"]: e for e in records(on, h)["employments"]} == before                                   # nothing changed yet
    decline = next(j for j in view["work"] if j["title"] == "Software Engineer")
    c = on.post(f"/api/v1/resume-import/{view['import_id']}/confirm", headers=h,
                json=complete(on, h, view, {"work": [{"id": decline["id"], "remove": True}]}))
    assert c.status_code == 200
    after = {e["title_raw"]: e for e in records(on, h)["employments"]}
    assert after["Staff Software Engineer"]["review"]["status"] == "user_confirmed"
    assert after["Senior Software Engineer"]["review"]["status"] == "user_corrected"                      # with history
    assert after["Software Engineer"]["start"] == before[after["Software Engineer"]["id"]]["start"]       # declined: unchanged


def test_legacy_pasted_text_suggestions_can_be_cleared(on, users):
    _, h = candidate(on, users)
    on.post("/api/v1/proposals/from-text", headers=h, json={"text": "Analyst at Example Co, Jan 2020 - Present\n- Did a thing well (F1)"})
    assert on.get("/api/v1/resume-import", headers=h).json()["legacy_suggestions"] > 0
    assert on.delete("/api/v1/resume-import", headers=h).json()["suggestions"] > 0
    assert on.get("/api/v1/proposals", headers=h).json() == []


def test_another_users_import_is_invisible_and_cannot_be_confirmed(on, users):
    _, h1 = candidate(on, users)
    v = up(on, h1, pdf(0)).json()["import"]
    _, h2 = candidate(on, users)
    assert on.get("/api/v1/resume-import", headers=h2).json()["status"] == "none"
    r = on.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h2, json={})
    assert r.status_code == 404 and v["import_id"] not in r.text
    on.delete("/api/v1/resume-import", headers=h2)
    assert on.get("/api/v1/resume-import", headers=h1).json()["status"] == "ready"   # untouched by the other user


def test_confirmation_leads_to_a_resolvable_current_role(on, users):
    _, h = candidate(on, users)
    v = up(on, h, pdf(0)).json()["import"]
    job = v["work"][0]
    assert job["employment_type"] == "unknown"                                       # not stated in the resume: not inferred
    role = job["role"]
    assert role["suggested_role_id"] and any(c["role_id"] == role["suggested_role_id"] for c in role["choices"])
    assert on.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=complete(on, h, v,
            {"work": [{"id": job["id"], "employment_type": "full_time", "role_id": role["suggested_role_id"]}]})).status_code == 200
    current = next(e for e in records(on, h)["employments"] if e["title_raw"] == "Senior Software Engineer")
    assert current["employment_type"] == "full_time" and current["review"]["status"] == "user_corrected"
    assert current["mapping"]["mapping_status"] in ("auto_high_confidence", "confirmed", "needs_review")
    position = on.get("/api/v1/current-position", headers=h).json()
    assert position["status"] == "known" and position["title_raw"] == "Senior Software Engineer", position
