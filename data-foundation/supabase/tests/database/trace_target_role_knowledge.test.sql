-- pgTAP tests for target-role knowledge (v2.16.0) and the pm-v1 pack (v2.17.0).
-- Run on a LOCAL Supabase stack only:  supabase test db
-- Everything runs in a transaction that is rolled back.

begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(41);

-- ------------------------------------------------------------------ 1. structure and access
select ok(to_regclass('public.trace_capability_requirements') is not null and to_regclass('public.trace_requirement_source_evidence') is not null
          and (select bool_and(relrowsecurity) from pg_class where oid in ('public.trace_capability_requirements'::regclass,
                                                                         'public.trace_requirement_source_evidence'::regclass)),
          'the knowledge tables exist with RLS');
select ok(has_table_privilege('authenticated', 'public.trace_capability_requirements', 'SELECT')
          and has_table_privilege('authenticated', 'public.trace_requirement_source_evidence', 'SELECT')
          and not has_table_privilege('authenticated', 'public.trace_capability_requirements', 'INSERT')
          and not has_table_privilege('authenticated', 'public.trace_capability_requirements', 'UPDATE')
          and not has_table_privilege('authenticated', 'public.trace_requirement_source_evidence', 'INSERT')
          and not has_table_privilege('authenticated', 'public.trace_requirement_source_evidence', 'DELETE')
          and not has_table_privilege('authenticated', 'public.trace_target_role_sources', 'UPDATE'),
          'signed-in users read knowledge and write none of it');
select ok(not has_table_privilege('anon', 'public.trace_capability_requirements', 'SELECT')
          and not has_table_privilege('anon', 'public.trace_requirement_source_evidence', 'SELECT')
          and not has_table_privilege('anon', 'public.trace_target_role_sources', 'SELECT'), 'anon reads no knowledge');
select ok(not has_function_privilege('authenticated', 'trace_util.record_posting_verification(text,text,timestamptz,integer)', 'EXECUTE')
          and not has_function_privilege('service_role', 'trace_util.record_posting_verification(text,text,timestamptz,integer)', 'EXECUTE')
          and not has_function_privilege('trace_app_user', 'trace_util.record_posting_verification(text,text,timestamptz,integer)', 'EXECUTE'),
          'only administrators record verifications');

-- ------------------------------------------------------------------ 2. the approved pm-v1 seed
select is((select count(*)::int from trace_target_role_sources where knowledge_pack = 'pm-v1'), 15, '15 approved sources');
select is((select string_agg(source_kind || '=' || n, ',' order by source_kind) from (
             select source_kind, count(*) n from trace_target_role_sources where knowledge_pack = 'pm-v1' group by 1) x),
          'employer_framework=1,framework=1,job_posting=12,national_standard=1', 'three frameworks or standards and twelve postings');
select is((select count(*)::int from trace_target_role_sources where source_url ilike '%sfia%' or publisher ilike '%sfia%'), 0,
          'SFIA is not in the pack (D16)');
select is((select source_status || '|' || coalesce(revalidate_by::text, 'none') from trace_target_role_sources where source_key = 'pm-v1:J12'),
          'expired|none', 'J12 is closed: expired, never revalidated as current (D22)');
select is((select count(*)::int from trace_target_role_sources where knowledge_pack = 'pm-v1' and source_kind = 'job_posting'
           and source_status = 'current' and revalidate_by = last_verified_at + interval '14 days'), 11,
          'eleven live postings, each due for revalidation 14 days after verification (D26)');
select is((select string_agg(entry_background_stance || '=' || n, ',' order by entry_background_stance) from (
             select entry_background_stance, count(*) n from trace_target_role_sources where knowledge_pack = 'pm-v1'
              and source_kind = 'job_posting' group by 1) x),
          'accepts_adjacent=1,excludes_specified_transitions=1,not_specified=2,requires_target_role_experience=8',
          'entry-background stances as approved (D23)');
select is((select entry_quote from trace_target_role_sources where source_key = 'pm-v1:J12'),
          'Currently a product manager — this role is not for people transitioning from consulting, project management, or program management',
          'the exact J12 requirement is preserved');
select is((select excluded_backgrounds from trace_target_role_sources where source_key = 'pm-v1:J12'),
          array['consulting', 'project management', 'program management']::text[], 'J12 names the excluded backgrounds');
select is((select accepted_backgrounds from trace_target_role_sources where source_key = 'pm-v1:J11'),
          array['data', 'analytics', 'engineering (with product exposure)']::text[], 'J11 names the accepted backgrounds');
select is((select count(*)::int from trace_target_role_sources where knowledge_pack = 'pm-v1'
           and (licence is null or attribution is null or source_url !~ '^https://')), 0, 'every source has licence, attribution and an HTTPS URL');
select is((select string_agg(requirement_class || '=' || n, ',' order by requirement_class) from (
             select requirement_class, count(*) n from trace_capability_requirements where knowledge_pack = 'pm-v1' group by 1) x),
          'context_dependent=7,core=6,differentiator=4', '17 canonical requirements: 6 core, 7 context-dependent, 4 Senior PM differentiators');
select is((select count(*)::int from trace_capability_requirements where knowledge_pack = 'pm-v1' and requirement_class = 'differentiator'
           and applies_to_level <> 'senior_pm'), 0, 'differentiators apply to Senior PM only');
select is((select count(*)::int from trace_requirement_source_evidence e join trace_capability_requirements r using (capability_requirement_id)
            where r.knowledge_pack = 'pm-v1'), 129, '129 requirement-source evidence rows');
select is((select count(*)::int from trace_capability_requirements r where knowledge_pack = 'pm-v1' and not exists (
             select 1 from trace_requirement_source_evidence e where e.capability_requirement_id = r.capability_requirement_id
              and e.stance = 'supports')), 0, 'every requirement has at least one supporting source');
select is((select count(*)::int from trace_target_role_requirements q join trace_target_role_sources s using (target_role_source_id)
            where s.knowledge_pack = 'pm-v1'), 18, 'eighteen employer-specific criteria');
select is((select count(*)::int from trace_target_role_requirements q join trace_target_role_sources s using (target_role_source_id)
            where s.knowledge_pack = 'pm-v1' and (s.source_kind <> 'job_posting' or q.extracted_by <> 'reviewer')), 0,
          'employer criteria come only from postings, reviewed (never LLM-extracted)');

-- ------------------------------------------------------------------ 3. constraints
select throws_ok($$ update trace_target_role_sources set entry_background_stance = 'not_specified' where source_key = 'pm-v1:F1' $$,
                 '23514', null, 'a framework has no entry-background stance');
select throws_ok($$ update trace_target_role_sources set entry_background_stance = null, entry_quote = null where source_key = 'pm-v1:J5' $$,
                 '23514', null, 'every pack posting has a stance');
select throws_ok($$ update trace_target_role_sources set entry_quote = repeat('x', 401) where source_key = 'pm-v1:J5' $$,
                 '23514', null, 'quotations are short');
select throws_ok($$ update trace_target_role_sources set excluded_backgrounds = array['consulting'] where source_key = 'pm-v1:J5' $$,
                 '23514', null, 'excluded backgrounds only with the excluding stance');
select throws_ok($$ update trace_capability_requirements set direct_areas = array['not_an_area'] where requirement_key = 'C1' $$,
                 'TR025', null, 'the mapping names existing taxonomy codes only');
select throws_ok($$ update trace_capability_requirements set contexts = array['platform_api'] where requirement_key = 'C1' $$,
                 '23514', null, 'core requirements carry no context');
select throws_ok($$ update trace_capability_requirements set gap_basis = 'reviewed_classified_evidence' where requirement_key = 'X6' $$,
                 '23514', null, 'a Gap needs an area basis');

-- ------------------------------------------------------------------ 4. D26 effective status
select is(trace_util.posting_effective_status('job_posting', 'current', '2026-11-30', '2026-11-29'), 'current', 'within 14 days: current');
select is(trace_util.posting_effective_status('job_posting', 'current', '2026-11-30', '2026-11-30'), 'current_check_required',
          'after 14 days: requires revalidation');
select is(trace_util.posting_effective_status('framework', 'current', '2027-11-16', '2030-01-01'), 'current',
          'frameworks keep their own policy');
select is(trace_util.posting_effective_status('job_posting', 'expired', null, '2026-11-20'), 'expired', 'a terminal status stays');

-- ------------------------------------------------------------------ 5. D26 verification outcomes
select is(trace_util.record_posting_verification('pm-v1:J5', 'inaccessible', '2026-12-05'), 'current_check_required',
          'an inaccessible source is never labelled closed');
select is((select source_status || '|' || last_verified_at::date from trace_target_role_sources where source_key = 'pm-v1:J5'),
          'current_check_required|2026-11-16', '... and its last verification is unchanged');
select is(trace_util.record_posting_verification('pm-v1:J5', 'open', '2026-12-06'), 'current', 'a confirmed-open check refreshes it');
select is((select revalidate_by::date || '|' || last_verified_at::date from trace_target_role_sources where source_key = 'pm-v1:J5'),
          '2026-12-20|2026-12-06', '... with a new verification time and validity');
select is(trace_util.record_posting_verification('pm-v1:J6', 'ambiguous', '2026-12-06'), 'current_check_required', 'ambiguous is never closed');
select is(trace_util.record_posting_verification('pm-v1:J6', 'closed', '2026-12-07'), 'expired', 'an employer-confirmed closure is terminal');
select is(trace_util.record_posting_verification('pm-v1:J6', 'inaccessible', '2026-12-08'), 'expired',
          'a later unclear check never changes a terminal status');
select throws_ok($$ select trace_util.record_posting_verification('pm-v1:J12', 'open', '2026-12-08') $$, 'TR024', null,
                 'a closed posting is not reopened: a reopened role is a new source');
select throws_ok($$ select trace_util.record_posting_verification('pm-v1:F1', 'open') $$, 'TR024', null,
                 'frameworks keep their own revalidation policy');

-- ------------------------------------------------------------------ 6. withdrawn sources are hidden with their evidence
select trace_util.record_posting_verification('pm-v1:J9', 'withdrawn', '2026-12-08');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub": "00000000-0000-4000-8000-0000000000e1", "role": "authenticated"}', true);
select ok((select count(*) from trace_target_role_sources where source_key = 'pm-v1:J9') = 0
          and (select count(*) from trace_requirement_source_evidence e join trace_target_role_sources s using (target_role_source_id)
                where s.source_key = 'pm-v1:J9') = 0
          and (select count(*) from trace_capability_requirements where knowledge_pack = 'pm-v1') = 17,
          'a withdrawn source and its evidence are hidden from users; requirements stay readable');
reset role;

select * from finish();
rollback;
