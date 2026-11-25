"""Phase 3: choose the hybrid fusion configuration on the TUNING set only. Never reads the held-out set.

    .venv/bin/python -m evaluation.retrieval.tune_hybrid

Search space (k is fixed at 60, the design's Reciprocal Rank Fusion constant; lexical and semantic weights are fixed
at 1 to limit the degrees of freedom on a small set):
  structured weight {1, 2}; mutual rank lexical and semantic {0, 1, 2, 3} (0 = leg off); single-leg cutoff lexical and
  semantic {0, 1, 2, unlimited} (0 = a line that leg alone supports is never kept); max results {3, 5}. 1024 configurations.

Selection rule (docs/RETRIEVAL_EVALUATION.md; amended once, see there):
  1. leak = 0 and FP@5 <= 0.35 for BOTH query groups (areas, capabilities);
  2. highest mean nDCG@5 over the two groups;
  3. ties (within 0.005): higher mean R@5, then the more conservative configuration (smaller single-leg cutoffs, then
     smaller mutual ranks, then fewer results).
"""
import itertools
import sys

from evaluation.retrieval import __main__ as h
from evaluation.retrieval.tuning import T

FP_MAX = 0.35


def configs():
    from app.services.retrieval.fusion import FusionConfig
    for sw, ml, ms, la, sa, mr in itertools.product((1.0, 2.0), (0, 1, 2, 3), (0, 1, 2, 3), (0, 1, 2, 99), (0, 1, 2, 99), (3, 5)):
        yield FusionConfig(version="candidate", k=60, weights=(("structured", sw), ("lexical", 1.0), ("semantic", 1.0)),
                           mutual=(("lexical", ml), ("semantic", ms)), alone_max_rank=(("lexical", la), ("semantic", sa)),
                           max_results=mr)


def main() -> int:
    assert h.fingerprint(T) == "3a4b6af453800eee", "tuning set changed"
    lines = h.leg_ranks(h.fts_retriever(), h.semantic_retriever())
    groups = {"areas": list(h.AREA_TEXT), "capabilities": list(h.CAPS)}
    rows = []
    for cfg in configs():
        ret = h.hybrid_retriever(cfg, lines)
        m = {g: h.evaluate(T, ret, qs) for g, qs in groups.items()}
        ok = all(x["leak"] == 0 and x["FP@5"] <= FP_MAX for x in m.values())
        ndcg = (m["areas"]["nDCG@5"] + m["capabilities"]["nDCG@5"]) / 2
        r5 = (m["areas"]["R@5"] + m["capabilities"]["R@5"]) / 2
        d, a = dict(cfg.mutual), dict(cfg.alone_max_rank)
        conservative = (a["lexical"] + a["semantic"], d["lexical"] + d["semantic"], cfg.max_results)
        rows.append((ok, ndcg, r5, conservative, cfg, m))
    eligible = [r for r in rows if r[0]]
    print(f"{len(rows)} configurations; {len(eligible)} meet leak 0 and FP@5 <= {FP_MAX} in both groups (TUNING set only)")
    best_ndcg = max(r[1] for r in eligible)
    near = [r for r in eligible if r[1] >= best_ndcg - 0.005]
    near.sort(key=lambda r: (-r[2], r[3]))
    for ok, ndcg, r5, cons, cfg, m in near[:8]:
        print(f"  nDCG {ndcg:.3f} R@5 {r5:.3f}  sw={dict(cfg.weights)['structured']} mutual={dict(cfg.mutual)} "
              f"alone={dict(cfg.alone_max_rank)} max={cfg.max_results}  "
              + "  ".join(f"{g}: R@5 {x['R@5']:.3f} MRR {x['MRR']:.3f} nDCG {x['nDCG@5']:.3f} FP@5 {x['FP@5']:.3f}"
                          for g, x in m.items()))
    print("SELECTED:", near[0][4])
    return 0


if __name__ == "__main__":
    sys.exit(main())
