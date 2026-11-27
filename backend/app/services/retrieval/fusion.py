"""Hybrid retrieval fusion (semantic-retrieval workstream, Phase 3). Pure functions: no database, no model, no I/O.

Queries come in FAMILIES (the 20 responsibility areas, or the capability requirements). Three legs each RANK the owner's
eligible lines for one query of the family:

  structured  the user's own categorisation of a line (always first), else Trace's deterministic reading
              (capability/interpret.py): clear before partial. Same precedence as the capability assessment
  lexical     PostgreSQL full-text search over the line (english configuration), ranked by ts_rank_cd
  semantic    gte-small embeddings (D53), ranked by cosine distance INSIDE the database

Only RANKS reach this module. Raw cosine similarity is never a threshold, a confidence or a score here: gte-small
similarities are compressed (unrelated ~0.79, related ~0.87), so only their order means anything.

Mutual rank (the absence signal). Ranking alone always returns SOMETHING, even for an area the person has never worked
in. So a line takes part in the lexical or semantic leg for query q only if q is among that line's `mutual` best queries
of the same family in that leg ("of all 20 areas, this line reads most like q"). Ranks, never similarity values.

Reciprocal Rank Fusion: score(line) = sum over legs of weight / (k + rank). Ties share a rank. The cutoff is
rank-based too: a line supported by ONE leg is kept only if its rank in that leg is within that leg's `alone_max_rank`
(the structured leg is deterministic and always kept); a line two or more legs agree on is kept. At most
`max_results` are returned. The fused order is a retrieval order for a reviewer or a judge, never a readiness score,
never shown as a number and never proof of a capability (the deterministic judge decides, Phase 4).

The frozen configuration (HYBRID) was chosen on the TUNING set only (docs/RETRIEVAL_EVALUATION.md); changing it means a
new version and a new held-out run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

LEGS = ("structured", "lexical", "semantic")


@dataclass(frozen=True)
class FusionConfig:
    version: str
    k: int = 60
    weights: Tuple[Tuple[str, float], ...] = (("structured", 1.0), ("lexical", 1.0), ("semantic", 1.0))
    mutual: Tuple[Tuple[str, int], ...] = (("lexical", 1), ("semantic", 1))          # 0 = the leg is off
    alone_max_rank: Tuple[Tuple[str, int], ...] = (("lexical", 0), ("semantic", 0))  # single-leg support; 0 = never
    max_results: int = 5

    def weight(self, leg: str) -> float:
        return dict(self.weights).get(leg, 1.0)

    def mutual_rank(self, leg: str) -> int:
        return dict(self.mutual).get(leg, 0)

    def alone(self, leg: str) -> Optional[int]:
        return None if leg == "structured" else dict(self.alone_max_rank).get(leg, 0)


# Frozen 2026-11-23 from the tuning-set search (evaluation/retrieval/tune_hybrid.py); see docs/RETRIEVAL_EVALUATION.md.
# Reader leg always; a lexical or semantic line only when BOTH legs rank this query as the line's best of its family.
HYBRID = FusionConfig(version="hybrid-1.0", k=60, weights=(("structured", 1.0), ("lexical", 1.0), ("semantic", 1.0)),
                      mutual=(("lexical", 1), ("semantic", 1)), alone_max_rank=(("lexical", 0), ("semantic", 0)),
                      max_results=3)


@dataclass
class Fused:
    id: str
    ranks: Dict[str, int] = field(default_factory=dict)      # leg -> rank (1 = best) for the legs that support it
    order: int = 0                                            # 1-based position in the fused list


def dense_ranks(scored: Iterable[Tuple[str, float]], higher_is_better: bool = True) -> List[Tuple[str, int]]:
    """[(id, value)] -> [(id, rank)], best first; equal values share a rank (1, 1, 2, ...). Ties are ordered by id."""
    items = sorted(scored, key=lambda x: ((-x[1] if higher_is_better else x[1]), x[0]))
    out, rank, last = [], 0, object()
    for i, v in items:
        if v != last:
            rank, last = rank + 1, v
        out.append((i, rank))
    return out


def structured_grade(areas_clear: Set[str], areas_partial: Set[str], direct: Set[str], transferable: Set[str]) -> int:
    """2 = shows a direct area clearly; 1 = a direct area partially, or a transferable area clearly; 0 = nothing.
    For a responsibility-area query, direct = {area} and transferable = {} (the assessment's own reading rules)."""
    if areas_clear & direct:
        return 2
    return 1 if (areas_partial & direct) or (areas_clear & transferable) else 0


def mutual_leg(ranks: Mapping[str, Tuple[Optional[int], Optional[int]]], m: int) -> List[Tuple[str, int]]:
    """ranks: line id -> (rank of the line within the query, rank of the query within the line's family). Keeps the
    lines whose query rank is within m and re-ranks them (order and ties preserved)."""
    if m <= 0:
        return []
    kept = [(i, float(r)) for i, (r, qr) in ranks.items() if r is not None and qr is not None and qr <= m]
    return dense_ranks(kept, higher_is_better=False)


def fuse(legs: Mapping[str, Sequence[Tuple[str, int]]], cfg: FusionConfig = HYBRID) -> List[Fused]:
    """legs: leg -> [(id, rank)] (ranks from dense_ranks). Returns the kept lines, fused order, at most max_results."""
    support: Dict[str, Dict[str, int]] = {}
    for leg, ranked in legs.items():
        if leg not in LEGS:
            raise ValueError(f"unknown leg {leg}")
        for i, r in ranked:
            support.setdefault(i, {})[leg] = r
    kept = []
    for i, ranks in support.items():
        if len(ranks) == 1:
            (leg, r), = ranks.items()
            limit = cfg.alone(leg)
            if limit is not None and r > limit:
                continue
        score = sum(cfg.weight(leg) / (cfg.k + r) for leg, r in ranks.items())
        kept.append((score, min(ranks.values()), i, ranks))
    kept.sort(key=lambda x: (-x[0], x[1], x[2]))
    return [Fused(id=i, ranks=ranks, order=n + 1) for n, (_, _, i, ranks) in enumerate(kept[: cfg.max_results])]
