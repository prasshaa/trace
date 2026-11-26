"""Release (D52): target-role selectability, landing-page positioning, production notice and data-use notice.

Static checks of the shipped frontend plus API checks on transient local users. The browser journey (desktop and
mobile) is the scratchpad e2e script; these keep the contracts from regressing.
"""
import re

import pytest

from app.api.routes import _document_view
from app.demo import consent
from tests.test_frontend_phase71 import code_of, visible_strings
from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import candidate, consent_document, make_client  # noqa: F401  (fixtures)
from app.config import Settings

WEB = Settings().trace_web_dir
CSS = "\n".join(p.read_text() for p in (WEB / "css").glob("*.css"))


# ------------------------------------------------------------------------------------------------- target roles
def test_every_role_option_is_selectable_and_looks_it():
    t = code_of("views/target.js")
    assert "disabled" not in re.sub(r"explore\.disabled|disabled: !chosen", "", t)     # no option is ever disabled
    assert "aria-disabled" not in t and "pointer-events" not in CSS.split(".role-option")[1][:2000]
    rules = re.findall(r"\.role-option[^{]*\{[^}]*\}", CSS)
    joined = "\n".join(rules)
    assert "not-allowed" not in joined and "dashed" not in joined and "surface-sunk" not in joined
    assert ".role-option:focus-within" in joined and ".role-option:has(input:checked)" in joined
    assert '"Your current role"' in t
    assert "\"You can choose the same role if you're exploring a move to another company.\"" in t
    assert "isCurrent(r.role_id)" in t          # computed for whichever role is the user's, never a fixed role


# ------------------------------------------------------------------------------------------------- landing
def test_landing_explains_the_differentiator_audience_and_research_honestly():
    s = code_of("views/signin.js")
    for text in ("Find your path to your target role.", "Upload your resume. Choose your target. See your path.",
                 "Your career decisions, grounded in real career journeys.", "Understand where you stand",
                 "Explore real career paths", "Know what to do next", "Who Trace is for", "Moving into product management",
                 "Product Managers aiming higher", "Making a lateral move", "observed career patterns from real Product Managers",
                 "non-representative sample", "doesn't predict your chances", "Coming next: career research"):
        assert text in s, text
    hero = s[s.index('"Find your path'):s.index('h("div.auth-card"')]
    assert "439" not in hero                                                    # the sample size only in the methodology note
    assert s.count("439") == 1 and "methodology()" in s and "research ? methodology() : upcoming()" in s
    shown = " ".join(visible_strings("views/signin.js")).lower()
    for word in ("bright", "linkedin", "scrap", "data provider", "individual profiles", "ai career coach", "career intelligence platform"):
        assert word not in shown, word


@pytest.mark.integration
def test_config_reports_the_research_flag(base_settings, consent_document):
    with make_client(base_settings) as c:
        assert c.get("/api/v1/config").json()["features"]["career_research"] is False
    with make_client(base_settings, trace_career_origins=True) as c:
        assert c.get("/api/v1/config").json()["features"]["career_research"] is True


@pytest.mark.integration
def test_research_is_unreachable_when_off(base_settings, consent_document, users):
    with make_client(base_settings) as c:
        _, h = candidate(c, users)
        for path in ("/api/v1/career-origins", "/api/v1/career-origins?plan=00000000-0000-0000-0000-000000000000"):
            r = c.get(path, headers=h)
            assert r.status_code == 404 and "pathways" not in r.text.lower()
        for path in ("/app/india-pm-origins-v3.json", "/app/content/india-pm-origins-v3.json", "/india-pm-origins-v3.json"):
            assert c.get(path).status_code == 404


# ------------------------------------------------------------------------------------------------- notices
def test_production_notice_is_verified_by_version_and_describes_the_lifecycle():
    doc = {"consent_version": consent.PRODUCTION_VERSION, "title": consent.PRODUCTION_TITLE, "body_sha256": consent.PRODUCTION_BODY_SHA256}
    v = _document_view(doc, "resume_processing")
    assert v.text_verified and v.text == consent.PRODUCTION_BODY
    assert not _document_view({**doc, "body_sha256": "0" * 64}, "resume_processing").text_verified
    assert not _document_view(doc, "deidentified_shared_dataset").text_verified       # never verified for another purpose
    b = consent.PRODUCTION_BODY
    for phrase in ("aren't saved anywhere", "isn't sent to any other company or AI service", "private draft",
                   "Updating from a new resume", "Clearing your profile", "isn't offered on this site",
                   "keeps no copy of your contact details", "withdraw this agreement"):
        assert phrase in b, phrase
    assert "local" not in b.lower() and "lawyer" not in b.lower()
    assert _document_view({"consent_version": consent.VERSION, "title": consent.TITLE, "body_sha256": consent.BODY_SHA256},
                          "resume_processing").text_verified                          # the local notice still works locally
    assert "/^trace-local-|-demo-/.test(" in code_of("views/setup.js")                  # the local label only on local notices


@pytest.mark.integration
def test_where_sharing_is_not_offered_the_onboarding_notice_says_so_and_records_private(base_settings, consent_document, users):
    with make_client(base_settings, trace_contribution_policy="disabled") as c:
        _, h = candidate(c, users)
        n = c.get("/api/v1/sharing", headers=h).json()["notice"]
        assert n["version"] == "trace-data-use-notice-private-v1" and "isn't offered" in " ".join(n["paragraphs"])
        assert c.post("/api/v1/sharing/notice", headers=h, json={"notice_version": "trace-data-use-notice-v2"}).status_code == 409
        r = c.post("/api/v1/sharing/notice", headers=h, json={"notice_version": n["version"]}).json()
        assert r["notice_acknowledged"] and r["preference"] == "private"
    with make_client(base_settings) as c:                                   # opt-in servers keep the existing notice
        _, h = candidate(c, users)
        assert c.get("/api/v1/sharing", headers=h).json()["notice"]["version"] == "trace-data-use-notice-v3"


def test_every_frontend_module_parses():
    """A syntax error in one module blanks the whole app; static string checks can't see it."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    bad = []
    for p in sorted((WEB / "js").rglob("*.js")):
        if "vendor" in p.parts:
            continue
        r = subprocess.run([node, "--input-type=module", "--check"], input=p.read_text(), capture_output=True, text=True)
        if r.returncode:
            bad.append(p.name)
    assert not bad, bad
