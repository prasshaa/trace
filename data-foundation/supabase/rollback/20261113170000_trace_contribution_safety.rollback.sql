-- Compensation for migration 20261113170000_trace_contribution_safety (v2.10.0). NOT a migration.
-- Deploy a backend that does not use identity markers or bounded retries first (Phase 6.1 code). Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261113170000 --status reverted
-- Effect: stored identity markers and queue events are DELETED (the restricted store goes away); requests in the
-- terminal failed state are queued again (unbounded retries, as in v2.9.0); the v2.9.0 functions are restored.
begin;
drop function trace_contrib.store_identity_markers(text[], text[], text[], text[], text[]);
drop function trace_contrib.report_contribution_failure(uuid, uuid, text, integer, integer, integer);
drop function trace_contrib.release_contribution_requests(uuid[]);
drop function trace_contrib.claim_contribution_requests(integer, integer, integer, integer, integer);
drop function trace_private.retry_contribution(uuid, text);
drop function trace_contrib.contribution_identity(uuid);

create or replace function trace_contrib.record_contribution_outcome(p_user uuid, p_requested_at timestamptz, p_state text, p_reason text,
                                                          p_record jsonb, p_record_sha256 text, p_report jsonb,
                                                          p_pipeline_version text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_state text := p_state;
  v_reason text := p_reason;
  v_contributor uuid;
  v_existing trace_private.contributions%rowtype;
  v_profile_id text;
  v_event uuid;
begin
  if p_state not in ('not_eligible', 'eligible', 'quarantined', 'failed') then
    raise exception using errcode = 'invalid_parameter_value', message = 'unknown contribution outcome';
  end if;
  -- Re-checked here, whatever the caller says.
  if v_state = 'eligible' and not trace_private.contribution_consent_active(p_user) then
    v_state := 'not_eligible'; v_reason := 'consent_not_granted';
  end if;
  if v_state = 'eligible' and exists (select 1 from public.trace_candidate_profiles p where p.owner_user_id = p_user
                                        and (p.is_test_fixture or p.fixture_profile_id is not null)) then
    v_state := 'not_eligible'; v_reason := 'synthetic_profile';
  end if;
  if v_state = 'eligible' and (p_record is null or p_record_sha256 is null
      or p_record::text ~* '[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}'
      or p_record::text ~* '(https?://|www\.|linkedin\.com|github\.com)') then
    v_state := 'failed'; v_reason := 'residual_identifier';
  end if;

  select c.* into v_existing from trace_private.contributions c join trace_private.contributors k using (contributor_id)
   where k.user_id = p_user and c.status = 'admitted' for update of c;

  if v_state = 'eligible' then
    select v.consent_event_id into v_event from trace_ref.v_current_consent v
     where v.user_id = p_user and v.purpose = 'deidentified_shared_dataset';
    if v_existing.contribution_id is not null then
      if v_existing.source_record_sha256 is distinct from p_record_sha256 then        -- same person, same ids, new content
        perform trace_private.write_reference_rows(v_existing.profile_id, p_record);
        update trace_private.contributions set source_record_sha256 = p_record_sha256, deidentification_report = coalesce(p_report, '{}'),
               pipeline_version = p_pipeline_version, consent_event_id = v_event, updated_at = now(), reviewed_at = now()
         where contribution_id = v_existing.contribution_id;
      end if;
    else
      insert into trace_private.contributors (source_kind, user_id) values ('app_user', p_user)
      on conflict (user_id) do nothing;
      select contributor_id into v_contributor from trace_private.contributors where user_id = p_user;
      loop
        v_profile_id := 'CRR-' || lpad((floor(random() * 1e12))::bigint::text, 12, '0');
        exit when not exists (select 1 from trace_ref.profiles where profile_id = v_profile_id)
              and not exists (select 1 from trace_private.contributions where profile_id = v_profile_id);
      end loop;
      insert into trace_private.contributions (contributor_id, profile_id, status, deidentification_status, deidentification_report,
                                               pipeline_version, reviewed_by, reviewed_at, admitted_at, source_record_sha256,
                                               consent_event_id)
      values (v_contributor, v_profile_id, 'admitted', 'passed', coalesce(p_report, '{}'), p_pipeline_version, 'automatic',
              now(), now(), p_record_sha256, v_event);
      insert into trace_ref.profiles (profile_id, source_id, dataset_class, country_code, review_status, synthetic_demo_allowed)
      values (v_profile_id, 'SRC-CONTRIB-001', 'consented_real_reference',
              coalesce(nullif(p_record ->> 'country_code', ''), 'IN'), 'participant_confirmed', false);
      perform trace_private.write_reference_rows(v_profile_id, p_record);
    end if;
  elsif v_existing.contribution_id is not null then
    -- No longer eligible as it stands: suspend now; a later eligible pass starts a fresh contribution.
    update trace_private.contributions set status = 'suspended', updated_at = now() where contribution_id = v_existing.contribution_id;
    delete from trace_ref.profiles where profile_id = v_existing.profile_id;
    update trace_private.contributions set profile_id = null, deleted_at = now() where contribution_id = v_existing.contribution_id;
  end if;

  perform trace_private.set_contribution_state(p_user, v_state, v_reason);
  delete from trace_private.contribution_requests r where r.user_id = p_user and r.requested_at = p_requested_at;
  update trace_private.contribution_requests r set locked_until = null where r.user_id = p_user;
  return v_state;
end;
$$;

create or replace function trace_private.enqueue_contribution(p_user uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if p_user is null or not trace_private.contribution_consent_active(p_user) then
    return;
  end if;
  insert into trace_private.contribution_requests (user_id, requested_at) values (p_user, clock_timestamp())
  on conflict (user_id) do update set requested_at = excluded.requested_at;
  perform trace_private.set_contribution_state(p_user, 'pending', null);
end;
$$;

create function trace_contrib.claim_contribution_requests(p_limit integer, p_lease_seconds integer)
returns table (user_id uuid, requested_at timestamptz)
language plpgsql
security definer
set search_path = ''
as $$
begin
  return query
  with picked as (
    select r.user_id from trace_private.contribution_requests r
     where r.locked_until is null or r.locked_until < clock_timestamp()
     order by r.requested_at
     limit greatest(1, least(p_limit, 100))
     for update skip locked
  )
  update trace_private.contribution_requests r
     set locked_until = clock_timestamp() + make_interval(secs => greatest(10, least(p_lease_seconds, 900))), attempts = r.attempts + 1
    from picked where r.user_id = picked.user_id
  returning r.user_id, r.requested_at;
  update trace_private.user_contribution_status s set state = 'processing', updated_at = now()
   where s.user_id in (select r.user_id from trace_private.contribution_requests r where r.locked_until > clock_timestamp())
     and s.state = 'pending';
end;
$$;

create function trace_contrib.contribution_identity(p_user uuid)
returns table (email text, names text[])
language sql
stable
security definer
set search_path = ''
as $$
  select u.email::text,
         array_remove(array[u.raw_user_meta_data ->> 'full_name', u.raw_user_meta_data ->> 'name',
                            u.raw_user_meta_data ->> 'preferred_username'], null)
    from auth.users u where u.id = p_user
$$;

drop function trace_private.exhaust_contribution_request(uuid, text);
drop function trace_private.suspend_contribution(uuid);
drop function trace_private.contribution_backoff(integer, integer, integer);
drop function trace_private.marker_set(text[], text[], integer);
drop table trace_private.contribution_queue_events;
drop table trace_private.identity_markers;
drop index trace_private.contribution_requests_due;
update trace_private.contribution_requests set locked_until = null where failed_at is not null;
alter table trace_private.contribution_requests drop column next_attempt_at, drop column lease_token, drop column last_error,
  drop column failed_at;

revoke all on function trace_contrib.claim_contribution_requests(integer, integer), trace_contrib.contribution_identity(uuid)
  from public, anon, authenticated, service_role;
grant execute on function trace_contrib.claim_contribution_requests(integer, integer), trace_contrib.contribution_identity(uuid)
  to trace_app_runtime;
commit;
