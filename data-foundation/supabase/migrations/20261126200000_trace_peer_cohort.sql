-- Trace data foundation v2.39.0: peer career-path patterns (demo).
--
-- The Career Origins cohort is not a consented contribution and not a reference profile. It lives in its own schema.
-- The table holds de-identified path attributes and a local gte-small embedding. No name, employer, or contact.
--
-- trace_peer_cohort.peer_patterns returns one JSON aggregate for the closest profiles. It does not return a profile
-- row, a surrogate id, the path text, or the vector. Counts are for the app to band before anyone sees them.
-- SECURITY DEFINER, empty search_path. EXECUTE is trace_app_runtime only. RLS is on and no client role has a policy.

create schema trace_peer_cohort;
revoke all on schema trace_peer_cohort from public, anon, authenticated, service_role;

create table trace_peer_cohort.profiles (
  profile_key text primary key,
  cohort_version text not null,
  taxonomy_version text not null default '',
  norm_seniority_group text not null,
  norm_city text not null default '',
  final_background_status text not null default '',
  final_primary_prior_function text not null default '',
  final_prior_functions text not null default '',
  final_multi_function boolean not null default false,
  final_prev_role_function text not null default '',
  final_entry_move text not null default '',
  final_first_pm_group text not null default '',
  final_first_pm_dated boolean not null default false,
  final_months_to_first_pm_from_earliest_nonintern integer,
  final_has_history boolean not null default false,
  final_dated_role_before_first_pm boolean not null default false,
  final_n_pre_pm_roles integer not null default 0,
  final_n_roles integer not null default 0,
  final_n_pm_roles integer not null default 0,
  norm_edu_has_degree boolean not null default false,
  norm_career_start_year integer,
  has_mba boolean not null default false,
  path_text text not null,
  embedding extensions.vector(384) not null,
  check (norm_seniority_group in (
    'Junior PM', 'APM', 'Product Manager', 'Senior Product Manager', 'Lead',
    'Principal/Staff/Group', 'Head', 'Director', 'VP/CPO'))
);

create index peer_profiles_version on trace_peer_cohort.profiles (cohort_version);
create index peer_profiles_embedding_hnsw on trace_peer_cohort.profiles
  using hnsw (embedding extensions.vector_cosine_ops);

alter table trace_peer_cohort.profiles enable row level security;
revoke all on table trace_peer_cohort.profiles from public, anon, authenticated, service_role, trace_app_user;

create function trace_peer_cohort.peer_patterns(
  p_query extensions.vector(384),
  p_target_level text,
  p_near_levels text[],
  p_k integer,
  p_cohort_version text
) returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  with scored as materialized (
    select p.norm_seniority_group, p.final_entry_move, p.final_primary_prior_function,
           p.final_months_to_first_pm_from_earliest_nonintern, p.final_n_pre_pm_roles,
           p.has_mba, p.norm_edu_has_degree, p.final_first_pm_group,
           (1 - (p.embedding operator(extensions.<=>) p_query))
             + case
                 when p.norm_seniority_group = p_target_level then 0.15
                 when p.norm_seniority_group = any (p_near_levels) then 0.08
                 else 0
               end as score
      from trace_peer_cohort.profiles p
     where p.cohort_version = p_cohort_version
     order by score desc, p.profile_key
     limit least(greatest(coalesce(p_k, 40), 1), 40)
  )
  select jsonb_build_object(
    'neighbour_count', (select count(*) from scored),
    'internal', (select count(*) from scored where final_entry_move = 'internal'),
    'external', (select count(*) from scored where final_entry_move = 'external'),
    'mba', (select count(*) from scored where has_mba),
    'degree', (select count(*) from scored where norm_edu_has_degree),
    'at_target', (select count(*) from scored where norm_seniority_group = p_target_level),
    'pre_pm_roles_median', (select percentile_cont(0.5) within group (order by final_n_pre_pm_roles) from scored),
    'months', (
      select jsonb_build_object(
        'n', count(final_months_to_first_pm_from_earliest_nonintern),
        'median', percentile_cont(0.5) within group (order by final_months_to_first_pm_from_earliest_nonintern),
        'low', percentile_cont(0.25) within group (order by final_months_to_first_pm_from_earliest_nonintern),
        'high', percentile_cont(0.75) within group (order by final_months_to_first_pm_from_earliest_nonintern))
        from scored),
    'priors', (
      select coalesce(jsonb_agg(jsonb_build_object('label', fn, 'count', n) order by n desc, fn), '[]'::jsonb)
        from (
          select final_primary_prior_function as fn, count(*) as n
            from scored
           where final_primary_prior_function <> ''
           group by final_primary_prior_function
           order by n desc, fn
           limit 3) s),
    'first_pm', (
      select jsonb_build_object('label', fn, 'count', n)
        from (
          select final_first_pm_group as fn, count(*) as n
            from scored
           where final_first_pm_group <> ''
           group by final_first_pm_group
           order by n desc, fn
           limit 1) s)
  );
$$;

revoke all on function trace_peer_cohort.peer_patterns(extensions.vector, text, text[], integer, text)
  from public, anon, authenticated, service_role, trace_app_user;
grant usage on schema trace_peer_cohort to trace_app_runtime;
grant execute on function trace_peer_cohort.peer_patterns(extensions.vector, text, text[], integer, text) to trace_app_runtime;

do $$
declare
  v_def text := pg_get_functiondef('trace_peer_cohort.peer_patterns(extensions.vector,text,text[],integer,text)'::regprocedure);
begin
  if not has_function_privilege('trace_app_runtime', 'trace_peer_cohort.peer_patterns(extensions.vector,text,text[],integer,text)', 'EXECUTE')
     or has_function_privilege('anon', 'trace_peer_cohort.peer_patterns(extensions.vector,text,text[],integer,text)', 'EXECUTE')
     or has_function_privilege('authenticated', 'trace_peer_cohort.peer_patterns(extensions.vector,text,text[],integer,text)', 'EXECUTE')
     or has_function_privilege('trace_app_user', 'trace_peer_cohort.peer_patterns(extensions.vector,text,text[],integer,text)', 'EXECUTE') then
    raise exception 'peer_patterns is callable by the runtime role only';
  end if;
  if has_schema_privilege('anon', 'trace_peer_cohort', 'USAGE')
     or has_schema_privilege('authenticated', 'trace_peer_cohort', 'USAGE')
     or has_table_privilege('anon', 'trace_peer_cohort.profiles', 'SELECT')
     or has_table_privilege('authenticated', 'trace_peer_cohort.profiles', 'SELECT')
     or has_table_privilege('trace_app_user', 'trace_peer_cohort.profiles', 'SELECT') then
    raise exception 'clients cannot read the peer cohort';
  end if;
  if position('jsonb_build_object' in v_def) = 0 or position('profile_key' in split_part(v_def, 'jsonb_build_object', 2)) > 0 then
    raise exception 'peer_patterns must return counts only';
  end if;
end;
$$;
