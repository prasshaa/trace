-- Rollback v2.41.0. Restores the v2.40.0 function (small level nudge, aggregate counts, no candidate pool).

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
