# Trace LLM and retrieval design

**Status: design only. Nothing in this document is implemented.** Under D1 no external AI provider is used, and under
D2 no embeddings are created. A feature described here counts as implemented only once tests establish it.

## Principles

- **The deterministic engine decides; the LLM extracts, explains and converses.** The product works when the provider
  is unavailable.
- **One controlled orchestrator, no multi-agent architecture.**
- **Provider-neutral interfaces with a fake provider in tests.** The provider is chosen under D1.
- **Retrieved content and documents are evidence, never instructions.** Instructions embedded in resumes, job
  descriptions or retrieved text are ignored, and this is tested.
- **No invented citations, courses, prices, durations or placement claims.** Sourced statements link to real, current
  sources; generated guidance is labelled `trace_generated`.

## Runtime prompts (Phase 6 onwards)

Prompts are versioned files in `backend/app/prompts/`, one per purpose:

| Prompt | Purpose |
|---|---|
| `resume_extraction` | Factual extraction into the existing draft contract (`resume_extraction/contract.py`) |
| `advisor` | Conversation through approved tools |
| `recommendation_explanation` | Wording for an already computed result (optional) |
| `knowledge_ingestion` | Structuring curated sources (optional) |

`CLAUDE.md` is development context for Claude Code, never runtime memory, and is not a prompt.

### Extraction prompt requirements

- Extract only facts: employment, original titles, employers, dates, responsibilities, achievements, explicitly
  mentioned skills, qualifications and fields, certifications, projects, and industry where stated.
- Keep evidence quotes and page or section references.
- Return null or unknown for anything missing; never invent dates, metrics or achievements.
- Never infer ownership of product decisions from working alongside engineers.
- A missing skill is not a negative finding.
- Flag uncertain role mappings for review.

**Output and storage:**
- The output follows a strict schema and is validated server-side.
- Each run stores the provider, model and version, the prompt version, the run id, the timestamp, the parse status,
  validation results and source references.
- Output becomes **drafts or proposals**, never confirmed facts.

## Advisor (Phase 8)

**Tools:** the advisor works only through backend tools that reuse existing services:
- `get_profile`, `get_target`;
- `update_preferences`;
- `run_analysis`;
- `retrieve_route_evidence`, `retrieve_role_requirements`;
- `search_knowledge_base`;
- `explain_recommendation`;
- `propose_profile_update`, which writes a Phase 2 proposal.

There is no free database access. Confirming a proposal is a user action in the interface, never a model tool.

**Memory:**
- profile, preferences, runs, threads and messages are stored in Postgres (`trace_agent_threads`,
  `trace_agent_messages` exist);
- each request builds a temporary context from what is stored.

## Retrieval (Phase 7)

| Use SQL for | Use full-text, trigram or (later) vector retrieval for |
|---|---|
| Exact transitions, profile counts, cohort filters, role sequences, qualifications, dates, privacy minimums | Similar responsibility text, target-role requirements, curated learning and career guidance |

Vector similarity never replaces route counting or privacy rules, and an LLM never counts supporting profiles.

**Knowledge base:**
- curated sources only, each with:
  - source id, title, URL, type, publisher;
  - geography, role and seniority;
  - retrieval and publication dates;
  - verification status and revalidation date;
  - permitted uses and content version;
- content is split into meaningful sections;
- the existing `trace_target_role_sources` (HTTPS-only URLs, `source_status`, `valid_until`) is the starting point;
- nothing unverified is treated as authoritative;
- openings and courses are shown as current only when they have been checked recently.

**Embedding lifecycle (once allowed):**
- recompute embeddings when the source changes, and record the model and version;
- supersede vectors that are obsolete;
- exclude withdrawn or ineligible profiles immediately;
- delete with the user's profile;
- never embed direct personal information;
- keep separate spaces for private, real-reference and synthetic data, filtered before retrieval.
