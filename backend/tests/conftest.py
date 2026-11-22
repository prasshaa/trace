# Identity markers are sealed (AES-256-GCM). The test session uses its own random key, generated here (before the app
# and its cached settings are imported) and never written anywhere; an explicit TRACE_IDENTITY_MARKER_KEY wins.
import os

from app.services.contributions.sealing import new_key as _new_marker_key

os.environ.setdefault("TRACE_IDENTITY_MARKER_KEY", _new_marker_key())

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    get_settings.cache_clear()


# --------------------------------------------------------------------------- shared rate-limit counters (Phase 6)
# Rate-limit counters now live in the database and are shared by every app instance. Each test gets its own counter
# namespace (read by every Settings built during the test), so tests never consume each other's budgets; the counters
# a test run created are removed once at the end of the session (they are test artefacts, keyed "test-...").
import os as _os
import uuid as _uuid



@pytest.fixture(autouse=True)
def _rate_limit_namespace(monkeypatch):
    monkeypatch.setenv("TRACE_RATE_LIMIT_NAMESPACE", f"test-{_uuid.uuid4().hex[:16]}")


@pytest.fixture(autouse=True, scope="session")
def _remove_test_rate_limit_counters():
    yield
    try:
        from app import db
        from app.config import Settings
        with db.connect(Settings()) as conn:
            conn.execute("delete from trace_ops.rate_limit_counters where key like 'test-%'")
            conn.commit()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _no_background_contribution_worker(monkeypatch):
    """Tests drive contribution processing explicitly (process_pending / process_user)."""
    monkeypatch.setenv("TRACE_CONTRIBUTION_WORKER", "0")


# --------------------------------------------------------------------------- isolation from a running local server
@pytest.fixture(autouse=True, scope="session")
def _pause_running_contribution_workers():
    """A local Trace server started against the same database may run the background contribution worker. Holding the
    worker pause lock EXCLUSIVELY for the whole session makes every such worker skip its passes, so it never claims the
    queue rows these tests process. Tests call process_pending directly, which does not take the lock."""
    try:
        from app import db
        from app.config import Settings
        from app.services.contributions.worker import PAUSE_LOCK
        conn = db.connect(Settings())
    except Exception:                                                     # no database configured: nothing to pause
        yield
        return
    conn.autocommit = True
    conn.execute("select pg_advisory_lock(%s)", (PAUSE_LOCK,))
    try:
        yield
    finally:
        conn.execute("select pg_advisory_unlock(%s)", (PAUSE_LOCK,))
        conn.close()
