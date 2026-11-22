"""Resume-first onboarding views (D47): one consolidated review, one confirmation, explicit replace and discard."""
import re

from tests.test_frontend_phase71 import code_of, visible_strings



def test_the_review_has_one_primary_action_and_no_per_item_confirmation():
    code = code_of("views/import.js")
    text = " ".join(visible_strings("views/import.js"))
    assert "Confirm profile and continue" in text and code.count("api.confirmImport(") == 1
    for gone in ("Add and confirm", "Add as draft", "acceptProposal", "dismissProposal", "confirmRecord"):
        assert gone not in code, gone
    assert "Replace resume" in text and "Discard this import and start over" in text
    assert code.count("confirmDialog(") == 2                                    # replace and discard both ask first


def test_controls_are_labelled_for_screen_readers():
    code = code_of("views/import.js")
    assert '"aria-label": `Remove ${k.name}`' in code and '"aria-label": `Rename ${k.name}`' in code
    assert 'h("legend", label, required' in code and '"aria-label": `${label}: month`' in code
    assert 'h("label", { for: id }' in code                                     # every text field has a visible label
    assert '"aria-live": "polite"' in code


def test_the_browser_never_decides_counts_or_matches():
    code = code_of("views/import.js")
    assert not re.search(r"\.sort\(|Math\.|toFixed|levenshtein|similar", code)
    assert "view.mode" in code and "view.updates" in code                     # the server says what is new or changed


def _joined(path):
    """The view's source with string concatenations joined, so a sentence split over several literals reads whole."""
    return re.sub(r'"\s*\+\s*"', "", code_of(path))


def test_get_started_is_resume_first_with_an_accurate_file_disclosure():
    setup = code_of("views/setup.js")
    text = _joined("views/setup.js")
    assert "Upload your resume" in text and "Upload a PDF or Word (.docx) file." in text
    assert "The file and its name aren't saved anywhere" in text and "isn't sent to any other company or AI service" in text
    # D48: the disclosure says a private draft IS saved before confirmation, and never claims that personal details are
    # removed "before anything is saved" or that the draft lives only in memory.
    assert "Before you confirm, Trace saves the career details it found as a private draft" in text
    assert "it stays when you reload or sign out" in text and "There's no automatic deletion date." in text
    assert "removed before anything" not in text and "only in memory" not in text and "synthetic" not in text.lower()
    assert "Agreeing here doesn't include it." in text                         # the contribution choice is separate
    assert "a short processing record when reading fails or you confirm: the date and the outcome, with no content" in text
    assert ".pdf,.docx" in setup and "Start over" in text


def test_the_full_notice_opens_in_an_accessible_dialog_without_leaving_the_page():
    ui = code_of("ui.js")
    setup = code_of("views/setup.js")
    assert "export function infoDialog(" in ui and "dialog.showModal()" in ui and '"aria-labelledby": titleId' in ui
    assert "opener.focus()" in ui                                               # focus goes back to the button
    assert "infoDialog({" in setup and "Read the full privacy notice" in " ".join(visible_strings("views/setup.js"))


def test_unplaced_text_is_shown_and_a_missing_role_can_be_added():
    code = code_of("views/import.js")
    text = " ".join(visible_strings("views/import.js"))
    assert "We found text that may contain another role. Please review it before confirming your profile." in text
    assert "Add as a role" in text and "Add a role that's missing" in text and "view.unresolved" in code
    assert "added_work" in code and "from_unresolved" in code
    assert "Already in your profile" in text and "Different dates from your profile" in text   # the server's status, shown


def test_routing_puts_an_open_import_first():
    main = code_of("main.js")
    assert 'if (state.pendingImport && !["import", "settings", "start"].includes(path)) path = "import";' in main
    assert "api.resumeImport()" in main


def test_confirmation_moves_on_to_the_next_onboarding_step():
    code = code_of("views/import.js")
    assert 'ctx.go("target");' in code                                          # never back to a per-record review page


def test_every_job_edit_the_server_accepts_is_sent():
    from app.services.resume_import import JobEdit
    code = code_of("views/import.js")
    sent = re.search(r'const ROLE_FIELDS = \[([^\]]+)\]', code).group(1)
    # D50: counts_full_time and side_role stay accepted by the API for compatibility, but the page no longer asks them:
    # the type is one choice, and overlapping part-time, freelance or founder work is treated as alongside by the server.
    for field in set(JobEdit.model_fields) - {"id", "bullets", "counts_full_time", "side_role"}:
        assert f'"{field}"' in sent, field                                     # e.g. role_id: the user's role choice
    assert '"side_role"' not in sent and '"counts_full_time"' not in sent
    text = _joined("views/import.js")
    assert "This was my main, full-time work" not in text and "This ran alongside my main job" not in text


def test_one_role_card_with_the_server_contract_for_every_role():
    code = code_of("views/import.js")
    text = _joined("views/import.js")
    assert "function roleCard(" in code and "function addedRoleForm(" not in code            # no second, simpler form
    assert code.count("roleCard(") >= 3                                                       # found roles and added roles
    for label, field in (("Job title", "title"), ("Employer", "employer"), ("Description", "description")):
        assert f'"{label}"' in code and f'field: "{field}"' in code or f'"data-field": "{field}"' in code
    assert 'monthYear("Start"' in code and 'monthYear("End"' in code and "I currently work here" in text
    assert "endBox.hidden = current.checked" in code                                         # current role: no end date
    # Phase 5: "I currently work here" only while the end date is empty; a missing date is never read as current
    assert "currentRow.hidden = !current.checked && !emptyEnd()" in code
    assert "(optional)" not in text.replace('h("span.muted", " (optional)")', "")            # only where genuinely optional
    assert "Start (at least the year)" not in text and "Fields marked * are needed for every role." in text


def test_add_as_a_role_is_prefilled_and_unplaced_text_can_be_left_out():
    code = code_of("views/import.js")
    text = _joined("views/import.js")
    assert "function fromSuggestion(" in code and "u.suggested" in code and "from_unresolved: u.id" in code
    assert "Leave this out" in text and "Text you leave out isn't saved and doesn't stop you confirming." in text
    assert "ignored: [...s.ignored]" in code
    assert "Added as a role in Work experience below." in text


def test_the_closest_trace_role_is_explained_and_placed_by_the_server():
    code = code_of("views/import.js")
    text = _joined("views/import.js")
    assert "Trace uses this to map your most recent job to its role framework, which sets your career starting point." in text
    assert "It doesn't change your job title." in text
    assert "api.checkImport(" in code and "result.starting_role" in code and "placeRoleChoices(" in code
    # D50: every role can be matched (optional); only the starting role must be. A title-word suggestion is labelled.
    assert "result.role_options" in code and '" (optional)"' in code and "Suggested because your title includes" in text


def test_errors_are_shown_per_field_with_a_summary_and_a_support_reference():
    code = code_of("views/import.js")
    assert 'err.code === "review_incomplete"' in code and "showErrors(root, s, err.fields" in code
    assert '"aria-invalid", "true"' in code and 'role: "alert"' in code and "first.focus()" in code
    assert "err.reference" in code and "Your edits are still here" in _joined("views/import.js")
    assert "extra.reference = payload.reference" in code_of("api.js")
