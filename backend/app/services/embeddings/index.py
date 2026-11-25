"""Candidate embedding lifecycle (D53): keep the owner's embeddings in step with their reviewed records.

index_profile embeds only what is missing or out of date for the configured (model, version), in small batches, each
stored in its own short owner transaction (trace_semantic.store_my_embeddings re-checks ownership, eligibility and the
CURRENT text hash, so a line edited mid-way is skipped, never stored stale). It is idempotent: a second run embeds
nothing. Deleted, rejected, unreviewed or replaced records disappear by cascade or are excluded at query time.

Failures never break the profile or the plan: an unavailable model or a database error returns a status and the caller
keeps the deterministic path. Nothing here logs text, only counts and error types.
"""
import json
import logging
import time
from typing import Dict, List, Optional
from uuid import UUID

import psycopg

from app.services.candidate_profile import OwnerContext
from app.services.candidate_profile.repository import owner_session
from app.services.embeddings.provider import EmbeddingProvider, EmbeddingUnavailable

logger = logging.getLogger(__name__)
BATCH = 32


class EmbeddingIndex:
    def __init__(self, conn: psycopg.Connection, provider: Optional[EmbeddingProvider]):
        self._conn = conn
        self._p = provider

    def _sources(self, ctx: OwnerContext) -> List[dict]:
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            return repo._rows("select * from trace_semantic.my_embedding_sources(%(p)s, %(m)s, %(v)s)",
                              {"p": ctx.candidate_profile_id, "m": self._p.model, "v": self._p.version})

    def index_profile(self, ctx: OwnerContext, budget_seconds: float = 20.0) -> Dict[str, object]:
        if self._p is None:
            return {"status": "disabled"}
        started = time.monotonic()
        try:
            sources = self._sources(ctx)
            todo = [s for s in sources if not s["embedded"]]
            stored = 0
            for i in range(0, len(todo), BATCH):
                if time.monotonic() - started > budget_seconds:
                    return {"status": "partial", "sources": len(sources), "stored": stored, "remaining": len(todo) - i}
                batch = todo[i:i + BATCH]
                vectors = self._p.embed([s["content"] for s in batch])
                items = [{"kind": s["source_kind"], "id": str(s["source_id"]), "sha": s["content_sha256"], "embedding": v}
                         for s, v in zip(batch, vectors)]
                with owner_session(self._conn, ctx.owner_user_id) as repo:
                    stored += repo._rows("select trace_semantic.store_my_embeddings(%(p)s, %(m)s, %(v)s, %(i)s::jsonb) as n",
                                         {"p": ctx.candidate_profile_id, "m": self._p.model, "v": self._p.version,
                                          "i": json.dumps(items)})[0]["n"]
            return {"status": "ok", "sources": len(sources), "stored": stored, "already": len(sources) - len(todo),
                    "skipped": len(todo) - stored, "ms": round((time.monotonic() - started) * 1000)}
        except EmbeddingUnavailable as exc:
            logger.warning("embeddings unavailable: %s", type(exc).__name__)
            return {"status": "unavailable"}
        except psycopg.Error as exc:
            logger.warning("embedding index failed: %s", type(exc).__name__)
            return {"status": "failed"}

    def match(self, ctx: OwnerContext, query: str, limit: int = 20) -> Optional[List[dict]]:
        """The owner's eligible, current records most similar to the query text, or None if unavailable."""
        if self._p is None:
            return None
        try:
            q = self._p.embed([query])[0]
            with owner_session(self._conn, ctx.owner_user_id) as repo:
                return repo._rows("select * from trace_semantic.match_my_evidence(%(p)s, %(q)s::real[], %(m)s, %(v)s, %(n)s)",
                                  {"p": ctx.candidate_profile_id, "q": q, "m": self._p.model, "v": self._p.version, "n": limit})
        except (EmbeddingUnavailable, psycopg.Error) as exc:
            logger.warning("embedding match failed: %s", type(exc).__name__)
            return None


def match_knowledge(conn: psycopg.Connection, provider: EmbeddingProvider, owner: UUID, query: str,
                    kinds: Optional[List[str]] = None, limit: int = 20) -> Optional[List[dict]]:
    """Shared role knowledge most similar to the query (read as the runtime's reference reader)."""
    try:
        q = provider.embed([query])[0]
        with owner_session(conn, owner) as repo, repo.as_runtime():
            return repo._rows("select * from trace_semantic.match_knowledge(%(q)s::real[], %(m)s, %(v)s, %(k)s, %(n)s)",
                              {"q": q, "m": provider.model, "v": provider.version, "k": kinds, "n": limit})
    except (EmbeddingUnavailable, psycopg.Error) as exc:
        logger.warning("knowledge match failed: %s", type(exc).__name__)
        return None
