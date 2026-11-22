# Access control, storage and retention

## Principles

1. **Least privilege by default.** Migration 09 revokes every privilege that Supabase's default grants give `anon` and `authenticated` on Trace tables. It then grants back only what each table needs, using column-level grants where some columns are system-managed.
2. **Shared career histories are never reachable from a browser.** The reference cohort, analytics views, settings and audit log live in `trace_ref`. That schema isn't exposed through the Data API, has no `USAGE` grant for client roles, and has RLS enabled with no policies, so only `service_role` (which has `BYPASSRLS`) can reach it.
3. **Views never run with their owner's privileges.** Every view is `security_invoker = true` and lives in `trace_ref`.
4. **Every row a user can reach is their own.** Child tables carry `owner_user_id` plus a composite foreign key `(candidate_profile_id, owner_user_id)`, so a row can't point at another user's profile even through a crafted insert.
5. **The service-role key stays on the server.** Use it only in Edge Functions or other backend jobs, never in a browser or mobile bundle.

## Privilege matrix

| Table (schema `public`) | `anon` | `authenticated` (own rows only, via `auth.uid()`) | Written by |
|---|---|---|---|
| `trace_seniority_levels`, `trace_role_families`, `trace_role_taxonomy`, `trace_role_aliases`, `trace_responsibility_areas`, `trace_skill_families`, `trace_skills`, `trace_skill_aliases` | SELECT | SELECT | migrations |
| `trace_consent_documents` | SELECT | SELECT | backend after legal review |
| `trace_consent_events` | – | SELECT; INSERT (`purpose`, `status`, `consent_version`) under an approved, unretired document; no UPDATE/DELETE | user (append-only) |
| `trace_candidate_profiles` | – | SELECT, DELETE; INSERT of `owner_user_id`, `country_code`, `headline`, `resume_as_of_*`; UPDATE of `country_code`, `headline`, `resume_as_of_*`. Not `confirmation_status` / `confirmed_at` (no self-confirmation, v2.3.0), `is_test_fixture`, `fixture_profile_id`, `extraction_status`, `raw_extraction_json` | user + backend |
| `trace_resume_documents` | – | SELECT, DELETE; INSERT of upload metadata; UPDATE of `candidate_profile_id` | user + backend |
| `trace_extraction_runs` | – | SELECT | backend; since v2.4.0 only through `trace_ingest` for candidate ingestion |
| `trace_candidate_employers` | – | SELECT, DELETE; INSERT of owner, profile and the six content columns; UPDATE of the six content columns (v2.3.0). Never `attribute_provenance` | user + backend |
| `trace_candidate_employments` / `_education` / `_career_breaks` / `_evidence` / `_skills` / `_career_activities` | – | SELECT, DELETE; INSERT of owner, profile and record content; UPDATE of record content only. Never `review_status` (only through `trace_review.review_record`), role or activity-taxonomy mapping, `information_origin`, `verification_status`, `extraction_confidence`, source references, legacy trust columns, primary keys or timestamps. An employment's `candidate_employer_id` is set on insert and changed only through `trace_review.reassign_employment` (v2.3.0) | user (content; review via function) + trusted processes (provenance, verification, mapping) |
| `trace_target_role_sources`, `trace_target_role_requirements` | – | SELECT (not withdrawn) | backend |
| `trace_pathway_runs` | – | SELECT, DELETE | backend |
| `trace_pathway_recommendations`, `trace_pathway_route_steps` | – | SELECT | backend |
| `trace_pathway_evidence` | – | SELECT of every column **except** `reference_profile_id`, `reference_dataset_class` | backend |
| `trace_agent_threads` | – | SELECT, DELETE; INSERT (`candidate_profile_id`) | user + backend |
| `trace_agent_messages` | – | SELECT; INSERT of `role = 'user'` messages only | user + backend |
| everything in `trace_ref` | – | – | backend (`service_role`) |

Column-level grants mean client queries must name their columns. On `trace_pathway_evidence`, `select *` fails for clients by design.

Since v2.3.0, `service_role` has the same INSERT/UPDATE column lists as `authenticated` on the eight candidate tables, keeps cross-owner SELECT and DELETE, and has no TRUNCATE, REFERENCES, TRIGGER or MAINTAIN on candidate tables, resumes or extraction runs.

## Review enforcement (v2.3.0, migration 14)

The candidate review rules hold in the database for every role except the table owner. The backend's checks run first and give clearer errors; the database is authoritative.

| Mechanism | What it enforces |
|---|---|
| Column grants (`authenticated`, `service_role`) | Only candidate-authored content can be inserted or updated (matrix above). |
| `trace_guard_insert` (G0) | An insert by any non-owner role carries no system-controlled value: `pending_review`, `user_stated`, `unverified`, no mapping, provenance, verification, extraction or legacy trust value (TR011). Sets `employments.entry_source = 'manual_entry'`. |
| `trace_guard_review_row` (G1), `trace_guard_employer_update` (G2) | Content changes only on the owner's own pending `user_stated` drafts (TR001 rejected, TR002 otherwise), for every role including the owner. Review status changes only as `trace_review_writer` along the M1 matrix (TR003). Employer edits follow the M1 lock protocol (employer, then its employments by id) and need NULL or legacy `participant_confirmed` attributes with every referencing employment pending. |
| `trace_guard_resume_delete` (G3) | A resume backing a `source_document_supported` record cannot be deleted (TR009). |
| `trace_guard_employer_delete`, `_employment_delete`, `_activity_delete`, `_education_delete`, `_evidence_delete` (G4 to G8) | Deleting a parent never deletes or detaches a confirmed or corrected dependent (TR008). Pending and rejected dependents are detached: the foreign key (`ON DELETE SET NULL (link)`) clears only the link column. A detachment that would duplicate an unattached skill fails (TR010). |
| `trace_review.review_record`, `trace_review.reassign_employment` | The only paths that change review status or an employment's employer. Owned by `trace_review_writer`, SECURITY DEFINER, empty `search_path`, no dynamic SQL, EXECUTE for `authenticated` only. They act only for `auth.uid()` (via `trace_review.request_uid()`, a postgres-owned wrapper executable only by the writer) and lock in the M1 order. |
| `trace_review_writer` | NOLOGIN, NOINHERIT, NOBYPASSRLS; no members (postgres keeps only the ADMIN option from creating it); owns only the two write functions; minimal column grants and its own RLS policies (SELECT, UPDATE; no INSERT or DELETE). |

**Referential actions.** Profile and account deletion still remove everything. A guard treats a statement as a foreign-key action only when the trigger depth is above 1 **and** `current_user` is the table owner (PostgreSQL runs referential actions as the referencing table's owner) **and** the change is exactly a declared action **and** the parent row (or, for cascaded deletes, the profile or account) is already gone. Depth alone never relaxes a rule.

**Exposure.** `trace_review` is not in the Data API's exposed schemas or extra search path and carries no pg_graphql comment. Locally (v2.3.0): REST returns PGRST106 for the schema; with pg_graphql 1.6.2 enabled in a rolled-back transaction, its functions are neither listed nor callable through GraphQL. The hosted project must be checked before production (see below).

**Limitations.**

- Any caller holding raw database credentials can forge request claims with `set_config` and act as any candidate. Must be resolved before externally accessible candidate APIs (D7).
- Compromised `service_role` credentials can read and delete any candidate's data. Writes are limited to pending drafts. Must be resolved before production (D7: key custody and rotation).
- The table owner (`postgres`) can disable triggers and write anything. The local backend logs in as `postgres`, so code that skips `owner_session` is not governed. Before production the backend needs a dedicated non-owner login (D7).
- GraphQL and REST invisibility and the ownership transfer are verified locally only. Hosted checks (preview branch first): the migration and its ownership assertions; `postgres` can read `auth.users` (otherwise change the children's `owner_user_id` foreign keys to NO ACTION); account deletion through the Auth admin API with reviewed data; `service_role` cannot set `session_replication_role`; exposed schemas exclude `trace_review`; GraphQL introspection and direct calls with the anon key and a user JWT; advisors show no lint 0028/0029 for `trace_review`.
- A resume backing `source_document_supported` records cannot be deleted until a controlled downgrade exists. The future verification process must also take `FOR KEY SHARE` on the resume when marking a record document-supported. Must be resolved before production.
- Deleting an extraction run clears `extraction_run_id` on the records that cite it (`ON DELETE SET NULL`), including confirmed and corrected records; no guard prevents it. Only `service_role` (and the owner) can delete extraction runs, so this is not a candidate-facing operation in the current implementation. It is a provenance limitation to address before production (for example, a guard or retention rule for runs cited by reviewed records), not an M2 blocker.

**Rollback.** `supabase/rollback/20261111150000_trace_candidate_review_enforcement.rollback.sql` is forward compensation (never applied automatically). It runs as one explicit transaction (its own `BEGIN`/`COMMIT`; run it with `psql -v ON_ERROR_STOP=1 -f`, without `--single-transaction`), restores the exact pre-M2 catalog (rehearsed locally) and changes nothing if any step fails, including when rows written under M2 carry NULL legacy labels.

Deploy the M1 backend **before** running it. Between that deployment and the end of the rollback, candidate writes, creates, review actions and reassignment fail, because M1 still uses legacy write paths that M2 no longer permits. This is a temporary fail-closed write outage, not a data-integrity failure; normal writes resume once the rollback has committed and the M1 backend and pre-M2 database are compatible again. Afterwards mark the migration reverted (`supabase migration repair 20261111150000 --status reverted`).

## Corrections (v2.5.0, migration 16)

A candidate corrects scalar content of a reviewed, extracted, inferred or legacy record through one controlled path. Every correction is kept in append-only history.

| Mechanism | What it enforces |
|---|---|
| `trace_review.correct_record(kind, profile, record_id, changes)` | Owned by `trace_review_writer`, SECURITY DEFINER, empty `search_path`, fixed per-kind allow-lists and UPDATE statements (no dynamic SQL), EXECUTE for `authenticated` only, acts for `auth.uid()` only. Unknown or protected fields are rejected (TR006). Rejected records: TR001; the candidate's own pending `user_stated` drafts: TR002 (edited instead). Locks the record, writes the history entry and the corrected row (`user_corrected`) in one statement. Source and extraction provenance never change |
| Correctable fields | Scalar content of employments, education, activities, evidence, skills and career breaks. Not: employers, links between records, taxonomy references (`skill_id`, `responsibility_area`), review, mapping, provenance, verification, identity, timestamps. The writer's UPDATE grants are exactly these columns (plus its v2.3.0 `review_status` and employer-link grants) |
| G1 (replaced) | Content changes on non-draft rows only as `trace_review_writer` and only together with `review_status = 'user_corrected'`; never from `rejected`, never on a pending `user_stated` draft, never on links or taxonomy. Every other rule unchanged |
| Deferred check `trace_require_correction_history` | For every update that leaves a record `user_corrected` after a transition into it or a change of a correctable field, a history entry of the same record, profile, owner and transaction with exactly matching fields, previous and corrected values must exist (TR017). Unrelated or forged entries do not satisfy it |
| `public.trace_candidate_corrections` | Written only by `trace_review_writer`, whose only inserting function is `correct_record`: `authenticated`, `anon` and `service_role` have no INSERT privilege (42501), and an insert guard rejects every other inserting role, including the table owner (TR016), so history cannot be fabricated, backdated or given another transaction id. Append-only for every role including the table owner (TR016: no UPDATE, TRUNCATE or direct DELETE). Rows disappear only through the cascades of record, profile or account deletion (per-kind generated foreign keys). Candidates read their own history (RLS); `service_role` reads all of it (it bypasses RLS, as for the record tables); the writer reads and appends only the caller's own (RLS scoped to `request_uid()`). The insert and append-only guards are triggers, so they hold while enabled: the table owner can disable them (see Limitations) |

**Lock order.** `correct_record` locks the record row (`FOR UPDATE`), then inserts the history entry. Its foreign-key checks take `FOR KEY SHARE` locks on the profile row and on the owner's `auth.users` row. That is the reverse of profile and account deletion, which lock the profile (or account) first and then cascade to the records. A correction racing the deletion of its own profile or account can therefore deadlock. PostgreSQL then aborts one of the two transactions: nothing of the aborted transaction is committed (no partial correction, no history entry without its correction), and the other completes. If the correction is the one aborted, the backend raises a generic `CandidateProfileError` (deadlock detected); the call can be retried. Review actions and reassignment keep their v2.3.0 lock order; only corrections add the profile and account locks.

**Limitations.**
- **Administrative access.** The table owner can still disable triggers (as in v2.3.0), and with the insert guard disabled it could write arbitrary history. The local backend logs in as the table owner (D7).
- **Previous values are kept.** A correction never removes the value it replaces. Previous values, which may include sensitive extracted content (for example a career-break note read from a resume), stay in correction history, readable by the candidate, `service_role` and the table owner, until the record, its profile or the account is deleted. There is no redaction or purge of a single entry.
- **Matching is not one-to-one.** The deferred check requires that each correcting update has a matching history entry; it does not consume entries. If one transaction made the identical correction of the same record twice, a single entry could satisfy both updates. `correct_record` always inserts exactly one entry per update, so this cannot arise through it; it would matter only for a future writer path that skipped history.
- **Scope.** No revert, bulk, employer, link or taxonomy corrections.
- **Rollback is lossy.** Rolling back v2.5.0 (`supabase/rollback/20261111170000_trace_candidate_corrections.rollback.sql`) permanently discards all correction history, including the original values it preserves; corrected records keep their latest values and `user_corrected`. Re-applying the migration starts with empty history. The script therefore refuses while history exists unless the operator sets `trace.rollback_discard_correction_history=on` for that session. It takes its locks (the six record tables, then history, ACCESS EXCLUSIVE) before that check, so a correction cannot commit history between the check and the drop: in-flight corrections finish first and are counted, later ones wait and then fail closed.

## Evidence classification (v2.7.0, migration 18)

A candidate classifies an evidence record they own (extracted or entered) with one or more responsibility areas, only through `trace_review.classify_evidence_areas(profile, evidence, areas, expected_text)`. The classification is the candidate's assertion (`area_classification_basis = 'candidate_selected'`), never verification.

| Mechanism | What it enforces |
|---|---|
| `trace_review.classify_evidence_areas` | Owned by `trace_review_writer`, SECURITY DEFINER, empty `search_path`, fixed SQL, EXECUTE for `authenticated` only, acts for `auth.uid()` only, locks the evidence row. Refuses rejected evidence (TR001; reinstate first), a text other than the record's (TR018), an empty list, a NULL or unknown area (TR006). Duplicates are removed and the list is stored sorted; a classification replaces the previous one. Writes only `area_classification`, `area_classification_basis` and `area_classified_text`: never review or verification status, provenance, sources, text, type, links, the candidate-content `responsibility_area` column or any employment |
| Staleness | `area_classified_text` keeps the exact text classified; the generated `area_classification_is_stale` is true once `evidence_text` changes (draft edit or M4 correction). A stale classification must not be used until the candidate classifies the new text |
| Privileges | Only the writer can update the three classification columns; `authenticated`, `anon`, `service_role` and `trace_ingest_writer` have no INSERT or UPDATE on them. CHECK constraints (basis is `candidate_selected`; areas, basis and text set together; at least one non-NULL area) bind every role including the owner. G0, G1 and the M4 triggers are unchanged |

**Lock order.** The function locks only the evidence row (no foreign-key change, so no other row is locked); a classification racing a correction or deletion of the same evidence waits for it or is waited for.

**Limitations.** `trace_responsibility_areas` has no active/retired flag, so every taxonomy area is accepted. The table owner can write the classification columns within the CHECKs (administrative access); area membership in the taxonomy is checked by the function, not by a constraint. Rolling back (`supabase/rollback/20261111190000_trace_candidate_evidence_classification.rollback.sql`) permanently discards classifications and requires `trace.rollback_discard_area_classifications=on` when any exist; it locks the evidence table before that check.

## Role mapping (v2.6.0, migration 17)

A candidate employment gets a taxonomy role only through two `trace_review` functions. Both are owned by `trace_review_writer`, SECURITY DEFINER with an empty `search_path` and fixed SQL, executable by `authenticated` only, act for `auth.uid()` only, lock the employment row (`FOR UPDATE`), refuse rejected records (TR001) and never change `review_status`.

| Mechanism | What it enforces |
|---|---|
| `trace_review.apply_alias_mapping(profile, employment, alias_id, expected_title)` | Automatic mapping. The database re-checks the alias itself: it exists (TR006), it matches the record's normalised title exactly, it is not context-required, it is the only alias row for that title, and its role is active and resolved (TR019 otherwise). Role, seniority hint and confidence come from the alias row. Status `auto_high_confidence`, basis `alias_match`. Never replaces a current candidate decision or a mapping set outside these functions (TR019) |
| `trace_review.choose_employment_role(profile, employment, role_id, expected_title, ownership_areas)` | The candidate's own decision. Any active, resolved role; never `ROLE-UNKNOWN`, retired or unresolved roles (TR006). Status `confirmed`, basis `candidate_selected`, `mapping_confidence` NULL (a candidate decision is not an independently verified confidence). A Product Owner-family title is mapped to a product-management role only with at least one PM-ownership responsibility area (TR020), stored in `role_mapping_ownership_areas`. The family is detected by `trace_util.is_product_owner_title` (the words `product owner` bounded by the title's ends or non-alphanumerics, so qualified titles such as "Product Owner (Payments)" are covered) or by a product-ownership alias |
| Product Owner titles, automatic | `apply_alias_mapping` never maps a Product Owner-family title to a product-management role (TR020), even through an alias that is not context-required: that mapping needs the candidate's decision with the clarification |
| Title normalisation contract v1 | `trace_util.normalize_title` (immutable, executable by the writer only): runs of 29 listed whitespace code points become one ASCII space, the ends are trimmed, only ASCII A–Z are lower-cased, nothing else changes; independent of the database locale. The backend implements the same contract. A CHECK on `trace_role_aliases` keeps `alias_normalized` equal to it for every current and future alias row |
| Expected title | Both functions refuse when the record's title differs from the title the caller saw (TR018), so a concurrent title change can never receive a mapping made for another title |
| Provenance and staleness | `role_mapping_basis`, `role_mapping_alias_id`, `mapped_title_raw` (the exact title mapped), `role_mapping_ownership_areas`; `mapping_is_stale` (generated) is true once `title_raw` differs from `mapped_title_raw`, after a draft edit or an M4 correction. A stale mapping is kept and visible but not eligible for sequencing until a fresh mapping decision. CHECK constraints keep these columns consistent for every role, including the owner |
| Privileges | Only `trace_review_writer` has UPDATE on the mapping columns (and its M2/M4 columns); `authenticated`, `anon`, `service_role` and `trace_ingest_writer` have no INSERT or UPDATE on any of them. The writer reads the alias, taxonomy and responsibility-area lookups through its own read policies. G0, G1 and the M4 triggers are unchanged: mapping columns are not candidate content |

**Lock order.** The functions lock only the employment row, plus foreign-key share locks on lookup rows (taxonomy, alias, seniority) that are written only by migrations. No profile or account lock is taken, so mapping adds no lock-order interaction with profile or account deletion; a mapping racing a deletion of its record waits, and the later statement either finds no record (TR005) or deletes the mapped record.

**Scope.** Candidate-selected mappings apply to that candidate's own sequence only. Candidate data never enters `trace_ref` automatically, and no mapping function reads or writes it; a future contribution flow must not carry candidate-selected mappings into reference evidence.

**Limitations.**
- **Administrative access.** The table owner can write mapping columns directly (within the CHECK constraints), as for every system column since v2.3.0.
- **Product Owner detection is word-based and conservative.** It does not recognise abbreviations ("PO"), joined or hyphenated forms ("ProductOwner", "Product-Owner"), plurals or other languages. Such a title mapped to a product-management role needs no clarification.
- **Characters outside the normalisation contract** (for example zero-width spaces or non-ASCII capitals) are kept, so the title matches no alias and needs a candidate decision. It fails closed, but such titles cannot be mapped automatically.
- **Rollback.** `supabase/rollback/20261111180000_trace_candidate_role_mapping.rollback.sql` resets every mapping made through these functions to unmapped (candidate decisions and clarifications are permanently discarded; review status and titles are kept) and requires `trace.rollback_discard_role_mappings=on` when any exist. It locks the employment table before that check.

## Extracted-draft ingestion (v2.4.0, migration 15)

Extracted records reach a candidate profile through one controlled path. Synthetic fixtures only (M3).

| Mechanism | What it enforces |
|---|---|
| `trace_ingest.start_fixture_extraction`, `ingest_fixture_extraction`, `fail_fixture_extraction` | Owned by `trace_ingest_writer` (NOLOGIN, NOINHERIT, NOBYPASSRLS, no members; postgres keeps only the ADMIN option), SECURITY DEFINER, empty `search_path`, no dynamic SQL, EXECUTE for `authenticated` only. They act only for `auth.uid()` (`trace_review.request_uid()`). Helpers `assert_ingestion_allowed` and `assert_allowed_keys` are writer-owned and executable by nobody else |
| Writer privileges | SELECT on the candidate tables, resumes, runs and consent; INSERT of the extracted-draft columns; UPDATE only of run status columns, `trace_resume_documents.parse_status` and the profile's `extraction_status` and `resume_as_of_*`. No DELETE, no update of any candidate record, no review, verification or mapping column. Own RLS policies scoped to `request_uid()` |
| Insert guard (G0, replaced) | Unchanged for every other role. From `trace_ingest_writer` it accepts only `source_extracted` / `pending_review` / `unverified` rows carrying source resume, run and location, default mappings, and the `resume_stated` / `resume_claim` / `resume_explicit` / `resume_extraction` legacy labels (TR011 otherwise) |
| Scope (TR012), database-enforced | The profile is marked a test fixture and names `SYN-nnn` (only the table owner can set either), and the resume is marked a test fixture (only the table owner can set it: candidates never could, and v2.4.0 revokes `service_role`'s INSERT/UPDATE of `is_test_fixture` and UPDATE of `content_sha256`). The pair (profile's fixture id, resume's recorded `content_sha256`) must be exactly a row of `trace_ingest.approved_fixture_resumes`, and the result must name the same fixture and hash. Neither a fixture id nor a hash counts on its own |
| Fixture allowlist, database-enforced | `trace_ingest.approved_fixture_resumes`: the 41 (fixture id, PDF SHA-256) pairs, derived by `scripts/build_seeds.py` from `data/synthetic/synthetic_resume_pdfs.zip` (checked by `build_seeds.py --check`; also in `data/synthetic/synthetic_resume_pdf_hashes.csv`) and loaded by the synthetic seed only. Production never runs the seed, so its allowlist is empty and every ingestion is refused. Only the table owner writes it; only `trace_ingest_writer` reads it |
| Consent (TR013) | The owner's current `resume_processing` event is `granted` under an approved, effective, unretired document, at start and again at ingestion. The fixture extractor makes no external call, so `model_provider_processing` is not required in M3 |
| Integrity | Allow-listed payload keys (TR006); atomic ingestion; never into a profile holding records (TR015); one succeeded run per resume and idempotent re-ingestion by result hash (TR014); one open run per resume |

**Limitations.**

- **No PDF parsing.** M3 identifies a synthetic fixture by SHA-256 and returns its curated record; nothing reads or parses document contents. The database binds identities and recorded hashes. It never sees document bytes and does not verify that the stored object's bytes match `content_sha256`, which is upload metadata supplied by the client. A future upload milestone must compute it on the server from the stored object.
- **Application-level, not database-enforced:** the content of an extraction result. The database checks that a result is shaped as an extracted draft, allow-listed, consented, and bound to an approved fixture pair. That the records equal the fixture is guaranteed by the backend's `FixtureExtractor` and its tests, not by the database.
- Designating fixture profiles and resumes needs the table owner. That is test and development setup only, never an ingestion path.
- Real resumes need an approved provider and consent text, and their own milestone.
- No trusted role-mapping process exists yet, so extracted employments stay unmapped.
- Rollback: `supabase/rollback/20261111160000_trace_fixture_extraction_ingestion.rollback.sql` runs as one explicit transaction, restores the exact v2.3.0 catalog (rehearsed locally, including over ingested data), and keeps ingested records. Deploy a backend that does not call `ingest_extraction` first; until the rollback commits, ingestion keeps working, and afterwards it fails closed. After a rollback and re-apply, resumes ingested earlier stay ingested and are not ingested again.

## Runtime identity and real reference data (v2.8.0, migrations 19–21)

- The API logs in as `trace_app_runtime` only: no superuser, no BYPASSRLS, owns nothing. Owner work runs in a
  transaction as `trace_app_user` (SET, not inherited) with the verified user id set transaction-locally; reference
  reads use the inherited, SELECT-only `trace_reference_reader`. The admin identity (`postgres`) is for migrations,
  scripts and tests.
- `authenticated` (the Data API role) has SELECT on its own rows and nothing else on private tables; it cannot call
  any writer function. PostgREST cannot assume `trace_app_user`.
- `trace_private` is reachable by the admin identity only. Eligibility of real reference profiles is exposed to the
  reader solely as profile ids through `trace_ref.eligible_real_profile_ids()`.
- Rate-limit counters (`trace_ops`) are readable and writable by `trace_app_runtime` only.
- v2.9.0: `trace_contrib` contains functions only. The runtime may claim queued contributions, read a user's
  contribution context and identity (for in-memory de-identification), record outcomes, quarantine a resume and log
  processing; `trace_app_user` may read only the caller's own contribution state and processing outcome. Clients have
  no access. `trace_private` remains admin-only. A user's own resume is ingested only after
  `trace_ingest.admit_deidentified_resume` (runtime-only) recorded that de-identification passed.
- v2.10.0: identity markers (`trace_private.identity_markers`) are readable by no role but the admin identity. The
  runtime writes the caller's own markers (`trace_contrib.store_identity_markers`: request identity, own non-fixture
  profile) and reads a user's markers only through `contribution_identity`, in memory. Retry handling
  (`report_contribution_failure`, `release_contribution_requests`, the bounded `claim_contribution_requests`) is
  runtime-only; `trace_private.retry_contribution` is executable by the admin identity only (no grant to any role).
- v2.11.0: markers are stored only sealed (AES-256-GCM, key outside the database); the database role that holds them
  cannot read them. The runtime may forget the caller's own markers when no admitted or held resume needs them. The
  orphan-marker sweep and queue-event retention (`trace_private.purge_*`) are admin-only.
- v2.12.0: sharing preferences (`trace_private.sharing_preferences`, `sharing_preference_events`) are readable and
  writable by no client role. `trace_app_user` reads and sets only the caller's own preference through
  `trace_contrib.my_sharing_preference`, `acknowledge_data_use_notice` and `set_sharing_preference` (request identity).
  A `private` preference makes `contribution_consent_active` false, so nothing is queued, processed or marked eligible
  for that user; the preference is never a consent decision.
- v2.30.0: transition goals (`trace_private.transition_goals`) follow the v2.29.0 pattern: no client grant;
  `trace_app_user` reads and sets only the caller's own goal for a target through `trace_plan.my_transition_goal` and
  `trace_plan.set_transition_goal` (request identity), owned by `trace_preference_writer` (select, insert, update and
  delete on the table under owner-scoped policies). Plans never read the table.
- v2.29.0: target-company preferences (`trace_private.target_company_preferences`) are readable and writable by no
  client role (`service_role` included). `trace_app_user` reads and replaces only the caller's own list for a target
  role through `trace_plan.my_target_companies` and `trace_plan.set_target_companies` (request identity), owned by
  `trace_preference_writer` (NOLOGIN, no BYPASSRLS, no usable members; select, insert and delete on the table under
  owner-scoped policies only). Plans never read the table. Rows go with the account (cascade).
- v2.13.0: `trace_app_user` may call `trace_contrib.my_reference_profile_ids()`, which returns only the caller's own
  contributed reference profile ids (request identity). The backend uses them to exclude those profiles from the
  caller's own evidence; they are never returned to a client.
- v2.16.0 / v2.17.0: target-role knowledge.
  - `trace_capability_requirements` and `trace_requirement_source_evidence`: `authenticated` may SELECT; anon none; no
    client writes. Evidence of a withdrawn source is hidden, like the source itself.
  - `trace_util.record_posting_verification` (D26) is not executable by any client or runtime role: administrators
    only.
  - `trace_util.posting_effective_status` is executable by `authenticated` (pure, no data access).
  - The content is curated public reference material (no user data).
- v2.24.0 – v2.27.0: Phase 4 remediation.
  - `trace_digital_role_candidates`, `trace_education_fields`, `trace_employer_criterion_rules`: `authenticated` may
    SELECT (rules of withdrawn sources hidden); no client writes.
  - `trace_plan_writer` may also SELECT fingerprinted snapshots (to reuse a shared state) and insert action labels.
  - D38: `service_role` (BYPASSRLS; usable by `authenticator` and `postgres`) has no INSERT, UPDATE, DELETE or TRUNCATE
    on the plan tables or `reference_snapshots`. Default privileges still grant new tables in `public` and `trace_ref` to
    `service_role`, so every migration revokes them explicitly.
- v2.21.0 – v2.23.0: transition planning (Phase 4).
  - `trace_bridge_roles`, `trace_action_templates`: `authenticated` may SELECT; no client writes.
  - `trace_plan_writer`: NOLOGIN, no BYPASSRLS, no members that can use it. It inherits `trace_reference_reader`
    (read-only) to count the reference cohort, and holds only the INSERT grants (plus `status`/`completed_at` UPDATE
    on its own running runs) that `trace_plan.record_transition_plan` needs, each limited by RLS to the request
    identity. Its one trace_ref policy is INSERT on `reference_snapshots` (counts and settings only).
  - `trace_plan.record_transition_plan(jsonb)`: EXECUTE for `trace_app_user` only (never `authenticated`, `anon` or
    `service_role`, so no Data API request can call it). Owners read their runs, routes, steps, evidence and actions
    through the existing owner SELECT policies (and the new one on `trace_pathway_actions`).
  - A completed run is immutable (TR027). Owners may still delete their runs; account and profile deletion cascade.
- v2.18.0 – v2.20.0: taxonomy v2.
  - The new classification columns on `trace_candidate_evidence` (`area_classification_none`,
    `area_classification_taxonomy_version`, `area_qualifiers`, `area_classified_at`) are readable by the owner and
    writable only by `trace_review_writer`.
  - `trace_review.classify_evidence_v2` and `trace_review.clear_evidence_classification`: writer-owned SECURITY
    DEFINER, EXECUTE for `trace_app_user` only, request identity, the caller's own non-rejected evidence only.
  - `trace_capability_requirements.area_conditions` and the new area columns follow their tables (read-only for
    `authenticated`, no client writes).
- v2.15.0: ordering events (`public.trace_candidate_order_events`).
  - Access: owners SELECT their own rows; nobody writes them except `trace_review.move_employment` (writer-owned,
    EXECUTE for `trace_app_user` only). Append-only (TR023); removed only by the cascade from the role, profile or
    account.
  - A position-only change of a reviewed or extracted role must come with its event in the same transaction
    (`guard_review_row` branch (f)). It never changes the review status, and it is never a correction. Rejected roles
    cannot be moved.
- v2.14.0: profile update proposals (`public.trace_profile_update_proposals`).
  - Access: owner-only through RLS (`(select auth.uid())`). `authenticated` (the Data API) may only SELECT its own
    rows. `trace_app_user` inserts open proposals and decides them through column grants (`status`, `proposed`,
    `resulting_record_id`, `decided_at`). Nobody but the table owner may delete; proposals are removed by the cascade
    from the profile and the account.
  - Rules (`trace_util.guard_update_proposal`, error TR022):
    - a proposal starts open, and a decision is final;
    - only the decision columns change;
    - the proposed content is cleared when decided (CHECK);
    - a named record (attach-to, would-change or resulting) must belong to the same owner's profile;
    - an accepted update names the record it changed;
    - one open proposal per profile and content fingerprint.
  - Accepting writes the record through the existing candidate paths: drafts as `user_stated`, or a correction with
    history. Nothing about review, origin or verification changes here.

## Storage

| Setting | Value |
|---|---|
| Bucket | `trace-resumes`, **private** |
| Object key | `<owner_user_id>/<resume_id>.pdf` or `.docx` (also enforced by a CHECK on `trace_resume_documents.storage_object_path`) |
| Allowed types | PDF, DOCX; maximum 10 MB |
| Client policies | upload, read and delete within the caller's own folder; **no update** (uploads are immutable) |
| Downloads | short-lived signed URLs only; never public URLs |

## Retention and deletion

- **No retention default (v2.31.0).** `trace_resume_documents.retention_until` has no default and is NULL for new rows: no
  retention period is approved for resume metadata, and no job acts on the column. Rows written before v2.31.0 keep the
  30-day value they were given; it is not enforced. A CHECK still caps any value at 365 days. An automatic retention policy
  needs an explicit product decision.
- **Resume import drafts (v2.31.0).** `trace_private.resume_import_drafts` has no client grant. The owner role reaches only its
  own draft through `trace_import.save_draft`, `my_draft` and `delete_draft` (owned by the NOLOGIN `trace_import_writer`,
  owner-scoped policies). A draft is deleted on confirmation, discard, replacement, withdrawal of resume processing (trigger)
  or with the account. It is never deleted because of its age.
- **Identity markers (v2.31.0).** Stored only while the owner's contribution consent is active (`store_identity_markers`
  refuses otherwise); deleted at once when it is withdrawn or declined (trigger `trace_consent_privacy`).
- **Expiry job (proposed, not built).** A scheduled backend job (Edge Function with the service role, triggered by Supabase Cron) runs on a schedule:
  - reads `trace_ref.v_resumes_due_for_deletion`;
  - deletes each object through the **Storage API** (not with SQL against `storage.objects`);
  - sets `deleted_at`;
  - writes an `access_log` row.

  This job is a launch prerequisite and isn't part of these migrations.
- **Account deletion.** Deleting the `auth.users` row cascades to the user's candidate data, resume metadata, runs, threads and consent events, including reviewed records (v2.3.0 guards allow cascades only once the profile or account is gone). Storage objects must be removed by the same backend job. Reference profiles they contributed lose `contributor_user_id`, which immediately makes them ineligible.
- **Consent withdrawal.** A `withdrawn` event removes the profile from every future empirical calculation at once. Whether to also delete the de-identified profile, and how to handle backups and published aggregates, are policy decisions recorded in `INTAKE_AND_CONSENT.md`.

## Audit

The backend writes to `trace_ref.access_log`, which is append-only, whenever it:
- reads or exports reference data;
- issues a signed URL for a resume;
- checks consent for analytics.

## Environment separation

| Environment | Migrations | Synthetic seed (`supabase/seeds/synthetic_fixtures.sql`) | Real participant data |
|---|---|---|---|
| Local (`supabase start`) | yes | yes | no |
| Preview branches | yes | yes | no |
| Dedicated test project | yes | yes | no |
| Production | yes | **no** (the seed refuses to run once real reference profiles exist) | yes |

The 18 uploaded third-party resume fixtures live only in git-ignored `local_only/` on the reviewer's machine. They are never imported anywhere; the database rejects their dataset class.
