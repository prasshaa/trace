"""A small in-process loop that drains the DURABLE contribution queue. The queue (trace_private.contribution_requests)
is the source of truth: if this thread stops, the process restarts, or several workers run, requests are neither lost
nor processed twice (lease + SKIP LOCKED). Enabled with TRACE_CONTRIBUTION_WORKER (default on). stop() lets the current
user finish and hands unstarted claims back to the queue without counting them as attempts."""
import logging
import threading
from typing import Optional

from app import db
from app.config import Settings
from app.services.contributions.processor import process_pending

logger = logging.getLogger(__name__)
# Background passes hold this advisory lock SHARED, so any number of workers run together. Whoever holds it EXCLUSIVELY
# (the test suite, or an operator pausing processing) makes background passes skip until it is released. Direct calls
# to process_pending (tests, scripts) do not take it.
PAUSE_LOCK = 7_310_412_026


def _pass(settings: Settings, stop: threading.Event) -> bool:
    """One background pass, unless processing is paused. Returns whether it ran."""
    with db.connect_runtime(settings) as conn:
        conn.autocommit = True
        if not conn.execute("select pg_try_advisory_lock_shared(%s)", (PAUSE_LOCK,)).fetchone()[0]:
            return False
        try:
            process_pending(settings, stop=stop)
        finally:
            conn.execute("select pg_advisory_unlock_shared(%s)", (PAUSE_LOCK,))
    return True


class ContributionWorker:
    def __init__(self, settings: Settings, interval: float):
        self.settings, self.interval = settings, max(1.0, interval)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                _pass(self.settings, self._stop)
            except Exception as exc:
                logger.warning("contribution worker pass failed: %s", type(exc).__name__)
            self._stop.wait(self.interval)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="trace-contribution-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
