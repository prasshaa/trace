-- Compensation for migration 20261113120000_trace_rate_limits (v2.8.0). NOT a migration; never applied automatically.
--
--   1. Deploy a backend that does not use the shared limiter first (TRACE_RATE_LIMITS_ENABLED=0, or a build before
--      v2.8.0). With the limiter table gone, rate-limited requests fail closed (503) by design.
--   2. Run as postgres: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>   (it carries its own BEGIN/COMMIT)
--   3. supabase migration repair 20261113120000 --status reverted   (add --local for the local stack)
--
-- Data: rate-limit counters are discarded (they are transient).
begin;
drop function trace_ops.consume_rate_limit(text[], integer[], integer[]);
drop table trace_ops.rate_limit_counters;
drop schema trace_ops;
commit;
