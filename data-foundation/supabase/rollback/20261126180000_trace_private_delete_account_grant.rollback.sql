-- Rollback v2.37.0. Removes the schema usage that lets trace_app_user call delete_my_account.
-- The function from v2.36.0 stays. Without schema usage the owner role cannot resolve it again.

revoke all on function trace_private.delete_my_account() from public, anon, authenticated, service_role;
revoke usage on schema trace_private from trace_app_user;
