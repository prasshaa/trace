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
# Hard verbs are ownership whoever the sentence is about. choose/decide/set/define/run count only when the
# user is the one who did them ("you chose", or the clause starts with the verb), not "has to choose".
_HARD_OWNER = re.compile(
    r"\b(own(?:s|ed|ing)?|ownership|led|leading|leads|headed|head|managed|manage|drove|drive|"
    r"responsible for|spearheads?|spearheaded|spearheading|took charge|in charge of|accountable for|"
    r"single-handedly|owned end to end)\b",
    re.I,
)
_SOFT_OWNER = re.compile(
    r"(?:^|(?<=[.!?;]\s))(chose|choose|decided|decide|set|defined|define|ran|run)\b"
    r"|\b(?:you|i)\s+(chose|choose|decided|decide|set|defined|define|ran|run)\b",
    re.I,
)
_OWNER_VERB = re.compile(
    r"\b(own(?:s|ed|ing)?|led|leading|headed|managed|spearheaded|set|defined|decided|chose|choose|ran|run|"
    r"accountable for|took charge of|in charge of)\b",
    re.I,
)
_COLLAB = re.compile(r"\b(worked with|working with|supported|supporting|partnered|partnering|collaborated|collaborating)\b", re.I)
_OTHER_SUBJECT = {"designer", "manager", "founder", "director", "lead", "she", "he", "they", "who", "not"}
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
# "a quarter" in "sequencing a quarter" is a time period. Only "by a quarter" / "a quarter of" is a fraction.
_FRACTION = re.compile(
    r"\b(?:a|one)\s+(?:half|third|fifth|sixth|seventh|eighth)\b"
    r"|\bby\s+(?:a\s+)?quarter\b"
    r"|\ba\s+quarter\s+of\b"
    r"|\bone\s+quarter\b"
    r"|\bby\s+half\b"
    r"|\b\d+(?:\.\d+)?x\b",
    re.I,
)
_GENERIC_STEP = re.compile(r"\bin about two weeks,\s+write\b|\bin two weeks,\s+write\b", re.I)
_PAST_OWNER = re.compile(
    r"\byou\s+(?:owned|led|headed|chose|decided|set|defined|ran|managed|drove)\b"
    r"|^(?:Owned|Led|Headed|Chose|Decided|Defined|Ran|Managed)\b",
    re.I,
)
_OTHER_ACTOR = {"designer", "manager", "founder", "director", "lead", "engineer", "analyst", "she", "he"}
_ADVICE_NUM = re.compile(
    r"\b\d+\s*[-–]?\s*(?:minute|min|hour|hr|day|week|month)s?\b"
    r"|\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:minute|hour|day|week|month)s?\b"
    r"|\b(?:[1-9]|10)\s*[-–]\s*(?:[1-9]|10)\s*(?:items?|steps?|pages?|slides?|bullets?|things?|ideas?|options?|bets?)\b"
    r"|\b(?:[1-9]|10)\s*(?:items?|steps?|pages?|slides?|bullets?|things?|ideas?|options?|bets?)\b",
    re.I,
)
_ADVICE_WORDS = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:minute|minutes|hour|hours|day|days|week|weeks|month|months)\b",
    re.I,
)
# Base form only, so "Ran the roadmap" is still a claim. A leading "On your next workshop, ask to…" counts.
_ADVICE_LEAD = re.compile(
    r"^(?:(?:In|Over|Within|During|After|On|in|over|within|during|after|on)\b[^,]{0,80},\s*"
    r"|(?:This|Next|this|next)\s+(?:week|month|quarter),?\s+)?"
    r"(?:Ask to|ask to|Ask|ask|Propose|propose|Volunteer|volunteer|Plan to|plan to|"
    r"Write|write|Draft|draft|Prepare|prepare|Build|build|Rewrite|rewrite|Start|start|"
    r"Make|make|Sit|sit|Talk|talk|Share|share|Map|map|Pick|pick|Offer|offer|Bring|bring|"
    r"Turn|turn|Outline|outline|Sketch|sketch|List|list|Spend|spend|Block|block|Book|book|"
    r"Schedule|schedule|Run|run)\b"
)
_CLAIM_AS_FACT = re.compile(
    r"\b(?:tell\b.{0,40}\byou\s+(?:owned|led|headed)|say you|claim you|you already own|you owned|you led|"
    r"you headed|keep owning)\b",
    re.I,
)
_LEAK = re.compile(
    r"\b(?:you are not expected to|not claiming|do not claim|don't claim|treat that as a request|without claiming|without saying)\b",
    re.I,
)
_STOP = {"the", "a", "an", "and", "for", "with", "your", "that", "this", "from", "into", "its", "their", "our",
         "you", "already", "have", "has", "been", "was", "were", "not", "end", "than", "then", "what", "when"}
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
    """Take evidence ids out of a sentence before any number check, and before the user sees it.

    Ids are matched on word boundaries, so a one-letter id cannot eat the letter inside a word. A very short id
    (under two characters) is left alone.
    """
    out = text or ""
    for i in known_ids:
        token = str(i or "")
        if len(token) < 2:
            continue
        out = re.sub(rf"\b{re.escape(token)}\b", " ", out, flags=re.I)
    out = _EVAL_ID.sub(" ", out)
    out = _UUID.sub(" ", out)
    return _tidy(out)


def _tidy(text: str) -> str:
    """Close the gap an id leaves behind, including a stranded colon ("See : …")."""
    out = text or ""
    out = re.sub(r"\s+([,.;!?])", r"\1", out)
    out = re.sub(r"\s*:\s*", ": ", out)
    out = re.sub(r"\bSee:\s*", "", out, flags=re.I)
    out = re.sub(r":\s*(?=[,.;!?])", "", out)
    out = re.sub(r"\s{2,}", " ", out)
    return " ".join(out.split())


def _strip_leaks(text: str) -> str:
    """Drop a sentence or a clause that repeats a safety instruction to the reader."""
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+", text or ""):
        if not sentence.strip():
            continue
        if not _LEAK.search(sentence):
            kept.append(sentence.strip())
            continue
        clauses = [c.strip() for c in re.split(r",\s*|;\s*", sentence) if c.strip() and not _LEAK.search(c)]
        if clauses:
            kept.append(", ".join(clauses))
    return " ".join(kept)


def _scrub(text: str, known_ids: Iterable[str]) -> str:
    out = remove_ids(text, known_ids)
    for rx, repl in _JARGON:
        out = rx.sub(repl, out)
    return _strip_leaks(" ".join(out.split()))


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
        # "top ten themes" is the same figure as "top ten ticket themes". The number word is what was cited.
        number = phrase.split()[0].lower()
        if number not in blob and phrase.lower() not in blob:
            return True
    for phrase in _FRACTION.findall(text):
        if phrase.lower() not in blob:
            return True
    return False


def _clauses(text: str) -> List[str]:
    return [part.strip() for part in re.split(r"[.;!?]+", text or "") if part.strip()]


def _stem(word: str) -> str:
    word = word.lower()
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _focus(text: str) -> set:
    """The thing an ownership verb is about: words after the verb, with the small ones left out."""
    clean = (text or "").replace("'", "").replace("'", "")
    match = _OWNER_VERB.search(clean)
    if not match:
        return set()
    words = re.findall(r"[a-z]{3,}", clean[match.end():].lower())
    return {_stem(word) for word in words if word not in _STOP}


def _overlaps(left: set, right: set) -> bool:
    return bool(left and right and left & right)


def _claims_ownership(text: str) -> bool:
    """True when the user is said to own the work. 'The designer owns the layout' is not that."""
    user_verb = False
    for match in _HARD_OWNER.finditer(text or ""):
        prefix = (text or "")[:match.start()].lower().split()
        if prefix and prefix[-1] in _OTHER_ACTOR:
            continue
        user_verb = True
        break
    if user_verb:
        return True
    if re.search(r"\bto\s+(?:choose|decide|set|define|run)\b", text or "", re.I) and not _SOFT_OWNER.search(text or ""):
        return False
    return bool(_SOFT_OWNER.search(text or ""))


def _skips_negation(text: str, kind: str) -> bool:
    """Future advice is not a claim that they already did the thing the resume denies."""
    if _PAST_OWNER.search(text or "") or _CLAIM_AS_FACT.search(text or ""):
        return False
    if _genuine_advice(text):
        return True
    return kind == "transferable"


def _genuine_advice(text: str) -> bool:
    """A step that asks them to do something next, not a step that tells them to say they already did it."""
    return bool(_ADVICE_LEAD.match((text or "").strip())) and not bool(_CLAIM_AS_FACT.search(text or ""))


def _user_owns(clause: str) -> bool:
    """True when this clause says the person themselves did the thing. 'The designer owned' does not count."""
    if _DENIED.search(clause or ""):
        return False
    for match in _HARD_OWNER.finditer(clause or ""):
        prefix = (clause or "")[:match.start()].lower().split()
        if prefix and prefix[-1] in _OTHER_SUBJECT:
            continue
        return True
    return False


def _removed_collaborator(claim: str, cited: str) -> bool:
    """True when the claim says they did it alone, and the experience names someone they did it with."""
    if not re.search(r"\b(?:without|single-handedly|on my own|by myself|alone)\b", claim or "", re.I):
        return False
    named = set(re.findall(
        r"\b(?:with|alongside)\s+(?:the\s+)?(design|engineering|product|marketing|sales|support|operations|finance|legal)"
        r"(?:\s+team)?\b", cited or "", re.I))
    if not named:
        return False
    if re.search(r"\b(?:single-handedly|alone|on my own|by myself)\b", claim or "", re.I):
        return True
    for name in named:
        if re.search(rf"\bwithout\b(?:\s+\w+){{0,3}}\s+{name}\b", claim or "", re.I):
            return True
    return False


def collaboration_as_ownership(claim: str, cited: str) -> bool:
    """True when the claim says the person already owned the work, and the experience only collaborates.

    Checked clause by clause. A clause that says they owned the support queue supports that claim even when
    another clause only shows collaboration, or denies a different thing.
    """
    if _genuine_advice(claim):
        return False
    if not _claims_ownership(claim or ""):
        return False
    if re.search(r"\b(?:did not|didn't|do not|don't|never)\b", claim or "", re.I):
        return False
    focus = _focus(claim)
    clauses = _clauses(cited)
    if any(_user_owns(clause) and (not focus or _overlaps(_focus(clause), focus)) for clause in clauses):
        return False
    if _removed_collaborator(claim, cited):
        return True
    return any(_COLLAB.search(clause) for clause in clauses)


def negation_as_positive(claim: str, cited: str) -> bool:
    """True when a clause denies the same thing the claim states as done.

    Denying the roadmap does not block 'you owned the support queue'. A clause that plainly supports the claim
    keeps it.
    """
    if _genuine_advice(claim):
        return False
    if not _claims_ownership(claim or ""):
        return False
    if re.search(r"\b(?:did not|didn't|do not|don't|never)\b", claim or "", re.I):
        return False
    focus = _focus(claim)
    clauses = _clauses(cited)
    if any(_user_owns(clause) and (not focus or _overlaps(_focus(clause), focus)) for clause in clauses):
        return False
    for clause in clauses:
        if not _DENIED.search(clause):
            continue
        denied = _focus(clause)
        if not focus or not denied or _overlaps(denied, focus):
            return True
    return False


def _peer_claim_problem(text: str, ids: Iterable[str]) -> Optional[str]:
    """A peer citation may repeat the group pattern. It may not name a person or promise an outcome."""
    if not any(str(i).startswith("PEER-") for i in ids):
        return None
    if re.search(r"\b(guarantee|guarantees|guaranteed|probability|likely to|will become|you will|percent chance)\b", text or "", re.I):
        return "peer_claim"
    if re.search(r"\b(she|he|one person|this person|named)\b", text or "", re.I):
        return "peer_individual"
    return None


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
    advice = (kind == "step" and _genuine_advice(raw_text)) or (kind == "transferable" and not _PAST_OWNER.search(raw_text))
    if invented_number(remove_ids(raw_text, known), universe, advice=advice):
        return None, "invented_number"
    if raw_why and invented_number(remove_ids(raw_why, known), universe, advice=advice):
        return None, "invented_number"
    peer_reason = _peer_claim_problem(raw_text + " " + raw_why, ids)
    if peer_reason:
        return None, peer_reason
    if ids and not advice and collaboration_as_ownership(raw_text, cited):
        return None, "collaboration_as_ownership"
    if ids and not _skips_negation(raw_text, kind) and negation_as_positive(raw_text, cited):
        return None, "negation"
    if kind in ("strength", "transferable") and _DISCLAIMER.search(raw_text):
        return None, "disclaimer"
    text = _scrub(raw_text, known)
    why = _trim(_scrub(raw_why, known), 30)
    if len(text) < 8:
        return None, "leaked_id"
    return {"text": text, "evidence_ids": ids, "why": why}, None


def _trim(text: str, limit: int) -> str:
    words = (text or "").split()
    if len(words) <= limit:
        return text or ""
    cut = " ".join(words[:limit]).rstrip(",;:")
    if cut and cut[-1] not in ".!?":
        cut += "."
    return cut


def plain_text(raw) -> str:
    """A model's opening may be a string or an object with a text field. Never pass the object through str()."""
    if isinstance(raw, dict):
        return plain_text(raw.get("text"))
    if isinstance(raw, list):
        return " ".join(part for part in (plain_text(item) for item in raw) if part)
    if raw is None:
        return ""
    return str(raw)


def trim_at_sentence(text: str, limit: int) -> str:
    """Keep whole sentences that fit. Do not stop in the middle of a phrase."""
    text = " ".join((text or "").split())
    if len(text.split()) <= limit:
        return text
    kept = []
    count = 0
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        words = len(sentence.split())
        if kept and count + words > limit:
            break
        if not kept and words > limit:
            return ""
        kept.append(sentence)
        count += words
    return " ".join(kept)


def _opening(raw, by_id, lines, reasons) -> str:
    """Keep every sentence that does not invent a figure. One bad sentence does not blank the rest."""
    universe = " ".join(row["text"] for row in lines)
    scrubbed = _scrub(" ".join(plain_text(raw).split()), by_id)
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+", scrubbed):
        sentence = sentence.strip()
        if not sentence:
            continue
        peerish = bool(re.search(r"closest PMs|path like yours", sentence, re.I))
        number_universe = " ".join(row["text"] for row in lines if str(row.get("id", "")).startswith("PEER-")) if peerish else universe
        if invented_number(remove_ids(sentence, by_id), number_universe):
            _bump(reasons, "invented_number")
            continue
        if peerish and _peer_claim_problem(sentence, ["PEER-1"]):
            _bump(reasons, "peer_claim")
            continue
        kept.append(sentence)
    return trim_at_sentence(" ".join(kept), 45)


def _reading_words(reading: dict) -> int:
    parts = [reading.get("opening") or ""]
    for key in ("strengths", "transferable", "gaps", "questions", "steps"):
        for item in reading.get(key) or []:
            parts.append(item.get("text") or "")
            parts.append(item.get("why") or "")
    return len(" ".join(parts).split())


def _has_figure(text: str) -> bool:
    """A digit, a percent, a multiplier, or a count such as six or two. A bare 'one' is not a figure."""
    if _NUM.search(text or "") or _MULTIPLIER.search(text or ""):
        return True
    return bool(re.search(
        r"\b(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty)\b",
        text or "", re.I))


def _measured(item: dict, by_id: Dict[str, dict]) -> bool:
    """A strength that states or cites a figure. The length cap does not remove these."""
    if _has_figure(item.get("text") or "") or _has_figure(item.get("why") or ""):
        return True
    for i in item.get("evidence_ids") or []:
        row = by_id.get(i) or {}
        if _has_figure(row.get("text") or ""):
            return True
    return False


def _limit_reading(reading: dict, reasons: Dict[str, int], by_id: Dict[str, dict]) -> None:
    """About 380 words. Questions first (keep one), then generic steps, then unmeasured strengths.

    A strength that cites a figure stays. Transferable items stay.
    """
    while _reading_words(reading) > 380:
        questions = reading.get("questions") or []
        if len(questions) > 1:
            questions.pop()
            _bump(reasons, "trimmed")
            continue
        steps = reading.get("steps") or []
        generic = [i for i, step in enumerate(steps) if _GENERIC_STEP.search(step.get("text") or "")]
        if generic and len(steps) > 1:
            steps.pop(generic[-1])
            _bump(reasons, "trimmed")
            continue
        strengths = reading.get("strengths") or []
        unmeasured = [i for i, item in enumerate(strengths) if not _measured(item, by_id)]
        if unmeasured and len(strengths) > 1:
            strengths.pop(unmeasured[-1])
            _bump(reasons, "trimmed")
            continue
        break


def _bump(reasons: Dict[str, int], reason: Optional[str]) -> None:
    if reason:
        reasons[reason] = reasons.get(reason, 0) + 1


def apply_guardrails(raw: dict, lines: List[dict]) -> dict:
    """Drop unsupported claims. Counts each drop by reason. User-facing text has ids and audit jargon removed."""
    by_id = {row["id"]: row for row in lines}
    reasons: Dict[str, int] = {}
    strengths = _collect(raw.get("strengths") or raw.get("proves") or [], by_id, "strength", True, reasons)[:5]
    transferable = _collect(raw.get("transferable") or [], by_id, "transferable", True, reasons)[:3]
    gaps = _gaps(raw.get("gaps") or raw.get("missing") or [], by_id, reasons)[:3]
    questions = _questions(raw.get("questions") or [], by_id, reasons)[:3]
    steps = _collect(raw.get("steps") or [], by_id, "step", False, reasons)[:3]
    opening = _opening(raw.get("opening"), by_id, lines, reasons)
    reading = {
        "opening": opening,
        "strengths": strengths,
        "transferable": transferable,
        "gaps": gaps,
        "questions": questions,
        "steps": steps,
    }
    _limit_reading(reading, reasons, by_id)
    return {
        **reading,
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
        out.append({"text": cleaned, "why": _trim(_scrub(why, by_id), 30)})
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
