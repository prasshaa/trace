-- Compensation for migration 20261116140000_trace_pm_knowledge_pack_v1_1 (v2.20.0). NOT a migration.
-- Deploy a backend that reads pm-v1 first. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261116140000 --status reverted
-- Effect: removes the pm-v1.1 requirements and their evidence links (cascade). The pm-v1 inventory and requirements stay.
begin;
delete from public.trace_capability_requirements where knowledge_pack = 'pm-v1.1';
commit;
