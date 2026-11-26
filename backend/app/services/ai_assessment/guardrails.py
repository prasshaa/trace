"""Checks on a model's reading. The model may suggest; these rules drop anything it cannot support.

A claim is kept only when it cites a line that was actually supplied, every number in the claim appears in that line,
a negation in the line is not restated as a positive, and a collaboration verb is not rewritten as ownership.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional

from app.services.capability.interpret import NEGATE, interpret

_NUM = re.compile(r"\d+(?:\.\d+)?%?")
_OWNERSHIP_WORD = re.compile(r"\b(own(?:s|ed|ing)?|ownership|led|leading|defined|drove|drive)\b", re.I)
_COLLAB = re.compile(r"\b(worked with|working with|supported|supporting|partnered|partnering|collaborated|collaborating)\b", re.I)
_CLEAR_OWNER = re.compile(r"\b(owned|led|defined|drove|built|launched|shipped)\b", re.I)
_DENIED = re.compile(r"\b(?:did not|didn't|never|without)\b(?:\s+\w+){0,5}\s+(?:own\w*|lead\w*|led)\b", re.I)


def _numbers(text: str) -> List[str]:
    return _NUM.findall(text or "")


def _cited_text(ids: Iterable[str], lines: Dict[str, dict]) -> Optional[str]:
    parts = []
    for i in ids:
        row = lines.get(i)
        if row is None:
            return None
        parts.append(row.get("text") or "")
    return "\n".join(parts)


def invented_number(claim: str, cited: str) -> bool:
    """True when the claim states a figure the cited line does not contain."""
    blob = cited or ""
    for n in _numbers(claim):
        if n not in blob and n.rstrip("%") not in blob:
            return True
    return False


def collaboration_as_ownership(claim: str, cited: str) -> bool:
    """True when the claim says the person owned or led the work, and the line only collaborates."""
    if not _OWNERSHIP_WORD.search(claim or "") or NEGATE.search(claim or ""):
        return False
    if not _COLLAB.search(cited or ""):
        return False
    if _CLEAR_OWNER.search(cited or ""):
        return False
    reading = interpret(cited)
    if reading.clear.keys() & {"roadmap_ownership", "strategy_portfolio", "outcome_ownership"}:
        return False
    return True


def negation_as_positive(claim: str, cited: str) -> bool:
    """True when the line denies ownership and the claim states it anyway."""
    if not _DENIED.search(cited or ""):
        return False
    if NEGATE.search(claim or ""):
        return False
    return bool(_OWNERSHIP_WORD.search(claim or "") or re.search(r"\broadmap\b", claim or "", re.I))


def keep_claim(claim: dict, lines: Dict[str, dict], *, require_citation: bool) -> Optional[dict]:
    """The claim, or None when a guardrail rejects it."""
    if not isinstance(claim, dict):
        return None
    text = " ".join(str(claim.get("text") or "").split())
    if not text:
        return None
    ids = [str(i) for i in (claim.get("evidence_ids") or []) if i]
    cited = _cited_text(ids, lines) if ids else ""
    if require_citation and cited is None:
        return None
    if require_citation and not ids:
        return None
    if cited is None:
        return None
    if invented_number(text, cited if ids else " ".join(row["text"] for row in lines.values())):
        return None
    if ids and (collaboration_as_ownership(text, cited) or negation_as_positive(text, cited)):
        return None
    why = " ".join(str(claim.get("why") or "").split())
    if why and invented_number(why, cited if ids else " ".join(row["text"] for row in lines.values())):
        return None
    return {"text": text, "evidence_ids": ids, "why": why}


def apply_guardrails(raw: dict, lines: List[dict]) -> dict:
    """Drop unsupported claims. Gaps may be uncited, but they may not invent a number."""
    by_id = {row["id"]: row for row in lines}
    proves = []
    for item in raw.get("proves") or []:
        kept = keep_claim(item, by_id, require_citation=True)
        if kept:
            proves.append(kept)
    steps = []
    for item in raw.get("steps") or []:
        kept = keep_claim(item, by_id, require_citation=False)
        if kept and (not kept["evidence_ids"] or _cited_text(kept["evidence_ids"], by_id) is not None):
            # A step that cites must cite a real line. A step with no citation is advice, and still cannot invent a number.
            if kept["evidence_ids"] and _cited_text(kept["evidence_ids"], by_id) is None:
                continue
            steps.append(kept)
    missing = []
    universe = " ".join(row["text"] for row in lines)
    for item in raw.get("missing") or []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        if text and not invented_number(text, universe):
            missing.append({"text": text, "why": " ".join(str(item.get("why") or "").split())})
    questions = []
    for item in raw.get("questions") or []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        eid = str(item["evidence_id"]) if item.get("evidence_id") else None
        if not text:
            continue
        if eid and eid not in by_id:
            continue
        if invented_number(text, by_id[eid]["text"] if eid else universe):
            continue
        questions.append({"text": text, "evidence_id": eid})
    opening = " ".join(str(raw.get("opening") or "").split())
    if invented_number(opening, universe):
        opening = ""
    dropped = int(raw.get("_offered") or 0)  # filled by the caller when it counted the raw lists
    return {"opening": opening, "proves": proves[:6], "missing": missing[:5], "questions": questions[:4], "steps": steps[:5],
            "dropped": max(0, _count(raw) - (len(proves) + len(missing) + len(questions) + len(steps))) if not dropped else dropped}


def _count(raw: dict) -> int:
    return sum(len(raw.get(k) or []) for k in ("proves", "missing", "questions", "steps"))
