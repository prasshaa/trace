-- Trace Data Foundation v2 · 02 · helper functions
--
-- Month index = year * 12 + (month - 1). A partial date is a closed interval [lo, hi]
-- of month indices. See docs/DATA_DICTIONARY.md section 5 and scripts/trace_model.py.

create function trace_util.valid_partial_date(
  p_year integer, p_month integer, p_precision public.trace_date_precision
) returns boolean
language sql immutable parallel safe
set search_path = ''
as $$
  select case p_precision
    when 'month'   then p_year between 1950 and 2100 and p_month between 1 and 12
    when 'year'    then p_year between 1950 and 2100 and p_month is null
    when 'unknown' then p_year is null and p_month is null
    when 'ongoing' then p_year is null and p_month is null
    else false
  end
$$;

create function trace_util.month_lo(
  p_year integer, p_month integer, p_precision public.trace_date_precision
) returns integer
language sql immutable parallel safe
set search_path = ''
as $$
  select case p_precision
    when 'month' then p_year * 12 + p_month - 1
    when 'year'  then p_year * 12
    else null
  end
$$;

create function trace_util.month_hi(
  p_year integer, p_month integer, p_precision public.trace_date_precision
) returns integer
language sql immutable parallel safe
set search_path = ''
as $$
  select case p_precision
    when 'month' then p_year * 12 + p_month - 1
    when 'year'  then p_year * 12 + 11
    else null
  end
$$;

-- First day of the month for a month index (display only; analytics use the integer index).
create function trace_util.month_index_to_date(p_index integer) returns date
language sql immutable parallel safe strict
set search_path = ''
as $$
  select make_date(p_index / 12, p_index % 12 + 1, 1)
$$;

-- True when start/end bounds can describe a real period (the latest possible end is not
-- before the earliest possible start). Unknown or ongoing ends are always feasible.
create function trace_util.feasible_period(
  p_start_year integer, p_start_month integer, p_start_precision public.trace_date_precision,
  p_end_year integer, p_end_month integer, p_end_precision public.trace_date_precision
) returns boolean
language sql immutable parallel safe
set search_path = ''
as $$
  select coalesce(
    trace_util.month_hi(p_end_year, p_end_month, p_end_precision)
      >= trace_util.month_lo(p_start_year, p_start_month, p_start_precision),
    true)
$$;

-- Remove consecutive duplicates: {A,A,B,A} -> {A,B,A}
create function trace_util.collapse_consecutive(p_values text[]) returns text[]
language sql immutable parallel safe
set search_path = ''
as $$
  select coalesce(array_agg(v order by i), '{}'::text[])
  from (
    select v, i, lag(v) over (order by i) as prev
    from unnest(p_values) with ordinality as t(v, i)
  ) s
  where prev is distinct from v
$$;

-- 1-based position of the first contiguous occurrence of p_needle in p_haystack, or null.
create function trace_util.subarray_position(p_haystack text[], p_needle text[]) returns integer
language sql immutable parallel safe
set search_path = ''
as $$
  select min(i)::integer
  from generate_series(1, coalesce(cardinality(p_haystack), 0) - coalesce(cardinality(p_needle), 0) + 1) as g(i)
  where cardinality(p_needle) > 0
    and p_haystack[i : i + cardinality(p_needle) - 1] = p_needle
$$;

create function trace_util.set_updated_at() returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end
$$;

create function trace_util.prevent_update() returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception '% is append-only; insert a new row instead of updating', tg_table_name
    using errcode = 'check_violation';
end
$$;

revoke execute on all functions in schema trace_util from public;
grant execute on function
  trace_util.valid_partial_date(integer, integer, public.trace_date_precision),
  trace_util.month_lo(integer, integer, public.trace_date_precision),
  trace_util.month_hi(integer, integer, public.trace_date_precision),
  trace_util.feasible_period(integer, integer, public.trace_date_precision, integer, integer, public.trace_date_precision),
  trace_util.month_index_to_date(integer),
  trace_util.collapse_consecutive(text[]),
  trace_util.subarray_position(text[], text[])
to authenticated, service_role;
grant execute on function trace_util.set_updated_at(), trace_util.prevent_update() to authenticated, service_role;
