-- v2.31.0 / v2.31.1: staged resume import drafts, no implied retention, identity markers follow contribution consent.
-- Rolled back.
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(20);

-- access
select ok(not has_table_privilege('anon', 'trace_private.resume_import_drafts', 'SELECT')
          and not has_table_privilege('authenticated', 'trace_private.resume_import_drafts', 'SELECT')
          and not has_table_privilege('trace_app_user', 'trace_private.resume_import_drafts', 'SELECT')
          and not has_table_privilege('trace_app_user', 'trace_private.resume_import_drafts', 'UPDATE')
          and not has_table_privilege('service_role', 'trace_private.resume_import_drafts', 'DELETE'),
          'no client role reads or writes drafts directly');
select ok(has_function_privilege('trace_app_user', 'trace_import.my_draft(uuid, boolean)', 'EXECUTE')
          and has_function_privilege('trace_app_user', 'trace_import.save_draft(uuid, jsonb, text, text, text, text, integer, text, text[], bytea, text)', 'EXECUTE')
          and not has_function_privilege('authenticated', 'trace_import.delete_draft(uuid, uuid)', 'EXECUTE')
          and not has_function_privilege('anon', 'trace_import.my_draft(uuid, boolean)', 'EXECUTE'),
          'only the owner role calls the draft functions');
select is((select string_agg(distinct pg_get_userbyid(p.proowner), ',') from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'trace_import'), 'trace_import_writer', 'owned by the dedicated NOLOGIN writer');
select ok(not pg_has_role('postgres', 'trace_import_writer', 'SET'), 'nobody can act as the writer');

-- retention
select is((select column_default from information_schema.columns where table_schema = 'public' and table_name = 'trace_resume_documents'
            and column_name = 'retention_until'), null, 'no default retention date');
select is((select is_nullable from information_schema.columns where table_schema = 'public' and table_name = 'trace_resume_documents'
            and column_name = 'retention_until'), 'YES', 'retention_until may be empty');

insert into auth.users (id, email) values ('e9100000-0000-4000-8000-000000000001', 'pgtap-draft-a@example.invalid'),
                                          ('e9100000-0000-4000-8000-000000000002', 'pgtap-draft-b@example.invalid');
insert into public.trace_candidate_profiles (candidate_profile_id, owner_user_id)
values ('e9110000-0000-4000-8000-000000000001', 'e9100000-0000-4000-8000-000000000001'),
       ('e9110000-0000-4000-8000-000000000002', 'e9100000-0000-4000-8000-000000000002');
insert into public.trace_consent_documents (consent_version, purpose, title, body_sha256, legal_review_status, effective_from)
values ('pgtap-draft-share-v1', 'deidentified_shared_dataset', 'pgTAP share', repeat('d', 64), 'approved', now() - interval '1 day'),
       ('pgtap-draft-resume-v1', 'resume_processing', 'pgTAP resume', repeat('e', 64), 'approved', now() - interval '1 day');

set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "e9100000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select set_config('request.jwt.claim.sub', 'e9100000-0000-4000-8000-000000000001', true);

select isnt(trace_import.save_draft('e9110000-0000-4000-8000-000000000001', '{"record": {}}', 'p', 'd', 'docx',
              'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 100, repeat('a', 64), '{}', null, null),
            null, 'the owner saves a draft');
select lives_ok($$select trace_import.save_draft('e9110000-0000-4000-8000-000000000001', '{"record": {"n": 2}}', 'p', 'd', 'pdf',
                  'application/pdf', 100, repeat('b', 64), '{}', null, null)$$, 'saving again replaces it');
select is((select count(*)::int from trace_import.my_draft('e9110000-0000-4000-8000-000000000001')), 1, 'one draft per profile');
select is((select mime_type from trace_import.my_draft('e9110000-0000-4000-8000-000000000001', true)), 'application/pdf',
          'the owner can lock it for a confirmation');
select throws_ok($$select trace_import.save_draft('e9110000-0000-4000-8000-000000000001', '{}', 'p', 'd', 'pdf',
                   'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 100, repeat('b', 64), '{}', null, null)$$,
                 '23514', null, 'the format and MIME type must agree');
select throws_ok($$select trace_import.save_draft('e9110000-0000-4000-8000-000000000001', '{}', 'p', 'd', 'pdf', 'application/pdf',
                   100, repeat('b', 64), '{}', '\x01'::bytea, null)$$, '23514', null, 'sealed markers always carry their key id');
select throws_ok($$select trace_import.save_draft('e9110000-0000-4000-8000-000000000002', '{}', 'p', 'd', 'pdf', 'application/pdf',
                   100, repeat('b', 64), '{}', null, null)$$, null, null, 'nobody saves a draft into another user''s profile');

select set_config('request.jwt.claims', '{"sub": "e9100000-0000-4000-8000-000000000002", "role": "authenticated"}', true);
select set_config('request.jwt.claim.sub', 'e9100000-0000-4000-8000-000000000002', true);
select is((select count(*)::int from trace_import.my_draft('e9110000-0000-4000-8000-000000000001')), 0, 'another user sees no draft');
select is(trace_import.delete_draft('e9110000-0000-4000-8000-000000000001', null), 0, 'and cannot delete it');
reset role;

-- markers follow the contribution consent (called as the owner's request identity, as the runtime does)
select set_config('request.jwt.claims', '{"sub": "e9100000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select set_config('request.jwt.claim.sub', 'e9100000-0000-4000-8000-000000000001', true);
select throws_ok($$select trace_contrib.store_identity_markers('\x01'::bytea, '0123456789abcdef')$$, 'TR022', null,
                 'markers are refused without an active contribution consent');
insert into public.trace_consent_events (user_id, purpose, status, consent_version)
values ('e9100000-0000-4000-8000-000000000001', 'deidentified_shared_dataset', 'granted', 'pgtap-draft-share-v1');
select lives_ok($$select trace_contrib.store_identity_markers('\x000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d'::bytea, '0123456789abcdef')$$, 'and kept with it');
insert into public.trace_consent_events (user_id, purpose, status, consent_version)
values ('e9100000-0000-4000-8000-000000000001', 'deidentified_shared_dataset', 'withdrawn', 'pgtap-draft-share-v1');
select is((select count(*)::int from trace_private.identity_markers where owner_user_id = 'e9100000-0000-4000-8000-000000000001'), 0,
          'withdrawing the contribution consent deletes the markers');
insert into public.trace_consent_events (user_id, purpose, status, consent_version)
values ('e9100000-0000-4000-8000-000000000001', 'resume_processing', 'withdrawn', 'pgtap-draft-resume-v1');
select is((select count(*)::int from trace_private.resume_import_drafts where owner_user_id = 'e9100000-0000-4000-8000-000000000001'), 0,
          'withdrawing resume processing deletes the open draft');
select is((select count(*)::int from public.trace_candidate_profiles where owner_user_id = 'e9100000-0000-4000-8000-000000000001'), 1,
          'and leaves the profile itself');

select * from finish();
rollback;
