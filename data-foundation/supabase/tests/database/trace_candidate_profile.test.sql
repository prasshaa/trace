-- pgTAP tests for candidate-side structured career profile storage (v2.2.0, migration 20261111140000).
-- Run on a LOCAL Supabase stack only:  supabase test db
-- Synthetic test users and records only; everything runs in a transaction that is rolled back.

begin;
create extension if not exists pgtap with schema extensions;
select plan(58);

-- ------------------------------------------------------------------ synthetic users, profiles, employment
insert into auth.users (id, email)
values ('a1111111-0000-0000-0000-000000000001', 'pgtap-cand-a@example.invalid'),
       ('b2222222-0000-0000-0000-000000000002', 'pgtap-cand-b@example.invalid');
insert into public.trace_candidate_profiles (candidate_profile_id, owner_user_id)
values ('a1111111-0000-0000-0000-0000000000aa', 'a1111111-0000-0000-0000-000000000001'),
       ('b2222222-0000-0000-0000-0000000000bb', 'b2222222-0000-0000-0000-000000000002');
insert into public.trace_candidate_employers (candidate_employer_id, owner_user_id, candidate_profile_id, employer_name)
values ('a1111111-0000-0000-0000-0000000000e1', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'Synthetic Co');
insert into public.trace_candidate_employments (candidate_employment_id, owner_user_id, candidate_profile_id, sequence_index,
  candidate_employer_id, title_raw, role_id, mapping_status, mapping_confidence, employment_type, primary_career_sequence,
  start_year, start_month, start_precision, end_year, end_month, end_precision, is_current_as_stated, entry_source)
values
  ('a1111111-0000-0000-0000-00000000e001', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 1,
   null, 'Product Intern', 'PM-INTERN', 'confirmed', 1, 'internship', true, 2017, 5, 'month', 2017, 7, 'month', false, 'manual_entry'),
  ('a1111111-0000-0000-0000-00000000e002', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 2,
   'a1111111-0000-0000-0000-0000000000e1', 'Product Manager', 'PM-PM', 'confirmed', 1, 'full_time', true, 2018, 1, 'month', null, null, 'ongoing', true, 'manual_entry'),
  ('a1111111-0000-0000-0000-00000000e003', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 3,
   null, 'Freelance product consultant', 'ROLE-CONSULT', 'confirmed', 1, 'freelance', false, 2020, null, 'year', 2021, null, 'year', false, 'manual_entry'),
  ('a1111111-0000-0000-0000-00000000e004', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 4,
   null, 'Co-founder', 'ROLE-ENT', 'confirmed', 1, 'founder', false, 2022, 3, 'month', null, null, 'ongoing', true, 'manual_entry');

insert into public.trace_resume_documents (resume_id, owner_user_id, candidate_profile_id, storage_object_path, mime_type, byte_size)
values ('a1111111-0000-0000-0000-00000000d001', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa',
        'a1111111-0000-0000-0000-000000000001/a1111111-0000-0000-0000-00000000d001.pdf', 'application/pdf', 1000),
       ('b2222222-0000-0000-0000-00000000d002', 'b2222222-0000-0000-0000-000000000002', 'b2222222-0000-0000-0000-0000000000bb',
        'b2222222-0000-0000-0000-000000000002/b2222222-0000-0000-0000-00000000d002.pdf', 'application/pdf', 1000);
insert into public.trace_extraction_runs (extraction_run_id, owner_user_id, resume_id, candidate_profile_id, provider_name, model_name, prompt_version, processing_region)
values ('a1111111-0000-0000-0000-00000000f001', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-00000000d001',
        'a1111111-0000-0000-0000-0000000000aa', 'pgtap', 'none', 'none', 'local'),
       ('b2222222-0000-0000-0000-00000000f002', 'b2222222-0000-0000-0000-000000000002', 'b2222222-0000-0000-0000-00000000d002',
        'b2222222-0000-0000-0000-0000000000bb', 'pgtap', 'none', 'none', 'local');

-- ------------------------------------------------------------------ structure and backward compatibility
select ok(to_regclass('public.trace_candidate_career_activities') is not null, 'career activities table exists');
select ok((select relrowsecurity from pg_class where oid = 'public.trace_candidate_career_activities'::regclass), 'career activities has RLS enabled');
select ok(exists (select 1 from information_schema.columns where table_schema = 'public' and table_name = 'trace_candidate_profiles'
                  and column_name = 'raw_extraction_json'), 'raw_extraction_json is preserved');
select is((select information_origin::text || '/' || review_status::text || '/' || verification_status::text
           from public.trace_candidate_employments where candidate_employment_id = 'a1111111-0000-0000-0000-00000000e002'),
          'user_stated/pending_review/unverified', 'a legacy-shaped employment insert still works and gets safe defaults');

-- ------------------------------------------------------------------ employment episodes stay employment
select is((select employment_type::text from public.trace_candidate_employments where candidate_employment_id = 'a1111111-0000-0000-0000-00000000e001'),
          'internship', 'an internship is an employment episode with employment_type internship');
select is((select string_agg(employment_type::text, ',' order by sequence_index) from public.trace_candidate_employments
           where candidate_profile_id = 'a1111111-0000-0000-0000-0000000000aa' and employment_type in ('freelance', 'founder')),
          'freelance,founder', 'freelance and founder work stay employment episodes');
select throws_ok($$insert into public.trace_candidate_career_activities (owner_user_id, candidate_profile_id, activity_type, title_raw)
                   values ('a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'internship', 'Summer internship')$$,
                 '22P02', null, 'internship is not a career activity type');

-- ------------------------------------------------------------------ education
insert into public.trace_candidate_education (candidate_education_id, owner_user_id, candidate_profile_id, qualification_level,
  qualification_title_raw, institution_name, field_of_study, completion_status, start_year, start_precision, end_year, end_precision, notes)
values ('a1111111-0000-0000-0000-00000000ed01', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa',
        'bachelor', 'B.Tech (CSE)', 'Synthetic Institute of Technology', 'Computer Science and Engineering', 'completed',
        2013, 'year', 2017, 'year', 'Final-year project on recommender systems'),
       ('a1111111-0000-0000-0000-00000000ed02', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa',
        'mba', 'PGDM (Executive)', null, null, 'in_progress', null, 'unknown', null, 'unknown', null);

select is((select qualification_title_raw || ' / ' || qualification_level from public.trace_candidate_education
           where candidate_education_id = 'a1111111-0000-0000-0000-00000000ed01'),
          'B.Tech (CSE) / bachelor', 'the original qualification title is kept alongside the normalised level');
select is((select (start_lo, start_hi, end_lo, end_hi)::text from public.trace_candidate_education
           where candidate_education_id = 'a1111111-0000-0000-0000-00000000ed02'),
          '(,,,)', 'unknown education dates stay unknown');
select is((select (end_hi - end_lo) from public.trace_candidate_education where candidate_education_id = 'a1111111-0000-0000-0000-00000000ed01'),
          11, 'a year-only education date stays a January-December range');

-- ------------------------------------------------------------------ career activities
insert into public.trace_candidate_career_activities (candidate_activity_id, owner_user_id, candidate_profile_id, activity_type,
  title_raw, organisation_raw, status, credential_identifier_raw, start_year, start_month, start_precision,
  expiry_year, expiry_month, expiry_precision, related_employment_id, description_raw, contribution_raw, outcome_raw,
  link_url, end_year, end_month, end_precision)
values
  ('a1111111-0000-0000-0000-00000000ac01', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'certification',
   'Certified Scrum Product Owner', 'Synthetic Scrum Body', 'completed', 'SYN-CSPO-0001', 2021, 3, 'month', 2023, 3, 'month', null, null, null, null, null, null, null, 'unknown'),
  ('a1111111-0000-0000-0000-00000000ac02', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'licence',
   'Synthetic professional licence', 'Synthetic Licensing Board', 'active', 'LIC-0002', 2020, null, 'year', null, null, 'unknown', null, null, null, null, null, null, null, 'unknown'),
  ('a1111111-0000-0000-0000-00000000ac03', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'course',
   'Product Analytics Fundamentals', 'Synthetic Online Academy', 'completed', null, 2022, null, 'year', null, null, 'unknown', null, null, null, null, null, null, null, 'unknown'),
  ('a1111111-0000-0000-0000-00000000ac04', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'independent_project',
   'Expense tracker side project', null, 'completed', null, 2019, 6, 'month', null, null, 'unknown', 'a1111111-0000-0000-0000-00000000e002',
   'Built a mobile expense tracker', 'Sole developer and designer', 'Reached 200 synthetic users', null, 2019, 12, 'month'),
  ('a1111111-0000-0000-0000-00000000ac05', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'portfolio_project',
   'Product teardown series', null, 'active', null, null, null, 'unknown', null, null, 'unknown', null, null, null, null,
   'https://example.invalid/portfolio', null, null, 'unknown'),
  ('a1111111-0000-0000-0000-00000000ac06', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'volunteering',
   'Mentor, synthetic coding club', 'Synthetic Coding Club', 'active', null, 2020, 1, 'month', null, null, 'unknown', null, null, null, null, null, null, null, 'ongoing'),
  ('a1111111-0000-0000-0000-00000000ac07', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'award',
   'Synthetic hackathon winner', 'Synthetic Hackathon', 'completed', null, 2016, 11, 'month', null, null, 'unknown', null, null, null, null, null, null, null, 'unknown'),
  ('a1111111-0000-0000-0000-00000000ac08', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'publication',
   'A synthetic paper on onboarding', 'Synthetic Journal', 'completed', null, 2018, null, 'year', null, null, 'unknown', null, null, null, null, null, null, null, 'unknown'),
  ('a1111111-0000-0000-0000-00000000ac09', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'research',
   'Synthetic user-research study', null, 'completed', null, null, null, 'unknown', null, null, 'unknown', null, null, null, null, null, null, null, 'unknown'),
  ('a1111111-0000-0000-0000-00000000ac0a', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'extracurricular',
   'Debate society', 'Synthetic Institute of Technology', 'completed', null, 2014, null, 'year', null, null, 'unknown', null, null, null, null, null, 2016, null, 'year'),
  ('a1111111-0000-0000-0000-00000000ac0b', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'leadership',
   'Student council president', 'Synthetic Institute of Technology', 'completed', null, 2016, null, 'year', null, null, 'unknown', null, null, null, null, null, 2017, null, 'year');

select is((select count(distinct activity_type)::int from public.trace_candidate_career_activities
           where candidate_profile_id = 'a1111111-0000-0000-0000-0000000000aa'),
          11, 'certification, licence, course, projects, volunteering, award, publication, research, extracurricular and leadership are stored');
select is((select credential_identifier_raw || ' / ' || expiry_year || '-' || expiry_month from public.trace_candidate_career_activities
           where candidate_activity_id = 'a1111111-0000-0000-0000-00000000ac01'),
          'SYN-CSPO-0001 / 2023-3', 'a certification keeps its credential id and expiry');
select is((select related_employment_id from public.trace_candidate_career_activities where candidate_activity_id = 'a1111111-0000-0000-0000-00000000ac04'),
          'a1111111-0000-0000-0000-00000000e002'::uuid, 'a project can be related to an employment episode');
select is((select count(*)::int from public.trace_candidate_employments where candidate_profile_id = 'a1111111-0000-0000-0000-0000000000aa'),
          4, 'activities never add employment episodes');
select is((select end_precision::text from public.trace_candidate_career_activities where candidate_activity_id = 'a1111111-0000-0000-0000-00000000ac06'),
          'ongoing', 'volunteering can be ongoing');
select is((select (start_lo is null and end_lo is null) from public.trace_candidate_career_activities where candidate_activity_id = 'a1111111-0000-0000-0000-00000000ac09'),
          true, 'an undated activity keeps unknown dates');
select throws_ok($$insert into public.trace_candidate_career_activities (owner_user_id, candidate_profile_id, activity_type, title_raw, credential_identifier_raw)
                   values ('a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'independent_project', 'Side app', 'ID-1')$$,
                 '23514', null, 'credential fields are only for certifications and licences');
select throws_ok($$insert into public.trace_candidate_career_activities (owner_user_id, candidate_profile_id, activity_type, title_raw, status)
                   values ('a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'volunteering', 'Club', 'expired')$$,
                 '23514', null, 'only certifications and licences can be expired');
select throws_ok($$insert into public.trace_candidate_career_activities (owner_user_id, candidate_profile_id, activity_type, title_raw,
                     start_year, start_month, start_precision, end_year, end_month, end_precision)
                   values ('a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'course', 'Course',
                     2022, 6, 'month', 2021, 1, 'month')$$,
                 '23514', null, 'an activity cannot end before it starts');
select throws_ok($$insert into public.trace_candidate_career_activities (owner_user_id, candidate_profile_id, activity_type, title_raw, link_url)
                   values ('a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'portfolio_project', 'P', 'javascript:alert(1)')$$,
                 '23514', null, 'links must be http(s) URLs');
select throws_ok($$insert into public.trace_candidate_career_activities (owner_user_id, candidate_profile_id, activity_type, title_raw, category_code)
                   values ('a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'course', 'C', 'product_analytics')$$,
                 '23514', null, 'a normalised category needs a mapping status');
select throws_ok($$insert into public.trace_candidate_career_activities (owner_user_id, candidate_profile_id, activity_type, title_raw)
                   values ('a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'award', '   ')$$,
                 '23514', null, 'the original title is required');

-- ------------------------------------------------------------------ achievements and skills linked to their source records
insert into public.trace_candidate_evidence (candidate_evidence_id, owner_user_id, candidate_profile_id, candidate_activity_id,
  evidence_type, evidence_text, provenance)
values ('a1111111-0000-0000-0000-00000000c001', 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa',
        'a1111111-0000-0000-0000-00000000ac04', 'achievement', 'Reached 200 synthetic users in six months', 'participant_confirmed');

select is((select candidate_activity_id from public.trace_candidate_evidence where candidate_evidence_id = 'a1111111-0000-0000-0000-00000000c001'),
          'a1111111-0000-0000-0000-00000000ac04'::uuid, 'an achievement can be linked to a project');
select throws_ok($$insert into public.trace_candidate_evidence (owner_user_id, candidate_profile_id, candidate_employment_id, candidate_activity_id,
                     evidence_type, evidence_text, provenance)
                   values ('a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'a1111111-0000-0000-0000-00000000e002',
                     'a1111111-0000-0000-0000-00000000ac04', 'achievement', 'Attached to two parents', 'resume_claim')$$,
                 '23514', null, 'evidence attaches to at most one employment or activity');

insert into public.trace_candidate_skills (owner_user_id, candidate_profile_id, skill_id, skill_text_raw, evidence_source,
  candidate_activity_id, candidate_education_id, candidate_evidence_id, candidate_employment_id, information_origin, stated_proficiency_raw)
select 'a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', s.skill_id, x.raw, x.src,
       x.act, x.edu, x.ev, x.emp, x.origin::public.trace_information_origin, x.prof
from (select skill_id from public.trace_skills order by skill_id limit 1) s,
     (values ('scrum', 'resume_explicit', 'a1111111-0000-0000-0000-00000000ac01'::uuid, null::uuid, null::uuid, null::uuid, 'source_extracted', null),
             ('algorithms', 'resume_explicit', null, 'a1111111-0000-0000-0000-00000000ed01'::uuid, null, null, 'user_stated', 'advanced'),
             ('growth', 'llm_inferred', null, null, 'a1111111-0000-0000-0000-00000000c001'::uuid, null, 'inferred', null),
             ('roadmaps', 'participant_confirmed', null, null, null, 'a1111111-0000-0000-0000-00000000e002'::uuid, 'user_stated', null)
     ) as x(raw, src, act, edu, ev, emp, origin, prof);
insert into public.trace_candidate_skills (owner_user_id, candidate_profile_id, skill_id, skill_text_raw, evidence_source, candidate_activity_id)
values ('a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', null, 'Mixpanel', 'resume_explicit', 'a1111111-0000-0000-0000-00000000ac03');

select is((select string_agg(case when candidate_activity_id is not null then 'activity' when candidate_education_id is not null then 'education'
                                  when candidate_evidence_id is not null then 'evidence' when candidate_employment_id is not null then 'employment' end,
                             ',' order by lower(skill_text_raw) collate "C")
           from public.trace_candidate_skills where candidate_profile_id = 'a1111111-0000-0000-0000-0000000000aa'),
          'education,evidence,activity,employment,activity', 'each skill traces to the record that demonstrates it');
select is((select skill_id is null from public.trace_candidate_skills where skill_text_raw = 'Mixpanel'
              and candidate_profile_id = 'a1111111-0000-0000-0000-0000000000aa'),
          true, 'a skill outside the taxonomy is kept as stated');
select throws_ok($$insert into public.trace_candidate_skills (owner_user_id, candidate_profile_id, skill_text_raw, evidence_source,
                     candidate_activity_id, candidate_education_id)
                   values ('a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'x', 'resume_explicit',
                     'a1111111-0000-0000-0000-00000000ac01', 'a1111111-0000-0000-0000-00000000ed01')$$,
                 '23514', null, 'a skill row links to at most one source record');
select throws_ok($$insert into public.trace_candidate_skills (owner_user_id, candidate_profile_id, evidence_source)
                   values ('a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'resume_explicit')$$,
                 '23514', null, 'a skill needs a taxonomy id or its stated text');
select throws_ok($$insert into public.trace_candidate_skills (owner_user_id, candidate_profile_id, skill_text_raw, evidence_source, information_origin, stated_proficiency_raw)
                   values ('a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'sql', 'llm_inferred', 'inferred', 'expert')$$,
                 '23514', null, 'proficiency is never inferred');
select throws_ok($$insert into public.trace_candidate_skills (owner_user_id, candidate_profile_id, skill_text_raw, evidence_source)
                   values ('a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'sql', 'llm_inferred')$$,
                 '23514', null, 'an LLM-inferred skill must be recorded as inferred');

-- ------------------------------------------------------------------ provenance, review and verification are independent
update public.trace_candidate_career_activities
   set information_origin = 'source_extracted', extraction_confidence = 0.82, source_resume_id = 'a1111111-0000-0000-0000-00000000d001',
       extraction_run_id = 'a1111111-0000-0000-0000-00000000f001', source_location = 'page 1, Certifications',
       verification_status = 'unverified'
 where candidate_activity_id = 'a1111111-0000-0000-0000-00000000ac01';
-- Since v2.3.0 review status changes only through trace_review.review_record, as the owner.
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a1111111-0000-0000-0000-000000000001", "role": "authenticated"}', true);
select trace_review.review_record('activity', 'a1111111-0000-0000-0000-0000000000aa', 'a1111111-0000-0000-0000-00000000ac01', 'confirm');
reset role;
select set_config('request.jwt.claims', '', true);
update public.trace_candidate_career_activities
   set information_origin = 'source_extracted', review_status = 'pending_review', verification_status = 'source_document_supported',
       source_resume_id = 'a1111111-0000-0000-0000-00000000d001'
 where candidate_activity_id = 'a1111111-0000-0000-0000-00000000ac02';

select is((select string_agg(information_origin::text, ',' order by lower(skill_text_raw) collate "C") from public.trace_candidate_skills
           where candidate_profile_id = 'a1111111-0000-0000-0000-0000000000aa' and skill_text_raw <> 'Mixpanel'),
          'user_stated,inferred,user_stated,source_extracted', 'stated, extracted and inferred origins are preserved');
select is((select review_status::text || '/' || verification_status::text || '/' || extraction_confidence::text
           from public.trace_candidate_career_activities where candidate_activity_id = 'a1111111-0000-0000-0000-00000000ac01'),
          'user_confirmed/unverified/0.820', 'a user-confirmed record can still be unverified, with its extraction confidence kept separately');
select is((select review_status::text || '/' || verification_status::text
           from public.trace_candidate_career_activities where candidate_activity_id = 'a1111111-0000-0000-0000-00000000ac02'),
          'pending_review/source_document_supported', 'document support does not imply user review');
select is((select source_location from public.trace_candidate_career_activities where candidate_activity_id = 'a1111111-0000-0000-0000-00000000ac01'),
          'page 1, Certifications', 'provenance keeps a source location, not a resume excerpt');
select throws_ok($$update public.trace_candidate_career_activities set extraction_confidence = 0.9
                   where candidate_activity_id = 'a1111111-0000-0000-0000-00000000ac03'$$,
                 '23514', null, 'a user-stated record cannot carry an extraction confidence');
select throws_ok($$update public.trace_candidate_career_activities set source_resume_id = 'b2222222-0000-0000-0000-00000000d002'
                   where candidate_activity_id = 'a1111111-0000-0000-0000-00000000ac03'$$,
                 '23503', null, 'a record cannot cite another user''s resume');
select throws_ok($$update public.trace_candidate_education set extraction_run_id = 'b2222222-0000-0000-0000-00000000f002'
                   where candidate_education_id = 'a1111111-0000-0000-0000-00000000ed01'$$,
                 '23503', null, 'a record cannot cite another user''s extraction run');

-- ------------------------------------------------------------------ column privileges
select ok(not has_column_privilege('trace_app_user', 'public.trace_candidate_career_activities', 'verification_status', 'INSERT'),
          'clients cannot set verification on insert');
select ok(not has_column_privilege('trace_app_user', 'public.trace_candidate_career_activities', 'verification_status', 'UPDATE'),
          'clients cannot set verification on update');
select ok(not has_column_privilege('trace_app_user', 'public.trace_candidate_career_activities', 'information_origin', 'UPDATE'),
          'clients cannot rewrite information origin');
select ok(not has_column_privilege('trace_app_user', 'public.trace_candidate_education', 'source_resume_id', 'UPDATE'),
          'clients cannot attach source documents');
select ok(not has_column_privilege('trace_app_user', 'public.trace_candidate_evidence', 'evidence_status', 'UPDATE'),
          'clients can no longer self-assert legacy evidence verification');
select ok(not has_column_privilege('trace_app_user', 'public.trace_candidate_skills', 'verified', 'UPDATE'),
          'clients can no longer self-assert legacy skill verification');
select ok(not has_column_privilege('trace_app_user', 'public.trace_candidate_career_activities', 'review_status', 'UPDATE'),
          'review status is not directly writable; the owner reviews through trace_review.review_record (v2.3.0)');
select ok(has_column_privilege('trace_app_user', 'public.trace_candidate_career_activities', 'title_raw', 'INSERT'),
          'the owner can enter their own records');
select ok(not has_table_privilege('anon', 'public.trace_candidate_career_activities', 'SELECT'), 'anon cannot read career activities');

-- ------------------------------------------------------------------ cross-user isolation (RLS + composite keys)
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "b2222222-0000-0000-0000-000000000002", "role": "authenticated"}', true);
select is((select count(*)::int from public.trace_candidate_career_activities), 0, 'another user sees none of the candidate''s activities');
select is((select count(*)::int from public.trace_candidate_education), 0, 'another user sees none of the candidate''s education');
select is((select count(*)::int from public.trace_candidate_skills), 0, 'another user sees none of the candidate''s skills');
select throws_ok($$insert into public.trace_candidate_career_activities (candidate_profile_id, activity_type, title_raw)
                   values ('a1111111-0000-0000-0000-0000000000aa', 'course', 'Injected')$$,
                 '23503', null, 'another user cannot attach an activity to the candidate''s profile');
select throws_ok($$insert into public.trace_candidate_career_activities (owner_user_id, candidate_profile_id, activity_type, title_raw)
                   values ('a1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-0000000000aa', 'course', 'Injected')$$,
                 '42501', null, 'another user cannot write rows owned by the candidate');
update public.trace_candidate_career_activities set title_raw = 'hijacked' where candidate_activity_id = 'a1111111-0000-0000-0000-00000000ac03';
select throws_ok($$insert into public.trace_candidate_career_activities (activity_type, title_raw, candidate_profile_id, verification_status)
                   values ('course', 'Self-verified', 'b2222222-0000-0000-0000-0000000000bb', 'independently_verified')$$,
                 '42501', null, 'a user cannot mark their own record independently verified');

select set_config('request.jwt.claims', '{"sub": "a1111111-0000-0000-0000-000000000001", "role": "authenticated"}', true);
select is((select count(*)::int from public.trace_candidate_career_activities), 11, 'the owner sees their own activities');
select throws_ok($$insert into public.trace_candidate_skills (candidate_profile_id, skill_text_raw, candidate_activity_id)
                   values ('a1111111-0000-0000-0000-0000000000aa', 'x', 'b2222222-0000-0000-0000-00000000ac01')$$,
                 '23503', null, 'a skill cannot link to a record outside the owner''s profile');
reset role;

select is((select title_raw from public.trace_candidate_career_activities where candidate_activity_id = 'a1111111-0000-0000-0000-00000000ac03'),
          'Product Analytics Fundamentals', 'another user''s update changed nothing');

-- ------------------------------------------------------------------ separation from reference analytics
select is((select count(*)::int
           from pg_depend d
           join pg_rewrite r on r.oid = d.objid
           join pg_class v on v.oid = r.ev_class
           join pg_namespace n on n.oid = v.relnamespace
           where n.nspname = 'trace_ref'
             and d.refobjid in ('public.trace_candidate_career_activities'::regclass, 'public.trace_candidate_education'::regclass,
                                'public.trace_candidate_skills'::regclass, 'public.trace_candidate_evidence'::regclass,
                                'public.trace_candidate_employments'::regclass, 'public.trace_candidate_career_breaks'::regclass)),
          0, 'no trace_ref view reads any candidate record table');
select is((select count(*)::int from trace_ref.v_mode_profiles where data_mode = 'empirical'),
          0, 'candidate records never create empirical reference profiles');
select is((select count(*)::int from trace_ref.v_career_episode_paths p
           where p.profile_id::text in (select candidate_profile_id::text from public.trace_candidate_profiles)),
          0, 'candidate profiles never appear in reference pathway analytics');

select * from finish();
rollback;
