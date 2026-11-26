-- Trace data foundation v2.42.0: peer_patterns returns aggregate counts only.
--
-- v2.41 handed the app one attribute row per neighbour. That is withdrawn. The rerank and the group counts now
-- happen inside this function. HNSW's default ef_search is 40, so a limit of 120 was returning about 40 rows.
-- Index scans are turned off for this call so the distance ordering is exact across the whole cohort (439 rows).
-- Counts under 5 are omitted. Counts from 5 to 9 are a band. Nothing returned is a profile.

drop function if exists trace_peer_cohort.peer_patterns(extensions.vector, text, text[], integer, text);

create function trace_peer_cohort.peer_patterns(
  p_query extensions.vector(384),
  p_user jsonb,
  p_cohort_version text
) returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_levels text[] := array['Junior PM','APM','Product Manager','Senior Product Manager','Lead','Principal/Staff/Group','Head','Director','VP/CPO'];
  v_prior text := coalesce(p_user->>'prior', '');
  v_move text := coalesce(p_user->>'move', '');
  v_n_pre integer := nullif(p_user->>'n_pre', '')::integer;
  v_mba boolean := case when jsonb_typeof(p_user->'mba') = 'boolean' then (p_user->>'mba')::boolean else null end;
  v_year integer := nullif(p_user->>'start_year', '')::integer;
  v_in_pm boolean := coalesce((p_user->>'in_pm')::boolean, false);
  v_first text := coalesce(p_user->>'first_pm_level', '');
  v_target text := coalesce(p_user->>'target_level', '');
  v_n integer := 0;
  v_cohort integer := 0;
  v_internal integer := 0;
  v_external integer := 0;
  v_move_known integer := 0;
  v_c_internal integer := 0;
  v_c_external integer := 0;
  v_c_move integer := 0;
  v_patterns jsonb := '[]'::jsonb;
  v_takeaways jsonb := '[]'::jsonb;
  v_preface text := '';
  v_text text;
  v_rate numeric;
  v_crate numeric;
  v_pct integer;
  v_label text;
  v_count integer;
  v_known integer;
  v_ccount integer;
  v_cknown integer;
  v_reached integer;
  v_roles_n integer;
  v_roles_med numeric;
  v_bg integer;
begin
  -- Exact distance over a few hundred rows. The HNSW default stops around 40 neighbours.
  perform set_config('enable_indexscan', 'off', true);
  perform set_config('enable_indexonlyscan', 'off', true);
  perform set_config('hnsw.ef_search', '200', true);
  perform set_config('hnsw.iterative_scan', 'relaxed_order', true);

  drop table if exists pg_temp.peer_pool;
  drop table if exists pg_temp.peer_neighbours;
  create temporary table pg_temp.peer_pool on commit drop as
  select p.norm_seniority_group as level,
         p.final_primary_prior_function as prior,
         p.final_entry_move as move,
         p.final_first_pm_group as first_pm,
         case when p.final_has_history then p.final_n_pre_pm_roles end as n_pre,
         p.final_n_pm_roles as n_pm,
         p.has_mba as mba,
         p.norm_career_start_year as start_year,
         p.final_months_to_first_pm_from_earliest_nonintern as months,
         (1 - (p.embedding operator(extensions.<=>) p_query)) as cosine
    from trace_peer_cohort.profiles p
   where p.cohort_version = p_cohort_version
   order by (p.embedding operator(extensions.<=>) p_query)
   limit 120;

  create temporary table pg_temp.peer_neighbours on commit drop as
  select *
    from (
      select pool.*,
             pool.cosine
             + case when v_prior <> '' and pool.prior = v_prior then 0.20 else 0 end
             + case when v_move <> '' and pool.move = v_move then 0.08 else 0 end
             + case
                 when pg_catalog.array_position(v_levels, pool.level) is not null
                  and pg_catalog.array_position(v_levels, v_target) is not null
                 then 0.12 * pg_catalog.greatest(0, 1 - pg_catalog.abs(pg_catalog.array_position(v_levels, pool.level)
                          - pg_catalog.array_position(v_levels, v_target)) / 4.0)
                 else 0
               end
             + case
                 when v_n_pre is not null and pool.n_pre is not null
                  and (case when v_n_pre <= 0 then 0 when v_n_pre = 1 then 1 when v_n_pre <= 3 then 2 else 3 end)
                    = (case when pool.n_pre <= 0 then 0 when pool.n_pre = 1 then 1 when pool.n_pre <= 3 then 2 else 3 end)
                 then 0.06 else 0
               end
             + case when v_mba is not null and pool.mba is not null and v_mba = pool.mba then 0.04 else 0 end
             + case when v_year is not null and pool.start_year is not null
                    then 0.06 * pg_catalog.greatest(0, 1 - pg_catalog.abs(v_year - pool.start_year) / 15.0)
                    else 0 end as score
        from pg_temp.peer_pool pool
    ) scored
   order by score desc, cosine desc
   limit 40;

  select count(*) into v_n from pg_temp.peer_neighbours;
  select count(*) into v_cohort from trace_peer_cohort.profiles where cohort_version = p_cohort_version;
  if v_n < 5 then
    return jsonb_build_object('available', false, 'reason', 'too_few');
  end if;

  select count(*) filter (where move = 'internal'),
         count(*) filter (where move = 'external'),
         count(*) filter (where move in ('internal', 'external'))
    into v_internal, v_external, v_move_known
    from pg_temp.peer_neighbours;
  select count(*) filter (where final_entry_move = 'internal'),
         count(*) filter (where final_entry_move = 'external'),
         count(*) filter (where final_entry_move in ('internal', 'external'))
    into v_c_internal, v_c_external, v_c_move
    from trace_peer_cohort.profiles
   where cohort_version = p_cohort_version;

  if v_prior <> '' then
    select count(*) into v_bg
      from trace_peer_cohort.profiles
     where cohort_version = p_cohort_version and final_primary_prior_function = v_prior;
    if v_bg < 5 then
      v_preface := 'Few PMs in our data came from ' ||
        case v_prior
          when 'Design / UX' then 'design'
          when 'Software Engineering / QA / IT' then 'software engineering'
          when 'Business / Consulting / Finance / Operations' then 'business or consulting'
          when 'Data / Analytics / Research' then 'data or analytics'
          when 'Sales / Marketing / Customer Success' then 'sales or marketing'
          when 'Entrepreneurship / Founders' then 'founding'
          else lower(v_prior)
        end || '; here is what PMs with similar experience did.';
    end if;
  end if;

  -- Shares that clear the lift bar. The comparison is appended without a second period.
  if v_move_known >= 5 and v_internal >= 5 and v_c_move > 0 and v_c_internal > 0
     and (v_internal::numeric / v_move_known) / (v_c_internal::numeric / v_c_move) >= 1.2 then
    v_pct := round(((v_c_internal::numeric / v_c_move) * 100) / 5.0) * 5;
    v_text := case
      when v_internal < 10 and v_move_known < 20 and v_internal::numeric / v_move_known >= 0.5
        then 'Most of the ' || v_move_known || ' closest PMs'
      when v_internal < 10 and v_move_known < 20 then null
      when v_internal < 10 then '5–9 of the ' || v_move_known || ' closest PMs'
      else v_internal::text || ' of the ' || v_move_known || ' closest PMs'
    end;
    if v_text is not null then
      v_patterns := v_patterns || jsonb_build_array(jsonb_build_object(
        'id', 'PEER-' || (jsonb_array_length(v_patterns) + 1),
        'text', v_text || ' with a recorded move moved into a product role from inside the employer, compared with about '
                || v_pct || '% of all ' || v_cohort || ' PMs.',
        'kind', 'internal'));
    end if;
  end if;

  if v_move_known >= 5 and v_external >= 5 and v_c_move > 0 and v_c_external > 0
     and (v_external::numeric / v_move_known) / (v_c_external::numeric / v_c_move) >= 1.2 then
    v_pct := round(((v_c_external::numeric / v_c_move) * 100) / 5.0) * 5;
    v_text := case
      when v_external < 10 and v_move_known < 20 and v_external::numeric / v_move_known >= 0.5
        then 'Most of the ' || v_move_known || ' closest PMs'
      when v_external < 10 and v_move_known < 20 then null
      when v_external < 10 then '5–9 of the ' || v_move_known || ' closest PMs'
      else v_external::text || ' of the ' || v_move_known || ' closest PMs'
    end;
    if v_text is not null then
      v_patterns := v_patterns || jsonb_build_array(jsonb_build_object(
        'id', 'PEER-' || (jsonb_array_length(v_patterns) + 1),
        'text', v_text || ' with a recorded move moved into a product role from outside the employer, compared with about '
                || v_pct || '% of all ' || v_cohort || ' PMs.',
        'kind', 'external'));
    end if;
  end if;

  for v_label, v_count in
    select prior, count(*)::integer from pg_temp.peer_neighbours
     where prior <> '' group by prior order by count(*) desc, prior
  loop
    exit when jsonb_array_length(v_patterns) >= 8;
    select count(*) into v_known from pg_temp.peer_neighbours where prior <> '';
    select count(*) into v_ccount from trace_peer_cohort.profiles
     where cohort_version = p_cohort_version and final_primary_prior_function = v_label;
    select count(*) into v_cknown from trace_peer_cohort.profiles
     where cohort_version = p_cohort_version and final_primary_prior_function <> '';
    if v_known >= 5 and v_count >= 5 and v_cknown > 0
       and (v_ccount = 0 or (v_count::numeric / v_known) / (v_ccount::numeric / v_cknown) >= 1.2) then
      v_pct := round(((v_ccount::numeric / v_cknown) * 100) / 5.0) * 5;
      v_text := case
        when v_count < 10 and v_known < 20 and v_count::numeric / v_known >= 0.5 then 'Most of the ' || v_known || ' closest PMs'
        when v_count < 10 and v_known < 20 then null
        when v_count < 10 then '5–9 of the ' || v_known || ' closest PMs'
        else v_count::text || ' of the ' || v_known || ' closest PMs'
      end;
      if v_text is not null then
        v_patterns := v_patterns || jsonb_build_array(jsonb_build_object(
          'id', 'PEER-' || (jsonb_array_length(v_patterns) + 1),
          'text', v_text || ' with a recorded background came from ' || v_label
                  || ' before a product role, compared with about ' || v_pct || '% of all ' || v_cohort || ' PMs.',
          'kind', 'prior'));
      end if;
    end if;
  end loop;

  if v_in_pm and v_first <> '' and v_target <> ''
     and pg_catalog.array_position(v_levels, v_first) is not null
     and pg_catalog.array_position(v_levels, v_target) is not null then
    select count(*),
           count(*) filter (where pg_catalog.array_position(v_levels, norm_seniority_group) >= pg_catalog.array_position(v_levels, v_target)),
           count(*) filter (where final_n_pm_roles > 0),
           percentile_cont(0.5) within group (order by final_n_pm_roles) filter (where final_n_pm_roles > 0)
      into v_known, v_reached, v_roles_n, v_roles_med
      from trace_peer_cohort.profiles
     where cohort_version = p_cohort_version and final_first_pm_group = v_first;
    if v_known >= 5 and v_reached >= 5 then
      v_text := case
        when v_reached < 10 and v_known < 20 and v_reached::numeric / v_known >= 0.5 then 'Most of the ' || v_known
        when v_reached < 10 and v_known < 20 then null
        when v_reached < 10 then '5–9 of the ' || v_known
        else v_reached::text || ' of the ' || v_known
      end;
      if v_text is not null then
        v_patterns := v_patterns || jsonb_build_array(jsonb_build_object(
          'id', 'PEER-' || (jsonb_array_length(v_patterns) + 1),
          'text', v_text || ' cohort PMs who started as ' || v_first || ' are now ' || v_target || ' or above.',
          'kind', 'progression'));
      end if;
      if v_roles_n >= 10 and v_roles_med is not null and round(v_roles_med) > 0 then
        v_patterns := v_patterns || jsonb_build_array(jsonb_build_object(
          'id', 'PEER-' || (jsonb_array_length(v_patterns) + 1),
          'text', 'The middle number of product roles along that path was about ' || round(v_roles_med)::integer || '.',
          'kind', 'progression'));
      end if;
    end if;
  end if;

  if v_internal > v_external and v_move_known > 0 and v_c_move > 0
     and (v_internal::numeric / v_move_known) >= 0.30
     and (v_internal::numeric / v_move_known) > (v_c_internal::numeric / v_c_move)
     and exists (
       select 1 from jsonb_array_elements(v_patterns) e where e->>'kind' = 'internal'
     ) then
    v_takeaways := v_takeaways || jsonb_build_array(
      'A common step in this group was moving into product inside the same employer.');
  end if;

  return jsonb_build_object(
    'available', jsonb_array_length(v_patterns) > 0 or v_preface <> '',
    'neighbour_count', v_n,
    'headline', 'PMs with paths like yours',
    'match_line', 'Matched by similarity of career path.',
    'preface', v_preface,
    'disclaimer', 'These are patterns across the closest profiles, not a prediction and not a guarantee.',
    'patterns', (select coalesce(jsonb_agg(jsonb_build_object('id', e->>'id', 'text', e->>'text')), '[]'::jsonb)
                   from jsonb_array_elements(v_patterns) e),
    'takeaways', v_takeaways,
    'note', 'Your profile is not in this group. A pattern shared by fewer than 5 people is left out. A pattern shared by 5 to 9 people is shown as a range.'
  );
end;
$$;

revoke all on function trace_peer_cohort.peer_patterns(extensions.vector, jsonb, text)
  from public, anon, authenticated, service_role, trace_app_user;
grant execute on function trace_peer_cohort.peer_patterns(extensions.vector, jsonb, text) to trace_app_runtime;

do $$
declare
  v_def text := pg_get_functiondef('trace_peer_cohort.peer_patterns(extensions.vector,jsonb,text)'::regprocedure);
begin
  if position('limit 120' in v_def) = 0 or position('enable_indexscan' in v_def) = 0 then
    raise exception 'the embedding pool must be an exact scan of about 120';
  end if;
  if position('profile_key' in v_def) > 0 or position('''candidates''' in v_def) > 0 then
    raise exception 'peer_patterns must return counts only';
  end if;
  if not has_function_privilege('trace_app_runtime', 'trace_peer_cohort.peer_patterns(extensions.vector,jsonb,text)', 'EXECUTE')
     or has_function_privilege('anon', 'trace_peer_cohort.peer_patterns(extensions.vector,jsonb,text)', 'EXECUTE')
     or has_function_privilege('authenticated', 'trace_peer_cohort.peer_patterns(extensions.vector,jsonb,text)', 'EXECUTE')
     or has_function_privilege('trace_app_user', 'trace_peer_cohort.peer_patterns(extensions.vector,jsonb,text)', 'EXECUTE') then
    raise exception 'peer_patterns stays callable by the runtime role only';
  end if;
end;
$$;
