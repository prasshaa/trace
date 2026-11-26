"""OpenAI-compatible chat client. Provider, model, base URL and key come from the environment. Nothing is hard-coded
as a secret, and the key is never written to a log.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Optional

from pydantic import SecretStr

logger = logging.getLogger(__name__)


class LlmNotConfigured(Exception):
    """The demo narrative was asked for and the provider is not configured."""


class LlmError(Exception):
    """The provider failed or returned something that is not JSON. The message names the kind of failure, not the body."""


class OpenAICompatible:
    def __init__(self, *, provider: str, base_url: str, model: str, api_key: str, timeout: float = 120.0,
                 reasoning_effort: Optional[str] = None):
        self.provider = provider or "openai"
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._key = api_key
        self.timeout = timeout
        self.reasoning_effort = (reasoning_effort or "").strip() or None
        self.last_meta: Optional[dict] = None

    def complete_json(self, system: str, user: str) -> dict:
        """POST {base}/chat/completions and parse a JSON object. The key stays on the Authorization header.
        Latency and token counts are logged. The prompt, the reply and the key are not."""
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._key}",
        })
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as exc:
            raise LlmError(f"the model provider returned HTTP {exc.code}") from None
        except OSError as exc:
            raise LlmError(f"the model provider could not be reached ({type(exc).__name__})") from None
        latency = round(time.perf_counter() - started, 3)
        try:
            body = json.loads(raw)
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LlmError("the model provider returned an unexpected payload") from exc
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
        meta = {
            "latency_s": latency,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "reasoning_tokens": details.get("reasoning_tokens"),
            },
        }
        self.last_meta = meta
        logger.info(
            "llm call provider=%s model=%s latency_s=%.3f prompt_tokens=%s completion_tokens=%s reasoning_tokens=%s",
            self.provider, self.model, latency, meta["usage"]["prompt_tokens"], meta["usage"]["completion_tokens"],
            meta["usage"]["reasoning_tokens"],
        )
        return _json_object(text)


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
    return OpenAICompatible(
        provider=(getattr(settings, "trace_llm_provider", "") or "openai").strip(),
        base_url=(getattr(settings, "trace_llm_base_url", "") or "https://api.openai.com/v1").strip(),
        model=model,
        api_key=raw,
        timeout=float(getattr(settings, "trace_llm_timeout", 120) or 120),
        reasoning_effort=getattr(settings, "trace_llm_reasoning_effort", None),
    )
