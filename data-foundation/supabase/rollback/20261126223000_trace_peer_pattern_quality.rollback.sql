-- Rollback v2.40.0. Restores the v2.39.0 function and the not-null columns.
-- Unknown degrees and pre-PM counts are filled so the not-null constraints can return. Those filled values are the
-- v2.39.0 defaults, not a claim that the field was observed.

update trace_peer_cohort.profiles set norm_edu_has_degree = false where norm_edu_has_degree is null;
update trace_peer_cohort.profiles set final_n_pre_pm_roles = 0 where final_n_pre_pm_roles is null;
alter table trace_peer_cohort.profiles alter column norm_edu_has_degree set default false;
alter table trace_peer_cohort.profiles alter column norm_edu_has_degree set not null;
alter table trace_peer_cohort.profiles alter column final_n_pre_pm_roles set default 0;
alter table trace_peer_cohort.profiles alter column final_n_pre_pm_roles set not null;

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
