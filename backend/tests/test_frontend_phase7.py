"""Phase 7: static guards on the redesigned frontend (no browser needed). The browser journey itself is exercised
separately in real Chrome; these checks keep its safety properties from regressing silently."""
import re

from app.config import Settings

WEB = Settings().trace_web_dir
JS = {p.name: p.read_text() for p in (WEB / "js").rglob("*.js")}


def code_of(name):
    """The file without // comments (comments may name endpoints or roles; only code is checked)."""
    return re.sub(r"(?m)^\s*//.*$|\s//\s.*$", "", JS[name])


def test_no_client_side_statistics_in_chart_or_insight_code():
    # Charts and insights draw only the API's own count, denominator and share_pct: no division, no percentages computed,
    # so nothing the API suppressed can be reconstructed in the browser.
    for name in ("charts.js", "insights.js", "results.js"):
        code = re.sub(r"//.*", "", JS[name])
        assert not re.search(r"\b(count|denominator|other_count|population_size|not_recorded)\s*[/*]", code), name
        assert not re.search(r"[/*]\s*\b(count|denominator|population_size)\b", code), name
        assert not re.search(r"\*\s*100\b|\b100\s*\*", code), name


def test_the_browser_never_chooses_or_substitutes_the_reference_source():
    # Phase 7.1: the server's data mode decides the source. The browser never sends a cohort, never names a combined
    # one, and shows "not enough profiles" instead of loading anything else.
    code = code_of("insights.js") + code_of("results.js") + code_of("api.js")
    assert not re.search(r"""["'](all|combined|both)["']""", code)
    assert "cohort=" not in code_of("api.js") and "/api/v1/cohorts" not in code_of("api.js")
    assert code.count("api.insights(") == 1                                            # one request, no retry elsewhere
    assert "Not enough career profiles yet" in JS["insights.js"]
    assert 'insights.status !== "ready"' in JS["insights.js"]


def test_real_uploads_follow_the_server_switch_and_send_no_filename():
    api = JS["api.js"]
    line = next(l for l in api.splitlines() if "uploadRealResume" in l)
    assert "/api/v1/real-resumes" in line and "X-Filename" not in line
    setup = code_of("setup.js")
    assert "config.features && ctx.app.config.features.real_resume_uploads" in setup
    assert "if (!offered) return unavailable(body);" in setup                         # nothing simulated, no substitute
    assert "/api/v1/resumes" not in setup and "/api/v1/real-resumes" not in setup     # only through api.js


def test_consents_are_never_preselected_and_inclusion_needs_both_flags():
    decl = next(l for l in JS["setup.js"].splitlines() if 'id: "consent-agree"' in l)
    assert "checked" not in decl
    # The settings modes show only the SAVED preference as selected; nothing is pre-selected on the browser's own.
    decl = next(l for l in JS["settings.js"].splitlines() if 'type: "radio"' in l)
    assert "checked: saved === key" in decl
    s = code_of("settings.js")
    assert 'case "eligible":\n      return s.included' in s                          # "Included" needs eligible AND included


def test_no_administrative_review_or_retry_operation_in_the_browser():
    code = "\n".join(code_of(n) for n in JS).lower()
    for bad in ("approve(", "approve-", "/approve", "retry_contribution", "review_quarantine", "contribution_queue", "trace_private", "trace_contrib",
                "service_role", "/admin/", "/rest/v1"):
        assert bad not in code, bad


def test_context_lives_in_history_state_not_storage():
    code = "\n".join(JS.values())
    assert "sessionStorage" not in code and "localStorage" not in code
    assert "history.replaceState({ target:" in JS["state.js"]
