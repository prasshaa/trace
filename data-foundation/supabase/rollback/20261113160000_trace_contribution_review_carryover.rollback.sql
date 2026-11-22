-- Compensation for migration 20261113160000_trace_contribution_review_carryover (v2.9.0). NOT a migration.
-- Deploy a backend that does not read requires_review first. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261113160000 --status reverted
-- Effect: profiles whose upload needed manual review no longer force contribution review.
begin;
drop function trace_contrib.contribution_context(uuid);
create function trace_contrib.contribution_context(p_user uuid)
returns table (consent_active boolean, candidate_profile_id uuid, is_fixture_profile boolean, has_admitted_resume boolean,
               approved_record_sha256 text[])
language sql
stable
security definer
set search_path = ''
as $$
  select trace_private.contribution_consent_active(p_user),
         p.candidate_profile_id, coalesce(p.is_test_fixture or p.fixture_profile_id is not null, false),
         exists (select 1 from trace_ingest.deidentified_resume_admissions a
                  where a.owner_user_id = p_user and a.candidate_profile_id = p.candidate_profile_id),
         coalesce((select array_agg(record_sha256) from trace_private.contribution_review_approvals where user_id = p_user), '{}')
    from (select p_user as u) x
    left join public.trace_candidate_profiles p on p.owner_user_id = x.u
$$;
revoke all on function trace_contrib.contribution_context(uuid) from public, anon, authenticated, service_role;
grant execute on function trace_contrib.contribution_context(uuid) to trace_app_runtime;
commit;
