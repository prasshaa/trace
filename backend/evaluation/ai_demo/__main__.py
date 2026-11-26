"""Run the twenty fictional resumes through retrieval and, when a model is configured, the guarded narrative.

    cd backend && python -m evaluation.ai_demo

Writes evaluation/ai_demo/outputs/<id>.json. No API key is read from the repo. When TRACE_LLM_API_KEY is unset the
file records the lines retrieval would hand to the model and status not_configured. It does not invent a reading.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from app.services.ai_assessment.client import OpenAICompatible
from app.services.ai_assessment.service import SYSTEM, _user_prompt, narrate_packet
from evaluation.ai_demo.personas import PERSONAS, lines_for

OUT = Path(__file__).resolve().parent / "outputs"
REQUIREMENTS = [
    {"requirement_key": "C1", "title": "Understand users", "description": "Learn what users need and use it to decide."},
    {"requirement_key": "C2", "title": "Roadmap and strategy", "description": "Own the direction and the order of work."},
    {"requirement_key": "C4", "title": "Outcomes", "description": "Define a measure and decide with it."},
    {"requirement_key": "C5", "title": "Stakeholders", "description": "Bring other functions to a decision."},
    {"requirement_key": "L2", "title": "Coaching", "description": "Develop another product person."},
]
_WORD = re.compile(r"[a-z0-9%]+")


def _tokens(text: str) -> set:
    return set(_WORD.findall((text or "").lower()))


def rank(lines: list, requirements: list) -> dict:
    """In-process overlap against the requirement texts. This is the evaluation stand-in when pgvector is not applied.
    It is not represented as a pgvector result."""
    picked = []
    for req in requirements:
        q = _tokens(req["title"] + " " + req["description"])
        scored = sorted(lines, key=lambda row: len(q & _tokens(row["text"])), reverse=True)
        for row in scored[:2]:
            if row["id"] not in picked:
                picked.append(row["id"])
    return {"mode": "in_process_overlap", "reason": "pgvector_not_applied", "ids": picked}


def _client():
    """Built from the environment only. No key is read from the repository."""
    if os.environ.get("TRACE_AI_ASSESSMENT") != "1":
        return None
    key = (os.environ.get("TRACE_LLM_API_KEY") or "").strip()
    model = (os.environ.get("TRACE_LLM_MODEL") or "").strip()
    if not key or not model:
        return None
    return OpenAICompatible(
        provider=(os.environ.get("TRACE_LLM_PROVIDER") or "openai").strip(),
        base_url=(os.environ.get("TRACE_LLM_BASE_URL") or "https://api.openai.com/v1").strip(),
        model=model, api_key=key)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = _client()
    index = []
    for persona in PERSONAS:
        lines = lines_for(persona)
        retrieval = rank(lines, REQUIREMENTS)
        shown = [row for row in lines if row["id"] in set(retrieval["ids"])] or lines
        packet = {
            "fictional": True,
            "id": persona["id"],
            "name_note": persona["header"],
            "move": persona["move"],
            "target_id": persona["target_id"],
            "retrieval": retrieval,
            "lines": shown,
            "system": SYSTEM,
            "user": _user_prompt(persona["target_id"], persona["move"], shown, REQUIREMENTS, None, retrieval["mode"]),
        }
        if client is None:
            packet["status"] = "not_configured"
            packet["narrative"] = None
        else:
            try:
                packet["narrative"] = narrate_packet(client, shown, REQUIREMENTS, persona["target_id"], persona["move"])
                packet["status"] = packet["narrative"]["status"]
            except Exception as exc:                                  # noqa: BLE001
                packet["status"] = "provider_error"
                packet["detail"] = type(exc).__name__
                packet["narrative"] = None
        path = OUT / f"{persona['id']}.json"
        path.write_text(json.dumps(packet, indent=2, ensure_ascii=False) + "\n")
        index.append({"id": persona["id"], "move": persona["move"], "status": packet["status"], "lines": len(shown)})
    (OUT / "index.json").write_text(json.dumps({"fictional": True, "runs": index}, indent=2) + "\n")
    print(f"wrote {len(index)} files to {OUT}")


if __name__ == "__main__":
    main()
