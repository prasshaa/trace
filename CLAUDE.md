# Trace: development guide for Claude Code

Trace helps professionals reach a target role. It produces a **Career Intelligence Report**: a personalised pathway in
four stages (build foundation, gain experience, become application-ready, get into the role), grounded in the user's
confirmed profile, eligible career histories and verified target-role knowledge.

This file is development context only. It is never the runtime prompt for Trace's own LLM.

## Read first

| Topic | Document |
|---|---|
| Decisions in force | `docs/DECISIONS.md` |
| Phase plan and status | `docs/IMPLEMENTATION_PLAN.md` |
| Vision, principles, journey | `docs/PRODUCT_VISION.md`, `docs/PRODUCT_PRINCIPLES.md`, `docs/USER_JOURNEY.md` |
| Architecture | `docs/SYSTEM_ARCHITECTURE.md` |
| Engine, evidence | `docs/RECOMMENDATION_ENGINE.md`, `docs/EVIDENCE_METHODOLOGY.md` |
| Data, privacy | `docs/DATA_AND_PRIVACY.md`, `data-foundation/docs/ACCESS_CONTROL.md` |
| LLM and retrieval (design only) | `docs/LLM_AND_RAG.md` |
| Frontend | `docs/DESIGN_SYSTEM.md`, `backend/docs/FRONTEND.md` |
| Technical, per package | `backend/docs/*.md`, `data-foundation/docs/*.md` |

## Layout

- `backend/`:
  - `app/`: FastAPI, with `api/` (thin routes) and `services/` (logic);
  - `web/`: vanilla JS frontend;
  - `evaluation/`: offline harness and shadow prototype, **never imported by `app/`**;
  - `tests/`.
- `data-foundation/`: Supabase migrations (additive, numbered), pgTAP tests, rollback scripts, seeds, `CHANGELOG.md`,
  `manifest.json`.

## Commands

```sh
cd backend && .venv/bin/python -m pytest -q                      # backend tests (needs the local Supabase stack)
cd backend && .venv/bin/python -m evaluation.recommendation --shadow   # evaluation and shadow prototype
cd data-foundation && supabase test db --local                   # pgTAP
cd data-foundation && ../backend/.venv/bin/python -m pytest -q   # static SQL and seed tests
```

## Non-negotiable guardrails

1. **Never commit or push** without explicit instruction. Never print secrets.
2. **Never reset or clean the database**, and never delete records to make tests pass. Local Supabase only.
3. **Migrations are additive and non-destructive**, each with a rollback script, pgTAP tests, a CHANGELOG entry and a
   version bump.
4. **Never mix synthetic, private-user and consented-real data.** Never present synthetic data as real evidence. Never
   fabricate resumes, sources, requirements or statistics.
5. **Privacy:** at least 5 distinct eligible profiles behind any shown evidence; check combinations; exclude the
   caller's own contribution; never show small exact counts.
6. **Backend decides, browser renders.** No client-side counts, statistics, labels or ranking.
7. **The deterministic engine works without an LLM.** No external AI calls or embeddings until D1 is approved. The LLM
   never counts, invents routes or confirms profile changes.
8. **Suitability is not evidence breadth;** never treat a larger group as a better recommendation. Production ranking
   changes pass the evaluation gate. The Phase 8.2 relevance prototype stays in shadow mode (D6).
9. **Do not change consent, sharing or retention, or enable real-resume uploads**, without an explicit decision.
10. **No new framework, database or infrastructure** without a demonstrated need. Extend existing structures.
11. **Don't claim a feature works until tests establish it.** Report phases with named tests and results.
