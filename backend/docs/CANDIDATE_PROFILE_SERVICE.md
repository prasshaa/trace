# Candidate profile service

**Package:** `app/services/candidate_profile/` (`models.py`, `repository.py`, `review.py`, `service.py`).

**What it is.** One consistent backend interface for reading and writing a signed-in candidate's structured career profile on the data foundation v2.2.0 schema (`DATA_DICTIONARY.md` §10a). It is a Python service only: there are no API endpoints, no UI, no resume extraction, no recommendations and no suitability analysis.

> **Development-only connection.** The local `DATABASE_URL` connects as `postgres`, which is not a production access model. See "Ownership" below for how owner operations are nevertheless held to the database's own rules.

> **Review rules are enforced by the database (M2, data foundation v2.3.0).** Explicit review actions, draft-only editing, protected fields and deletion protection hold for every role except the table owner, through column grants, guard triggers and the `trace_review` functions (`data-foundation/docs/ACCESS_CONTROL.md` § Review enforcement). This service's own checks run first and give clearer errors. Not production-secure yet: raw database credentials can forge request claims, and the development login is the table owner (see "Remaining gaps").

## Operations (`CandidateProfileService(conn)`)

| Area | Operations |
|---|---|
| Profile | `create_profile(owner_user_id, ProfileCreate)`, `update_profile(ctx, ProfileUpdate)`, `get_profile(ctx)` |
| Employment | `add_employer` / `update_employer`, `add_employment` / `update_employment` |
| Supporting records | `add_education` / `update_education`, `add_activity` / `update_activity`, `add_career_break` / `update_career_break` |
| Evidence and skills | `add_evidence` / `update_evidence`, `add_skill` / `update_skill` |
| Review | `confirm_record`, `reject_record`, `reinstate_record` `(ctx, kind, record_id)`. Each returns the resulting status. There is no free-form status setter |
| Sequencing | `sequence_employment_history(ctx, SequencingContext)`, which uses `career_history`. `load_sequencing_context(conn)` reads the lookups |
| Current position and route evidence (M5) | `current_position(ctx, context)` says where the candidate is today, or why that is unknown; `route_evidence(ctx, context, target_role_id)` returns observed routes from that role to one of the nine PM targets (read-only, synthetic test data). See "Current position and route evidence (M5)" |
| Pathway result (M6) | `pathway_result(ctx, context, target_role_id)` returns ranked observed routes from the current position to one of the nine PM targets, with the safely disclosed route experience, the candidate's fit from their own reviewed, classified evidence, area and route-step gaps, and generated guidance. Read-only and deterministic. See `docs/PATHWAY_RESULT.md` |
| Evidence classification (M6) | `classify_evidence(ctx, evidence_id, areas)` records the candidate's responsibility areas for one of their evidence records (extracted or entered), basis `candidate_selected`. Not review or verification; rejected evidence is refused; the stored list is sorted and distinct; it goes stale when the text changes (`EvidenceRecord.area_classification_is_stale`) |
| Role mapping (M5) | `role_mapping_options(ctx, employment_id)` lists the automatic proposal (if any), the alias-derived options, then every other active, resolved role; `map_employment(ctx, employment_id)` applies an automatic alias mapping when there is one; `choose_role(ctx, employment_id, role_id, ownership_areas=None)` records the candidate's decision. See `docs/ROLE_MAPPING.md` |
| Corrections (M4) | `correct_record(ctx, kind, record_id, <Kind>Correction)` corrects scalar content of a reviewed, extracted, inferred or legacy record and returns `user_corrected`; `correction_history(ctx, kind, record_id)` returns its events, oldest first. See "Corrections (M4)" |
| Extraction (M3) | `ingest_extraction(ctx, resume_id, ExtractionResult)` turns one extraction result into `source_extracted`, `pending_review` drafts through `trace_ingest` (synthetic fixtures only); `extraction_run(ctx, run_id)`. See `RESUME_EXTRACTION.md` |

`update_*` operations edit a draft only (see "Editing rules").

`ctx` is an `OwnerContext(owner_user_id, candidate_profile_id)` built by a trusted caller, never from a payload. The service has no delete operation: retention and deletion workflows are undecided. The database's deletion rules (below) apply to any delete issued as the owner.

## Complete profile (`CandidateProfile`)

Every category is always present; it is empty when there are no records.

| Category | Contents |
|---|---|
| `employment_history` | Episodes in `sequence_index` order, each with:<br>• `title_raw` (original) and `role_id` / `normalised_title` / `role_family` (taxonomy);<br>• mapping status;<br>• the employer;<br>• `employment_type`;<br>• partial dates with bounds and labels;<br>• `responsibilities`, `achievements` and `other_evidence`;<br>• linked `skills`;<br>• `provenance` |
| `education` | `qualification_title_raw` (as stated) and `qualification_level` (normalised), institution, field, mode, completion, dates, notes, skills, provenance |
| `career_activities` | A dict keyed by **all 12** activity types. Each record has its original title, category mapping, organisation, description, contribution, outcome, link, status, credential and expiry, related employment, achievements, skills and provenance |
| `skills` | Stated text, `taxonomy` (or `null` when unmapped), `stated_proficiency_raw` (only when stated), and `supported_by` / `supported_by_id`: the employment, education, activity or evidence record that demonstrates it |
| `unattached_evidence` | Evidence not attached to any employment or activity |
| `career_breaks` | Reason, note, dates, provenance |

Unknown dates stay unknown, and unresolved mappings and original text are returned unchanged. `raw_extraction_json` is never returned, and no resume excerpt exists to return.

## Provenance and trust boundaries

**Content inputs** (`*Create` / `*Update`) forbid unknown fields. A payload containing any of these fields is rejected before SQL:
- `information_origin`, `verification_status`, `review_status`;
- `extraction_confidence`, `source_resume_id`, `extraction_run_id`, `source_location`;
- `owner_user_id`, `candidate_profile_id`;
- the legacy `confidence`, `verified`, `evidence_status`, `evidence_source`, `provenance`, `entry_source`, `attribute_provenance` and `participant_confirmed`.

**Candidate-authored records** get the database defaults:
- `information_origin = 'user_stated'`;
- `review_status = 'pending_review'`;
- `verification_status = 'unverified'`;
- no extraction confidence and no source references.

The service writes no legacy trust column (v2.3.0). The database sets `entry_source = 'manual_entry'` from the origin, and leaves evidence `provenance`, skill `evidence_source` and employer `attribute_provenance` NULL: entering a record neither reviews nor verifies it. Rows written before v2.3.0 keep their values.

A record whose origin was not recorded (`null`, pre-v2.2.0) stays `null` and is never upgraded.

**Review actions change only the review status.** **Nothing in this service can set verification, origin, extraction confidence, source references, role or category mapping, or profile confirmation.** These belong to future trusted extraction, mapping, verification and confirmation workflows.

## Review actions (M1 rules, enforced by the database since M2)

The service calls `trace_review.review_record(kind, profile, record, action)` in the owner transaction. The function locks the row (`FOR UPDATE`), applies the matrix (`trace_review.next_review_status`) and acts only for `auth.uid()`. No other path can change `review_status`. `review.py` mirrors the matrix for unit tests; a test asserts parity with the database function.

| Action | From | To |
|---|---|---|
| `confirm_record` | `pending_review` | `user_confirmed` |
| | `user_confirmed`, `user_corrected` | unchanged (no-op) |
| | `rejected` | refused: `InvalidTransition` (reinstate first) |
| `reject_record` | `pending_review`, `user_confirmed`, `user_corrected` | `rejected` |
| | `rejected` | unchanged (no-op) |
| `reinstate_record` | `rejected` | `pending_review`, never straight to confirmed |
| | anything else | refused: `InvalidTransition` |

- There is no "unconfirm": undoing a confirmation and then editing would overwrite a confirmed value without history.
- `user_corrected` is produced only by a correction (M4, `correct_record`); legacy rows may also carry it. Confirming a corrected record is a no-op; it can be rejected and reinstated like any other record.
- The same rules apply to employment, education, activities, evidence, skills and career breaks. Employers have no review status (`InvalidRecord`).

## Editing rules (M1)

An `update_*` call is an **edit of the candidate's own unreviewed draft**: allowed only when the record is `pending_review` **and** `information_origin = 'user_stated'`. An edit never changes the review status.

| Record state | Result |
|---|---|
| `user_stated`, `pending_review` | edited; stays `pending_review` |
| `user_confirmed` or `user_corrected` | `CorrectionNotAvailable` for `update_*`; change it with `correct_record` (M4) |
| `rejected` | `RecordNotEditable` (reinstate it first), for edits and corrections |
| `pending_review` with origin `source_extracted`, `inferred` or `null` | `CorrectionNotAvailable` for `update_*`; change it with `correct_record` (M4) |

**Employers** have no review status. An employer is editable only if the candidate entered it (`attribute_provenance` NULL, or the legacy `participant_confirmed`) and every employment that shows it is still `pending_review`. The database enforces the same rule under the same locks (`trace_guard_employer_update`).

**Employer edits and concurrent reviews.** The rule is checked under locks, so a concurrent review cannot slip past it:

| Operation | Locks, in order |
|---|---|
| Employer edit | The employer row (`FOR UPDATE`), then every employment that shows it, in `candidate_employment_id` order (`FOR UPDATE`). The statuses are checked **after** the locks are held |
| Review action (confirm, reject, reinstate) | That employment row (`FOR UPDATE`) |
| Reassigning an existing employment (`update_employment` with `candidate_employer_id`, drafts only) | Inside `trace_review.reassign_employment`: the old and new employer rows (`FOR KEY SHARE`, in `candidate_employer_id` order), **then** the employment row (`FOR UPDATE`). The foreign-key check's own `FOR KEY SHARE` on the new employer is then already held. `candidate_employer_id` is not otherwise updatable |
| Creating an employment that names an employer | The foreign-key check takes `FOR KEY SHARE` on the employer row, which waits for an employer edit in progress. The new row is invisible to the edit, so no cycle is possible |

**One lock order: employer rows by id, then employment rows.** Every operation that changes employer relationships follows it. Without the reassignment rule, a reassignment would hold its employment row while the foreign-key check waits for the new employer, the reverse of an employer edit. Two employer edits racing two crossing reassignments (X: E2 → E1, Y: E1 → E2) would then deadlock, and PostgreSQL would abort one of them. A regression test reproduces that four-transaction cycle. The old employer is read before it is locked; if a concurrent reassignment changed it in between, nothing is lost, because the employment row is the last lock this transaction waits for.

- If a review holds the employment lock first, the employer edit waits. Under READ COMMITTED it then reads the committed status, so a confirmation or rejection makes it refuse, and a reinstatement lets it proceed.
- If the employer edit holds the locks first, the review waits until the edit commits. The job is then reviewed with the employer as edited.
- Review actions never lock the employer, so the two operations cannot deadlock.

Tests run both sides concurrently on separate connections, pause one inside its transaction, and assert that the other is waiting on a lock (`pg_stat_activity`). Since M2 the locks are taken by the database (guard triggers and functions), so direct SQL follows the same order.

**Deletion (M2, decision A).** Deleting an employer, employment, activity, education record or evidence is refused (`DeletionBlocked`, TR008) when a confirmed or corrected dependent would be deleted or detached. Pending and rejected dependents are detached: only the link becomes NULL. A detachment that would duplicate an unattached skill is refused (TR010). A resume backing a `source_document_supported` record cannot be deleted (TR009). Delete guards lock dependents in the global order (employments, activities, evidence, skills, each by id). Profile and account deletion remove everything.

**Current position and route evidence (M5).**

- **Current position** (`current_position`, pure rule `derive_current_position`). The latest primary employment in stated order decides. If it is transition-eligible (reviewed; mapped to a resolved role, automatically at or above `min_mapping_confidence` or by the candidate's own decision; not stale; full-time by the existing rules; with a known start), it is the current position: role, canonical title, family, effective seniority and mapping basis. Otherwise the position is `unknown` with a reason from that record: `pending_review`, `stale_mapping`, `unresolved_role_mapping`, `low_mapping_confidence` or `latest_employment_ineligible` (with its exclusion reasons), or `no_employment_history` / `no_eligible_primary_employment`. An older eligible role is never used instead. Parallel activity is not primary and never the current position. An ended latest role is still the current position (`is_current_as_stated` says whether it is ongoing).
- **Route evidence** (`route_evidence`). Only the nine PM target roles. It calls the existing reference evidence service with the current role as origin, in `synthetic_test` mode only: each synthetic profile contributes its route from its first episode in the current role to its first later episode in the target, with the foundation's cohort summaries unchanged. All results carry the synthetic notice and labels; no reference identifiers are returned; nothing is written (no pathway runs, recommendations or route steps). When the current position is unknown, no evidence is returned and the reason is. An empty result says there are no observations, nothing more.

**Corrections (M4).** Changing a reviewed, extracted, inferred or legacy value is a *correction* (design decision D3: keep the original structured value, who changed it and when). `correct_record(ctx, kind, record_id, changes)`:

- **Records and fields:** employments, education, activities, evidence, skills and career breaks; scalar content only, as declared by `EmploymentCorrection`, `EducationCorrection`, `ActivityCorrection`, `EvidenceCorrection`, `SkillCorrection` and `CareerBreakCorrection` (unknown fields rejected). Employers, links between records (`candidate_employer_id`, `related_employment_id`, evidence and skill links) and taxonomy references (`skill_id`, `responsibility_area`) are not correctable; nor are review, mapping, provenance, verification, identity or timestamps.
- **States:** pending extracted, inferred or legacy records, and confirmed or corrected records, become `user_corrected` (a review act; sequenced like confirmed). Rejected records: reinstate first (`RecordNotEditable`). The candidate's own pending `user_stated` drafts are edited with `update_*` instead (no history, still pending; `CorrectionNotAvailable` from `correct_record`).
- **History:** every correction appends one event to `public.trace_candidate_corrections` with the changed fields, their previous and corrected values, the prior review status, time and author. The record holds the latest value; a field's original value is its previous value in the earliest event that changed it. History survives rejection and reinstatement and goes only with its record, profile or account.
- **Provenance:** `information_origin`, `source_resume_id`, `extraction_run_id`, `source_location` and `extraction_confidence` never change: they describe where the record came from.
- **Database:** `trace_review.correct_record` (writer-owned, fixed allow-lists and UPDATE statements) locks the row, writes the history entry and the corrected row in one statement; G1 allows content changes on non-draft rows only in that shape; a deferred check requires a matching history entry (same record, profile, owner, transaction and values) for every correction; history is written only by `correct_record` (any other insert, including by the table owner, fails with TR016) and is append-only even for the table owner, while those guard triggers are enabled (the owner can disable triggers).
- **Lock order:** the record row (`FOR UPDATE`), then the history insert, whose foreign-key checks take `FOR KEY SHARE` on the profile row and the owner's `auth.users` row. Profile and account deletion take these locks in the reverse order, so a correction racing the deletion of its own profile or account can deadlock: PostgreSQL aborts one transaction, nothing of it is committed (no partial correction or history entry), and the other completes. An aborted correction surfaces as a generic `CandidateProfileError` and can be retried. Correction against deletion of the record itself is tested in both orders and does not deadlock. Review actions and reassignment keep their M2 lock order.
- **Previous values stay:** a correction never removes the value it replaces. Previous values, possibly sensitive extracted content, remain in correction history (readable by the candidate, `service_role` and the table owner) until the record, its profile or the account is deleted. No single entry can be redacted.
- **Matching is not one-to-one:** the deferred check requires a matching history entry for each correcting update but does not consume it, so two identical corrections of the same record in one transaction could be covered by one entry. `correct_record` always writes one entry per update; this matters only for a future writer path that skipped history.
- **Rollback:** rolling back M4 permanently discards correction history, including original values; corrected records keep their latest values and `user_corrected`. The rollback script requires an explicit acknowledgement and locks the record and history tables before checking it (`data-foundation/docs/ACCESS_CONTROL.md` § Corrections).
- **Not in M4:** reverting, bulk corrections, employer, link and taxonomy corrections.

## Protected input fields (M1)

`models.SYSTEM_CONTROLLED_FIELDS` lists every column no candidate input model may declare (tested across all input models):

| Fields | Why they are system-controlled |
|---|---|
| `role_id`, `seniority_code`, `mapping_status`, `mapping_confidence`, `mapping_note` | Employment role mapping to the taxonomy. `mapping_status` and `mapping_confidence` decide transition eligibility. A candidate-entered employment keeps the defaults (`ROLE-UNKNOWN`, `unresolved`, 0) until it is mapped through `map_employment` (automatic alias match) or `choose_role` (the candidate's decision) since M5, which record `role_mapping_basis`, the alias and the mapped title. Never candidate input otherwise. The stated title stays in `title_raw` |
| `category_code`, `category_mapping_status`, `category_mapping_confidence` | Activity taxonomy mapping ("normalised category, when mapped"), with a status and confidence. No vocabulary exists yet. Candidates classify their own activities through `activity_type` (12 types), which stays candidate input |
| `review_status` | Changed only by the review actions |
| `confirmation_status`, `confirmed_at` | Profile confirmation will be a backend operation with a fingerprint (later milestone). `ProfileUpdate` never declared them; now tested |
| `extraction_status`, `raw_extraction_json` | Extraction state and audit copy |
| `information_origin`, `verification_status`, `extraction_confidence`, `source_resume_id`, `extraction_run_id`, `source_location` | v2.2.0 provenance and verification |
| `participant_confirmed`, `confidence`, `verified`, `evidence_status`, `evidence_source`, `provenance`, `entry_source`, `attribute_provenance` | Legacy self-assertable trust columns. Never written by the service; not writable by `authenticated` or `service_role` (v2.3.0) |
| `owner_user_id`, `candidate_profile_id` | Come only from the trusted `OwnerContext` |

**Kept as candidate input** (the candidate's own description of their records). Every one is editable only while its record is an unreviewed draft. None is a processing, trust, review or verification status.

| Field | Accepted by, on | Effect | Safeguard |
|---|---|---|---|
| `counts_as_full_time_experience` + `classification_note` | `EmploymentCreate/Update`, employment | Self-reported full-time override. Replaces the `employment_type` default in `career_history`; the note is required (CHECK) | Counts only once the record is confirmed: a pending record is excluded (`review_pending`) whatever the override. It never bypasses the role-mapping rules |
| `primary_career_sequence` | `EmploymentCreate/Update`, employment | The candidate's declaration of which concurrent job belongs to the primary career. A parallel record leaves the chain, so it affects which **other** records are adjacent | A pending record stays in the chain as a barrier even when declared parallel; the declaration applies only after confirmation |
| `employment_type` | `EmploymentCreate/Update`, employment | Default full-time basis (`full_time` and `contract` count) | As above |
| `status` | `ActivityCreate/Update`, career activity | The activity's own state: `completed`, `in_progress`, `active`, `expired` (certifications and licences only, CHECK), `not_completed`, `unknown`. Not read by any rule | Not a review or trust value; review actions never touch it |
| `qualification_level` | `EducationCreate/Update`, education | Normalised qualification type, chosen from a CHECK list. Never replaces `qualification_title_raw`. Not read by any rule (education is never sequenced) | – |
| `skill_id` | `SkillCreate/Update`, skill | Optional selection from the skill taxonomy; part of the skill's uniqueness key. Not read by any rule | – |
| `activity_type`, `study_mode`, `completion_status`, `break_reason`, `evidence_type`, `responsibility_area`, employer attributes | the corresponding models | Descriptive | – |

`ClassifiedEmployment` still *reports* `counts_as_full_time_experience` and tenure for pending records, labelled `is_transition_eligible = false`. No code sums experience today; any future experience calculation must use eligible records only.

## Ownership

Every operation is one transaction in `owner_session`:
- `SET LOCAL ROLE authenticated`;
- transaction-local `request.jwt.claim.sub`, `request.jwt.claim.role` and `request.jwt.claims`, all naming the owner;
- `select auth.uid()` must equal the owner before anything else runs (`IdentityMismatch` otherwise). A session-level claim left on a pooled connection can therefore never become the identity.

Tests audit every SQL statement the service issues on candidate data (it must run as `authenticated` with `auth.uid()` = owner) and check statically that candidate SQL lives only in the owner-scoped repository.

The database therefore enforces three things itself, even on the development `postgres` connection:
- **RLS:** only the owner's rows are visible or writable.
- **Column privileges and guards:** only candidate content is writable, only on drafts; review and reassignment only through `trace_review`.
- **Composite foreign keys:** links resolve only within the same profile or owner.

Every query also filters on `owner_user_id` and `candidate_profile_id` explicitly. The role switch is transaction-local; no role or RLS configuration is changed.

**Mutation check.** With the role switch removed, the database-privilege test fails while the ownership tests still pass. This shows the explicit filters and composite keys hold independently of RLS.

**Errors:**

| Error | Raised when |
|---|---|
| `ProfileNotFound` | Also raised for another owner's profile, so nothing is revealed |
| `RecordNotFound` | The record is not in this owner's profile |
| `InvalidReference` | A link points outside the profile or owner, or to an unknown taxonomy value |
| `InvalidRecord` | A CHECK, enum, uniqueness or deferred `sequence_index` violation, raised at commit |
| `PermissionDenied` | A write to a column the owner may not change |
| `InvalidTransition` | A review action not allowed from the record's current status |
| `RecordNotEditable` | An edit of a rejected record (reinstate it first) |
| `CorrectionNotAvailable` (a `RecordNotEditable`) | An edit that would correct a reviewed, extracted, inferred or legacy record |
| `DeletionBlocked` | A delete refused by the database (TR008, TR009, TR010) |
| `IdentityMismatch` | The request identity is missing or is not the trusted owner (TR004, or the owner_session check) |
| `IngestionNotAllowed` | Outside the synthetic-fixture ingestion scope (TR012) |
| `ConsentRequired` | No current `resume_processing` grant under an approved consent document (TR013) |
| `ClassificationConflict` | An evidence classification was refused because the evidence text changed since it was proposed (TR018) |
| `MappingConflict` | A role mapping was refused: the title changed since it was proposed (TR018), or an automatic mapping is not allowed (TR019) |
| `ClarificationRequired` | A product-ownership title mapped to a product-management role needs a PM-ownership responsibility area (TR020) |
| `CorrectionHistoryViolation` | Correction history is written only by `correct_record` and is append-only (TR016), or a correction lacks its history entry (TR017) |
| `IngestionConflict` | The resume was ingested with a different result, the run failed, or the profile already holds records (TR014, TR015) |

Database guard errors carry SQLSTATEs TR001 to TR011 and are mapped by SQLSTATE (`repository.GUARD_ERRORS`).

## Employment versus supporting records

- `employment_inputs(profile)` maps **only** `employment_history` to `career_history.CandidateEmployment`. Education, activities (certifications, projects, volunteering, …), skills and breaks never enter a sequence.
- `sequence_employment_history` reuses the episode-aware `career_history` service. Internships and freelance or founder work stay employment episodes under its existing rules.
- A project's `related_employment_id` does not make it employment.
- **Review eligibility (M1):**
  - Rejected employment is removed before sequencing.
  - Pending employment stays in the chain as an ineligible barrier (`review_pending`). Confirmed A → pending B → confirmed C therefore never yields A → C.
  - Confirmed and legacy corrected employment are eligible, subject to every existing rule.
  - See `CANDIDATE_SEQUENCING.md`, "Candidate review eligibility".

## Limitations and open decisions

1. **No trusted provenance writer yet.** Extraction is a future milestone that will need its own trusted path to set origin, confidence, source references and verification.
2. **Legacy columns.** `participant_confirmed`, `provenance`, `evidence_source`, `evidence_status` and `verified` remain and overlap the v2.2.0 fields. They are never authoritative; new candidate rows leave them NULL (or `false`).
3. **Employer `attribute_provenance`.** NULL for candidate-entered employers since v2.3.0 ("no source provenance recorded"). A future trusted extraction insert must set it, or the employer becomes candidate-editable.
4. **No deletion operation or retention policy** in the service. There is also no explicit detach or correction operation: a reviewed relationship can be removed only by deleting or rejecting the reviewed dependent itself.
5. **No controlled vocabularies.** Activity categories and credentials have none yet, so `category_code` stays empty until one exists.
6. **Review default for candidate-authored records** is `pending_review`, which makes no claim of review. Bulk acknowledgement of the candidate's own entries (decision D1) is a later milestone.

## Remaining gaps after M2

- **Raw credentials.** Any caller with database credentials can forge request claims with `set_config` and act as any candidate. Must be resolved before externally accessible candidate APIs (D7).
- **Development login is the table owner.** `postgres` can disable triggers and, with a trigger disabled, write anything (including correction history), so a code path that skips `owner_session` is not governed. Tests guard against such paths. Before production the backend needs a dedicated non-owner login with SET only on `authenticated` (D7).
- **service_role** keeps cross-owner SELECT and DELETE; its writes are limited to pending drafts. Key custody before production (D7).
- **Hosted checks** (GraphQL and REST invisibility, ownership transfer, account deletion via the Auth API) are verified locally only.
- **Extraction-run deletion.** Deleting an extraction run (service_role or the owner only; not a candidate-facing operation today) clears `extraction_run_id` on the records that cite it, including reviewed ones. A provenance limitation to address before production; not an M2 blocker.
- **Rolling back M2.** Deploy the M1 backend first. Until the database rollback has committed, this service's creates, review actions and reassignment fail closed (M1 uses legacy write paths M2 refuses). See `data-foundation/docs/ACCESS_CONTROL.md` § Review enforcement, Rollback.
- **Milestones:** M3 extracted-draft ingestion (synthetic fixtures); M4 correction history; M5 role mapping, candidate role decisions, current position and read-only route evidence (this milestone). Later, not yet numbered: revert and employer, link or taxonomy corrections, bulk acknowledgement and fingerprinted profile confirmation, production authentication and database roles, API, employer identity.
