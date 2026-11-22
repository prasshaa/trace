-- Compensation for migration 20261113110000_trace_real_reference_contributions (v2.8.0). NOT a migration.
--
--   1. Stop the import pipeline and disable the consented_real cohort (TRACE_ENABLED_COHORTS=synthetic) first.
--   2. Run as postgres: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>   (it carries its own BEGIN/COMMIT)
--   3. supabase migration repair 20261113110000 --status reverted   (add --local for the local stack)
--
-- What it restores: the v2.7.0 eligibility view and consent-integrity trigger; it drops trace_private (contributors,
-- consent attestations, contributions, import log), the withdrawal trigger and functions, trace_ref.activities and the
-- retained-detail columns (institution, qualification title, grades, employer name).
--
-- Data: every contributed real reference profile, its consent attestation and the contributor mapping would be
-- PERMANENTLY DISCARDED. The script refuses while any exist unless the operator acknowledges the loss for this session:
--   set trace.rollback_discard_real_reference = 'I understand';
begin;
lock table trace_ref.profiles in exclusive mode;
do $$
begin
  if (exists (select 1 from trace_private.contributors) or exists (select 1 from trace_ref.profiles
        where dataset_class = 'consented_real_reference' and contributor_user_id is null))
     and coalesce(current_setting('trace.rollback_discard_real_reference', true), '') <> 'I understand' then
    raise exception 'real reference data or contributor records exist; acknowledge the loss first (see header)';
  end if;
end
$$;
delete from trace_ref.profiles where dataset_class = 'consented_real_reference' and contributor_user_id is null;
drop trigger trace_contribution_withdrawal on public.trace_consent_events;

create or replace view trace_ref.v_eligible_profiles with (security_invoker = true) as
select p.*
  from trace_ref.profiles p
  join trace_ref.v_current_consent c
    on c.user_id = p.contributor_user_id and c.purpose = 'deidentified_shared_dataset'::public.trace_consent_purpose and c.is_active
 where p.dataset_class = 'consented_real_reference'::public.trace_dataset_class
   and p.source_consent_event_id is not null
   and p.review_status = any (array['participant_confirmed', 'reviewer_confirmed']::public.trace_review_status[]);

create or replace function trace_util.enforce_profile_consent_integrity()
returns trigger
language plpgsql
security definer
set search_path = ''
as $fn$
declare
  v_becomes_real boolean := tg_op = 'INSERT' or old.dataset_class is distinct from new.dataset_class;
begin
  if new.dataset_class <> 'consented_real_reference' then
    return new;
  end if;
  if v_becomes_real and (new.contributor_user_id is null or new.source_consent_event_id is null) then
    raise exception 'consented_real_reference profile % needs contributor_user_id and source_consent_event_id', new.profile_id
      using errcode = 'check_violation';
  end if;
  if new.contributor_user_id is not null and new.source_consent_event_id is not null then
    if not exists (select 1 from public.trace_consent_events e where e.consent_event_id = new.source_consent_event_id
                     and e.user_id = new.contributor_user_id and e.purpose = 'deidentified_shared_dataset' and e.status = 'granted') then
      raise exception 'profile %: source_consent_event_id must be a granted deidentified_shared_dataset event of contributor %',
        new.profile_id, new.contributor_user_id using errcode = 'check_violation';
    end if;
  elsif tg_op = 'UPDATE'
        and ((new.contributor_user_id is not null and new.contributor_user_id is distinct from old.contributor_user_id)
             or (new.source_consent_event_id is not null and new.source_consent_event_id is distinct from old.source_consent_event_id)) then
    raise exception 'profile %: contributor_user_id and source_consent_event_id must be set together', new.profile_id
      using errcode = 'check_violation';
  end if;
  return new;
end;
$fn$;

drop function trace_ref.eligible_real_profile_ids();
drop function trace_private.withdraw_participant(text, text);
drop function trace_private.on_contribution_consent_event();
drop function trace_private.remove_contributions(uuid[]);
drop table trace_private.import_log, trace_private.contributions, trace_private.consent_attestations, trace_private.contributors;
drop schema trace_private;
drop table trace_ref.activities;
alter table trace_ref.employers drop column employer_name;
alter table trace_ref.education drop constraint education_grade_range, drop column grade_value, drop column grade_scale,
  drop column grade_raw, drop column qualification_title, drop column institution_name;
commit;
