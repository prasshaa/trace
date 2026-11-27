"""New accounts default to anonymous recommendation use. The profile is not published. No database is used."""
from pathlib import Path

from app.api.sharing_routes import NOTICE_LINE, NOTICE_PARAGRAPHS, NOTICE_VERSION

ROOT = Path(__file__).resolve().parents[2]
NEW = ROOT / "data-foundation/supabase/migrations/20261126190000_trace_anonymous_recommendation_default.sql"
OLD = ROOT / "data-foundation/supabase/migrations/20261126100000_trace_private_sharing_default_and_account.sql"


def test_the_onboarding_default_is_anonymous_recommendation_use_and_not_a_published_profile():
    body = NEW.read_text()
    assert "values (v_uid, 'shared', 'onboarding_default', p_notice_version)" in body
    assert "trace_consent_events" not in body.split("as $$")[1].split("$$;")[0]
    assert "enqueue_contribution" not in body.split("as $$")[1].split("$$;")[0]
    assert "security definer" in body.lower() and "set search_path = ''" in body
    assert "values (v_uid, 'private', 'onboarding_default', p_notice_version)" in OLD.read_text()
    assert NOTICE_VERSION == "trace-data-use-notice-v4"
    assert NOTICE_PARAGRAPHS[0] == NOTICE_LINE
    assert "uses your data anonymously to improve recommendations for everyone" in NOTICE_LINE
    assert "never shown or shared with other users" in NOTICE_LINE
    assert "change this in Settings" in NOTICE_LINE


def test_sign_up_and_settings_use_the_same_words_as_the_notice():
    signin = (ROOT / "backend/web/js/views/signin.js").read_text()
    settings = (ROOT / "backend/web/js/views/settings.js").read_text()
    assert NOTICE_LINE in signin
    assert "Use my data anonymously to improve recommendations for everyone" in settings
    assert "Your profile is never shown or shared with other users." in settings
    assert "You can turn this off here at any time." in settings
