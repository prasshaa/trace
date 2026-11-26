-- v2.12.0: the sharing preference, separate from the processing authority. Rolled back.
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(17);

-- ------------------------------------------------------------------ access
select ok(not has_table_privilege('anon', 'trace_private.sharing_preferences', 'SELECT')
          and not has_table_privilege('authenticated', 'trace_private.sharing_preferences', 'SELECT')
          and not has_table_privilege('trace_app_user', 'trace_private.sharing_preferences', 'SELECT')
          and not has_table_privilege('trace_app_runtime', 'trace_private.sharing_preferences', 'SELECT')
          and not has_table_privilege('trace_app_user', 'trace_private.sharing_preference_events', 'INSERT')
          and not has_table_privilege('authenticated', 'trace_private.sharing_preference_events', 'SELECT'),
          'no client role reads or writes sharing preferences directly');
select ok(has_function_privilege('trace_app_user', 'trace_contrib.set_sharing_preference(text)', 'EXECUTE')
          and has_function_privilege('trace_app_user', 'trace_contrib.acknowledge_data_use_notice(text)', 'EXECUTE')
          and has_function_privilege('trace_app_user', 'trace_contrib.my_sharing_preference()', 'EXECUTE')
          and not has_function_privilege('authenticated', 'trace_contrib.set_sharing_preference(text)', 'EXECUTE')
          and not has_function_privilege('anon', 'trace_contrib.acknowledge_data_use_notice(text)', 'EXECUTE')
          and not has_function_privilege('trace_app_runtime', 'trace_contrib.set_sharing_preference(text)', 'EXECUTE'),
          'only the owner role sets its own preference');
select ok(not has_function_privilege('trace_app_user', 'trace_private.exclude_from_shared_pool(uuid, text)', 'EXECUTE')
          and not has_function_privilege('trace_app_runtime', 'trace_private.exclude_from_shared_pool(uuid, text)', 'EXECUTE')
          and not has_function_privilege('trace_app_user', 'trace_private.sharing_is_private(uuid)', 'EXECUTE'),
          'the helpers are not callable by clients');

-- ------------------------------------------------------------------ fixtures
insert into auth.users (id, email) values ('00000000-0000-4000-8000-0000000000a1', 'pgtap-a1@example.invalid'),
                                          ('00000000-0000-4000-8000-0000000000a2', 'pgtap-a2@example.invalid');
insert into public.trace_consent_documents (consent_version, purpose, title, body_sha256, legal_review_status, effective_from)
values ('pgtap-sharing-v1', 'deidentified_shared_dataset', 'pgTAP sharing notice', repeat('b', 64), 'approved', now() - interval '1 day');

select set_config('request.jwt.claim.sub', '00000000-0000-4000-8000-0000000000a1', true);
select set_config('request.jwt.claims', '{"sub": "00000000-0000-4000-8000-0000000000a1", "role": "authenticated"}', true);

-- ------------------------------------------------------------------ the onboarding default
select is(trace_contrib.acknowledge_data_use_notice('trace-data-use-notice-v1'), 'shared',
          'Continue records anonymous recommendation use, not a public profile');
select is((select source || '/' || notice_version from trace_contrib.my_sharing_preference()),
          'onboarding_default/trace-data-use-notice-v1', 'the source and the notice version are recorded');
select is((select count(*)::int from public.trace_consent_events where user_id = '00000000-0000-4000-8000-0000000000a1'), 0,
          'acknowledging the notice never records a consent decision');
select ok(not trace_private.contribution_consent_active('00000000-0000-4000-8000-0000000000a1'),
          'the default preference alone is no processing authority');
select throws_ok($$select trace_contrib.acknowledge_data_use_notice('bad version!')$$, '22023', null, 'a malformed notice version is refused');

-- ------------------------------------------------------------------ private wins over a grant
insert into public.trace_consent_events (user_id, purpose, status, consent_version)
values ('00000000-0000-4000-8000-0000000000a1', 'deidentified_shared_dataset', 'granted', 'pgtap-sharing-v1');
select ok(trace_private.contribution_consent_active('00000000-0000-4000-8000-0000000000a1'), 'an explicit grant is the authority');
select is((select count(*)::int from trace_private.contribution_requests where user_id = '00000000-0000-4000-8000-0000000000a1'), 1,
          'the grant queued processing');
select lives_ok($$select trace_contrib.set_sharing_preference('private')$$, 'the owner chooses private');
select ok(not trace_private.contribution_consent_active('00000000-0000-4000-8000-0000000000a1'), 'private overrides an active grant');
select is((select count(*)::int from trace_private.contribution_requests where user_id = '00000000-0000-4000-8000-0000000000a1')
          || '/' || (select state || ':' || reason from trace_private.user_contribution_status where user_id = '00000000-0000-4000-8000-0000000000a1'),
          '0/withdrawn:private', 'queued work is dropped and the state is withdrawn at once');
select is(trace_contrib.acknowledge_data_use_notice('trace-data-use-notice-v1'), 'private',
          'seeing the notice again never turns sharing back on');
select lives_ok($$select trace_private.enqueue_contribution('00000000-0000-4000-8000-0000000000a1')$$, 'a later change tries to queue');
select is((select count(*)::int from trace_private.contribution_requests where user_id = '00000000-0000-4000-8000-0000000000a1'), 0,
          'nothing is queued for a private user');

-- ------------------------------------------------------------------ history
select throws_ok($$update trace_private.sharing_preference_events set mode = 'shared' where user_id = '00000000-0000-4000-8000-0000000000a1'$$,
                 '42501', null, 'the preference history is append-only');

select * from finish();
rollback;
