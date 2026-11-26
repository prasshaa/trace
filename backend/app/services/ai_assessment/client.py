"""OpenAI-compatible chat client. Provider, model, base URL and key come from the environment. Nothing is hard-coded
as a secret, and the key is never written to a log.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

from pydantic import SecretStr


class LlmNotConfigured(Exception):
    """The demo narrative was asked for and the provider is not configured."""


class LlmError(Exception):
    """The provider failed or returned something that is not JSON. The message names the kind of failure, not the body."""


class OpenAICompatible:
    def __init__(self, *, provider: str, base_url: str, model: str, api_key: str, timeout: float = 45.0):
        self.provider = provider or "openai"
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._key = api_key
        self.timeout = timeout

    def complete_json(self, system: str, user: str) -> dict:
        """POST {base}/chat/completions and parse a JSON object. The key stays on the Authorization header."""
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._key}",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as exc:
            raise LlmError(f"the model provider returned HTTP {exc.code}") from None
        except OSError as exc:
            raise LlmError(f"the model provider could not be reached ({type(exc).__name__})") from None
        try:
            body = json.loads(raw)
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LlmError("the model provider returned an unexpected payload") from exc
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
    )
