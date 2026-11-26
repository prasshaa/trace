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
from app.services.ai_assessment.rubric import requirements_for
from app.services.ai_assessment.select import select_lines

logger = logging.getLogger(__name__)
REVIEWED = ("user_confirmed", "user_corrected")

SYSTEM = """You are a career coach sitting with this person, in India, helping them toward a product role.
Speak only in the second person ("you"). Never say "line", "confirmed line", "evidence id", "retrieval", or "they".
You are given experience excerpts and the requirements that matter for THIS target. Do not apply a senior rubric to an associate role.
Do not invent employers, dates, tools, companies, vacancies, salaries, or numbers.

Safety, which overrides tone:
- Put citation ids only in evidence_ids / evidence_id. Never write an id, or the word "line", in text or why.
- "worked with", "supported", "partnered" and "collaborated" are collaboration, not ownership. Do not say they already owned or led that work.
- If the excerpt says they did not own, set, define, decide, choose, manage, or run something, do not say they did.
- Do not introduce a percentage, count, money figure, or a word like "doubled" that is not written in the cited excerpt.
- A strength is only what they did. A disclaimer ("you did not…") is not a strength.
- A step may tell them to ask to own something next. That is advice, not a claim they already own it. A step may name a duration (for example two weeks) or a small count of pages.

Shape the JSON object exactly like this:
- opening: two sentences. First, the strongest true signal for this move. Then the single most important gap.
- strengths: up to 4 items, positive only. {"text", "evidence_ids", "why"}.
- transferable: up to 3 items, only when they are not already in product. How this experience maps to product work, cited the same way. Empty for someone already a product manager.
- gaps: at most 3, ranked, the ones that matter most for this target. {"text", "why"}. Say why it matters in Indian product hiring (an APM programme, a product-sense round, a portfolio, a referral) without inventing a fact about a named company.
- questions: up to 3. {"text", "evidence_id"}.
- steps: at most 3. {"text", "evidence_ids", "why"}. At least one is something they can do in the job they have now. At least one is an interview or portfolio artefact (a one-page PRD, a metric teardown, or a case write-up) they could finish in about two weeks. Where it fits, mention an Indian route: an APM programme, a product-sense round, a portfolio piece, or a referral. Do not invent an opening or a salary.

Associate targets are not expected to own company strategy or to coach other product managers. Product Manager targets own one area, and are not expected to coach. Senior Product Manager targets can be asked about coaching, scope and senior stakeholders.
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


def _user_prompt(target: str, move: str, lines: List[dict], requirements: Sequence[dict], answers: Optional[List[dict]],
                 retrieval_mode: str) -> str:
    reqs = []
    for r in list(requirements)[:12]:
        note = f" {r.get('level_note')}" if r.get("level_note") else ""
        reqs.append(f"- {r.get('title') or r.get('requirement_key')}: {r.get('description') or ''}{note}".strip())
    ev = [f"- {row['id']} | {row.get('job') or 'role not named'} | {row['text']}" for row in lines]
    answered = ""
    if answers:
        answered = "\nThe person answered:\n" + "\n".join(
            f"- {a.get('question')}: {a.get('answer')}" for a in answers if a.get("question") and a.get("answer"))
    return (
        f"Target role: {target}\nMove: {move or 'not stated'}\n"
        f"How the excerpts were chosen (do not mention this): {retrieval_mode}. Use only the excerpts below.\n\n"
        f"What this target is hired for:\n" + "\n".join(reqs) + "\n\nExperience excerpts:\n" + "\n".join(ev) + answered
    )


def _env_status() -> dict:
    return {"status": "not_configured", "env": ["TRACE_AI_ASSESSMENT", "TRACE_LLM_API_KEY", "TRACE_LLM_MODEL",
                                                "TRACE_LLM_BASE_URL", "TRACE_LLM_PROVIDER", "TRACE_LLM_TIMEOUT",
                                                "TRACE_LLM_REASONING_EFFORT", "TRACE_SEMANTIC_RETRIEVAL", "TRACE_EMBEDDINGS"]}


def narrative_for(conn, ctx, profile, roadmap: dict, requirements: Sequence[dict] = (), answers: Optional[List[dict]] = None) -> dict:
    """Status `ready` with a guarded narrative, or `not_configured` / `provider_error` with nothing invented.
    `requirements` from the caller are ignored in favour of the target rubric, so an associate is not scored on coaching."""
    del requirements
    client = client_from_settings(get_settings())
    lines = confirmed_lines(profile)
    if client is None:
        return _env_status()
    target_id = (roadmap or {}).get("target_role_id") or ""
    rubric = requirements_for(target_id)
    shown, retrieval = select_lines(lines, rubric, conn, ctx)
    if not shown:
        return {"status": "no_evidence", "retrieval": retrieval, "strengths": [], "gaps": [], "questions": [], "steps": [],
                "proves": [], "missing": []}
    target = (roadmap or {}).get("target_role") or target_id or "the target role"
    move = ((roadmap or {}).get("move") or {}).get("text") or ""
    try:
        raw = client.complete_json(SYSTEM, _user_prompt(target, move, shown, rubric, answers, retrieval.get("mode") or ""))
    except LlmError as exc:
        logger.warning("narrative provider failed: %s", type(exc).__name__)
        return {"status": "provider_error", "retrieval": {k: retrieval[k] for k in ("mode", "reason") if k in retrieval},
                "detail": str(exc)}
    guarded = apply_guardrails(raw, shown)
    meta = getattr(client, "last_meta", None) or {}
    return {"status": "ready", "provider": client.provider, "model": client.model,
            "retrieval": retrieval, "lines": shown, "latency_s": meta.get("latency_s"), "usage": meta.get("usage"),
            **guarded}


def start_narrative(ctx, profile, roadmap: dict, requirements: Sequence[dict] = ()) -> dict:
    """Return at once. The model call, if one is needed, continues in the background."""
    if client_from_settings(get_settings()) is None:
        return _env_status()
    from app.services.ai_assessment.jobs import launch
    from app.services.candidate_profile.entries import profile_version
    key = f"{ctx.owner_user_id}:{(roadmap or {}).get('target_role_id')}:{profile_version(profile)}"

    def work() -> dict:
        conn = None
        try:
            try:
                from app.db import DatabaseNotConfigured, connect_runtime
                conn = connect_runtime(get_settings())
            except DatabaseNotConfigured:
                conn = None
            except Exception as exc:                                      # noqa: BLE001
                logger.warning("narrative database unavailable: %s", type(exc).__name__)
                conn = None
            return narrative_for(conn, ctx, profile, roadmap, requirements)
        finally:
            if conn is not None:
                conn.close()

    return launch(key, work)


def narrate_packet(client, lines: List[dict], requirements: Sequence[dict], target: str, move: str = "",
                   retrieval_mode: str = "in_process") -> dict:
    """The same prompt and guardrails, for the offline evaluation of fictional resumes (no database)."""
    raw = client.complete_json(SYSTEM, _user_prompt(target, move, lines, requirements, None, retrieval_mode))
    guarded = apply_guardrails(raw, lines)
    meta = getattr(client, "last_meta", None) or {}
    return {"status": "ready", "provider": getattr(client, "provider", "openai"), "model": getattr(client, "model", ""),
            "latency_s": meta.get("latency_s"), "usage": meta.get("usage"), **guarded}
