-- v2.35.0: hybrid_candidates returns only the caller's eligible records, ranks only (no similarity), current
-- categorisations only; refuses oversized query families and calls without a request identity.
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(11);

select ok(has_function_privilege('trace_app_user', 'trace_semantic.hybrid_candidates(uuid, jsonb, text, text)', 'EXECUTE')
          and not has_function_privilege('authenticated', 'trace_semantic.hybrid_candidates(uuid, jsonb, text, text)', 'EXECUTE')
          and not has_function_privilege('anon', 'trace_semantic.hybrid_candidates(uuid, jsonb, text, text)', 'EXECUTE')
          and not has_function_privilege('service_role', 'trace_semantic.hybrid_candidates(uuid, jsonb, text, text)', 'EXECUTE'),
          'only the owner role calls hybrid_candidates');
select is((select pg_get_userbyid(p.proowner)::text from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'trace_semantic' and p.proname = 'hybrid_candidates'), 'trace_embedding_writer', 'owned by the writer');
select ok(not has_column_privilege('trace_embedding_writer', 'public.trace_candidate_evidence', 'area_qualifiers', 'SELECT')
          and has_column_privilege('trace_embedding_writer', 'public.trace_candidate_evidence', 'area_classification', 'SELECT'),
          'the writer reads only the columns it needs');

insert into auth.users (id, email) values ('e9500000-0000-4000-8000-000000000001', 'pgtap-hyb-a@example.invalid'),
                                          ('e9500000-0000-4000-8000-000000000002', 'pgtap-hyb-b@example.invalid');
insert into public.trace_candidate_profiles (candidate_profile_id, owner_user_id) values
  ('e9510000-0000-4000-8000-000000000001', 'e9500000-0000-4000-8000-000000000001'),
  ('e9510000-0000-4000-8000-000000000002', 'e9500000-0000-4000-8000-000000000002');
insert into public.trace_candidate_evidence (candidate_evidence_id, owner_user_id, candidate_profile_id, evidence_type, evidence_text, review_status, evidence_status)
values ('e9520000-0000-4000-8000-000000000001', 'e9500000-0000-4000-8000-000000000001', 'e9510000-0000-4000-8000-000000000001', 'responsibility', 'Owned the pricing roadmap.', 'user_confirmed', 'participant_confirmed'),
       ('e9520000-0000-4000-8000-000000000002', 'e9500000-0000-4000-8000-000000000001', 'e9510000-0000-4000-8000-000000000001', 'responsibility', 'Wrote the roadmap newsletter.', 'pending_review', 'participant_confirmed'),
       ('e9520000-0000-4000-8000-000000000003', 'e9500000-0000-4000-8000-000000000001', 'e9510000-0000-4000-8000-000000000001', 'responsibility', 'Planned the roadmap offsite.', 'user_confirmed', 'rejected'),
       ('e9520000-0000-4000-8000-000000000004', 'e9500000-0000-4000-8000-000000000002', 'e9510000-0000-4000-8000-000000000002', 'responsibility', 'Owned the roadmap for search.', 'user_confirmed', 'participant_confirmed');
-- a categorisation made against OLDER text (stale) must not be returned
update public.trace_candidate_evidence set area_classification = array['roadmap_ownership'], area_classification_basis = 'candidate_selected',
       area_classified_text = 'an earlier wording' where candidate_evidence_id = 'e9520000-0000-4000-8000-000000000001';

set local role trace_app_user;
select set_config('request.jwt.claims', '{"sub": "e9500000-0000-4000-8000-000000000001", "role": "authenticated"}', true);
select is((select array_agg(source_id::text) from trace_semantic.hybrid_candidates('e9510000-0000-4000-8000-000000000001',
            '[{"key": "roadmap_ownership", "text": "roadmap"}]', 'm', 'v')),
          array['e9520000-0000-4000-8000-000000000001'], 'only the caller''s reviewed, non-rejected records');
select is((select user_areas from trace_semantic.hybrid_candidates('e9510000-0000-4000-8000-000000000001',
            '[{"key": "roadmap_ownership", "text": "roadmap"}]', 'm', 'v')), null::text[], 'a stale categorisation is not returned');
select is((select ranks from trace_semantic.hybrid_candidates('e9510000-0000-4000-8000-000000000001',
            '[{"key": "roadmap_ownership", "text": "roadmap"}, {"key": "x", "text": "zebra"}]', 'm', 'v')),
          '{"roadmap_ownership": {"lq": 1, "lr": 1}}'::jsonb, 'lexical ranks both ways; a non-matching query is absent');
select ok((select bool_and(not (ranks::text ~ 'simil|dist|score')) from trace_semantic.hybrid_candidates('e9510000-0000-4000-8000-000000000001',
            ('[{"key": "roadmap_ownership", "text": "roadmap", "embedding": ' || to_jsonb(array_fill(0.05::real, array[384]))::text || '}]')::jsonb, 'm', 'v')),
          'ranks only: no similarity, distance or score leaves the database');
select throws_ok($$select * from trace_semantic.hybrid_candidates('e9510000-0000-4000-8000-000000000001',
                   (select jsonb_agg(jsonb_build_object('key', g::text, 'text', 'x')) from generate_series(1, 65) g), 'm', 'v')$$,
                 '22023', null, 'a query family larger than 64 is refused');

select set_config('request.jwt.claims', '{"sub": "e9500000-0000-4000-8000-000000000002", "role": "authenticated"}', true);
select is((select count(*)::int from trace_semantic.hybrid_candidates('e9510000-0000-4000-8000-000000000001',
            '[{"key": "roadmap_ownership", "text": "roadmap"}]', 'm', 'v')), 0, 'another user asking with that profile id gets nothing');
select is((select count(*)::int from trace_semantic.hybrid_candidates('e9510000-0000-4000-8000-000000000002',
            '[{"key": "roadmap_ownership", "text": "roadmap"}]', 'm', 'v')), 1, 'the second user sees their own line');

select set_config('request.jwt.claims', '', true);
select throws_ok($$select * from trace_semantic.hybrid_candidates('e9510000-0000-4000-8000-000000000001', '[]', 'm', 'v')$$,
                 '42501', null, 'no request identity is refused');
reset role;

select * from finish();
rollback;
