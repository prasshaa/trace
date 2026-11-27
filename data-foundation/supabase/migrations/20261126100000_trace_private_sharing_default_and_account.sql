-- Trace data foundation v2.36.0: sharing defaults to private (opt-in), and the owner can delete their account.
--
-- acknowledge_data_use_notice used to insert mode 'shared' when the caller had no preference. Sharing is opt-in:
-- Continue on the notice records 'private' and never starts shared-pool processing. An existing preference is still
-- left untouched (on conflict do nothing).
--
-- trace_private.delete_my_account() deletes the auth user for the request identity only. Career data, plans, drafts,
-- consent rows and contributions follow the existing foreign keys (on delete cascade) and the contribution-delete
-- trigger. No other account is touched. Executable by trace_app_user only.

create or replace function trace_contrib.acknowledge_data_use_notice(p_notice_version text)
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
  values (v_uid, 'private', 'onboarding_default', p_notice_version)
  on conflict (user_id) do nothing;
  if found then
    insert into trace_private.sharing_preference_events (user_id, mode, source, notice_version)
    values (v_uid, 'private', 'onboarding_default', p_notice_version);
  end if;
  select s.mode into v_mode from trace_private.sharing_preferences s where s.user_id = v_uid;
  return v_mode;
end;
$$;

create function trace_private.delete_my_account()
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
  delete from auth.users where id = v_uid;
end;
$$;

revoke all on function trace_private.delete_my_account() from public, anon, authenticated, service_role;
grant execute on function trace_private.delete_my_account() to trace_app_user;

do $$
begin
  if has_function_privilege('anon', 'trace_private.delete_my_account()', 'execute')
     or has_function_privilege('authenticated', 'trace_private.delete_my_account()', 'execute')
     or has_function_privilege('service_role', 'trace_private.delete_my_account()', 'execute')
     or not has_function_privilege('trace_app_user', 'trace_private.delete_my_account()', 'execute') then
    raise exception 'delete_my_account must be executable by trace_app_user only';
  end if;
end;
$$;
