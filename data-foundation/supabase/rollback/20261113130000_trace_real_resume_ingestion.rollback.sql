-- Compensation for migration 20261113130000_trace_real_resume_ingestion (v2.9.0). NOT a migration.
--   1. Deploy a backend before v2.9.0 (or TRACE_REAL_RESUME_UPLOADS=0) and roll back 20261113140000 first.
--   2. Run as postgres: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>
--   3. supabase migration repair 20261113130000 --status reverted
-- Restores the exact v2.8.0 ingestion functions (fixture-only). Data: admission records are discarded; profiles already
-- ingested from real resumes keep their (de-identified, pending or reviewed) records. Refuses while admissions exist
-- unless acknowledged for this session:   set trace.rollback_discard_resume_admissions = 'I understand';
begin;
do $$
begin
  if exists (select 1 from trace_ingest.deidentified_resume_admissions)
     and coalesce(current_setting('trace.rollback_discard_resume_admissions', true), '') <> 'I understand' then
    raise exception 'de-identification admissions exist; acknowledge the loss first (see header)';
  end if;
end
$$;
grant trace_ingest_writer to postgres with set true, inherit false;
grant create on schema trace_ingest to trace_ingest_writer;
set role trace_ingest_writer;
CREATE OR REPLACE FUNCTION trace_ingest.assert_ingestion_allowed(p_uid uuid, p_candidate_profile_id uuid, p_resume_id uuid)
 RETURNS uuid
 LANGUAGE plpgsql
 STABLE
 SET search_path TO ''
AS $function$
declare
  v_profile_fixture boolean;
  v_fixture_id text;
  v_resume_fixture boolean;
  v_resume_sha text;
  v_event uuid;
  v_status text;
  v_review text;
  v_effective timestamptz;
  v_retired timestamptz;
begin
  select p.is_test_fixture, p.fixture_profile_id into v_profile_fixture, v_fixture_id
    from public.trace_candidate_profiles p
   where p.candidate_profile_id = p_candidate_profile_id and p.owner_user_id = p_uid;
  if not found then
    raise exception using errcode = 'TR005', message = format('profile %s not found', p_candidate_profile_id);
  end if;
  select r.is_test_fixture, r.content_sha256 into v_resume_fixture, v_resume_sha
    from public.trace_resume_documents r
   where r.resume_id = p_resume_id and r.owner_user_id = p_uid and r.candidate_profile_id = p_candidate_profile_id
     and r.deleted_at is null;
  if not found then
    raise exception using errcode = 'TR005', message = format('resume %s not found in this profile', p_resume_id);
  end if;
  if not v_profile_fixture or v_fixture_id is null or v_fixture_id !~ '^SYN-[0-9]{3}$'
     or not v_resume_fixture or v_resume_sha is null then
    raise exception using errcode = 'TR012',
      message = 'ingestion is limited to synthetic test-fixture profiles and resumes';
  end if;
  -- The exact approved pair: the profile's fixture and the hash of that fixture's own PDF.
  if not exists (select 1 from trace_ingest.approved_fixture_resumes a
                  where a.fixture_profile_id = v_fixture_id and a.pdf_sha256 = v_resume_sha) then
    raise exception using errcode = 'TR012',
      message = 'the resume is not the approved PDF of this synthetic fixture';
  end if;
  select e.consent_event_id, e.status::text, d.legal_review_status, d.effective_from, d.retired_at
    into v_event, v_status, v_review, v_effective, v_retired
    from public.trace_consent_events e
    join public.trace_consent_documents d on d.consent_version = e.consent_version and d.purpose = e.purpose
   where e.user_id = p_uid and e.purpose = 'resume_processing'
   order by e.recorded_at desc, e.consent_event_id desc
   limit 1;
  if not found or v_status <> 'granted' or v_review <> 'approved' or v_retired is not null
     or v_effective is null or v_effective > pg_catalog.now() then
    raise exception using errcode = 'TR013',
      message = 'resume processing needs a current grant under an approved consent document';
  end if;
  return v_event;
end
$function$;

CREATE OR REPLACE FUNCTION trace_ingest.ingest_fixture_extraction(p_extraction_run_id uuid, p_result jsonb)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
  v_uid uuid := trace_review.request_uid();
  v_hash text;
  v_resume uuid;
  v_profile uuid;
  v_status text;
  v_result_sha text;
  v_provider text;
  v_fixture_id text;
  v_as_of_precision public.trace_date_precision;
  v_resume_sha text;
  v_keys jsonb := '{}';
  v_id uuid;
  r record;
begin
  if v_uid is null then
    raise exception using errcode = 'TR004', message = 'no request identity';
  end if;
  if p_result is null or pg_catalog.jsonb_typeof(p_result) <> 'object' then
    raise exception using errcode = 'TR006', message = 'the extraction result must be an object';
  end if;
  v_hash := pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(p_result::text, 'UTF8')), 'hex');

  select x.resume_id into v_resume from public.trace_extraction_runs x
   where x.extraction_run_id = p_extraction_run_id and x.owner_user_id = v_uid;
  if not found then
    raise exception using errcode = 'TR005', message = format('extraction run %s not found', p_extraction_run_id);
  end if;
  perform 1 from public.trace_resume_documents d where d.resume_id = v_resume and d.owner_user_id = v_uid for update;
  select x.candidate_profile_id, x.status, x.result_sha256, x.provider_name
    into v_profile, v_status, v_result_sha, v_provider
    from public.trace_extraction_runs x
   where x.extraction_run_id = p_extraction_run_id and x.owner_user_id = v_uid
     for update;
  if v_status = 'succeeded' then
    if v_result_sha = v_hash then
      return 'succeeded';   -- idempotent: the same result was already ingested
    end if;
    raise exception using errcode = 'TR014', message = 'this resume was already ingested with a different result';
  end if;
  if v_status <> 'queued' then
    raise exception using errcode = 'TR014', message = format('the extraction run is %s; start a new run', v_status);
  end if;
  if v_provider <> 'trace-fixture-extractor' then
    raise exception using errcode = 'TR012', message = 'only fixture extraction runs can be ingested';
  end if;

  -- Ownership, fixture scope and consent, re-checked at ingestion time.
  perform trace_ingest.assert_ingestion_allowed(v_uid, v_profile, v_resume);
  select p.fixture_profile_id, p.resume_as_of_precision into v_fixture_id, v_as_of_precision
    from public.trace_candidate_profiles p
   where p.candidate_profile_id = v_profile and p.owner_user_id = v_uid
     for update;     -- also makes concurrent record inserts into this profile wait (their FK check)
  select d.content_sha256 into v_resume_sha from public.trace_resume_documents d where d.resume_id = v_resume and d.owner_user_id = v_uid;

  -- The payload: allow-listed keys only, bound to this fixture and this resume.
  if exists (select 1 from pg_catalog.jsonb_object_keys(p_result) k
              where k <> all (array['contract_version', 'fixture_profile_id', 'source_sha256', 'resume_as_of_year',
                                    'resume_as_of_month', 'resume_as_of_precision', 'employers', 'employments', 'education',
                                    'career_activities', 'career_breaks', 'evidence', 'skills'])) then
    raise exception using errcode = 'TR006', message = 'the extraction result carries fields that are not accepted';
  end if;
  if p_result ->> 'contract_version' is distinct from '1' then
    raise exception using errcode = 'TR006', message = 'unsupported extraction contract version';
  end if;
  if p_result ->> 'fixture_profile_id' is distinct from v_fixture_id or p_result ->> 'source_sha256' is distinct from v_resume_sha then
    raise exception using errcode = 'TR012', message = 'the extraction result does not belong to this fixture resume';
  end if;
  perform trace_ingest.assert_allowed_keys(p_result -> 'employers',
    array['key', 'employer_name', 'industry_raw', 'industry_sector', 'company_model', 'business_model', 'company_size_band'], 'employers');
  perform trace_ingest.assert_allowed_keys(p_result -> 'employments',
    array['key', 'sequence_index', 'employer_key', 'title_raw', 'employment_type', 'primary_career_sequence',
          'counts_as_full_time_experience', 'classification_note', 'start_year', 'start_month', 'start_precision',
          'end_year', 'end_month', 'end_precision', 'is_current_as_stated', 'source_location', 'extraction_confidence'], 'employments');
  perform trace_ingest.assert_allowed_keys(p_result -> 'education',
    array['key', 'qualification_title_raw', 'qualification_level', 'institution_name', 'field_of_study', 'study_mode',
          'completion_status', 'notes', 'start_year', 'start_month', 'start_precision', 'end_year', 'end_month', 'end_precision',
          'source_location', 'extraction_confidence'], 'education');
  perform trace_ingest.assert_allowed_keys(p_result -> 'career_activities',
    array['key', 'activity_type', 'title_raw', 'organisation_raw', 'description_raw', 'contribution_raw', 'outcome_raw',
          'link_url', 'status', 'related_employment_key', 'credential_identifier_raw', 'start_year', 'start_month',
          'start_precision', 'end_year', 'end_month', 'end_precision', 'expiry_year', 'expiry_month', 'expiry_precision',
          'source_location', 'extraction_confidence'], 'career_activities');
  perform trace_ingest.assert_allowed_keys(p_result -> 'career_breaks',
    array['break_reason', 'note', 'start_year', 'start_month', 'start_precision', 'end_year', 'end_month', 'end_precision',
          'source_location', 'extraction_confidence'], 'career_breaks');
  perform trace_ingest.assert_allowed_keys(p_result -> 'evidence',
    array['key', 'evidence_type', 'evidence_text', 'employment_key', 'activity_key', 'source_location', 'extraction_confidence'], 'evidence');
  perform trace_ingest.assert_allowed_keys(p_result -> 'skills',
    array['skill_text_raw', 'stated_proficiency_raw', 'employment_key', 'education_key', 'activity_key', 'evidence_key',
          'source_location', 'extraction_confidence'], 'skills');

  -- Ingestion never merges into existing data.
  if exists (select 1 from public.trace_candidate_employers t where t.candidate_profile_id = v_profile)
     or exists (select 1 from public.trace_candidate_employments t where t.candidate_profile_id = v_profile)
     or exists (select 1 from public.trace_candidate_education t where t.candidate_profile_id = v_profile)
     or exists (select 1 from public.trace_candidate_career_activities t where t.candidate_profile_id = v_profile)
     or exists (select 1 from public.trace_candidate_career_breaks t where t.candidate_profile_id = v_profile)
     or exists (select 1 from public.trace_candidate_evidence t where t.candidate_profile_id = v_profile)
     or exists (select 1 from public.trace_candidate_skills t where t.candidate_profile_id = v_profile) then
    raise exception using errcode = 'TR015', message = 'the profile already holds records; ingestion never merges into existing data';
  end if;

  -- Records. Keys connect records within this payload only; they are never stored.
  for r in select * from pg_catalog.jsonb_to_recordset(coalesce(p_result -> 'employers', '[]'))
             as x(key text, employer_name text, industry_raw text, industry_sector text, company_model text,
                  business_model text, company_size_band text)
  loop
    if r.key is null or v_keys ? ('employer:' || r.key) then
      raise exception using errcode = 'TR006', message = 'employer keys must be present and unique';
    end if;
    insert into public.trace_candidate_employers (owner_user_id, candidate_profile_id, employer_name, industry_raw,
      industry_sector, company_model, business_model, company_size_band, attribute_provenance)
    values (v_uid, v_profile, r.employer_name, r.industry_raw, coalesce(r.industry_sector, 'unknown'),
            coalesce(r.company_model, 'unknown'), coalesce(r.business_model, 'unknown'), coalesce(r.company_size_band, 'unknown'),
            'resume_stated')
    returning candidate_employer_id into v_id;
    v_keys := v_keys || pg_catalog.jsonb_build_object('employer:' || r.key, v_id);
  end loop;

  for r in select * from pg_catalog.jsonb_to_recordset(coalesce(p_result -> 'employments', '[]'))
             as x(key text, sequence_index integer, employer_key text, title_raw text, employment_type text,
                  primary_career_sequence boolean, counts_as_full_time_experience boolean, classification_note text,
                  start_year integer, start_month integer, start_precision text, end_year integer, end_month integer,
                  end_precision text, is_current_as_stated boolean, source_location text, extraction_confidence numeric)
  loop
    if r.key is null or v_keys ? ('employment:' || r.key) then
      raise exception using errcode = 'TR006', message = 'employment keys must be present and unique';
    end if;
    if r.employer_key is not null and not v_keys ? ('employer:' || r.employer_key) then
      raise exception using errcode = 'TR006', message = format('employment %s names an unknown employer', r.key);
    end if;
    insert into public.trace_candidate_employments (owner_user_id, candidate_profile_id, sequence_index, candidate_employer_id,
      title_raw, employment_type, primary_career_sequence, counts_as_full_time_experience, classification_note,
      start_year, start_month, start_precision, end_year, end_month, end_precision, is_current_as_stated,
      information_origin, source_resume_id, extraction_run_id, source_location, extraction_confidence)
    values (v_uid, v_profile, r.sequence_index, (v_keys ->> ('employer:' || r.employer_key))::uuid,
            r.title_raw, coalesce(r.employment_type, 'unknown')::public.trace_employment_type,
            coalesce(r.primary_career_sequence, true), r.counts_as_full_time_experience, r.classification_note,
            r.start_year, r.start_month, coalesce(r.start_precision, 'unknown')::public.trace_date_precision,
            r.end_year, r.end_month, coalesce(r.end_precision, 'unknown')::public.trace_date_precision,
            coalesce(r.is_current_as_stated, r.end_precision = 'ongoing'),
            'source_extracted', v_resume, p_extraction_run_id, r.source_location, r.extraction_confidence)
    returning candidate_employment_id into v_id;
    v_keys := v_keys || pg_catalog.jsonb_build_object('employment:' || r.key, v_id);
  end loop;

  for r in select * from pg_catalog.jsonb_to_recordset(coalesce(p_result -> 'education', '[]'))
             as x(key text, qualification_title_raw text, qualification_level text, institution_name text, field_of_study text,
                  study_mode text, completion_status text, notes text, start_year integer, start_month integer,
                  start_precision text, end_year integer, end_month integer, end_precision text,
                  source_location text, extraction_confidence numeric)
  loop
    if r.key is null or v_keys ? ('education:' || r.key) then
      raise exception using errcode = 'TR006', message = 'education keys must be present and unique';
    end if;
    insert into public.trace_candidate_education (owner_user_id, candidate_profile_id, qualification_title_raw, qualification_level,
      institution_name, field_of_study, study_mode, completion_status, notes, start_year, start_month, start_precision,
      end_year, end_month, end_precision, information_origin, source_resume_id, extraction_run_id, source_location, extraction_confidence)
    values (v_uid, v_profile, r.qualification_title_raw, coalesce(r.qualification_level, 'unknown'), r.institution_name,
            r.field_of_study, coalesce(r.study_mode, 'unknown'), coalesce(r.completion_status, 'unknown'), r.notes,
            r.start_year, r.start_month, coalesce(r.start_precision, 'unknown')::public.trace_date_precision,
            r.end_year, r.end_month, coalesce(r.end_precision, 'unknown')::public.trace_date_precision,
            'source_extracted', v_resume, p_extraction_run_id, r.source_location, r.extraction_confidence)
    returning candidate_education_id into v_id;
    v_keys := v_keys || pg_catalog.jsonb_build_object('education:' || r.key, v_id);
  end loop;

  for r in select * from pg_catalog.jsonb_to_recordset(coalesce(p_result -> 'career_activities', '[]'))
             as x(key text, activity_type text, title_raw text, organisation_raw text, description_raw text,
                  contribution_raw text, outcome_raw text, link_url text, status text, related_employment_key text,
                  credential_identifier_raw text, start_year integer, start_month integer, start_precision text,
                  end_year integer, end_month integer, end_precision text, expiry_year integer, expiry_month integer,
                  expiry_precision text, source_location text, extraction_confidence numeric)
  loop
    if r.key is null or v_keys ? ('activity:' || r.key) then
      raise exception using errcode = 'TR006', message = 'activity keys must be present and unique';
    end if;
    if r.related_employment_key is not null and not v_keys ? ('employment:' || r.related_employment_key) then
      raise exception using errcode = 'TR006', message = format('activity %s names an unknown employment', r.key);
    end if;
    insert into public.trace_candidate_career_activities (owner_user_id, candidate_profile_id, activity_type, title_raw,
      organisation_raw, description_raw, contribution_raw, outcome_raw, link_url, status, related_employment_id,
      credential_identifier_raw, start_year, start_month, start_precision, end_year, end_month, end_precision,
      expiry_year, expiry_month, expiry_precision, information_origin, source_resume_id, extraction_run_id,
      source_location, extraction_confidence)
    values (v_uid, v_profile, r.activity_type::public.trace_career_activity_type, r.title_raw, r.organisation_raw,
            r.description_raw, r.contribution_raw, r.outcome_raw, r.link_url, coalesce(r.status, 'unknown'),
            (v_keys ->> ('employment:' || r.related_employment_key))::uuid, r.credential_identifier_raw,
            r.start_year, r.start_month, coalesce(r.start_precision, 'unknown')::public.trace_date_precision,
            r.end_year, r.end_month, coalesce(r.end_precision, 'unknown')::public.trace_date_precision,
            r.expiry_year, r.expiry_month, coalesce(r.expiry_precision, 'unknown')::public.trace_date_precision,
            'source_extracted', v_resume, p_extraction_run_id, r.source_location, r.extraction_confidence)
    returning candidate_activity_id into v_id;
    v_keys := v_keys || pg_catalog.jsonb_build_object('activity:' || r.key, v_id);
  end loop;

  for r in select * from pg_catalog.jsonb_to_recordset(coalesce(p_result -> 'career_breaks', '[]'))
             as x(break_reason text, note text, start_year integer, start_month integer, start_precision text,
                  end_year integer, end_month integer, end_precision text, source_location text, extraction_confidence numeric)
  loop
    insert into public.trace_candidate_career_breaks (owner_user_id, candidate_profile_id, break_reason, note,
      start_year, start_month, start_precision, end_year, end_month, end_precision,
      information_origin, source_resume_id, extraction_run_id, source_location, extraction_confidence)
    values (v_uid, v_profile, coalesce(r.break_reason, 'unspecified'), r.note,
            r.start_year, r.start_month, coalesce(r.start_precision, 'unknown')::public.trace_date_precision,
            r.end_year, r.end_month, coalesce(r.end_precision, 'unknown')::public.trace_date_precision,
            'source_extracted', v_resume, p_extraction_run_id, r.source_location, r.extraction_confidence);
  end loop;

  for r in select * from pg_catalog.jsonb_to_recordset(coalesce(p_result -> 'evidence', '[]'))
             as x(key text, evidence_type text, evidence_text text, employment_key text, activity_key text,
                  source_location text, extraction_confidence numeric)
  loop
    if r.key is null or v_keys ? ('evidence:' || r.key) then
      raise exception using errcode = 'TR006', message = 'evidence keys must be present and unique';
    end if;
    if (r.employment_key is not null and not v_keys ? ('employment:' || r.employment_key))
       or (r.activity_key is not null and not v_keys ? ('activity:' || r.activity_key)) then
      raise exception using errcode = 'TR006', message = format('evidence %s names an unknown record', r.key);
    end if;
    insert into public.trace_candidate_evidence (owner_user_id, candidate_profile_id, evidence_type, evidence_text,
      candidate_employment_id, candidate_activity_id, provenance,
      information_origin, source_resume_id, extraction_run_id, source_location, extraction_confidence)
    values (v_uid, v_profile, r.evidence_type, r.evidence_text,
            (v_keys ->> ('employment:' || r.employment_key))::uuid, (v_keys ->> ('activity:' || r.activity_key))::uuid,
            'resume_claim', 'source_extracted', v_resume, p_extraction_run_id, r.source_location, r.extraction_confidence)
    returning candidate_evidence_id into v_id;
    v_keys := v_keys || pg_catalog.jsonb_build_object('evidence:' || r.key, v_id);
  end loop;

  for r in select * from pg_catalog.jsonb_to_recordset(coalesce(p_result -> 'skills', '[]'))
             as x(skill_text_raw text, stated_proficiency_raw text, employment_key text, education_key text,
                  activity_key text, evidence_key text, source_location text, extraction_confidence numeric)
  loop
    if (r.employment_key is not null and not v_keys ? ('employment:' || r.employment_key))
       or (r.education_key is not null and not v_keys ? ('education:' || r.education_key))
       or (r.activity_key is not null and not v_keys ? ('activity:' || r.activity_key))
       or (r.evidence_key is not null and not v_keys ? ('evidence:' || r.evidence_key)) then
      raise exception using errcode = 'TR006', message = 'a skill names an unknown record';
    end if;
    insert into public.trace_candidate_skills (owner_user_id, candidate_profile_id, skill_text_raw, stated_proficiency_raw,
      candidate_employment_id, candidate_education_id, candidate_activity_id, candidate_evidence_id, evidence_source,
      information_origin, source_resume_id, extraction_run_id, source_location, extraction_confidence)
    values (v_uid, v_profile, r.skill_text_raw, r.stated_proficiency_raw,
            (v_keys ->> ('employment:' || r.employment_key))::uuid, (v_keys ->> ('education:' || r.education_key))::uuid,
            (v_keys ->> ('activity:' || r.activity_key))::uuid, (v_keys ->> ('evidence:' || r.evidence_key))::uuid,
            'resume_explicit', 'source_extracted', v_resume, p_extraction_run_id, r.source_location, r.extraction_confidence);
  end loop;

  -- The document's "as of" date fills an unknown profile value; it never overwrites a stated one.
  if v_as_of_precision = 'unknown' and p_result ? 'resume_as_of_precision' then
    update public.trace_candidate_profiles p
       set resume_as_of_year = (p_result ->> 'resume_as_of_year')::integer,
           resume_as_of_month = (p_result ->> 'resume_as_of_month')::integer,
           resume_as_of_precision = (p_result ->> 'resume_as_of_precision')::public.trace_date_precision
     where p.candidate_profile_id = v_profile and p.owner_user_id = v_uid;
  end if;

  update public.trace_extraction_runs x
     set status = 'succeeded', result_sha256 = v_hash, started_at = coalesce(x.started_at, pg_catalog.now()),
         finished_at = pg_catalog.now(), error_code = null
   where x.extraction_run_id = p_extraction_run_id and x.owner_user_id = v_uid;
  update public.trace_candidate_profiles p set extraction_status = 'extracted'
   where p.candidate_profile_id = v_profile and p.owner_user_id = v_uid;
  update public.trace_resume_documents d set parse_status = 'parsed'
   where d.resume_id = v_resume and d.owner_user_id = v_uid;
  return 'succeeded';
end
$function$;
drop function trace_ingest.start_real_extraction(uuid, uuid, text);
drop function trace_ingest.admit_deidentified_resume(uuid, uuid, text, text);
reset role;
revoke create on schema trace_ingest from trace_ingest_writer;
revoke trace_ingest_writer from postgres;
drop table trace_ingest.deidentified_resume_admissions;
commit;
