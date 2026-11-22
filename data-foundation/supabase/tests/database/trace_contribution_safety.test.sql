-- v2.10.0: identity-marker store and bounded contribution retries. Rolled back.
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(20);

-- ------------------------------------------------------------------ the restricted marker store
select ok(not has_table_privilege('anon', 'trace_private.identity_markers', 'SELECT')
          and not has_table_privilege('authenticated', 'trace_private.identity_markers', 'SELECT')
          and not has_table_privilege('service_role', 'trace_private.identity_markers', 'SELECT')
          and not has_table_privilege('trace_app_runtime', 'trace_private.identity_markers', 'SELECT')
          and not has_table_privilege('trace_app_user', 'trace_private.identity_markers', 'SELECT')
          and not has_table_privilege('trace_reference_reader', 'trace_private.identity_markers', 'SELECT'),
          'no role but the admin identity reads identity markers');
select ok(has_function_privilege('trace_app_runtime', 'trace_contrib.store_identity_markers(bytea, text)', 'EXECUTE')
          and not has_function_privilege('trace_app_user', 'trace_contrib.store_identity_markers(bytea, text)', 'EXECUTE')
          and not has_function_privilege('authenticated', 'trace_contrib.store_identity_markers(bytea, text)', 'EXECUTE'),
          'only the runtime login stores markers');
select ok(not exists (select 1 from information_schema.columns where table_schema = 'trace_ref'
                       and column_name in ('names', 'emails', 'phones', 'identity_markers')),
          'no marker column exists in the reference schema');
select is(trace_private.marker_set(array['A B', ' A B ', ''], array['C', null, 'A B'], 16), array['A B', 'C'],
          'markers are trimmed, de-duplicated and blanks dropped');
select is(cardinality(trace_private.marker_set('{}', array(select 'n' || g from generate_series(1, 40) g), 16)), 16, 'markers are capped');

insert into auth.users (id, email) values ('00000000-0000-4000-8000-0000000000d1', 'pgtap-d1@example.invalid');
insert into public.trace_candidate_profiles (candidate_profile_id, owner_user_id)
values ('00000000-0000-4000-8000-0000000000e1', '00000000-0000-4000-8000-0000000000d1');
-- v2.31.0 (D48): markers are kept only for an active contribution consent.
insert into public.trace_consent_documents (consent_version, purpose, title, body_sha256, legal_review_status, effective_from)
values ('pgtap-cs-share-v1', 'deidentified_shared_dataset', 'pgTAP share', repeat('c', 64), 'approved', now() - interval '1 day');
insert into public.trace_consent_events (user_id, purpose, status, consent_version)
values ('00000000-0000-4000-8000-0000000000d1', 'deidentified_shared_dataset', 'granted', 'pgtap-cs-share-v1');
delete from trace_private.contribution_requests where user_id = '00000000-0000-4000-8000-0000000000d1';   -- queued by the grant; set up below
select set_config('request.jwt.claim.sub', '00000000-0000-4000-8000-0000000000d1', true);
select set_config('request.jwt.claims', '{"sub": "00000000-0000-4000-8000-0000000000d1", "role": "authenticated"}', true);
select lives_ok($$select trace_contrib.store_identity_markers('\x000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d'::bytea, '0123456789abcdef')$$,
                'the owner''s sealed markers are stored');
select throws_ok($$update trace_private.identity_markers set names = array['Plain Name'] where owner_user_id = '00000000-0000-4000-8000-0000000000d1'$$,
                 '23514', null, 'a sealed row can never also hold plaintext markers');
select is((select names from trace_contrib.contribution_identity('00000000-0000-4000-8000-0000000000d1')), '{}'::text[],
          'no plaintext names are returned for a sealed row');
select is((select markers_recorded and sealed is not null and key_id = '0123456789abcdef'
             from trace_contrib.contribution_identity('00000000-0000-4000-8000-0000000000d1')), true, 'the sealed blob is returned to the runtime');

-- ------------------------------------------------------------------ backoff and terminal failure
select is(trace_private.contribution_backoff(1, 30, 3600), interval '30 seconds', 'first retry after the base delay');
select is(trace_private.contribution_backoff(3, 30, 3600), interval '120 seconds', 'exponential');
select is(trace_private.contribution_backoff(20, 30, 3600), interval '3600 seconds', 'capped');
select is(trace_private.contribution_backoff(1, 0, 0), interval '1 second', 'inputs are clamped');

insert into trace_private.contribution_requests (user_id, attempts, locked_until, lease_token)
values ('00000000-0000-4000-8000-0000000000d1', 2, now() + interval '1 minute', '00000000-0000-4000-8000-0000000000f1');
select is(trace_contrib.report_contribution_failure('00000000-0000-4000-8000-0000000000d1', gen_random_uuid(), 'RuntimeError', 2, 10, 60),
          'stale', 'a stale lease token changes nothing');
select is(trace_contrib.report_contribution_failure('00000000-0000-4000-8000-0000000000d1', '00000000-0000-4000-8000-0000000000f1',
          'error with content: someone@example.org', 2, 10, 60), 'failed', 'the last allowed attempt ends in the terminal state');
select is((select last_error from trace_private.contribution_requests where user_id = '00000000-0000-4000-8000-0000000000d1'), 'error',
          'only an error class code is stored, never a message');
select is((select state || '/' || reason from trace_private.user_contribution_status where user_id = '00000000-0000-4000-8000-0000000000d1'),
          'failed/retries_exhausted', 'the user sees the terminal state');
select is((select count(*)::int from trace_contrib.claim_contribution_requests(100, 60, 20, 10, 60)
            where user_id = '00000000-0000-4000-8000-0000000000d1'), 0, 'a terminal request is never claimed');
select ok(not has_function_privilege('trace_app_runtime', 'trace_private.retry_contribution(uuid, text)', 'EXECUTE')
          and not has_function_privilege('authenticated', 'trace_private.retry_contribution(uuid, text)', 'EXECUTE'),
          'the explicit retry is for the admin identity only');
select throws_ok($$select trace_private.retry_contribution('00000000-0000-4000-8000-0000000000d1', ' ')$$, '22023',
                 'a named operator is required', 'an operator must be named');

select * from finish();
rollback;
