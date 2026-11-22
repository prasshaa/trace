-- Trace data foundation v2.11.0: identity-marker encryption, marker lifecycle and queue-event retention.
--
-- 1. Encryption. Identity markers are now written only as an authenticated-encryption blob (AES-256-GCM, sealed by the
--    backend with a key held in its environment, never in the database or Git; the owner's user id is bound in as
--    associated data). The database stores `sealed` and `key_id` (a fingerprint of the key, not the key) and cannot
--    decrypt. Plaintext marker columns remain only for legacy rows written by v2.10.0; a row is either legacy plaintext
--    or sealed, never both, and every new write is sealed (the store function clears the plaintext columns).
-- 2. Lifecycle. A marker row is NEEDED while its private profile has an admitted real resume or a resume held for
--    review (pending or approved). trace_contrib.forget_identity_markers lets the runtime remove the caller's own
--    markers after an ingestion that did not complete (only when not needed). trace_private.purge_orphan_identity_markers
--    (admin only) removes marker rows that are not needed and older than a grace period; it can never remove the markers
--    of a profile with an admitted resume. Account and profile deletion already cascade.
-- 3. Retention. trace_private.purge_contribution_queue_events (admin only) deletes queue events older than a retention
--    period (at least 7 days). Events hold a pseudonymous user id, an event code, an attempt count, an error class code
--    (restricted pattern) and, for a manual retry, the operator's name: never resume content or identity markers.

-- --------------------------------------------------------------------------- 1. sealed markers
alter table trace_private.identity_markers
  add column sealed bytea check (octet_length(sealed) between 29 and 16384),
  add column key_id text check (key_id ~ '^[0-9a-f]{16}$'),
  add constraint identity_markers_sealed_pair check ((sealed is null) = (key_id is null)),
  add constraint identity_markers_sealed_or_legacy check (
    sealed is null or (cardinality(names) = 0 and cardinality(emails) = 0 and cardinality(phones) = 0
                       and cardinality(urls) = 0 and cardinality(handles) = 0));

drop function trace_contrib.store_identity_markers(text[], text[], text[], text[], text[]);
create function trace_contrib.store_identity_markers(p_sealed bytea, p_key_id text)
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
  insert into trace_private.identity_markers (owner_user_id, candidate_profile_id, sealed, key_id)
  values (v_uid, v_profile, p_sealed, p_key_id)
  on conflict (owner_user_id) do update
     set candidate_profile_id = excluded.candidate_profile_id, sealed = excluded.sealed, key_id = excluded.key_id,
         names = '{}', emails = '{}', phones = '{}', urls = '{}', handles = '{}', updated_at = now();
end;
$$;

drop function trace_contrib.contribution_identity(uuid);
create function trace_contrib.contribution_identity(p_user uuid)
returns table (email text, names text[], emails text[], phones text[], urls text[], handles text[], markers_recorded boolean,
               sealed bytea, key_id text)
language sql
stable
security definer
set search_path = ''
as $$
  select u.email::text,
         trace_private.marker_set(array_remove(array[u.raw_user_meta_data ->> 'full_name', u.raw_user_meta_data ->> 'name',
                                                     u.raw_user_meta_data ->> 'preferred_username'], null), m.names, 32),
         coalesce(m.emails, '{}'), coalesce(m.phones, '{}'), coalesce(m.urls, '{}'), coalesce(m.handles, '{}'),
         m.owner_user_id is not null, m.sealed, m.key_id
    from auth.users u
    left join trace_private.identity_markers m on m.owner_user_id = u.id
   where u.id = p_user
$$;

-- --------------------------------------------------------------------------- 2. lifecycle
-- True while the markers of this owner's profile are needed for future de-identification.
create function trace_private.identity_markers_needed(p_owner uuid, p_profile uuid)
returns boolean
language sql
stable
set search_path = ''
as $$
  select exists (select 1 from trace_ingest.deidentified_resume_admissions a
                  where a.owner_user_id = p_owner and a.candidate_profile_id = p_profile)
      or exists (select 1 from trace_private.resume_quarantine q
                  where q.owner_user_id = p_owner and q.candidate_profile_id = p_profile
                    and q.status in ('pending_review', 'approved'))
$$;

-- Runtime, caller's own: remove markers left by an ingestion that did not complete. Never removes needed markers.
create function trace_contrib.forget_identity_markers()
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
begin
  if v_uid is null then
    return false;
  end if;
  delete from trace_private.identity_markers m
   where m.owner_user_id = v_uid and not trace_private.identity_markers_needed(m.owner_user_id, m.candidate_profile_id);
  return found;
end;
$$;

-- Admin: marker rows that are not needed and older than the grace period (at least one hour, so an upload in progress,
-- which stores markers just before its admission, is never affected). Dry run unless p_apply.
create function trace_private.purge_orphan_identity_markers(p_min_age interval, p_apply boolean)
returns integer
language plpgsql
set search_path = ''
as $$
declare
  v_count integer;
begin
  if p_min_age is null or p_min_age < interval '1 hour' then
    raise exception using errcode = 'invalid_parameter_value', message = 'the grace period must be at least one hour';
  end if;
  if not coalesce(p_apply, false) then
    select count(*) into v_count from trace_private.identity_markers m
     where m.updated_at < now() - p_min_age and not trace_private.identity_markers_needed(m.owner_user_id, m.candidate_profile_id);
    return v_count;
  end if;
  delete from trace_private.identity_markers m
   where m.updated_at < now() - p_min_age and not trace_private.identity_markers_needed(m.owner_user_id, m.candidate_profile_id);
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

-- --------------------------------------------------------------------------- 3. queue-event retention
create function trace_private.purge_contribution_queue_events(p_retention_days integer, p_apply boolean)
returns integer
language plpgsql
set search_path = ''
as $$
declare
  v_count integer;
begin
  if p_retention_days is null or p_retention_days < 7 then
    raise exception using errcode = 'invalid_parameter_value', message = 'the retention period must be at least 7 days';
  end if;
  if not coalesce(p_apply, false) then
    select count(*) into v_count from trace_private.contribution_queue_events e
     where e.created_at < now() - make_interval(days => p_retention_days);
    return v_count;
  end if;
  delete from trace_private.contribution_queue_events e where e.created_at < now() - make_interval(days => p_retention_days);
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke all on function trace_contrib.store_identity_markers(bytea, text), trace_contrib.contribution_identity(uuid),
  trace_contrib.forget_identity_markers(), trace_private.identity_markers_needed(uuid, uuid),
  trace_private.purge_orphan_identity_markers(interval, boolean), trace_private.purge_contribution_queue_events(integer, boolean)
  from public, anon, authenticated, service_role;
grant execute on function trace_contrib.store_identity_markers(bytea, text), trace_contrib.contribution_identity(uuid),
  trace_contrib.forget_identity_markers()
  to trace_app_runtime;
