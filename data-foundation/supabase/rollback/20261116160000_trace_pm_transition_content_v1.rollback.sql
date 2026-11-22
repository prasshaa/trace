-- Compensation for migration 20261116160000_trace_pm_transition_content_v1 (v2.22.0). NOT a migration.
-- Roll back 20261116170000 (run write path) first. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261116160000 --status reverted
-- Effect: removes the seeded catalogue, templates and the two bridge sources, and clears the structured criteria and
-- background families on the pm-v1 rows (their original text, importance and sources are untouched).
begin;
delete from public.trace_bridge_roles where catalogue_version = 'pm-bridges-v1';
delete from public.trace_action_templates where template_version = 'pm-actions-v1';
update public.trace_target_role_requirements set criterion = null, criteria_version = null where criteria_version = 'pm-eligibility-v1';
update public.trace_target_role_sources set accepted_role_families = '{}', excluded_role_families = '{}'
 where source_key in ('pm-v1:J11', 'pm-v1:J12');
delete from public.trace_target_role_sources where knowledge_pack = 'pm-bridges-v1';
commit;
