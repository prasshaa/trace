# Trace API (/api/v1)

FastAPI routers in `app/api/` (`routes.py`, `deps.py`, `auth.py`, `errors.py`). Handlers are thin: business rules stay in
`CandidateProfileService`. Local development only (see Limitations).

## Authentication

Every endpoint except `/config` needs `Authorization: Bearer <Supabase access token>`.

| Check | Rule |
|---|---|
| Signature | ES256 only (pinned; the token's `alg` header never widens it: no `none`, no HS256). The key is chosen by the token's `kid` from the issuer's JWKS (`$SUPABASE_URL/auth/v1/.well-known/jwks.json`, cached 5 minutes), or from `SUPABASE_JWKS_JSON` when set (tests, offline) |
| Claims | `exp`, `sub`, `iss`, `aud` required; not expired; `iss` = `SUPABASE_JWT_ISSUER` (default `$SUPABASE_URL/auth/v1`); `aud` = `authenticated`; `role` = `authenticated`; `sub` is a UUID |
| Failure | `401 {"error": "unauthorized", "message": "A valid access token is required."}` with `WWW-Authenticate: Bearer`; the response never says which check failed. Keys unobtainable or issuer not configured: `503 auth_unavailable` |

**Ownership.** The owner is the verified token's `sub`, and nothing else. No endpoint accepts an owner or profile id: request bodies forbid unknown fields (422), and query parameters or headers naming an owner are ignored. The candidate profile is resolved for the owner inside an owner session (role `authenticated`, RLS), so existing database security applies unchanged. A record that is not the caller's is `404`.

**One profile per account.** `POST /profile` creates a profile only if the account has none, under a per-account advisory lock (concurrent requests create at most one). An account that somehow has more than one is never resolved silently (`409 profile_ambiguous`).

## Endpoints

| Method and path | Auth | Response |
|---|---|---|
| `GET /api/v1/config` | none | `200 {"supabase_url", "supabase_anon_key", "data_mode", "features": {"real_resume_uploads"}}`: nothing else. `real_resume_uploads` is true only when `TRACE_REAL_RESUME_UPLOADS` is on AND a valid `TRACE_IDENTITY_MARKER_KEY` is configured. `503 auth_unavailable` if not configured; `503 data_mode_unsupported` unless `TRACE_DATA_MODE` is `synthetic_demo` or `production` (and in `production`, also when `TRACE_DEMO_FIXTURES` is on) |
| `GET /api/v1/me` | bearer | `200 {"user_id", "has_profile", "profile": ProfileSummary or null}` (`user_id` is the caller's own token subject) |
| `POST /api/v1/profile` | bearer | body `{"headline"?, "country_code"?, "resume_as_of"?}` (unknown fields: 422). `201 ProfileSummary`; `409 profile_already_exists` |
| `GET /api/v1/profile` | bearer | `200 ProfileSummary`; `404 profile_not_found` when the account has no profile yet |

`ProfileSummary` = `{"headline", "country_code", "resume_as_of", "confirmation_status"}`: no internal identifiers.

## Consent and synthetic resume upload (Phase 2)

| Method and path | Auth | Response |
|---|---|---|
| `GET /api/v1/consent/resume-processing` | bearer | `200 {"purpose", "document": {"consent_version", "title", "text", "text_verified"} or null, "decision": {"status", "consent_version", "recorded_at"} or null, "active"}`. `text` is returned only when the installed document's hash matches the demo text (`app/demo/consent.py`) |
| `POST /api/v1/consent/resume-processing` | bearer | body `{"decision": "granted"\|"denied"\|"withdrawn", "consent_version"}`: the caller's own explicit decision for the current approved document (`409 consent_conflict` for any other version). Returns the same shape |
| `POST /api/v1/resumes` | bearer, profile | raw PDF body, `Content-Type: application/pdf`, optional `X-Filename`. `201 {"status", "is_synthetic_test_data", "notice", "extracted": {"employments", "education", "skills", "career_breaks", "evidence"}}` |

**Upload checks, in order; nothing is written until all pass:** size limit (`MAX_RESUME_BYTES`, 1 MiB; `413`, enforced while streaming and again on the full body), PDF signature and media type (`415`), the SHA-256 of the received bytes is an approved synthetic fixture (`422 unsupported_document` otherwise: arbitrary or modified PDFs are not processed), `TRACE_DEMO_FIXTURES=1` (`403 demo_fixtures_disabled` otherwise), the open database connection is local (unix socket or loopback address reported by the connection; unknown is refused) and its allowlist `trace_ingest.approved_fixture_resumes` holds exactly that (fixture id, hash) pair (`403`), active consent (`403 consent_required`), and an empty profile (`409 ingestion_conflict`: extraction never merges). No client-supplied hash, fixture id or fixture flag is read.

**Then:** the resume metadata is registered as the candidate (owner session; the bytes are never stored; the storage path is a placeholder in the owner's folder), the owner's own profile and that resume are designated in one transaction (below), and the existing `ingest_extraction` runs (`trace_ingest`, which re-checks ownership, the fixture pair, consent and never-merge). The result is a set of pending drafts to review.

**Fixture designation (`app/demo/fixtures.designate_fixture`)** is the only privileged write, and it is not an endpoint. One transaction, refused unless the session user is the table owner: one UPDATE sets `is_test_fixture` and `fixture_profile_id` on the profile matching both the resolved profile id and the verified owner, and only if it is not linked to a different fixture; one UPDATE sets `is_test_fixture` on the resume matching the resume id, the owner, the profile and the server-computed hash. Each must match exactly one row, or the transaction rolls back (`409 designation_refused`) and the registered metadata row is deleted. If ingestion fails afterwards, the profile and resume stay designated together with no records (consistent; the run is marked failed by the existing ingestion path).

## Local demo setup

```bash
cd backend
export SUPABASE_SERVICE_ROLE_KEY=...    # from `supabase status`; local only; never stored or printed
export TRACE_DEMO_PASSWORD=...          # at least 8 characters; never printed
.venv/bin/python -m scripts.demo_setup --users 2
```

`scripts/demo_setup.py` refuses unless `SUPABASE_URL` is a loopback URL, the actual database connection is local and the database holds the 41 approved synthetic fixtures. It installs the synthetic demo consent document ("not legal text"; an existing document with the same version but different content is never overwritten) and creates, or on rerun updates, `demo-candidate-<n>@example.invalid` users labelled as synthetic demo accounts. It never records consent, creates profiles or designates fixtures. Then run the API with `TRACE_DEMO_FIXTURES=1`.

## Review, corrections, role mapping and classification (Phase 3)

All need a bearer token and an existing profile; the profile is the caller's own. A record that is not the caller's is `404 record_not_found` on every endpoint (never revealed). `{kind}` is one of `employment`, `education`, `activity`, `evidence`, `skill`, `career_break` (anything else: `422 invalid_request`).

| Method and path | Service operation | Response |
|---|---|---|
| `GET /api/v1/profile/records` | `get_profile` | Review view: employments (with review, mapping and evidence), activity evidence, unattached evidence and other records. Each record has its own `id` (the handle used in action URLs) and no other internal id. Since Phase 7, other records also carry the owner's own detail: education `qualification_level`, `institution_name`, `field_of_study`, `notes` (a grade is kept there as `Grade: …`) and `end`; activities `activity_type`, `organisation` and `end` (null for other kinds) |
| `POST /api/v1/records/{kind}/{id}/confirm`, `/reject`, `/reinstate` | `confirm_record`, `reject_record`, `reinstate_record` | `200 {"kind", "status", "label"}`. No-ops of the M1 matrix (confirming a confirmed record, rejecting a rejected one) are `200`; disallowed transitions are `409 invalid_transition` |
| `POST /api/v1/records/{kind}/{id}/corrections` | `correct_record` (M4) | body: the kind's correction model (scalar content only). `201 {"kind", "status": "user_corrected", "label"}`. Unknown fields `422 invalid_record` (field names only); a correction that changes nothing `422 invalid_reference`; rejected record `409 record_not_editable`; the candidate's own draft `409 correction_not_available` |
| `GET /api/v1/records/{kind}/{id}/corrections` | `require_record`, `correction_history` | `200 [{"sequence", "changed_fields", "previous_values", "corrected_values", "prior_review_status", "corrected_at"}]`, oldest first; append-only |
| `GET /api/v1/employments/{id}/role-options` | `role_mapping_options` (M5) | outcome (`automatic`, `decision_required`, `unresolved`), the automatic proposal, alias options first, then every other eligible role, each with `requires_ownership_clarification` |
| `POST /api/v1/employments/{id}/map` | `map_employment` | `{"result": mapped_automatically \| decision_required \| unresolved \| candidate_decision_kept \| existing_mapping_kept, "role_id", "options"}` |
| `POST /api/v1/employments/{id}/role` | `choose_role` | body `{"role_id", "ownership_areas"?}`. `200 {"role_id", "mapping_basis": "candidate_selected"}`. Product Owner titles mapped to product management without PM-ownership areas: `422 clarification_required`; ineligible roles `422 invalid_reference` |
| `PUT /api/v1/evidence/{id}/classification` | `classify_evidence` (M6) | body `{"areas": [...]}`. `200 {"areas" (sorted, distinct), "basis": "candidate_selected", "state": "current", "note"}`. Never confirms the evidence. Rejected evidence `409 record_not_editable` (reinstate first); unknown areas `422 invalid_reference` |

**States in the review view.** `review.status`: `pending_review`, `user_confirmed`, `user_corrected`, `rejected` (with `label`, `origin`, `verification_status`, `is_own_draft` and the allowed actions). `mapping.state`: `automatic`, `candidate_selected`, `unmapped`, `set_outside_app`, with `is_stale` (the title changed after mapping), `mapped_title`, `confidence` (automatic only) and `ownership_areas`. A context-required title shows as `unmapped` until a decision; `role-options` or `map` say whether a decision is required. `classification.state`: `none`, `current`, `stale` (with `classified_text`, the text that was classified).

## Taxonomy, current position and pathway (Phase 4)

All need a bearer token and the caller's own profile (`401` / `404 profile_not_found` otherwise).

| Method and path | Service operation | Response |
|---|---|---|
| `GET /api/v1/taxonomy/target-roles` | `target_roles` | `[{"role_id", "label"}]`: the nine active PM target roles in ladder order (PM-APM ... PM-SDIR) |
| `GET /api/v1/taxonomy/responsibility-areas` | `responsibility_areas` | `[{"code", "label", "is_pm_ownership_signal"}]`: the 16 areas (the flag drives the Product Owner clarification) |
| `GET /api/v1/current-position` | `current_position` | `{"status", "reason", "explanation", "title_raw", "role_id", "canonical_title", "role_family", "seniority_code", "mapping_basis", "is_current_as_stated", "exclusion_reasons"}`: every unknown reason code is preserved; no record id |
| `GET /api/v1/pathway?target=<role_id>` | `pathway_result` (M6) | `PathwayResponse` (below). A role that is not one of the nine PM targets is `422 invalid_reference`; a malformed or missing `target` is `422 invalid_request` |

**`PathwayResponse`** is built explicitly from the M6 `PathwayResult`: `current_position` (as above), `target`, `role_only_notice`, `data_mode`, `is_synthetic_test_data`, `evidence_notice`, `interpretation_note`, `candidate_evidence` (the candidate's own counted areas, supporting records and excluded counts by reason), `routes` (ranked; each with `observed`, `fit`, `area_gaps`, `step_gaps`, `next_steps`), profile-level `next_steps`, `limitations` and `unavailable_assessments` (requirements, skills, time on route, company fit). Observed evidence, candidate fit and generated guidance stay separate; all four `observed.experience_state` values are preserved. It never contains reference identifiers, record UUIDs, individual observations, `supporting_profile_count`, counts below five, timelines, hidden areas, or consent and database internals. An unknown current position gives no routes and the reason; no observed route gives an empty list and an "absence of observations" limitation. Reference-evidence integrity failures are a generic `500`.

## Career insights, sample profiles, rate limits and headers (Phase 5)

| Method and path | Response |
|---|---|
| `GET /api/v1/insights?target=<role id>` | `200 CareerInsights`: distributions over **Trace's synthetic sample only** (education, employers and industries, transitions, responsibility areas, skills) with population, denominator, `not_recorded`, definition and `share_pct` per category, `in_your_profile` markers from the caller's reviewed records, the disclosure and limitations. `422` unless one of the nine PM targets; `404 profile_not_found`; `503 data_mode_unsupported`; `429 rate_limited` |
| `GET /api/v1/demo/samples` | `200 [{"number", "latest_title", "summary", "roles", "notice"}]`: the approved synthetic samples (fixture set AND database allowlist). `403` unless `TRACE_DEMO_FIXTURES=1` and the database is local |
| `GET /api/v1/demo/samples/{number}/resume.pdf` | `200 application/pdf` (no-store): the sample's bytes, for the browser to send to `POST /resumes`, which re-checks everything. `404` for an unknown or unapproved number |

All three need a verified bearer token; insights also need the caller's profile.

**Synthetic-only boundary.** Every insights query starts from `trace_ref.profiles WHERE dataset_class = 'synthetic_test_fixture' AND synthetic_demo_allowed` (a positive allowlist; see `app/services/career_insights/repository.py`), and every returned row is re-checked to be synthetic. Consented real references, public aggregates, uploaded fixtures, unapproved synthetic profiles and any future class are excluded by construction. The data mode comes only from `TRACE_DATA_MODE`; no request can choose it.

**Statistics rules.** The target cohort (synthetic profiles that held the target role) is described separately only when it has at least `min_profiles_moderate` (15) profiles; otherwise the whole sample is described and `scope.note` says so. A breakdown needs a denominator of at least `min_profiles_limited` (5); categories shared by fewer than 5 profiles are never shown with a count (`small_categories` reports how many were hidden); the denominator counts only profiles with that information recorded. No timelines, probabilities, percentiles, scores or salary figures.

**Rate limits** (HTTP 429 `rate_limited` with `Retry-After`), per verified user AND per client IP, in-process sliding windows; defaults in `app/config.py`:

| Bucket | Endpoints | Per user | Per IP |
|---|---|---|---|
| upload | `POST /resumes` | 5 per hour | 20 per hour |
| mutation | profile creation, consent, confirm / reject / reinstate, corrections, map, role choice, classification | 240 per minute | 600 per minute |
| read | `GET /pathway`, `GET /insights`, demo samples | 60 per minute | 240 per minute |

Since Phase 6 the counters are shared in PostgreSQL (see below). `X-Forwarded-For` is not trusted.

**Browser headers** on every response: a strict `Content-Security-Policy` (`script-src 'self'`, `connect-src 'self' <SUPABASE_URL>`, `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'none'`; not applied to `/docs` and `/redoc`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Permissions-Policy`, `Cross-Origin-Opener-Policy: same-origin`; `Cache-Control: no-store` on `/api/`. No CORS headers are sent (the app and API share one origin).

## Cohorts, consent purposes and the runtime database identity (Phase 6)

| Method and path | Response |
|---|---|
| `GET /api/v1/cohorts` | `200 [{"cohort", "label", "is_synthetic", "disclosure", "status", "eligible_profiles", "min_cell"}]` for the cohorts this server enables. `status`: `ready`, `insufficient` (1-4 profiles; the count is withheld) or `not_ready` (none) |
| `GET /api/v1/insights?target=&cohort=synthetic\|consented_real` | As before, for ONE cohort (default: the data mode's source, see Phase 7.1). New fields: `cohort`, `cohort_label`, `status`, `eligible_profiles`, `included_profiles`; `sections` is empty unless `status` is `ready`. New metrics where the cohort records them: universities and colleges, recorded grades (per scale), employers, certifications. `403 cohort_not_enabled`; `422` for any other value (there is no `all` or `combined`) |
| `GET /api/v1/pathway?target=&cohort=` | As before; `cohort` selects the observed route evidence (`synthetic` -> data mode `synthetic_test`, `consented_real` -> `empirical`). Personal fit always comes from the caller's own reviewed profile. Response adds `cohort` |
| `GET /api/v1/consent` | Both purposes side by side: `personal_analysis` (`resume_processing`) and `shared_contribution` (`deidentified_shared_dataset`), each with its document, latest decision, `active`, `offered` and `required_for_personal_pathways`; plus `contribution_policy` |
| `GET/POST /api/v1/consent/contribution` | The caller's own explicit decision on the current contribution notice: `{"decision": "granted"\|"denied"\|"withdrawn", "consent_version"}`. Never inferred from uploads, the other purpose or terms. Grant refused (`403 contribution_not_offered`) when the policy is `disabled`; withdrawal always accepted and removes the caller's contributed reference profiles in the same transaction |

**Small-cell rules (unchanged, extended).** Cohorts with fewer than 5 eligible profiles return no statistics. In a single-valued breakdown, when the hidden remainder (denominator minus the shown counts) would be 1-4, further categories are hidden until it reaches 5 (complementary suppression); the MBA / postgraduate headlines are suppressed as a pair the same way. `not_recorded` (profiles without that field) remains visible: it describes data completeness, not a category.

**Database identity.** The API opens only `DATABASE_RUNTIME_URL` (`trace_app_runtime`: no superuser, no BYPASSRLS, owns nothing). Each request's owner work runs in a transaction as `trace_app_user` with the verified user id set transaction-locally; reference reads use the inherited read-only `trace_reference_reader`. A privileged runtime login is refused (`503 database_unavailable`). The Supabase Data API role `authenticated` keeps SELECT on its own rows only: every write goes through this API.

**Rate limits** are now shared PostgreSQL counters (`trace_ops`), atomic across workers; a limiter failure is `503 rate_limiter_unavailable`, never an unlimited request.

## A user's own resume and automatic contribution (Phase 6.1)

| Method and path | Response |
|---|---|
| `POST /api/v1/real-resumes` | Raw PDF body (`Content-Type: application/pdf`, size limit as for uploads). Verified owner, own profile, `resume_processing` consent, upload rate limit, server switch `TRACE_REAL_RESUME_UPLOADS=1`. Parsed and de-identified locally. `201 {"status": "processed", "extracted": {counts}, "notice"}`: drafts in the private profile, pending the user's review. `202 {"status": "awaiting_manual_review"}`: held for a privacy reviewer, nothing added. `422 resume_not_processable` (unreadable, no career content, or de-identification failed: nothing stored); `403 real_uploads_disabled`; `409 ingestion_conflict` (profile not empty) or `fixture_profile_conflict` (a synthetic demo profile); `401`, `413`, `415`, `429` as usual |
| `GET /api/v1/real-resumes/status` | `{"status": "none"\|"processed"\|"awaiting_manual_review"\|"not_processed"\|"approved"\|"rejected", "reason", "updated_at"}`: the caller's latest outcome; no content |
| `GET /api/v1/contribution` | `{"consent_active", "state", "included", "label", "updated_at"}`. `state`: `not_enabled`, `awaiting_resume`, `awaiting_review`, `pending`, `processing`, `eligible` (included), `quarantined` (manual privacy review), `failed`, `withdrawn`, `not_eligible`. `label` may be more specific for some reasons (another attempt queued, retries exhausted, an edit that may contain personal details); the state set is unchanged. Never an id, report, reason code or anyone else's state |

Contribution consent stays `GET/POST /api/v1/consent/contribution` (explicit, opt-in). Uploading never grants it.
Processing is automatic: changes to the profile and consent decisions queue the user (one durable row per user);
the backend worker (`TRACE_CONTRIBUTION_WORKER`, interval `TRACE_CONTRIBUTION_WORKER_INTERVAL`) rebuilds the record
from the REVIEWED private profile, de-identifies it again and records the outcome. The contribution appears only as
`eligible`; withdrawal removes it at once; a later grant creates a new contribution after it passes again.

## Data use, sharing preference and reference sources (Phase 7.1)

| Endpoint | Behaviour |
|---|---|
| `GET /api/v1/sharing` | `{"notice": {"version", "title", "paragraphs", "is_consent_record": false}, "notice_acknowledged", "preference": "shared"\|"private"\|null, "preference_source": "onboarding_default"\|"settings"\|null, "preference_updated_at", "explicit_permission", "activation", "confirmation_notice", "status"}`. `activation`: `not_chosen`, `private`, `active` (explicit permission in force), `confirmation_required` (preference shared, no explicit permission, and the server offers it: `confirmation_notice` is the contribution notice to confirm), `not_offered`. `status` is the `GET /api/v1/contribution` object (the actual state; `included` only when included). The caller's own only |
| `POST /api/v1/sharing/notice` | `{"notice_version"}` (must be the current version, else `409`). Continue on the onboarding data-use notice: records the preference `shared` (source `onboarding_default`) only when the caller has none; never overrides a choice; records no consent decision and starts no processing. Rate limited (mutation) |
| `PUT /api/v1/sharing` | `{"mode": "shared"\|"private", "confirm_version"?}`. `private`: records a `withdrawn` contribution decision when a grant is active and excludes the caller from the shared pool in the same transaction (the contributed reference profile is deleted). `shared`: sets the preference; with `confirm_version` = the current, verified contribution notice it also records the explicit `granted` decision (`409` for another version or an unverified text; `403 contribution_not_offered` under the `disabled` policy). Unknown fields `422`. Rate limited (mutation) |

The preference is not a consent decision; processing for the shared pool still needs the explicit permission, and a
`private` preference blocks processing in the database whatever grants exist. See `SHARING_PREFERENCE.md`.

**Reference sources.** The server's data mode chooses the source that pathways and insights read; the frontend never
sends one. `synthetic_demo` (local development and internal QA): the synthetic cohort by default; `TRACE_ENABLED_COHORTS`
may add `consented_real` for engineering isolation tests. `production`: only `consented_real`, whatever
`TRACE_ENABLED_COHORTS` says; `cohort=synthetic` is `403 cohort_not_enabled`, `/cohorts` lists only the real cohort, the
fixture upload and sample routes are refused (`403 demo_fixtures_disabled`), and the mode itself refuses to start
(`503`) if `TRACE_DEMO_FIXTURES` is on. An empty or small real pool is reported as `not_ready` or `insufficient` with no
statistic and no route; nothing falls back to synthetic data. The demo sample routes (`/api/v1/demo/samples...`) remain
for internal QA tooling only (`scripts/qa_test_account.py`); the product frontend does not call them.

**Privacy threshold versus evidence sufficiency.** The minimum of five profiles is a privacy-suppression threshold. It
does not by itself make a recommendation reliable; the API reports `ready` from five eligible profiles, and fit
(`fit.tier`) is what decides whether a route is presented as a recommendation. See "Limitations".

## Starting point, self-target guard and sample caution (Phase 7.2)

- **Starting point.** A pathway is built only from an established current position: the latest primary job reviewed
  (confirmed or corrected by the user), matched to a standard role with sufficient confidence and not stale
  (`GET /api/v1/current-position`, `status = known`). Until then `GET /api/v1/pathway` returns `200` with the unknown
  position, its reason and `routes: []` (unchanged contract); no route is ever generated without it. Nothing else in the
  profile has to be reviewed first.
- **Self-target guard.** `GET /api/v1/pathway?target=<current role id>` is `409 {"error": "target_is_current_role"}` and
  builds nothing (no zero-move route, no substitute target). The check is in the service (`pathway_result`) and compares
  canonical taxonomy role ids: a job title, whatever its wording, is resolved to a role id when it is mapped. It also
  applies when a previously chosen target becomes the current role after a profile change. `GET /api/v1/insights` is
  unaffected: the current role can still be explored there.
- **`sample_caution_below`** (new, additive, in the pathway response): the existing analytics setting
  `min_profiles_moderate`, the size below which a target group is not described on its own. The frontend shows a
  small-sample caution next to observed counts below it (insights use the same value as `scope.min_target_cohort`). It
  is not a reliability measure, and no new threshold was introduced.
- **Data-use notice v2**: `trace-data-use-notice-v2` (the last paragraph now says the default preference does not share
  anything until sharing is confirmed and the checks pass). v1 acknowledgements remain valid preference records.

## Final hardening (Phase 7.2.1)

- **Zero-move routes.** A route whose first and last canonical role ids are equal (for example Product Manager at one
  employer, then Product Manager at another; or a role, another role, and back) is employment history, not a career
  progression. `reference_evidence.progression_routes_only` removes such routes from the observations, the route
  summaries and the step experience together, and every route consumer uses it: `pathway_result` (the recommended
  route, alternatives and ranking inputs) and `route_evidence`; `build_pathway_result` also drops them itself. Preserved:
  every source record (employments, episodes, transitions, the foundation views and functions are unchanged), the
  foundation's own retrieval (`retrieve_reference_evidence` still reports a same-role employer change as an observed
  episode route), and routes that pass through a same-role employer change on the way to a different role. Career
  insights already count only role changes (`v_episode_transitions.role_changed`) and distinct earlier roles other than
  the target. Comparisons use role ids, never titles. There is no cache of route results (computed per request).
- **User-confirmed starting point.** `GET /api/v1/current-position` (and every pathway) is `known` only when the latest
  job is confirmed by the user AND its standard role was chosen or explicitly confirmed by the user (role basis
  `candidate_selected`, through `POST /api/v1/employments/{id}/role`). An automatic match, however confident, or a
  mapping set outside the app gives `unknown` with reason `role_unconfirmed`. A later automatic re-match replaces the
  basis and so needs confirming again; a title edit makes the match stale. No migration: the existing basis column
  records the user's decision. The sequencing engine and the matcher are unchanged.

## Route privacy, own-contribution exclusion, fit limitations and ties (Phase 8.1)

- **Route-level privacy.** A complete route supported by fewer distinct reference profiles than `min_profiles_limited`
  (5) is never returned. Its sequence, intermediate roles, count, experience, fit and guidance are all withheld, in the
  pathway (main and alternative routes) and in route evidence. The "no route" limitation is worded the same whether no
  profile or too few profiles made the move.
- **Own contribution.** The caller's own contributed reference profile (`trace_contrib.my_reference_profile_ids()`,
  server-side only) is excluded before route evidence, counts, fit and the insights statistics are computed. Other
  users still count it. Profile ids never appear in a response. `GET /api/v1/cohorts` still reports the whole pool.
- **`routes[].fit.limitations`** (additive): `incomplete_user_profile`, `insufficient_reference_evidence`,
  `insufficient_route_evidence`. Empty when fit is established; both sides can apply.
- **`routes[].rank_group` and `routes[].tied`** (additive): routes with the same fit tier and the same number of
  intermediate roles not held share a group. The order inside a group is only the stable route-id order. `rank` is
  unchanged (unique, for display order).
- **`role_unconfirmed`** has its own next-step guidance.

## Cohort counts and move directions (Phase 8.2)

- **`GET /api/v1/cohorts`:** counts exclude the caller's own contributed profiles before suppression (the population
  that can support this caller's evidence). Responses are `no-store`.
- **`routes[].step_directions`** (additive, presentation only): one entry per move, each one of `more_senior`,
  `more_junior`, `lateral`, `career_track_change` or `null` (no reliable direction). It is derived from the taxonomy's
  role family and seniority, and only within a family that defines more than one level. Discovery and ranking don't
  use it.

## Errors

Every error is `{"error": <code>, "message": <text>}`; invalid request bodies, paths or queries are `422 {"error": "invalid_request", "fields": [...]}` without echoing input values. Messages never echo record ids. Status mapping: not found 404; identity, permission and consent 403; invalid input and clarification required 422; state conflicts (not editable, invalid transition, mapping, classification, consent-version or ingestion conflicts, designation refused, profile already exists or ambiguous) 409; upload too large 413; not a PDF 415; rate limited 429 (`Retry-After`); unsupported data mode 503; unsupported document 422; demo fixtures disabled or not local 403; database unreachable or not configured 503; anything else 500 with a fixed message. A service message is passed through only when it contains no SQL or schema detail (relation, constraint, column, SQL keywords, ...); otherwise the status's fixed message is used. Only exception types are logged. No stack trace, SQL, credential, key or secret reaches a client.

## Configuration

`DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY` (public), optional `SUPABASE_JWT_ISSUER`, `SUPABASE_JWKS_JSON`, `TRACE_DATA_MODE` (`synthetic_demo`, or `production`: real contributed profiles only), `TRACE_RATE_LIMITS_ENABLED` and `RATE_LIMIT_<BUCKET>_<USER|IP>` (`"<count>/<seconds>"`). The API never needs the JWT secret or the service-role key.

## Limitations

- **Local development only.** The database login is still the table owner (D7), and raw database credentials can forge request identity (L1). Both must be resolved before any non-local deployment.
- Tokens are not checked for revocation (logout) before they expire, as usual for stateless JWTs; keys are re-read from JWKS at most every 5 minutes.
- One database connection per request, no pool.
- **Synthetic fixtures only.** Only the 41 approved synthetic PDFs can be uploaded, and only in local demo mode. The "extraction" is the M3 fixture lookup, not PDF parsing. Resume bytes are not stored; the metadata's storage path names no real object.
- **The demo consent document is marked approved** so that the database's consent rule can be exercised locally. It is labelled synthetic and not legal text, and is installed only by the local setup script.
- **Designation runs as the table owner**, the same login the local backend already uses (D7). It is limited to two scoped UPDATEs in one transaction and to local databases.
- **Evidence sufficiency is not modelled separately from privacy suppression (Phase 7.1).** A cohort is `ready` from five eligible profiles (the privacy threshold), and a route is shown when it is observed at all. There is no backend measure yet of target-role coverage, the number and spread of observed transitions, or how comparable the reference profiles are to the user; the fit tier is the only signal of relevance. No new threshold was invented in Phase 7.1; this is a known gap before real-data use.

## Product Phase 2: manual entry, draft edits, update proposals, versions

All endpoints need a bearer token and act only on the caller's own profile. Another user's record or proposal is
returned as 404.

| Method and path | Purpose |
|---|---|
| `POST /api/v1/entries/{employment,education,activity,skill}` | Create the user's own pending draft. `confirm: true` is the user's explicit "save and confirm". An employment takes `before_employment_id`, or is placed by its start date, otherwise as the latest role. Later roles that must move get an **ordering event** each (D10, v2.15.0) and keep their review status. A rejected role as anchor, or one that would have to move, gives 409 `record_not_editable` (reinstate it first). An explicit position that clearly established start dates contradict gives 409 `order_conflicts_with_dates`; unknown or ambiguous dates keep the user's order |
| `GET /api/v1/records/employment/{id}/order-events` | The role's position-only moves (previous and new position, review status at the move, cause, time). Separate from `.../corrections`, which lists content corrections only |
| `PATCH /api/v1/records/{employment,evidence,education,activity,skill}/{id}` | Edit the user's own pending draft. A reviewed or extracted record gives 409 `correction_not_available`; use corrections |
| `POST /api/v1/proposals/from-text` `{text}` | Parse pasted text locally (deterministic parser, contact lines ignored) into proposals. Only the text's SHA-256 is kept |
| `GET /api/v1/proposals` | The open proposals: new items, and possible updates (shown with the record's current values) |
| `POST /api/v1/proposals/{id}/accept` `{confirm}` / `.../dismiss` | Accept (creates a draft, or applies an update as an edit or a correction) or dismiss. The decision is final, and the proposed content is cleared |
| `POST /api/v1/real-resumes` into a profile that already holds records | `status: "unchanged"` for the same file, or `"proposed"` with counts. Nothing is merged |

**Optimistic concurrency.** Every record view carries `review.version`.
- Send it back as `If-Match` on confirm, reject, reinstate, corrections, classification and draft edits.
- A stale version returns 409 `record_changed`, and nothing is written.
- Without `If-Match`, there is no check (backward compatible).

**Profile version.** `GET /api/v1/profile/records` returns `profile_version`, a fingerprint of content and review
state.


## D20: role-family names for moves across families

Each pathway route has `step_family_changes`, parallel to `step_directions` (one entry per move):
- **For a `career_track_change` move:** `{"from_family", "to_family"}`, holding the taxonomy's family names
  (`trace_role_families.label`). Either value can be `null` when the taxonomy has no name.
- **For every other move:** `null`.

The client shows "Moving from {from_family} to {to_family}", or "A change in career direction" when a name is missing.
`career_track_change` itself, discovery and ranking are unchanged.


## Product Phase 3: capability assessment and posting metadata

| Method and path | Purpose |
|---|---|
| `GET /api/v1/capability-assessment?target=PM-PM\|PM-SPM[&context=…]` | Assessment 1 (target-role capability). For each applicable canonical requirement: state (`demonstrated`, `transferable`, `gap`, `unknown`, `not_applicable`), basis or reason, the caller's own supporting records, the transfer rationale, and every source with stance, attribution and effective status. Also returns review coverage and qualifications interpreted at read time (an MBA reads as a master's-level type). No scores. `Cache-Control: no-store`. Other targets: 422 `target_not_covered`. Unknown contexts: 422 |
| `GET /api/v1/knowledge/postings` | Employer-specific posting metadata for assessment 2. Per posting: entry-background stance, accepted or excluded backgrounds, short quotation and locator, criteria, `last_verified_at`, effective status, `is_current_opportunity` (verified within 14 days, D26) and `evidence_use` (`current_opportunity`, `requires_revalidation`, `historical_eligibility_evidence`). Not matched against the user; never part of the capability states |

See `backend/docs/CAPABILITY_ASSESSMENT.md`.

## Product Phase 3.1: taxonomy v2 and classification

| Method and path | Purpose |
|---|---|
| `GET /api/v1/taxonomy/responsibility-areas` | Each area with label, definition, examples, scope `questions`, `introduced_in_version`; plus `current_taxonomy_version` |
| `PUT /api/v1/evidence/{id}/classification` (`If-Match`) | Body `{areas, none, qualifiers}`. `areas` (non-empty) or `none: true`, never both. `qualifiers` is `{area: {question: true/false}}` for chosen areas only; leave a question out for "not sure". Records the current taxonomy version. 409 if the text changed since it was shown |
| `DELETE /api/v1/evidence/{id}/classification` (`If-Match`) | Clears the classification (back to uncategorised) |
| `POST /api/v1/entries/employment` | Optional `responsibility_classifications` and `achievement_classifications`: one entry (or `null`) per line, in order |
| `POST /api/v1/proposals/{id}/accept` | Optional `classifications`: one per line (responsibilities then achievements; or the single evidence line) |

Evidence views carry `classification.state` (`none`, `current`, `none_of_these`, `stale`), `qualifiers` and
`taxonomy_version` (earlier classifications read as 1). The capability assessment adds `taxonomy_version`,
`source_inventory`, per-requirement `area_conditions`, and `blocking_records` on Unknown results and in coverage.

## Product Phase 4: transition plan (saved runs)

| Method and path | Purpose |
|---|---|
| `POST /api/v1/transition-plan?target=PM-PM\|PM-SPM[&context=…]` | Builds the caller's plan from their reviewed profile and saves it as an append-only run (201). Returns it as stored: `decision` (rule, primary routes, rule trace), `routes` (direct, curated bridges and observed; each with steps, feasibility, trade-offs, evidence-breadth label and actions by stage 0–4), `assessment` (states only), `eligibility` (per posting: required and preferred criteria, each meets, does not meet or unclear, with explanations; current, needs revalidation, or historical), `versions`, `source_versions` and `privacy_state`. No scores, no supporter counts, no reference ids. 409 if the target is the current role; 422 for targets the pack doesn't cover. Reference cohort chosen by the server |
| `GET /api/v1/transition-plan?target=…` | The caller's latest saved run for the target (404 if none) |
| `GET /api/v1/transition-plan/{run_id}` | One of the caller's own runs (404 for anyone else's) |

See `backend/docs/TRANSITION_PLAN.md`.

**Product Phase 5: `summary`.** Every plan response (POST and both GETs) carries `summary`, a presentation projection
of the stored run computed at read time (`app/services/transition/summary.py`). It makes no decision of its own:
- `current_role`, `target_role`;
- `decision`: `rule`, `kind` (resolve_first, direct, direct_conditional, bridge), `reason` (the stored explanation),
  `selected` (the primary or tied primary routes, in stored order) and `detailed_route_position`;
- `strengths` (Demonstrated) and `priority` (`gaps`, `transferable`, `unknown` with its reason), decision rights first,
  then by requirement key;
- `resolve_first`;
- `stage_previews`: stages 1–4 of the detailed route, each with its title, its number of actions and its first objective.

It has no score, percentage or weight. The field is additive; nothing else in the response changed.

**Phase 4 remediation (D31–D38).**
- `POST /api/v1/transition-plan` accepts an optional `scope=portfolio` (Senior PM target only): the user states that the
  target role has portfolio scope, so L1 counts towards the route.
- Routes report decision-rights gaps and partial development (what each bridge develops, what remains and what to
  seek).
- Actions carry `labels`.
- The Data analysis area returns the D35 question in `/taxonomy/responsibility-areas`, and classifications accept it.

## POC iteration (after product Phase 5): target companies, company research, freshness

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/target-companies?target=` | The caller's chosen companies for a target role, in order; each `source` is `researched_employer` or `user_provided` |
| `PUT /api/v1/target-companies?target=` | Body `{"companies": [names]}` replaces the list (add, remove, reorder; `[]` clears). Max 20, 1–100 characters each; 422 otherwise or for an unknown target |
| `GET /api/v1/target-companies/search?target=&q=` | Employers Trace holds job-posting research for (substring match), each with `has_posting_for_target`, plus `exact_match` and a note that this is not a directory |
| `GET /api/v1/company-research?target=` | For the chosen companies only: each company `assessed` (its postings, each assessed on its own stated criteria now, never merged) or `not_assessed` (`no_relevant_posting` or `not_researched`); `current_openings` lists verified current postings at chosen companies only |

Companies never change the plan and never create a run (D42). Plan responses add `freshness` (`current`, `changed`:
any of `profile_version`, `data_mode`, `engine_version`, `knowledge_pack`, `source_inventory`, `taxonomy_version`,
`bridge_catalogue_version`, `action_template_version`, `eligibility_rules_version`, `posting_status`); a stale run is
never altered. `summary` adds `development` (a bridge route's own fully / partly / not-developed requirements),
`career_histories` (`shown` with D4 labels, or `not_enough_evidence`; never counts) and stage `subtitle`s. Stage titles
are Build, Demonstrate, Reposition and Hiring transition (D43). `/taxonomy/target-roles` adds `report_available`.

## Product redesign: the career transition roadmap (D44)

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/transition-goal?target=` | The caller's planning goal for a target (all fields null when none) |
| `PUT /api/v1/transition-goal?target=` | `{timeframe, target_date, weekly_hours, checkpoint_dates}`, all optional. 422 for an unknown value, a past date or one more than five years ahead, or an unknown target. Never creates a plan run |
| `GET /api/v1/transition-plan/{run_id}/roadmap` | The roadmap projection of one of the caller's saved runs, with the current goal: `recommendation` (headline, route with current / intermediate / target, at most two reasons, what the route offers, the trade-off, `why_path`), `build_on`, `focus`, `related` (each claim with `evidence` from records eligible now), `not_assessed`, `resolve_first`, `stages` (four; `highlighted` and `more` actions, each with what to do, why, where, what to produce and Trace's evidence-to-keep guidance), `checkpoints` (dates only if the user set them), `timeline` (the goal; `duration_evidence.status` is `not_available`), `alternatives`, `career_histories`. 404 for another user's run |

The roadmap never changes the stored plan; employer-posting actions are not part of it. The company endpoints (POC
iteration) remain available but no view calls them.

## Comprehensive redesign (D45)

`GET /api/v1/transition-plan/{run_id}/roadmap` adds `experience` (themes, records listed once, a single limitation),
`development` (per priority: concept, subtopics, why, learning methods with verified resources, recommended first
approach, alternatives, practice steps, output, how to use it, bridge note), `sequence`, `stage_guidance` and
`learning_version`. `GET /api/v1/transition-plans/latest` returns the caller's most recent saved plan for any target
(`run_id`, `target_role_id`, `created_at`), or 204 when there is none.

## Career Origins & Transitions (D46, internal preview)

`GET /api/v1/career-origins[?plan=<run id>]`. 404 `career_origins_disabled` unless `TRACE_CAREER_ORIGINS=1`. A verified
user is required, and the endpoint is rate limited (read bucket). It returns `CareerOriginsView`:
- `status` (`available` or `unavailable`);
- the artifact sections (`research`, `display_policy`, `definitions`, `limitations`, `coverage`, `facts`, `metrics`,
  `time_to_first_pm`, `progression`);
- with `plan` (the caller's own run, otherwise 404), a `connection` from the confirmed current role family: `mapped`,
  `already_in_field`, `not_mapped` or `no_current_role`.

The response is an explicit allowlist (`app/services/career_origins/schema.py`, `extra="forbid"` at every level):
- the artifact's `internal` provenance block is never served;
- null fields are dropped, so a hidden group carries only its band;
- `plan` is the only parameter: it selects a precomputed group and never filters or recounts;
- `Cache-Control: private, no-store`;
- GET only.

It never changes the plan. `/api/v1/config` is unchanged. See `docs/CAREER_ORIGINS_TRANSITIONS_POC.md`.

## Resume import (D47, D48: staged drafts)

Verified bearer token and the caller's own profile are required. Responses are `Cache-Control: private, no-store`.

Since D48 an unconfirmed import is a private, de-identified draft (`trace_private.resume_import_drafts`). Nothing reaches
the profile tables until confirmation, for a new profile and an existing one alike. Imports made before D48 (pending
records, or a proposal batch) are still shown and confirmed as before (`"staged": false`).

**`GET /api/v1/resume-import`**: the open import as one structured profile, or `{"status": "none", "legacy_suggestions": n}`.
Every call also removes identity markers that nothing needs any more.
- `staged`: true for a D48 draft. `mode`: `new` (empty profile) or `update` (the profile has records).
- Item ids are draft keys: `J<seq>` jobs, `X<n>` lines, `D<n>` education, `S<n>` skills, `A<n>` activities, `U<n>` unplaced
  text. In `update` mode each item has a `status`: `new`, `update` (different dates from an existing role, applied as a
  correction if kept) or `present` (already in the profile; skipped).
- It returns `work`, `education`, `skills` (by category, with a `recognised` flag), `activities`, `unresolved` (text the
  parser couldn't place: `kind`, `section`, `text`) and `warnings` (codes, for example `unresolved_text`).
- The most recent job carries `role` options for the user's choice.

**`POST /api/v1/resume-import[?replace=true]`**: the raw PDF or DOCX body.
- The `Content-Type` must agree with the file signature. Limits: `MAX_RESUME_BYTES` (default 1 MiB), 8 pages, 60k
  characters, and a 20-second parse in an isolated process. Rate limited by the upload bucket.
- Refused unless `TRACE_REAL_RESUME_UPLOADS` and an identity-marker key are set, and resume-processing consent is active.
- **409 `import_pending`**: an import is already open, unless `replace=true` is passed.
- **422 errors**, with codes: `unsupported_format`, `unreadable`, `encrypted`, `no_text`, `too_many_pages`, `too_long`,
  `unsafe_document`, `timeout`, `no_career_content`, `needs_privacy_review`, `deidentification_failed`.
- Success: `{"result": "imported"|"unchanged"|"nothing_new", "skipped": {...}, "warnings": [...], "import": <view>}`.
  The file, its name and its text are never stored or echoed. The draft keeps the size, the SHA-256 and the format proven
  by the content (`application/pdf` or the DOCX type; a declared type that disagrees is refused). `nothing_new`: every
  item is already in the profile, so no draft is saved.

**`POST /api/v1/resume-import/{id}/confirm`**: a body of `{work, added_work, education, skills, activities, updates}` edits and
removals, applied in **one** transaction with confirmation.
- `work` items accept `title`, `employer`, `employment_type`, `role_id`, `start`, `end`, `bullets`, `remove`.
- `added_work` (staged drafts only): roles the user adds, for example from unplaced text (`title`, `employer`,
  `employment_type`, `role_id`, `start` with at least the year, `end`, `description`, `bullets`, `from_unresolved`). They
  are stored as the user's own statements.
- The same transaction writes the resume metadata row (placeholder path `<owner>/<import id>.<ext>`, no object, no file
  name, `retention_until` NULL), the de-identification admission, the records, the markers (only for an active
  contribution consent), and deletes the draft.
- An unknown or foreign id gives 404, and nothing is saved; the draft stays.
- Repeating the call returns `{"status": "confirmed", "already": true}`.

**`POST /api/v1/resume-import/{id}/check`** (D49, read-only, read bucket): the same body as confirm. Returns
`{"starting_role": "J4"|"N0"|null, "role": {suggested_role_id, outcome, choices}|null, "errors": [{item, field, message}],
"roles": n}`. `starting_role` is the role that will set the starting point once confirmed (stored order, as
`entries.place` decides; null when it is a role already in the profile), and is the only role asked for `role_id`.

**The role contract (D49)**, applied to every role the review saves (`work` items that aren't removed, and `added_work`):
- `title`, `employer` and `employment_type` (not `unknown`);
- `start` with month and year;
- `end` with month and year, or `{"precision": "ongoing"}` for the current role;
- a start that isn't in the future and an end that isn't before it;
- a `description` of at least 20 characters and 4 words. Lines are split on line breaks or bullet glyphs, each must be
  under 1,000 characters, and a role holds at most 30 lines;
- `role_id` on the starting role when choices exist.

Optional fields: `counts_full_time` (asked for founder, freelance or part-time roles) and `side_role` (stored as
`primary_career_sequence = false`). `ignored` lists unplaced-text ids the user left out; an id can't be both ignored and
added, nor added twice. Confirm answers **422 `review_incomplete`** with `fields`, and nothing is written. Errors from
roles already in an existing profile ("present", or a date "update") are not raised.

Errors: a value a service model rejects is **422 `invalid_input`**, which never echoes the value. An unexpected error is
**500** `{"error": "internal_error", "reference": "<10 hex>"}`; the log holds only the exception class and the reference.

**`DELETE /api/v1/resume-import`**: discards the open draft (with any sealed markers), a legacy import and earlier
leftovers, then removes markers nothing needs. Returns `{"status": "discarded", "drafts", "records", "suggestions"}`.
- Leftovers are open pasted-text suggestions (dismissed, not deleted) and unconfirmed (pending or rejected) records.
- Reviewed records, the profile and saved plans are never touched.

## Phase 5 (D50, D51)

- `GET|PUT /api/v1/move-preference?target=`: `{scope: specific_companies|company_types|open, company_types[], companies[]}` plus
  the fixed `options` (each says whether it changes the plan) and `limits`. Up to three kinds; companies reuse the v2.29.0 list.
- `POST /api/v1/transition-plan`: the current role is a valid target (201; no zero-move route). Contexts from the move
  preference are added by the server; freshness reports `move_preference` when they change.
- Capability assessment items add `display_state`, `needs_context`; supporting items add `read_by`, `cue`, `why`.
- Roadmap adds `capabilities`, `move`, `readiness`, `next_actions`; evidence refs may have basis `read_by_trace`.
- `/career-origins?plan=` adds `routes_by_background` and `narrative`.
- Resume import: `check` returns `role_options` per role and `alongside`; education edits take `start`, `end`, `studying`;
  education errors use items `D<n>`. Profile records return education `start` and `completion_status`.

## Release (D52): replace and clear

- `POST /api/v1/resume-import?replace_profile=true` (with `replace=true` to replace a pending draft): the reviewed draft
  REPLACES the confirmed profile on confirmation. The view has `mode: "replace"` and `replaces` (the current profile's
  counts). `POST /resume-import/{id}/confirm` then clears the old profile and ingests the draft in one owner transaction;
  any failure rolls back and leaves the old profile and the draft as they were. On an empty profile the flag is ignored.
- `GET /api/v1/profile/clear`: `{deletes: {jobs, employers, experience_lines, skills, education, activities,
  career_breaks, corrections, saved_plans, pending_imports, resume_records}, keeps: [...], sharing_active, empty}`.
- `POST /api/v1/profile/clear` `{"confirm": "clear my career profile"}`: 422 without the exact phrase (nothing deleted);
  `cleared` or, on a retry, `already_clear`. Owner-scoped; another user's data is never reachable.
- `GET /api/v1/config` → `features.career_research` (TRACE_CAREER_ORIGINS), so pages never describe research that isn't served.
- `GET /api/v1/sharing` under `TRACE_CONTRIBUTION_POLICY=disabled` serves `trace-data-use-notice-private-v1`;
  acknowledging it records the preference `private`.
