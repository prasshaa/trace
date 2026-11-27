"""Phase 8.1: static guards on the frontend changes (exercised in real Chrome as well)."""
from tests.test_frontend_phase71 import code_of


def test_fit_limitations_use_the_api_categories_and_honest_words():
    c = code_of("copy.js")
    assert "Categorise your responsibilities so Trace can compare your experience with this path." in c
    assert "There's not enough career detail across the available profiles to compare this path reliably." in c
    assert "Not enough career profiles show this route for Trace to assess it." in c
    r = code_of("views/results.js")
    assert "(fit.limitations || []).filter((k) => FIT_LIMITATION[k])" in r and "FIT_REASON" not in r


def test_ties_are_presented_as_ties():
    r = code_of("views/results.js")
    assert 'r.tied && r.rank_group === best.rank_group' in r
    assert "These paths are equally ranked based on the available evidence." in r
    assert 'badge("Equally ranked"' in r


def test_no_route_wording_never_reveals_a_small_group():
    r = code_of("views/results.js")
    assert "Not enough career profiles show a route from" in r
    assert "No move from" not in r and "appears in the available profiles`" not in r


def test_role_unconfirmed_has_a_direct_action_to_the_job():
    c = code_of("copy.js")
    assert "Your current job is confirmed, but the standard role matched to it hasn't been confirmed yet." in c
    r = code_of("views/results.js")
    assert '"Review the role match"' in r and 'ctx.app.flash = "focus_current_job"' in r
    v = code_of("views/review.js")
    assert 'this.ctx.app.flash === "focus_current_job" && current' in v and "focusJob(current.id);" in v
    assert "positionText(" in code_of("views/target.js")


def test_the_role_picker_starts_on_the_suggested_role():
    r = code_of("views/review.js")
    assert "const preset = options.suggested_role_id && byId[options.suggested_role_id] ? options.suggested_role_id : \"\"" in r
    assert "select.value = preset" in r


def test_move_directions_come_only_from_the_api():                       # Phase 8.2
    c = code_of("copy.js")
    for text in ("Move to a more senior role", "Move to a more junior role", "Lateral move",
                 "A change in career direction"):                    # D20: replaces "Change of career track" (fallback)
        assert text in c
    r = code_of("views/results.js")
    # D20: still null -> no label; career_track_change is named from the API's family names (familyChangeLabel).
    assert "(route.step_directions || []).map((d, i) => (!d ? null" in r and "familyChangeLabel((route.step_family_changes || [])[i])" in r
    assert "moveLabels[i - 1] ? h(\"span.route-move\"" in code_of("charts.js")
