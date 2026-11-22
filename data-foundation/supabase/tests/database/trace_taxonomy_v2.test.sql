-- pgTAP tests for responsibility taxonomy v2, versioned classifications (v2.18.0), area conditions (v2.19.0) and pm-v1.1 (v2.20.0).
-- Run on a LOCAL Supabase stack only:  supabase test db
-- Synthetic test users and records only; everything runs in a transaction that is rolled back.

begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(32);

-- ------------------------------------------------------------------ 1. taxonomy
select is((select count(*)::int from trace_responsibility_areas), 20, '20 responsibility areas');
select is((select string_agg(responsibility_area, ',' order by responsibility_area) from trace_responsibility_areas where introduced_in_version = 2),
          'ai_product_work,business_case_economics,data_governance_quality,executive_communication', 'four areas introduced in version 2');
select is((select count(*)::int from trace_responsibility_areas where description is null or cardinality(examples) < 2), 0,
          'every area has a definition and examples');
select is((select string_agg(responsibility_area || '.' || q, ',' order by responsibility_area)
             from trace_responsibility_areas, jsonb_object_keys(qualifiers) q),
          'business_case_economics.tracks_outcomes,data_analysis.outcome_metrics_influence,delivery_execution.cross_team,people_leadership.coaching_mentoring,strategy_portfolio.multi_product_scope',
          'five scope questions (v2.24.0 adds the D35 outcome-metrics question)');
select is((select count(*)::int from trace_responsibility_areas where introduced_in_version = 1 and definition_version <> 1), 0,
          'existing areas keep definition version 1 (a scope question carries its own version)');
select is(trace_util.current_taxonomy_version(), 3, 'the current taxonomy version is 3 (v2.24.0, D35)');

-- ------------------------------------------------------------------ fixtures
insert into auth.users (id, email) values ('a9000000-0000-4000-8000-000000000001', 'pgtap-tax-a@example.invalid'),
                                         ('b9000000-0000-4000-8000-000000000002', 'pgtap-tax-b@example.invalid');
insert into trace_candidate_profiles (candidate_profile_id, owner_user_id)
values ('a9000000-0000-4000-8000-0000000000aa', 'a9000000-0000-4000-8000-000000000001'),
       ('b9000000-0000-4000-8000-0000000000bb', 'b9000000-0000-4000-8000-000000000002');
insert into trace_candidate_evidence (candidate_evidence_id, owner_user_id, candidate_profile_id, evidence_type, evidence_text, review_status, information_origin)
values ('a9000000-0000-4000-8000-000000000201', 'a9000000-0000-4000-8000-000000000001', 'a9000000-0000-4000-8000-0000000000aa', 'responsibility', 'Mentored two analysts', 'user_confirmed', 'user_stated'),
       ('a9000000-0000-4000-8000-000000000202', 'a9000000-0000-4000-8000-000000000001', 'a9000000-0000-4000-8000-0000000000aa', 'responsibility', 'Organised the team offsite', 'pending_review', 'user_stated'),
       ('a9000000-0000-4000-8000-000000000203', 'a9000000-0000-4000-8000-000000000001', 'a9000000-0000-4000-8000-0000000000aa', 'responsibility', 'Rejected bullet', 'rejected', 'user_stated'),
       ('a9000000-0000-4000-8000-000000000204', 'a9000000-0000-4000-8000-000000000001', 'a9000000-0000-4000-8000-0000000000aa', 'responsibility', 'Old classification', 'user_confirmed', 'user_stated'),
       ('b9000000-0000-4000-8000-000000000301', 'b9000000-0000-4000-8000-000000000002', 'b9000000-0000-4000-8000-0000000000bb', 'responsibility', 'Other user', 'user_confirmed', 'user_stated');

-- ------------------------------------------------------------------ 2. classify (as the owner role)
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a9000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select is(trace_review.classify_evidence_v2('a9000000-0000-4000-8000-0000000000aa', 'a9000000-0000-4000-8000-000000000201',
          array['people_leadership'], false, '{"people_leadership": {"coaching_mentoring": true}}', 'Mentored two analysts'),
          array['people_leadership'], 'areas with a scope answer');
select is((select area_qualifiers::text || '|' || area_classification_taxonomy_version || '|' || (area_classified_at is not null)::text
             from trace_candidate_evidence where candidate_evidence_id = 'a9000000-0000-4000-8000-000000000201'),
          '{"people_leadership": {"coaching_mentoring": true}}|3|true', 'answers, taxonomy version and time are recorded');
select lives_ok($$ select trace_review.classify_evidence_v2('a9000000-0000-4000-8000-0000000000aa', 'a9000000-0000-4000-8000-000000000201',
                   array['people_leadership', 'delivery_execution'], false, '{}', 'Mentored two analysts') $$,
                'scope questions may be left unanswered (stored as absent, never as no)');
select is((select area_qualifiers::text from trace_candidate_evidence where candidate_evidence_id = 'a9000000-0000-4000-8000-000000000201'),
          '{}', 'an unanswered question is absent');
select is(trace_review.classify_evidence_v2('a9000000-0000-4000-8000-0000000000aa', 'a9000000-0000-4000-8000-000000000202',
          null, true, null, 'Organised the team offsite'), '{}'::text[], '"None of these areas" (a pending draft may be classified)');
select is((select area_classification_none::text || '|' || area_classification_basis || '|' || area_classified_text
             from trace_candidate_evidence where candidate_evidence_id = 'a9000000-0000-4000-8000-000000000202'),
          'true|candidate_selected|Organised the team offsite', '"None" is a recorded, user-selected classification');
update trace_candidate_evidence set evidence_text = 'Organised the team offsite and budget'
 where candidate_evidence_id = 'a9000000-0000-4000-8000-000000000202';
select ok((select area_classification_is_stale from trace_candidate_evidence where candidate_evidence_id = 'a9000000-0000-4000-8000-000000000202'),
          'editing the text makes a "None" classification stale too');
select throws_ok($$ select trace_review.classify_evidence_v2('a9000000-0000-4000-8000-0000000000aa', 'a9000000-0000-4000-8000-000000000201',
                    array['people_leadership'], true, '{}', 'Mentored two analysts') $$, 'TR006', null, '"None" cannot be combined with areas');
select throws_ok($$ select trace_review.classify_evidence_v2('a9000000-0000-4000-8000-0000000000aa', 'a9000000-0000-4000-8000-000000000201',
                    '{}', false, '{}', 'Mentored two analysts') $$, 'TR006', null, 'an empty choice is not a classification');
select throws_ok($$ select trace_review.classify_evidence_v2('a9000000-0000-4000-8000-0000000000aa', 'a9000000-0000-4000-8000-000000000201',
                    array['people_leadership'], false, '{"people_leadership": {"no_such_question": true}}', 'Mentored two analysts') $$,
                 'TR006', null, 'only defined scope questions');
select throws_ok($$ select trace_review.classify_evidence_v2('a9000000-0000-4000-8000-0000000000aa', 'a9000000-0000-4000-8000-000000000201',
                    array['data_analysis'], false, '{"people_leadership": {"coaching_mentoring": true}}', 'Mentored two analysts') $$,
                 'TR006', null, 'answers only for chosen areas');
select throws_ok($$ select trace_review.classify_evidence_v2('a9000000-0000-4000-8000-0000000000aa', 'a9000000-0000-4000-8000-000000000201',
                    array['people_leadership'], false, '{"people_leadership": {"coaching_mentoring": "yes"}}', 'Mentored two analysts') $$,
                 'TR006', null, 'answers are yes or no');
select throws_ok($$ select trace_review.classify_evidence_v2('a9000000-0000-4000-8000-0000000000aa', 'a9000000-0000-4000-8000-000000000203',
                    array['data_analysis'], false, '{}', 'Rejected bullet') $$, 'TR001', null, 'rejected evidence is not classified');
select throws_ok($$ select trace_review.classify_evidence_v2('a9000000-0000-4000-8000-0000000000aa', 'a9000000-0000-4000-8000-000000000201',
                    array['data_analysis'], false, '{}', 'Something else') $$, 'TR018', null, 'only the text the user saw');
select throws_ok($$ select trace_review.classify_evidence_v2('b9000000-0000-4000-8000-0000000000bb', 'b9000000-0000-4000-8000-000000000301',
                    array['data_analysis'], false, '{}', 'Other user') $$, 'TR005', null, 'never another user''s evidence');
select throws_ok($$ update trace_candidate_evidence set area_classification_taxonomy_version = 9
                    where candidate_evidence_id = 'a9000000-0000-4000-8000-000000000201' $$, '42501', null,
                 'the owner role cannot write classification columns directly');
select lives_ok($$ select trace_review.clear_evidence_classification('a9000000-0000-4000-8000-0000000000aa', 'a9000000-0000-4000-8000-000000000201') $$,
                'a classification can be cleared');
select is((select (area_classification is null and not area_classification_none and area_qualifiers = '{}' and area_classification_taxonomy_version is null)::text
             from trace_candidate_evidence where candidate_evidence_id = 'a9000000-0000-4000-8000-000000000201'), 'true',
          'cleared: back to unclassified');
reset role;

-- ------------------------------------------------------------------ 3. old classifications are kept, unversioned
update trace_candidate_evidence set area_classification = array['data_analysis'], area_classification_basis = 'candidate_selected',
       area_classified_text = 'Old classification' where candidate_evidence_id = 'a9000000-0000-4000-8000-000000000204';
select is((select coalesce(area_classification_taxonomy_version::text, 'none') from trace_candidate_evidence
            where candidate_evidence_id = 'a9000000-0000-4000-8000-000000000204'), 'none',
          'a pre-versioning classification keeps no version (read as 1) and is not rewritten');
select throws_ok($$ update trace_candidate_evidence set area_classification_none = true
                    where candidate_evidence_id = 'a9000000-0000-4000-8000-000000000204' $$, '23514', null, '"None" never coexists with areas');

-- ------------------------------------------------------------------ 4. pm-v1.1
select is((select count(*)::int from trace_capability_requirements where knowledge_pack = 'pm-v1.1'), 17, 'pm-v1.1 has 17 requirements');
select is((select count(*)::int from trace_requirement_source_evidence e join trace_capability_requirements r using (capability_requirement_id)
            where r.knowledge_pack = 'pm-v1.1'), 129, 'with the same 129 source links (pm-v1 inventory)');
select is((select string_agg(requirement_key || '=' || area_conditions::text, ' ' order by requirement_key) from trace_capability_requirements
            where knowledge_pack = 'pm-v1.1' and area_conditions <> '{}'),
          'C4={"business_case_economics": "tracks_outcomes"} L1={"strategy_portfolio": "multi_product_scope"} '
          'L2={"people_leadership": "coaching_mentoring"} L4={"delivery_execution": "cross_team"}', 'evidence-specific conditions');
select is((select count(*)::int from trace_capability_requirements where knowledge_pack = 'pm-v1' and area_conditions <> '{}'), 0,
          'pm-v1 is unchanged (kept for audit)');
select throws_ok($$ update trace_capability_requirements set area_conditions = '{"people_leadership": "cross_team"}'
                    where knowledge_pack = 'pm-v1.1' and requirement_key = 'L2' $$, 'TR025', null, 'a condition names a question of that area');
select throws_ok($$ update trace_capability_requirements set area_conditions = '{"technical_engineering": "cross_team"}'
                    where knowledge_pack = 'pm-v1.1' and requirement_key = 'L2' $$, 'TR025', null, 'a condition names a mapped area');

select * from finish();
rollback;
