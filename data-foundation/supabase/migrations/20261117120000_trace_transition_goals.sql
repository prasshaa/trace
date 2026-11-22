-- v2.30.0: transition goals (product redesign: the career transition roadmap).
--
-- The user's own planning goal for one target role: a desired timeframe (or a specific target date) and, optionally,
-- the time they can give each week, plus any checkpoint dates they set themselves. A PLANNING GOAL only:
--   * never an input to route selection, capability or eligibility: plans never read this table, and changing it never
--     creates a plan run; it only organises the roadmap shown with a saved plan;
--   * never a forecast: Trace stores the user's ambition, not a predicted duration.
-- Same pattern as v2.29.0: private table, no client grant; the owner role reads and sets only its own goal through two
-- functions acting for the request identity, owned by trace_preference_writer under owner-scoped policies.
-- Requires PostgreSQL 16+ and v2.29.0 (trace_preference_writer).

create table trace_private.transition_goals (
  user_id          uuid not null references auth.users (id) on delete cascade,
  target_role_id   text not null references public.trace_role_taxonomy (role_id),
  timeframe        text check (timeframe in ('within_3_months', 'within_6_months', 'within_12_months', 'within_18_24_months', 'flexible')),
  target_date      date,
  weekly_hours     text check (weekly_hours in ('1_3', '4_6', '7_10', 'more_than_10', 'varies')),
  checkpoint_dates jsonb not null default '{}'::jsonb check (jsonb_typeof(checkpoint_dates) = 'object'),
  goal_set_on      date not null default current_date,
  updated_at       timestamptz not null default now(),
  primary key (user_id, target_role_id),
  constraint transition_goals_date_or_timeframe check (target_date is null or timeframe is null or timeframe <> 'flexible')
);
alter table trace_private.transition_goals enable row level security;
revoke all on trace_private.transition_goals from public, anon, authenticated, service_role;

grant select, insert, update, delete on trace_private.transition_goals to trace_preference_writer;
create policy transition_goals_writer_select on trace_private.transition_goals
  for select to trace_preference_writer using (user_id = (select trace_review.request_uid()));
create policy transition_goals_writer_insert on trace_private.transition_goals
  for insert to trace_preference_writer with check (user_id = (select trace_review.request_uid()));
create policy transition_goals_writer_update on trace_private.transition_goals
  for update to trace_preference_writer using (user_id = (select trace_review.request_uid()))
  with check (user_id = (select trace_review.request_uid()));
create policy transition_goals_writer_delete on trace_private.transition_goals
  for delete to trace_preference_writer using (user_id = (select trace_review.request_uid()));

create function trace_plan.my_transition_goal(p_target_role_id text)
returns table (timeframe text, target_date date, weekly_hours text, checkpoint_dates jsonb, goal_set_on date, updated_at timestamptz)
language sql
stable
security definer
set search_path = ''
as $$
  select g.timeframe, g.target_date, g.weekly_hours, g.checkpoint_dates, g.goal_set_on, g.updated_at
    from trace_private.transition_goals g
   where g.user_id = trace_review.request_uid() and g.target_role_id = p_target_role_id
$$;

-- Set (or replace) the caller's goal for one target. All values optional; checkpoint dates are {"1".."4": "YYYY-MM-DD"}.
-- goal_set_on moves only when the timeframe or target date changes (it anchors "within N months").
create function trace_plan.set_transition_goal(p_target_role_id text, p_timeframe text, p_target_date date, p_weekly_hours text,
                                               p_checkpoint_dates jsonb)
returns table (timeframe text, target_date date, weekly_hours text, checkpoint_dates jsonb, goal_set_on date, updated_at timestamptz)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  v_dates jsonb := coalesce(p_checkpoint_dates, '{}'::jsonb);
  v_key text;
begin
  if v_uid is null then
    raise exception 'no request identity' using errcode = '42501';
  end if;
  if jsonb_typeof(v_dates) <> 'object' then
    raise exception 'checkpoint dates must be an object' using errcode = '22023';
  end if;
  for v_key in select jsonb_object_keys(v_dates) loop
    if v_key not in ('1', '2', '3', '4') or jsonb_typeof(v_dates -> v_key) <> 'string'
       or (v_dates ->> v_key) !~ '^\d{4}-\d{2}-\d{2}$' then
      raise exception 'invalid checkpoint date' using errcode = '22023';
    end if;
    perform (v_dates ->> v_key)::date;
  end loop;
  if p_target_date is not null and (p_target_date < current_date or p_target_date > current_date + interval '5 years') then
    raise exception 'the target date must be within the next five years' using errcode = '22023';
  end if;
  begin
    insert into trace_private.transition_goals as g (user_id, target_role_id, timeframe, target_date, weekly_hours, checkpoint_dates)
    values (v_uid, p_target_role_id, p_timeframe, p_target_date, p_weekly_hours, v_dates)
    on conflict (user_id, target_role_id) do update
       set goal_set_on = case when g.timeframe is distinct from excluded.timeframe or g.target_date is distinct from excluded.target_date
                              then current_date else g.goal_set_on end,
           timeframe = excluded.timeframe, target_date = excluded.target_date, weekly_hours = excluded.weekly_hours,
           checkpoint_dates = excluded.checkpoint_dates, updated_at = now();
  exception
    when foreign_key_violation then raise exception 'unknown target role' using errcode = '22023';
    when check_violation then raise exception 'invalid goal' using errcode = '22023';
  end;
  return query select * from trace_plan.my_transition_goal(p_target_role_id);
end;
$$;

revoke all on function trace_plan.my_transition_goal(text), trace_plan.set_transition_goal(text, text, date, text, jsonb)
  from public, anon, authenticated, service_role;
grant execute on function trace_plan.my_transition_goal(text), trace_plan.set_transition_goal(text, text, date, text, jsonb)
  to trace_app_user;
grant trace_preference_writer to postgres with set true, inherit false;
grant create on schema trace_plan to trace_preference_writer;
alter function trace_plan.my_transition_goal(text) owner to trace_preference_writer;
alter function trace_plan.set_transition_goal(text, text, date, text, jsonb) owner to trace_preference_writer;
revoke create on schema trace_plan from trace_preference_writer;
revoke trace_preference_writer from postgres;

do $$
begin
  if pg_catalog.pg_has_role('postgres', 'trace_preference_writer', 'SET') or pg_catalog.pg_has_role('postgres', 'trace_preference_writer', 'USAGE') then
    raise exception 'postgres must not be able to act as trace_preference_writer';
  end if;
  if has_function_privilege('anon', 'trace_plan.set_transition_goal(text, text, date, text, jsonb)', 'execute')
     or has_function_privilege('authenticated', 'trace_plan.set_transition_goal(text, text, date, text, jsonb)', 'execute')
     or has_function_privilege('service_role', 'trace_plan.set_transition_goal(text, text, date, text, jsonb)', 'execute')
     or not has_function_privilege('trace_app_user', 'trace_plan.set_transition_goal(text, text, date, text, jsonb)', 'execute')
     or not has_function_privilege('trace_app_user', 'trace_plan.my_transition_goal(text)', 'execute') then
    raise exception 'the transition-goal functions must be executable by trace_app_user only';
  end if;
end
$$;
