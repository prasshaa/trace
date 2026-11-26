# Running a Trace demo

Use a **fictional** resume. Do not upload a real person's CV. The twenty personas in
`backend/evaluation/ai_demo/personas.py` are invented and say so in the header.

This does not deploy anything and does not touch the production database.

## What the demo shows

Upload a resume, confirm the profile, pick a target on the product ladder, and read three things: what the experience
proves for that role, what is still missing, and a concrete next step. Every stated result cites a line from the profile.
A figure that is not in the cited line is dropped. "Worked with", "supported" and "partnered" are not treated as
ownership. "Did not own" is not treated as ownership.

Associate Product Manager, Product Manager and Senior Product Manager produce a plan. The moves Trace assesses in full
are: into Associate or Product Manager from outside product, Associate to Product Manager, and Product Manager to
Senior Product Manager. Any other move on that ladder is labelled **Limited support**: the lines are shown, and no
capability status is assigned.

## Local app

```sh
cd data-foundation && supabase start
cd ../backend && python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
# fill SUPABASE_URL and SUPABASE_ANON_KEY from `supabase status`
.venv/bin/python -m scripts.provision_runtime_role
.venv/bin/python -m uvicorn app.main:app --port 8000
```

Open http://127.0.0.1:8000/app/ . Create an account, upload a fictional PDF or Word file, confirm the profile, choose
Associate Product Manager or Product Manager, and open the plan.

Apply database migrations only to this local stack (or a preview project). Do not apply them to production.
v2.36.0 is required for the private-by-default sharing preference and for account deletion.
v2.34.0 and v2.35.0 are required before semantic retrieval can use pgvector.

## Model and retrieval

The narrative is off until these are set in the environment (or `.env`, which is not committed):

| Variable | Purpose |
|---|---|
| `TRACE_AI_ASSESSMENT` | `1` to turn the narrative on |
| `TRACE_LLM_PROVIDER` | A label. For xAI Grok, `xai` |
| `TRACE_LLM_BASE_URL` | OpenAI-compatible origin. For xAI, `https://api.x.ai/v1` |
| `TRACE_LLM_MODEL` | Model name the provider expects, for example `grok-4.7` |
| `TRACE_LLM_API_KEY` | Secret. Never commit it |
| `TRACE_LLM_TIMEOUT` | Seconds for the whole reading, including one retry. Default `120` |
| `TRACE_LLM_FIRST_ATTEMPT_SECONDS` | First try stops here. Default `70`, so a timeout still leaves time for the retry |
| `TRACE_LLM_REASONING_EFFORT` | `low` by default. grok-4.7 accepts `low`, `medium`, `high`, `xhigh` on chat completions. Omitting it makes xAI use `high` |
| `TRACE_EMBEDDINGS` | `1` to compute gte-small vectors locally |
| `TRACE_SEMANTIC_RETRIEVAL` | `1` to use the pgvector hybrid function |

The plan returns immediately. The page shows “Reading your experience.” After about 30 seconds it says it is still working, and if the first call times out or the provider returns a server error it tries once more. The wait stops on its own. It does not hang.
The model call shuts the socket down when its budget ends, including when headers have already arrived and the body is still arriving. The first try uses `TRACE_LLM_FIRST_ATTEMPT_SECONDS` (default 70). A timeout or a server error is tried once more with the time still left inside `TRACE_LLM_TIMEOUT`.
A short profile (eight excerpts or fewer) is passed in full. A longer one is ranked with local gte-small, and with
pgvector when `TRACE_SEMANTIC_RETRIEVAL=1` and the database is available. A denial and a measured result are always kept.
The key is sent only as an `Authorization` header. Logs record latency and token counts, not the text and not the key.

Confirmed experience is sent to the configured provider when the narrative runs. Say that before a demo with anyone
else's text. Embeddings are not sent to the provider. The same `TRACE_LLM_TIMEOUT` is read by `python -m evaluation.ai_demo`.

## Fictional evaluation set

```sh
cd backend && .venv/bin/python -m evaluation.ai_demo
```

This writes `backend/evaluation/ai_demo/outputs/`. Without a key, each file is the retrieval packet and
`status: not_configured`. It does not invent a reading. With a key, each file also contains the guarded narrative.

## Preview, not production

The steps for a Cloud Run preview in `asia-south1` are in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Use a preview
Supabase project. Do not point it at production, and do not set `TRACE_AI_ASSESSMENT` on the production Fly app.
