-- Compensation for migration 20261116200000_trace_pm_transition_content_v2 (v2.26.0). NOT a migration.
-- Roll back 20261116210000 first, and deploy a backend that reads the v1 content. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261116200000 --status reverted
-- Effect: removes the v2 catalogue, candidate pool, templates, education fields and v2 rules. v1 content is untouched.
-- Saved plan actions reference v2 templates by foreign key, so this refuses while any such action exists.
begin;
delete from public.trace_employer_criterion_rules where rules_version = 'pm-eligibility-v2';
delete from public.trace_education_fields;
delete from public.trace_action_templates where template_version = 'pm-actions-v2';
delete from public.trace_digital_role_candidates where catalogue_version = 'pm-bridges-v2';
delete from public.trace_bridge_roles where catalogue_version = 'pm-bridges-v2';
commit;
