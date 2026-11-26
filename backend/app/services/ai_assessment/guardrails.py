"""Checks on a model's reading. The model may suggest; these rules drop anything it cannot support.

A claim is kept only when every number it states is in the cited experience, a negation is not flipped into
ownership, and collaboration is not rewritten as ownership. Steps may advise future ownership ("ask to own") and
may name a duration or a small count. Evidence ids are removed before the number check, because a UUID is full of
digits, and they are stripped from anything the user will read.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple

_NUM = re.compile(r"\d+(?:\.\d+)?%?")
_EVAL_ID = re.compile(r"\bF\d{2,}-L\d+\b", re.I)
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_OWNERSHIP_WORD = re.compile(
    r"\b(own(?:s|ed|ing)?|ownership|led|leading|leads|defined|define|defining|drove|drive|"
    r"set|managed|manage|headed|head|ran|run|responsible for|decided|decide|chose|choose)\b",
    re.I,
)
_COLLAB = re.compile(r"\b(worked with|working with|supported|supporting|partnered|partnering|collaborated|collaborating)\b", re.I)
_CLEAR_OWNER = re.compile(r"\b(owned|led|defined|drove|built|launched|shipped|headed|managed|decided)\b", re.I)
_DENIED = re.compile(
    r"\b(?:did not|didn't|do not|don't|never|not|without)\b"
    r"(?:\s+\w+){0,6}\s+"
    r"(?:own|lead|led|set|define|decide|choose|chose|manage|managed|run|ran)\w*",
    re.I,
)
_MULTIPLIER = re.compile(r"\b(doubled|tripled|halved|twice|quadrupled)\b", re.I)
_SPELLED_QTY = re.compile(
    r"\b((?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|"
    r"hundred|thousand)(?:\s+\w+){0,3}\s+(?:percent|percentage|points?|users?|merchants?|customers?|"
    r"hours?|rupees?|lakhs?|crores?))\b",
    re.I,
)
_ADVICE_NUM = re.compile(
    r"\b\d+\s*[-–]?\s*(?:minute|min|hour|hr|day|week|month)s?\b"
    r"|\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:minute|hour|day|week|month)s?\b"
    r"|\b(?:[1-9]|10)\s*[-–]\s*(?:[1-9]|10)\s*(?:items?|steps?|pages?|slides?|bullets?)\b"
    r"|\b(?:[1-9]|10)\s*(?:items?|steps?|pages?|slides?|bullets?)\b",
    re.I,
)
_ADVICE_WORDS = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:minute|minutes|hour|hours|day|days|week|weeks|month|months)\b",
    re.I,
)
_FUTURE = re.compile(
    r"\b(ask to|asks to|propose|volunteer|take on|next|will|write|draft|prepare|build a|in the next)\b",
    re.I,
)
_JARGON = (
    (re.compile(r"\bno line shows\b", re.I), "Nothing in your experience shows"),
    (re.compile(r"\bthe confirmed lines\b", re.I), "your experience"),
    (re.compile(r"\bconfirmed lines\b", re.I), "your experience"),
    (re.compile(r"\bthis line\b", re.I), "this"),
    (re.compile(r"\bthe lines\b", re.I), "your experience"),
    (re.compile(r"\bevidence ids?\b", re.I), ""),
    (re.compile(r"\bretrieval\b", re.I), ""),
)
_DISCLAIMER = re.compile(r"^(?:no |you did not|they did not|this does not|it does not|that does not)\b", re.I)


def _cited_text(ids: Iterable[str], lines: Dict[str, dict]) -> Optional[str]:
    parts = []
    for i in ids:
        row = lines.get(i)
        if row is None:
            return None
        parts.append(row.get("text") or "")
    return "\n".join(parts)


def remove_ids(text: str, known_ids: Iterable[str] = ()) -> str:
    """Take evidence ids out of a sentence before any number check, and before the user sees it."""
    out = text or ""
    for i in known_ids:
        if i:
            out = re.sub(re.escape(str(i)), " ", out, flags=re.I)
    out = _EVAL_ID.sub(" ", out)
    out = _UUID.sub(" ", out)
    return " ".join(out.split())


def _scrub(text: str, known_ids: Iterable[str]) -> str:
    out = remove_ids(text, known_ids)
    for rx, repl in _JARGON:
        out = rx.sub(repl, out)
    return " ".join(out.split())


def invented_number(claim: str, cited: str, *, advice: bool = False) -> bool:
    """True when the claim states a figure the cited experience does not contain."""
    blob = (cited or "").lower()
    text = claim or ""
    if advice:
        text = _ADVICE_NUM.sub(" ", text)
        text = _ADVICE_WORDS.sub(" ", text)
    for n in _NUM.findall(text):
        if n.lower() not in blob and n.rstrip("%").lower() not in blob:
            return True
    for word in _MULTIPLIER.findall(text):
        if word.lower() not in blob:
            return True
    for phrase in _SPELLED_QTY.findall(text):
        if phrase.lower() not in blob:
            return True
    return False


def _future(text: str) -> bool:
    return bool(_FUTURE.search(text or ""))


def collaboration_as_ownership(claim: str, cited: str) -> bool:
    """True when the claim says the person already owned the work, and the experience only collaborates."""
    if _future(claim):
        return False
    if not _OWNERSHIP_WORD.search(claim or ""):
        return False
    if re.search(r"\b(?:did not|didn't|do not|don't|never|not)\b", claim or "", re.I):
        return False
    if not _COLLAB.search(cited or ""):
        return False
    if _CLEAR_OWNER.search(cited or "") and not _DENIED.search(cited or ""):
        return False
    return True


def negation_as_positive(claim: str, cited: str) -> bool:
    """True when the experience denies an action and the claim states that action as done."""
    if _future(claim):
        return False
    if not _DENIED.search(cited or ""):
        return False
    if re.search(r"\b(?:did not|didn't|do not|don't|never|not)\b", claim or "", re.I):
        return False
    return bool(_OWNERSHIP_WORD.search(claim or ""))


def keep_claim(claim: dict, lines: Dict[str, dict], *, require_citation: bool, kind: str) -> Tuple[Optional[dict], Optional[str]]:
    """The claim and None, or None and a drop reason."""
    if not isinstance(claim, dict):
        return None, "empty"
    raw_text = " ".join(str(claim.get("text") or "").split())
    raw_why = " ".join(str(claim.get("why") or "").split())
    if not raw_text:
        return None, "empty"
    if _EVAL_ID.search(raw_text) or _UUID.search(raw_text):
        return None, "leaked_id"
    ids = [str(i) for i in (claim.get("evidence_ids") or []) if i]
    if any(i and i.lower() in raw_text.lower() for i in ids):
        return None, "leaked_id"
    known = list(lines)
    if require_citation and not ids:
        return None, "no_citation"
    cited = _cited_text(ids, lines) if ids else ""
    if cited is None:
        return None, "unknown_citation"
    universe = cited if ids else " ".join(row.get("text") or "" for row in lines.values())
    advice = kind == "step"
    if invented_number(remove_ids(raw_text, known), universe, advice=advice):
        return None, "invented_number"
    if raw_why and invented_number(remove_ids(raw_why, known), universe, advice=advice):
        return None, "invented_number"
    if ids and not advice and collaboration_as_ownership(raw_text, cited):
        return None, "collaboration_as_ownership"
    if ids and not advice and negation_as_positive(raw_text, cited):
        return None, "negation"
    if kind == "strength" and _DISCLAIMER.search(raw_text):
        return None, "disclaimer"
    text = _scrub(raw_text, known)
    why = _scrub(raw_why, known)
    if len(text) < 8:
        return None, "leaked_id"
    return {"text": text, "evidence_ids": ids, "why": why}, None


def _bump(reasons: Dict[str, int], reason: Optional[str]) -> None:
    if reason:
        reasons[reason] = reasons.get(reason, 0) + 1


def apply_guardrails(raw: dict, lines: List[dict]) -> dict:
    """Drop unsupported claims. Counts each drop by reason. User-facing text has ids and audit jargon removed."""
    by_id = {row["id"]: row for row in lines}
    reasons: Dict[str, int] = {}
    strengths = _collect(raw.get("strengths") or raw.get("proves") or [], by_id, "strength", True, reasons)[:5]
    transferable = _collect(raw.get("transferable") or [], by_id, "strength", True, reasons)[:3]
    gaps = _gaps(raw.get("gaps") or raw.get("missing") or [], by_id, reasons)[:3]
    questions = _questions(raw.get("questions") or [], by_id, reasons)[:3]
    steps = _collect(raw.get("steps") or [], by_id, "step", False, reasons)[:3]
    opening = _scrub(" ".join(str(raw.get("opening") or "").split()), by_id)
    if invented_number(remove_ids(str(raw.get("opening") or ""), by_id), " ".join(r["text"] for r in lines)):
        opening = ""
        _bump(reasons, "invented_number")
    return {
        "opening": opening,
        "strengths": strengths,
        "transferable": transferable,
        "gaps": gaps,
        "questions": questions,
        "steps": steps,
        # Names the previous prompt used. Same objects, so older callers keep working.
        "proves": strengths,
        "missing": gaps,
        "dropped": sum(reasons.values()),
        "dropped_reasons": reasons,
    }


def _collect(items, by_id, kind, require, reasons) -> List[dict]:
    out = []
    for item in items:
        kept, reason = keep_claim(item, by_id, require_citation=require, kind=kind)
        if kept:
            out.append(kept)
        else:
            _bump(reasons, reason)
    return out


def _gaps(items, by_id, reasons) -> List[dict]:
    universe = " ".join(row["text"] for row in by_id.values())
    out = []
    for item in items:
        if not isinstance(item, dict):
            _bump(reasons, "empty")
            continue
        text = " ".join(str(item.get("text") or "").split())
        why = " ".join(str(item.get("why") or "").split())
        if not text:
            _bump(reasons, "empty")
            continue
        if invented_number(remove_ids(text, by_id), universe) or (why and invented_number(remove_ids(why, by_id), universe)):
            _bump(reasons, "invented_number")
            continue
        cleaned = _scrub(text, by_id)
        if len(cleaned) < 8:
            _bump(reasons, "leaked_id")
            continue
        out.append({"text": cleaned, "why": _scrub(why, by_id)})
    return out


def _questions(items, by_id, reasons) -> List[dict]:
    universe = " ".join(row["text"] for row in by_id.values())
    out = []
    for item in items:
        if not isinstance(item, dict):
            _bump(reasons, "empty")
            continue
        text = " ".join(str(item.get("text") or "").split())
        eid = str(item["evidence_id"]) if item.get("evidence_id") else None
        if not text:
            _bump(reasons, "empty")
            continue
        if eid and eid not in by_id:
            _bump(reasons, "unknown_citation")
            continue
        cited = by_id[eid]["text"] if eid else universe
        if invented_number(remove_ids(text, by_id), cited):
            _bump(reasons, "invented_number")
            continue
        cleaned = _scrub(text, by_id)
        if len(cleaned) < 8:
            _bump(reasons, "leaked_id")
            continue
        out.append({"text": cleaned, "evidence_id": eid})
    return out
