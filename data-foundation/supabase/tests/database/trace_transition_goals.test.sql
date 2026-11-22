-- v2.30.0: transition goals (owner-scoped planning goals; never plan inputs). Rolled back.
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(15);

select ok(not has_table_privilege('anon', 'trace_private.transition_goals', 'SELECT')
          and not has_table_privilege('authenticated', 'trace_private.transition_goals', 'SELECT')
          and not has_table_privilege('trace_app_user', 'trace_private.transition_goals', 'SELECT')
          and not has_table_privilege('trace_app_user', 'trace_private.transition_goals', 'INSERT')
          and not has_table_privilege('service_role', 'trace_private.transition_goals', 'UPDATE'),
          'no client role reads or writes the table directly');
select ok(has_function_privilege('trace_app_user', 'trace_plan.set_transition_goal(text, text, date, text, jsonb)', 'EXECUTE')
          and has_function_privilege('trace_app_user', 'trace_plan.my_transition_goal(text)', 'EXECUTE')
          and not has_function_privilege('authenticated', 'trace_plan.set_transition_goal(text, text, date, text, jsonb)', 'EXECUTE')
          and not has_function_privilege('service_role', 'trace_plan.my_transition_goal(text)', 'EXECUTE'),
          'only the owner role calls the functions');
select is((select string_agg(distinct pg_get_userbyid(p.proowner), ',') from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'trace_plan' and p.proname in ('my_transition_goal', 'set_transition_goal')),
          'trace_preference_writer', 'owned by the dedicated writer');
select ok(not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                      where n.nspname = 'trace_plan' and p.proname = 'record_transition_plan'
                        and pg_get_functiondef(p.oid) ilike '%transition_goals%'), 'the plan writer never reads goals');

insert into auth.users (id, email) values ('e9000000-0000-4000-8000-000000000001', 'pgtap-goal-a@example.invalid'),
                                          ('e9000000-0000-4000-8000-000000000002', 'pgtap-goal-b@example.invalid');
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "e9000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);

select is((select timeframe || '/' || weekly_hours from trace_plan.set_transition_goal('PM-PM', 'within_12_months', null, '4_6', null)),
          'within_12_months/4_6', 'a goal is saved');
select is((select goal_set_on from trace_plan.my_transition_goal('PM-PM')), current_date, 'the goal is anchored today');
select is((select checkpoint_dates ->> '2' from trace_plan.set_transition_goal('PM-PM', 'within_12_months', null, '4_6',
            jsonb_build_object('2', to_char(current_date + 90, 'YYYY-MM-DD')))), to_char(current_date + 90, 'YYYY-MM-DD'),
          'checkpoint dates are saved');
select is((select timeframe from trace_plan.set_transition_goal('PM-PM', 'flexible', null, null, null)), 'flexible', 'flexible is allowed');
select is((select count(*)::int from trace_plan.my_transition_goal('PM-SPM')), 0, 'goals are per target role');
select throws_ok($$select * from trace_plan.set_transition_goal('PM-PM', 'within_2_weeks', null, null, null)$$, '22023', null, 'an unknown timeframe is refused');
select throws_ok($$select * from trace_plan.set_transition_goal('PM-PM', 'within_12_months', current_date - 1, null, null)$$, '22023', null, 'a past date is refused');
select throws_ok($$select * from trace_plan.set_transition_goal('PM-PM', null, null, null, '{"9": "2027-01-01"}')$$, '22023', null, 'an unknown checkpoint is refused');
select throws_ok($$select * from trace_plan.set_transition_goal('NO-SUCH', 'flexible', null, null, null)$$, '22023', null, 'an unknown target is refused');

select set_config('request.jwt.claims', '{"sub": "e9000000-0000-4000-8000-000000000002", "role": "authenticated"}', true);
select is((select count(*)::int from trace_plan.my_transition_goal('PM-PM')), 0, 'user B never sees user A''s goal');
reset role;
select is((select count(*)::int from public.trace_pathway_runs where owner_user_id = 'e9000000-0000-4000-8000-000000000001'), 0,
          'setting a goal never creates a plan run');
delete from auth.users where id in ('e9000000-0000-4000-8000-000000000001', 'e9000000-0000-4000-8000-000000000002');
select * from finish();
rollback;
