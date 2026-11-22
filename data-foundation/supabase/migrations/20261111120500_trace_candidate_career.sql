-- Trace Data Foundation v2 · 06 · signed-in user's own profile, resumes and career history
--
-- These tables hold the user's own data and are visible only to that user (RLS in migration 09).
-- Every child row carries owner_user_id and a composite foreign key
-- (candidate_profile_id, owner_user_id) so a row can never attach to another user's profile.
-- Candidate data is never copied into trace_ref automatically; contribution requires a
-- separate, active deidentified_shared_dataset consent and a de-identification step.

create table public.trace_candidate_profiles (
  candidate_profile_id   uuid primary key default gen_random_uuid(),
  owner_user_id          uuid not null default auth.uid() references auth.users(id) on delete cascade,
  country_code           char(2) not null default 'IN' check (country_code ~ '^[A-Z]{2}$'),
  headline               text,
  resume_as_of_year      integer,
  resume_as_of_month     integer,
  resume_as_of_precision public.trace_date_precision not null default 'unknown',
  resume_as_of_lo        integer generated always as (trace_util.month_lo(resume_as_of_year, resume_as_of_month, resume_as_of_precision)) stored,
  resume_as_of_hi        integer generated always as (trace_util.month_hi(resume_as_of_year, resume_as_of_month, resume_as_of_precision)) stored,
  extraction_status      text not null default 'not_started' check (extraction_status in ('not_started', 'queued', 'processing', 'extracted', 'failed')),
  confirmation_status    text not null default 'pending' check (confirmation_status in ('pending', 'participant_confirmed', 'needs_changes')),
  confirmed_at           timestamptz,
  is_test_fixture        boolean not null default false,
  fixture_profile_id     text references trace_ref.profiles(profile_id) on delete set null,
  -- Audit copy of the extraction output. NOT the source of truth: the structured tables are.
  raw_extraction_json    jsonb not null default '{}'::jsonb,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  unique (candidate_profile_id, owner_user_id),
  constraint candidate_profiles_resume_as_of_valid check (
    resume_as_of_precision <> 'ongoing'
    and trace_util.valid_partial_date(resume_as_of_year, resume_as_of_month, resume_as_of_precision)),
  constraint candidate_profiles_fixture_flag check (fixture_profile_id is null or is_test_fixture),
  constraint candidate_profiles_confirmed_at check ((confirmation_status = 'participant_confirmed') = (confirmed_at is not null))
);
alter table public.trace_candidate_profiles enable row level security;
create index trace_candidate_profiles_owner_idx on public.trace_candidate_profiles (owner_user_id);
create index trace_candidate_profiles_fixture_idx on public.trace_candidate_profiles (fixture_profile_id);

create table public.trace_resume_documents (
  resume_id            uuid primary key default gen_random_uuid(),
  owner_user_id        uuid not null default auth.uid() references auth.users(id) on delete cascade,
  candidate_profile_id uuid,
  storage_bucket       text not null default 'trace-resumes' check (storage_bucket = 'trace-resumes'),
  storage_object_path  text not null unique,
  original_filename    text,
  mime_type            text not null check (mime_type in (
                         'application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')),
  byte_size            integer not null check (byte_size between 1 and 10485760),
  content_sha256       text check (content_sha256 ~ '^[0-9a-f]{64}$'),
  parse_status         text not null default 'pending' check (parse_status in ('pending', 'processing', 'parsed', 'failed')),
  is_test_fixture      boolean not null default false,
  uploaded_at          timestamptz not null default now(),
  retention_until      timestamptz not null default (now() + interval '30 days'),
  deleted_at           timestamptz,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  unique (resume_id, owner_user_id),
  foreign key (candidate_profile_id, owner_user_id)
    references public.trace_candidate_profiles(candidate_profile_id, owner_user_id)
    on delete set null (candidate_profile_id),
  -- Object path must sit in the owner's folder: <owner_user_id>/<resume_id>.<ext>
  constraint resume_documents_path_in_owner_folder check (split_part(storage_object_path, '/', 1) = owner_user_id::text),
  constraint resume_documents_retention_bounded check (retention_until <= uploaded_at + interval '365 days')
);
alter table public.trace_resume_documents enable row level security;
create index trace_resume_documents_owner_idx on public.trace_resume_documents (owner_user_id);
create index trace_resume_documents_profile_idx on public.trace_resume_documents (candidate_profile_id, owner_user_id);
create index trace_resume_documents_retention_idx on public.trace_resume_documents (retention_until) where deleted_at is null;

-- One row per extraction attempt: which provider/model processed which resume, where, and
-- under which consent event. Written by the backend only.
create table public.trace_extraction_runs (
  extraction_run_id    uuid primary key default gen_random_uuid(),
  owner_user_id        uuid not null references auth.users(id) on delete cascade,
  resume_id            uuid not null,
  candidate_profile_id uuid not null,
  provider_name        text not null,
  model_name           text not null,
  prompt_version       text not null,
  processing_region    text not null,
  consent_event_id     uuid references public.trace_consent_events(consent_event_id) on delete set null,
  status               text not null default 'queued' check (status in ('queued', 'running', 'succeeded', 'failed')),
  error_code           text,
  started_at           timestamptz,
  finished_at          timestamptz,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  foreign key (resume_id, owner_user_id) references public.trace_resume_documents(resume_id, owner_user_id) on delete cascade,
  foreign key (candidate_profile_id, owner_user_id) references public.trace_candidate_profiles(candidate_profile_id, owner_user_id) on delete cascade
);
alter table public.trace_extraction_runs enable row level security;
create index trace_extraction_runs_owner_idx on public.trace_extraction_runs (owner_user_id);
create index trace_extraction_runs_resume_idx on public.trace_extraction_runs (resume_id, owner_user_id);
create index trace_extraction_runs_profile_idx on public.trace_extraction_runs (candidate_profile_id, owner_user_id);
create index trace_extraction_runs_consent_idx on public.trace_extraction_runs (consent_event_id);

create table public.trace_candidate_employers (
  candidate_employer_id uuid primary key default gen_random_uuid(),
  owner_user_id         uuid not null default auth.uid() references auth.users(id) on delete cascade,
  candidate_profile_id  uuid not null,
  employer_name         text,   -- the user's own data; never copied to trace_ref
  industry_raw          text,
  industry_sector       text not null default 'unknown' check (industry_sector in (
                          'fintech', 'banking_financial_services', 'saas', 'enterprise_software', 'consumer_internet',
                          'ecommerce_marketplace', 'logistics', 'hr_tech', 'analytics_ai', 'media_entertainment',
                          'manufacturing', 'healthcare', 'edtech', 'it_services', 'consulting', 'agency', 'other', 'unknown')),
  company_model         text not null default 'unknown' check (company_model in (
                          'product_company', 'it_services', 'consulting', 'agency', 'global_capability_centre',
                          'non_tech_enterprise', 'hybrid', 'unknown')),
  business_model        text not null default 'unknown' check (business_model in (
                          'b2b', 'b2c', 'b2b2c', 'marketplace', 'internal', 'mixed', 'unknown')),
  company_size_band     text not null default 'unknown' check (company_size_band in (
                          '1-10', '11-50', '51-200', '201-1000', '1001-5000', '5001-plus', 'unknown')),
  attribute_provenance  text not null default 'unknown' check (attribute_provenance in (
                          'resume_stated', 'participant_confirmed', 'inferred_unverified', 'unknown')),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  unique (candidate_employer_id, candidate_profile_id),
  foreign key (candidate_profile_id, owner_user_id)
    references public.trace_candidate_profiles(candidate_profile_id, owner_user_id) on delete cascade
);
alter table public.trace_candidate_employers enable row level security;
create index trace_candidate_employers_profile_idx on public.trace_candidate_employers (candidate_profile_id, owner_user_id);
create index trace_candidate_employers_owner_idx on public.trace_candidate_employers (owner_user_id);

create table public.trace_candidate_employments (
  candidate_employment_id        uuid primary key default gen_random_uuid(),
  owner_user_id                  uuid not null default auth.uid() references auth.users(id) on delete cascade,
  candidate_profile_id           uuid not null,
  sequence_index                 integer not null check (sequence_index >= 1),
  candidate_employer_id          uuid,
  title_raw                      text not null check (btrim(title_raw) <> ''),
  role_id                        text not null default 'ROLE-UNKNOWN' references public.trace_role_taxonomy(role_id),
  seniority_code                 text references public.trace_seniority_levels(seniority_code),
  mapping_status                 public.trace_mapping_status not null default 'unresolved',
  mapping_confidence             numeric(4,3) not null default 0 check (mapping_confidence between 0 and 1),
  mapping_note                   text,
  employment_type                public.trace_employment_type not null default 'unknown',
  primary_career_sequence        boolean not null default true,
  counts_as_full_time_experience boolean,
  classification_note            text,
  start_year integer, start_month integer, start_precision public.trace_date_precision not null default 'unknown',
  end_year integer,   end_month integer,   end_precision public.trace_date_precision not null default 'unknown',
  is_current_as_stated           boolean not null default false,
  start_lo integer generated always as (trace_util.month_lo(start_year, start_month, start_precision)) stored,
  start_hi integer generated always as (trace_util.month_hi(start_year, start_month, start_precision)) stored,
  end_lo   integer generated always as (trace_util.month_lo(end_year, end_month, end_precision)) stored,
  end_hi   integer generated always as (trace_util.month_hi(end_year, end_month, end_precision)) stored,
  entry_source                   text not null default 'resume_extraction' check (entry_source in ('resume_extraction', 'manual_entry')),
  participant_confirmed          boolean not null default false,
  created_at                     timestamptz not null default now(),
  updated_at                     timestamptz not null default now(),
  unique (candidate_employment_id, candidate_profile_id),
  constraint candidate_employments_sequence_unique unique (candidate_profile_id, sequence_index) deferrable initially deferred,
  foreign key (candidate_profile_id, owner_user_id)
    references public.trace_candidate_profiles(candidate_profile_id, owner_user_id) on delete cascade,
  foreign key (candidate_employer_id, candidate_profile_id)
    references public.trace_candidate_employers(candidate_employer_id, candidate_profile_id)
    on delete set null (candidate_employer_id),
  constraint candidate_employments_start_valid check (start_precision <> 'ongoing' and trace_util.valid_partial_date(start_year, start_month, start_precision)),
  constraint candidate_employments_end_valid check (trace_util.valid_partial_date(end_year, end_month, end_precision)),
  constraint candidate_employments_ongoing_is_current check ((end_precision = 'ongoing') = is_current_as_stated),
  constraint candidate_employments_feasible_period check (trace_util.feasible_period(start_year, start_month, start_precision, end_year, end_month, end_precision)),
  constraint candidate_employments_unresolved_role check (mapping_status <> 'unresolved' or role_id = 'ROLE-UNKNOWN'),
  constraint candidate_employments_unknown_role_status check (role_id <> 'ROLE-UNKNOWN' or mapping_status in ('unresolved', 'needs_review')),
  constraint candidate_employments_override_explained check (counts_as_full_time_experience is null or classification_note is not null)
);
alter table public.trace_candidate_employments enable row level security;
create index trace_candidate_employments_profile_idx on public.trace_candidate_employments (candidate_profile_id, owner_user_id);
create index trace_candidate_employments_owner_idx on public.trace_candidate_employments (owner_user_id);
create index trace_candidate_employments_employer_idx on public.trace_candidate_employments (candidate_employer_id, candidate_profile_id);
create index trace_candidate_employments_role_idx on public.trace_candidate_employments (role_id);
create index trace_candidate_employments_seniority_idx on public.trace_candidate_employments (seniority_code);

create table public.trace_candidate_education (
  candidate_education_id uuid primary key default gen_random_uuid(),
  owner_user_id          uuid not null default auth.uid() references auth.users(id) on delete cascade,
  candidate_profile_id   uuid not null,
  qualification_level    text not null default 'unknown' check (qualification_level in (
                           'secondary', 'diploma', 'bachelor', 'master', 'mba', 'doctorate', 'certificate', 'other', 'unknown')),
  field_of_study         text,
  institution_name       text,   -- the user's own data; never copied to trace_ref
  study_mode             text not null default 'unknown' check (study_mode in ('full_time', 'part_time', 'executive', 'online', 'unknown')),
  completion_status      text not null default 'unknown' check (completion_status in ('completed', 'in_progress', 'incomplete', 'unknown')),
  start_year integer, start_month integer, start_precision public.trace_date_precision not null default 'unknown',
  end_year integer,   end_month integer,   end_precision public.trace_date_precision not null default 'unknown',
  start_lo integer generated always as (trace_util.month_lo(start_year, start_month, start_precision)) stored,
  start_hi integer generated always as (trace_util.month_hi(start_year, start_month, start_precision)) stored,
  end_lo   integer generated always as (trace_util.month_lo(end_year, end_month, end_precision)) stored,
  end_hi   integer generated always as (trace_util.month_hi(end_year, end_month, end_precision)) stored,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  foreign key (candidate_profile_id, owner_user_id)
    references public.trace_candidate_profiles(candidate_profile_id, owner_user_id) on delete cascade,
  constraint candidate_education_start_valid check (start_precision <> 'ongoing' and trace_util.valid_partial_date(start_year, start_month, start_precision)),
  constraint candidate_education_end_valid check (trace_util.valid_partial_date(end_year, end_month, end_precision)),
  constraint candidate_education_feasible_period check (trace_util.feasible_period(start_year, start_month, start_precision, end_year, end_month, end_precision))
);
alter table public.trace_candidate_education enable row level security;
create index trace_candidate_education_profile_idx on public.trace_candidate_education (candidate_profile_id, owner_user_id);
create index trace_candidate_education_owner_idx on public.trace_candidate_education (owner_user_id);

create table public.trace_candidate_career_breaks (
  candidate_break_id   uuid primary key default gen_random_uuid(),
  owner_user_id        uuid not null default auth.uid() references auth.users(id) on delete cascade,
  candidate_profile_id uuid not null,
  break_reason         text not null default 'unspecified' check (break_reason in (
                         'education', 'caregiving', 'health', 'relocation', 'job_search', 'sabbatical', 'personal', 'unspecified')),
  note                 text,
  start_year integer, start_month integer, start_precision public.trace_date_precision not null default 'unknown',
  end_year integer,   end_month integer,   end_precision public.trace_date_precision not null default 'unknown',
  start_lo integer generated always as (trace_util.month_lo(start_year, start_month, start_precision)) stored,
  start_hi integer generated always as (trace_util.month_hi(start_year, start_month, start_precision)) stored,
  end_lo   integer generated always as (trace_util.month_lo(end_year, end_month, end_precision)) stored,
  end_hi   integer generated always as (trace_util.month_hi(end_year, end_month, end_precision)) stored,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  foreign key (candidate_profile_id, owner_user_id)
    references public.trace_candidate_profiles(candidate_profile_id, owner_user_id) on delete cascade,
  constraint candidate_breaks_start_valid check (start_precision <> 'ongoing' and trace_util.valid_partial_date(start_year, start_month, start_precision)),
  constraint candidate_breaks_end_valid check (trace_util.valid_partial_date(end_year, end_month, end_precision)),
  constraint candidate_breaks_feasible_period check (trace_util.feasible_period(start_year, start_month, start_precision, end_year, end_month, end_precision))
);
alter table public.trace_candidate_career_breaks enable row level security;
create index trace_candidate_career_breaks_profile_idx on public.trace_candidate_career_breaks (candidate_profile_id, owner_user_id);
create index trace_candidate_career_breaks_owner_idx on public.trace_candidate_career_breaks (owner_user_id);

create table public.trace_candidate_evidence (
  candidate_evidence_id   uuid primary key default gen_random_uuid(),
  owner_user_id           uuid not null default auth.uid() references auth.users(id) on delete cascade,
  candidate_profile_id    uuid not null,
  candidate_employment_id uuid,
  evidence_type           text not null check (evidence_type in ('responsibility', 'achievement', 'scope', 'leadership', 'skill_evidence', 'other')),
  responsibility_area     text references public.trace_responsibility_areas(responsibility_area),
  evidence_text           text not null check (char_length(btrim(evidence_text)) >= 10),
  provenance              text not null default 'resume_claim' check (provenance in ('resume_claim', 'participant_confirmed')),
  evidence_status         public.trace_evidence_status not null default 'extracted_unverified',
  confidence              numeric(4,3) check (confidence between 0 and 1),
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),
  foreign key (candidate_profile_id, owner_user_id)
    references public.trace_candidate_profiles(candidate_profile_id, owner_user_id) on delete cascade,
  foreign key (candidate_employment_id, candidate_profile_id)
    references public.trace_candidate_employments(candidate_employment_id, candidate_profile_id) on delete cascade
);
alter table public.trace_candidate_evidence enable row level security;
create index trace_candidate_evidence_profile_idx on public.trace_candidate_evidence (candidate_profile_id, owner_user_id);
create index trace_candidate_evidence_employment_idx on public.trace_candidate_evidence (candidate_employment_id, candidate_profile_id);
create index trace_candidate_evidence_owner_idx on public.trace_candidate_evidence (owner_user_id);
create index trace_candidate_evidence_area_idx on public.trace_candidate_evidence (responsibility_area);

create table public.trace_candidate_skills (
  candidate_skill_id      uuid primary key default gen_random_uuid(),
  owner_user_id           uuid not null default auth.uid() references auth.users(id) on delete cascade,
  candidate_profile_id    uuid not null,
  skill_id                text not null references public.trace_skills(skill_id),
  skill_text_raw          text,
  candidate_employment_id uuid,
  evidence_source         text not null check (evidence_source in ('resume_explicit', 'llm_inferred', 'participant_confirmed')),
  confidence              numeric(4,3) check (confidence between 0 and 1),
  verified                boolean not null default false,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),
  foreign key (candidate_profile_id, owner_user_id)
    references public.trace_candidate_profiles(candidate_profile_id, owner_user_id) on delete cascade,
  foreign key (candidate_employment_id, candidate_profile_id)
    references public.trace_candidate_employments(candidate_employment_id, candidate_profile_id) on delete cascade,
  unique nulls not distinct (candidate_profile_id, skill_id, evidence_source, candidate_employment_id)
);
alter table public.trace_candidate_skills enable row level security;
create index trace_candidate_skills_profile_idx on public.trace_candidate_skills (candidate_profile_id, owner_user_id);
create index trace_candidate_skills_employment_idx on public.trace_candidate_skills (candidate_employment_id, candidate_profile_id);
create index trace_candidate_skills_owner_idx on public.trace_candidate_skills (owner_user_id);
create index trace_candidate_skills_skill_idx on public.trace_candidate_skills (skill_id);

create trigger set_updated_at before update on public.trace_candidate_profiles for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on public.trace_resume_documents for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on public.trace_extraction_runs for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on public.trace_candidate_employers for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on public.trace_candidate_employments for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on public.trace_candidate_education for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on public.trace_candidate_career_breaks for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on public.trace_candidate_evidence for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on public.trace_candidate_skills for each row execute function trace_util.set_updated_at();
