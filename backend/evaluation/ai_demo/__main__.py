"""Run the fictional resumes through retrieval and, when a model is configured, the guarded narrative.

    cd backend && python -m evaluation.ai_demo

TRACE_LLM_TIMEOUT (seconds) is read from the environment when a model is configured. TRACE_LLM_REASONING_EFFORT
defaults to low for grok-4.7; set it to medium, high or xhigh to override, or to an empty value to omit it.
With no key the files are status not_configured. Nothing is invented.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.services.ai_assessment.client import OpenAICompatible
from app.services.ai_assessment.rubric import requirements_for
from app.services.ai_assessment.select import select_lines
from app.services.ai_assessment.service import SYSTEM, _user_prompt, narrate_packet
from evaluation.ai_demo.personas import PERSONAS, lines_for

OUT = Path(__file__).resolve().parent / "outputs"


def _reasoning_effort():
    """Unset means low. An explicit empty value omits the field."""
    if "TRACE_LLM_REASONING_EFFORT" not in os.environ:
        return "low"
    return (os.environ.get("TRACE_LLM_REASONING_EFFORT") or "").strip()


def _client():
    """Built from the environment only. No key is read from the repository."""
    if os.environ.get("TRACE_AI_ASSESSMENT") != "1":
        return None
    key = (os.environ.get("TRACE_LLM_API_KEY") or "").strip()
    model = (os.environ.get("TRACE_LLM_MODEL") or "").strip()
    if not key or not model:
        return None
    raw_timeout = (os.environ.get("TRACE_LLM_TIMEOUT") or "120").strip()
    try:
        timeout = float(raw_timeout)
    except ValueError:
        timeout = 120.0
    return OpenAICompatible(
        provider=(os.environ.get("TRACE_LLM_PROVIDER") or "openai").strip(),
        base_url=(os.environ.get("TRACE_LLM_BASE_URL") or "https://api.openai.com/v1").strip(),
        model=model, api_key=key, timeout=timeout,
        reasoning_effort=_reasoning_effort())


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = _client()
    index = []
    for persona in PERSONAS:
        lines = lines_for(persona)
        rubric = requirements_for(persona["target_id"])
        shown, retrieval = select_lines(lines, rubric, None, None)
        packet = {
            "fictional": True,
            "id": persona["id"],
            "name_note": persona["header"],
            "move": persona["move"],
            "target_id": persona["target_id"],
            "retrieval": retrieval,
            "lines": shown,
            "requirements": [r["requirement_key"] for r in rubric],
            "system": SYSTEM,
            "user": _user_prompt(persona["target_id"], persona["move"], shown, rubric, None, retrieval.get("mode") or ""),
        }
        if client is None:
            packet["status"] = "not_configured"
            packet["narrative"] = None
        else:
            try:
                packet["narrative"] = narrate_packet(
                    client, shown, rubric, persona["target_id"], persona["move"], retrieval.get("mode") or "")
                packet["status"] = packet["narrative"]["status"]
                packet["latency_s"] = packet["narrative"].get("latency_s")
                packet["usage"] = packet["narrative"].get("usage")
                packet["attempts"] = packet["narrative"].get("attempts")
            except Exception as exc:                                  # noqa: BLE001
                packet["status"] = "provider_error"
                packet["detail"] = type(exc).__name__
                packet["narrative"] = None
        (OUT / f"{persona['id']}.json").write_text(json.dumps(packet, indent=2, ensure_ascii=False) + "\n")
        index.append({"id": persona["id"], "move": persona["move"], "status": packet["status"],
                      "lines": len(shown), "retrieval": retrieval.get("mode")})
    (OUT / "index.json").write_text(json.dumps({"fictional": True, "runs": index}, indent=2) + "\n")
    print(f"wrote {len(index)} files to {OUT}")


if __name__ == "__main__":
    main()
