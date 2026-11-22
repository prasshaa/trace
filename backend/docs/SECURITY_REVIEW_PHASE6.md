# Phase 6 security review (local stack, 2026-11-13)

Each status below is backed by tests that ran: `tests/test_security_phase6.py`, `tests/test_security_phase5.py`
(including the live Data API test), `tests/test_real_reference_integration.py`, `tests/test_deidentification.py` and
the pgTAP file `trace_runtime_and_real_reference.test.sql`.

## Phase 5 open findings

| Finding | Status now | Evidence |
|---|---|---|
| Backend connected as the table owner (BYPASSRLS) | **Fixed.** The API opens only `trace_app_runtime` (LOGIN; no superuser, BYPASSRLS, CREATEROLE or CREATEDB; owns nothing). It can SET (not inherit) `trace_app_user` for owner sessions and inherits only the read-only `trace_reference_reader`. A privileged runtime login is refused at connect time | 27 escalation attempts refused by the real login; catalog checks; pgTAP |
| In-process rate limiter | **Fixed.** Shared PostgreSQL counters (`trace_ops`), atomic, runtime-only, fail closed | 24 concurrent requests against a limit of 7: exactly 7 accepted |
| Signed-in users could write drafts through the Data API | **Fixed.** `authenticated` has no INSERT/UPDATE/DELETE on any private table and no writer function; PostgREST's authenticator cannot reach `trace_app_user`; direct storage upload policy removed | live Data API: every write refused; catalog |
| Starlette advisories | **Fixed.** FastAPI 0.128.8 → 0.133.1 (first release allowing Starlette 1.x), Starlette 1.3.1 pinned. Python 3.10+ now required (3.12 used) | pip-audit: only pytest (dev) remains |
| Email confirmation and password policy | **Local aligned, beta documented.** Local minimum password length 8 (matches the UI); email confirmation stays off locally for synthetic testing; beta settings in BETA_CONFIGURATION.md. Takes effect after the next local `supabase stop && supabase start` (not restarted in this phase) | config |
| Public API docs | **Fixed.** `/docs`, `/redoc`, `/openapi.json` are off unless `TRACE_API_DOCS_ENABLED=1` | test |
| Google sign-in not verified end to end | **Still not verified** (no Google OAuth credentials). Code unchanged since Phase 5 | — |

## Found and fixed during this phase

- Ownership-transfer trap: grants or revokes issued by `postgres` on functions owned by the writer roles silently do
  nothing. It hid twice: `designate_fixture` briefly kept the default PUBLIC execute, and the writer entry points
  kept `authenticated` execute. Both corrected by running the statements as the owners; tests now assert the ACLs.
- The runtime login could `SET ROLE authenticated` transitively; `authenticated` is now granted to `trace_app_user`
  inherit-only.
- De-identification: an employer containing the owner's surname was retained without review; now flagged.

## Remaining risks

| Risk | Severity | Blocks real-user beta? |
|---|---|---|
| Google OAuth round trip untested | Medium | Yes, if Google is the primary sign-in at beta |
| Hosted configuration not applied (email confirmation, password policy, redirect allowlist, CAPTCHA, spend caps, runtime password in a secret store) | High until done | **Yes**: see BETA_CONFIGURATION.md |
| `X-Forwarded-For` not trusted, so behind a proxy the per-IP limit applies to the proxy | Medium | Yes behind a proxy: configure trusted proxies first |
| Indirect re-identification through rare combinations of retained attributes in the real pool | Medium | No (aggregates only, ≥5 per cell, complementary suppression); review before external sharing |
| Legacy `contributor_user_id` / `source_consent_event_id` columns on `trace_ref.profiles` still allow the original identity-on-row path (unused, no rows) | Low | No; drop them in a later migration |
| `service_role` (the Supabase admin key) bypasses RLS by design | Inherent | Keep it out of every app environment |
| pytest 8.4.2 advisory (local `/tmp` handling) | Low (dev only) | No |
