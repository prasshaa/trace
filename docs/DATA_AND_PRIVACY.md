# Trace data and privacy

This document gives the product-level summary. The authoritative technical detail is in:
- `data-foundation/docs/ACCESS_CONTROL.md` (roles, row-level security, review enforcement);
- `data-foundation/docs/DATA_DICTIONARY.md` (tables);
- `data-foundation/docs/INTAKE_AND_CONSENT.md` (consent);
- `backend/docs/SHARING_PREFERENCE.md`;
- `backend/docs/REAL_REFERENCE_DATA.md`.

## Three data categories, never mixed

1. **Private user profiles.** Owner-only, through row-level security and owner sessions. They never become reference
   data automatically.
2. **Consented real reference profiles.** De-identified contributions made under an explicit grant and the sharing
   preference, with withdrawal and review. There are currently **none**: real uploads are disabled and the 18
   participant resumes are not present or imported.
3. **Synthetic evaluation profiles.** 41 synthetic profiles, used in synthetic modes and evaluation only, always
   labelled, and never counted as real evidence.

## The profile is the source of truth (Postgres)

- **Structured, queryable records:** employers, employments (original `title_raw` plus the normalised role), education,
  activities (certifications, projects and 10 other types), evidence (responsibilities and achievements), skills and
  career breaks.
- **Flexible metadata:** JSONB only where needed, for example `raw_extraction_json`, which is never returned.
- **Provenance:** origin (`user_stated`, `source_extracted`, `inferred`) and source references (extraction run, resume
  hash, source location).
- **Review status:** separate from verification.
- **Corrections:** an append-only history.
- **Persistence:** the profile survives sessions and devices. A new resume never overwrites it; it creates proposals
  (Phase 2).

## Upload, storage, contribution and ingestion: five separate flows

Checked against the code and the server settings on 2026-11-16. **Implemented but disabled** means the code exists
and is tested (tests switch it on inside the test client only), but this server's settings keep it off.
**Unavailable** means there is no code path at all.

| # | Flow | What it does | Gate (setting or precondition) | Status |
|---|---|---|---|---|
| 1 | **First private resume upload** (`POST /api/v1/real-resumes` into an empty profile) | The PDF is read in memory on Trace's own server: local parser, then local de-identification. The result becomes pending drafts in the user's private profile. A version needing a privacy check is quarantined for a person to review | `TRACE_REAL_RESUME_UPLOADS` (default off) **and** an identity-marker key; plus current `resume_processing` consent | **Implemented but disabled.** The start page says so |
| 2 | **Re-upload into an existing profile** (same endpoint, profile already has records) | The same checks as #1. The same file is reported as `unchanged`; a newer version becomes update proposals the user accepts one by one. Nothing is merged, and a version needing a privacy check is refused rather than quarantined | Same as #1 | **Implemented but disabled** (Phase 2) |
| 3 | **Original resume-file storage** | Keeping the PDF itself after reading it | None: there is no code. The private `trace-resumes` bucket and its policies exist in the database only | **Unavailable by decision (D5).** Only size and SHA-256 are kept |
| 4 | **Contribution to the shared reference dataset** | A de-identified copy of the user's **reviewed** profile (never the file) becomes one reference career history. It is de-identified, not anonymous. It is rebuilt when the profile changes and withdrawn at once on withdrawal or a `private` preference | `TRACE_CONTRIBUTION_POLICY=opt_in` (default) **and** an explicit contribution grant **and** a `shared` sharing preference **and an admitted real resume (#1)** | **Implemented; the permission is offered, but nothing can become a contribution.** Users can set the sharing preference and grant the explicit permission in Profile settings, but the processor marks them `not_eligible` (`no_processed_resume`) because #1 is off. Manual-entry and pasted-text profiles have no admitted resume, so they are not contributable either. **0 contributions exist** |
| 5 | **Ingestion of real reference profiles** | Either (a) contributions from #4 admitted into `trace_ref`, or (b) an operator's controlled import from a restricted local staging folder (`scripts/import_real_reference.py`: de-identify, then admit or quarantine; local database only) | (a) needs #4. (b) is a manual admin action. Serving them to users also needs the `consented_real` cohort (`TRACE_ENABLED_COHORTS`, default `synthetic`; production mode uses only `consented_real`) | **Implemented but not activated.** The import has never been run, nothing is staged, and the 18 participant resumes are not present. **0 real reference profiles** |

**Separate permissions:**
- `resume_processing` consent authorises #1 and #2 for the user's own profile only.
- Contribution (#4) needs its own explicit grant. The sharing preference is separate again and is not itself a grant.
- #3 is a retention decision, unrelated to the others.
- #5(b) is an operator-side data decision. It is never a side effect of a user's action.

**Also local and never stored as text:** pasted text and manual entry (Phase 2) serve the user's own profile only and
need no upload.

**Not changed without an explicit decision:**
- the upload switch (#1, #2);
- original-file storage (#3);
- the contribution policy and consent design (#4);
- real-data ingestion (#5);
- external AI and embeddings (D1, D2).

## Documents (D5)

Original resume bytes are **not stored**. Only metadata is: size, SHA-256 hash, the format proven by the content,
extraction status. The private, owner-scoped `trace-resumes` bucket exists but is unused. The confirmed profile stays
available without the original.

**Resume import lifecycle (D48):**
- **Upload:** the file is parsed in memory in an isolated process, with no disk writes by Trace, no network and no
  external service.
- **Draft:** a de-identified draft (career details only; contact details removed) is saved in
  `trace_private.resume_import_drafts`. Only the owner can reach it. It survives reload and sign-out, and is never part of
  the profile, the current position or a plan.
- **Confirm:** one transaction applies what the user kept, records corrections with history, writes the metadata row and
  admission, and deletes the draft.
- **Discard or replace:** deletes the draft. Withdrawing resume-processing consent deletes it too. Confirmed records are
  never touched by any of these.
- **Retention:** no retention period is approved for resume metadata. `retention_until` is NULL for new rows; older rows
  keep a 30-day value that nothing enforces. Nothing is deleted by age.
- **Identity markers** (the name, emails, phones, links and handles found in a resume) are kept, sealed, only while the
  owner's contribution consent is active, and only to re-check shared contributions. They are deleted when that consent
  is withdrawn or declined.
- **Trade-off:** a contributor without markers (opted in after uploading, or after a withdrawal) is reviewed by a person
  before anything is shared.
- **Key:** markers are sealed with a stable key (`TRACE_IDENTITY_MARKER_KEY`; rotation through
  `TRACE_IDENTITY_MARKER_PREVIOUS_KEYS` and `privacy_maintenance reseal-markers`). A keyed, non-reversible representation
  (an HMAC of name tokens) was considered: it would support exact-token checks only and lose variant spellings. A name
  space is small enough to guess, so it is pseudonymous, not anonymous. It is not adopted.

## Consent and sharing

- Resume-processing consent and contribution consent are separate decisions with versioned documents.
- The sharing preference is separate again, and is not a processing authority.
- Choosing private, or withdrawing, removes the user from the shared pool immediately and excludes them from future
  retrieval. Later embeddings will follow the same removal rule.
- De-identified is not anonymous, and Trace never calls it anonymous.

## External processing (D1)

No resume, profile or conversation content goes to any external AI provider until the provider, data handling,
disclosure and explicit consent are approved. Today nothing is sent anywhere: parsing and de-identification are local.

## Security rules

Every profile operation:
- verifies authenticated ownership;
- runs with server-side authorisation, row-level security and runtime role separation.

**Data access:**
- shared reference data is not readable by browser clients;
- secrets and privileged credentials never reach the browser;
- audit records are kept for sensitive operations (consent events, correction history, contribution queue events,
  access log).

**Untrusted input:** uploaded documents and retrieved content are data, never instructions.

## Deletion and withdrawal

- Account deletion removes the profile, its records and its contribution.
- Withdrawal removes the user's data from future reference retrieval.
- Withdrawing contribution consent also deletes the identity markers; withdrawing resume-processing consent deletes the
  open import draft (D48). Neither deletes the confirmed profile.
- Pending update proposals are removed with the profile, and a proposal's content is cleared once the user decides on
  it (Phase 2).
