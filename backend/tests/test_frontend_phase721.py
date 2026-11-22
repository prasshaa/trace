"""Phase 7.2.1: static guards (the behaviour is exercised in real Chrome as well)."""
import re

from tests.test_frontend_phase71 import JS, code_of


def test_only_the_specific_409_is_a_validation_state():
    r = code_of("views/results.js")
    assert 'err.status === 409 && err.code === "target_is_current_role"' in r          # status AND code
    assert 'position.role_id === target) return backToTarget(ctx);' in r              # recognised before any request
    assert "errorState(pathwayRes.reason" in r                                       # everything else stays an error
    t = code_of("views/target.js")
    # Phase 5 (D51): the target page no longer refuses the current role, so it has no such state; it clears old flashes
    assert "You're already in this role" not in t and "ctx.app.flash = null;" in t


def test_an_automatic_match_is_confirmed_explicitly():
    r = code_of("views/review.js")
    assert '["automatic", "set_outside_app"].includes(m.state)' in r and "!m.is_stale" in r
    assert re.search(r'api\.chooseRole\(rec\.id, confirmRole\.role_id\);\s*await api\.review\("confirm", kind, rec\.id\);', r)
    assert 'button("Confirm role"' in r and "api.chooseRole(e.id, m.role_id)" in r
    assert "role_unconfirmed" in code_of("copy.js")


def test_one_outside_click_listener_for_the_menu():
    m = code_of("main.js")
    assert m.count('document.addEventListener("click"') == 1
    render = m[m.index("function renderHeader"):m.index("async function route")]
    assert "document.addEventListener" not in render                                  # never per render (no leaks)
    assert "shell.header.contains(e.target)) return;" in m                            # inside clicks are left alone
