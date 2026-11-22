"""Backend rate limiting (Phase 5, shared since Phase 6).

A dependency applied after token verification, per bucket, keyed by the verified user id AND by the client IP; both
limits apply. Exceeding either is HTTP 429 (RateLimited) with Retry-After.

Backend "postgres" (default): fixed-window counters in trace_ops.rate_limit_counters, consumed atomically by
trace_ops.consume_rate_limit on the request's own restricted runtime connection, so every worker and process shares
them; only the runtime login can touch the table and no client can read or reset it. A refused request is not
counted. If the limiter cannot be consulted (database error), the request is refused (503 RateLimiterUnavailable):
there is no fallback to unlimited requests.

Backend "memory": an in-process sliding window for isolated tests only; refused unless TRACE_RATE_LIMIT_ALLOW_MEMORY=1.

The client IP is the socket peer (request.client.host); X-Forwarded-For is NOT trusted, so behind a proxy the IP limit
applies to the proxy until a trusted-proxy configuration is added.
"""
import logging
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Sequence, Tuple
from uuid import UUID

import psycopg
from fastapi import Depends, Request

from app.api.deps import current_owner, db_connection
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)
BUCKETS = ("upload", "mutation", "read")
Rule = Tuple[int, float]


class RateLimited(Exception):
    """Too many requests in the window (429)."""

    def __init__(self, retry_after: int):
        super().__init__("too many requests")
        self.retry_after = max(1, int(retry_after))


class RateLimiterUnavailable(Exception):
    """The limiter could not be consulted or is misconfigured (503). The request is refused, never let through."""


def parse_rule(rule: str) -> Rule:
    """'<count>/<seconds>' -> (count, seconds)."""
    count, _, seconds = rule.partition("/")
    n, s = int(count), float(seconds)
    if n < 1 or s <= 0:
        raise ValueError(f"invalid rate limit rule {rule!r}")
    return n, s


class SlidingWindowLimiter:
    """In-process sliding window (isolated tests only). Thread-safe; a refused request is not counted."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, max_keys: int = 50_000):
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()
        self._clock = clock
        self._max_keys = max_keys

    def check(self, keys_and_rules: Sequence[Tuple[str, Rule]]) -> None:
        now = self._clock()
        with self._lock:
            wait = 0.0
            for key, (limit, window) in keys_and_rules:
                q = self._hits.get(key)
                if q is None:
                    continue
                while q and q[0] <= now - window:
                    q.popleft()
                if len(q) >= limit:
                    wait = max(wait, q[0] + window - now)
            if wait > 0:
                raise RateLimited(retry_after=int(wait) + 1)
            if len(self._hits) > self._max_keys:
                for k in [k for k, q in self._hits.items() if not q or q[-1] <= now - 3600]:
                    del self._hits[k]
            for key, _ in keys_and_rules:
                self._hits.setdefault(key, deque()).append(now)


def consume_shared(conn: psycopg.Connection, keys_and_rules: Sequence[Tuple[str, Rule]]) -> None:
    """One short transaction on the runtime connection: trace_ops.consume_rate_limit (atomic, shared)."""
    keys: List[str] = [k for k, _ in keys_and_rules]
    limits = [int(limit) for _, (limit, _) in keys_and_rules]
    windows = [max(1, int(round(window))) for _, (_, window) in keys_and_rules]
    try:
        with conn.transaction():
            wait = conn.execute("select trace_ops.consume_rate_limit(%s, %s, %s)", (keys, limits, windows)).fetchone()[0]
    except psycopg.Error as exc:
        logger.error("rate limiter unavailable: %s", type(exc).__name__)
        raise RateLimiterUnavailable("the rate limiter is unavailable") from exc
    if wait:
        raise RateLimited(retry_after=wait)


def get_memory_limiter(request: Request) -> SlidingWindowLimiter:
    limiter: Optional[SlidingWindowLimiter] = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        limiter = request.app.state.rate_limiter = SlidingWindowLimiter()
    return limiter


def limit(bucket: str):
    """Dependency factory: enforce the bucket's per-user and per-IP limits for the verified caller."""
    if bucket not in BUCKETS:
        raise ValueError(bucket)

    def dependency(request: Request, owner: UUID = Depends(current_owner), settings: Settings = Depends(get_settings),
                   conn: psycopg.Connection = Depends(db_connection)) -> None:
        if not settings.trace_rate_limits_enabled:
            return
        try:
            user_rule = parse_rule(getattr(settings, f"rate_limit_{bucket}_user"))
            ip_rule = parse_rule(getattr(settings, f"rate_limit_{bucket}_ip"))
        except ValueError as exc:
            raise RateLimiterUnavailable("invalid rate limit configuration") from exc
        ip = request.client.host if request.client else "unknown"
        ns = settings.trace_rate_limit_namespace
        rules = [(f"{ns}:{bucket}:user:{owner}", user_rule), (f"{ns}:{bucket}:ip:{ip}", ip_rule)]
        backend = settings.trace_rate_limit_backend
        if backend == "postgres":
            consume_shared(conn, rules)
        elif backend == "memory" and settings.trace_rate_limit_allow_memory:
            get_memory_limiter(request).check(rules)
        else:
            raise RateLimiterUnavailable("the rate limiter backend is not allowed")

    dependency.__name__ = f"rate_limit_{bucket}"
    return dependency
