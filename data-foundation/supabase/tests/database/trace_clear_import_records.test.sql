-- v2.33.0: clear my career profile. The import-records function is owner-scoped and idempotent; the owner's own delete
-- of the profile cascades through every dependent record, and never touches another user. Rolled back.
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(9);

select ok(has_function_privilege('trace_app_user', 'trace_import.clear_my_import_records()', 'EXECUTE')
          and not has_function_privilege('authenticated', 'trace_import.clear_my_import_records()', 'EXECUTE')
          and not has_function_privilege('anon', 'trace_import.clear_my_import_records()', 'EXECUTE')
          and not has_function_privilege('service_role', 'trace_import.clear_my_import_records()', 'EXECUTE'),
          'only the owner role calls the function');
select is((select pg_get_userbyid(p.proowner)::text from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'trace_import' and p.proname = 'clear_my_import_records'), 'trace_import_writer', 'owned by the import writer');
select ok(not has_table_privilege('trace_app_user', 'trace_private.resume_quarantine', 'DELETE')
          and not has_table_privilege('trace_app_user', 'trace_private.resume_processing_log', 'DELETE'),
          'no client delete grant on the quarantine or the processing log');

insert into auth.users (id, email) values ('e9300000-0000-4000-8000-000000000001', 'pgtap-clear-a@example.invalid'),
                                          ('e9300000-0000-4000-8000-000000000002', 'pgtap-clear-b@example.invalid');
insert into public.trace_candidate_profiles (candidate_profile_id, owner_user_id) values
  ('e9310000-0000-4000-8000-000000000001', 'e9300000-0000-4000-8000-000000000001'),
  ('e9310000-0000-4000-8000-000000000002', 'e9300000-0000-4000-8000-000000000002');
insert into trace_private.resume_processing_log (owner_user_id, outcome) values
  ('e9300000-0000-4000-8000-000000000001', 'admitted'), ('e9300000-0000-4000-8000-000000000001', 'failed'),
  ('e9300000-0000-4000-8000-000000000002', 'admitted');
insert into trace_private.resume_quarantine (owner_user_id, candidate_profile_id, content_sha256, record, report) values
  ('e9300000-0000-4000-8000-000000000001', 'e9310000-0000-4000-8000-000000000001', repeat('a', 64), '{}', '{}'),
  ('e9300000-0000-4000-8000-000000000002', 'e9310000-0000-4000-8000-000000000002', repeat('b', 64), '{}', '{}');

set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "e9300000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select is((select quarantined || ':' || log_entries from trace_import.clear_my_import_records()), '1:2', 'the caller''s rows are deleted');
select is((select quarantined || ':' || log_entries from trace_import.clear_my_import_records()), '0:0', 'a retry deletes nothing');
select is((select count(*)::int from public.trace_candidate_profiles where candidate_profile_id = 'e9310000-0000-4000-8000-000000000002'), 0,
          'another user''s profile is invisible to the owner');
delete from public.trace_candidate_profiles where candidate_profile_id = 'e9310000-0000-4000-8000-000000000002';
delete from public.trace_candidate_profiles where candidate_profile_id = 'e9310000-0000-4000-8000-000000000001';
reset role;
select is((select count(*)::int from public.trace_candidate_profiles where candidate_profile_id = 'e9310000-0000-4000-8000-000000000001'), 0,
          'the owner deletes their own profile');
select is((select count(*)::int from public.trace_candidate_profiles where candidate_profile_id = 'e9310000-0000-4000-8000-000000000002'), 1,
          'a delete aimed at another user''s profile deletes nothing');
select is((select count(*)::int from trace_private.resume_processing_log where owner_user_id = 'e9300000-0000-4000-8000-000000000002')
          + (select count(*)::int from trace_private.resume_quarantine where owner_user_id = 'e9300000-0000-4000-8000-000000000002'), 2,
          'the other user''s log and quarantine are untouched');

select * from finish();
rollback;
