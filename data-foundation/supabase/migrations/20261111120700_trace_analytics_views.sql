-- Trace Data Foundation v2 · 08 · consent-aware eligibility, career sequences, transitions, routes
--
-- All views live in trace_ref (not exposed) and use security_invoker = true so that they never
-- run with the view owner's privileges. The Python reference implementation of the same rules
-- is scripts/trace_model.py; docs/DATA_DICTIONARY.md sections 5-7 define the semantics.
--
-- Key rule: the career sequence is built from EVERY primary record first (internships,
-- unresolved titles, low-confidence mappings and undated roles included). Exclusions are applied
-- only afterwards, as reasons on each transition, so an excluded record breaks the chain instead
-- of being skipped over. A -> unknown -> B therefore yields two unconfirmed transitions, never A -> B.

-- ------------------------------------------------------------------ consent and cohorts
create view trace_ref.v_current_consent with (security_invoker = true) as
select distinct on (e.user_id, e.purpose)
  e.user_id,
  e.purpose,
  e.status,
  e.consent_version,
  e.consent_event_id,
  e.recorded_at,
  d.legal_review_status,
  (e.status = 'granted' and d.legal_review_status = 'approved') as is_active
from public.trace_consent_events e
join public.trace_consent_documents d
  on d.consent_version = e.consent_version and d.purpose = e.purpose
order by e.user_id, e.purpose, e.recorded_at desc, e.consent_event_id desc;

-- The ONLY entry point for empirical analytics.
create view trace_ref.v_eligible_profiles with (security_invoker = true) as
select p.*
from trace_ref.profiles p
join trace_ref.v_current_consent c
  on c.user_id = p.contributor_user_id
 and c.purpose = 'deidentified_shared_dataset'
 and c.is_active
where p.dataset_class = 'consented_real_reference'
  and p.source_consent_event_id is not null
  and p.review_status in ('participant_confirmed', 'reviewer_confirmed');

-- Demo eligibility is separate from analytics eligibility.
create view trace_ref.v_demo_eligible_profiles with (security_invoker = true) as
select p.profile_id, p.dataset_class, 'synthetic_clearly_labelled'::text as demo_basis
from trace_ref.profiles p
where p.dataset_class = 'synthetic_test_fixture' and p.synthetic_demo_allowed
union all
select p.profile_id, p.dataset_class, 'external_demo_consent'::text
from trace_ref.v_eligible_profiles p
join trace_ref.v_current_consent c
  on c.user_id = p.contributor_user_id and c.purpose = 'external_demo' and c.is_active;

-- Which profiles each data_mode may read.
create view trace_ref.v_mode_profiles with (security_invoker = true) as
select 'empirical'::public.trace_data_mode as data_mode, profile_id from trace_ref.v_eligible_profiles
union all
select 'synthetic_test'::public.trace_data_mode, profile_id
from trace_ref.profiles where dataset_class = 'synthetic_test_fixture'
union all
select 'synthetic_demo'::public.trace_data_mode, profile_id
from trace_ref.profiles where dataset_class = 'synthetic_test_fixture' and synthetic_demo_allowed;

-- ------------------------------------------------------------------ settings (fail closed)
create view trace_ref.v_settings with (security_invoker = true) as
select
  coalesce((select value_numeric from trace_ref.analytics_settings where setting_key = 'min_mapping_confidence'), 1.0) as min_mapping_confidence,
  (select value_numeric from trace_ref.analytics_settings where setting_key = 'min_profiles_limited') as min_profiles_limited,
  (select value_numeric from trace_ref.analytics_settings where setting_key = 'min_profiles_moderate') as min_profiles_moderate,
  (select value_numeric from trace_ref.analytics_settings where setting_key = 'min_profiles_strong') as min_profiles_strong,
  (select value_numeric from trace_ref.analytics_settings where setting_key = 'min_dated_profiles_for_timeline') as min_dated_profiles_for_timeline;

-- ------------------------------------------------------------------ employments
create view trace_ref.v_employment_classified with (security_invoker = true) as
select
  e.employment_id,
  e.profile_id,
  p.dataset_class,
  e.sequence_index,
  e.employer_id,
  e.title_raw_redacted,
  e.role_id,
  t.role_family,                                    -- derived, never stored on the employment
  t.is_pm_target,
  coalesce(e.seniority_code, t.default_seniority_code) as seniority_code,
  s.rank as seniority_rank,
  e.employment_type,
  e.primary_career_sequence,
  e.mapping_status,
  e.mapping_confidence,
  e.start_precision, e.end_precision, e.is_current_as_stated,
  e.start_lo, e.start_hi, e.end_lo, e.end_hi,
  -- Founder (like internship, freelance, part-time) counts only with an explicit, documented override.
  coalesce(e.counts_as_full_time_experience, e.employment_type in ('full_time', 'contract')) as counts_as_full_time_experience,
  -- Inclusive tenure bounds; ongoing roles are censored at the profile's resume_as_of.
  case when e.start_lo is null then null
       when e.end_precision = 'ongoing' then case when p.resume_as_of_lo is null then null
                                                 else greatest(1, p.resume_as_of_lo - e.start_hi + 1) end
       when e.end_lo is null then null
       else greatest(1, e.end_lo - e.start_hi + 1) end as tenure_months_min,
  case when e.start_lo is null then null
       when e.end_precision = 'ongoing' then p.resume_as_of_hi - e.start_lo + 1
       else e.end_hi - e.start_lo + 1 end as tenure_months_max,
  (e.end_precision = 'ongoing') as tenure_is_censored,
  array_remove(array[
    case when not e.primary_career_sequence then 'parallel_activity' end,
    case when not coalesce(e.counts_as_full_time_experience, e.employment_type in ('full_time', 'contract'))
         then 'not_full_time_experience' end,
    case when not t.is_resolved then 'unresolved_role' end,
    case when e.mapping_status not in ('confirmed', 'auto_high_confidence') then 'mapping_not_confirmed' end,
    case when e.mapping_confidence < cfg.min_mapping_confidence then 'low_mapping_confidence' end,
    case when e.start_lo is null then 'unknown_start_date' end
  ], null) as exclusion_reasons
from trace_ref.employments e
join trace_ref.profiles p on p.profile_id = e.profile_id
join public.trace_role_taxonomy t on t.role_id = e.role_id
join public.trace_seniority_levels s on s.seniority_code = coalesce(e.seniority_code, t.default_seniority_code)
cross join trace_ref.v_settings cfg;

-- Every primary record, in sequence order, with its predecessor. Nothing else is filtered.
create view trace_ref.v_career_sequence with (security_invoker = true) as
select
  c.*,
  cardinality(c.exclusion_reasons) = 0 as is_transition_eligible,
  row_number() over w as primary_position,
  lag(c.employment_id) over w as prev_employment_id
from trace_ref.v_employment_classified c
where c.primary_career_sequence
window w as (partition by c.profile_id order by c.sequence_index);

-- ------------------------------------------------------------------ transitions
create view trace_ref.v_role_transitions with (security_invoker = true) as
with pairs as (
  select
    n.profile_id, n.dataset_class,
    p.employment_id as from_employment_id, n.employment_id as to_employment_id,
    p.role_id as from_role_id, n.role_id as to_role_id,
    p.role_family as from_role_family, n.role_family as to_role_family,
    p.employer_id as from_employer_id, n.employer_id as to_employer_id,
    p.seniority_rank as from_rank, n.seniority_rank as to_rank,
    p.end_precision as prev_end_precision,
    p.start_lo as prev_start_lo, p.end_lo as prev_end_lo, p.end_hi as prev_end_hi,
    n.start_lo as next_start_lo, n.start_hi as next_start_hi,
    p.exclusion_reasons as from_reasons, n.exclusion_reasons as to_reasons
  from trace_ref.v_career_sequence n
  join trace_ref.v_career_sequence p on p.employment_id = n.prev_employment_id
), timed as (
  select pairs.*,
    case
      when prev_end_precision = 'ongoing' then 'previous_role_ongoing'
      when prev_end_lo is null or next_start_lo is null then 'timing_unknown'
      when prev_start_lo is not null and next_start_hi < prev_start_lo then 'sequence_order_conflict'
      when next_start_lo > prev_end_hi then 'sequential'
      -- Any overlap beyond the shared boundary month is never auto-confirmed. It stays
      -- unconfirmed until a reviewer corrects the dates or marks one role as parallel activity.
      when prev_end_lo = prev_end_hi and next_start_lo = next_start_hi then
        case when prev_end_lo = next_start_lo then 'same_month_boundary' else 'overlapping' end
      when prev_end_lo > next_start_hi then 'overlapping'
      else 'boundary_uncertain'
    end as timing_relation
  from pairs
)
select
  profile_id, dataset_class,
  from_employment_id, to_employment_id,
  from_role_id, to_role_id, from_role_family, to_role_family,
  case when from_employer_id is null or to_employer_id is null then 'unknown'
       when from_employer_id = to_employer_id then 'internal'
       else 'external' end as move_type,
  to_rank - from_rank as seniority_rank_change,
  timing_relation,
  case timing_relation
    when 'sequential' then next_start_lo - prev_end_hi - 1
    when 'same_month_boundary' then 0
    when 'boundary_uncertain' then 0
  end as gap_months_min,
  case timing_relation
    when 'sequential' then next_start_hi - prev_end_lo - 1
    when 'same_month_boundary' then 0
    when 'boundary_uncertain' then greatest(0, next_start_hi - prev_end_lo - 1)
  end as gap_months_max,
  (timing_relation in ('sequential', 'same_month_boundary')
   and prev_end_lo = prev_end_hi and next_start_lo = next_start_hi) as gap_is_precise,
  reasons as unconfirmed_reasons,
  case when cardinality(reasons) = 0 then 'confirmed' else 'unconfirmed' end as transition_status
from (
  select timed.*,
    array(select 'from:' || r from unnest(from_reasons) as r)
    || array(select 'to:' || r from unnest(to_reasons) as r)
    -- boundary_uncertain (e.g. year-only end and start in the same year) is never confirmable:
    -- chronological order cannot be established. Both records stay in the sequence.
    || case when timing_relation in ('sequential', 'same_month_boundary')
            then '{}'::text[] else array['timing:' || timing_relation] end as reasons
  from timed
) x;

-- Profile-level integrity flags (sequence gaps, order conflicts, several ongoing primary roles).
create view trace_ref.v_profile_sequence_integrity with (security_invoker = true) as
select
  e.profile_id,
  count(*) as employment_count,
  max(e.sequence_index) as max_sequence_index,
  max(e.sequence_index) <> count(*) as has_sequence_gaps,
  count(*) filter (where e.primary_career_sequence and e.end_precision = 'ongoing') as ongoing_primary_count,
  exists (
    select 1 from trace_ref.v_role_transitions t
    where t.profile_id = e.profile_id and t.timing_relation = 'sequence_order_conflict'
  ) as has_order_conflict
from trace_ref.employments e
group by e.profile_id;

-- ------------------------------------------------------------------ segments, stints, paths
-- A segment is a maximal run of records joined by confirmed transitions. Consecutive records with
-- the same role inside a segment collapse into one stint (e.g. PM at A then PM at B).
create view trace_ref.v_career_stints with (security_invoker = true) as
with seq as (
  select s.*, t.transition_status as incoming_status
  from trace_ref.v_career_sequence s
  left join trace_ref.v_role_transitions t on t.to_employment_id = s.employment_id
), segmented as (
  select seq.*,
    sum(case when incoming_status = 'confirmed' then 0 else 1 end)
      over (partition by profile_id order by sequence_index) as segment_no
  from seq
), eligible as (
  select segmented.*,
    row_number() over (partition by profile_id, segment_no order by sequence_index)
    - row_number() over (partition by profile_id, segment_no, role_id order by sequence_index) as island
  from segmented
  where is_transition_eligible
)
select
  profile_id, dataset_class, segment_no, role_id,
  min(sequence_index) as first_sequence_index,
  (array_agg(start_lo order by sequence_index))[1] as start_lo,
  (array_agg(start_hi order by sequence_index))[1] as start_hi,
  array_agg(employment_id order by sequence_index) as employment_ids
from eligible
group by profile_id, dataset_class, segment_no, role_id, island;

create view trace_ref.v_career_paths with (security_invoker = true) as
select
  profile_id, dataset_class, segment_no,
  array_agg(role_id order by first_sequence_index) as role_path,
  array_agg(start_lo order by first_sequence_index) as stint_start_lo,
  array_agg(start_hi order by first_sequence_index) as stint_start_hi
from trace_ref.v_career_stints
group by profile_id, dataset_class, segment_no;

-- Unified timeline (employment, education, breaks) for review screens.
create view trace_ref.v_career_timeline with (security_invoker = true) as
select profile_id, 'employment'::text as item_type, employment_id as item_id, sequence_index,
       title_raw_redacted as label, start_lo, start_hi, end_lo, end_hi, end_precision
from trace_ref.employments
union all
select profile_id, 'education', education_id, null, qualification_level || coalesce(' - ' || field_of_study, ''),
       start_lo, start_hi, end_lo, end_hi, end_precision
from trace_ref.education
union all
select profile_id, 'career_break', break_id, null, break_reason, start_lo, start_hi, end_lo, end_hi, end_precision
from trace_ref.career_breaks;

-- ------------------------------------------------------------------ routes and timelines
-- One observation per individual: the first place the route occurs as consecutive stints inside
-- one confirmed segment of that individual's own history. Routes are never stitched across people.
create function trace_ref.route_observations(p_route text[], p_data_mode public.trace_data_mode)
returns table (
  profile_id text, segment_no bigint, start_position integer,
  route_months_min integer, route_months_max integer, is_precise boolean
)
language sql stable
security invoker
set search_path = ''
as $$
  with route as (select trace_util.collapse_consecutive(p_route) as r),
  matches as (
    select cp.profile_id, cp.segment_no, cp.stint_start_lo, cp.stint_start_hi,
           trace_util.subarray_position(cp.role_path, route.r) as pos,
           cardinality(route.r) as n
    from trace_ref.v_career_paths cp
    join trace_ref.v_mode_profiles mp on mp.profile_id = cp.profile_id and mp.data_mode = p_data_mode
    cross join route
    where cardinality(route.r) >= 2
  )
  select distinct on (m.profile_id)
    m.profile_id, m.segment_no, m.pos,
    greatest(0, m.stint_start_lo[m.pos + m.n - 1] - m.stint_start_hi[m.pos]),
    m.stint_start_hi[m.pos + m.n - 1] - m.stint_start_lo[m.pos],
    (m.stint_start_lo[m.pos] = m.stint_start_hi[m.pos]
     and m.stint_start_lo[m.pos + m.n - 1] = m.stint_start_hi[m.pos + m.n - 1])
  from matches m
  where m.pos is not null
  order by m.profile_id, m.segment_no, m.pos
$$;

-- Cohort summary for one route. Percentiles use month-precision observations only, and only
-- when there are at least min_dated_profiles_for_timeline of them. Synthetic modes always
-- return evidence_class synthetic_test_only and timeline_display_allowed = false.
create function trace_ref.route_timeline_summary(p_route text[], p_data_mode public.trace_data_mode)
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
language sql stable
security invoker
set search_path = ''
as $$
  with obs as (
    select * from trace_ref.route_observations(p_route, p_data_mode)
  ), agg as (
    select
      count(*)::integer as supporting,
      count(*) filter (where is_precise)::integer as dated,
      percentile_cont(0.25) within group (order by route_months_min) filter (where is_precise) as p25,
      percentile_cont(0.50) within group (order by route_months_min) filter (where is_precise) as p50,
      percentile_cont(0.75) within group (order by route_months_min) filter (where is_precise) as p75,
      min(route_months_min) as cmin,
      max(route_months_max) as cmax
    from obs
  )
  select
    p_data_mode,
    trace_util.collapse_consecutive(p_route),
    agg.supporting,
    agg.dated,
    coalesce(agg.supporting >= cfg.min_profiles_limited, false),
    case
      when p_data_mode <> 'empirical' then 'synthetic_test_only'
      when agg.supporting >= cfg.min_profiles_strong then 'strong'
      when agg.supporting >= cfg.min_profiles_moderate then 'moderate'
      when agg.supporting >= cfg.min_profiles_limited then 'limited'
      else 'insufficient'
    end::public.trace_evidence_class,
    case when agg.dated >= cfg.min_dated_profiles_for_timeline then agg.p25::numeric end,
    case when agg.dated >= cfg.min_dated_profiles_for_timeline then agg.p50::numeric end,
    case when agg.dated >= cfg.min_dated_profiles_for_timeline then agg.p75::numeric end,
    agg.cmin,
    agg.cmax,
    'route_start_to_target_start_months',
    coalesce(p_data_mode = 'empirical' and agg.dated >= cfg.min_dated_profiles_for_timeline, false)
  from agg cross join trace_ref.v_settings cfg
$$;

-- ------------------------------------------------------------------ evidence guard
-- Empirical pathway evidence may cite only a profile that is eligible at the moment of insert.
create function trace_util.enforce_pathway_evidence_eligibility() returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.data_mode = 'empirical' and new.reference_profile_id is not null
     and not exists (select 1 from trace_ref.v_eligible_profiles e where e.profile_id = new.reference_profile_id) then
    raise exception 'profile % is not eligible for empirical evidence (consent, review status or dataset class)', new.reference_profile_id
      using errcode = 'check_violation';
  end if;
  return new;
end
$$;
revoke execute on function trace_util.enforce_pathway_evidence_eligibility() from public;

create trigger enforce_evidence_eligibility before insert or update of reference_profile_id, data_mode
  on public.trace_pathway_evidence
  for each row execute function trace_util.enforce_pathway_evidence_eligibility();

-- Resumes past their retention date; consumed by the scheduled deletion job (docs/ACCESS_CONTROL.md).
create view trace_ref.v_resumes_due_for_deletion with (security_invoker = true) as
select resume_id, owner_user_id, storage_bucket, storage_object_path, retention_until
from public.trace_resume_documents
where deleted_at is null and retention_until <= now();

revoke all on all tables in schema trace_ref from public, anon, authenticated;
grant select on all tables in schema trace_ref to service_role;
revoke execute on all functions in schema trace_ref from public, anon, authenticated;
grant execute on function trace_ref.route_observations(text[], public.trace_data_mode),
                          trace_ref.route_timeline_summary(text[], public.trace_data_mode)
  to service_role;
