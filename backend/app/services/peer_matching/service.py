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
from app.services.peer_matching.text import near_levels, user_path_text

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
    text, level = user_path_text(profile, target_role_id, target_title)
    provider = _provider(settings)
    if provider is None:
        return {"available": False, "reason": "embeddings_unavailable"}
    try:
        vector = provider.embed([text])[0]
    except EmbeddingUnavailable:
        return {"available": False, "reason": "embeddings_unavailable"}
    near = near_levels(level)
    try:
        with conn.transaction():
            row = conn.execute(
                "select trace_peer_cohort.peer_patterns(%s::extensions.vector, %s, %s, %s, %s)",
                (_vector_literal(vector), level, near, 40, settings.trace_peer_cohort_version),
            ).fetchone()
    except Exception as exc:                                              # noqa: BLE001 - the plan must still render
        logger.warning("peer patterns unavailable: %s", type(exc).__name__)
        return {"available": False, "reason": "unavailable"}
    payload = row[0] if row else None
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        return {"available": False, "reason": "unavailable"}
    return _from_payload(payload, level)


def _stats_from_sql(block: dict) -> dict:
    """The function's count object, with list fields the ranker already understands."""
    months = block.get("months") or {}
    month_values = []
    if int(months.get("n") or 0) >= 10 and months.get("median") is not None:
        # A list of the median, repeated, is enough for the middle value. The span uses low and high.
        n = int(months["n"])
        median = int(round(float(months["median"])))
        low = int(round(float(months["low"]))) if months.get("low") is not None else median
        high = int(round(float(months["high"]))) if months.get("high") is not None else median
        month_values = [low] * (n // 4) + [median] * (n // 2) + [high] * (n - n // 4 - n // 2)
    pre_values = []
    if block.get("pre_n") and int(block["pre_n"]) >= 10 and block.get("pre_median") is not None:
        pre_values = [int(round(float(block["pre_median"])))] * int(block["pre_n"])
    priors = block.get("priors") or {}
    if isinstance(priors, list):
        priors = {item.get("label"): int(item.get("count") or 0) for item in priors if item.get("label")}
    first = block.get("first") or block.get("first_pm") or {}
    if isinstance(first, list):
        first = {item.get("label"): int(item.get("count") or 0) for item in first if item.get("label")}
    if isinstance(first, dict) and "label" in first and "count" in first:
        first = {first["label"]: int(first["count"])}
    total = int(block.get("n") or block.get("neighbour_count") or 0)
    prior_map = {str(k): int(v) for k, v in priors.items()} if isinstance(priors, dict) else {}
    first_map = {str(k): int(v) for k, v in first.items()} if isinstance(first, dict) else {}
    return {
        "n": total,
        "internal": int(block.get("internal") or 0),
        "external": int(block.get("external") or 0),
        "move_known": int(block.get("move_known") or (int(block.get("internal") or 0) + int(block.get("external") or 0))),
        "priors": prior_map,
        "prior_known": int(block.get("prior_known") or sum(prior_map.values())),
        "months": month_values,
        "pre": pre_values,
        "mba": int(block.get("mba") or 0),
        "mba_known": int(block["mba_known"]) if block.get("mba_known") is not None else total,
        "degree": int(block.get("degree") or 0),
        "degree_known": int(block["degree_known"]) if block.get("degree_known") is not None else 0,
        "first": first_map,
        "first_known": int(block["first_known"]) if block.get("first_known") is not None else sum(first_map.values()),
    }


def _from_payload(payload: dict, target_level: str) -> dict:
    """Band the aggregate counts. The payload is counts, never profiles."""
    del target_level
    if any(key in payload for key in ("profile_key", "path_text", "embedding", "norm_city")):
        logger.warning("peer payload included profile fields; they were discarded")
        return {"available": False, "reason": "refused"}
    if isinstance(payload.get("neighbours"), list):
        logger.warning("peer payload included profile rows; they were discarded")
        return {"available": False, "reason": "refused"}
    from app.services.peer_matching.aggregate import present
    if isinstance(payload.get("group"), dict):
        group = _stats_from_sql(payload["group"])
        cohort = _stats_from_sql(payload.get("cohort") or payload["group"])
        view = present(group, cohort)
    else:
        group = _stats_from_sql(payload)
        view = present(group, group)
    if view.get("available"):
        view["synthetic"] = bool(payload.get("synthetic"))
    return view
