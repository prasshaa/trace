-- Compensation for migration 20261117110000_trace_target_company_preferences (v2.29.0). NOT a migration.
-- Deploy a backend without the target-company endpoints first. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261117110000 --status reverted
-- Refuses while any user has saved target companies: users' own preferences are never discarded silently. Nothing else
-- depends on this table (plans never read it).
begin;
do $$
begin
  if exists (select 1 from trace_private.target_company_preferences) then
    raise exception 'target company preferences exist; export or have users clear them before rolling back';
  end if;
end;
$$;
drop function trace_plan.set_target_companies(text, text[]);
drop function trace_plan.my_target_companies(text);
drop table trace_private.target_company_preferences;          -- its policies go with it
revoke execute on function trace_review.request_uid() from trace_preference_writer;
revoke usage on schema trace_private, trace_review, trace_plan from trace_preference_writer;
drop role trace_preference_writer;
commit;
