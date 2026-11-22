-- Trace Data Foundation v2 · 09 · Data API privileges and row-level security policies
--
-- Model (see docs/ACCESS_CONTROL.md for the full matrix):
--   * Start from zero: revoke every privilege that Supabase's default grants give anon and
--     authenticated on Trace tables, then grant back only what each table needs.
--   * Lookups: SELECT for anon and authenticated; no client writes.
--   * User-owned tables: the owner (auth.uid()) only. System-managed columns are excluded from
--     client INSERT/UPDATE through column privileges.
--   * Pathway results: written by service_role, read by the owner. Reference profile ids in
--     pathway evidence are hidden from clients.
--   * trace_ref and trace_util: no client access at all (migration 01).

do $$
declare
  t record;
begin
  for t in
    select c.relname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind in ('r', 'p') and c.relname like 'trace\_%'
  loop
    execute format('revoke all on table public.%I from anon, authenticated, public', t.relname);
    execute format('grant all on table public.%I to service_role', t.relname);
  end loop;
end
$$;

-- ------------------------------------------------------------------ read-only lookups
grant select on public.trace_seniority_levels, public.trace_role_families, public.trace_role_taxonomy,
                public.trace_role_aliases, public.trace_responsibility_areas, public.trace_skill_families,
                public.trace_skills, public.trace_skill_aliases, public.trace_consent_documents
  to anon, authenticated;

create policy "lookup read" on public.trace_seniority_levels for select to anon, authenticated using (true);
create policy "lookup read" on public.trace_role_families for select to anon, authenticated using (true);
create policy "lookup read" on public.trace_role_taxonomy for select to anon, authenticated using (true);
create policy "lookup read" on public.trace_role_aliases for select to anon, authenticated using (true);
create policy "lookup read" on public.trace_responsibility_areas for select to anon, authenticated using (true);
create policy "lookup read" on public.trace_skill_families for select to anon, authenticated using (true);
create policy "lookup read" on public.trace_skills for select to anon, authenticated using (true);
create policy "lookup read" on public.trace_skill_aliases for select to anon, authenticated using (true);
create policy "consent documents read" on public.trace_consent_documents for select to anon, authenticated using (true);

grant select on public.trace_target_role_sources, public.trace_target_role_requirements to authenticated;
create policy "target role sources read" on public.trace_target_role_sources
  for select to authenticated using (source_status <> 'withdrawn');
create policy "target role requirements read" on public.trace_target_role_requirements
  for select to authenticated using (
    exists (select 1 from public.trace_target_role_sources s
            where s.target_role_source_id = trace_target_role_requirements.target_role_source_id
              and s.source_status <> 'withdrawn'));

-- ------------------------------------------------------------------ consent events (append-only)
grant select on public.trace_consent_events to authenticated;
grant insert (purpose, status, consent_version) on public.trace_consent_events to authenticated;
create policy "consent events owner read" on public.trace_consent_events
  for select to authenticated using (user_id = (select auth.uid()));
create policy "consent events owner append" on public.trace_consent_events
  for insert to authenticated with check (
    user_id = (select auth.uid())
    and source = 'in_app'
    and exists (select 1 from public.trace_consent_documents d
                where d.consent_version = trace_consent_events.consent_version
                  and d.purpose = trace_consent_events.purpose
                  and d.legal_review_status = 'approved'
                  and d.retired_at is null));

-- ------------------------------------------------------------------ candidate profile and resumes
grant select, delete on public.trace_candidate_profiles to authenticated;
grant insert (country_code, headline, resume_as_of_year, resume_as_of_month, resume_as_of_precision)
  on public.trace_candidate_profiles to authenticated;
grant update (country_code, headline, resume_as_of_year, resume_as_of_month, resume_as_of_precision,
              confirmation_status, confirmed_at)
  on public.trace_candidate_profiles to authenticated;
create policy "candidate profiles owner select" on public.trace_candidate_profiles
  for select to authenticated using (owner_user_id = (select auth.uid()));
create policy "candidate profiles owner insert" on public.trace_candidate_profiles
  for insert to authenticated with check (owner_user_id = (select auth.uid()));
create policy "candidate profiles owner update" on public.trace_candidate_profiles
  for update to authenticated using (owner_user_id = (select auth.uid())) with check (owner_user_id = (select auth.uid()));
create policy "candidate profiles owner delete" on public.trace_candidate_profiles
  for delete to authenticated using (owner_user_id = (select auth.uid()));

grant select, delete on public.trace_resume_documents to authenticated;
grant insert (candidate_profile_id, storage_object_path, original_filename, mime_type, byte_size, content_sha256)
  on public.trace_resume_documents to authenticated;
grant update (candidate_profile_id) on public.trace_resume_documents to authenticated;
create policy "resume documents owner select" on public.trace_resume_documents
  for select to authenticated using (owner_user_id = (select auth.uid()));
create policy "resume documents owner insert" on public.trace_resume_documents
  for insert to authenticated with check (owner_user_id = (select auth.uid()));
create policy "resume documents owner update" on public.trace_resume_documents
  for update to authenticated using (owner_user_id = (select auth.uid())) with check (owner_user_id = (select auth.uid()));
create policy "resume documents owner delete" on public.trace_resume_documents
  for delete to authenticated using (owner_user_id = (select auth.uid()));

grant select on public.trace_extraction_runs to authenticated;
create policy "extraction runs owner select" on public.trace_extraction_runs
  for select to authenticated using (owner_user_id = (select auth.uid()));

-- ------------------------------------------------------------------ structured career history
-- The user can read and correct every part of their own history.
grant select, insert, update, delete on
  public.trace_candidate_employers, public.trace_candidate_employments, public.trace_candidate_education,
  public.trace_candidate_career_breaks, public.trace_candidate_evidence, public.trace_candidate_skills
  to authenticated;

create policy "candidate employers owner all" on public.trace_candidate_employers
  for all to authenticated using (owner_user_id = (select auth.uid())) with check (owner_user_id = (select auth.uid()));
create policy "candidate employments owner all" on public.trace_candidate_employments
  for all to authenticated using (owner_user_id = (select auth.uid())) with check (owner_user_id = (select auth.uid()));
create policy "candidate education owner all" on public.trace_candidate_education
  for all to authenticated using (owner_user_id = (select auth.uid())) with check (owner_user_id = (select auth.uid()));
create policy "candidate career breaks owner all" on public.trace_candidate_career_breaks
  for all to authenticated using (owner_user_id = (select auth.uid())) with check (owner_user_id = (select auth.uid()));
create policy "candidate evidence owner all" on public.trace_candidate_evidence
  for all to authenticated using (owner_user_id = (select auth.uid())) with check (owner_user_id = (select auth.uid()));
create policy "candidate skills owner all" on public.trace_candidate_skills
  for all to authenticated using (owner_user_id = (select auth.uid())) with check (owner_user_id = (select auth.uid()));

-- ------------------------------------------------------------------ pathways (backend-written, owner-read)
grant select, delete on public.trace_pathway_runs to authenticated;
create policy "pathway runs owner select" on public.trace_pathway_runs
  for select to authenticated using (owner_user_id = (select auth.uid()));
create policy "pathway runs owner delete" on public.trace_pathway_runs
  for delete to authenticated using (owner_user_id = (select auth.uid()));

grant select on public.trace_pathway_recommendations, public.trace_pathway_route_steps to authenticated;
create policy "pathway recommendations owner select" on public.trace_pathway_recommendations
  for select to authenticated using (owner_user_id = (select auth.uid()));
create policy "pathway route steps owner select" on public.trace_pathway_route_steps
  for select to authenticated using (owner_user_id = (select auth.uid()));

-- reference_profile_id and reference_dataset_class are deliberately not granted.
grant select (pathway_evidence_id, recommendation_id, owner_user_id, data_mode, evidence_type,
              target_role_source_id, evidence_summary, created_at)
  on public.trace_pathway_evidence to authenticated;
create policy "pathway evidence owner select" on public.trace_pathway_evidence
  for select to authenticated using (owner_user_id = (select auth.uid()));

-- ------------------------------------------------------------------ agent conversations
grant select, delete on public.trace_agent_threads to authenticated;
grant insert (candidate_profile_id) on public.trace_agent_threads to authenticated;
create policy "agent threads owner select" on public.trace_agent_threads
  for select to authenticated using (owner_user_id = (select auth.uid()));
create policy "agent threads owner insert" on public.trace_agent_threads
  for insert to authenticated with check (owner_user_id = (select auth.uid()));
create policy "agent threads owner delete" on public.trace_agent_threads
  for delete to authenticated using (owner_user_id = (select auth.uid()));

grant select on public.trace_agent_messages to authenticated;
grant insert (thread_id, role, content_redacted) on public.trace_agent_messages to authenticated;
create policy "agent messages owner select" on public.trace_agent_messages
  for select to authenticated using (owner_user_id = (select auth.uid()));
-- Clients may only add their own 'user' messages; assistant/tool/system messages come from the backend.
create policy "agent messages owner insert user role" on public.trace_agent_messages
  for insert to authenticated with check (owner_user_id = (select auth.uid()) and role = 'user');
