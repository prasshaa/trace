-- Compensation for migration 20261116150000_trace_transition_knowledge (v2.21.0). NOT a migration.
-- Roll back 20261116170000 and 20261116160000 first. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261116150000 --status reverted
-- Effect: drops the catalogue and template tables (empty after the content rollback) and the structured-criteria
-- columns and checks. No pm-v1 content is touched.
begin;
drop table public.trace_bridge_roles;
drop table public.trace_action_templates;
drop function trace_util.check_bridge_role();
drop function trace_util.check_action_template();
drop trigger check_employer_criterion on public.trace_target_role_requirements;
drop trigger check_background_families on public.trace_target_role_sources;
drop function trace_util.check_employer_criterion();
drop function trace_util.check_background_families();
drop function trace_util.check_criterion_node(jsonb);
alter table public.trace_target_role_requirements
  drop constraint target_requirements_criterion_versioned, drop constraint target_requirements_criterion_object,
  drop column criterion, drop column criteria_version;
alter table public.trace_target_role_sources
  drop constraint target_sources_accepted_families_when_accepting, drop constraint target_sources_excluded_families_when_excluding,
  drop column accepted_role_families, drop column excluded_role_families;
commit;
