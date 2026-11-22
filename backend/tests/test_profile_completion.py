"""Product Phase 2: profile completion.

Covers manual entry, draft edits, optimistic concurrency, update proposals (pasted text and a resume uploaded into a
non-empty profile), persistence across sessions and devices, ownership isolation and the profile version.

Integration tests use the real API and the local database. Users are transient (deleted at teardown); every value is an
invented test value. Real-resume uploads are switched on only inside the test client, as in Phase 6.1; the server
default stays off.
"""
import pytest

from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import candidate, consent_document, make_client  # noqa: F401  (fixtures)
from tests.test_real_resume_contributions import contribution_document, post_real, rc, resume  # noqa: F401  (fixtures)

pytestmark = pytest.mark.integration


@pytest.fixture
def c(base_settings, consent_document):
    with make_client(base_settings) as client:
        yield client


def records(c, h):
    r = c.get("/api/v1/profile/records", headers=h)
    assert r.status_code == 200
    return r.json()


def job(c, h, title, **kw):
    r = c.post("/api/v1/entries/employment", headers=h, json={"title_raw": title, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def admin(users, sql, *args):
    return users.admin.execute(sql, args).fetchall()


def ym(y, m=None):
    return {"year": y, "month": m, "precision": "month"} if m else {"year": y, "precision": "year"}


# --------------------------------------------------------------------------- manual entry
def test_a_manual_employment_is_stored_as_the_users_own_pending_draft(c, users):
    u, h = candidate(c, users)
    out = job(c, h, "  Business  Analyst ", employer_name="Northwind Finance", employment_type="full_time", start=ym(2019, 4),
              end={"precision": "ongoing"}, responsibilities=["Wrote requirements for settlements", "  "],
              achievements=["Cut reconciliation time by a third"], skills=["SQL", "SQL", "Stakeholder management"])
    assert out["created"] == {"employment": 1, "evidence": 2, "skill": 2} and not out["confirmed"] and out["placement"] == "latest"
    v = records(c, h)
    (e,) = v["employments"]
    assert (e["title_raw"], e["employer_name"], e["is_current_as_stated"]) == ("Business Analyst", "Northwind Finance", True)
    assert {(x["evidence_type"], x["review"]["status"], x["review"]["origin"]) for x in e["evidence"]} == {
        ("responsibility", "pending_review", "user_stated"), ("achievement", "pending_review", "user_stated")}
    assert e["review"]["is_own_draft"] and e["review"]["version"]
    assert admin(users, "select entry_source, verification_status::text from public.trace_candidate_employments where owner_user_id = %s",
                 u) == [("manual_entry", "unverified")]          # entering is not verifying


def test_save_and_confirm_is_the_users_explicit_review(c, users):
    u, h = candidate(c, users)
    job(c, h, "Software Engineer", start=ym(2018), end=ym(2021), responsibilities=["Built payment APIs"], confirm=True)
    r = c.post("/api/v1/entries/skill", headers=h, json={"skill_text_raw": "Python", "confirm": True})
    assert r.status_code == 201 and r.json()["confirmed"]
    v = records(c, h)
    assert {x["review"]["status"] for x in v["employments"] + v["employments"][0]["evidence"] + v["other_records"]} == {"user_confirmed"}


def test_education_and_activities_are_entered(c, users):
    u, h = candidate(c, users)
    assert c.post("/api/v1/entries/education", headers=h, json={"qualification_title_raw": "B.Tech", "qualification_level": "bachelor",
                                                                "field_of_study": "Computer Science", "end": ym(2017)}).status_code == 201
    assert c.post("/api/v1/entries/activity", headers=h, json={"activity_type": "certification", "title_raw": "Product discovery course",
                                                               "organisation_raw": "An invented institute"}).status_code == 201
    kinds = sorted(o["kind"] for o in records(c, h)["other_records"])
    assert kinds == ["activity", "education"]
    bad = c.post("/api/v1/entries/education", headers=h, json={"qualification_title_raw": "MBA", "qualification_level": "phd-ish"})
    assert bad.status_code == 422


def test_new_roles_are_placed_in_stated_order(c, users):
    u, h = candidate(c, users)
    job(c, h, "Product Manager", start=ym(2022, 1), end={"precision": "ongoing"})
    early = job(c, h, "Business Analyst", start=ym(2016, 6), end=ym(2019, 12))          # by start date: before the PM role
    assert early["placement"] == "by_start_date"
    middle = job(c, h, "Product Analyst", start=ym(2020, 1), end=ym(2021, 12))
    assert [e["title_raw"] for e in records(c, h)["employments"]] == ["Business Analyst", "Product Analyst", "Product Manager"]
    assert middle["placement"] == "by_start_date"
    undated = job(c, h, "Consultant")                                                      # no date: the latest
    assert undated["placement"] == "latest" and records(c, h)["employments"][-1]["title_raw"] == "Consultant"


def test_placing_before_a_confirmed_role_is_an_ordering_event_not_a_correction(c, users):
    u, h = candidate(c, users)
    job(c, h, "Product Manager", start=ym(2021, 1), confirm=True)
    pm = records(c, h)["employments"][0]
    out = job(c, h, "Analyst", before_employment_id=pm["id"])
    assert out["reordered"] == 1 and out["placement"] == "before_selected"
    v = records(c, h)
    assert [e["title_raw"] for e in v["employments"]] == ["Analyst", "Product Manager"]
    moved = v["employments"][1]
    assert moved["review"]["status"] == "user_confirmed"                                   # still Confirmed (D10)
    assert c.get(f"/api/v1/records/employment/{moved['id']}/corrections", headers=h).json() == []   # not a correction
    (ev,) = c.get(f"/api/v1/records/employment/{moved['id']}/order-events", headers=h).json()
    assert (ev["previous_position"], ev["new_position"], ev["review_status_at_move"], ev["cause"]) == (1, 2, "user_confirmed", "role_inserted")
    (row,) = admin(users, "select moved_by, caused_by_employment_id from public.trace_candidate_order_events where employment_id = %s", moved["id"])
    assert (str(row[0]), str(row[1])) == (str(u), v["employments"][0]["id"])               # the actor, and the role that caused it


def test_unreviewed_extracted_roles_move_without_review_and_rejected_roles_never_move(rc, users):
    u, h = candidate(rc, users)
    assert post_real(rc, h, resume()).status_code == 201                                   # extracted, pending review
    before = {e["id"]: e["review"]["status"] for e in records(rc, h)["employments"]}
    out = rc.post("/api/v1/entries/employment", headers=h, json={"title_raw": "Intern", "start": ym(2014, 1), "end": ym(2014, 6)})
    assert out.status_code == 201 and out.json()["reordered"] == len(before)               # earliest: every role moves down
    after = records(rc, h)["employments"]
    assert after[0]["title_raw"] == "Intern"
    assert {e["id"]: e["review"]["status"] for e in after if e["id"] in before} == before   # still pending: not reviewed by a move
    assert admin(users, "select count(*) from public.trace_candidate_corrections where owner_user_id = %s", u)[0][0] == 0
    ba = next(e for e in after if e["title_raw"] == "Business Analyst")
    assert rc.post(f"/api/v1/records/employment/{ba['id']}/reject", headers=h).status_code == 200
    anchor = rc.post("/api/v1/entries/employment", headers=h, json={"title_raw": "Consultant", "before_employment_id": ba["id"]})
    assert (anchor.status_code, anchor.json()["error"]) == (409, "record_not_editable")    # a rejected role is no anchor
    blocked = rc.post("/api/v1/entries/employment", headers=h, json={"title_raw": "Trainee", "start": ym(2013, 1)})
    assert (blocked.status_code, blocked.json()["error"]) == (409, "record_not_editable")  # it would have to move
    assert "reinstate" in blocked.json()["message"]



def test_an_explicit_position_is_kept_when_dates_agree_or_are_ambiguous(c, users):
    u, h = candidate(c, users)
    job(c, h, "Engineer", start=ym(2016, 1))
    later = job(c, h, "Product Manager", start=ym(2022, 1))
    pm = next(e for e in records(c, h)["employments"] if e["title_raw"] == "Product Manager")
    out = job(c, h, "Analyst", start=ym(2019, 3), before_employment_id=pm["id"])          # explicit position and a date
    assert out["placement"] == "before_selected"
    assert [e["title_raw"] for e in records(c, h)["employments"]] == ["Engineer", "Analyst", "Product Manager"]
    eng = next(e for e in records(c, h)["employments"] if e["title_raw"] == "Engineer")
    amb = job(c, h, "Consultant", start=ym(2016), before_employment_id=eng["id"])          # year 2016 vs Jan 2016: ambiguous
    assert amb["placement"] == "before_selected" and records(c, h)["employments"][0]["title_raw"] == "Consultant"
    assert later["placement"] == "latest"


def test_an_explicit_position_that_clear_dates_contradict_is_refused(c, users):
    u, h = candidate(c, users)
    job(c, h, "Engineer", start=ym(2016, 1))
    job(c, h, "Product Manager", start=ym(2022, 1))
    eng, pm = records(c, h)["employments"]
    after_target = c.post("/api/v1/entries/employment", headers=h,
                          json={"title_raw": "Analyst", "start": ym(2023, 5), "before_employment_id": pm["id"]})
    assert (after_target.status_code, after_target.json()["error"]) == (409, "order_conflicts_with_dates")
    before_prev = c.post("/api/v1/entries/employment", headers=h,
                         json={"title_raw": "Intern", "start": ym(2012, 5), "before_employment_id": pm["id"]})
    assert (before_prev.status_code, before_prev.json()["error"]) == (409, "order_conflicts_with_dates")
    assert [e["title_raw"] for e in records(c, h)["employments"]] == ["Engineer", "Product Manager"]   # nothing written
    assert admin(users, "select count(*) from public.trace_candidate_order_events where owner_user_id = %s", u)[0][0] == 0


def test_order_history_is_private_to_the_owner(c, users):
    a, ha = candidate(c, users)
    b, hb = candidate(c, users)
    job(c, ha, "Engineer", confirm=True)
    e = records(c, ha)["employments"][0]
    assert c.get(f"/api/v1/records/employment/{e['id']}/order-events", headers=hb).status_code == 404
    assert c.get(f"/api/v1/records/employment/{e['id']}/order-events", headers=ha).json() == []

# --------------------------------------------------------------------------- draft edits and optimistic concurrency
def test_drafts_are_edited_with_their_version_and_a_stale_version_is_refused(c, users):
    u, h = candidate(c, users)
    job(c, h, "Analist", employer_name="Nortwind", responsibilities=["Wrote specs"])
    e = records(c, h)["employments"][0]
    v1 = e["review"]["version"]
    ok = c.patch(f"/api/v1/records/employment/{e['id']}", headers={**h, "If-Match": v1},
                 json={"title_raw": "Analyst", "employer_name": "Northwind", "start": ym(2019, 2)})
    assert ok.status_code == 200, ok.text
    e2 = records(c, h)["employments"][0]
    assert (e2["title_raw"], e2["employer_name"], e2["start"]["year"]) == ("Analyst", "Northwind", 2019)
    assert e2["review"]["version"] != v1
    stale = c.patch(f"/api/v1/records/employment/{e['id']}", headers={**h, "If-Match": v1}, json={"title_raw": "Lost update"})
    assert (stale.status_code, stale.json()["error"]) == (409, "record_changed")
    assert records(c, h)["employments"][0]["title_raw"] == "Analyst"                       # nothing was overwritten
    x = e2["evidence"][0]
    assert c.patch(f"/api/v1/records/evidence/{x['id']}", headers={**h, "If-Match": x["review"]["version"]},
                   json={"evidence_text": "Wrote product specifications"}).status_code == 200
    assert c.post(f"/api/v1/records/evidence/{x['id']}/confirm", headers={**h, "If-Match": x["review"]["version"]}).status_code == 409
    fresh = records(c, h)["employments"][0]["evidence"][0]["review"]["version"]
    assert c.post(f"/api/v1/records/evidence/{x['id']}/confirm", headers={**h, "If-Match": fresh}).status_code == 200
    assert c.post(f"/api/v1/records/employment/{e['id']}/confirm", headers={**h, "If-Match": "yesterday"}).status_code == 422
    assert c.post(f"/api/v1/records/employment/{e['id']}/confirm", headers=h).status_code == 200      # no If-Match: unchanged API


def test_reviewed_records_are_corrected_not_edited(c, users):
    u, h = candidate(c, users)
    job(c, h, "Engineer", confirm=True)
    e = records(c, h)["employments"][0]
    r = c.patch(f"/api/v1/records/employment/{e['id']}", headers=h, json={"title_raw": "Senior Engineer"})
    assert (r.status_code, r.json()["error"]) == (409, "correction_not_available")
    stale = c.post(f"/api/v1/records/employment/{e['id']}/corrections", headers={**h, "If-Match": "2001-01-01T00:00:00+00:00"},
                   json={"title_raw": "Senior Engineer"})
    assert (stale.status_code, stale.json()["error"]) == (409, "record_changed")
    assert c.post(f"/api/v1/records/employment/{e['id']}/corrections", headers={**h, "If-Match": e["review"]["version"]},
                  json={"title_raw": "Senior Engineer"}).status_code == 201


# --------------------------------------------------------------------------- persistence and isolation
def test_the_profile_persists_across_sessions_and_devices(base_settings, consent_document, users):
    with make_client(base_settings) as first:
        u, h = candidate(first, users)
        job(first, h, "Software Engineer", employer_name="Zentrix", start=ym(2018, 7), responsibilities=["Built APIs"], confirm=True)
        before = records(first, h)
    with make_client(base_settings) as other_device:                                       # new app instance, new token
        again = records(other_device, auth(token(u)))
    assert again == before                                                                  # same records, versions and fingerprint
    assert again["profile_version"] and again["employments"][0]["title_raw"] == "Software Engineer"


def test_another_user_cannot_see_or_change_the_records_or_proposals(c, users):
    a, ha = candidate(c, users)
    b, hb = candidate(c, users)
    job(c, ha, "Product Manager", responsibilities=["Owned the roadmap"])
    e = records(c, ha)["employments"][0]
    c.post("/api/v1/proposals/from-text", headers=ha, json={"text": "Data Analyst, Invented Co Jan 2015 - Dec 2016"})
    (p,) = c.get("/api/v1/proposals", headers=ha).json()
    assert c.patch(f"/api/v1/records/employment/{e['id']}", headers=hb, json={"title_raw": "x"}).status_code == 404
    assert c.post(f"/api/v1/records/employment/{e['id']}/confirm", headers=hb).status_code == 404
    assert c.post(f"/api/v1/proposals/{p['id']}/accept", headers=hb).status_code == 404
    assert c.post(f"/api/v1/proposals/{p['id']}/dismiss", headers=hb).status_code == 404
    assert c.get("/api/v1/proposals", headers=hb).json() == [] and records(c, hb)["employments"] == []
    r = c.post("/api/v1/entries/employment", headers=hb, json={"title_raw": "x", "before_employment_id": e["id"]})
    assert r.status_code == 404                                                             # another user's role is not a position


def test_the_profile_version_changes_with_content_and_is_stable_otherwise(c, users):
    u, h = candidate(c, users)
    empty = records(c, h)["profile_version"]
    job(c, h, "Analyst")
    v1 = records(c, h)["profile_version"]
    assert v1 != empty and records(c, h)["profile_version"] == v1
    e = records(c, h)["employments"][0]
    c.post(f"/api/v1/records/employment/{e['id']}/confirm", headers=h)
    assert records(c, h)["profile_version"] not in (v1, empty)                              # a review changes it too


# --------------------------------------------------------------------------- update proposals: pasted text
PASTE = """Priya Invented | priya.invented@example.com | +91 90000 12345
Product Owner, Acme Payments Jan 2021 - Present
- Ran discovery interviews with merchants
- Prioritised the integrations backlog
Business Analyst | Northwind | Jul 2017 - Dec 2020
- Wrote requirements for the settlements module
SKILLS
SQL, Jira"""


def test_pasted_text_becomes_proposals_and_nothing_enters_the_profile_until_accepted(c, users):
    u, h = candidate(c, users)
    r = c.post("/api/v1/proposals/from-text", headers=h, json={"text": PASTE})
    assert r.status_code == 201 and r.json()["proposed"] == 4                              # 2 roles, 2 skills
    assert records(c, h)["employments"] == []                                              # nothing added yet
    props = c.get("/api/v1/proposals", headers=h).json()
    assert sorted(p["kind"] for p in props) == ["employment", "employment", "skill", "skill"]
    owner = next(p for p in props if p["kind"] == "employment" and "Product Owner" in p["summary"])
    assert owner["proposed"]["responsibilities"] == ["Ran discovery interviews with merchants", "Prioritised the integrations backlog"]
    stored = " ".join(str(x) for row in admin(users, "select proposed::text, source_sha256 from public.trace_profile_update_proposals "
                                                   "where owner_user_id = %s", u) for x in row)
    for leak in ("Priya", "example.com", "90000"):                                          # header and contact lines never stored
        assert leak not in stored
    again = c.post("/api/v1/proposals/from-text", headers=h, json={"text": PASTE}).json()
    assert (again["proposed"], again["already_open"]) == (0, 4)                            # never duplicated
    acc = c.post(f"/api/v1/proposals/{owner['id']}/accept", headers=h, json={})
    assert acc.status_code == 200 and acc.json()["status"] == "accepted"
    (e,) = records(c, h)["employments"]
    assert (e["title_raw"], e["employer_name"], e["review"]["status"], e["review"]["origin"]) == (
        "Product Owner", "Acme Payments", "pending_review", "user_stated")
    assert len(e["evidence"]) == 2
    assert c.post(f"/api/v1/proposals/{owner['id']}/accept", headers=h).status_code == 409   # decided once
    skill = next(p for p in props if p["kind"] == "skill")
    assert c.post(f"/api/v1/proposals/{skill['id']}/dismiss", headers=h).json()["status"] == "dismissed"
    assert admin(users, "select proposed from public.trace_profile_update_proposals where proposal_id = %s", skill["id"]) == [(None,)]
    third = c.post("/api/v1/proposals/from-text", headers=h, json={"text": PASTE}).json()
    assert third["already_in_profile"] >= 3                                                # the accepted role and its bullets


def test_a_changed_date_of_a_confirmed_role_is_a_possible_update_applied_as_a_correction(c, users):
    u, h = candidate(c, users)
    job(c, h, "Business Analyst", employer_name="Northwind", start=ym(2017, 6), end=ym(2020, 12),
        responsibilities=["Wrote requirements for the settlements module"], confirm=True)
    c.post("/api/v1/proposals/from-text", headers=h, json={"text": PASTE})
    upd = next(p for p in c.get("/api/v1/proposals", headers=h).json() if p["change"] == "possible_update")
    assert upd["applies_to"] == "Business Analyst at Northwind"
    assert upd["proposed"]["start"]["month"] == 7 and upd["current"]["start"]["month"] == 6
    assert records(c, h)["employments"][0]["start"]["month"] == 6                          # untouched until accepted
    assert c.post(f"/api/v1/proposals/{upd['id']}/accept", headers=h).status_code == 200
    e = records(c, h)["employments"][0]
    assert (e["start"]["month"], e["review"]["status"]) == (7, "user_corrected")
    (hist,) = c.get(f"/api/v1/records/employment/{e['id']}/corrections", headers=h).json()
    assert "start_month" in hist["changed_fields"]                                         # the previous value is kept


def test_text_without_career_entries_proposes_nothing(c, users):
    u, h = candidate(c, users)
    r = c.post("/api/v1/proposals/from-text", headers=h, json={"text": "Hello, I would like to become a product manager."})
    assert r.status_code == 201 and r.json()["proposed"] == 0 and "No career entries" in r.json()["note"]
    assert c.post("/api/v1/proposals/from-text", headers=h, json={"text": "x" * 20_001}).status_code == 422


# --------------------------------------------------------------------------- update proposals: a second resume
def test_a_second_resume_never_overwrites_the_profile(rc, users):
    u, h = candidate(rc, users)
    assert post_real(rc, h, resume()).status_code == 201
    count = lambda: admin(users, "select count(*) from public.trace_candidate_employments where owner_user_id = %s", u)[0][0]   # noqa: E731
    n = count()
    same = post_real(rc, h, resume())
    assert (same.status_code, same.json()["status"]) == (201, "unchanged") and count() == n
    newer = post_real(rc, h, resume(extra=("- Launched a merchant analytics dashboard",)))
    body = newer.json()
    assert (newer.status_code, body["status"]) == (201, "proposed") and body["proposals"]["proposed"] == 1
    assert count() == n                                                                    # nothing merged
    (p,) = rc.get("/api/v1/proposals", headers=h).json()
    assert (p["kind"], p["source"], p["summary"]) == ("evidence", "resume_update", "Launched a merchant analytics dashboard")
    assert p["applies_to"].startswith("Business Analyst")
    assert rc.post(f"/api/v1/proposals/{p['id']}/accept", headers=h, json={"confirm": True}).json()["status"] == "accepted"
    assert count() == n


# --------------------------------------------------------------------------- parser (pure, no database)
@pytest.mark.parametrize("text,expected", [
    ("Priya Invented\npriya@example.com | +91 90000 12345\nAcme Payments Jan 2021 - Present\n- Ran discovery",
     [("Acme Payments", None)]),                                            # resume-style name and contact header dropped
    ("Acme Payments\nProduct Owner Jan 2021 - Present", [("Product Owner", "Acme Payments")]),   # an employer line is kept
    ("Product Owner, Acme Payments Jan 2021 - Present\nSKILLS\nSQL", [("Product Owner", "Acme Payments")]),
])
def test_pasted_text_reads_job_entries_and_never_contact_lines(text, expected):
    from app.services.resume_extraction.real_parser import parse_text
    r = parse_text(text, untitled_is_experience=True).record
    names = {e["employer_id"]: e["employer_name"] for e in r["employers"]}
    assert [(j["title"], names.get(j.get("employer_id"))) for j in r["employments"]] == expected
    assert "example.com" not in str(r) and "90000" not in str(r)


def test_resume_parsing_is_unchanged_by_the_text_entry_point():
    from app.services.resume_extraction.real_parser import parse_text
    r = parse_text("Priya Invented\nEXPERIENCE\nProduct Owner, Acme Payments Jan 2021 - Present")   # default: header stays header
    assert [j["title"] for j in r.record["employments"]] == ["Product Owner"] and r.identity["names"] == ["Priya Invented"]



# --------------------------------------------------------------------------- placement rules (pure)
def _row(i, seq, status="user_confirmed", lo=None, hi=None, title=None):
    import uuid as _u
    return {"candidate_employment_id": _u.UUID(int=i), "sequence_index": seq, "review_status": status, "start_lo": lo,
            "start_hi": hi if hi is not None else lo, "title_raw": title or f"role {i}", "information_origin": "user_stated"}


def test_place_uses_gaps_and_moves_only_when_needed():
    from app.services.candidate_profile.entries import place
    rows = [_row(1, 1, lo=100), _row(2, 3, lo=200)]
    assert place(rows, None, (150, 150))[:2] == (2, [])                                     # the free place between 1 and 3
    seq, moves, how = place(rows, None, (50, 50))
    assert (seq, [(m[0]["sequence_index"], m[1]) for m in moves], how) == (1, [(1, 2), (3, 4)], "by_start_date")
    assert place([], None, (None, None)) == (1, [], "latest")


def test_place_honours_an_explicit_position_over_the_date_rule():
    from app.services.candidate_profile.entries import place
    rows = [_row(1, 1, lo=100), _row(2, 2, lo=300)]
    # Start bounds 90-120 (ambiguous against role 1): the date rule alone would place it first; the user chose second.
    seq, _, how = place(rows, rows[1]["candidate_employment_id"], (90, 120))
    assert (seq, how) == (2, "before_selected")


def test_place_refuses_rejected_anchors_and_moves_and_date_contradictions():
    from app.services.candidate_profile.entries import place
    from app.services.candidate_profile.repository import OrderConflictsWithDates, RecordNotEditable
    rows = [_row(1, 1, lo=100), _row(2, 2, status="rejected", lo=200), _row(3, 3, lo=300)]
    with pytest.raises(RecordNotEditable):
        place(rows, rows[1]["candidate_employment_id"], (None, None))
    with pytest.raises(RecordNotEditable):
        place(rows, rows[0]["candidate_employment_id"], (None, None))                        # the rejected role would move
    gap = [_row(1, 1, lo=100), _row(2, 3, status="rejected", lo=200)]
    with pytest.raises(RecordNotEditable):
        place(gap, gap[1]["candidate_employment_id"], (None, None))                           # a gap would avoid moving it: still no anchor
    with pytest.raises(OrderConflictsWithDates):
        place(rows, rows[2]["candidate_employment_id"], (400, 400))                           # starts after the anchor
    with pytest.raises(OrderConflictsWithDates):
        place(rows, rows[2]["candidate_employment_id"], (50, 50))                             # starts before the previous role
    seq, _, _ = place(rows, rows[2]["candidate_employment_id"], (None, None))                 # unknown dates: user's order kept
    assert seq == 3
