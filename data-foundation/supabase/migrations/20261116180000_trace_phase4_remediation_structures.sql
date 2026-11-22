-- Trace data foundation v2.24.0: structures for the Phase 4 remediation (D31-D36). Additive only.
--
--   Taxonomy v3 (D35): the Data analysis area gains the scope question "outcome_metrics_influence" (version 3). Existing
--     classifications are not changed; under the Phase 3.1 safeguard they cannot support a new Gap that depends on it.
--   trace_capability_requirements (D31, D35): decision_rights (a decision-rights requirement) and area_condition_fallback
--     ({area: "transferable"}: an area whose condition was answered No still counts as Transferable).
--   trace_bridge_roles (D32, D34): entry_evidence and progression_evidence (source fact or hypothesis), may_be_primary,
--     partial_development ({requirement: {develops, incomplete, seek}}).
--   trace_digital_role_candidates (D33): F1's explicitly listed digital roles, with mapping confidence. A candidate pool,
--     not approved bridges.
--   trace_action_templates.labels (+ trace_pathway_actions.labels), and the place value "trace_profile".
--   trace_employer_criterion_rules (D36): versioned structured rules per employer criterion. pm-eligibility-v1 stays in
--     trace_target_role_requirements.criterion for audit; later versions live here, so nothing is overwritten.
--   trace_education_fields (D36): education fields with explicit aliases, for whole-value matching.
-- Curated content only; readable by signed-in users; written by migrations.

-- ------------------------------------------------------------------ taxonomy v3 (D35)
update public.trace_responsibility_areas
   set qualifiers = qualifiers || jsonb_build_object('outcome_metrics_influence', jsonb_build_object(
         'question', 'Did you define or own product outcome metrics and use them to influence product decisions, rather than only analyse or report metrics?',
         'version', 3))
 where responsibility_area = 'data_analysis' and not (qualifiers ? 'outcome_metrics_influence');

-- ------------------------------------------------------------------ requirements (D31, D35)
alter table public.trace_capability_requirements
  add column decision_rights boolean not null default false,
  add column area_condition_fallback jsonb not null default '{}';

create or replace function trace_util.check_capability_codes()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_bad text;
begin
  select string_agg(x, ', ') into v_bad
    from unnest(new.direct_areas || new.transferable_areas) x
   where not exists (select 1 from public.trace_responsibility_areas a where a.responsibility_area = x);
  if v_bad is not null then
    raise exception using errcode = 'TR025', message = format('unknown responsibility areas: %s', v_bad);
  end if;
  select string_agg(x, ', ') into v_bad
    from unnest(new.direct_skills || new.transferable_skills) x
   where not exists (select 1 from public.trace_skills s where s.skill_id = x);
  if v_bad is not null then
    raise exception using errcode = 'TR025', message = format('unknown skills: %s', v_bad);
  end if;
  select string_agg(x, ', ') into v_bad
    from unnest(new.transferable_role_families) x
   where not exists (select 1 from public.trace_role_families f where f.role_family = x);
  if v_bad is not null then
    raise exception using errcode = 'TR025', message = format('unknown role families: %s', v_bad);
  end if;
  select string_agg(c.key || '=' || (c.value #>> '{}'), ', ') into v_bad
    from jsonb_each(new.area_conditions) c
   where jsonb_typeof(c.value) <> 'string'
      or not (c.key = any (new.direct_areas || new.transferable_areas))
      or not exists (select 1 from public.trace_responsibility_areas a where a.responsibility_area = c.key and a.qualifiers ? (c.value #>> '{}'));
  if v_bad is not null then
    raise exception using errcode = 'TR025', message = format('area conditions must name a mapped area and one of its qualifiers: %s', v_bad);
  end if;
  -- v2.24.0: a fallback applies only to a conditioned DIRECT area, and only as Transferable.
  select string_agg(f.key, ', ') into v_bad
    from jsonb_each(new.area_condition_fallback) f
   where (f.value #>> '{}') is distinct from 'transferable' or not (new.area_conditions ? f.key) or not (f.key = any (new.direct_areas));
  if v_bad is not null then
    raise exception using errcode = 'TR025', message = format('a fallback is "transferable" for a conditioned direct area: %s', v_bad);
  end if;
  return new;
end;
$$;

-- ------------------------------------------------------------------ bridge roles (D32, D34)
alter table public.trace_bridge_roles
  add column entry_evidence text check (entry_evidence in ('employer_stated', 'source_stated_context', 'target_plan', 'hypothesis')),
  add column progression_evidence text check (progression_evidence in ('source_stated', 'hypothesis')),
  add column may_be_primary boolean not null default true,
  add column partial_development jsonb not null default '{}';

create or replace function trace_util.check_bridge_role()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_bad text;
begin
  select string_agg(k, ', ') into v_bad
    from unnest(new.develops || new.partly_develops || new.entry_signal_requirements) k
   where not exists (select 1 from public.trace_capability_requirements r
                      where r.knowledge_pack = new.requirements_pack and r.requirement_key = k);
  if v_bad is not null then
    raise exception using errcode = 'TR026', message = format('unknown requirements in %s: %s', new.requirements_pack, v_bad);
  end if;
  select string_agg(f, ', ') into v_bad
    from unnest(new.entry_signal_families) f
   where not exists (select 1 from public.trace_role_families r where r.role_family = f);
  if v_bad is not null then
    raise exception using errcode = 'TR026', message = format('unknown role families: %s', v_bad);
  end if;
  select string_agg(k, ', ') into v_bad
    from unnest(new.source_keys) k
   where not exists (select 1 from public.trace_target_role_sources s where s.source_key = k);
  if v_bad is not null then
    raise exception using errcode = 'TR026', message = format('unknown sources: %s', v_bad);
  end if;
  if exists (select 1 from public.trace_role_taxonomy t where t.role_id = new.bridge_role_id and t.role_family = 'project_program_management') then
    raise exception using errcode = 'TR026', message = 'program and project management are not bridge roles (D27)';
  end if;
  -- v2.24.0 (D32): every partly developed requirement explains what it develops, what remains and what to seek.
  select string_agg(k, ', ') into v_bad
    from unnest(new.partly_develops) k
   where new.entry_evidence is not null
     and not (new.partial_development ? k and coalesce(new.partial_development -> k ->> 'develops', '') <> ''
              and coalesce(new.partial_development -> k ->> 'incomplete', '') <> '' and coalesce(new.partial_development -> k ->> 'seek', '') <> '');
  if v_bad is not null then
    raise exception using errcode = 'TR026', message = format('partial development needs develops, incomplete and seek: %s', v_bad);
  end if;
  if exists (select 1 from jsonb_object_keys(new.partial_development) k where not (k = any (new.partly_develops))) then
    raise exception using errcode = 'TR026', message = 'partial development notes only for partly developed requirements';
  end if;
  return new;
end;
$$;

-- ------------------------------------------------------------------ digital role candidate pool (D33)
create table public.trace_digital_role_candidates (
  catalogue_version  text not null,
  f1_role            text not null,
  trace_role_family  text references public.trace_role_families (role_family),
  mapping_confidence text not null check (mapping_confidence in ('source_direct', 'curated_hypothesis', 'unmapped')),
  summary            text not null,
  shared_skills      text[] not null,
  develops           text[] not null default '{}',
  suitable_backgrounds text not null,
  source_key         text not null,
  created_at         timestamptz not null default now(),
  primary key (catalogue_version, f1_role),
  constraint digital_candidates_mapping check ((mapping_confidence = 'unmapped') = (trace_role_family is null))
);
comment on table public.trace_digital_role_candidates is
  'D33: digital roles F1 lists as sharing product manager skills. A candidate pool, never an approved bridge.';

-- ------------------------------------------------------------------ action template labels and places
alter table public.trace_action_templates
  add column labels text[] not null default '{}';
alter table public.trace_action_templates
  add constraint action_templates_labels check (labels <@ array['learning_exercise', 'personal_project_possible', 'factual_accuracy',
                                                                'trace_curated']::text[]),
  drop constraint action_templates_where,
  add constraint action_templates_where check (cardinality(where_options) >= 1 and where_options <@ array['current_job', 'internal_move',
    'external_project', 'self_directed_study', 'job_market', 'trace_profile']::text[]);
alter table public.trace_pathway_actions
  add column labels text[] not null default '{}';

-- ------------------------------------------------------------------ education fields and versioned employer rules (D36)
create table public.trace_education_fields (
  field_code text primary key,
  label      text not null,
  category   text not null check (category in ('computing', 'engineering', 'quantitative', 'business')),
  aliases    text[] not null check (cardinality(aliases) >= 1),
  created_at timestamptz not null default now()
);

create table public.trace_employer_criterion_rules (
  rules_version  text not null,
  requirement_id uuid not null references public.trace_target_role_requirements (requirement_id) on delete cascade,
  rule           jsonb not null,
  created_at     timestamptz not null default now(),
  primary key (rules_version, requirement_id)
);
comment on table public.trace_employer_criterion_rules is
  'D29/D36: versioned structured rules per employer criterion. Earlier versions are kept; nothing is overwritten.';

-- Codes inside a rule: equivalent families exist; qualification fields are known codes or categories; degree types known.
create or replace function trace_util.check_criterion_codes(p_node jsonb)
returns void
language plpgsql
set search_path = ''
as $$
declare
  v_child jsonb;
  v_bad text;
begin
  if p_node ? 'all' or p_node ? 'any' then
    for v_child in select value from jsonb_array_elements(coalesce(p_node -> 'all', p_node -> 'any')) loop
      perform trace_util.check_criterion_codes(v_child);
    end loop;
    return;
  end if;
  if p_node ? 'equivalent_families' then
    select string_agg(f, ', ') into v_bad from jsonb_array_elements_text(p_node -> 'equivalent_families') f
     where not exists (select 1 from public.trace_role_families r where r.role_family = f);
    if v_bad is not null then
      raise exception using errcode = 'TR026', message = format('unknown equivalent families: %s', v_bad);
    end if;
  end if;
  if p_node ->> 'type' = 'qualification' then
    select string_agg(f, ', ') into v_bad from jsonb_array_elements_text(p_node -> 'fields') f
     where not exists (select 1 from public.trace_education_fields e where e.field_code = f or 'category:' || e.category = f);
    if v_bad is not null then
      raise exception using errcode = 'TR026', message = format('unknown education fields: %s', v_bad);
    end if;
    select string_agg(t, ', ') into v_bad from jsonb_array_elements_text(coalesce(p_node -> 'types', '[]')) t
     where t not in ('BE', 'BTech', 'BSc', 'BCom', 'BBA', 'BCA', 'MTech', 'MSc', 'MCA', 'MCom', 'MBA', 'PhD', 'Diploma');
    if v_bad is not null then
      raise exception using errcode = 'TR026', message = format('unknown qualification types: %s', v_bad);
    end if;
  end if;
end;
$$;

create or replace function trace_util.check_employer_criterion_rule()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  perform trace_util.check_criterion_node(new.rule);
  perform trace_util.check_criterion_codes(new.rule);
  return new;
end;
$$;
create trigger check_employer_criterion_rule
  before insert or update on public.trace_employer_criterion_rules
  for each row execute function trace_util.check_employer_criterion_rule();

-- ------------------------------------------------------------------ privileges and RLS
alter table public.trace_digital_role_candidates enable row level security;
alter table public.trace_education_fields enable row level security;
alter table public.trace_employer_criterion_rules enable row level security;
revoke all on public.trace_digital_role_candidates, public.trace_education_fields, public.trace_employer_criterion_rules
  from public, anon, authenticated, service_role;
grant select on public.trace_digital_role_candidates, public.trace_education_fields, public.trace_employer_criterion_rules to authenticated;
create policy "digital role candidates read" on public.trace_digital_role_candidates for select to authenticated using (true);
create policy "education fields read" on public.trace_education_fields for select to authenticated using (true);
create policy "employer criterion rules read" on public.trace_employer_criterion_rules
  for select to authenticated using (exists (
    select 1 from public.trace_target_role_requirements q join public.trace_target_role_sources s using (target_role_source_id)
     where q.requirement_id = trace_employer_criterion_rules.requirement_id and s.source_status <> 'withdrawn'));
revoke all on function trace_util.check_criterion_codes(jsonb), trace_util.check_employer_criterion_rule()
  from public, anon, authenticated, service_role;
