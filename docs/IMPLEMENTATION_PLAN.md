# Trace implementation plan (revised 2026-11-15)

This plan follows the decision register (`DECISIONS.md`): deterministic foundation first; LLM, RAG and the advisor
come later, behind provider-neutral interfaces.

Each phase ends with an implementation report covering:
- what was built and what existing code was reused;
- files changed;
- migrations;
- named tests and their results;
- what is incomplete or blocked;
- decisions that need attention.

Nothing is committed or pushed without instruction.

| Phase | Status | Scope | Depends on |
|---|---|---|---|
| 0. Repository audit | **done** (2026-11-15) | Inventory of what is implemented, incomplete, proposed and conflicting | none |
| 1. Product documentation | **done** (2026-11-15) | Root `docs/`, the decision register, this plan, `CLAUDE.md`; superseded notes on older pathway docs | none |
| 2. Profile completion | **done** (2026-11-15) | See below | none |
| 3. Target-role knowledge and capability assessment | **done** (2026-11-16) | Curated PM pack (after source approval, P2). Deterministic assessment of each requirement as Demonstrated, Transferable, Gap or Unknown, with evidence links. A curated transferability map. Read-time qualification semantics (P1). | D3, P1, P2 |
| 3.1 Taxonomy v2 and classification | **done** (2026-11-16) | Four new areas, scope questions (conditions for C4, L1, L2, L4), versioned classifications, "None of these areas", pack pm-v1.1, the classification picker with per-job progress, optional categorisation during entry and proposal acceptance | Phase 3.1 approval |
| 4. Engine evolution | **remediated after review (D31–D38), awaiting acceptance** (2026-11-16; `backend/docs/TRANSITION_PLAN.md`) | Pathway composer: observed routes, or profile-led routes when evidence is thin, plus four stages and Build/Demonstrate/Reposition actions. A transparent suitability rule set kept separate from evidence breadth. The backend evidence-breadth presentation layer (D4). A provenance category on every item. Runs saved to the existing `trace_pathway_*` tables. Production ranking changes gated by the evaluation harness. | 3, D4, D6 |
| 5. Career Intelligence Report | **implemented, awaiting review** (2026-11-17; `backend/docs/FRONTEND.md`, `backend/docs/TRANSITION_PLAN.md`) | `web/js/views/report.js` at `#/pathway/<role id>`, from the saved transition plan. Server summary projection (`summary` on the plan response; no new decision logic, no migration). Above-the-fold summary with all four stage previews. Routes, four stages, capability, employer criteria, career histories and method as separate sections. Senior PM portfolio-scope question. Fictional test data labelled everywhere; D4 breadth labels, never counts. My career path and Career insights retired from navigation (their links open the report); the client-side "most common" pick removed. | 4 |
| 5.1 POC iteration | **implemented, awaiting review** (2026-11-17; D42, D43) | Optional target companies (search over researched employers, own unverified choices, per-role persistence, migration v2.29.0); company-specific research for chosen companies only; canonical stage names; data-driven bridge summary; honest comparable-histories state; numbered stage sources; full saved-plan freshness | 5 |
| 5.2 Product redesign: career transition roadmap | **implemented, awaiting review** (2026-11-17; D44) | Roadmap-first report; evidence-backed personal statements; planning goal (timeframe or date, weekly time, user checkpoint dates; migration v2.30.0); compact four stages; employer matching and company selector retired from the flow (preserved) | 5.1 |
| 5.3 Comprehensive redesign: detailed career plan | **implemented, awaiting review** (2026-11-17; D45) | Experience themes; detailed learning and development plans with verified resources (`pm-learning-v1`); sequence and stage guidance; returning users land on their saved plan; no migration | 5.2 |
| 5.4 Career-planning workspace (frontend) | **implemented, awaiting review** (2026-11-18) | Five-section workspace from the reviewed design (Overview, My Roadmap, Learn & Develop, Experience & Reposition, Profile & evidence) on existing endpoints; no backend or schema change | 5.3 |
| 5.5 Career Origins & Transitions (POC) | **internal preview, flag off; hardened (allowlist, provenance split, tables); release gates open** (2026-11-20) | Read-only research-cohort aggregates beside the plan (D46); no migration | 5.4 |
| 5.6 Resume-first onboarding (parser v2) | **implemented locally; uploads off by default; release gates open** (2026-11-20) | One upload, one consolidated review, one confirmation; replace and discard; parser evaluation with fixed thresholds (D47, `docs/RESUME_PARSER_EVALUATION.md`); no migration |
| 5.7 Resume import hardening (parser 2.1, staged drafts) | **implemented locally; uploads off by default; fresh held-out gate FAILED (reported, not tuned)** (2026-11-20) | Staged private drafts for both profile states; unresolved text shown, never dropped; markers only for contributors; no implied retention; proven MIME; local-testing notice v2 (D48, migrations v2.31.0–v2.31.1) |
| 5.8 Review contract and confirmation reliability (parser 2.2) | **implemented locally; uploads off by default; fresh held-out (holdout3) gate PASSED, first blind run** (2026-11-22) | One role contract with field errors; check endpoint; explicit record segmentation; untitled roles kept; prefilled "Add as a role", leave-out; starting-role mapping placed by the server; safe error handling (D49). No migration |
| 5.9 Career planning intelligence (Phase 5) | **implemented locally** (2026-11-22) | Trace reads confirmed lines (deterministic, local; D50) with dev and held-out evaluation; three plain capability states with provenance; readiness facts kept apart; one employment-type choice and overlap-derived concurrency; "I currently work here" only without an end date; education start and completion dates; title suggestions and ten exact aliases; same-role and step-back targets (D51); "where would you like to move" preference; research-led overview A–E with routes by background (artifact 2.1.0); specific next actions. Migration v2.32.0 |
| 5.10 Release controls and public release (D52) | **implemented locally** (2026-11-22); production deployment waits for the `trace-prod` project | Update using a new resume (replace, one transaction), clear my career profile, selectable target roles, landing positioning, production notice, Replit configuration (`docs/DEPLOYMENT.md`). Career Origins stays off in production. |
| 6. LLM extraction interface | not started | Versioned prompts, strict schema, server-side validation, a fake provider in tests. No external calls (D1). | D1 |
| 7. Knowledge retrieval | not started | Ingestion with source metadata; full-text and trigram retrieval; pgvector only after D1/D2 allow it | D2, D3 |
| 8. Advisor | not started | One orchestrator over approved backend tools, using the existing thread and message tables; profile changes go through the Phase 2 proposal pipeline | D1, 4 |
| 9. Product validation | not started | Five product scenarios plus User A (SWE, B2B SaaS) and User B (BA, fintech), all synthetic. Desktop and mobile review. | all |

## Phase 2 scope (profile completion)

| Item | What | Why |
|---|---|---|
| 2.1 | **Manual structured entry API and UI:** employment (with employer, responsibilities, achievements and skills), education, certifications and projects, standalone skills. Built on the existing `CandidateProfileService.add_*`. Records are `user_stated`, `pending_review`; "Save and confirm" is an explicit user action. | The service supports it, but no route or UI exposes it |
| 2.2 | **Draft editing API** (`PATCH`) for the user's own unreviewed drafts. Corrections of reviewed records keep the existing append-only path. | Users must be able to fix what they typed |
| 2.3 | **Optimistic concurrency.** Every record carries a `version` (its `updated_at`). Edits, corrections, review actions and classification accept `expected_version`; the record is compared under a row lock and a stale version returns 409. | Last-write-wins currently loses edits silently |
| 2.4 | **Update proposals:** one owner-scoped table with **one** pipeline for profile changes that the user must accept. Sources: pasted experience text (deterministic parser, D7), a resume uploaded into a non-empty profile, and later voice and the advisor. Each proposal is compared against the profile (new, already present, or a possible update of an existing record). Accepting one creates a draft linked back to the proposal; dismissing it clears the proposed content. Confirmed records are never changed. | A new resume must not silently replace the profile, and the same resume twice must not duplicate it. One proposal path for every future input |
| 2.5 | **Profile version:** a deterministic fingerprint of the reviewed profile, exposed with the records. | Phase 4 runs record which profile they used |
| 2.6 | **Tests:** persistence across sessions and devices (a new token for the same user), ownership isolation, duplicate uploads, edits, version conflicts, proposals | Acceptance criteria from the brief |

Phase 2 makes no change to: consent, sharing, retention, real-upload enablement (stays off), production ranking, or
stored reference data.
