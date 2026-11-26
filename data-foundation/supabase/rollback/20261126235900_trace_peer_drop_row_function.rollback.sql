-- Rollback v2.43.0. Restores the five-argument function so an older app can still call it.

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
    select p.norm_seniority_group, p.final_primary_prior_function, p.final_entry_move, p.final_first_pm_group,
           p.final_n_pre_pm_roles, p.final_n_pm_roles, p.final_has_history, p.has_mba, p.norm_career_start_year,
           p.final_months_to_first_pm_from_earliest_nonintern,
           (1 - (p.embedding operator(extensions.<=>) p_query)) as cosine
      from trace_peer_cohort.profiles p
     where p.cohort_version = p_cohort_version
     order by p.embedding operator(extensions.<=>) p_query
     limit least(greatest(coalesce(p_k, 120), 1), 120)
  ),
  everyone as (
    select p.norm_seniority_group, p.final_primary_prior_function, p.final_entry_move, p.final_first_pm_group,
           p.final_n_pre_pm_roles, p.final_n_pm_roles, p.final_has_history, p.has_mba,
           p.final_months_to_first_pm_from_earliest_nonintern
      from trace_peer_cohort.profiles p
     where p.cohort_version = p_cohort_version
  )
  select jsonb_build_object(
    'candidates', (
      select coalesce(jsonb_agg(jsonb_build_object(
        'level', norm_seniority_group,
        'prior', final_primary_prior_function,
        'move', final_entry_move,
        'first', final_first_pm_group,
        'n_pre', case when final_has_history then final_n_pre_pm_roles else null end,
        'n_pm', final_n_pm_roles,
        'mba', has_mba,
        'start_year', norm_career_start_year,
        'has_history', final_has_history,
        'months', final_months_to_first_pm_from_earliest_nonintern,
        'cosine', cosine
      ) order by cosine desc), '[]'::jsonb)
      from scored),
    'backgrounds', (
      select coalesce(jsonb_object_agg(fn, n), '{}'::jsonb) from (
        select final_primary_prior_function as fn, count(*) as n
          from everyone where final_primary_prior_function <> '' group by final_primary_prior_function) q),
    'by_first', (
      select coalesce(jsonb_object_agg(fn, body), '{}'::jsonb) from (
        select final_first_pm_group as fn,
               jsonb_build_object(
                 'n', count(*),
                 'pm_roles_n', count(*) filter (where final_n_pm_roles > 0),
                 'pm_roles_median', percentile_cont(0.5) within group (order by final_n_pm_roles)
                                     filter (where final_n_pm_roles > 0),
                 'reached', jsonb_build_object(
                   'Junior PM', count(*) filter (where norm_seniority_group in ('Junior PM','APM','Product Manager','Senior Product Manager','Lead','Principal/Staff/Group','Head','Director','VP/CPO')),
                   'APM', count(*) filter (where norm_seniority_group in ('APM','Product Manager','Senior Product Manager','Lead','Principal/Staff/Group','Head','Director','VP/CPO')),
                   'Product Manager', count(*) filter (where norm_seniority_group in ('Product Manager','Senior Product Manager','Lead','Principal/Staff/Group','Head','Director','VP/CPO')),
                   'Senior Product Manager', count(*) filter (where norm_seniority_group in ('Senior Product Manager','Lead','Principal/Staff/Group','Head','Director','VP/CPO')),
                   'Lead', count(*) filter (where norm_seniority_group in ('Lead','Principal/Staff/Group','Head','Director','VP/CPO')),
                   'Principal/Staff/Group', count(*) filter (where norm_seniority_group in ('Principal/Staff/Group','Head','Director','VP/CPO')),
                   'Head', count(*) filter (where norm_seniority_group in ('Head','Director','VP/CPO')),
                   'Director', count(*) filter (where norm_seniority_group in ('Director','VP/CPO')),
                   'VP/CPO', count(*) filter (where norm_seniority_group = 'VP/CPO'))
               ) as body
          from everyone
         where final_first_pm_group <> ''
         group by final_first_pm_group) s),
    'cohort', (
      select jsonb_build_object(
        'n', count(*),
        'internal', count(*) filter (where final_entry_move = 'internal'),
        'external', count(*) filter (where final_entry_move = 'external'),
        'move_known', count(*) filter (where final_entry_move in ('internal', 'external')),
        'mba', count(*) filter (where has_mba),
        'mba_known', count(*),
        'prior_known', count(*) filter (where final_primary_prior_function <> ''),
        'first_known', count(*) filter (where final_first_pm_group <> ''),
        'pre_n', count(*) filter (where final_has_history and final_n_pre_pm_roles > 0),
        'pre_median', percentile_cont(0.5) within group (order by final_n_pre_pm_roles)
                      filter (where final_has_history and final_n_pre_pm_roles > 0),
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
  if position('limit least' in v_def) = 0 or position('120' in v_def) = 0 then
    raise exception 'peer search must keep a bounded embedding pool';
  end if;
  if position('profile_key' in v_def) > 0 or position('path_text' in v_def) > 0 or position('0.15' in v_def) > 0 then
    raise exception 'peer_patterns must not return a profile or apply a large level bonus';
  end if;
end;
$$;
