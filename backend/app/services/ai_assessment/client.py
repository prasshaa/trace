"""OpenAI-compatible chat client. Provider, model, base URL and key come from the environment. Nothing is hard-coded
as a secret, and the key is never written to a log.

xAI's chat completions endpoint accepts a top-level reasoning_effort of low, medium, high or xhigh for grok-4.7.
Omitting it makes grok-4.7 reason at high. Trace sends low unless TRACE_LLM_REASONING_EFFORT says otherwise.
"""
from __future__ import annotations

import http.client
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, List, Optional

from pydantic import SecretStr

logger = logging.getLogger(__name__)


class LlmNotConfigured(Exception):
    """The demo narrative was asked for and the provider is not configured."""


class LlmError(Exception):
    """The provider failed or returned something that is not JSON. The message names the kind of failure, not the body."""


class OpenAICompatible:
    def __init__(self, *, provider: str, base_url: str, model: str, api_key: str, timeout: float = 120.0,
                 reasoning_effort: Optional[str] = "low", first_attempt_seconds: float = 70.0):
        self.provider = provider or "openai"
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._key = api_key
        self.timeout = timeout
        # The first try stops early enough that a timeout still leaves time for one retry inside `timeout`.
        self.first_attempt_seconds = float(first_attempt_seconds or timeout)
        # None uses the demo default (low). An empty string omits the field so the provider uses its own default.
        if reasoning_effort is None:
            self.reasoning_effort: Optional[str] = "low"
        else:
            self.reasoning_effort = str(reasoning_effort).strip() or None
        self.last_meta: Optional[dict] = None

    def complete_json(self, system: str, user: str, on_attempt: Optional[Callable[[int], None]] = None) -> dict:
        """POST {base}/chat/completions and parse a JSON object. The key stays on the Authorization header.

        The timeout is a deadline for the whole request. The first try uses at most first_attempt_seconds, and a
        timeout or an HTTP 5xx is tried once more with the time still left. Latency and token counts are logged.
        The prompt, the reply and the key are not.
        """
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        body = json.dumps(payload).encode()
        deadline = time.monotonic() + self.timeout
        started_all = time.perf_counter()
        attempt_log = []
        last_error = "the model provider could not be reached"
        for attempt in (1, 2):
            remaining = deadline - time.monotonic()
            # The first attempt uses whatever budget is left. A retry needs at least a second to be worth sending.
            if remaining <= 0 or (attempt == 2 and remaining < 1):
                break
            if on_attempt is not None:
                on_attempt(attempt)
            budget = remaining
            if attempt == 1:
                budget = min(remaining, self.first_attempt_seconds)
            started = time.perf_counter()
            try:
                raw = _post_bounded(url, body, self._key, budget)
            except urllib.error.HTTPError as exc:
                latency = round(time.perf_counter() - started, 3)
                attempt_log.append({"n": attempt, "ok": False, "error": f"http_{exc.code}", "latency_s": latency})
                self._remember(attempt_log, None, time.perf_counter() - started_all)
                if 500 <= exc.code <= 599 and attempt == 1:
                    logger.info("llm call provider=%s model=%s attempt=%s http=%s latency_s=%.3f",
                                self.provider, self.model, attempt, exc.code, latency)
                    continue
                raise LlmError(f"the model provider returned HTTP {exc.code}") from None
            except TimeoutError:
                latency = round(time.perf_counter() - started, 3)
                attempt_log.append({"n": attempt, "ok": False, "error": "timeout", "latency_s": latency})
                self._remember(attempt_log, None, time.perf_counter() - started_all)
                logger.info("llm call provider=%s model=%s attempt=%s timeout latency_s=%.3f",
                            self.provider, self.model, attempt, latency)
                last_error = "the model provider timed out"
                if attempt == 1:
                    continue
                raise LlmError(last_error) from None
            except OSError as exc:
                latency = round(time.perf_counter() - started, 3)
                attempt_log.append({"n": attempt, "ok": False, "error": type(exc).__name__, "latency_s": latency})
                self._remember(attempt_log, None, time.perf_counter() - started_all)
                raise LlmError(f"the model provider could not be reached ({type(exc).__name__})") from None
            latency = round(time.perf_counter() - started, 3)
            try:
                parsed = json.loads(raw)
                text = parsed["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                attempt_log.append({"n": attempt, "ok": False, "error": "bad_payload", "latency_s": latency})
                self._remember(attempt_log, None, time.perf_counter() - started_all)
                raise LlmError("the model provider returned an unexpected payload") from exc
            usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {}
            details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
            clean_usage = {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "reasoning_tokens": details.get("reasoning_tokens"),
            }
            attempt_log.append({"n": attempt, "ok": True, "latency_s": latency})
            total = round(time.perf_counter() - started_all, 3)
            self._remember(attempt_log, clean_usage, total)
            logger.info(
                "llm call provider=%s model=%s attempt=%s latency_s=%.3f prompt_tokens=%s completion_tokens=%s reasoning_tokens=%s",
                self.provider, self.model, attempt, latency, clean_usage["prompt_tokens"],
                clean_usage["completion_tokens"], clean_usage["reasoning_tokens"],
            )
            return _json_object(text)
        raise LlmError(last_error)

    def _remember(self, attempt_log, usage, total_s: float) -> None:
        self.last_meta = {
            "latency_s": round(total_s, 3),
            "usage": usage,
            "attempts": len(attempt_log),
            "attempt_log": attempt_log,
        }


def _post_bounded(url: str, body: bytes, api_key: str, budget: float) -> bytes:
    """POST and read the body. Return within budget seconds even if headers arrive and the body then trickles.

    urllib's timeout resets on every socket read, and HTTPResponse.close waits on the same lock as read, so a
    late header plus a slow body used to run on past the deadline. The read is abandoned by shutting the socket
    down. close() is never called from this thread.
    """
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    box: dict = {}
    socks: List[socket.socket] = []
    deadline = time.monotonic() + budget

    def run() -> None:
        resp = None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("request deadline exceeded")
            resp = _open(req, remaining, socks)
            box["resp"] = resp
            chunks = []
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("request deadline exceeded")
                _arm(socks, remaining)
                block = resp.read(65536)
                if not block:
                    break
                chunks.append(block)
            box["raw"] = b"".join(chunks)
        except Exception as exc:                                          # noqa: BLE001 - the caller classifies it
            box["exc"] = exc
        finally:
            # Same thread as read(), so this does not wait on the caller's deadline.
            if resp is not None:
                try:
                    resp.close()
                except Exception:                                          # noqa: BLE001
                    pass

    worker = threading.Thread(target=run, name="trace-llm-http", daemon=True)
    worker.start()
    worker.join(budget)
    if worker.is_alive():
        _shutdown(socks)
        raise TimeoutError("request deadline exceeded")
    exc = box.get("exc")
    if isinstance(exc, (TimeoutError, socket.timeout)):
        raise TimeoutError("request deadline exceeded")
    if exc is not None:
        raise exc
    return box.get("raw") or b""


def _open(req: urllib.request.Request, timeout: float, socks: List[socket.socket]):
    """Open the request and remember the live socket so the caller can shut it down."""
    class _HTTP(http.client.HTTPConnection):
        def connect(self):
            super().connect()
            if self.sock is not None:
                socks.append(self.sock)

    class _HTTPS(http.client.HTTPSConnection):
        def connect(self):
            super().connect()
            if self.sock is not None:
                socks.append(self.sock)

    class _HTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, request):
            return self.do_open(_HTTP, request)

    class _HTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, request):
            return self.do_open(_HTTPS, request)

    return urllib.request.build_opener(_HTTPHandler(), _HTTPSHandler()).open(req, timeout=timeout)


def _shutdown(socks: List[socket.socket]) -> None:
    """Unblock a read in another thread. shutdown does not take the response lock that close() waits on."""
    for sock in list(socks):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass


def _arm(socks: List[socket.socket], seconds: float) -> None:
    """Cap the next blocking read at the time still left."""
    for sock in socks:
        try:
            sock.settimeout(max(0.05, seconds))
        except OSError:
            pass


def _json_object(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LlmError("the model did not return JSON") from exc
    if not isinstance(value, dict):
        raise LlmError("the model did not return a JSON object")
    return value


def client_from_settings(settings) -> Optional[OpenAICompatible]:
    """None when the demo path is off or the key/model is missing. An empty key is missing."""
    if not getattr(settings, "trace_ai_assessment", False):
        return None
    key: Optional[SecretStr] = getattr(settings, "trace_llm_api_key", None)
    raw = key.get_secret_value().strip() if key is not None else ""
    model = (getattr(settings, "trace_llm_model", "") or "").strip()
    if not raw or not model:
        return None
    effort = getattr(settings, "trace_llm_reasoning_effort", "low")
    return OpenAICompatible(
        provider=(getattr(settings, "trace_llm_provider", "") or "openai").strip(),
        base_url=(getattr(settings, "trace_llm_base_url", "") or "https://api.openai.com/v1").strip(),
        model=model,
        api_key=raw,
        timeout=float(getattr(settings, "trace_llm_timeout", 120) or 120),
        reasoning_effort="low" if effort is None else effort,
        first_attempt_seconds=float(getattr(settings, "trace_llm_first_attempt_seconds", 70) or 70),
    )
