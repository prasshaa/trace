-- Compensation for migration 20261114100000_trace_sharing_preference (v2.12.0). NOT a migration.
-- Deploy a backend without the /api/v1/sharing routes (Phase 7 code) first. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261114100000 --status reverted
-- Effect: sharing preferences and their history are DELETED. Users who chose 'private' had their contributions
-- withdrawn when they chose it; that removal is not undone. Before running, record a 'withdrawn' contribution consent
-- event for every user whose preference is 'private' and who still has an active grant, or their next profile change
-- would queue a contribution again under the restored v2.11.0 rule (explicit grant only):
--   select user_id from trace_private.sharing_preferences s where s.mode = 'private'
--      and exists (select 1 from trace_ref.v_current_consent v where v.user_id = s.user_id
--                   and v.purpose = 'deidentified_shared_dataset' and v.is_active);
begin;
drop function trace_contrib.set_sharing_preference(text);
drop function trace_contrib.acknowledge_data_use_notice(text);
drop function trace_contrib.my_sharing_preference();
drop function trace_private.exclude_from_shared_pool(uuid, text);

create or replace function trace_private.contribution_consent_active(p_user uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (select 1 from trace_ref.v_current_consent v
                  where v.user_id = p_user and v.purpose = 'deidentified_shared_dataset' and v.is_active)
$$;
revoke all on function trace_private.contribution_consent_active(uuid) from public, anon, authenticated, service_role;

drop function trace_private.sharing_is_private(uuid);
drop table trace_private.sharing_preference_events;
drop table trace_private.sharing_preferences;
drop function trace_private.sharing_events_append_only();
commit;
