# Semantic retrieval: architecture audit and plan

Status: **Phases 0–3 complete locally (2026-11-23).** Phase 2 (D53) added pgvector storage, the local gte-small provider
and the embedding lifecycle. Phase 3 added hybrid retrieval behind `TRACE_SEMANTIC_RETRIEVAL` (off); on held-out text it
does NOT meet the pre-registered criteria (docs/RETRIEVAL_EVALUATION.md). Nothing in the product calls it; the capability
assessment and pathways are unchanged. No production migration.

## Phase 0: what exists today (verified from code and databases, 2026-11-23)

| Area | Today | Matching |
|---|---|---|
| Frontend / backend | Vanilla JS (`backend/web`) served by FastAPI; services in `backend/app/services` | — |
| Auth | Supabase Auth; API verifies ES256 tokens via JWKS; every profile call is owner-scoped (`owner_session`, RLS, restricted `trace_app_runtime` login) | — |
| Resume ingestion | Local PDF/DOCX parser (parser 2.1) in an isolated process; de-identification; private draft until confirmed | Heading keywords, date patterns |
| Profile and evidence | `trace_candidate_*` tables (jobs, evidence lines, education, skills, activities, corrections); review status; provenance | — |
| Role mapping | Exact normalised-title alias match (`role_mapping/matcher.py`); `suggest_role` tail-of-title suggestion, never automatic | **Exact / token rules** |
| Skills | `resume_parsing/skills.py` VOCAB + `trace_skill_aliases` | **Exact alias** |
| Capability assessment | `capability/assess.py`: the user's own categorisation first, else Trace's reading (`capability/interpret.py`, trace-reading-1.0): a curated cue lexicon, an action word must govern the responsibility phrase, negation and learning excluded, strict decision rights | **Cue phrases (keyword)** |
| Pathways | Deterministic planner over knowledge packs (`pm-v1.2`, eligibility v3, bridges v2, actions v2) | Structured |
| Career Origins | Frozen aggregate artifact; off in production (`TRACE_CAREER_ORIGINS=0`); artifact not in the public repo | Explicit family→function table |
| Full-text / trigram | **None.** No `tsvector`, `pg_trgm` or fuzzy matching in any migration or service | — |
| pgvector | Available (0.8.2 locally; Supabase hosted) but **not installed** | — |
| Production | Fly.io `trace-career` (nrt), Supabase `trace-prod` (ap-northeast-1), 1 shared-cpu VM, 1 GB | — |

Repository: the working repository (`feat/candidate-career-profile`, 227 uncommitted paths, no remote) is the source of
truth. GitHub `prasshaa/trace` (private) holds one audited snapshot commit of it, identical apart from the excluded
restricted files.

Scale: role knowledge is ~20 responsibility areas, 51 capability requirements, 104 action templates, 17 sources
(about 250 short texts). A candidate has ~10–60 evidence lines. Trace's problem is **matching one person's few dozen
lines against a few hundred knowledge texts**, not searching a large corpus.

### Where semantic retrieval helps (measured, Phase 1)

The production reader finds 57–70% of relevant evidence on the tuning set but **18–22% on the held-out set**, which is
worded differently. Capability verdicts on held-out: precision 0.50, recall 0.19. Different wording is the main failure:
"Listened in on seller support calls…" (discovery), "Sequenced a migration that touched seven teams…" (delivery),
"Took the annual plan to the board…" (executive communication) are all missed. Postgres full-text search alone recalls
more (area R@5 0.64) but is noisy (FP@5 0.82). Both are reasons for a hybrid that **proposes** candidates and a
deterministic judge that **decides**.

### What stays deterministic

Role taxonomy and mapping; current-employment and primary-sequence rules; evidence eligibility (only reviewed, current
records); provenance; the capability judgement (what counts as "evidence found"); decision-rights rules (roadmap,
strategy, outcome ownership need explicit ownership); privacy thresholds; route eligibility; pathway structure;
readiness; actions and saved plans. Similarity is never a readiness score, never shown as a percentage, and never
turns a record into a confirmed fact.

### Proposed design (Phase 2–5)

```
confirmed, eligible evidence lines (owner-scoped)
   ├─ structured filter (status, current/primary rules)          deterministic
   ├─ lexical leg: Postgres FTS (computed per request; tiny scale)
   └─ semantic leg: gte-small embeddings, cosine in pgvector
        → fusion (Reciprocal Rank Fusion, k=60) → candidate (line, area) pairs
        → deterministic judge: action governs object, negation, learning/course, decision rights
             clear  → "Evidence found"            (needs the judge, never similarity alone)
             else   → "Needs more context" + one focused clarification question
        → existing assessment → existing planner (unchanged)
```

Embedding model: **gte-small** (thenlper, MIT, 384 dimensions, English only, 512-token limit, MTEB retrieval 49.46),
the model Supabase supports natively. Two ways to run the same model behind one `EmbeddingProvider` interface:

| Option | For | Against |
|---|---|---|
| **In-process ONNX in the FastAPI app (recommended)** | No network hop or new runtime; text never leaves the app server; $0 per call; deterministic version pinning | +~70 MB model in the image, ~150–250 MB RAM (VM has 1 GB); CPU per line ~5–20 ms |
| Supabase Edge Function (`Supabase.ai.Session('gte-small')`) | Supabase-native; no model in our image | New Deno component; 256 MB / 2 s CPU limits; extra round trip Fly↔Edge; harder to pin model version |

Both are measured in Phase 2 before choosing; indexing and querying always use the same provider, model and version.

Database (additive, reversible, Phase 2):
- `create extension vector` (in `extensions`).
- `trace_private.candidate_evidence_embeddings`: owner, profile, source record (FK, `on delete cascade`), content
  SHA-256, model, model version, `vector(384)`, status, timestamps. No client grant; written and read only through
  security-definer functions acting for the request identity (the v2.29+ pattern). A stale hash is never returned;
  clearing or replacing a profile removes embeddings by cascade.
- `trace_ref.knowledge_embeddings`: corpus `role_knowledge` only (areas, capability requirements, level notes, action
  templates), keyed by knowledge-pack version.
- Research corpus: **not created** until the Career Origins release gates are cleared; then aggregates only.
- Indexes: none at first (exact scan of ≤100 rows per user and ~250 knowledge rows is faster than HNSW, and filtered
  HNSW loses recall). HNSW only if a corpus passes ~10k rows.
- Backfill: batched, idempotent on (source, hash, model), with progress and retries; failures leave the lexical path.

Risks: embeddings of private lines are sensitive derived data (text can be partly reconstructed from vectors), so they
get the same ownership, deletion and logging rules as the text. Latency and memory on the 1 GB VM. Model drift on
upgrade (re-embed everything; never mix versions). English-only model. Author-written labels (see evaluation).

## Phases

| Phase | Content | Gate |
|---|---|---|
| 0 | Audit (this document) | done |
| 1 | Evaluation framework and baseline (`docs/RETRIEVAL_EVALUATION.md`) | done; labels await human review |
| 2 | pgvector, provider abstraction, embeddings tables and lifecycle, backfill (local only) | done (D53) |
| 3 | Hybrid retrieval behind `TRACE_SEMANTIC_RETRIEVAL` (off), reader fallback | done; held-out criteria not met |
| 4 | Judge integration: semantic candidates → deterministic verdicts, clarification questions; fix the negation bug | pre-registered held-out criteria |
| 5 | Plan explanations and next actions use the better evidence; planner logic unchanged | regression suite |
| 6 | Security, isolation (multi-account), deletion propagation, latency, memory, cost | all pass |
| 7 | Internal rollout, then production after a fresh, independently written held-out set | your approval |

## Issues found during the audit (not fixed; outside this phase)

1. Trace's reading treats "Did not own the roadmap; contributed test plans to each roadmap item" as clear roadmap
   ownership (negation ends at the semicolon). An unsupported decision-rights claim. Fix in Phase 4 with a test.
2. The production machine auto-stops: the first request after idle took 8.4 s. `fly.toml` now keeps one machine
   running, but the Fly trial stops any machine after 5 minutes until a card is added.
3. `trace-prod` showed no backups on the dashboard (plan-dependent).
4. The Phase 5 capability evaluation maps C4 to outcome ownership only, while pm-v1.2 lists data analysis as direct too.

## Phase 2 results (local, 2026-11-23)

| Measure | Result |
|---|---|
| Model files | `thenlper/gte-small` @ `17e1f347d17f`, fp32 ONNX 133 MB (not ~70 MB: the official export is 32-bit), checksum-pinned |
| Image (compressed / uncompressed) | 89 → 232 MB / 402 → 926 MB (onnxruntime pulls sympy, 74 MB, unused for inference) |
| Memory with the model loaded (container, 1 CPU, 1 GB) | 79 → 320 MB RSS |
| First load | 0.73 s (lazy, on first use) |
| Embed a 60-line profile | 0.46 s (1 thread, container); 0.23 s with 2 threads (laptop) |
| Embed one query | 4.7 ms |
| Index role knowledge (92 items) | < 3 s |

Measured on an Apple-silicon laptop (arm64); Fly's shared-cpu-1x (x86) will be slower and must be measured before
rollout. Similarities are compressed (unrelated pairs ~0.79, related ~0.87): only ranks are meaningful.

### Production hardware (Fly x86, measured 2026-11-23)

A temporary app (`trace-bench-x86-tmp`, destroyed after the run) ran the Phase 2 image on the production machine shape
(shared-cpu-1x, 1 GB, nrt; AMD EPYC, 1 vCPU). Synthetic text only; no secrets, no database, production untouched.

| Measure | Fly x86 | Laptop container (arm64) |
|---|---|---|
| Image (compressed) | 230 MB | 232 MB |
| Image pull, cold | 12.2 s | — |
| Server ready (2 workers, cold) | 8.6 s | — |
| Server RSS, 2 workers, no model | 227 MB | — |
| Model files read, cold disk | **8.0 s** (0.33 s cached) | — |
| Model load (session) | 1.1 s CPU-bound | 0.73 s |
| Process RSS with model / peak | 221 MB / 246 MB (+206 MB) | 320 MB |
| Embed a 60-line profile | **0.72 s** (cpu/wall 0.99) | 0.46 s |
| Same, two processes at once | 1.42–1.58 s each | — |
| Query embedding (area text) | 22 ms mean, 36 ms p95 | 4.7 ms |
| Short query | 6.7 ms p50 | — |
| Memory available after all of the above | 594 MB of 963 MB | — |

Implications: the ≤ 2 s criterion for a 60-line profile holds even with two workers indexing at once. With two uvicorn
workers each loading the model, steady memory is ~230 + 2 × 210 ≈ 650 MB of 1 GB. The first model load after a machine
boot is dominated by a cold 133 MB disk read (~8 s); if the flag is ever turned on, load the model in the background at
startup rather than on a user's first request. Embedding is fully CPU-bound on a shared vCPU, so sustained indexing
would draw on Fly's shared-CPU burst allowance.

## Phase 3 results (local, 2026-11-23)

Built: `app/services/retrieval/` (`fusion.py`: pure rank fusion, frozen config `hybrid-1.0`; `hybrid.py`:
`HybridRetriever`, flag-gated, reader fallback on any failure); migration v2.35.0
`trace_semantic.hybrid_candidates` (eligible records only, provenance, the user's current categorisation, lexical and
semantic RANKS both ways for one query family; similarity never leaves the database); the evaluation's `hybrid`
retriever runs the same app code on the same ranks (an integration test checks the database ranks equal the
evaluation's).

Design change found on the tuning set: rank fusion alone always returns something, so false positives stayed ≥ 0.76.
**Mutual rank** supplies the missing absence signal: a line counts for a query only if, of all the queries in the family,
that query is among the line's best. `hybrid-1.0` keeps the reader's lines and adds a line only when full-text AND
semantic both rank the query as that line's best.

Held-out (one run): area R@5 0.180 → 0.400, nDCG 0.171 → 0.378; capabilities R@5 0.220 → 0.260; but FP@5 rose (areas
0.439 → 0.495, capabilities 0.362 → 0.525) and 3 decision-rights false positives appeared. **Criteria not met.** The
lexical-agreement requirement transfers poorly to different wording; semantic ranking transfers well (held-out MRR 0.705)
but has no reliable "nothing relevant" signal yet.
