-- Rollback v2.39.0. Drops the peer cohort schema and the aggregate function. No user data lives here.

drop function if exists trace_peer_cohort.peer_patterns(extensions.vector, text, text[], integer, text);
drop schema if exists trace_peer_cohort cascade;
