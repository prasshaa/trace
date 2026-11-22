-- pgTAP tests for the Phase 4 remediation: v2.24.0 structures, v2.25.0 pack pm-v1.2, v2.26.0 content v2, v2.27.0
-- consolidated snapshots and service_role remediation (D31-D38), v2.28.0 rules pm-eligibility-v3 (Phase 4 closure).
-- Run on a LOCAL Supabase stack only:  supabase test db
-- Synthetic test users and records only; everything runs in a transaction that is rolled back.

begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(44);

-- ------------------------------------------------------------------ 1. pm-v1.2 (D31, D35)
select is((select count(*)::int from trace_capability_requirements where knowledge_pack = 'pm-v1.2'), 17, 'pm-v1.2 has 17 requirements');
select is((select string_agg(requirement_key, ',' order by requirement_key) from trace_capability_requirements
            where knowledge_pack = 'pm-v1.2' and decision_rights), 'C2,C4,L1', 'decision-rights requirements: C2, C4, L1 (C1 excluded)');
select is((select count(*)::int from trace_capability_requirements where knowledge_pack = 'pm-v1.2' and decision_rights
             and cardinality(direct_skills) > 0), 0, 'a stated skill never demonstrates a decision-rights requirement');
select is((select area_conditions ->> 'data_analysis' || '|' || (area_condition_fallback ->> 'data_analysis') from trace_capability_requirements
            where knowledge_pack = 'pm-v1.2' and requirement_key = 'C4'), 'outcome_metrics_influence|transferable',
          'C4: analysis counts directly only with a Yes; with a No it is Transferable');
select is((select count(*)::int from trace_requirement_source_evidence e join trace_capability_requirements r using (capability_requirement_id)
            where r.knowledge_pack = 'pm-v1.2'), 129, 'the same 129 source links');
select is((select count(*)::int from trace_capability_requirements where knowledge_pack in ('pm-v1', 'pm-v1.1') and decision_rights), 0,
          'earlier packs are unchanged');
select throws_ok($$ update trace_capability_requirements set area_condition_fallback = '{"go_to_market_growth": "transferable"}'
                    where knowledge_pack = 'pm-v1.2' and requirement_key = 'C4' $$, 'TR025', null, 'a fallback only for a conditioned direct area');
select is((select qualifiers -> 'outcome_metrics_influence' ->> 'question' from trace_responsibility_areas where responsibility_area = 'data_analysis'),
          'Did you define or own product outcome metrics and use them to influence product decisions, rather than only analyse or report metrics?',
          'the approved D35 wording');

-- ------------------------------------------------------------------ 2. bridges v2 and the candidate pool (D32-D34)
select is((select string_agg(bridge_role_id || ':' || array_to_string(partly_develops, '+'), ',' order by bridge_role_id)
             from trace_bridge_roles where catalogue_version = 'pm-bridges-v2' and target_role_id = 'PM-PM'),
          'PM-APM:C2+C4+X5+L2,ROLE-OWNER:C2,ROLE-PAN:C1+X5', 'partial development as the sources support it (APM: C2 and C4 partly)');
select is((select may_be_primary::text || '|' || entry_evidence || '|' || progression_evidence from trace_bridge_roles
            where catalogue_version = 'pm-bridges-v2' and bridge_role_id = 'ROLE-OWNER'), 'false|hypothesis|hypothesis',
          'Product Owner: alternative only; entry and progression are hypotheses (D34)');
select is((select count(*)::int from trace_bridge_roles b, unnest(b.partly_develops) k where b.catalogue_version = 'pm-bridges-v2'
             and not (b.partial_development ? k)), 0, 'every partial requirement says what it develops, what remains and what to seek');
select throws_ok($$ update trace_bridge_roles set partial_development = partial_development - 'C2'
                    where catalogue_version = 'pm-bridges-v2' and bridge_role_id = 'PM-APM' $$, 'TR026', null,
                 'partial development cannot lose its explanation');
select is((select count(*)::int from trace_bridge_roles where catalogue_version = 'pm-bridges-v1'), 4, 'pm-bridges-v1 kept for audit');
select is((select string_agg(f1_role || '=' || mapping_confidence, ',' order by f1_role) from trace_digital_role_candidates),
          'Agile coach=unmapped,Business analyst=source_direct,Delivery manager=curated_hypothesis,Programme delivery manager=curated_hypothesis,Service owner=unmapped',
          'F1''s five listed roles, original names, mapping confidence (D33)');
select throws_ok($$ insert into trace_digital_role_candidates (catalogue_version, f1_role, trace_role_family, mapping_confidence, summary,
                      shared_skills, suitable_backgrounds, source_key) values ('t', 'x', 'consulting', 'unmapped', 's', '{}', 'b', 'pm-v1:F1') $$,
                 '23514', null, 'an unmapped role has no family');

-- ------------------------------------------------------------------ 3. templates v2
select is((select count(*)::int from trace_action_templates where template_version = 'pm-actions-v2'), 55, '55 templates');
select is((select count(*)::int from trace_action_templates where template_version = 'pm-actions-v2' and kind = 'build'
             and not ('learning_exercise' = any (labels))), 0, 'every build action is labelled a learning exercise');
select is((select count(*)::int from trace_action_templates where template_version = 'pm-actions-v2' and 'external_project' = any (where_options)
             and kind = 'demonstrate' and not ('personal_project_possible' = any (labels))), 0, 'personal projects are labelled');
select is((select count(*)::int from trace_action_templates where template_version = 'pm-actions-v2' and kind = 'reposition'
             and not ('factual_accuracy' = any (labels))), 0, 'repositioning keeps to confirmed records');
select is((select string_agg(template_key, ',' order by template_key) from trace_action_templates where template_version = 'pm-actions-v2'
            and template_key in ('apply.alternative_routes', 'apply.case_study', 'apply.check_criteria', 'apply.bridge_seek')),
          'apply.alternative_routes,apply.bridge_seek,apply.case_study,apply.check_criteria', 'the new stage 4 actions');
select throws_ok($$ insert into trace_action_templates (template_version, template_key, requirements_pack, stage, kind, objective, action_text,
                      deliverable, where_options, labels) values ('t', 'x', 'pm-v1.2', 4, 'apply', 'o', 'a', 'd', array['job_market'], array['guaranteed']) $$,
                 '23514', null, 'only defined labels');

-- ------------------------------------------------------------------ 4. employer rules v2 and education fields (D36)
select is((select count(*)::int from trace_employer_criterion_rules where rules_version = 'pm-eligibility-v2'), 18, 'all 18 criteria, v2');
select is((select count(*)::int from trace_target_role_requirements where criteria_version = 'pm-eligibility-v1'), 18,
          'v1 rules are kept, not overwritten');
select is((select v.rule -> 'types' from trace_employer_criterion_rules v join trace_target_role_requirements q using (requirement_id)
            where v.rules_version = 'pm-eligibility-v2' and q.requirement_text like 'B.E./B.Tech%'), '["BE", "BTech"]'::jsonb,
          'B.E./B.Tech by degree type');
select is((select count(*)::int from trace_employer_criterion_rules v, jsonb_path_query(v.rule, 'strict $.**.equivalent_families') e
            where v.rules_version = 'pm-eligibility-v2'), 10, 'PM tenure leaves list Product Owner and founder as possible equivalents');
select is((select count(*)::int from (select a from trace_education_fields, unnest(aliases) a group by a having count(*) > 1) d), 0,
          'an alias names exactly one field');
select throws_ok($$ insert into trace_employer_criterion_rules (rules_version, requirement_id, rule)
                    select 't', requirement_id, '{"type": "qualification", "fields": ["cs-ish"], "or_equivalent": true}'::jsonb
                      from trace_target_role_requirements limit 1 $$, 'TR026', null, 'only known education fields');

-- ------------------------------------------------------------------ 4b. rules v3 (Phase 4 closure, v2.28.0)
select is((select count(*)::int from trace_employer_criterion_rules where rules_version = 'pm-eligibility-v3'), 18, 'all 18 criteria, v3');
select is((select jsonb_path_query_first(v.rule, 'strict $.**.unconfirmed_roles') from trace_employer_criterion_rules v
             join trace_target_role_requirements q using (requirement_id) join trace_target_role_sources s using (target_role_source_id)
            where v.rules_version = 'pm-eligibility-v3' and s.source_key = 'pm-v1:J12'), '["PM-APM"]'::jsonb,
          'J12: a current Associate PM is not confirmed as "currently a product manager"');
select is((select count(*)::int from trace_employer_criterion_rules where rules_version = 'pm-eligibility-v3'
             and rule::text like '%unconfirmed_roles%'), 1, 'only J12 carries the APM rule (no global APM rule)');
select is((select v.rule -> 'before_families' from trace_employer_criterion_rules v join trace_target_role_requirements q using (requirement_id)
            where v.rules_version = 'pm-eligibility-v3' and q.requirement_text like 'A computer science or engineering degree%'),
          '["product_management"]'::jsonb, 'J3: only engineering experience before product counts (E8)');

-- ------------------------------------------------------------------ 5. service_role (D38)
select ok(not exists (select 1 from unnest(array['public.trace_pathway_runs', 'public.trace_pathway_recommendations',
                                                 'public.trace_pathway_route_steps', 'public.trace_pathway_evidence',
                                                 'public.trace_pathway_actions', 'trace_ref.reference_snapshots']) t,
                                    unnest(array['INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) p
                       where has_table_privilege('service_role', t, p)), 'service_role cannot write plan or snapshot tables');
select ok(has_table_privilege('service_role', 'public.trace_pathway_runs', 'SELECT'), 'service_role can still read for administration');
set local role service_role;
select throws_ok($$ insert into trace_ref.reference_snapshots (data_mode, settings, eligible_profile_count, confirmed_transition_count)
                    values ('empirical', '{}', 0, 0) $$, '42501', null, 'service_role: no snapshot insert');
select throws_ok($$ truncate public.trace_pathway_runs cascade $$, '42501', null, 'service_role: no truncate (which would skip row triggers)');
select throws_ok($$ delete from public.trace_pathway_runs $$, '42501', null, 'service_role: no delete');
reset role;

-- ------------------------------------------------------------------ 6. shared, dated snapshots (D37)
insert into auth.users (id, email) values ('a7000000-0000-4000-8000-000000000001', 'pgtap-rem-a@example.invalid'),
                                         ('b7000000-0000-4000-8000-000000000002', 'pgtap-rem-b@example.invalid');
insert into trace_candidate_profiles (candidate_profile_id, owner_user_id)
values ('a7000000-0000-4000-8000-0000000000aa', 'a7000000-0000-4000-8000-000000000001'),
       ('b7000000-0000-4000-8000-0000000000bb', 'b7000000-0000-4000-8000-000000000002');
create temporary table rem_counts as select count(*) filter (where state_fingerprint is null) as legacy from trace_ref.reference_snapshots;
create temporary table rem_payload (v jsonb);
grant select on rem_payload to trace_app_user;
insert into rem_payload values ('{"data_mode": "synthetic_test", "target_role_id": "PM-PM", "engine_version": "t", "profile_version": "p",
  "knowledge_pack": "pm-v1.2", "source_inventory": "pm-v1", "taxonomy_version": 3, "bridge_catalogue_version": "pm-bridges-v2",
  "action_template_version": "pm-actions-v2", "eligibility_rules_version": "pm-eligibility-v2", "assessment": {}, "eligibility": {},
  "source_versions": {}, "decision": {}, "privacy_state": {},
  "routes": [{"route_basis": "profile_led", "route_kind": "direct", "primary_status": "primary", "steps": ["PM-PM"],
              "actions": [{"stage": 1, "kind": "build", "template_key": "build.C1", "objective": "o", "action": "a", "why_for_you": "w",
                           "deliverable": "d", "where": ["self_directed_study"], "labels": ["learning_exercise"]}]}]}');
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a7000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select isnt(trace_plan.record_transition_plan((select v || '{"candidate_profile_id": "a7000000-0000-4000-8000-0000000000aa"}' from rem_payload)),
            null, 'user A records a plan');
select set_config('request.jwt.claims', '{"sub": "b7000000-0000-4000-8000-000000000002", "role": "authenticated"}', true);
select isnt(trace_plan.record_transition_plan((select v || '{"candidate_profile_id": "b7000000-0000-4000-8000-0000000000bb"}' from rem_payload)),
            null, 'user B records a plan');
select is((select count(*)::int from public.trace_pathway_actions where 'learning_exercise' = any (labels)), 1, 'action labels are stored');
reset role;
select is((select count(distinct reference_snapshot_id)::int from public.trace_pathway_runs
            where owner_user_id in ('a7000000-0000-4000-8000-000000000001', 'b7000000-0000-4000-8000-000000000002')), 1,
          'two users, one reference-pool state: one shared snapshot');
select is((select date_trunc('day', s.created_at at time zone 'utc') = (s.created_at at time zone 'utc') and s.first_observed_on is not null
             and s.state_fingerprint ~ '^[0-9a-f]{64}$'
             from trace_ref.reference_snapshots s join public.trace_pathway_runs r on r.reference_snapshot_id = s.snapshot_id
            where r.owner_user_id = 'a7000000-0000-4000-8000-000000000001'), true, 'date-level precision and a state fingerprint');
select is((select count(*)::int from trace_ref.reference_snapshots where state_fingerprint is null), (select legacy::int from rem_counts),
          'existing (unfingerprinted) snapshots are untouched');
select throws_ok($$ insert into trace_ref.reference_snapshots (data_mode, settings, eligible_profile_count, confirmed_transition_count,
                      state_fingerprint, first_observed_on) values ('empirical', '{}', 0, 0, 'not-a-hash', current_date) $$,
                 '23514', null, 'a fingerprint is a sha256');
delete from auth.users where id in ('a7000000-0000-4000-8000-000000000001', 'b7000000-0000-4000-8000-000000000002');
select is((select count(*)::int from public.trace_pathway_runs where owner_user_id in ('a7000000-0000-4000-8000-000000000001',
                                                                                     'b7000000-0000-4000-8000-000000000002')), 0,
          'account deletion removes the runs; the shared snapshot holds no link to either user');

select * from finish();
rollback;
