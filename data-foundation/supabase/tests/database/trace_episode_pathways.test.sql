-- pgTAP tests for the episode-aware pathway objects (v2.1.0, migration 20261111130000).
-- Run on a LOCAL Supabase stack only:  supabase test db
-- Requires the synthetic seed (supabase/seeds/synthetic_fixtures.sql) for the SYN-0xx checks.
-- Everything runs inside a transaction that is rolled back, so no rows persist.

begin;
create extension if not exists pgtap with schema extensions;
select plan(48);

-- ------------------------------------------------------------------ transient fixtures
insert into trace_ref.data_sources (source_id, dataset_class, source_type, source_label)
values ('SRC-PGTAP-EP', 'synthetic_test_fixture', 'synthetic_generator', 'pgTAP episode fixtures');

insert into trace_ref.profiles (profile_id, source_id, dataset_class, review_status, resume_as_of_year, resume_as_of_month, resume_as_of_precision)
select 'SYN-' || n, 'SRC-PGTAP-EP', 'synthetic_test_fixture', 'synthetic_curated', 2026, 11, 'month'
from unnest(array['911','912','913','914','915','916','917','918','919','920','921','922']) as n;

insert into trace_ref.employers (profile_id, employer_id)
select 'SYN-' || n, 'SYN-' || n || '-ORG-' || o
from unnest(array['911','912','913','914','915','916','917','918','919','920','921','922']) as n,
     unnest(array['A','B','C','F']) as o;

insert into trace_ref.employments (employment_id, profile_id, sequence_index, employer_id, title_raw_redacted, role_id,
  mapping_status, mapping_confidence, employment_type, primary_career_sequence, classification_note,
  start_year, start_month, start_precision, end_year, end_month, end_precision, is_current_as_stated)
values
  -- 911 role change, same employer
  ('SYN-911-EMP-01', 'SYN-911', 1, 'SYN-911-ORG-A', 'APM', 'PM-APM', 'confirmed', 1, 'full_time', true, null, 2019, 1, 'month', 2021, 6, 'month', false),
  ('SYN-911-EMP-02', 'SYN-911', 2, 'SYN-911-ORG-A', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2021, 7, 'month', null, null, 'ongoing', true),
  -- 912 destination role first, then same-role employer change
  ('SYN-912-EMP-01', 'SYN-912', 1, 'SYN-912-ORG-A', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2018, 1, 'month', 2021, 6, 'month', false),
  ('SYN-912-EMP-02', 'SYN-912', 2, 'SYN-912-ORG-B', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2021, 7, 'month', null, null, 'ongoing', true),
  -- 913 multiple same-role employer moves
  ('SYN-913-EMP-01', 'SYN-913', 1, 'SYN-913-ORG-A', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2016, 1, 'month', 2018, 12, 'month', false),
  ('SYN-913-EMP-02', 'SYN-913', 2, 'SYN-913-ORG-B', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2019, 1, 'month', 2021, 12, 'month', false),
  ('SYN-913-EMP-03', 'SYN-913', 3, 'SYN-913-ORG-C', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2022, 1, 'month', null, null, 'ongoing', true),
  -- 914 role and employer change
  ('SYN-914-EMP-01', 'SYN-914', 1, 'SYN-914-ORG-A', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2018, 1, 'month', 2021, 6, 'month', false),
  ('SYN-914-EMP-02', 'SYN-914', 2, 'SYN-914-ORG-B', 'Senior PM', 'PM-SPM', 'confirmed', 1, 'full_time', true, null, 2021, 7, 'month', null, null, 'ongoing', true),
  -- 915 single episode
  ('SYN-915-EMP-01', 'SYN-915', 1, 'SYN-915-ORG-A', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2019, 1, 'month', null, null, 'ongoing', true),
  -- 916 same role, same employer (continuation), then promotion
  ('SYN-916-EMP-01', 'SYN-916', 1, 'SYN-916-ORG-A', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2018, 1, 'month', 2019, 12, 'month', false),
  ('SYN-916-EMP-02', 'SYN-916', 2, 'SYN-916-ORG-A', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2020, 1, 'month', 2021, 12, 'month', false),
  ('SYN-916-EMP-03', 'SYN-916', 3, 'SYN-916-ORG-A', 'Senior PM', 'PM-SPM', 'confirmed', 1, 'full_time', true, null, 2022, 1, 'month', null, null, 'ongoing', true),
  -- 917 overlapping employer move
  ('SYN-917-EMP-01', 'SYN-917', 1, 'SYN-917-ORG-A', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2017, 6, 'month', 2021, 8, 'month', false),
  ('SYN-917-EMP-02', 'SYN-917', 2, 'SYN-917-ORG-B', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2021, 7, 'month', null, null, 'ongoing', true),
  -- 918 same-year, year-only boundary
  ('SYN-918-EMP-01', 'SYN-918', 1, 'SYN-918-ORG-A', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2015, null, 'year', 2018, null, 'year', false),
  ('SYN-918-EMP-02', 'SYN-918', 2, 'SYN-918-ORG-B', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2018, null, 'year', null, null, 'ongoing', true),
  -- 919 parallel freelance work alongside the first job
  ('SYN-919-EMP-01', 'SYN-919', 1, 'SYN-919-ORG-A', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2017, 6, 'month', 2021, 6, 'month', false),
  ('SYN-919-EMP-02', 'SYN-919', 2, 'SYN-919-ORG-F', 'PM consultant', 'PM-PM', 'confirmed', 1, 'freelance', false, 'parallel consulting', 2020, 1, 'month', 2021, 6, 'month', false),
  ('SYN-919-EMP-03', 'SYN-919', 3, 'SYN-919-ORG-B', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2021, 7, 'month', null, null, 'ongoing', true),
  -- 920 same role, employer not stated
  ('SYN-920-EMP-01', 'SYN-920', 1, 'SYN-920-ORG-A', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2018, 1, 'month', 2020, 12, 'month', false),
  ('SYN-920-EMP-02', 'SYN-920', 2, null, 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2021, 1, 'month', null, null, 'ongoing', true),
  -- 921 unresolved intermediate episode
  ('SYN-921-EMP-01', 'SYN-921', 1, 'SYN-921-ORG-A', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2016, 7, 'month', 2019, 3, 'month', false),
  ('SYN-921-EMP-02', 'SYN-921', 2, 'SYN-921-ORG-B', 'Associate', 'ROLE-UNKNOWN', 'unresolved', 0.3, 'full_time', true, null, 2019, 4, 'month', 2020, 12, 'month', false),
  ('SYN-921-EMP-03', 'SYN-921', 3, 'SYN-921-ORG-C', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2021, 1, 'month', null, null, 'ongoing', true),
  -- 922 previous role still ongoing
  ('SYN-922-EMP-01', 'SYN-922', 1, 'SYN-922-ORG-A', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2017, 6, 'month', null, null, 'ongoing', true),
  ('SYN-922-EMP-02', 'SYN-922', 2, 'SYN-922-ORG-B', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', true, null, 2021, 7, 'month', null, null, 'ongoing', true);

-- ------------------------------------------------------------------ existing objects unchanged
select is((select role_path from trace_ref.v_career_paths where profile_id = 'SYN-912'),
          array['PM-PM'], 'v2.0.2 v_career_paths still merges a same-role employer move (unchanged)');
select is((select route from trace_ref.route_timeline_summary(array['PM-PM', 'PM-PM'], 'synthetic_test')),
          array['PM-PM'], 'v2.0.2 route_timeline_summary still collapses its input (unchanged; use the episode functions)');

-- ------------------------------------------------------------------ change types
select is((select change_type from trace_ref.v_episode_transitions where profile_id = 'SYN-911'), 'role_change', 'role change within one employer');
select is((select change_type from trace_ref.v_episode_transitions where profile_id = 'SYN-912'), 'employer_change', 'same role, different employer');
select is((select change_type from trace_ref.v_episode_transitions where profile_id = 'SYN-914'), 'role_and_employer_change', 'role and employer change');
select is((select change_type from trace_ref.v_episode_transitions where from_employment_id = 'SYN-916-EMP-01'), 'no_change', 'same role, same employer is no change');
select is((select change_type from trace_ref.v_episode_transitions where profile_id = 'SYN-920'), 'employer_unknown', 'same role with an unstated employer is employer_unknown');
select is((select is_pathway_move from trace_ref.v_episode_transitions where profile_id = 'SYN-920'), false, 'employer_unknown is not a pathway move');
select is((select is_pathway_move from trace_ref.v_episode_transitions where profile_id = 'SYN-912'), true, 'a confirmed employer change is a pathway move');

-- ------------------------------------------------------------------ episode paths
select is((select role_path from trace_ref.v_career_episode_paths where profile_id = 'SYN-912'),
          array['PM-PM', 'PM-PM'], 'same-role employer move keeps two episodes');
select is((select entry_change_types from trace_ref.v_career_episode_paths where profile_id = 'SYN-912'),
          array[null, 'employer_change']::text[], 'the second episode is entered by an employer change');
select is((select role_path from trace_ref.v_career_episode_paths where profile_id = 'SYN-913'),
          array['PM-PM', 'PM-PM', 'PM-PM'], 'multiple same-role employer moves keep every episode');
select is((select role_path from trace_ref.v_career_episode_paths where profile_id = 'SYN-915'),
          array['PM-PM'], 'a single episode is a one-step path');
select is((select role_path from trace_ref.v_career_episode_paths where profile_id = 'SYN-916'),
          array['PM-PM', 'PM-SPM'], 'same role at the same employer collapses into one episode stint');
select is((select role_path from trace_ref.v_career_episode_paths where profile_id = 'SYN-920'),
          array['PM-PM'], 'same role with an unknown employer is not split into a move');
select is((select max(cardinality(role_path))::int from trace_ref.v_career_episode_paths where profile_id = 'SYN-921'),
          1, 'an unresolved intermediate episode is never bridged');
select is((select role_path from trace_ref.v_career_episode_paths where profile_id = 'SYN-919'),
          array['PM-PM', 'PM-PM'], 'parallel work stays outside the episode chain');

-- ------------------------------------------------------------------ observations
select is((select array_agg(o.profile_id order by o.profile_id)
           from trace_ref.episode_route_observations(array['PM-PM', 'PM-PM'], 'synthetic_test') o
           where o.profile_id between 'SYN-911' and 'SYN-922'),
          array['SYN-912', 'SYN-913', 'SYN-919'],
          'PM -> PM matches only confirmed employer moves (not single, continuation, unknown employer, overlap, uncertain, ongoing or bridged)');
select is((select o.step_change_types from trace_ref.episode_route_observations(array['PM-PM', 'PM-PM', 'PM-PM'], 'synthetic_test') o
           where o.profile_id = 'SYN-913'),
          array['employer_change', 'employer_change'], 'a three-episode route reports each step''s change type');
select is((select o.route_months_min::text || '/' || o.is_precise::text
           from trace_ref.episode_route_observations(array['PM-PM', 'PM-PM'], 'synthetic_test') o where o.profile_id = 'SYN-912'),
          '42/true', 'route months and precision come from the episode starts');
select is((select array_agg(o.profile_id order by o.profile_id)
           from trace_ref.episode_route_observations(array['PM-PM', 'PM-PM'], 'synthetic_test') o
           where o.profile_id in ('SYN-018', 'SYN-019', 'SYN-025', 'SYN-026', 'SYN-027', 'SYN-029', 'SYN-030', 'SYN-032')),
          array['SYN-018', 'SYN-019', 'SYN-025', 'SYN-026', 'SYN-027', 'SYN-029', 'SYN-030'],
          'the seven previously missed PM -> PM fixtures are now observed');
select is((select count(*)::int from trace_ref.episode_route_observations(array['PM-DIR', 'PM-DIR'], 'synthetic_test') o
           where o.profile_id = 'SYN-032'),
          1, 'the previously missed Director -> Director fixture is now observed');

-- ------------------------------------------------------------------ integrity rules inherited
select is((select transition_status || '/' || change_type from trace_ref.v_episode_transitions where profile_id = 'SYN-917'),
          'unconfirmed/employer_change', 'an overlapping employer move stays unconfirmed');
select is((select timing_relation || '/' || transition_status from trace_ref.v_episode_transitions where profile_id = 'SYN-918'),
          'boundary_uncertain/unconfirmed', 'a same-year, year-only employer move stays unconfirmed');
select is((select timing_relation || '/' || transition_status from trace_ref.v_episode_transitions where profile_id = 'SYN-922'),
          'previous_role_ongoing/unconfirmed', 'a move after an ongoing role stays unconfirmed');

-- ------------------------------------------------------------------ invalid routes raise
select throws_ok($$select * from trace_ref.episode_route_observations(array['PM-PM'], 'synthetic_test')$$,
                 '22023', null, 'a one-step route is rejected (observations)');
select throws_ok($$select * from trace_ref.episode_route_timeline_summary(array['PM-PM'], 'synthetic_test')$$,
                 '22023', null, 'a one-step route is rejected (summary), not reported as zero observations');
select throws_ok($$select * from trace_ref.episode_route_timeline_summary(null, 'synthetic_test')$$,
                 '22023', null, 'a null route is rejected');
select throws_ok($$select * from trace_ref.episode_route_timeline_summary('{}'::text[], 'synthetic_test')$$,
                 '22023', null, 'an empty route is rejected');
select throws_ok($$select * from trace_ref.episode_route_timeline_summary(array['PM-PM', 'PM-NOPE'], 'synthetic_test')$$,
                 '22023', null, 'an unknown role id is rejected');
select throws_ok($$select * from trace_ref.episode_route_timeline_summary(array['ROLE-UNKNOWN', 'PM-PM'], 'synthetic_test')$$,
                 '22023', null, 'an unresolved role id is rejected');
select throws_ok($$select * from trace_ref.episode_route_timeline_summary(array['PM-PM', null], 'synthetic_test')$$,
                 '22023', null, 'a null step is rejected');
select throws_ok($$select * from trace_ref.episode_route_timeline_summary(array[['PM-PM', 'PM-PM'], ['PM-PM', 'PM-PM']], 'synthetic_test')$$,
                 '22023', null, 'a multi-dimensional route is rejected');

-- ------------------------------------------------------------------ summaries
select is((select route from trace_ref.episode_route_timeline_summary(array['PM-PM', 'PM-PM'], 'synthetic_test')),
          array['PM-PM', 'PM-PM'], 'the episode summary returns the route as given (not collapsed)');
select is((select evidence_class::text || '/' || timeline_display_allowed::text
           from trace_ref.episode_route_timeline_summary(array['PM-PM', 'PM-PM'], 'synthetic_test')),
          'synthetic_test_only/false', 'synthetic summaries stay synthetic_test_only with no timeline');
select is((select supporting_profile_count from trace_ref.episode_route_timeline_summary(array['PM-PM', 'PM-PM'], 'synthetic_test')),
          10, 'synthetic PM -> PM support = 7 seeded fixtures + 3 transient fixtures');

-- ------------------------------------------------------------------ mode separation
select is((select count(*)::int from trace_ref.episode_route_observations(array['PM-PM', 'PM-PM'], 'empirical')),
          0, 'synthetic profiles never appear in empirical mode');

insert into auth.users (id, email) values ('55555555-5555-5555-5555-555555555555', 'pgtap-ep@example.invalid');
insert into public.trace_consent_documents (consent_version, purpose, title, body_sha256, legal_review_status, effective_from)
values ('pgtap-ep-v1', 'deidentified_shared_dataset', 'pgTAP episode shared dataset', repeat('c', 64), 'approved', now() - interval '30 days');
insert into public.trace_consent_events (consent_event_id, user_id, purpose, status, consent_version, recorded_at)
values ('c0000000-0000-0000-0000-0000000000e1', '55555555-5555-5555-5555-555555555555', 'deidentified_shared_dataset', 'granted', 'pgtap-ep-v1', now() - interval '2 days');
insert into trace_ref.profiles (profile_id, source_id, dataset_class, review_status, contributor_user_id, source_consent_event_id,
                                resume_as_of_year, resume_as_of_month, resume_as_of_precision)
values ('REF-911', 'SRC-CONTRIB-001', 'consented_real_reference', 'participant_confirmed',
        '55555555-5555-5555-5555-555555555555', 'c0000000-0000-0000-0000-0000000000e1', 2026, 11, 'month');
insert into trace_ref.employers (profile_id, employer_id) values ('REF-911', 'REF-911-ORG-A'), ('REF-911', 'REF-911-ORG-B');
insert into trace_ref.employments (employment_id, profile_id, sequence_index, employer_id, title_raw_redacted, role_id,
  mapping_status, mapping_confidence, employment_type, start_year, start_month, start_precision, end_year, end_month, end_precision, is_current_as_stated)
values ('REF-911-EMP-01', 'REF-911', 1, 'REF-911-ORG-A', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', 2018, 1, 'month', 2020, 12, 'month', false),
       ('REF-911-EMP-02', 'REF-911', 2, 'REF-911-ORG-B', 'PM', 'PM-PM', 'confirmed', 1, 'full_time', 2021, 1, 'month', null, null, 'ongoing', true);

select is((select array_agg(o.profile_id) from trace_ref.episode_route_observations(array['PM-PM', 'PM-PM'], 'empirical') o),
          array['REF-911'], 'empirical mode reads only the currently eligible real profile');
select is((select count(*)::int from trace_ref.episode_route_observations(array['PM-PM', 'PM-PM'], 'synthetic_test') o
           where o.profile_id = 'REF-911'),
          0, 'real profiles never appear in synthetic mode');
select is((select evidence_class::text || '/' || supporting_profile_count::text
           from trace_ref.episode_route_timeline_summary(array['PM-PM', 'PM-PM'], 'empirical')),
          'insufficient/1', 'an empirical summary counts only eligible real profiles');

insert into public.trace_consent_events (user_id, purpose, status, consent_version, recorded_at)
values ('55555555-5555-5555-5555-555555555555', 'deidentified_shared_dataset', 'withdrawn', 'pgtap-ep-v1', now() - interval '1 hour');
select is((select count(*)::int from trace_ref.episode_route_observations(array['PM-PM', 'PM-PM'], 'empirical')),
          0, 'withdrawal removes the profile from episode observations immediately');

-- ------------------------------------------------------------------ privileges
select ok(not has_table_privilege('anon', 'trace_ref.v_career_episode_paths', 'SELECT'), 'anon cannot read episode paths');
select ok(not has_table_privilege('authenticated', 'trace_ref.v_episode_transitions', 'SELECT'), 'authenticated cannot read episode transitions');
select ok(not has_function_privilege('authenticated', 'trace_ref.episode_route_observations(text[], public.trace_data_mode)', 'EXECUTE'),
          'authenticated cannot execute episode route observations');
select ok(has_function_privilege('service_role', 'trace_ref.episode_route_timeline_summary(text[], public.trace_data_mode)', 'EXECUTE'),
          'service_role can execute the episode summary');

-- ------------------------------------------------------------------ no employer pseudonyms in outputs
select is((select count(*)::int from information_schema.columns
           where table_schema = 'trace_ref'
             and table_name in ('v_episode_transitions', 'v_career_episode_stints', 'v_career_episode_paths')
             and column_name like '%employer%' and column_name <> 'employer_changed'),
          0, 'episode views expose no employer identifier column');
select is((select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
           where n.nspname = 'trace_ref' and p.proname in ('episode_route_observations', 'episode_route_timeline_summary')
             and pg_get_function_result(p.oid) ilike '%employer%'),
          0, 'episode functions return no employer column');
select is((select count(*)::int from trace_ref.episode_route_observations(array['PM-PM', 'PM-PM'], 'synthetic_test') o
           where o::text like '%-ORG-%'),
          0, 'no employer pseudonym appears in episode observations');

select * from finish();
rollback;
