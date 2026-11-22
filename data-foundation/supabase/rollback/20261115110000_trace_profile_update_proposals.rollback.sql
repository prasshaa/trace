-- Compensation for migration 20261115110000_trace_profile_update_proposals (v2.14.0). NOT a migration.
-- Deploy a backend that does not use update proposals (pre product Phase 2) first. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261115110000 --status reverted
-- Effect: OPEN proposals (items a user has not yet accepted or dismissed) are deleted with the table. Records already
-- created or corrected by accepting a proposal stay in the profile, with their correction history. They lose only the
-- link back to the proposal (source hash and extractor version). No other data is touched.
begin;
drop table public.trace_profile_update_proposals;
drop function trace_util.guard_update_proposal();
drop function trace_util.proposal_record_exists(text, uuid, uuid, uuid);
commit;
