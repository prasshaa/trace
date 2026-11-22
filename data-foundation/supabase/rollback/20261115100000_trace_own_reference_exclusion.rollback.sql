-- Compensation for migration 20261115100000_trace_own_reference_exclusion (v2.13.0). NOT a migration.
-- Deploy a backend that does not call trace_contrib.my_reference_profile_ids (Phase 8 code) first. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261115100000 --status reverted
-- Effect: a contributor's own reference profile may again count towards their own route evidence and comparisons.
-- No data is touched.
begin;
drop function trace_contrib.my_reference_profile_ids();
commit;
