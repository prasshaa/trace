-- Compensation for migration 20261113100000_trace_runtime_roles (v2.8.0). NOT a migration.
--
--   1. Deploy a backend before v2.8.0 FIRST: this script gives the write grants back to `authenticated` and removes
--      the runtime login, so a v2.8.0 backend would stop working (it refuses a privileged login and needs
--      trace_app_user). This RE-OPENS direct client writes through the Data API: only do it to restore v2.7.0.
--   2. Run as postgres: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>   (it carries its own BEGIN/COMMIT)
--   3. supabase migration repair 20261113100000 --status reverted   (add --local for the local stack)
--   The roles are cluster-wide; they are dropped here only when nothing else depends on them.
begin;
create policy "trace resumes owner upload" on storage.objects
  for insert to authenticated
  with check (bucket_id = 'trace-resumes' and (storage.foldername(name))[1] = (select auth.uid())::text);

grant usage on schema trace_review, trace_ingest to authenticated;
grant delete on public.trace_agent_threads, public.trace_candidate_career_activities, public.trace_candidate_career_breaks,
  public.trace_candidate_education, public.trace_candidate_employers, public.trace_candidate_employments,
  public.trace_candidate_evidence, public.trace_candidate_profiles, public.trace_candidate_skills, public.trace_pathway_runs,
  public.trace_resume_documents to authenticated;
-- Column grants: identical to those listed in the migration, to `authenticated`.
grant insert (content_redacted, role, thread_id) on public.trace_agent_messages to authenticated;
grant insert (candidate_profile_id) on public.trace_agent_threads to authenticated;
grant insert (activity_type, candidate_profile_id, contribution_raw, credential_identifier_raw, description_raw, end_month,
  end_precision, end_year, expiry_month, expiry_precision, expiry_year, link_url, metadata, organisation_raw, outcome_raw,
  owner_user_id, related_employment_id, start_month, start_precision, start_year, status, title_raw)
  on public.trace_candidate_career_activities to authenticated;
grant update (activity_type, contribution_raw, credential_identifier_raw, description_raw, end_month, end_precision, end_year,
  expiry_month, expiry_precision, expiry_year, link_url, metadata, organisation_raw, outcome_raw, related_employment_id,
  start_month, start_precision, start_year, status, title_raw) on public.trace_candidate_career_activities to authenticated;
grant insert (break_reason, candidate_profile_id, end_month, end_precision, end_year, note, owner_user_id, start_month,
  start_precision, start_year) on public.trace_candidate_career_breaks to authenticated;
grant update (break_reason, end_month, end_precision, end_year, note, start_month, start_precision, start_year)
  on public.trace_candidate_career_breaks to authenticated;
grant insert (candidate_profile_id, completion_status, end_month, end_precision, end_year, field_of_study, institution_name,
  notes, owner_user_id, qualification_level, qualification_title_raw, start_month, start_precision, start_year, study_mode)
  on public.trace_candidate_education to authenticated;
grant update (completion_status, end_month, end_precision, end_year, field_of_study, institution_name, notes,
  qualification_level, qualification_title_raw, start_month, start_precision, start_year, study_mode)
  on public.trace_candidate_education to authenticated;
grant insert (business_model, candidate_profile_id, company_model, company_size_band, employer_name, industry_raw,
  industry_sector, owner_user_id) on public.trace_candidate_employers to authenticated;
grant update (business_model, company_model, company_size_band, employer_name, industry_raw, industry_sector)
  on public.trace_candidate_employers to authenticated;
grant insert (candidate_employer_id, candidate_profile_id, classification_note, counts_as_full_time_experience,
  employment_type, end_month, end_precision, end_year, is_current_as_stated, owner_user_id, primary_career_sequence,
  sequence_index, start_month, start_precision, start_year, title_raw) on public.trace_candidate_employments to authenticated;
grant update (classification_note, counts_as_full_time_experience, employment_type, end_month, end_precision, end_year,
  is_current_as_stated, primary_career_sequence, sequence_index, start_month, start_precision, start_year, title_raw)
  on public.trace_candidate_employments to authenticated;
grant insert (candidate_activity_id, candidate_employment_id, candidate_profile_id, evidence_text, evidence_type,
  owner_user_id, responsibility_area) on public.trace_candidate_evidence to authenticated;
grant update (candidate_activity_id, candidate_employment_id, evidence_text, evidence_type, responsibility_area)
  on public.trace_candidate_evidence to authenticated;
grant insert (country_code, headline, owner_user_id, resume_as_of_month, resume_as_of_precision, resume_as_of_year)
  on public.trace_candidate_profiles to authenticated;
grant update (country_code, headline, resume_as_of_month, resume_as_of_precision, resume_as_of_year)
  on public.trace_candidate_profiles to authenticated;
grant insert (candidate_activity_id, candidate_education_id, candidate_employment_id, candidate_evidence_id,
  candidate_profile_id, owner_user_id, skill_id, skill_text_raw, stated_proficiency_raw) on public.trace_candidate_skills to authenticated;
grant update (candidate_activity_id, candidate_education_id, candidate_employment_id, candidate_evidence_id, skill_id,
  skill_text_raw, stated_proficiency_raw) on public.trace_candidate_skills to authenticated;
grant insert (consent_version, purpose, status) on public.trace_consent_events to authenticated;
grant insert (byte_size, candidate_profile_id, content_sha256, mime_type, original_filename, storage_object_path)
  on public.trace_resume_documents to authenticated;
grant update (candidate_profile_id) on public.trace_resume_documents to authenticated;

-- Writer entry points back to authenticated, changed by their owners (the M2 procedure).
grant trace_review_writer to postgres with set true, inherit false;
grant trace_ingest_writer to postgres with set true, inherit false;
set role trace_review_writer;
grant execute on function trace_review.apply_alias_mapping(uuid, uuid, text, text),
  trace_review.choose_employment_role(uuid, uuid, text, text, text[]), trace_review.classify_evidence_areas(uuid, uuid, text[], text),
  trace_review.correct_record(text, uuid, uuid, jsonb), trace_review.reassign_employment(uuid, uuid, uuid),
  trace_review.review_record(text, uuid, uuid, text) to authenticated;
revoke execute on function trace_review.apply_alias_mapping(uuid, uuid, text, text),
  trace_review.choose_employment_role(uuid, uuid, text, text, text[]), trace_review.classify_evidence_areas(uuid, uuid, text[], text),
  trace_review.correct_record(text, uuid, uuid, jsonb), trace_review.reassign_employment(uuid, uuid, uuid),
  trace_review.review_record(text, uuid, uuid, text) from trace_app_user;
reset role;
set role trace_ingest_writer;
grant execute on function trace_ingest.fail_fixture_extraction(uuid, text), trace_ingest.ingest_fixture_extraction(uuid, jsonb),
  trace_ingest.start_fixture_extraction(uuid, uuid, text) to authenticated;
revoke execute on function trace_ingest.fail_fixture_extraction(uuid, text), trace_ingest.ingest_fixture_extraction(uuid, jsonb),
  trace_ingest.start_fixture_extraction(uuid, uuid, text) from trace_app_user;
drop function trace_ingest.designate_fixture(uuid, uuid, text, text);
reset role;
revoke trace_review_writer from postgres;
revoke trace_ingest_writer from postgres;
revoke update (is_test_fixture, fixture_profile_id) on public.trace_candidate_profiles from trace_ingest_writer;
revoke update (is_test_fixture) on public.trace_resume_documents from trace_ingest_writer;

drop policy "approved fixture resumes reference reader select" on trace_ingest.approved_fixture_resumes;
drop policy "reference reader read" on trace_ref.profiles;
drop policy "reference reader read" on trace_ref.employers;
drop policy "reference reader read" on trace_ref.employments;
drop policy "reference reader read" on trace_ref.education;
drop policy "reference reader read" on trace_ref.career_breaks;
drop policy "reference reader read" on trace_ref.experience_evidence;
drop policy "reference reader read" on trace_ref.profile_skills;
drop policy "reference reader read" on trace_ref.analytics_settings;
drop policy "reference reader read" on trace_ref.data_sources;
drop policy "lookup reference reader read" on public.trace_seniority_levels;
drop policy "lookup reference reader read" on public.trace_role_families;
drop policy "lookup reference reader read" on public.trace_role_taxonomy;
drop policy "lookup reference reader read" on public.trace_role_aliases;
drop policy "lookup reference reader read" on public.trace_responsibility_areas;
drop policy "lookup reference reader read" on public.trace_skill_families;
drop policy "lookup reference reader read" on public.trace_skills;
drop policy "lookup reference reader read" on public.trace_skill_aliases;

-- DROP OWNED needs the privileges of these roles; the memberships disappear with the roles below.
grant trace_app_runtime, trace_reference_reader, trace_app_user to postgres with inherit true, set false;
drop owned by trace_app_runtime, trace_reference_reader, trace_app_user;      -- privileges only: they own no objects
drop role trace_app_runtime;
drop role trace_reference_reader;
drop role trace_app_user;
commit;
