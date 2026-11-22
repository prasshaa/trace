-- Compensation for migration 20261113180000_trace_marker_protection (v2.11.0). NOT a migration.
-- Deploy a backend that writes plaintext markers (Phase 6.2 code) first. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261113180000 --status reverted
-- Effect: SEALED marker rows are DELETED (they cannot be turned back into plaintext without the key, and the database
-- never has the key); those users' next contributions wait for manual review (identity_markers_missing) until they
-- upload again. The maintenance functions go away (no retention or orphan sweep).
begin;
drop function trace_private.purge_contribution_queue_events(integer, boolean);
drop function trace_private.purge_orphan_identity_markers(interval, boolean);
drop function trace_contrib.forget_identity_markers();
drop function trace_private.identity_markers_needed(uuid, uuid);
drop function trace_contrib.store_identity_markers(bytea, text);
drop function trace_contrib.contribution_identity(uuid);
delete from trace_private.identity_markers where sealed is not null;
alter table trace_private.identity_markers drop constraint identity_markers_sealed_or_legacy,
  drop constraint identity_markers_sealed_pair, drop column sealed, drop column key_id;

create function trace_contrib.store_identity_markers(p_names text[], p_emails text[], p_phones text[], p_urls text[], p_handles text[])
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  v_profile uuid;
begin
  select p.candidate_profile_id into v_profile from public.trace_candidate_profiles p
   where p.owner_user_id = v_uid and not coalesce(p.is_test_fixture, false) and p.fixture_profile_id is null;
  if v_uid is null or v_profile is null then
    raise exception using errcode = 'TR021', message = 'identity markers need the owner''s own non-fixture profile';
  end if;
  insert into trace_private.identity_markers as m (owner_user_id, candidate_profile_id, names, emails, phones, urls, handles)
  values (v_uid, v_profile, trace_private.marker_set('{}', p_names, 16), trace_private.marker_set('{}', p_emails, 16),
          trace_private.marker_set('{}', p_phones, 16), trace_private.marker_set('{}', p_urls, 32),
          trace_private.marker_set('{}', p_handles, 16))
  on conflict (owner_user_id) do update
     set candidate_profile_id = excluded.candidate_profile_id,
         names = trace_private.marker_set(m.names, p_names, 16), emails = trace_private.marker_set(m.emails, p_emails, 16),
         phones = trace_private.marker_set(m.phones, p_phones, 16), urls = trace_private.marker_set(m.urls, p_urls, 32),
         handles = trace_private.marker_set(m.handles, p_handles, 16), updated_at = now();
end;
$$;

create function trace_contrib.contribution_identity(p_user uuid)
returns table (email text, names text[], emails text[], phones text[], urls text[], handles text[], markers_recorded boolean)
language sql
stable
security definer
set search_path = ''
as $$
  select u.email::text,
         trace_private.marker_set(array_remove(array[u.raw_user_meta_data ->> 'full_name', u.raw_user_meta_data ->> 'name',
                                                     u.raw_user_meta_data ->> 'preferred_username'], null), m.names, 32),
         coalesce(m.emails, '{}'), coalesce(m.phones, '{}'), coalesce(m.urls, '{}'), coalesce(m.handles, '{}'),
         m.owner_user_id is not null
    from auth.users u
    left join trace_private.identity_markers m on m.owner_user_id = u.id
   where u.id = p_user
$$;

revoke all on function trace_contrib.store_identity_markers(text[], text[], text[], text[], text[]), trace_contrib.contribution_identity(uuid)
  from public, anon, authenticated, service_role;
grant execute on function trace_contrib.store_identity_markers(text[], text[], text[], text[], text[]), trace_contrib.contribution_identity(uuid)
  to trace_app_runtime;
commit;
