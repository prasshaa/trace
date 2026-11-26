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


def _from_payload(payload: dict, target_level: str) -> dict:
    """Band the aggregate counts. The payload is counts, never profiles."""
    hidden = ("neighbours", "profile_key", "path_text", "embedding", "norm_city")
    if any(key in payload for key in hidden):
        logger.warning("peer payload included profile fields; they were discarded")
        return {"available": False, "reason": "refused"}
    total = int(payload.get("neighbour_count") or 0)
    if total < 5:
        return {"available": False, "reason": "too_few"}
    return _patterns_from_counts(payload, target_level, total)


def _patterns_from_counts(payload: dict, target_level: str, total: int) -> dict:
    from app.services.peer_matching.aggregate import _of, _takeaways, band_count
    patterns = []

    def add(text):
        if text:
            patterns.append({"id": f"PEER-{len(patterns) + 1}", "text": text})

    add(_of(band_count(int(payload.get("internal") or 0)), total, "moved into a product role from inside the employer."))
    add(_of(band_count(int(payload.get("external") or 0)), total, "moved into a product role from outside the employer."))
    for item in payload.get("priors") or []:
        add(_of(band_count(int(item.get("count") or 0)), total, f"came from {item.get('label')} before a product role."))
    months = payload.get("months") or {}
    if int(months.get("n") or 0) >= 10 and months.get("median") is not None:
        add(f"Among the {total} closest PMs with a dated path, the middle time to a first product role was about {int(round(float(months['median'])))} months.")
        if months.get("low") is not None and months.get("high") is not None:
            low = int(round(float(months["low"]) / 6.0) * 6)
            high = int(round(float(months["high"]) / 6.0) * 6)
            if high > low:
                add(f"Most of those dated paths took about {low} to {high} months.")
    pre = payload.get("pre_pm_roles_median")
    if pre is not None and int(payload.get("neighbour_count") or 0) >= 10:
        add(f"Among the {total} closest PMs, the middle number of roles before a product role was about {int(round(float(pre)))}.")
    add(_of(band_count(int(payload.get("mba") or 0)), total, "had an MBA."))
    add(_of(band_count(int(payload.get("degree") or 0)), total, "had a degree."))
    first = payload.get("first_pm") or {}
    if first.get("label"):
        add(_of(band_count(int(first.get("count") or 0)), total, f"started in product as {first['label']}."))
    add(_of(band_count(int(payload.get("at_target") or 0)), total, f"are at {target_level}."))
    return {
        "available": True,
        "synthetic": bool(payload.get("synthetic")),
        "neighbour_count": total,
        "headline": "PMs with paths like yours",
        "match_line": "Matched by similarity of career path.",
        "disclaimer": "These are patterns across the closest profiles, not a prediction and not a guarantee.",
        "patterns": patterns[:8],
        "takeaways": _takeaways(patterns),
        "note": "Your profile is not in this group. A pattern shared by fewer than 5 people is left out. "
                "A pattern shared by 5 to 9 people is shown as a range.",
    }
