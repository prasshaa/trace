-- Rollback v2.36.0. Restores the previous onboarding default ('shared') and removes account deletion.
-- Does not rewrite preferences already recorded as private.

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

drop function if exists trace_private.delete_my_account();
