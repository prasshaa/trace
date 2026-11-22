-- Trace Data Foundation v2.2.0 · 13 · candidate-side structured career profile storage
--
-- Additive. Extends the signed-in user's own career profile (public.trace_candidate_*) beyond
-- employment, with provenance, review and verification tracked separately on every record.
--
-- Two kinds of record, kept apart:
--   EMPLOYMENT EPISODES  trace_candidate_employments (existing). The ONLY input to employment
--                        pathway construction. Internships, freelance and founder work stay here.
--   SUPPORTING RECORDS   trace_candidate_education (extended), trace_candidate_career_activities
--                        (new: certifications, licences, courses, projects, awards, publications,
--                        research, volunteering, extracurricular and leadership activities),
--                        trace_candidate_career_breaks. They are never employment transitions.
--
-- Candidate data stays candidate-side: nothing here is read by trace_ref, the reference pathway
-- analytics, consent eligibility or shared reference evidence. Existing columns, including
-- trace_candidate_profiles.raw_extraction_json, are kept; existing rows are backfilled, not lost.
--
-- Provenance (per record):
--   information_origin   user_stated | source_extracted | inferred      (null = recorded before v2.2.0)
--   review_status        pending_review | user_confirmed | user_corrected | rejected
--   verification_status  unverified | source_document_supported | independently_verified
--   extraction_confidence, source_resume_id, extraction_run_id, source_location
-- Extraction confidence, review and verification are independent columns. No resume excerpt is
-- stored: provenance is a document reference plus a location. Raw titles/descriptions are the
-- user's own record content, not copied excerpts.
--
-- Security: verification, origin and source references are backend-written only (column
-- privileges). This also closes an existing gap where clients could self-assert
-- trace_candidate_evidence.evidence_status = 'document_verified' or trace_candidate_skills.verified.

-- ------------------------------------------------------------------ vocabularies
create type public.trace_information_origin as enum ('user_stated', 'source_extracted', 'inferred');
create type public.trace_record_review_status as enum ('pending_review', 'user_confirmed', 'user_corrected', 'rejected');
create type public.trace_record_verification_status as enum ('unverified', 'source_document_supported', 'independently_verified');
create type public.trace_career_activity_type as enum (
  'certification', 'licence', 'course', 'independent_project', 'portfolio_project', 'award',
  'publication', 'research', 'volunteering', 'extracurricular', 'leadership', 'other'
);

-- ------------------------------------------------------------------ ownership-safe link keys
-- Composite keys so that a child row can only reference a record of the SAME candidate profile,
-- or a source document / extraction run of the SAME owner.
alter table public.trace_candidate_education
  add constraint trace_candidate_education_id_profile_key unique (candidate_education_id, candidate_profile_id);
alter table public.trace_candidate_evidence
  add constraint trace_candidate_evidence_id_profile_key unique (candidate_evidence_id, candidate_profile_id);
alter table public.trace_extraction_runs
  add constraint trace_extraction_runs_id_owner_key unique (extraction_run_id, owner_user_id);

-- ------------------------------------------------------------------ career activities (new)
create table public.trace_candidate_career_activities (
  candidate_activity_id       uuid primary key default gen_random_uuid(),
  owner_user_id               uuid not null default auth.uid() references auth.users(id) on delete cascade,
  candidate_profile_id        uuid not null,
  activity_type               public.trace_career_activity_type not null,
  title_raw                   text not null check (btrim(title_raw) <> ''),        -- original title or name
  category_code               text check (category_code ~ '^[a-z0-9_]+$'),          -- normalised category, when mapped
  category_mapping_status     public.trace_mapping_status not null default 'unresolved',
  category_mapping_confidence numeric(4,3) check (category_mapping_confidence between 0 and 1),
  organisation_raw            text,     -- issuer, publisher, host or associated organisation
  description_raw             text,
  contribution_raw            text,     -- the candidate's own role or contribution
  outcome_raw                 text,     -- outcome or impact, only as stated
  link_url                    text check (link_url ~ '^https?://[^[:space:]]+$'),
  status                      text not null default 'unknown'
                              check (status in ('completed', 'in_progress', 'active', 'expired', 'not_completed', 'unknown')),
  related_employment_id       uuid,     -- e.g. a project or award connected to a job
  credential_identifier_raw   text,     -- certifications and licences only
  start_year integer, start_month integer, start_precision public.trace_date_precision not null default 'unknown',
  end_year integer,   end_month integer,   end_precision public.trace_date_precision not null default 'unknown',
  expiry_year integer, expiry_month integer, expiry_precision public.trace_date_precision not null default 'unknown',
  start_lo integer generated always as (trace_util.month_lo(start_year, start_month, start_precision)) stored,
  start_hi integer generated always as (trace_util.month_hi(start_year, start_month, start_precision)) stored,
  end_lo   integer generated always as (trace_util.month_lo(end_year, end_month, end_precision)) stored,
  end_hi   integer generated always as (trace_util.month_hi(end_year, end_month, end_precision)) stored,
  metadata                    jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),  -- supplementary only
  information_origin          public.trace_information_origin not null default 'user_stated',
  review_status               public.trace_record_review_status not null default 'pending_review',
  verification_status         public.trace_record_verification_status not null default 'unverified',
  extraction_confidence       numeric(4,3) check (extraction_confidence between 0 and 1),
  source_resume_id            uuid,
  extraction_run_id           uuid,
  source_location             text check (source_location is null or btrim(source_location) <> ''),
  created_at                  timestamptz not null default now(),
  updated_at                  timestamptz not null default now(),
  unique (candidate_activity_id, candidate_profile_id),
  foreign key (candidate_profile_id, owner_user_id)
    references public.trace_candidate_profiles(candidate_profile_id, owner_user_id) on delete cascade,
  foreign key (related_employment_id, candidate_profile_id)
    references public.trace_candidate_employments(candidate_employment_id, candidate_profile_id)
    on delete set null (related_employment_id),
  constraint cand_activities_source_resume_fkey foreign key (source_resume_id, owner_user_id)
    references public.trace_resume_documents(resume_id, owner_user_id) on delete set null (source_resume_id),
  constraint cand_activities_extraction_run_fkey foreign key (extraction_run_id, owner_user_id)
    references public.trace_extraction_runs(extraction_run_id, owner_user_id) on delete set null (extraction_run_id),
  constraint cand_activities_start_valid check (start_precision <> 'ongoing' and trace_util.valid_partial_date(start_year, start_month, start_precision)),
  constraint cand_activities_end_valid check (trace_util.valid_partial_date(end_year, end_month, end_precision)),
  constraint cand_activities_expiry_valid check (expiry_precision <> 'ongoing' and trace_util.valid_partial_date(expiry_year, expiry_month, expiry_precision)),
  constraint cand_activities_feasible_period check (trace_util.feasible_period(start_year, start_month, start_precision, end_year, end_month, end_precision)),
  constraint cand_activities_credential_fields check (
    activity_type in ('certification', 'licence')
    or (credential_identifier_raw is null and expiry_precision = 'unknown' and status <> 'expired')),
  constraint cand_activities_category_mapping check ((category_code is null) = (category_mapping_status = 'unresolved')),
  constraint cand_activities_confidence_not_user_stated check (extraction_confidence is null or information_origin <> 'user_stated')
);
alter table public.trace_candidate_career_activities enable row level security;
create index trace_candidate_career_activities_profile_idx on public.trace_candidate_career_activities (candidate_profile_id, owner_user_id);
create index trace_candidate_career_activities_owner_idx on public.trace_candidate_career_activities (owner_user_id);
create index trace_candidate_career_activities_type_idx on public.trace_candidate_career_activities (activity_type);
create index trace_candidate_career_activities_employment_idx on public.trace_candidate_career_activities (related_employment_id, candidate_profile_id);
create index trace_candidate_career_activities_source_resume_idx on public.trace_candidate_career_activities (source_resume_id, owner_user_id);
create index trace_candidate_career_activities_extraction_run_idx on public.trace_candidate_career_activities (extraction_run_id, owner_user_id);
create trigger set_updated_at before update on public.trace_candidate_career_activities
  for each row execute function trace_util.set_updated_at();

comment on table public.trace_candidate_career_activities is
  'Signed-in user''s supporting career records (certifications, licences, courses, projects, awards, publications, '
  'research, volunteering, extracurricular and leadership activities). Never employment episodes; never read by trace_ref.';

-- ------------------------------------------------------------------ provenance on existing records
-- Added without defaults first so existing rows are backfilled from their legacy columns below.
alter table public.trace_candidate_employments
  add column information_origin    public.trace_information_origin,
  add column review_status         public.trace_record_review_status,
  add column verification_status   public.trace_record_verification_status,
  add column extraction_confidence numeric(4,3) check (extraction_confidence between 0 and 1),
  add column source_resume_id      uuid,
  add column extraction_run_id     uuid,
  add column source_location       text check (source_location is null or btrim(source_location) <> ''),
  add constraint cand_employments_source_resume_fkey foreign key (source_resume_id, owner_user_id)
    references public.trace_resume_documents(resume_id, owner_user_id) on delete set null (source_resume_id),
  add constraint cand_employments_extraction_run_fkey foreign key (extraction_run_id, owner_user_id)
    references public.trace_extraction_runs(extraction_run_id, owner_user_id) on delete set null (extraction_run_id),
  add constraint cand_employments_confidence_not_user_stated check (extraction_confidence is null or information_origin is distinct from 'user_stated');
create index trace_candidate_employments_source_resume_idx on public.trace_candidate_employments (source_resume_id, owner_user_id);
create index trace_candidate_employments_extraction_run_idx on public.trace_candidate_employments (extraction_run_id, owner_user_id);

alter table public.trace_candidate_education
  add column information_origin    public.trace_information_origin,
  add column review_status         public.trace_record_review_status,
  add column verification_status   public.trace_record_verification_status,
  add column extraction_confidence numeric(4,3) check (extraction_confidence between 0 and 1),
  add column source_resume_id      uuid,
  add column extraction_run_id     uuid,
  add column source_location       text check (source_location is null or btrim(source_location) <> ''),
  add constraint cand_education_source_resume_fkey foreign key (source_resume_id, owner_user_id)
    references public.trace_resume_documents(resume_id, owner_user_id) on delete set null (source_resume_id),
  add constraint cand_education_extraction_run_fkey foreign key (extraction_run_id, owner_user_id)
    references public.trace_extraction_runs(extraction_run_id, owner_user_id) on delete set null (extraction_run_id),
  add constraint cand_education_confidence_not_user_stated check (extraction_confidence is null or information_origin is distinct from 'user_stated');
create index trace_candidate_education_source_resume_idx on public.trace_candidate_education (source_resume_id, owner_user_id);
create index trace_candidate_education_extraction_run_idx on public.trace_candidate_education (extraction_run_id, owner_user_id);

alter table public.trace_candidate_career_breaks
  add column information_origin    public.trace_information_origin,
  add column review_status         public.trace_record_review_status,
  add column verification_status   public.trace_record_verification_status,
  add column extraction_confidence numeric(4,3) check (extraction_confidence between 0 and 1),
  add column source_resume_id      uuid,
  add column extraction_run_id     uuid,
  add column source_location       text check (source_location is null or btrim(source_location) <> ''),
  add constraint cand_breaks_source_resume_fkey foreign key (source_resume_id, owner_user_id)
    references public.trace_resume_documents(resume_id, owner_user_id) on delete set null (source_resume_id),
  add constraint cand_breaks_extraction_run_fkey foreign key (extraction_run_id, owner_user_id)
    references public.trace_extraction_runs(extraction_run_id, owner_user_id) on delete set null (extraction_run_id),
  add constraint cand_breaks_confidence_not_user_stated check (extraction_confidence is null or information_origin is distinct from 'user_stated');
create index trace_candidate_career_breaks_source_resume_idx on public.trace_candidate_career_breaks (source_resume_id, owner_user_id);
create index trace_candidate_career_breaks_extraction_run_idx on public.trace_candidate_career_breaks (extraction_run_id, owner_user_id);

alter table public.trace_candidate_evidence
  add column information_origin    public.trace_information_origin,
  add column review_status         public.trace_record_review_status,
  add column verification_status   public.trace_record_verification_status,
  add column extraction_confidence numeric(4,3) check (extraction_confidence between 0 and 1),
  add column source_resume_id      uuid,
  add column extraction_run_id     uuid,
  add column source_location       text check (source_location is null or btrim(source_location) <> ''),
  add constraint cand_evidence_source_resume_fkey foreign key (source_resume_id, owner_user_id)
    references public.trace_resume_documents(resume_id, owner_user_id) on delete set null (source_resume_id),
  add constraint cand_evidence_extraction_run_fkey foreign key (extraction_run_id, owner_user_id)
    references public.trace_extraction_runs(extraction_run_id, owner_user_id) on delete set null (extraction_run_id),
  add constraint cand_evidence_confidence_not_user_stated check (extraction_confidence is null or information_origin is distinct from 'user_stated');
create index trace_candidate_evidence_source_resume_idx on public.trace_candidate_evidence (source_resume_id, owner_user_id);
create index trace_candidate_evidence_extraction_run_idx on public.trace_candidate_evidence (extraction_run_id, owner_user_id);

alter table public.trace_candidate_skills
  add column information_origin    public.trace_information_origin,
  add column review_status         public.trace_record_review_status,
  add column verification_status   public.trace_record_verification_status,
  add column extraction_confidence numeric(4,3) check (extraction_confidence between 0 and 1),
  add column source_resume_id      uuid,
  add column extraction_run_id     uuid,
  add column source_location       text check (source_location is null or btrim(source_location) <> ''),
  add constraint cand_skills_source_resume_fkey foreign key (source_resume_id, owner_user_id)
    references public.trace_resume_documents(resume_id, owner_user_id) on delete set null (source_resume_id),
  add constraint cand_skills_extraction_run_fkey foreign key (extraction_run_id, owner_user_id)
    references public.trace_extraction_runs(extraction_run_id, owner_user_id) on delete set null (extraction_run_id),
  add constraint cand_skills_confidence_not_user_stated check (extraction_confidence is null or information_origin is distinct from 'user_stated');
create index trace_candidate_skills_source_resume_idx on public.trace_candidate_skills (source_resume_id, owner_user_id);
create index trace_candidate_skills_extraction_run_idx on public.trace_candidate_skills (extraction_run_id, owner_user_id);

-- Backfill existing rows from legacy columns. Where the legacy data does not say where a value came
-- from, information_origin stays null ("not recorded before v2.2.0") rather than being guessed.
update public.trace_candidate_employments set
  information_origin = case entry_source when 'manual_entry' then 'user_stated' else 'source_extracted' end::public.trace_information_origin,
  review_status = case when participant_confirmed then 'user_confirmed' else 'pending_review' end::public.trace_record_review_status,
  verification_status = 'unverified';
update public.trace_candidate_evidence set
  information_origin = case provenance when 'participant_confirmed' then 'user_stated' else 'source_extracted' end::public.trace_information_origin,
  review_status = case evidence_status when 'participant_confirmed' then 'user_confirmed' when 'rejected' then 'rejected'
                  else 'pending_review' end::public.trace_record_review_status,
  -- Legacy document_verified means a document supports it; that is NOT independent verification.
  verification_status = case evidence_status when 'document_verified' then 'source_document_supported'
                        else 'unverified' end::public.trace_record_verification_status;
update public.trace_candidate_skills set
  information_origin = case evidence_source when 'llm_inferred' then 'inferred' when 'participant_confirmed' then 'user_stated'
                       else 'source_extracted' end::public.trace_information_origin,
  review_status = case evidence_source when 'participant_confirmed' then 'user_confirmed' else 'pending_review' end::public.trace_record_review_status,
  verification_status = 'unverified';   -- legacy `verified` had no defined meaning; not mapped
update public.trace_candidate_education set review_status = 'pending_review', verification_status = 'unverified';
update public.trace_candidate_career_breaks set review_status = 'pending_review', verification_status = 'unverified';

alter table public.trace_candidate_employments
  alter column information_origin set default 'user_stated',
  alter column review_status set default 'pending_review',
  alter column review_status set not null,
  alter column verification_status set default 'unverified',
  alter column verification_status set not null;

alter table public.trace_candidate_education
  alter column information_origin set default 'user_stated',
  alter column review_status set default 'pending_review',
  alter column review_status set not null,
  alter column verification_status set default 'unverified',
  alter column verification_status set not null;

alter table public.trace_candidate_career_breaks
  alter column information_origin set default 'user_stated',
  alter column review_status set default 'pending_review',
  alter column review_status set not null,
  alter column verification_status set default 'unverified',
  alter column verification_status set not null;

alter table public.trace_candidate_evidence
  alter column information_origin set default 'user_stated',
  alter column review_status set default 'pending_review',
  alter column review_status set not null,
  alter column verification_status set default 'unverified',
  alter column verification_status set not null;

alter table public.trace_candidate_skills
  alter column information_origin set default 'user_stated',
  alter column review_status set default 'pending_review',
  alter column review_status set not null,
  alter column verification_status set default 'unverified',
  alter column verification_status set not null;

-- ------------------------------------------------------------------ education: original title
alter table public.trace_candidate_education
  add column qualification_title_raw text check (qualification_title_raw is null or btrim(qualification_title_raw) <> ''),
  add column notes text;
comment on column public.trace_candidate_education.qualification_title_raw is
  'Qualification exactly as stated (e.g. "B.Tech (CSE)"). qualification_level is the normalised type; it never replaces this.';

-- ------------------------------------------------------------------ evidence: achievements may attach to an activity
alter table public.trace_candidate_evidence
  add column candidate_activity_id uuid,
  add constraint trace_candidate_evidence_activity_fkey foreign key (candidate_activity_id, candidate_profile_id)
    references public.trace_candidate_career_activities(candidate_activity_id, candidate_profile_id) on delete cascade,
  add constraint trace_candidate_evidence_single_parent check (num_nonnulls(candidate_employment_id, candidate_activity_id) <= 1);
create index trace_candidate_evidence_activity_idx on public.trace_candidate_evidence (candidate_activity_id, candidate_profile_id);

-- ------------------------------------------------------------------ skills: any source record, unmapped skills, stated proficiency
alter table public.trace_candidate_skills
  alter column skill_id drop not null,          -- a candidate skill may not exist in the taxonomy yet
  add column candidate_education_id uuid,
  add column candidate_activity_id  uuid,
  add column candidate_evidence_id  uuid,
  add column stated_proficiency_raw text,       -- only as explicitly stated; never inferred
  add constraint trace_candidate_skills_education_fkey foreign key (candidate_education_id, candidate_profile_id)
    references public.trace_candidate_education(candidate_education_id, candidate_profile_id) on delete cascade,
  add constraint trace_candidate_skills_activity_fkey foreign key (candidate_activity_id, candidate_profile_id)
    references public.trace_candidate_career_activities(candidate_activity_id, candidate_profile_id) on delete cascade,
  add constraint trace_candidate_skills_evidence_fkey foreign key (candidate_evidence_id, candidate_profile_id)
    references public.trace_candidate_evidence(candidate_evidence_id, candidate_profile_id) on delete cascade,
  add constraint trace_candidate_skills_single_source check (
    num_nonnulls(candidate_employment_id, candidate_education_id, candidate_activity_id, candidate_evidence_id) <= 1),
  add constraint trace_candidate_skills_identified check (skill_id is not null or btrim(coalesce(skill_text_raw, '')) <> ''),
  add constraint trace_candidate_skills_no_inferred_proficiency check (stated_proficiency_raw is null or information_origin is distinct from 'inferred'),
  add constraint trace_candidate_skills_llm_is_inferred check (evidence_source <> 'llm_inferred' or information_origin = 'inferred');
-- One row per (skill, source record, legacy evidence_source); replaces the v2.0 key, which could
-- not include the new source links or unmapped skills.
alter table public.trace_candidate_skills drop constraint trace_candidate_skills_candidate_profile_id_skill_id_eviden_key;
create unique index trace_candidate_skills_claim_key on public.trace_candidate_skills (
  candidate_profile_id, coalesce(skill_id, 'raw:' || lower(btrim(skill_text_raw))), evidence_source,
  candidate_employment_id, candidate_education_id, candidate_activity_id, candidate_evidence_id
) nulls not distinct;
create index trace_candidate_skills_education_idx on public.trace_candidate_skills (candidate_education_id, candidate_profile_id);
create index trace_candidate_skills_activity_idx on public.trace_candidate_skills (candidate_activity_id, candidate_profile_id);
create index trace_candidate_skills_evidence_idx on public.trace_candidate_skills (candidate_evidence_id, candidate_profile_id);

-- ------------------------------------------------------------------ privileges and RLS
-- Start from zero on the new table (Supabase default privileges grant new public tables to
-- anon/authenticated), then grant back what the owner needs.
revoke all on public.trace_candidate_career_activities from anon, authenticated, public;
grant all on public.trace_candidate_career_activities to service_role;
grant select, delete on public.trace_candidate_career_activities to authenticated;
create policy "candidate career activities owner all" on public.trace_candidate_career_activities
  for all to authenticated using (owner_user_id = (select auth.uid())) with check (owner_user_id = (select auth.uid()));

-- Clients may write their own record content and review_status, but never the backend-managed
-- provenance and verification columns, the legacy self-assertable verification flags, or
-- generated columns. Table-level INSERT/UPDATE is replaced by column-level grants.
do $$
declare
  t text;
  cols text;
begin
  foreach t in array array['trace_candidate_employments', 'trace_candidate_education', 'trace_candidate_career_breaks',
                           'trace_candidate_evidence', 'trace_candidate_skills', 'trace_candidate_career_activities']
  loop
    execute format('revoke insert, update on public.%I from authenticated', t);
    select string_agg(quote_ident(c.column_name), ', ' order by c.ordinal_position) into cols
    from information_schema.columns c
    where c.table_schema = 'public' and c.table_name = t and c.is_generated = 'NEVER'
      and c.column_name not in ('information_origin', 'verification_status', 'extraction_confidence',
                                'source_resume_id', 'extraction_run_id', 'source_location',
                                'evidence_status', 'verified');
    execute format('grant insert (%s), update (%s) on public.%I to authenticated', cols, cols, t);
  end loop;
end
$$;
