# Beta configuration (separate from local development)

Local development keeps settings that synthetic testing needs (no email confirmation, local keys, API docs on demand).
A real-user beta must use the settings below. Hosted Supabase Auth is configured in the dashboard (Authentication
settings), not through `config.toml`.

## Supabase Auth

| Setting | Local (`config.toml`) | Beta |
|---|---|---|
| Email confirmation | off | **on** |
| Minimum password length | 8 | **8 or more**, with letters and digits required |
| Site URL / redirect allowlist | `http://127.0.0.1:8000/app/` | the exact HTTPS app URL only (no wildcards) |
| Google provider | disabled until credentials exist | enabled; Google redirect URI `https://<project-ref>.supabase.co/auth/v1/callback`; scopes stay `openid email profile` |
| CAPTCHA on sign-up and sign-in | off | on |
| Auth rate limits | defaults | reviewed; custom SMTP with its own sending limits |

## Database

- Apply migrations with `supabase db push` (migrations only; never `--include-seed`).
- Set the `trace_app_runtime` password in the hosting secret store (`ALTER ROLE trace_app_runtime PASSWORD ...` as the
  admin). The API environment gets only `DATABASE_RUNTIME_URL`; never `DATABASE_URL`, the service-role key or the
  database owner password.
- `TRACE_DEMO_FIXTURES=0`, `TRACE_API_DOCS_ENABLED=0`, `TRACE_ENABLED_COHORTS` as approved, `TRACE_CONTRIBUTION_POLICY=opt_in`
  until counsel approves the notice; a contribution notice with `legal_review_status` other than `approved` keeps
  contribution inactive.

## Data mode and reference source

`TRACE_DATA_MODE=production` serves only eligible real contributed profiles (the synthetic cohort is refused whatever
`TRACE_ENABLED_COHORTS` says) and refuses to start if `TRACE_DEMO_FIXTURES` is on. Local development and internal QA
keep `synthetic_demo`. With an empty or small real pool, production says there isn't enough evidence; it never falls
back to synthetic data.

## Real resumes (keep disabled until approved)

Real-resume processing stays off (`TRACE_REAL_RESUME_UPLOADS=0`) in every environment that has not been approved for
real data. Before turning it on:
- `TRACE_IDENTITY_MARKER_KEY` set from a secret manager (32 random bytes, URL-safe base64); without it uploads are refused.
- An approved, real-data personal-analysis consent document. The installed local document describes synthetic test
  resumes only, and so does the local contribution notice: both must be replaced by counsel-approved texts.
- A scheduled `scripts.privacy_maintenance` (orphan markers, queue-event retention) and a named privacy reviewer for
  `scripts.review_quarantine`.
- Database backups encrypted at rest and access-restricted.
- **Legal review of the default-on sharing model** (Phase 7.1): the onboarding data-use notice, the effect of its
  Continue button, the legal basis for the shared-recommendation purpose in each intended jurisdiction (GDPR for EU
  users, applicable Indian requirements), withdrawal, retention, deletion and previously generated outputs, and whether
  an additional affirmative permission is required. Until then the default preference does not authorise processing;
  an explicit confirmation in Profile settings does (see SHARING_PREFERENCE.md). Do not describe either as legally
  approved.
- The onboarding notice and the contribution notice replaced by counsel-approved texts (the installed ones are local
  test texts).
- End-to-end resume-upload testing and verified withdrawal and retention behaviour on the hosted stack.

## Network and usage controls

- Put the API behind HTTPS; configure trusted proxies before relying on per-IP limits.
- Supabase: spend cap on; alerts for database size, egress, Auth MAU and storage.
- Hosting: request and bandwidth alerts, an upstream rate limit or WAF, alerting on 401/403/429/503 spikes.
- Google Cloud: verified OAuth consent screen, budget alert (no paid Google APIs are used).

## Google sign-in: what is still needed

The PKCE flow is implemented (FRONTEND.md). To verify it end to end: create the OAuth client, enable the provider
(locally: `[auth.external.google] enabled = true` with the two environment variables, then restart the local stack),
sign in once, and confirm that `/api/v1/me` returns the account. Until then it is implemented but unverified.
