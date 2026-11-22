-- pgTAP tests for the Trace Data Foundation v2 schema.
-- Run on a LOCAL Supabase stack only:  supabase start && supabase test db
-- NOT executed as part of this package review (no database was used). Everything runs inside a
-- transaction that is rolled back, so no rows persist.

begin;
create extension if not exists pgtap with schema extensions;
select plan(39);

-- ------------------------------------------------------------------ exposure and privileges
select ok(not has_schema_privilege('anon', 'trace_ref', 'USAGE'), 'anon cannot use trace_ref');
select ok(not has_schema_privilege('authenticated', 'trace_ref', 'USAGE'), 'authenticated cannot use trace_ref');

select is(
  (select count(*)::int
   from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where c.relkind = 'r'
     and ((n.nspname = 'public' and c.relname like 'trace\_%') or n.nspname = 'trace_ref')
     and not c.relrowsecurity),
  0, 'every Trace table has row-level security enabled');

select is(
  (select count(*)::int
   from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'trace_ref' and c.relkind = 'v'
     and not coalesce(c.reloptions @> array['security_invoker=true'], false)),
  0, 'every trace_ref view is security_invoker');

select ok(not has_table_privilege('anon', 'public.trace_candidate_profiles', 'SELECT'), 'anon cannot read candidate profiles');
select ok(has_table_privilege('anon', 'public.trace_role_taxonomy', 'SELECT'), 'anon can read the role taxonomy');
select ok(not has_table_privilege('authenticated', 'public.trace_role_taxonomy', 'INSERT'), 'clients cannot write the role taxonomy');
select ok(not has_column_privilege('authenticated', 'public.trace_pathway_evidence', 'reference_profile_id', 'SELECT'),
          'clients cannot read reference profile ids in pathway evidence');
select ok(not has_column_privilege('authenticated', 'public.trace_candidate_profiles', 'is_test_fixture', 'UPDATE'),
          'clients cannot flip the test-fixture flag');
select is((select public from storage.buckets where id = 'trace-resumes'), false, 'resume bucket is private');

-- ------------------------------------------------------------------ dataset separation
select throws_ok(
  $$insert into trace_ref.data_sources (source_id, dataset_class, source_type, source_label)
    values ('SRC-PGTAP-UPL', 'uploaded_resume_fixture', 'participant_contribution', 'should fail')$$,
  '23514', null, 'uploaded resume fixtures are rejected by the database');

-- ------------------------------------------------------------------ transition fixtures
insert into trace_ref.data_sources (source_id, dataset_class, source_type, source_label)
values ('SRC-PGTAP', 'synthetic_test_fixture', 'synthetic_generator', 'pgTAP fixtures');

insert into trace_ref.profiles (profile_id, source_id, dataset_class, review_status, resume_as_of_year, resume_as_of_month, resume_as_of_precision)
values ('SYN-901', 'SRC-PGTAP', 'synthetic_test_fixture', 'synthetic_curated', 2026, 11, 'month'),
       ('SYN-902', 'SRC-PGTAP', 'synthetic_test_fixture', 'synthetic_curated', 2026, 11, 'month'),
       ('SYN-903', 'SRC-PGTAP', 'synthetic_test_fixture', 'synthetic_curated', 2026, 11, 'month'),
       ('SYN-904', 'SRC-PGTAP', 'synthetic_test_fixture', 'synthetic_curated', 2026, 11, 'month');

insert into trace_ref.employers (profile_id, employer_id)
values ('SYN-901', 'SYN-901-ORG-A'),
       ('SYN-902', 'SYN-902-ORG-A'), ('SYN-902', 'SYN-902-ORG-B'), ('SYN-902', 'SYN-902-ORG-C'),
       ('SYN-903', 'SYN-903-ORG-A'), ('SYN-903', 'SYN-903-ORG-B'),
       ('SYN-904', 'SYN-904-ORG-A'), ('SYN-904', 'SYN-904-ORG-B');

insert into trace_ref.employments (employment_id, profile_id, sequence_index, employer_id, title_raw_redacted, role_id,
  mapping_status, mapping_confidence, employment_type, start_year, start_month, start_precision,
  end_year, end_month, end_precision, is_current_as_stated)
values
  -- same-month internal promotion
  ('SYN-901-EMP-01', 'SYN-901', 1, 'SYN-901-ORG-A', 'Associate Product Manager', 'PM-APM', 'confirmed', 1, 'full_time', 2020, 7, 'month', 2022, 3, 'month', false),
  ('SYN-901-EMP-02', 'SYN-901', 2, 'SYN-901-ORG-A', 'Product Manager', 'PM-PM', 'confirmed', 1, 'full_time', 2022, 3, 'month', null, null, 'ongoing', true),
  -- unresolved intermediate role
  ('SYN-902-EMP-01', 'SYN-902', 1, 'SYN-902-ORG-A', 'Software Engineer', 'ROLE-SWE', 'confirmed', 1, 'full_time', 2016, 7, 'month', 2019, 3, 'month', false),
  ('SYN-902-EMP-02', 'SYN-902', 2, 'SYN-902-ORG-B', 'Associate', 'ROLE-UNKNOWN', 'unresolved', 0.3, 'full_time', 2019, 4, 'month', 2020, 12, 'month', false),
  ('SYN-902-EMP-03', 'SYN-902', 3, 'SYN-902-ORG-C', 'Product Manager', 'PM-PM', 'confirmed', 1, 'full_time', 2021, 1, 'month', null, null, 'ongoing', true),
  -- year-precision dates
  ('SYN-903-EMP-01', 'SYN-903', 1, 'SYN-903-ORG-A', 'Business Analyst', 'ROLE-BA', 'confirmed', 1, 'full_time', 2015, null, 'year', 2018, null, 'year', false),
  ('SYN-903-EMP-02', 'SYN-903', 2, 'SYN-903-ORG-B', 'Associate Product Manager', 'PM-APM', 'confirmed', 1, 'full_time', 2018, null, 'year', null, null, 'ongoing', true),
  -- year-only dates, previous role ends in an EARLIER year: order is established
  ('SYN-904-EMP-01', 'SYN-904', 1, 'SYN-904-ORG-A', 'Business Analyst', 'ROLE-BA', 'confirmed', 1, 'full_time', 2015, null, 'year', 2017, null, 'year', false),
  ('SYN-904-EMP-02', 'SYN-904', 2, 'SYN-904-ORG-B', 'Associate Product Manager', 'PM-APM', 'confirmed', 1, 'full_time', 2018, null, 'year', null, null, 'ongoing', true);

select is((select transition_status from trace_ref.v_role_transitions where from_employment_id = 'SYN-901-EMP-01'),
          'confirmed', 'same-month promotion is a confirmed transition');
select is((select timing_relation from trace_ref.v_role_transitions where from_employment_id = 'SYN-901-EMP-01'),
          'same_month_boundary', 'same-month promotion is classified as a same-month boundary');
select is((select move_type from trace_ref.v_role_transitions where from_employment_id = 'SYN-901-EMP-01'),
          'internal', 'same-employer promotion is an internal move');

select is((select count(*)::int from trace_ref.v_role_transitions
           where profile_id = 'SYN-902' and from_role_id = 'ROLE-SWE' and to_role_id = 'PM-PM'),
          0, 'unresolved intermediate role never yields a direct SWE -> PM transition');
select is((select count(*)::int from trace_ref.v_role_transitions where profile_id = 'SYN-902' and transition_status = 'unconfirmed'),
          2, 'both transitions around the unresolved role are unconfirmed');
select is((select count(*)::int from trace_ref.route_observations(array['ROLE-SWE', 'PM-PM'], 'synthetic_test') o
           where o.profile_id = 'SYN-902'),
          0, 'route validation does not bridge the unresolved role');

select is((select timing_relation from trace_ref.v_role_transitions where from_employment_id = 'SYN-903-EMP-01'),
          'boundary_uncertain', 'same-year year-only boundary is uncertain');
select is((select transition_status from trace_ref.v_role_transitions where from_employment_id = 'SYN-903-EMP-01'),
          'unconfirmed', 'same-year year-only boundary is unconfirmed (order cannot be established)');
select is((select count(*)::int from trace_ref.route_observations(array['ROLE-BA', 'PM-APM'], 'synthetic_test') o
           where o.profile_id = 'SYN-903'),
          0, 'same-year year-only boundary does not support a route');
select is((select transition_status from trace_ref.v_role_transitions where from_employment_id = 'SYN-904-EMP-01'),
          'confirmed', 'earlier end year than start year confirms the order');
select is((select gap_is_precise from trace_ref.v_role_transitions where from_employment_id = 'SYN-904-EMP-01'),
          false, 'year-only gap stays imprecise');
select is((select o.is_precise from trace_ref.route_observations(array['ROLE-BA', 'PM-APM'], 'synthetic_test') o
           where o.profile_id = 'SYN-904'),
          false, 'year-only route observation is kept out of precise percentiles');

select is((select s.evidence_class from trace_ref.route_timeline_summary(array['PM-APM', 'PM-PM'], 'synthetic_test') s),
          'synthetic_test_only'::public.trace_evidence_class, 'synthetic mode only produces synthetic_test_only evidence');
select is((select count(*)::int from trace_ref.route_observations(array['PM-APM', 'PM-PM'], 'empirical')),
          0, 'synthetic profiles never appear in empirical mode');

-- ------------------------------------------------------------------ integrity constraints
select throws_ok(
  $$insert into trace_ref.experience_evidence (evidence_id, profile_id, employment_id, evidence_type, evidence_text_redacted, provenance)
    values ('SYN-901-X-E01', 'SYN-901', 'SYN-902-EMP-01', 'responsibility', 'Evidence attached to another profile', 'synthetic_fixture')$$,
  '23503', null, 'evidence cannot attach to an employment of another profile');

select throws_ok(
  $$insert into trace_ref.employments (employment_id, profile_id, sequence_index, title_raw_redacted, role_id,
      mapping_status, mapping_confidence, start_precision, end_precision)
    values ('SYN-903-EMP-09', 'SYN-903', 9, 'Associate', 'PM-PM', 'unresolved', 0.3, 'unknown', 'unknown')$$,
  '23514', null, 'unresolved mappings must use ROLE-UNKNOWN');

-- ------------------------------------------------------------------ consent integrity
insert into auth.users (id, email)
values ('33333333-3333-3333-3333-333333333333', 'pgtap-c@example.invalid'),
       ('44444444-4444-4444-4444-444444444444', 'pgtap-d@example.invalid');
insert into public.trace_consent_documents (consent_version, purpose, title, body_sha256, legal_review_status, effective_from)
values ('pgtap-v1', 'deidentified_shared_dataset', 'pgTAP shared dataset', repeat('a', 64), 'approved', now() - interval '30 days'),
       ('pgtap-v1', 'external_demo', 'pgTAP external demo', repeat('b', 64), 'approved', now() - interval '30 days');
insert into public.trace_consent_events (consent_event_id, user_id, purpose, status, consent_version, recorded_at)
values ('c0000000-0000-0000-0000-000000000001', '33333333-3333-3333-3333-333333333333', 'deidentified_shared_dataset', 'granted', 'pgtap-v1', now() - interval '2 days'),
       ('c0000000-0000-0000-0000-000000000002', '44444444-4444-4444-4444-444444444444', 'deidentified_shared_dataset', 'granted', 'pgtap-v1', now() - interval '2 days'),
       ('c0000000-0000-0000-0000-000000000003', '33333333-3333-3333-3333-333333333333', 'external_demo', 'granted', 'pgtap-v1', now() - interval '2 days'),
       ('c0000000-0000-0000-0000-000000000004', '44444444-4444-4444-4444-444444444444', 'deidentified_shared_dataset', 'withdrawn', 'pgtap-v1', now() - interval '1 day');

select lives_ok(
  $$insert into trace_ref.profiles (profile_id, source_id, dataset_class, review_status, contributor_user_id, source_consent_event_id)
    values ('REF-901', 'SRC-CONTRIB-001', 'consented_real_reference', 'participant_confirmed',
            '33333333-3333-3333-3333-333333333333', 'c0000000-0000-0000-0000-000000000001')$$,
  'a real profile linked to its contributor''s granted shared-dataset event is accepted');

select throws_ok(
  $$insert into trace_ref.profiles (profile_id, source_id, dataset_class, contributor_user_id, source_consent_event_id)
    values ('REF-902', 'SRC-CONTRIB-001', 'consented_real_reference',
            '33333333-3333-3333-3333-333333333333', 'c0000000-0000-0000-0000-000000000002')$$,
  '23514', null, 'consent event of a different user is rejected');

select throws_ok(
  $$insert into trace_ref.profiles (profile_id, source_id, dataset_class, contributor_user_id, source_consent_event_id)
    values ('REF-903', 'SRC-CONTRIB-001', 'consented_real_reference',
            '33333333-3333-3333-3333-333333333333', 'c0000000-0000-0000-0000-000000000003')$$,
  '23514', null, 'consent event for a different purpose is rejected');

select throws_ok(
  $$insert into trace_ref.profiles (profile_id, source_id, dataset_class, contributor_user_id, source_consent_event_id)
    values ('REF-904', 'SRC-CONTRIB-001', 'consented_real_reference',
            '44444444-4444-4444-4444-444444444444', 'c0000000-0000-0000-0000-000000000004')$$,
  '23514', null, 'a withdrawn (non-granted) consent event is rejected');

select throws_ok(
  $$insert into trace_ref.profiles (profile_id, source_id, dataset_class, contributor_user_id, source_consent_event_id)
    values ('REF-905', 'SRC-CONTRIB-001', 'consented_real_reference',
            '33333333-3333-3333-3333-333333333333', 'c0000000-0000-0000-0000-0000000000ff')$$,
  '23514', null, 'a non-existent consent event is rejected');

select throws_ok(
  $$insert into trace_ref.profiles (profile_id, source_id, dataset_class, contributor_user_id)
    values ('REF-906', 'SRC-CONTRIB-001', 'consented_real_reference', '33333333-3333-3333-3333-333333333333')$$,
  '23514', null, 'a real profile without a consent event is rejected');

select throws_ok(
  $$update trace_ref.profiles set contributor_user_id = '44444444-4444-4444-4444-444444444444' where profile_id = 'REF-901'$$,
  '23514', null, 're-pointing a profile to another contributor without that contributor''s grant is rejected');

select is((select count(*)::int from trace_ref.v_eligible_profiles where profile_id = 'REF-901'),
          1, 'profile with an active, approved shared-dataset grant is eligible');

insert into public.trace_consent_events (user_id, purpose, status, consent_version, recorded_at)
values ('33333333-3333-3333-3333-333333333333', 'deidentified_shared_dataset', 'withdrawn', 'pgtap-v1', now() - interval '1 hour');

select is((select count(*)::int from trace_ref.v_eligible_profiles where profile_id = 'REF-901'),
          0, 'withdrawal excludes the profile immediately');

-- ------------------------------------------------------------------ pathway results and RLS
insert into auth.users (id, email)
values ('11111111-1111-1111-1111-111111111111', 'pgtap-a@example.invalid'),
       ('22222222-2222-2222-2222-222222222222', 'pgtap-b@example.invalid');
insert into public.trace_candidate_profiles (candidate_profile_id, owner_user_id)
values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111');
insert into public.trace_pathway_runs (pathway_run_id, owner_user_id, candidate_profile_id, data_mode, target_role_id)
values ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', '11111111-1111-1111-1111-111111111111',
        'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'synthetic_test', 'PM-PM');

select throws_ok(
  $$insert into public.trace_pathway_recommendations (pathway_run_id, owner_user_id, data_mode, rank, evidence_class)
    values ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', '11111111-1111-1111-1111-111111111111', 'synthetic_test', 1, 'strong')$$,
  '23514', null, 'a synthetic run cannot claim strong evidence');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub": "22222222-2222-2222-2222-222222222222", "role": "authenticated"}', true);
select is((select count(*)::int from public.trace_candidate_profiles), 0, 'another user cannot see the candidate profile');
select set_config('request.jwt.claims', '{"sub": "11111111-1111-1111-1111-111111111111", "role": "authenticated"}', true);
select is((select count(*)::int from public.trace_candidate_profiles), 1, 'the owner can see their own candidate profile');
reset role;

select * from finish();
rollback;
