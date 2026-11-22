-- Compensation for migration 20261113140000_trace_automatic_contributions (v2.9.0). NOT a migration.
--   1. Stop the contribution worker (TRACE_CONTRIBUTION_WORKER=0) and deploy a backend before v2.9.0.
--   2. Run as postgres: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>
--   3. supabase migration repair 20261113140000 --status reverted
-- Data: every automatic (app-user) contribution, its reference rows, the queue, the user-visible states, review
-- approvals and the resume quarantine are DISCARDED. Refuses while any exist unless acknowledged for this session:
--   set trace.rollback_discard_automatic_contributions = 'I understand';
begin;
lock table trace_private.contributions in exclusive mode;
do $$
begin
  if (exists (select 1 from trace_private.contributors where source_kind = 'app_user')
      or exists (select 1 from trace_private.resume_quarantine) or exists (select 1 from trace_private.contribution_requests))
     and coalesce(current_setting('trace.rollback_discard_automatic_contributions', true), '') <> 'I understand' then
    raise exception 'automatic contributions, queued work or quarantined resumes exist; acknowledge the loss first (see header)';
  end if;
end
$$;
delete from trace_ref.profiles where profile_id in (select c.profile_id from trace_private.contributions c
  join trace_private.contributors k using (contributor_id) where k.source_kind = 'app_user' and c.profile_id is not null);
delete from trace_private.contributions where contributor_id in (select contributor_id from trace_private.contributors where source_kind = 'app_user');
drop trigger trace_contribution_enqueue on public.trace_candidate_profiles;
drop trigger trace_contribution_enqueue on public.trace_candidate_employers;
drop trigger trace_contribution_enqueue on public.trace_candidate_employments;
drop trigger trace_contribution_enqueue on public.trace_candidate_education;
drop trigger trace_contribution_enqueue on public.trace_candidate_evidence;
drop trigger trace_contribution_enqueue on public.trace_candidate_skills;
drop trigger trace_contribution_enqueue on public.trace_candidate_career_activities;
drop trigger trace_contribution_enqueue on public.trace_candidate_career_breaks;
create or replace function trace_private.on_contribution_consent_event()
returns trigger
language plpgsql
security definer
set search_path = ''
as $fn$
begin
  if new.purpose = 'deidentified_shared_dataset' and new.status <> 'granted' then
    perform trace_private.remove_contributions(array(
      select c.contribution_id from trace_private.contributions c
        join trace_private.contributors k on k.contributor_id = c.contributor_id
       where k.user_id = new.user_id and c.status <> 'withdrawn'));
  end if;
  return null;
end;
$fn$;
drop schema trace_contrib cascade;
drop function trace_private.write_reference_rows(text, jsonb);
drop function trace_private.on_candidate_change();
drop function trace_private.enqueue_contribution(uuid);
drop function trace_private.set_contribution_state(uuid, text, text);
drop function trace_private.contribution_consent_active(uuid);
drop table trace_private.resume_processing_log, trace_private.resume_quarantine, trace_private.contribution_review_approvals,
  trace_private.user_contribution_status, trace_private.contribution_requests;
drop index trace_private.contributions_one_admitted_per_contributor;
update trace_private.contributions set status = 'rejected' where status = 'suspended';
alter table trace_private.contributions drop column updated_at, drop column consent_event_id, drop column source_record_sha256;
alter table trace_private.contributions drop constraint contributions_status_check;
alter table trace_private.contributions add constraint contributions_status_check check (status in ('admitted', 'withdrawn', 'rejected'));
commit;
