"""Phase 7.2: static guards on the targeted fixes (no browser needed; the journey is exercised in real Chrome)."""
import re

from tests.test_frontend_phase71 import JS, code_of, visible_strings


def test_the_current_role_is_selectable_and_recognised_by_canonical_id_not_by_label():
    # Phase 5 (D51): the current role is a valid target; it's recognised (by role id) only to explain the move.
    t = code_of("views/target.js")
    assert "const isCurrent = (id) => known && position.role_id === id;" in t
    assert "disabled: true" not in t and "You're already in this role" not in t
    assert "You can choose the same role if you're exploring a move to another company." in t   # release copy (D52)
    assert "label ===" not in t and "canonical_title ===" not in t


def test_continue_needs_an_established_starting_point():
    r = code_of("views/review.js")
    assert 'const started = Boolean(this.position && this.position.status === "known");' in r
    assert 'button("Continue to your target", { disabled: true' in r
    assert "Confirm your current role to continue." in r
    t = code_of("views/target.js")
    assert "if (!known) {" in t and "Confirm your current role to continue" in t


def test_a_self_target_is_explained_never_drawn():
    p = code_of("views/results.js")
    assert "if (isCurrentRoleRefusal(pathwayRes.reason)) return backToTarget(ctx);" in p


def test_counts_never_call_test_records_people():
    p = code_of("views/results.js")
    assert "is_synthetic_test_data" in p and "profilesCount(" in p
    text = " ".join(t for n in JS for t in visible_strings(n)).lower()
    for bad in ("people made this move", "person made this move", "people took this", "people on this path"):
        assert bad not in text, bad
    assert '"fictional test profiles" : "career profiles"' in code_of("copy.js")


def test_unavailable_categories_use_written_sentences():
    i = code_of("views/insights.js")
    assert "Not enough profiles to show common ways people reached this role." in i
    assert "Not enough profiles to show earlier career roles." in i
    assert not re.search(r"Not enough profiles to show \$\{", i)                 # no generated grammar


def test_small_sample_caution_uses_server_settings_only():
    c = code_of("copy.js")
    assert "Treat this as an observed pattern, not a prediction" in c
    assert "cautionBelow = typeof p.sample_caution_below" in code_of("views/results.js")
    assert "insights.scope.min_target_cohort" in code_of("views/insights.js")
    for name in ("views/results.js", "views/insights.js"):
        assert not re.search(r"<\s*(10|15|20|25|30)\b", code_of(name)), name     # no invented threshold


def test_google_is_hidden_unless_enabled():
    s = code_of("views/signin.js")
    assert 'h("div.google-block", { hidden: true }' in s and "googleBlock.hidden = false;" in s


def test_mobile_menu_is_accessible():
    m = code_of("main.js")
    assert '"aria-expanded": "false", "aria-controls": "main-nav"' in m
    assert 'e.key === "Escape"' in m and "toggle.focus()" in m


def test_mobile_journey_and_selectors_exist():
    assert "Step ${index + 1} of ${JOURNEY.length}" in code_of("ui.js")
    assert '"aria-label": "Jump to section"' in code_of("views/results.js")
    assert '"aria-label": "Insight category"' in code_of("views/insights.js")
