-- Trace data foundation v2.9.0: automatic, idempotent contribution of a consenting user's de-identified profile.
--
-- Durable queue: any change to a user's candidate records (while their contribution consent is active) and every
-- contribution consent decision upserts ONE row per user in trace_private.contribution_requests (no duplicates; a
-- newer change only moves requested_at). The backend runtime claims rows with a lease (FOR UPDATE SKIP LOCKED, safe
-- across workers and restarts), rebuilds and de-identifies the profile locally, and calls
-- trace_private.record_contribution_outcome, which in one transaction:
--   * re-checks the current consent, that the profile is not a synthetic fixture, and that the record holds no email
--     address or link (it never trusts the caller for these);
--   * eligible: creates the user's single admitted contribution (random contribution id, random CRR- profile id) or,
--     if one exists, updates it in place (same ids) unless the record is unchanged (content hash: idempotent);
--   * otherwise: suspends an existing contribution (its reference rows are deleted at once);
--   * records the user-visible state, and removes the request unless it was re-requested meanwhile.
-- States: not_eligible | pending | processing | eligible | quarantined | withdrawn | failed (with a reason code).
-- Withdrawal (a withdrawn or denied decision) removes the contribution in the same transaction (v2.8.0 trigger,
-- extended to record the state and drop the request). A later grant starts a NEW contribution only after it passes
-- again; a withdrawn one is never reactivated.
-- Ambiguous records go to manual review: the profile-derived contribution is quarantined until a reviewer approves
-- that exact content (trace_private.contribution_review_approvals, bound to the record hash); an ambiguous uploaded
-- resume is held, already de-identified, in trace_private.resume_quarantine for review and never reaches the
-- candidate tables until approved.
-- Admin-only data stays in trace_private; the runtime and the owner role call narrow definer functions in trace_contrib.

alter table trace_private.contributions drop constraint contributions_status_check;
alter table trace_private.contributions add constraint contributions_status_check
  check (status in ('admitted', 'withdrawn', 'rejected', 'suspended'));
alter table trace_private.contributions
  add column source_record_sha256 text check (source_record_sha256 ~ '^[0-9a-f]{64}$'),
  add column consent_event_id uuid,
  add column updated_at timestamptz not null default now();
create unique index contributions_one_admitted_per_contributor on trace_private.contributions (contributor_id)
  where status = 'admitted';

create table trace_private.contribution_requests (
  user_id uuid primary key references auth.users (id) on delete cascade,
  requested_at timestamptz not null default clock_timestamp(),
  attempts integer not null default 0,
  locked_until timestamptz
);

create table trace_private.user_contribution_status (
  user_id uuid primary key references auth.users (id) on delete cascade,
  state text not null check (state in ('not_eligible', 'pending', 'processing', 'eligible', 'quarantined', 'withdrawn', 'failed')),
  reason text check (char_length(reason) <= 60),
  updated_at timestamptz not null default now()
);

create table trace_private.contribution_review_approvals (
  user_id uuid not null references auth.users (id) on delete cascade,
  record_sha256 text not null check (record_sha256 ~ '^[0-9a-f]{64}$'),
  approved_by text not null check (char_length(btrim(approved_by)) between 2 and 120),
  approved_at timestamptz not null default now(),
  primary key (user_id, record_sha256)
);

create table trace_private.resume_quarantine (
  quarantine_id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null references auth.users (id) on delete cascade,
  candidate_profile_id uuid not null,
  content_sha256 text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
  record jsonb not null,                 -- already de-identified; held only until a reviewer decides
  report jsonb not null,                 -- counts and checklist only
  status text not null default 'pending_review' check (status in ('pending_review', 'approved', 'rejected')),
  reviewed_by text,
  reviewed_at timestamptz,
  created_at timestamptz not null default now()
);

create table trace_private.resume_processing_log (
  log_id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null references auth.users (id) on delete cascade,
  outcome text not null check (outcome in ('admitted', 'quarantined', 'failed', 'approved', 'rejected')),
  reason_codes text[] not null default '{}',
  created_at timestamptz not null default clock_timestamp()
);

alter table trace_private.contribution_requests enable row level security;
alter table trace_private.user_contribution_status enable row level security;
alter table trace_private.contribution_review_approvals enable row level security;
alter table trace_private.resume_quarantine enable row level security;
alter table trace_private.resume_processing_log enable row level security;
revoke all on all tables in schema trace_private from public, anon, authenticated, service_role;

-- --------------------------------------------------------------------------- helpers (no client grants)
create function trace_private.contribution_consent_active(p_user uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (select 1 from trace_ref.v_current_consent v
                  where v.user_id = p_user and v.purpose = 'deidentified_shared_dataset' and v.is_active)
$$;

create function trace_private.set_contribution_state(p_user uuid, p_state text, p_reason text)
returns void
language sql
security definer
set search_path = ''
as $$
  insert into trace_private.user_contribution_status (user_id, state, reason, updated_at)
  values (p_user, p_state, p_reason, now())
  on conflict (user_id) do update set state = excluded.state, reason = excluded.reason, updated_at = excluded.updated_at
$$;

create function trace_private.enqueue_contribution(p_user uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if p_user is null or not trace_private.contribution_consent_active(p_user) then
    return;
  end if;
  insert into trace_private.contribution_requests (user_id, requested_at) values (p_user, clock_timestamp())
  on conflict (user_id) do update set requested_at = excluded.requested_at;
  perform trace_private.set_contribution_state(p_user, 'pending', null);
end;
$$;

create function trace_private.on_candidate_change()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform trace_private.enqueue_contribution(coalesce((to_jsonb(new) ->> 'owner_user_id')::uuid, (to_jsonb(old) ->> 'owner_user_id')::uuid));
  return null;
end;
$$;

create trigger trace_contribution_enqueue after insert or update or delete on public.trace_candidate_profiles
  for each row execute function trace_private.on_candidate_change();
create trigger trace_contribution_enqueue after insert or update or delete on public.trace_candidate_employers
  for each row execute function trace_private.on_candidate_change();
create trigger trace_contribution_enqueue after insert or update or delete on public.trace_candidate_employments
  for each row execute function trace_private.on_candidate_change();
create trigger trace_contribution_enqueue after insert or update or delete on public.trace_candidate_education
  for each row execute function trace_private.on_candidate_change();
create trigger trace_contribution_enqueue after insert or update or delete on public.trace_candidate_evidence
  for each row execute function trace_private.on_candidate_change();
create trigger trace_contribution_enqueue after insert or update or delete on public.trace_candidate_skills
  for each row execute function trace_private.on_candidate_change();
create trigger trace_contribution_enqueue after insert or update or delete on public.trace_candidate_career_activities
  for each row execute function trace_private.on_candidate_change();
create trigger trace_contribution_enqueue after insert or update or delete on public.trace_candidate_career_breaks
  for each row execute function trace_private.on_candidate_change();

-- The v2.8.0 consent trigger, extended: a grant queues processing; anything else withdraws everything at once.
create or replace function trace_private.on_contribution_consent_event()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.purpose <> 'deidentified_shared_dataset' then
    return null;
  end if;
  if new.status = 'granted' then
    perform trace_private.enqueue_contribution(new.user_id);
  else
    perform trace_private.remove_contributions(array(
      select c.contribution_id from trace_private.contributions c
        join trace_private.contributors k on k.contributor_id = c.contributor_id
       where k.user_id = new.user_id and c.status <> 'withdrawn'));
    delete from trace_private.contribution_requests where user_id = new.user_id;
    delete from trace_private.contribution_review_approvals where user_id = new.user_id;
    perform trace_private.set_contribution_state(new.user_id, 'withdrawn', new.status::text);
  end if;
  return null;
end;
$$;

-- Reference rows of one contribution from a de-identified canonical record (the loader's mapping, in SQL).
create function trace_private.write_reference_rows(p_profile_id text, p_record jsonb)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_year integer;
  v_month integer;
  v_prec public.trace_date_precision;
begin
  delete from trace_ref.experience_evidence where profile_id = p_profile_id;
  delete from trace_ref.profile_skills where profile_id = p_profile_id;
  delete from trace_ref.employments where profile_id = p_profile_id;
  delete from trace_ref.employers where profile_id = p_profile_id;
  delete from trace_ref.education where profile_id = p_profile_id;
  delete from trace_ref.activities where profile_id = p_profile_id;
  delete from trace_ref.career_breaks where profile_id = p_profile_id;

  insert into trace_ref.employers (profile_id, employer_id, employer_name, industry_raw, industry_sector, company_model,
                                   business_model, company_size_band, attribute_provenance)
  select p_profile_id, p_profile_id || '-ORG-' || lpad(o::text, 2, '0'), e ->> 'employer_name', e ->> 'industry_raw',
         coalesce(e ->> 'industry_sector', 'unknown'), coalesce(e ->> 'company_model', 'unknown'),
         coalesce(e ->> 'business_model', 'unknown'), coalesce(e ->> 'company_size_band', 'unknown'), 'participant_confirmed'
    from jsonb_array_elements(coalesce(p_record -> 'employers', '[]')) with ordinality as x(e, o);

  insert into trace_ref.employments (employment_id, profile_id, sequence_index, employer_id, title_raw_redacted, role_id, seniority_code,
                                     mapping_status, mapping_confidence, employment_type, primary_career_sequence, start_year,
                                     start_month, start_precision, end_year, end_month, end_precision, is_current_as_stated,
                                     extraction_status)
  select p_profile_id || '-EMP-' || lpad((e ->> 'sequence_index'), 2, '0'), p_profile_id, (e ->> 'sequence_index')::integer,
         (select p_profile_id || '-ORG-' || lpad(o::text, 2, '0')
            from jsonb_array_elements(coalesce(p_record -> 'employers', '[]')) with ordinality as y(r, o)
           where r ->> 'employer_id' = e ->> 'employer_id' limit 1),
         e ->> 'title', coalesce(e ->> 'role_id', 'ROLE-UNKNOWN'), e ->> 'seniority_code',
         coalesce(e ->> 'mapping_status', 'unresolved')::public.trace_mapping_status, coalesce((e ->> 'mapping_confidence')::numeric, 0),
         coalesce(e ->> 'employment_type', 'unknown')::public.trace_employment_type, coalesce((e ->> 'primary_career_sequence')::boolean, true),
         nullif(split_part(e ->> 'start', '-', 1), '')::integer,
         case when e ->> 'start_precision' = 'month' then nullif(split_part(e ->> 'start', '-', 2), '')::integer end,
         coalesce(e ->> 'start_precision', 'unknown')::public.trace_date_precision,
         nullif(split_part(e ->> 'end', '-', 1), '')::integer,
         case when e ->> 'end_precision' = 'month' then nullif(split_part(e ->> 'end', '-', 2), '')::integer end,
         coalesce(e ->> 'end_precision', 'unknown')::public.trace_date_precision,
         coalesce(e ->> 'end_precision', 'unknown') = 'ongoing', 'participant_confirmed'
    from jsonb_array_elements(coalesce(p_record -> 'employments', '[]')) as x(e);

  insert into trace_ref.education (education_id, profile_id, qualification_level, qualification_title, field_of_study, institution_name,
                                   study_mode, completion_status, grade_raw, grade_scale, grade_value, start_year, start_month,
                                   start_precision, end_year, end_month, end_precision)
  select p_profile_id || '-EDU-' || lpad(o::text, 2, '0'), p_profile_id, coalesce(d ->> 'qualification_level', 'unknown'),
         d ->> 'qualification_title', d ->> 'field_of_study', d ->> 'institution_name', coalesce(d ->> 'study_mode', 'unknown'),
         coalesce(d ->> 'completion_status', 'unknown'), d ->> 'grade_raw', d ->> 'grade_scale', (d ->> 'grade_value')::numeric,
         nullif(split_part(d ->> 'start', '-', 1), '')::integer,
         case when d ->> 'start_precision' = 'month' then nullif(split_part(d ->> 'start', '-', 2), '')::integer end,
         coalesce(d ->> 'start_precision', 'unknown')::public.trace_date_precision,
         nullif(split_part(d ->> 'end', '-', 1), '')::integer,
         case when d ->> 'end_precision' = 'month' then nullif(split_part(d ->> 'end', '-', 2), '')::integer end,
         coalesce(d ->> 'end_precision', 'unknown')::public.trace_date_precision
    from jsonb_array_elements(coalesce(p_record -> 'education', '[]')) with ordinality as x(d, o);

  insert into trace_ref.experience_evidence (evidence_id, profile_id, employment_id, evidence_type, responsibility_area,
                                             evidence_text_redacted, provenance, evidence_status)
  select p_profile_id || '-EV-' || lpad(o::text, 2, '0'), p_profile_id,
         case when v ->> 'employment_seq' is not null then p_profile_id || '-EMP-' || lpad(v ->> 'employment_seq', 2, '0') end,
         coalesce(v ->> 'evidence_type', 'other'), v ->> 'responsibility_area', v ->> 'text', 'participant_confirmed',
         'participant_confirmed'
    from jsonb_array_elements(coalesce(p_record -> 'evidence', '[]')) with ordinality as x(v, o);

  insert into trace_ref.profile_skills (profile_skill_id, profile_id, skill_id, skill_text_raw, employment_id, evidence_source, verified)
  select p_profile_id || '-SKL-' || lpad(o::text, 2, '0'), p_profile_id, k ->> 'skill_id', k ->> 'text',
         case when k ->> 'employment_seq' is not null then p_profile_id || '-EMP-' || lpad(k ->> 'employment_seq', 2, '0') end,
         'participant_confirmed', false
    from jsonb_array_elements(coalesce(p_record -> 'skills', '[]')) with ordinality as x(k, o);

  insert into trace_ref.activities (activity_id, profile_id, activity_type, title, organisation, description_redacted, year)
  select p_profile_id || '-ACT-' || lpad(o::text, 2, '0'), p_profile_id, coalesce(a ->> 'activity_type', 'other'), a ->> 'title',
         a ->> 'organisation', a ->> 'description', (a ->> 'year')::integer
    from jsonb_array_elements(coalesce(p_record -> 'activities', '[]')) with ordinality as x(a, o);

  insert into trace_ref.career_breaks (break_id, profile_id, break_reason, start_year, start_month, start_precision, end_year,
                                       end_month, end_precision)
  select p_profile_id || '-BRK-' || lpad(o::text, 2, '0'), p_profile_id, coalesce(b ->> 'break_reason', 'unspecified'),
         nullif(split_part(b ->> 'start', '-', 1), '')::integer,
         case when b ->> 'start_precision' = 'month' then nullif(split_part(b ->> 'start', '-', 2), '')::integer end,
         coalesce(b ->> 'start_precision', 'unknown')::public.trace_date_precision,
         nullif(split_part(b ->> 'end', '-', 1), '')::integer,
         case when b ->> 'end_precision' = 'month' then nullif(split_part(b ->> 'end', '-', 2), '')::integer end,
         coalesce(b ->> 'end_precision', 'unknown')::public.trace_date_precision
    from jsonb_array_elements(coalesce(p_record -> 'career_breaks', '[]')) with ordinality as x(b, o);

  v_year := nullif(split_part(p_record ->> 'resume_as_of', '-', 1), '')::integer;
  v_prec := coalesce(p_record ->> 'resume_as_of_precision', 'unknown')::public.trace_date_precision;
  v_month := case when v_prec = 'month' then nullif(split_part(p_record ->> 'resume_as_of', '-', 2), '')::integer end;
  update trace_ref.profiles set resume_as_of_year = v_year, resume_as_of_month = v_month,
         resume_as_of_precision = case when v_year is null then 'unknown' else v_prec end, updated_at = now()
   where profile_id = p_profile_id;
end;
$$;

-- --------------------------------------------------------------------------- runtime and owner entry points
-- trace_contrib holds functions only. trace_private itself stays unreachable for every role but the admin identity.
create schema trace_contrib;
revoke all on schema trace_contrib from public, anon, authenticated, service_role;
create function trace_contrib.claim_contribution_requests(p_limit integer, p_lease_seconds integer)
returns table (user_id uuid, requested_at timestamptz)
language plpgsql
security definer
set search_path = ''
as $$
begin
  return query
  with picked as (
    select r.user_id from trace_private.contribution_requests r
     where r.locked_until is null or r.locked_until < clock_timestamp()
     order by r.requested_at
     limit greatest(1, least(p_limit, 100))
     for update skip locked
  )
  update trace_private.contribution_requests r
     set locked_until = clock_timestamp() + make_interval(secs => greatest(10, least(p_lease_seconds, 900))), attempts = r.attempts + 1
    from picked where r.user_id = picked.user_id
  returning r.user_id, r.requested_at;
  update trace_private.user_contribution_status s set state = 'processing', updated_at = now()
   where s.user_id in (select r.user_id from trace_private.contribution_requests r where r.locked_until > clock_timestamp())
     and s.state = 'pending';
end;
$$;

create function trace_contrib.contribution_context(p_user uuid)
returns table (consent_active boolean, candidate_profile_id uuid, is_fixture_profile boolean, has_admitted_resume boolean,
               approved_record_sha256 text[])
language sql
stable
security definer
set search_path = ''
as $$
  select trace_private.contribution_consent_active(p_user),
         p.candidate_profile_id, coalesce(p.is_test_fixture or p.fixture_profile_id is not null, false),
         exists (select 1 from trace_ingest.deidentified_resume_admissions a
                  where a.owner_user_id = p_user and a.candidate_profile_id = p.candidate_profile_id),
         coalesce((select array_agg(record_sha256) from trace_private.contribution_review_approvals where user_id = p_user), '{}')
    from (select p_user as u) x
    left join public.trace_candidate_profiles p on p.owner_user_id = x.u
$$;

-- The user's own contact identity, for de-identification in memory only (never stored by the caller).
create function trace_contrib.contribution_identity(p_user uuid)
returns table (email text, names text[])
language sql
stable
security definer
set search_path = ''
as $$
  select u.email::text,
         array_remove(array[u.raw_user_meta_data ->> 'full_name', u.raw_user_meta_data ->> 'name',
                            u.raw_user_meta_data ->> 'preferred_username'], null)
    from auth.users u where u.id = p_user
$$;

create function trace_contrib.record_contribution_outcome(p_user uuid, p_requested_at timestamptz, p_state text, p_reason text,
                                                          p_record jsonb, p_record_sha256 text, p_report jsonb,
                                                          p_pipeline_version text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_state text := p_state;
  v_reason text := p_reason;
  v_contributor uuid;
  v_existing trace_private.contributions%rowtype;
  v_profile_id text;
  v_event uuid;
begin
  if p_state not in ('not_eligible', 'eligible', 'quarantined', 'failed') then
    raise exception using errcode = 'invalid_parameter_value', message = 'unknown contribution outcome';
  end if;
  -- Re-checked here, whatever the caller says.
  if v_state = 'eligible' and not trace_private.contribution_consent_active(p_user) then
    v_state := 'not_eligible'; v_reason := 'consent_not_granted';
  end if;
  if v_state = 'eligible' and exists (select 1 from public.trace_candidate_profiles p where p.owner_user_id = p_user
                                        and (p.is_test_fixture or p.fixture_profile_id is not null)) then
    v_state := 'not_eligible'; v_reason := 'synthetic_profile';
  end if;
  if v_state = 'eligible' and (p_record is null or p_record_sha256 is null
      or p_record::text ~* '[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}'
      or p_record::text ~* '(https?://|www\.|linkedin\.com|github\.com)') then
    v_state := 'failed'; v_reason := 'residual_identifier';
  end if;

  select c.* into v_existing from trace_private.contributions c join trace_private.contributors k using (contributor_id)
   where k.user_id = p_user and c.status = 'admitted' for update of c;

  if v_state = 'eligible' then
    select v.consent_event_id into v_event from trace_ref.v_current_consent v
     where v.user_id = p_user and v.purpose = 'deidentified_shared_dataset';
    if v_existing.contribution_id is not null then
      if v_existing.source_record_sha256 is distinct from p_record_sha256 then        -- same person, same ids, new content
        perform trace_private.write_reference_rows(v_existing.profile_id, p_record);
        update trace_private.contributions set source_record_sha256 = p_record_sha256, deidentification_report = coalesce(p_report, '{}'),
               pipeline_version = p_pipeline_version, consent_event_id = v_event, updated_at = now(), reviewed_at = now()
         where contribution_id = v_existing.contribution_id;
      end if;
    else
      insert into trace_private.contributors (source_kind, user_id) values ('app_user', p_user)
      on conflict (user_id) do nothing;
      select contributor_id into v_contributor from trace_private.contributors where user_id = p_user;
      loop
        v_profile_id := 'CRR-' || lpad((floor(random() * 1e12))::bigint::text, 12, '0');
        exit when not exists (select 1 from trace_ref.profiles where profile_id = v_profile_id)
              and not exists (select 1 from trace_private.contributions where profile_id = v_profile_id);
      end loop;
      insert into trace_private.contributions (contributor_id, profile_id, status, deidentification_status, deidentification_report,
                                               pipeline_version, reviewed_by, reviewed_at, admitted_at, source_record_sha256,
                                               consent_event_id)
      values (v_contributor, v_profile_id, 'admitted', 'passed', coalesce(p_report, '{}'), p_pipeline_version, 'automatic',
              now(), now(), p_record_sha256, v_event);
      insert into trace_ref.profiles (profile_id, source_id, dataset_class, country_code, review_status, synthetic_demo_allowed)
      values (v_profile_id, 'SRC-CONTRIB-001', 'consented_real_reference',
              coalesce(nullif(p_record ->> 'country_code', ''), 'IN'), 'participant_confirmed', false);
      perform trace_private.write_reference_rows(v_profile_id, p_record);
    end if;
  elsif v_existing.contribution_id is not null then
    -- No longer eligible as it stands: suspend now; a later eligible pass starts a fresh contribution.
    update trace_private.contributions set status = 'suspended', updated_at = now() where contribution_id = v_existing.contribution_id;
    delete from trace_ref.profiles where profile_id = v_existing.profile_id;
    update trace_private.contributions set profile_id = null, deleted_at = now() where contribution_id = v_existing.contribution_id;
  end if;

  perform trace_private.set_contribution_state(p_user, v_state, v_reason);
  delete from trace_private.contribution_requests r where r.user_id = p_user and r.requested_at = p_requested_at;
  update trace_private.contribution_requests r set locked_until = null where r.user_id = p_user;
  return v_state;
end;
$$;

create function trace_contrib.log_resume_processing(p_outcome text, p_reason_codes text[])
returns void
language sql
security definer
set search_path = ''
as $$
  insert into trace_private.resume_processing_log (owner_user_id, outcome, reason_codes)
  select trace_review.request_uid(), p_outcome, coalesce(p_reason_codes, '{}')
   where trace_review.request_uid() is not null and p_outcome in ('admitted', 'quarantined', 'failed')
$$;

create function trace_contrib.quarantine_resume(p_candidate_profile_id uuid, p_content_sha256 text, p_record jsonb, p_report jsonb)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
begin
  if v_uid is null or not exists (select 1 from public.trace_candidate_profiles p
                                   where p.candidate_profile_id = p_candidate_profile_id and p.owner_user_id = v_uid) then
    raise exception using errcode = 'TR021', message = 'quarantine needs the owner''s own profile';
  end if;
  insert into trace_private.resume_quarantine (owner_user_id, candidate_profile_id, content_sha256, record, report)
  values (v_uid, p_candidate_profile_id, p_content_sha256, p_record, p_report);
  perform trace_contrib.log_resume_processing('quarantined', array['manual_review_required']);
end;
$$;

-- The caller's own states only (owner role).
create function trace_contrib.my_contribution_status()
returns table (state text, reason text, updated_at timestamptz, consent_active boolean)
language sql
stable
security definer
set search_path = ''
as $$
  select s.state, s.reason, s.updated_at, trace_private.contribution_consent_active(x.u)
    from (select trace_review.request_uid() as u) x
    left join trace_private.user_contribution_status s on s.user_id = x.u
   where x.u is not null
$$;

create function trace_contrib.my_resume_processing()
returns table (outcome text, reason_codes text[], created_at timestamptz)
language sql
stable
security definer
set search_path = ''
as $$
  select l.outcome, l.reason_codes, l.created_at from trace_private.resume_processing_log l
   where l.owner_user_id = trace_review.request_uid() order by l.created_at desc limit 1
$$;

revoke all on function trace_private.contribution_consent_active(uuid), trace_private.set_contribution_state(uuid, text, text),
  trace_private.enqueue_contribution(uuid), trace_private.on_candidate_change(), trace_private.write_reference_rows(text, jsonb),
  trace_contrib.claim_contribution_requests(integer, integer), trace_contrib.contribution_context(uuid),
  trace_contrib.contribution_identity(uuid),
  trace_contrib.record_contribution_outcome(uuid, timestamptz, text, text, jsonb, text, jsonb, text),
  trace_contrib.log_resume_processing(text, text[]), trace_contrib.quarantine_resume(uuid, text, jsonb, jsonb),
  trace_contrib.my_contribution_status(), trace_contrib.my_resume_processing()
  from public, anon, authenticated, service_role;
grant usage on schema trace_contrib to trace_app_runtime, trace_app_user;
grant execute on function trace_contrib.claim_contribution_requests(integer, integer), trace_contrib.contribution_context(uuid),
  trace_contrib.contribution_identity(uuid),
  trace_contrib.record_contribution_outcome(uuid, timestamptz, text, text, jsonb, text, jsonb, text),
  trace_contrib.log_resume_processing(text, text[]), trace_contrib.quarantine_resume(uuid, text, jsonb, jsonb)
  to trace_app_runtime;
grant execute on function trace_contrib.my_contribution_status(), trace_contrib.my_resume_processing() to trace_app_user;
