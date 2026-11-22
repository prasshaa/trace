-- Compensation for migration 20261113150000_trace_contribution_account_deletion (v2.9.0). NOT a migration.
-- Run as postgres: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>
--   supabase migration repair 20261113150000 --status reverted
-- Effect: deleting an account again sets the contributor's user_id to NULL instead of erasing the contribution, and
-- an account with contributions can no longer be deleted while contributions reference it through RESTRICT.
begin;
drop trigger trace_contribution_deleted on trace_private.contributions;
drop function trace_private.on_contribution_deleted();
alter table trace_private.contributors drop constraint contributors_user_id_fkey;
alter table trace_private.contributors add constraint contributors_user_id_fkey
  foreign key (user_id) references auth.users (id) on delete set null;
alter table trace_private.contributions drop constraint contributions_contributor_id_fkey;
alter table trace_private.contributions add constraint contributions_contributor_id_fkey
  foreign key (contributor_id) references trace_private.contributors (contributor_id) on delete restrict;
commit;
