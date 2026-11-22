-- Trace Data Foundation v2 · 05 · de-identified reference cohort (schema trace_ref, not exposed)
--
-- No names, contact details, URLs, named institutions, named employers or raw resume text.
-- Uploaded third-party resume fixtures are rejected by CHECK constraints: their subjects have
-- not given permission for shared reuse. Lifting that rule requires a new, reviewed migration.

create table trace_ref.data_sources (
  source_id             text primary key check (source_id ~ '^SRC-[A-Z0-9-]+$'),
  dataset_class         public.trace_dataset_class not null,
  source_type           text not null check (source_type in ('participant_contribution', 'synthetic_generator', 'public_dataset')),
  source_label          text not null,
  geographic_scope      text not null default 'IN',
  licence_or_permission text,
  permitted_uses        text[] not null default '{}',
  prohibited_uses       text[] not null default '{}',
  provenance_url        text,
  provenance_note       text,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  unique (source_id, dataset_class),
  constraint data_sources_no_uploaded_resume_fixtures check (dataset_class <> 'uploaded_resume_fixture')
);

create table trace_ref.profiles (
  profile_id              text primary key check (profile_id ~ '^[A-Z]{3}-[0-9]{3,}$'),
  source_id               text not null,
  dataset_class           public.trace_dataset_class not null,
  country_code            char(2) not null default 'IN' check (country_code ~ '^[A-Z]{2}$'),
  region_group            text,
  profile_summary         text,
  resume_as_of_year       integer,
  resume_as_of_month      integer,
  resume_as_of_precision  public.trace_date_precision not null default 'unknown',
  resume_as_of_lo         integer generated always as (trace_util.month_lo(resume_as_of_year, resume_as_of_month, resume_as_of_precision)) stored,
  resume_as_of_hi         integer generated always as (trace_util.month_hi(resume_as_of_year, resume_as_of_month, resume_as_of_precision)) stored,
  review_status           public.trace_review_status not null default 'extracted_unverified',
  scope_status            text not null default 'review_required',
  synthetic_demo_allowed  boolean not null default false,
  -- Pseudonymous link to the contributing user. Needed to honour withdrawal; service_role only.
  contributor_user_id     uuid references auth.users(id) on delete set null,
  source_consent_event_id uuid references public.trace_consent_events(consent_event_id) on delete set null,
  source_ref              text,
  scenario_code           text,
  test_tags               text[] not null default '{}',
  data_quality_flags      text[] not null default '{}',
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),
  unique (profile_id, dataset_class),
  foreign key (source_id, dataset_class) references trace_ref.data_sources(source_id, dataset_class),
  constraint profiles_no_uploaded_resume_fixtures check (dataset_class <> 'uploaded_resume_fixture'),
  constraint profiles_resume_as_of_valid check (
    resume_as_of_precision <> 'ongoing'
    and trace_util.valid_partial_date(resume_as_of_year, resume_as_of_month, resume_as_of_precision)),
  constraint profiles_demo_only_synthetic check (not synthetic_demo_allowed or dataset_class = 'synthetic_test_fixture'),
  constraint profiles_synthetic_review_status check ((dataset_class = 'synthetic_test_fixture') = (review_status = 'synthetic_curated')),
  constraint profiles_synthetic_prefix check (dataset_class <> 'synthetic_test_fixture' or profile_id like 'SYN-%'),
  constraint profiles_contributor_only_real check (
    dataset_class = 'consented_real_reference' or (contributor_user_id is null and source_consent_event_id is null))
);
create index profiles_source_idx on trace_ref.profiles (source_id, dataset_class);
create index profiles_dataset_class_idx on trace_ref.profiles (dataset_class);
create index profiles_contributor_idx on trace_ref.profiles (contributor_user_id);
create index profiles_consent_event_idx on trace_ref.profiles (source_consent_event_id);

-- Consent integrity, enforced in the database: a consented real profile must be created under
-- an actual GRANTED deidentified_shared_dataset consent event that belongs to the same
-- contributor. This checks provenance at write time; trace_ref.v_eligible_profiles still checks
-- the contributor's CURRENT consent on every read, so a later withdrawal excludes the profile
-- immediately. Setting either link to null is allowed only as the account-deletion cascade
-- (it can only remove eligibility, never grant it).
create function trace_util.enforce_profile_consent_integrity() returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_becomes_real boolean := tg_op = 'INSERT' or old.dataset_class is distinct from new.dataset_class;
begin
  if new.dataset_class <> 'consented_real_reference' then
    return new;  -- profiles_contributor_only_real forbids both links on other classes
  end if;
  if v_becomes_real and (new.contributor_user_id is null or new.source_consent_event_id is null) then
    raise exception 'consented_real_reference profile % needs contributor_user_id and source_consent_event_id', new.profile_id
      using errcode = 'check_violation';
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
end
$$;
revoke execute on function trace_util.enforce_profile_consent_integrity() from public;

create trigger enforce_consent_integrity
  before insert or update of dataset_class, contributor_user_id, source_consent_event_id on trace_ref.profiles
  for each row execute function trace_util.enforce_profile_consent_integrity();

-- Per-profile employer pseudonyms. Tokens are not shared across profiles.
create table trace_ref.employers (
  profile_id           text not null references trace_ref.profiles(profile_id) on delete cascade,
  employer_id          text not null,
  industry_raw         text,
  industry_sector      text not null default 'unknown' check (industry_sector in (
                         'fintech', 'banking_financial_services', 'saas', 'enterprise_software', 'consumer_internet',
                         'ecommerce_marketplace', 'logistics', 'hr_tech', 'analytics_ai', 'media_entertainment',
                         'manufacturing', 'healthcare', 'edtech', 'it_services', 'consulting', 'agency', 'other', 'unknown')),
  company_model        text not null default 'unknown' check (company_model in (
                         'product_company', 'it_services', 'consulting', 'agency', 'global_capability_centre',
                         'non_tech_enterprise', 'hybrid', 'unknown')),
  business_model       text not null default 'unknown' check (business_model in (
                         'b2b', 'b2c', 'b2b2c', 'marketplace', 'internal', 'mixed', 'unknown')),
  company_size_band    text not null default 'unknown' check (company_size_band in (
                         '1-10', '11-50', '51-200', '201-1000', '1001-5000', '5001-plus', 'unknown')),
  attribute_provenance text not null default 'unknown' check (attribute_provenance in (
                         'resume_stated', 'participant_confirmed', 'inferred_unverified', 'synthetic_curated', 'unknown')),
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  primary key (profile_id, employer_id),
  constraint employers_token_scoped check (employer_id like profile_id || '-ORG-%')
);

create table trace_ref.employments (
  employment_id                  text primary key,
  profile_id                     text not null references trace_ref.profiles(profile_id) on delete cascade,
  sequence_index                 integer not null check (sequence_index >= 1),
  employer_id                    text,
  title_raw_redacted             text not null check (btrim(title_raw_redacted) <> ''),
  role_id                        text not null references public.trace_role_taxonomy(role_id),
  seniority_code                 text references public.trace_seniority_levels(seniority_code),  -- null = taxonomy default
  mapping_status                 public.trace_mapping_status not null,
  mapping_confidence             numeric(4,3) not null check (mapping_confidence between 0 and 1),
  mapping_note                   text,
  employment_type                public.trace_employment_type not null default 'unknown',
  primary_career_sequence        boolean not null default true,
  counts_as_full_time_experience boolean,   -- null = derive from employment_type; explicit value needs classification_note
  classification_note            text,
  start_year                     integer,
  start_month                    integer,
  start_precision                public.trace_date_precision not null,
  end_year                       integer,
  end_month                      integer,
  end_precision                  public.trace_date_precision not null,
  is_current_as_stated           boolean not null default false,
  start_lo integer generated always as (trace_util.month_lo(start_year, start_month, start_precision)) stored,
  start_hi integer generated always as (trace_util.month_hi(start_year, start_month, start_precision)) stored,
  end_lo   integer generated always as (trace_util.month_lo(end_year, end_month, end_precision)) stored,
  end_hi   integer generated always as (trace_util.month_hi(end_year, end_month, end_precision)) stored,
  extraction_status              public.trace_evidence_status not null default 'extracted_unverified',
  data_quality_flags             text[] not null default '{}',
  created_at                     timestamptz not null default now(),
  updated_at                     timestamptz not null default now(),
  unique (profile_id, employment_id),
  -- Deferred so a whole profile can be re-sequenced inside one transaction.
  constraint employments_sequence_unique unique (profile_id, sequence_index) deferrable initially deferred,
  foreign key (profile_id, employer_id) references trace_ref.employers(profile_id, employer_id),
  constraint employments_id_scoped check (employment_id like profile_id || '-EMP-%'),
  constraint employments_start_valid check (start_precision <> 'ongoing' and trace_util.valid_partial_date(start_year, start_month, start_precision)),
  constraint employments_end_valid check (trace_util.valid_partial_date(end_year, end_month, end_precision)),
  constraint employments_ongoing_is_current check ((end_precision = 'ongoing') = is_current_as_stated),
  constraint employments_feasible_period check (trace_util.feasible_period(start_year, start_month, start_precision, end_year, end_month, end_precision)),
  constraint employments_unresolved_role check (mapping_status <> 'unresolved' or role_id = 'ROLE-UNKNOWN'),
  constraint employments_unknown_role_status check (role_id <> 'ROLE-UNKNOWN' or mapping_status in ('unresolved', 'needs_review')),
  constraint employments_override_explained check (counts_as_full_time_experience is null or classification_note is not null)
);
create index employments_profile_seq_idx on trace_ref.employments (profile_id, sequence_index);
create index employments_role_idx on trace_ref.employments (role_id);
create index employments_seniority_idx on trace_ref.employments (seniority_code);
create index employments_employer_idx on trace_ref.employments (profile_id, employer_id);

create table trace_ref.education (
  education_id        text primary key,
  profile_id          text not null references trace_ref.profiles(profile_id) on delete cascade,
  qualification_level text not null check (qualification_level in (
                        'secondary', 'diploma', 'bachelor', 'master', 'mba', 'doctorate', 'certificate', 'other', 'unknown')),
  field_of_study      text,
  study_mode          text not null default 'unknown' check (study_mode in ('full_time', 'part_time', 'executive', 'online', 'unknown')),
  completion_status   text not null default 'unknown' check (completion_status in ('completed', 'in_progress', 'incomplete', 'unknown')),
  start_year integer, start_month integer, start_precision public.trace_date_precision not null default 'unknown',
  end_year integer,   end_month integer,   end_precision public.trace_date_precision not null default 'unknown',
  start_lo integer generated always as (trace_util.month_lo(start_year, start_month, start_precision)) stored,
  start_hi integer generated always as (trace_util.month_hi(start_year, start_month, start_precision)) stored,
  end_lo   integer generated always as (trace_util.month_lo(end_year, end_month, end_precision)) stored,
  end_hi   integer generated always as (trace_util.month_hi(end_year, end_month, end_precision)) stored,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint education_id_scoped check (education_id like profile_id || '-EDU-%'),
  constraint education_start_valid check (start_precision <> 'ongoing' and trace_util.valid_partial_date(start_year, start_month, start_precision)),
  constraint education_end_valid check (trace_util.valid_partial_date(end_year, end_month, end_precision)),
  constraint education_feasible_period check (trace_util.feasible_period(start_year, start_month, start_precision, end_year, end_month, end_precision))
);
create index education_profile_idx on trace_ref.education (profile_id);

create table trace_ref.career_breaks (
  break_id      text primary key,
  profile_id    text not null references trace_ref.profiles(profile_id) on delete cascade,
  break_reason  text not null default 'unspecified' check (break_reason in (
                  'education', 'caregiving', 'health', 'relocation', 'job_search', 'sabbatical', 'personal', 'unspecified')),
  note_redacted text,
  start_year integer, start_month integer, start_precision public.trace_date_precision not null,
  end_year integer,   end_month integer,   end_precision public.trace_date_precision not null,
  start_lo integer generated always as (trace_util.month_lo(start_year, start_month, start_precision)) stored,
  start_hi integer generated always as (trace_util.month_hi(start_year, start_month, start_precision)) stored,
  end_lo   integer generated always as (trace_util.month_lo(end_year, end_month, end_precision)) stored,
  end_hi   integer generated always as (trace_util.month_hi(end_year, end_month, end_precision)) stored,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint career_breaks_id_scoped check (break_id like profile_id || '-BRK-%'),
  constraint career_breaks_start_valid check (start_precision <> 'ongoing' and trace_util.valid_partial_date(start_year, start_month, start_precision)),
  constraint career_breaks_end_valid check (trace_util.valid_partial_date(end_year, end_month, end_precision)),
  constraint career_breaks_feasible_period check (trace_util.feasible_period(start_year, start_month, start_precision, end_year, end_month, end_precision))
);
create index career_breaks_profile_idx on trace_ref.career_breaks (profile_id);

-- Responsibilities, achievements, scope and leadership claims. Responsibilities are rows with
-- evidence_type = 'responsibility' and a controlled responsibility_area.
create table trace_ref.experience_evidence (
  evidence_id            text primary key,
  profile_id             text not null references trace_ref.profiles(profile_id) on delete cascade,
  employment_id          text,
  evidence_type          text not null check (evidence_type in ('responsibility', 'achievement', 'scope', 'leadership', 'skill_evidence', 'other')),
  responsibility_area    text references public.trace_responsibility_areas(responsibility_area),
  evidence_text_redacted text not null check (char_length(btrim(evidence_text_redacted)) >= 10),
  provenance             text not null check (provenance in ('resume_claim', 'participant_confirmed', 'public_source', 'synthetic_fixture')),
  evidence_status        public.trace_evidence_status not null default 'extracted_unverified',
  confidence             numeric(4,3) check (confidence between 0 and 1),
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  -- Composite key: evidence can only attach to an employment of the same profile.
  foreign key (profile_id, employment_id) references trace_ref.employments(profile_id, employment_id) on delete cascade,
  constraint evidence_id_scoped check (evidence_id like profile_id || '-%')
);
create index experience_evidence_profile_employment_idx on trace_ref.experience_evidence (profile_id, employment_id);
create index experience_evidence_area_idx on trace_ref.experience_evidence (responsibility_area);

create table trace_ref.profile_skills (
  profile_skill_id text primary key,
  profile_id       text not null references trace_ref.profiles(profile_id) on delete cascade,
  skill_id         text not null references public.trace_skills(skill_id),
  skill_text_raw   text,
  employment_id    text,
  evidence_id      text references trace_ref.experience_evidence(evidence_id) on delete set null,
  evidence_source  text not null check (evidence_source in ('resume_explicit', 'llm_inferred', 'participant_confirmed', 'synthetic_fixture')),
  confidence       numeric(4,3) check (confidence between 0 and 1),
  verified         boolean not null default false,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  foreign key (profile_id, employment_id) references trace_ref.employments(profile_id, employment_id) on delete cascade,
  unique nulls not distinct (profile_id, skill_id, evidence_source, employment_id),
  constraint profile_skills_id_scoped check (profile_skill_id like profile_id || '-%')
);
create index profile_skills_skill_idx on trace_ref.profile_skills (skill_id);
create index profile_skills_profile_employment_idx on trace_ref.profile_skills (profile_id, employment_id);
create index profile_skills_evidence_idx on trace_ref.profile_skills (evidence_id);

create table trace_ref.analytics_settings (
  setting_key   text primary key check (setting_key ~ '^[a-z0-9_]+$'),
  value_numeric numeric not null,
  unit          text not null,
  description   text not null,
  updated_at    timestamptz not null default now()
);

-- Frozen record of the settings and cohort size used by a set of pathway runs.
create table trace_ref.reference_snapshots (
  snapshot_id                 uuid primary key default gen_random_uuid(),
  data_mode                   public.trace_data_mode not null,
  settings                    jsonb not null,
  eligible_profile_count      integer not null check (eligible_profile_count >= 0),
  confirmed_transition_count  integer not null check (confirmed_transition_count >= 0),
  notes                       text,
  created_at                  timestamptz not null default now(),
  unique (snapshot_id, data_mode)
);

-- Service-side audit trail for access to reference data, resumes and exports.
create table trace_ref.access_log (
  access_log_id bigint generated always as identity primary key,
  occurred_at   timestamptz not null default now(),
  actor_user_id uuid,
  actor_role    text not null,
  action        text not null check (action in ('read', 'export', 'insert', 'update', 'delete', 'download_resume', 'consent_check', 'other')),
  object_schema text not null,
  object_table  text not null,
  object_id     text,
  purpose       text not null,
  details       jsonb not null default '{}'::jsonb
);
create index access_log_actor_idx on trace_ref.access_log (actor_user_id, occurred_at desc);
create index access_log_object_idx on trace_ref.access_log (object_schema, object_table, object_id);
create trigger access_log_append_only before update on trace_ref.access_log
  for each row execute function trace_util.prevent_update();

create trigger set_updated_at before update on trace_ref.data_sources for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on trace_ref.profiles for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on trace_ref.employers for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on trace_ref.employments for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on trace_ref.education for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on trace_ref.career_breaks for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on trace_ref.experience_evidence for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on trace_ref.profile_skills for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on trace_ref.analytics_settings for each row execute function trace_util.set_updated_at();

-- Defence in depth: RLS on every table even though the schema is not exposed.
-- No policies are created, so only service_role (BYPASSRLS) can read or write.
alter table trace_ref.data_sources enable row level security;
alter table trace_ref.profiles enable row level security;
alter table trace_ref.employers enable row level security;
alter table trace_ref.employments enable row level security;
alter table trace_ref.education enable row level security;
alter table trace_ref.career_breaks enable row level security;
alter table trace_ref.experience_evidence enable row level security;
alter table trace_ref.profile_skills enable row level security;
alter table trace_ref.analytics_settings enable row level security;
alter table trace_ref.reference_snapshots enable row level security;
alter table trace_ref.access_log enable row level security;
