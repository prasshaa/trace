"""In-process narrative jobs. The roadmap returns immediately; the model call finishes in the background.

Each narrative runs on its own thread. The lock only updates the job map, so two readings do not wait on each other.
A second request for the same owner, target and profile version reuses the call already running.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, Optional

_lock = threading.Lock()
_jobs: Dict[str, dict] = {}


def launch(key: str, work: Callable[[], dict]) -> dict:
    """Return the finished narrative, or status pending if this call started the work or it is still running."""
    with _lock:
        current = _jobs.get(key)
        if current and current["status"] in ("pending", "ready"):
            return _public(current)
        job = {"status": "pending", "result": None, "started": time.monotonic(), "attempt": 1, "phase": "reading"}
        _jobs[key] = job

    def run() -> None:
        try:
            result = work()
        except Exception as exc:                                          # noqa: BLE001 - the plan must still render
            result = {"status": "provider_error", "detail": type(exc).__name__}
        with _lock:
            job["status"] = result.get("status") or "ready"
            job["result"] = result

    threading.Thread(target=run, name="trace-ai-narrative", daemon=True).start()
    return _public(job)


def note(key: str, *, attempt: int, phase: str) -> None:
    """The model call reports which attempt it is on, so the page can say it is trying once more."""
    with _lock:
        job = _jobs.get(key)
        if job and job["status"] == "pending":
            job["attempt"] = attempt
            job["phase"] = phase


def lookup(owner: str, target: str) -> Optional[dict]:
    """The newest job for this owner and target, if one has been started."""
    prefix = f"{owner}:{target}:"
    with _lock:
        matches = [job for key, job in _jobs.items() if key.startswith(prefix)]
    if not matches:
        return None
    return _public(matches[-1])


def _public(job: dict) -> dict:
    if job["status"] == "pending" or job.get("result") is None:
        elapsed = time.monotonic() - job.get("started", time.monotonic())
        phase = job.get("phase") or "reading"
        if phase == "reading" and elapsed >= 30:
            phase = "still_working"
        return {"status": "pending", "elapsed_s": round(elapsed, 1), "attempt": job.get("attempt") or 1, "phase": phase}
    return job["result"]
