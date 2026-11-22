-- Trace data foundation v2.23.0: owner-scoped, append-only transition-plan runs (product Phase 4, D30).
--
-- Before: trace_pathway_runs and its children existed, owner-readable, but only service_role could write them (the
-- backend never uses service_role), and trace_ref.reference_snapshots had no writer.
--
-- After: the narrowest backend-mediated write path.
--   trace_plan_writer              NOLOGIN, no BYPASSRLS, no members that can use it. Owns one function and holds only
--                                  the INSERT grants (and the run-status UPDATE) that function needs, each limited by
--                                  RLS to the request identity. It inherits trace_reference_reader (read-only) to count
--                                  the reference pool for the snapshot.
--   trace_plan.record_transition_plan(jsonb)
--                                  SECURITY DEFINER, empty search_path, EXECUTE for trace_app_user only (never
--                                  authenticated, so no Data API request can call it). One call = one transaction: it
--                                  verifies the caller owns the profile, records a reference snapshot (counts and settings
--                                  only, computed here, never supplied), inserts the run, its routes, steps, knowledge
--                                  evidence and actions, and marks the run completed. Scores are never accepted
--                                  (score_breakdown stays {}), reference profile ids are never accepted, and a
--                                  profile-led route always carries supporting_profile_count 0.
--   Immutability                   A completed (or failed) run cannot be updated, and nothing can be added to or changed
--                                  in its routes, steps, evidence or actions (TR027). Deletion is unchanged: owners may
--                                  delete their own runs, and account or profile deletion cascades (privacy retention).
--   trace_pathway_actions          The four-stage actions of a route (D28), owner-readable.
--
-- An empty reference snapshot (no eligible real profiles) is legitimate and recorded as such; nothing synthetic is
-- ever substituted. Run inputs are recorded for audit and reproduction: profile version, assessment with taxonomy
-- version, knowledge pack, source inventory and source verification states, engine version, catalogue, template and
-- eligibility-rule versions, reference snapshot and privacy state.
-- Requires PostgreSQL 16+ (GRANT ... WITH INHERIT / SET options).

do $$
begin
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'trace_plan_writer') then
    create role trace_plan_writer nologin inherit nosuperuser nocreatedb nocreaterole nobypassrls noreplication;
  end if;
end
$$;
alter role trace_plan_writer nologin inherit nocreatedb nocreaterole nobypassrls;

create schema if not exists trace_plan;
revoke all on schema trace_plan from public;

-- ------------------------------------------------------------------ additive columns: runs
alter table public.trace_pathway_runs
  add column plan_kind                 text check (plan_kind = 'transition_plan'),
  add column engine_version            text,
  add column profile_version           text,
  add column knowledge_pack            text,
  add column source_inventory          text,
  add column taxonomy_version          integer,
  add column bridge_catalogue_version  text,
  add column action_template_version   text,
  add column eligibility_rules_version text,
  add column assessment                jsonb,
  add column eligibility               jsonb,
  add column source_versions           jsonb,
  add column privacy_state             jsonb,
  add column decision                  jsonb,
  add column completed_at              timestamptz;
alter table public.trace_pathway_runs
  add constraint pathway_runs_plan_audit_complete check (
    plan_kind is null or status <> 'completed' or (
      engine_version is not null and profile_version is not null and knowledge_pack is not null and source_inventory is not null
      and taxonomy_version is not null and bridge_catalogue_version is not null and action_template_version is not null
      and eligibility_rules_version is not null and assessment is not null and eligibility is not null and source_versions is not null
      and privacy_state is not null and decision is not null and completed_at is not null));

-- ------------------------------------------------------------------ additive columns: routes
alter table public.trace_pathway_recommendations
  add column route_basis            text check (route_basis in ('profile_led', 'observed')),
  add column route_kind             text check (route_kind in ('direct', 'bridge', 'observed')),
  add column primary_status         text check (primary_status in ('primary', 'tied_primary', 'alternative', 'not_recommended', 'not_evaluated')),
  add column bridge_role_id         text references public.trace_role_taxonomy (role_id),
  add column explanation            text,
  add column feasibility            jsonb not null default '{}',
  add column tradeoffs              jsonb not null default '[]',
  add column evidence_breadth_label text;
alter table public.trace_pathway_recommendations
  add constraint recommendations_plan_fields_together check (
    (route_basis is null) = (route_kind is null) and (route_basis is null) = (primary_status is null)),
  add constraint recommendations_plan_no_scores check (route_basis is null or score_breakdown = '{}'::jsonb),
  add constraint recommendations_profile_led_no_supporters check (
    route_basis is distinct from 'profile_led' or (supporting_profile_count = 0 and dated_profile_count = 0
      and evidence_class in ('insufficient', 'synthetic_test_only'))),
  add constraint recommendations_basis_kind check (
    route_basis is null or (route_basis = 'observed') = (route_kind = 'observed')),
  add constraint recommendations_bridge_role check ((route_kind = 'bridge') = (bridge_role_id is not null));

-- ------------------------------------------------------------------ actions (D28)
create table public.trace_pathway_actions (
  action_id         uuid primary key default gen_random_uuid(),
  recommendation_id uuid not null,
  owner_user_id     uuid not null references auth.users (id) on delete cascade,
  data_mode         public.trace_data_mode not null,
  sequence          integer not null check (sequence >= 1),
  stage             smallint not null check (stage between 0 and 4),
  kind              text not null check (kind in ('resolve', 'build', 'demonstrate', 'reposition', 'apply')),
  template_version  text not null,
  template_key      text not null,
  requirement_key   text,
  objective         text not null,
  action_text       text not null,
  why_for_you       text not null,
  deliverable       text not null,
  where_options     text[] not null,
  source_keys       text[] not null default '{}',
  record_refs       text[] not null default '{}',
  created_at        timestamptz not null default now(),
  unique (recommendation_id, sequence),
  foreign key (recommendation_id, owner_user_id, data_mode)
    references public.trace_pathway_recommendations (recommendation_id, owner_user_id, data_mode) on delete cascade,
  foreign key (template_version, template_key) references public.trace_action_templates (template_version, template_key),
  constraint pathway_actions_stage_kind check (
    (kind = 'resolve' and stage = 0) or (kind = 'build' and stage = 1) or (kind = 'demonstrate' and stage = 2)
    or (kind = 'reposition' and stage = 3) or (kind = 'apply' and stage = 4))
);
create index trace_pathway_actions_rec_idx on public.trace_pathway_actions (recommendation_id, owner_user_id, data_mode);
create index trace_pathway_actions_owner_idx on public.trace_pathway_actions (owner_user_id);
comment on table public.trace_pathway_actions is
  'D28/D30: the actions of one saved route, from versioned templates. Append-only; owner-readable.';

-- ------------------------------------------------------------------ immutability (TR027)
create or replace function trace_util.guard_pathway_run_immutable()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if old.status in ('completed', 'failed') then
    raise exception using errcode = 'TR027', message = 'a completed pathway run cannot be changed; create a new run';
  end if;
  if old.plan_kind is not null and ((to_jsonb(new) - 'status' - 'completed_at' - 'updated_at')
                                    is distinct from (to_jsonb(old) - 'status' - 'completed_at' - 'updated_at')) then
    raise exception using errcode = 'TR027', message = 'a transition-plan run changes only its status';
  end if;
  return new;
end;
$$;
create trigger guard_pathway_run_immutable
  before update on public.trace_pathway_runs
  for each row execute function trace_util.guard_pathway_run_immutable();

-- Children of a completed run: no inserts, no updates (deletes stay possible for owner and account deletion).
create or replace function trace_util.guard_pathway_child()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_status text;
begin
  if TG_TABLE_NAME = 'trace_pathway_recommendations' then
    select r.status into v_status from public.trace_pathway_runs r where r.pathway_run_id = new.pathway_run_id;
  else
    select r.status into v_status
      from public.trace_pathway_recommendations c join public.trace_pathway_runs r on r.pathway_run_id = c.pathway_run_id
     where c.recommendation_id = new.recommendation_id;
  end if;
  if TG_OP = 'UPDATE' and v_status is not null and old is distinct from new then
    if v_status in ('completed', 'failed') then
      raise exception using errcode = 'TR027', message = 'the routes, steps, evidence and actions of a completed run cannot be changed';
    end if;
  end if;
  if TG_OP = 'INSERT' and v_status in ('completed', 'failed') then
    raise exception using errcode = 'TR027', message = 'nothing can be added to a completed run';
  end if;
  return new;
end;
$$;
create trigger guard_pathway_child before insert or update on public.trace_pathway_recommendations
  for each row execute function trace_util.guard_pathway_child();
create trigger guard_pathway_child before insert or update on public.trace_pathway_route_steps
  for each row execute function trace_util.guard_pathway_child();
create trigger guard_pathway_child before insert or update on public.trace_pathway_evidence
  for each row execute function trace_util.guard_pathway_child();
create trigger guard_pathway_child before insert or update on public.trace_pathway_actions
  for each row execute function trace_util.guard_pathway_child();

-- ------------------------------------------------------------------ the writer function
create or replace function trace_plan.record_transition_plan(p_plan jsonb)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid       uuid := trace_review.request_uid();
  v_profile   uuid;
  v_mode      public.trace_data_mode;
  v_run       uuid := gen_random_uuid();
  v_snapshot  uuid := gen_random_uuid();
  v_eligible  integer;
  v_moves     integer;
  v_minimum   numeric;
  v_settings  jsonb;
  v_rec       jsonb;
  v_rec_ord   integer;
  v_rec_id    uuid;
  v_basis     text;
  v_class     public.trace_evidence_class;
  v_count     integer;
  v_item      jsonb;
  v_ord       integer;
  v_key       text;
begin
  if v_uid is null then
    raise exception using errcode = 'TR004', message = 'no request identity';
  end if;
  if p_plan is null or jsonb_typeof(p_plan) <> 'object' or jsonb_typeof(p_plan -> 'routes') <> 'array'
     or jsonb_array_length(p_plan -> 'routes') = 0 then
    raise exception using errcode = 'TR028', message = 'a plan has at least one route';
  end if;
  foreach v_key in array array['candidate_profile_id', 'data_mode', 'target_role_id', 'engine_version', 'profile_version',
                               'knowledge_pack', 'source_inventory', 'bridge_catalogue_version', 'action_template_version',
                               'eligibility_rules_version'] loop
    if coalesce(btrim(p_plan ->> v_key), '') = '' then
      raise exception using errcode = 'TR028', message = format('missing %s', v_key);
    end if;
  end loop;
  foreach v_key in array array['assessment', 'eligibility', 'source_versions', 'decision', 'privacy_state'] loop
    if jsonb_typeof(p_plan -> v_key) is distinct from 'object' then
      raise exception using errcode = 'TR028', message = format('missing %s', v_key);
    end if;
  end loop;
  if jsonb_typeof(p_plan -> 'taxonomy_version') <> 'number' then
    raise exception using errcode = 'TR028', message = 'missing taxonomy_version';
  end if;
  v_profile := (p_plan ->> 'candidate_profile_id')::uuid;
  if (p_plan ->> 'data_mode') not in ('empirical', 'synthetic_test') then
    raise exception using errcode = 'TR028', message = 'a plan reads exactly one reference cohort: empirical or synthetic_test';
  end if;
  v_mode := (p_plan ->> 'data_mode')::public.trace_data_mode;
  perform 1 from public.trace_candidate_profiles p where p.candidate_profile_id = v_profile and p.owner_user_id = v_uid;
  if not found then
    raise exception using errcode = 'TR005', message = 'profile not found';
  end if;

  -- Reference snapshot: counts and settings only, computed here for the run's one cohort. Zero is a legitimate value.
  select count(*) into v_eligible from trace_ref.v_mode_profiles m where m.data_mode = v_mode;
  select count(*) into v_moves
    from trace_ref.v_episode_transitions t
    join trace_ref.v_mode_profiles m on m.profile_id = t.profile_id and m.data_mode = v_mode
   where t.is_pathway_move and t.transition_status = 'confirmed';
  select coalesce(jsonb_object_agg(s.setting_key, s.value_numeric), '{}'::jsonb) into v_settings from trace_ref.analytics_settings s;
  v_minimum := (v_settings ->> 'min_profiles_limited')::numeric;
  insert into trace_ref.reference_snapshots (snapshot_id, data_mode, settings, eligible_profile_count, confirmed_transition_count, notes)
  values (v_snapshot, v_mode, v_settings, v_eligible, v_moves, 'transition plan run: counts and settings only');

  insert into public.trace_pathway_runs (
    pathway_run_id, owner_user_id, candidate_profile_id, data_mode, target_role_id, constraints_json, reference_snapshot_id, status,
    plan_kind, engine_version, profile_version, knowledge_pack, source_inventory, taxonomy_version, bridge_catalogue_version,
    action_template_version, eligibility_rules_version, assessment, eligibility, source_versions, privacy_state, decision)
  values (
    v_run, v_uid, v_profile, v_mode, p_plan ->> 'target_role_id', coalesce(p_plan -> 'constraints', '{}'::jsonb), v_snapshot, 'running',
    'transition_plan', p_plan ->> 'engine_version', p_plan ->> 'profile_version', p_plan ->> 'knowledge_pack', p_plan ->> 'source_inventory',
    (p_plan ->> 'taxonomy_version')::integer, p_plan ->> 'bridge_catalogue_version', p_plan ->> 'action_template_version',
    p_plan ->> 'eligibility_rules_version', p_plan -> 'assessment', p_plan -> 'eligibility', p_plan -> 'source_versions',
    (p_plan -> 'privacy_state') || jsonb_build_object(
      'reference_pool', case when v_eligible = 0 then 'empty' when v_minimum is null or v_eligible < v_minimum then 'below_minimum'
                             else 'at_or_above_minimum' end,
      'reference_data_mode', v_mode::text),
    p_plan -> 'decision');

  for v_rec, v_rec_ord in select value, ordinality from jsonb_array_elements(p_plan -> 'routes') with ordinality loop
    v_basis := v_rec ->> 'route_basis';
    if v_basis is null or v_basis not in ('profile_led', 'observed') then
      raise exception using errcode = 'TR028', message = 'each route is profile_led or observed';
    end if;
    if v_basis = 'profile_led' then
      v_class := case when v_mode = 'empirical' then 'insufficient' else 'synthetic_test_only' end::public.trace_evidence_class;
      v_count := 0;                                   -- a profile-led route has no supporting reference profiles
    else
      v_class := case when v_mode = 'empirical' then (v_rec ->> 'evidence_class') else 'synthetic_test_only' end::public.trace_evidence_class;
      v_count := coalesce((v_rec ->> 'supporting_profile_count')::integer, 0);
    end if;
    v_rec_id := gen_random_uuid();
    insert into public.trace_pathway_recommendations (
      recommendation_id, pathway_run_id, owner_user_id, data_mode, rank, recommendation_reason, score_breakdown, evidence_class,
      supporting_profile_count, limitations, route_basis, route_kind, primary_status, bridge_role_id, explanation, feasibility, tradeoffs,
      evidence_breadth_label)
    values (
      v_rec_id, v_run, v_uid, v_mode, v_rec_ord, v_rec ->> 'reason', '{}'::jsonb, v_class, v_count,
      coalesce(array(select jsonb_array_elements_text(v_rec -> 'limitations')), '{}'), v_basis, v_rec ->> 'route_kind',
      v_rec ->> 'primary_status', v_rec ->> 'bridge_role_id', v_rec ->> 'explanation', coalesce(v_rec -> 'feasibility', '{}'::jsonb),
      coalesce(v_rec -> 'tradeoffs', '[]'::jsonb), v_rec ->> 'evidence_breadth_label');
    for v_key, v_ord in select value, ordinality from jsonb_array_elements_text(coalesce(v_rec -> 'steps', '[]'::jsonb)) with ordinality loop
      insert into public.trace_pathway_route_steps (recommendation_id, owner_user_id, step_index, role_id)
      values (v_rec_id, v_uid, v_ord, v_key);
    end loop;
    for v_item in select value from jsonb_array_elements(coalesce(v_rec -> 'evidence', '[]'::jsonb)) loop
      if (v_item ->> 'evidence_type') not in ('role_requirement', 'observed_sequence', 'other') or v_item ? 'reference_profile_id' then
        raise exception using errcode = 'TR028', message = 'route evidence is knowledge or an observed sequence, never an individual profile';
      end if;
      insert into public.trace_pathway_evidence (recommendation_id, owner_user_id, data_mode, evidence_type, target_role_source_id, evidence_summary)
      values (v_rec_id, v_uid, v_mode, v_item ->> 'evidence_type', (v_item ->> 'target_role_source_id')::uuid, v_item ->> 'summary');
    end loop;
    for v_item, v_ord in select value, ordinality from jsonb_array_elements(coalesce(v_rec -> 'actions', '[]'::jsonb)) with ordinality loop
      insert into public.trace_pathway_actions (
        recommendation_id, owner_user_id, data_mode, sequence, stage, kind, template_version, template_key, requirement_key, objective,
        action_text, why_for_you, deliverable, where_options, source_keys, record_refs)
      values (
        v_rec_id, v_uid, v_mode, v_ord, (v_item ->> 'stage')::smallint, v_item ->> 'kind', p_plan ->> 'action_template_version',
        v_item ->> 'template_key', v_item ->> 'requirement_key', v_item ->> 'objective', v_item ->> 'action', v_item ->> 'why_for_you',
        v_item ->> 'deliverable', array(select jsonb_array_elements_text(v_item -> 'where')),
        coalesce(array(select jsonb_array_elements_text(v_item -> 'source_keys')), '{}'),
        coalesce(array(select jsonb_array_elements_text(v_item -> 'record_refs')), '{}'));
    end loop;
  end loop;

  update public.trace_pathway_runs set status = 'completed', completed_at = now()
   where pathway_run_id = v_run and owner_user_id = v_uid;
  return v_run;
end;
$$;

-- ------------------------------------------------------------------ privileges and RLS
alter table public.trace_pathway_actions enable row level security;
revoke all on public.trace_pathway_actions from public, anon, authenticated, service_role;
grant select on public.trace_pathway_actions to authenticated;
create policy "pathway actions owner select" on public.trace_pathway_actions
  for select to authenticated using (owner_user_id = (select auth.uid()));

grant usage on schema trace_plan to trace_app_user;
grant usage on schema trace_review to trace_plan_writer;
grant execute on function trace_review.request_uid() to trace_plan_writer;
grant trace_reference_reader to trace_plan_writer with inherit true, set false;

grant select (candidate_profile_id, owner_user_id) on public.trace_candidate_profiles to trace_plan_writer;
create policy "candidate profiles plan writer select" on public.trace_candidate_profiles
  for select to trace_plan_writer using (owner_user_id = (select trace_review.request_uid()));

grant insert on trace_ref.reference_snapshots to trace_plan_writer;
create policy "reference snapshots plan writer insert" on trace_ref.reference_snapshots
  for insert to trace_plan_writer with check (true);

grant insert (pathway_run_id, owner_user_id, candidate_profile_id, data_mode, target_role_id, constraints_json, reference_snapshot_id,
              status, plan_kind, engine_version, profile_version, knowledge_pack, source_inventory, taxonomy_version,
              bridge_catalogue_version, action_template_version, eligibility_rules_version, assessment, eligibility, source_versions,
              privacy_state, decision)
  on public.trace_pathway_runs to trace_plan_writer;
grant select (pathway_run_id, owner_user_id, status, plan_kind) on public.trace_pathway_runs to trace_plan_writer;
grant update (status, completed_at) on public.trace_pathway_runs to trace_plan_writer;
create policy "pathway runs plan writer insert" on public.trace_pathway_runs
  for insert to trace_plan_writer with check (owner_user_id = (select trace_review.request_uid()) and plan_kind = 'transition_plan');
create policy "pathway runs plan writer select" on public.trace_pathway_runs
  for select to trace_plan_writer using (owner_user_id = (select trace_review.request_uid()) and plan_kind = 'transition_plan');
create policy "pathway runs plan writer update" on public.trace_pathway_runs
  for update to trace_plan_writer using (owner_user_id = (select trace_review.request_uid()) and plan_kind = 'transition_plan' and status = 'running')
  with check (owner_user_id = (select trace_review.request_uid()) and status = 'completed');

grant insert (recommendation_id, pathway_run_id, owner_user_id, data_mode, rank, recommendation_reason, score_breakdown, evidence_class,
              supporting_profile_count, limitations, route_basis, route_kind, primary_status, bridge_role_id, explanation, feasibility,
              tradeoffs, evidence_breadth_label)
  on public.trace_pathway_recommendations to trace_plan_writer;
create policy "pathway recommendations plan writer insert" on public.trace_pathway_recommendations
  for insert to trace_plan_writer with check (owner_user_id = (select trace_review.request_uid()) and route_basis is not null);
grant insert (recommendation_id, owner_user_id, step_index, role_id) on public.trace_pathway_route_steps to trace_plan_writer;
create policy "pathway route steps plan writer insert" on public.trace_pathway_route_steps
  for insert to trace_plan_writer with check (owner_user_id = (select trace_review.request_uid()));
grant insert (recommendation_id, owner_user_id, data_mode, evidence_type, target_role_source_id, evidence_summary)
  on public.trace_pathway_evidence to trace_plan_writer;
create policy "pathway evidence plan writer insert" on public.trace_pathway_evidence
  for insert to trace_plan_writer with check (owner_user_id = (select trace_review.request_uid()) and reference_profile_id is null);
grant insert (recommendation_id, owner_user_id, data_mode, sequence, stage, kind, template_version, template_key, requirement_key,
              objective, action_text, why_for_you, deliverable, where_options, source_keys, record_refs)
  on public.trace_pathway_actions to trace_plan_writer;
create policy "pathway actions plan writer insert" on public.trace_pathway_actions
  for insert to trace_plan_writer with check (owner_user_id = (select trace_review.request_uid()));

-- Privileges are set while postgres still owns the function (they move with ownership); then ownership is transferred
-- the same way as the review functions (M2): postgres may SET ROLE to the writer only for the transfer.
revoke all on function trace_plan.record_transition_plan(jsonb) from public, anon, authenticated, service_role;
grant execute on function trace_plan.record_transition_plan(jsonb) to trace_app_user;
grant trace_plan_writer to postgres with set true, inherit false;
grant create on schema trace_plan to trace_plan_writer;
alter function trace_plan.record_transition_plan(jsonb) owner to trace_plan_writer;
revoke create on schema trace_plan from trace_plan_writer;
revoke trace_plan_writer from postgres;

revoke all on function trace_util.guard_pathway_run_immutable(), trace_util.guard_pathway_child()
  from public, anon, authenticated, service_role;

do $$
begin
  if pg_catalog.pg_has_role('postgres', 'trace_plan_writer', 'SET') or pg_catalog.pg_has_role('postgres', 'trace_plan_writer', 'USAGE') then
    raise exception 'postgres must not be able to act as trace_plan_writer';
  end if;
  if exists (select 1 from pg_catalog.pg_auth_members m
              where m.roleid = 'trace_plan_writer'::regrole and (m.set_option or m.inherit_option)) then
    raise exception 'trace_plan_writer must have no members that can use it';
  end if;
  if has_function_privilege('anon', 'trace_plan.record_transition_plan(jsonb)', 'execute')
     or has_function_privilege('authenticated', 'trace_plan.record_transition_plan(jsonb)', 'execute')
     or has_function_privilege('service_role', 'trace_plan.record_transition_plan(jsonb)', 'execute')
     or not has_function_privilege('trace_app_user', 'trace_plan.record_transition_plan(jsonb)', 'execute') then
    raise exception 'record_transition_plan must be executable by trace_app_user only';
  end if;
end
$$;
