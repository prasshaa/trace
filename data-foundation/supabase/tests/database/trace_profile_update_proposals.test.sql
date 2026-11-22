-- pgTAP tests for profile update proposals (v2.14.0, migration 20261115110000).
-- Run on a LOCAL Supabase stack only:  supabase test db
-- Synthetic test users and records only; everything runs in a transaction that is rolled back.

begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(25);

-- ------------------------------------------------------------------ fixtures (as owner)
insert into auth.users (id, email)
values ('a7000000-0000-4000-8000-000000000001', 'pgtap-proposal-a@example.invalid'),
       ('b7000000-0000-4000-8000-000000000002', 'pgtap-proposal-b@example.invalid');
insert into public.trace_candidate_profiles (candidate_profile_id, owner_user_id)
values ('a7000000-0000-4000-8000-0000000000aa', 'a7000000-0000-4000-8000-000000000001'),
       ('b7000000-0000-4000-8000-0000000000bb', 'b7000000-0000-4000-8000-000000000002');
insert into public.trace_candidate_employments (candidate_employment_id, owner_user_id, candidate_profile_id, sequence_index, title_raw,
  employment_type, review_status, information_origin)
values ('a7000000-0000-4000-8000-000000000101', 'a7000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-0000000000aa', 1,
        'Business Analyst', 'full_time', 'user_confirmed', 'user_stated'),
       ('a7000000-0000-4000-8000-000000000102', 'a7000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-0000000000aa', 2,
        'Product Analyst', 'full_time', 'pending_review', 'user_stated'),
       ('b7000000-0000-4000-8000-000000000201', 'b7000000-0000-4000-8000-000000000002', 'b7000000-0000-4000-8000-0000000000bb', 1,
        'Engineer', 'full_time', 'user_confirmed', 'user_stated');

-- ------------------------------------------------------------------ 1. structure and grants
select ok(to_regclass('public.trace_profile_update_proposals') is not null, 'the proposals table exists');
select ok((select relrowsecurity from pg_class where oid = 'public.trace_profile_update_proposals'::regclass), 'RLS is enabled');
select ok(has_table_privilege('authenticated', 'public.trace_profile_update_proposals', 'SELECT')
          and not has_table_privilege('authenticated', 'public.trace_profile_update_proposals', 'INSERT')
          and not has_table_privilege('authenticated', 'public.trace_profile_update_proposals', 'UPDATE')
          and not has_table_privilege('authenticated', 'public.trace_profile_update_proposals', 'DELETE'),
          'the browser role can only read proposals');
select ok(not has_table_privilege('anon', 'public.trace_profile_update_proposals', 'SELECT')
          and not has_table_privilege('service_role', 'public.trace_profile_update_proposals', 'INSERT'),
          'anon cannot read; service_role cannot write');
select ok(has_column_privilege('trace_app_user', 'public.trace_profile_update_proposals', 'proposed', 'INSERT')
          and has_column_privilege('trace_app_user', 'public.trace_profile_update_proposals', 'status', 'UPDATE')
          and not has_column_privilege('trace_app_user', 'public.trace_profile_update_proposals', 'status', 'INSERT')
          and not has_column_privilege('trace_app_user', 'public.trace_profile_update_proposals', 'fingerprint', 'UPDATE')
          and not has_column_privilege('trace_app_user', 'public.trace_profile_update_proposals', 'resulting_record_id', 'INSERT')
          and not has_table_privilege('trace_app_user', 'public.trace_profile_update_proposals', 'DELETE'),
          'the owner role inserts open proposals and decides them; it cannot pre-decide, rewrite or delete');

-- ------------------------------------------------------------------ 2. owner inserts and reads
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a7000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select lives_ok($$
  insert into public.trace_profile_update_proposals (proposal_id, owner_user_id, candidate_profile_id, batch_id, source, source_sha256,
    extractor_version, record_kind, match_state, target_record_id, fingerprint, proposed)
  values ('a7000000-0000-4000-8000-000000000301', 'a7000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-0000000000aa',
          'a7000000-0000-4000-8000-000000000900', 'pasted_text', repeat('a', 64), 'trace-resume-parser-1', 'employment', 'new', null,
          repeat('1', 64), '{"title_raw": "Product Owner"}'),
         ('a7000000-0000-4000-8000-000000000302', 'a7000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-0000000000aa',
          'a7000000-0000-4000-8000-000000000900', 'pasted_text', repeat('a', 64), 'trace-resume-parser-1', 'evidence', 'new',
          'a7000000-0000-4000-8000-000000000101', repeat('2', 64), '{"evidence_text": "Ran discovery interviews"}'),
         ('a7000000-0000-4000-8000-000000000303', 'a7000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-0000000000aa',
          'a7000000-0000-4000-8000-000000000900', 'resume_update', repeat('b', 64), 'trace-resume-parser-1', 'employment',
          'possible_update', 'a7000000-0000-4000-8000-000000000101', repeat('3', 64), '{"start": {"year": 2018}}')
$$, 'the owner inserts new items and a possible update');
select is((select count(*)::int from public.trace_profile_update_proposals), 3, 'the owner sees their own proposals');
select throws_ok($$
  insert into public.trace_profile_update_proposals (owner_user_id, candidate_profile_id, batch_id, source, source_sha256,
    extractor_version, record_kind, match_state, fingerprint, proposed)
  values ('a7000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-0000000000aa', gen_random_uuid(), 'pasted_text',
          repeat('a', 64), 'v', 'employment', 'new', repeat('1', 64), '{"title_raw": "Product Owner"}')
$$, '23505', null, 'the same open item cannot be proposed twice');
select throws_ok($$
  insert into public.trace_profile_update_proposals (owner_user_id, candidate_profile_id, batch_id, source, source_sha256,
    extractor_version, record_kind, match_state, target_record_id, fingerprint, proposed)
  values ('a7000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-0000000000aa', gen_random_uuid(), 'pasted_text',
          repeat('a', 64), 'v', 'evidence', 'new', 'b7000000-0000-4000-8000-000000000201', repeat('4', 64), '{"evidence_text": "x"}')
$$, 'TR022', null, 'a proposal cannot point at another user''s record');
select throws_ok($$
  insert into public.trace_profile_update_proposals (owner_user_id, candidate_profile_id, batch_id, source, source_sha256,
    extractor_version, record_kind, match_state, fingerprint, proposed)
  values ('b7000000-0000-4000-8000-000000000002', 'b7000000-0000-4000-8000-0000000000bb', gen_random_uuid(), 'pasted_text',
          repeat('a', 64), 'v', 'skill', 'new', repeat('5', 64), '{"skill_text_raw": "SQL"}')
$$, '42501', null, 'the owner cannot insert into another user''s profile (RLS)');
select throws_ok($$
  insert into public.trace_profile_update_proposals (owner_user_id, candidate_profile_id, batch_id, source, source_sha256,
    extractor_version, record_kind, match_state, fingerprint, proposed)
  values ('a7000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-0000000000aa', gen_random_uuid(), 'voice',
          repeat('a', 64), 'v', 'skill', 'new', repeat('6', 64), '{"skill_text_raw": "SQL"}')
$$, '23514', null, 'only known sources are accepted');
select throws_ok($$
  insert into public.trace_profile_update_proposals (owner_user_id, candidate_profile_id, batch_id, source, source_sha256,
    extractor_version, record_kind, match_state, fingerprint, proposed)
  values ('a7000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-0000000000aa', gen_random_uuid(), 'pasted_text',
          repeat('a', 64), 'v', 'skill', 'possible_update', repeat('7', 64), '{"skill_text_raw": "SQL"}')
$$, '23514', null, 'a possible update must name the record it would change (and only employment or education)');

-- ------------------------------------------------------------------ 3. decisions
select throws_ok($$
  update public.trace_profile_update_proposals set status = 'accepted', proposed = null, decided_at = now(),
         resulting_record_id = 'b7000000-0000-4000-8000-000000000201'
   where proposal_id = 'a7000000-0000-4000-8000-000000000301'
$$, 'TR022', null, 'accepting must name a record of this profile');
select throws_ok($$
  update public.trace_profile_update_proposals set status = 'accepted', decided_at = now(),
         resulting_record_id = 'a7000000-0000-4000-8000-000000000102'
   where proposal_id = 'a7000000-0000-4000-8000-000000000301'
$$, '23514', null, 'the proposed content must be cleared when decided');
select lives_ok($$
  update public.trace_profile_update_proposals set status = 'accepted', proposed = null, decided_at = now(),
         resulting_record_id = 'a7000000-0000-4000-8000-000000000102'
   where proposal_id = 'a7000000-0000-4000-8000-000000000301'
$$, 'a new item is accepted with the record it produced; its content is cleared');
select throws_ok($$
  update public.trace_profile_update_proposals set status = 'dismissed', resulting_record_id = null
   where proposal_id = 'a7000000-0000-4000-8000-000000000301'
$$, 'TR022', null, 'a decision is final');
select throws_ok($$
  update public.trace_profile_update_proposals set status = 'accepted', proposed = null, decided_at = now(),
         resulting_record_id = 'a7000000-0000-4000-8000-000000000102'
   where proposal_id = 'a7000000-0000-4000-8000-000000000303'
$$, 'TR022', null, 'an accepted update must change the record it named');
select lives_ok($$
  update public.trace_profile_update_proposals set status = 'accepted', proposed = null, decided_at = now(),
         resulting_record_id = 'a7000000-0000-4000-8000-000000000101'
   where proposal_id = 'a7000000-0000-4000-8000-000000000303'
$$, 'an update is accepted against the record it named');
select lives_ok($$
  update public.trace_profile_update_proposals set status = 'dismissed', proposed = null, decided_at = now()
   where proposal_id = 'a7000000-0000-4000-8000-000000000302'
$$, 'a proposal is dismissed and its content cleared');
select is((select count(*)::int from public.trace_profile_update_proposals where proposed is not null), 0,
          'no decided proposal keeps proposed content');
select throws_ok($$
  update public.trace_profile_update_proposals set fingerprint = repeat('9', 64)
   where proposal_id = 'a7000000-0000-4000-8000-000000000302'
$$, '42501', null, 'content-derived columns cannot be rewritten (column grants)');
select lives_ok($$
  insert into public.trace_profile_update_proposals (owner_user_id, candidate_profile_id, batch_id, source, source_sha256,
    extractor_version, record_kind, match_state, fingerprint, proposed)
  values ('a7000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-0000000000aa', gen_random_uuid(), 'pasted_text',
          repeat('a', 64), 'v', 'employment', 'new', repeat('1', 64), '{"title_raw": "Product Owner"}')
$$, 'after a decision, the same item may be proposed again');

-- ------------------------------------------------------------------ 4. isolation and deletion
select set_config('request.jwt.claims', '{"sub": "b7000000-0000-4000-8000-000000000002", "role": "authenticated"}', true);
select is((select count(*)::int from public.trace_profile_update_proposals), 0, 'another user sees none of them');
reset role;
set local role authenticated;
select set_config('request.jwt.claims', '{"sub": "a7000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select is((select count(*)::int from public.trace_profile_update_proposals), 4, 'the owner can read their own through the Data API role');
reset role;
delete from public.trace_candidate_profiles where candidate_profile_id = 'a7000000-0000-4000-8000-0000000000aa';
select is((select count(*)::int from public.trace_profile_update_proposals
           where candidate_profile_id = 'a7000000-0000-4000-8000-0000000000aa'), 0, 'proposals are deleted with the profile');

select * from finish();
rollback;
