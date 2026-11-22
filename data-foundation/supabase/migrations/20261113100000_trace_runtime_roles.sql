-- Trace data foundation v2.8.0: restricted backend runtime identity and no direct client writes.
--
-- Before: the backend logged in as the table owner (BYPASSRLS) and switched to `authenticated` for owner sessions;
-- `authenticated` (also PostgREST's role for signed-in users) held write grants on private tables and could call the
-- review and ingestion writer functions.
--
-- After:
--   trace_app_runtime       LOGIN. The only database login the API uses. Not a superuser, no BYPASSRLS, owns
--                           nothing, cannot create roles or databases. It can SET ROLE trace_app_user (without
--                           inheriting it) and inherits only trace_reference_reader. Its password is set outside
--                           migrations (local: backend/scripts/provision_runtime_role.py; hosted: secret store).
--   trace_app_user          NOLOGIN. The per-request owner role (owner_session): a member of `authenticated`, so the
--                           existing owner policies and auth.uid() apply unchanged; it alone holds the candidate
--                           write grants and the writer entry points. PostgREST's authenticator is NOT a member, so no
--                           Data API request can use it.
--   trace_reference_reader  NOLOGIN. Read-only access to the reference cohort, lookups and the fixture allowlist.
--   authenticated           Keeps SELECT on its own rows (RLS); loses every INSERT, UPDATE and DELETE on private
--                           tables and the writer entry points. Supabase Auth from the browser is unaffected.
--
-- Fixture designation no longer needs the table owner: trace_ingest.designate_fixture (definer, owned by the existing
-- ingest writer, scoped by the request identity) is executable by trace_app_runtime only.
-- Requires PostgreSQL 16+ (GRANT ... WITH INHERIT / SET options).

do $$
begin
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'trace_app_user') then
    create role trace_app_user nologin inherit nosuperuser nocreatedb nocreaterole nobypassrls noreplication;
  end if;
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'trace_reference_reader') then
    create role trace_reference_reader nologin noinherit nosuperuser nocreatedb nocreaterole nobypassrls noreplication;
  end if;
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'trace_app_runtime') then
    create role trace_app_runtime login noinherit nosuperuser nocreatedb nocreaterole nobypassrls noreplication connection limit 60;
  end if;
end
$$;
alter role trace_app_user nologin inherit nocreatedb nocreaterole nobypassrls;
alter role trace_reference_reader nologin noinherit nocreatedb nocreaterole nobypassrls;
alter role trace_app_runtime login noinherit nocreatedb nocreaterole nobypassrls;
-- Never let a request identity outlive its statement timeout budget.
alter role trace_app_runtime set statement_timeout = '15s';
alter role trace_app_runtime set idle_in_transaction_session_timeout = '30s';

grant authenticated to trace_app_user with inherit true, set false;     -- policies and reads apply; no switching into it
grant trace_app_user to trace_app_runtime with inherit false, set true;
grant trace_reference_reader to trace_app_runtime with inherit true, set false;
-- The admin / migration identity may act as the owner role too (administration and tests); it gains nothing new.
grant trace_app_user to postgres with inherit false, set true;

-- --------------------------------------------------------------------------- writes move from authenticated
grant usage on schema trace_review, trace_util, trace_ingest to trace_app_user;

grant delete on public.trace_agent_threads, public.trace_candidate_career_activities, public.trace_candidate_career_breaks,
  public.trace_candidate_education, public.trace_candidate_employers, public.trace_candidate_employments,
  public.trace_candidate_evidence, public.trace_candidate_profiles, public.trace_candidate_skills, public.trace_pathway_runs,
  public.trace_resume_documents to trace_app_user;

grant insert (content_redacted, role, thread_id) on public.trace_agent_messages to trace_app_user;
grant insert (candidate_profile_id) on public.trace_agent_threads to trace_app_user;
grant insert (activity_type, candidate_profile_id, contribution_raw, credential_identifier_raw, description_raw, end_month,
  end_precision, end_year, expiry_month, expiry_precision, expiry_year, link_url, metadata, organisation_raw, outcome_raw,
  owner_user_id, related_employment_id, start_month, start_precision, start_year, status, title_raw)
  on public.trace_candidate_career_activities to trace_app_user;
grant update (activity_type, contribution_raw, credential_identifier_raw, description_raw, end_month, end_precision, end_year,
  expiry_month, expiry_precision, expiry_year, link_url, metadata, organisation_raw, outcome_raw, related_employment_id,
  start_month, start_precision, start_year, status, title_raw)
  on public.trace_candidate_career_activities to trace_app_user;
grant insert (break_reason, candidate_profile_id, end_month, end_precision, end_year, note, owner_user_id, start_month,
  start_precision, start_year) on public.trace_candidate_career_breaks to trace_app_user;
grant update (break_reason, end_month, end_precision, end_year, note, start_month, start_precision, start_year)
  on public.trace_candidate_career_breaks to trace_app_user;
grant insert (candidate_profile_id, completion_status, end_month, end_precision, end_year, field_of_study, institution_name,
  notes, owner_user_id, qualification_level, qualification_title_raw, start_month, start_precision, start_year, study_mode)
  on public.trace_candidate_education to trace_app_user;
grant update (completion_status, end_month, end_precision, end_year, field_of_study, institution_name, notes,
  qualification_level, qualification_title_raw, start_month, start_precision, start_year, study_mode)
  on public.trace_candidate_education to trace_app_user;
grant insert (business_model, candidate_profile_id, company_model, company_size_band, employer_name, industry_raw,
  industry_sector, owner_user_id) on public.trace_candidate_employers to trace_app_user;
grant update (business_model, company_model, company_size_band, employer_name, industry_raw, industry_sector)
  on public.trace_candidate_employers to trace_app_user;
grant insert (candidate_employer_id, candidate_profile_id, classification_note, counts_as_full_time_experience,
  employment_type, end_month, end_precision, end_year, is_current_as_stated, owner_user_id, primary_career_sequence,
  sequence_index, start_month, start_precision, start_year, title_raw) on public.trace_candidate_employments to trace_app_user;
grant update (classification_note, counts_as_full_time_experience, employment_type, end_month, end_precision, end_year,
  is_current_as_stated, primary_career_sequence, sequence_index, start_month, start_precision, start_year, title_raw)
  on public.trace_candidate_employments to trace_app_user;
grant insert (candidate_activity_id, candidate_employment_id, candidate_profile_id, evidence_text, evidence_type,
  owner_user_id, responsibility_area) on public.trace_candidate_evidence to trace_app_user;
grant update (candidate_activity_id, candidate_employment_id, evidence_text, evidence_type, responsibility_area)
  on public.trace_candidate_evidence to trace_app_user;
grant insert (country_code, headline, owner_user_id, resume_as_of_month, resume_as_of_precision, resume_as_of_year)
  on public.trace_candidate_profiles to trace_app_user;
grant update (country_code, headline, resume_as_of_month, resume_as_of_precision, resume_as_of_year)
  on public.trace_candidate_profiles to trace_app_user;
grant insert (candidate_activity_id, candidate_education_id, candidate_employment_id, candidate_evidence_id,
  candidate_profile_id, owner_user_id, skill_id, skill_text_raw, stated_proficiency_raw)
  on public.trace_candidate_skills to trace_app_user;
grant update (candidate_activity_id, candidate_education_id, candidate_employment_id, candidate_evidence_id, skill_id,
  skill_text_raw, stated_proficiency_raw) on public.trace_candidate_skills to trace_app_user;
grant insert (consent_version, purpose, status) on public.trace_consent_events to trace_app_user;
grant insert (byte_size, candidate_profile_id, content_sha256, mime_type, original_filename, storage_object_path)
  on public.trace_resume_documents to trace_app_user;
grant update (candidate_profile_id) on public.trace_resume_documents to trace_app_user;


-- The Data API role keeps reads of its own rows only.
revoke insert, update, delete, truncate, references, trigger on
  public.trace_agent_messages, public.trace_agent_threads, public.trace_candidate_career_activities,
  public.trace_candidate_career_breaks, public.trace_candidate_education, public.trace_candidate_employers,
  public.trace_candidate_employments, public.trace_candidate_evidence, public.trace_candidate_profiles,
  public.trace_candidate_skills, public.trace_candidate_corrections, public.trace_consent_events,
  public.trace_extraction_runs, public.trace_pathway_runs, public.trace_pathway_recommendations,
  public.trace_pathway_route_steps, public.trace_pathway_evidence, public.trace_resume_documents
  from anon, authenticated;
-- The writer entry points are owned by the writer roles: their grants can only be changed by those owners (a grant or
-- revoke by postgres would silently do nothing). Temporary SET membership, removed immediately (the M2 procedure).
grant trace_review_writer to postgres with set true, inherit false;
grant trace_ingest_writer to postgres with set true, inherit false;
set role trace_review_writer;
grant execute on function
  trace_review.apply_alias_mapping(uuid, uuid, text, text),
  trace_review.choose_employment_role(uuid, uuid, text, text, text[]),
  trace_review.classify_evidence_areas(uuid, uuid, text[], text),
  trace_review.correct_record(text, uuid, uuid, jsonb),
  trace_review.reassign_employment(uuid, uuid, uuid),
  trace_review.review_record(text, uuid, uuid, text)
  to trace_app_user;
revoke execute on function
  trace_review.apply_alias_mapping(uuid, uuid, text, text),
  trace_review.choose_employment_role(uuid, uuid, text, text, text[]),
  trace_review.classify_evidence_areas(uuid, uuid, text[], text),
  trace_review.correct_record(text, uuid, uuid, jsonb),
  trace_review.reassign_employment(uuid, uuid, uuid),
  trace_review.review_record(text, uuid, uuid, text)
  from public, anon, authenticated, service_role;
reset role;
set role trace_ingest_writer;
grant execute on function
  trace_ingest.fail_fixture_extraction(uuid, text),
  trace_ingest.ingest_fixture_extraction(uuid, jsonb),
  trace_ingest.start_fixture_extraction(uuid, uuid, text)
  to trace_app_user;
revoke execute on function
  trace_ingest.fail_fixture_extraction(uuid, text),
  trace_ingest.ingest_fixture_extraction(uuid, jsonb),
  trace_ingest.start_fixture_extraction(uuid, uuid, text)
  from public, anon, authenticated, service_role;
reset role;
revoke trace_review_writer from postgres;
revoke trace_ingest_writer from postgres;
revoke usage on schema trace_review, trace_ingest from authenticated;

-- The app never stores resume bytes; signed-in users no longer upload objects directly (read and delete of their own
-- folder stay).
drop policy if exists "trace resumes owner upload" on storage.objects;

-- --------------------------------------------------------------------------- reference reader (read-only)
grant usage on schema trace_ref, trace_ingest, trace_util to trace_reference_reader;
grant select on trace_ref.profiles, trace_ref.employers, trace_ref.employments, trace_ref.education, trace_ref.career_breaks,
  trace_ref.experience_evidence, trace_ref.profile_skills, trace_ref.analytics_settings, trace_ref.data_sources
  to trace_reference_reader;
grant select on trace_ref.v_career_episode_paths, trace_ref.v_career_episode_stints, trace_ref.v_career_paths,
  trace_ref.v_career_sequence, trace_ref.v_career_stints, trace_ref.v_career_timeline, trace_ref.v_employment_classified,
  trace_ref.v_episode_transitions, trace_ref.v_mode_profiles, trace_ref.v_profile_sequence_integrity,
  trace_ref.v_role_transitions, trace_ref.v_settings, trace_ref.v_eligible_profiles
  to trace_reference_reader;
grant execute on function trace_ref.episode_route_observations(text[], public.trace_data_mode),
  trace_ref.episode_route_timeline_summary(text[], public.trace_data_mode),
  trace_ref.route_observations(text[], public.trace_data_mode),
  trace_ref.route_timeline_summary(text[], public.trace_data_mode),
  trace_ref.assert_valid_episode_route(text[])
  to trace_reference_reader;
grant select on public.trace_seniority_levels, public.trace_role_families, public.trace_role_taxonomy, public.trace_role_aliases,
  public.trace_responsibility_areas, public.trace_skill_families, public.trace_skills, public.trace_skill_aliases
  to trace_reference_reader;
grant select on trace_ingest.approved_fixture_resumes to trace_reference_reader;
grant execute on function trace_util.collapse_consecutive(text[]),
  trace_util.month_hi(integer, integer, public.trace_date_precision),
  trace_util.month_index_to_date(integer),
  trace_util.month_lo(integer, integer, public.trace_date_precision),
  trace_util.subarray_position(text[], text[]),
  trace_util.valid_partial_date(integer, integer, public.trace_date_precision),
  trace_util.feasible_period(integer, integer, public.trace_date_precision, integer, integer, public.trace_date_precision)
  to trace_reference_reader;

create policy "reference reader read" on trace_ref.profiles for select to trace_reference_reader using (true);
create policy "reference reader read" on trace_ref.employers for select to trace_reference_reader using (true);
create policy "reference reader read" on trace_ref.employments for select to trace_reference_reader using (true);
create policy "reference reader read" on trace_ref.education for select to trace_reference_reader using (true);
create policy "reference reader read" on trace_ref.career_breaks for select to trace_reference_reader using (true);
create policy "reference reader read" on trace_ref.experience_evidence for select to trace_reference_reader using (true);
create policy "reference reader read" on trace_ref.profile_skills for select to trace_reference_reader using (true);
create policy "reference reader read" on trace_ref.analytics_settings for select to trace_reference_reader using (true);
create policy "reference reader read" on trace_ref.data_sources for select to trace_reference_reader using (true);
create policy "lookup reference reader read" on public.trace_seniority_levels for select to trace_reference_reader using (true);
create policy "lookup reference reader read" on public.trace_role_families for select to trace_reference_reader using (true);
create policy "lookup reference reader read" on public.trace_role_taxonomy for select to trace_reference_reader using (true);
create policy "lookup reference reader read" on public.trace_role_aliases for select to trace_reference_reader using (true);
create policy "lookup reference reader read" on public.trace_responsibility_areas for select to trace_reference_reader using (true);
create policy "lookup reference reader read" on public.trace_skill_families for select to trace_reference_reader using (true);
create policy "lookup reference reader read" on public.trace_skills for select to trace_reference_reader using (true);
create policy "lookup reference reader read" on public.trace_skill_aliases for select to trace_reference_reader using (true);
create policy "approved fixture resumes reference reader select" on trace_ingest.approved_fixture_resumes
  for select to trace_reference_reader using (true);

-- --------------------------------------------------------------------------- fixture designation without the owner
grant update (is_test_fixture, fixture_profile_id) on public.trace_candidate_profiles to trace_ingest_writer;
grant update (is_test_fixture) on public.trace_resume_documents to trace_ingest_writer;

create function trace_ingest.designate_fixture(p_candidate_profile_id uuid, p_resume_id uuid, p_fixture_profile_id text,
                                               p_pdf_sha256 text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  v_profiles integer;
  v_resumes integer;
begin
  if v_uid is null then
    raise exception using errcode = 'TR021', message = 'fixture designation needs the request identity';
  end if;
  if not exists (select 1 from trace_ingest.approved_fixture_resumes a
                 where a.fixture_profile_id = p_fixture_profile_id and a.pdf_sha256 = p_pdf_sha256) then
    raise exception using errcode = 'TR021', message = 'not an approved synthetic fixture';
  end if;
  update public.trace_candidate_profiles p
     set is_test_fixture = true, fixture_profile_id = p_fixture_profile_id
   where p.candidate_profile_id = p_candidate_profile_id and p.owner_user_id = v_uid
     and (p.fixture_profile_id is null or p.fixture_profile_id = p_fixture_profile_id);
  get diagnostics v_profiles = row_count;
  update public.trace_resume_documents r
     set is_test_fixture = true
   where r.resume_id = p_resume_id and r.owner_user_id = v_uid and r.candidate_profile_id = p_candidate_profile_id
     and r.content_sha256 = p_pdf_sha256 and r.deleted_at is null;
  get diagnostics v_resumes = row_count;
  if v_profiles <> 1 or v_resumes <> 1 then
    raise exception using errcode = 'TR021', message = 'the profile or resume could not be designated';
  end if;
end;
$$;
-- Privileges BEFORE the ownership transfer: afterwards the default PUBLIC execute privilege belongs to the new owner,
-- and postgres could no longer remove it.
revoke all on function trace_ingest.designate_fixture(uuid, uuid, text, text) from public, anon, authenticated, service_role, trace_app_user;
grant execute on function trace_ingest.designate_fixture(uuid, uuid, text, text) to trace_app_runtime;
-- Ownership transfer (the M2 procedure): temporary grants, removed immediately.
grant trace_ingest_writer to postgres with set true, inherit false;
grant create on schema trace_ingest to trace_ingest_writer;
alter function trace_ingest.designate_fixture(uuid, uuid, text, text) owner to trace_ingest_writer;
revoke create on schema trace_ingest from trace_ingest_writer;
revoke trace_ingest_writer from postgres;
grant usage on schema trace_ingest, trace_review to trace_app_runtime;
grant execute on function trace_review.request_uid() to trace_app_runtime;
