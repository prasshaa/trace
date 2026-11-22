-- Trace Data Foundation v2 · 07 · target roles, pathway runs, recommendations, evidence, agent threads
--
-- Pathway runs, recommendations and evidence are written by the backend (service_role) and
-- read by their owner. data_mode is carried on every pathway row through composite foreign
-- keys, so CHECK constraints can stop synthetic data being presented as empirical evidence.

create table public.trace_target_role_sources (
  target_role_source_id uuid primary key default gen_random_uuid(),
  company_name          text not null,
  role_title            text not null,
  role_id               text references public.trace_role_taxonomy(role_id),
  location              text,
  source_url            text not null check (source_url ~ '^https://'),
  source_type           text not null check (source_type in ('official_job_posting', 'official_career_framework', 'other_public_source')),
  published_at          date,
  retrieved_at          timestamptz not null default now(),
  valid_until           date,
  content_sha256        text check (content_sha256 ~ '^[0-9a-f]{64}$'),
  source_text_redacted  text,
  source_status         text not null default 'current_check_required' check (source_status in ('current', 'current_check_required', 'expired', 'withdrawn')),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);
alter table public.trace_target_role_sources enable row level security;
create index trace_target_role_sources_role_idx on public.trace_target_role_sources (role_id);

create table public.trace_target_role_requirements (
  requirement_id            uuid primary key default gen_random_uuid(),
  target_role_source_id     uuid not null references public.trace_target_role_sources(target_role_source_id) on delete cascade,
  requirement_type          text not null check (requirement_type in ('experience', 'skill', 'responsibility', 'education', 'domain', 'leadership', 'other')),
  requirement_text          text not null,
  skill_id                  text references public.trace_skills(skill_id),
  responsibility_area       text references public.trace_responsibility_areas(responsibility_area),
  min_years_experience      numeric(4,1) check (min_years_experience >= 0),
  importance                text not null default 'unknown' check (importance in ('required', 'preferred', 'contextual', 'unknown')),
  extracted_by              text not null default 'llm' check (extracted_by in ('llm', 'reviewer', 'llm_reviewed')),
  user_visible_explanation  text,
  created_at                timestamptz not null default now(),
  updated_at                timestamptz not null default now(),
  constraint target_requirements_skill_type check (requirement_type <> 'skill' or skill_id is not null)
);
alter table public.trace_target_role_requirements enable row level security;
create index trace_target_role_requirements_source_idx on public.trace_target_role_requirements (target_role_source_id);
create index trace_target_role_requirements_skill_idx on public.trace_target_role_requirements (skill_id);
create index trace_target_role_requirements_area_idx on public.trace_target_role_requirements (responsibility_area);

create table public.trace_pathway_runs (
  pathway_run_id        uuid primary key default gen_random_uuid(),
  owner_user_id         uuid not null references auth.users(id) on delete cascade,
  candidate_profile_id  uuid not null,
  data_mode             public.trace_data_mode not null,
  target_role_id        text not null references public.trace_role_taxonomy(role_id),
  target_role_source_id uuid references public.trace_target_role_sources(target_role_source_id) on delete set null,
  target_company        text,
  constraints_json      jsonb not null default '{}'::jsonb,
  reference_snapshot_id uuid,
  status                text not null default 'created' check (status in ('created', 'running', 'completed', 'failed')),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  unique (pathway_run_id, owner_user_id, data_mode),
  foreign key (candidate_profile_id, owner_user_id)
    references public.trace_candidate_profiles(candidate_profile_id, owner_user_id) on delete cascade,
  -- The snapshot must have been taken in the same data mode as the run.
  foreign key (reference_snapshot_id, data_mode)
    references trace_ref.reference_snapshots(snapshot_id, data_mode) on delete restrict,
  constraint pathway_runs_completed_has_snapshot check (status <> 'completed' or reference_snapshot_id is not null)
);
alter table public.trace_pathway_runs enable row level security;
create index trace_pathway_runs_owner_idx on public.trace_pathway_runs (owner_user_id, created_at desc);
create index trace_pathway_runs_profile_idx on public.trace_pathway_runs (candidate_profile_id, owner_user_id);
create index trace_pathway_runs_target_role_idx on public.trace_pathway_runs (target_role_id);
create index trace_pathway_runs_target_source_idx on public.trace_pathway_runs (target_role_source_id);
create index trace_pathway_runs_snapshot_idx on public.trace_pathway_runs (reference_snapshot_id, data_mode);

-- Timeline metric (docs/DATA_DICTIONARY.md section 7):
--   route_start_to_target_start_months = start month of the target-role stint minus the start
--   month of the first route stint, from month-precision dates only.
create table public.trace_pathway_recommendations (
  recommendation_id        uuid primary key default gen_random_uuid(),
  pathway_run_id           uuid not null,
  owner_user_id            uuid not null,
  data_mode                public.trace_data_mode not null,
  rank                     integer not null check (rank >= 1),
  recommendation_reason    text,
  score_breakdown          jsonb not null default '{}'::jsonb,
  evidence_class           public.trace_evidence_class not null,
  supporting_profile_count integer not null default 0 check (supporting_profile_count >= 0),
  dated_profile_count      integer not null default 0 check (dated_profile_count >= 0),
  timeline_metric          text check (timeline_metric in ('route_start_to_target_start_months')),
  p25_months               numeric,
  median_months            numeric,
  p75_months               numeric,
  timeline_display_allowed boolean not null default false,
  limitations              text[] not null default '{}',
  created_at               timestamptz not null default now(),
  unique (pathway_run_id, rank),
  unique (recommendation_id, owner_user_id),
  unique (recommendation_id, owner_user_id, data_mode),
  foreign key (pathway_run_id, owner_user_id, data_mode)
    references public.trace_pathway_runs(pathway_run_id, owner_user_id, data_mode) on delete cascade,
  constraint recommendations_dated_le_supporting check (dated_profile_count <= supporting_profile_count),
  -- Synthetic runs can only produce synthetic_test_only evidence; empirical runs never can.
  constraint recommendations_evidence_class_matches_mode check ((data_mode = 'empirical') = (evidence_class <> 'synthetic_test_only')),
  constraint recommendations_no_synthetic_timelines check (data_mode = 'empirical' or not timeline_display_allowed),
  constraint recommendations_percentiles_all_or_none check (
    (p25_months is null and median_months is null and p75_months is null)
    or (p25_months is not null and median_months is not null and p75_months is not null
        and p25_months <= median_months and median_months <= p75_months and p25_months >= 0)),
  constraint recommendations_percentiles_need_metric check (median_months is null or timeline_metric is not null),
  constraint recommendations_timeline_needs_percentiles check (not timeline_display_allowed or median_months is not null)
);
alter table public.trace_pathway_recommendations enable row level security;
create index trace_pathway_recommendations_owner_idx on public.trace_pathway_recommendations (owner_user_id);
create index trace_pathway_recommendations_run_idx on public.trace_pathway_recommendations (pathway_run_id, owner_user_id, data_mode);

create table public.trace_pathway_route_steps (
  recommendation_id uuid not null,
  owner_user_id     uuid not null,
  step_index        integer not null check (step_index >= 1),
  role_id           text not null references public.trace_role_taxonomy(role_id),
  step_note         text,
  primary key (recommendation_id, step_index),
  foreign key (recommendation_id, owner_user_id)
    references public.trace_pathway_recommendations(recommendation_id, owner_user_id) on delete cascade
);
alter table public.trace_pathway_route_steps enable row level security;
create index trace_pathway_route_steps_owner_idx on public.trace_pathway_route_steps (owner_user_id);
create index trace_pathway_route_steps_role_idx on public.trace_pathway_route_steps (role_id);
create index trace_pathway_route_steps_rec_owner_idx on public.trace_pathway_route_steps (recommendation_id, owner_user_id);

create table public.trace_pathway_evidence (
  pathway_evidence_id     uuid primary key default gen_random_uuid(),
  recommendation_id       uuid not null,
  owner_user_id           uuid not null,
  data_mode               public.trace_data_mode not null,
  evidence_type           text not null check (evidence_type in (
                            'observed_sequence', 'transition_count', 'duration_distribution', 'role_requirement', 'other')),
  -- Hidden from clients by column privileges (migration 09).
  reference_profile_id    text,
  reference_dataset_class public.trace_dataset_class,
  target_role_source_id   uuid references public.trace_target_role_sources(target_role_source_id) on delete set null,
  evidence_summary        text not null,
  created_at              timestamptz not null default now(),
  foreign key (recommendation_id, owner_user_id, data_mode)
    references public.trace_pathway_recommendations(recommendation_id, owner_user_id, data_mode) on delete cascade,
  -- dataset_class travels with the profile id, so the CHECKs below see the real class.
  -- Withdrawn/deleted profiles drop out of evidence rather than blocking deletion.
  foreign key (reference_profile_id, reference_dataset_class)
    references trace_ref.profiles(profile_id, dataset_class) on delete set null,
  constraint pathway_evidence_profile_pair check ((reference_profile_id is null) = (reference_dataset_class is null)),
  constraint pathway_evidence_empirical_uses_real check (
    data_mode <> 'empirical' or reference_dataset_class is null or reference_dataset_class = 'consented_real_reference'),
  constraint pathway_evidence_synthetic_uses_synthetic check (
    data_mode = 'empirical' or reference_dataset_class is null or reference_dataset_class = 'synthetic_test_fixture')
);
alter table public.trace_pathway_evidence enable row level security;
create index trace_pathway_evidence_rec_idx on public.trace_pathway_evidence (recommendation_id, owner_user_id, data_mode);
create index trace_pathway_evidence_owner_idx on public.trace_pathway_evidence (owner_user_id);
create index trace_pathway_evidence_profile_idx on public.trace_pathway_evidence (reference_profile_id, reference_dataset_class);
create index trace_pathway_evidence_source_idx on public.trace_pathway_evidence (target_role_source_id);

create table public.trace_agent_threads (
  thread_id            uuid primary key default gen_random_uuid(),
  owner_user_id        uuid not null default auth.uid() references auth.users(id) on delete cascade,
  candidate_profile_id uuid,
  thread_state         jsonb not null default '{}'::jsonb,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  unique (thread_id, owner_user_id),
  foreign key (candidate_profile_id, owner_user_id)
    references public.trace_candidate_profiles(candidate_profile_id, owner_user_id)
    on delete set null (candidate_profile_id)
);
alter table public.trace_agent_threads enable row level security;
create index trace_agent_threads_owner_idx on public.trace_agent_threads (owner_user_id);
create index trace_agent_threads_profile_idx on public.trace_agent_threads (candidate_profile_id, owner_user_id);

create table public.trace_agent_messages (
  message_id       uuid primary key default gen_random_uuid(),
  thread_id        uuid not null,
  owner_user_id    uuid not null default auth.uid(),
  role             text not null check (role in ('user', 'assistant', 'tool', 'system')),
  content_redacted text,
  tool_name        text,
  tool_input       jsonb,
  tool_output      jsonb,
  created_at       timestamptz not null default now(),
  foreign key (thread_id, owner_user_id) references public.trace_agent_threads(thread_id, owner_user_id) on delete cascade
);
alter table public.trace_agent_messages enable row level security;
create index trace_agent_messages_thread_idx on public.trace_agent_messages (thread_id, owner_user_id, created_at);
create index trace_agent_messages_owner_idx on public.trace_agent_messages (owner_user_id);

-- Evidence thresholds are configurable (trace_ref.analytics_settings), so they are enforced by
-- trigger rather than CHECK. Missing settings fail closed.
create function trace_util.enforce_recommendation_thresholds() returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_limited  numeric;
  v_moderate numeric;
  v_strong   numeric;
  v_dated    numeric;
begin
  if new.data_mode <> 'empirical' then
    return new;  -- CHECK constraints already force synthetic_test_only and no timeline
  end if;
  select value_numeric into v_limited  from trace_ref.analytics_settings where setting_key = 'min_profiles_limited';
  select value_numeric into v_moderate from trace_ref.analytics_settings where setting_key = 'min_profiles_moderate';
  select value_numeric into v_strong   from trace_ref.analytics_settings where setting_key = 'min_profiles_strong';
  select value_numeric into v_dated    from trace_ref.analytics_settings where setting_key = 'min_dated_profiles_for_timeline';
  if v_limited is null or v_moderate is null or v_strong is null or v_dated is null then
    raise exception 'analytics thresholds are not configured in trace_ref.analytics_settings' using errcode = 'check_violation';
  end if;
  if (new.evidence_class = 'strong' and new.supporting_profile_count < v_strong)
     or (new.evidence_class = 'moderate' and new.supporting_profile_count < v_moderate)
     or (new.evidence_class = 'limited' and new.supporting_profile_count < v_limited) then
    raise exception 'evidence_class % needs more supporting profiles than %', new.evidence_class, new.supporting_profile_count
      using errcode = 'check_violation';
  end if;
  if new.timeline_display_allowed and new.dated_profile_count < v_dated then
    raise exception 'timeline display needs at least % month-precision profiles (got %)', v_dated, new.dated_profile_count
      using errcode = 'check_violation';
  end if;
  if new.median_months is not null and new.dated_profile_count < v_dated then
    raise exception 'percentiles may only be stored when dated_profile_count >= %', v_dated using errcode = 'check_violation';
  end if;
  return new;
end
$$;
revoke execute on function trace_util.enforce_recommendation_thresholds() from public;

create trigger enforce_thresholds before insert or update on public.trace_pathway_recommendations
  for each row execute function trace_util.enforce_recommendation_thresholds();

create trigger set_updated_at before update on public.trace_target_role_sources for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on public.trace_target_role_requirements for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on public.trace_pathway_runs for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on public.trace_agent_threads for each row execute function trace_util.set_updated_at();
