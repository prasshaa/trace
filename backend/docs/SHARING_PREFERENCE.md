# Sharing preference, explicit permission and status (Phase 7.1)

A user's career information has two uses, kept apart everywhere (UI, API and database):

| Use | What it is | Recorded as |
|---|---|---|
| Personal career analysis | Trace reads the user's resume to build their private profile and their own career path | consent purpose `resume_processing` (explicit checkbox; unchanged) |
| Shared career insights | career details from the reviewed profile, after removing direct identifiers and passing the checks, improve recommendations and insights for others | the **sharing preference**, and separately the **explicit permission** (consent purpose `deidentified_shared_dataset`) |

## Three separate things

1. **Sharing preference** (`trace_private.sharing_preferences`, history in `sharing_preference_events`; v2.12.0):
   `shared` or `private`, with its source (`onboarding_default` or `settings`) and, for the onboarding default, the
   data-use notice version that was shown. This is what the product shows as "Sharing preference".
2. **Explicit permission**: the existing contribution consent, an active `granted` event under the current approved
   contribution notice. **Processing for the shared pool still requires it**, exactly as before Phase 7.1.
3. **Status**: what actually happened (`trace_private.user_contribution_status`): not yet processed, processing,
   pending review, manual review (paused), included, not eligible, couldn't be processed, removed. Shown as
   "Profile status". "Included" only when the API returns `eligible` and `included`.

Neither is inferred from another. A preference is never a consent decision, and an enabled preference never means
the profile is included.

## The journey

- **Onboarding notice** (after profile review, before the target role): the server's text ("Your experience helps make
  Trace better." …; version `trace-data-use-notice-v2`; v1 until Phase 7.2, whose last paragraph said only "Sharing is on by default") and one button, Continue. Continue calls
  `POST /api/v1/sharing/notice`, which records the preference `shared` with source `onboarding_default` **only if the user
  has no preference yet**. It never overrides an earlier choice, so a profile is never silently shared again after a
  repeated onboarding, a refresh or an account reactivation. It records **no consent event** and starts no processing.
- **Profile settings → Data & privacy**: two mutually exclusive modes and an explicit Save.
  - **Private**: one transaction records a `withdrawn` contribution decision (if a grant was active) and sets the
    preference to private, which removes the user's contributed reference profile (deleted), drops queued work and
    reviewer approvals, and sets the state to `withdrawn` (`trace_private.exclude_from_shared_pool`, the same removal a
    consent withdrawal performs). The private profile, the personal-analysis permission and the user's own career path
    are untouched.
  - **Shared**: sets the preference. When the explicit permission is not active and the server offers it (policy
    `opt_in` and a verified contribution notice), the Save button reads "Confirm sharing" and sends the notice version
    (`confirm_version`); that explicit settings action records the `granted` contribution decision, which queues a
    fresh processing run from the start (privacy checks, identifier scan, review requirements, eligibility).

## Why the default preference does not start processing

The agreed product behaviour is default-on sharing with a clear onboarding notice and a setting to switch to private.
Whether a Continue on that notice is a valid legal basis for the shared-recommendation purpose has **not** been
decided (GDPR for EU users, applicable Indian requirements, and others). The existing safeguard is an explicit,
version-bound decision under an approved notice, enforced in the database (`trace_private.contribution_consent_active`
gates queueing, processing and the `eligible` state). Treating a notice acknowledgement as that decision would bypass
the safeguard and misrepresent the acknowledgement, so it is not done.

So, until counsel decides: the default preference is recorded and shown; processing starts only after the explicit
settings confirmation; and in every environment real-resume processing is off anyway (`TRACE_REAL_RESUME_UPLOADS=0`).
If counsel approves a different mechanism (for example the notice itself as the basis in some jurisdictions), the
required change is: a per-jurisdiction, server-controlled setting naming the approved basis; a database rule that
accepts `source = onboarding_default` with an approved notice version as the authority for those users only; the
notice stored as a versioned document with its hash (like consent documents); and tests. None of that exists yet.

Older acknowledgements are never treated as permission retroactively: the onboarding default carries its notice
version, and any future rule must name the versions it accepts.

## Database rule (v2.12.0)

`trace_private.contribution_consent_active(user)` = an active explicit grant **and** a preference that is not
`private`. So a private user is never queued, processed or marked eligible, whatever grants exist, including one made
through the older `POST /api/v1/consent/contribution` endpoint. Users with no preference row (accounts from before
v2.12.0) behave exactly as before.

## What is kept after switching to private

Deleted at once: the contributed de-identified reference profile and its rows, queued work, reviewer approvals.
Insights and route evidence are calculated when they are viewed, so the profile stops counting immediately; no
aggregate output is stored. Kept: the contribution record (status `withdrawn`, no profile id), the consent and
preference history (to show what the user chose and when), queue events (pseudonymous, deleted by
`scripts.privacy_maintenance` after the retention period). Database backups may hold earlier copies until they expire;
their retention is a hosting setting, not something the application controls.
