# Reference-snapshot cleanup (D37 proposal; done 2026-11-17 under D39)

**Date:** 2026-11-16. **Environment:** local Supabase only.

**Status:** **done on 2026-11-17 (option A, D39).** The outcome is at the end; the analysis below is as proposed.

## What exists

`trace_ref.reference_snapshots` holds 642 rows (392 kB):

| Group | Rows | Contents | Referenced by a run |
|---|---|---|---|
| Legacy (no `state_fingerprint`), written by the v2.23.0 writer | **640** | One row per plan created before v2.27.0 | **0** |
| Shared (fingerprinted), written by the v2.27.0 writer | 2 | One row per distinct reference-pool state | 0 (all their runs were deleted with their test accounts) |

### The 640 legacy rows

| Item | Finding |
|---|---|
| Columns | `snapshot_id`, `data_mode`, `settings`, `eligible_profile_count`, `confirmed_transition_count`, `notes`, `created_at` (with `state_fingerprint` and `first_observed_on` null). No user, plan, profile or reference-profile identifier |
| Provenance | All have `notes = 'transition plan run: counts and settings only'`, the fixed text of the v2.23.0 writer. All were created on 2026-11-16 |
| Who created them | Plans created by transient test accounts during the Phase 4 test runs. The local database now has **0 accounts and 0 plan runs**, so no living user is linked to any row |
| Distinct states | Only **two** distinct pool states: `synthetic_test`, 41 profiles, 55 confirmed transitions (599 rows); `empirical`, 0 and 0 (41 rows). Both use the same analytics settings. These are the same two states the shared rows already hold |
| Relationships | One foreign key: `trace_pathway_runs(reference_snapshot_id, data_mode)`, `ON DELETE RESTRICT`. No run references a legacy row. No triggers, views or other references depend on the table |
| Who can read them | Administrators only. Clients have no grant or policy. The plan writer reads fingerprinted rows only |
| Re-identification risk | None direct. The residual timing link (one row per plan, microsecond time) ended when their runs and accounts were deleted. Nothing else holds a run time to correlate with |

## Cleanup impact

Deleting the 640 legacy rows:
- affects **no run**: none references them, so the restrictive foreign key would not block the deletion;
- breaks no audit trail: their runs no longer exist;
- removes no information that isn't already held by the two shared rows, which have the same states and settings;
- frees about 390 kB locally;
- makes the v2.27.0 rollback no harder: it already refuses while shared rows exist, and legacy rows don't affect it.

`service_role` can no longer delete from this table (D38). The cleanup would run as the migration and administration
identity (`postgres`), as a one-off compensation script under the same conventions as the rollbacks.

## Proposed options (choose one)

| Option | What it does | Recommendation |
|---|---|---|
| A. Delete the unreferenced legacy rows | `delete from trace_ref.reference_snapshots s where s.state_fingerprint is null and not exists (select 1 from public.trace_pathway_runs r where r.reference_snapshot_id = s.snapshot_id);`, in a reviewed one-off script with a before and after count, run locally only | **Recommended.** They exist only because of local test runs; any future environment starts with the v2.27.0 writer and never creates them |
| B. Keep them, marked as legacy | Leave the rows; document them as test artefacts | Acceptable, but they serve no purpose |
| C. Retention policy for unreferenced snapshots in general | An administrator-only job that removes snapshots no run has referenced for a stated period | Not needed now: shared snapshots grow with distinct pool states, not with plans. Revisit when real contributions exist |

Whichever option you choose, there is no change to the schema, the writer or the privileges.

## Outcome (2026-11-17, option A approved as D39)

**Pre-checks** (read-only, all passed):

| Check | Result |
|---|---|
| Legacy rows (no fingerprint) | Exactly **640**, all with the v2.23.0 writer's note |
| Referenced by a plan run (the only foreign key) | 0 |
| Referenced anywhere else | 0: every uuid, text and jsonb column in `public`, `trace_ref`, `trace_private`, `trace_contrib`, `trace_ops` and `auth` was scanned for the 640 ids |
| Plan runs or accounts in the local database | 0 and 0 |
| Distinct states among the 640 | 2 |
| Legacy rows without a shared row identical in data mode, settings, both counts and fingerprint (recomputed as the writer does) | **0** |
| Earliest legacy date against the shared `first_observed_on` | 2026-11-16 for both states. Already the earliest, so nothing needed preserving |

**Backups** (local, git-ignored, never packaged), in `data-foundation/local_only/backups/`:
- `reference_snapshots_pre_cleanup_20261117.pg_dump.sql`: `pg_dump --data-only` of the whole table, 642 rows; SHA-256
  `9d70ca0acf61ef90235d808ce8849128f61bc52e71ddfb3aa4c3de9db43c4dc0`;
- `legacy_snapshots_20261117.csv`: exactly the 640 rows, all columns; SHA-256
  `4baf74ffb1a5b03277e2364bc6d59dcea243b2d5b31b5c8b9405d3692a5ababa`;
- `SHA256SUMS`.

**Rehearsal** (inside one transaction, then rolled back):
1. 642 rows before;
2. the cleanup removed 640, leaving the 2 shared rows;
3. the restore script reloaded 640 from the CSV;
4. the table was **byte-identical** to before (md5 over every row);
5. after the rollback, 642 rows, unchanged.

**Cleanup:** `data-foundation/supabase/maintenance/20261117_legacy_reference_snapshot_cleanup.sql`, run as `postgres`
with `expected=640`. It is one transaction: preconditions, then the earliest-date preservation, then a delete limited to
the verified ids, then an exact row-count check.

| Result | Value |
|---|---|
| Rows removed | **640** (NOTICE "removed 640 legacy reference snapshots"; COMMIT) |
| Table after | 2 rows, both shared, 0 legacy |
| Shared rows | **byte-identical** before and after (md5 match) |
| Remaining states | `synthetic_test` 41/55 and `empirical` 0/0, both first observed 2026-11-16 |

**Restoring** (only if ever needed): `20261117_legacy_reference_snapshot_restore.sql` reloads the exact original rows
from the CSV backup. It refuses if the file doesn't hold exactly 640 rows, or if any of those rows already exists. It
never fabricates rows; without the backup it cannot run.
