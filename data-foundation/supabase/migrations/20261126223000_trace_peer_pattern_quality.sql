-- Trace data foundation v2.40.0: peer patterns use a small level nudge and known values only.
--
-- v2.39.0 added 0.15 for the same level. The path sentences are a template, so cosine already sits near 0.9 and that
-- bonus behaved like a level filter. Same level is now 0.03 and an adjacent level is 0.02.
-- A blank degree or a pre-PM count with no history is unknown, not false and not zero.
-- The function still returns one jsonb of counts: the neighbour group and the whole cohort. No profile row.

alter table trace_peer_cohort.profiles alter column norm_edu_has_degree drop not null;
alter table trace_peer_cohort.profiles alter column norm_edu_has_degree drop default;
alter table trace_peer_cohort.profiles alter column final_n_pre_pm_roles drop not null;
alter table trace_peer_cohort.profiles alter column final_n_pre_pm_roles drop default;

create or replace function trace_peer_cohort.peer_patterns(
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
    select p.profile_key, p.final_entry_move, p.final_primary_prior_function, p.final_first_pm_group,
           p.final_months_to_first_pm_from_earliest_nonintern, p.final_n_pre_pm_roles,
           p.final_has_history, p.has_mba, p.norm_edu_has_degree,
           (1 - (p.embedding operator(extensions.<=>) p_query))
             + case
                 when p.norm_seniority_group = p_target_level then 0.03
                 when p.norm_seniority_group = any (p_near_levels) then 0.02
                 else 0
               end as score
      from trace_peer_cohort.profiles p
     where p.cohort_version = p_cohort_version
     order by score desc, p.profile_key
     limit least(greatest(coalesce(p_k, 40), 1), 40)
  ),
  everyone as (
    select p.final_entry_move, p.final_primary_prior_function, p.final_first_pm_group,
           p.final_months_to_first_pm_from_earliest_nonintern, p.final_n_pre_pm_roles,
           p.final_has_history, p.has_mba, p.norm_edu_has_degree
      from trace_peer_cohort.profiles p
     where p.cohort_version = p_cohort_version
  )
  select jsonb_build_object(
    'neighbour_count', (select count(*) from scored),
    'group', (
      select jsonb_build_object(
        'n', count(*),
        'internal', count(*) filter (where final_entry_move = 'internal'),
        'external', count(*) filter (where final_entry_move = 'external'),
        'move_known', count(*) filter (where final_entry_move in ('internal', 'external')),
        'mba', count(*) filter (where has_mba),
        'mba_known', count(*),
        'degree', count(*) filter (where norm_edu_has_degree is true),
        'degree_known', count(*) filter (where norm_edu_has_degree is not null),
        'prior_known', count(*) filter (where final_primary_prior_function <> ''),
        'first_known', count(*) filter (where final_first_pm_group <> ''),
        'pre_n', count(*) filter (where final_has_history and final_n_pre_pm_roles is not null),
        'pre_median', percentile_cont(0.5) within group (order by final_n_pre_pm_roles)
                      filter (where final_has_history and final_n_pre_pm_roles is not null),
        'months', jsonb_build_object(
          'n', count(final_months_to_first_pm_from_earliest_nonintern),
          'median', percentile_cont(0.5) within group (order by final_months_to_first_pm_from_earliest_nonintern),
          'low', percentile_cont(0.25) within group (order by final_months_to_first_pm_from_earliest_nonintern),
          'high', percentile_cont(0.75) within group (order by final_months_to_first_pm_from_earliest_nonintern)),
        'priors', (
          select coalesce(jsonb_object_agg(fn, n), '{}'::jsonb) from (
            select final_primary_prior_function as fn, count(*) as n
              from scored where final_primary_prior_function <> '' group by final_primary_prior_function) q),
        'first', (
          select coalesce(jsonb_object_agg(fn, n), '{}'::jsonb) from (
            select final_first_pm_group as fn, count(*) as n
              from scored where final_first_pm_group <> '' group by final_first_pm_group) q))
        from scored),
    'cohort', (
      select jsonb_build_object(
        'n', count(*),
        'internal', count(*) filter (where final_entry_move = 'internal'),
        'external', count(*) filter (where final_entry_move = 'external'),
        'move_known', count(*) filter (where final_entry_move in ('internal', 'external')),
        'mba', count(*) filter (where has_mba),
        'mba_known', count(*),
        'degree', count(*) filter (where norm_edu_has_degree is true),
        'degree_known', count(*) filter (where norm_edu_has_degree is not null),
        'prior_known', count(*) filter (where final_primary_prior_function <> ''),
        'first_known', count(*) filter (where final_first_pm_group <> ''),
        'pre_n', count(*) filter (where final_has_history and final_n_pre_pm_roles is not null),
        'pre_median', percentile_cont(0.5) within group (order by final_n_pre_pm_roles)
                      filter (where final_has_history and final_n_pre_pm_roles is not null),
        'months', jsonb_build_object(
          'n', count(final_months_to_first_pm_from_earliest_nonintern),
          'median', percentile_cont(0.5) within group (order by final_months_to_first_pm_from_earliest_nonintern),
          'low', percentile_cont(0.25) within group (order by final_months_to_first_pm_from_earliest_nonintern),
          'high', percentile_cont(0.75) within group (order by final_months_to_first_pm_from_earliest_nonintern)),
        'priors', (
          select coalesce(jsonb_object_agg(fn, n), '{}'::jsonb) from (
            select final_primary_prior_function as fn, count(*) as n
              from everyone where final_primary_prior_function <> '' group by final_primary_prior_function) q),
        'first', (
          select coalesce(jsonb_object_agg(fn, n), '{}'::jsonb) from (
            select final_first_pm_group as fn, count(*) as n
              from everyone where final_first_pm_group <> '' group by final_first_pm_group) q))
        from everyone)
  );
$$;

revoke all on function trace_peer_cohort.peer_patterns(extensions.vector, text, text[], integer, text)
  from public, anon, authenticated, service_role, trace_app_user;
grant execute on function trace_peer_cohort.peer_patterns(extensions.vector, text, text[], integer, text) to trace_app_runtime;

do $$
declare
  v_def text := pg_get_functiondef('trace_peer_cohort.peer_patterns(extensions.vector,text,text[],integer,text)'::regprocedure);
begin
  if position('0.03' in v_def) = 0 or position('0.15' in v_def) > 0 then
    raise exception 'the level nudge must stay small';
  end if;
  if position('profile_key' in split_part(v_def, 'jsonb_build_object', 2)) > 0 or position('path_text' in v_def) > 0 then
    raise exception 'peer_patterns must return counts only';
  end if;
end;
$$;
