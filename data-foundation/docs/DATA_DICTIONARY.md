# Trace Data Foundation v2 — Data dictionary and modelling rules

This document is the specification. The SQL in `supabase/migrations/` implements it, and `scripts/trace_model.py` is an executable reference implementation of sections 5–7 that the tests run against. If the three ever disagree, treat it as a bug.

## 1. Schemas

| Schema | Exposed through the Data API? | Contents | Who can read it |
|---|---|---|---|
| `public` | Yes | Read-only lookups, consent documents and events, the signed-in user's own career data, pathway results | Lookups: anyone. Everything else: the owning user only (RLS) |
| `trace_ref` | **No** | De-identified reference cohort, analytics views, settings, snapshots, access log | `service_role` only |
| `trace_util` | **No** | Immutable date helpers and trigger functions | Not callable through the API |

Never add `trace_ref` or `trace_util` to *Exposed schemas* in the Supabase API settings.

## 2. Dataset classes and data modes

| `dataset_class` | Meaning | Stored in this database? | Empirical analytics? | Demo use |
|---|---|---|---|---|
| `consented_real_reference` | A participant's de-identified career history | Yes | Only through `trace_ref.v_eligible_profiles` (active consent + confirmed review) | Only with a separate, active `external_demo` consent |
| `synthetic_test_fixture` | Fictional test profile (`SYN-…`) | Yes, in local, preview-branch and test databases | **Never** | When `synthetic_demo_allowed = true`, clearly labelled |
| `public_aggregate_reference` | Public aggregate data after licence review | Yes (none loaded yet) | Depends on licence | Depends on licence |
| `uploaded_resume_fixture` | Third-party resumes supplied for the prototype; reuse permission unverified | **No.** Rejected by CHECK constraints. Kept only in git-ignored `local_only/` | Never | Never |

A pathway run declares its `data_mode`:

| `data_mode` | Cohort read | Allowed `evidence_class` | Timelines |
|---|---|---|---|
| `empirical` | `v_eligible_profiles` | `strong`, `moderate`, `limited`, `insufficient` (thresholds in section 8) | Only if enough month-precision profiles |
| `synthetic_test` | All synthetic profiles | `synthetic_test_only` | Never displayed |
| `synthetic_demo` | Synthetic profiles with `synthetic_demo_allowed` | `synthetic_test_only` | Never displayed |

`data_mode` travels through composite foreign keys from runs to recommendations to evidence. CHECK constraints then make these impossible:
- a synthetic run labelled `strong` or `moderate`;
- an empirical run citing a synthetic profile;
- a synthetic run with a displayed timeline.

## 3. Reference cohort (`trace_ref`)

### `profiles`

| Column | Meaning |
|---|---|
| `profile_id` | Pseudonymous stable id. `SYN-NNN` for synthetic profiles. |
| `source_id`, `dataset_class` | Composite FK to `data_sources`, so a profile's class must match its source's class. |
| `resume_as_of_year/month/precision` | When the resume or history was current. `Present` in a role means "ongoing at this date", not today. Generated `resume_as_of_lo/hi` give month-index bounds. |
| `review_status` | `extracted_unverified`, `participant_confirmed`, `reviewer_confirmed`, `synthetic_curated` (synthetic only, and required for synthetic) or `rejected`. |
| `synthetic_demo_allowed` | Synthetic profiles only. Demo eligibility, kept separate from analytics eligibility. |
| `contributor_user_id` | Pseudonymous link to the contributing user, needed to honour withdrawal. `service_role` only. Set to null if the account is deleted, which makes the profile ineligible. |
| `source_consent_event_id` | The consent grant under which the profile was contributed (audit). |
| `scenario_code`, `test_tags` | Synthetic test scenario metadata. |
| `data_quality_flags` | Open issues such as date conflicts or unresolved titles. |

v1's profile-level consent flags (`consent_status`, `shared_analytics_consent`, `external_demo_consent`) have been removed. Consent now lives only in consent events (section 4).

### `employers`

These are per-profile pseudonyms, keyed `(profile_id, employer_id)` with `employer_id = <profile_id>-ORG-<token>`. Tokens are never shared across profiles. Employer attributes are recorded where known:
- `industry_raw`;
- `industry_sector` (controlled list);
- `company_model`: `product_company`, `it_services`, `consulting`, `agency`, `global_capability_centre`, `non_tech_enterprise`, `hybrid` or `unknown`;
- `business_model`: `b2b`, `b2c`, `b2b2c`, `marketplace`, `internal`, `mixed` or `unknown`;
- `company_size_band`: `1-10` … `5001-plus` or `unknown`;
- `attribute_provenance`.

### `employments`

| Column | Meaning |
|---|---|
| `employment_id` | `<profile_id>-EMP-NN`, stable. |
| `sequence_index` | 1..n with no gaps, in chronological order of start (validator). The DB uniqueness is deferrable, so a profile can be re-sequenced in one transaction. |
| `employer_id` | Nullable (employer not stated). Composite FK to the same profile's employers. |
| `title_raw_redacted` | Original title with identifiers removed. Never upgraded or rewritten. |
| `role_id` | **Required.** Unresolved titles use `ROLE-UNKNOWN`. `role_family` is **not stored**; views derive it from the taxonomy. |
| `seniority_code` | Optional title-level override (for example "Digital Marketing Head" → `head`). Null means the taxonomy default. |
| `mapping_status` | `confirmed`, `auto_high_confidence`, `needs_review` or `unresolved`. `unresolved` ⇔ `ROLE-UNKNOWN`. |
| `mapping_confidence` | 0–1. |
| `employment_type` | `full_time`, `part_time`, `contract`, `internship`, `apprenticeship`, `freelance`, `founder`, `volunteer` or `unknown`. |
| `primary_career_sequence` | `false` only for parallel activity, such as freelance work or a co-founder role alongside a job, or an internship during full-time study. Needs a `classification_note`. |
| `counts_as_full_time_experience` | Null means derived from `employment_type`. An explicit true or false (such as a reviewed 12-month full-time "internship" programme) requires `classification_note`. |
| `start_*`, `end_*`, `*_precision` | See section 5. `end_precision = 'ongoing'` ⇔ `is_current_as_stated`. |
| `start_lo/hi`, `end_lo/hi` | Generated month-index bounds. |

### `education`, `career_breaks`

Both use the same partial-date columns and generated bounds as employments.
- **Education** records qualification level, field of study, study mode and completion status. Institution names are never stored in `trace_ref`.
- **Career breaks** record a reason (`education`, `caregiving`, `health`, `relocation`, `job_search`, `sabbatical`, `personal` or `unspecified`) and a redacted note.

### `experience_evidence`

These are responsibilities, achievements, scope and leadership claims. A responsibility is a row with `evidence_type = 'responsibility'` and a controlled `responsibility_area`.
- Four areas are **PM-ownership signals** (`is_pm_ownership_signal`): `product_discovery`, `roadmap_ownership`, `prioritisation_backlog` and `outcome_ownership`. The Product Owner rule depends on them.
- `(profile_id, employment_id)` is a composite FK, so evidence can only attach to an employment of the same profile.
- `evidence_text_redacted` must be at least 10 characters. This guards against the v1 defect that split one sentence into 59 single-character rows.

### `profile_skills`

These link a profile to a canonical skill:
- the raw text is kept in `skill_text_raw`;
- an optional `employment_id` records when the skill was evidenced;
- an optional `evidence_id` points to the supporting evidence.

### `analytics_settings`, `reference_snapshots`, `access_log`

Settings are described in section 8. Each pathway run references a snapshot of the settings and cohort size it used. The access log is append-only and service-written.

## 4. Consent

- **`public.trace_consent_documents`** has one row per `(consent_version, purpose)`, holding the text hash and `legal_review_status`. Only `approved` documents make a grant effective. **None are seeded**, because the drafts need counsel review first.
- **`public.trace_consent_events`** is an append-only event log keyed by user and purpose, with status `granted`, `denied` or `withdrawn`. Users insert their own events from the app and cannot update them.
- **`trace_ref.v_current_consent`** gives the latest event per (user, purpose). `is_active` means granted under an approved document.
- **`trace_ref.v_eligible_profiles`** is the only entry point for empirical analytics. A profile is eligible when:
  - `dataset_class = 'consented_real_reference'`;
  - it has a contributor;
  - the contributor's current `deidentified_shared_dataset` consent is active;
  - `review_status` is participant- or reviewer-confirmed.

  Withdrawal takes effect immediately in every view and function.
- **Provenance is enforced by the database.** The trigger `enforce_consent_integrity` on `trace_ref.profiles` requires a `consented_real_reference` profile to be created with a `contributor_user_id` and a `source_consent_event_id`. That event must be a `granted`, `deidentified_shared_dataset` event belonging to the same user. Re-pointing either link to a non-matching value is rejected. Setting a link to null is allowed only so the account-deletion cascade can work, and it can only remove eligibility.

## 5. Dates

**Month index.** A month is the integer `year × 12 + (month − 1)`. Every partial date is a closed interval `[lo, hi]`:

| Precision | Stored | `lo` | `hi` |
|---|---|---|---|
| `month` | year + month | that month | that month |
| `year` | year | January | December |
| `unknown` | nothing | null | null |
| `ongoing` (end only) | nothing | null | null; censored at `resume_as_of` |

v1 stored every date as the first day of the period. That made a year-only "2025" end look like January 2025 and turned contiguous months into a one-month gap.

**Inclusive employment months.** A role from 2021‑06 to 2023‑03 occupies both June 2021 and March 2023.
- Tenure is `end − start + 1` months: 22 in this example.
- Year-only dates give a range. A role from 2019 to 2020 has a tenure of 2–24 months.
- Ongoing tenure is censored at `resume_as_of`.

**Gap.** The gap is the number of whole calendar months strictly between the previous role's last month and the next role's first month: `next_start − prev_end − 1`.
- A role ending in June followed by one starting in July has a gap of 0.
- A role ending in June followed by one starting in September has a gap of 2.

**Timing relation between consecutive primary roles.** There is no automatic notice-period tolerance:

| Relation | Rule | Confirmable? |
|---|---|---|
| `sequential` | next start is after the latest possible previous end; this includes a year-only end in an earlier year than a year-only start | yes. With year precision `gap_is_precise = false`, and the route is excluded from percentiles |
| `same_month_boundary` | both month-precise, next start = previous end (gap 0) | yes. **v1 silently dropped these.** |
| `boundary_uncertain` | the bounds overlap only because of coarse precision, e.g. a year-only end and a year-only start in the **same year**, so chronological order cannot be established | **no**. Both records are kept; only the transition is unconfirmed |
| `overlapping` | any overlap beyond the shared boundary month, including one month, or a year-precision overlap that holds whatever the true months are | **no**, until explicitly verified (below) |
| `previous_role_ongoing` | previous role has no end | no |
| `timing_unknown` | a needed date is unknown | no |
| `sequence_order_conflict` | `sequence_index` order contradicts start dates | no |

**Verifying an overlap.** An overlapping transition stays unconfirmed until a participant or reviewer resolves it explicitly, in one of two ways:
- correct the dates, for example when the true last month was the boundary month;
- set `primary_career_sequence = false` on the concurrent role, with a `classification_note`.

Nothing is confirmed automatically on the basis of a presumed notice period.

## 6. Career sequences and transitions

1. **Build the full sequence first.** `v_career_sequence` takes every primary record in `sequence_index` order and pairs it with its predecessor. Internships, unresolved titles, low-confidence mappings and undated roles stay in place. Only parallel activity (`primary_career_sequence = false`) sits outside the linear chain.
2. **Then classify each record.** `v_employment_classified.exclusion_reasons`:
   - `not_full_time_experience`: only `full_time` and `contract` count by default. Founder, internship, apprenticeship, part-time, freelance, volunteer and unknown roles count only with an explicit `counts_as_full_time_experience = true` and a `classification_note` (enforced by CHECK). The validator also requires every primary founder role in the synthetic seed to carry an explicit decision;
   - `unresolved_role`;
   - `mapping_not_confirmed` (status not `confirmed`/`auto_high_confidence`);
   - `low_mapping_confidence` (below `min_mapping_confidence`);
   - `unknown_start_date`.
3. **Then classify each transition.** `v_role_transitions` reports every consecutive pair.
   - It is `confirmed` only if both ends have no exclusion reasons and the timing relation is confirmable.
   - Otherwise it is `unconfirmed`, with reasons such as `from:unresolved_role`, `to:mapping_not_confirmed` or `timing:overlapping`.
   - Because exclusions are applied after pairing, **A → unknown → B produces two unconfirmed transitions and never A → B.**
4. **Move type.** `internal` means the same employer token, `external` means different tokens, and `unknown` means an employer is missing. Seniority change is the difference in ladder rank when both ranks exist.
5. **Segments and stints.**
   - A segment is a maximal run of records joined by confirmed transitions.
   - Inside a segment, consecutive records with the same role collapse into one *stint*: PM at A followed by PM at B is one PM stint, while the external move is still visible in `v_role_transitions`.
   - `v_career_paths` gives each segment's ordered role path.
6. **Route validation.** `trace_ref.route_observations(route, data_mode)` finds the first place a route appears as consecutive stints within one confirmed segment of **one individual's** history.
   - Each individual counts at most once.
   - Routes are never stitched from different people.

### 6a. Episode-aware pathways (v2.1.0)

A pathway is a sequence of **employment episodes**, each with a role and an employer, rather than a sequence of role codes. The episode objects are additive: everything above, including `v_career_stints`, `v_career_paths`, `route_observations` and `route_timeline_summary`, behaves exactly as in v2.0.2.

1. **Change type.** `v_episode_transitions` is `v_role_transitions` plus four columns:
   - `role_changed`;
   - `employer_changed` (`null` when an employer is missing);
   - `change_type`;
   - `is_pathway_move`.

   | `change_type` | Role | Employer | Pathway move when confirmed? |
   |---|---|---|---|
   | `role_change` | changed | same | yes |
   | `employer_change` | same | different | yes |
   | `role_and_employer_change` | changed | different | yes |
   | `role_change_employer_unknown` | changed | unknown | yes (the role change is established) |
   | `no_change` | same | same | no: a continuation |
   | `employer_unknown` | same | unknown | no: a change cannot be established |

2. **Integrity rules are inherited, not re-implemented.** Segments are still built from confirmed transitions. So overlaps, parallel activity, year-only and same-year boundaries, ongoing previous roles, unknown dates, unresolved or low-confidence mappings, and unclassified founder or internship roles all break the chain exactly as before.
3. **Episode stints.** Inside a confirmed segment, consecutive records collapse into one stint **only** on a continuation (`no_change` or `employer_unknown`).
   - PM at A → PM at B is two stints.
   - PM at A → PM at A is one stint.
   - `v_career_episode_stints` and `v_career_episode_paths` carry `entry_change_type` for each stint, or `entry_change_types` for each path, where the first element is `null`.
4. **Routes are not collapsed.** In the episode functions, repeated consecutive role ids mean an employer move: `{PM-PM, PM-PM}` is PM at one employer followed by PM at another.
   - `episode_route_observations(route, data_mode)` adds `step_change_types` for each matched step.
   - `episode_route_timeline_summary(route, data_mode)` returns the same columns, thresholds and fail-closed rules as `route_timeline_summary`, with the route echoed exactly as given.
5. **Invalid routes raise `22023`** (`invalid_parameter_value`). Invalid means:
   - null or fewer than two steps;
   - multi-dimensional;
   - a null step;
   - an unknown or unresolved role id.

   They are never reported as a valid route with zero observations. (The v2.0.2 `route_timeline_summary` still collapses `{PM-PM, PM-PM}` to `{PM-PM}` and returns a zero row; it is unchanged by design.)
6. **A single episode is not a pathway.** A one-stint path shows that the person holds the role, but no route of two or more steps can match it.
7. **Employer pseudonyms stay private.** Tokens are used only to compare consecutive records of one profile. No episode view or function outputs an employer identifier, and tokens are never compared across profiles. Destination-company identity is a separate, consent-gated design.
8. **Seniority within one role id is not an episode boundary yet.** A seniority override between two records with the same `role_id` at the same employer is a `no_change` continuation, pending a taxonomy decision on levels such as PM II.

## 7. Timeline metrics

| Metric | Definition | Used for |
|---|---|---|
| `route_start_to_target_start_months` | Start month of the target-role stint minus start month of the first route stint, within one individual's confirmed segment | Recommendation percentiles (`p25/median/p75_months`) |
| `gap_months_min/max` | Section 5 | Transition detail |
| `tenure_months_min/max` | Inclusive tenure; ongoing roles censored | Role detail |

Rules:
- Percentiles use only observations whose first and last stint starts are month-precise (`is_precise`). Year-only observations contribute only to the clearly labelled `coarse_min_months`/`coarse_max_months` range.
- Percentiles are computed only when `dated_profile_count ≥ min_dated_profiles_for_timeline`, and displayed only in `empirical` mode.
- They describe people who **did** complete the route. They are not success probabilities, and no probability may be derived from them.

## 8. Configurable evidence thresholds (`trace_ref.analytics_settings`)

| Key | Default | Meaning |
|---|---|---|
| `min_profiles_limited` | 5 | `limited` evidence; also the minimum count shown to a user |
| `min_profiles_moderate` | 15 | `moderate` evidence |
| `min_profiles_strong` | 40 | `strong` evidence |
| `min_dated_profiles_for_timeline` | 20 | Percentiles computed and displayable |
| `min_mapping_confidence` | 0.8 | Minimum confidence for a confirmed transition |

The defaults are product placeholders. They should be revisited with whoever owns the methodology before any real cohort is shown.

Enforcement:
- A trigger on `trace_pathway_recommendations` enforces these thresholds and fails closed if a setting is missing.
- The views treat a missing `min_mapping_confidence` as 1.0.
- `max_boundary_overlap_months` was retired in 2.0.1. The validator rejects it if it is reintroduced.

## 9. Role taxonomy

- **Family and seniority are separate.** `role_family` is defined once, in `trace_role_taxonomy`. `trace_seniority_levels` is a controlled ladder with ranks (intern 10 … executive 100). `founder` and `unknown` have no rank and are never compared.
- **PM ladder:**

  | `role_id` | Role |
  |---|---|
  | `PM-INTERN` | PM intern (not a target) |
  | `PM-APM` | APM |
  | `PM-PM` | PM |
  | `PM-SPM` | Senior PM |
  | `PM-LEAD` | Lead PM |
  | `PM-PRINCIPAL` | Principal PM |
  | `PM-GPM` | Group PM |
  | `PM-HEAD` | Head of Product |
  | `PM-DIR` | Director |
  | `PM-SDIR` | Senior Director |
  | `PM-VP` | VP (out of current scope) |
  | `PM-CPO` | CPO (out of current scope) |

- **Product Owner** is `ROLE-OWNER` in its own `product_ownership` family, **not** product management. `ROLE-OWNER → PM-PM` is therefore a real cross-family transition. The `Product Owner` alias is context-required and maps to both `ROLE-OWNER` and `PM-PM`. A reviewer may remap to `PM-PM` only when the evidence carries PM-ownership responsibility areas.
- **Ambiguous aliases** must be `context_required` with a `mapping_rule`. This applies to every alias that maps to several roles (`PM`, `TPM`, `Product Lead`, `Product & Growth`, `Product Consultant`, `Product Owner`) and to generic titles (`Associate`, `Technical Associate`, `Technology Associate`, `Assistant Manager`, `Analyst`, `Junior Analyst`). A context-required match is stored as `needs_review` and cannot be auto-confirmed.
- **`ROLE-UNKNOWN`** preserves unresolved roles (`is_resolved = false`) so they stay visible in the sequence and break transitions.

## 10. Candidate (signed-in user) tables

The user's own history is structured:
- `trace_candidate_profiles`;
- `trace_candidate_employers`;
- `trace_candidate_employments`;
- `trace_candidate_education`;
- `trace_candidate_career_breaks`;
- `trace_candidate_evidence`;
- `trace_candidate_skills`.

These tables mirror the reference columns, so the same sequence logic applies to the user's history. They may hold the user's own employer and institution names, because only that user can read them.

`raw_extraction_json` is an audit copy of the model output, not the source of truth. `trace_extraction_runs` records the provider, model, prompt version, processing region and consent event for each extraction.

Candidate data is never copied into `trace_ref` automatically.

### 10a. Structured career profile (v2.2.0)

A candidate's profile has two kinds of record. Only the first builds pathways.

| Kind | Tables | Used for |
|---|---|---|
| **Employment episodes** | `trace_candidate_employments` (+ `trace_candidate_employers`) | The **only** input to employment pathway construction. Internships (`employment_type = 'internship'`), freelance and founder work stay here, with the existing full-time classification rules. |
| **Supporting records** | `trace_candidate_education`, `trace_candidate_career_activities`, `trace_candidate_career_breaks` | Background context, and later fit and gap analysis. Never employment transitions, and never an input to pathway confidence. |

Responsibilities and achievements (`trace_candidate_evidence`) attach to an employment episode **or** an activity, never both. Skills (`trace_candidate_skills`) attach to the record that demonstrates them.

**`trace_candidate_career_activities`** has one row per certification, licence, course, independent project, portfolio project, award, publication, research, volunteering, extracurricular or leadership activity (`activity_type`).

| Fields | Notes |
|---|---|
| `title_raw` | Original title or name. Required and never rewritten. |
| `category_code`, `category_mapping_status`, `category_mapping_confidence` | Normalised category once a vocabulary exists. A code requires a mapping status other than `unresolved`. |
| `organisation_raw`, `description_raw`, `contribution_raw`, `outcome_raw` | As stated. Outcomes and metrics are never inferred. |
| `link_url` | http(s) only. As provided by the user. |
| `status` | `completed`, `in_progress`, `active`, `expired` (certifications and licences only), `not_completed` or `unknown`. |
| start, end and generated bounds | Same partial-date rules as employment. Unknown stays unknown; `end_precision = 'ongoing'` is allowed. |
| `credential_identifier_raw`, `expiry_*` | Certifications and licences only (CHECK). |
| `related_employment_id` | Optional link to an employment episode of the **same** profile. It never makes the activity employment. |
| `metadata` | JSON object, supplementary only. Never the source of truth. |

**Education** adds `qualification_title_raw` (exactly as stated, e.g. "B.Tech (CSE)") and `notes`. `qualification_level` is the normalised type; it never replaces or upgrades the stated title.

**Skills:**
- One row per (skill, source record). A skill can link to an employment, education, activity or evidence row: at most one per row, all scoped to the same profile.
- `skill_id` may be null for a skill outside the taxonomy, in which case `skill_text_raw` is required.
- `stated_proficiency_raw` is recorded only when stated and can never accompany an `inferred` origin.
- A mentioned technology is not proof of proficiency.

**Provenance, review and verification** are separate columns on employments, education, career breaks, evidence, skills and activities:

| Column | Values | Meaning |
|---|---|---|
| `information_origin` | `user_stated`, `source_extracted`, `inferred` | Where the information came from. `null` only for rows created before v2.2.0 whose legacy columns did not say. |
| `review_status` | `pending_review`, `user_confirmed`, `user_corrected`, `rejected` | The candidate's own review |
| `verification_status` | `unverified`, `source_document_supported`, `independently_verified` | `source_document_supported` means a document supports it; it is **not** independent verification. `independently_verified` only where verification actually occurred. |
| `extraction_confidence` | 0–1 | Confidence of an extraction or inference. Not allowed on `user_stated` rows. Never a verification or review signal. |
| `source_resume_id`, `extraction_run_id`, `source_location` | | Reference to the source document and extraction run of the **same owner**, plus a location such as "page 1, Certifications". |

**No resume excerpts are stored.** `*_raw` fields are the user's own record content, not copied excerpts. Retention of copied excerpts is undecided, so the schema has no excerpt column.

**Backfill of existing rows:**
- Employments: `entry_source` and `participant_confirmed` map to origin and review.
- Evidence: `provenance` and `evidence_status` map to origin, review and verification. Legacy `document_verified` becomes `source_document_supported`.
- Skills: `evidence_source` maps to origin and review. The undefined legacy `verified` flag is not mapped.
- Education and breaks: origin stays null.

The legacy columns are kept.

**Privileges (v2.3.0).** The owner may write their record content, and only while the record is their own pending `user_stated` draft. `review_status` changes only through `trace_review.review_record` (confirm, reject, reinstate; no unconfirm), and an employment's employer only through `trace_review.reassign_employment`. No client role may write `information_origin`, `verification_status`, `extraction_confidence`, the source references, role or activity-taxonomy mapping, or the legacy trust columns. Deleting a parent never deletes or silently detaches a confirmed or corrected supporting record; pending and rejected dependents are detached (link column set to NULL; content, review status and provenance kept). See `ACCESS_CONTROL.md` § Review enforcement. (v2.2.0 closed an earlier gap where a user could mark their own evidence `document_verified`.)

**Evidence classification (v2.7.0).** `trace_candidate_evidence.area_classification` holds the responsibility areas the candidate assigned to that evidence (sorted, distinct, at least one), with `area_classification_basis = 'candidate_selected'` and `area_classified_text`, the exact text classified. `area_classification_is_stale` (generated) is true once `evidence_text` no longer equals it. The classification is separate from the candidate-content `responsibility_area` column and never changes review or verification status. Written only by `trace_review.classify_evidence_areas` (`ACCESS_CONTROL.md` § Evidence classification).

**Role mapping (v2.6.0).** `role_mapping_basis` records how an employment's role was chosen: `alias_match` (automatic, from `trace_role_aliases`; `role_mapping_alias_id` names the alias, status `auto_high_confidence`, the alias's confidence) or `candidate_selected` (the candidate's own decision; status `confirmed`, `mapping_confidence` NULL). NULL means no mapping was made through the v2.6.0 functions. `mapped_title_raw` is the exact title the mapping was based on; `mapping_is_stale` (generated) is true once `title_raw` no longer equals it, and a stale mapping is not eligible for sequencing until a fresh decision. `role_mapping_ownership_areas` holds the PM-ownership responsibility areas the candidate stated when mapping a product-ownership title to a product-management role. Candidate-selected mappings describe the candidate's own sequence only and are never reference evidence. Titles are compared under the title normalisation contract v1 (`trace_util.normalize_title`, `ACCESS_CONTROL.md` § Role mapping); `trace_role_aliases.alias_normalized` must equal it (CHECK).

**Correction history (v2.5.0).** `public.trace_candidate_corrections` holds one append-only event per correction: `record_kind`, `record_id`, `changed_fields`, `previous_values` and `corrected_values` (jsonb, the changed fields only), `prior_review_status`, `corrected_at`, `corrected_by`, plus `correction_seq` (total order) and `txid` (the correcting transaction). The record holds the latest value; a field's original value is its `previous_values` entry in the earliest event that changed it. A corrected record has `review_status = 'user_corrected'`; its `information_origin`, source references and `extraction_confidence` are unchanged. Entries are inserted only by `trace_review.correct_record` (`txid` and `corrected_at` are set by the database) and never updated; they are removed only with their record, profile or account, so previous values, possibly sensitive extracted content, are kept until then (`ACCESS_CONTROL.md` § Corrections).

**Legacy trust columns (v2.3.0).** Kept for compatibility, never authoritative. `information_origin`, `review_status` and `verification_status` are the authoritative fields, and stay separate: entering a record does not review it, and reviewing it does not verify it.

| Column | New candidate rows | Meaning of NULL | Existing rows |
|---|---|---|---|
| `skills.evidence_source` | NULL (nullable since v2.3.0) | No legacy source label recorded | Unchanged |
| `evidence.provenance` | NULL (nullable, no default since v2.3.0) | No legacy source label recorded | Unchanged |
| `employers.attribute_provenance` | NULL (nullable, no default since v2.3.0) | Attributes as entered by the candidate; no source provenance recorded. Candidate-editable while every referencing employment is pending, like the legacy `participant_confirmed`. `unknown`, `resume_stated` and `inferred_unverified` are not candidate-editable | Unchanged |
| `employments.entry_source` | `manual_entry`, set by the insert guard from the origin | Not nullable | Unchanged |
| `employments.participant_confirmed` | `false`; not client-writable; nothing reads it | Not nullable | Unchanged |

Extracted drafts (v2.4.0, ingested through `trace_ingest`) carry the origin-only labels `entry_source = 'resume_extraction'`, `evidence.provenance = 'resume_claim'`, `skills.evidence_source = 'resume_explicit'` and `employers.attribute_provenance = 'resume_stated'`, with `information_origin = 'source_extracted'`, `review_status = 'pending_review'` and `verification_status = 'unverified'`. `trace_extraction_runs.result_sha256` (v2.4.0) is the SHA-256 of the ingested result; a resume has at most one succeeded run and at most one open (`queued`/`running`) run. `trace_ingest.approved_fixture_resumes` (v2.4.0) pairs each synthetic fixture id with the SHA-256 of its rendered PDF; ingestion accepts only those exact pairs. It is filled by the synthetic seed only and is empty in production. The rule that a succeeded run carries its hash is `NOT VALID`: runs from before v2.4.0, or kept by a rollback of it, have none, and ingestion treats a missing hash as a different result, so such a resume is never ingested twice.

Nothing reads these columns for eligibility, review, verification, trust or recommendations: no view, function or trigger, and the backend only displays `entry_source`, `evidence_source` and `attribute_provenance`.

### 10i. Transition goals (v2.30.0)

`trace_private.transition_goals`: the user's planning goal for one target role. A planning target, never a plan input
and never a forecast.

| Column | Meaning |
|---|---|
| `user_id`, `target_role_id` | Owner (cascade) and target role; one goal per pair |
| `timeframe` | `within_3_months`, `within_6_months`, `within_12_months`, `within_18_24_months`, `flexible`, or null |
| `target_date` | A specific date the user chose instead (today to five years ahead); never with `flexible` |
| `weekly_hours` | `1_3`, `4_6`, `7_10`, `more_than_10`, `varies`, or null |
| `checkpoint_dates` | `{"1".."4": "YYYY-MM-DD"}`: dates the user set for the roadmap's stage checkpoints; Trace never sets them |
| `goal_set_on` | The day the timeframe or date was last chosen (anchors "within N months") |
| `updated_at` | Last change |

### 10h. Target-company preferences (v2.29.0)

`trace_private.target_company_preferences`: the companies a user would like to work at, per target role. An
aspiration, never employment and never evidence; plans never read it.

| Column | Meaning |
|---|---|
| `user_id` | Owner (`auth.users`, cascade) |
| `target_role_id` | The target role the list belongs to (`trace_role_taxonomy`) |
| `company_name` | As typed, trimmed, inner whitespace collapsed; 1–100 characters, no control characters |
| `company_key` | Lower-case `company_name`; unique per user and target (case-insensitive de-duplication) |
| `position` | The user's order, 1–20, unique per user and target |
| `added_at` | When first added; kept when the list is reordered |

Whether Trace holds research for a name is decided at read time against the curated job postings; nothing about the
company is stored or claimed here.

### 10g. Phase 4 remediation (v2.24.0 – v2.27.0)

**`trace_capability_requirements`:**
- `decision_rights` (C2, C4 and L1 in pm-v1.2);
- `area_condition_fallback`: `{area: "transferable"}`; a conditioned direct area answered "no" counts as Transferable.

**`trace_bridge_roles`:**
- `entry_evidence`: `employer_stated`, `source_stated_context`, `target_plan` or `hypothesis`;
- `progression_evidence`: `source_stated` or `hypothesis`;
- `may_be_primary`;
- `partial_development`: `{requirement: {develops, incomplete, seek}}`.

**`trace_digital_role_candidates`:**
- `f1_role` (F1's name), `trace_role_family`, and `mapping_confidence` (`source_direct`, `curated_hypothesis` or
  `unmapped`);
- `summary`, `shared_skills`, `develops`, `suitable_backgrounds` and `source_key`.

**`trace_education_fields`:** `field_code`, `label`, `category` (`computing`, `engineering`, `quantitative` or
`business`) and `aliases`. A stated field matches when its whole normalised value equals an alias.

**`trace_employer_criterion_rules`:** `rules_version`, `requirement_id` and `rule`. Leaves may add:
- `equivalent_families` (years), which can give only "unclear";
- `types` (qualification: BE, BTech, ...);
- qualification `fields` as codes or `category:<name>`.

pm-eligibility-v3 (v2.28.0) adds two leaf keys:
- `unconfirmed_roles` (current-role leaf): roles inside the family that give "unclear" rather than "meets";
- `before_families` (years leaf): count only experience before the first role in those families.

**`trace_action_templates.labels`** and **`trace_pathway_actions.labels`**: `learning_exercise`,
`personal_project_possible`, `factual_accuracy` or `trace_curated`.

**`trace_ref.reference_snapshots`:** `state_fingerprint` (sha256 of the cohort, settings and counts) and
`first_observed_on` (a date). Shared by every plan in the same state.

### 10f. Transition planning (v2.21.0 – v2.23.0)

**`trace_bridge_roles`** (D27), per catalogue version, target and bridge role: responsibilities; requirements it
`develops` and `partly_develops`; `entry_basis` (`open_entry`, `signals`, `target_assessment`) with entry signal
families and requirements; the entry requirements as stated; suitable backgrounds; suitability and trade-off notes;
`level_step` (Product Manager before Senior Product Manager); source keys and notes.

**`trace_action_templates`** (D28), per template version and key: stage (0 resolve, 1 build, 2 demonstrate, 3
reposition, 4 apply, each with its one kind), requirement (build and demonstrate only), objective, action text,
deliverable, `where_options` (`current_job`, `internal_move`, `external_project`, `self_directed_study`, `job_market`),
basis sources.

**`trace_target_role_requirements.criterion`** (D29): `{"all": [...]}`, `{"any": [...]}` or a leaf:
`years_in_families` (families, min, optional max), `product_exposure` (families), `current_role_in_families`,
`not_transitioning_from` (families; the current role only), `total_years` (min), `qualification` (fields, optional
minimum level, `or_equivalent`), `stated_condition` (text; always unclear). An optional `note` keeps the curated
reading. `criteria_version` names the rule set.

**`trace_pathway_runs`** (plan runs, `plan_kind = 'transition_plan'`): the audit columns listed in the CHANGELOG
(2.23.0). **`trace_pathway_recommendations`**: `route_basis` (`profile_led`, `observed`), `route_kind` (`direct`,
`bridge`, `observed`), `primary_status` (`primary`, `tied_primary`, `alternative`, `not_recommended`,
`not_evaluated`), `bridge_role_id`, `explanation`, `feasibility`, `tradeoffs`, `evidence_breadth_label`.
**`trace_pathway_actions`**: stage, kind, template version and key, requirement, objective, action, `why_for_you`,
deliverable, where, source keys and the owner's own record references.

### 10e. Taxonomy v2, versioned classifications and pm-v1.1 (v2.18.0 – v2.20.0)

**`trace_responsibility_areas`** (extended):
- `examples` (text[]): at least two plain-language examples per area;
- `introduced_in_version` and `definition_version`;
- `qualifiers` (jsonb): scope questions, `{code: {question, version}}`.

`trace_util.current_taxonomy_version()` returns 2.

**`trace_candidate_evidence`** (extended):

| Column | Meaning |
|---|---|
| `area_classification_none` | The user chose "None of these areas". `area_classification` is then an empty array |
| `area_classification_taxonomy_version` | The taxonomy version shown when the user chose. Null for earlier classifications, read as 1 |
| `area_qualifiers` | Answered scope questions, `{area: {question: true/false}}`. An unanswered question is absent |
| `area_classified_at` | When the user chose |

An area or question introduced after a classification's version cannot support a gap from that classification until
the user reviews the line again.

**`trace_capability_requirements.area_conditions`** (jsonb): `{area: question}`; the area counts only when the answer is
yes. pm-v1.1 uses the same sources (`pm-v1`) and evidence links as pm-v1.

### 10d. Target-role knowledge (v2.16.0) and the pm-v1 pack (v2.17.0)

**`trace_target_role_sources`** (extended). A knowledge-pack source has:
- `source_key` (for example `pm-v1:J11`), `knowledge_pack` and `source_kind` (`framework`, `national_standard`,
  `employer_framework`, `job_posting`);
- publisher, geography, licence and attribution, level as stated, context tags;
- `last_verified_at` and `revalidate_by`: postings are 14 days after verification (D26); frameworks follow their own
  policies;
- `status_note`.

Job postings only:
- `entry_background_stance` (`accepts_adjacent`, `requires_target_role_experience`, `excludes_specified_transitions`,
  `not_specified`);
- accepted or excluded backgrounds;
- `entry_quote` (one verbatim sentence, at most 400 characters) and `entry_locator`.

`source_status` keeps its lifecycle: `current`, `current_check_required`, `expired` (closed by the employer),
`withdrawn`. The effective status is computed at read time by `trace_util.posting_effective_status`.

**`trace_target_role_requirements`** (unchanged schema) now holds the **employer-specific** criteria stated by one posting
(experience, education, domain), with `extracted_by = 'reviewer'`. They are never canonical requirements.

**`trace_capability_requirements`** holds the canonical requirements per pack:
- key, role family, level (`pm`, `senior_pm`, `all`) and class (`core`, `context_dependent`, `differentiator`);
- title, description, level notes, product contexts;
- the transferability mapping: direct and transferable responsibility areas and skills, transferable role families,
  and the rationale;
- `gap_basis`.

**`trace_requirement_source_evidence`** links requirements and sources many-to-many, with stance, locator and note.

### 10c. Ordering events (v2.15.0)

`public.trace_candidate_order_events` records one row per position-only move of an employment. Columns:
- `previous_sequence_index` and `new_sequence_index`;
- `review_status_at_move` (never `rejected`; the move does not change it);
- `cause` (`role_inserted` or `user_reordered`);
- `caused_by_employment_id` (the inserted role; no foreign key, so history outlives it);
- `moved_at`, `moved_by` and `txid`.

A role's content history stays in `trace_candidate_corrections`. The two histories are separate by design.

### 10b. Profile update proposals (v2.14.0)

`public.trace_profile_update_proposals` holds parsed career items that the user must accept before anything enters
their profile. The sources are pasted experience text, and a resume uploaded into a profile that already holds records
(ingestion still never merges, TR015). Later sources are voice transcripts and the advisor.

| Column | Meaning |
|---|---|
| `batch_id` | One parse of one source |
| `source` | `pasted_text` or `resume_update` |
| `source_sha256` | SHA-256 of the source (normalised pasted text, or the resume bytes). The source itself is never stored |
| `extractor_version` | Parser version (plus the de-identification pipeline version for resumes) |
| `record_kind` | `employment`, `evidence`, `education`, `activity` or `skill` |
| `match_state` | `new`, or `possible_update` (an existing employment's or qualification's dates or field differ) |
| `target_record_id` | The employment an evidence item attaches to, or the record a possible update would change |
| `fingerprint` | SHA-256 of the kind, state, target and content: at most one open proposal per profile and fingerprint |
| `proposed` | The proposed content (JSON object, at most 20,000 bytes). Present only while open; cleared on decision |
| `status`, `decided_at`, `resulting_record_id` | `proposed` → `accepted` (with the record created or changed) or `dismissed`; final |

Items already in the profile are not stored. Accepted records carry the normal candidate provenance (`user_stated`
drafts, or a correction with history), plus this link back to their source hash and parser version.

**Separation.** No `trace_ref` view or function reads any candidate table. Supporting records never enter reference pathway analytics, consent eligibility or shared reference evidence.

## 11. Synthetic data rules

The 41 synthetic profiles are for parser, sequence, agent, UI and edge-case tests.
- Profiles `SYN-033` to `SYN-041` are targeted edge cases (see `data/synthetic/synthetic_scenario_coverage.csv`).
- None of them are observations, and they must never feed claims about real Indian career transitions.
- The edge-case summaries read like real resume summaries. The test intent lives in `scenario` and `test_tags`, so parsers are not given the answer.
