-- v2.9.0: real resume admission, automatic contributions, withdrawal and account deletion. Rolled back.
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(18);

-- ------------------------------------------------------------------ privileges
select ok(not has_schema_privilege('anon', 'trace_contrib', 'USAGE') and not has_schema_privilege('authenticated', 'trace_contrib', 'USAGE')
          and not has_schema_privilege('service_role', 'trace_contrib', 'USAGE'), 'clients cannot reach trace_contrib');
select ok(has_function_privilege('trace_app_runtime', 'trace_ingest.admit_deidentified_resume(uuid, uuid, text, text)', 'EXECUTE')
          and not has_function_privilege('trace_app_user', 'trace_ingest.admit_deidentified_resume(uuid, uuid, text, text)', 'EXECUTE')
          and not has_function_privilege('authenticated', 'trace_ingest.admit_deidentified_resume(uuid, uuid, text, text)', 'EXECUTE'),
          'only the runtime login records a de-identification admission');
select ok(has_function_privilege('trace_app_user', 'trace_ingest.start_real_extraction(uuid, uuid, text)', 'EXECUTE')
          and not has_function_privilege('authenticated', 'trace_ingest.start_real_extraction(uuid, uuid, text)', 'EXECUTE'),
          'the owner role starts real extractions; the Data API role cannot');
select ok(not has_function_privilege('trace_app_user', 'trace_contrib.record_contribution_outcome(uuid, timestamptz, text, text, jsonb, text, jsonb, text)', 'EXECUTE')
          and has_function_privilege('trace_app_user', 'trace_contrib.my_contribution_status()', 'EXECUTE')
          and not has_function_privilege('trace_app_runtime', 'trace_contrib.my_contribution_status()', 'EXECUTE'),
          'the owner role only reads its own state; only the runtime records outcomes');
select is((select count(*)::int from pg_proc p where p.pronamespace in ('trace_contrib'::regnamespace, 'trace_private'::regnamespace)
            and (p.proacl is null or array_to_string(p.proacl, ',') ~ '(^|,)=X')), 0, 'no function in trace_contrib or trace_private is executable by PUBLIC');

-- ------------------------------------------------------------------ fixtures for the scenario
insert into auth.users (id, email) values ('00000000-0000-4000-8000-0000000000a1', 'pgtap-a1@example.invalid'),
                                          ('00000000-0000-4000-8000-0000000000a2', 'pgtap-a2@example.invalid');
insert into public.trace_candidate_profiles (candidate_profile_id, owner_user_id)
values ('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000a1');
insert into public.trace_resume_documents (resume_id, owner_user_id, candidate_profile_id, storage_object_path, byte_size, content_sha256, mime_type)
values ('00000000-0000-4000-8000-0000000000c1', '00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000b1',
        '00000000-0000-4000-8000-0000000000a1/r.pdf', 100, repeat('a', 64), 'application/pdf');
insert into public.trace_consent_documents (consent_version, purpose, title, body_sha256, legal_review_status, effective_from)
values ('pgtap-rp', 'resume_processing', 'pgtap', repeat('b', 64), 'approved', now() - interval '1 day'),
       ('pgtap-sc', 'deidentified_shared_dataset', 'pgtap', repeat('c', 64), 'approved', now() - interval '1 day');
insert into public.trace_consent_events (user_id, purpose, status, consent_version, source)
values ('00000000-0000-4000-8000-0000000000a1', 'resume_processing', 'granted', 'pgtap-rp', 'in_app');

-- ------------------------------------------------------------------ the ingestion gate
grant trace_app_runtime to postgres with set true, inherit false;     -- this transaction only (rolled back)
select set_config('request.jwt.claim.sub', '00000000-0000-4000-8000-0000000000a1', true);
select set_config('request.jwt.claims', '{"sub": "00000000-0000-4000-8000-0000000000a1", "role": "authenticated"}', true);
set local role trace_app_user;
select throws_ok($$select trace_ingest.start_real_extraction('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000c1', 'v1')$$,
                 'TR012', 'the resume has not passed de-identification', 'a non-fixture resume without an admission is refused');
reset role;
set local role trace_app_runtime;   -- outcomes captured in transaction-local settings (no pgTAP access as the runtime)
do $$ begin perform trace_ingest.admit_deidentified_resume('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000c1', repeat('a', 64), 'pgtap'); perform set_config('pgtap.own', 'ok', true); exception when others then perform set_config('pgtap.own', sqlstate, true); end $$;
do $$ begin perform trace_ingest.admit_deidentified_resume('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000c1', repeat('d', 64), 'pgtap'); perform set_config('pgtap.hash', 'ok', true); exception when others then perform set_config('pgtap.hash', sqlstate, true); end $$;
reset role;
select is(current_setting('pgtap.own'), 'ok', 'the owner''s own resume can be admitted');
select is(current_setting('pgtap.hash'), 'TR012', 'a different hash is refused');
set local role trace_app_user;
select lives_ok($$select trace_ingest.start_real_extraction('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000c1', 'v1')$$,
                'an admitted resume may be ingested');
reset role;
select set_config('request.jwt.claim.sub', '00000000-0000-4000-8000-0000000000a2', true);
select set_config('request.jwt.claims', '{"sub": "00000000-0000-4000-8000-0000000000a2", "role": "authenticated"}', true);
set local role trace_app_runtime;
do $$ begin perform trace_ingest.admit_deidentified_resume('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000c1', repeat('a', 64), 'pgtap'); perform set_config('pgtap.other', 'ok', true); exception when others then perform set_config('pgtap.other', sqlstate, true); end $$;
reset role;
select is(current_setting('pgtap.other'), 'TR012', 'another user cannot admit it');

-- ------------------------------------------------------------------ queue, outcomes, withdrawal
insert into public.trace_consent_events (user_id, purpose, status, consent_version, source)
values ('00000000-0000-4000-8000-0000000000a1', 'deidentified_shared_dataset', 'granted', 'pgtap-sc', 'in_app');
update public.trace_candidate_profiles set headline = 'x' where candidate_profile_id = '00000000-0000-4000-8000-0000000000b1';
update public.trace_candidate_profiles set headline = 'y' where candidate_profile_id = '00000000-0000-4000-8000-0000000000b1';
select is((select count(*)::int from trace_private.contribution_requests where user_id = '00000000-0000-4000-8000-0000000000a1'), 1,
          'repeated changes keep one queued request per user');
select is(trace_contrib.record_contribution_outcome('00000000-0000-4000-8000-0000000000a1', now(), 'eligible', null,
            '{"evidence": [{"text": "write to someone@example.org please"}]}', repeat('e', 64), '{}', 'pgtap'), 'failed',
          'a record holding an email address is never admitted');
select is(trace_contrib.record_contribution_outcome('00000000-0000-4000-8000-0000000000a1', now(), 'eligible', null,
            '{"employers": [], "employments": [], "country_code": "IN"}', repeat('f', 64), '{}', 'pgtap'), 'eligible',
          'a clean record with current consent is admitted');
select is((select count(*)::int from trace_private.contributions c join trace_private.contributors k using (contributor_id)
            where k.user_id = '00000000-0000-4000-8000-0000000000a1' and c.status = 'admitted'), 1, 'one admitted contribution');
select is(trace_contrib.record_contribution_outcome('00000000-0000-4000-8000-0000000000a1', now(), 'eligible', null,
            '{"employers": [], "employments": [], "country_code": "IN"}', repeat('f', 64), '{}', 'pgtap'), 'eligible',
          'an unchanged record is a no-op');
select is((select count(*)::int from trace_private.contributions), 1, 'still exactly one contribution (idempotent)');
insert into public.trace_consent_events (user_id, purpose, status, consent_version, source)
values ('00000000-0000-4000-8000-0000000000a1', 'deidentified_shared_dataset', 'withdrawn', 'pgtap-sc', 'in_app');
select ok((select status = 'withdrawn' and profile_id is null from trace_private.contributions)
          and (select state from trace_private.user_contribution_status where user_id = '00000000-0000-4000-8000-0000000000a1') = 'withdrawn'
          and not exists (select 1 from trace_ref.profiles where dataset_class = 'consented_real_reference'),
          'withdrawal removes the reference rows in the same transaction');
delete from auth.users where id = '00000000-0000-4000-8000-0000000000a1';
select is((select count(*)::int from trace_private.contributors), 0, 'deleting the account erases its contributor and contributions');

select * from finish();
rollback;
