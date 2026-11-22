-- Trace data foundation v2.21.0: structures for transition planning knowledge (product Phase 4: D27, D28, D29).
--
-- Additive only. Curated, public reference content; never user data, never synthetic data. Content is seeded separately
-- (v2.22.0, generated from reviewed JSON).
--
--   trace_bridge_roles            D27: a small, target-specific catalogue of bridge roles. Each row documents the bridge's
--                                 responsibilities, the target requirements it develops (fully or partly), its stated
--                                 entry requirements and entry signals, suitable backgrounds, trade-offs and sources.
--   trace_action_templates        D28: a versioned library of action templates for the four stages (plus stage 0:
--                                 resolve missing information first).
--   trace_target_role_requirements.criterion / criteria_version
--                                 D29: the structured form of one employer criterion (AND/OR, ranges, leaves). The
--                                 criterion text, importance and source are unchanged.
--   trace_target_role_sources.accepted_role_families / excluded_role_families
--                                 D29: the curated mapping of a posting's stated backgrounds to role families.
--
-- Readable by signed-in users; written by nobody but migrations.

-- ------------------------------------------------------------------ D29: structured employer criteria
alter table public.trace_target_role_sources
  add column accepted_role_families text[] not null default '{}',
  add column excluded_role_families text[] not null default '{}';
alter table public.trace_target_role_sources
  add constraint target_sources_accepted_families_when_accepting
    check (cardinality(accepted_role_families) = 0 or entry_background_stance = 'accepts_adjacent'),
  add constraint target_sources_excluded_families_when_excluding
    check (cardinality(excluded_role_families) = 0 or entry_background_stance = 'excludes_specified_transitions');

alter table public.trace_target_role_requirements
  add column criterion jsonb,
  add column criteria_version text;
alter table public.trace_target_role_requirements
  add constraint target_requirements_criterion_versioned check ((criterion is null) = (criteria_version is null)),
  add constraint target_requirements_criterion_object check (criterion is null or jsonb_typeof(criterion) = 'object');

-- Validates one criterion node: {"all": [...]}, {"any": [...]} or a leaf of a known type with valid role families.
create or replace function trace_util.check_criterion_node(p_node jsonb)
returns void
language plpgsql
set search_path = ''
as $$
declare
  v_child jsonb;
  v_type text;
  v_bad text;
begin
  if jsonb_typeof(p_node) <> 'object' then
    raise exception using errcode = 'TR026', message = 'a criterion node is an object';
  end if;
  if p_node ? 'note' and jsonb_typeof(p_node -> 'note') <> 'string' then
    raise exception using errcode = 'TR026', message = 'a note is text';
  end if;
  if p_node ? 'all' or p_node ? 'any' then
    if (select count(*) from jsonb_object_keys(p_node) k where k <> 'note') <> 1 then
      raise exception using errcode = 'TR026', message = 'a combination node has exactly one of all or any (and an optional note)';
    end if;
    v_child := coalesce(p_node -> 'all', p_node -> 'any');
    if jsonb_typeof(v_child) <> 'array' or jsonb_array_length(v_child) < 2 then
      raise exception using errcode = 'TR026', message = 'all/any combine at least two criteria';
    end if;
    for v_child in select value from jsonb_array_elements(coalesce(p_node -> 'all', p_node -> 'any')) loop
      perform trace_util.check_criterion_node(v_child);
    end loop;
    return;
  end if;
  v_type := p_node ->> 'type';
  if v_type is null or v_type not in ('years_in_families', 'product_exposure', 'current_role_in_families', 'not_transitioning_from',
                                      'total_years', 'qualification', 'stated_condition') then
    raise exception using errcode = 'TR026', message = format('unknown criterion type: %s', coalesce(v_type, 'none'));
  end if;
  if v_type in ('years_in_families', 'product_exposure', 'current_role_in_families', 'not_transitioning_from') then
    if jsonb_typeof(p_node -> 'families') <> 'array' or jsonb_array_length(p_node -> 'families') = 0 then
      raise exception using errcode = 'TR026', message = format('%s names at least one role family', v_type);
    end if;
    select string_agg(f, ', ') into v_bad
      from jsonb_array_elements_text(p_node -> 'families') f
     where not exists (select 1 from public.trace_role_families r where r.role_family = f);
    if v_bad is not null then
      raise exception using errcode = 'TR026', message = format('unknown role families: %s', v_bad);
    end if;
  end if;
  if v_type in ('years_in_families', 'total_years') then
    if jsonb_typeof(p_node -> 'min') <> 'number' or (p_node ->> 'min')::numeric < 0 then
      raise exception using errcode = 'TR026', message = 'a years criterion has a minimum';
    end if;
    if jsonb_typeof(p_node -> 'max') = 'number' and (p_node ->> 'max')::numeric < (p_node ->> 'min')::numeric then
      raise exception using errcode = 'TR026', message = 'a range maximum is not below its minimum';
    end if;
  end if;
  if v_type = 'qualification' then
    if jsonb_typeof(p_node -> 'fields') <> 'array' or jsonb_typeof(p_node -> 'or_equivalent') <> 'boolean'
       or (p_node ? 'min_level' and jsonb_typeof(p_node -> 'min_level') not in ('string', 'null'))
       or coalesce(p_node ->> 'min_level', 'bachelor') not in ('diploma', 'bachelor', 'master', 'doctorate') then
      raise exception using errcode = 'TR026', message = 'a qualification criterion has fields, an optional minimum level and or_equivalent';
    end if;
  end if;
  if v_type = 'stated_condition' and coalesce(btrim(p_node ->> 'text'), '') = '' then
    raise exception using errcode = 'TR026', message = 'a stated condition keeps its text';
  end if;
end;
$$;

create or replace function trace_util.check_employer_criterion()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.criterion is not null then
    perform trace_util.check_criterion_node(new.criterion);
  end if;
  return new;
end;
$$;
create trigger check_employer_criterion
  before insert or update of criterion on public.trace_target_role_requirements
  for each row execute function trace_util.check_employer_criterion();

create or replace function trace_util.check_background_families()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_bad text;
begin
  select string_agg(f, ', ') into v_bad
    from unnest(new.accepted_role_families || new.excluded_role_families) f
   where not exists (select 1 from public.trace_role_families r where r.role_family = f);
  if v_bad is not null then
    raise exception using errcode = 'TR026', message = format('unknown role families: %s', v_bad);
  end if;
  return new;
end;
$$;
create trigger check_background_families
  before insert or update of accepted_role_families, excluded_role_families on public.trace_target_role_sources
  for each row execute function trace_util.check_background_families();

-- ------------------------------------------------------------------ D27: bridge-role catalogue
create table public.trace_bridge_roles (
  catalogue_version         text not null,
  target_role_id            text not null references public.trace_role_taxonomy (role_id),
  bridge_role_id            text not null references public.trace_role_taxonomy (role_id),
  requirements_pack         text not null,
  responsibilities          text[] not null,
  develops                  text[] not null,
  partly_develops           text[] not null default '{}',
  develops_note             text not null,
  entry_basis               text not null check (entry_basis in ('open_entry', 'signals', 'target_assessment')),
  entry_requirements_stated text not null,
  entry_signal_families     text[] not null default '{}',
  entry_signal_requirements text[] not null default '{}',
  suitable_backgrounds      text not null,
  suitability_note          text not null,
  tradeoff_note             text not null,
  level_step                boolean not null default false,
  source_keys               text[] not null,
  source_notes              jsonb not null default '{}',
  created_at                timestamptz not null default now(),
  primary key (catalogue_version, target_role_id, bridge_role_id),
  constraint bridge_roles_not_target check (bridge_role_id <> target_role_id),
  constraint bridge_roles_responsibilities check (cardinality(responsibilities) >= 1),
  constraint bridge_roles_develops check (cardinality(develops) >= 1 and not (develops && partly_develops)),
  constraint bridge_roles_sources check (cardinality(source_keys) >= 1),
  constraint bridge_roles_signals check (entry_basis = 'signals' or (cardinality(entry_signal_families) = 0 and cardinality(entry_signal_requirements) = 0)),
  constraint bridge_roles_signals_present check (entry_basis <> 'signals' or cardinality(entry_signal_families) + cardinality(entry_signal_requirements) >= 1)
);
comment on table public.trace_bridge_roles is
  'D27: curated bridge roles per target. A candidate route, never a default; recommended only by the transparent rules of the transition planner.';

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
  return new;
end;
$$;
create trigger check_bridge_role
  before insert or update on public.trace_bridge_roles
  for each row execute function trace_util.check_bridge_role();

-- ------------------------------------------------------------------ D28: action templates
create table public.trace_action_templates (
  template_version  text not null,
  template_key      text not null,
  requirements_pack text not null,
  stage             smallint not null check (stage between 0 and 4),
  kind              text not null check (kind in ('resolve', 'build', 'demonstrate', 'reposition', 'apply')),
  requirement_key   text,
  objective         text not null,
  action_text       text not null,
  deliverable       text not null,
  where_options     text[] not null,
  basis_sources     text[] not null default '{}',
  created_at        timestamptz not null default now(),
  primary key (template_version, template_key),
  constraint action_templates_stage_kind check (
    (kind = 'resolve' and stage = 0) or (kind = 'build' and stage = 1) or (kind = 'demonstrate' and stage = 2)
    or (kind = 'reposition' and stage = 3) or (kind = 'apply' and stage = 4)),
  constraint action_templates_requirement check ((kind in ('build', 'demonstrate')) = (requirement_key is not null)),
  constraint action_templates_where check (cardinality(where_options) >= 1 and where_options <@ array['current_job', 'internal_move',
    'external_project', 'self_directed_study', 'job_market']::text[])
);
comment on table public.trace_action_templates is
  'D28: versioned action templates. No courses, providers, costs, timelines, placement outcomes or openings.';

create or replace function trace_util.check_action_template()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_bad text;
begin
  if new.requirement_key is not null and not exists (
       select 1 from public.trace_capability_requirements r
        where r.knowledge_pack = new.requirements_pack and r.requirement_key = new.requirement_key) then
    raise exception using errcode = 'TR026', message = format('unknown requirement %s in %s', new.requirement_key, new.requirements_pack);
  end if;
  select string_agg(k, ', ') into v_bad
    from unnest(new.basis_sources) k
   where not exists (select 1 from public.trace_target_role_sources s where s.source_key = k);
  if v_bad is not null then
    raise exception using errcode = 'TR026', message = format('unknown sources: %s', v_bad);
  end if;
  return new;
end;
$$;
create trigger check_action_template
  before insert or update on public.trace_action_templates
  for each row execute function trace_util.check_action_template();

-- ------------------------------------------------------------------ privileges and RLS
alter table public.trace_bridge_roles enable row level security;
alter table public.trace_action_templates enable row level security;
revoke all on public.trace_bridge_roles, public.trace_action_templates from public, anon, authenticated, service_role;
grant select on public.trace_bridge_roles, public.trace_action_templates to authenticated, service_role;
create policy "bridge roles read" on public.trace_bridge_roles for select to authenticated using (true);
create policy "action templates read" on public.trace_action_templates for select to authenticated using (true);

revoke all on function trace_util.check_criterion_node(jsonb), trace_util.check_employer_criterion(),
  trace_util.check_background_families(), trace_util.check_bridge_role(), trace_util.check_action_template()
  from public, anon, authenticated, service_role;
