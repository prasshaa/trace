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
| `TRACE_LLM_PROVIDER` | A label, such as `openai` |
| `TRACE_LLM_BASE_URL` | OpenAI-compatible origin, for example `https://api.openai.com/v1` |
| `TRACE_LLM_MODEL` | Model name the provider expects |
| `TRACE_LLM_API_KEY` | Secret. Never commit it |
| `TRACE_EMBEDDINGS` | `1` to compute gte-small vectors locally |
| `TRACE_SEMANTIC_RETRIEVAL` | `1` to use the pgvector hybrid function |

The assessment calls retrieval first. When pgvector and the local model are available, the model sees those lines.
If retrieval falls back, the response says so. The key is sent only as an `Authorization` header and is not logged.

Confirmed lines are sent to the configured provider when the narrative runs. Say that before a demo with anyone
else's text. Embeddings are not sent to the provider.

## Fictional evaluation set

```sh
cd backend && .venv/bin/python -m evaluation.ai_demo
```

This writes `backend/evaluation/ai_demo/outputs/`. Without a key, each file is the retrieval packet and
`status: not_configured`. It does not invent a reading. With a key, each file also contains the guarded narrative.

## Preview, not production

The steps for a Cloud Run preview in `asia-south1` are in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Use a preview
Supabase project. Do not point it at production, and do not set `TRACE_AI_ASSESSMENT` on the production Fly app.
