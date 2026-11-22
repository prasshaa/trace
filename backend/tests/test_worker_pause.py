"""The background contribution worker pauses while another session holds the pause lock exclusively (test isolation
from a running local server; an operator can use the same lock to pause processing)."""
import threading

import pytest

from app import db
from app.config import Settings
from app.services.contributions import worker

pytestmark = pytest.mark.integration


def test_a_background_pass_is_skipped_while_paused_and_runs_otherwise(monkeypatch):
    ran = []
    monkeypatch.setattr(worker, "process_pending", lambda settings, stop=None: ran.append(1))
    # The test session already holds the lock exclusively (conftest): a background pass must skip.
    assert worker._pass(Settings(), threading.Event()) is False and ran == []
    with db.connect(Settings()) as other:
        other.autocommit = True
        held = other.execute("select pg_try_advisory_lock(%s)", (worker.PAUSE_LOCK,)).fetchone()[0]
        assert held is False                                              # exclusive: nobody else can take it either
