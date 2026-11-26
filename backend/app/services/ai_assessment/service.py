"""Build the demo narrative.

Semantic retrieval (pgvector hybrid when the database function and the local model are available) chooses which
confirmed lines and which role requirements are relevant. The configured model writes the reading. Guardrails then
drop any claim that does not cite a supplied line, invents a number, ignores a negation, or treats collaboration
as ownership.

When the model is not configured the status is `not_configured` and nothing is sent anywhere.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence

from app.config import get_settings
from app.services.ai_assessment.client import LlmError, client_from_settings
from app.services.ai_assessment.guardrails import apply_guardrails
from app.services.retrieval.hybrid import HybridRetriever, Query

logger = logging.getLogger(__name__)
REVIEWED = ("user_confirmed", "user_corrected")

SYSTEM = """You are Trace, reading one person's confirmed experience for a product-management move in India.
You receive only the lines and the role requirements that retrieval selected. You do not invent employers, dates, tools, or numbers.
Rules:
- Every item in "proves" cites one or more evidence_ids from the lines you were given, and says what that line actually shows for the target role.
- "worked with", "supported", "partnered" and "collaborated" are collaboration, not ownership. Do not say the person owned or led that work.
- If a line says they did not own or never owned something, do not count it as ownership.
- Do not introduce a percentage, count, or money figure that is not written in the cited line.
- "missing" names a real gap for the target role without inventing a metric.
- "questions" ask one thing the line leaves ambiguous. Cite the line when the question is about it.
- "steps" are concrete things they can do next in their current work. Cite a line when the step builds on it.
- Write in plain, specific sentences. No scores, no percentages of fit, no "certainly".
Return a JSON object with keys opening, proves, missing, questions, steps.
proves and steps items are {"text", "evidence_ids", "why"}. missing items are {"text", "why"}. questions are {"text", "evidence_id"}.
"""


def confirmed_lines(profile) -> List[dict]:
    """Reviewed lines only, with the job they belong to. Ids are the profile's own evidence ids."""
    out = []
    for job in getattr(profile, "employment_history", []) or []:
        if job.provenance.review_status not in REVIEWED:
            continue
        employer = job.employer.employer_name if job.employer and job.employer.employer_name else None
        label = f"{job.title_raw}, {employer}" if employer else job.title_raw
        for line in list(job.responsibilities) + list(job.achievements) + list(job.other_evidence):
            if line.provenance.review_status not in REVIEWED or not (line.evidence_text or "").strip():
                continue
            out.append({"id": str(line.candidate_evidence_id), "text": " ".join(line.evidence_text.split()), "job": label})
    return out


def _queries(requirements: Sequence[dict]) -> List[Query]:
    out = []
    seen = set()
    for r in requirements:
        key = r.get("requirement_key") or r.get("key")
        if not key or key in seen:
            continue
        seen.add(key)
        title = r.get("title") or key
        description = r.get("description") or ""
        areas = r.get("direct_areas") or []
        out.append(Query(key=str(key), text=f"{title}. {description}".strip(), direct=frozenset(areas)))
        if len(out) == 24:
            break
    return out


def retrieve_for_narrative(conn, ctx, requirements: Sequence[dict]) -> dict:
    """Hybrid retrieval when the stack can do it. The mode and reason are returned; a fallback is never hidden."""
    family = _queries(requirements)
    if conn is None or ctx is None or not family:
        return {"mode": "not_run", "reason": "no_queries", "ids": []}
    settings = get_settings()
    forced = settings.model_copy(update={"trace_semantic_retrieval": True, "trace_embeddings": True})
    try:
        found = HybridRetriever(conn, forced).retrieve(ctx, family)
    except Exception as exc:                                              # noqa: BLE001
        logger.warning("narrative retrieval failed: %s", type(exc).__name__)
        return {"mode": "reader", "reason": type(exc).__name__, "ids": []}
    ids = []
    for rows in (found.results or {}).values():
        for cand in rows:
            if cand.source_id not in ids:
                ids.append(cand.source_id)
    return {"mode": found.mode, "reason": found.reason, "ids": ids[:24], "version": found.version}


def _packet(lines: List[dict], suggested_ids: Sequence[str], mode: str) -> List[dict]:
    """Hybrid mode shows the retrieved lines. A reader fallback shows the confirmed lines and says so."""
    if mode == "hybrid" and suggested_ids:
        by_id = {row["id"]: row for row in lines}
        picked = [by_id[i] for i in suggested_ids if i in by_id]
        return picked or lines
    return lines


def _user_prompt(target: str, move: str, lines: List[dict], requirements: Sequence[dict], answers: Optional[List[dict]],
                 retrieval_mode: str) -> str:
    reqs = []
    for r in list(requirements)[:12]:
        reqs.append(f"- {r.get('title') or r.get('requirement_key')}: {r.get('description') or ''}".strip())
    ev = [f"- {row['id']} | {row.get('job') or 'role not named'} | {row['text']}" for row in lines]
    answered = ""
    if answers:
        answered = "\nThe person answered:\n" + "\n".join(
            f"- {a.get('question')}: {a.get('answer')}" for a in answers if a.get("question") and a.get("answer"))
    return (
        f"Target role: {target}\nMove: {move or 'not stated'}\n"
        f"Retrieval: {retrieval_mode}. Use only the lines below.\n\n"
        f"Role requirements:\n" + "\n".join(reqs) + "\n\nConfirmed lines:\n" + "\n".join(ev) + answered
    )


def narrative_for(conn, ctx, profile, roadmap: dict, requirements: Sequence[dict], answers: Optional[List[dict]] = None) -> dict:
    """Status `ready` with a guarded narrative, or `not_configured` / `provider_error` with nothing invented."""
    settings = get_settings()
    client = client_from_settings(settings)
    lines = confirmed_lines(profile)
    if client is None:
        return {"status": "not_configured", "env": ["TRACE_AI_ASSESSMENT", "TRACE_LLM_API_KEY", "TRACE_LLM_MODEL",
                                                    "TRACE_LLM_BASE_URL", "TRACE_LLM_PROVIDER",
                                                    "TRACE_SEMANTIC_RETRIEVAL", "TRACE_EMBEDDINGS"]}
    retrieval = retrieve_for_narrative(conn, ctx, requirements)
    shown = _packet(lines, retrieval.get("ids") or [], retrieval.get("mode") or "")
    if not shown:
        return {"status": "no_evidence", "retrieval": retrieval, "proves": [], "missing": [], "questions": [], "steps": []}
    target = (roadmap or {}).get("target_role") or "the target role"
    move = ((roadmap or {}).get("move") or {}).get("text") or ""
    try:
        raw = client.complete_json(SYSTEM, _user_prompt(target, move, shown, requirements, answers, retrieval.get("mode") or ""))
    except LlmError as exc:
        logger.warning("narrative provider failed: %s", type(exc).__name__)
        return {"status": "provider_error", "retrieval": {k: retrieval[k] for k in ("mode", "reason") if k in retrieval},
                "detail": str(exc)}
    guarded = apply_guardrails(raw, shown)
    return {"status": "ready", "provider": client.provider, "model": client.model,
            "retrieval": {"mode": retrieval.get("mode"), "reason": retrieval.get("reason"), "line_ids": [row["id"] for row in shown]},
            "lines": shown, **guarded}


def narrate_packet(client, lines: List[dict], requirements: Sequence[dict], target: str, move: str = "") -> dict:
    """The same prompt and guardrails, for the offline evaluation of fictional resumes (no database)."""
    raw = client.complete_json(SYSTEM, _user_prompt(target, move, lines, requirements, None, "in_process"))
    guarded = apply_guardrails(raw, lines)
    return {"status": "ready", "provider": getattr(client, "provider", "openai"), "model": getattr(client, "model", ""),
            **guarded}
