# Trace system architecture

## Components

| Component | Technology | Location |
|---|---|---|
| Database, auth, storage | Supabase (local stack; Postgres with row-level security on every user table) | `data-foundation/supabase/` (additive migrations, pgTAP tests, rollback scripts) |
| Backend API | FastAPI with psycopg (no ORM) | `backend/app/` |
| Frontend | Vanilla HTML, CSS and JS modules, served by the backend; no build step | `backend/web/` |
| Evaluation | Offline harness and the shadow prototype (never imported by `app/`) | `backend/evaluation/` |
| Runtime LLM | **None yet** (D1). Provider-neutral interfaces arrive in Phase 6 | `backend/app/prompts/` (planned) |
| Retrieval | **None yet.** Postgres full-text and `pg_trgm` first; pgvector later (D2) | Postgres |

## Backend layout

```
backend/app/
  api/        thin route handlers (auth, rate limits, response models without internal identifiers)
  services/   business logic
    candidate_profile/   profile read and write, review, corrections, role mapping, pathway result
    career_history/      sequencing of employment episodes
    reference_evidence/  observed routes, privacy filters, own-contribution exclusion
    role_mapping/        title → taxonomy matcher
    resume_extraction/   extraction contract, fixture extractor, local real-resume parser
    deidentification/    identifier detection and de-identification pipeline
    contributions/       consented sharing: build, seal, queue, process
    career_insights/     synthetic-sample distributions (to be retired or re-expressed as evidence labels, Phase 5)
  demo/       synthetic demo fixtures and demo consent
```

The brief's suggested `repositories/` and `models/` folders map to the per-service `repository.py` and `models.py`
modules. They are not reorganised.

## Data domains (never mixed)

| Domain | Schema | Who can read |
|---|---|---|
| Private user profiles | `public.trace_candidate_*` | the owner only (row-level security, owner session) |
| Consented real reference profiles | `trace_ref` (dataset class `consented_real_reference`) | backend read roles; **no browser access**; eligibility through `trace_ref.v_eligible_profiles` |
| Synthetic evaluation profiles | `trace_ref` (dataset class `synthetic_test_fixture`; 41 profiles) | evaluation and demo modes only, always labelled |
| Contribution and identity machinery | `trace_private`, `trace_contrib`, `trace_ingest` | runtime-only functions; administrators |

Database roles are separated at runtime (`trace_app_runtime`, `trace_app_user`, `trace_reference_reader`, `trace_review_writer`, `trace_ingest_writer`). See
`data-foundation/docs/ACCESS_CONTROL.md`.

## Request flow (today)

1. The browser calls `/api/v1/*` with a Supabase bearer token.
2. The backend verifies the token, builds an `OwnerContext` from the verified identity (never from the payload), and
   opens an owner session that sets the request claims.
3. The service applies business rules; the database enforces review rules, ownership and the row-level-security guards
   a second time.
4. The response contains derived values only: no reference profile identifiers, and labels instead of internal codes.

## Responsibilities: deterministic vs LLM

| Deterministic backend (always) | LLM (planned, optional) |
|---|---|
| Profile storage, review and provenance | Resume extraction into the **same** draft contract (Phase 6) |
| Capability assessment against target requirements | Explaining an already computed recommendation |
| Route discovery, counts, privacy minimum, own exclusion | Conversation, through approved tools |
| Suitability, ranking, evidence-breadth labels | Proposing profile updates for the user to confirm |
| Action planning from gaps | Drafting wording, never facts |

The LLM never counts profiles, invents routes, bypasses privacy rules, confirms its own proposals, or queries the
database freely.

## Persistence (the database is the memory)

| What | Where | Status |
|---|---|---|
| Profile records and provenance | `trace_candidate_*` tables | built |
| Correction history | `trace_candidate_corrections` (append-only) | built |
| Consent and sharing | `trace_consent_events`, `trace_private.sharing_*` | built |
| Update proposals | `trace_profile_update_proposals` (v2.14.0) | built (Phase 2) |
| Recommendation runs and evidence | `trace_pathway_runs`, `_recommendations`, `_route_steps`, `_evidence` (exist, unused) | Phase 4 |
| Target-role sources and requirements | `trace_target_role_sources`, `_requirements` (exist, empty) | Phase 3 |
| Conversation threads and messages | `trace_agent_threads`, `trace_agent_messages` (exist, unused) | Phase 8 |
| Original resume bytes | never stored (D5) | policy |
