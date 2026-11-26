"""OpenAI-compatible chat client. Provider, model, base URL and key come from the environment. Nothing is hard-coded
as a secret, and the key is never written to a log.

xAI's chat completions endpoint accepts a top-level reasoning_effort of low, medium, high or xhigh for grok-4.7.
Omitting it makes grok-4.7 reason at high. Trace sends low unless TRACE_LLM_REASONING_EFFORT says otherwise.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

from pydantic import SecretStr

logger = logging.getLogger(__name__)


class LlmNotConfigured(Exception):
    """The demo narrative was asked for and the provider is not configured."""


class LlmError(Exception):
    """The provider failed or returned something that is not JSON. The message names the kind of failure, not the body."""


class OpenAICompatible:
    def __init__(self, *, provider: str, base_url: str, model: str, api_key: str, timeout: float = 120.0,
                 reasoning_effort: Optional[str] = "low"):
        self.provider = provider or "openai"
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._key = api_key
        self.timeout = timeout
        # None uses the demo default (low). An empty string omits the field so the provider uses its own default.
        if reasoning_effort is None:
            self.reasoning_effort: Optional[str] = "low"
        else:
            self.reasoning_effort = str(reasoning_effort).strip() or None
        self.last_meta: Optional[dict] = None

    def complete_json(self, system: str, user: str, on_attempt: Optional[Callable[[int], None]] = None) -> dict:
        """POST {base}/chat/completions and parse a JSON object. The key stays on the Authorization header.

        The timeout is a deadline for the whole request, not for each socket read. A timeout or an HTTP 5xx is
        tried once more with whatever time is left. Latency and token counts are logged. The prompt, the reply
        and the key are not.
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
            started = time.perf_counter()
            try:
                raw = _post_bounded(url, body, self._key, remaining)
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
    """POST and read the body. Return within budget seconds even if the server trickles bytes.

    urllib's timeout is re-armed on every socket read, so a slow stream can outlive it. The read runs on a
    daemon thread and the socket is closed when the deadline passes.
    """
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    box: dict = {}
    deadline = time.monotonic() + budget

    def run() -> None:
        resp = None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("request deadline exceeded")
            resp = urllib.request.urlopen(req, timeout=remaining)
            box["resp"] = resp
            chunks = []
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("request deadline exceeded")
                _arm(resp, remaining)
                block = resp.read(65536)
                if not block:
                    break
                chunks.append(block)
            box["raw"] = b"".join(chunks)
        except Exception as exc:                                          # noqa: BLE001 - the caller classifies it
            box["exc"] = exc
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:                                          # noqa: BLE001
                    pass

    worker = threading.Thread(target=run, name="trace-llm-http", daemon=True)
    worker.start()
    worker.join(budget)
    if worker.is_alive():
        resp = box.get("resp")
        if resp is not None:
            try:
                resp.close()
            except Exception:                                              # noqa: BLE001
                pass
            worker.join(1.0)
        raise TimeoutError("request deadline exceeded")
    exc = box.get("exc")
    if isinstance(exc, (TimeoutError, socket.timeout)):
        raise TimeoutError("request deadline exceeded")
    if exc is not None:
        raise exc
    return box.get("raw") or b""


def _arm(resp, seconds: float) -> None:
    """Cap the next blocking read at the time still left. Missing the socket is safe: the caller still joins."""
    sock = None
    fp = getattr(resp, "fp", None)
    raw = getattr(fp, "raw", None) if fp is not None else None
    if raw is not None:
        sock = getattr(raw, "_sock", None) or getattr(getattr(raw, "_fp", None), "raw", None)
        if sock is not None and not hasattr(sock, "settimeout"):
            sock = getattr(sock, "_sock", None)
    if sock is not None and hasattr(sock, "settimeout"):
        sock.settimeout(max(0.1, seconds))


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
    )
