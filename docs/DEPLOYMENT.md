# Deploying Trace (Fly.io + Supabase)

Status (2026-11-22): **live at https://trace-career.fly.dev/** on Fly.io (`fly.toml`, `Dockerfile`, region `nrt`), with
the dedicated `trace-prod` Supabase project. The public site never points at the local stack or at `trace-dev`.

Fly.io (used): `fly secrets set DATABASE_RUNTIME_URL=... SUPABASE_URL=... SUPABASE_ANON_KEY=... TRACE_IDENTITY_MARKER_KEY=...`
then `fly deploy --remote-only`. Non-secret settings are in `fly.toml` `[env]`. The image holds only `backend/` and the
knowledge packs (`.dockerignore`): no secrets, tests, evaluation code or research files. The Replit configuration
(`.replit`, section 4) is an alternative that was prepared but not used.

## Architecture in production

| Part | Where | Notes |
|---|---|---|
| FastAPI app + static frontend (`/app/`) | Replit Deployment (`.replit`) | One process, 2 workers, HTTPS by Replit |
| Sign-in | Supabase Auth (`trace-prod`) | Email and password; tokens verified server-side (ES256, JWKS) |
| Data | Supabase PostgreSQL (`trace-prod`) | RLS on every private table; the API uses the restricted `trace_app_runtime` login only |
| Resume parsing | Inside the app, in a short-lived local process | No external AI or parsing service; files never stored |
| Career Origins research | **Off** (`TRACE_CAREER_ORIGINS=0`) | Release gates open; the artifact is not in the public repository |

## 1. Create and prepare `trace-prod` (owner)

1. Create the project (Pro plan, separate production organisation). Enable MFA on every admin account of that organisation.
2. **Auth → Sign In / Providers → Email:** email sign-up on; "Confirm email" on.
3. **Auth → URL Configuration:** Site URL = the Replit deployment URL (`https://<app>.replit.app`); add
   `https://<app>.replit.app/app/` to Redirect URLs. Remove localhost entries.
4. **Settings → JWT Keys:** the current signing key must be **ECC (P-256)** (asymmetric). The API accepts ES256 only;
   a project still on the legacy shared secret can't sign users in. Rotate to an ECC key if needed.
5. **Database → Backups:** confirm daily backups are listed (Pro). Optionally enable PITR.
6. Send (privately, not in chat history that is shared):
   - the project ref and region;
   - the **session pooler** connection string for the `postgres` user (admin, used only to apply migrations; never on Replit);
   - the API URL and the **anon (public)** key.

## 2. Database setup (engineering, with the admin URL)

```sh
cd data-foundation
supabase db push --db-url "$PROD_ADMIN_URL" --dry-run     # review the list: all v2.x migrations, nothing else
supabase db push --db-url "$PROD_ADMIN_URL"               # additive migrations only; no seed, no reset
pg_dump "$PROD_ADMIN_URL" --schema-only -f trace-prod-schema-<date>.sql   # kept outside the repository
```

Never run `supabase db reset`, `supabase/seeds/synthetic_fixtures.sql`, `scripts/demo_setup.py` or
`scripts/qa_test_account.py` against production. No local accounts, plans or research files are copied.

Then, as the admin:

```sql
alter role trace_app_runtime with login password '<new random password, 32+ chars>';
```

and install the production notice:

```sh
cd backend && DATABASE_URL="$PROD_ADMIN_URL" .venv/bin/python scripts/install_production_notice.py
```

Check: `select consent_version, legal_review_status from public.trace_consent_documents;` shows only
`trace-resume-processing-v3 | approved`.

## 3. Replit secrets (Deployments → Secrets)

| Secret | Value |
|---|---|
| `DATABASE_RUNTIME_URL` | `postgresql://trace_app_runtime.<ref>:<password>@<pooler-host>:5432/postgres?sslmode=require` (session pooler, IPv4) |
| `SUPABASE_URL` | `https://<ref>.supabase.co` |
| `SUPABASE_ANON_KEY` | the anon (public) key |
| `TRACE_IDENTITY_MARKER_KEY` | a NEW key for production only: `python -c "from app.services.contributions.sealing import new_key; print(new_key())"`. Stable: set once, never regenerated on restart |
| `TRACE_REAL_RESUME_UPLOADS` | `1` |
| `MAX_RESUME_BYTES` | `5242880` |
| `TRACE_CONTRIBUTION_POLICY` | `disabled` |
| `TRACE_CONTRIBUTION_WORKER` | `0` |
| `TRACE_CAREER_ORIGINS` | `0` |
| `TRACE_HSTS` | `1` |
| `TRACE_RATE_LIMIT_NAMESPACE` | `prod` |

Never set `DATABASE_URL` (the admin identity), `TRACE_DEMO_FIXTURES`, `TRACE_API_DOCS_ENABLED` or `SUPABASE_JWKS_JSON` on
Replit. The API refuses a runtime login that is a superuser, has BYPASSRLS or owns application tables.

## 4. Deploy (owner, in Replit)

1. Create a Repl from the GitHub repository (Import from GitHub). Don't use Replit Agent to change the code.
2. Add the secrets above to the Deployment.
3. Deploy → Autoscale (or Reserved VM for always-on). Build and run commands come from `.replit`.
4. Health check: `GET /health` returns `{"status":"ok"}`; `/` redirects to `/app/`.

## 5. Smoke test (engineering, against the public URL)

Sign up with a new address and confirm the email, sign in, upload a synthetic PDF and a DOCX, correct and confirm, choose
a same-role target, open the plan, update from a new resume, clear the profile, and check cross-user isolation with a
second account. `GET /api/v1/career-origins` must return 404. Delete the smoke-test accounts afterwards (Auth → Users).

## Rollback

- Application: Replit keeps previous deployments; redeploy the previous one. Or switch a feature off with its secret
  (`TRACE_REAL_RESUME_UPLOADS=0`) and redeploy.
- Database: migrations are additive; rollback scripts exist per migration but are not used for an application rollback.
  Recovery is from the Supabase daily backup (or PITR) and the schema dump taken before applying migrations.

## Known production limits

- The IP rate limit uses the client address reported by Replit's proxy (`--forwarded-allow-ips='*'`). A client can
  prepend a forwarded address, so the per-IP limit is advisory; the per-user limits (verified account) always apply.
- Plans show profile-led routes only: production has no reference career histories (the synthetic sample is never
  loaded there and the research is off).
- Account deletion isn't in the app; clearing the career profile removes the career information.
