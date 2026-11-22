-- Trace data foundation v2.8.0: shared, backend-only rate limiting.
--
-- Fixed-window counters keyed by "<namespace>:<bucket>:<user|ip>:<value>". trace_ops.consume_rate_limit checks every
-- key's window and records the request under all of them only if none is full, atomically: each key's row is locked
-- (in sorted key order, so concurrent requests cannot deadlock), and a refused request is not counted. It returns 0
-- when accepted, otherwise the seconds until the fullest window resets (Retry-After).
-- Counters expire: rows older than two days are deleted opportunistically.
-- Only trace_app_runtime can use it. The schema is not exposed through the Data API and no client role has any
-- privilege, so counters cannot be read or reset by users. A fixed window allows a burst of up to twice the limit
-- across a window boundary; that is accepted for this API.

create schema trace_ops;
revoke all on schema trace_ops from public, anon, authenticated, service_role;
grant usage on schema trace_ops to trace_app_runtime;

create table trace_ops.rate_limit_counters (
  key text not null check (char_length(key) between 3 and 200),
  window_start timestamptz not null,
  window_seconds integer not null check (window_seconds between 1 and 86400),
  hits integer not null default 0 check (hits >= 0),
  primary key (key, window_start)
);
alter table trace_ops.rate_limit_counters enable row level security;
revoke all on trace_ops.rate_limit_counters from public, anon, authenticated, service_role;
grant select, insert, update, delete on trace_ops.rate_limit_counters to trace_app_runtime;
create policy "runtime only" on trace_ops.rate_limit_counters for all to trace_app_runtime using (true) with check (true);

create function trace_ops.consume_rate_limit(p_keys text[], p_limits integer[], p_window_seconds integer[])
returns integer
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_now timestamptz := clock_timestamp();
  v_order integer[];
  v_i integer;
  v_start timestamptz;
  v_hits integer;
  v_wait integer := 0;
begin
  if cardinality(p_keys) is distinct from cardinality(p_limits) or cardinality(p_keys) is distinct from cardinality(p_window_seconds)
     or cardinality(p_keys) = 0 then
    raise exception using errcode = 'invalid_parameter_value', message = 'keys, limits and windows must align';
  end if;
  select array_agg(i order by p_keys[i]) into v_order from generate_subscripts(p_keys, 1) as i;
  foreach v_i in array v_order loop
    if p_limits[v_i] < 1 or p_window_seconds[v_i] < 1 then
      raise exception using errcode = 'invalid_parameter_value', message = 'limits and windows must be positive';
    end if;
    v_start := to_timestamp(floor(extract(epoch from v_now) / p_window_seconds[v_i]) * p_window_seconds[v_i]);
    insert into trace_ops.rate_limit_counters as c (key, window_start, window_seconds, hits)
    values (p_keys[v_i], v_start, p_window_seconds[v_i], 0)
    on conflict (key, window_start) do nothing;
    select c.hits into v_hits from trace_ops.rate_limit_counters c
     where c.key = p_keys[v_i] and c.window_start = v_start for update;
    if v_hits >= p_limits[v_i] then
      v_wait := greatest(v_wait, ceil(extract(epoch from (v_start + make_interval(secs => p_window_seconds[v_i]) - v_now)))::integer);
    end if;
  end loop;
  if v_wait > 0 then
    return greatest(v_wait, 1);
  end if;
  foreach v_i in array v_order loop
    update trace_ops.rate_limit_counters c set hits = c.hits + 1
     where c.key = p_keys[v_i]
       and c.window_start = to_timestamp(floor(extract(epoch from v_now) / p_window_seconds[v_i]) * p_window_seconds[v_i]);
  end loop;
  if random() < 0.01 then
    delete from trace_ops.rate_limit_counters c where c.window_start < v_now - interval '2 days';
  end if;
  return 0;
end;
$$;
revoke all on function trace_ops.consume_rate_limit(text[], integer[], integer[]) from public, anon, authenticated, service_role;
grant execute on function trace_ops.consume_rate_limit(text[], integer[], integer[]) to trace_app_runtime;
