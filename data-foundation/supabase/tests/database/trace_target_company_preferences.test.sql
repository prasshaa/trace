-- v2.29.0: target-company preferences (owner-scoped aspirations; never plan inputs). Rolled back.
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(18);

-- ------------------------------------------------------------------ access
select ok(not has_table_privilege('anon', 'trace_private.target_company_preferences', 'SELECT')
          and not has_table_privilege('authenticated', 'trace_private.target_company_preferences', 'SELECT')
          and not has_table_privilege('trace_app_user', 'trace_private.target_company_preferences', 'SELECT')
          and not has_table_privilege('trace_app_user', 'trace_private.target_company_preferences', 'INSERT')
          and not has_table_privilege('trace_app_runtime', 'trace_private.target_company_preferences', 'SELECT')
          and not has_table_privilege('service_role', 'trace_private.target_company_preferences', 'INSERT'),
          'no client role reads or writes the table directly');
select ok(has_function_privilege('trace_app_user', 'trace_plan.set_target_companies(text, text[])', 'EXECUTE')
          and has_function_privilege('trace_app_user', 'trace_plan.my_target_companies(text)', 'EXECUTE')
          and not has_function_privilege('authenticated', 'trace_plan.set_target_companies(text, text[])', 'EXECUTE')
          and not has_function_privilege('anon', 'trace_plan.my_target_companies(text)', 'EXECUTE')
          and not has_function_privilege('trace_app_runtime', 'trace_plan.set_target_companies(text, text[])', 'EXECUTE')
          and not has_function_privilege('service_role', 'trace_plan.set_target_companies(text, text[])', 'EXECUTE'),
          'only the owner role calls the functions');
select ok((select relrowsecurity from pg_class where oid = 'trace_private.target_company_preferences'::regclass), 'RLS is enabled');
select is((select string_agg(distinct pg_get_userbyid(p.proowner), ',') from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'trace_plan' and p.proname in ('my_target_companies', 'set_target_companies')),
          'trace_preference_writer', 'both functions are owned by the dedicated writer, not postgres');
select ok(not pg_has_role('postgres', 'trace_preference_writer', 'SET') and not pg_has_role('trace_app_user', 'trace_preference_writer', 'SET')
          and (select not rolcanlogin and not rolbypassrls from pg_roles where rolname = 'trace_preference_writer'),
          'the writer cannot log in, bypass RLS or be assumed');

-- ------------------------------------------------------------------ fixtures
insert into auth.users (id, email) values ('c8000000-0000-4000-8000-000000000001', 'pgtap-tcp-a@example.invalid'),
                                          ('c8000000-0000-4000-8000-000000000002', 'pgtap-tcp-b@example.invalid');
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "c8000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);

-- ------------------------------------------------------------------ add, normalise, de-duplicate, order
select is((select string_agg(company_name || '#' || position, ',' order by position)
             from trace_plan.set_target_companies('PM-PM', array['  Razorpay ', 'My   Dream  Startup', 'razorpay', 'Sarvam'])),
          'Razorpay#1,My Dream Startup#2,Sarvam#3', 'names are trimmed, inner spaces collapsed, case-insensitive duplicates dropped');
select is((select string_agg(company_key, ',' order by position) from trace_plan.my_target_companies('PM-PM')),
          'razorpay,my dream startup,sarvam', 'keys are normalised');
select is((select count(*)::int from trace_plan.my_target_companies('PM-SPM')), 0, 'lists are per target role');
select lives_ok($$select * from trace_plan.set_target_companies('PM-SPM', array['Adobe'])$$, 'a second target has its own list');

-- ------------------------------------------------------------------ remove and reorder
select is((select string_agg(company_name, ',' order by position) from trace_plan.set_target_companies('PM-PM', array['Sarvam', 'Razorpay'])),
          'Sarvam,Razorpay', 'remove and reorder by replacing the list');
select is((select string_agg(company_name, ',') from trace_plan.my_target_companies('PM-SPM')), 'Adobe', 'the other target is untouched');
select is((select count(*)::int from trace_plan.set_target_companies('PM-PM', array[]::text[])), 0, 'an empty list clears it');

-- ------------------------------------------------------------------ validation
select throws_ok($$select * from trace_plan.set_target_companies('NO-SUCH-ROLE', array['Razorpay'])$$, '22023', null, 'an unknown target is refused');
select throws_ok($$select * from trace_plan.set_target_companies('PM-PM', array[repeat('x', 101)])$$, '22023', null, 'an over-long name is refused');
select throws_ok($$select * from trace_plan.set_target_companies('PM-PM', array['   '])$$, '22023', null, 'a blank name is refused');
select throws_ok($$select * from trace_plan.set_target_companies('PM-PM', array_fill('Co'::text, array[21]))$$, '22023', null,
                 'more than 20 is refused');

-- ------------------------------------------------------------------ isolation
select lives_ok($$select * from trace_plan.set_target_companies('PM-PM', array['Paytm'])$$, 'user A saves a list');
select set_config('request.jwt.claims', '{"sub": "c8000000-0000-4000-8000-000000000002", "role": "authenticated"}', true);
select is((select count(*)::int from trace_plan.my_target_companies('PM-PM')), 0, 'user B never sees user A''s list');

reset role;
delete from auth.users where id in ('c8000000-0000-4000-8000-000000000001', 'c8000000-0000-4000-8000-000000000002');
select * from finish();
rollback;
