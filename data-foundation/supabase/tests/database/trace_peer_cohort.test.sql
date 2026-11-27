-- v2.39.0: the peer cohort is not readable by clients, and the function returns one aggregate.
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(4);

select ok(has_function_privilege('trace_app_runtime', 'trace_peer_cohort.peer_patterns(extensions.vector,text,text[],integer,text)', 'EXECUTE')
          and not has_function_privilege('anon', 'trace_peer_cohort.peer_patterns(extensions.vector,text,text[],integer,text)', 'EXECUTE')
          and not has_function_privilege('authenticated', 'trace_peer_cohort.peer_patterns(extensions.vector,text,text[],integer,text)', 'EXECUTE')
          and not has_function_privilege('trace_app_user', 'trace_peer_cohort.peer_patterns(extensions.vector,text,text[],integer,text)', 'EXECUTE'),
          'only the runtime role can ask for peer patterns');

select ok(not has_table_privilege('anon', 'trace_peer_cohort.profiles', 'SELECT')
          and not has_table_privilege('authenticated', 'trace_peer_cohort.profiles', 'SELECT')
          and not has_table_privilege('trace_app_user', 'trace_peer_cohort.profiles', 'SELECT')
          and not has_schema_privilege('anon', 'trace_peer_cohort', 'USAGE')
          and not has_schema_privilege('authenticated', 'trace_peer_cohort', 'USAGE'),
          'clients cannot read the peer cohort');

select ok((select p.prosecdef and pg_get_function_result(p.oid) = 'jsonb'
            from pg_proc p join pg_namespace n on n.oid = p.pronamespace
           where n.nspname = 'trace_peer_cohort' and p.proname = 'peer_patterns'),
          'the function is security definer and returns one jsonb aggregate');

select ok(position('profile_key' in split_part(pg_get_functiondef('trace_peer_cohort.peer_patterns(extensions.vector,text,text[],integer,text)'::regprocedure),
                    'jsonb_build_object', 2)) = 0,
          'the aggregate document does not contain a profile key');

select * from finish();
rollback;
