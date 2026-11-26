"""Match a reviewed profile to the peer cohort and return group patterns only.

Off unless TRACE_PEER_MATCHING=1. The cohort query runs as the runtime role through
trace_peer_cohort.peer_patterns, which returns one aggregate payload and no profile row.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import get_settings
from app.services.embeddings.provider import EmbeddingUnavailable, GteSmallOnnx
from app.services.peer_matching.text import user_match_features

logger = logging.getLogger(__name__)


def _provider(settings):
    model_dir = Path(settings.trace_embedding_model_dir)
    if not (model_dir / "model.onnx").is_file():
        return None
    try:
        return GteSmallOnnx(model_dir, threads=1)
    except EmbeddingUnavailable:
        return None


def _vector_literal(values) -> str:
    return "[" + ",".join(f"{float(v):.7f}" for v in values) + "]"


def peer_section(conn, profile, target_role_id: str, target_title: str) -> dict:
    """The plan section. available is false when the flag is off, embeddings are missing, or the group is too small."""
    settings = get_settings()
    if not settings.trace_peer_matching:
        return {"available": False}
    if conn is None:
        return {"available": False, "reason": "no_database"}
    user = user_match_features(profile, target_role_id, target_title)
    provider = _provider(settings)
    if provider is None:
        return {"available": False, "reason": "embeddings_unavailable"}
    try:
        vector = provider.embed([user["text"]])[0]
    except EmbeddingUnavailable:
        return {"available": False, "reason": "embeddings_unavailable"}
    try:
        with conn.transaction():
            row = conn.execute(
                "select trace_peer_cohort.peer_patterns(%s::extensions.vector, %s::jsonb, %s)",
                (_vector_literal(vector), json.dumps({
                    "prior": user.get("prior") or "",
                    "move": user.get("move") or "",
                    "n_pre": user.get("n_pre"),
                    "mba": user.get("mba"),
                    "start_year": user.get("start_year"),
                    "in_pm": bool(user.get("in_pm")),
                    "first_pm_level": user.get("first_pm_level") or "",
                    "target_level": user.get("target_level") or "",
                }), settings.trace_peer_cohort_version),
            ).fetchone()
    except Exception as exc:                                              # noqa: BLE001 - the plan must still render
        logger.warning("peer patterns unavailable: %s", type(exc).__name__)
        return {"available": False, "reason": "unavailable"}
    payload = row[0] if row else None
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        return {"available": False, "reason": "unavailable"}
    return _from_payload(payload, user)


def _from_payload(payload: dict, user: dict) -> dict:
    """The database function already aggregated. A payload that still carries profile rows is refused."""
    del user
    if any(key in payload for key in ("candidates", "profile_key", "path_text", "embedding", "norm_city", "neighbours", "by_first")):
        logger.warning("peer payload included profile fields; they were discarded")
        return {"available": False, "reason": "refused"}
    patterns = payload.get("patterns")
    if not isinstance(patterns, list) or not payload.get("headline"):
        return {"available": False, "reason": "unavailable"}
    clean = []
    for item in patterns:
        if not isinstance(item, dict) or not item.get("text"):
            continue
        if any(key in item for key in ("profile_key", "prior", "level", "embedding")):
            return {"available": False, "reason": "refused"}
        clean.append({"id": item.get("id") or f"PEER-{len(clean) + 1}", "text": item["text"]})
    return {
        "available": bool(payload.get("available", True)) and (bool(clean) or bool(payload.get("preface"))),
        "synthetic": bool(payload.get("synthetic")),
        "neighbour_count": payload.get("neighbour_count"),
        "headline": payload.get("headline"),
        "match_line": payload.get("match_line") or "Matched by similarity of career path.",
        "preface": payload.get("preface") or "",
        "disclaimer": payload.get("disclaimer") or "These are patterns across the closest profiles, not a prediction and not a guarantee.",
        "patterns": clean,
        "takeaways": [line for line in (payload.get("takeaways") or []) if isinstance(line, str)],
        "note": payload.get("note") or "",
    }
