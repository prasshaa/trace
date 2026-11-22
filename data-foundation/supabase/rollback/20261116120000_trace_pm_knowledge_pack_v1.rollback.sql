-- Compensation for migration 20261116120000_trace_pm_knowledge_pack_v1 (v2.17.0). NOT a migration.
-- Deploy a backend that does not read the pm-v1 pack (pre product Phase 3) first. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261116120000 --status reverted
-- Effect: removes the pm-v1 sources, their employer criteria and evidence (cascade), and the pm-v1 canonical requirements.
-- No user data is involved.
begin;
delete from public.trace_capability_requirements where knowledge_pack = 'pm-v1';
delete from public.trace_target_role_sources where knowledge_pack = 'pm-v1';
commit;
