# Phase 5 security review (local stack, 2026-11-13)

Scope: the actual local Supabase database (catalog queries, run as read-only transactions), the live local Data API
(PostgREST, Auth), the FastAPI backend and the new frontend. Every "verified" item below is backed by an executed test
(`tests/test_security_phase5.py`, `tests/test_career_insights_*.py`, the existing suites and 540 pgTAP tests).

## Findings

| # | Finding | Status | Evidence |
|---|---|---|---|
| 1 | Reference tables (`trace_data_sources`, `trace_role_taxonomy`, `trace_role_aliases`, `trace_skills`) without RLS and client-writable (earlier review) | **Already fixed.** RLS is on for every table in `public`, `trace_ref`, `trace_ingest`; lookups grant only `SELECT` to `anon`/`authenticated`; `trace_data_sources` now lives in the non-exposed `trace_ref` schema (no client schema usage) | `test_reference_tables_are_read_only_for_clients` (11 tables × insert/update/delete/truncate × anon and user), `test_rls_is_enabled_and_client_grants_are_the_reviewed_set`, live PostgREST test |
| 2 | Views executing with owner rights could bypass RLS (earlier review) | **Not applicable / already fixed.** There are no views in exposed schemas; the `trace_ref` views have no client privilege | `test_exposed_views_cannot_bypass_rls`, live PostgREST (`v_mode_profiles` 404) |
| 3 | Child records pointing at another user's profile (earlier review) | **Already fixed.** Every child table has composite foreign keys `(parent_id, owner_user_id)` / `(parent_id, candidate_profile_id)` plus owner RLS | `test_user_a_cannot_create_records_linked_to_user_b` (10 attempts), `test_user_a_cannot_read_modify_or_delete_user_b`, API 404 checks |
| 4 | Users changing system-controlled fields (owner, fixture designation, review/verification state, role mapping, classification, consent) | **Already fixed.** Column-level grants exclude them; `guard_candidate_insert` forces system values on insert; consent events append-only | 14 update cases + insert cases at the database; live PostgREST PATCH of `is_test_fixture`, `fixture_profile_id`, `owner_user_id` refused while `headline` is allowed |
| 5 | Synthetic relabelling from the browser | **Already fixed.** Designation comes only from SHA-256 of the received bytes plus the database allowlist; request headers, query parameters and filenames are ignored | `test_synthetic_designation_comes_only_from_the_bytes`, `test_unapproved_bytes_are_refused_whatever_the_name_or_type` |
| 6 | No backend rate limiting | **Fixed in this phase.** Per-user and per-IP sliding windows on upload, mutations, pathway, insights, demo samples (429 + Retry-After); counters in process memory | limiter unit tests and API tests for upload, mutation (user and IP), pathway, insights, disable switch |
| 7 | No browser security headers | **Fixed in this phase.** Strict CSP, nosniff, no-referrer, frame denial, COOP, no-store on API | `test_security_headers_on_app_and_api`, zero CSP violations in the browser run |
| 8 | PyJWT 2.10.1 advisories; one applies: an unknown `kid` forces a JWKS fetch per request (DoS amplification, runs before rate limiting) | **Fixed in this phase.** Upgraded to PyJWT 2.15.0 (Python ≥3.9). The others do not apply (ES256 only, raw key objects, no HMAC, no detached payloads) | `pip-audit`: no PyJWT findings; all auth tests pass |
| 9 | Secrets in the browser or in git | **Verified absent.** `/config` returns URL, anon key, data mode only; `.env`, `.mcp.json`, backups ignored; only `.env.example` tracked; web assets contain no keys, JWTs, DB URLs or source maps | `test_browser_assets_contain_no_privileged_credentials`, `test_config_exposes_only_url_anon_key_and_data_mode` |
| 10 | Frontend bypassing FastAPI | **Verified absent.** No Data API, Storage, Functions or RPC calls; only `/api/v1/` and Supabase Auth | `test_app_code_never_renders_html_or_calls_supabase_data_apis` |
| 11 | XSS through resume or API text | **Mitigated.** No HTML sinks; CSP without `unsafe-inline`; injected markup renders as text in the browser run | static test + browser check |
| 12 | SECURITY DEFINER functions | **Verified.** All have `search_path=""`, none in `public`, none executable by `anon`; writer functions are owned by NOLOGIN writer roles | `test_security_definer_functions_are_pinned_and_not_exposed` |
| 13 | Storage | **Verified.** Bucket `trace-resumes` is private with owner-folder policies; no PDF bytes are stored | `test_resume_storage_bucket_is_private` |
| 14 | Starlette 0.52.1 advisories (Host-header URL reconstruction, form limits, Windows StaticFiles, HTTPEndpoint) | **Deferred, low.** No affected feature is used (no `request.form()`, `HTTPEndpoint`, Windows, or `request.url`-based logic). Fix needs a FastAPI upgrade: do it before beta | `pip-audit` |
| 15 | pytest 8.4.2 advisory (`/tmp` directory, local users) | **Deferred, low.** Development only | `pip-audit` |
| 16 | Authenticated users can write their own candidate rows directly through the Data API, bypassing FastAPI validation | **Deferred, medium.** The database still enforces ownership, system fields and review semantics (only `user_stated` pending drafts can be created). Before beta: revoke client write grants on candidate tables or stop exposing `public` through the Data API, since the frontend uses only the Trace API | live PostgREST test |
| 17 | `FOR ALL` owner policies on candidate child tables | **Deferred, low.** Equivalent to per-operation policies here (identical `USING`/`WITH CHECK`, writes limited by column grants and guards); split when revisiting finding 16 | catalog review |
| 18 | Backend database login is the table owner (D7) and raw credentials can forge request identity (L1) | **Release-blocking before real-user beta.** Contained today: local only, owner sessions switch to `authenticated` per transaction, privileged code is limited to read-only snapshots and the narrow designation | existing guards and tests |
| 19 | In-process rate limiter | **Release-blocking for multi-worker or public deployment.** Replace with a shared limiter; configure trusted proxies for client IPs | documented |
| 20 | Auth hardening for beta: email confirmation is off locally, the server minimum password length is 6 (the UI asks for 8), `/docs` is public | **Deferred, low (local).** Before beta: enable confirmations, raise the minimum length, disable `/docs` | config review |
| 21 | Tokens valid until expiry after sign-out; session in browser storage (SDK default) | **Accepted.** One-hour expiry with refresh-token rotation; XSS mitigations above | — |

## Synthetic-data boundary

41 reference profiles in the database, all `dataset_class = synthetic_test_fixture` from `SRC-SYNTHETIC-001`
(Trace-generated), all demo-approved, `review_status = synthetic_curated`. The 18 uploaded-resume fixtures noted by an
earlier review were moved to git-ignored `local_only/` in v2.0.1; they are not on this machine, not in any database
table, and a CHECK constraint rejects `uploaded_resume_fixture` reference profiles. Insights use the positive filter
`dataset_class = 'synthetic_test_fixture' AND synthetic_demo_allowed`; a rolled-back sentinel test with 6
non-synthetic and 6 unapproved synthetic profiles leaves every statistic unchanged.

## Before a public beta: usage and budget controls

Not configured in this phase (local only). Configure on the hosted project before inviting real users:

- Supabase: spend cap on; usage alerts for database size, egress, Auth MAU and Storage; Auth rate limits reviewed
  (`[auth.rate_limit]`); CAPTCHA on sign-up; email confirmation on; custom SMTP with its own sending limits.
- Hosting: request and bandwidth alerts, an upstream rate limit or WAF in front of the API, log retention and alerting
  on 401/403/429 spikes.
- Google Cloud: OAuth consent screen verification, budget alert on the project (no paid Google APIs are used).
- Secrets: service-role key, database password and OAuth secret only in the hosting secret store; rotation plan.
