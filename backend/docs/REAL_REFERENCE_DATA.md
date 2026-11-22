# Consented real reference data (Phase 6)

## Three data categories

| Category | Where | Who can read it | Enters statistics |
|---|---|---|---|
| Synthetic reference (41 profiles) | `trace_ref.profiles`, `dataset_class = 'synthetic_test_fixture' AND synthetic_demo_allowed` | the backend reader only | the `synthetic` cohort only |
| Consented real reference | `trace_ref.profiles`, `dataset_class = 'consented_real_reference'`, admitted through `trace_private.contributions` | the backend reader only (aggregates leave the API, rows never do) | the `consented_real` cohort only, while eligible |
| A user's private profile | `public.trace_candidate_*` | that user (through the API) | never; a copied synthetic sample is not a reference profile |

The cohorts are never combined and never substituted for each other. The API serves only the cohorts listed in
`TRACE_ENABLED_COHORTS`.

## Identity stays outside the analytical data

`trace_private` (no access for any client, the runtime login or the reference reader) holds:
- `contributors`: a random `contributor_id`, and either the app user's id or an opaque `participant_reference` chosen
  by the product owner (a CHECK refuses anything shaped like an email). Trace never stores the participant's name.
  The product owner keeps the person-to-reference list in their own consent records, outside Trace.
- `consent_attestations`: basis (`product_owner_attestation`, `signed_consent_form`, `recorded_verbal_consent`; an
  owner attestation is never labelled a signed form), purposes, evidence reference, `original_consent_date` and
  `original_notice_version` only when evidenced (otherwise NULL, never estimated), `recorded_at` (the import), the
  verifier and `verified_at`, and withdrawal.
- `contributions`: the random reference `profile_id`, status, de-identification status and a report of counts and
  checklist results only.
- `import_log`: outcome and reason codes for every import attempt.

Reference profile ids are random (`CRR-` + 12 random digits), never derived from a name, email or file. Analytical
rows carry no contributor identity.

## Eligibility

A real profile counts only while ALL hold: dataset class `consented_real_reference`; review status reviewer- or
participant-confirmed; an `admitted` contribution with de-identification `passed`; and a current consent: either a
`verified`, unwithdrawn attestation covering all four purposes (`deidentified_career_analysis`,
`aggregate_research`, `benchmarking`, `recommendations`) or, for an app user, an active
`deidentified_shared_dataset` consent under an approved notice. This is computed by
`trace_ref.eligible_real_profile_ids()` and used by `trace_ref.v_eligible_profiles` (data mode `empirical`).

## Withdrawal

- App user: a `withdrawn` or `denied` contribution decision (`POST /api/v1/consent/contribution`) excludes and deletes
  their contributed reference profiles in the same transaction (database trigger). Their private profile and personal
  analysis are unaffected.
- Participant: `select trace_private.withdraw_participant('<participant reference>', '<withdrawal evidence reference>');`
  as the admin identity.
- Kept for audit: the attestation (with `withdrawn_at`), the contribution row (status `withdrawn`, `deleted_at`, no
  profile link) and the import log. No career data remains. Statistics are computed per request, so there is no cache
  to invalidate.

## De-identification policy

Removed wherever it appears (any field, any position, PDF text, links and annotations, metadata, free text):
full name and name parts, personal emails, phone numbers, LinkedIn/GitHub/GitLab/X/Instagram/Facebook/Medium/Behance/
Dribbble/Kaggle/Stack Overflow and any other personal URL, social handles, government and personal ID formats
(Aadhaar, PAN, passport, SSN), address lines, and any field outside the canonical career format (name, contact,
photo, signature, references, links, ...). PDF metadata, images (photos, signatures, QR codes) and the file name are
never stored at all.

Retained: universities and colleges, employers and organisations, degree and qualification names, grades and scores,
graduation and employment dates, job titles and seniority, industries, responsibilities, skills, achievements,
certifications, projects and career transitions.

Detection is deterministic (no external service, no AI). The participant's known identity (supplied only during
restricted processing, never stored) is used to find their name in free text. Identifiers found in the PDF (text,
link annotations) are added to it automatically. A second scan of the output must find nothing; otherwise the record
fails. A name part that also appears in a retained employer or institution name (for example a family business) is
kept but sent to human review; such a record is admitted only after a named reviewer approves.

Residual risk: rare combinations of retained attributes (institution + employer + title + year) can indirectly
identify someone. Mitigations: rows never leave the database through the API; only aggregates with at least 5
profiles per shown category; complementary suppression; no cross-tabulations; access to `trace_ref` limited to the
backend reader and the admin identity.

## Importing the 18 participant resumes

Status: **not imported.** No source files exist in the approved location (`data-foundation/local_only/`, git-ignored)
or anywhere else in the project. The pool is empty; nothing has been fabricated.

To proceed:
1. Create `data-foundation/local_only/real_reference/` with mode 700 (owner only).
2. For each participant, place the original PDF and a structured career record (JSON, canonical format: see
   `app/services/deidentification/pipeline.py` `FIELDS` and `tests/test_deidentification.py::record`). The structured
   record is prepared in this restricted environment by a person; Trace does not parse arbitrary resumes.
3. Write `manifest.json` (see `scripts/import_real_reference.py`): opaque participant reference, the identity used
   for detection, and the consent attestation as actually evidenced (dates and notice versions only if known),
   verified by a named person; a reviewer for each record.
4. `python -m scripts.import_real_reference --dry-run`, then without `--dry-run`. Output is counts and reason codes.
5. Records that fail or need review are quarantined (nothing stored but the reason codes). Fix and re-run.
6. Retention: the originals stay only in the restricted folder until the import is verified; then the operator
   deletes the PDFs, structured files and manifest (the manifest holds identities). The script never deletes the only
   copy and writes no temporary files.
7. Enable the cohort when at least 5 profiles are eligible: `TRACE_ENABLED_COHORTS=synthetic,consented_real`.

Never store originals in Git, public storage or Supabase Storage, never send them to external AI or resume services,
never log their contents.

## A user's own resume (Phase 6.1)

`POST /api/v1/real-resumes` (server switch `TRACE_REAL_RESUME_UPLOADS`): the PDF is read in memory only (no file is
written, the bytes and the file name are not stored), parsed locally (`real_parser.py`: sections, dated experience
entries, bullets, education with institution and grade, skills, certifications, projects; no AI, no external
service), and de-identified with the SAME pipeline as the participant import, using the header's name and contact
details and the account's own email and name. Only a `passed` result is ingested into the user's private profile,
through the same database ingestion path as fixtures (admission record, then pending drafts the user reviews).
`needs_review` is held, already de-identified, in `trace_private.resume_quarantine` for a person
(`python -m scripts.review_quarantine`); `failed` stores nothing but the outcome. Grades are kept in the education
notes of the private profile (`Grade: ...`) and become structured grades in a contribution.

## Automatic contributions (Phase 6.1)

When a user has (1) a resume processed through de-identification, (2) every non-rejected record reviewed, and (3) a
current, explicit contribution consent, the backend contributes automatically. The queue lives in the database (one
row per user, deduplicated; leased claims with SKIP LOCKED), so restarts, retries and several workers neither lose
nor duplicate work. Each pass rebuilds the record from the reviewed private profile (rejected items are left out;
only taxonomy-mapped skills enter the pool), de-identifies it again, and calls `record_contribution_outcome`, which
re-checks consent, fixture status and residual emails or links itself. One admitted contribution per person: a
profile change updates it in place (same random ids); an unchanged record is a no-op; a failure or new ambiguity
suspends it immediately. Synthetic demo profiles can never contribute.

Manual review: an ambiguous record (and every contribution of a profile whose upload needed review) is
`quarantined` until a reviewer approves exactly that content (`review_quarantine approve-contribution`); any later
change needs a new review. States shown to the user: not enabled, awaiting resume, awaiting review, pending,
processing, included, manual review, failed, withdrawn.

Withdrawal (`POST /api/v1/consent/contribution` with `withdrawn` or `denied`) removes the reference rows in the same
transaction and records the state; a later grant creates a NEW contribution only after it passes again. Deleting the
account erases the contributor, contributions and reference rows. The participant batch import (`import_real_reference`)
uses the same de-identification pipeline; there is no separate process for the 18 participants.

## Identity markers and edits (Phase 6.2)

The private profile is de-identified once, at upload. A user can later type identifiers into corrections and edits, and
an email/password account usually has no name, so the account alone cannot reveal the user's name at rebuild time.
Therefore the identity used at upload (the resume header's name, emails, phones and links, links found in the PDF, and
the account's email and name) is kept in `trace_private.identity_markers`:
- readable by no client, not by the runtime login, the owner role or the reference reader; the runtime writes the
  caller's own markers (`trace_contrib.store_identity_markers`) and reads them in memory (`contribution_identity`);
- never written to `trace_ref`, a contribution record, a de-identification report, a log or an API response, and never
  used for an id (reference and contribution ids stay random);
- merged with any name later added to the account; deleted with the account or the private profile; not kept for a
  failed upload.

At every rebuild the record is de-identified with these markers. Accent and case variants of a name part are removed
like the name itself; a capitalised word one letter away from a name part of 5+ letters is ambiguous. The
contribution is quarantined for a person (and an existing one suspended at once, so its reference rows disappear)
when the rebuild:
- removes anything (`new_identifier_in_edit`): an identifier there was introduced by an edit, and nearby text may hold
  others that no rule recognises;
- finds an ambiguity (`manual_review_required`);
- has no recorded markers, or no known name at all (`identity_markers_missing`), for example a profile processed
  before Phase 6.2 or a resume whose header held no recognisable name.
A reviewer approves exactly the redacted content (`review_quarantine approve-contribution`); any later change needs a
new review. Employer, institution and other career names are kept as before.

## Marker encryption, lifecycle and retention (Phase 7)

- **Encryption.** Markers are stored only as an AES-256-GCM blob (`trace_private.identity_markers.sealed`), sealed in the
  backend with `TRACE_IDENTITY_MARKER_KEY` (32 random bytes, URL-safe base64; environment or secret manager, never the
  database or Git). The owner's user id is bound in as associated data. The database holds the blob and a key
  fingerprint (`key_id`) only and cannot decrypt; nor can anyone with database or backup access but not the key. The
  runtime receives the blob through `contribution_identity` and opens it in memory. Without a valid key,
  `features.real_resume_uploads` is false and `POST /real-resumes` is refused (fail closed); a blob that cannot be
  opened makes the contribution wait for review (`identity_markers_missing`), never an empty identity. Invalid keys are
  never echoed in errors. Plaintext columns remain only for rows written by v2.10.0 (none exist locally); a row is
  either plaintext or sealed, and every new write is sealed.
- **Key rotation (manual).** Not automated. To rotate: keep the old key available, re-seal each row with the new key
  through the runtime (`contributions.identity.store_identity`), then remove the old key. A lost key means stored
  markers are unusable: affected contributions wait for manual review until the user uploads again.
- **Lifecycle.** Markers are needed while the private profile has an admitted real resume or a resume held for review
  (`trace_private.identity_markers_needed`). An ingestion that fails after admission removes the admission (cascade)
  and then the markers (`trace_contrib.forget_identity_markers`, own markers only, never needed ones). Deleting the
  account or the private profile deletes them (foreign keys). `python -m scripts.privacy_maintenance orphan-markers
  [--min-age-hours 24] --apply` removes rows that are not needed and older than the grace period (at least 1 hour);
  dry run by default; it can never remove the markers of a profile with an admitted resume.
- **Queue events.** They hold a pseudonymous user id, an event code, an attempt count, an error class code (pattern
  `^[A-Za-z0-9_.]{1,60}$`, so no message, address or content fits) and, for a manual retry, the operator's name.
  `python -m scripts.privacy_maintenance queue-events [--retention-days N] --apply` deletes events older than
  `TRACE_QUEUE_EVENT_RETENTION_DAYS` (default 90, at least 7). Queued requests are never touched. Run both on a schedule.

## Bounded retries (Phase 6.2)

An error while processing a request (not a de-identification result: those are final outcomes) is recorded with its
class name only and retried after `base * 2^(attempt-1)` seconds, capped (`TRACE_CONTRIBUTION_BACKOFF_BASE_SECONDS`,
`TRACE_CONTRIBUTION_BACKOFF_MAX_SECONDS`), up to `TRACE_CONTRIBUTION_MAX_ATTEMPTS` (default 5, at most 20). Then the
request is terminally `failed` (`retries_exhausted`), its existing contribution is suspended, and it is not claimed again
until the user changes their profile or consent or an operator runs
`python -m scripts.contribution_queue retry <user id> --operator "<name>"` (admin identity only). An attempt cut short
by a crash counts when its lease (`TRACE_CONTRIBUTION_LEASE_SECONDS`) expires; a graceful stop hands unstarted claims
back without counting them. The queue lives in the database, so a restart loses nothing.
