"""Hybrid retrieval of the owner's own experience lines for one query (semantic-retrieval Phase 3). Read-only for the
caller's data apart from refreshing their own embeddings (idempotent, D53).

    HybridRetriever(conn, settings).retrieve(ctx, family) -> Retrieval      (family: the Query objects of one kind,
                                                                             e.g. the 20 responsibility areas)

Mode "hybrid" (TRACE_SEMANTIC_RETRIEVAL=1 and TRACE_EMBEDDINGS=1): the database returns the caller's ELIGIBLE records
only (reviewed; evidence not rejected; owner-scoped by the request identity) with their provenance and, per query, the
lexical and semantic RANKS both ways (trace_semantic.hybrid_candidates, v2.35.0). The structured leg is the user's own
current categorisation, else Trace's deterministic reading. fusion.mutual_leg() and fusion.fuse() combine them by rank.

Mode "reader" (the default, and the fallback on any failure: flag off, embeddings off, model unavailable, database
function missing or failing): Trace's deterministic reader alone over the reviewed lines of the loaded profile, exactly
as the capability assessment reads them today.

The result is an ordered list of candidate lines with exact provenance (record id, parent employment or activity, review
status, which legs support it and at what rank). It carries no similarity value and no score, and is not a verdict:
whether a line is evidence of a capability stays with the deterministic assessment. Nothing in the product calls this yet.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple
from uuid import UUID

import psycopg

from app.services.candidate_profile import CandidateProfileService, OwnerContext
from app.services.candidate_profile.repository import owner_session
from app.services.embeddings.index import EmbeddingIndex
from app.services.embeddings.provider import EmbeddingProvider, EmbeddingUnavailable, provider_from
from app.services.retrieval.fusion import HYBRID, FusionConfig, dense_ranks, fuse, mutual_leg, structured_grade

logger = logging.getLogger(__name__)
REVIEWED = ("user_confirmed", "user_corrected")


@dataclass(frozen=True)
class Query:
    key: str                                   # an area code or a requirement key
    text: str                                  # the knowledge text (label, description, examples)
    direct: FrozenSet[str]                     # the area itself, or the requirement's direct areas
    transferable: FrozenSet[str] = frozenset()


@dataclass
class Line:
    """One eligible line as the legs see it."""
    source_kind: str                           # evidence | activity
    source_id: str
    text: str
    candidate_employment_id: Optional[str] = None
    candidate_activity_id: Optional[str] = None
    review_status: Optional[str] = None
    user_areas: Optional[Sequence[str]] = None   # the user's CURRENT categorisation ([] = "None of these areas")
    evidence_type: Optional[str] = None
    # per query key: {"lr", "lq", "sr", "sq"}: the line's rank within the query and the query's rank within the line's
    # family, lexical (l) and semantic (s). Ranks only.
    ranks: Dict[str, Dict[str, int]] = field(default_factory=dict)


@dataclass
class Candidate:
    order: int
    source_kind: str
    source_id: str
    excerpt: str
    candidate_employment_id: Optional[str]
    candidate_activity_id: Optional[str]
    review_status: Optional[str]
    legs: Dict[str, int]                       # leg -> rank (1 = best); no similarity, no score
    read_by: Optional[str] = None              # structured leg: "you" (the user's categorisation) or "trace"


@dataclass
class Retrieval:
    mode: str                                  # hybrid | reader
    reason: Optional[str]                      # why the reader was used (None in hybrid mode)
    version: str
    results: Dict[str, List[Candidate]] = field(default_factory=dict)     # query key -> candidates, fused order
    index: Optional[dict] = None               # the embedding refresh status (hybrid mode)


def structured_leg(lines: Sequence[Line], q: Query) -> Tuple[List[Tuple[str, int]], Dict[str, str]]:
    """The user's own current categorisation first (always takes precedence, including "None of these areas"), else
    Trace's deterministic reading: clear before partial. Same rules as the capability assessment."""
    from app.services.capability.interpret import interpret
    graded, read_by = [], {}
    for ln in lines:
        if ln.user_areas is not None:
            g, who = structured_grade(set(ln.user_areas), set(), set(q.direct), set(q.transferable)), "you"
        elif ln.source_kind == "evidence":
            r = interpret(ln.text, ln.evidence_type)
            g, who = structured_grade(set(r.clear), set(r.partial), set(q.direct), set(q.transferable)), "trace"
        else:
            continue                                         # an activity title is never read as evidence
        if g:
            graded.append((ln.source_id, float(g)))
            read_by[ln.source_id] = who
    return dense_ranks(graded), read_by


def legs_for(lines: Sequence[Line], q: Query, cfg: FusionConfig, structured=None) -> Tuple[Dict[str, List[Tuple[str, int]]], Dict[str, str]]:
    structured, read_by = structured if structured is not None else structured_leg(lines, q)
    lex = {ln.source_id: (ln.ranks.get(q.key, {}).get("lr"), ln.ranks.get(q.key, {}).get("lq")) for ln in lines}
    sem = {ln.source_id: (ln.ranks.get(q.key, {}).get("sr"), ln.ranks.get(q.key, {}).get("sq")) for ln in lines}
    return ({"structured": structured, "lexical": mutual_leg(lex, cfg.mutual_rank("lexical")),
             "semantic": mutual_leg(sem, cfg.mutual_rank("semantic"))}, read_by)


def _excerpt(text: str, n: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _candidates(lines: Sequence[Line], legs, read_by, cfg: FusionConfig) -> List[Candidate]:
    by_id = {ln.source_id: ln for ln in lines}
    out = []
    for f in fuse(legs, cfg):
        ln = by_id[f.id]
        out.append(Candidate(order=f.order, source_kind=ln.source_kind, source_id=ln.source_id, excerpt=_excerpt(ln.text),
                             candidate_employment_id=ln.candidate_employment_id, candidate_activity_id=ln.candidate_activity_id,
                             review_status=ln.review_status, legs=dict(f.ranks), read_by=read_by.get(f.id)))
    return out


def _s(v) -> Optional[str]:
    return None if v is None else str(v)


_QUERY_VECTORS: Dict[Tuple[str, str, str], List[float]] = {}      # (model, version, knowledge text) -> vector


class HybridRetriever:
    def __init__(self, conn: psycopg.Connection, settings, provider: Optional[EmbeddingProvider] = None,
                 config: FusionConfig = HYBRID):
        self._conn = conn
        self._settings = settings
        self._p = provider if provider is not None else provider_from(settings)
        self._cfg = config

    def _vectors(self, family: Sequence[Query]) -> List[List[float]]:
        """Knowledge texts are fixed, so their vectors are computed once per process (never candidate text)."""
        missing = [q.text for q in family if (self._p.model, self._p.version, q.text) not in _QUERY_VECTORS]
        if missing:
            for t, v in zip(missing, self._p.embed(missing)):
                _QUERY_VECTORS[(self._p.model, self._p.version, t)] = v
        return [_QUERY_VECTORS[(self._p.model, self._p.version, q.text)] for q in family]

    def retrieve(self, ctx: OwnerContext, family: Sequence[Query]) -> Retrieval:
        if len({q.key for q in family}) != len(family) or not 0 < len(family) <= 64:
            raise ValueError("a query family has 1-64 queries with distinct keys")
        if not self._settings.trace_semantic_retrieval:
            return self._reader(ctx, family, "flag_off")
        if self._p is None:
            return self._reader(ctx, family, "embeddings_off")
        try:
            index = EmbeddingIndex(self._conn, self._p).index_profile(ctx, budget_seconds=5.0)
            if index.get("status") in ("unavailable", "failed"):
                return self._reader(ctx, family, f"index_{index['status']}")
            queries = [{"key": q.key, "text": q.text, "embedding": v} for q, v in zip(family, self._vectors(family))]
            with owner_session(self._conn, ctx.owner_user_id) as repo:
                rows = repo._rows("select * from trace_semantic.hybrid_candidates(%(p)s, %(q)s::jsonb, %(m)s, %(v)s)",
                                  {"p": ctx.candidate_profile_id, "q": json.dumps(queries), "m": self._p.model,
                                   "v": self._p.version})
        except EmbeddingUnavailable as exc:
            logger.warning("hybrid retrieval fell back: %s", type(exc).__name__)
            return self._reader(ctx, family, "embeddings_unavailable")
        except psycopg.Error as exc:
            logger.warning("hybrid retrieval fell back: %s", type(exc).__name__)
            return self._reader(ctx, family, "database_error")
        lines = [Line(source_kind=r["source_kind"], source_id=str(r["source_id"]), text=r["content"],
                      candidate_employment_id=_s(r["candidate_employment_id"]), candidate_activity_id=_s(r["candidate_activity_id"]),
                      review_status=r["review_status"],
                      user_areas=(r["user_areas"] or []) if (r["user_areas"] is not None or r["user_none"]) else None,
                      ranks=r["ranks"] or {}) for r in rows]
        results = {}
        for q in family:
            legs, read_by = legs_for(lines, q, self._cfg)
            results[q.key] = _candidates(lines, legs, read_by, self._cfg)
        return Retrieval(mode="hybrid", reason=None, version=self._cfg.version, results=results, index=index)

    def _reader(self, ctx: OwnerContext, family: Sequence[Query], reason: str) -> Retrieval:
        """Trace's deterministic reader alone, over the reviewed lines of the profile (today's behaviour)."""
        profile = CandidateProfileService(self._conn).get_profile(ctx)
        evidence = [x for e in profile.employment_history for x in e.responsibilities + e.achievements + e.other_evidence]
        evidence += [x for acts in profile.career_activities.values() for a in acts for x in a.achievements + a.other_evidence]
        evidence += list(profile.unattached_evidence)
        lines = []
        for x in evidence:
            if x.provenance.review_status not in REVIEWED:
                continue
            current = x.area_classification is not None and not x.area_classification_is_stale
            lines.append(Line(source_kind="evidence", source_id=str(x.candidate_evidence_id), text=x.evidence_text,
                              candidate_employment_id=_s(x.attached_id) if x.attached_to == "employment" else None,
                              candidate_activity_id=_s(x.attached_id) if x.attached_to == "activity" else None,
                              review_status=x.provenance.review_status, evidence_type=x.evidence_type,
                              user_areas=list(x.area_classification) if current else None))
        reader_only = FusionConfig(version="reader", max_results=self._cfg.max_results)
        results = {}
        for q in family:
            structured, read_by = structured_leg(lines, q)
            results[q.key] = _candidates(lines, {"structured": structured}, read_by, reader_only)
        return Retrieval(mode="reader", reason=reason, version="trace-reading-1.0", results=results)
