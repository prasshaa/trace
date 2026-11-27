"""v2.37.0 grants the minimum so the owner role can call delete_my_account. Nothing is applied to a database."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NEW = ROOT / "data-foundation/supabase/migrations/20261126180000_trace_private_delete_account_grant.sql"
OLD = ROOT / "data-foundation/supabase/migrations/20261126100000_trace_private_sharing_default_and_account.sql"
ROLLBACK = ROOT / "data-foundation/supabase/rollback/20261126180000_trace_private_delete_account_grant.rollback.sql"


def test_the_new_migration_grants_schema_usage_and_only_that_function():
    text = NEW.read_text()
    old = OLD.read_text()
    assert "grant usage on schema trace_private to trace_app_user" in text
    assert "grant usage on schema trace_private" not in old
    assert "grant execute on function trace_private.delete_my_account() to trace_app_user" in text
    assert "revoke all on function trace_private.delete_my_account() from public" in text
    assert "revoke all on schema trace_private from public" in text
    for verb in ("select", "insert", "update", "delete"):
        assert f"grant {verb} " not in text.lower()
    assert "security definer" not in text.lower() or "search_path" in text
    assert "request_uid" in text
    assert "revoke usage on schema trace_private from trace_app_user" in ROLLBACK.read_text()
    assert "drop function" not in ROLLBACK.read_text().lower()
