-- Compensation for migration 20261116190000_trace_pm_knowledge_pack_v1_2 (v2.25.0). NOT a migration.
-- Roll back 20261116200000 first (its bridges reference pm-v1.2), and deploy a backend that reads pm-v1.1. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261116190000 --status reverted
-- Effect: removes the pm-v1.2 requirements and their evidence links (cascade). pm-v1 and pm-v1.1 stay.
begin;
delete from public.trace_capability_requirements where knowledge_pack = 'pm-v1.2';
commit;
