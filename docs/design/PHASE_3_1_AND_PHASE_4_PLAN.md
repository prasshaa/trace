# Phase 3.1 preflight and Phase 4 design (for review; nothing implemented)

**Date:** 2026-11-16.
**Status:** proposal. No code or database structure was changed for this review.
**Builds on:** Phase 3 (`backend/docs/CAPABILITY_ASSESSMENT.md`), pack `pm-v1`, decisions D1–D26.

## 1. Responsibility taxonomy completeness

### 1a. What exists

16 responsibility areas (`public.trace_responsibility_areas`, each with a label and a one-line description).

| Requirement | Mapping today | Can it produce a Gap? | Root cause |
|---|---|---|---|
| X5 value, business case, unit economics | Transferable via `outcome_ownership`, `strategy_portfolio`; skills such as pricing | No | No area for business-case or economics work |
| X6 AI/LLM product work | Direct only via AI skills; role family `data_science` transferable | No | No area for AI product work |
| X7 data governance and quality | Transferable via `quality_testing`, `data_analysis` | No | No area for governance or quality |
| L1 Senior PM scope | **Direct `strategy_portfolio` already exists** | No | The pack chose `not_inferable`, not the taxonomy |
| L2 coaching and mentoring | Direct `people_leadership` ("Leads or mentors people or teams") | **Yes, already** | Adequately represented; no change |
| L3 executive communication | Transferable via `stakeholder_management` | No | No area that distinguishes a senior-leadership audience |
| L4 cross-team planning | Transferable via `delivery_execution`, `stakeholder_management` | No | `delivery_execution` already covers "dependencies"; team-level and cross-team can't be told apart reliably |

### 1b. Proposal: four new areas, and gap_basis changes in a pack revision

The aim is the smallest set that lets these requirements be assessed when reviewed experience genuinely supports a
determination. Every new area has `is_pm_ownership_signal = false`.

| New area (code) | User-facing name | Definition (shown in the picker) | Evidence that belongs | Supports | Level / context | Reuses |
|---|---|---|---|---|---|---|
| `ai_product_work` | AI and machine-learning product work | Specifies, builds, evaluates or launches features powered by machine-learning models or LLMs | "Defined the evaluation set and pass criteria for our support chatbot"; "Chose between two models on cost and latency"; "Wrote prompt and guardrail requirements for an agent" | X6 (direct) | PM and Senior PM; **only** in the `ai_product` context | Direct skills (the ai_ml family) unchanged; `data_science` role history stays transferable |
| `data_governance_quality` | Data quality and governance | Defines, certifies or controls data: shared definitions, quality checks, lineage, access and consent controls | "Defined the single 'active customer' definition used in every report"; "Set up data-quality checks and an access-request process"; "Owned consent flags in the customer data pipeline" | X7 (direct) | PM and Senior PM; **only** in the `data_product` context | `quality_testing`, `data_analysis` stay transferable |
| `business_case_economics` | Business cases and economics | Builds business cases, pricing or cost models, or is accountable for budget, revenue or unit economics | "Built the business case that funded the new pricing tier"; "Cut cost per verification by renegotiating vendors"; "Owned a ₹2 crore annual budget" | X5 (direct); C4 (transferable) | PM and Senior PM; X5 **only** in the `commercial_product` context | Skills (pricing, monetisation, financial modelling) direct; `outcome_ownership`, `strategy_portfolio` stay transferable |
| `executive_communication` | Communicating with senior leadership | Presents strategy, progress, risks or decisions to senior leaders, or writes decision papers for them | "Presented the quarterly roadmap to the CTO and CFO"; "Wrote the decision memo for the leadership committee"; "Ran the monthly business review with directors" | L3 (direct); C5 (direct) | Senior PM for L3; all levels for C5 | Executive-communication and executive-alignment skills direct; `stakeholder_management` stays transferable for L3 |

**Not proposed:**
- **A cross-team planning area (L4).** It overlaps `delivery_execution` ("plans and coordinates delivery, launches and
  dependencies"), and users can't reliably tell "my team" from "several teams". L4 stays `not_inferable`
  (Transferable or Unknown). Adding it would be an arbitrary category.
- **A domain area (X4).** Domain is not a responsibility; it stays skill-based and `not_inferable`.
- **An L1 area.** `strategy_portfolio` already fits.

**Pack revision `pm-v1.1`.** The sources are unchanged; only the mappings are revised. This needs your approval.
`pm-v1` stays recorded for audit.

| Requirement | Change |
|---|---|
| X5 | Direct area `business_case_economics`; `gap_basis` becomes `reviewed_classified_evidence` |
| X6 | Direct area `ai_product_work`; `gap_basis` becomes `reviewed_classified_evidence` |
| X7 | Direct area `data_governance_quality`; `gap_basis` becomes `reviewed_classified_evidence` |
| L1 | `gap_basis` becomes `reviewed_classified_evidence` (direct `strategy_portfolio` already) |
| L3 | Direct area `executive_communication`; `gap_basis` becomes `reviewed_classified_evidence` |
| C4 | Add transferable area `business_case_economics` |
| C5 | Add direct area `executive_communication` |
| L4, X4 | Unchanged (`not_inferable`) |

**Side effects checked:**
- Synthetic reference evidence uses only the existing areas, so observed-route fit, insights and the evaluation
  baselines don't change (re-verified when implemented).
- The Product Owner clarification uses only PM-ownership areas, so it's unaffected.

## 2. Gap semantics

### 2a. Current behaviour (Phase 3)

A Gap is possible only if:
- the requirement's `gap_basis` is `reviewed_classified_evidence`; **and**
- the profile has at least one reviewed evidence record; **and**
- no evidence is pending review; **and**
- every reviewed evidence record has a current (non-stale) classification;

and no direct or transferable mapping matches. Otherwise the result is Unknown, with the reason.

### 2b. Where it correctly produces Unknown

- No reviewed experience at all (`no_reviewed_evidence`).
- Drafts still pending review. They may hold exactly the missing evidence (`evidence_pending_review`).
- Confirmed but unclassified bullets. The evidence may be there but unlabelled (`evidence_not_classified`).
- Text edited after classifying (stale). The old label may no longer describe it.
- Requirements whose evidence can't be read from the codes (X4, L4).

### 2c. Where it may unnecessarily prevent a Gap

| Situation | Effect | Is it a real problem? |
|---|---|---|
| A user with 30 bullets classifies the 25 that matter; 5 are irrelevant (for example "Organised the team offsite") | Every Gap is blocked for the whole profile | **Yes.** There is no way to say "this fits no area" |
| A single forgotten pending draft in an old role | Every Gap is blocked | Partly. Correct in principle, but the reason must point to the exact record |
| Achievements of a certification or project (activity evidence) not classified | Every Gap is blocked | Yes, the same as row 1 |

### 2d. A missing safeguard: taxonomy additions and old classifications

**Finding:** adding areas (§1b) without a safeguard **would** create false gaps.
- A user who fully classified their profile before `executive_communication` existed would immediately get
  L3 = Gap, although they never had the option to choose it.
- Unclassified experience is still safe, since coverage is incomplete, so it stays Unknown. But **previously classified**
  experience isn't safe.

**Required safeguard:** an additive column `area_classified_at` on evidence, set by the classification function.
- A Gap for requirement R is allowed only if every reviewed evidence record was classified **after** all of R's
  direct areas were introduced. `trace_responsibility_areas.created_at` already exists.
- Otherwise R is Unknown with reason `classified_before_new_areas`, and the interface invites the user to re-check
  those bullets.
- Existing classifications (with a null timestamp) count as "before".

### 2e. Proposed refinement (keeps "missing is not absence")

1. **An explicit "None of these areas" choice.** The user can mark a bullet as fitting no area. The value is stored by
   the same trusted function, with basis `candidate_selected`. It counts as classified. It is a positive statement
   by the user, not an inference, and it removes the burden of forcing irrelevant categories.
2. **Clearing a classification.** It is possible now (today an empty list is refused). The bullet becomes
   unclassified again.
3. **Pending drafts still block gaps**, but the response lists the blocking record ids. The interface can then say
   "1 draft in *Data Analyst (2018)* still needs review", and the user can confirm or reject it.
4. **No relaxation** of the rule that every reviewed record must be classified (with "none" now available) before any
   Gap. No partial or "provisional" gaps.

**Tests when implemented:**
- "None" counts as coverage;
- clearing a classification returns it to unclassified (Unknown);
- the new-area safeguard holds (pre-existing classifications never produce gaps for requirements that use new areas);
- the pending-record list is accurate;
- the mutation set is extended accordingly.

## 3. Classification experience

### 3a. What exists

| Capability | Today |
|---|---|
| Evidence grouped under its job | **Yes**, a disclosure "Experience in this job (n)" on each job card |
| Choose areas | **Yes**: "Categorise" → area chips → Save, through `PUT /api/v1/evidence/{id}/classification` and the trusted DB function (`candidate_selected`) |
| Area explanations | **No.** The chips show labels only; the descriptions exist in the DB but aren't returned or shown |
| Correction | **Yes** ("Change", which replaces the selection) |
| Removal | **No.** An empty list is refused by the API and the DB |
| "Fits no area" | **No** |
| What remains | **Partly.** The summary shows "N responsibilities need categorising", but not which job they're in |
| Stale after a text edit | **Yes** ("Text changed: categorise again") |
| Why it matters | **No** explanation |
| Classify while entering or accepting a proposal | **No.** Only afterwards, per bullet |

### 3b. Proposal (Phase 3.1, small, no inference)

1. Return the area `description` and curated `examples` with `/taxonomy/responsibility-areas`. This needs an
   additive `examples text[]` column, filled for all 20 areas, and approved copy. The picker shows the definition
   on demand.
2. A per-job progress line ("3 of 5 categorised") and a "Show uncategorised only" filter on the review page.
3. "None of these areas" and "Clear" actions next to "Change" (needs the §2e functions).
4. An optional area selection per responsibility line in the manual job form, and on accepting a pasted-text
   proposal. It is still the user's own explicit choice, stored by the same trusted function after the record is
   created. Nothing is pre-selected.
5. An explanation shown once on the review page: "Categorising tells Trace which of your experience counts towards a
   target role's requirements. Uncategorised experience is never treated as missing; it just can't be counted yet."
6. **No suggested or pre-filled categories in 3.1.** A deterministic keyword suggestion could come later, but only as
   an unselected hint the user must accept.

**Phase 3.1 database changes:** one additive migration.
- Insert 4 areas.
- Add columns `examples` (areas) and `area_classified_at` (evidence).
- Two trusted functions: `classify_evidence_none` and `clear_evidence_classification`, with the existing guards and
  locks.

Plus pgTAP, a rollback, and the pack revision `pm-v1.1` as a generated data migration.

## 4. Phase 4 design: personalised transition recommendations

### 4.0 Principles carried forward

- Three assessments are kept apart (D25).
- Suitability is separate from evidence breadth (D4).
- There is no score.
- The privacy minimum, own-contribution exclusion and combination checks are unchanged.
- The existing `/api/v1/pathway` endpoint and its ranking stay **unchanged**, so the Phase 8.1 and 8.2 baselines are
  preserved. Phase 4 adds a **new composer and endpoint**.
- The Phase 8.2 relevance prototype stays in shadow mode.
- Everything works without external AI.

### 4A. Personal transition feasibility (assessment 3)

**Candidate routes**, in this order:

1. **Direct (profile-led):** current confirmed role → target. Always considered.
2. **Observed:** the privacy-safe routes from the existing `recommendation_evidence` composition (own exclusion,
   progressions only, minimum 5). They may be direct or intermediary.
3. **Profile-led intermediary:** **only** through curated *bridge roles* in the approved pack (for example Product
   Analyst, Associate PM, Product Owner), each with sources. **The pack has none yet**, so this is a content decision
   (D27). Until then, no intermediary role is ever invented.

**Per route**, transparent findings (each a named rule with its inputs shown):

| Finding | Rule |
|---|---|
| `constraint_ok` | Satisfies the user's hard constraints (for example "direct moves only"), stored in `trace_pathway_runs.constraints_json`. A failing route is excluded and listed with the reason |
| `start_fit` | The route starts at the user's confirmed current role. For observed routes this is the existing current-position rule |
| `target_basis` | From the Phase 3 assessment for the **target** (core requirements only): `strong` (no core Gap and no core Unknown), `partial` (some Gaps, but at least one of C1, C2, C4 Demonstrated or Transferable), `limited` (Gaps in all of C1, C2, C4) or `undetermined` (core Unknowns block a conclusion). Categories, not counts, and no weights |
| `bridge_value` (intermediary routes) | The user's core Gaps, set against what the bridge step offers: for observed routes, the **privacy-safe disclosed** step areas; for curated bridges, the bridge role's curated areas. Result: `closes_gaps` (non-empty overlap, listed), `no_added_value` or `unknown` |
| `moves` | The number of additional role moves (0 for direct) |
| `evidence_breadth` | The **D4 label** (Limited, Growing, Broad, Extensive) for observed routes; "limited or unavailable comparable career-history evidence" for profile-led routes. **Never used for ordering** |

**Direct vs intermediary, the ordered rules:**

1. Exclude routes that fail constraints.
2. If `target_basis` is `strong`, or `partial` where the Gaps are only in C3, C5 or C6 (practice areas a first PM
   role builds), prefer **direct**. Intermediary routes are alternatives whose trade-off is "more time in a bridge
   role, little added".
3. If `target_basis` is `limited`, or `partial` with Gaps in C1, C2 or C4 (core ownership), **and** an intermediary
   route has `bridge_value = closes_gaps` for those requirements, prefer that **intermediary**. Direct remains an
   alternative ("possible, but your profile doesn't yet show X; these employers accept adjacent backgrounds…").
4. If `undetermined`, no primary is chosen on capability. The result asks the user to complete the classification and
   still shows the direct route and any observed routes as options, with the reason.
5. Ties are shown as tied (no hidden ordering). Evidence breadth never breaks a tie.

**Why a route is recommended:** the response lists each rule outcome in plain text, with the requirements and
records behind it. For example: "Your experience shows discovery and requirements work (transferable to C1, C3), but
not roadmap or outcome ownership (C2, C4 gaps). Product Analyst roles in comparable histories commonly involve outcome
and data work, which closes C4."

**Qualifications, skills and industries** inform findings only through the assessment and eligibility (4B). Domain
(X4) and context requirements appear only in the user's chosen contexts.

### 4B. Employer-specific application eligibility (assessment 2)

- **Which postings:** for the target level, `effective_status = current` (verified within 14 days) as **current
  opportunities**. Expired postings (J12) appear only in a separate "dated examples" list. Postings needing
  revalidation are listed as such and never shown as current.
- **Per criterion: `meets_stated`, `does_not_meet_stated` or `unclear`:**

  | Criterion | How it's checked |
  |---|---|
  | Experience years | From reviewed employments in the relevant role families, using partial-date bounds: certain minimum and maximum months. "Meets" only if the certain minimum reaches the stated minimum; "does not meet" only if the maximum can't reach it; otherwise unclear |
  | Education | Needs structured criteria. **Additive columns** on `trace_target_role_requirements`: minimum level, accepted fields, "or equivalent". "Or equivalent" always yields `unclear`, never "does not meet" |
  | Entry-background stance | Needs structured background → role-family mapping. **Additive columns** `accepted_role_families` and `excluded_role_families` on sources, curated in the pack (for example "project management" → `project_program_management`). `requires_target_role_experience` checks reviewed PM-family roles |

- **Output per posting:** overall `meets_stated_criteria`, `partly`, `does_not_meet_stated_criteria` or `unclear`;
  per-criterion results with the quote, locator and last-verified date.
- **Never counted:** results aren't turned into shares across employers, and never become capability gaps or general
  advice. Copy is per employer: "Target's posting (verified 16 Nov) accepts…".

### 4C. Pathway composition

| Field | Contents |
|---|---|
| `route_basis` | `observed` or `profile_led` (never mixed). Synthetic-mode observed routes carry the synthetic label |
| `steps` | Current → (bridge) → target. Each step has its role and its role-level capability notes |
| Findings | 4A findings, `rule_trace`, trade-offs versus each alternative |
| `actions` | The four stages (4D) |
| `eligibility_examples` | 4B, per posting, linked but separate |

Courses, projects, referrals and learning are **actions inside stages**, never routes.

### 4D. Four-stage action planning

**Rules from the assessment.** Templates are curated content in the pack, a new approval item (D28):

| Requirement state | Actions |
|---|---|
| Gap | **Build** (stage 1) + **Demonstrate** (stage 2) |
| Transferable | **Demonstrate** (stage 2: turn the transfer into direct evidence) + **Reposition** (stage 3, citing *their* supporting record) |
| Demonstrated | **Reposition** only (stage 3). Never "learn the basics" |
| Unknown | No stage action. A "complete your profile" item before stage 1 (classify or confirm the named records) |
| Not applicable | Nothing |

**Stage objectives and milestones:**

| Stage | Objective | Example deliverable | Actions |
|---|---|---|---|
| 1 Build foundation | Close knowledge gaps in the target's core requirements | A short written product brief applying the method | Gap-driven only. No verified course is named unless the knowledge base holds a verified resource (it doesn't yet, so none are invented) |
| 2 Gain practical experience | Turn Transferable and Gap requirements into real evidence, preferably in the current job | "Own the success metric for one feature in your current team and review it after launch" | Each says whether it fits the current job, an internal move or an external project |
| 3 Become application-ready | Position Demonstrated and Transferable experience as product evidence | Rewritten bullets for named profile records; one case study from real work, labelled honestly when self-directed | Reposition actions |
| 4 Get into the target role | Apply where the user is eligible, or take the bridge role | A list of current eligible postings with last-verified dates; interview preparation for the user's Gap and Transferable areas; the bridge-role application plan (intermediary routes) | Employer stance examples (attributed); referral approach |

**Every action carries:**
- `why_for_you` (the requirement, the state and the user's record);
- `addresses` (a requirement key);
- `what_to_do`;
- `deliverable`;
- `where` (`current_job`, `internal_move`, `external_project`, `formal_learning`, `job_change`);
- `sources` (requirement sources).

There are no durations, costs or outcome promises.

### 4E. Recommendation selection

- **Ordered rules (4A), not a score.** The primary is the first route by: constraints → `target_basis`/`bridge_value`
  rule → fewer moves when otherwise equal.
- **Alternatives:** included only when they differ in route basis, steps or trade-off, with up to 3 credible ones.
  None are manufactured.
- **Evidence breadth:** a separate label with the D26/D4 wording. A lower-evidence primary gets the approved subtle
  note only when the rule trace shows it was chosen for profile fit.
- **Frequency:** never described as probability or effectiveness.

### 4F. Persistence (existing tables first)

| Need | Use |
|---|---|
| A run | **`trace_pathway_runs`** (exists): owner, profile, `data_mode`, `target_role_id`, `constraints_json` (contexts, hard constraints, openness to intermediaries), `reference_snapshot_id`, `status` |
| Run audit inputs | **Additive columns:** `profile_version`, `knowledge_pack`, `knowledge_pack_digest` (SHA of the seeded content), `engine_version`, `assessment jsonb` (requirement states, the record ids used, source keys with their `last_verified_at`), `eligibility jsonb` (per-posting results with verification dates) |
| Reference snapshot | **`trace_ref.reference_snapshots`** (exists, **empty**, no writer). It needs a definer function, `trace_ref.record_reference_snapshot(data_mode)`, holding counts and settings only, callable by the runtime. A completed run already requires one |
| Each route | **`trace_pathway_recommendations`** (exists): rank, `recommendation_reason`, `evidence_class` (forced by existing constraints: `synthetic_test_only` in synthetic mode; `insufficient` with count 0 for profile-led routes in empirical mode), `supporting_profile_count`, `limitations`. **Additive:** `route_basis`, `is_primary`, `feasibility jsonb` (findings and rule trace), `evidence_breadth_label`, `tradeoffs jsonb`. **`score_breakdown` stays `{}`** (no scores) |
| Steps | **`trace_pathway_route_steps`** (exists) |
| Evidence | **`trace_pathway_evidence`** (exists): `observed_sequence` and `transition_count` for observed routes; `role_requirement` with `target_role_source_id` for knowledge sources. Reference profile ids stay hidden by column privileges |
| Actions | **New `trace_pathway_actions`** (no existing structure): recommendation, stage 1–4, kind (build, demonstrate, reposition, apply), requirement key, objective, action, deliverable, where, `why_for_you`, source keys |
| Write path | Today **only `service_role`** writes these tables. Proposal: the owner role (`trace_app_user`) gets insert column grants with RLS owner policies, plus a guard making runs and their children **append-only** (no updates except `status`). Alternatively a writer-owned definer function, as for corrections. The backend never uses `service_role` |

**Reproducibility:** a run records:
- the profile version (fingerprint) and the record ids used;
- the pack key and content digest;
- each source's last verification;
- the reference snapshot id;
- the engine version;
- the full rule trace.

Re-running with the same inputs must give identical output; a test will check this. Runs are never overwritten, so a
profile change means a new run.

**API (structured for the later report UX):** `POST /api/v1/transition-plan` (creates and saves a run) and `GET`
(latest run, or by id). It returns the assessment summary, routes with findings, actions by stage, eligibility
examples and provenance, with no HTML or display text beyond short explanations.

### Decisions needed before Phase 4

| ID | Question | Recommendation |
|---|---|---|
| D27 | Curated bridge roles for profile-led intermediary routes (role, typical areas, sources) | Approve a small set with sources (Product Analyst, Associate PM, Product Owner), or accept that only observed intermediaries are offered for now |
| D28 | Curated action templates (per requirement × kind × stage) | Approve the content before it's seeded, like the pack |
| D29 | Structured eligibility criteria (education level and fields; background → role-family maps) | Approve the additive columns and the curated maps |
| D30 | Write path for runs | Owner insert grants with an append-only guard (recommended) |

## 5. Validation plan (synthetic users, through the API)

| # | Scenario | Expected assessment | Expected recommendation | Expected actions |
|---|---|---|---|---|
| V1 | Software Engineer → PM (platform, commercial) | C6 and X3 Demonstrated; C3, C5 Transferable; C1, C2, C4 Gap | **Intermediary preferred if** a route with a bridge exists that closes C1, C2, C4 (observed in synthetic mode); otherwise direct, `partial` basis, with the trade-off explained | Build: discovery, roadmap and metrics; Demonstrate: own a metric in the current team; Reposition: API work as platform-PM evidence; stage 4: platform-PM postings with eligibility |
| V2 | Business Analyst → PM (fintech, growth) | C3, C4, C5, X1, X4 Demonstrated; C1 Transferable; C2, C6 Gap | `partial` basis with one ownership Gap (C2). **Direct**, unless an available bridge route's step areas close C2, in which case that intermediary is primary and direct is the alternative. Both outcomes are tested with different fixtures | No "learn analytics" action. Build roadmap; Demonstrate delivery ownership; stage 4: Target's posting (accepts adjacent) as a current example |
| V3 | Product Owner → Senior PM (commercial) | C1, C3, L2 Demonstrated; C2, C5 Transferable; C4, C6, X2 Gap; L1, L3 per the §1 changes | Direct to Senior PM vs. PM first. With Gaps in C4 (ownership), **PM first** is preferred when the rule fires | Demonstrate outcome ownership; Reposition backlog leadership |
| V4 | Extensive transferable experience, some genuine Gaps | Mostly Transferable; 1–2 core Gaps | Direct, `partial`; the primary explains which Gaps remain | Demonstrate-heavy plan; Build only for the real Gaps |
| V5 | Incomplete or unclassified | Core Unknown (`evidence_not_classified`) | **No primary chosen**; the direct route is shown as an option; profile-completion items first | Only "complete your profile" items before stage 1; no stage actions on Unknowns |
| V6 | No comparable history (empirical mode, 0 real profiles) | As the profile allows | A **profile-led** direct route with "limited or unavailable comparable career-history evidence"; still a full four-stage plan | Normal |
| V7 | Direct beats intermediary | `strong` basis | Direct primary; the observed intermediary is shown as an alternative ("adds a move without closing a gap") | Reposition-heavy |
| V8 | Intermediary beats direct | `limited` basis with a closing bridge | Intermediary primary; direct is an alternative with the trade-off | Stage 4 targets the bridge role first |
| V9 | Same user, different eligibility | (One user, V2) | Unchanged by eligibility | Eligibility differs per posting: Target J11 `meets_stated` (adjacent accepted, years met); Paytm J5 `does_not_meet_stated` (3+ PM years); Sarvam J12 shown only as a **dated** example. The capability states are identical in all cases |

**Cross-checks:**
- determinism (the same inputs give an identical run);
- evidence breadth never changes the primary;
- no scores, percentages or probabilities in any output;
- the privacy minimum and own exclusion hold;
- expired or stale postings are never current;
- the Phase 8.1 and 8.2 baselines stay byte-identical;
- mutation tests on each ordered rule.

## 6. Proposed order

1. **Phase 3.1 (small, on approval):**
   - the four areas;
   - the examples column;
   - `area_classified_at`, with the new-area safeguard;
   - the "none of these" and "clear" functions;
   - pack `pm-v1.1`;
   - classification UX items 3b-1 to 3b-5.
2. **Phase 4 (on approval of D27–D30):**
   - the snapshot writer and run write path;
   - feasibility, eligibility and composer modules;
   - action templates;
   - persistence;
   - the API;
   - the V1–V9 validation.
