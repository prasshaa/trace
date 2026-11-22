-- Trace data foundation v2.13.0: exclude a user's own contribution from their own reference evidence.
--
-- A contributor's de-identified reference profile must not count as independent evidence for that same user's
-- pathways or comparisons (circular evidence). The link between an app user and their contributed reference profile
-- exists only in trace_private (admin-only), so the backend cannot exclude the right record without help.
--
-- trace_contrib.my_reference_profile_ids() returns the reference profile ids of the CALLER's own contributions
-- (request identity; owner role only). The backend uses them server-side to drop those records before route evidence,
-- counts, fit and comparison statistics are computed. They are never returned by the API. Nothing else changes: no
-- table, no new identity link in trace_ref, no grant on trace_private, and other users still see the contribution as
-- independent evidence. Withdrawn contributions have no profile (it is deleted on withdrawal), so nothing is returned
-- for them; a contribution held for review has no profile yet either.

create function trace_contrib.my_reference_profile_ids()
returns setof text
language sql
stable
security definer
set search_path = ''
as $$
  select c.profile_id
    from trace_private.contributions c
    join trace_private.contributors k on k.contributor_id = c.contributor_id
   where k.user_id = trace_review.request_uid()
     and trace_review.request_uid() is not null
     and c.profile_id is not null
$$;

revoke all on function trace_contrib.my_reference_profile_ids() from public, anon, authenticated, service_role;
grant execute on function trace_contrib.my_reference_profile_ids() to trace_app_user;
