-- Compensation for migration 20261122100000_trace_role_aliases_v2_and_move_preferences (v2.32.0). NOT a migration.
-- Deploy a backend without the move-preference endpoint first. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261122100000 --status reverted
-- Refuses while any user has a saved move preference (users' own preferences are never discarded silently), or while any
-- employment is mapped through one of the v2.32.0 aliases (removing the alias would orphan that mapping's provenance).
-- The v2.29.0 writer role stays: the earlier preference tables still use it.
begin;
do $$
begin
  if exists (select 1 from trace_private.target_move_preferences) then
    raise exception 'move preferences exist; export or have users clear them before rolling back';
  end if;
  if exists (select 1 from public.trace_candidate_employments
              where role_mapping_alias_id between 'ALS-0107' and 'ALS-0116') then
    raise exception 'employments are mapped through v2.32.0 aliases; remap them before rolling back';
  end if;
end;
$$;
drop function trace_plan.set_move_preference(text, text, text[]);
drop function trace_plan.my_move_preference(text);
drop table trace_private.target_move_preferences;             -- its policies go with it
delete from public.trace_role_aliases where alias_id between 'ALS-0107' and 'ALS-0116';
commit;
