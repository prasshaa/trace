# Retrieval evaluation

Harness: `backend/evaluation/retrieval` (`python -m evaluation.retrieval [--holdout] [--retriever=NAME]`). Offline,
synthetic, never imported by `app/`. Design and metrics are documented in its `__main__.py`.

## Data

| Set | Fingerprint | Cases | Records (eligible) | Use |
|---|---|---|---|---|
| tuning (`tuning.py`) | `3a4b6af453800eee` | 8 | 49 (46) | build and tune freely |
| held-out (`holdout.py`) | `90215641cccecbee` | 8 | 43 (40) | frozen methods only; every run logged below |

All cases are SYNTHETIC (invented people, employers, products and numbers). Categories covered: different wording,
synonyms, implicit vs explicit ownership, seniority, irrelevant keyword overlap, negative statements, courses,
outdated/superseded and conflicting (rejected) records, overlapping roles, sparse resumes, non-PM backgrounds, and
ineligible (pending, rejected, superseded) records.

**Labels are author-proposed** (written 2026-11-23 by the engineer building the retrieval, before any semantic method
existed) and **not yet reviewed by an independent person**. `python -m evaluation.retrieval --export-review review.csv`
produces a review sheet. Until reviewed labels replace them, every number here is "against author-proposed labels".
The tuning set shares the author's phrasing more than the held-out set does; the held-out set is deliberately worded
differently.

Queries: `queries.json` (`retrieval-queries-1.0`), frozen from taxonomy v2 (20 areas: label, description, examples) and
pm-v1.2 (C1–C6, L1–L4: title and description).

## Baseline (2026-11-23)

Current production reader (`keyword`, trace-reading-1.0) and the Postgres full-text leg (`fts`, untuned).

| Set | Retriever | Queries | R@5 | R@5 (any) | MRR | nDCG@5 | FP@5 | Missed | Leak |
|---|---|---|---|---|---|---|---|---|---|
| tuning | keyword, areas | 36 | 0.569 | 0.512 | 0.583 | 0.545 | 0.221 | 18 | 0 |
| tuning | keyword, capabilities | 30 | 0.700 | 0.682 | 0.700 | 0.680 | 0.306 | 12 | 0 |
| tuning | fts, areas | 36 | 0.847 | 0.812 | 0.787 | 0.782 | 0.805 | 6 | 0 |
| tuning | fts, capabilities | 30 | 0.517 | 0.437 | 0.500 | 0.451 | 0.699 | 18 | 0 |
| **held-out** | **keyword, areas** | 25 | **0.180** | 0.147 | 0.200 | 0.171 | 0.439 | 23 | 0 |
| **held-out** | **keyword, capabilities** | 25 | **0.220** | 0.190 | 0.240 | 0.217 | 0.362 | 21 | 0 |
| held-out | fts, areas | 25 | 0.640 | 0.600 | 0.573 | 0.578 | 0.822 | 10 | 0 |
| held-out | fts, capabilities | 25 | 0.240 | 0.297 | 0.210 | 0.236 | 0.794 | 21 | 0 |

P@5 is bounded by the few relevant records per query (most queries have one or two) and is not used as a criterion.

Capability verdicts (C1–C6, production reader on eligible records):

| Set | Found precision | Found recall | Unsupported decision-rights claims | Related read as clear |
|---|---|---|---|---|
| tuning | 0.857 (12/14) | 0.632 (12/19) | **2** | 5 |
| held-out | 0.500 (3/6) | 0.188 (3/16) | 0 | 4 |

Latency (local stack, one fictional profile, 10 runs; `python -m evaluation.retrieval.latency`): capability assessment
27.5 ms mean / 32.7 ms p95; create plan 130.3 / 184.7 ms; plan roadmap 31.9 / 33.6 ms. Embedding cost today: none.

Historical reference: the Phase 5 interpreter held-out (a different, per-line task) gave precision 0.73, recall 0.59.

## Phase 2: semantic leg on the tuning set (2026-11-23)

gte-small, cosine over all eligible records, no cutoff yet (labels independently unreviewed):

| Retriever | Group | R@5 | MRR | nDCG@5 | FP@5 |
|---|---|---|---|---|---|
| keyword | areas | 0.569 | 0.583 | 0.545 | 0.221 |
| semantic | areas | 0.972 | 0.813 | 0.833 | 0.905* |
| keyword | capabilities | 0.700 | 0.700 | 0.680 | 0.306 |
| semantic | capabilities | 0.850 | 0.629 | 0.693 | 0.818* |

*No cutoff: every record is ranked, so FP@5 and R@5 are inflated by the small cases (~6 eligible records each). MRR and
nDCG are the informative numbers. The held-out set was NOT used.

## Phase 3: hybrid configuration protocol (written 2026-11-23, before the search ran)

Search on the TUNING set only (`python -m evaluation.retrieval.tune_hybrid`; it never imports the held-out set).
Reciprocal Rank Fusion k = 60 (design constant); lexical and semantic weights 1. Searched: structured weight {1, 2};
lexical and semantic depth {3, 5, 10}; single-leg cutoff lexical {0, 1, 2}, semantic {0, 1, 2, 3}; max results {3, 5}
(432 configurations). Cutoffs are RANKS, never similarity values.

Selection: (1) leak 0 and FP@5 ≤ 0.30 in both query groups; (2) highest mean nDCG@5; (3) within 0.005, higher mean
R@5, then the more conservative configuration. The chosen configuration is frozen in `app/services/retrieval/fusion.py`
(`HYBRID`, version `hybrid-1.0`) BEFORE the single held-out run.

**Two changes after the first search, recorded as they happened (tuning set only; the held-out set was not opened):**

1. With rank-only fusion (depth and single-leg cutoffs), no configuration reached FP@5 ≤ 0.30; the lowest was 0.76.
   Cause: ranking always returns something, and most (case, area) pairs have no relevant record, so every result
   there is a false positive. Fix, still rank-based: **mutual rank**. A line takes part in the lexical or semantic
   leg for query q only if q is among that line's m best queries of the same family. The search space became
   mutual rank {0–3} per leg (0 = leg off), single-leg cutoff {0, 1, 2, unlimited}, structured weight {1, 2},
   max results {3, 5}: 1024 configurations.
2. The FP@5 ≤ 0.30 bar was infeasible by construction: the existing reader itself scores 0.306 on capabilities. It was
   amended to **≤ 0.35**, the lowest bar any configuration met, before any configuration was chosen.

### Phase 3 tuning results (tuning set; labels independently unreviewed)

164 of 1024 configurations met the amended bar. Selected and frozen: **`hybrid-1.0`** = structured leg always (the user's
own categorisation, else Trace's reader); a lexical or semantic line only when BOTH legs rank this query as the line's
best of its family (mutual rank 1, no single-leg support); RRF k = 60, equal weights; at most 3 results.

| Tuning set | Areas R@5 | nDCG@5 | FP@5 | Decision-rights FP | Capabilities R@5 | nDCG@5 | FP@5 |
|---|---|---|---|---|---|---|---|
| Existing reader (keyword) | 0.569 | 0.545 | 0.221 | 3 | 0.700 | 0.680 | 0.306 |
| Reader + semantic (mutual 1) | 0.750 | 0.712 | 0.381 | 4 | 0.767 | 0.721 | 0.440 |
| Reader + lexical (mutual 1) | 0.819 | 0.772 | 0.544 | 8 | 0.833 | 0.760 | 0.517 |
| **hybrid-1.0** | **0.736** | **0.725** | **0.296** | 4 | **0.767** | **0.742** | **0.337** |

The two legs agreeing is what keeps false positives near the reader's level; either leg alone adds recall and noise.

## Success criteria (pre-registered 2026-11-23, before any semantic method exists)

A semantic or hybrid configuration may be proposed for production only if, on the **held-out** set, with its
configuration frozen beforehand:

| Criterion | Baseline (keyword) | Required |
|---|---|---|
| R@5, areas and capabilities | 0.180 / 0.220 | ≥ 0.55 each |
| nDCG@5, areas and capabilities | 0.171 / 0.217 | ≥ 0.50 each |
| FP@5 | 0.439 / 0.362 | ≤ 0.45 each |
| Capability "found" recall (full pipeline incl. judge) | 0.188 | ≥ 0.50 |
| Capability "found" precision | 0.500 | ≥ 0.80 |
| Unsupported decision-rights claims (tuning and held-out) | 2 / 0 | **0 and 0** |
| Related read as clear | 4 | ≤ 4 |
| Ineligible leaks; cross-user leaks (DB tests) | 0 | 0 and 0 |
| Added latency to create plan, local p95 | — | ≤ 150 ms |
| Embedding a 60-line profile on the production VM | — | ≤ 2 s; no external call |

The held-out set is small (25 scored queries per group, 16 capability truths): differences under ~0.1 are not
meaningful. Production rollout (Phase 7) additionally needs reviewed labels and a fresh held-out set written by
someone other than the author.

## Held-out run log

| Date | Method (version) | Set fingerprint | Result |
|---|---|---|---|
| 2026-11-23 | keyword (trace-reading-1.0), fts (untuned) | `90215641cccecbee` | Baseline above |
| 2026-11-23 | semantic (gte-small, no cutoff), **hybrid-1.0** (frozen before the run; one run) | `90215641cccecbee` | Below. **Criteria not met** |

### Held-out result, Phase 3 (2026-11-23; labels independently unreviewed)

| Held-out | Areas R@5 | MRR | nDCG@5 | FP@5 | Decision-rights FP | Capabilities R@5 | MRR | nDCG@5 | FP@5 |
|---|---|---|---|---|---|---|---|---|---|
| Existing reader (keyword) | 0.180 | 0.200 | 0.171 | 0.439 | 0 | 0.220 | 0.240 | 0.217 | 0.362 |
| fts | 0.640 | 0.573 | 0.578 | 0.822 | 30 | 0.240 | 0.210 | 0.236 | 0.794 |
| semantic (ranks all, no cutoff) | 0.940 | 0.705 | 0.752 | 0.905 | 106 | 1.000 | 0.606 | 0.720 | 0.831 |
| **hybrid-1.0** | **0.400** | 0.440 | **0.378** | **0.495** | 3 | **0.260** | 0.280 | **0.257** | **0.525** |
| Criterion | ≥ 0.55 | | ≥ 0.50 | ≤ 0.45 | | ≥ 0.55 | | ≥ 0.50 | ≤ 0.45 |

Reading: hybrid-1.0 roughly doubles the reader's area recall and ranking quality on differently worded text, but it
**fails every retrieval criterion** and returns more false positives than the reader (areas 0.495 vs 0.439,
capabilities 0.525 vs 0.362; 3 decision-rights false positives vs 0). The tuning-to-held-out drop (area nDCG
0.725 → 0.378) shows the configuration is fitted to the tuning set's wording: the lexical agreement it requires
rarely happens when the wording differs, which is exactly what the held-out set tests. Semantic ranking itself
transfers well (held-out MRR 0.705 vs the reader's 0.200); what doesn't yet work is deciding when to return NOTHING.
This held-out set has now been used for hybrid-1.0; another configuration evaluated on it would be partly fitted to it.
