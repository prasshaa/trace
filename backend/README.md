# Trace backend

FastAPI service for Trace. This is the initial scaffold: only health endpoints exist.

## Layout

```
app/
  main.py            FastAPI app factory and router registration
  config.py          settings from environment / .env (pydantic-settings)
  db.py              psycopg 3 connectivity check
  routers/health.py  GET /health, GET /health/db
  api/               /api/v1: Supabase access-token auth, owner context, error mapping, routes;
                     see docs/API.md
  services/          domain and application services (no FastAPI)
    career_history/  deterministic domain logic, no database: candidate career
                     sequencing; see docs/CANDIDATE_SEQUENCING.md
    reference_evidence/  database-backed: read-only reference route evidence. The
                     service logic runs against a repository protocol; the Postgres
                     repository reads trace_ref; see docs/REFERENCE_EVIDENCE.md
    candidate_profile/   database-backed: owner-scoped structured career profile
                     service and repository (reads and writes public.trace_candidate_*;
                     reads taxonomy lookups and trace_ref.analytics_settings for the
                     sequencing context, then delegates sequencing to career_history);
                     see docs/CANDIDATE_PROFILE_SERVICE.md
    resume_extraction/   provider-agnostic extraction contract and a deterministic
                     extractor for the synthetic fixture resumes (no LLM); ingestion into a
                     profile is CandidateProfileService.ingest_extraction (trace_ingest
                     functions); see docs/RESUME_EXTRACTION.md
    role_mapping/    deterministic title-to-role proposals from trace_role_aliases (read-only;
                     nothing is written) and the fixture agreement report; see docs/ROLE_MAPPING.md
docs/                schema map and service contracts
tests/               pytest suite
```

## Requirements

- Python 3.9 or later
- The local Supabase stack running (`supabase start`), with Postgres on `127.0.0.1:54322`

This service connects only to the local development database. Do not point
`DATABASE_URL` at a hosted Supabase project.

## Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # or requirements.txt for runtime only
cp .env.example .env                  # then edit if your local values differ
```

`.env` is git-ignored. Credentials are read only from the environment or `.env`;
nothing is hardcoded.

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | yes, for `/health/db` | PostgreSQL connection URL |
| `DB_CONNECT_TIMEOUT` | no (default 5) | Connection timeout in seconds |
| `SUPABASE_URL` | yes, for `/api/v1` | Local Supabase URL (from `supabase status`) |
| `SUPABASE_ANON_KEY` | yes, for `/api/v1/config` | Public anon key; the only key sent to browsers |
| `SUPABASE_JWT_ISSUER` | no | Defaults to `$SUPABASE_URL/auth/v1` |
| `TRACE_DEMO_FIXTURES` | no (default 0) | 1 enables guarded synthetic fixture uploads (local database only); see docs/API.md |
| `TRACE_DATA_MODE` | no (default `synthetic_demo`) | Server-controlled data mode and reference source: `synthetic_demo` (synthetic test profiles; local and internal QA) or `production` (eligible real contributed profiles only; refuses to start with `TRACE_DEMO_FIXTURES=1`). Anything else fails closed (503) |
| `TRACE_RATE_LIMITS_ENABLED` | no (default true) | Backend rate limits (shared PostgreSQL counters); per-bucket rules `RATE_LIMIT_<UPLOAD|MUTATION|READ>_<USER|IP>` |
| `DATABASE_RUNTIME_URL` | yes, for the API | The restricted runtime login (`trace_app_runtime`). `python -m scripts.provision_runtime_role` creates it locally. `DATABASE_URL` stays the admin identity for scripts and tests |
| `TRACE_ENABLED_COHORTS` | no (default `synthetic`) | `synthetic_demo` only: cohorts an explicit `cohort` parameter may name, for engineering isolation tests (never combined). Ignored in `production`. The product frontend never names one |
| `TRACE_CONTRIBUTION_POLICY` | no (default `opt_in`) | `opt_in` or `disabled`; anything else fails closed |
| `TRACE_API_DOCS_ENABLED` | no (default false) | Serve `/docs`, `/redoc`, `/openapi.json` (local development only) |
| `TRACE_REAL_RESUME_UPLOADS` | no (default false) | Accept a user's own resume at `POST /api/v1/real-resumes` |
| `TRACE_CONTRIBUTION_WORKER` | no (default true) | Run the in-process loop that drains the durable contribution queue; `TRACE_CONTRIBUTION_WORKER_INTERVAL` seconds (default 5) |
| `TRACE_CONTRIBUTION_MAX_ATTEMPTS` | no (default 5) | Attempts (1-20) before a failing request becomes terminally `failed`; retry with `scripts.contribution_queue` |
| `TRACE_CONTRIBUTION_BACKOFF_BASE_SECONDS` / `_MAX_SECONDS` | no (30 / 3600) | Exponential backoff between attempts |
| `TRACE_CONTRIBUTION_LEASE_SECONDS` | no (default 120) | Claim lease (10-900); an expired lease counts as an attempt |
| `TRACE_IDENTITY_MARKER_KEY` | for real resumes | AES-256-GCM key sealing identity markers (secret; never in Git). Without it real-resume processing is refused. It must stay the same across restarts: locally keep it in the gitignored `backend/.env`, never generate one per start |
| `TRACE_IDENTITY_MARKER_PREVIOUS_KEYS` | during a rotation | Earlier keys (comma-separated) used only to open older rows; `python -m scripts.privacy_maintenance reseal-markers --apply` re-seals them with the current key |
| `TRACE_QUEUE_EVENT_RETENTION_DAYS` | no (default 90) | Retention for contribution queue events (`scripts.privacy_maintenance`; at least 7) |

Internal QA: `QA_TEST_PASSWORD=... python -m scripts.qa_test_account --email <test account> --fixture <n>` gives a test
account a private profile from an approved synthetic fixture through the normal API (local only; not a product
feature). Sharing preference and its relation to the explicit contribution permission: `docs/SHARING_PREFERENCE.md`.

## Run

```bash
uvicorn app.main:app --reload
```

- `GET http://127.0.0.1:8000/health` → `200 {"status": "ok"}`
- `GET http://127.0.0.1:8000/health/db` → `200 {"status": "ok", "database": "ok"}`,
  or `503` with `"database": "not_configured"` / `"unreachable"`
- Interactive docs: `http://127.0.0.1:8000/docs` (only with `TRACE_API_DOCS_ENABLED=1`)
- The Trace app: `http://127.0.0.1:8000/` (redirects to `/app/`). Needs `SUPABASE_URL`, `SUPABASE_ANON_KEY` and, for
  sample profiles, `TRACE_DEMO_FIXTURES=1`. See [docs/FRONTEND.md](docs/FRONTEND.md).

## Test

```bash
pytest -v
```

The test marked `integration` runs `select 1` against `DATABASE_URL` and is
skipped if `DATABASE_URL` is not set. Run only the offline tests with
`pytest -m "not integration"`.
