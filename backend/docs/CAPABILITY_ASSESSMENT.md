# Target-role capability assessment (product Phase 3)

**Code:** `app/services/capability/` (`assess.py` pure rules, `service.py`, `repository.py`); API in `app/api/capability_routes.py`.
**Knowledge:** the approved `pm-v1.1` pack (`data-foundation/knowledge_packs/pm-v1.1.json`, migration v2.20.0), which reuses
the `pm-v1` source inventory (v2.17.0). Taxonomy v2 (v2.18.0) and area conditions (v2.19.0).
**Decisions:** D11, D13, D16–D19, D22–D26 (`docs/DECISIONS.md`).

This is **assessment 1 of 3** (D25): target-role capability. The other two are kept apart:
- **Employer-specific application eligibility.** The data is available at `GET /api/v1/knowledge/postings`. It is not
  matched against the user in Phase 3, and it never feeds capability states.
- **Personal transition feasibility.** This is Phase 4.

The assessment is deterministic: there is no external AI and no text interpretation. There is no score, weighting,
readiness percentage or probability.

## States

Only **reviewed** records count (`user_confirmed` or `user_corrected`); pending and rejected records never do.
Responsibility areas come from the user's own classification of their evidence (`candidate_selected`). A stale
classification, where the text changed after classifying, doesn't count.

| State | When | `basis` / `reason` |
|---|---|---|
| **Demonstrated** | Reviewed evidence the user classified with a **direct** area, or a confirmed skill in the **direct** skill set | `classified_evidence` / `confirmed_skill` |
| **Transferable** | No direct support, but a **transferable** area, a transferable skill, or a reviewed role in a transferable role family. The curated rationale is returned | `classified_evidence`, `confirmed_skill`, `role_history` |
| **Gap** | Only if the requirement allows it (`gap_basis = reviewed_classified_evidence`) **and** the profile's relevant information is reviewed: at least one reviewed evidence record, none pending, and every reviewed record currently classified. None of it maps | `reviewed_classified_evidence` |
| **Unknown** | Everything else | `no_reviewed_evidence`, `evidence_pending_review`, `evidence_not_classified`, `classified_before_taxonomy_update`, `scope_question_not_answered`, `not_inferable_from_absence` |
| Not applicable | A context-dependent requirement outside the user's selected product contexts | `not_in_selected_context` |

**Phase 3.1 rules (taxonomy v2):**
- **"None of these areas"** is a real, current classification: the line is covered, maps to nothing, and goes stale
  if its text changes.
- **Conditions.** An area with a condition counts only on lines where the user answered that scope question "yes".
  A reviewed line in the area with the question unanswered gives Unknown (`scope_question_not_answered`), never Gap.
- **Taxonomy versions.** Each classification records the taxonomy version the user saw (earlier ones read as 1). An
  area or scope question introduced in a later version can't be ruled out by an older classification: the result is
  Unknown (`classified_before_taxonomy_update`) until the user reviews those lines. A taxonomy update never creates a
  Gap on its own, and nothing is re-labelled automatically.
- **Blocking records.** Unknown results name the records that stand in the way (pending, unclassified, stale, older
  version or unanswered question), so the user knows exactly what to review.

**Scope rules:**
- Senior PM differentiators (L1–L4) apply only to the Senior PM target (`PM-SPM`).
- The pack covers `PM-PM` and `PM-SPM` only; other targets get 422 `target_not_covered`.

**Product contexts** (the `context` query parameter) are what the user intends to target:
- `commercial_product`;
- `growth_analytics`;
- `platform_api`;
- `data_product`;
- `regulated_domain`;
- `ai_product`.

**Typed skills:** a confirmed skill without a taxonomy id counts only on an **exact** normalised match with a
taxonomy skill name or curated alias. The match happens at read time; nothing stored changes, and there is no fuzzy
matching.

**Qualifications (D11):** a stored level `mba` reads as level `master`, type `MBA`. Other types come from the stated
title when recognisable, otherwise `null`. Stored records are never rewritten. Qualifications are reported, not
assessed: no degree is a requirement in the pack.

## The approved transferability mapping (from `pm-v1.1.json`)

pm-v1.1 (Phase 3.1) keeps pm-v1's 17 requirements, statements and 129 source links (`sources_from: "pm-v1"`); only the
mapping changes. pm-v1 stays in the database for audit. The "Skills" column gives counts: direct / transferable.

| Key | Requirement | Class / level | Contexts | Direct areas | Transferable areas | Conditions | Skills | Transferable role families | Gap possible | Rationale |
|---|---|---|---|---|---|---|---|---|---|---|
| C1 | Understand users and customers and set priorities from their needs | core / all | — | product_discovery, design_research | customer_account_management, requirements_analysis | — | 11 / 0 | — | yes | Account management and requirements analysis involve learning customers' needs directly, which carries over to product discovery. |
| C2 | Product vision, strategy and roadmap | core / all | — | roadmap_ownership, strategy_portfolio | prioritisation_backlog | — | 5 / 5 | — | yes | Prioritising a backlog, or planning technical or business roadmaps, exercises the sequencing judgement a product roadmap needs. |
| C3 | Turn problems into requirements and priorities | core / all | — | requirements_analysis, prioritisation_backlog | delivery_execution, quality_testing, technical_engineering | — | 5 / 5 | — | yes | Delivery, testing and engineering work involve writing or consuming precise specifications and acceptance criteria. |
| C4 | Define outcome metrics and make data-informed decisions | core / all | — | outcome_ownership, data_analysis | go_to_market_growth, business_case_economics | business_case_economics needs `tracks_outcomes` = yes | 5 / 5 | — | yes | Growth work is run against measurable targets; general analytics skills support defining and reading product metrics; business-case work transfers only where it included defining or tracking outcomes. |
| C5 | Cross-functional collaboration and stakeholder communication | core / all | — | stakeholder_management, executive_communication | customer_account_management, people_leadership, delivery_execution | — | 6 / 0 | — | yes | Managing accounts, leading people and coordinating delivery all require aligning people with different goals. |
| C6 | Iterative delivery across the product lifecycle | core / all | — | delivery_execution | quality_testing, operations_process, technical_engineering | — | 2 / 2 | — | yes | Testing, operations and engineering work are part of shipping and running a product iteratively. |
| X1 | Hands-on analytics (SQL, dashboards, experimentation tooling) | context_dependent / all | growth_analytics, platform_api, data_product | data_analysis | — | — | 8 / 2 | — | yes | Programming or metric work supports learning product analytics tooling. |
| X2 | Owning go-to-market | context_dependent / all | commercial_product | go_to_market_growth | customer_account_management | — | 6 / 3 | — | yes | Customer-facing account and success work exposes how products are adopted and sold. |
| X3 | Technical depth: APIs, platform and data architecture, security | context_dependent / all | platform_api, data_product | technical_engineering | — | — | 7 / 3 | software_engineering, qa_testing, data_science | yes | Engineering, testing or data-science roles build working knowledge of systems and data that platform PM work relies on. |
| X4 | Domain and regulatory knowledge | context_dependent / all | regulated_domain | — | — | — | 2 / 0 | — | never (Unknown) | Assessed only from stated domain skills; employer industry is shown as context, never as proof of domain knowledge. |
| X5 | Value, business case and unit economics | context_dependent / all | commercial_product | business_case_economics | outcome_ownership, strategy_portfolio | — | 3 / 1 | — | yes | Owning outcomes or strategy involves weighing value against cost. |
| X6 | Practical AI and LLM experience | context_dependent / all | ai_product | ai_product_work | — | — | 11 / 0 | data_science | yes | Data-science roles build familiarity with models and evaluation. |
| X7 | Data governance and data quality | context_dependent / all | data_product | data_governance_quality | quality_testing, data_analysis | — | 0 / 0 | — | yes | Quality and data-analysis work involve checking and reconciling data. |
| L1 | Scope: several products or a complex, high-risk product | differentiator / senior_pm | — | strategy_portfolio | roadmap_ownership | strategy_portfolio needs `multi_product_scope` = yes | 3 / 0 | — | yes | Owning a roadmap is the single-product form of portfolio scope. |
| L2 | Coaching, mentoring and product leadership | differentiator / senior_pm | — | people_leadership | — | people_leadership needs `coaching_mentoring` = yes | 3 / 0 | — | yes | — |
| L3 | Executive-level communication | differentiator / senior_pm | — | executive_communication | stakeholder_management | — | 2 / 1 | — | yes | Stakeholder management builds the communication executive work extends. |
| L4 | Planning across teams and functions | differentiator / senior_pm | — | delivery_execution | stakeholder_management | delivery_execution needs `cross_team` = yes | 1 / 2 | — | yes | Coordinating stakeholders across functions is the closest experience to cross-team planning. |

**Mapping limits:**
- X4 (domain) is assessed only from stated domain skills; employer industry is never treated as proof. It never
  produces a Gap.
- A condition is evidence-specific: for example, `strategy_portfolio` counts for L1 only on a line where the user
  answered "yes" to "several products or a complex, high-risk product". An unanswered question is never read as "no".

## What the response contains

For each applicable requirement:
- its state, basis or reason, and level note;
- the user's **own** supporting records: record id, the matched area labels or skill name, and a short excerpt of
  their evidence;
- the curated rationale, for a Transferable result;
- **every source with its stance**, attribution, URL, effective status (D26) and last verification.

A closed posting (J12) appears with status `expired`, as evidence only.

**Never included:** employer criteria; entry-background stances; other users' records; reference or synthetic profiles.

## Limitations, and what is deferred to Phase 4

- A useful assessment depends on the user classifying their evidence. Until then, results stay Unknown (by design: no
  guessing). Phase 3.1 adds the picker (definitions, examples, scope questions, "None of these areas"), per-job
  progress, and optional categorisation while adding a job or accepting pasted suggestions.
- Matching the user against employer criteria (assessment 2) and feasibility (assessment 3) are Phase 4.
- Assessment results are not yet saved as runs. Phase 4 records them with the profile version and pack version.
- Revalidating postings is an administrator action (`trace_util.record_posting_verification`); automated checking is
  not built. Until revalidated, a posting reads as `current_check_required` 14 days after its last verification.

## Phase 5: Trace's reading of confirmed lines (D50)

**Root cause of the wrong states.** The assessment read only the user's own categorisation of each line ("Evidence text is
never interpreted"). A confirmed profile with no categorised lines had every requirement Unknown, the route decision fell
back to "resolve first", and the plan showed capabilities such as strategy and roadmap as not established although the
confirmed lines described them.

**Now.** `interpret.py` reads every reviewed line the user hasn't categorised. An action must govern a responsibility in
the same sentence (a word on its own isn't evidence); the verb sets the strength; negation and learning contexts give
nothing; decision-rights areas need facts. Each reading keeps the cue and why. The user's categorisation wins. Order of
evidence: user direct, Trace clear direct, confirmed skill, then related (needs context), then transferable sources.

**Evaluation** (`python -m evaluation.capability_interpretation [--holdout]`; synthetic, author-annotated):

| Set | Lines | Precision | Recall | False positives (rate) | False negatives | Unsupported decision-rights claims |
|---|---|---|---|---|---|---|
| Development (used for tuning) | 56 | 0.941 | 0.960 | 3 (0.28%) | 2 | 0 |
| Held-out (frozen first, scored once) | 30 | 0.731 | 0.594 | 7 (1.2%) | 13 | 0 |

Held-out per capability: C1 P 1.00 R 0.50; C2 P 1.00 R 0.50; C3 P 0.25 R 0.50; C4 P 1.00 R 1.00; C5 P 1.00 R 0.50;
C6 P 0.50 R 0.25. The gap from development shows overfitting; recall on unseen wording is the main limitation. A miss
becomes "needs more context" or "not yet demonstrated in the available evidence", and the user can categorise the line.
Not tuned against the held-out set. Interpreter source hash at the freeze: `d6cf167280808091`.
