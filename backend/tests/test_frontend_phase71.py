"""Phase 7.1: static guards on the product journey (no browser needed). The journey itself is exercised in real Chrome
separately; these checks keep its promises from regressing silently."""
import re

from app.config import Settings

WEB = Settings().trace_web_dir
JS = {p.relative_to(WEB / "js").as_posix(): p.read_text() for p in (WEB / "js").rglob("*.js") if "vendor" not in p.parts}
HTML = (WEB / "index.html").read_text()


def code_of(name):
    return re.sub(r"(?m)^\s*//.*$|\s//\s.*$", "", JS[name])


def visible_strings(name):
    """String literals that read as text for people (contain a space), from code only."""
    found = [s for s in re.findall(r'"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`', code_of(name)) for s in s if s and " " in s]
    stripped = (re.sub(r"\$\{[^}]*\}", "", t) for t in found)                # template expressions are code, not text
    return [t for t in stripped if " " in t.strip()]


def test_there_is_no_sample_or_demo_journey():
    code = "\n".join(code_of(n) for n in JS)
    for bad in ("/api/v1/demo", "/api/v1/resumes\"", "samples(", "samplePdf", "uploadResume(", "Choose a sample", "sample profile",
                "Explore with a sample", "mode-tag", '"Demo"'):
        assert bad not in code, bad
    assert "views/contribution.js" not in JS


def test_a_disabled_upload_is_stated_truthfully_and_never_replaced():
    setup = code_of("views/setup.js")
    assert "if (!offered) return unavailable(body);" in setup
    assert "Resume upload is switched off on this server" in setup             # resume-first (D47): no manual stand-in
    assert "importResume" in setup and "fixture" not in setup.lower().replace("fixture_profile_conflict", "")
    for gone in ("Add a job", "Add a qualification", "Add a certification or project", "Add a skill", "Paste your experience",
                 "addPanel", "proposalsPanel"):
        assert gone not in setup, gone


def test_the_data_use_notice_comes_after_review_and_before_the_target():
    main = code_of("main.js")
    assert 'state.hasRecords && !state.sharing.notice_acknowledged && ["target", "pathway"].includes(path)' in main
    assert '"data-use"' in main
    review = code_of("views/review.js")
    assert 'href: "#/target" }, "Continue to your target"' in review          # the router shows the notice first


def test_the_notice_has_one_continue_and_no_choice():
    d = code_of("views/datause.js")
    assert d.count("button(") == 1 and 'button("Continue")' in d
    assert "checkbox" not in d and "radio" not in d and "without" not in d.lower()
    assert "api.acknowledgeNotice(n.version)" in d                            # the version shown is the one recorded
    assert "consent" not in d.lower()                                          # not presented as a consent


def test_settings_offer_exactly_two_mutually_exclusive_modes_and_separate_status():
    s = code_of("views/settings.js")
    assert '["shared", "private"].map' in s and s.count('type: "radio"') == 1   # one radio group, two options
    assert "Use my career information to improve recommendations for everyone" in s
    assert "Use my career information only for my recommendations" in s
    assert '"Sharing preference"' in s and '"Profile status"' in s
    assert 'if (result.preference !== "private") throw' in s                  # shown only after the API confirms


def test_bulk_confirmation_is_gone():
    r = code_of("views/review.js")
    assert "remaining items" not in r and 'api.review("confirm", item.kind' not in r
    assert "for (const item of pending)" not in r


def test_recommendation_wording_follows_the_api_fit_only():
    r = code_of("views/results.js")
    assert 'const isStrong = (route) => route.fit.tier === "aligned";' in r
    # Phase 8.1: the plural forms are for tied top routes; recommended wording still only under isStrong(best).
    assert 'const heading2 = isStrong(best) ? (tiedTop > 1 ? "Your recommended pathways" : "Your recommended pathway")' in r
    assert ': (tiedTop > 1 ? "Possible paths to your target" : "A possible path to your target");' in r
    assert r.count('"Your recommended pathway"') == 1 and r.count('"Recommended path"') == 2  # both gated by isStrong
    assert "No reliable pathway is available yet." in r
    assert "Top-ranked" not in r and "Best fit" not in r


def test_missing_detail_is_never_called_missing_experience():
    i = code_of("views/insights.js")
    assert "Not recorded in your profile doesn't mean you lack the experience." in i
    text = " ".join(t for n in JS for t in visible_strings(n)).lower().replace("doesn't mean you lack", "")
    for bad in ("you lack ", "you don't have", "you are missing", "you're missing"):
        assert bad not in text, bad


def test_user_facing_text_avoids_internal_terms_and_anonymity_promises():
    banned = ("cohort", "synthetic", "de-identified", "deidentified", "pii", "reference dataset", "taxonomy", "aggregate analytics",
              "untagged", "classification state")
    for name in JS:
        for text in visible_strings(name):
            low = text.lower()
            for word in banned:
                assert word not in low, (name, word, text)
    every = (HTML + "\n".join(JS.values())).lower()
    for promise in ("fully anonymous", "completely anonymous", "100% anonymous", "anonymised so no one", "guaranteed anonymity"):
        assert promise not in every, promise
    assert "Your individual career profile is never shown to other users. Shared career insights are shown only as group patterns." in HTML


def test_approved_sign_in_copy():
    s = code_of("views/signin.js")
    # release (D52): the hero and tagline are the approved release copy (tests/test_release.py checks the rest)
    assert '"Career pathways for product roles"' in s and '"Find your path to your target role."' in s
    assert '"Upload your resume. Choose your target. See your path."' in s and "Plan your path" not in s
    assert "Understand the experience you already have" not in s              # replaced by the approved copy (Phase 4)                                 # approved copy (D47, 2026-11-20)
    assert "googleBtn.disabled = true;" in s                                    # never active-looking while unknown
