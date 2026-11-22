-- v2.8.0: restricted runtime login, no client writes, protected contributor mapping, real-pool eligibility,
-- withdrawal and shared rate limits. Everything runs in one transaction that is rolled back.
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(25);

-- ------------------------------------------------------------------ roles
select is((select row(rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication, rolcanlogin)::text from pg_roles
            where rolname = 'trace_app_runtime'), '(f,f,f,f,f,t)', 'the runtime login is not privileged');
select is((select count(*)::int from pg_class where relowner = 'trace_app_runtime'::regrole), 0, 'the runtime login owns nothing');
select is((select string_agg(r.rolname || ':' || a.inherit_option || ':' || a.set_option, ' ' order by r.rolname)
             from pg_auth_members a join pg_roles r on r.oid = a.roleid where a.member = 'trace_app_runtime'::regrole),
          'trace_app_user:false:true trace_reference_reader:true:false',
          'runtime: may SET the owner role without inheriting it; inherits only the read-only reference reader');
select ok(not pg_has_role('authenticator', 'trace_app_user', 'MEMBER') and not pg_has_role('trace_app_runtime', 'authenticated', 'SET')
          and not pg_has_role('trace_app_runtime', 'service_role', 'MEMBER') and not pg_has_role('trace_app_runtime', 'postgres', 'MEMBER'),
          'PostgREST cannot reach the owner role; the runtime cannot become authenticated, service_role or postgres');

-- ------------------------------------------------------------------ no client write path
select is((select count(*)::int from information_schema.role_table_grants
            where grantee in ('anon', 'authenticated') and table_schema = 'public'
              and privilege_type in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')), 0, 'no table-level client writes');
select is((select count(*)::int from information_schema.column_privileges
            where grantee in ('anon', 'authenticated') and table_schema = 'public' and privilege_type in ('INSERT', 'UPDATE')), 0,
          'no column-level client writes');
select is((select count(*)::int from pg_proc p where p.prosecdef
             and p.pronamespace in ('trace_review'::regnamespace, 'trace_ingest'::regnamespace, 'trace_private'::regnamespace,
                                    'trace_ops'::regnamespace, 'trace_ref'::regnamespace)
             and (has_function_privilege('authenticated', p.oid, 'EXECUTE') or has_function_privilege('anon', p.oid, 'EXECUTE'))), 0,
          'clients execute no definer function');
select ok(has_function_privilege('trace_app_user', 'trace_review.review_record(text, uuid, uuid, text)', 'EXECUTE')
          and has_column_privilege('trace_app_user', 'public.trace_candidate_profiles', 'headline', 'UPDATE')
          and not has_column_privilege('trace_app_user', 'public.trace_candidate_profiles', 'is_test_fixture', 'UPDATE'),
          'the owner role writes content, never system fields');
select ok(has_function_privilege('trace_app_runtime', 'trace_ingest.designate_fixture(uuid, uuid, text, text)', 'EXECUTE')
          and not has_function_privilege('trace_app_user', 'trace_ingest.designate_fixture(uuid, uuid, text, text)', 'EXECUTE'),
          'only the runtime login designates fixtures');

-- ------------------------------------------------------------------ private mapping
select is((select string_agg(r || ':' || has_schema_privilege(r, 'trace_private', 'USAGE'), ' ' order by r)
             from unnest(array['anon', 'authenticated', 'service_role', 'trace_app_runtime', 'trace_app_user', 'trace_reference_reader']) r),
          'anon:false authenticated:false service_role:false trace_app_runtime:false trace_app_user:false trace_reference_reader:false',
          'nobody but the admin identity reaches trace_private');
select is((select count(*)::int from information_schema.columns where table_schema = 'trace_ref'
            and column_name in ('participant_reference', 'contributor_id')), 0, 'no contributor identity column on any reference table');

-- ------------------------------------------------------------------ eligibility
insert into trace_private.contributors (contributor_id, source_kind, participant_reference)
values ('00000000-0000-4000-8000-0000000000c1', 'legacy_participant', 'PGTAP-P1');
insert into trace_private.consent_attestations (contributor_id, basis, purposes, evidence_reference, attested_by,
                                                verification_status, verified_by, verified_at)
values ('00000000-0000-4000-8000-0000000000c1', 'product_owner_attestation',
        array['deidentified_career_analysis', 'aggregate_research', 'benchmarking', 'recommendations'], 'pgtap evidence', 'pgtap',
        'verified', 'pgtap steward', now());
insert into trace_private.contributions (contribution_id, contributor_id, profile_id, status, deidentification_status, pipeline_version,
                                         admitted_at)
values ('00000000-0000-4000-8000-0000000000d1', '00000000-0000-4000-8000-0000000000c1', 'CRR-900000000001', 'admitted', 'passed',
        'pgtap', now());
insert into trace_ref.profiles (profile_id, source_id, dataset_class, country_code, review_status)
values ('CRR-900000000001', 'SRC-CONTRIB-001', 'consented_real_reference', 'IN', 'reviewer_confirmed');

select ok('CRR-900000000001' in (select profile_id from trace_ref.v_eligible_profiles), 'an admitted, verified contribution is eligible');
select ok('CRR-900000000001' in (select profile_id from trace_ref.v_mode_profiles where data_mode = 'empirical')
          and 'CRR-900000000001' not in (select profile_id from trace_ref.v_mode_profiles where data_mode <> 'empirical'),
          'it is in the empirical mode only, never a synthetic mode');
select is((select contributor_user_id from trace_ref.profiles where profile_id = 'CRR-900000000001'), null,
          'the analytical row carries no contributor identity');

update trace_private.consent_attestations set purposes = array['benchmarking'] where contributor_id = '00000000-0000-4000-8000-0000000000c1';
select ok('CRR-900000000001' not in (select profile_id from trace_ref.v_eligible_profiles), 'an incomplete consent scope is not eligible');
update trace_private.consent_attestations
   set purposes = array['deidentified_career_analysis', 'aggregate_research', 'benchmarking', 'recommendations'],
       verification_status = 'pending', verified_by = null, verified_at = null
 where contributor_id = '00000000-0000-4000-8000-0000000000c1';
select ok('CRR-900000000001' not in (select profile_id from trace_ref.v_eligible_profiles), 'an unverified attestation is not eligible');
update trace_private.consent_attestations set verification_status = 'verified', verified_by = 'pgtap', verified_at = now()
 where contributor_id = '00000000-0000-4000-8000-0000000000c1';

select throws_ok($$insert into trace_ref.profiles (profile_id, source_id, dataset_class, country_code, review_status)
                   values ('CRR-900000000002', 'SRC-CONTRIB-001', 'consented_real_reference', 'IN', 'reviewer_confirmed')$$,
                 '23514', null, 'a real profile without a registered contribution is refused');
select throws_ok($$insert into trace_private.contributors (source_kind, participant_reference) values ('legacy_participant', 'a@b.example')$$,
                 '23514', null, 'a participant reference cannot be an email address');

-- ------------------------------------------------------------------ withdrawal
select is(trace_private.withdraw_participant('PGTAP-P1', 'pgtap withdrawal'), 1, 'withdrawal removes the contributed profile');
select ok(not exists (select 1 from trace_ref.profiles where profile_id = 'CRR-900000000001')
          and (select status = 'withdrawn' and profile_id is null and deleted_at is not null from trace_private.contributions
                where contribution_id = '00000000-0000-4000-8000-0000000000d1'),
          'the profile is deleted; a minimal withdrawn contribution record remains');
select ok((select withdrawn_at is not null from trace_private.consent_attestations
            where contributor_id = '00000000-0000-4000-8000-0000000000c1'), 'the consent evidence records the withdrawal');

-- ------------------------------------------------------------------ shared rate limits
select is(trace_ops.consume_rate_limit(array['pgtap:a', 'pgtap:b'], array[2, 1], array[60, 60]), 0, 'a request with room is accepted');
select ok(trace_ops.consume_rate_limit(array['pgtap:c', 'pgtap:b'], array[2, 1], array[60, 60]) > 0,
          'a full key refuses the request with a wait (Retry-After)');
select is((select hits from trace_ops.rate_limit_counters where key = 'pgtap:c'), 0, 'the refused request is not counted');
select throws_ok($$select trace_ops.consume_rate_limit(array['pgtap:d'], array[0], array[60])$$, '22023', null,
                 'invalid limits are refused, never treated as unlimited');

select * from finish();
rollback;
