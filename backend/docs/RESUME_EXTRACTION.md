# Resume extraction and extracted-draft ingestion (M3)

**Packages:** `app/services/resume_extraction/` (`contract.py`, `fixture.py`) and `CandidateProfileService.ingest_extraction` (`app/services/candidate_profile/service.py`). **Database:** `trace_ingest` functions, data foundation v2.4.0 (`20261111160000_trace_fixture_extraction_ingestion.sql`).

**Scope.** Backend only, synthetic data only. There is no LLM or external provider, no real resume, no upload API, no UI and no conversational clarification. Correcting extracted values is M4 (correction history).

## Flow

1. **Look up (not parse).** `FixtureExtractor(profiles_path, pdf_zip_path).extract(pdf_bytes)` hashes the bytes, identifies one of the 41 curated synthetic resume PDFs by SHA-256, and returns that fixture's curated record as an `ExtractionResult`. It never reads PDF contents; any other document raises `UnknownResume`. This is fixture lookup, not validated resume parsing.
2. **Ingest.** `CandidateProfileService.ingest_extraction(ctx, resume_id, result)` runs three owner-session transactions, each through a `trace_ingest` function:
   - `start_fixture_extraction` checks ownership, fixture scope and consent. It returns the resume's extraction run: a new `queued` run, or the existing succeeded or open run.
   - `ingest_fixture_extraction` inserts every record in one statement, or none. The run becomes `succeeded` with the result's hash, the profile's `extraction_status` becomes `extracted`, the resume's `parse_status` becomes `parsed`, and an unknown profile "as of" date is filled from the document.
   - On any error, `fail_fixture_extraction` marks the run `failed` with a short error code, and the original error is raised. The profile and resume show `failed`.
3. **Review.** The candidate reviews the drafts with the existing actions: `confirm_record`, `reject_record`, `reinstate_record` (M1/M2, `trace_review.review_record`). There is no parallel review path. An extracted value is changed with `correct_record` (M4): the record becomes `user_corrected` and the extracted original stays in correction history; plain `update_*` edits still raise `CorrectionNotAvailable`.

## The contract (`ExtractionResult`)

Provider-agnostic. It mirrors the candidate record types: employers, employments, education, career activities, career breaks, evidence and skills.

- **Per record:** `source_location` (non-blank, for example "Professional experience, entry 2, bullet 1") and `extraction_confidence` (0–1). Employers have no provenance columns in the schema, so their location and confidence are not stored; employers are recorded as `attribute_provenance = 'resume_stated'`.
- **As stated:** original text is kept and partial dates keep their precision. Unknown stays unknown, and nothing is inferred.
- **Not extraction:** taxonomy mappings (`role_id`, `seniority_code`, `mapping_*`, `skill_id`, `responsibility_area`, activity categories), review, verification, origin and legacy trust fields cannot be expressed. `extraction_confidence` is unrelated to `mapping_confidence`.
- **Keys** connect records within one result and are never stored.
- **Validation:** unknown fields are forbidden (`extra="forbid"`). Keys must be unique, references must resolve, and sequence indexes must be unique.
- `ingestion_payload()` produces the JSON the database accepts. The database re-checks every field against its own allow-lists, and a unit test keeps the two in step.

## The fixture extractor

- **Deterministic.** Each of the 41 PDFs maps to exactly one fixture by SHA-256 (tested), the same pairs the database allowlist holds (tested pair for pair).
- **Lookup only.** No PDF text is read. The "extraction" is the curated record the PDF was rendered from.
- **Content.** It returns the structured fixture the PDF was rendered from. Some structured fields are not visibly printed in the synthetic PDFs (`employment_type`, `primary_career_sequence`, the full-time override, employer attributes other than industry). For fixtures, the source record is the ground truth.
- **Employer name.** The name is the pseudonymous employer id printed on the resume.
- **Confidence.** `extraction_confidence` is `1.000`, because the fixture source is exact. Mappings and fixture curation metadata are not extracted.

## Database guarantees (`trace_ingest`, v2.4.0)

| Guarantee | How |
|---|---|
| Single trusted write path | Writer-owned (`trace_ingest_writer`: NOLOGIN, NOINHERIT, NOBYPASSRLS, no members) SECURITY DEFINER functions, empty `search_path`, no dynamic SQL, EXECUTE for `authenticated` only. Identity from `trace_review.request_uid()` (`auth.uid()`). The table-owner connection is not used |
| Drafts, not facts | The insert guard (G0) accepts from the ingest writer only `source_extracted`, `pending_review`, `unverified`, with source resume, run and location, default mappings, and `resume_stated` / `resume_claim` / `resume_explicit` / `resume_extraction` legacy labels. Candidates and `service_role` still cannot insert any of these values |
| Allow-listed payloads | Any field outside the allow-list, at the top level or in a record, is rejected (TR006), not ignored |
| Fixture scope (database) | The profile and the resume must be marked as test fixtures by the table owner: candidates cannot set the flags, and v2.4.0 revokes `service_role`'s INSERT/UPDATE of the resume flag and UPDATE of `content_sha256`. The pair (profile's `SYN-nnn`, resume's recorded `content_sha256`) must be exactly one of the 41 approved pairs in `trace_ingest.approved_fixture_resumes`, and the result must name the same fixture and hash (TR012) |
| Fixture allowlist (database) | 41 (fixture id, PDF SHA-256) pairs derived by `build_seeds.py` from the PDF archive (verified by `--check`), loaded only by the synthetic seed. Empty in production, so ingestion is refused there. Owner-written, writer-read |
| Consent | The owner's current `resume_processing` event must be `granted` under an approved, effective, unretired document. This is checked at start and again at ingestion (TR013) |
| Atomic | One statement; any error rolls back every inserted record (tested with a CHECK violation after employers, employments and education were inserted) |
| Never merges | A profile that already holds records is refused (TR015) |
| Idempotent | One succeeded run per resume (unique index). The same result again is a no-op; a different result is refused (TR014). A failed run cannot be ingested; a new run is started instead |
| Lock order | Resume, then run, then profile (`FOR UPDATE`). The profile lock makes concurrent record inserts into the profile wait |

**Errors:**
- `IngestionNotAllowed` (TR012)
- `ConsentRequired` (TR013)
- `IngestionConflict` (TR014, TR015)
- `InvalidReference` (TR006, malformed or forged payload)
- `RecordNotFound` / `ProfileNotFound` (TR005)
- `InvalidRecord` (a CHECK or uniqueness violation)

## Tests

- **`tests/test_resume_extraction.py`** (no database): 41-PDF mapping, determinism, as-stated values, no mapping or trust fields, payload versus database allow-list drift, and contract validation.
- **`tests/test_fixture_ingestion_integration.py`:**
  - All 41 fixtures end to end: records equal the fixture, provenance is correct, and pending drafts block sequencing. After confirmation and real role mapping (M5: `map_employment`, plus the candidate's decision where no automatic mapping exists), sequencing equals `trace_model.py` run on the mapped fixture; the mapping differs from the curated labels only for SYN-011 (alias vs. curation, pending source verification) and two curated `needs_review` records left undecided. Current position and route evidence are checked for all 41.
  - Review reuse; activities, evidence and skill links.
  - Forged fields; consent (missing, denied, draft, retired, withdrawn mid-run).
  - Scope and ownership; idempotency and never-merge; atomicity and retry.
  - An `owner_session` audit.
- **`data-foundation/supabase/tests/database/trace_fixture_ingestion.test.sql`** (pgTAP): role, ownership, EXECUTE and privileges, plus the database-level behaviour above.

## Limitations

- **Fixture-only scope.** Synthetic fixtures only; real extraction needs an approved provider, a consent text approved by counsel, and its own milestone. Designating a fixture profile or resume needs the table owner (test and development setup only).
- **Database versus application controls.** The database enforces identity, ownership, consent, the approved fixture pair, the extracted-draft shape, allow-listed fields, atomicity and idempotency. It does not see document bytes. `content_sha256` is client-supplied upload metadata, and the content of a result is trusted from the backend (`FixtureExtractor`). Nothing here parses a PDF.
- **Mapping.** Extraction never maps roles: extracted employments arrive `ROLE-UNKNOWN` / `unresolved` and become eligible only after review and mapping through the M5 functions (`docs/ROLE_MAPPING.md`).
- **Employers** do not store per-record location or confidence.
- **Corrections** (M4) cover scalar content; employer, link and taxonomy corrections are not available yet, so extracted employer names cannot be corrected. A corrected extracted value is not erased: it stays in correction history (candidate, `service_role` and table owner can read it) until the record, profile or account is deleted.
- **Consent tie-break.** Two consent events with the same `recorded_at` are ordered by event id, as `trace_ref.v_current_consent` does. Real events come from separate transactions, so they never share a timestamp.
- **Deadlock.** A profile deletion racing an ingestion of the same profile can deadlock: one transaction aborts and nothing is half-written.
- **After a rollback.** Rolling back v2.4.0 keeps ingested records and run rows but drops result hashes. A resume ingested before the rollback is then refused on re-ingestion (`IngestionConflict`), never duplicated.
