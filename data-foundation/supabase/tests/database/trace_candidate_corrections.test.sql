-- pgTAP tests for corrections and append-only correction history (v2.5.0, migration 20261111170000).
-- Run on a LOCAL Supabase stack only:  supabase test db
-- Synthetic test users and records only; everything runs in a transaction that is rolled back.
-- Records in states no client role may create (extracted, confirmed, rejected) are inserted as the
-- table owner; every behaviour under test then runs as authenticated or as the review writer.

begin;
create extension if not exists pgtap with schema extensions;
select plan(59);

-- ------------------------------------------------------------------ fixtures (as owner)
insert into auth.users (id, email)
values ('a4000000-0000-4000-8000-000000000001', 'pgtap-correct-a@example.invalid'),
       ('b4000000-0000-4000-8000-000000000002', 'pgtap-correct-b@example.invalid');
insert into public.trace_candidate_profiles (candidate_profile_id, owner_user_id)
values ('a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000001'),
       ('b4000000-0000-4000-8000-0000000000bb', 'b4000000-0000-4000-8000-000000000002');
insert into public.trace_resume_documents (resume_id, owner_user_id, candidate_profile_id, storage_object_path, mime_type, byte_size)
values ('a4000000-0000-4000-8000-000000000701', 'a4000000-0000-4000-8000-000000000001', 'a4000000-0000-4000-8000-0000000000aa',
        'a4000000-0000-4000-8000-000000000001/a4000000-0000-4000-8000-000000000701.pdf', 'application/pdf', 1000);
insert into public.trace_candidate_employers (candidate_employer_id, owner_user_id, candidate_profile_id, employer_name)
values ('a4000000-0000-4000-8000-0000000000e1', 'a4000000-0000-4000-8000-000000000001', 'a4000000-0000-4000-8000-0000000000aa', 'Synthetic employer');
insert into public.trace_candidate_employments (candidate_employment_id, owner_user_id, candidate_profile_id, sequence_index, title_raw,
  employment_type, start_year, start_month, start_precision, review_status, information_origin, extraction_confidence, source_resume_id, source_location)
select x.id::uuid, 'a4000000-0000-4000-8000-000000000001', 'a4000000-0000-4000-8000-0000000000aa', x.n, x.title, 'full_time', 2020, 1, 'month',
       x.status::public.trace_record_review_status, x.origin::public.trace_information_origin,
       case when x.origin = 'source_extracted' then 0.800 end, case when x.origin = 'source_extracted' then 'a4000000-0000-4000-8000-000000000701'::uuid end,
       case when x.origin = 'source_extracted' then 'Professional experience, entry ' || x.n end
from (values ('a4000000-0000-4000-8000-000000000101', 1, 'Associate Product Manager', 'pending_review', 'source_extracted'),
             ('a4000000-0000-4000-8000-000000000102', 2, 'Confirmed PM', 'user_confirmed', 'user_stated'),
             ('a4000000-0000-4000-8000-000000000103', 3, 'Rejected PM', 'rejected', 'source_extracted'),
             ('a4000000-0000-4000-8000-000000000104', 4, 'Draft PM', 'pending_review', 'user_stated'),
             ('a4000000-0000-4000-8000-000000000105', 5, 'Writer probe', 'pending_review', 'source_extracted'),
             ('a4000000-0000-4000-8000-000000000106', 6, 'Delete me', 'pending_review', 'source_extracted')) as x(id, n, title, status, origin);
insert into public.trace_candidate_skills (candidate_skill_id, owner_user_id, candidate_profile_id, skill_text_raw, information_origin,
  extraction_confidence, review_status)
values ('a4000000-0000-4000-8000-000000000401', 'a4000000-0000-4000-8000-000000000001', 'a4000000-0000-4000-8000-0000000000aa', 'Sql',
        'inferred', 0.600, 'pending_review');

-- ------------------------------------------------------------------ 1. structure and privileges
select ok((select relrowsecurity from pg_class where oid = 'public.trace_candidate_corrections'::regclass), 'correction history has RLS enabled');
select is((select string_agg(r.rolname || ':' || x.p, ' ' order by r.rolname, x.p)
           from (values ('anon'), ('trace_app_user'), ('service_role'), ('trace_review_writer'), ('trace_ingest_writer')) r(rolname)
           cross join (values ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'), ('TRUNCATE'), ('TRIGGER'), ('REFERENCES')) x(p)
           where has_table_privilege(r.rolname, 'public.trace_candidate_corrections', x.p)
              or (x.p = 'INSERT' and has_any_column_privilege(r.rolname, 'public.trace_candidate_corrections', 'INSERT'))),
          'service_role:SELECT trace_app_user:SELECT trace_review_writer:INSERT trace_review_writer:SELECT',
          'the owner role and service_role read history; only the review writer appends; nobody updates, deletes or truncates');
select ok(not has_column_privilege('trace_review_writer', 'public.trace_candidate_corrections', 'txid', 'INSERT')
          and not has_column_privilege('trace_review_writer', 'public.trace_candidate_corrections', 'corrected_at', 'INSERT'),
          'the writer cannot choose the transaction or time of a history entry');
select is((select pg_get_userbyid(proowner) || ':' || prosecdef || ':' || array_to_string(proconfig, ',')
           from pg_proc where oid = 'trace_review.correct_record(text, uuid, uuid, jsonb)'::regprocedure),
          'trace_review_writer:true:search_path=""', 'correct_record is writer-owned, SECURITY DEFINER, with a pinned search_path');
select ok(has_function_privilege('trace_app_user', 'trace_review.correct_record(text, uuid, uuid, jsonb)', 'EXECUTE')
          and not has_function_privilege('anon', 'trace_review.correct_record(text, uuid, uuid, jsonb)', 'EXECUTE')
          and not has_function_privilege('service_role', 'trace_review.correct_record(text, uuid, uuid, jsonb)', 'EXECUTE')
          and not exists (select 1 from aclexplode((select proacl from pg_proc where oid = 'trace_review.correct_record(text, uuid, uuid, jsonb)'::regprocedure)) a
                          where a.grantee = 0),
          'only the backend owner role (trace_app_user) calls correct_record');
select ok(has_column_privilege('trace_review_writer', 'public.trace_candidate_employments', 'title_raw', 'UPDATE')
          and not has_column_privilege('trace_review_writer', 'public.trace_candidate_skills', 'skill_id', 'UPDATE')
          and not has_column_privilege('trace_review_writer', 'public.trace_candidate_evidence', 'responsibility_area', 'UPDATE')
          and not has_column_privilege('trace_review_writer', 'public.trace_candidate_evidence', 'candidate_employment_id', 'UPDATE')
          and not has_column_privilege('trace_review_writer', 'public.trace_candidate_career_activities', 'related_employment_id', 'UPDATE')
          -- v2.6.0: role_id is writable by the writer, for the mapping functions only (trace_candidate_role_mapping.test.sql)
          and not has_column_privilege('trace_review_writer', 'public.trace_candidate_employments', 'information_origin', 'UPDATE')
          and not has_column_privilege('trace_review_writer', 'public.trace_candidate_employments', 'source_resume_id', 'UPDATE'),
          'the writer may update correctable content only: no taxonomy, link, mapping or provenance column');
select is((select count(*)::int from pg_trigger where tgname = 'trace_require_correction_history' and tgdeferrable and tginitdeferred), 6,
          'the six record tables carry the deferred correction-history check');

-- ------------------------------------------------------------------ 2. corrections (as candidate A)
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a4000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select is(trace_review.correct_record('employment', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000101',
                                      '{"title_raw": "Product Manager"}'), 'user_corrected', 'a pending extracted record is corrected');
select is(trace_review.correct_record('employment', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000101',
                                      '{"title_raw": "Senior Product Manager", "start_month": 3}'), 'user_corrected', 'and corrected again');
select is((select title_raw || '/' || start_month || '/' || review_status || '/' || information_origin || '/' || extraction_confidence || '/'
                  || source_location || '/' || (source_resume_id = 'a4000000-0000-4000-8000-000000000701')
           from public.trace_candidate_employments where candidate_employment_id = 'a4000000-0000-4000-8000-000000000101'),
          'Senior Product Manager/3/user_corrected/source_extracted/0.800/Professional experience, entry 1/true',
          'the record holds the latest values; origin, confidence, source and location are kept');
select is((select string_agg(array_to_string(changed_fields, ',') || ':' || previous_values::text || '->' || corrected_values::text || ':' || prior_review_status,
                             ' | ' order by correction_seq)
           from public.trace_candidate_corrections where record_id = 'a4000000-0000-4000-8000-000000000101'),
          'title_raw:{"title_raw": "Associate Product Manager"}->{"title_raw": "Product Manager"}:pending_review | '
          'start_month,title_raw:{"title_raw": "Product Manager", "start_month": 1}->{"title_raw": "Senior Product Manager", "start_month": 3}:user_corrected',
          'history keeps every transition, including the original extracted value');
select is(trace_review.correct_record('employment', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000102',
                                      '{"title_raw": "Product Manager"}'), 'user_corrected', 'a confirmed record is corrected');
select is(trace_review.correct_record('skill', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000401',
                                      '{"skill_text_raw": "SQL"}'), 'user_corrected', 'a pending inferred record is corrected');
select is((select prior_review_status::text from public.trace_candidate_corrections where record_id = 'a4000000-0000-4000-8000-000000000102'),
          'user_confirmed', 'the history records the status before the correction');
select throws_ok($$select trace_review.correct_record('employment', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000103', '{"title_raw": "x"}')$$,
                 'TR001', null, 'a rejected record cannot be corrected');
select throws_ok($$select trace_review.correct_record('employment', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000104', '{"title_raw": "x"}')$$,
                 'TR002', null, 'a pending user_stated draft is edited, not corrected');
select lives_ok($$update public.trace_candidate_employments set title_raw = 'Draft PM (edited)' where candidate_employment_id = 'a4000000-0000-4000-8000-000000000104'$$,
                'the draft edit still works');
select is((select count(*)::int from public.trace_candidate_corrections where record_id = 'a4000000-0000-4000-8000-000000000104'), 0,
          'a draft edit leaves no history');
select throws_ok($$select trace_review.correct_record('employment', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000102', '{"review_status": "user_confirmed"}')$$,
                 'TR006', null, 'review status is not correctable');
select throws_ok($$select trace_review.correct_record('employment', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000102', '{"role_id": "PM-PM"}')$$,
                 'TR006', null, 'role mapping is not correctable');
select throws_ok($$select trace_review.correct_record('employment', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000102',
                     '{"title_raw": "Genuine change", "verification_status": "independently_verified"}')$$,
                 'TR006', null, 'a protected field is refused even alongside a genuine change');
select throws_ok($$select trace_review.correct_record('employment', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000102', '{"source_resume_id": null}')$$,
                 'TR006', null, 'provenance is not correctable');
select throws_ok($$select trace_review.correct_record('employment', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000102', '{"candidate_employer_id": null}')$$,
                 'TR006', null, 'links are not correctable');
select throws_ok($$select trace_review.correct_record('skill', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000401', '{"skill_id": "SKL-SQL"}')$$,
                 'TR006', null, 'taxonomy references are not correctable');
select throws_ok($$select trace_review.correct_record('employment', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000102', '{"title_raw": "Product Manager"}')$$,
                 'TR006', null, 'a correction must change something');
select throws_ok($$select trace_review.correct_record('employer', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000102', '{"employer_name": "x"}')$$,
                 'TR006', null, 'employers are not correctable');
select throws_ok($$select trace_review.correct_record('employment', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000102', '{"start_month": 13}')$$,
                 '23514', null, 'corrected values must satisfy the record''s constraints');
select throws_ok($$update public.trace_candidate_employments set title_raw = 'Forged' where candidate_employment_id = 'a4000000-0000-4000-8000-000000000101'$$,
                 'TR002', null, 'a corrected record cannot be changed by direct SQL');
select throws_ok($$insert into public.trace_candidate_corrections (owner_user_id, candidate_profile_id, record_kind, record_id, changed_fields,
                     previous_values, corrected_values, prior_review_status, corrected_by)
                   values ('a4000000-0000-4000-8000-000000000001', 'a4000000-0000-4000-8000-0000000000aa', 'employment',
                           'a4000000-0000-4000-8000-000000000105', '{title_raw}', '{}', '{}', 'pending_review', 'a4000000-0000-4000-8000-000000000001')$$,
                 '42501', null, 'a candidate cannot write history');
select throws_ok($$delete from public.trace_candidate_corrections$$, '42501', null, 'a candidate cannot delete history');
select set_config('request.jwt.claims', '{"sub": "b4000000-0000-4000-8000-000000000002", "role": "authenticated"}', true);
select throws_ok($$select trace_review.correct_record('employment', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000105', '{"title_raw": "Hijacked"}')$$,
                 'TR005', null, 'another candidate''s record is not found');
select is((select count(*)::int from public.trace_candidate_corrections), 0, 'another candidate sees none of the history');
select set_config('request.jwt.claims', '', true);
select throws_ok($$select trace_review.correct_record('employment', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000105', '{"title_raw": "x"}')$$,
                 'TR004', null, 'no request identity, no correction');
reset role;
select lives_ok($$set constraints trace_require_correction_history immediate$$,
                'every correction made so far has its matching history (the deferred check passes)');
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a4000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select is(trace_review.correct_record('employment', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000102',
                                      '{"classification_note": "Checked immediately"}'), 'user_corrected',
          'with the check immediate (it fires inside correct_record, as the writer), a correction still passes');
reset role;
set constraints trace_require_correction_history deferred;

-- ------------------------------------------------------------------ 3. written only by correct_record, append-only, even for the table owner
select throws_ok($$insert into public.trace_candidate_corrections (owner_user_id, candidate_profile_id, record_kind, record_id, changed_fields,
                     previous_values, corrected_values, prior_review_status, corrected_by)
                   values ('a4000000-0000-4000-8000-000000000001', 'a4000000-0000-4000-8000-0000000000aa', 'employment',
                           'a4000000-0000-4000-8000-000000000101', '{title_raw}', '{"title_raw": "Forged original"}', '{"title_raw": "x"}',
                           'user_confirmed', 'a4000000-0000-4000-8000-000000000001')$$,
                 'TR016', null, 'the table owner cannot write history (insert guard)');
select throws_ok($$insert into public.trace_candidate_corrections (owner_user_id, candidate_profile_id, record_kind, record_id, changed_fields,
                     previous_values, corrected_values, prior_review_status, corrected_by, txid, corrected_at)
                   values ('a4000000-0000-4000-8000-000000000001', 'a4000000-0000-4000-8000-0000000000aa', 'employment',
                           'a4000000-0000-4000-8000-000000000101', '{title_raw}', '{"title_raw": "Forged original"}', '{"title_raw": "x"}',
                           'user_confirmed', 'a4000000-0000-4000-8000-000000000001', 1, '2001-01-01')$$,
                 'TR016', null, 'the table owner cannot backdate history or choose its transaction');
set local role service_role;
select throws_ok($$insert into public.trace_candidate_corrections (owner_user_id, candidate_profile_id, record_kind, record_id, changed_fields,
                     previous_values, corrected_values, prior_review_status, corrected_by)
                   values ('a4000000-0000-4000-8000-000000000001', 'a4000000-0000-4000-8000-0000000000aa', 'employment',
                           'a4000000-0000-4000-8000-000000000101', '{title_raw}', '{"title_raw": "Forged original"}', '{"title_raw": "x"}',
                           'user_confirmed', 'a4000000-0000-4000-8000-000000000001')$$,
                 '42501', null, 'service_role cannot write history (no INSERT privilege)');
reset role;
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a4000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select is(trace_review.correct_record('employment', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000101',
                                      '{"classification_note": "Through the insert guard"}'), 'user_corrected',
          'correct_record still writes history through the insert guard');
select is((select corrected_values from public.trace_candidate_corrections
           where record_id = 'a4000000-0000-4000-8000-000000000101' order by correction_seq desc limit 1),
          '{"classification_note": "Through the insert guard"}'::jsonb, 'its history entry is the one correct_record wrote');
reset role;
select is((select count(*)::int from public.trace_candidate_corrections where previous_values ->> 'title_raw' = 'Forged original'), 0,
          'no forged entry exists');
select throws_ok($$update public.trace_candidate_corrections set corrected_values = '{}'$$, 'TR016', null, 'the owner cannot rewrite history');
select throws_ok($$delete from public.trace_candidate_corrections$$, 'TR016', null, 'the owner cannot delete history');
select throws_ok($$truncate public.trace_candidate_corrections$$, 'TR016', null, 'the owner cannot truncate history');
select throws_ok($$update public.trace_candidate_employments set title_raw = 'Owner edit' where candidate_employment_id = 'a4000000-0000-4000-8000-000000000101'$$,
                 'TR002', null, 'the owner cannot change a corrected record outside correct_record');

-- ------------------------------------------------------------------ 4. the deferred check cannot be satisfied by unrelated or forged history
-- Test-only: act as the writer directly, with the check made immediate so it fires at statement end.
grant trace_review_writer to postgres with set true, inherit false;
grant usage on schema extensions to trace_review_writer;
set constraints trace_require_correction_history immediate;
set local role trace_review_writer;
select set_config('request.jwt.claims', '{"sub": "a4000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select throws_ok($$update public.trace_candidate_employments set title_raw = 'No history', review_status = 'user_corrected'
                   where candidate_employment_id = 'a4000000-0000-4000-8000-000000000105'$$,
                 'TR017', null, 'a correction without history is refused');
select throws_ok($$update public.trace_candidate_employments set review_status = 'user_corrected'
                   where candidate_employment_id = 'a4000000-0000-4000-8000-000000000105'$$,
                 'TR003', null, 'user_corrected without a content change is refused');
select throws_ok($$update public.trace_candidate_employments set title_raw = 'Rejected edit', review_status = 'user_corrected'
                   where candidate_employment_id = 'a4000000-0000-4000-8000-000000000103'$$,
                 'TR001', null, 'G1 refuses to correct a rejected record, even from the writer');
select throws_ok($$update public.trace_candidate_employments set title_raw = 'Draft edit', review_status = 'user_corrected'
                   where candidate_employment_id = 'a4000000-0000-4000-8000-000000000104'$$,
                 'TR002', null, 'G1 refuses to turn a pending user_stated draft into a correction, even from the writer');
select throws_ok($$update public.trace_candidate_employments set candidate_employer_id = 'a4000000-0000-4000-8000-0000000000e1', title_raw = 'Relinked', review_status = 'user_corrected'
                   where candidate_employment_id = 'a4000000-0000-4000-8000-000000000105'$$,
                 'TR002', null, 'a link cannot change in a correction');
insert into public.trace_candidate_corrections (owner_user_id, candidate_profile_id, record_kind, record_id, changed_fields, previous_values,
  corrected_values, prior_review_status, corrected_by)
values ('a4000000-0000-4000-8000-000000000001', 'a4000000-0000-4000-8000-0000000000aa', 'employment', 'a4000000-0000-4000-8000-000000000106',
        '{title_raw}', '{"title_raw": "Writer probe"}', '{"title_raw": "Forged A"}', 'pending_review', 'a4000000-0000-4000-8000-000000000001'),
       ('a4000000-0000-4000-8000-000000000001', 'a4000000-0000-4000-8000-0000000000aa', 'employment', 'a4000000-0000-4000-8000-000000000105',
        '{title_raw}', '{"title_raw": "Writer probe"}', '{"title_raw": "Something else"}', 'pending_review', 'a4000000-0000-4000-8000-000000000001');
select throws_ok($$update public.trace_candidate_employments set title_raw = 'Forged A', review_status = 'user_corrected'
                   where candidate_employment_id = 'a4000000-0000-4000-8000-000000000105'$$,
                 'TR017', null, 'history of another record (or with other values) does not satisfy the check');
reset role;
-- A history entry from another transaction can only be fabricated by bypassing the insert guard, which only
-- the table owner can do (administrative access: disabling a trigger).
alter table public.trace_candidate_corrections disable trigger trace_guard_correction_history;
insert into public.trace_candidate_corrections (owner_user_id, candidate_profile_id, record_kind, record_id, changed_fields, previous_values,
  corrected_values, prior_review_status, corrected_by, txid)
values ('a4000000-0000-4000-8000-000000000001', 'a4000000-0000-4000-8000-0000000000aa', 'employment', 'a4000000-0000-4000-8000-000000000105',
        '{title_raw}', '{"title_raw": "Writer probe"}', '{"title_raw": "Other transaction"}', 'pending_review', 'a4000000-0000-4000-8000-000000000001',
        txid_current() - 1);
alter table public.trace_candidate_corrections enable trigger trace_guard_correction_history;
set local role trace_review_writer;
select throws_ok($$update public.trace_candidate_employments set title_raw = 'Other transaction', review_status = 'user_corrected'
                   where candidate_employment_id = 'a4000000-0000-4000-8000-000000000105'$$,
                 'TR017', null, 'matching history from another transaction does not satisfy the check');
insert into public.trace_candidate_corrections (owner_user_id, candidate_profile_id, record_kind, record_id, changed_fields, previous_values,
  corrected_values, prior_review_status, corrected_by)
values ('a4000000-0000-4000-8000-000000000001', 'a4000000-0000-4000-8000-0000000000aa', 'employment', 'a4000000-0000-4000-8000-000000000105',
        '{title_raw}', '{"title_raw": "Writer probe"}', '{"title_raw": "Matched"}', 'pending_review', 'a4000000-0000-4000-8000-000000000001');
select lives_ok($$update public.trace_candidate_employments set title_raw = 'Matched', review_status = 'user_corrected'
                  where candidate_employment_id = 'a4000000-0000-4000-8000-000000000105'$$,
                'exactly matching history of the same record and transaction satisfies the check (what correct_record writes)');
reset role;
revoke trace_review_writer from postgres;
set constraints trace_require_correction_history deferred;

-- ------------------------------------------------------------------ 5. deletion: history goes only with its record, profile or account
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a4000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select is(trace_review.correct_record('employment', 'a4000000-0000-4000-8000-0000000000aa', 'a4000000-0000-4000-8000-000000000106',
                                      '{"title_raw": "Deleted later"}'), 'user_corrected', 'a record to delete is corrected');
select lives_ok($$delete from public.trace_candidate_employments where candidate_employment_id = 'a4000000-0000-4000-8000-000000000106'$$,
                'a corrected record can be deleted (D4)');
select is((select count(*)::int from public.trace_candidate_corrections where record_id = 'a4000000-0000-4000-8000-000000000106'), 0,
          'its history went with it (both the forged and the real entry)');
select is((select count(*)::int from public.trace_candidate_corrections where record_id = 'a4000000-0000-4000-8000-000000000101'), 3,
          'other records keep their history');
select lives_ok($$delete from public.trace_candidate_profiles where candidate_profile_id = 'a4000000-0000-4000-8000-0000000000aa'$$,
                'profile deletion still works with corrected records');
reset role;
select is((select count(*)::int from public.trace_candidate_corrections where candidate_profile_id = 'a4000000-0000-4000-8000-0000000000aa'), 0,
          'profile deletion removes the profile''s history');

select * from finish();
rollback;
