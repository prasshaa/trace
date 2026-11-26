-- Trace data foundation v2.37.0: let the owner role call delete_my_account.
--
-- v2.36.0 let trace_app_user call delete_my_account and took that right away from every other role.
-- Calling it still failed. A role cannot resolve a name in a schema without USAGE on the schema, so the
-- statement failed with insufficient_privilege before the function ran. The API shows that as not allowed.
--
-- USAGE on the schema lets the role look up the function. It does not allow reading or writing any table in
-- trace_private. Every other function in the schema already refuses public, anon, authenticated and service_role,
-- and is not given to trace_app_user, so resolving the schema does not open them.
--
-- The function itself is unchanged: SECURITY DEFINER, search_path fixed to empty, and it deletes auth.users only for
-- trace_review.request_uid(), which is auth.uid() for this request. It cannot name another account.

revoke all on schema trace_private from public, anon, authenticated, service_role;
grant usage on schema trace_private to trace_app_user;

revoke all on function trace_private.delete_my_account() from public, anon, authenticated, service_role;
grant execute on function trace_private.delete_my_account() to trace_app_user;

do $$
declare
  v_callable int;
  v_definer boolean;
  v_path text;
begin
  if not has_schema_privilege('trace_app_user', 'trace_private', 'USAGE')
     or has_schema_privilege('trace_app_user', 'trace_private', 'CREATE')
     or has_schema_privilege('anon', 'trace_private', 'USAGE')
     or has_schema_privilege('authenticated', 'trace_private', 'USAGE')
     or has_schema_privilege('service_role', 'trace_private', 'USAGE')
     or has_schema_privilege('public', 'trace_private', 'USAGE') then
    raise exception 'trace_private usage must be trace_app_user only, and must not include CREATE';
  end if;
  if not has_function_privilege('trace_app_user', 'trace_private.delete_my_account()', 'EXECUTE')
     or has_function_privilege('public', 'trace_private.delete_my_account()', 'EXECUTE')
     or has_function_privilege('anon', 'trace_private.delete_my_account()', 'EXECUTE')
     or has_function_privilege('authenticated', 'trace_private.delete_my_account()', 'EXECUTE')
     or has_function_privilege('service_role', 'trace_private.delete_my_account()', 'EXECUTE') then
    raise exception 'delete_my_account must be executable by trace_app_user only';
  end if;
  select count(*) into v_callable
    from pg_catalog.pg_proc p
    join pg_catalog.pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'trace_private'
     and has_function_privilege('trace_app_user', p.oid, 'EXECUTE');
  if v_callable <> 1 then
    raise exception 'trace_app_user must be able to execute exactly one trace_private function (found %)', v_callable;
  end if;
  if exists (
    select 1 from information_schema.role_table_grants
     where grantee = 'trace_app_user' and table_schema = 'trace_private') then
    raise exception 'trace_app_user must have no table privileges in trace_private';
  end if;
  select p.prosecdef,
         (select c from unnest(coalesce(p.proconfig, array[]::text[])) c where c like 'search_path=%' limit 1)
    into v_definer, v_path
    from pg_catalog.pg_proc p
    join pg_catalog.pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'trace_private' and p.proname = 'delete_my_account';
  if not v_definer or v_path is null or position('search_path=' in v_path) <> 1 then
    raise exception 'delete_my_account must stay security definer with a fixed search_path';
  end if;
  if position('request_uid' in pg_get_functiondef('trace_private.delete_my_account()'::regprocedure)) = 0 then
    raise exception 'delete_my_account must keep deleting only the request identity (auth.uid())';
  end if;
end;
$$;
