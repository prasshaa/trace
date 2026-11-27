-- Trace data foundation v2.45.0: the time-to-first-PM sentence needs a verb.
--
-- v2.44.0 is already applied. This replaces only peer_patterns. The helpers stay.

create or replace function trace_peer_cohort.peer_patterns(
  p_query extensions.vector(384),
  p_user jsonb,
  p_cohort_version text
) returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  with levels as (
    select array['Junior PM','APM','Product Manager','Senior Product Manager','Lead',
                 'Principal/Staff/Group','Head','Director','VP/CPO']::text[] as names
  ),
  args as (
    select coalesce(p_user->>'prior', '') as prior,
           coalesce(p_user->>'move', '') as move,
           nullif(p_user->>'n_pre', '')::integer as n_pre,
           case when jsonb_typeof(p_user->'mba') = 'boolean' then (p_user->>'mba')::boolean else null end as mba,
           nullif(p_user->>'start_year', '')::integer as start_year,
           coalesce((p_user->>'in_pm')::boolean, false) as in_pm,
           coalesce(p_user->>'first_pm_level', '') as first_pm,
           coalesce(p_user->>'target_level', '') as target
  ),
  pool as materialized (
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
     order by ((p.embedding operator(extensions.<=>) p_query) + 0)
     limit 120
  ),
  ranked as materialized (
    select pool.*,
           pool.cosine
           + case when args.prior <> '' and pool.prior = args.prior then 0.20 else 0 end
           + case when args.move <> '' and pool.move = args.move then 0.08 else 0 end
           + case
               when array_position(levels.names, pool.level) is not null
                and array_position(levels.names, args.target) is not null
               then 0.12 * greatest(0, 1 - abs(array_position(levels.names, pool.level)
                        - array_position(levels.names, args.target)) / 4.0)
               else 0
             end
           + case
               when args.n_pre is not null and pool.n_pre is not null
                and (case when args.n_pre <= 0 then 0 when args.n_pre = 1 then 1 when args.n_pre <= 3 then 2 else 3 end)
                  = (case when pool.n_pre <= 0 then 0 when pool.n_pre = 1 then 1 when pool.n_pre <= 3 then 2 else 3 end)
               then 0.06 else 0
             end
           + case when args.mba is not null and pool.mba is not null and args.mba = pool.mba then 0.04 else 0 end
           + case when args.start_year is not null and pool.start_year is not null
                  then 0.06 * greatest(0, 1 - abs(args.start_year - pool.start_year) / 15.0)
                  else 0 end as score
      from pool
      cross join args
      cross join levels
     order by score desc, pool.cosine desc
     limit 40
  ),
  cohort as (
    select norm_seniority_group as level,
           final_primary_prior_function as prior,
           final_entry_move as move,
           final_first_pm_group as first_pm,
           case when final_has_history and final_n_pre_pm_roles > 0 then final_n_pre_pm_roles end as n_pre,
           final_n_pm_roles as n_pm,
           has_mba as mba,
           final_months_to_first_pm_from_earliest_nonintern as months
      from trace_peer_cohort.profiles
     where cohort_version = p_cohort_version
  ),
  move_line as (
    select trace_peer_cohort.share_text(
             (select count(*) from ranked where move = 'internal')::integer,
             (select count(*) from ranked where move in ('internal', 'external'))::integer,
             (select count(*) from cohort where move = 'internal')::integer,
             (select count(*) from cohort where move in ('internal', 'external'))::integer,
             (select count(*) from cohort)::integer,
             'with a recorded move moved into a product role from inside the employer.',
             case when args.move = 'internal' then 1.1 else 1.2 end) as internal_text,
           trace_peer_cohort.share_text(
             (select count(*) from ranked where move = 'external')::integer,
             (select count(*) from ranked where move in ('internal', 'external'))::integer,
             (select count(*) from cohort where move = 'external')::integer,
             (select count(*) from cohort where move in ('internal', 'external'))::integer,
             (select count(*) from cohort)::integer,
             'with a recorded move moved into a product role from outside the employer.',
             case when args.move = 'external' then 1.1 else 1.2 end) as external_text
      from args
  ),
  prior_lines as (
    select trace_peer_cohort.share_text(
             g.cnt, g.known, g.ccnt, g.cknown, g.cohort_n,
             'with a recorded background came from ' || g.label || ' before a product role.', 1.2) as text,
           g.label, g.cnt, g.known, g.ccnt, g.cknown
      from (
        select r.prior as label,
               count(*)::integer as cnt,
               (select count(*) from ranked where prior <> '')::integer as known,
               (select count(*) from cohort where prior = r.prior)::integer as ccnt,
               (select count(*) from cohort where prior <> '')::integer as cknown,
               (select count(*) from cohort)::integer as cohort_n
          from ranked r
         where r.prior <> '' and r.prior <> (select prior from args)
         group by r.prior
         order by count(*) desc, r.prior
         limit 4
      ) g
  ),
  first_line as (
    select trace_peer_cohort.share_text(
             (select count(*) from ranked where first_pm = args.target)::integer,
             (select count(*) from ranked where first_pm <> '')::integer,
             (select count(*) from cohort where first_pm = args.target)::integer,
             (select count(*) from cohort where first_pm <> '')::integer,
             (select count(*) from cohort)::integer,
             'with a recorded first product level started in product as ' || args.target || '.',
             1.1) as text
      from args
     where args.target <> '' and not args.in_pm
  ),
  level_line as (
    select trace_peer_cohort.share_text(
             (select count(*) from ranked r
               where array_position((select names from levels), r.level) >= array_position((select names from levels), args.target))::integer,
             (select count(*) from ranked)::integer,
             (select count(*) from cohort c
               where array_position((select names from levels), c.level) >= array_position((select names from levels), args.target))::integer,
             (select count(*) from cohort)::integer,
             (select count(*) from cohort)::integer,
             'are at ' || args.target || ' or above.',
             1.1) as text
      from args
     where args.in_pm and args.target <> ''
  ),
  role_window as (
    select trace_peer_cohort.share_text(
             (select count(*) from ranked where n_pre in (1, 2))::integer,
             (select count(*) from ranked where n_pre is not null and n_pre > 0)::integer,
             (select count(*) from cohort where n_pre in (1, 2))::integer,
             (select count(*) from cohort where n_pre is not null and n_pre > 0)::integer,
             (select count(*) from cohort)::integer,
             'who had a role before product had 1 or 2 roles before a product role.',
             null) as text
      from args
     where not args.in_pm
  ),
  mba_line as (
    select case
      when coalesce(args.mba, false)
       and (select count(*) from cohort) >= 10
       and (select count(*) from cohort where mba) >= 5
       and round((select count(*) from cohort where mba)::numeric / (select count(*) from cohort) * 100) < 15
      then 'MBAs are uncommon among these PMs: about '
           || round((select count(*) from cohort where mba)::numeric / (select count(*) from cohort) * 100)::integer
           || '% of all ' || (select count(*) from cohort) || '.'
      when coalesce(args.mba, false)
       and (select count(*) from cohort) >= 10
       and (select count(*) from cohort where mba) >= 5
      then 'About '
           || round((select count(*) from cohort where mba)::numeric / (select count(*) from cohort) * 100)::integer
           || '% of all ' || (select count(*) from cohort) || ' PMs have an MBA.'
      else trace_peer_cohort.share_text(
             (select count(*) from ranked where mba)::integer,
             (select count(*) from ranked)::integer,
             (select count(*) from cohort where mba)::integer,
             (select count(*) from cohort)::integer,
             (select count(*) from cohort)::integer,
             'had an MBA.',
             1.2)
    end as text
      from args
  ),
  month_stats as (
    select count(months)::integer as n,
           percentile_cont(0.5) within group (order by months) as median,
           percentile_cont(0.25) within group (order by months) as low,
           percentile_cont(0.75) within group (order by months) as high
      from ranked
  ),
  cohort_months as (
    select percentile_cont(0.5) within group (order by months) as median
      from cohort
     where months is not null
  ),
  pre_stats as (
    select count(n_pre)::integer as n,
           percentile_cont(0.5) within group (order by n_pre) as median
      from ranked
     where n_pre is not null and n_pre > 0
  ),
  progress as (
    select count(*)::integer as n,
           count(*) filter (
             where array_position((select names from levels), level) >= array_position((select names from levels), args.target)
           )::integer as reached,
           count(*) filter (where n_pm > 0)::integer as roles_n,
           percentile_cont(0.5) within group (order by n_pm) filter (where n_pm > 0) as roles_med
      from cohort
      cross join args
     where args.in_pm
       and args.first_pm <> ''
       and args.target <> ''
       and cohort.first_pm = args.first_pm
  ),
  years_in as (
    select count(*)::integer as n,
           percentile_cont(0.5) within group (order by yrs) as median
      from (
        select (extract(year from current_date)::integer - ranked.start_year) - ranked.months / 12.0 as yrs
          from ranked
          cross join args
         where args.in_pm
           and ranked.start_year is not null and ranked.months is not null
           and (extract(year from current_date)::integer - ranked.start_year) - ranked.months / 12.0 >= 0
      ) s
  ),

  reached_people as (
    select r.prior
      from ranked r
      cross join args
      cross join levels
     where args.in_pm
       and args.target <> ''
       and array_position(levels.names, r.level) >= array_position(levels.names, args.target)
  ),
  mix_counts as (
    select prior as label, count(*)::integer as cnt
      from reached_people
     where prior <> ''
     group by prior
    having count(*) >= 5
  ),
  mix_line as (
    select case
      when (select count(*) from reached_people) < 5 then null
      when (select count(*) from mix_counts) = 0 then null
      when (select count(*) from reached_people) < 10 then
        'Among the closest PMs at ' || (select target from args) || ' or above, '
        || case
             when (select count(*) from mix_counts) = 1
             then 'the most common background before product was ' || (select label from mix_counts)
             else 'backgrounds before product include '
                  || (select string_agg(label, ' and ' order by cnt desc, label) from mix_counts)
           end || '.'
      else
        'Among the ' || (select count(*) from reached_people) || ' closest PMs at '
        || (select target from args) || ' or above, '
        || (select string_agg(
              case when cnt < 10 then '5–9 came from ' || label
                   else cnt::text || ' came from ' || label end,
              ' and ' order by cnt desc, label)
            from mix_counts)
        || ' before product.'
    end as text
  ),
  career_years as (
    select count(*)::integer as n,
           percentile_cont(0.5) within group (order by yrs) as median
      from (
        select (extract(year from current_date)::integer - ranked.start_year)::numeric as yrs
          from ranked
          cross join args
         where args.in_pm
           and args.target <> ''
           and ranked.level = args.target
           and ranked.start_year is not null
           and extract(year from current_date)::integer >= ranked.start_year
      ) s
  ),
  preface as (
    select case
      when args.prior <> '' and (select count(*) from cohort where prior = args.prior) < 5
      then 'Few PMs in our data came from ' ||
        case args.prior
          when 'Design / UX' then 'design'
          when 'Software Engineering / QA / IT' then 'software engineering'
          when 'Business / Consulting / Finance / Operations' then 'business or consulting'
          when 'Data / Analytics / Research' then 'data or analytics'
          when 'Sales / Marketing / Customer Success' then 'sales or marketing'
          when 'Entrepreneurship / Founders' then 'founding'
          else lower(args.prior)
        end || '; here is what PMs with similar experience did.'
      else ''
    end as text
    from args
  ),
  pieces as (
    select text, kind, ord from (
      select internal_text as text, 'internal' as kind, 1 as ord from move_line
      union all
      select external_text, 'external', 2 from move_line
      union all
      select text, 'prior', 3 from prior_lines
      union all
      select text, 'first', 4 from first_line
      union all
      select text, 'mba', 5 from mba_line
      union all
      select text, 'level', 11 from level_line
      union all
      select text, 'roles', 12 from role_window
      union all
      select case
        when n >= 10 and median is not null
        then 'Among the ' || n || ' closest PMs with a dated path, it took about '
             || trace_peer_cohort.duration_phrase(round(median::numeric))
             || ' to reach a first PM role'
             || case
                  when low is not null and high is not null
                   and round(high / 6.0) * 6 > round(low / 6.0) * 6
                  then '; the middle half took '
                       || trace_peer_cohort.duration_span((round((low / 6.0)::numeric) * 6), (round((high / 6.0)::numeric) * 6))
                  else ''
                end
             || '.'
        else null
      end, 'months', 6 from month_stats
      union all
      select case
        when n >= 10 and median is not null and round(median) > 0
        then 'Among the ' || n || ' closest PMs who had a role before product, the middle number of those roles was about '
             || round(median)::integer || '.'
        else null
      end, 'pre', 8 from pre_stats
      union all
      select case
        when n < 10 or reached < 5 then null
        else (
          case
            when reached < 10 and reached::numeric / n >= 0.5 then 'Most of the ' || n
            when reached < 10 then '5–9 of the ' || n
            else reached::text || ' of the ' || n
          end
          || ' cohort PMs who started as ' || (select first_pm from args)
          || ' are now ' || (select target from args) || ' or above.'
        )
      end, 'progression', 9 from progress
      union all
      select case
        when roles_n >= 10 and roles_med is not null and round(roles_med) > 0
        then 'The middle number of product roles along that path was about ' || round(roles_med)::integer || '.'
        else null
      end, 'progression', 10 from progress
      union all
      select case
        when n >= 10 and median is not null and round(median) >= 0
        then 'Among the ' || n || ' closest PMs with a dated path, the middle time in a product role was about '
             || round(median)::integer || ' years.'
        else null
      end, 'years', 13 from years_in
      union all
      select text, 'mix', 14 from mix_line
      union all
      select case
        when n >= 10 and median is not null and median > 0
        then 'Among the ' || n || ' closest PMs at ' || (select target from args)
             || ', the middle time since career start was about '
             || trace_peer_cohort.duration_phrase((median * 12)::numeric) || '.'
        else null
      end, 'career', 15 from career_years
    ) raw
    where text is not null
  ),
  numbered as (
    select text, kind, row_number() over (order by ord, text) as n
      from pieces
  )
  select jsonb_build_object(
    'available', (select count(*) from numbered) > 0 or (select text from preface) <> '',
    'pool_count', (select count(*) from pool),
    'neighbour_count', (select count(*) from ranked),
    'headline', 'PMs with paths like yours',
    'match_line', (
      select case
        when prior <> '' and (select count(*) from ranked where ranked.prior = args.prior) >= 10
        then 'Matched by similarity of career path. Matched on your background: ' || prior || '.'
        else 'Matched by similarity of career path.'
      end
      from args
    ),
    'preface', (select text from preface),
    'disclaimer', 'These are patterns across the closest profiles, not a prediction and not a guarantee.',
    'patterns', coalesce((
      select jsonb_agg(jsonb_build_object('id', 'PEER-' || n, 'text', text) order by n) from numbered
    ), '[]'::jsonb),
    'takeaways', (
      select coalesce(jsonb_agg(line), '[]'::jsonb) from (
        select 'A common step in this group was moving into product inside the same employer.' as line
         where (select count(*) from ranked where move = 'internal')
             > (select count(*) from ranked where move = 'external')
           and (select count(*) from ranked where move in ('internal', 'external')) > 0
           and (select count(*) from ranked where move = 'internal')::numeric
             / (select count(*) from ranked where move in ('internal', 'external')) >= 0.30
           and (select internal_text from move_line) is not null
        union all
        select case label
          when 'Software Engineering / QA / IT' then 'Software engineering, QA or IT was a frequent path into product in this group.'
          when 'Business / Consulting / Finance / Operations' then 'Business, consulting, finance or operations was a frequent path into product in this group.'
          when 'Design / UX' then 'Design was a frequent path into product in this group.'
          else null
        end
          from prior_lines
         where text is not null and known > 0 and cnt::numeric / known >= 0.30
           and (cknown = 0 or cnt::numeric / known > ccnt::numeric / nullif(cknown, 0))
        union all
        select 'An MBA shows up in this group, and it is not described as a requirement.'
         where (select text from mba_line) is not null
           and (select count(*) from ranked) > 0
           and (select count(*) from ranked where mba)::numeric / (select count(*) from ranked) >= 0.30
      ) lines
      where line is not null
    ),
    'note', 'Your profile is not in this group. A pattern shared by fewer than 5 people is left out. A pattern shared by 5 to 9 people is shown as a range.'
  );
$$;



revoke all on function trace_peer_cohort.peer_patterns(extensions.vector, jsonb, text)
  from public, anon, authenticated, service_role, trace_app_user;
grant execute on function trace_peer_cohort.peer_patterns(extensions.vector, jsonb, text) to trace_app_runtime;
