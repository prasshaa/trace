-- Compensation for migration 20261117120000_trace_transition_goals (v2.30.0). NOT a migration.
-- Deploy a backend without the transition-goal endpoint first. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261117120000 --status reverted
-- Refuses while any user has a saved goal: users' own goals are never discarded silently. Plans never read this table.
-- trace_preference_writer stays (it belongs to v2.29.0).
begin;
do $$
begin
  if exists (select 1 from trace_private.transition_goals) then
    raise exception 'transition goals exist; export or have users clear them before rolling back';
  end if;
end;
$$;
drop function trace_plan.set_transition_goal(text, text, date, text, jsonb);
drop function trace_plan.my_transition_goal(text);
drop table trace_private.transition_goals;          -- its policies go with it
commit;
