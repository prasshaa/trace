-- v2.37.0: the owner role can call delete_my_account and nothing else in trace_private.
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(5);

select ok(has_schema_privilege('trace_app_user', 'trace_private', 'USAGE')
          and not has_schema_privilege('trace_app_user', 'trace_private', 'CREATE')
          and not has_schema_privilege('anon', 'trace_private', 'USAGE')
          and not has_schema_privilege('authenticated', 'trace_private', 'USAGE')
          and not has_schema_privilege('service_role', 'trace_private', 'USAGE'),
          'only the owner role can resolve names in trace_private, and it cannot create there');

select ok(has_function_privilege('trace_app_user', 'trace_private.delete_my_account()', 'EXECUTE')
          and not has_function_privilege('anon', 'trace_private.delete_my_account()', 'EXECUTE')
          and not has_function_privilege('authenticated', 'trace_private.delete_my_account()', 'EXECUTE')
          and not has_function_privilege('service_role', 'trace_private.delete_my_account()', 'EXECUTE'),
          'only the owner role can execute delete_my_account');

select is((select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'trace_private' and has_function_privilege('trace_app_user', p.oid, 'EXECUTE')),
          1, 'no other trace_private function is executable by the owner role');

select is((select count(*)::int from information_schema.role_table_grants
            where grantee = 'trace_app_user' and table_schema = 'trace_private'),
          0, 'the owner role has no table privileges in trace_private');

select ok((select p.prosecdef and exists (select 1 from unnest(coalesce(p.proconfig, array[]::text[])) c
                                           where c like 'search_path=%')
            and position('request_uid' in pg_get_functiondef(p.oid)) > 0
            from pg_proc p join pg_namespace n on n.oid = p.pronamespace
           where n.nspname = 'trace_private' and p.proname = 'delete_my_account'),
          'the function stays security definer, with a fixed search_path, scoped to auth.uid()');

select * from finish();
rollback;
