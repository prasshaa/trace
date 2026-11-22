-- pgTAP tests for candidate classification of evidence responsibility areas (v2.7.0, migration 20261111190000).
-- Run on a LOCAL Supabase stack only:  supabase test db
-- Synthetic test users and records only; everything runs in a transaction that is rolled back.
-- Records in states no client role may create (extracted, confirmed, rejected, corrected) are inserted as the
-- table owner; every behaviour under test then runs as authenticated.

begin;
create extension if not exists pgtap with schema extensions;
select plan(47);

-- ------------------------------------------------------------------ fixtures (as owner)
insert into auth.users (id, email)
values ('a6000000-0000-4000-8000-000000000001', 'pgtap-classify-a@example.invalid'),
       ('b6000000-0000-4000-8000-000000000002', 'pgtap-classify-b@example.invalid');
insert into public.trace_candidate_profiles (candidate_profile_id, owner_user_id)
values ('a6000000-0000-4000-8000-0000000000aa', 'a6000000-0000-4000-8000-000000000001'),
       ('b6000000-0000-4000-8000-0000000000bb', 'b6000000-0000-4000-8000-000000000002');
insert into public.trace_resume_documents (resume_id, owner_user_id, candidate_profile_id, storage_object_path, mime_type, byte_size)
values ('a6000000-0000-4000-8000-000000000701', 'a6000000-0000-4000-8000-000000000001', 'a6000000-0000-4000-8000-0000000000aa',
        'a6000000-0000-4000-8000-000000000001/a6000000-0000-4000-8000-000000000701.pdf', 'application/pdf', 1000);
insert into public.trace_candidate_employments (candidate_employment_id, owner_user_id, candidate_profile_id, sequence_index, title_raw,
  employment_type, start_year, start_month, start_precision, review_status, information_origin)
values ('a6000000-0000-4000-8000-000000000101', 'a6000000-0000-4000-8000-000000000001', 'a6000000-0000-4000-8000-0000000000aa', 1,
        'Product Manager', 'full_time', 2020, 1, 'month', 'user_confirmed', 'user_stated');
insert into public.trace_candidate_evidence (candidate_evidence_id, owner_user_id, candidate_profile_id, candidate_employment_id, evidence_type,
  evidence_text, review_status, information_origin, extraction_confidence, source_resume_id, source_location)
select x.id::uuid, 'a6000000-0000-4000-8000-000000000001', 'a6000000-0000-4000-8000-0000000000aa', 'a6000000-0000-4000-8000-000000000101',
       'responsibility', x.txt, x.status::public.trace_record_review_status, x.origin::public.trace_information_origin,
       case when x.origin = 'source_extracted' then 0.800 end,
       case when x.origin = 'source_extracted' then 'a6000000-0000-4000-8000-000000000701'::uuid end,
       case when x.origin = 'source_extracted' then 'Experience, entry 1, bullet ' || x.n end
from (values ('a6000000-0000-4000-8000-000000000201', 1, 'Owned the payments roadmap', 'pending_review', 'source_extracted'),
             ('a6000000-0000-4000-8000-000000000202', 2, 'Ran customer interviews', 'pending_review', 'user_stated'),
             ('a6000000-0000-4000-8000-000000000203', 3, 'Prioritised the backlog', 'user_confirmed', 'user_stated'),
             ('a6000000-0000-4000-8000-000000000204', 4, 'Rejected bullet', 'rejected', 'source_extracted'),
             ('a6000000-0000-4000-8000-000000000205', 5, 'Grew retention', 'user_corrected', 'source_extracted'),
             ('a6000000-0000-4000-8000-000000000206', 6, 'Shipped a synthetic app', 'user_confirmed', 'source_extracted')) as x(id, n, txt, status, origin);
insert into public.trace_candidate_evidence (candidate_evidence_id, owner_user_id, candidate_profile_id, evidence_type, evidence_text,
  review_status, information_origin)
values ('b6000000-0000-4000-8000-000000000301', 'b6000000-0000-4000-8000-000000000002', 'b6000000-0000-4000-8000-0000000000bb',
        'responsibility', 'Another candidate''s evidence', 'user_confirmed', 'user_stated');

create temp table snap as
select candidate_evidence_id, evidence_text, evidence_type, responsibility_area, review_status, verification_status, information_origin,
       provenance, evidence_status, confidence, extraction_confidence, source_resume_id, extraction_run_id, source_location,
       candidate_employment_id, candidate_activity_id
from public.trace_candidate_evidence;
create temp table emp_snap as select to_jsonb(t) - 'updated_at' as j from public.trace_candidate_employments t;
grant select on snap, emp_snap to authenticated;

-- ------------------------------------------------------------------ 1. structure and privileges
select ok((select attgenerated = 's' from pg_attribute where attrelid = 'public.trace_candidate_evidence'::regclass
           and attname = 'area_classification_is_stale'), 'area_classification_is_stale is a stored generated column');
select is((select pg_get_userbyid(proowner) || ':' || prosecdef || ':' || array_to_string(proconfig, ',')
           from pg_proc where oid = 'trace_review.classify_evidence_areas(uuid, uuid, text[], text)'::regprocedure),
          'trace_review_writer:true:search_path=""', 'classify_evidence_areas is writer-owned, SECURITY DEFINER, with a pinned search_path');
select is((select string_agg(r || '=' || has_function_privilege(r, 'trace_review.classify_evidence_areas(uuid, uuid, text[], text)', 'EXECUTE'), ' ' order by r)
           from unnest(array['anon', 'trace_app_user', 'service_role']) r),
          'anon=false service_role=false trace_app_user=true', 'only the backend owner role (trace_app_user) calls classify_evidence_areas');
select is((select count(*)::int from (values ('trace_app_user'), ('anon'), ('service_role'), ('trace_ingest_writer')) r(rolname)
           cross join (values ('area_classification'), ('area_classification_basis'), ('area_classified_text')) c(col)
           cross join (values ('INSERT'), ('UPDATE')) x(p)
           where has_column_privilege(r.rolname, 'public.trace_candidate_evidence', c.col, x.p)), 0,
          'no client or ingest role can insert or update a classification column');
select is((select string_agg(column_name, ',' order by column_name) from information_schema.column_privileges
           where grantee = 'trace_review_writer' and table_name = 'trace_candidate_evidence' and privilege_type = 'UPDATE'),
          'area_classification,area_classification_basis,area_classification_none,area_classification_taxonomy_version,'   -- v2.18.0
          'area_classified_at,area_classified_text,area_qualifiers,evidence_text,evidence_type,review_status',
          'the writer''s evidence UPDATE columns: review (M2), correction (M4) and classification (M6); never provenance or verification');

-- ------------------------------------------------------------------ 2. classification
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a6000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select is(trace_review.classify_evidence_areas('a6000000-0000-4000-8000-0000000000aa', 'a6000000-0000-4000-8000-000000000201',
                                               '{roadmap_ownership,product_discovery,roadmap_ownership}', 'Owned the payments roadmap'),
          '{product_discovery,roadmap_ownership}'::text[], 'own extracted evidence is classified; duplicates removed, sorted');
select is((select row(area_classification, area_classification_basis, area_classified_text, area_classification_is_stale)::text
           from public.trace_candidate_evidence where candidate_evidence_id = 'a6000000-0000-4000-8000-000000000201'),
          '("{product_discovery,roadmap_ownership}",candidate_selected,"Owned the payments roadmap",f)',
          'the classification, its basis and the classified text are stored');
select is(trace_review.classify_evidence_areas('a6000000-0000-4000-8000-0000000000aa', 'a6000000-0000-4000-8000-000000000202',
                                               '{product_discovery}', 'Ran customer interviews'),
          '{product_discovery}'::text[], 'own user-entered draft evidence is classified');
select is(trace_review.classify_evidence_areas('a6000000-0000-4000-8000-0000000000aa', 'a6000000-0000-4000-8000-000000000203',
                                               '{prioritisation_backlog}', 'Prioritised the backlog'),
          '{prioritisation_backlog}'::text[], 'confirmed evidence is classified');
select is(trace_review.classify_evidence_areas('a6000000-0000-4000-8000-0000000000aa', 'a6000000-0000-4000-8000-000000000205',
                                               '{outcome_ownership}', 'Grew retention'),
          '{outcome_ownership}'::text[], 'corrected evidence is classified');
select is(trace_review.classify_evidence_areas('a6000000-0000-4000-8000-0000000000aa', 'a6000000-0000-4000-8000-000000000201',
                                               '{outcome_ownership}', 'Owned the payments roadmap'),
          '{outcome_ownership}'::text[], 'a classification replaces the previous one');
select is(trace_review.classify_evidence_areas('a6000000-0000-4000-8000-0000000000aa', 'a6000000-0000-4000-8000-000000000206',
                                               (select array_agg(responsibility_area) from public.trace_responsibility_areas), 'Shipped a synthetic app'),
          (select array_agg(responsibility_area order by responsibility_area) from public.trace_responsibility_areas),
          'every taxonomy area is accepted (the taxonomy has no inactive entries)');
select is((select count(*)::int from public.trace_candidate_evidence e join snap s using (candidate_evidence_id)
           where row(e.evidence_text, e.evidence_type, e.responsibility_area, e.review_status, e.verification_status, e.information_origin,
                     e.provenance, e.evidence_status, e.confidence, e.extraction_confidence, e.source_resume_id, e.extraction_run_id,
                     e.source_location, e.candidate_employment_id, e.candidate_activity_id)
             is distinct from row(s.evidence_text, s.evidence_type, s.responsibility_area, s.review_status, s.verification_status, s.information_origin,
                     s.provenance, s.evidence_status, s.confidence, s.extraction_confidence, s.source_resume_id, s.extraction_run_id,
                     s.source_location, s.candidate_employment_id, s.candidate_activity_id)), 0,
          'classification changed no text, review, verification, provenance, source, link or candidate-content area');
select is((select count(*)::int from public.trace_candidate_employments t where not exists (select 1 from emp_snap s where s.j = to_jsonb(t) - 'updated_at')), 0,
          'no employment changed');
select is((select review_status::text from public.trace_candidate_evidence where candidate_evidence_id = 'a6000000-0000-4000-8000-000000000201'),
          'pending_review', 'classified pending evidence stays pending (classification is not review)');

-- refusals
select throws_ok($$select trace_review.classify_evidence_areas('a6000000-0000-4000-8000-0000000000aa', 'a6000000-0000-4000-8000-000000000204', '{roadmap_ownership}', 'Rejected bullet')$$,
                 'TR001', null, 'rejected evidence cannot be classified');
select is((select area_classification from public.trace_candidate_evidence where candidate_evidence_id = 'a6000000-0000-4000-8000-000000000204'),
          null::text[], 'and stays unclassified');
select throws_ok($$select trace_review.classify_evidence_areas('b6000000-0000-4000-8000-0000000000bb', 'b6000000-0000-4000-8000-000000000301', '{roadmap_ownership}', 'Another candidate''s evidence')$$,
                 'TR005', null, 'another candidate''s evidence is not found');
select throws_ok($$select trace_review.classify_evidence_areas('a6000000-0000-4000-8000-0000000000aa', 'b6000000-0000-4000-8000-000000000301', '{roadmap_ownership}', 'Another candidate''s evidence')$$,
                 'TR005', null, 'nor under the caller''s own profile');
select throws_ok($$select trace_review.classify_evidence_areas('a6000000-0000-4000-8000-0000000000aa', 'a6000000-0000-4000-8000-000000000203', '{not_an_area}', 'Prioritised the backlog')$$,
                 'TR006', null, 'an unknown area is refused');
select throws_ok($$select trace_review.classify_evidence_areas('a6000000-0000-4000-8000-0000000000aa', 'a6000000-0000-4000-8000-000000000203', array['roadmap_ownership', null], 'Prioritised the backlog')$$,
                 'TR006', null, 'a NULL area is refused');
select throws_ok($$select trace_review.classify_evidence_areas('a6000000-0000-4000-8000-0000000000aa', 'a6000000-0000-4000-8000-000000000203', '{}', 'Prioritised the backlog')$$,
                 'TR006', null, 'an empty classification is refused');
select throws_ok($$select trace_review.classify_evidence_areas('a6000000-0000-4000-8000-0000000000aa', 'a6000000-0000-4000-8000-000000000203', null, 'Prioritised the backlog')$$,
                 'TR006', null, 'a NULL classification is refused');
select throws_ok($$select trace_review.classify_evidence_areas('a6000000-0000-4000-8000-0000000000aa', 'a6000000-0000-4000-8000-000000000203', '{ROADMAP_OWNERSHIP}', 'Prioritised the backlog')$$,
                 'TR006', null, 'area codes are exact (no case folding)');
select throws_ok($$select trace_review.classify_evidence_areas('a6000000-0000-4000-8000-0000000000aa', 'a6000000-0000-4000-8000-000000000203', '{roadmap_ownership}', 'Prioritised  the backlog')$$,
                 'TR018', null, 'a text other than the record''s is refused');
select is((select area_classification from public.trace_candidate_evidence where candidate_evidence_id = 'a6000000-0000-4000-8000-000000000203'),
          '{prioritisation_backlog}'::text[], 'refused calls changed nothing');
select set_config('request.jwt.claims', '', true);
select throws_ok($$select trace_review.classify_evidence_areas('a6000000-0000-4000-8000-0000000000aa', 'a6000000-0000-4000-8000-000000000203', '{roadmap_ownership}', 'Prioritised the backlog')$$,
                 'TR004', null, 'no request identity, no classification');
select set_config('request.jwt.claims', '{"sub": "a6000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);

-- ------------------------------------------------------------------ 3. direct SQL cannot forge classification or provenance
select throws_ok($$update public.trace_candidate_evidence set area_classification = '{roadmap_ownership}' where candidate_evidence_id = 'a6000000-0000-4000-8000-000000000202'$$,
                 '42501', null, 'a candidate cannot write a classification directly');
select throws_ok($$update public.trace_candidate_evidence set area_classification_basis = 'candidate_selected' where candidate_evidence_id = 'a6000000-0000-4000-8000-000000000202'$$,
                 '42501', null, 'a candidate cannot write the classification basis');
select throws_ok($$update public.trace_candidate_evidence set area_classified_text = evidence_text where candidate_evidence_id = 'a6000000-0000-4000-8000-000000000202'$$,
                 '42501', null, 'a candidate cannot reset staleness');
select throws_ok($$insert into public.trace_candidate_evidence (owner_user_id, candidate_profile_id, evidence_type, evidence_text, area_classification,
                     area_classification_basis, area_classified_text)
                   values ('a6000000-0000-4000-8000-000000000001', 'a6000000-0000-4000-8000-0000000000aa', 'responsibility', 'Forged',
                           '{roadmap_ownership}', 'candidate_selected', 'Forged')$$,
                 '42501', null, 'a candidate cannot insert classified evidence');
reset role;
set local role service_role;
select throws_ok($$update public.trace_candidate_evidence set area_classification = '{roadmap_ownership}' where candidate_evidence_id = 'a6000000-0000-4000-8000-000000000202'$$,
                 '42501', null, 'service_role cannot write a classification');
reset role;
select throws_ok($$update public.trace_candidate_evidence set area_classification_basis = 'verified' where candidate_evidence_id = 'a6000000-0000-4000-8000-000000000203'$$,
                 '23514', null, 'even the owner cannot label a classification other than candidate_selected');
select throws_ok($$update public.trace_candidate_evidence set area_classification = '{roadmap_ownership}' where candidate_evidence_id = 'a6000000-0000-4000-8000-000000000204'$$,
                 '23514', null, 'even the owner cannot store areas without a basis and classified text');
select throws_ok($$update public.trace_candidate_evidence set area_classification = '{}' where candidate_evidence_id = 'a6000000-0000-4000-8000-000000000203'$$,
                 '23514', null, 'an empty classification is refused by CHECK');
select throws_ok($$update public.trace_candidate_evidence set area_classification = array['roadmap_ownership', null] where candidate_evidence_id = 'a6000000-0000-4000-8000-000000000203'$$,
                 '23514', null, 'a NULL area is refused by CHECK');

-- ------------------------------------------------------------------ 4. staleness after text changes
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a6000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select is(trace_review.correct_record('evidence', 'a6000000-0000-4000-8000-0000000000aa', 'a6000000-0000-4000-8000-000000000203',
                                      '{"evidence_text": "Prioritised the payments backlog"}'), 'user_corrected', 'an M4 correction changes classified text');
select is((select row(area_classification_is_stale, area_classification, area_classified_text)::text from public.trace_candidate_evidence
           where candidate_evidence_id = 'a6000000-0000-4000-8000-000000000203'),
          '(t,{prioritisation_backlog},"Prioritised the backlog")', 'the classification is kept but marked stale, with the text it described');
select is(trace_review.classify_evidence_areas('a6000000-0000-4000-8000-0000000000aa', 'a6000000-0000-4000-8000-000000000203',
                                               '{prioritisation_backlog,roadmap_ownership}', 'Prioritised the payments backlog'),
          '{prioritisation_backlog,roadmap_ownership}'::text[], 'a fresh classification of the corrected text');
select ok(not (select area_classification_is_stale from public.trace_candidate_evidence where candidate_evidence_id = 'a6000000-0000-4000-8000-000000000203'),
          'is current again');
update public.trace_candidate_evidence set evidence_text = 'Ran customer and partner interviews' where candidate_evidence_id = 'a6000000-0000-4000-8000-000000000202';
select ok((select area_classification_is_stale from public.trace_candidate_evidence where candidate_evidence_id = 'a6000000-0000-4000-8000-000000000202'),
          'a draft edit of the text also makes its classification stale');

-- ------------------------------------------------------------------ 5. M4 interplay and deletion
reset role;
set constraints trace_require_correction_history immediate;
set local role trace_app_user;
select lives_ok($$select trace_review.classify_evidence_areas('a6000000-0000-4000-8000-0000000000aa', 'a6000000-0000-4000-8000-000000000205',
                                                             '{outcome_ownership,people_leadership}', 'Grew retention')$$,
                'classifying corrected evidence needs no correction history (classification is not a correction)');
reset role;
set constraints trace_require_correction_history deferred;
select is((select count(*)::int from public.trace_candidate_corrections where record_id = 'a6000000-0000-4000-8000-000000000205'), 0, 'and writes none');
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a6000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select lives_ok($$delete from public.trace_candidate_evidence where candidate_evidence_id = 'a6000000-0000-4000-8000-000000000202'$$,
                'classified draft evidence can be deleted as before');
select lives_ok($$delete from public.trace_candidate_profiles where candidate_profile_id = 'a6000000-0000-4000-8000-0000000000aa'$$,
                'profile deletion still works with classified evidence');
reset role;
select is((select count(*)::int from public.trace_candidate_evidence where owner_user_id = 'a6000000-0000-4000-8000-000000000001'), 0,
          'every classified record went with its profile');
select is((select coalesce(area_classification_basis, 'none') from public.trace_candidate_evidence
           where candidate_evidence_id = 'b6000000-0000-4000-8000-000000000301'), 'none', 'the other candidate''s evidence was never touched');

select * from finish();
rollback;
