-- v2.13.0: the caller's own contributed reference profile ids (owner role only). Rolled back.
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(6);

select ok(has_function_privilege('trace_app_user', 'trace_contrib.my_reference_profile_ids()', 'EXECUTE')
          and not has_function_privilege('authenticated', 'trace_contrib.my_reference_profile_ids()', 'EXECUTE')
          and not has_function_privilege('anon', 'trace_contrib.my_reference_profile_ids()', 'EXECUTE')
          and not has_function_privilege('trace_app_runtime', 'trace_contrib.my_reference_profile_ids()', 'EXECUTE')
          and not has_function_privilege('service_role', 'trace_contrib.my_reference_profile_ids()', 'EXECUTE'),
          'only the owner role may ask for its own reference profile ids');

insert into auth.users (id, email) values ('00000000-0000-4000-8000-0000000000b1', 'pgtap-b1@example.invalid'),
                                          ('00000000-0000-4000-8000-0000000000b2', 'pgtap-b2@example.invalid');
insert into trace_private.contributors (contributor_id, source_kind, user_id)
values ('00000000-0000-4000-8000-0000000000c1', 'app_user', '00000000-0000-4000-8000-0000000000b1'),
       ('00000000-0000-4000-8000-0000000000c2', 'app_user', '00000000-0000-4000-8000-0000000000b2');
-- The profile foreign key is deferred to commit; this test always rolls back.
insert into trace_private.contributions (contributor_id, profile_id, status, deidentification_status, pipeline_version,
                                         admitted_at, withdrawn_at)
values ('00000000-0000-4000-8000-0000000000c1', 'CRR-000000000901', 'admitted', 'passed', 'pgtap', now(), null),
       ('00000000-0000-4000-8000-0000000000c1', null, 'withdrawn', 'passed', 'pgtap', null, now()),
       ('00000000-0000-4000-8000-0000000000c2', 'CRR-000000000902', 'admitted', 'passed', 'pgtap', now(), null);

select set_config('request.jwt.claim.sub', '00000000-0000-4000-8000-0000000000b1', true);
select set_config('request.jwt.claims', '{"sub": "00000000-0000-4000-8000-0000000000b1", "role": "authenticated"}', true);
select is(array(select trace_contrib.my_reference_profile_ids()), array['CRR-000000000901'],
          'the caller gets exactly their own profile id');
select ok(not ('CRR-000000000902' = any(array(select trace_contrib.my_reference_profile_ids()))),
          'never another user''s profile id');
select is((select count(*)::int from trace_contrib.my_reference_profile_ids() where my_reference_profile_ids is null), 0,
          'a withdrawn contribution (no profile) returns nothing');

select set_config('request.jwt.claim.sub', '00000000-0000-4000-8000-0000000000b2', true);
select set_config('request.jwt.claims', '{"sub": "00000000-0000-4000-8000-0000000000b2", "role": "authenticated"}', true);
select is(array(select trace_contrib.my_reference_profile_ids()), array['CRR-000000000902'], 'the other user sees only theirs');

select set_config('request.jwt.claim.sub', '', true);
select set_config('request.jwt.claims', '{}', true);
select is((select count(*)::int from trace_contrib.my_reference_profile_ids()), 0, 'no identity, nothing');

select * from finish();
rollback;
