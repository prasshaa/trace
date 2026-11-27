"""Which confirmed lines the model is allowed to see.

Eight lines or fewer are all passed through. Longer profiles are ranked with local gte-small embeddings when the
model files are present, and with pgvector hybrid retrieval when TRACE_SEMANTIC_RETRIEVAL=1 and a database connection
is available. A negation and a measured result are always kept, even if ranking missed them.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from app.config import get_settings
from app.services.embeddings.provider import EmbeddingUnavailable, GteSmallOnnx

logger = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z0-9%]+")
_NEGATION = re.compile(
    r"\b(?:did not|didn't|do not|don't|never|without|have not|has not|not yet|have never)\b",
    re.I,
)
_MEASURED = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|percent|users?|hours?|points?|merchants?)"
    r"|\d+\s*(?:%|to)\s*\d+"
    r"|\b(?:doubled|tripled|halved)\b"
    r"|\b(?:eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty)\s+\w+",
    re.I,
)


def must_keep(text: str) -> bool:
    """A denial or a measured result. Ranking is not allowed to drop these."""
    return bool(_NEGATION.search(text or "") or _MEASURED.search(text or ""))


def _tokens(text: str) -> set:
    return set(_WORD.findall((text or "").lower()))


def _overlap(lines: Sequence[dict], requirements: Sequence[dict]) -> List[str]:
    """Up to two lines per requirement, skipping lines an earlier requirement already took.
    The same high-overlap line must not crowd out the rest of the resume."""
    picked: List[str] = []
    for req in requirements:
        q = _tokens((req.get("title") or "") + " " + (req.get("description") or ""))
        scored = sorted(lines, key=lambda row: (len(q & _tokens(row["text"])), row["id"]), reverse=True)
        added = 0
        for row in scored:
            if row["id"] in picked:
                continue
            picked.append(row["id"])
            added += 1
            if added == 2:
                break
    return picked


def _embed(texts: Sequence[str], model_dir: Path) -> Optional[List[List[float]]]:
    if not (model_dir / "model.onnx").is_file():
        return None
    try:
        return GteSmallOnnx(model_dir, threads=1).embed(list(texts))
    except EmbeddingUnavailable:
        return None
    except Exception as exc:                                              # noqa: BLE001 - never log the text
        logger.warning("in-process embeddings unavailable: %s", type(exc).__name__)
        return None


def _cosine_ids(lines: Sequence[dict], requirements: Sequence[dict], vectors: List[List[float]]) -> List[str]:
    n_req = len(requirements)
    req_vecs, line_vecs = vectors[:n_req], vectors[n_req:]
    scored = []
    for i, line_vec in enumerate(line_vecs):
        best = 0.0
        for req_vec in req_vecs:
            best = max(best, sum(a * b for a, b in zip(line_vec, req_vec)))
        scored.append((best, lines[i]["id"]))
    scored.sort(reverse=True)
    return [i for _, i in scored[:4]]


def _hybrid_ids(conn, ctx, requirements: Sequence[dict]) -> Tuple[List[str], Optional[str]]:
    """pgvector hybrid retrieval. Empty when the flag is off, embeddings are off, or the call fails."""
    settings = get_settings()
    if not settings.trace_semantic_retrieval or conn is None or ctx is None:
        return [], None
    try:
        from app.services.embeddings.provider import provider_from
        from app.services.retrieval.hybrid import HybridRetriever, Query
        family = []
        for req in requirements[:24]:
            key = str(req.get("requirement_key") or req.get("title"))
            family.append(Query(
                key=key,
                text=f"{req.get('title') or key}. {req.get('description') or ''}".strip(),
                direct=frozenset(req.get("direct_areas") or []),
            ))
        if not family:
            return [], None
        # The semantic flag is the switch. The vectors stay on this machine; a missing TRACE_EMBEDDINGS
        # flag does not skip pgvector when the local model is already on disk.
        provider = provider_from(settings)
        model_dir = Path(settings.trace_embedding_model_dir)
        if provider is None and (model_dir / "model.onnx").is_file():
            try:
                provider = GteSmallOnnx(model_dir, threads=1)
            except EmbeddingUnavailable:
                provider = None
        found = HybridRetriever(conn, settings, provider).retrieve(ctx, family)
    except Exception as exc:                                              # noqa: BLE001
        logger.warning("pgvector retrieval unavailable: %s", type(exc).__name__)
        return [], type(exc).__name__
    if found.mode != "hybrid":
        return [], found.reason
    ids: List[str] = []
    for rows in (found.results or {}).values():
        for cand in rows:
            if cand.source_id not in ids:
                ids.append(cand.source_id)
    return ids, None


def select_lines(lines: Sequence[dict], requirements: Sequence[dict], conn=None, ctx=None) -> Tuple[List[dict], dict]:
    """(lines the model may cite, retrieval description). The description never includes resume text."""
    rows = list(lines)
    if len(rows) <= 8:
        return rows, {"mode": "all_lines", "reason": "at_most_eight", "ids": [r["id"] for r in rows]}
    by_id = {r["id"]: r for r in rows}
    hybrid_ids, hybrid_reason = _hybrid_ids(conn, ctx, requirements)
    if hybrid_ids:
        mode, reason = "hybrid", None
        ranked = hybrid_ids
    else:
        settings = get_settings()
        texts = [f"{r.get('title') or ''}. {r.get('description') or ''}" for r in requirements] + [r["text"] for r in rows]
        vectors = _embed(texts, Path(settings.trace_embedding_model_dir))
        if vectors is not None and len(vectors) == len(texts):
            mode, reason = "in_process_semantic", hybrid_reason
            ranked = _cosine_ids(rows, requirements, vectors)
        else:
            mode, reason = "in_process_overlap", hybrid_reason or "embeddings_unavailable"
            ranked = _overlap(rows, requirements)
    keep = [r["id"] for r in rows if must_keep(r["text"])]
    ordered: List[str] = []
    for i in keep + list(ranked):
        if i in by_id and i not in ordered:
            ordered.append(i)
    shown = [by_id[i] for i in ordered[:5]]
    return shown, {"mode": mode, "reason": reason, "ids": [r["id"] for r in shown]}
