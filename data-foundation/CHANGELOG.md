# Changelog

## 2.42.0 — 2026-11-26 (peer patterns are counts again)

| Change | Files | Why |
|---|---|---|
| `peer_patterns(vector, jsonb, text)` reranks in a CTE and returns aggregate sentences. The distance sort cannot use the HNSW index, so the pool is 120 rather than the index default of about 40. The v2.41 five-argument function is left in place so the running app still resolves during rollout. A pattern needs 10 known profiles before it is phrased as "most of the N". The caller's own background is a match line, not a pattern. A switcher always gets the target-level start, the months median (the range only beside it), and the MBA line when they have an MBA. Someone already in product gets the target-level line, years in product, and an MBA comparison. Execute on the new function stays with `trace_app_runtime` only | migration 59 | v2.41 exposed neighbour attributes, and a stable function that built temporary tables could not be called |
| Rollback restores the v2.41.0 function | rollback | |

## 2.45.0 — 2026-11-27 (time to a first PM role)

| Change | Files | Why |
|---|---|---|
| The dated-path sentence is "it took about 8 years to reach a first PM role" | migration 62 | "about 8 years to a first PM role" had no verb |
| Rollback restores the v2.44.0 sentence | rollback | |

## 2.44.0 — 2026-11-27 (peer pattern wording)

| Change | Files | Why |
|---|---|---|
| Replace `peer_patterns(vector, jsonb, text)`. A duration over 24 months is the nearest half year. The 25th–75th range is folded into the median sentence as "the middle half took", and it is not a separate pattern. An MBA holder sees how uncommon an MBA is in the whole cohort. Someone already in product also sees the pre-PM background mix among people at their target level, and the typical years since career start at that level. Lines about the caller's own move or target level use a lift of 1.1 | migration 61 | The range was being read as "most", and an MBA comparison overstated a rare credential |
| Rollback restores the v2.42.0 function | rollback | |

## 2.43.0 — 2026-11-26 (drop the per-profile peer function)

| Change | Files | Why |
|---|---|---|
| Drop `peer_patterns(vector, text, text[], integer, text)`, the v2.41 function that returned one attribute object per neighbour. The file is `20261126235900_trace_peer_drop_row_function.sql` | migration 60 | Apply only after the app that calls `peer_patterns(vector, jsonb, text)` is deployed |
| Rollback recreates that five-argument function | rollback | |

## 2.41.0 — 2026-11-26 (peer search returns a candidate pool)

| Change | Files | Why |
|---|---|---|
| `peer_patterns` returns the 120 closest anonymous attribute rows, plus cohort counts, background counts and first-level progression counts. No profile key. The level nudge is no longer applied in the database | migration 58 | The application reranks that pool. Embedding distance alone was too compressed to separate paths |
| Rollback restores the v2.40.0 aggregate function | rollback | |

## 2.40.0 — 2026-11-26 (peer patterns stay a similarity search)

| Change | Files | Why |
|---|---|---|
| `peer_patterns` uses a 0.03 same-level nudge and a 0.02 adjacent nudge, and returns neighbour counts beside whole-cohort counts. A blank degree and a pre-PM count with no history can be null | migration 57 | The previous 0.15 nudge dominated the narrow cosine range, and a blank was being stored as false or zero |
| Rollback restores the v2.39.0 function and the not-null columns | rollback | |

## 2.39.0 — 2026-11-26 (peer career-path patterns)

| Change | Files | Why |
|---|---|---|
| Schema `trace_peer_cohort` holds de-identified career-path rows and a local embedding. It is not a user table, a contribution, or a reference profile. RLS is on. No client role can read the table. `peer_patterns` is security definer, returns one jsonb of counts, and is executable by `trace_app_runtime` only | migration 56 | The plan can match a career path to the closest profiles without returning a person |
| Rollback drops the schema | rollback | |

## 2.38.0 — 2026-11-26 (anonymous recommendation use is the default)

| Change | Files | Why |
|---|---|---|
| `trace_contrib.acknowledge_data_use_notice` records `shared` when the caller has no preference yet. An existing choice is still never overwritten. No consent event is written and nothing is enqueued | migration 55 | New users default to using their data anonymously to improve recommendations. The profile is not published and is not added to the shared pool |
| Rollback restores the v2.36.0 body (`private`). Preferences already stored are not rewritten | rollback | |

## 2.37.0 — 2026-11-26 (account deletion can be called)

| Change | Files | Why |
|---|---|---|
| `USAGE` on schema `trace_private` for `trace_app_user` only, plus the existing `EXECUTE` on `trace_private.delete_my_account()`. No `CREATE`. No table privileges. `EXECUTE` stays revoked from public, anon, authenticated and service_role. The function is unchanged: security definer, empty `search_path`, deletes `auth.users` only for `trace_review.request_uid()` (`auth.uid()`) | migration 54 | v2.36.0 granted execute, but a role cannot resolve a function without schema usage, so deletion failed before the function ran |
| pgTAP `trace_delete_my_account_grant.test.sql`. The runtime test now expects that one usage grant. Rollback revokes the usage and does not drop the function | tests, rollback | |

## 2.36.0 — 2026-11-26 (private sharing default, account deletion)

| Change | Files | Why |
|---|---|---|
| `trace_contrib.acknowledge_data_use_notice` records `private` when the caller has no preference yet. An existing choice is still never overwritten. No consent event is written | migration 53 | Sharing is opt-in. Continuing the notice must not turn sharing on |
| `trace_private.delete_my_account()` deletes `auth.users` for the request identity only. Career rows, plans, drafts and contributions follow existing `on delete cascade` foreign keys. `trace_app_user` only | migration 53 | The owner can delete their account. The function cannot name another user |
| pgTAP: the onboarding assertion expects `private`. Rollback restores the previous function body and drops `delete_my_account` (preferences already stored are not rewritten) | tests, rollback | |

## 2.35.0 — 2026-11-23 (semantic retrieval, Phase 3: hybrid candidates)

| Change | Files | Why |
|---|---|---|
| `trace_semantic.hybrid_candidates(profile, queries jsonb, model, version)`: for one query family (≤ 64), the caller's ELIGIBLE records only (same set as `my_embedding_sources`) with provenance (source, parent employment or activity, review status), the user's CURRENT categorisation (stale ones not returned), and per query the lexical (full-text, english, `ts_rank_cd`) and semantic (cosine over the current embedding) RANKS both ways: line within query, query within line. Security definer, owned by `trace_embedding_writer`, `trace_app_user` only; refuses calls without a request identity | migration 52 | Phase 3 hybrid retrieval: ranks computed where the data is; similarity values never leave the database (D53) |
| Column grants to `trace_embedding_writer`: evidence `area_classification`, `area_classification_is_stale`, `area_classification_none`, `candidate_employment_id`, `candidate_activity_id`; activities `related_employment_id` | migration 52 | Provenance and the user's own categorisation; nothing else (`area_qualifiers` stays unreadable) |
| pgTAP `trace_semantic_hybrid_candidates.test.sql` (11). Rollback (drops the function and revokes the column grants; no data), rehearsed and re-applied | tests, rollback | |

## 2.34.0 — 2026-11-23 (semantic retrieval, Phase 2: pgvector storage)

| Change | Files | Why |
|---|---|---|
| `create extension vector` (schema `extensions`); schema `trace_semantic`; NOLOGIN role `trace_embedding_writer` | migration 51 | D53: embeddings generated inside Trace (local gte-small, 384 dimensions) |
| `trace_private.candidate_embeddings`: one row per (reviewed evidence line or career activity, model, version); content SHA-256, no text; FK `on delete cascade` to the source record, the profile and the account; RLS on the request identity; no client grant. Functions (security definer, `trace_app_user` only, owned by the writer): `my_embedding_sources`, `store_my_embeddings` (stores only the caller's eligible records whose CURRENT text has the given hash), `match_my_evidence` (cosine; stale, unreviewed and rejected records excluded) | migration 51 | Private corpus: strict ownership; deleted on clear, replace or account deletion; never returned stale |
| `trace_ref.knowledge_embeddings` (corpus constrained to `role_knowledge`; areas, capability requirements, action templates), read by `trace_reference_reader`; `trace_semantic.match_knowledge` | migration 51 | Shared role knowledge, kept separate; the research corpus can't be written here |
| No vector index (exact scan: tens of rows per person, ~100 knowledge rows). pgTAP `trace_semantic_embeddings.test.sql` (12). Rollback (drops only what v2.34.0 added; embeddings are regenerable), rehearsed and re-applied | tests, rollback | |

## 2.33.0 — 2026-11-22 (release: clear my career profile and start over)

| Change | Files | Why |
|---|---|---|
| `trace_import.clear_my_import_records()` (security definer, request identity, `trace_app_user` only, owned by `trace_import_writer`; migration self-check): deletes the caller's rows in `trace_private.resume_quarantine` and `trace_private.resume_processing_log`, the two owner-keyed tables with no client grant and no foreign key to the profile. Owner-scoped delete and select policies for `trace_import_writer` on both. Idempotent. The profile itself is cleared by the owner's existing delete policy and the foreign-key cascade the delete guards already allow (the account-deletion path) | migration 50 | D52: a user can clear their career profile and start over; nothing derived from it may be left behind |
| pgTAP: new `trace_clear_import_records.test.sql` (9 assertions). Rollback for migration 50 (removes the function and policies only; no data), rehearsed on the local stack and re-applied | tests, rollback | |

## 2.32.0 — 2026-11-22 (Phase 5: common title aliases; where the user wants to move)

| Change | Files | Why |
|---|---|---|
| Ten exact, non-context-required role aliases (`ALS-0107`–`ALS-0116`): Software QA Engineer, Software Test Engineer, Quality Assurance Engineer, QA Analyst, Automation Test Engineer → QA / Quality Engineer; Cofounder, Co founder, Founder & CEO, Co-founder & CEO → Founder / Co-founder; Global Product Manager → Product Manager. Automatic mapping stays an exact match; no existing mapping is rewritten | migration 49 | D50: common titles were left unmatched in review, so the user had to choose a role the taxonomy already has |
| `trace_private.target_move_preferences` (owner, target role, scope `specific_companies` / `company_types` / `open`, up to three kinds of company from a fixed list); no client grant. `trace_plan.my_move_preference(text)` and `trace_plan.set_move_preference(text, text, text[])` (security definer, request identity, `trace_app_user` only, owned by `trace_preference_writer` under owner-scoped policies; migration self-check). Named companies stay in `target_company_preferences` (v2.29.0) | migration 49 | D51: "Where would you like to make this move?" A planning preference; never employer evidence, never read by the plan writer |
| pgTAP: new `trace_move_preferences_and_aliases.test.sql` (16 assertions). `trace_candidate_profile.test.sql`: the "skill outside the taxonomy" assertion is scoped to its own test profile (it matched a real local profile's skill of the same name). Rollback for migration 49 (refuses while preferences exist or an employment is mapped through the new aliases), rehearsed on the local stack and re-applied | tests, rollback | |

## 2.31.1 — 2026-11-20 (resume import drafts: lock for confirmation)

| Change | Files | Why |
|---|---|---|
| `trace_import_writer` gets `update (warnings)` on `trace_private.resume_import_drafts` under an owner-scoped update policy, so `trace_import.my_draft(p_lock => true)` can take `FOR UPDATE`. No function updates a draft | migration 48 | Serialises two confirmations of one import (the second finds no draft and reports "already confirmed") |
| Rollback for migration 48, rehearsed on the local stack with migration 47 and re-applied | rollback | |

## 2.31.0 — 2026-11-20 (one staged model for resume imports; markers only for contributors; no implied retention)

| Change | Files | Why |
|---|---|---|
| `trace_private.resume_import_drafts`: the owner's unconfirmed, de-identified resume import (draft JSON, parser and pipeline versions, proven format and MIME type, size, SHA-256, warnings, sealed markers only for a contributor); one per profile; no client grant. `trace_import.save_draft`, `my_draft`, `delete_draft` (security definer, request identity, `trace_app_user` only, owned by the new NOLOGIN role `trace_import_writer`; migration self-check) | migration 47 | D48: nothing from an import reaches the profile tables before confirmation, for a new and an existing profile alike |
| `trace_resume_documents.retention_until`: default dropped and nullable, with a column comment. Existing values unchanged | migration 47 | No retention period is approved and nothing enforced the 30-day default; no automatic deletion is introduced |
| `trace_private.identity_markers_needed` also requires an active contribution consent; `trace_contrib.store_identity_markers` refuses without one (TR022). Trigger `trace_consent_privacy` on consent events: withdrawing or declining contribution deletes the markers; withdrawing resume processing deletes the open import draft | migration 47 | Markers are kept only for the purpose that needs them |
| pgTAP: new `trace_resume_import_drafts.test.sql` (20 assertions). `trace_marker_protection` and `trace_contribution_safety` set up an active contribution consent where markers are expected. Rollback for migration 47 (refuses while drafts exist, or while a resume row has no retention date), rehearsed and re-applied | tests, rollback | |

## 2.30.0 — 2026-11-17 (transition goals: the career transition roadmap)

| Change | Files | Why |
|---|---|---|
| `trace_private.transition_goals` (owner, target role, timeframe or target date, optional weekly hours, the user's own checkpoint dates, the date the goal was set); no client grant. `trace_plan.my_transition_goal(text)` and `trace_plan.set_transition_goal(text, text, date, text, jsonb)` (security definer, request identity, executable by `trace_app_user` only, owned by `trace_preference_writer` under owner-scoped policies; migration self-check) | migration 46 | D44: a planning goal per target role; never a plan input, never a forecast |
| pgTAP: new `trace_transition_goals.test.sql` (15 assertions: access, ownership, values, per-target goals, validation, isolation, no plan run created). Rollback for migration 46 (refuses while any goals exist; keeps the v2.29.0 writer role), run on the local stack and re-applied | tests, rollback | |

## 2.29.0 — 2026-11-17 (target-company preferences: POC iteration)

| Change | Files | Why |
|---|---|---|
| `trace_private.target_company_preferences` (owner, target role, name, normalised key, position, added date); no client grant. `trace_plan.my_target_companies(text)` and `trace_plan.set_target_companies(text, text[])` (security definer, request identity, executable by `trace_app_user` only; owned by the new NOLOGIN role `trace_preference_writer`, which holds only select, insert and delete on the table under owner-scoped policies, with a migration self-check that nobody can assume it): replace the caller's own list for one target with trimming, case-insensitive de-duplication, at most 20 and target validation | migration 45 | D42: companies are aspirations, stored per user and target; never plan inputs |
| pgTAP: new `trace_target_company_preferences.test.sql` (18 assertions: access, writer ownership, normalisation, per-target lists, remove and reorder, validation, isolation). Rollback for migration 45 (refuses while any preferences exist, checked; drops the writer role), run on the local stack and re-applied | tests, rollback | |

## 2.28.0 — 2026-11-17 (employer rules pm-eligibility-v3: Phase 4 closure)

| Change | Files | Why |
|---|---|---|
| New rule rows `pm-eligibility-v3` (v1 and v2 kept): J12's current-role leaf lists `unconfirmed_roles: ["PM-APM"]` (a current Associate PM is unclear for "currently a product manager"); J3's engineering preference lists `before_families: ["product_management"]` (only experience before the first product role counts). The generated migration checks that both keys name existing taxonomy entries | migration 44 (generated) | D41; review finding E8 |
| pgTAP: 4 assertions added to `trace_phase4_remediation.test.sql`. Rollback for migration 44, run on the local stack and re-applied | tests, rollback | |
| Maintenance (not a migration): `supabase/maintenance/20261117_legacy_reference_snapshot_cleanup.sql` removed the 640 verified legacy snapshots after a rehearsed restore; `..._restore.sql` reloads them from the pre-cleanup backup | maintenance | D39 |

## 2.27.0 — 2026-11-16 (shared, dated reference snapshots; service_role remediation: D37, D38)

| Change | Files | Why |
|---|---|---|
| `trace_ref.reference_snapshots`: `state_fingerprint` (sha256 of cohort, settings and aggregate counts) and `first_observed_on` (date). Unique fingerprint index. The plan writer, replaced by its owner, reuses one row per reference-pool state, sets `created_at` to the day, and records action labels | migration 43 | D37: no per-plan timing link, no accumulation |
| Existing (unfingerprinted) snapshot rows are left exactly as they are. Cleanup is proposed separately (`docs/design/SNAPSHOT_CLEANUP_PROPOSAL.md`) | — | D37 |
| `service_role` loses INSERT, UPDATE, DELETE and TRUNCATE on the plan tables and `reference_snapshots` (SELECT kept); a self-check fails the migration otherwise | migration 43 | D38: no bypass of the writer or of row-level immutability |
| pgTAP: new `trace_phase4_remediation.test.sql` (40 assertions, covering 2.24.0 to 2.27.0). Rollback for migration 43 (refuses while shared snapshots exist), run on the local stack and re-applied | tests, rollback | |

## 2.26.0 — 2026-11-16 (Phase 4 remediation content: pm-bridges-v2, pm-actions-v2, pm-eligibility-v2)

| Change | Files | Why |
|---|---|---|
| Bridges v2: the Associate PM develops C2 and C4 only partly (F1 "awareness"); Product Analyst C1 partly; the Product Owner is alternative-only with hypothesis entry and progression; every partial requirement states what it develops, what remains and what to seek. D33 candidate pool: F1's five listed roles, original names, mapping confidence | migration 42 (generated) | D32–D34 |
| Templates v2 (55): learning exercises and personal projects labelled; factual-accuracy repositioning, with skill and role variants; realistic L1/L2; Stage 4 adds employer criteria, a case study, case practice, alternative routes and the responsibility to seek on bridge routes | migration 42 | Phase 4 review T1–T8 |
| Employer rules v2 (18), in `trace_employer_criterion_rules` (v1 kept), and 14 education fields with explicit aliases | migration 42 | D36 |
| Rollback for migration 42, run on the local stack and re-applied | rollback | |

## 2.25.0 — 2026-11-16 (pack pm-v1.2)

| Change | Files | Why |
|---|---|---|
| pm-v1.2: decision-rights flag on C2, C4 and L1; their stated skills are Transferable only; C4 counts Data analysis directly only with a Yes to the outcome-metrics question, and as Transferable with a No. Same 17 requirements and 129 source links | migration 41 (generated) | D31, D35 |
| Rollback for migration 41, run on the local stack and re-applied | rollback | |

## 2.24.0 — 2026-11-16 (structures for the Phase 4 remediation)

| Change | Files | Why |
|---|---|---|
| Taxonomy v3: the D35 question on Data analysis. Requirements: `decision_rights` and `area_condition_fallback` (validated, TR025). Bridges: entry and progression evidence, `may_be_primary`, `partial_development` (validated, TR026). New `trace_digital_role_candidates`, `trace_education_fields` and `trace_employer_criterion_rules` (validated, TR026). Template and action `labels`; place value `trace_profile` | migration 40 | D31–D36 |
| Rollback for migration 40 (refuses while answers to the D35 question exist), run on the local stack and re-applied | rollback | |

## 2.23.0 — 2026-11-16 (owner-scoped, append-only transition-plan runs: D30)

| Change | Files | Why |
|---|---|---|
| `trace_plan_writer` (NOLOGIN, no BYPASSRLS, no usable members) owns `trace_plan.record_transition_plan(jsonb)`: SECURITY DEFINER, empty search_path, EXECUTE for `trace_app_user` only. One call is one transaction: verifies the caller owns the profile (TR005), validates the payload (TR028), records a reference snapshot it computes itself (counts and settings only), inserts the run, its routes, steps, knowledge evidence and actions, and completes the run. Supplied scores and supporter counts are ignored; reference profile ids are refused | migration 39 | D30: the narrowest backend-mediated write path; the backend never uses service_role |
| Additive audit columns on `trace_pathway_runs` (plan kind, engine, profile, pack, source inventory, taxonomy, catalogue, template and rule versions, assessment, eligibility, source verification states, privacy state, decision, completion time) and on `trace_pathway_recommendations` (route basis and kind, primary status, bridge role, explanation, feasibility, trade-offs, evidence-breadth label). Checks: completed plan runs carry every audit field; plan routes have `score_breakdown = {}`; a profile-led route has no supporting profiles | migration 39 | Reproducible, auditable runs; no opaque scores |
| `trace_pathway_actions` (new): the four-stage actions of a route, from the template library (FK), owner-readable | migration 39 | D28 |
| Immutability (TR027): a completed run cannot be updated, and nothing can be added to or changed in its routes, steps, evidence or actions. Deletion is unchanged (owner delete policy; account and profile cascades) | migration 39 | D30 |
| pgTAP: new `trace_transition_plans.test.sql` (48 assertions, covering 2.21.0 to 2.23.0). Rollback script for migration 39 (refuses while plan runs exist), run on the local stack and re-applied | tests, rollback | |

An empty reference snapshot (no eligible real profiles) is legitimate and recorded as `reference_pool = empty`; nothing synthetic is substituted.

## 2.22.0 — 2026-11-16 (Phase 4 content: bridge roles, action templates, structured employer criteria)

| Change | Files | Why |
|---|---|---|
| Two role-level sources: GitLab Product Analyst job family (CC BY-SA 4.0) and the Scrum Guide 2020 Product Owner accountability (CC BY-SA 4.0). Four bridges (`pm-bridges-v1`): Associate PM, Product Analyst and Product Owner for Product Manager; Product Manager before Senior Product Manager. No program or project management bridge | migration 38 (generated by `scripts/build_transition_content.py`) | D27 |
| 49 action templates (`pm-actions-v1`): build and demonstrate for every pm-v1.1 requirement, reposition, resolve-first and stage 4 | migration 38 | D28 |
| All 18 employer criteria structured (`pm-eligibility-v1`, new columns only; text, importance and sources unchanged); J11 accepted and J12 excluded backgrounds mapped to role families | migration 38 | D29 |
| `tests/test_knowledge_pack.py`: the migration matches the JSON; every criterion is structured exactly once; no title-only rule; no program-manager bridge. Rollback for migration 38, run on the local stack and re-applied | tests, rollback | |

## 2.21.0 — 2026-11-16 (structures for transition knowledge)

| Change | Files | Why |
|---|---|---|
| `trace_bridge_roles` and `trace_action_templates` (curated, versioned; read by signed-in users; written by migrations only; validated by triggers, TR026) | migration 37 | D27, D28 |
| `trace_target_role_requirements.criterion` (structured AND/OR rule) and `criteria_version`; `trace_target_role_sources.accepted_role_families` and `excluded_role_families`, with checks and validation (TR026) | migration 37 | D29 |
| Rollback for migration 37, run on the local stack and re-applied | rollback | |

## 2.20.0 — 2026-11-16 (pm-v1.1: the mapping for taxonomy v2)

| Change | Files | Why |
|---|---|---|
| Seed of pm-v1.1: the same 17 requirements and the same 129 requirement–source links (to the pm-v1 sources; no new sources). Mapping changes only: C4 transfers from `business_case_economics` only with `tracks_outcomes`; C5 adds `executive_communication`; X5, X6 and X7 map directly to `business_case_economics`, `ai_product_work` and `data_governance_quality`; L1 needs `multi_product_scope`; L2 needs `coaching_mentoring`; L3 maps directly to `executive_communication`; L4 maps directly to `delivery_execution` with `cross_team`. pm-v1 is kept unchanged for audit | migration 36 (generated by `scripts/build_knowledge_pack.py` from `knowledge_packs/pm-v1.1.json`) | Phase 3.1, approved 2026-11-16 |
| `tests/test_knowledge_pack.py`: pm-v1.1 reuses the pm-v1 inventory, keeps requirements and evidence, and carries exactly the approved conditions. pgTAP in `trace_taxonomy_v2.test.sql`. Rollback for migration 36, run on the local stack and re-applied | tests, rollback | |

## 2.19.0 — 2026-11-16 (evidence-specific area conditions)

| Change | Files | Why |
|---|---|---|
| `trace_capability_requirements.area_conditions` (jsonb, default `{}`): an area counts for a requirement only when the user answered that area's scope question "yes". `trace_util.check_capability_codes` also validates conditions: a condition names a mapped area and one of that area's questions (TR025) | migration 35 | Phase 3.1: L1, L2, L4 and C4 need specific evidence, not just the area |
| pgTAP in `trace_taxonomy_v2.test.sql`. Rollback for migration 35, run on the local stack and re-applied | tests, rollback | |

## 2.18.0 — 2026-11-16 (responsibility taxonomy v2 and versioned classifications)

| Change | Files | Why |
|---|---|---|
| `trace_responsibility_areas`: `examples`, `introduced_in_version`, `definition_version`, `qualifiers` (scope questions with their own version). Four new areas (version 2): `ai_product_work`, `data_governance_quality`, `business_case_economics`, `executive_communication`. Four scope questions: `strategy_portfolio.multi_product_scope`, `people_leadership.coaching_mentoring`, `delivery_execution.cross_team`, `business_case_economics.tracks_outcomes`. `trace_util.current_taxonomy_version()` = 2 | migration 34 | Phase 3.1 |
| `trace_candidate_evidence`: `area_classification_none` ("None of these areas", stored with an empty area list and the text the user saw, so editing the text makes it stale), `area_classification_taxonomy_version`, `area_qualifiers` (answered questions only; an unanswered question is absent, never "no") and `area_classified_at`. Existing classifications are kept and not rewritten: no version means version 1 | migration 34 | A taxonomy change never silently re-labels anyone's evidence |
| `trace_review.classify_evidence_v2` and `trace_review.clear_evidence_classification`: writer-owned SECURITY DEFINER, EXECUTE for `trace_app_user` only, request identity, owner's non-rejected evidence only (TR001, TR005), only the text the user saw (TR018), validated areas and answers (TR006) | migration 34 | |
| pgTAP: new `trace_taxonomy_v2.test.sql` (32 assertions); review-enforcement and classification inventories updated. Rollback for migration 34, run on the local stack and re-applied | tests, rollback | |

No user data changes.

## 2.17.0 — 2026-11-16 (the approved pm-v1 knowledge pack)

| Change | Files | Why |
|---|---|---|
| Seed of the approved PM knowledge pack. 15 sources: F1, F3 and F4; J1–J11 current; J12 expired as dated eligibility evidence. 17 canonical requirements with the transferability mapping, 129 requirement–source evidence rows and 18 employer-specific criteria. **SFIA excluded (D16)** | migration 33 (generated by `scripts/build_knowledge_pack.py` from `knowledge_packs/pm-v1.json`) | Product Phase 3 (D12, D13, D16–D19, D22–D26). Curated public content: no user data, no synthetic data |
| `tests/test_knowledge_pack.py`: the migration matches the JSON (`--check`), and the JSON keeps the approved rules. Rollback for migration 33, dry-run tested | tests, rollback | |

## 2.16.0 — 2026-11-16 (role-agnostic target-role knowledge)

| Change | Files | Why |
|---|---|---|
| `trace_target_role_sources`: knowledge-pack columns (key, kind, publisher, geography, licence, attribution, level as stated, context tags, last verification, revalidation date, status note) and, for job postings only, the entry-background stance with accepted or excluded backgrounds, a short quotation (at most 400 characters) and its locator | migration 32 | D23, D26 |
| `trace_capability_requirements`: canonical requirements (core, context-dependent, Senior PM differentiators) with the curated transferability mapping; taxonomy codes checked (TR025). `trace_requirement_source_evidence`: many-to-many with stance (supports, qualifies, disagrees, silent) | migration 32 | D13, D19 |
| `trace_util.posting_effective_status` (a current posting reads `current_check_required` after its revalidation date) and `trace_util.record_posting_verification` (administrators only: open, closed, withdrawn, inaccessible, ambiguous; an unclear check is never "closed", TR024) | migration 32 | D26 |
| Signed-in users read knowledge; nobody else writes it. Withdrawn sources and their evidence are hidden. pgTAP: `trace_target_role_knowledge.test.sql` (41 assertions). Rollback for migration 32, dry-run tested | tests, rollback | |

The existing `trace_target_role_requirements` table holds the employer-specific criteria (unchanged schema).

## 2.15.0 — 2026-11-16 (ordering events: a role's position is not its content)

| Change | Files | Why |
|---|---|---|
| `public.trace_candidate_order_events`: append-only (TR023). Holds old and new position, review status at the move, cause, the role that caused it, actor, time and transaction. Owner-readable; written only by the writer | migration 31 | Decision D10: moving a confirmed role (for example when an earlier role is added) must not make it "Corrected" |
| `trace_review.move_employment`: writer-owned SECURITY DEFINER; callable by `trace_app_user` only. Moves one role (never a rejected one, TR001), writes the event, changes `sequence_index` only | migration 31 | Position-only changes have their own audited path |
| `trace_util.guard_review_row`: new branch (f) lets the writer change exactly `sequence_index` with the matching event in the same transaction. `trace_util.require_correction_history`: a position-only move of an already corrected role is backed by its event. Both functions are otherwise unchanged (re-stated from the live definitions) | migration 31 | Every other review, content and correction rule is preserved |
| pgTAP: new `trace_candidate_order_events.test.sql` (24 assertions). The review-enforcement inventories (triggers, `trace_review` functions, EXECUTE matrix) now list the new objects. Rollback script for migration 31, run on the local stack: it restores both guards exactly | tests, rollback | |

No data changes. Earlier sequence corrections, if any, stay as true history.

## 2.14.0 — 2026-11-15 (profile update proposals: one pipeline for changes the user accepts)

| Change | Files | Why |
|---|---|---|
| `public.trace_profile_update_proposals`: open proposals (new items, or possible updates of an existing employment or qualification) from pasted text or a later resume. Owner-only RLS; the browser reads only; `trace_app_user` inserts and decides through column grants; one open proposal per content fingerprint; proposed content cleared on decision; final decisions; named records must belong to the same profile (`trace_util.guard_update_proposal`, TR022) | migration 30 | A new resume or pasted text must never silently change a profile, and the same source twice must never duplicate it (product Phase 2, decisions D5 and D7). One proposal path also serves voice and the advisor later |
| pgTAP: new `trace_profile_update_proposals.test.sql` (25 assertions). Rollback script for migration 30, tested on the local stack | tests, rollback | |

Ingestion still never merges (TR015 unchanged). No data changes. The 18 participant resumes are still not present and
were not imported.

## 2.13.0 — 2026-11-15 (exclude a user's own contribution from their own evidence)

| Change | Files | Why |
|---|---|---|
| `trace_contrib.my_reference_profile_ids()`: the caller's own contributed reference profile ids (owner role, request identity, read-only; no other grant) | migration 29 | The user-to-contribution link exists only in admin-only `trace_private`; the backend needs it, server-side, to keep a contributor's own profile out of their own route evidence and comparisons (no circular evidence). Never returned by the API; no new link in `trace_ref` |
| pgTAP: new `trace_own_reference_exclusion.test.sql` (6 assertions). Rollback script for migration 29, dry-run tested | tests, rollback | |

No data changes. The 18 participant resumes are still not present and were not imported.

## 2.12.0 — 2026-11-14 (sharing preference, separate from the processing authority)

| Change | Files | Why |
|---|---|---|
| `trace_private.sharing_preferences` (`shared` or `private`, source `onboarding_default` or `settings`, notice version) and the append-only `sharing_preference_events`; no client grants; deleted with the account | migration 28 | The product's default-on sharing preference and the Profile settings choice, recorded with history, without being a consent decision |
| Owner-role entry points `trace_contrib.my_sharing_preference`, `acknowledge_data_use_notice` (creates the default only when no preference exists; never overrides; records no consent) and `set_sharing_preference` (`private` excludes the user from the shared pool at once through `trace_private.exclude_from_shared_pool`, the same removal as a consent withdrawal; `shared` re-queues only if an explicit grant is active) | migration 28 | Switching to private stops sharing immediately; nothing is ever re-shared silently |
| `trace_private.contribution_consent_active` now also requires that the preference is not `private` | migration 28 | A private preference blocks queueing, processing and the eligible state whatever grants exist. Users without a preference row behave exactly as before |
| pgTAP: new `trace_sharing_preference.test.sql` (17 assertions). Rollback script for migration 28 (deletes preferences; see its header for users who chose private), dry-run tested | tests, rollback | |

The default preference is not a processing authority: an explicit contribution grant is still required (see
backend/docs/SHARING_PREFERENCE.md). The 18 participant resumes are still not present and were not imported.

## 2.11.0 — 2026-11-13 (identity-marker encryption, marker lifecycle, queue-event retention)

| Change | Files | Why |
|---|---|---|
| `identity_markers.sealed` (AES-256-GCM blob, sealed by the backend) and `key_id` (key fingerprint); a row is legacy plaintext or sealed, never both; `store_identity_markers(bytea, text)` replaces the plaintext version and clears plaintext columns; `contribution_identity` returns the blob | migration 27 | The database and its backups never hold readable identity markers |
| `identity_markers_needed`, `trace_contrib.forget_identity_markers` (runtime, own, never needed markers), `trace_private.purge_orphan_identity_markers` (admin, grace period of at least 1 hour, dry run by default) | migration 27 | Markers of failed or abandoned ingestion are removed; markers of an active profile never are |
| `trace_private.purge_contribution_queue_events` (admin, at least 7 days, dry run by default) | migration 27 | Bounded retention for queue events |
| pgTAP: definer invariant uses the new store signature; v2.10.0 tests store a sealed blob; new `trace_marker_protection.test.sql` (12 assertions). Rollback script for migration 27 (deletes sealed rows: they cannot be decrypted without the key), dry-run tested together with migration 26 | tests, rollback | |

The 18 participant resumes are still not present and were not imported.

## 2.10.0 — 2026-11-13 (contribution safety: identity-marker continuity, bounded retries)

| Change | Files | Why |
|---|---|---|
| `trace_private.identity_markers` (names, emails, phones, links, handles per user; bounded; deleted with the account or the private profile) written only through `trace_contrib.store_identity_markers` (runtime-only, request-identity scoped, own non-fixture profile); `contribution_identity` now returns the account identity merged with the stored markers and `markers_recorded` | migration 26 | Every contribution rebuild can detect the owner's identifiers in edited text, also for email/password accounts without a name |
| Queue: `next_attempt_at`, `lease_token`, `last_error` (class code only), `failed_at`; `claim_contribution_requests` takes max attempts and backoff (expired leases count as an attempt; terminal at the limit), `report_contribution_failure` (exponential backoff, stale tokens ignored), `release_contribution_requests` (graceful stop, not counted); `trace_private.retry_contribution` (admin only, named operator); `contribution_queue_events` audit; a new request resets the series; terminal failure suspends the existing contribution | migration 26 | No unbounded retry loop; no lost work after a restart |
| `record_contribution_outcome` and suspension share `trace_private.suspend_contribution`; checks unchanged | migration 26 | |
| pgTAP: definer invariant names four read-only functions (`store_identity_markers` checks ownership only); new `trace_contribution_safety.test.sql` (20 assertions). Rollback script for migration 26, dry-run tested | tests, rollback | |

The 18 participant resumes are still not present and were not imported.

## 2.9.0 — 2026-11-13 (a user's own resume, automatic contributions)

| Change | Files | Why |
|---|---|---|
| `trace_ingest.deidentified_resume_admissions` + `admit_deidentified_resume` (runtime-only, request-identity scoped, non-fixture only) + `start_real_extraction` (provider `trace-resume-parser`); `assert_ingestion_allowed` accepts the unchanged fixture pair OR an admission; `ingest_fixture_extraction` accepts either provider (one line). Consent, never-merge and pending-draft rules unchanged | migration 22 | A user's own resume enters their private profile only after local de-identification passed |
| Durable per-user contribution queue, triggers on candidate tables and consent events, user-visible states, review approvals bound to a record hash, resume quarantine and processing log (all in `trace_private`); entry points in the function-only schema `trace_contrib` (runtime: claim, context, identity, record outcome, log, quarantine; owner role: own state and own processing outcome) | migration 23 | Automatic, idempotent contribution with no administrator step for ordinary users |
| `record_contribution_outcome` re-checks consent, fixture status and residual emails/links; one admitted contribution per contributor (unique index); updates in place on content change; suspends on failure; withdrawal records the state and drops queued work | migration 23 | Never trusts the caller; no duplicate person in the cohort |
| Deleting an account erases its contributor, contributions and reference rows | migration 24 | No orphaned reference data |
| A profile whose upload needed manual review keeps needing review for each contributed content | migration 25 | The owner's name is not known later; the ambiguity cannot be re-detected otherwise |
| pgTAP: candidate-table trigger list and the definer invariant (three named read-only contribution functions); ingestion lists; new `trace_real_resume_and_contributions.test.sql` (18 assertions). Rollback scripts for migrations 22–25, dry-run tested together with 19–21 | tests, rollback | |

The 18 participant resumes are still not present and were not imported.

## 2.8.0 — 2026-11-13 (restricted runtime identity, consented real reference data, shared rate limits)

| Change | Files | Why |
|---|---|---|
| Roles `trace_app_runtime` (LOGIN; no superuser, BYPASSRLS, CREATEROLE or CREATEDB; owns nothing), `trace_app_user` (NOLOGIN owner-session role; member of `authenticated` inherit-only) and `trace_reference_reader` (NOLOGIN, SELECT-only on the reference cohort, lookups and the fixture allowlist). The runtime may SET `trace_app_user` without inheriting it and inherits only the reader. Password set outside migrations | migration 19 | The backend no longer runs as the table owner |
| Every INSERT/UPDATE/DELETE grant on private tables and every writer entry point moved from `authenticated` to `trace_app_user`; `authenticated` keeps SELECT on its own rows; PostgREST's authenticator cannot reach `trace_app_user`; direct storage upload policy dropped. Grants on writer-owned functions are changed by their owners (a postgres revoke would silently do nothing) | migration 19 | No write path through the Data API; all writes go through the Trace API |
| `trace_ingest.designate_fixture` (definer, ingest-writer owned, request-identity scoped, allowlist-checked, runtime-only) replaces the table-owner UPDATEs | migration 19 | Fixture designation without owner privileges |
| SELECT-only policies for `trace_reference_reader` on `trace_ref` tables and lookups (the static rule now allows only these) | migration 19, `test_sql_static.py` | The restricted runtime can read the reference cohort |
| `trace_private` schema (contributors with opaque participant references, consent attestations with evidenced dates only, contributions, import log); `trace_ref.eligible_real_profile_ids()` and a new `v_eligible_profiles`; real profiles need a registered contribution or the original consent link; withdrawal trigger on contribution consent events and `trace_private.withdraw_participant` | migration 20 | Consented real reference data with the identity mapping outside the analytical data |
| Retained career detail on reference rows: education institution, qualification title and grades; employer name; `trace_ref.activities` (certifications, projects, awards) | migration 20 | De-identification removes identifiers, not career facts |
| `trace_ops.rate_limit_counters` and `consume_rate_limit` (atomic, sorted-key locking, refused requests not counted, runtime-only) | migration 21 | Shared backend rate limits across workers |
| Local Auth minimum password length 8; `[auth.external.google]` unchanged (disabled) | `config.toml` | Matches the UI; beta settings documented separately |
| pgTAP: owner-session and privilege expectations moved to `trace_app_user`; `trace_ingest` lists `designate_fixture`; new `trace_runtime_and_real_reference.test.sql` (25 assertions). Rollback scripts for migrations 19–21, each dry-run tested | tests, rollback | |

No taxonomy, alias, fixture or synthetic reference row changed. The 18 participant resumes are not present and were not imported.

## 2.7.0 — 2026-11-12 (candidate classification of evidence responsibility areas, M6)

| Change | Files | Why |
|---|---|---|
| `trace_review.classify_evidence_areas`: the candidate classifies their own evidence (extracted or entered) with one or more taxonomy responsibility areas; basis `candidate_selected`. Writer-owned SECURITY DEFINER, empty `search_path`, fixed SQL, `authenticated` only, owner-scoped, row-locked; refuses rejected evidence (TR001), another text (TR018), empty, NULL or unknown areas (TR006); de-duplicates and sorts. Never changes review, verification, provenance, text, links or employments. | migration 18 | Extracted evidence carried no responsibility areas and could not be corrected (taxonomy); later fit needs the candidate's own classification. |
| Evidence columns `area_classification`, `area_classification_basis` (CHECK `candidate_selected`), `area_classified_text` and generated `area_classification_is_stale`, with CHECKs keeping them consistent. Writer UPDATE on the three columns only. | migration 18, `ACCESS_CONTROL.md` | Provenance; a text change makes a classification stale instead of silently keeping it. |
| New pgTAP suite `trace_candidate_evidence_classification.test.sql` (47 assertions); M2 suite expectations list the new function; static tests. Rollback script: locks evidence, requires an acknowledgement, drops classifications. | tests, `supabase/rollback/` | |

No taxonomy, alias, fixture, `trace_ref` object or M1–M5 rule changed.

## 2.6.0 — 2026-11-12 (trusted role mapping and candidate role decisions, M5)

| Change | Files | Why |
|---|---|---|
| `trace_review.apply_alias_mapping`: automatic mapping from `trace_role_aliases`, re-checked by the database (exact normalised match, not context-required, a single alias row, active and resolved role); role, seniority hint and confidence from the alias row; status `auto_high_confidence`. Never replaces a candidate decision or a mapping set outside the functions. | migration 17 | Candidate employments stayed `ROLE-UNKNOWN`, so no candidate history could produce a confirmed transition. |
| `trace_review.choose_employment_role`: the candidate's own decision from any active, resolved role; status `confirmed`, no confidence; Product Owner-family titles, including qualified ones such as "Product Owner (Payments)" (`trace_util.is_product_owner_title`, word-based), need a PM-ownership responsibility area for any product-management role (TR020); `apply_alias_mapping` never maps them to product management. | migration 17 | Context-required and unmatched titles need a decision; the data dictionary's Product Owner rule is enforced. |
| Employment columns `role_mapping_basis`, `role_mapping_alias_id`, `mapped_title_raw`, `role_mapping_ownership_areas` and generated `mapping_is_stale`; `mapping_confidence` nullable (only for candidate decisions); CHECK constraints keep them consistent. | migration 17 | Mapping provenance; a title change (draft edit or M4 correction) makes the mapping stale instead of silently keeping it. |
| Both functions: writer-owned SECURITY DEFINER, empty `search_path`, fixed SQL, `authenticated` only, owner-scoped, row-locked, refuse rejected records, never change `review_status`, refuse a title other than the record's (TR018). Writer UPDATE on the mapping columns only; lookup read policies for the writer. G0, G1 and the M4 triggers unchanged. | migration 17, `ACCESS_CONTROL.md` | Same security pattern as M2–M4. |
| Title normalisation contract v1: `trace_util.normalize_title` (29 listed whitespace code points to one ASCII space, trimmed, ASCII-only lower-casing; locale-independent), identical in the backend, with a CHECK on `trace_role_aliases` that `alias_normalized` follows it. | migration 17 | The database and backend agree on every title; nothing is folded by the database locale. |
| New pgTAP suite `trace_candidate_role_mapping.test.sql` (97 assertions); M2 and M4 suite expectations list the new functions and the writer's `role_id` grant; static tests. Rollback script: locks employments, requires an acknowledgement, resets M5 mappings to unmapped. | tests, `supabase/rollback/` | |

No alias, taxonomy, fixture, `trace_ref` object, pathway, consent or ingestion rule changed. The SYN-011 fixture discrepancy ("Project Manager" curated as `ROLE-PGM`, alias `ROLE-PROJ`) is documented, not corrected, pending source verification.

## 2.5.0 — 2026-11-12 (corrections with append-only correction history, M4)

| Change | Files | Why |
|---|---|---|
| `trace_review.correct_record(kind, profile, record_id, changes)`: owned by `trace_review_writer`, SECURITY DEFINER, empty `search_path`, fixed per-kind allow-lists and UPDATE statements, executable by `authenticated` only. Corrects scalar content of employments, education, activities, evidence, skills and career breaks; the record becomes `user_corrected`. Rejected records must be reinstated first; pending `user_stated` drafts are still edited. Employers, links and taxonomy references are not correctable. | migration 16 | Candidates could confirm or reject extracted records but not fix them (D3). |
| `public.trace_candidate_corrections`: append-only history (previous and corrected values of the changed fields, prior status, time, author, transaction). Inserted only by `trace_review_writer` through `correct_record`: client roles have no INSERT privilege, and an insert guard rejects any other role, including the table owner (TR016). No UPDATE, TRUNCATE or direct DELETE for any role including the owner (TR016); rows go only with their record, profile or account (per-kind generated foreign keys). The guards are triggers: the table owner can still disable them (administrative access). Previous values, possibly sensitive extracted content, are kept until the record, profile or account is deleted. | migration 16 | The original value of every corrected field stays recoverable, and history cannot be fabricated or backdated through SQL. |
| G1 accepts content changes on non-draft rows only as the writer together with `user_corrected`. A deferred constraint trigger requires a matching history entry (same record, profile, owner, transaction and values) for every correction (TR017). It does not consume entries (not one-to-one); `correct_record` writes exactly one per update. | migration 16 | No correction without history, even if a future path forgets it. |
| Unique `(id, profile)` keys on skills and career breaks (for the history foreign keys). | migration 16 | Additive. |
| Lock order of a correction: record row, then (history foreign-key checks) profile and account rows. A correction racing the deletion of its own profile or account can deadlock; PostgreSQL aborts one transaction and commits nothing of it. | migration 16, `ACCESS_CONTROL.md` | Documented; no partial write is possible. |
| New pgTAP suite `trace_candidate_corrections.test.sql` (59 assertions); M2 suite expectations list the new function and triggers; static tests for the migration and the rollback's lock order. Rollback script: locks the six record tables and history before its acknowledgement check, and refuses to discard history (permanently, including original values; corrected records keep their latest values) without an explicit acknowledgement. | tests, `supabase/rollback/` | |

No `trace_ref` object, fixture, pathway, consent or ingestion rule changed.

## 2.4.0 — 2026-11-12 (trusted ingestion of extracted drafts, synthetic fixtures only, M3)

| Change | Files | Why |
|---|---|---|
| New schema `trace_ingest` (not exposed) with `start_fixture_extraction`, `ingest_fixture_extraction` and `fail_fixture_extraction`. They are owned by the new role `trace_ingest_writer` (NOLOGIN, NOINHERIT, NOBYPASSRLS, no members), SECURITY DEFINER with an empty `search_path`, executable by `authenticated` only, act for `auth.uid()`, and use the M2 ownership-transfer procedure. | migration 15 | Extracted records need one trusted write path; M2 deliberately blocks every client role from inserting extracted provenance, and the table-owner connection is not a write path. |
| Ingestion inserts `source_extracted` / `pending_review` / `unverified` drafts with source resume, extraction run, source location and extraction confidence; employers are `resume_stated`; mappings stay unresolved. The payload is allow-listed (TR006). It is limited to synthetic test-fixture profiles and resumes bound to the result (TR012), needs current approved `resume_processing` consent at start and at ingestion (TR013), is atomic, never merges into a profile holding records (TR015), and is idempotent by result hash (TR014). | migration 15 | M3 scope. |
| The insert guard `trace_util.guard_candidate_insert` (G0) accepts the extracted-draft shape from `trace_ingest_writer` only; it is unchanged for every other role. | migration 15 | Narrow extension of M2. |
| `trace_extraction_runs.result_sha256`; at most one succeeded and one open run per resume. | migration 15 | Idempotency. |
| Fixture allowlist `trace_ingest.approved_fixture_resumes`: the 41 (fixture id, PDF SHA-256) pairs, derived by `build_seeds.py` from the PDF archive (also `synthetic_resume_pdf_hashes.csv`) and loaded by the synthetic seed only. Ingestion requires the exact pair (TR012). `service_role` can no longer insert or update `trace_resume_documents.is_test_fixture` or update `content_sha256`; its other resume privileges are unchanged. | migration 15, `scripts/build_seeds.py`, seed | The database, not the caller, decides which document is an approved fixture. |
| New pgTAP suite `trace_fixture_ingestion.test.sql` (70 assertions); static tests for the migration; writer policies of either writer role may use `trace_review.request_uid()`. Rollback script `supabase/rollback/…160000….rollback.sql`. | tests, `supabase/rollback/` | |

No `trace_ref` object, fixture, pathway or consent rule changed. No consent document is added; tests add synthetic test-only documents inside their own transactions or clean them up.

## 2.3.0 — 2026-11-12 (database-level review enforcement for candidate profiles, M2)

| Change | Files | Why |
|---|---|---|
| Review status changes only through `trace_review.review_record` (confirm, reject, reinstate, the M1 matrix) and an employment's employer only through `trace_review.reassign_employment`. Both are owned by the new role `trace_review_writer` (NOLOGIN, NOINHERIT, NOBYPASSRLS, no members), are SECURITY DEFINER with an empty `search_path`, act only for `auth.uid()` (through the postgres-owned `trace_review.request_uid()`) and lock in the M1 order. `trace_review` is not exposed. | migration 14 | The M1 rules were backend guardrails only; `authenticated` could write `review_status` and mapping columns directly. |
| Column grants for `authenticated` and `service_role` cover candidate-authored content only. Review status, mapping, taxonomy, provenance, verification, extraction and legacy trust columns, primary keys, timestamps and profile confirmation cannot be written. Table-level INSERT/UPDATE (employers) and service_role's TRUNCATE, REFERENCES, TRIGGER and MAINTAIN are revoked. An insert guard rejects forged system values from any non-owner role (TR011). | migration 14, `ACCESS_CONTROL.md` | Ordinary inserts must always be pending, user-stated, unverified drafts. |
| Guard triggers: content edits only on the owner's pending `user_stated` drafts (TR001, TR002); employer edits follow the M1 lock protocol; review-status changes only from the writer along the matrix (TR003). | migration 14 | Confirmed and rejected content must not change through ordinary SQL. |
| Deletion (decision A): evidence and skill links to employments, activities, education and evidence change from ON DELETE CASCADE to `SET NULL (link)`. Deleting a parent is refused when a confirmed or corrected dependent would be deleted or detached (TR008); pending and rejected dependents are detached intact; a detachment that would duplicate a skill fails (TR010). A resume backing `source_document_supported` records cannot be deleted (TR009). Profile and account deletion still remove everything; cascades are recognised by depth, table-owner identity, the exact action and the parent being gone, never by depth alone. | migration 14 | A reviewed record must never disappear or lose its relationship silently. |
| Decisions B and C: `skills.evidence_source`, `evidence.provenance` and `employers.attribute_provenance` are nullable; new candidate rows leave them NULL. Nothing is derived as `participant_confirmed`. Existing values are not rewritten (upgrade checksum test). | migration 14, data dictionary §10a | Entering a record is neither reviewing nor verifying it. |
| New pgTAP suite `trace_candidate_review_enforcement.test.sql` (122 assertions); `trace_candidate_profile.test.sql` adjusted (review via the function; the `review_status` grant assertion inverted). Static tests for the new migration; writer policies may use `trace_review.request_uid()`. Forward-compensation script `supabase/rollback/…rollback.sql`, rehearsed. | tests, `supabase/rollback/` | |

No `trace_ref` object, reference fixture, pathway rule or consent rule changed. The backend change ships with this migration: the M1 direct status write no longer works.

## 2.2.0 — 2026-11-11 (candidate-side structured career profile storage; additive)

| Change | Files | Why |
|---|---|---|
| New `public.trace_candidate_career_activities` covers certifications, licences, courses, independent and portfolio projects, awards, publications, research, volunteering, extracurricular and leadership activities. Structured common fields, type-specific credential and expiry fields, partial dates, an optional link to an employment episode of the same profile, and supplementary `metadata` only. | migration 13 | Supporting records had no structured home and must never be forced into employment episodes. |
| Education keeps the original qualification title (`qualification_title_raw`) and `notes`. | migration 13 | The normalised `qualification_level` must not replace what the candidate stated. |
| Skills can link to education, activity or evidence rows as well as employment (at most one per row, same profile). Unmapped skills (`skill_id` null, `skill_text_raw` required) and `stated_proficiency_raw` (never with an `inferred` origin) are added. The v2.0 uniqueness key is replaced by an equivalent index covering the new links. | migration 13 | Trace why a skill is in a profile, without implying proficiency. |
| Achievements and other evidence can attach to an activity (never to both an activity and an employment). | migration 13 | Achievements of projects and awards. |
| `information_origin`, `review_status`, `verification_status`, `extraction_confidence`, `source_resume_id`, `extraction_run_id` and `source_location` on employments, education, career breaks, evidence, skills and activities. Source references are owner-scoped composite FKs. Existing rows are backfilled from legacy columns; legacy columns and `raw_extraction_json` are kept. | migration 13 | Separate stated / extracted / inferred origin from the user's review and from verification. Provenance is a document reference plus a location; no resume excerpts are stored. |
| Clients can no longer write backend-managed provenance or verification columns. Table-level INSERT/UPDATE on candidate record tables is replaced by column-level grants. This also stops clients self-asserting legacy `evidence_status = 'document_verified'` and `skills.verified`. | migration 13, `ACCESS_CONTROL.md` | Security fix: self-asserted verification must not be trusted. |
| New pgTAP suite `trace_candidate_profile.test.sql` (58 assertions); static tests for additivity, owner-only access, ownership-safe links and separation from `trace_ref`; data dictionary §10a. | tests, docs | |

No `trace_ref` object, reference fixture, pathway rule or consent rule changed. Candidate records never enter reference analytics.

## 2.1.0 — 2026-11-11 (episode-aware career pathways; additive)

| Change | Files | Why |
|---|---|---|
| New migration `…130000_trace_episode_pathways.sql` adds `trace_ref.v_episode_transitions`, `v_career_episode_stints`, `v_career_episode_paths`, `assert_valid_episode_route()`, `episode_route_observations()` and `episode_route_timeline_summary()`. Pathways are now sequences of employment episodes (role + employer). Consecutive records collapse only when the role is unchanged and the employer is unchanged or unknown. Every transition gets a `change_type`. | migration 12; `trace_model.py` (`change_type`, `episode_transitions`, `career_episode_paths`, `validate_episode_route`, `episode_route_observation`, `episode_route_summary`); `tests/test_episode_pathways.py`; `supabase/tests/database/trace_episode_pathways.test.sql`; static SQL tests; data dictionary §6a | `v_career_stints` merged consecutive same-role records whatever the employer, so confirmed same-role employer moves (SYN-018, 019, 025, 026, 027, 029, 030, 032) never became routes. |
| Episode route functions reject invalid routes with SQLSTATE `22023`: null, fewer than two steps, multi-dimensional, null step, or unknown/unresolved role id. Routes are never collapsed. | migration 12 | `route_timeline_summary(['PM-PM','PM-PM'])` silently returned a zero-count row for `['PM-PM']`. |
| Existing objects are unchanged. `v_career_stints`, `v_career_paths`, `route_observations` and `route_timeline_summary` keep their v2.0.2 behaviour, which is asserted in the tests. | — | Additive change: no consumer's behaviour changes silently. |
| The lookup-ordering static test now checks its intent (no migration after the lookup-data migration creates a table or type) instead of requiring that migration to be last. | `tests/test_sql_static.py` | Migrations already applied must not be reordered; new view/function migrations come after it. |
| `build_seeds.py` excludes Supabase CLI local state (`supabase/.temp/`, `supabase/.branches/`) from the manifest, with a regression test. | `scripts/build_seeds.py`, `tests/test_packaging.py` | After `supabase start`, the manifest picked up `supabase/.temp/start-secrets/…/docker.env`. Because `package_review.py` zips exactly the manifest's files, regenerating the manifest would have put local credentials into the shareable review ZIP. |

Integrity, consent, eligibility and evidence-mode rules are inherited unchanged from `v_role_transitions` and `v_mode_profiles`. No tables, columns, grants on existing objects or synthetic fixtures changed.

## 2.0.2 — 2026-11-11 (date-handling decision; not applied to any database)

| Change | Files | Why |
|---|---|---|
| `boundary_uncertain` is no longer confirmable. When the previous role's year-only end and the next role's year-only start are the same year, the transition is unconfirmed (`timing:boundary_uncertain`). Both employment records and the profile are kept, and other transitions in the profile are unaffected. When the end year is earlier than the start year, the transition is `sequential` and confirmed, but `gap_is_precise` and the route's `is_precise` stay false, so it never enters precise percentiles. | `scripts/trace_model.py`, migration `…120700_trace_analytics_views.sql`, `tests/test_transitions.py`, pgTAP tests, data dictionary | Chronological order between two same-year, year-only records cannot be established reliably. |

## 2.0.1 — 2026-11-11 (targeted review fixes; not applied to any database)

| Change | Files | Why |
|---|---|---|
| Consent provenance enforced in the database: trigger `enforce_consent_integrity` on `trace_ref.profiles` requires `source_consent_event_id` to be a **granted `deidentified_shared_dataset` event of the same `contributor_user_id`** | migration `…120400_trace_reference_cohort.sql`; pgTAP tests | Previously only the foreign keys existed, so a profile could cite another user's or another purpose's event. Current-consent eligibility (`v_eligible_profiles`) is unchanged, so withdrawal still excludes the profile immediately. |
| One-month overlaps are no longer auto-confirmed as a "handover". Any overlap beyond the shared boundary month is `overlapping` and stays unconfirmed until explicitly verified (dates corrected, or the concurrent role marked parallel with a note). `max_boundary_overlap_months` is retired. | `trace_model.py`, migration `…120700_trace_analytics_views.sql`, `analytics_settings.csv`, lookup-data migration (regenerated), validator, tests, data dictionary | A presumed notice period is an inference, not evidence. Same-month boundaries remain accepted. |
| Founder roles no longer count as full-time experience by default. They need an explicit `counts_as_full_time_experience = true` with a `classification_note`. SYN-040's founder role now carries that documented decision. | `trace_model.py`, analytics views, SYN-040 in `synthetic_profiles.jsonl` (and its generated copies), validator, tests, data dictionary | Founder activity ranges from full-time to a side project; it should not enter a confirmed sequence without review. |
| The manifest no longer lists restricted files. It is static with respect to `local_only/`, so it verifies identically in a review ZIP that excludes that folder. New `scripts/package_review.py` builds the review ZIP from the manifest, scans every shareable file for restricted identifiers and copied restricted text, and re-verifies the ZIP without `local_only/`. The validator runs the same scan. | `build_seeds.py`, `package_review.py`, validator, `tests/test_packaging.py`; restricted-fixture specifics removed from this changelog; the local regression test no longer hard-codes fixture ids | v2.0.0's manifest listed restricted filenames and changed when `local_only/` was removed. |

## 2.0.0 — 2026-11-11 (for review; not applied to any database)

A full revision of Trace Data Foundation v1, following the v1 audit. The original v1 package is preserved unchanged in `../data-foundation-v1-backup/`.

### Security and privacy

| Decision | Why |
|---|---|
| Reference cohort, analytics views, settings, snapshots and audit log moved to a new `trace_ref` schema that is not exposed through the Data API; RLS enabled there with no policies | v1 left shared career histories and views in `public`, where Supabase's default grants would have exposed them. Defence in depth: even if the schema were exposed by mistake, RLS would return nothing. |
| All views `security_invoker = true` | v1 views ran with the owner's privileges and bypassed RLS. |
| Migration 09 revokes all default `anon`/`authenticated` privileges on Trace tables, then grants back the minimum; RLS enabled on **every** table | v1 left four lookup tables without RLS, so they were writable by anyone with the public API key. |
| Lookups readable by `anon`/`authenticated` through explicit read-only policies | The app needs the taxonomy before sign-in; nobody needs to write it. |
| Owner-only policies with `(select auth.uid())`, plus composite FKs `(candidate_profile_id, owner_user_id)` on every child table | v1 let a user attach a run or resume to another user's profile. The composite key makes that structurally impossible. |
| Column-level grants for system-managed fields (`is_test_fixture`, `extraction_status`, `raw_extraction_json`, …); clients cannot read `reference_profile_id` in pathway evidence | Users can edit their own data but cannot relabel it as fixture data, forge extraction state or learn reference-profile pseudonyms. |
| Private `trace-resumes` bucket with owner-folder policies and no update policy; path rule duplicated as a CHECK; MIME and size limits | v1 had no storage design. |
| Retention: `retention_until` defaults to 30 days (capped at 365); a deletion view exists for the backend job; deletion goes through the Storage API | Implements the v1 consent draft's retention recommendation. |
| `trace_ref.access_log` (append-only) and `trace_extraction_runs` (provider, model, prompt version, region, consent event) | The intake document required access logs and provider disclosure tracking; v1 had neither. |
| No functions in `public`; security-definer functions pin `search_path = ''` | Avoids accidental RPC exposure and search-path hijacking. |

### Consent

| Decision | Why |
|---|---|
| Profile-level consent flags removed; consent is an append-only event log (`trace_consent_events`) per user and purpose, referencing a versioned `trace_consent_documents` row | v1 eligibility trusted flags copied onto profiles, and `subject_ref` had no foreign key, so withdrawal could not take effect. |
| `trace_ref.profiles.contributor_user_id` and `source_consent_event_id` link each real profile to its contributor and grant | Satisfies "link consent records to actual profiles through foreign keys" while keeping the link service-only. |
| Eligibility = latest `deidentified_shared_dataset` event is `granted` **and** its document is counsel-`approved` **and** the profile is participant- or reviewer-confirmed | Consent is purpose-specific and active; the drafts are not legal instruments, so nothing is eligible until counsel approves. |
| No consent documents or events are seeded | Avoids anyone treating the drafts as approved. |

### Career-history model

| Decision | Why |
|---|---|
| Structured candidate tables (employers, employments, education, career breaks, evidence, skills) mirror the reference tables; `profile_json` became an audit-only `raw_extraction_json` | v1 held the user's history only as JSON, which cannot be matched against reference histories. |
| New `employers` entity with industry sector, company model (product / IT services / consulting / agency / GCC / …), business model and size band | The services-to-product move is a key Indian PM pathway; v1 had only free-text industry. |
| New `education` and `career_breaks` tables with dated bounds | Full-time MBAs and breaks explain gaps; v1 had only an education summary string. |
| Responsibilities are evidence rows with a controlled `responsibility_area`; four areas are PM-ownership signals | Makes the Product Owner rule enforceable from data, and gives skill-gap analysis a vocabulary. |
| `resume_as_of` on reference and candidate profiles | "Present" is meaningful only relative to the resume date. |
| `ROLE-UNKNOWN` required instead of null role; `mapping_status` with `needs_review` and `unresolved` | Ambiguous and unresolved roles are preserved in place, never silently dropped. |
| `primary_career_sequence` plus `counts_as_full_time_experience` override (requires a note) | Concurrent freelance or founder work and internships during study are explicit, and reclassification is explicit and explained. |

### Dates

| Decision | Why |
|---|---|
| Dates stored as year + month + precision (`month`, `year`, `unknown`, `ongoing`), with generated month-index bounds `[lo, hi]` | v1 stored the first day of the period. A year-only end looked like January, and precision was lost in arithmetic. |
| Inclusive employment months; gap = whole months strictly between; tenure = end − start + 1 | One consistent definition everywhere. v1's June → July counted as a 1-month gap. |
| Explicit timing relations (`same_month_boundary`, `boundary_uncertain`, `overlapping`, …). 2.0.0 also had a `handover_overlap` tolerance, removed in 2.0.1 | v1 required `start > previous end`, which silently dropped 11 same-month transitions in the uploaded fixtures. |
| Year-only observations never enter percentiles; they contribute only to a labelled coarse range | Avoids falsely precise timelines. |

### Role taxonomy

| Decision | Why |
|---|---|
| `role_family` stored once (taxonomy) and derived everywhere; employment tables no longer have the column | Two v1 rows had a family that contradicted their role id. |
| Controlled seniority ladder with ranks (`trace_seniority_levels`); employment-level override is a foreign key | v1 had 14 free-text seniority values and no ordering. |
| Added Lead, Principal, Group PM, Head of Product, Senior Director, VP and CPO, plus Engineering Manager, Project Manager and Technical Program Manager | Missing rungs and adjacent roles. |
| Product Owner moved to its own `product_ownership` family | v1 placed it in product management, so unreviewed Product Owner roles counted as PM. |
| Aliases grew from 45 to 106; every alias that maps to several roles (PM, TPM, Product Lead, Product & Growth, Product Consultant, Product Owner) or is generic (Associate, Assistant Manager, Analyst, …) is `context_required` with a written `mapping_rule` and stays `needs_review` until confirmed | Preserves v1's contextual rules, which were lost on import, and stops ambiguous titles from being auto-confirmed. |
| Low-confidence mappings (below `min_mapping_confidence`) and unconfirmed statuses are excluded from confirmed transitions | As requested; also covers high-confidence but unreviewed titles. |
| Skills: all 126 classified into 14 families; 10 near-duplicates merged through `trace_skill_aliases`, keeping the raw text | v1 had 136 skills, all "unclassified", including duplicates such as "ml" and "machine learning". |

### Dataset separation

| Decision | Why |
|---|---|
| 18 uploaded fixtures removed from the shared package into git-ignored `local_only/`; CHECK constraints reject `uploaded_resume_fixture`; the validator fails on any `UPL-` id in shared files | Subjects' permission for shared reuse is unverified. |
| `data_mode` (`empirical`, `synthetic_test`, `synthetic_demo`) on runs, propagated by composite FKs to recommendations and evidence | Lets CHECK constraints forbid synthetic `strong`/`moderate` evidence, synthetic timelines, empirical citations of synthetic profiles and synthetic citations of real profiles. |
| `synthetic_demo_allowed` and `v_demo_eligible_profiles` (synthetic labelled, or real with active `external_demo` consent) | v1's `demo_eligible` flag was lost on import; demo and analytics eligibility are now separate. |
| Profile `(source_id, dataset_class)` is a composite FK to the source registry | A profile's class must match its source's class. |
| `is_test_fixture` / `fixture_profile_id` on candidate profiles and resumes | Synthetic PDFs uploaded through the real pipeline stay distinguishable from real users. |
| Synthetic seed refuses to run in a database that holds consented real profiles | Defence against seeding production. |

### Pathway discovery and evidence

| Decision | Why |
|---|---|
| Sequence built from **all** primary records before any exclusion; exclusions become per-transition reasons | v1 filtered rows before `lag()`, turning A → [excluded B] → C into a false A → C. |
| Segments of consecutive confirmed transitions, collapsed into role stints; `route_observations()` matches routes only within one individual's segment, once per person | Every multi-step route is validated against a single person's chronology; no stitching. |
| `route_timeline_summary()` returns counts, evidence class, month-precise percentiles and a coarse range, using configurable thresholds | Defines the timeline metric (`route_start_to_target_start_months`) and minimum evidence explicitly. |
| Thresholds in `trace_ref.analytics_settings`, enforced by a fail-closed trigger on recommendations | Configurable without migrations, but impossible to bypass when writing results. |
| Trigger: empirical evidence may cite only a currently eligible profile; FK on `(profile_id, dataset_class)` with `ON DELETE SET NULL` | Synthetic or withdrawn profiles cannot back empirical claims, and withdrawal is never blocked by old citations. |
| Route steps normalised into `trace_pathway_route_steps` with role FKs (was a JSON array) | Referential integrity for recommended routes. |
| Runs reference a `reference_snapshots` row in the same `data_mode` | Records which settings and cohort size produced a result. |

### Data integrity fixes

- **New defect found and fixed:** in v1, SYN-025's second evidence item was a string instead of a list. It was split into 59 single-character evidence rows, and into 59 bullets in its resume card and PDF. It is now one evidence row, and the database (minimum length), validator and tests all guard against a recurrence. Actual v1 synthetic evidence was 75 rows, not 133.
- Restricted local-only fixtures: v1's two role/family contradictions are resolved as `needs_review` mappings, and one undated record that v1 omitted is preserved with unknown dates. Details stay in `local_only/`.
- Internship flags made consistent: internships are primary unless concurrent with study or another job.
- Every evidence row typed with a responsibility area. The synthetic evidence was classified by rule and the misclassifications were hand-corrected; the local-only rows were classified automatically and remain unverified.
- Edge-case synthetic summaries read like real resumes. Test intent lives in `scenario`/`test_tags`, so parser tests are not given the answer.
- Stable ids everywhere (`…-EMP-NN`, `…-EDU-NN`, `…-BRK-NN`, `…-EMP-NN-ENN`, `ALS-NNNN`, `SKL-…`). Seeds are upserts; synthetic rows missing from the source are deleted in the same transaction.

### Synthetic coverage

Nine edge-case profiles were added (SYN-033 to SYN-041):

| Profile | Scenario |
|---|---|
| SYN-033 | Same-month internal promotions |
| SYN-034 | Overlapping primary jobs and parallel freelance |
| SYN-035 | Unresolved intermediate role |
| SYN-036 | Product Owner awaiting review |
| SYN-037 | Year-only dates |
| SYN-038 | Unknown dates and an internship |
| SYN-039 | Career break and full-time MBA |
| SYN-040 | Founder, then Lead and Principal PM |
| SYN-041 | Explicitly reclassified internship |

All 41 PDFs were re-rendered deterministically.

### Migrations and tooling

- Eleven timestamped Supabase migrations replace the two hand-run scripts. They rely on Supabase migration tracking and contain no `IF NOT EXISTS` re-run guards. Indexes cover every foreign key and `updated_at` triggers cover every mutable table.
- Lookup rows are a generated migration, because production needs them. Synthetic fixtures are a generated seed for local, preview and test databases only.
- `scripts/build_seeds.py` generates everything from one canonical source, and its `--check` mode is part of validation. The validator now covers all CSV and JSONL sources: taxonomy, aliases, skills, settings, sources, dates, sequences, relationships, separation, consent, generated artefacts and PDFs.
- 117 unit tests: date semantics, the transition scenarios, mutation tests proving each validator check fails on the defect it targets, static security checks on the SQL, and local-only regression tests. pgTAP tests are written for `supabase test db` but have not been run.

### Known limitations and follow-ups

- The SQL has been parsed with the PostgreSQL 17 grammar (pglast), but it has **not been executed**. Run `supabase db reset`, `supabase test db` and `supabase db lint` on a local stack before any remote use.
- The Python reference model and the SQL views implement the same rules separately. The pgTAP tests check the SQL side on key scenarios; keep both in step when either changes.
- The rule-based evidence classification of the local-only uploaded fixtures is unverified.
- Threshold defaults are placeholders pending methodology review.
- Not built yet: the resume-retention job, the contribution and de-identification flow, and a cold-start strategy (with 0 eligible profiles, empirical discovery returns nothing).
