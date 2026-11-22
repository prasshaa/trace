-- v2.32.0: move preferences (owner-scoped; never employer evidence or plan runs) and the added role aliases. Rolled back.
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(16);

-- access
select ok(not has_table_privilege('anon', 'trace_private.target_move_preferences', 'SELECT')
          and not has_table_privilege('authenticated', 'trace_private.target_move_preferences', 'SELECT')
          and not has_table_privilege('trace_app_user', 'trace_private.target_move_preferences', 'SELECT')
          and not has_table_privilege('trace_app_user', 'trace_private.target_move_preferences', 'INSERT')
          and not has_table_privilege('service_role', 'trace_private.target_move_preferences', 'UPDATE'),
          'no client role reads or writes the table directly');
select ok(has_function_privilege('trace_app_user', 'trace_plan.set_move_preference(text, text, text[])', 'EXECUTE')
          and has_function_privilege('trace_app_user', 'trace_plan.my_move_preference(text)', 'EXECUTE')
          and not has_function_privilege('authenticated', 'trace_plan.set_move_preference(text, text, text[])', 'EXECUTE')
          and not has_function_privilege('service_role', 'trace_plan.my_move_preference(text)', 'EXECUTE'),
          'only the owner role calls the functions');
select is((select string_agg(distinct pg_get_userbyid(p.proowner), ',') from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'trace_plan' and p.proname in ('my_move_preference', 'set_move_preference')),
          'trace_preference_writer', 'owned by the dedicated writer');
select ok(not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                      where n.nspname = 'trace_plan' and p.proname = 'record_transition_plan'
                        and pg_get_functiondef(p.oid) ilike '%target_move_preferences%'), 'the plan writer never reads move preferences');

-- aliases: exact, not context-required, one role each
select is((select count(*)::int from public.trace_role_aliases where alias_id between 'ALS-0107' and 'ALS-0116' and not context_required), 10,
          'ten added aliases, none context-required');
select is((select role_id from public.trace_role_aliases where alias_normalized = 'software qa engineer'), 'ROLE-QA', 'Software QA Engineer is a QA role');
select is((select role_id from public.trace_role_aliases where alias_normalized = 'founder & ceo'), 'ROLE-ENT', 'Founder & CEO is a founder role');
select is((select count(*)::int from (select alias_normalized from public.trace_role_aliases group by alias_normalized having count(*) > 1
                                       and bool_or(not context_required)) x), 0, 'no alias maps to two roles without a decision');

insert into auth.users (id, email) values ('e9200000-0000-4000-8000-000000000001', 'pgtap-move-a@example.invalid'),
                                          ('e9200000-0000-4000-8000-000000000002', 'pgtap-move-b@example.invalid');
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "e9200000-0000-4000-8000-000000000001", "role": "authenticated"}', true);

select is((select scope || ':' || array_to_string(company_types, ',') from trace_plan.set_move_preference('PM-PM', 'company_types',
            array['b2b_saas', 'ai_native', 'ai_native'])), 'company_types:ai_native,b2b_saas', 'kinds of company are saved, sorted and de-duplicated');
select is((select scope || ':' || cardinality(company_types) from trace_plan.set_move_preference('PM-PM', 'open', array['ai_native'])),
          'open:0', 'open to options keeps no kinds of company');
select is((select scope from trace_plan.set_move_preference('PM-PM', 'specific_companies', null)), 'specific_companies',
          'specific companies (named in the existing companies table)');
select is((select count(*)::int from trace_plan.my_move_preference('PM-SPM')), 0, 'preferences are per target role');
select throws_ok($$select * from trace_plan.set_move_preference('PM-PM', 'company_types', array['unicorns'])$$, '22023', null,
                 'an unknown kind of company is refused');
select throws_ok($$select * from trace_plan.set_move_preference('PM-PM', 'company_types', array['ai_native', 'b2b_saas', 'scale_up', 'large_tech'])$$,
                 '22023', null, 'at most three kinds');
select throws_ok($$select * from trace_plan.set_move_preference('PM-PM', 'company_types', array[]::text[])$$, '22023', null,
                 'kinds of company need at least one kind');

select set_config('request.jwt.claims', '{"sub": "e9200000-0000-4000-8000-000000000002", "role": "authenticated"}', true);
select is((select count(*)::int from trace_plan.my_move_preference('PM-PM')), 0, 'another user sees nothing');
reset role;

select * from finish();
rollback;
