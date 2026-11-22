-- Trace data foundation v2.9.0: a resume that needed manual review keeps needing review for contribution.
--
-- An ambiguity found while de-identifying the upload (for example the owner's surname inside an employer name) is
-- resolved by a reviewer for the private profile. At contribution time the owner's name is no longer known (it was
-- removed, and an email/password account has none), so the same ambiguity could not be detected again. The context
-- now reports requires_review for such profiles; the processor then quarantines the contribution until a reviewer
-- approves its exact content. No identity is stored to achieve this.

drop function trace_contrib.contribution_context(uuid);
create function trace_contrib.contribution_context(p_user uuid)
returns table (consent_active boolean, candidate_profile_id uuid, is_fixture_profile boolean, has_admitted_resume boolean,
               approved_record_sha256 text[], requires_review boolean)
language sql
stable
security definer
set search_path = ''
as $$
  select trace_private.contribution_consent_active(p_user),
         p.candidate_profile_id, coalesce(p.is_test_fixture or p.fixture_profile_id is not null, false),
         exists (select 1 from trace_ingest.deidentified_resume_admissions a
                  where a.owner_user_id = p_user and a.candidate_profile_id = p.candidate_profile_id),
         coalesce((select array_agg(record_sha256) from trace_private.contribution_review_approvals where user_id = p_user), '{}'),
         exists (select 1 from trace_private.resume_quarantine q
                  where q.owner_user_id = p_user and q.candidate_profile_id = p.candidate_profile_id and q.status = 'approved')
    from (select p_user as u) x
    left join public.trace_candidate_profiles p on p.owner_user_id = x.u
$$;
revoke all on function trace_contrib.contribution_context(uuid) from public, anon, authenticated, service_role;
grant execute on function trace_contrib.contribution_context(uuid) to trace_app_runtime;
