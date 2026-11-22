-- Trace data foundation v2.8.0: consented real reference data, protected contributor mapping, withdrawal.
--
-- Three data categories stay separate:
--   synthetic reference   trace_ref.profiles, dataset_class 'synthetic_test_fixture' (unchanged)
--   consented real        trace_ref.profiles, dataset_class 'consented_real_reference', admitted only through a
--                         registered contribution (this migration) or the original contributor_user_id path
--   private user profiles public.trace_candidate_* (never read by reference queries)
--
-- trace_private (no grants to any client, the runtime or the reference reader) holds who contributed what:
--   contributors          random contributor_id; an app user's id OR an opaque participant reference supplied by the
--                         product owner (never a name, email or phone: enforced by a CHECK)
--   consent_attestations  consent scope for participants who are not app users: basis (an owner attestation is not a
--                         signed form), purposes, evidence reference, original consent date and notice version ONLY
--                         when evidenced (nullable, never invented), recorded and verification timestamps, withdrawal
--   contributions         one de-identified reference profile per contribution: status, de-identification status and
--                         report (counts and checklist only, no values), review, admission, withdrawal and deletion
--   import_log            every import attempt with reason codes only (quarantine/exclusion audit)
-- The analytical profile carries no contributor identity: the mapping exists only in trace_private.
--
-- Eligibility (trace_ref.v_eligible_profiles, used by data mode 'empirical') = the original contributor path OR an
-- admitted contribution that passed de-identification and has a current consent (a verified, unwithdrawn attestation
-- covering all four purposes, or the app user's active deidentified_shared_dataset consent). Computed by one pinned,
-- postgres-owned definer function that returns profile ids only.
--
-- Withdrawal: an app user's withdrawn or denied deidentified_shared_dataset event removes their contributed reference
-- profiles in the same transaction; trace_private.withdraw_participant does the same for a participant reference.
-- Consent evidence and the contribution row (minus its profile link) remain for audit.
--
-- Retained career detail on reference rows (nullable, additive): institution names, qualification titles and grades
-- on education; employer names on employers; certifications, projects and similar on trace_ref.activities.

create schema trace_private;
revoke all on schema trace_private from public, anon, authenticated, service_role;

create table trace_private.contributors (
  contributor_id uuid primary key default gen_random_uuid(),
  source_kind text not null check (source_kind in ('legacy_participant', 'app_user')),
  user_id uuid unique references auth.users (id) on delete set null,
  participant_reference text unique
    check (participant_reference ~ '^[A-Za-z0-9._-]{3,64}$' and participant_reference !~ '@'),
  created_at timestamptz not null default now(),
  check ((source_kind = 'app_user') = (participant_reference is null)),
  check (source_kind = 'app_user' or user_id is null)
);

create table trace_private.consent_attestations (
  attestation_id uuid primary key default gen_random_uuid(),
  contributor_id uuid not null references trace_private.contributors (contributor_id) on delete restrict,
  basis text not null check (basis in ('product_owner_attestation', 'signed_consent_form', 'recorded_verbal_consent')),
  purposes text[] not null check (cardinality(purposes) > 0 and purposes <@ array['deidentified_career_analysis',
                                  'aggregate_research', 'benchmarking', 'recommendations']::text[]),
  evidence_reference text not null check (char_length(btrim(evidence_reference)) between 3 and 200),
  original_consent_date date,                    -- only when evidenced; never estimated
  original_notice_version text,                  -- only when evidenced
  attested_by text not null check (char_length(btrim(attested_by)) between 2 and 120),
  recorded_at timestamptz not null default now(),
  verification_status text not null default 'pending' check (verification_status in ('pending', 'verified', 'rejected')),
  verified_by text,
  verified_at timestamptz,
  withdrawn_at timestamptz,
  withdrawal_reference text,
  check ((verification_status = 'verified') = (verified_by is not null and verified_at is not null)),
  check ((withdrawn_at is null) = (withdrawal_reference is null))
);

create table trace_private.contributions (
  contribution_id uuid primary key default gen_random_uuid(),
  contributor_id uuid not null references trace_private.contributors (contributor_id) on delete restrict,
  profile_id text unique,
  status text not null check (status in ('admitted', 'withdrawn', 'rejected')),
  deidentification_status text not null check (deidentification_status in ('passed', 'needs_review', 'failed')),
  deidentification_report jsonb not null default '{}' check (jsonb_typeof(deidentification_report) = 'object'),
  pipeline_version text not null,
  reviewed_by text,
  reviewed_at timestamptz,
  admitted_at timestamptz,
  withdrawn_at timestamptz,
  deleted_at timestamptz,
  created_at timestamptz not null default now(),
  check (status <> 'admitted' or (deidentification_status = 'passed' and admitted_at is not null and profile_id is not null)),
  check ((status = 'withdrawn') = (withdrawn_at is not null))
);
alter table trace_private.contributions add constraint contributions_profile_fk
  foreign key (profile_id) references trace_ref.profiles (profile_id) on delete set null deferrable initially deferred;

create table trace_private.import_log (
  import_id uuid primary key default gen_random_uuid(),
  contributor_id uuid references trace_private.contributors (contributor_id) on delete set null,
  contribution_id uuid references trace_private.contributions (contribution_id) on delete set null,
  outcome text not null check (outcome in ('admitted', 'quarantined', 'excluded')),
  reason_codes text[] not null default '{}',
  pipeline_version text not null,
  created_at timestamptz not null default now()
);

alter table trace_private.contributors enable row level security;
alter table trace_private.consent_attestations enable row level security;
alter table trace_private.contributions enable row level security;
alter table trace_private.import_log enable row level security;
revoke all on all tables in schema trace_private from public, anon, authenticated, service_role;

-- --------------------------------------------------------------------------- retained career detail (additive)
alter table trace_ref.education
  add column institution_name text,
  add column qualification_title text,
  add column grade_raw text,
  add column grade_scale text check (grade_scale in ('percentage', 'cgpa_10', 'gpa_4', 'class_division', 'other')),
  add column grade_value numeric(6, 2),
  add constraint education_grade_range check (
    grade_value is null
    or (grade_scale = 'percentage' and grade_value between 0 and 100)
    or (grade_scale = 'cgpa_10' and grade_value between 0 and 10)
    or (grade_scale = 'gpa_4' and grade_value between 0 and 4)
    or (grade_scale in ('class_division', 'other')));
alter table trace_ref.employers add column employer_name text;

create table trace_ref.activities (
  activity_id text primary key,
  profile_id text not null references trace_ref.profiles (profile_id) on delete cascade,
  activity_type text not null check (activity_type in ('certification', 'project', 'award', 'publication', 'volunteering', 'other')),
  title text not null check (char_length(btrim(title)) > 0),
  organisation text,
  description_redacted text,
  year integer check (year between 1950 and 2100),
  created_at timestamptz not null default now(),
  check (activity_id like profile_id || '-ACT-%')
);
alter table trace_ref.activities enable row level security;
revoke all on trace_ref.activities from public, anon, authenticated;
grant select on trace_ref.activities to trace_reference_reader;
create policy "reference reader read" on trace_ref.activities for select to trace_reference_reader using (true);

-- --------------------------------------------------------------------------- eligibility
create function trace_ref.eligible_real_profile_ids()
returns setof text
language sql
stable
security definer
set search_path = ''
as $$
  select p.profile_id
    from trace_ref.profiles p
    join trace_ref.v_current_consent c
      on c.user_id = p.contributor_user_id and c.purpose = 'deidentified_shared_dataset' and c.is_active
   where p.dataset_class = 'consented_real_reference' and p.source_consent_event_id is not null
  union
  select c.profile_id
    from trace_private.contributions c
    join trace_private.contributors k on k.contributor_id = c.contributor_id
   where c.profile_id is not null
     and c.status = 'admitted'
     and c.deidentification_status = 'passed'
     and ((k.source_kind = 'legacy_participant'
           and exists (select 1 from trace_private.consent_attestations a
                        where a.contributor_id = k.contributor_id and a.verification_status = 'verified'
                          and a.withdrawn_at is null
                          and a.purposes @> array['deidentified_career_analysis', 'aggregate_research', 'benchmarking',
                                                  'recommendations']::text[]))
       or (k.source_kind = 'app_user'
           and exists (select 1 from trace_ref.v_current_consent v
                        where v.user_id = k.user_id and v.purpose = 'deidentified_shared_dataset' and v.is_active)))
$$;
revoke all on function trace_ref.eligible_real_profile_ids() from public, anon, authenticated, service_role;
grant execute on function trace_ref.eligible_real_profile_ids() to trace_reference_reader;

create or replace view trace_ref.v_eligible_profiles with (security_invoker = true) as
select p.*
  from trace_ref.profiles p
 where p.dataset_class = 'consented_real_reference'
   and p.review_status = any (array['participant_confirmed', 'reviewer_confirmed']::public.trace_review_status[])
   and p.profile_id in (select trace_ref.eligible_real_profile_ids());

-- A real reference profile needs its consent link: either the original contributor pair on the row, or (new) a
-- registered contribution, in which case the row carries no contributor identity at all.
create or replace function trace_util.enforce_profile_consent_integrity()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_becomes_real boolean := tg_op = 'INSERT' or old.dataset_class is distinct from new.dataset_class;
begin
  if new.dataset_class <> 'consented_real_reference' then
    return new;
  end if;
  if v_becomes_real then
    if new.contributor_user_id is null and new.source_consent_event_id is null then
      if not exists (select 1 from trace_private.contributions c where c.profile_id = new.profile_id) then
        raise exception 'consented_real_reference profile % needs a registered contribution or a contributor consent link',
          new.profile_id using errcode = 'check_violation';
      end if;
      return new;
    end if;
    if new.contributor_user_id is null or new.source_consent_event_id is null then
      raise exception 'consented_real_reference profile % needs contributor_user_id and source_consent_event_id', new.profile_id
        using errcode = 'check_violation';
    end if;
  end if;
  if new.contributor_user_id is not null and new.source_consent_event_id is not null then
    if not exists (
      select 1 from public.trace_consent_events e
      where e.consent_event_id = new.source_consent_event_id
        and e.user_id = new.contributor_user_id
        and e.purpose = 'deidentified_shared_dataset'
        and e.status = 'granted'
    ) then
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
$$;

-- --------------------------------------------------------------------------- withdrawal
create function trace_private.remove_contributions(p_contribution_ids uuid[])
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_profiles text[];
begin
  update trace_private.contributions
     set status = 'withdrawn', withdrawn_at = coalesce(withdrawn_at, now())
   where contribution_id = any (p_contribution_ids) and status <> 'withdrawn';
  select coalesce(array_agg(profile_id), '{}') into v_profiles
    from trace_private.contributions where contribution_id = any (p_contribution_ids) and profile_id is not null;
  delete from trace_ref.profiles where profile_id = any (v_profiles);          -- children cascade
  update trace_private.contributions set profile_id = null, deleted_at = now()
   where contribution_id = any (p_contribution_ids) and deleted_at is null;
  return cardinality(v_profiles);
end;
$$;

create function trace_private.on_contribution_consent_event()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.purpose = 'deidentified_shared_dataset' and new.status <> 'granted' then
    perform trace_private.remove_contributions(array(
      select c.contribution_id from trace_private.contributions c
        join trace_private.contributors k on k.contributor_id = c.contributor_id
       where k.user_id = new.user_id and c.status <> 'withdrawn'));
  end if;
  return null;
end;
$$;
create trigger trace_contribution_withdrawal after insert on public.trace_consent_events
  for each row execute function trace_private.on_contribution_consent_event();

create function trace_private.withdraw_participant(p_participant_reference text, p_withdrawal_reference text)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_contributor uuid;
begin
  select contributor_id into v_contributor from trace_private.contributors
   where participant_reference = p_participant_reference and source_kind = 'legacy_participant';
  if v_contributor is null then
    raise exception using errcode = 'no_data_found', message = 'unknown participant reference';
  end if;
  update trace_private.consent_attestations
     set withdrawn_at = now(), withdrawal_reference = p_withdrawal_reference
   where contributor_id = v_contributor and withdrawn_at is null;
  return trace_private.remove_contributions(array(
    select contribution_id from trace_private.contributions where contributor_id = v_contributor and status <> 'withdrawn'));
end;
$$;

revoke all on function trace_private.remove_contributions(uuid[]), trace_private.on_contribution_consent_event(),
  trace_private.withdraw_participant(text, text) from public, anon, authenticated, service_role;
