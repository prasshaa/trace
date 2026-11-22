-- pgTAP tests for extracted-draft ingestion (v2.4.0, migration 20261111160000).
-- Run on a LOCAL Supabase stack only:  supabase test db
-- Synthetic test users, consent text and records only; everything runs in a transaction that is
-- rolled back. Fixture designation and the test consent document are set up as the table owner,
-- because no client role may set them; ingestion itself runs as authenticated through trace_ingest.

begin;
create extension if not exists pgtap with schema extensions;
select plan(70);

-- ------------------------------------------------------------------ fixtures (as owner)
insert into auth.users (id, email)
values ('a3000000-0000-4000-8000-000000000001', 'pgtap-ingest-a@example.invalid'),
       ('b3000000-0000-4000-8000-000000000002', 'pgtap-ingest-b@example.invalid'),
       ('c3000000-0000-4000-8000-000000000003', 'pgtap-ingest-c@example.invalid');
insert into public.trace_consent_documents (consent_version, purpose, title, body_sha256, legal_review_status, effective_from)
values ('pgtap-m3-approved', 'resume_processing', 'Synthetic test consent (not legal text)', repeat('1', 64), 'approved', now() - interval '1 day'),
       -- Effective date set on purpose: only the missing legal approval makes this grant ineffective.
       ('pgtap-m3-draft', 'resume_processing', 'Synthetic draft consent (not legal text)', repeat('2', 64), 'draft_not_reviewed', now() - interval '1 day');
insert into public.trace_consent_events (user_id, purpose, status, consent_version)
values ('a3000000-0000-4000-8000-000000000001', 'resume_processing', 'granted', 'pgtap-m3-approved'),
       ('b3000000-0000-4000-8000-000000000002', 'resume_processing', 'granted', 'pgtap-m3-approved'),
       ('c3000000-0000-4000-8000-000000000003', 'resume_processing', 'granted', 'pgtap-m3-draft');
insert into public.trace_candidate_profiles (candidate_profile_id, owner_user_id, is_test_fixture, fixture_profile_id)
values ('a3000000-0000-4000-8000-0000000000aa', 'a3000000-0000-4000-8000-000000000001', true, 'SYN-001'),
       ('a3000000-0000-4000-8000-0000000000ab', 'a3000000-0000-4000-8000-000000000001', false, null),
       ('a3000000-0000-4000-8000-0000000000ac', 'a3000000-0000-4000-8000-000000000001', true, 'SYN-003'),
       ('b3000000-0000-4000-8000-0000000000bb', 'b3000000-0000-4000-8000-000000000002', true, 'SYN-002'),
       ('c3000000-0000-4000-8000-0000000000cc', 'c3000000-0000-4000-8000-000000000003', true, 'SYN-004');
-- Resumes record the approved PDF hashes (trace_ingest.approved_fixture_resumes, filled by the synthetic seed),
-- except the adversarial ones: SYN-001's profile with SYN-002's PDF, and an arbitrary hash.
create temporary table approved as select fixture_profile_id as id, pdf_sha256 as sha from trace_ingest.approved_fixture_resumes;
insert into public.trace_resume_documents (resume_id, owner_user_id, candidate_profile_id, storage_object_path, mime_type, byte_size,
                                           content_sha256, is_test_fixture)
select x.id::uuid, x.owner::uuid, x.profile::uuid, x.owner || '/' || x.id || '.pdf', 'application/pdf', 1000,
       coalesce((select sha from approved where id = x.fixture_pdf), x.other_sha), x.id <> 'a3000000-0000-4000-8000-000000000706'
from (values ('a3000000-0000-4000-8000-000000000701', 'a3000000-0000-4000-8000-000000000001', 'a3000000-0000-4000-8000-0000000000aa', 'SYN-001', null),
             -- 706: SYN-001's approved PDF, but the resume is not marked as a test fixture.
             ('a3000000-0000-4000-8000-000000000706', 'a3000000-0000-4000-8000-000000000001', 'a3000000-0000-4000-8000-0000000000aa', 'SYN-001', null),
             ('a3000000-0000-4000-8000-000000000702', 'a3000000-0000-4000-8000-000000000001', 'a3000000-0000-4000-8000-0000000000ab', 'SYN-001', null),
             ('a3000000-0000-4000-8000-000000000703', 'a3000000-0000-4000-8000-000000000001', 'a3000000-0000-4000-8000-0000000000ac', 'SYN-003', null),
             ('a3000000-0000-4000-8000-000000000704', 'a3000000-0000-4000-8000-000000000001', 'a3000000-0000-4000-8000-0000000000aa', 'SYN-002', null),
             ('a3000000-0000-4000-8000-000000000705', 'a3000000-0000-4000-8000-000000000001', 'a3000000-0000-4000-8000-0000000000aa', null, repeat('9', 64)),
             ('b3000000-0000-4000-8000-000000000701', 'b3000000-0000-4000-8000-000000000002', 'b3000000-0000-4000-8000-0000000000bb', 'SYN-002', null),
             ('c3000000-0000-4000-8000-000000000701', 'c3000000-0000-4000-8000-000000000003', 'c3000000-0000-4000-8000-0000000000cc', 'SYN-004', null)
     ) as x(id, owner, profile, fixture_pdf, other_sha);
-- Profile ...ac already holds a manually entered record: ingestion must never merge into it.
insert into public.trace_candidate_skills (owner_user_id, candidate_profile_id, skill_text_raw)
values ('a3000000-0000-4000-8000-000000000001', 'a3000000-0000-4000-8000-0000000000ac', 'Manually entered');

create temporary table payloads (name text primary key, body jsonb);
insert into payloads values ('good', $j${
  "contract_version": "1", "fixture_profile_id": "SYN-001", "source_sha256": "placeholder",
  "resume_as_of_year": 2026, "resume_as_of_month": 11, "resume_as_of_precision": "month",
  "employers": [{"key": "E1", "employer_name": "SYN-001-ORG-A", "industry_raw": "B2B SaaS", "industry_sector": "saas",
                 "company_model": "product_company", "business_model": "b2b", "company_size_band": "unknown"}],
  "employments": [
    {"key": "J1", "sequence_index": 1, "employer_key": "E1", "title_raw": "Software Engineer", "employment_type": "full_time",
     "primary_career_sequence": true, "counts_as_full_time_experience": null, "classification_note": null,
     "start_year": 2018, "start_month": 7, "start_precision": "month", "end_year": 2021, "end_month": 6, "end_precision": "month",
     "is_current_as_stated": false, "source_location": "Professional experience, entry 1", "extraction_confidence": "0.950"},
    {"key": "J2", "sequence_index": 2, "employer_key": "E1", "title_raw": "Associate Product Manager", "employment_type": "full_time",
     "primary_career_sequence": true, "start_year": 2021, "start_month": 7, "start_precision": "month",
     "end_year": null, "end_month": null, "end_precision": "ongoing", "is_current_as_stated": true,
     "source_location": "Professional experience, entry 2", "extraction_confidence": "0.900"}],
  "education": [{"key": "D1", "qualification_level": "bachelor", "field_of_study": "Computer Science", "study_mode": "unknown",
                 "completion_status": "completed", "start_precision": "unknown", "end_precision": "unknown",
                 "source_location": "Education, entry 1", "extraction_confidence": "1.000"}],
  "career_activities": [],
  "career_breaks": [{"break_reason": "education", "note": "Full-time MBA.", "start_year": 2017, "start_precision": "year",
                     "end_year": 2018, "end_precision": "year", "source_location": "Career breaks, entry 1", "extraction_confidence": "1.000"}],
  "evidence": [{"key": "X1", "evidence_type": "responsibility", "evidence_text": "Built workflow APIs for business teams.",
                "employment_key": "J1", "source_location": "Professional experience, entry 1, bullet 1", "extraction_confidence": "1.000"}],
  "skills": [{"skill_text_raw": "APIs", "employment_key": "J1", "source_location": "Skills, item 1", "extraction_confidence": "1.000"}]
}$j$);
update payloads set body = jsonb_set(body, '{source_sha256}', to_jsonb((select sha from approved where id = 'SYN-001'))) where name = 'good';
insert into payloads select 'forged_review', jsonb_set(body, '{employments,0,review_status}', '"user_confirmed"') from payloads where name = 'good';
insert into payloads select 'forged_mapping', jsonb_set(body, '{employments,0,role_id}', '"PM-PM"') from payloads where name = 'good';
insert into payloads select 'forged_provenance', jsonb_set(body, '{employers,0,attribute_provenance}', '"participant_confirmed"') from payloads where name = 'good';
insert into payloads select 'forged_top', body || '{"information_origin": "user_stated"}' from payloads where name = 'good';
insert into payloads select 'other_fixture', jsonb_set(body, '{fixture_profile_id}', '"SYN-009"') from payloads where name = 'good';
insert into payloads select 'other_document', jsonb_set(body, '{source_sha256}', to_jsonb(repeat('f', 64))) from payloads where name = 'good';
insert into payloads select 'changed', jsonb_set(body, '{skills}', '[]') from payloads where name = 'good';
insert into payloads select 'broken', jsonb_set(body, '{evidence,0,evidence_text}', '"Too short"') from payloads where name = 'good';
insert into payloads select 'dangling', jsonb_set(body, '{evidence,0,employment_key}', '"J9"') from payloads where name = 'good';
insert into payloads select 'for_c', jsonb_set(jsonb_set(body, '{fixture_profile_id}', '"SYN-004"'), '{source_sha256}', to_jsonb((select sha from approved where id = 'SYN-004')))
  from payloads where name = 'good';
insert into payloads select 'for_ac', jsonb_set(jsonb_set(body, '{fixture_profile_id}', '"SYN-003"'), '{source_sha256}', to_jsonb((select sha from approved where id = 'SYN-003')))
  from payloads where name = 'good';
grant select on payloads, approved to authenticated;
create temporary table runs (name text primary key, id uuid);
grant select, insert on runs to authenticated;

-- ------------------------------------------------------------------ 1. role, ownership and EXECUTE
select ok((select not (rolcanlogin or rolinherit or rolbypassrls or rolsuper or rolcreaterole or rolcreatedb or rolreplication)
           from pg_roles where rolname = 'trace_ingest_writer'), 'trace_ingest_writer is NOLOGIN, NOINHERIT, NOBYPASSRLS and unprivileged');
select is((select count(*)::int from pg_auth_members where roleid = 'trace_ingest_writer'::regrole and (set_option or inherit_option)),
          0, 'no role can use trace_ingest_writer');
select is((select count(*)::int from pg_auth_members where member = 'trace_ingest_writer'::regrole), 0, 'trace_ingest_writer is a member of no role');
select ok(not pg_has_role('postgres', 'trace_ingest_writer', 'SET') and not pg_has_role('postgres', 'trace_ingest_writer', 'USAGE')
          and not has_schema_privilege('trace_ingest_writer', 'trace_ingest', 'CREATE'),
          'the temporary SET membership and CREATE grant used for the ownership transfer are gone');
select is((select string_agg(p.proname || ':' || pg_get_userbyid(p.proowner) || ':' || p.prosecdef || ':' || coalesce(array_to_string(p.proconfig, ','), ''), ' ' order by p.proname)
           from pg_proc p where p.pronamespace = 'trace_ingest'::regnamespace),
          'admit_deidentified_resume:trace_ingest_writer:true:search_path="" '
          'assert_allowed_keys:trace_ingest_writer:false:search_path="" assert_ingestion_allowed:trace_ingest_writer:false:search_path="" '
          'designate_fixture:trace_ingest_writer:true:search_path="" '
          'fail_fixture_extraction:trace_ingest_writer:true:search_path="" ingest_fixture_extraction:trace_ingest_writer:true:search_path="" '
          'start_fixture_extraction:trace_ingest_writer:true:search_path="" start_real_extraction:trace_ingest_writer:true:search_path=""',
          'trace_ingest functions: writer-owned SECURITY DEFINER entry points (v2.8.0 designate_fixture; v2.9.0 admit_deidentified_resume, '
          'start_real_extraction), pinned search_path');
select is((select string_agg(p.proname || '=' || r.rolname, ' ' order by p.proname, r.rolname)
           from pg_proc p cross join (values ('anon'), ('trace_app_user'), ('service_role'), ('trace_review_writer'), ('public_probe')) r(rolname)
           where p.pronamespace = 'trace_ingest'::regnamespace
             and case when r.rolname = 'public_probe'
                      then exists (select 1 from aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a where a.grantee = 0)
                      else has_function_privilege(r.rolname, p.oid, 'EXECUTE') end),
          'fail_fixture_extraction=trace_app_user ingest_fixture_extraction=trace_app_user start_fixture_extraction=trace_app_user '
          'start_real_extraction=trace_app_user',
          'EXECUTE: only the backend owner role (trace_app_user) calls the entry points (v2.9.0: start_real_extraction); nobody calls the helpers');
select ok(not has_schema_privilege('anon', 'trace_ingest', 'USAGE') and not has_schema_privilege('service_role', 'trace_ingest', 'USAGE'),
          'anon and service_role have no USAGE on trace_ingest');
select is((select count(*)::int from pg_class c cross join (values ('DELETE'), ('TRUNCATE'), ('TRIGGER')) x(p)
           where c.relnamespace = 'public'::regnamespace and c.relname ~ '^trace_(candidate_|resume_documents|extraction_runs|consent_)'
             and has_table_privilege('trace_ingest_writer', c.oid, x.p)),
          0, 'the ingest writer can delete, truncate or attach triggers to nothing');
select is((select count(*)::int from pg_attribute a join pg_class c on c.oid = a.attrelid
           where c.relnamespace = 'public'::regnamespace and a.attnum > 0 and not a.attisdropped
             and c.relname in ('trace_candidate_employers', 'trace_candidate_employments', 'trace_candidate_education',
                               'trace_candidate_career_activities', 'trace_candidate_career_breaks', 'trace_candidate_evidence', 'trace_candidate_skills')
             and (has_column_privilege('trace_ingest_writer', c.oid, a.attnum, 'UPDATE')
                  or (a.attname in ('review_status', 'verification_status', 'role_id', 'mapping_status', 'mapping_confidence', 'skill_id',
                                    'category_code', 'responsibility_area', 'confidence', 'verified', 'participant_confirmed')
                      and has_column_privilege('trace_ingest_writer', c.oid, a.attnum, 'INSERT')))),
          0, 'the ingest writer updates no candidate record and cannot insert review, verification or mapping values');
select is((select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
           where n.nspname in ('public', 'graphql_public') and p.prosrc ~ 'trace_ingest'),
          0, 'no function in an exposed schema calls into trace_ingest');

-- ------------------------------------------------------------------ 1b. the fixture allowlist and resume fixture metadata
select is((select count(*)::int || '/' || count(distinct pdf_sha256)::int from trace_ingest.approved_fixture_resumes),
          '41/41', 'the allowlist holds the 41 synthetic fixtures, each with its own PDF hash');
select is((select count(*)::int from trace_ingest.approved_fixture_resumes a join trace_ref.profiles p on p.profile_id = a.fixture_profile_id
           where p.dataset_class = 'synthetic_test_fixture'),
          41, 'every allowlisted id is a synthetic test fixture of the reference cohort');
select is((select string_agg(r.rolname || ':' || x.p, ' ' order by r.rolname, x.p) from (values ('anon'), ('trace_app_user'), ('service_role'), ('trace_ingest_writer'), ('trace_review_writer')) r(rolname)
           cross join (values ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'), ('TRUNCATE')) x(p)
           where has_table_privilege(r.rolname, 'trace_ingest.approved_fixture_resumes', x.p)),
          'trace_ingest_writer:SELECT', 'only the table owner writes the allowlist; only the ingest writer reads it');
select ok(not has_column_privilege('service_role', 'public.trace_resume_documents', 'is_test_fixture', 'INSERT')
          and not has_column_privilege('service_role', 'public.trace_resume_documents', 'is_test_fixture', 'UPDATE')
          and not has_column_privilege('service_role', 'public.trace_resume_documents', 'content_sha256', 'UPDATE')
          and not has_column_privilege('trace_app_user', 'public.trace_resume_documents', 'is_test_fixture', 'INSERT')
          and not has_column_privilege('trace_app_user', 'public.trace_resume_documents', 'is_test_fixture', 'UPDATE')
          and not has_column_privilege('trace_app_user', 'public.trace_resume_documents', 'content_sha256', 'UPDATE'),
          'neither service_role nor candidates can mark a resume as a fixture or change its recorded hash');
select ok(has_column_privilege('service_role', 'public.trace_resume_documents', 'content_sha256', 'INSERT')
          and has_column_privilege('service_role', 'public.trace_resume_documents', 'deleted_at', 'UPDATE')
          and has_column_privilege('service_role', 'public.trace_resume_documents', 'parse_status', 'UPDATE')
          and has_table_privilege('service_role', 'public.trace_resume_documents', 'SELECT')
          and has_table_privilege('service_role', 'public.trace_resume_documents', 'DELETE')
          and not has_table_privilege('service_role', 'public.trace_resume_documents', 'INSERT')
          and has_column_privilege('trace_app_user', 'public.trace_resume_documents', 'content_sha256', 'INSERT'),
          'every other resume privilege is kept (upload metadata, retention, deletion)');
set local role service_role;
select throws_ok($$update public.trace_resume_documents set is_test_fixture = true where resume_id = 'a3000000-0000-4000-8000-000000000702'$$,
                 '42501', null, 'service_role cannot mark a resume as a test fixture');
select throws_ok($$update public.trace_resume_documents set content_sha256 = repeat('0', 64) where resume_id = 'a3000000-0000-4000-8000-000000000705'$$,
                 '42501', null, 'service_role cannot change a resume''s recorded hash');
select throws_ok($$insert into public.trace_resume_documents (owner_user_id, storage_object_path, mime_type, byte_size, content_sha256, is_test_fixture)
                   values ('c3000000-0000-4000-8000-000000000003', 'c3000000-0000-4000-8000-000000000003/x.pdf', 'application/pdf', 1, repeat('1', 64), true)$$,
                 '42501', null, 'service_role cannot insert a resume marked as a test fixture');
select throws_ok($$insert into trace_ingest.approved_fixture_resumes values ('SYN-005', repeat('9', 64))$$,
                 '42501', null, 'service_role cannot add an approved fixture pair');
select lives_ok($$update public.trace_resume_documents set deleted_at = now(), parse_status = 'failed' where resume_id = 'c3000000-0000-4000-8000-000000000701'$$,
                'service_role still maintains resumes (retention and parse status)');
select lives_ok($$insert into public.trace_resume_documents (owner_user_id, storage_object_path, mime_type, byte_size, content_sha256)
                  values ('c3000000-0000-4000-8000-000000000003', 'c3000000-0000-4000-8000-000000000003/server.pdf', 'application/pdf', 1, repeat('1', 64))$$,
                'service_role still records resume uploads');
reset role;
update public.trace_resume_documents set deleted_at = null, parse_status = 'pending' where resume_id = 'c3000000-0000-4000-8000-000000000701';
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "c3000000-0000-4000-8000-000000000003", "role": "authenticated"}', true);
select lives_ok($$insert into public.trace_resume_documents (candidate_profile_id, storage_object_path, mime_type, byte_size, content_sha256)
                  values ('c3000000-0000-4000-8000-0000000000cc', 'c3000000-0000-4000-8000-000000000003/upload.pdf', 'application/pdf', 1, repeat('2', 64))$$,
                'candidate resume uploads still work');
reset role;

-- ------------------------------------------------------------------ 2. scope and consent (as candidates)
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a3000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select throws_ok($$select trace_ingest.start_fixture_extraction('a3000000-0000-4000-8000-0000000000ab', 'a3000000-0000-4000-8000-000000000702', 'fixture-1')$$,
                 'TR012', null, 'a profile that is not a synthetic test fixture cannot be ingested into');
select throws_ok($$select trace_ingest.start_fixture_extraction('b3000000-0000-4000-8000-0000000000bb', 'b3000000-0000-4000-8000-000000000701', 'fixture-1')$$,
                 'TR005', null, 'another user''s profile is not found');
select throws_ok($$select trace_ingest.start_fixture_extraction('a3000000-0000-4000-8000-0000000000aa', 'a3000000-0000-4000-8000-000000000701', 'not a version!')$$,
                 'TR006', null, 'the extractor version is a short identifier');
select throws_ok($$select trace_ingest.start_fixture_extraction('a3000000-0000-4000-8000-0000000000aa', 'a3000000-0000-4000-8000-000000000704', 'fixture-1')$$,
                 'TR012', null, 'another fixture''s approved PDF is not this fixture''s resume');
select throws_ok($$select trace_ingest.start_fixture_extraction('a3000000-0000-4000-8000-0000000000aa', 'a3000000-0000-4000-8000-000000000705', 'fixture-1')$$,
                 'TR012', null, 'a PDF that is not on the allowlist is refused');
select throws_ok($$select trace_ingest.start_fixture_extraction('a3000000-0000-4000-8000-0000000000aa', 'a3000000-0000-4000-8000-000000000706', 'fixture-1')$$,
                 'TR012', null, 'an approved hash is not enough: the resume must also be designated a fixture by the owner');
select set_config('request.jwt.claims', '{"sub": "c3000000-0000-4000-8000-000000000003", "role": "authenticated"}', true);
select throws_ok($$select trace_ingest.start_fixture_extraction('c3000000-0000-4000-8000-0000000000cc', 'c3000000-0000-4000-8000-000000000701', 'fixture-1')$$,
                 'TR013', null, 'a grant under an unapproved consent text does not count');
select set_config('request.jwt.claims', '', true);
select throws_ok($$select trace_ingest.start_fixture_extraction('a3000000-0000-4000-8000-0000000000aa', 'a3000000-0000-4000-8000-000000000701', 'fixture-1')$$,
                 'TR004', null, 'no request identity, no ingestion');

-- ------------------------------------------------------------------ 3. start, validation and ingestion (as candidate A)
select set_config('request.jwt.claims', '{"sub": "a3000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
insert into runs select 'a', trace_ingest.start_fixture_extraction('a3000000-0000-4000-8000-0000000000aa', 'a3000000-0000-4000-8000-000000000701', 'fixture-1');
select is((select status || '/' || provider_name || '/' || prompt_version || '/' || (consent_event_id is not null)
           from public.trace_extraction_runs where extraction_run_id = (select id from runs where name = 'a')),
          'queued/trace-fixture-extractor/fixture-1/true', 'start creates a queued fixture run tied to the consent grant');
select is(trace_ingest.start_fixture_extraction('a3000000-0000-4000-8000-0000000000aa', 'a3000000-0000-4000-8000-000000000701', 'fixture-1'),
          (select id from runs where name = 'a'), 'starting again returns the open run');
select is((select extraction_status from public.trace_candidate_profiles where candidate_profile_id = 'a3000000-0000-4000-8000-0000000000aa'),
          'queued', 'the profile shows the queued extraction');
select throws_ok($$select trace_ingest.ingest_fixture_extraction((select id from runs where name = 'a'), (select body from payloads where name = 'forged_review'))$$,
                 'TR006', null, 'an extracted record cannot carry a review status');
select throws_ok($$select trace_ingest.ingest_fixture_extraction((select id from runs where name = 'a'), (select body from payloads where name = 'forged_mapping'))$$,
                 'TR006', null, 'an extracted record cannot carry a role mapping');
select throws_ok($$select trace_ingest.ingest_fixture_extraction((select id from runs where name = 'a'), (select body from payloads where name = 'forged_provenance'))$$,
                 'TR006', null, 'an extracted employer cannot choose its provenance');
select throws_ok($$select trace_ingest.ingest_fixture_extraction((select id from runs where name = 'a'), (select body from payloads where name = 'forged_top'))$$,
                 'TR006', null, 'the result cannot carry unknown top-level fields');
select throws_ok($$select trace_ingest.ingest_fixture_extraction((select id from runs where name = 'a'), (select body from payloads where name = 'other_fixture'))$$,
                 'TR012', null, 'a result for another fixture is refused');
select throws_ok($$select trace_ingest.ingest_fixture_extraction((select id from runs where name = 'a'), (select body from payloads where name = 'other_document'))$$,
                 'TR012', null, 'a result read from another document is refused');
select throws_ok($$select trace_ingest.ingest_fixture_extraction((select id from runs where name = 'a'), (select body from payloads where name = 'dangling'))$$,
                 'TR006', null, 'a dangling record reference is refused');
select throws_ok($$select trace_ingest.ingest_fixture_extraction((select id from runs where name = 'a'), (select body from payloads where name = 'broken'))$$,
                 '23514', null, 'a record that violates a CHECK fails the whole ingestion');
select is((select count(*)::int from public.trace_candidate_employments where candidate_profile_id = 'a3000000-0000-4000-8000-0000000000aa')
          + (select count(*)::int from public.trace_candidate_employers where candidate_profile_id = 'a3000000-0000-4000-8000-0000000000aa'),
          0, 'nothing from the failed attempts was written (employers and employments precede the failing evidence)');
select is(trace_ingest.ingest_fixture_extraction((select id from runs where name = 'a'), (select body from payloads where name = 'good')),
          'succeeded', 'a valid result is ingested');
select is((select string_agg(information_origin || '/' || review_status || '/' || verification_status || '/' || role_id || '/' || mapping_status
                  || '/' || entry_source || '/' || extraction_confidence || '/' || source_location
                  || '/' || (source_resume_id = 'a3000000-0000-4000-8000-000000000701') || '/' || (extraction_run_id = (select id from runs where name = 'a')),
                  ' | ' order by sequence_index)
           from public.trace_candidate_employments where candidate_profile_id = 'a3000000-0000-4000-8000-0000000000aa'),
          'source_extracted/pending_review/unverified/ROLE-UNKNOWN/unresolved/resume_extraction/0.950/Professional experience, entry 1/true/true | '
          'source_extracted/pending_review/unverified/ROLE-UNKNOWN/unresolved/resume_extraction/0.900/Professional experience, entry 2/true/true',
          'employments are pending, unverified, unmapped extracted drafts with their source, run, location and confidence');
select is((select attribute_provenance || '/' || employer_name || '/' || industry_sector from public.trace_candidate_employers
           where candidate_profile_id = 'a3000000-0000-4000-8000-0000000000aa'), 'resume_stated/SYN-001-ORG-A/saas', 'the employer is resume_stated');
select is((select (select provenance || '/' || review_status from public.trace_candidate_evidence where candidate_profile_id = 'a3000000-0000-4000-8000-0000000000aa')
                  || ' ' || (select evidence_source || '/' || coalesce(skill_id, 'unmapped') || '/' || (candidate_employment_id is not null)
                             from public.trace_candidate_skills where candidate_profile_id = 'a3000000-0000-4000-8000-0000000000aa')
                  || ' ' || (select information_origin || '/' || study_mode from public.trace_candidate_education where candidate_profile_id = 'a3000000-0000-4000-8000-0000000000aa')
                  || ' ' || (select information_origin || '/' || note from public.trace_candidate_career_breaks where candidate_profile_id = 'a3000000-0000-4000-8000-0000000000aa')),
          'resume_claim/pending_review resume_explicit/unmapped/true source_extracted/unknown source_extracted/Full-time MBA.',
          'evidence, skills (linked, unmapped), education and the career break are ingested as extracted drafts');
select is((select r.status || '/' || (r.result_sha256 is not null) || '/' || p.extraction_status || '/' || d.parse_status || '/'
                  || p.resume_as_of_year || '-' || p.resume_as_of_month
           from public.trace_extraction_runs r join public.trace_candidate_profiles p on p.candidate_profile_id = r.candidate_profile_id
           join public.trace_resume_documents d on d.resume_id = r.resume_id where r.extraction_run_id = (select id from runs where name = 'a')),
          'succeeded/true/extracted/parsed/2026-11', 'the run succeeded; profile, resume and the as-of date are updated');
select is(trace_ingest.ingest_fixture_extraction((select id from runs where name = 'a'), (select body from payloads where name = 'good')),
          'succeeded', 'ingesting the identical result again is a no-op');
select is((select count(*)::int from public.trace_candidate_employments where candidate_profile_id = 'a3000000-0000-4000-8000-0000000000aa'),
          2, 'nothing was duplicated');
select throws_ok($$select trace_ingest.ingest_fixture_extraction((select id from runs where name = 'a'), (select body from payloads where name = 'changed'))$$,
                 'TR014', null, 'a different result for an ingested resume is refused');
select is(trace_ingest.start_fixture_extraction('a3000000-0000-4000-8000-0000000000aa', 'a3000000-0000-4000-8000-000000000701', 'fixture-1'),
          (select id from runs where name = 'a'), 'starting again after success returns the succeeded run');
select throws_ok($$select trace_ingest.fail_fixture_extraction((select id from runs where name = 'a'), 'too_late')$$,
                 'TR014', null, 'a succeeded run cannot be failed');
select throws_ok($$select trace_review.review_record('employment', 'a3000000-0000-4000-8000-0000000000aa',
                     (select candidate_employment_id from public.trace_candidate_employments where candidate_profile_id = 'a3000000-0000-4000-8000-0000000000aa' and sequence_index = 1), 'reinstate')$$,
                 'TR003', null, 'extracted drafts follow the existing review matrix');
select is(trace_review.review_record('employment', 'a3000000-0000-4000-8000-0000000000aa',
            (select candidate_employment_id from public.trace_candidate_employments where candidate_profile_id = 'a3000000-0000-4000-8000-0000000000aa' and sequence_index = 1), 'confirm'),
          'user_confirmed', 'the candidate confirms an extracted draft with review_record');
select throws_ok($$update public.trace_candidate_employments set title_raw = 'Changed' where candidate_profile_id = 'a3000000-0000-4000-8000-0000000000aa' and sequence_index = 2$$,
                 'TR002', null, 'an extracted draft cannot be overwritten (correction history is M4)');

-- TR015: never merge into a profile that already holds records; the run can then be failed.
insert into runs select 'ac', trace_ingest.start_fixture_extraction('a3000000-0000-4000-8000-0000000000ac', 'a3000000-0000-4000-8000-000000000703', 'fixture-1');
select throws_ok($$select trace_ingest.ingest_fixture_extraction((select id from runs where name = 'ac'), (select body from payloads where name = 'for_ac'))$$,
                 'TR015', null, 'a profile that already holds records is never merged into');
select throws_ok($$select trace_ingest.fail_fixture_extraction((select id from runs where name = 'ac'), 'Not An Identifier')$$,
                 'TR006', null, 'error codes are short identifiers, never free text');
select is(trace_ingest.fail_fixture_extraction((select id from runs where name = 'ac'), 'ingestion_conflict'), 'failed', 'a queued run can be failed');
select is((select r.status || '/' || r.error_code || '/' || p.extraction_status || '/' || d.parse_status
           from public.trace_extraction_runs r join public.trace_candidate_profiles p on p.candidate_profile_id = r.candidate_profile_id
           join public.trace_resume_documents d on d.resume_id = r.resume_id where r.extraction_run_id = (select id from runs where name = 'ac')),
          'failed/ingestion_conflict/failed/failed', 'failing a run records the code on run, profile and resume');
select throws_ok($$select trace_ingest.ingest_fixture_extraction((select id from runs where name = 'ac'), (select body from payloads where name = 'for_ac'))$$,
                 'TR014', null, 'a failed run cannot be ingested');
select isnt(trace_ingest.start_fixture_extraction('a3000000-0000-4000-8000-0000000000ac', 'a3000000-0000-4000-8000-000000000703', 'fixture-1'),
            (select id from runs where name = 'ac'), 'after a failure a new run is started');

-- A fixture whose pair is no longer on the allowlist is refused (ingestion re-checks the same rule).
reset role;
delete from trace_ingest.approved_fixture_resumes where fixture_profile_id = 'SYN-003';
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a3000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select throws_ok($$select trace_ingest.start_fixture_extraction('a3000000-0000-4000-8000-0000000000ac', 'a3000000-0000-4000-8000-000000000703', 'fixture-1')$$,
                 'TR012', null, 'a fixture whose pair is not on the allowlist cannot be ingested');

-- Another candidate cannot use A's run.
select set_config('request.jwt.claims', '{"sub": "b3000000-0000-4000-8000-000000000002", "role": "authenticated"}', true);
select throws_ok($$select trace_ingest.ingest_fixture_extraction((select id from runs where name = 'a'), (select body from payloads where name = 'good'))$$,
                 'TR005', null, 'another user''s run is not found');
select throws_ok($$select trace_ingest.fail_fixture_extraction((select id from runs where name = 'ac'), 'hijack')$$,
                 'TR005', null, 'another user cannot fail a run');
-- Consent withdrawn after the run started: refused at ingestion.
insert into runs select 'b', trace_ingest.start_fixture_extraction('b3000000-0000-4000-8000-0000000000bb', 'b3000000-0000-4000-8000-000000000701', 'fixture-1');
-- Recorded as the owner with a later timestamp: inside this single test transaction now() is constant,
-- and a real withdrawal always comes from a later transaction.
reset role;
insert into public.trace_consent_events (user_id, purpose, status, consent_version, recorded_at)
values ('b3000000-0000-4000-8000-000000000002', 'resume_processing', 'withdrawn', 'pgtap-m3-approved', now() + interval '1 minute');
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "b3000000-0000-4000-8000-000000000002", "role": "authenticated"}', true);
select throws_ok($$select trace_ingest.ingest_fixture_extraction((select id from runs where name = 'b'),
                     (select jsonb_set(jsonb_set(body, '{fixture_profile_id}', '"SYN-002"'), '{source_sha256}', to_jsonb((select sha from approved where id = 'SYN-002'))) from payloads where name = 'good'))$$,
                 'TR013', null, 'consent is re-checked at ingestion time');
reset role;

-- ------------------------------------------------------------------ 4. other roles and the insert guard
set local role service_role;
select throws_ok($$select trace_ingest.start_fixture_extraction('a3000000-0000-4000-8000-0000000000aa', 'a3000000-0000-4000-8000-000000000701', 'fixture-1')$$,
                 '42501', null, 'service_role cannot call the ingestion functions');
select throws_ok($$insert into public.trace_candidate_employments (owner_user_id, candidate_profile_id, sequence_index, title_raw, information_origin)
                   values ('c3000000-0000-4000-8000-000000000003', 'c3000000-0000-4000-8000-0000000000cc', 1, 'Forged', 'source_extracted')$$,
                 '42501', null, 'service_role cannot insert extracted records directly');
reset role;
-- G0 lets the ingest writer insert only the extracted-draft shape (test-only: act as the writer directly).
grant trace_ingest_writer to postgres with set true, inherit false;
grant usage on schema extensions to trace_ingest_writer;
set local role trace_ingest_writer;
select set_config('request.jwt.claims', '{"sub": "c3000000-0000-4000-8000-000000000003", "role": "authenticated"}', true);
select throws_ok($$insert into public.trace_candidate_employments (owner_user_id, candidate_profile_id, sequence_index, title_raw)
                   values ('c3000000-0000-4000-8000-000000000003', 'c3000000-0000-4000-8000-0000000000cc', 1, 'No provenance')$$,
                 'TR011', null, 'the ingest writer cannot insert a record without extracted provenance');
select throws_ok($$insert into public.trace_candidate_employers (owner_user_id, candidate_profile_id, employer_name)
                   values ('c3000000-0000-4000-8000-000000000003', 'c3000000-0000-4000-8000-0000000000cc', 'No provenance')$$,
                 'TR011', null, 'the ingest writer can only insert resume_stated employers');
reset role;
revoke trace_ingest_writer from postgres;
select throws_ok($$insert into public.trace_extraction_runs (owner_user_id, resume_id, candidate_profile_id, provider_name, model_name,
                     prompt_version, processing_region, status, result_sha256)
                   values ('a3000000-0000-4000-8000-000000000001', 'a3000000-0000-4000-8000-000000000701', 'a3000000-0000-4000-8000-0000000000aa',
                           'x', 'x', 'x', 'local', 'succeeded', repeat('0', 64))$$,
                 '23505', null, 'a resume has at most one succeeded extraction run');

select * from finish();
rollback;
