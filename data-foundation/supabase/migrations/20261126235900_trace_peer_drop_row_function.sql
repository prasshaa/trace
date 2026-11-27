-- Trace data foundation v2.43.0: drop the v2.41 function that returned one attribute object per neighbour.
--
-- Apply this only after the app that calls peer_patterns(vector, jsonb, text) is deployed.
-- Until then the five-argument function must stay, or the running app loses the peer section.

drop function if exists trace_peer_cohort.peer_patterns(extensions.vector, text, text[], integer, text);
