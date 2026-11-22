-- Compensation for migration 20261116210000_trace_snapshots_and_service_role (v2.27.0). NOT a migration.
-- Deploy a backend that does not write labels first. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261116210000 --status reverted
-- Effect: restores the v2.23.0 writer (one snapshot per run), the earlier service_role grants, and removes the
-- fingerprint columns. It REFUSES to run while any shared (fingerprinted) snapshot exists: no data is dropped.
begin;
do $$
begin
  if exists (select 1 from trace_ref.reference_snapshots where state_fingerprint is not null) then
    raise exception 'shared snapshots exist; they would lose their fingerprint. Not rolled back.';
  end if;
end
$$;
grant trace_plan_writer to postgres with set true, inherit false;
grant create on schema trace_plan to trace_plan_writer;
set role trace_plan_writer;
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

reset role;
revoke create on schema trace_plan from trace_plan_writer;
revoke trace_plan_writer from postgres;
drop policy "reference snapshots plan writer select" on trace_ref.reference_snapshots;
revoke select (snapshot_id, state_fingerprint) on trace_ref.reference_snapshots from trace_plan_writer;
revoke insert (labels) on public.trace_pathway_actions from trace_plan_writer;
drop index trace_ref.reference_snapshots_state_fingerprint_key;
alter table trace_ref.reference_snapshots drop constraint reference_snapshots_fingerprint_dated,
  drop column state_fingerprint, drop column first_observed_on;
grant insert, update, delete, truncate on public.trace_pathway_runs, public.trace_pathway_recommendations,
  public.trace_pathway_route_steps, public.trace_pathway_evidence, trace_ref.reference_snapshots to service_role;
commit;
