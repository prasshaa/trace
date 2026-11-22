-- pgTAP tests for database-level review enforcement (v2.3.0, migration 20261111150000).
-- Run on a LOCAL Supabase stack only:  supabase test db
-- Synthetic test users and records only; everything runs in a transaction that is rolled back.
-- Fixture rows are inserted as the table owner (postgres), which may insert any state. Every
-- behaviour under test then runs as authenticated, service_role or through the review functions.
--
-- Id scheme (user a / b / c): ...aa profile, ...e1-e5 employers, ...1nn employments, ...2nn activities,
-- ...3nn evidence, ...4nn skills, ...5nn education, ...6nn career breaks, ...7nn resumes, ...8nn runs,
-- ...9ks review-matrix rows (k = kind, s = starting status).

begin;
create extension if not exists pgtap with schema extensions;
select plan(122);

-- ------------------------------------------------------------------ fixtures (as owner)
insert into auth.users (id, email)
values ('a0000000-0000-4000-8000-000000000001', 'pgtap-review-a@example.invalid'),
       ('b0000000-0000-4000-8000-000000000002', 'pgtap-review-b@example.invalid'),
       ('c0000000-0000-4000-8000-000000000003', 'pgtap-review-c@example.invalid');
insert into public.trace_candidate_profiles (candidate_profile_id, owner_user_id)
values ('a0000000-0000-4000-8000-0000000000aa', 'a0000000-0000-4000-8000-000000000001'),
       ('b0000000-0000-4000-8000-0000000000bb', 'b0000000-0000-4000-8000-000000000002'),
       ('c0000000-0000-4000-8000-0000000000cc', 'c0000000-0000-4000-8000-000000000003');
insert into public.trace_candidate_employers (candidate_employer_id, owner_user_id, candidate_profile_id, employer_name, attribute_provenance)
values ('a0000000-0000-4000-8000-0000000000e1', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Shown on confirmed', null),
       ('a0000000-0000-4000-8000-0000000000e2', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Pending and rejected only', null),
       ('a0000000-0000-4000-8000-0000000000e3', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Unknown provenance', 'unknown'),
       ('a0000000-0000-4000-8000-0000000000e4', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Legacy participant', 'participant_confirmed'),
       ('a0000000-0000-4000-8000-0000000000e5', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Reassignment target', null),
       ('b0000000-0000-4000-8000-0000000000e1', 'b0000000-0000-4000-8000-000000000002', 'b0000000-0000-4000-8000-0000000000bb', 'Other owner', null),
       ('c0000000-0000-4000-8000-0000000000e1', 'c0000000-0000-4000-8000-000000000003', 'c0000000-0000-4000-8000-0000000000cc', 'Account deletion', null);
insert into public.trace_candidate_employments (candidate_employment_id, owner_user_id, candidate_profile_id, sequence_index,
  candidate_employer_id, title_raw, employment_type, start_year, start_precision, review_status, information_origin)
select x.id::uuid, x.owner::uuid, x.profile::uuid, x.n, x.employer::uuid, x.title, 'full_time', 2000 + x.n, 'year',
       x.status::public.trace_record_review_status, x.origin::public.trace_information_origin
from (values
  ('a0000000-0000-4000-8000-000000000101', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 1, 'a0000000-0000-4000-8000-0000000000e1', 'Confirmed PM', 'user_confirmed', 'user_stated'),
  ('a0000000-0000-4000-8000-000000000102', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 2, 'a0000000-0000-4000-8000-0000000000e2', 'Pending PM', 'pending_review', 'user_stated'),
  ('a0000000-0000-4000-8000-000000000103', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 3, 'a0000000-0000-4000-8000-0000000000e2', 'Rejected PM', 'rejected', 'user_stated'),
  ('a0000000-0000-4000-8000-000000000104', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 4, null, 'Pending, pending dependents', 'pending_review', 'user_stated'),
  ('a0000000-0000-4000-8000-000000000105', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 5, null, 'Pending, confirmed skill', 'pending_review', 'user_stated'),
  ('a0000000-0000-4000-8000-000000000106', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 6, null, 'Pending, corrected evidence', 'pending_review', 'user_stated'),
  ('a0000000-0000-4000-8000-000000000107', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 7, null, 'Pending, confirmed activity', 'pending_review', 'user_stated'),
  ('a0000000-0000-4000-8000-000000000108', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 8, null, 'Pending, colliding skill', 'pending_review', 'user_stated'),
  ('a0000000-0000-4000-8000-000000000109', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 9, 'a0000000-0000-4000-8000-0000000000e4', 'Draft to reassign', 'pending_review', 'user_stated'),
  ('a0000000-0000-4000-8000-000000000110', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 10, null, 'Extracted draft', 'pending_review', 'source_extracted'),
  ('b0000000-0000-4000-8000-000000000101', 'b0000000-0000-4000-8000-000000000002', 'b0000000-0000-4000-8000-0000000000bb', 1, 'b0000000-0000-4000-8000-0000000000e1', 'Other owner PM', 'pending_review', 'user_stated'),
  ('c0000000-0000-4000-8000-000000000101', 'c0000000-0000-4000-8000-000000000003', 'c0000000-0000-4000-8000-0000000000cc', 1, 'c0000000-0000-4000-8000-0000000000e1', 'Account PM', 'user_confirmed', 'user_stated')
) as x(id, owner, profile, n, employer, title, status, origin);
insert into public.trace_candidate_career_activities (candidate_activity_id, owner_user_id, candidate_profile_id, activity_type, title_raw,
  related_employment_id, review_status)
values ('a0000000-0000-4000-8000-000000000201', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'independent_project', 'Pending project', 'a0000000-0000-4000-8000-000000000104', 'pending_review'),
       ('a0000000-0000-4000-8000-000000000202', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'independent_project', 'Confirmed project', 'a0000000-0000-4000-8000-000000000107', 'user_confirmed'),
       ('a0000000-0000-4000-8000-000000000203', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'course', 'Course with confirmed evidence', null, 'pending_review'),
       ('a0000000-0000-4000-8000-000000000204', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'course', 'Course with pending dependents', null, 'pending_review');
insert into public.trace_candidate_education (candidate_education_id, owner_user_id, candidate_profile_id, qualification_title_raw, review_status)
values ('a0000000-0000-4000-8000-000000000501', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Degree with confirmed skill', 'pending_review'),
       ('a0000000-0000-4000-8000-000000000502', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Degree with pending skill', 'pending_review');
insert into public.trace_candidate_evidence (candidate_evidence_id, owner_user_id, candidate_profile_id, candidate_employment_id,
  candidate_activity_id, evidence_type, evidence_text, review_status, provenance)
values ('a0000000-0000-4000-8000-000000000301', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'a0000000-0000-4000-8000-000000000104', null, 'achievement', 'Rejected achievement', 'rejected', 'participant_confirmed'),
       ('a0000000-0000-4000-8000-000000000302', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'a0000000-0000-4000-8000-000000000106', null, 'achievement', 'Corrected achievement', 'user_corrected', 'participant_confirmed'),
       ('a0000000-0000-4000-8000-000000000303', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', null, 'a0000000-0000-4000-8000-000000000203', 'achievement', 'Confirmed course outcome', 'user_confirmed', null),
       ('a0000000-0000-4000-8000-000000000304', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', null, 'a0000000-0000-4000-8000-000000000204', 'achievement', 'Pending course outcome', 'pending_review', null),
       ('a0000000-0000-4000-8000-000000000305', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', null, null, 'other', 'Evidence with confirmed skill', 'pending_review', null),
       ('a0000000-0000-4000-8000-000000000306', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', null, null, 'other', 'Evidence with rejected skill', 'pending_review', null),
       ('a0000000-0000-4000-8000-000000000307', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'a0000000-0000-4000-8000-000000000102', null, 'achievement', 'Confirmed, nested-probe target', 'user_confirmed', null);
insert into public.trace_candidate_skills (candidate_skill_id, owner_user_id, candidate_profile_id, skill_text_raw, evidence_source,
  candidate_employment_id, candidate_education_id, candidate_activity_id, candidate_evidence_id, review_status)
values ('a0000000-0000-4000-8000-000000000401', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Kotlin', null, 'a0000000-0000-4000-8000-000000000104', null, null, null, 'pending_review'),
       ('a0000000-0000-4000-8000-000000000402', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Swift', null, 'a0000000-0000-4000-8000-000000000105', null, null, null, 'user_confirmed'),
       ('a0000000-0000-4000-8000-000000000403', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Rust', null, 'a0000000-0000-4000-8000-000000000108', null, null, null, 'pending_review'),
       ('a0000000-0000-4000-8000-000000000404', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'rust ', null, null, null, null, null, 'pending_review'),
       ('a0000000-0000-4000-8000-000000000405', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Figma', null, null, null, 'a0000000-0000-4000-8000-000000000204', null, 'pending_review'),
       ('a0000000-0000-4000-8000-000000000406', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Statistics', null, null, 'a0000000-0000-4000-8000-000000000501', null, null, 'user_confirmed'),
       ('a0000000-0000-4000-8000-000000000407', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Go', 'participant_confirmed', null, 'a0000000-0000-4000-8000-000000000502', null, null, 'pending_review'),
       ('a0000000-0000-4000-8000-000000000408', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Storytelling', null, null, null, null, 'a0000000-0000-4000-8000-000000000305', 'user_confirmed'),
       ('a0000000-0000-4000-8000-000000000409', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Excel', null, null, null, null, 'a0000000-0000-4000-8000-000000000306', 'rejected'),
       ('c0000000-0000-4000-8000-000000000401', 'c0000000-0000-4000-8000-000000000003', 'c0000000-0000-4000-8000-0000000000cc', 'SQL', null, 'c0000000-0000-4000-8000-000000000101', null, null, null, 'user_confirmed');
insert into public.trace_resume_documents (resume_id, owner_user_id, candidate_profile_id, storage_object_path, mime_type, byte_size)
values ('a0000000-0000-4000-8000-000000000701', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa',
        'a0000000-0000-4000-8000-000000000001/a0000000-0000-4000-8000-000000000701.pdf', 'application/pdf', 1000),
       ('a0000000-0000-4000-8000-000000000702', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa',
        'a0000000-0000-4000-8000-000000000001/a0000000-0000-4000-8000-000000000702.pdf', 'application/pdf', 1000),
       ('a0000000-0000-4000-8000-000000000703', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa',
        'a0000000-0000-4000-8000-000000000001/a0000000-0000-4000-8000-000000000703.pdf', 'application/pdf', 1000),
       ('c0000000-0000-4000-8000-000000000701', 'c0000000-0000-4000-8000-000000000003', 'c0000000-0000-4000-8000-0000000000cc',
        'c0000000-0000-4000-8000-000000000003/c0000000-0000-4000-8000-000000000701.pdf', 'application/pdf', 1000);
insert into public.trace_extraction_runs (extraction_run_id, owner_user_id, resume_id, candidate_profile_id, provider_name, model_name, prompt_version, processing_region)
values ('a0000000-0000-4000-8000-000000000801', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000703',
        'a0000000-0000-4000-8000-0000000000aa', 'pgtap', 'none', 'none', 'local');
insert into public.trace_candidate_career_breaks (candidate_break_id, owner_user_id, candidate_profile_id, break_reason, information_origin,
  review_status, verification_status, source_resume_id, extraction_run_id)
values ('a0000000-0000-4000-8000-000000000601', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'unspecified', 'source_extracted', 'user_confirmed', 'source_document_supported', 'a0000000-0000-4000-8000-000000000701', null),
       ('a0000000-0000-4000-8000-000000000602', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'unspecified', 'source_extracted', 'user_confirmed', 'independently_verified', 'a0000000-0000-4000-8000-000000000702', null),
       ('a0000000-0000-4000-8000-000000000603', 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'unspecified', 'source_extracted', 'user_confirmed', 'unverified', 'a0000000-0000-4000-8000-000000000703', 'a0000000-0000-4000-8000-000000000801'),
       ('c0000000-0000-4000-8000-000000000601', 'c0000000-0000-4000-8000-000000000003', 'c0000000-0000-4000-8000-0000000000cc', 'unspecified', 'source_extracted', 'user_confirmed', 'source_document_supported', 'c0000000-0000-4000-8000-000000000701', null);

-- Review matrix rows: one per kind (k = 1..6) and starting status (s = 1 pending, 2 confirmed, 3 corrected, 4 rejected).
create temporary table matrix_status (s int primary key, status public.trace_record_review_status);
insert into matrix_status values (1, 'pending_review'), (2, 'user_confirmed'), (3, 'user_corrected'), (4, 'rejected');
insert into public.trace_candidate_employments (candidate_employment_id, owner_user_id, candidate_profile_id, sequence_index, title_raw, review_status)
select ('a0000000-0000-4000-8000-00000000091' || s)::uuid, 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 20 + s, 'Matrix', status from matrix_status;
insert into public.trace_candidate_education (candidate_education_id, owner_user_id, candidate_profile_id, qualification_title_raw, review_status)
select ('a0000000-0000-4000-8000-00000000092' || s)::uuid, 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Matrix', status from matrix_status;
insert into public.trace_candidate_career_activities (candidate_activity_id, owner_user_id, candidate_profile_id, activity_type, title_raw, review_status)
select ('a0000000-0000-4000-8000-00000000093' || s)::uuid, 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'course', 'Matrix', status from matrix_status;
insert into public.trace_candidate_evidence (candidate_evidence_id, owner_user_id, candidate_profile_id, evidence_type, evidence_text, review_status)
select ('a0000000-0000-4000-8000-00000000094' || s)::uuid, 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'other', 'Matrix evidence', status from matrix_status;
insert into public.trace_candidate_skills (candidate_skill_id, owner_user_id, candidate_profile_id, skill_text_raw, review_status)
select ('a0000000-0000-4000-8000-00000000095' || s)::uuid, 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Matrix ' || s, status from matrix_status;
insert into public.trace_candidate_career_breaks (candidate_break_id, owner_user_id, candidate_profile_id, review_status)
select ('a0000000-0000-4000-8000-00000000096' || s)::uuid, 'a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', status from matrix_status;
create temporary table matrix_result (kind text, s int, action text, outcome text);
grant select on matrix_status to authenticated;
grant insert, select on matrix_result to authenticated;

-- ------------------------------------------------------------------ 1. roles, ownership and EXECUTE
select ok((select not (rolcanlogin or rolinherit or rolbypassrls or rolsuper or rolcreaterole or rolcreatedb or rolreplication)
           from pg_roles where rolname = 'trace_review_writer'), 'trace_review_writer is NOLOGIN, NOINHERIT, NOBYPASSRLS and unprivileged');
select is((select count(*)::int from pg_auth_members where roleid = 'trace_review_writer'::regrole and (set_option or inherit_option)),
          0, 'no role can use trace_review_writer');
select is((select count(*)::int from pg_auth_members where member = 'trace_review_writer'::regrole), 0, 'trace_review_writer is a member of no role');
select ok(not pg_has_role('postgres', 'trace_review_writer', 'SET') and not pg_has_role('postgres', 'trace_review_writer', 'USAGE'),
          'postgres keeps no SET or USAGE on the writer after the ownership transfer');
select ok(not pg_has_role('authenticated', 'trace_review_writer', 'MEMBER') and not pg_has_role('service_role', 'trace_review_writer', 'MEMBER')
          and not pg_has_role('anon', 'trace_review_writer', 'MEMBER'), 'no client role is a member of the writer');
select ok(not has_schema_privilege('trace_review_writer', 'trace_review', 'CREATE'), 'the writer keeps no CREATE on trace_review');
select is((select string_agg(p.proname || ':' || pg_get_userbyid(p.proowner) || ':' || p.prosecdef || ':' || coalesce(array_to_string(p.proconfig, ','), ''), ' ' order by p.proname)
           from pg_proc p where p.pronamespace = 'trace_review'::regnamespace),
          'apply_alias_mapping:trace_review_writer:true:search_path="" '   -- v2.6.0
          'choose_employment_role:trace_review_writer:true:search_path="" '   -- v2.6.0
          'classify_evidence_areas:trace_review_writer:true:search_path="" '   -- v2.7.0
          'classify_evidence_v2:trace_review_writer:true:search_path="" clear_evidence_classification:trace_review_writer:true:search_path="" '   -- v2.18.0
          'correct_record:trace_review_writer:true:search_path="" '   -- v2.5.0
          'move_employment:trace_review_writer:true:search_path="" '   -- v2.15.0
          'next_review_status:postgres:false:search_path="" reassign_employment:trace_review_writer:true:search_path="" '
          'request_uid:postgres:true:search_path="" review_record:trace_review_writer:true:search_path=""',
          'trace_review function owners, security mode and pinned search_path');
select is((select string_agg(p.proname || '=' || r.rolname, ' ' order by p.proname, r.rolname)
           from pg_proc p cross join (values ('anon'), ('trace_app_user'), ('service_role'), ('trace_review_writer'), ('public_probe')) r(rolname)
           where p.pronamespace = 'trace_review'::regnamespace
             and case when r.rolname = 'public_probe'
                      then exists (select 1 from aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a where a.grantee = 0)
                      else has_function_privilege(r.rolname, p.oid, 'EXECUTE') end),
          'apply_alias_mapping=trace_app_user apply_alias_mapping=trace_review_writer '   -- v2.6.0
          'choose_employment_role=trace_app_user choose_employment_role=trace_review_writer '   -- v2.6.0
          'classify_evidence_areas=trace_app_user classify_evidence_areas=trace_review_writer '   -- v2.7.0
          'classify_evidence_v2=trace_app_user classify_evidence_v2=trace_review_writer '   -- v2.18.0
          'clear_evidence_classification=trace_app_user clear_evidence_classification=trace_review_writer '   -- v2.18.0
          'correct_record=trace_app_user correct_record=trace_review_writer '   -- v2.5.0
          'move_employment=trace_app_user move_employment=trace_review_writer '   -- v2.15.0
          'next_review_status=trace_review_writer reassign_employment=trace_app_user reassign_employment=trace_review_writer '
          'request_uid=trace_review_writer review_record=trace_app_user review_record=trace_review_writer',
          'EXECUTE matrix: only the backend owner role (trace_app_user) calls the write functions; only the writer calls the helpers; PUBLIC has nothing');
select ok(not has_schema_privilege('anon', 'trace_review', 'USAGE') and not has_schema_privilege('service_role', 'trace_review', 'USAGE'),
          'anon and service_role have no USAGE on trace_review');
select is((select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
           where n.nspname in ('public', 'graphql_public') and p.prosrc ~ 'trace_review'),
          0, 'no function in an exposed schema calls into trace_review');
-- v2.9.0: the contribution functions READ a profile's existence and fixture flag (named here); none may write.
select is((select count(*)::int from pg_proc p
           where p.prosecdef and pg_get_userbyid(p.proowner) in ('postgres', 'supabase_admin')
             and p.prosrc ~ '(trace_candidate_|trace_resume_documents)'
             and (p.oid::regprocedure::text not in ('trace_contrib.contribution_context(uuid)',
                    'trace_contrib.record_contribution_outcome(uuid,timestamp with time zone,text,text,jsonb,text,jsonb,text)',
                    'trace_contrib.quarantine_resume(uuid,text,jsonb,jsonb)',
                    'trace_contrib.store_identity_markers(bytea,text)')   -- v2.10.0/v2.11.0: ownership check only
                  or p.prosrc ~* '(insert into|update|delete from)\s+public\.(trace_candidate_|trace_resume_documents)')),
          0, 'no postgres- or supabase_admin-owned SECURITY DEFINER function writes candidate tables; only four named ones read them');
select is((select string_agg(distinct t.tgname, ',' order by t.tgname) from pg_trigger t
           where not t.tgisinternal and t.tgrelid in (select oid from pg_class where relname ~ '^trace_(candidate_|resume_documents)')),
          'set_updated_at,trace_contribution_enqueue,trace_guard_activity_delete,trace_guard_correction_history,trace_guard_correction_history_truncate,'
          'trace_guard_education_delete,trace_guard_employer_delete,trace_guard_employer_update,'
          'trace_guard_employment_delete,trace_guard_evidence_delete,trace_guard_insert,'
          'trace_guard_order_events,trace_guard_order_events_truncate,'   -- v2.15.0: ordering events
          'trace_guard_resume_delete,trace_guard_review_row,'
          'trace_require_correction_history',   -- v2.5.0: correction history
          'candidate tables carry only the expected triggers');

-- ------------------------------------------------------------------ 2. privilege matrix
select is((select count(*)::int from pg_class c
           cross join (values ('trace_app_user'), ('service_role'), ('anon')) r(rolname)
           cross join (values ('INSERT'), ('UPDATE'), ('TRUNCATE'), ('TRIGGER'), ('REFERENCES'), ('MAINTAIN')) x(p)
           where c.relname in ('trace_candidate_profiles', 'trace_candidate_employers', 'trace_candidate_employments', 'trace_candidate_education',
                               'trace_candidate_career_breaks', 'trace_candidate_career_activities', 'trace_candidate_evidence', 'trace_candidate_skills')
             and c.relnamespace = 'public'::regnamespace and has_table_privilege(r.rolname, c.oid, x.p)),
          0, 'no table-level INSERT, UPDATE, TRUNCATE, TRIGGER, REFERENCES or MAINTAIN on candidate tables for client roles');
select is((select count(*)::int from pg_class c
           cross join (values ('TRUNCATE'), ('TRIGGER'), ('REFERENCES'), ('MAINTAIN')) x(p)
           where c.oid in ('public.trace_resume_documents'::regclass, 'public.trace_extraction_runs'::regclass)
             and has_table_privilege('service_role', c.oid, x.p)),
          0, 'service_role cannot truncate or attach triggers to resumes and extraction runs');
select is((select count(*)::int from pg_trigger t join pg_class c on c.oid = t.tgrelid
           cross join lateral (select array_agg(a order by a) args
                               from unnest(string_to_array(encode(t.tgargs, 'escape'), '\000')) a where a <> '') g
           cross join lateral (select array_agg(col.attname::text order by col.attname::text) cols
                               from pg_attribute col where col.attrelid = c.oid and col.attnum > 0 and not col.attisdropped
                                 and has_column_privilege('trace_app_user', c.oid, col.attnum, 'UPDATE')) u
           cross join lateral (select array_agg(col.attname::text order by col.attname::text) cols
                               from pg_attribute col where col.attrelid = c.oid and col.attnum > 0 and not col.attisdropped
                                 and has_column_privilege('service_role', c.oid, col.attnum, 'UPDATE')) v
           where t.tgname in ('trace_guard_review_row', 'trace_guard_employer_update')
             and (g.args is distinct from u.cols or g.args is distinct from v.cols)),
          0, 'the UPDATE column grants for authenticated and service_role equal the guard triggers'' content columns');
select is((select count(*)::int from pg_trigger t where t.tgname in ('trace_guard_review_row', 'trace_guard_employer_update')), 7,
          'six review tables and employers carry an update guard');
select ok(not has_column_privilege('trace_app_user', 'public.trace_candidate_employments', 'candidate_employer_id', 'UPDATE')
          and has_column_privilege('trace_app_user', 'public.trace_candidate_employments', 'candidate_employer_id', 'INSERT'),
          'an employment''s employer is set on insert and changed only by reassign_employment');
select is((select count(*)::int from information_schema.columns c
           where c.table_schema = 'public' and c.table_name ~ '^trace_candidate_' and c.table_name <> 'trace_candidate_profiles'
             and c.column_name in ('review_status', 'information_origin', 'verification_status', 'extraction_confidence', 'source_resume_id',
                                   'extraction_run_id', 'source_location', 'role_id', 'seniority_code', 'mapping_status', 'mapping_confidence',
                                   'mapping_note', 'category_code', 'category_mapping_status', 'category_mapping_confidence',
                                   'participant_confirmed', 'entry_source', 'evidence_source', 'provenance', 'attribute_provenance',
                                   'evidence_status', 'confidence', 'verified')
             and (has_column_privilege('trace_app_user', format('public.%I', c.table_name), c.column_name, 'INSERT')
               or has_column_privilege('trace_app_user', format('public.%I', c.table_name), c.column_name, 'UPDATE')
               or has_column_privilege('service_role', format('public.%I', c.table_name), c.column_name, 'INSERT')
               or has_column_privilege('service_role', format('public.%I', c.table_name), c.column_name, 'UPDATE'))),
          0, 'review, mapping, taxonomy, provenance, verification, extraction and legacy trust columns are not writable by client roles');
select ok(not has_column_privilege('trace_app_user', 'public.trace_candidate_profiles', 'confirmation_status', 'UPDATE')
          and not has_column_privilege('trace_app_user', 'public.trace_candidate_profiles', 'confirmed_at', 'UPDATE')
          and has_column_privilege('trace_app_user', 'public.trace_candidate_profiles', 'headline', 'UPDATE'),
          'profile self-confirmation is blocked; the headline stays editable');
select is((select count(*)::int from information_schema.columns c
           where c.table_schema = 'public' and c.table_name ~ '^trace_candidate_'
             and (c.column_name in ('created_at', 'updated_at') or c.column_name = (select a.attname from pg_index i join pg_attribute a
                    on a.attrelid = i.indrelid and a.attnum = i.indkey[0] where i.indrelid = format('public.%I', c.table_name)::regclass and i.indisprimary))
             and (has_column_privilege('trace_app_user', format('public.%I', c.table_name), c.column_name, 'INSERT')
               or has_column_privilege('trace_app_user', format('public.%I', c.table_name), c.column_name, 'UPDATE'))),
          0, 'primary keys and timestamps are not client-writable');
select ok(not has_column_privilege('trace_app_user', 'public.trace_candidate_skills', 'owner_user_id', 'UPDATE')
          and not has_column_privilege('trace_app_user', 'public.trace_candidate_skills', 'candidate_profile_id', 'UPDATE')
          and has_column_privilege('trace_app_user', 'public.trace_candidate_skills', 'candidate_profile_id', 'INSERT'),
          'owner and profile are set on insert and never updated');

-- ------------------------------------------------------------------ 3. ordinary candidate operations (authenticated)
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a0000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select throws_ok($$set role trace_review_writer$$, '42501', null, 'a candidate cannot become the review writer');
select throws_ok($$select trace_review.request_uid()$$, '42501', null, 'a candidate cannot call the identity helper');

insert into public.trace_candidate_employers (candidate_profile_id, employer_name) values ('a0000000-0000-4000-8000-0000000000aa', 'Candidate employer');
insert into public.trace_candidate_employments (candidate_profile_id, sequence_index, title_raw, employment_type, start_year, start_precision)
values ('a0000000-0000-4000-8000-0000000000aa', 50, 'Candidate employment', 'full_time', 2024, 'year');
insert into public.trace_candidate_education (candidate_profile_id, qualification_title_raw) values ('a0000000-0000-4000-8000-0000000000aa', 'Candidate degree');
insert into public.trace_candidate_career_activities (candidate_profile_id, activity_type, title_raw) values ('a0000000-0000-4000-8000-0000000000aa', 'course', 'Candidate course');
insert into public.trace_candidate_evidence (candidate_profile_id, evidence_type, evidence_text) values ('a0000000-0000-4000-8000-0000000000aa', 'other', 'Candidate evidence');
insert into public.trace_candidate_skills (candidate_profile_id, skill_text_raw) values ('a0000000-0000-4000-8000-0000000000aa', 'Candidate skill');
insert into public.trace_candidate_career_breaks (candidate_profile_id, note) values ('a0000000-0000-4000-8000-0000000000aa', 'Candidate break');
select is((select information_origin || '/' || review_status || '/' || verification_status || '/' || entry_source || '/' || role_id || '/' || mapping_status
                  || '/' || participant_confirmed
           from public.trace_candidate_employments where title_raw = 'Candidate employment'),
          'user_stated/pending_review/unverified/manual_entry/ROLE-UNKNOWN/unresolved/false',
          'a candidate employment is a pending, unverified, unmapped manual draft');
select is((select count(*)::int from (
             select review_status::text, information_origin::text, verification_status::text from public.trace_candidate_education where qualification_title_raw = 'Candidate degree'
             union all select review_status::text, information_origin::text, verification_status::text from public.trace_candidate_career_activities where title_raw = 'Candidate course'
             union all select review_status::text, information_origin::text, verification_status::text from public.trace_candidate_evidence where evidence_text = 'Candidate evidence'
             union all select review_status::text, information_origin::text, verification_status::text from public.trace_candidate_skills where skill_text_raw = 'Candidate skill'
             union all select review_status::text, information_origin::text, verification_status::text from public.trace_candidate_career_breaks where note = 'Candidate break'
           ) r where (review_status, information_origin, verification_status) = ('pending_review', 'user_stated', 'unverified')),
          5, 'every other record type is created as a pending, user_stated, unverified draft');
select is((select (select attribute_provenance from public.trace_candidate_employers where employer_name = 'Candidate employer') is null
              and (select provenance from public.trace_candidate_evidence where evidence_text = 'Candidate evidence') is null
              and (select evidence_source from public.trace_candidate_skills where skill_text_raw = 'Candidate skill') is null
              and (select category_mapping_status::text from public.trace_candidate_career_activities where title_raw = 'Candidate course') = 'unresolved'),
          true, 'legacy trust columns stay NULL on candidate rows; nothing is derived as participant_confirmed');
select throws_ok($$insert into public.trace_candidate_employments (candidate_profile_id, sequence_index, title_raw, review_status)
                   values ('a0000000-0000-4000-8000-0000000000aa', 51, 'x', 'user_confirmed')$$, '42501', null, 'a candidate cannot insert a confirmed record');
select throws_ok($$insert into public.trace_candidate_employments (candidate_profile_id, sequence_index, title_raw, role_id, mapping_status)
                   values ('a0000000-0000-4000-8000-0000000000aa', 51, 'x', 'PM-PM', 'confirmed')$$, '42501', null, 'a candidate cannot insert a role mapping');
select throws_ok($$insert into public.trace_candidate_career_activities (candidate_profile_id, activity_type, title_raw, category_code)
                   values ('a0000000-0000-4000-8000-0000000000aa', 'course', 'x', 'product_analytics')$$, '42501', null, 'a candidate cannot insert an activity taxonomy mapping');
select throws_ok($$insert into public.trace_candidate_skills (candidate_profile_id, skill_text_raw, evidence_source)
                   values ('a0000000-0000-4000-8000-0000000000aa', 'x', 'participant_confirmed')$$, '42501', null, 'a candidate cannot set a legacy skill source');
select throws_ok($$insert into public.trace_candidate_employments (candidate_employment_id, candidate_profile_id, sequence_index, title_raw)
                   values (gen_random_uuid(), 'a0000000-0000-4000-8000-0000000000aa', 51, 'x')$$, '42501', null, 'a candidate cannot choose a primary key');
select throws_ok($$insert into public.trace_candidate_employments (candidate_profile_id, sequence_index, title_raw)
                   values ('b0000000-0000-4000-8000-0000000000bb', 51, 'x')$$, '23503', null, 'a candidate cannot insert into another user''s profile');
select throws_ok($$update public.trace_candidate_employments set review_status = 'user_confirmed'
                   where candidate_employment_id = 'a0000000-0000-4000-8000-000000000102'$$, '42501', null, 'a candidate cannot update review status directly');
select throws_ok($$update public.trace_candidate_employments set candidate_employer_id = null
                   where candidate_employment_id = 'a0000000-0000-4000-8000-000000000101'$$, '42501', null, 'a candidate cannot unlink an employer directly');
select throws_ok($$update public.trace_candidate_profiles set confirmation_status = 'confirmed'
                   where candidate_profile_id = 'a0000000-0000-4000-8000-0000000000aa'$$, '42501', null, 'a candidate cannot confirm their own profile');
select throws_ok($$update public.trace_candidate_skills set candidate_profile_id = 'a0000000-0000-4000-8000-0000000000aa'
                   where candidate_skill_id = 'a0000000-0000-4000-8000-000000000401'$$, '42501', null, 'a candidate cannot move a record between profiles');
select lives_ok($$update public.trace_candidate_employments set title_raw = 'Pending PM (edited)'
                  where candidate_employment_id = 'a0000000-0000-4000-8000-000000000102'$$, 'a candidate edits their own draft');
select is((select title_raw || '/' || review_status from public.trace_candidate_employments where candidate_employment_id = 'a0000000-0000-4000-8000-000000000102'),
          'Pending PM (edited)/pending_review', 'editing a draft keeps it pending');
select throws_ok($$update public.trace_candidate_employments set title_raw = 'Rewritten'
                   where candidate_employment_id = 'a0000000-0000-4000-8000-000000000101'$$, 'TR002', null, 'a confirmed record cannot be edited');
select throws_ok($$update public.trace_candidate_employments set title_raw = 'Rewritten'
                   where candidate_employment_id = 'a0000000-0000-4000-8000-000000000103'$$, 'TR001', null, 'a rejected record cannot be edited');
select throws_ok($$update public.trace_candidate_employments set title_raw = 'Rewritten'
                   where candidate_employment_id = 'a0000000-0000-4000-8000-000000000110'$$, 'TR002', null, 'an extracted draft cannot be overwritten');
select throws_ok($$update public.trace_candidate_evidence set evidence_text = 'Rewritten'
                   where candidate_evidence_id = 'a0000000-0000-4000-8000-000000000302'$$, 'TR002', null, 'a corrected record cannot be edited');
select throws_ok($$update public.trace_candidate_evidence set candidate_employment_id = null
                   where candidate_evidence_id = 'a0000000-0000-4000-8000-000000000307'$$, 'TR002', null, 'a confirmed record cannot be detached by an edit');

-- ------------------------------------------------------------------ 4. review_record
create temporary table x_before as select to_jsonb(t) - 'review_status' - 'updated_at' as row
  from public.trace_candidate_employments t where candidate_employment_id = 'a0000000-0000-4000-8000-000000000911';
do $$
declare
  k record; m record; a text; v_out text; v_state text; v_msg text; v_hint text; v_expected text;
begin
  for k in select * from (values (1, 'employment'), (2, 'education'), (3, 'activity'), (4, 'evidence'), (5, 'skill'), (6, 'career_break')) x(n, kind) loop
    for m in select * from matrix_status loop
      foreach a in array array['confirm', 'reject', 'reinstate'] loop
        begin
          v_out := trace_review.review_record(k.kind, 'a0000000-0000-4000-8000-0000000000aa',
                                              ('a0000000-0000-4000-8000-0000000009' || k.n || m.s)::uuid, a);
          raise exception using errcode = 'P0001', message = 'undo', hint = v_out;
        exception when others then
          get stacked diagnostics v_state = returned_sqlstate, v_msg = message_text, v_hint = pg_exception_hint;
          insert into matrix_result values (k.kind, m.s, a, case when v_msg = 'undo' then v_hint else v_state end);
        end;
      end loop;
    end loop;
  end loop;
end
$$;
select is((select count(*)::int from matrix_result r
           join (values (1, 'confirm', 'user_confirmed'), (2, 'confirm', 'user_confirmed'), (3, 'confirm', 'user_corrected'), (4, 'confirm', 'TR003'),
                        (1, 'reject', 'rejected'), (2, 'reject', 'rejected'), (3, 'reject', 'rejected'), (4, 'reject', 'rejected'),
                        (1, 'reinstate', 'TR003'), (2, 'reinstate', 'TR003'), (3, 'reinstate', 'TR003'), (4, 'reinstate', 'pending_review')) e(s, action, outcome)
             on e.s = r.s and e.action = r.action and e.outcome = r.outcome),
          72, 'review_record follows the M1 matrix in all 12 cells for each of the six record kinds');
select is(trace_review.review_record('employment', 'a0000000-0000-4000-8000-0000000000aa', 'a0000000-0000-4000-8000-000000000911', 'confirm'),
          'user_confirmed', 'review_record confirms a pending record');
select is((select to_jsonb(t) - 'review_status' - 'updated_at' from public.trace_candidate_employments t
           where candidate_employment_id = 'a0000000-0000-4000-8000-000000000911'), (select row from x_before),
          'a review action changes nothing but the review status');
select throws_ok($$select trace_review.review_record('employer', 'a0000000-0000-4000-8000-0000000000aa', 'a0000000-0000-4000-8000-0000000000e1', 'confirm')$$,
                 'TR006', null, 'employers have no review status');
select throws_ok($$select trace_review.review_record('employment', 'b0000000-0000-4000-8000-0000000000bb', 'b0000000-0000-4000-8000-000000000101', 'confirm')$$,
                 'TR005', null, 'a candidate cannot review another user''s record');
select throws_ok($$select trace_review.review_record('employment', 'a0000000-0000-4000-8000-0000000000aa', 'a0000000-0000-4000-8000-000000000912', 'unconfirm')$$,
                 'TR003', null, 'there is no unconfirm action');

-- reassign_employment
select lives_ok($$select trace_review.reassign_employment('a0000000-0000-4000-8000-0000000000aa', 'a0000000-0000-4000-8000-000000000109',
                                                          'a0000000-0000-4000-8000-0000000000e5')$$, 'a draft can be reassigned to another of the owner''s employers');
select is((select candidate_employer_id from public.trace_candidate_employments where candidate_employment_id = 'a0000000-0000-4000-8000-000000000109'),
          'a0000000-0000-4000-8000-0000000000e5'::uuid, 'the draft now shows the new employer');
select throws_ok($$select trace_review.reassign_employment('a0000000-0000-4000-8000-0000000000aa', 'a0000000-0000-4000-8000-000000000101', null)$$,
                 'TR002', null, 'a confirmed employment cannot be reassigned');
select throws_ok($$select trace_review.reassign_employment('a0000000-0000-4000-8000-0000000000aa', 'a0000000-0000-4000-8000-000000000103', null)$$,
                 'TR001', null, 'a rejected employment cannot be reassigned');
select throws_ok($$select trace_review.reassign_employment('a0000000-0000-4000-8000-0000000000aa', 'a0000000-0000-4000-8000-000000000109',
                                                           'b0000000-0000-4000-8000-0000000000e1')$$, 'TR006', null, 'another user''s employer cannot be used');
select throws_ok($$select trace_review.reassign_employment('b0000000-0000-4000-8000-0000000000bb', 'b0000000-0000-4000-8000-000000000101', null)$$,
                 'TR005', null, 'another user''s employment cannot be reassigned');

-- employer edits (G2)
select lives_ok($$update public.trace_candidate_employers set industry_raw = 'Fintech' where candidate_employer_id = 'a0000000-0000-4000-8000-0000000000e5'$$,
                'a candidate-entered employer (NULL provenance) shown only on drafts is editable');
select lives_ok($$update public.trace_candidate_employers set industry_raw = 'Fintech' where candidate_employer_id = 'a0000000-0000-4000-8000-0000000000e4'$$,
                'a legacy participant_confirmed employer is still editable');
select throws_ok($$update public.trace_candidate_employers set industry_raw = 'Fintech' where candidate_employer_id = 'a0000000-0000-4000-8000-0000000000e1'$$,
                 'TR002', null, 'an employer shown on confirmed employment cannot be edited');
select throws_ok($$update public.trace_candidate_employers set industry_raw = 'Fintech' where candidate_employer_id = 'a0000000-0000-4000-8000-0000000000e3'$$,
                 'TR002', null, 'an employer with unknown provenance cannot be edited');
select throws_ok($$update public.trace_candidate_employers set employer_name = 'Renamed' where candidate_employer_id = 'a0000000-0000-4000-8000-0000000000e2'$$,
                 'TR002', null, 'an employer shown on rejected employment cannot be edited');

-- no identity
select set_config('request.jwt.claims', '', true);
select throws_ok($$select trace_review.review_record('employment', 'a0000000-0000-4000-8000-0000000000aa', 'a0000000-0000-4000-8000-000000000912', 'reject')$$,
                 'TR004', null, 'a call without request identity is refused');
reset role;

-- G1 checks the transition itself, independently of review_record (test-only: act as the writer directly).
grant trace_review_writer to postgres with set true, inherit false;
grant usage on schema extensions to trace_review_writer;   -- pgTAP lives there; rolled back with the test
set local role trace_review_writer;
select set_config('request.jwt.claims', '{"sub": "a0000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select throws_ok($$update public.trace_candidate_employments set review_status = 'user_corrected' where candidate_employment_id = 'a0000000-0000-4000-8000-000000000912'$$,
                 'TR003', null, 'G1 refuses a transition outside the matrix even from the writer role');
select throws_ok($$update public.trace_candidate_employments set review_status = 'user_confirmed' where candidate_employment_id = 'a0000000-0000-4000-8000-000000000914'$$,
                 'TR003', null, 'G1 refuses rejected -> confirmed even from the writer role');
reset role;
revoke trace_review_writer from postgres;

-- owner (postgres) is bound by the update guards too
select throws_ok($$update public.trace_candidate_employments set review_status = 'user_confirmed' where candidate_employment_id = 'a0000000-0000-4000-8000-000000000102'$$,
                 'TR003', null, 'even the table owner cannot change review status outside review_record');
select throws_ok($$update public.trace_candidate_employments set title_raw = 'x' where candidate_employment_id = 'a0000000-0000-4000-8000-000000000101'$$,
                 'TR002', null, 'even the table owner cannot edit a confirmed record');
select throws_ok($$update public.trace_candidate_employments set candidate_employer_id = null where candidate_employment_id = 'a0000000-0000-4000-8000-000000000102'$$,
                 'TR002', null, 'even the table owner cannot relink an employment outside reassign_employment');

-- ------------------------------------------------------------------ 5. service_role
set local role service_role;
select set_config('request.jwt.claims', '', true);
-- SET ROLE is checked against the session user (here postgres), so role switching is asserted from the catalog.
select ok(not pg_has_role('service_role', 'authenticated', 'MEMBER') and not pg_has_role('service_role', 'trace_review_writer', 'MEMBER')
          and not pg_has_role('service_role', 'postgres', 'MEMBER'), 'service_role is a member of no role it could switch to');
select throws_ok($$set session_replication_role = replica$$, '42501', null, 'service_role cannot switch off triggers with session_replication_role');
select throws_ok($$truncate public.trace_candidate_skills$$, '42501', null, 'service_role cannot truncate a candidate table');
select throws_ok($$create trigger t before insert on public.trace_candidate_skills for each row execute function trace_util.set_updated_at()$$,
                 '42501', null, 'service_role cannot attach a trigger to a candidate table');
select throws_ok($$select trace_review.review_record('employment', 'a0000000-0000-4000-8000-0000000000aa', 'a0000000-0000-4000-8000-000000000102', 'confirm')$$,
                 '42501', null, 'service_role cannot call review_record');
select throws_ok($$insert into public.trace_candidate_employments (owner_user_id, candidate_profile_id, sequence_index, title_raw, review_status)
                   values ('a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 60, 'Forged', 'user_confirmed')$$,
                 '42501', null, 'service_role cannot insert a confirmed record');
select throws_ok($$insert into public.trace_candidate_employments (owner_user_id, candidate_profile_id, sequence_index, title_raw, review_status)
                   values ('a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 60, 'Forged', 'rejected')$$,
                 '42501', null, 'service_role cannot insert a rejected record');
select throws_ok($$insert into public.trace_candidate_employments (owner_user_id, candidate_profile_id, sequence_index, title_raw, role_id, mapping_status, mapping_confidence)
                   values ('a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 60, 'Forged', 'PM-PM', 'confirmed', 1)$$,
                 '42501', null, 'service_role cannot insert a role mapping');
select throws_ok($$insert into public.trace_candidate_career_activities (owner_user_id, candidate_profile_id, activity_type, title_raw, category_code, category_mapping_status)
                   values ('a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'course', 'Forged', 'product_analytics', 'confirmed')$$,
                 '42501', null, 'service_role cannot insert an activity taxonomy mapping');
select throws_ok($$insert into public.trace_candidate_skills (owner_user_id, candidate_profile_id, skill_text_raw, information_origin, verification_status)
                   values ('a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Forged', 'source_extracted', 'independently_verified')$$,
                 '42501', null, 'service_role cannot insert origin or verification');
select throws_ok($$insert into public.trace_candidate_skills (owner_user_id, candidate_profile_id, skill_text_raw, extraction_confidence, source_resume_id)
                   values ('a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Forged', 0.9, 'a0000000-0000-4000-8000-000000000701')$$,
                 '42501', null, 'service_role cannot insert extraction trust fields');
select throws_ok($$insert into public.trace_candidate_evidence (owner_user_id, candidate_profile_id, evidence_type, evidence_text, provenance)
                   values ('a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'other', 'Forged', 'participant_confirmed')$$,
                 '42501', null, 'service_role cannot insert evidence provenance');
select throws_ok($$insert into public.trace_candidate_employers (owner_user_id, candidate_profile_id, employer_name, attribute_provenance)
                   values ('a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Forged', 'resume_stated')$$,
                 '42501', null, 'service_role cannot insert employer provenance');
select lives_ok($$insert into public.trace_candidate_skills (owner_user_id, candidate_profile_id, skill_text_raw)
                  values ('a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Server skill')$$,
                'service_role can insert candidate content');
select is((select review_status || '/' || information_origin || '/' || verification_status || '/' || coalesce(evidence_source, 'NULL')
           from public.trace_candidate_skills where skill_text_raw = 'Server skill'),
          'pending_review/user_stated/unverified/NULL', 'a service_role insert is a pending, user_stated, unverified draft');
select throws_ok($$update public.trace_candidate_employments set review_status = 'user_confirmed' where candidate_employment_id = 'a0000000-0000-4000-8000-000000000102'$$,
                 '42501', null, 'service_role cannot update review status');
select throws_ok($$update public.trace_candidate_employments set title_raw = 'x' where candidate_employment_id = 'a0000000-0000-4000-8000-000000000101'$$,
                 'TR002', null, 'service_role cannot edit a confirmed record');
select throws_ok($$delete from public.trace_candidate_employments where candidate_employment_id = 'a0000000-0000-4000-8000-000000000105'$$,
                 'TR008', null, 'service_role (BYPASSRLS) is still bound by the deletion guards');
reset role;
-- Grant drift: if a protected column ever became insertable, G0 still rejects the forged value.
grant insert (review_status) on public.trace_candidate_skills to service_role;
set local role service_role;
select throws_ok($$insert into public.trace_candidate_skills (owner_user_id, candidate_profile_id, skill_text_raw, review_status)
                   values ('a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-0000000000aa', 'Drift', 'user_confirmed')$$,
                 'TR011', null, 'G0 rejects a forged review status even when a grant drifts');
reset role;
revoke insert (review_status) on public.trace_candidate_skills from service_role;

-- ------------------------------------------------------------------ 6. nested execution cannot bypass the guards
create function trace_util.pgtap_nested_detach() returns trigger language plpgsql set search_path = '' as $$
begin
  update public.trace_candidate_evidence set candidate_employment_id = null
   where candidate_evidence_id = 'a0000000-0000-4000-8000-000000000307';
  return NEW;
end
$$;
create function trace_util.pgtap_definer_detach() returns trigger language plpgsql security definer set search_path = '' as $$
begin
  update public.trace_candidate_evidence set candidate_employment_id = null
   where candidate_evidence_id = 'a0000000-0000-4000-8000-000000000307';
  return NEW;
end
$$;
create trigger pgtap_nested after insert on public.trace_candidate_career_breaks for each row
  when (NEW.break_reason = 'unspecified' and NEW.note = 'nested') execute function trace_util.pgtap_nested_detach();
create trigger pgtap_definer after insert on public.trace_candidate_career_breaks for each row
  when (NEW.break_reason = 'unspecified' and NEW.note = 'definer') execute function trace_util.pgtap_definer_detach();
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a0000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select throws_ok($$insert into public.trace_candidate_career_breaks (candidate_profile_id, note) values ('a0000000-0000-4000-8000-0000000000aa', 'nested')$$,
                 'TR002', null, 'a nested update issued as authenticated (depth 2) is not treated as a referential action');
select throws_ok($$insert into public.trace_candidate_career_breaks (candidate_profile_id, note) values ('a0000000-0000-4000-8000-0000000000aa', 'definer')$$,
                 'TR002', null, 'a nested update as the table owner is not a referential action while the parent still exists');
reset role;
drop trigger pgtap_nested on public.trace_candidate_career_breaks;
drop trigger pgtap_definer on public.trace_candidate_career_breaks;
-- The predicate itself, observed at depth 2 on a nested update that is allowed (a pending draft edit).
create temporary table nested_probe (who text, depth int, recognised boolean);
grant insert, select on nested_probe to authenticated;
create function trace_util.pgtap_record_predicate() returns trigger language plpgsql set search_path = '' as $$
begin
  insert into pg_temp.nested_probe values (current_user, pg_trigger_depth(), trace_util.in_referential_action(TG_RELID));
  return NEW;
end
$$;
create function trace_util.pgtap_nested_edit() returns trigger language plpgsql set search_path = '' as $$
begin
  update public.trace_candidate_evidence set evidence_text = 'Pending course outcome (edited in a nested statement)'
   where candidate_evidence_id = 'a0000000-0000-4000-8000-000000000304';
  return NEW;
end
$$;
create trigger pgtap_record_predicate before update on public.trace_candidate_evidence for each row execute function trace_util.pgtap_record_predicate();
create trigger pgtap_nested_edit after insert on public.trace_candidate_career_breaks for each row
  when (NEW.note = 'nested edit') execute function trace_util.pgtap_nested_edit();
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a0000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
insert into public.trace_candidate_career_breaks (candidate_profile_id, note) values ('a0000000-0000-4000-8000-0000000000aa', 'nested edit');
reset role;
select is((select string_agg(who || '@' || depth || ':' || recognised, ',') from nested_probe), 'trace_app_user@2:false',
          'at trigger depth 2 a candidate statement is never recognised as a referential action (depth alone is not enough)');
drop trigger pgtap_record_predicate on public.trace_candidate_evidence;
drop trigger pgtap_nested_edit on public.trace_candidate_career_breaks;
-- Defence in depth: with the employment delete guard removed, the referential action itself is refused.
alter table public.trace_candidate_employments disable trigger trace_guard_employment_delete;
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a0000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select throws_ok($$delete from public.trace_candidate_employments where candidate_employment_id = 'a0000000-0000-4000-8000-000000000105'$$,
                 'TR008', null, 'G1 refuses to detach a reviewed record even if the parent guard is missing');
reset role;
alter table public.trace_candidate_employments enable trigger trace_guard_employment_delete;

-- ------------------------------------------------------------------ 7. deletion (decision A)
create temporary table ri_probe (who text, depth int);
create function trace_util.pgtap_ri_probe() returns trigger language plpgsql set search_path = '' as $$
begin
  insert into pg_temp.ri_probe values (current_user, pg_trigger_depth());
  return NEW;
end
$$;
create trigger pgtap_ri_probe before update on public.trace_candidate_skills for each row execute function trace_util.pgtap_ri_probe();
create temporary table snapshot as
  select 'activity' as kind, candidate_activity_id as id, to_jsonb(t) - 'related_employment_id' - 'updated_at' as row
    from public.trace_candidate_career_activities t where candidate_activity_id = 'a0000000-0000-4000-8000-000000000201'
  union all select 'evidence', candidate_evidence_id, to_jsonb(t) - 'candidate_employment_id' - 'updated_at'
    from public.trace_candidate_evidence t where candidate_evidence_id = 'a0000000-0000-4000-8000-000000000301'
  union all select 'skill', candidate_skill_id, to_jsonb(t) - 'candidate_employment_id' - 'updated_at'
    from public.trace_candidate_skills t where candidate_skill_id = 'a0000000-0000-4000-8000-000000000401';
grant select, insert on ri_probe to authenticated;
grant select on snapshot to authenticated;

set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a0000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select throws_ok($$delete from public.trace_candidate_employers where candidate_employer_id = 'a0000000-0000-4000-8000-0000000000e1'$$,
                 'TR008', null, 'an employer shown on confirmed employment cannot be deleted');
select is((select candidate_employer_id from public.trace_candidate_employments where candidate_employment_id = 'a0000000-0000-4000-8000-000000000101'),
          'a0000000-0000-4000-8000-0000000000e1'::uuid, 'the confirmed employment keeps its employer');
select lives_ok($$delete from public.trace_candidate_employers where candidate_employer_id = 'a0000000-0000-4000-8000-0000000000e2'$$,
                'an employer shown only on pending and rejected employment can be deleted');
select is((select string_agg(coalesce(candidate_employer_id::text, 'NULL') || '/' || review_status, ',' order by sequence_index)
           from public.trace_candidate_employments where candidate_employment_id in ('a0000000-0000-4000-8000-000000000102', 'a0000000-0000-4000-8000-000000000103')),
          'NULL/pending_review,NULL/rejected', 'those employments are detached with their review status unchanged');
select throws_ok($$delete from public.trace_candidate_employments where candidate_employment_id = 'a0000000-0000-4000-8000-000000000105'$$,
                 'TR008', null, 'an employment with a confirmed skill cannot be deleted');
select throws_ok($$delete from public.trace_candidate_employments where candidate_employment_id = 'a0000000-0000-4000-8000-000000000106'$$,
                 'TR008', null, 'an employment with corrected evidence cannot be deleted');
select throws_ok($$delete from public.trace_candidate_employments where candidate_employment_id = 'a0000000-0000-4000-8000-000000000107'$$,
                 'TR008', null, 'an employment with a confirmed related activity cannot be deleted');
select is((select count(*)::int from public.trace_candidate_skills where candidate_skill_id = 'a0000000-0000-4000-8000-000000000402'
             and candidate_employment_id = 'a0000000-0000-4000-8000-000000000105'), 1, 'the confirmed skill is untouched');
select throws_ok($$delete from public.trace_candidate_employments where candidate_employment_id = 'a0000000-0000-4000-8000-000000000108'$$,
                 'TR010', null, 'detaching a skill that would duplicate an unattached skill fails clearly');
select is((select count(*)::int from public.trace_candidate_employments where candidate_employment_id = 'a0000000-0000-4000-8000-000000000108'),
          1, 'nothing was deleted');
select lives_ok($$delete from public.trace_candidate_employments where candidate_employment_id = 'a0000000-0000-4000-8000-000000000104'$$,
                'an employment with only pending and rejected dependents can be deleted');
select is((select count(*)::int from snapshot s join (
             select 'activity' kind, candidate_activity_id id, to_jsonb(t) - 'related_employment_id' - 'updated_at' row, related_employment_id link
               from public.trace_candidate_career_activities t
             union all select 'evidence', candidate_evidence_id, to_jsonb(t) - 'candidate_employment_id' - 'updated_at', candidate_employment_id
               from public.trace_candidate_evidence t
             union all select 'skill', candidate_skill_id, to_jsonb(t) - 'candidate_employment_id' - 'updated_at', candidate_employment_id
               from public.trace_candidate_skills t) n on n.kind = s.kind and n.id = s.id
           where n.row = s.row and n.link is null),
          3, 'its activity, evidence and skill survive unchanged except for the nulled link (content, status and provenance kept)');
select is((select string_agg(distinct who || '@' || depth, ',') from ri_probe), 'postgres@2',
          'the referential action ran as the table owner at trigger depth 2');
select throws_ok($$delete from public.trace_candidate_career_activities where candidate_activity_id = 'a0000000-0000-4000-8000-000000000203'$$,
                 'TR008', null, 'an activity with confirmed evidence cannot be deleted');
select lives_ok($$delete from public.trace_candidate_career_activities where candidate_activity_id = 'a0000000-0000-4000-8000-000000000204'$$,
                'an activity with only pending dependents can be deleted');
select is((select count(*)::int from public.trace_candidate_evidence where candidate_evidence_id = 'a0000000-0000-4000-8000-000000000304' and candidate_activity_id is null)
          + (select count(*)::int from public.trace_candidate_skills where candidate_skill_id = 'a0000000-0000-4000-8000-000000000405' and candidate_activity_id is null),
          2, 'the activity''s evidence and skill are detached, not deleted');
select throws_ok($$delete from public.trace_candidate_education where candidate_education_id = 'a0000000-0000-4000-8000-000000000501'$$,
                 'TR008', null, 'an education record with a confirmed skill cannot be deleted');
select lives_ok($$delete from public.trace_candidate_education where candidate_education_id = 'a0000000-0000-4000-8000-000000000502'$$,
                'an education record with only a pending skill can be deleted');
select is((select evidence_source || '/' || review_status || '/' || coalesce(candidate_education_id::text, 'NULL') from public.trace_candidate_skills
           where candidate_skill_id = 'a0000000-0000-4000-8000-000000000407'),
          'participant_confirmed/pending_review/NULL', 'the detached skill keeps its legacy source and review status');
select throws_ok($$delete from public.trace_candidate_evidence where candidate_evidence_id = 'a0000000-0000-4000-8000-000000000305'$$,
                 'TR008', null, 'evidence with a confirmed skill cannot be deleted');
select lives_ok($$delete from public.trace_candidate_evidence where candidate_evidence_id = 'a0000000-0000-4000-8000-000000000306'$$,
                'evidence with only a rejected skill can be deleted');
select is((select review_status || '/' || coalesce(candidate_evidence_id::text, 'NULL') from public.trace_candidate_skills
           where candidate_skill_id = 'a0000000-0000-4000-8000-000000000409'), 'rejected/NULL', 'the rejected skill is detached, still rejected');
select lives_ok($$delete from public.trace_candidate_skills where candidate_skill_id = 'a0000000-0000-4000-8000-000000000402'$$,
                'a reviewed record itself can be deleted (D4)');

-- resumes
select throws_ok($$delete from public.trace_resume_documents where resume_id = 'a0000000-0000-4000-8000-000000000701'$$,
                 'TR009', null, 'a resume backing a source_document_supported record cannot be deleted');
select lives_ok($$delete from public.trace_resume_documents where resume_id = 'a0000000-0000-4000-8000-000000000702'$$,
                'independently verified records do not depend on the resume');
select lives_ok($$delete from public.trace_resume_documents where resume_id = 'a0000000-0000-4000-8000-000000000703'$$,
                'a resume backing only unverified records can be deleted');
select is((select coalesce(source_resume_id::text, 'NULL') || '/' || coalesce(extraction_run_id::text, 'NULL') || '/' || review_status || '/'
                  || verification_status || '/' || information_origin
           from public.trace_candidate_career_breaks where candidate_break_id = 'a0000000-0000-4000-8000-000000000603'),
          'NULL/NULL/user_confirmed/unverified/source_extracted',
          'deleting the resume clears only the source and extraction-run references');

-- ------------------------------------------------------------------ 8. profile and account deletion
select lives_ok($$delete from public.trace_candidate_profiles where candidate_profile_id = 'a0000000-0000-4000-8000-0000000000aa'$$,
                'a candidate can delete their profile with confirmed, corrected and rejected records in it');
reset role;
select is((select (select count(*) from public.trace_candidate_employers where candidate_profile_id = 'a0000000-0000-4000-8000-0000000000aa')
                + (select count(*) from public.trace_candidate_employments where candidate_profile_id = 'a0000000-0000-4000-8000-0000000000aa')
                + (select count(*) from public.trace_candidate_education where candidate_profile_id = 'a0000000-0000-4000-8000-0000000000aa')
                + (select count(*) from public.trace_candidate_career_activities where candidate_profile_id = 'a0000000-0000-4000-8000-0000000000aa')
                + (select count(*) from public.trace_candidate_evidence where candidate_profile_id = 'a0000000-0000-4000-8000-0000000000aa')
                + (select count(*) from public.trace_candidate_skills where candidate_profile_id = 'a0000000-0000-4000-8000-0000000000aa')
                + (select count(*) from public.trace_candidate_career_breaks where candidate_profile_id = 'a0000000-0000-4000-8000-0000000000aa'))::int,
          0, 'profile deletion removes every record of the profile');
select is((select coalesce(candidate_profile_id::text, 'NULL') from public.trace_resume_documents where resume_id = 'a0000000-0000-4000-8000-000000000701'),
          'NULL', 'the resume outlives the profile, unlinked (it is deleted with the account or by retention)');
select lives_ok($$delete from auth.users where id = 'c0000000-0000-4000-8000-000000000003'$$,
                'account deletion succeeds with confirmed records and a resume backing document-supported records');
select is((select (select count(*) from public.trace_candidate_profiles where owner_user_id = 'c0000000-0000-4000-8000-000000000003')
                + (select count(*) from public.trace_candidate_employers where owner_user_id = 'c0000000-0000-4000-8000-000000000003')
                + (select count(*) from public.trace_candidate_employments where owner_user_id = 'c0000000-0000-4000-8000-000000000003')
                + (select count(*) from public.trace_candidate_skills where owner_user_id = 'c0000000-0000-4000-8000-000000000003')
                + (select count(*) from public.trace_candidate_career_breaks where owner_user_id = 'c0000000-0000-4000-8000-000000000003')
                + (select count(*) from public.trace_resume_documents where owner_user_id = 'c0000000-0000-4000-8000-000000000003'))::int,
          0, 'account deletion removes every candidate row and resume of the user');
select lives_ok($$delete from auth.users where id = 'a0000000-0000-4000-8000-000000000001'$$,
                'account deletion after profile deletion removes the remaining resume');

select * from finish();
rollback;
