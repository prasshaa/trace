-- Trace data foundation v2.10.0: contribution safety (identity-marker continuity and bounded retries).
--
-- 1. Identity markers. The resume owner's known direct identifiers (names, emails, phones, links, handles), taken from
--    the resume header, the PDF and the authenticated account during upload, are kept in trace_private.identity_markers
--    so that EVERY later contribution rebuild can detect them again in corrected or edited free text, including for
--    email/password accounts that carry no name. The store is restricted: no client, the runtime login or the owner
--    role can read the table; the runtime writes the caller's own markers through store_identity_markers and reads
--    them in memory through contribution_identity. Markers never enter trace_ref, are never part of a contribution
--    record or report, and are never used for contribution or reference ids (those stay random). They are deleted with
--    the account or the private profile.
-- 2. Bounded retries. A contribution request that fails to process is retried with exponential backoff up to a
--    configurable maximum number of attempts, then reaches a terminal 'failed' state (retries_exhausted) and its
--    existing contribution is suspended. An expired lease (the worker stopped mid-attempt) counts as an attempt; a
--    graceful stop releases unstarted claims without counting them. A terminal request is never claimed again until
--    the user changes their profile or consent (a new request) or an operator runs trace_private.retry_contribution
--    (admin identity only). Queue events (attempt failures, lease expiries, exhaustion, manual retries) are recorded
--    with error class codes only, never messages or content.

-- --------------------------------------------------------------------------- 1. identity markers
create table trace_private.identity_markers (
  owner_user_id uuid primary key references auth.users (id) on delete cascade,
  candidate_profile_id uuid not null references public.trace_candidate_profiles (candidate_profile_id) on delete cascade,
  names text[] not null default '{}',
  emails text[] not null default '{}',
  phones text[] not null default '{}',
  urls text[] not null default '{}',
  handles text[] not null default '{}',
  updated_at timestamptz not null default now(),
  constraint identity_markers_bounded check (cardinality(names) <= 16 and cardinality(emails) <= 16 and cardinality(phones) <= 16
                                              and cardinality(urls) <= 32 and cardinality(handles) <= 16)
);
alter table trace_private.identity_markers enable row level security;
revoke all on trace_private.identity_markers from public, anon, authenticated, service_role;

-- Ordered union of two marker lists: trimmed, non-empty, at most 320 characters each, first occurrence kept, capped.
create function trace_private.marker_set(p_old text[], p_new text[], p_max integer)
returns text[]
language sql
immutable
set search_path = ''
as $$
  select coalesce((array_agg(v order by first_seen))[1:greatest(1, least(p_max, 64))], '{}')
    from (select btrim(x.v) as v, min(x.o) as first_seen
            from unnest(coalesce(p_old, '{}') || coalesce(p_new, '{}')) with ordinality as x(v, o)
           where x.v is not null and char_length(btrim(x.v)) between 1 and 320
           group by btrim(x.v)) y
$$;

-- The caller's own markers (runtime only, with the verified owner as the transaction-local request identity).
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

-- The user's identity for de-identification, in memory only: the account (email and any name in its metadata) together
-- with the stored markers. markers_recorded is false for a profile processed before markers were kept.
drop function trace_contrib.contribution_identity(uuid);
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

-- --------------------------------------------------------------------------- 2. bounded retries
alter table trace_private.contribution_requests
  add column next_attempt_at timestamptz,
  add column lease_token uuid,
  add column last_error text check (last_error ~ '^[A-Za-z0-9_.]{1,60}$'),
  add column failed_at timestamptz;
create index contribution_requests_due on trace_private.contribution_requests (requested_at) where failed_at is null;

create table trace_private.contribution_queue_events (
  event_id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  event text not null check (event in ('attempt_failed', 'lease_expired', 'retries_exhausted', 'manual_retry', 'released')),
  attempts integer not null,
  error_code text check (error_code ~ '^[A-Za-z0-9_.]{1,60}$'),
  actor text check (char_length(btrim(actor)) between 2 and 120),
  created_at timestamptz not null default clock_timestamp()
);
create index contribution_queue_events_user on trace_private.contribution_queue_events (user_id, created_at);
alter table trace_private.contribution_queue_events enable row level security;
revoke all on trace_private.contribution_queue_events from public, anon, authenticated, service_role;

-- Delay before the next attempt: base * 2^(attempts - 1), capped. Inputs are clamped (base 1..3600 s, cap base..86400 s).
create function trace_private.contribution_backoff(p_attempts integer, p_base_seconds integer, p_max_seconds integer)
returns interval
language sql
immutable
set search_path = ''
as $$
  select make_interval(secs => least(greatest(coalesce(p_max_seconds, 3600), b), 86400,
                                     b * power(2, greatest(1, least(coalesce(p_attempts, 1), 30)) - 1)))
    from (select greatest(1, least(coalesce(p_base_seconds, 30), 3600))::double precision as b) x
$$;

-- Suspend the user's admitted contribution, if any: its reference rows are deleted at once.
create function trace_private.suspend_contribution(p_user uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_existing trace_private.contributions%rowtype;
begin
  select c.* into v_existing from trace_private.contributions c join trace_private.contributors k using (contributor_id)
   where k.user_id = p_user and c.status = 'admitted' for update of c;
  if v_existing.contribution_id is not null then
    update trace_private.contributions set status = 'suspended', updated_at = now() where contribution_id = v_existing.contribution_id;
    delete from trace_ref.profiles where profile_id = v_existing.profile_id;
    update trace_private.contributions set profile_id = null, deleted_at = now() where contribution_id = v_existing.contribution_id;
  end if;
end;
$$;

-- Terminal: no more automatic attempts for this request.
create function trace_private.exhaust_contribution_request(p_user uuid, p_error_code text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  update trace_private.contribution_requests
     set failed_at = clock_timestamp(), locked_until = null, lease_token = null, next_attempt_at = null, last_error = p_error_code
   where user_id = p_user;
  insert into trace_private.contribution_queue_events (user_id, event, attempts, error_code)
  select r.user_id, 'retries_exhausted', r.attempts, p_error_code from trace_private.contribution_requests r where r.user_id = p_user;
  perform trace_private.suspend_contribution(p_user);
  perform trace_private.set_contribution_state(p_user, 'failed', 'retries_exhausted');
end;
$$;

-- A new request (profile change, consent grant, reviewer approval) starts a fresh series of attempts.
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
  on conflict (user_id) do update
     set requested_at = excluded.requested_at, attempts = 0, next_attempt_at = null, last_error = null, failed_at = null;
  perform trace_private.set_contribution_state(p_user, 'pending', null);
end;
$$;

drop function trace_contrib.claim_contribution_requests(integer, integer);
create function trace_contrib.claim_contribution_requests(p_limit integer, p_lease_seconds integer, p_max_attempts integer,
                                                          p_backoff_base_seconds integer, p_backoff_max_seconds integer)
returns table (user_id uuid, requested_at timestamptz, lease_token uuid, attempt integer)
language plpgsql
security definer
set search_path = ''
as $$
#variable_conflict use_column
declare
  v_max integer := greatest(1, least(coalesce(p_max_attempts, 5), 20));
  v_expired record;
begin
  -- Expired leases: the worker stopped or crashed during the attempt. That attempt counts.
  for v_expired in
    select r.user_id as uid, r.attempts as n from trace_private.contribution_requests r
     where r.failed_at is null and r.locked_until is not null and r.locked_until < clock_timestamp()
     for update skip locked
  loop
    insert into trace_private.contribution_queue_events (user_id, event, attempts, error_code)
    values (v_expired.uid, 'lease_expired', v_expired.n, 'lease_expired');
    if v_expired.n >= v_max then
      perform trace_private.exhaust_contribution_request(v_expired.uid, 'lease_expired');
    else
      update trace_private.contribution_requests r
         set locked_until = null, lease_token = null, last_error = 'lease_expired',
             next_attempt_at = clock_timestamp() + trace_private.contribution_backoff(v_expired.n, p_backoff_base_seconds, p_backoff_max_seconds)
       where r.user_id = v_expired.uid;
      perform trace_private.set_contribution_state(v_expired.uid, 'pending', 'retry_scheduled');
    end if;
  end loop;

  return query
  with picked as (
    select r.user_id as uid from trace_private.contribution_requests r
     where r.failed_at is null and r.locked_until is null
       and (r.next_attempt_at is null or r.next_attempt_at <= clock_timestamp())
     order by coalesce(r.next_attempt_at, r.requested_at), r.requested_at
     limit greatest(1, least(p_limit, 100))
     for update skip locked
  )
  update trace_private.contribution_requests r
     set locked_until = clock_timestamp() + make_interval(secs => greatest(10, least(p_lease_seconds, 900))),
         attempts = r.attempts + 1, lease_token = gen_random_uuid()
    from picked where r.user_id = picked.uid
  returning r.user_id, r.requested_at, r.lease_token, r.attempts;

  update trace_private.user_contribution_status s set state = 'processing', updated_at = now()
   where s.user_id in (select r.user_id from trace_private.contribution_requests r
                        where r.locked_until > clock_timestamp() and r.failed_at is null)
     and s.state = 'pending';
end;
$$;

-- An attempt raised an error (class code only). Retried with backoff, or terminal at the limit. A stale lease token
-- (the lease expired and the request was claimed again, or the request was replaced) changes nothing.
create function trace_contrib.report_contribution_failure(p_user uuid, p_lease_token uuid, p_error_code text, p_max_attempts integer,
                                                          p_backoff_base_seconds integer, p_backoff_max_seconds integer)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_max integer := greatest(1, least(coalesce(p_max_attempts, 5), 20));
  v_code text := case when p_error_code ~ '^[A-Za-z0-9_.]{1,60}$' then p_error_code else 'error' end;
  v_attempts integer;
begin
  select r.attempts into v_attempts from trace_private.contribution_requests r
   where r.user_id = p_user and r.lease_token = p_lease_token and r.failed_at is null
   for update;
  if not found then
    return 'stale';
  end if;
  insert into trace_private.contribution_queue_events (user_id, event, attempts, error_code)
  values (p_user, 'attempt_failed', v_attempts, v_code);
  if v_attempts >= v_max then
    perform trace_private.exhaust_contribution_request(p_user, v_code);
    return 'failed';
  end if;
  update trace_private.contribution_requests
     set locked_until = null, lease_token = null, last_error = v_code,
         next_attempt_at = clock_timestamp() + trace_private.contribution_backoff(v_attempts, p_backoff_base_seconds, p_backoff_max_seconds)
   where user_id = p_user;
  perform trace_private.set_contribution_state(p_user, 'pending', 'retry_scheduled');
  return 'retry_scheduled';
end;
$$;

-- Graceful stop: claims that were never started go back to the queue without counting as attempts.
create function trace_contrib.release_contribution_requests(p_lease_tokens uuid[])
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_users uuid[];
begin
  with released as (
    update trace_private.contribution_requests r
       set locked_until = null, lease_token = null, attempts = greatest(r.attempts - 1, 0)
     where r.lease_token = any(coalesce(p_lease_tokens, '{}')) and r.failed_at is null
    returning r.user_id
  )
  select coalesce(array_agg(released.user_id), '{}') into v_users from released;
  insert into trace_private.contribution_queue_events (user_id, event, attempts)
  select r.user_id, 'released', r.attempts from trace_private.contribution_requests r where r.user_id = any(v_users);
  update trace_private.user_contribution_status s set state = 'pending', updated_at = now()
   where s.user_id = any(v_users) and s.state = 'processing';
  return cardinality(v_users);
end;
$$;

-- Outcome recording, unchanged except that it uses the shared suspension and resets the attempt series of a request
-- that was re-requested meanwhile.
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
    perform trace_private.suspend_contribution(p_user);
  end if;

  perform trace_private.set_contribution_state(p_user, v_state, v_reason);
  delete from trace_private.contribution_requests r where r.user_id = p_user and r.requested_at = p_requested_at;
  update trace_private.contribution_requests r
     set locked_until = null, lease_token = null, attempts = 0, next_attempt_at = null, last_error = null
   where r.user_id = p_user and r.failed_at is null;
  return v_state;
end;
$$;

-- Explicit retry of a terminally failed request. Admin identity only (no grant to any other role); a named operator is
-- recorded. Refused while the user's contribution consent is not active.
create function trace_private.retry_contribution(p_user uuid, p_operator text)
returns text
language plpgsql
set search_path = ''
as $$
begin
  if p_operator is null or char_length(btrim(p_operator)) not between 2 and 120 then
    raise exception using errcode = 'invalid_parameter_value', message = 'a named operator is required';
  end if;
  if not exists (select 1 from trace_private.contribution_requests r where r.user_id = p_user and r.failed_at is not null) then
    return 'nothing_to_retry';
  end if;
  if not trace_private.contribution_consent_active(p_user) then
    return 'consent_not_active';
  end if;
  update trace_private.contribution_requests
     set failed_at = null, attempts = 0, next_attempt_at = null, last_error = null, locked_until = null, lease_token = null,
         requested_at = clock_timestamp()
   where user_id = p_user;
  insert into trace_private.contribution_queue_events (user_id, event, attempts, actor) values (p_user, 'manual_retry', 0, btrim(p_operator));
  perform trace_private.set_contribution_state(p_user, 'pending', 'manual_retry');
  return 'queued';
end;
$$;

revoke all on function trace_private.marker_set(text[], text[], integer), trace_private.contribution_backoff(integer, integer, integer),
  trace_private.suspend_contribution(uuid), trace_private.exhaust_contribution_request(uuid, text),
  trace_private.retry_contribution(uuid, text),
  trace_contrib.store_identity_markers(text[], text[], text[], text[], text[]), trace_contrib.contribution_identity(uuid),
  trace_contrib.claim_contribution_requests(integer, integer, integer, integer, integer),
  trace_contrib.report_contribution_failure(uuid, uuid, text, integer, integer, integer),
  trace_contrib.release_contribution_requests(uuid[])
  from public, anon, authenticated, service_role;
grant execute on function trace_contrib.store_identity_markers(text[], text[], text[], text[], text[]), trace_contrib.contribution_identity(uuid),
  trace_contrib.claim_contribution_requests(integer, integer, integer, integer, integer),
  trace_contrib.report_contribution_failure(uuid, uuid, text, integer, integer, integer),
  trace_contrib.release_contribution_requests(uuid[])
  to trace_app_runtime;
