-- pgTAP tests for employment ordering events (v2.15.0, migration 20261116100000).
-- Run on a LOCAL Supabase stack only:  supabase test db
-- Synthetic test users and records only; everything runs in a transaction that is rolled back.
-- Records in states no client role may create (extracted, confirmed, corrected, rejected) are inserted as the table owner.

begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
set constraints all immediate;            -- deferred checks (correction history, unique order) fire at each statement
select plan(24);

insert into auth.users (id, email)
values ('a8000000-0000-4000-8000-000000000001', 'pgtap-order-a@example.invalid'),
       ('b8000000-0000-4000-8000-000000000002', 'pgtap-order-b@example.invalid');
insert into public.trace_candidate_profiles (candidate_profile_id, owner_user_id)
values ('a8000000-0000-4000-8000-0000000000aa', 'a8000000-0000-4000-8000-000000000001'),
       ('b8000000-0000-4000-8000-0000000000bb', 'b8000000-0000-4000-8000-000000000002');
insert into public.trace_candidate_employments (candidate_employment_id, owner_user_id, candidate_profile_id, sequence_index, title_raw,
  employment_type, review_status, information_origin)
values ('a8000000-0000-4000-8000-000000000101', 'a8000000-0000-4000-8000-000000000001', 'a8000000-0000-4000-8000-0000000000aa', 1,
        'Engineer', 'full_time', 'user_confirmed', 'user_stated'),
       ('a8000000-0000-4000-8000-000000000102', 'a8000000-0000-4000-8000-000000000001', 'a8000000-0000-4000-8000-0000000000aa', 2,
        'Analyst', 'full_time', 'pending_review', 'source_extracted'),
       ('a8000000-0000-4000-8000-000000000103', 'a8000000-0000-4000-8000-000000000001', 'a8000000-0000-4000-8000-0000000000aa', 3,
        'Rejected role', 'full_time', 'rejected', 'source_extracted'),
       ('a8000000-0000-4000-8000-000000000104', 'a8000000-0000-4000-8000-000000000001', 'a8000000-0000-4000-8000-0000000000aa', 4,
        'Corrected role', 'full_time', 'user_corrected', 'source_extracted'),
       ('b8000000-0000-4000-8000-000000000201', 'b8000000-0000-4000-8000-000000000002', 'b8000000-0000-4000-8000-0000000000bb', 1,
        'Other user role', 'full_time', 'user_confirmed', 'user_stated');

-- ------------------------------------------------------------------ 1. structure and privileges
select ok(to_regclass('public.trace_candidate_order_events') is not null
          and (select relrowsecurity from pg_class where oid = 'public.trace_candidate_order_events'::regclass), 'the events table exists with RLS');
select ok(has_table_privilege('authenticated', 'public.trace_candidate_order_events', 'SELECT')
          and not has_table_privilege('authenticated', 'public.trace_candidate_order_events', 'INSERT')
          and not has_table_privilege('authenticated', 'public.trace_candidate_order_events', 'UPDATE')
          and not has_table_privilege('authenticated', 'public.trace_candidate_order_events', 'DELETE')
          and not has_table_privilege('anon', 'public.trace_candidate_order_events', 'SELECT')
          and not has_table_privilege('trace_app_user', 'public.trace_candidate_order_events', 'INSERT'),
          'clients only read events; nobody but the move function writes them');
select is(pg_get_userbyid((select proowner from pg_proc where oid = 'trace_review.move_employment(uuid,uuid,integer,text,uuid)'::regprocedure)),
          'trace_review_writer', 'the move function is owned by the review writer');
select ok(has_function_privilege('trace_app_user', 'trace_review.move_employment(uuid,uuid,integer,text,uuid)', 'EXECUTE')
          and not has_function_privilege('anon', 'trace_review.move_employment(uuid,uuid,integer,text,uuid)', 'EXECUTE')
          and not has_function_privilege('service_role', 'trace_review.move_employment(uuid,uuid,integer,text,uuid)', 'EXECUTE'),
          'only the backend owner role may call it');

-- ------------------------------------------------------------------ 2. moves keep the review status and write one event each
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a8000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select set_config('request.jwt.claim.sub', 'a8000000-0000-4000-8000-000000000001', true);
set constraints all deferred;              -- a shift may pass through a duplicate position inside the transaction
select lives_ok($$ select trace_review.move_employment('a8000000-0000-4000-8000-0000000000aa', 'a8000000-0000-4000-8000-000000000104', 5,
                                                      'role_inserted', 'a8000000-0000-4000-8000-000000000199') $$,
                'a corrected role is moved');
select lives_ok($$ select trace_review.move_employment('a8000000-0000-4000-8000-0000000000aa', 'a8000000-0000-4000-8000-000000000102', 6,
                                                      'role_inserted', null) $$,
                'an unreviewed extracted role is moved');
select lives_ok($$ select trace_review.move_employment('a8000000-0000-4000-8000-0000000000aa', 'a8000000-0000-4000-8000-000000000101', 2,
                                                      'role_inserted', null) $$,
                'a confirmed role is moved');
set constraints all immediate;             -- the deferred correction-history check runs now, for every move
select ok(true, 'the deferred correction-history check accepted the moves (none needed a correction)');
select is((select string_agg(title_raw || ':' || sequence_index || ':' || review_status, ',' order by title_raw)
           from public.trace_candidate_employments where candidate_profile_id = 'a8000000-0000-4000-8000-0000000000aa'),
          'Analyst:6:pending_review,Corrected role:5:user_corrected,Engineer:2:user_confirmed,Rejected role:3:rejected',
          'positions changed; every review status is unchanged');
select is((select count(*)::int from public.trace_candidate_corrections where candidate_profile_id = 'a8000000-0000-4000-8000-0000000000aa'), 0,
          'no correction history was written for a position change');
select is((select string_agg(previous_sequence_index || '>' || new_sequence_index || ':' || review_status_at_move || ':' || cause, ',' order by event_seq)
           from public.trace_candidate_order_events),
          '4>5:user_corrected:role_inserted,2>6:pending_review:role_inserted,1>2:user_confirmed:role_inserted',
          'one event per move, with old and new position, status at the move and cause');
select ok((select bool_and(moved_by = 'a8000000-0000-4000-8000-000000000001' and moved_at is not null and txid = txid_current())
           from public.trace_candidate_order_events), 'each event names the actor, the time and the transaction');
select is((select caused_by_employment_id::text from public.trace_candidate_order_events where employment_id = 'a8000000-0000-4000-8000-000000000104'),
          'a8000000-0000-4000-8000-000000000199', 'the role whose insertion caused a move is recorded');
select lives_ok($$ select trace_review.move_employment('a8000000-0000-4000-8000-0000000000aa', 'a8000000-0000-4000-8000-000000000101', 2,
                                                      'user_reordered', null) $$, 'moving to the same position is a no-op');
select is((select count(*)::int from public.trace_candidate_order_events), 3, 'a no-op writes no event');

-- ------------------------------------------------------------------ 3. refusals
select throws_ok($$ select trace_review.move_employment('a8000000-0000-4000-8000-0000000000aa', 'a8000000-0000-4000-8000-000000000103', 9,
                                                       'role_inserted', null) $$, 'TR001', null, 'a rejected role cannot be moved');
select throws_ok($$ select trace_review.move_employment('b8000000-0000-4000-8000-0000000000bb', 'b8000000-0000-4000-8000-000000000201', 9,
                                                       'role_inserted', null) $$, 'TR005', null, 'another user''s role is not found');
select throws_ok($$ select trace_review.move_employment('a8000000-0000-4000-8000-0000000000aa', 'a8000000-0000-4000-8000-000000000101', 0,
                                                       'role_inserted', null) $$, 'TR006', null, 'positions start at 1');
select throws_ok($$ update public.trace_candidate_employments set sequence_index = 7
                    where candidate_employment_id = 'a8000000-0000-4000-8000-000000000101' $$, 'TR002', null,
                 'the owner role cannot change a reviewed role''s position directly (content guard)');
select throws_ok($$ insert into public.trace_candidate_order_events (owner_user_id, candidate_profile_id, employment_id,
                      previous_sequence_index, new_sequence_index, review_status_at_move, cause, moved_by)
                    values ('a8000000-0000-4000-8000-000000000001', 'a8000000-0000-4000-8000-0000000000aa', 'a8000000-0000-4000-8000-000000000101',
                            2, 8, 'user_confirmed', 'user_reordered', 'a8000000-0000-4000-8000-000000000001') $$, '42501', null,
                 'an event cannot be forged by the owner role');
reset role;
select throws_ok($$ update public.trace_candidate_order_events set new_sequence_index = 9 $$, 'TR023', null, 'events cannot be edited');
select throws_ok($$ delete from public.trace_candidate_order_events $$, 'TR023', null, 'events cannot be deleted directly');
select throws_ok($$ update public.trace_candidate_employments set sequence_index = 9
                    where candidate_employment_id = 'a8000000-0000-4000-8000-000000000101' $$, 'TR002', null,
                 'a reviewed role''s position never changes without the move function (the content guard still applies)');

-- ------------------------------------------------------------------ 4. history goes with its role
delete from public.trace_candidate_profiles where candidate_profile_id = 'a8000000-0000-4000-8000-0000000000aa';
select is((select count(*)::int from public.trace_candidate_order_events), 0, 'events are deleted with the profile (cascade)');

select * from finish();
rollback;
