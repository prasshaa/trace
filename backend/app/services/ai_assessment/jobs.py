"""In-process narrative jobs. The roadmap returns immediately; the model call finishes in the background.

Keyed by owner, target and profile version. A second request while one is running does not start another call.
The process stores the result only. It does not store the API key or the prompt.
"""
from __future__ import annotations

import threading
from typing import Callable, Dict, Optional

_lock = threading.Lock()
_jobs: Dict[str, dict] = {}


def launch(key: str, work: Callable[[], dict]) -> dict:
    """Return the finished narrative, or status pending if this call started the work or it is still running."""
    with _lock:
        current = _jobs.get(key)
        if current and current["status"] in ("pending", "ready"):
            return _public(current)
        job = {"status": "pending", "result": None}
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
    return {"status": "pending"}


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
        return {"status": "pending"}
    return job["result"]
