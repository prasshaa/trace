-- v2.34.0: candidate embeddings are owner-scoped, unreachable by clients, deleted with their source; role knowledge is
-- a separate corpus that refuses research content. Rolled back.
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(12);

select ok(exists (select 1 from pg_extension where extname = 'vector'), 'pgvector is installed');
select ok(not has_table_privilege('authenticated', 'trace_private.candidate_embeddings', 'SELECT')
          and not has_table_privilege('anon', 'trace_private.candidate_embeddings', 'SELECT')
          and not has_table_privilege('service_role', 'trace_private.candidate_embeddings', 'SELECT')
          and not has_table_privilege('trace_app_user', 'trace_private.candidate_embeddings', 'SELECT'),
          'no client role reads candidate embeddings directly');
select ok(has_function_privilege('trace_app_user', 'trace_semantic.match_my_evidence(uuid, real[], text, text, integer)', 'EXECUTE')
          and not has_function_privilege('authenticated', 'trace_semantic.store_my_embeddings(uuid, text, text, jsonb)', 'EXECUTE')
          and not has_function_privilege('anon', 'trace_semantic.my_embedding_sources(uuid, text, text)', 'EXECUTE'),
          'only the owner role calls the candidate functions');
select is((select string_agg(distinct pg_get_userbyid(p.proowner)::text, ',') from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'trace_semantic' and p.proname in ('my_embedding_sources', 'store_my_embeddings', 'match_my_evidence')),
          'trace_embedding_writer', 'owned by the dedicated writer');
select ok(not has_table_privilege('authenticated', 'trace_ref.knowledge_embeddings', 'SELECT')
          and has_table_privilege('trace_reference_reader', 'trace_ref.knowledge_embeddings', 'SELECT'),
          'role knowledge is read by the reference reader only');
select throws_ok($$insert into trace_ref.knowledge_embeddings (corpus, source_kind, source_key, source_version, content, content_sha256, model, model_version, embedding)
                   values ('career_research', 'responsibility_area', 'x', 'v', 'x', repeat('a', 64), 'm', 'v', array_fill(0.1::real, array[384])::extensions.vector)$$,
                 '23514', null, 'the research corpus cannot be written into role knowledge');

-- two owners, one evidence line each
insert into auth.users (id, email) values ('e9400000-0000-4000-8000-000000000001', 'pgtap-emb-a@example.invalid'),
                                          ('e9400000-0000-4000-8000-000000000002', 'pgtap-emb-b@example.invalid');
insert into public.trace_candidate_profiles (candidate_profile_id, owner_user_id) values
  ('e9410000-0000-4000-8000-000000000001', 'e9400000-0000-4000-8000-000000000001'),
  ('e9410000-0000-4000-8000-000000000002', 'e9400000-0000-4000-8000-000000000002');
insert into public.trace_candidate_evidence (candidate_evidence_id, owner_user_id, candidate_profile_id, evidence_type, evidence_text, review_status)
values ('e9420000-0000-4000-8000-000000000001', 'e9400000-0000-4000-8000-000000000001', 'e9410000-0000-4000-8000-000000000001', 'responsibility', 'Interviewed customers about onboarding.', 'user_confirmed'),
       ('e9420000-0000-4000-8000-000000000002', 'e9400000-0000-4000-8000-000000000002', 'e9410000-0000-4000-8000-000000000002', 'responsibility', 'Built the payments API.', 'user_confirmed');

set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "e9400000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select is((select trace_semantic.store_my_embeddings('e9410000-0000-4000-8000-000000000001', 'm', 'v',
            jsonb_build_array(jsonb_build_object('kind', 'evidence', 'id', 'e9420000-0000-4000-8000-000000000001',
              'sha', encode(sha256(convert_to('Interviewed customers about onboarding.', 'UTF8')), 'hex'),
              'embedding', to_jsonb(array_fill(0.05::real, array[384])))))), 1, 'the owner stores an embedding for their own current line');
select is((select trace_semantic.store_my_embeddings('e9410000-0000-4000-8000-000000000002', 'm', 'v',
            jsonb_build_array(jsonb_build_object('kind', 'evidence', 'id', 'e9420000-0000-4000-8000-000000000002',
              'sha', encode(sha256(convert_to('Built the payments API.', 'UTF8')), 'hex'),
              'embedding', to_jsonb(array_fill(0.05::real, array[384])))))), 0, 'another user''s line is never stored');
select is((select trace_semantic.store_my_embeddings('e9410000-0000-4000-8000-000000000001', 'm', 'v',
            jsonb_build_array(jsonb_build_object('kind', 'evidence', 'id', 'e9420000-0000-4000-8000-000000000001',
              'sha', repeat('0', 64), 'embedding', to_jsonb(array_fill(0.05::real, array[384])))))), 0, 'a stale hash is never stored');
select is((select count(*)::int from trace_semantic.match_my_evidence('e9410000-0000-4000-8000-000000000001', array_fill(0.05::real, array[384]), 'm', 'v', 10)),
          1, 'the owner matches their own line');
select set_config('request.jwt.claims', '{"sub": "e9400000-0000-4000-8000-000000000002", "role": "authenticated"}', true);
select is((select count(*)::int from trace_semantic.match_my_evidence('e9410000-0000-4000-8000-000000000001', array_fill(0.05::real, array[384]), 'm', 'v', 10)),
          0, 'another user asking with that profile id gets nothing');
reset role;
delete from public.trace_candidate_profiles where candidate_profile_id = 'e9410000-0000-4000-8000-000000000001';
select is((select count(*)::int from trace_private.candidate_embeddings where owner_user_id = 'e9400000-0000-4000-8000-000000000001'), 0,
          'deleting the profile deletes its embeddings');

select * from finish();
rollback;
