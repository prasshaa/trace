-- Compensation for migration 20261116110000_trace_target_role_knowledge (v2.16.0). NOT a migration.
-- Roll back 20261116120000 (the pm-v1 pack) first, and deploy a backend without the capability assessment. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261116110000 --status reverted
-- Effect: drops the canonical requirements, the evidence table, the two D26 functions and the knowledge-pack columns on
-- trace_target_role_sources (their values are lost; rows that existed before v2.16.0 are otherwise unchanged).
begin;
drop function trace_util.record_posting_verification(text, text, timestamptz, integer);
drop function trace_util.posting_effective_status(text, text, timestamptz, timestamptz);
drop table public.trace_requirement_source_evidence;
drop table public.trace_capability_requirements;
drop function trace_util.check_capability_codes();
drop index public.trace_target_role_sources_pack_idx;
alter table public.trace_target_role_sources
  drop constraint target_sources_key_unique,
  drop constraint target_sources_kind,
  drop constraint target_sources_pack_complete,
  drop constraint target_sources_posting_type,
  drop constraint target_sources_stance,
  drop constraint target_sources_stance_postings_only,
  drop constraint target_sources_accepted_only_when_accepting,
  drop constraint target_sources_excluded_only_when_excluding,
  drop constraint target_sources_quote_short,
  drop constraint target_sources_quote_has_locator,
  drop constraint target_sources_posting_current_has_revalidation,
  drop column source_key, drop column knowledge_pack, drop column source_kind, drop column publisher, drop column geography,
  drop column licence, drop column attribution, drop column level_as_stated, drop column context_tags, drop column last_verified_at,
  drop column revalidate_by, drop column status_note, drop column entry_background_stance, drop column accepted_backgrounds,
  drop column excluded_backgrounds, drop column entry_quote, drop column entry_locator;
commit;
