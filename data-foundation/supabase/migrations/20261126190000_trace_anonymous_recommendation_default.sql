-- Trace data foundation v2.38.0: new accounts default to anonymous recommendation use.
--
-- v2.36.0 recorded 'private' when someone continued the data-use notice. The product default is now the other
-- option: use career information anonymously to improve recommendations for everyone. An existing preference is
-- still never overwritten.
--
-- This records a preference only. It does not write a consent event, does not enqueue processing, and does not
-- publish a profile. trace_private.contribution_consent_active still requires an explicit grant and a preference
-- that is not private, so a profile is not shown to other users and is not added to the shared pool.
-- Choosing private in Settings still stops that use at once.
--
-- The function stays SECURITY DEFINER with an empty search_path, and it writes only for trace_review.request_uid()
-- (auth.uid() for this request).

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

do $$
declare
  v_def text := pg_get_functiondef('trace_contrib.acknowledge_data_use_notice(text)'::regprocedure);
begin
  if position('security definer' in lower(v_def)) = 0 or position('search_path' in lower(v_def)) = 0 then
    raise exception 'acknowledge_data_use_notice must stay security definer with a fixed search_path';
  end if;
  if position('request_uid' in v_def) = 0 or position('''shared''' in v_def) = 0 then
    raise exception 'the default must be shared, scoped to the request identity';
  end if;
  if position('trace_consent_events' in v_def) > 0 or position('enqueue_contribution' in v_def) > 0 then
    raise exception 'the onboarding default must not grant consent or start shared-pool processing';
  end if;
end;
$$;
