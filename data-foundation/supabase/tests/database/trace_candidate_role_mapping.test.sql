-- pgTAP tests for trusted role mapping of candidate employments (v2.6.0, migration 20261111180000).
-- Run on a LOCAL Supabase stack only:  supabase test db
-- Synthetic test users and records only; everything runs in a transaction that is rolled back.
-- Records in states no client role may create (extracted, confirmed, rejected, corrected, legacy-mapped)
-- are inserted as the table owner; every behaviour under test then runs as authenticated.

begin;
create extension if not exists pgtap with schema extensions;
select plan(97);

-- ------------------------------------------------------------------ fixtures (as owner)
insert into auth.users (id, email)
values ('a5000000-0000-4000-8000-000000000001', 'pgtap-map-a@example.invalid'),
       ('b5000000-0000-4000-8000-000000000002', 'pgtap-map-b@example.invalid');
insert into public.trace_candidate_profiles (candidate_profile_id, owner_user_id)
values ('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000001'),
       ('b5000000-0000-4000-8000-0000000000bb', 'b5000000-0000-4000-8000-000000000002');
insert into public.trace_resume_documents (resume_id, owner_user_id, candidate_profile_id, storage_object_path, mime_type, byte_size)
values ('a5000000-0000-4000-8000-000000000701', 'a5000000-0000-4000-8000-000000000001', 'a5000000-0000-4000-8000-0000000000aa',
        'a5000000-0000-4000-8000-000000000001/a5000000-0000-4000-8000-000000000701.pdf', 'application/pdf', 1000);
insert into public.trace_candidate_employments (candidate_employment_id, owner_user_id, candidate_profile_id, sequence_index, title_raw,
  employment_type, start_year, start_month, start_precision, review_status, information_origin, extraction_confidence, source_resume_id, source_location)
select x.id::uuid, 'a5000000-0000-4000-8000-000000000001', 'a5000000-0000-4000-8000-0000000000aa', x.n, x.title, 'full_time', 2020, 1, 'month',
       x.status::public.trace_record_review_status, x.origin::public.trace_information_origin,
       case when x.origin = 'source_extracted' then 0.800 end, case when x.origin = 'source_extracted' then 'a5000000-0000-4000-8000-000000000701'::uuid end,
       case when x.origin = 'source_extracted' then 'Experience, entry ' || x.n end
from (values ('a5000000-0000-4000-8000-000000000101', 1, 'Product Manager', 'pending_review', 'source_extracted'),
             ('a5000000-0000-4000-8000-000000000102', 2, 'Senior Software Engineer', 'user_confirmed', 'user_stated'),
             ('a5000000-0000-4000-8000-000000000103', 3, 'Product Owner', 'user_confirmed', 'user_stated'),
             ('a5000000-0000-4000-8000-000000000104', 4, 'Associate', 'pending_review', 'source_extracted'),
             ('a5000000-0000-4000-8000-000000000105', 5, 'Chief Vibes Officer', 'user_confirmed', 'user_stated'),
             ('a5000000-0000-4000-8000-000000000106', 6, 'Product Manager', 'rejected', 'source_extracted'),
             ('a5000000-0000-4000-8000-000000000107', 7, 'Project Manager', 'pending_review', 'user_stated'),
             ('a5000000-0000-4000-8000-000000000108', 8, 'Senior Product Owner', 'user_corrected', 'source_extracted'),
             ('a5000000-0000-4000-8000-000000000109', 9, 'Data Analyst', 'user_confirmed', 'user_stated'),
             ('a5000000-0000-4000-8000-000000000110', 10, 'Program Management', 'pending_review', 'user_stated'),
             ('a5000000-0000-4000-8000-000000000111', 11, 'Product Manager', 'pending_review', 'user_stated'),
             ('a5000000-0000-4000-8000-000000000112', 12, 'Generalist', 'pending_review', 'user_stated'),
             ('a5000000-0000-4000-8000-000000000113', 13, 'QA Engineer', 'pending_review', 'user_stated'),
             ('a5000000-0000-4000-8000-000000000114', 14, 'Product Owner (Payments)', 'pending_review', 'user_stated'),
             ('a5000000-0000-4000-8000-000000000115', 15, 'PRODUCT OWNER - Growth', 'user_confirmed', 'user_stated'),
             ('a5000000-0000-4000-8000-000000000116', 16, E'Lead\u00a0Product\u3000Owner', 'pending_review', 'source_extracted'),
             ('a5000000-0000-4000-8000-000000000117', 17, 'Product Owner/Scrum Master', 'pending_review', 'user_stated'),
             ('a5000000-0000-4000-8000-000000000118', 18, 'Product Ownership Lead', 'pending_review', 'user_stated'),
             ('a5000000-0000-4000-8000-000000000119', 19, 'Product Owner Payments', 'pending_review', 'user_stated')) as x(id, n, title, status, origin);
-- A mapping set outside the mapping functions (administrative / legacy): never replaced automatically.
update public.trace_candidate_employments set role_id = 'ROLE-DA', mapping_status = 'confirmed', mapping_confidence = 1
 where candidate_employment_id = 'a5000000-0000-4000-8000-000000000109';
insert into public.trace_candidate_employments (candidate_employment_id, owner_user_id, candidate_profile_id, sequence_index, title_raw,
  employment_type, start_year, start_month, start_precision, review_status, information_origin)
values ('b5000000-0000-4000-8000-000000000201', 'b5000000-0000-4000-8000-000000000002', 'b5000000-0000-4000-8000-0000000000bb', 1,
        'Product Manager', 'full_time', 2020, 1, 'month', 'user_confirmed', 'user_stated');

-- ------------------------------------------------------------------ 1. structure and privileges
select ok((select attgenerated = 's' from pg_attribute where attrelid = 'public.trace_candidate_employments'::regclass and attname = 'mapping_is_stale'),
          'mapping_is_stale is a stored generated column');
select ok(not (select attnotnull from pg_attribute where attrelid = 'public.trace_candidate_employments'::regclass and attname = 'mapping_confidence'),
          'mapping_confidence may be NULL (candidate-selected mappings carry no confidence)');
select is((select string_agg(p.proname || ':' || pg_get_userbyid(p.proowner) || ':' || p.prosecdef || ':' || array_to_string(p.proconfig, ','), ' ' order by p.proname)
           from pg_proc p where p.oid in ('trace_review.apply_alias_mapping(uuid, uuid, text, text)'::regprocedure,
                                          'trace_review.choose_employment_role(uuid, uuid, text, text, text[])'::regprocedure)),
          'apply_alias_mapping:trace_review_writer:true:search_path="" choose_employment_role:trace_review_writer:true:search_path=""',
          'the mapping functions are writer-owned, SECURITY DEFINER, with a pinned search_path');
select is((select string_agg(r || '=' || has_function_privilege(r, f, 'EXECUTE'), ' ' order by r, f::text)
           from unnest(array['anon', 'trace_app_user', 'service_role']) r,
                unnest(array['trace_review.apply_alias_mapping(uuid, uuid, text, text)'::regprocedure,
                             'trace_review.choose_employment_role(uuid, uuid, text, text, text[])'::regprocedure]) f),
          'anon=false anon=false service_role=false service_role=false trace_app_user=true trace_app_user=true',
          'only the backend owner role (trace_app_user) calls the mapping functions');
select is((select count(*)::int from (values ('trace_app_user'), ('anon'), ('service_role'), ('trace_ingest_writer')) r(rolname)
           cross join (values ('role_id'), ('seniority_code'), ('mapping_status'), ('mapping_confidence'), ('mapping_note'), ('role_mapping_basis'),
                              ('role_mapping_alias_id'), ('mapped_title_raw'), ('role_mapping_ownership_areas')) c(col)
           cross join (values ('INSERT'), ('UPDATE')) x(p)
           where has_column_privilege(r.rolname, 'public.trace_candidate_employments', c.col, x.p)), 0,
          'no client or ingest role can insert or update a mapping column');
select is((select string_agg(column_name, ',' order by column_name) from information_schema.column_privileges
           where grantee = 'trace_review_writer' and table_name = 'trace_candidate_employments' and privilege_type = 'UPDATE'),
          'candidate_employer_id,classification_note,counts_as_full_time_experience,employment_type,end_month,end_precision,end_year,'
          'is_current_as_stated,mapped_title_raw,mapping_confidence,mapping_note,mapping_status,primary_career_sequence,review_status,'
          'role_id,role_mapping_alias_id,role_mapping_basis,role_mapping_ownership_areas,seniority_code,sequence_index,start_month,'
          'start_precision,start_year,title_raw',
          'the writer''s UPDATE columns: review (M2), correction (M4) and mapping (M5); never provenance or verification');

-- normalisation contract v1 (identical in the backend; parity is tested there for every code point to U+30FF)
select is(trace_util.normalize_title(E'\t Product\u00a0\u00a0Manager\n'), 'product manager', 'tabs, newlines and NBSP collapse to one space, trimmed');
select is(trace_util.normalize_title(E'Product\u3000\u2028\u0085\u2003Manager\u001f'), 'product manager', 'Unicode separators (U+3000, U+2028, U+0085, U+2003, U+001F) are whitespace');
select is(trace_util.normalize_title(E'Product\u200bManager'), E'product\u200bmanager', 'a zero-width space is not whitespace: kept, so no alias matches');
select is(trace_util.normalize_title(E'\u212aEY ACCOUNT'), E'\u212aey account', 'only ASCII A-Z are lower-cased: the Kelvin sign is never folded into k');
select is(trace_util.normalize_title('Director - Product (UI/UX)!'), 'director - product (ui/ux)!', 'punctuation is kept');
select is(trace_util.normalize_title(E' \t\u00a0'), null, 'a whitespace-only title normalises to NULL');
select is((select string_agg(r || ':' || has_function_privilege(r, f, 'EXECUTE'), ' ' order by r, f::text)
           from unnest(array['anon', 'trace_app_user', 'service_role', 'trace_review_writer']) r,
                unnest(array['trace_util.normalize_title(text)'::regprocedure, 'trace_util.is_product_owner_title(text)'::regprocedure]) f),
          'anon:false anon:false service_role:false service_role:false trace_app_user:false trace_app_user:false trace_review_writer:true trace_review_writer:true',
          'only the writer executes the title functions');
select throws_ok($$insert into public.trace_role_aliases (alias_id, alias, role_id, default_confidence) values ('ALS-9950', E'\tTab Led Title', 'PM-PM', 0.9)$$,
                 '23514', null, 'an alias whose stored normalisation differs from the contract is refused');
select is((select count(*)::int from public.trace_role_aliases where alias_normalized is distinct from trace_util.normalize_title(alias)), 0,
          'every alias row satisfies the contract');
select is((select string_agg(t || '=' || trace_util.is_product_owner_title(t), ' | ' order by t collate "C")
           from unnest(array['Product Owner (Payments)', 'Product Owner - Growth', 'Senior Product Owner', 'Product Ownership Lead',
                             'Product-Owner', 'Product Owners', 'Product Owner2']) t),
          'Product Owner (Payments)=true | Product Owner - Growth=true | Product Owner2=false | Product Owners=false | Product Ownership Lead=false | Product-Owner=false | Senior Product Owner=true',
          'Product Owner family: the words, bounded by the title ends or non-alphanumerics');

-- ------------------------------------------------------------------ 2. automatic alias mapping
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a5000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select is(trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000101', 'ALS-0003', 'Product Manager'),
          'PM-PM', 'a unique, unambiguous alias maps automatically');
select is((select row(role_id, seniority_code, mapping_status::text, mapping_confidence, role_mapping_basis, role_mapping_alias_id, mapped_title_raw,
                      mapping_is_stale, review_status::text)::text
           from public.trace_candidate_employments where candidate_employment_id = 'a5000000-0000-4000-8000-000000000101'),
          '(PM-PM,,auto_high_confidence,0.950,alias_match,ALS-0003,"Product Manager",f,pending_review)',
          'alias role, no seniority hint, alias confidence, alias provenance and mapped title; review status unchanged (still pending)');
select is(trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000102', 'ALS-0051', 'Senior Software Engineer'),
          'ROLE-SWE', 'a confirmed record can be mapped');
select is((select seniority_code || ':' || review_status from public.trace_candidate_employments where candidate_employment_id = 'a5000000-0000-4000-8000-000000000102'),
          'senior:user_confirmed', 'the alias seniority hint becomes the override; review status unchanged');
select is(trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000101', 'ALS-0003', 'Product Manager'),
          'PM-PM', 'repeating an automatic mapping is idempotent');
select throws_ok($$select trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000103', 'ALS-0035', 'Product Owner')$$,
                 'TR019', null, 'a context-required alias (several roles) is never mapped automatically');
select throws_ok($$select trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000104', 'ALS-0042', 'Associate')$$,
                 'TR019', null, 'a context-required alias with one role is never mapped automatically');
select throws_ok($$select trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000105', 'ALS-0003', 'Chief Vibes Officer')$$,
                 'TR019', null, 'an alias that does not match the title exactly is refused');
select throws_ok($$select trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000107', 'ALS-9999', 'Project Manager')$$,
                 'TR006', null, 'an unknown alias is refused');
select throws_ok($$select trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000107', 'ALS-0090', 'Project  Manager')$$,
                 'TR018', null, 'a title other than the record''s is refused');
select throws_ok($$select trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000106', 'ALS-0003', 'Product Manager')$$,
                 'TR001', null, 'a rejected record cannot be mapped');
select throws_ok($$select trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000109', 'ALS-0068', 'Data Analyst')$$,
                 'TR019', null, 'a mapping set outside the functions is never replaced automatically');
select throws_ok($$select trace_review.apply_alias_mapping('b5000000-0000-4000-8000-0000000000bb', 'b5000000-0000-4000-8000-000000000201', 'ALS-0003', 'Product Manager')$$,
                 'TR005', null, 'another candidate''s record is not found');
select throws_ok($$select trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000110', 'ALS-0049', 'Program Management')$$,
                 'TR019', null, 'a context-required alias is never automatic, even when its single role is resolved');
reset role;
-- Test-only alias rows (rolled back): a non-context-required duplicate and an alias of an unresolved role.
insert into public.trace_role_aliases (alias_id, alias, role_id, context_required, default_confidence)
values ('ALS-9901', 'Product Manager', 'ROLE-PROJ', false, 0.9), ('ALS-9902', 'Generalist', 'ROLE-UNKNOWN', false, 0.9);
update public.trace_role_taxonomy set active = false where role_id = 'ROLE-QA';
set local role trace_app_user;
select throws_ok($$select trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000111', 'ALS-0003', 'Product Manager')$$,
                 'TR019', null, 'a title with several alias rows is never automatic, even if the data marks none context-required');
select throws_ok($$select trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000112', 'ALS-9902', 'Generalist')$$,
                 'TR019', null, 'an alias of an unresolved role is never automatic');
select throws_ok($$select trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000113', 'ALS-0060', 'QA Engineer')$$,
                 'TR019', null, 'an alias of a retired role is never automatic');
reset role;
delete from public.trace_role_aliases where alias_id in ('ALS-9901', 'ALS-9902');
update public.trace_role_taxonomy set active = true where role_id = 'ROLE-QA';
set local role trace_app_user;
select is(trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000107', 'ALS-0090', 'Project Manager'),
          'ROLE-PROJ', 'Project Manager maps to ROLE-PROJ (the alias table), whatever a fixture says');

-- ------------------------------------------------------------------ 3. candidate decisions
select is(trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000105', 'PM-SPM', 'Chief Vibes Officer', null),
          'PM-SPM', 'an unmatched title can be mapped by the candidate to any active, resolved role');
select is((select row(role_id, seniority_code, mapping_status::text, mapping_confidence, role_mapping_basis, role_mapping_alias_id, mapped_title_raw, review_status::text)::text
           from public.trace_candidate_employments where candidate_employment_id = 'a5000000-0000-4000-8000-000000000105'),
          '(PM-SPM,,confirmed,,candidate_selected,,"Chief Vibes Officer",user_confirmed)',
          'candidate_selected: confirmed, no confidence, no alias, the mapped title kept; review status unchanged');
select is(trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000104', 'ROLE-BA', 'Associate', null),
          'ROLE-BA', 'a generic title (only ROLE-UNKNOWN as an alias option) is decided from the full taxonomy');
select throws_ok($$select trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000104', 'ROLE-UNKNOWN', 'Associate', null)$$,
                 'TR006', null, 'ROLE-UNKNOWN is never a choice');
select throws_ok($$select trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000104', 'ROLE-NOPE', 'Associate', null)$$,
                 'TR006', null, 'an unknown role is refused');
reset role;
update public.trace_role_taxonomy set active = false where role_id = 'ROLE-OPS';
set local role trace_app_user;
select throws_ok($$select trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000104', 'ROLE-OPS', 'Associate', null)$$,
                 'TR006', null, 'a retired role is refused');
reset role;
update public.trace_role_taxonomy set active = true where role_id = 'ROLE-OPS';
set local role trace_app_user;
select throws_ok($$select trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000103', 'PM-PM', 'Product Owner', null)$$,
                 'TR020', null, 'Product Owner -> PM-PM needs PM-ownership responsibility areas');
select throws_ok($$select trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000103', 'PM-SPM', 'Product Owner', '{}')$$,
                 'TR020', null, 'the rule covers every product-management role, and an empty list is no clarification');
select throws_ok($$select trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000103', 'PM-PM', 'Product Owner', '{stakeholder_management}')$$,
                 'TR006', null, 'only PM-ownership responsibility areas count');
select throws_ok($$select trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000103', 'PM-PM', 'Product Owner', array['roadmap_ownership', null])$$,
                 'TR006', null, 'a NULL area is refused');
select is(trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000103', 'PM-PM', 'Product Owner',
                                              '{roadmap_ownership,product_discovery,roadmap_ownership}'),
          'PM-PM', 'with PM-ownership areas, Product Owner -> PM-PM is accepted');
select is((select role_mapping_ownership_areas from public.trace_candidate_employments where candidate_employment_id = 'a5000000-0000-4000-8000-000000000103'),
          '{product_discovery,roadmap_ownership}'::text[], 'the clarification is stored with the mapping (distinct, sorted)');
select is(trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000103', 'ROLE-OWNER', 'Product Owner', null),
          'ROLE-OWNER', 'Product Owner -> ROLE-OWNER needs no clarification, and replaces the earlier decision');
select is((select role_mapping_ownership_areas from public.trace_candidate_employments where candidate_employment_id = 'a5000000-0000-4000-8000-000000000103'),
          null::text[], 'the replaced decision''s clarification is cleared');
select throws_ok($$select trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000105', 'PM-PM', 'Chief Vibes Officer', '{roadmap_ownership}')$$,
                 'TR006', null, 'ownership areas are refused where the rule does not apply');
select throws_ok($$select trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000108', 'PM-SPM', 'Senior Product Owner', null)$$,
                 'TR020', null, 'Senior Product Owner follows the same rule');
select is(trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000108', 'PM-SPM', 'Senior Product Owner', '{outcome_ownership}'),
          'PM-SPM', 'a corrected (user_corrected) record can be mapped by decision');
select is((select review_status::text from public.trace_candidate_employments where candidate_employment_id = 'a5000000-0000-4000-8000-000000000108'),
          'user_corrected', 'its review status is unchanged');
select is(trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000101', 'PM-APM', 'Product Manager', null),
          'PM-APM', '"None of these": a decision replaces an automatic mapping');
select throws_ok($$select trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000101', 'ALS-0003', 'Product Manager')$$,
                 'TR019', null, 'an automatic mapping never replaces a current candidate decision');
select throws_ok($$select trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000106', 'PM-PM', 'Product Manager', null)$$,
                 'TR001', null, 'a rejected record cannot be mapped by decision either');
select throws_ok($$select trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000105', 'PM-PM', 'Chief Vibes  Officer', null)$$,
                 'TR018', null, 'a decision for a title other than the record''s is refused');
select throws_ok($$select trace_review.choose_employment_role('b5000000-0000-4000-8000-0000000000bb', 'b5000000-0000-4000-8000-000000000201', 'PM-PM', 'Product Manager', null)$$,
                 'TR005', null, 'another candidate''s record cannot be mapped');
select set_config('request.jwt.claims', '', true);
select throws_ok($$select trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000105', 'PM-PM', 'Chief Vibes Officer', null)$$,
                 'TR004', null, 'no request identity, no mapping');
select set_config('request.jwt.claims', '{"sub": "a5000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);

-- qualified Product Owner titles (no alias matches them): the clarification rule still applies
select throws_ok($$select trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000114', 'PM-PM', 'Product Owner (Payments)', null)$$,
                 'TR020', null, 'a qualified Product Owner title needs the clarification');
select throws_ok($$select trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000115', 'PM-SPM', 'PRODUCT OWNER - Growth', '{}')$$,
                 'TR020', null, 'case and dash qualifiers do not bypass it');
select throws_ok($$select trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000116', 'PM-LEAD', E'Lead\u00a0Product\u3000Owner', null)$$,
                 'TR020', null, 'Unicode whitespace does not bypass it');
select throws_ok($$select trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000117', 'PM-PM', 'Product Owner/Scrum Master', null)$$,
                 'TR020', null, 'punctuation boundaries do not bypass it');
select is(trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000114', 'PM-PM', 'Product Owner (Payments)', '{outcome_ownership}'),
          'PM-PM', 'with a PM-ownership area the qualified title maps to PM-PM');
select is((select review_status::text from public.trace_candidate_employments where candidate_employment_id = 'a5000000-0000-4000-8000-000000000114'),
          'pending_review', 'mapping did not review the record');
select is(trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000115', 'ROLE-OWNER', 'PRODUCT OWNER - Growth', null),
          'ROLE-OWNER', 'a qualified Product Owner title maps to ROLE-OWNER without clarification');
select is(trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000118', 'PM-PM', 'Product Ownership Lead', null),
          'PM-PM', 'a non-Product-Owner title needs no clarification');
select throws_ok($$select trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000118', 'PM-PM', 'Product Ownership Lead', '{roadmap_ownership}')$$,
                 'TR006', null, 'and is refused ownership areas');
select throws_ok($$select trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000114', 'ALS-0035', 'Product Owner (Payments)')$$,
                 'TR019', null, 'a qualified title is never mapped automatically through a Product Owner alias');
reset role;
insert into public.trace_role_aliases (alias_id, alias, role_id, context_required, default_confidence)
values ('ALS-9903', 'Product Owner Payments', 'PM-PM', false, 0.9);          -- test-only, rolled back
set local role trace_app_user;
select throws_ok($$select trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000119', 'ALS-9903', 'Product Owner Payments')$$,
                 'TR020', null, 'even a non-context alias never maps a Product Owner title to product management automatically');
reset role;
delete from public.trace_role_aliases where alias_id = 'ALS-9903';
set local role trace_app_user;

-- ------------------------------------------------------------------ 4. direct SQL cannot forge mapping fields
select throws_ok($$update public.trace_candidate_employments set role_id = 'PM-DIR', mapping_status = 'confirmed', mapping_confidence = 1
                   where candidate_employment_id = 'a5000000-0000-4000-8000-000000000107'$$,
                 '42501', null, 'a candidate cannot write role mapping directly');
select throws_ok($$update public.trace_candidate_employments set role_mapping_basis = 'candidate_selected' where candidate_employment_id = 'a5000000-0000-4000-8000-000000000107'$$,
                 '42501', null, 'a candidate cannot write mapping provenance directly');
select throws_ok($$update public.trace_candidate_employments set mapped_title_raw = title_raw where candidate_employment_id = 'a5000000-0000-4000-8000-000000000107'$$,
                 '42501', null, 'a candidate cannot reset staleness directly');
select throws_ok($$insert into public.trace_candidate_employments (owner_user_id, candidate_profile_id, sequence_index, title_raw, role_mapping_basis, mapped_title_raw)
                   values ('a5000000-0000-4000-8000-000000000001', 'a5000000-0000-4000-8000-0000000000aa', 20, 'Forged', 'alias_match', 'Forged')$$,
                 '42501', null, 'a candidate cannot insert a mapped record');
reset role;
set local role service_role;
select throws_ok($$update public.trace_candidate_employments set role_id = 'PM-DIR', mapping_status = 'confirmed' where candidate_employment_id = 'a5000000-0000-4000-8000-000000000107'$$,
                 '42501', null, 'service_role cannot write role mapping');
reset role;
select throws_ok($$update public.trace_candidate_employments set mapping_confidence = 0.9 where candidate_employment_id = 'a5000000-0000-4000-8000-000000000105'$$,
                 '23514', null, 'even the owner cannot give a candidate decision a confidence (CHECK)');
select throws_ok($$update public.trace_candidate_employments set role_mapping_basis = null where candidate_employment_id = 'a5000000-0000-4000-8000-000000000101'$$,
                 '23514', null, 'even the owner cannot drop the basis but keep the mapped title (CHECK)');
select throws_ok($$update public.trace_candidate_employments set mapped_title_raw = null where candidate_employment_id = 'a5000000-0000-4000-8000-000000000105'$$,
                 '23514', null, 'a mapping basis always keeps the title it was based on (CHECK)');
select throws_ok($$update public.trace_candidate_employments set role_mapping_alias_id = null where candidate_employment_id = 'a5000000-0000-4000-8000-000000000102'$$,
                 '23514', null, 'an alias match always names its alias (CHECK)');
select throws_ok($$update public.trace_candidate_employments set mapping_confidence = null where candidate_employment_id = 'a5000000-0000-4000-8000-000000000109'$$,
                 '23514', null, 'a confidence is NULL only for a candidate decision (CHECK)');

-- ------------------------------------------------------------------ 5. stale mappings after title changes
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a5000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select is(trace_review.correct_record('employment', 'a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000102',
                                      '{"title_raw": "Staff Software Engineer"}'), 'user_corrected', 'an M4 correction changes a mapped title');
select is((select row(mapping_is_stale, role_id, mapped_title_raw, role_mapping_basis)::text from public.trace_candidate_employments
           where candidate_employment_id = 'a5000000-0000-4000-8000-000000000102'),
          '(t,ROLE-SWE,"Senior Software Engineer",alias_match)', 'the mapping is kept but marked stale, with the title it was based on');
select throws_ok($$select trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000102', 'ALS-0051', 'Staff Software Engineer')$$,
                 'TR019', null, 'the old alias no longer matches the corrected title');
select is(trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000102', 'ROLE-SWE', 'Staff Software Engineer', null),
          'ROLE-SWE', 'a fresh decision maps the corrected title');
select is((select mapping_is_stale::text || ':' || role_mapping_basis from public.trace_candidate_employments where candidate_employment_id = 'a5000000-0000-4000-8000-000000000102'),
          'false:candidate_selected', 'after the fresh decision the mapping is current');
update public.trace_candidate_employments set title_raw = 'Project Lead' where candidate_employment_id = 'a5000000-0000-4000-8000-000000000107';
select ok((select mapping_is_stale from public.trace_candidate_employments where candidate_employment_id = 'a5000000-0000-4000-8000-000000000107'),
          'a draft edit of the title also makes its mapping stale');
select is((select role_id || ':' || review_status from public.trace_candidate_employments where candidate_employment_id = 'a5000000-0000-4000-8000-000000000107'),
          'ROLE-PROJ:pending_review', 'the stale mapping is not silently re-mapped, and the draft stays pending');
select is(trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000105', 'PM-LEAD', 'Chief Vibes Officer', null),
          'PM-LEAD', 'a decision may be changed');
select is(trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000107', 'ROLE-PGM', 'Project Lead', null),
          'ROLE-PGM', 'a fresh decision replaces the stale automatic mapping');
update public.trace_candidate_employments set title_raw = 'Program Manager' where candidate_employment_id = 'a5000000-0000-4000-8000-000000000107';
select ok((select mapping_is_stale from public.trace_candidate_employments where candidate_employment_id = 'a5000000-0000-4000-8000-000000000107'),
          'a candidate decision goes stale when its title changes');
select is(trace_review.apply_alias_mapping('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000107', 'ALS-0091', 'Program Manager'),
          'ROLE-PGM', 'a stale candidate decision may be replaced by an exact alias match');
select is((select role_mapping_basis || ':' || mapping_is_stale from public.trace_candidate_employments where candidate_employment_id = 'a5000000-0000-4000-8000-000000000107'),
          'alias_match:false', 'the new mapping is current and automatic');

-- ------------------------------------------------------------------ 6. M4 interplay and deletion
reset role;
set constraints trace_require_correction_history immediate;
set local role trace_app_user;
select lives_ok($$select trace_review.choose_employment_role('a5000000-0000-4000-8000-0000000000aa', 'a5000000-0000-4000-8000-000000000108', 'PM-LEAD',
                                                             'Senior Product Owner', '{prioritisation_backlog}')$$,
                'mapping a corrected record needs no correction history (mapping is not a correction)');
reset role;
set constraints trace_require_correction_history deferred;
select is((select count(*)::int from public.trace_candidate_corrections where record_id = 'a5000000-0000-4000-8000-000000000108'), 0,
          'and writes none');
set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "a5000000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select lives_ok($$delete from public.trace_candidate_employments where candidate_employment_id = 'a5000000-0000-4000-8000-000000000105'$$,
                'a mapped record can be deleted as before');
select lives_ok($$delete from public.trace_candidate_profiles where candidate_profile_id = 'a5000000-0000-4000-8000-0000000000aa'$$,
                'profile deletion still works with mapped records');
reset role;
select is((select count(*)::int from public.trace_candidate_employments where owner_user_id = 'a5000000-0000-4000-8000-000000000001'), 0,
          'every mapped record went with its profile');
select is((select role_id || ':' || coalesce(role_mapping_basis, 'none') from public.trace_candidate_employments
           where candidate_employment_id = 'b5000000-0000-4000-8000-000000000201'), 'ROLE-UNKNOWN:none',
          'the other candidate''s record was never touched');

select * from finish();
rollback;
