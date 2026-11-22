-- v2.11.0: sealed identity markers, marker lifecycle and queue-event retention. Rolled back.
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(12);

select ok(not has_function_privilege('trace_app_runtime', 'trace_private.purge_orphan_identity_markers(interval, boolean)', 'EXECUTE')
          and not has_function_privilege('trace_app_runtime', 'trace_private.purge_contribution_queue_events(integer, boolean)', 'EXECUTE')
          and not has_function_privilege('authenticated', 'trace_private.purge_orphan_identity_markers(interval, boolean)', 'EXECUTE'),
          'maintenance is for the admin identity only');
select ok(has_function_privilege('trace_app_runtime', 'trace_contrib.forget_identity_markers()', 'EXECUTE')
          and not has_function_privilege('trace_app_user', 'trace_contrib.forget_identity_markers()', 'EXECUTE')
          and not has_function_privilege('authenticated', 'trace_contrib.forget_identity_markers()', 'EXECUTE'),
          'only the runtime may forget the caller''s own markers');
select is((select count(*)::int from pg_proc where proname = 'store_identity_markers'), 1, 'the plaintext store function is gone');

insert into auth.users (id, email) values ('00000000-0000-4000-8000-0000000000e1', 'pgtap-e1@example.invalid'),
                                          ('00000000-0000-4000-8000-0000000000e2', 'pgtap-e2@example.invalid');
insert into public.trace_candidate_profiles (candidate_profile_id, owner_user_id)
values ('00000000-0000-4000-8000-0000000000f1', '00000000-0000-4000-8000-0000000000e1'),
       ('00000000-0000-4000-8000-0000000000f2', '00000000-0000-4000-8000-0000000000e2');
insert into public.trace_resume_documents (resume_id, owner_user_id, candidate_profile_id, storage_object_path, byte_size, content_sha256, mime_type)
values ('00000000-0000-4000-8000-0000000000c1', '00000000-0000-4000-8000-0000000000e1', '00000000-0000-4000-8000-0000000000f1',
        '00000000-0000-4000-8000-0000000000e1/r.pdf', 100, repeat('a', 64), 'application/pdf');
insert into trace_ingest.deidentified_resume_admissions (owner_user_id, candidate_profile_id, resume_id, content_sha256, pipeline_version)
values ('00000000-0000-4000-8000-0000000000e1', '00000000-0000-4000-8000-0000000000f1', '00000000-0000-4000-8000-0000000000c1', repeat('a', 64), 'pgtap');
-- v2.31.0 (D48): markers are needed only while the owner's contribution consent is active.
insert into public.trace_consent_documents (consent_version, purpose, title, body_sha256, legal_review_status, effective_from)
values ('pgtap-mp-share-v1', 'deidentified_shared_dataset', 'pgTAP share', repeat('c', 64), 'approved', now() - interval '1 day');
insert into public.trace_consent_events (user_id, purpose, status, consent_version)
values ('00000000-0000-4000-8000-0000000000e1', 'deidentified_shared_dataset', 'granted', 'pgtap-mp-share-v1');
insert into trace_private.identity_markers (owner_user_id, candidate_profile_id, sealed, key_id, updated_at)
values ('00000000-0000-4000-8000-0000000000e1', '00000000-0000-4000-8000-0000000000f1', decode(repeat('ab', 40), 'hex'), '0123456789abcdef', now() - interval '3 days'),
       ('00000000-0000-4000-8000-0000000000e2', '00000000-0000-4000-8000-0000000000f2', decode(repeat('ab', 40), 'hex'), '0123456789abcdef', now() - interval '3 days');

select ok(trace_private.identity_markers_needed('00000000-0000-4000-8000-0000000000e1', '00000000-0000-4000-8000-0000000000f1'),
          'markers of a contributor''s profile with an admitted resume are needed');
select ok(not trace_private.identity_markers_needed('00000000-0000-4000-8000-0000000000e2', '00000000-0000-4000-8000-0000000000f2'),
          'markers without an admitted or held resume are not');
select throws_ok($$select trace_private.purge_orphan_identity_markers(interval '10 minutes', true)$$, '22023', null, 'grace period of at least an hour');
select ok(trace_private.purge_orphan_identity_markers(interval '1 day', false) >= 1, 'a dry run counts orphans');
select is((select count(*)::int from trace_private.identity_markers where owner_user_id in
            ('00000000-0000-4000-8000-0000000000e1', '00000000-0000-4000-8000-0000000000e2')), 2, 'a dry run removes nothing');
select ok(trace_private.purge_orphan_identity_markers(interval '1 day', true) >= 1, 'orphans are purged');
select is((select array_agg(owner_user_id::text order by owner_user_id) from trace_private.identity_markers where owner_user_id in
            ('00000000-0000-4000-8000-0000000000e1', '00000000-0000-4000-8000-0000000000e2')),
          array['00000000-0000-4000-8000-0000000000e1'], 'the active profile keeps its markers');

select throws_ok($$select trace_private.purge_contribution_queue_events(3, false)$$, '22023', null, 'retention of at least 7 days');
insert into trace_private.contribution_queue_events (user_id, event, attempts, error_code, created_at)
values ('00000000-0000-4000-8000-0000000000e1', 'attempt_failed', 1, 'RuntimeError', now() - interval '400 days');
select ok(trace_private.purge_contribution_queue_events(365, true) >= 1, 'events past retention are purged');

select * from finish();
rollback;
