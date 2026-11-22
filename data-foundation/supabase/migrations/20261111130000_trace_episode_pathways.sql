-- Trace Data Foundation v2.1.0 · 12 · episode-aware career pathways
--
-- Adds pathway objects whose unit is an employment EPISODE (role + employer) instead of a role
-- code. Nothing existing is replaced or altered: v_career_stints, v_career_paths,
-- route_observations and route_timeline_summary keep their v2.0.2 behaviour.
--
-- Built on trace_ref.v_role_transitions, so every existing integrity rule is inherited unchanged:
-- confirmation, overlap, parallel activity, date precision, chronological uncertainty,
-- founder/freelance/internship classification and incomplete histories. A segment is still a
-- maximal run of records joined by CONFIRMED transitions; an excluded record still breaks the
-- chain instead of being bridged.
--
-- What changes is only how records inside a confirmed segment collapse into stints:
--   v2.0.2 objects   consecutive records with the same role_id collapse, whatever the employer
--   episode objects  consecutive records collapse only when the role is unchanged AND the employer
--                    is unchanged or cannot be established (change_type no_change / employer_unknown)
-- So PM at A -> PM at B is two stints (an employer_change), while PM at A -> PM at A is one.
--
-- Employer pseudonyms are profile-scoped tokens. They are used only to compare consecutive
-- records of the same profile and never appear in any output of these objects.
-- Like every trace_ref view, the views below cover ALL dataset classes; only the functions apply
-- data_mode (through trace_ref.v_mode_profiles).

-- ------------------------------------------------------------------ transitions with change type
create view trace_ref.v_episode_transitions with (security_invoker = true) as
select
  x.*,
  (x.transition_status = 'confirmed'
   and x.change_type in ('role_change', 'employer_change', 'role_and_employer_change', 'role_change_employer_unknown'))
    as is_pathway_move
from (
  select
    t.*,
    (t.from_role_id <> t.to_role_id) as role_changed,
    case t.move_type when 'external' then true when 'internal' then false end as employer_changed,  -- null = unknown
    case
      when t.move_type = 'unknown' then
        case when t.from_role_id <> t.to_role_id then 'role_change_employer_unknown' else 'employer_unknown' end
      when t.from_role_id <> t.to_role_id then
        case when t.move_type = 'external' then 'role_and_employer_change' else 'role_change' end
      else
        case when t.move_type = 'external' then 'employer_change' else 'no_change' end
    end as change_type
  from trace_ref.v_role_transitions t
) x;

comment on view trace_ref.v_episode_transitions is
  'v_role_transitions plus change_type (role_change, employer_change, role_and_employer_change, no_change, '
  'role_change_employer_unknown, employer_unknown) and is_pathway_move (confirmed and a role or employer change).';

-- ------------------------------------------------------------------ episode stints and paths
create view trace_ref.v_career_episode_stints with (security_invoker = true) as
with seq as (
  select s.profile_id, s.dataset_class, s.employment_id, s.sequence_index, s.role_id,
         s.start_lo, s.start_hi, s.is_transition_eligible,
         t.transition_status as incoming_status, t.change_type as incoming_change_type
  from trace_ref.v_career_sequence s
  left join trace_ref.v_episode_transitions t on t.to_employment_id = s.employment_id
), segmented as (
  select seq.*,
    sum(case when incoming_status = 'confirmed' then 0 else 1 end)
      over (partition by profile_id order by sequence_index) as segment_no
  from seq
), eligible as (
  -- Within a segment every record after the first has a confirmed incoming transition, and a
  -- confirmed transition requires both ends to be eligible; ineligible records are singleton
  -- segments and drop out here, exactly as in v_career_stints.
  select segmented.*,
    case when incoming_status = 'confirmed' and incoming_change_type in ('no_change', 'employer_unknown')
         then 0 else 1 end as starts_stint
  from segmented
  where is_transition_eligible
), numbered as (
  select eligible.*,
    sum(starts_stint) over (partition by profile_id, segment_no order by sequence_index) as stint_no
  from eligible
)
select
  profile_id, dataset_class, segment_no, stint_no, role_id,
  case when stint_no = 1 then null
       else (array_agg(incoming_change_type order by sequence_index))[1] end as entry_change_type,
  min(sequence_index) as first_sequence_index,
  (array_agg(start_lo order by sequence_index))[1] as start_lo,
  (array_agg(start_hi order by sequence_index))[1] as start_hi,
  array_agg(employment_id order by sequence_index) as employment_ids
from numbered
group by profile_id, dataset_class, segment_no, stint_no, role_id;

create view trace_ref.v_career_episode_paths with (security_invoker = true) as
select
  profile_id, dataset_class, segment_no,
  array_agg(role_id order by stint_no) as role_path,
  array_agg(entry_change_type order by stint_no) as entry_change_types,  -- first element is always null
  array_agg(start_lo order by stint_no) as stint_start_lo,
  array_agg(start_hi order by stint_no) as stint_start_hi
from trace_ref.v_career_episode_stints
group by profile_id, dataset_class, segment_no;

-- ------------------------------------------------------------------ route validation
-- Episode routes are NOT collapsed: consecutive identical role ids mean an employer change
-- (PM at A -> PM at B is the route {PM-PM, PM-PM}). An invalid route raises 22023
-- (invalid_parameter_value) instead of returning an empty or zero-count result.
create function trace_ref.assert_valid_episode_route(p_route text[]) returns void
language plpgsql stable
security invoker
set search_path = ''
as $$
declare
  v_bad text[];
begin
  if p_route is null or cardinality(p_route) < 2 then
    raise exception 'an episode route needs at least two steps (got %)', coalesce(cardinality(p_route), 0)
      using errcode = '22023';
  end if;
  if array_ndims(p_route) <> 1 then
    raise exception 'an episode route must be a one-dimensional array' using errcode = '22023';
  end if;
  if array_position(p_route, null) is not null then
    raise exception 'an episode route must not contain null steps' using errcode = '22023';
  end if;
  select array_agg(distinct r order by r) into v_bad
  from unnest(p_route) as u(r)
  where not exists (select 1 from public.trace_role_taxonomy t where t.role_id = u.r and t.is_resolved);
  if v_bad is not null then
    raise exception 'episode route contains unknown or unresolved role ids: %', v_bad using errcode = '22023';
  end if;
end
$$;

-- ------------------------------------------------------------------ route functions
-- One observation per individual: the first place the route occurs as consecutive episode stints
-- inside one confirmed segment of that individual's own history. Never stitched across people.
create function trace_ref.episode_route_observations(p_route text[], p_data_mode public.trace_data_mode)
returns table (
  profile_id text, segment_no bigint, start_position integer, step_change_types text[],
  route_months_min integer, route_months_max integer, is_precise boolean
)
language plpgsql stable
security invoker
set search_path = ''
as $$
begin
  perform trace_ref.assert_valid_episode_route(p_route);
  if p_data_mode is null then
    raise exception 'data_mode is required' using errcode = '22023';
  end if;
  return query
  with matches as (
    select cp.profile_id as m_profile_id, cp.segment_no as m_segment_no,
           cp.entry_change_types as m_changes, cp.stint_start_lo as m_lo, cp.stint_start_hi as m_hi,
           trace_util.subarray_position(cp.role_path, p_route) as m_pos,
           cardinality(p_route) as m_n
    from trace_ref.v_career_episode_paths cp
    join trace_ref.v_mode_profiles mp on mp.profile_id = cp.profile_id and mp.data_mode = p_data_mode
  )
  select distinct on (m.m_profile_id)
    m.m_profile_id,
    m.m_segment_no,
    m.m_pos,
    m.m_changes[m.m_pos + 1 : m.m_pos + m.m_n - 1],
    greatest(0, m.m_lo[m.m_pos + m.m_n - 1] - m.m_hi[m.m_pos]),
    m.m_hi[m.m_pos + m.m_n - 1] - m.m_lo[m.m_pos],
    (m.m_lo[m.m_pos] = m.m_hi[m.m_pos] and m.m_lo[m.m_pos + m.m_n - 1] = m.m_hi[m.m_pos + m.m_n - 1])
  from matches m
  where m.m_pos is not null
  order by m.m_profile_id, m.m_segment_no, m.m_pos;
end
$$;

-- Cohort summary for one episode route: same columns, thresholds and fail-closed rules as
-- route_timeline_summary. The route is returned exactly as given (never collapsed).
create function trace_ref.episode_route_timeline_summary(p_route text[], p_data_mode public.trace_data_mode)
returns table (
  data_mode public.trace_data_mode,
  route text[],
  supporting_profile_count integer,
  dated_profile_count integer,
  count_display_allowed boolean,
  evidence_class public.trace_evidence_class,
  p25_months numeric, median_months numeric, p75_months numeric,
  coarse_min_months integer, coarse_max_months integer,
  timeline_metric text,
  timeline_display_allowed boolean
)
language plpgsql stable
security invoker
set search_path = ''
as $$
begin
  perform trace_ref.assert_valid_episode_route(p_route);
  return query
  with obs as (
    select o.route_months_min as o_min, o.route_months_max as o_max, o.is_precise as o_precise
    from trace_ref.episode_route_observations(p_route, p_data_mode) o
  ), agg as (
    select
      count(*)::integer as a_supporting,
      count(*) filter (where obs.o_precise)::integer as a_dated,
      percentile_cont(0.25) within group (order by obs.o_min) filter (where obs.o_precise) as a_p25,
      percentile_cont(0.50) within group (order by obs.o_min) filter (where obs.o_precise) as a_p50,
      percentile_cont(0.75) within group (order by obs.o_min) filter (where obs.o_precise) as a_p75,
      min(obs.o_min) as a_cmin,
      max(obs.o_max) as a_cmax
    from obs
  )
  select
    p_data_mode,
    p_route,
    agg.a_supporting,
    agg.a_dated,
    coalesce(agg.a_supporting >= cfg.min_profiles_limited, false),
    (case
      when p_data_mode <> 'empirical' then 'synthetic_test_only'
      when agg.a_supporting >= cfg.min_profiles_strong then 'strong'
      when agg.a_supporting >= cfg.min_profiles_moderate then 'moderate'
      when agg.a_supporting >= cfg.min_profiles_limited then 'limited'
      else 'insufficient'
    end)::public.trace_evidence_class,
    case when agg.a_dated >= cfg.min_dated_profiles_for_timeline then agg.a_p25::numeric end,
    case when agg.a_dated >= cfg.min_dated_profiles_for_timeline then agg.a_p50::numeric end,
    case when agg.a_dated >= cfg.min_dated_profiles_for_timeline then agg.a_p75::numeric end,
    agg.a_cmin,
    agg.a_cmax,
    'route_start_to_target_start_months'::text,
    coalesce(p_data_mode = 'empirical' and agg.a_dated >= cfg.min_dated_profiles_for_timeline, false)
  from agg cross join trace_ref.v_settings cfg;
end
$$;

-- ------------------------------------------------------------------ privileges (service_role only)
revoke all on trace_ref.v_episode_transitions, trace_ref.v_career_episode_stints, trace_ref.v_career_episode_paths
  from public, anon, authenticated;
grant select on trace_ref.v_episode_transitions, trace_ref.v_career_episode_stints, trace_ref.v_career_episode_paths
  to service_role;

revoke execute on function trace_ref.assert_valid_episode_route(text[]),
                           trace_ref.episode_route_observations(text[], public.trace_data_mode),
                           trace_ref.episode_route_timeline_summary(text[], public.trace_data_mode)
  from public, anon, authenticated;
grant execute on function trace_ref.assert_valid_episode_route(text[]),
                          trace_ref.episode_route_observations(text[], public.trace_data_mode),
                          trace_ref.episode_route_timeline_summary(text[], public.trace_data_mode)
  to service_role;
