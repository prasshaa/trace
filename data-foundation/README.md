# Trace Data Foundation v2

The database foundation for Trace: a product that helps working professionals in India explore routes into APM, PM, Senior PM, Lead/Principal PM and Director of Product roles.

The package contains:
- versioned Supabase migrations;
- a controlled role taxonomy;
- 41 clearly labelled synthetic career histories, with seed SQL generated from them;
- a validator, an automated test-suite and design documentation.

> **Status: for review.** Nothing has been applied to any Supabase project. No SQL has been executed, and no database objects or records exist yet.

## What changed from v1

See [CHANGELOG.md](CHANGELOG.md) for every design decision. In short:
- shared career data has moved to a non-exposed schema;
- consent is an append-only event log tied to profiles;
- dates carry explicit bounds;
- transitions no longer drop same-month moves or bridge unknown roles;
- `data_mode` keeps synthetic evidence from being presented as real;
- the 18 uploaded third-party resumes have been removed from the shared package;
- v2.1.0 adds episode-aware pathways (role + employer episodes) alongside the role-code objects. See `docs/DATA_DICTIONARY.md` §6a;
- v2.2.0 adds candidate-side structured career profile storage (education, career activities, skill links, provenance). See §10a;
- v2.3.0 enforces candidate review rules in the database (column grants, guard triggers, `trace_review` functions). See `docs/ACCESS_CONTROL.md` § Review enforcement;
- v2.4.0 adds trusted ingestion of extracted drafts for the synthetic fixture resumes (`trace_ingest` functions). See `docs/ACCESS_CONTROL.md` § Extracted-draft ingestion;
- v2.5.0 adds corrections with append-only correction history (`trace_review.correct_record`). See `docs/ACCESS_CONTROL.md` § Corrections;
- v2.6.0 adds trusted role mapping and candidate role decisions (`trace_review.apply_alias_mapping`, `choose_employment_role`). See `docs/ACCESS_CONTROL.md` § Role mapping;
- v2.7.0 adds candidate classification of evidence responsibility areas (`trace_review.classify_evidence_areas`). See `docs/ACCESS_CONTROL.md` § Evidence classification.

## Layout

```
supabase/
  migrations/                 versioned migrations, applied in filename order (01–18)
  rollback/                   forward-compensation scripts (never applied automatically)
  seeds/synthetic_fixtures.sql  GENERATED synthetic fixtures: local / preview / test databases only
  tests/database/             pgTAP tests for `supabase test db` (local stack)
data/                         canonical sources (edit these)
  taxonomy/                   role families, seniority ladder, roles, aliases, responsibility areas
  skills/                     skill families, skills, skill aliases
  config/                     analytics thresholds, data-source registry
  synthetic/
    synthetic_profiles.jsonl  canonical synthetic profiles (source of truth)
    csv/                      GENERATED flat review copies
    synthetic_resume_cards.md, synthetic_scenario_coverage.csv     GENERATED
    synthetic_resume_pdfs/, synthetic_resume_pdfs.zip             GENERATED (render script)
    synthetic_resume_pdf_hashes.csv   GENERATED (build_seeds.py): approved fixture id -> PDF SHA-256
scripts/
  trace_model.py              reference implementation of dates, sequences, transitions, routes
  build_seeds.py              generates every derived artefact; --check verifies they are current
  validate_seed_data.py       validates all CSV/JSONL sources and generated artefacts
  render_synthetic_resumes.py renders the synthetic PDFs (optional; needs reportlab)
  package_review.py           builds a shareable review ZIP without local_only/ and verifies it
tests/                        unittest suite (standard library; pglast optional)
docs/
  DATA_DICTIONARY.md          specification: classes, dates, sequences, metrics, thresholds, taxonomy
  ACCESS_CONTROL.md           RLS matrix, storage, retention, environments
  INTAKE_AND_CONSENT.md       participant intake and consent drafts (not legal advice)
local_only/                   RESTRICTED, git-ignored. Never commit, share or import.
manifest.json                 GENERATED file hashes and counts
```

## Data-use rules

1. **The 18 uploaded resume fixtures stay out of every shared database.** Permission from the resume subjects for shared reuse is unverified. The database rejects the `uploaded_resume_fixture` class. The files live only in git-ignored `local_only/` for local regression tests.
2. **Synthetic profiles are for testing and labelled demos only.** They never produce empirical statistics, prevalence figures, success probabilities or market timelines. The schema prevents a synthetic run from carrying `strong`/`moderate` evidence or displaying a timeline.
3. **Empirical analytics read only `trace_ref.v_eligible_profiles`.** That view needs consented real profiles, an active `deidentified_shared_dataset` consent under a counsel-approved consent document, and participant or reviewer confirmation. No consent document is approved yet, so **0 profiles are eligible**.
4. **Original resumes stay private.** They go to the private `trace-resumes` bucket, are served by signed URLs only, and are deleted after 30 days by default.
5. **The service-role key never reaches a browser.**

## Validate and test (no database needed)

Requires Python 3.9 or later.

```bash
python3 scripts/build_seeds.py --check        # generated artefacts match their sources
python3 scripts/validate_seed_data.py         # all seed, taxonomy and config checks
python3 -m unittest discover -s tests -t tests -v
```

Optional extras:
- With `pip install pglast`, the SQL tests also parse every migration, seed and pgTAP file, and every function body, with the PostgreSQL 17 grammar.
- Without `local_only/`, the restricted-fixture tests are skipped automatically.

## Sharing for review

Run `python3 scripts/package_review.py --out ../trace-data-foundation-v2-review.zip`. This:
- packages exactly the files in `manifest.json`;
- refuses if any shared file contains restricted identifiers or text;
- re-verifies the manifest inside the ZIP, without `local_only/`.

Never ZIP the folder by hand.

## Editing data

1. Edit the canonical sources in `data/`.
2. If synthetic profiles changed, re-render the PDFs: `pip install "reportlab<4.3"`, then run `python3 scripts/render_synthetic_resumes.py`.
3. Run `python3 scripts/build_seeds.py` to regenerate the seed SQL, the lookup-data migration, the CSVs, the cards and the manifest.
4. Run the validator and the tests.

Lookup changes made **after** the lookup-data migration has been applied to a shared database must ship as a **new** timestamped migration. Never edit an applied migration. Retire roles with `active = false` instead of deleting them.

## Setting up Supabase (when approved)

Not done yet. These are the intended steps for review.

1. **Start locally first.** Run `supabase init` in the app repository and copy `supabase/migrations`, `supabase/seeds` and `supabase/tests` into it. In `supabase/config.toml`, set:
   ```toml
   [db.seed]
   enabled = true
   sql_paths = ["./seeds/synthetic_fixtures.sql"]
   ```
2. Run `supabase start`, then `supabase db reset`. This applies the migrations in order through Supabase's migration tracking and loads the synthetic seed.
3. Run `supabase test db` (the pgTAP tests) and `supabase db lint`.
4. **Check the API settings.** Exposed schemas must be `public` (plus `graphql_public` if used) only. Never expose `trace_ref` or `trace_util`.
5. **Preview branches** run the same migrations and synthetic seed.
6. **Production.** Link the project and run `supabase db push`, which applies migrations only. **Do not** use `--include-seed`; the synthetic seed also refuses to run once real reference profiles exist.
7. **Before collecting any real data:**
   - get counsel approval of the consent texts;
   - insert the approved `trace_consent_documents` rows;
   - deploy the resume-retention job (see `docs/ACCESS_CONTROL.md`);
   - set up a server-side contribution flow that de-identifies a confirmed candidate profile into `trace_ref` only under an active `deidentified_shared_dataset` grant.

The seed is repeatable: it upserts by stable id and deletes synthetic rows that are no longer in the source file, inside one transaction.

## Counts

| Item | Count |
|---|---|
| Migrations | 18 |
| Roles / aliases / seniority levels | 34 / 106 / 15 |
| Skills (all classified) / skill aliases | 126 / 10 |
| Synthetic profiles / employment records / evidence rows | 41 / 106 / 106 |
| Synthetic resume PDFs | 41 |
| Uploaded-resume fixtures in shared files | 0 (18 restricted, local only) |
| Profiles eligible for empirical analytics | 0 |

## Not a legal assessment

The consent material is a product-design draft, not legal advice. Have Indian privacy counsel review it, including DPDP Act obligations, cross-border processing and processor contracts, before collecting or reusing career histories.
