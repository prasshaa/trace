-- Compensation for migration 20261117100000_trace_pm_eligibility_v3 (v2.28.0). NOT a migration.
-- Deploy a backend that reads pm-eligibility-v2 first. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261117100000 --status reverted
-- Effect: removes the pm-eligibility-v3 rule rows only. v1 (criterion column) and v2 rules are untouched; saved plans keep
-- their stored eligibility results (runs record the rules version they used).
begin;
delete from public.trace_employer_criterion_rules where rules_version = 'pm-eligibility-v3';
commit;
