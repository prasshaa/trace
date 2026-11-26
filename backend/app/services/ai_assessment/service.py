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
Speak only in the second person ("you"). Never say "line", "confirmed line", "evidence id", "retrieval", or "they" for this person.
Use only the experience excerpts. Do not invent employers, dates, tools, companies, vacancies, salaries, or numbers.
Private notes in the prompt are for you. Never repeat them, and never write "you are not expected to", "not claiming", "do not claim", "without claiming", "without saying", or "treat that as a request".

Safety, which overrides tone:
- Put citation ids only in evidence_ids / evidence_id. Never write an id, or the word "line", in text or why.
- "worked with", "supported", "partnered" and "collaborated" are collaboration, not ownership. Do not say the person already owned or led that work.
- If an excerpt says they did not own, set, define, decide, choose, manage, or run one thing, do not say they did that thing. A different thing in the same excerpt can still be theirs.
- Do not introduce a percentage, count, money figure, fraction, or a word like "doubled" that is not written in the cited excerpt.
- At most one step may cite one PEER id, and only when that step is about the same move as the pattern. A progression pattern belongs on a step about getting promoted or owning the roadmap. A background pattern belongs on a step about that background. Do not cite a PEER id on a generic preparation step (a write-up, a practice round, a course, or a portfolio piece), on a strength, or in the opening. Do not change its numbers.
- A strength is only what they did. A disclaimer is not a strength.
- A step may tell them to ask to own something next. That is advice. Do not tell them to tell a recruiter they already owned something the excerpt denies.
- A step may name a duration (for example two weeks) or a small count of pages.

Length: opening at most 45 words. Each why at most 30 words. The whole reading at most 380 words.

Shape the JSON object exactly like this:
- opening: two short sentences. First, the strongest true signal for this move. Then the single most important gap.
- strengths: up to 4 items, positive only. {"text", "evidence_ids", "why"}.
- transferable: up to 3 items, only when they are not already in product. How this experience maps to product work, cited the same way. Empty for someone already a product manager.
- gaps: at most 3, ranked, the ones that matter most for this target. {"text", "why"}. The why explains the hiring signal once.
- questions: up to 3. {"text", "evidence_id"}.
- steps: at most 3. {"text", "evidence_ids", "why"}. At least one is something they can do in the job they have now. At least one is an artefact drawn from this person's own evidence: a metric teardown when they have a measure, a case write-up when they shipped something, a short requirement when they have user notes, or a PRD when they already write recommendations. Name the actual work (the queue, the workshop, the dashboard). Vary the timing and the shape. Use "this week", "this month", or "before the next round" when that fits. Do not start the artefact step with "In about two weeks, write". Do not give everyone the same PRD.

India, at most one route in the whole reading, matched to the move:
- Into an associate role: one mention of either an APM programme or a product-sense round.
- Into a product manager role: one mention of a product-sense round together with a portfolio PRD.
- Into a senior product manager role: one mention of a leadership or strategy case, or a senior referral. Do not mention an APM programme, a product-sense round, or a portfolio piece for a product-sense round.
Never suggest asking their current manager, or the product manager they support, for a referral outside the company. Do not invent an opening or a salary.
Coaching other product managers matters only for a senior product manager move.
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


def _peer_lines(roadmap: Optional[dict]) -> List[dict]:
    peers = (roadmap or {}).get("peers") or {}
    if not peers.get("available"):
        return []
    chosen = []
    for item in peers.get("patterns") or []:
        text = item.get("text") or ""
        if item.get("id") and ("started as" in text or "came from" in text):
            chosen.append({"id": item["id"], "text": text, "job": "PMs with a similar career path"})
        if len(chosen) == 2:
            break
    return chosen


def _user_prompt(target: str, move: str, lines: List[dict], requirements: Sequence[dict], answers: Optional[List[dict]],
                 retrieval_mode: str, peers: Optional[dict] = None) -> str:
    reqs = []
    for r in list(requirements)[:4]:
        desc = " ".join((r.get("description") or "").split())[:140]
        note = f" (context for you, do not repeat: {r['level_note']})" if r.get("level_note") else ""
        reqs.append(f"- {r.get('title') or r.get('requirement_key')}: {desc}{note}".strip())
    ev = [f"- {row['id']} | {row.get('job') or 'role not named'} | {row['text']}" for row in lines[:6]]
    answered = ""
    if answers:
        answered = "\nAnswered:\n" + "\n".join(
            f"- {a.get('question')}: {a.get('answer')}" for a in answers[:3] if a.get("question") and a.get("answer"))
    peer_rows = _peer_lines({"peers": peers} if peers is not None else None)
    peer_block = ""
    if peer_rows:
        listed = "\n".join(f"- {row['id']}: {row['text']}" for row in peer_rows)
        peer_block = "\n\nOptional, at most one step, only if it matches that step. Do not change the numbers:\n" + listed
    del retrieval_mode
    return (
        f"Target: {target}\nMove: {move or 'not stated'}\n\n"
        f"Hired for:\n" + "\n".join(reqs) + "\n\nExcerpts:\n" + "\n".join(ev) + answered
        + peer_block
    )


def _env_status() -> dict:
    return {"status": "not_configured", "env": ["TRACE_AI_ASSESSMENT", "TRACE_LLM_API_KEY", "TRACE_LLM_MODEL",
                                                "TRACE_LLM_BASE_URL", "TRACE_LLM_PROVIDER", "TRACE_LLM_TIMEOUT",
                                                "TRACE_LLM_REASONING_EFFORT", "TRACE_LLM_MAX_OUTPUT_TOKENS",
                                                "TRACE_SEMANTIC_RETRIEVAL", "TRACE_EMBEDDINGS"]}


def _paraphrases_pattern(opening: str, pattern: str) -> bool:
    """True when the opening already states this pattern's 'N of the M' figure."""
    import re
    numbers = re.findall(r"\d+", pattern or "")
    if len(numbers) < 2:
        return pattern in opening if pattern else False
    return bool(re.search(rf"\b{numbers[0]}\s+of\s+(?:the\s+)?{numbers[1]}\b", opening or ""))


def _keep_peer_pattern(guarded: dict, peers: dict) -> dict:
    """Leave the opening as the model wrote it. Do not prepend a peer sentence onto a short reading."""
    from app.services.ai_assessment.guardrails import plain_text
    opening = plain_text(guarded.get("opening"))
    patterns = (peers or {}).get("patterns") or []
    if any(_paraphrases_pattern(opening, item.get("text") or "") for item in patterns):
        guarded["opening"] = opening
        return guarded
    guarded["opening"] = opening
    return guarded


def narrative_for(conn, ctx, profile, roadmap: dict, requirements: Sequence[dict] = (), answers: Optional[List[dict]] = None,
                  on_attempt=None) -> dict:
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
    peers = (roadmap or {}).get("peers") or {}
    peer_rows = _peer_lines(roadmap)
    try:
        raw = client.complete_json(
            SYSTEM, _user_prompt(target, move, shown, rubric, answers, retrieval.get("mode") or "", peers or None),
            on_attempt=on_attempt)
    except LlmError as exc:
        logger.warning("narrative provider failed: %s", type(exc).__name__)
        meta = getattr(client, "last_meta", None) or {}
        message = str(exc)
        timed_out = "timed out" in message
        cut_off = "cut off" in message
        if timed_out:
            detail = "The reading took too long. It can be tried again. Your plan is unchanged."
        elif cut_off:
            detail = "The reading was cut off before it finished. Open the plan again to try once more. Your saved plan is unchanged."
        else:
            detail = message
        return {"status": "retry" if timed_out or cut_off else "provider_error",
                "retrieval": {k: retrieval[k] for k in ("mode", "reason") if k in retrieval},
                "detail": detail,
                "attempts": meta.get("attempts"), "latency_s": meta.get("latency_s")}
    guarded = apply_guardrails(raw, shown + peer_rows)
    guarded = _keep_peer_pattern(guarded, peers)
    meta = getattr(client, "last_meta", None) or {}
    return {"status": "ready", "provider": client.provider, "model": client.model,
            "retrieval": retrieval, "lines": shown, "latency_s": meta.get("latency_s"), "usage": meta.get("usage"),
            "attempts": meta.get("attempts"), **guarded}


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
            from app.services.ai_assessment.jobs import note

            def on_attempt(n: int) -> None:
                note(key, attempt=n, phase="retrying" if n > 1 else "reading")

            return narrative_for(conn, ctx, profile, roadmap, requirements, on_attempt=on_attempt)
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
            "latency_s": meta.get("latency_s"), "usage": meta.get("usage"), "attempts": meta.get("attempts"), **guarded}
