"""Phase 7: the two small API additions the redesigned frontend needs.

1. GET /api/v1/config reports server-controlled features (real_resume_uploads), so the browser never probes by uploading.
2. GET /api/v1/profile/records carries education and activity detail (institution, field, level, grade notes, end
   date; activity type and organisation), so the owner can review what was extracted. Own records only, as before.
"""
import pytest

from tests.test_api_foundation import assert_no_secrets, base_settings, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import PDF, candidate, consent_document, make_client, upload  # noqa: F401
from tests.test_real_resume_contributions import contribution_document, post_real, rc, resume  # noqa: F401

pytestmark = pytest.mark.integration


def test_config_reports_the_real_upload_feature_only(base_settings):
    with make_client(base_settings) as c:
        body = c.get("/api/v1/config").json()
    assert body["features"] == {"real_resume_uploads": False, "career_research": False}                   # off by default
    assert set(body) == {"supabase_url", "supabase_anon_key", "data_mode", "features"}


def test_records_carry_education_and_activity_detail_for_the_owner(rc, users):
    u, h = candidate(rc, users)
    assert post_real(rc, h, resume()).status_code == 201
    other = rc.get("/api/v1/profile/records", headers=h).json()["other_records"]
    edu = next(o for o in other if o["kind"] == "education")
    assert edu["institution_name"] == "College of Engineering Pune" and edu["field_of_study"] in ("Computer Engineering", None)
    assert edu["qualification_level"] and "8.4" in (edu["notes"] or "") and edu["end"]["year"] == 2016
    act = next(o for o in other if o["kind"] == "activity")
    assert act["activity_type"] == "certification" and act["organisation"] in ("Scrum Alliance", None)
    skill = next(o for o in other if o["kind"] == "skill")
    assert skill["institution_name"] is None and skill["activity_type"] is None
    v, hv = candidate(rc, users)                                                  # another user sees only their own (empty)
    assert rc.get("/api/v1/profile/records", headers=hv).json()["other_records"] == []


def test_synthetic_records_still_load(base_settings, consent_document, users):
    with make_client(base_settings, trace_demo_fixtures=True) as c:
        u, h = candidate(c, users)
        assert upload(c, h, PDF["SYN-001"]).status_code == 201
        r = c.get("/api/v1/profile/records", headers=h)
        assert r.status_code == 200 and any(o["kind"] == "education" for o in r.json()["other_records"])
        assert_no_secrets(r)
