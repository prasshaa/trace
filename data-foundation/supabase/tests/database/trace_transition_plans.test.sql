-- pgTAP tests for transition-planning knowledge (v2.21.0, v2.22.0) and the append-only plan write path (v2.23.0).
-- Run on a LOCAL Supabase stack only:  supabase test db
-- Synthetic test users and records only; everything runs in a transaction that is rolled back.

begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(48);

-- ------------------------------------------------------------------ 1. D27 bridge catalogue
select is((select string_agg(target_role_id || '>' || bridge_role_id, ',' order by target_role_id, bridge_role_id)
             from trace_bridge_roles where catalogue_version = 'pm-bridges-v1'),
          'PM-PM>PM-APM,PM-PM>ROLE-OWNER,PM-PM>ROLE-PAN,PM-SPM>PM-PM', 'four curated bridges (Product Analyst, APM, Product Owner; PM before Senior PM)');
select is((select count(*)::int from trace_bridge_roles where cardinality(source_keys) = 0 or cardinality(responsibilities) = 0), 0,
          'every bridge has responsibilities and sources');
select is((select count(*)::int from trace_target_role_sources where knowledge_pack = 'pm-bridges-v1'), 2, 'two new role-level sources');
select throws_ok($$ insert into trace_bridge_roles (catalogue_version, target_role_id, bridge_role_id, requirements_pack, responsibilities,
                      develops, develops_note, entry_basis, entry_requirements_stated, suitable_backgrounds, suitability_note, tradeoff_note, source_keys)
                    values ('t', 'PM-PM', 'ROLE-PGM', 'pm-v1.1', array['x'], array['C1'], 'n', 'open_entry', 'n', 'n', 'n', 'n', array['pm-v1:F1']) $$,
                 'TR026', null, 'Program Manager is never a bridge (D27)');
select throws_ok($$ insert into trace_bridge_roles (catalogue_version, target_role_id, bridge_role_id, requirements_pack, responsibilities,
                      develops, develops_note, entry_basis, entry_requirements_stated, suitable_backgrounds, suitability_note, tradeoff_note, source_keys)
                    values ('t', 'PM-PM', 'ROLE-BA', 'pm-v1.1', array['x'], array['Z9'], 'n', 'open_entry', 'n', 'n', 'n', 'n', array['pm-v1:F1']) $$,
                 'TR026', null, 'a bridge develops only known requirements');
select throws_ok($$ insert into trace_bridge_roles (catalogue_version, target_role_id, bridge_role_id, requirements_pack, responsibilities,
                      develops, develops_note, entry_basis, entry_requirements_stated, suitable_backgrounds, suitability_note, tradeoff_note, source_keys)
                    values ('t', 'PM-PM', 'ROLE-BA', 'pm-v1.1', array['x'], array['C1'], 'n', 'open_entry', 'n', 'n', 'n', 'n', array['nope:X']) $$,
                 'TR026', null, 'a bridge cites only known sources');

-- ------------------------------------------------------------------ 2. D28 action templates
select is((select count(*)::int from trace_action_templates where template_version = 'pm-actions-v1'), 49, '49 templates');
select is((select count(*)::int from trace_capability_requirements r where r.knowledge_pack = 'pm-v1.1'
             and (select count(*) from trace_action_templates t where t.template_version = 'pm-actions-v1'
                   and t.requirement_key = r.requirement_key and t.kind in ('build', 'demonstrate')) <> 2), 0,
          'every requirement has a build and a demonstrate template');
select is((select count(*)::int from trace_action_templates where template_version = 'pm-actions-v1' and stage = 4), 7,
          'stage 4 templates (always considered)');
select throws_ok($$ insert into trace_action_templates (template_version, template_key, requirements_pack, stage, kind, objective, action_text,
                      deliverable, where_options) values ('t', 'x', 'pm-v1.1', 1, 'apply', 'o', 'a', 'd', array['job_market']) $$,
                 '23514', null, 'stage and kind agree');
select throws_ok($$ insert into trace_action_templates (template_version, template_key, requirements_pack, stage, kind, objective, action_text,
                      deliverable, where_options) values ('t', 'x', 'pm-v1.1', 4, 'apply', 'o', 'a', 'd', array['a_course']) $$,
                 '23514', null, 'only the defined places');

-- ------------------------------------------------------------------ 3. D29 structured criteria
select is((select count(*)::int from trace_target_role_requirements where criteria_version = 'pm-eligibility-v1'), 18,
          'all 18 employer criteria are structured');
select is((select count(*)::int from trace_target_role_requirements where criterion is null), 0, 'no criterion is left unstructured');
select is((select criterion -> 'any' -> 1 -> 'all' -> 1 ->> 'type' from trace_target_role_requirements r
             join trace_target_role_sources s using (target_role_source_id) where s.source_key = 'pm-v1:J11'),
          'product_exposure', 'Target: an adjacent background counts only WITH product exposure');
select is((select excluded_role_families::text from trace_target_role_sources where source_key = 'pm-v1:J12'),
          '{consulting,project_program_management}', 'Sarvam J12: excluded backgrounds mapped to role families');
select throws_ok($$ update trace_target_role_requirements set criterion = '{"type": "title_contains", "text": "PM"}'
                    where criteria_version = 'pm-eligibility-v1' and requirement_text like '3+ years%' $$, 'TR026', null,
                 'no title-based criterion type exists');
select throws_ok($$ update trace_target_role_requirements set criterion = '{"type": "years_in_families", "families": ["nope"], "min": 1}'
                    where criteria_version = 'pm-eligibility-v1' and requirement_text like '3+ years%' $$, 'TR026', null,
                 'role families must exist');
select throws_ok($$ update trace_target_role_requirements set criterion = '{"type": "years_in_families", "families": ["product_management"], "min": 5, "max": 3}'
                    where criteria_version = 'pm-eligibility-v1' and requirement_text like '3+ years%' $$, 'TR026', null,
                 'a range is ordered');
select throws_ok($$ update trace_target_role_requirements set criterion = '{"any": [{"type": "total_years", "min": 1}]}'
                    where criteria_version = 'pm-eligibility-v1' and requirement_text like '3+ years%' $$, 'TR026', null,
                 'OR combines at least two criteria');
select throws_ok($$ update trace_target_role_sources set accepted_role_families = array['consulting'] where source_key = 'pm-v1:J5' $$,
                 '23514', null, 'accepted families only on a posting that accepts adjacent backgrounds');
select ok(has_table_privilege('authenticated', 'public.trace_bridge_roles', 'select')
          and not has_table_privilege('authenticated', 'public.trace_bridge_roles', 'insert')
          and not has_table_privilege('anon', 'public.trace_action_templates', 'select'), 'knowledge: read by signed-in users, written by none');

-- ------------------------------------------------------------------ 4. the writer (D30)
select ok((select not rolcanlogin and not rolbypassrls and not rolsuper from pg_roles where rolname = 'trace_plan_writer'),
          'trace_plan_writer: no login, no BYPASSRLS');
select is((select count(*)::int from pg_auth_members m where m.roleid = 'trace_plan_writer'::regrole and (m.set_option or m.inherit_option)), 0,
          'nobody can act as the writer');
select is((select pg_get_userbyid(proowner) || '|' || prosecdef::text from pg_proc where proname = 'record_transition_plan'),
          'trace_plan_writer|true', 'the function is owned by the writer, SECURITY DEFINER');
select ok(has_function_privilege('trace_app_user', 'trace_plan.record_transition_plan(jsonb)', 'execute')
          and not has_function_privilege('authenticated', 'trace_plan.record_transition_plan(jsonb)', 'execute')
          and not has_function_privilege('anon', 'trace_plan.record_transition_plan(jsonb)', 'execute')
          and not has_function_privilege('service_role', 'trace_plan.record_transition_plan(jsonb)', 'execute'),
          'EXECUTE for the backend owner role only (never the Data API roles)');
select ok(not has_table_privilege('trace_app_user', 'public.trace_pathway_runs', 'insert')
          and not has_table_privilege('trace_app_user', 'public.trace_pathway_actions', 'insert')
          and not has_table_privilege('authenticated', 'public.trace_pathway_recommendations', 'insert'),
          'no direct client or owner-role writes');

insert into auth.users (id, email) values ('a8000000-0000-4000-8000-000000000001', 'pgtap-plan-a@example.invalid'),
                                         ('b8000000-0000-4000-8000-000000000002', 'pgtap-plan-b@example.invalid');
insert into trace_candidate_profiles (candidate_profile_id, owner_user_id)
values ('a8000000-0000-4000-8000-0000000000aa', 'a8000000-0000-4000-8000-000000000001'),
       ('b8000000-0000-4000-8000-0000000000bb', 'b8000000-0000-4000-8000-000000000002');
create temporary table plan_payload (k text primary key, v jsonb);
grant select on plan_payload to trace_app_user;
insert into plan_payload values ('base', '{
  "candidate_profile_id": "a8000000-0000-4000-8000-0000000000aa", "data_mode": "synthetic_test", "target_role_id": "PM-PM",
  "engine_version": "test", "profile_version": "p1", "knowledge_pack": "pm-v1.1", "source_inventory": "pm-v1", "taxonomy_version": 2,
  "bridge_catalogue_version": "pm-bridges-v1", "action_template_version": "pm-actions-v1", "eligibility_rules_version": "pm-eligibility-v1",
  "assessment": {"support": "partial"}, "eligibility": {"postings": []}, "source_versions": {}, "decision": {"rule": "R4"}, "privacy_state": {},
  "routes": [
    {"route_basis": "profile_led", "route_kind": "direct", "primary_status": "primary", "steps": ["PM-PM"], "supporting_profile_count": 50,
     "score_breakdown": {"fit": 0.9},
     "actions": [{"stage": 4, "kind": "apply", "template_key": "apply.referrals", "objective": "o", "action": "a", "why_for_you": "w",
                  "deliverable": "d", "where": ["job_market"]}]},
    {"route_basis": "profile_led", "route_kind": "bridge", "primary_status": "alternative", "bridge_role_id": "PM-APM", "steps": ["PM-APM", "PM-PM"]}]}');

set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a8000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select isnt(trace_plan.record_transition_plan((select v from plan_payload where k = 'base')), null, 'the owner records a plan');
select isnt(trace_plan.record_transition_plan((select v || '{"data_mode": "empirical"}' from plan_payload where k = 'base')), null,
            'and one against the consented real cohort');
select throws_ok($$ select trace_plan.record_transition_plan((select v || '{"candidate_profile_id": "b8000000-0000-4000-8000-0000000000bb"}'
                    from plan_payload where k = 'base')) $$, 'TR005', null, 'never for another user''s profile');
select throws_ok($$ select trace_plan.record_transition_plan((select v - 'engine_version' from plan_payload where k = 'base')) $$,
                 'TR028', null, 'every audit field is required');
select throws_ok($$ select trace_plan.record_transition_plan((select v || '{"routes": []}' from plan_payload where k = 'base')) $$,
                 'TR028', null, 'a plan has a route');
select throws_ok($$ select trace_plan.record_transition_plan((select v || '{"data_mode": "synthetic_demo"}' from plan_payload where k = 'base')) $$,
                 'TR028', null, 'exactly one reference cohort: empirical or synthetic_test');
select throws_ok($$ select trace_plan.record_transition_plan((select v || '{"routes": [{"route_basis": "observed", "route_kind": "observed",
                    "primary_status": "not_evaluated", "evidence_class": "synthetic_test_only", "evidence": [{"evidence_type": "observed_sequence",
                    "reference_profile_id": "SYN-001", "summary": "x"}]}]}' from plan_payload where k = 'base')) $$,
                 'TR028', null, 'never an individual reference profile');
select throws_ok($$ select trace_plan.record_transition_plan(jsonb_set((select v from plan_payload where k = 'base'),
                    '{routes,0,actions,0,template_key}', '"no.such.template"')) $$, '23503', null, 'actions come from the template library');
select throws_ok($$ insert into public.trace_pathway_runs (owner_user_id, candidate_profile_id, data_mode, target_role_id)
                    values ('a8000000-0000-4000-8000-000000000001', 'a8000000-0000-4000-8000-0000000000aa', 'synthetic_test', 'PM-PM') $$,
                 '42501', null, 'the owner role cannot write runs directly');
select is((select count(*)::int from public.trace_pathway_runs where plan_kind = 'transition_plan'), 2, 'the owner reads their own runs');
select is((select count(*)::int from public.trace_pathway_actions), 2, 'and their actions');
select set_config('request.jwt.claims', '{"sub": "b8000000-0000-4000-8000-000000000002", "role": "authenticated"}', true);
select is((select count(*)::int from public.trace_pathway_runs) + (select count(*)::int from public.trace_pathway_actions), 0,
          'another user sees none of them');
reset role;

-- ------------------------------------------------------------------ 5. what was stored
select is((select string_agg(status || '|' || (completed_at is not null)::text || '|' || (reference_snapshot_id is not null)::text, ',')
             from public.trace_pathway_runs where owner_user_id = 'a8000000-0000-4000-8000-000000000001'),
          'completed|true|true,completed|true|true', 'runs are completed with a reference snapshot');
select is((select bool_and(supporting_profile_count = 0 and score_breakdown = '{}'::jsonb)
             from public.trace_pathway_recommendations where owner_user_id = 'a8000000-0000-4000-8000-000000000001'),
          true, 'supplied counts and scores are never stored: no supporters for profile-led routes, no scores');
select is((select s.eligible_profile_count from public.trace_pathway_runs r join trace_ref.reference_snapshots s on s.snapshot_id = r.reference_snapshot_id
            where r.owner_user_id = 'a8000000-0000-4000-8000-000000000001' and r.data_mode = 'empirical'),
          (select count(*)::int from trace_ref.v_mode_profiles where data_mode = 'empirical'),
          'the snapshot counts the cohort itself (an empty real pool is recorded as it is)');
select is((select r.privacy_state ->> 'reference_pool' || '|' || p.evidence_class::text from public.trace_pathway_runs r
             join public.trace_pathway_recommendations p using (pathway_run_id)
            where r.owner_user_id = 'a8000000-0000-4000-8000-000000000001' and r.data_mode = 'empirical' and p.route_kind = 'direct'),
          (select case when count(*) = 0 then 'empty' else 'at_or_above_minimum' end from trace_ref.v_mode_profiles where data_mode = 'empirical')
            || '|insufficient', 'the privacy state is computed, and a profile-led route is never empirical evidence');

-- ------------------------------------------------------------------ 6. immutability (TR027)
select throws_ok($$ update public.trace_pathway_runs set decision = '{}' where owner_user_id = 'a8000000-0000-4000-8000-000000000001' $$,
                 'TR027', null, 'a completed run cannot be changed');
select throws_ok($$ update public.trace_pathway_recommendations set explanation = 'x' where owner_user_id = 'a8000000-0000-4000-8000-000000000001' $$,
                 'TR027', null, 'nor its routes');
select throws_ok($$ update public.trace_pathway_actions set action_text = 'x' where owner_user_id = 'a8000000-0000-4000-8000-000000000001' $$,
                 'TR027', null, 'nor its actions');
select throws_ok($$ insert into public.trace_pathway_route_steps (recommendation_id, owner_user_id, step_index, role_id)
                    select recommendation_id, owner_user_id, 9, 'PM-PM' from public.trace_pathway_recommendations
                     where owner_user_id = 'a8000000-0000-4000-8000-000000000001' limit 1 $$, 'TR027', null, 'nothing is added to a completed run');

-- ------------------------------------------------------------------ 7. deletion stays possible (owner and account)
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a8000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
delete from public.trace_pathway_runs where data_mode = 'empirical';
reset role;
select is((select count(*)::int from public.trace_pathway_runs where owner_user_id = 'a8000000-0000-4000-8000-000000000001'), 1,
          'owners may delete their own runs');
delete from auth.users where id = 'a8000000-0000-4000-8000-000000000001';
select is((select count(*)::int from public.trace_pathway_runs where owner_user_id = 'a8000000-0000-4000-8000-000000000001')
          + (select count(*)::int from public.trace_pathway_actions where owner_user_id = 'a8000000-0000-4000-8000-000000000001'), 0,
          'account deletion removes runs and their actions');

select * from finish();
rollback;
