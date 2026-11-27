"""Deleting an account does not require a career profile."""
import inspect
from contextlib import nullcontext
from uuid import uuid4

from app.api.deps import current_owner
from app.api.profile_reset_routes import delete_account
from app.services.profile_reset import ProfileResetService


def test_the_delete_route_uses_the_owner_and_not_a_profile():
    dependency = inspect.signature(delete_account).parameters["owner"].default
    assert dependency.dependency is current_owner


def test_an_account_with_no_profile_is_still_deleted(monkeypatch):
    calls = []

    class Conn:
        def execute(self, sql, params=None):
            calls.append(sql)

            class Row:
                def fetchone(self):
                    return (False,)

            return Row()

        def transaction(self):
            return nullcontext()

    monkeypatch.setattr("app.services.profile_reset.owner_session", lambda conn, uid: nullcontext())
    result = ProfileResetService(Conn()).delete_account(uuid4(), "delete my account")
    assert result == {"status": "deleted"}
    assert any("delete_my_account" in sql for sql in calls)
