-- Trace data foundation v2.9.0: deleting an account erases its contribution completely.
--
-- Before: deleting an auth user set trace_private.contributors.user_id to NULL; the contribution became ineligible
-- (no active consent) but its de-identified reference rows remained. Now the contributor row is deleted with the
-- account, its contributions with it, and each removed contribution deletes its reference profile (children cascade).
-- Participants without an app account (legacy contributors) are unaffected: they are withdrawn through
-- trace_private.withdraw_participant, which keeps the minimal audit record.

alter table trace_private.contributors drop constraint contributors_user_id_fkey;
alter table trace_private.contributors add constraint contributors_user_id_fkey
  foreign key (user_id) references auth.users (id) on delete cascade;
alter table trace_private.contributions drop constraint contributions_contributor_id_fkey;
alter table trace_private.contributions add constraint contributions_contributor_id_fkey
  foreign key (contributor_id) references trace_private.contributors (contributor_id) on delete cascade;
alter table trace_private.import_log drop constraint import_log_contribution_id_fkey;
alter table trace_private.import_log add constraint import_log_contribution_id_fkey
  foreign key (contribution_id) references trace_private.contributions (contribution_id) on delete set null;

create function trace_private.on_contribution_deleted()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if old.profile_id is not null then
    delete from trace_ref.profiles where profile_id = old.profile_id;
  end if;
  return null;
end;
$$;
revoke all on function trace_private.on_contribution_deleted() from public, anon, authenticated, service_role;
create trigger trace_contribution_deleted after delete on trace_private.contributions
  for each row execute function trace_private.on_contribution_deleted();
