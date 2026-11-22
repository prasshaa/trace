-- Trace data foundation v2.12.0: the sharing preference, kept separate from the processing authority.
--
-- A user's career information has two uses: personal analysis (purpose resume_processing) and the shared career pool
-- (purpose deidentified_shared_dataset). This migration adds the user's SHARING PREFERENCE for the second use:
--   'shared'   use my career information to improve recommendations for everyone (the product default)
--   'private'  use it only for my own recommendations
-- recorded with its source ('onboarding_default': the onboarding data-use notice was acknowledged with Continue;
-- 'settings': the user chose a mode in Profile settings) and an append-only history.
--
-- The preference is NOT a consent decision and never creates one. Processing a profile for the shared pool still needs
-- the existing explicit contribution consent (an active 'granted' event under an approved document). What changes:
--   * 'private' is a hard stop: trace_private.contribution_consent_active() is false for a user whose preference is
--     private, so nothing is queued, processed or marked eligible for them even if a grant exists; and choosing private
--     withdraws the user's contributions at once (same removal as a consent withdrawal: contributed reference profiles
--     are deleted, queued work and reviewer approvals are dropped, the state becomes 'withdrawn').
--   * Acknowledging the onboarding notice only ever creates a preference that does not exist yet: it never overrides a
--     choice, so a profile is never silently shared again after a reset or a repeated onboarding.
--   * Choosing 'shared' again in settings re-queues processing from the start, but only if an explicit grant is active.
-- Users with no preference row (all existing accounts) keep today's behaviour exactly.

create table trace_private.sharing_preferences (
  user_id uuid primary key references auth.users (id) on delete cascade,
  mode text not null check (mode in ('shared', 'private')),
  source text not null check (source in ('onboarding_default', 'settings')),
  notice_version text check (notice_version ~ '^[A-Za-z0-9_.:-]{1,80}$'),
  updated_at timestamptz not null default now()
);

create table trace_private.sharing_preference_events (
  event_id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  mode text not null check (mode in ('shared', 'private')),
  source text not null check (source in ('onboarding_default', 'settings')),
  notice_version text check (notice_version ~ '^[A-Za-z0-9_.:-]{1,80}$'),
  recorded_at timestamptz not null default clock_timestamp()
);
create index sharing_preference_events_user on trace_private.sharing_preference_events (user_id, recorded_at desc);

alter table trace_private.sharing_preferences enable row level security;
alter table trace_private.sharing_preference_events enable row level security;
revoke all on trace_private.sharing_preferences, trace_private.sharing_preference_events from public, anon, authenticated, service_role;

-- History is append-only: no update ever. No client role holds any grant on it; rows go only with the account (cascade).
create function trace_private.sharing_events_append_only()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'sharing preference history is append-only' using errcode = '42501';
end;
$$;
create trigger trace_sharing_events_no_update before update on trace_private.sharing_preference_events
  for each row execute function trace_private.sharing_events_append_only();

-- --------------------------------------------------------------------------- helpers (no client grants)
create function trace_private.sharing_is_private(p_user uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (select 1 from trace_private.sharing_preferences s where s.user_id = p_user and s.mode = 'private')
$$;

-- An explicit grant is still required; a private preference overrides it.
create or replace function trace_private.contribution_consent_active(p_user uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (select 1 from trace_ref.v_current_consent v
                  where v.user_id = p_user and v.purpose = 'deidentified_shared_dataset' and v.is_active)
     and not trace_private.sharing_is_private(p_user)
$$;

-- The same removal a consent withdrawal performs.
create function trace_private.exclude_from_shared_pool(p_user uuid, p_reason text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform trace_private.remove_contributions(array(
    select c.contribution_id from trace_private.contributions c
      join trace_private.contributors k on k.contributor_id = c.contributor_id
     where k.user_id = p_user and c.status <> 'withdrawn'));
  delete from trace_private.contribution_requests where user_id = p_user;
  delete from trace_private.contribution_review_approvals where user_id = p_user;
  perform trace_private.set_contribution_state(p_user, 'withdrawn', p_reason);
end;
$$;

-- --------------------------------------------------------------------------- the caller's own preference (owner role)
create function trace_contrib.my_sharing_preference()
returns table (mode text, source text, notice_version text, updated_at timestamptz)
language sql
stable
security definer
set search_path = ''
as $$
  select s.mode, s.source, s.notice_version, s.updated_at from trace_private.sharing_preferences s
   where s.user_id = trace_review.request_uid()
$$;

-- Continue on the onboarding data-use notice: the default preference ('shared'), only if none exists. Returns the
-- preference in force afterwards. Never records or implies a consent decision.
create function trace_contrib.acknowledge_data_use_notice(p_notice_version text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  v_mode text;
begin
  if v_uid is null then
    raise exception 'no request identity' using errcode = '42501';
  end if;
  if p_notice_version is null or p_notice_version !~ '^[A-Za-z0-9_.:-]{1,80}$' then
    raise exception 'invalid notice version' using errcode = '22023';
  end if;
  insert into trace_private.sharing_preferences (user_id, mode, source, notice_version)
  values (v_uid, 'shared', 'onboarding_default', p_notice_version)
  on conflict (user_id) do nothing;
  if found then
    insert into trace_private.sharing_preference_events (user_id, mode, source, notice_version)
    values (v_uid, 'shared', 'onboarding_default', p_notice_version);
  end if;
  select s.mode into v_mode from trace_private.sharing_preferences s where s.user_id = v_uid;
  return v_mode;
end;
$$;

-- A choice in Profile settings. 'private' excludes the caller from the shared pool at once; 'shared' re-queues
-- processing from the start only when an explicit contribution grant is active (otherwise nothing is processed).
create function trace_contrib.set_sharing_preference(p_mode text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
begin
  if v_uid is null then
    raise exception 'no request identity' using errcode = '42501';
  end if;
  if p_mode is null or p_mode not in ('shared', 'private') then
    raise exception 'invalid sharing mode' using errcode = '22023';
  end if;
  insert into trace_private.sharing_preferences (user_id, mode, source, notice_version, updated_at)
  values (v_uid, p_mode, 'settings', null, now())
  on conflict (user_id) do update set mode = excluded.mode, source = excluded.source, updated_at = excluded.updated_at;
  insert into trace_private.sharing_preference_events (user_id, mode, source) values (v_uid, p_mode, 'settings');
  if p_mode = 'private' then
    perform trace_private.exclude_from_shared_pool(v_uid, 'private');
  else
    perform trace_private.enqueue_contribution(v_uid);
  end if;
end;
$$;

revoke all on function trace_private.sharing_is_private(uuid), trace_private.exclude_from_shared_pool(uuid, text),
  trace_private.sharing_events_append_only(), trace_contrib.my_sharing_preference(),
  trace_contrib.acknowledge_data_use_notice(text), trace_contrib.set_sharing_preference(text)
  from public, anon, authenticated, service_role;
revoke all on function trace_private.contribution_consent_active(uuid) from public, anon, authenticated, service_role;
grant execute on function trace_contrib.my_sharing_preference(), trace_contrib.acknowledge_data_use_notice(text),
  trace_contrib.set_sharing_preference(text) to trace_app_user;
