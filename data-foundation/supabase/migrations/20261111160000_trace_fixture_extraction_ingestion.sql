-- Trace data foundation v2.4.0: trusted ingestion of extracted drafts, synthetic fixtures only (M3).
--
-- A candidate's resume extraction becomes draft records through one controlled path:
--   trace_ingest.start_fixture_extraction  -> creates (or resumes) a 'queued' extraction run
--   trace_ingest.ingest_fixture_extraction -> inserts every extracted record atomically, run -> 'succeeded'
--   trace_ingest.fail_fixture_extraction   -> marks a queued run 'failed' (nothing was ingested)
--
-- The functions are owned by the dedicated role trace_ingest_writer (NOLOGIN, NOINHERIT, NOBYPASSRLS,
-- no members), are SECURITY DEFINER with an empty search_path and no dynamic SQL, are executable by
-- authenticated only, and act only for the current request's auth.uid() (trace_review.request_uid()).
-- The postgres table-owner connection is not an ingestion path.
--
-- What ingestion guarantees:
--   * Records are inserted with information_origin = 'source_extracted', review_status = 'pending_review',
--     verification_status = 'unverified', the extraction run, source resume, source location and
--     extraction confidence. Role, seniority, skill and responsibility taxonomy mappings and every
--     review or verification value stay at their defaults: mapping is a separate trusted process, and
--     review stays with the candidate (trace_review.review_record). Employers get
--     attribute_provenance = 'resume_stated'. The insert guard (G0) accepts these values only from
--     trace_ingest_writer.
--   * Scope (M3): synthetic fixtures only. The profile and the resume must be marked as test fixtures
--     (profile flags: table owner only; resume flag: table owner only since this migration revokes it
--     from service_role), and the pair (profile's fixture id, resume's content_sha256) must be exactly an
--     approved pair in trace_ingest.approved_fixture_resumes, which only the table owner can write and
--     the synthetic seed fills from the rendered fixture PDFs. The result must name the same fixture and
--     hash. Neither a fixture id nor a hash is accepted on its own, and nothing here reads or parses a
--     PDF: the database binds identities and hashes, it does not see document bytes.
--   * Consent: the owner's current resume_processing consent must be granted under an approved,
--     effective, unretired consent document, when the run starts and again when it is ingested.
--   * Atomic: one statement inserts every record or none. A profile that already holds records is
--     refused, so an ingestion never merges into existing data.
--   * Idempotent: one succeeded run per resume. Re-ingesting the identical result is a no-op; a
--     different result for an ingested resume is refused.
--   * Payloads are allow-listed key by key: a payload that carries any other field (for example a
--     review status, a provenance value or a role mapping) is rejected, not ignored.
--
-- Error codes (in addition to TR001-TR011 from v2.3.0):
--   TR012 outside the allowed fixture scope       TR013 resume_processing consent missing or inactive
--   TR014 extraction run state conflict           TR015 the profile already holds records

-- ------------------------------------------------------------------ writer role and schema
do $$
begin
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'trace_ingest_writer') then
    create role trace_ingest_writer nologin noinherit nosuperuser nocreatedb nocreaterole nobypassrls noreplication;
  end if;
end
$$;
alter role trace_ingest_writer nologin noinherit nocreatedb nocreaterole nobypassrls;

create schema trace_ingest;
revoke all on schema trace_ingest from public;
grant usage on schema trace_ingest to authenticated, trace_ingest_writer;

-- ------------------------------------------------------------------ approved fixture resumes (allowlist)
-- One row per synthetic fixture: its id and the SHA-256 of the PDF rendered from it
-- (data/synthetic/synthetic_resume_pdfs.zip). Filled only by the synthetic seed, which never runs in
-- production, so the allowlist there is empty and every ingestion is refused. Only the table owner can
-- write it; only trace_ingest_writer can read it.
create table trace_ingest.approved_fixture_resumes (
  fixture_profile_id text primary key references trace_ref.profiles(profile_id) on delete cascade
                     check (fixture_profile_id ~ '^SYN-[0-9]{3}$'),
  pdf_sha256         text not null unique check (pdf_sha256 ~ '^[0-9a-f]{64}$')
);
alter table trace_ingest.approved_fixture_resumes enable row level security;
revoke all on trace_ingest.approved_fixture_resumes from public, anon, authenticated, service_role;
grant select on trace_ingest.approved_fixture_resumes to trace_ingest_writer;
create policy "approved fixture resumes ingest writer select" on trace_ingest.approved_fixture_resumes
  for select to trace_ingest_writer using (true);

-- ------------------------------------------------------------------ resume fixture metadata: owner only
-- service_role keeps every other resume privilege, but can no longer mark a resume as a test fixture or
-- change its recorded hash. Candidates already could not (column grants since v2.0). The column lists
-- are read from the catalog so that later columns stay writable as before.
do $$
declare
  v_insert text;
  v_update text;
begin
  select string_agg(quote_ident(c.column_name), ', ' order by c.ordinal_position) filter (where c.column_name <> 'is_test_fixture'),
         string_agg(quote_ident(c.column_name), ', ' order by c.ordinal_position) filter (where c.column_name not in ('is_test_fixture', 'content_sha256'))
    into v_insert, v_update
    from information_schema.columns c
   where c.table_schema = 'public' and c.table_name = 'trace_resume_documents' and c.is_generated = 'NEVER';
  revoke insert, update on public.trace_resume_documents from service_role;
  execute format('grant insert (%s) on public.trace_resume_documents to service_role', v_insert);
  execute format('grant update (%s) on public.trace_resume_documents to service_role', v_update);
end
$$;

-- ------------------------------------------------------------------ extraction runs: idempotency
-- Runs created before v2.4.0 (or kept by a rollback of it) have no result hash, so the rule that a
-- succeeded run carries its hash applies to new and changed rows only (NOT VALID). Ingestion treats a
-- missing hash as a different result: such a resume is never ingested twice.
do $$
begin
  if exists (select 1 from public.trace_extraction_runs where status = 'succeeded' group by resume_id having count(*) > 1)
     or exists (select 1 from public.trace_extraction_runs where status in ('queued', 'running') group by resume_id having count(*) > 1) then
    raise exception 'v2.4.0 needs at most one succeeded and one open extraction run per resume; resolve the duplicates first';
  end if;
end
$$;
alter table public.trace_extraction_runs
  add column result_sha256 text check (result_sha256 ~ '^[0-9a-f]{64}$'),
  add constraint trace_extraction_runs_succeeded_has_result check (status <> 'succeeded' or result_sha256 is not null) not valid;
create unique index trace_extraction_runs_one_succeeded_per_resume on public.trace_extraction_runs (resume_id)
  where status = 'succeeded';
create unique index trace_extraction_runs_one_open_per_resume on public.trace_extraction_runs (resume_id)
  where status in ('queued', 'running');

-- ------------------------------------------------------------------ G0: the ingest writer's insert shape
-- Replaces the v2.3.0 guard. Unchanged for every other role; trace_ingest_writer may insert exactly the
-- extracted-draft shape and nothing else.
create or replace function trace_util.guard_candidate_insert()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_new jsonb;
  v_expected jsonb;
  v_key text;
  v_ingest boolean := current_user = 'trace_ingest_writer';
begin
  -- The table owner (migrations, administration, test fixtures) may insert any state; it can disable
  -- triggers anyway. Every other role inserts candidate-authored drafts only, except the ingest
  -- writer, which inserts extracted drafts only.
  if trace_util.is_table_owner(TG_RELID) then
    return NEW;
  end if;
  if TG_TABLE_NAME = 'trace_candidate_profiles' then
    v_expected := '{"confirmation_status": "pending", "confirmed_at": null, "extraction_status": "not_started",
                    "is_test_fixture": false, "fixture_profile_id": null, "raw_extraction_json": {}}';
  elsif TG_TABLE_NAME = 'trace_candidate_employers' then
    v_expected := case when v_ingest then '{"attribute_provenance": "resume_stated"}'::jsonb
                       else '{"attribute_provenance": null}'::jsonb end;
  else
    if v_ingest then
      v_expected := '{"information_origin": "source_extracted", "review_status": "pending_review",
                      "verification_status": "unverified"}';
      v_new := to_jsonb(NEW);
      if v_new -> 'source_resume_id' = 'null'::jsonb or v_new -> 'extraction_run_id' = 'null'::jsonb
         or v_new -> 'source_location' = 'null'::jsonb then
        raise exception using errcode = 'TR011',
          message = format('%s: an extracted record needs its source resume, extraction run and source location', TG_TABLE_NAME);
      end if;
    else
      v_expected := '{"information_origin": "user_stated", "review_status": "pending_review", "verification_status": "unverified",
                      "extraction_confidence": null, "source_resume_id": null, "extraction_run_id": null, "source_location": null}';
    end if;
    if TG_TABLE_NAME = 'trace_candidate_employments' then
      v_expected := v_expected || '{"role_id": "ROLE-UNKNOWN", "seniority_code": null, "mapping_status": "unresolved",
                                    "mapping_confidence": 0, "mapping_note": null, "participant_confirmed": false}'::jsonb;
    elsif TG_TABLE_NAME = 'trace_candidate_career_activities' then
      v_expected := v_expected || '{"category_code": null, "category_mapping_status": "unresolved",
                                    "category_mapping_confidence": null}'::jsonb;
    elsif TG_TABLE_NAME = 'trace_candidate_evidence' then
      v_expected := v_expected || case when v_ingest
                      then '{"provenance": "resume_claim", "evidence_status": "extracted_unverified", "confidence": null}'::jsonb
                      else '{"provenance": null, "evidence_status": "extracted_unverified", "confidence": null}'::jsonb end;
    elsif TG_TABLE_NAME = 'trace_candidate_skills' then
      v_expected := v_expected || case when v_ingest
                      then '{"evidence_source": "resume_explicit", "skill_id": null, "confidence": null, "verified": false}'::jsonb
                      else '{"evidence_source": null, "confidence": null, "verified": false}'::jsonb end;
    end if;
  end if;
  v_new := to_jsonb(NEW);
  for v_key in select jsonb_object_keys(v_expected) loop
    if v_new -> v_key is distinct from v_expected -> v_key then
      raise exception using errcode = 'TR011',
        message = format('%s.%s is system-controlled and cannot be set on insert', TG_TABLE_NAME, v_key);
    end if;
  end loop;
  if TG_TABLE_NAME = 'trace_candidate_employments' then
    -- Restates information_origin; no review meaning.
    NEW.entry_source := case when v_ingest then 'resume_extraction' else 'manual_entry' end;
  end if;
  return NEW;
end
$$;

-- ------------------------------------------------------------------ writer-only helpers (invoker)
-- Checks ownership, fixture scope and consent for one profile and resume, and returns the current
-- resume_processing consent event. Runs as trace_ingest_writer inside the functions below.
create function trace_ingest.assert_ingestion_allowed(p_uid uuid, p_candidate_profile_id uuid, p_resume_id uuid)
returns uuid
language plpgsql stable
set search_path = ''
as $$
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
$$;

-- Rejects a payload array whose elements carry any key outside the allow-list.
create function trace_ingest.assert_allowed_keys(p_items jsonb, p_allowed text[], p_kind text)
returns void
language plpgsql immutable
set search_path = ''
as $$
declare
  v_bad text;
begin
  if p_items is null then
    return;
  end if;
  if pg_catalog.jsonb_typeof(p_items) <> 'array' then
    raise exception using errcode = 'TR006', message = format('%s must be an array', p_kind);
  end if;
  select string_agg(distinct k, ', ') into v_bad
    from pg_catalog.jsonb_array_elements(p_items) e
   cross join lateral pg_catalog.jsonb_object_keys(case when pg_catalog.jsonb_typeof(e) = 'object' then e else '{"<not an object>": 0}'::jsonb end) k
   where k <> all (p_allowed);
  if v_bad is not null then
    raise exception using errcode = 'TR006', message = format('%s: field(s) not accepted from an extraction: %s', p_kind, v_bad);
  end if;
end
$$;

-- ------------------------------------------------------------------ start
create function trace_ingest.start_fixture_extraction(p_candidate_profile_id uuid, p_resume_id uuid, p_extractor_version text)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  v_consent uuid;
  v_run uuid;
begin
  if v_uid is null then
    raise exception using errcode = 'TR004', message = 'no request identity';
  end if;
  if p_extractor_version is null or p_extractor_version !~ '^[a-z0-9][a-z0-9._-]{0,63}$' then
    raise exception using errcode = 'TR006', message = 'invalid extractor version';
  end if;
  -- Lock order for every ingestion function: resume, then run, then profile.
  perform 1 from public.trace_resume_documents r
   where r.resume_id = p_resume_id and r.owner_user_id = v_uid
     for update;
  v_consent := trace_ingest.assert_ingestion_allowed(v_uid, p_candidate_profile_id, p_resume_id);

  select x.extraction_run_id into v_run from public.trace_extraction_runs x
   where x.resume_id = p_resume_id and x.owner_user_id = v_uid and x.status in ('succeeded', 'queued', 'running')
   order by (x.status = 'succeeded') desc
   limit 1;
  if found then
    return v_run;      -- idempotent: the resume is already ingested, or a run is already open
  end if;

  insert into public.trace_extraction_runs (owner_user_id, resume_id, candidate_profile_id, provider_name, model_name,
                                            prompt_version, processing_region, consent_event_id, status)
  values (v_uid, p_resume_id, p_candidate_profile_id, 'trace-fixture-extractor', 'deterministic-fixture',
          p_extractor_version, 'local', v_consent, 'queued')
  returning extraction_run_id into v_run;
  update public.trace_resume_documents r set parse_status = 'processing'
   where r.resume_id = p_resume_id and r.owner_user_id = v_uid;
  update public.trace_candidate_profiles p set extraction_status = 'queued'
   where p.candidate_profile_id = p_candidate_profile_id and p.owner_user_id = v_uid;
  return v_run;
end
$$;

-- ------------------------------------------------------------------ ingest
create function trace_ingest.ingest_fixture_extraction(p_extraction_run_id uuid, p_result jsonb)
returns text
language plpgsql
security definer
set search_path = ''
as $$
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
$$;

-- ------------------------------------------------------------------ fail
create function trace_ingest.fail_fixture_extraction(p_extraction_run_id uuid, p_error_code text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  v_resume uuid;
  v_profile uuid;
  v_status text;
begin
  if v_uid is null then
    raise exception using errcode = 'TR004', message = 'no request identity';
  end if;
  if p_error_code is null or p_error_code !~ '^[a-z0-9_]{1,40}$' then
    raise exception using errcode = 'TR006', message = 'error codes are short identifiers, never free text';
  end if;
  select x.resume_id into v_resume from public.trace_extraction_runs x
   where x.extraction_run_id = p_extraction_run_id and x.owner_user_id = v_uid;
  if not found then
    raise exception using errcode = 'TR005', message = format('extraction run %s not found', p_extraction_run_id);
  end if;
  perform 1 from public.trace_resume_documents d where d.resume_id = v_resume and d.owner_user_id = v_uid for update;
  select x.candidate_profile_id, x.status into v_profile, v_status from public.trace_extraction_runs x
   where x.extraction_run_id = p_extraction_run_id and x.owner_user_id = v_uid
     for update;
  if v_status = 'failed' then
    return 'failed';
  end if;
  if v_status <> 'queued' then
    raise exception using errcode = 'TR014', message = format('the extraction run is %s and cannot fail', v_status);
  end if;
  update public.trace_extraction_runs x
     set status = 'failed', error_code = p_error_code, finished_at = pg_catalog.now()
   where x.extraction_run_id = p_extraction_run_id and x.owner_user_id = v_uid;
  update public.trace_resume_documents d set parse_status = 'failed'
   where d.resume_id = v_resume and d.owner_user_id = v_uid;
  update public.trace_candidate_profiles p set extraction_status = 'failed'
   where p.candidate_profile_id = v_profile and p.owner_user_id = v_uid and p.extraction_status <> 'extracted';
  return 'failed';
end
$$;

-- ------------------------------------------------------------------ writer privileges and RLS
grant usage on schema trace_review, trace_util to trace_ingest_writer;
grant execute on function trace_review.request_uid() to trace_ingest_writer;
grant execute on function trace_util.is_table_owner(oid),
  trace_util.month_lo(integer, integer, public.trace_date_precision),
  trace_util.month_hi(integer, integer, public.trace_date_precision),
  trace_util.valid_partial_date(integer, integer, public.trace_date_precision),
  trace_util.feasible_period(integer, integer, public.trace_date_precision, integer, integer, public.trace_date_precision)
  to trace_ingest_writer;

grant select on public.trace_candidate_profiles, public.trace_resume_documents, public.trace_extraction_runs,
  public.trace_consent_events, public.trace_consent_documents,
  public.trace_candidate_employers, public.trace_candidate_employments, public.trace_candidate_education,
  public.trace_candidate_career_activities, public.trace_candidate_career_breaks, public.trace_candidate_evidence,
  public.trace_candidate_skills to trace_ingest_writer;
grant update (resume_as_of_year, resume_as_of_month, resume_as_of_precision, extraction_status)
  on public.trace_candidate_profiles to trace_ingest_writer;
grant update (parse_status) on public.trace_resume_documents to trace_ingest_writer;
grant insert (owner_user_id, resume_id, candidate_profile_id, provider_name, model_name, prompt_version, processing_region,
              consent_event_id, status) on public.trace_extraction_runs to trace_ingest_writer;
grant update (status, result_sha256, started_at, finished_at, error_code) on public.trace_extraction_runs to trace_ingest_writer;
grant insert (owner_user_id, candidate_profile_id, employer_name, industry_raw, industry_sector, company_model, business_model,
              company_size_band, attribute_provenance) on public.trace_candidate_employers to trace_ingest_writer;
grant insert (owner_user_id, candidate_profile_id, sequence_index, candidate_employer_id, title_raw, employment_type,
              primary_career_sequence, counts_as_full_time_experience, classification_note, start_year, start_month,
              start_precision, end_year, end_month, end_precision, is_current_as_stated, information_origin,
              source_resume_id, extraction_run_id, source_location, extraction_confidence)
  on public.trace_candidate_employments to trace_ingest_writer;
grant insert (owner_user_id, candidate_profile_id, qualification_title_raw, qualification_level, institution_name,
              field_of_study, study_mode, completion_status, notes, start_year, start_month, start_precision, end_year,
              end_month, end_precision, information_origin, source_resume_id, extraction_run_id, source_location,
              extraction_confidence)
  on public.trace_candidate_education to trace_ingest_writer;
grant insert (owner_user_id, candidate_profile_id, activity_type, title_raw, organisation_raw, description_raw,
              contribution_raw, outcome_raw, link_url, status, related_employment_id, credential_identifier_raw,
              start_year, start_month, start_precision, end_year, end_month, end_precision, expiry_year, expiry_month,
              expiry_precision, information_origin, source_resume_id, extraction_run_id, source_location, extraction_confidence)
  on public.trace_candidate_career_activities to trace_ingest_writer;
grant insert (owner_user_id, candidate_profile_id, break_reason, note, start_year, start_month, start_precision, end_year,
              end_month, end_precision, information_origin, source_resume_id, extraction_run_id, source_location,
              extraction_confidence)
  on public.trace_candidate_career_breaks to trace_ingest_writer;
grant insert (owner_user_id, candidate_profile_id, evidence_type, evidence_text, candidate_employment_id,
              candidate_activity_id, provenance, information_origin, source_resume_id, extraction_run_id, source_location,
              extraction_confidence)
  on public.trace_candidate_evidence to trace_ingest_writer;
grant insert (owner_user_id, candidate_profile_id, skill_text_raw, stated_proficiency_raw, candidate_employment_id,
              candidate_education_id, candidate_activity_id, candidate_evidence_id, evidence_source, information_origin,
              source_resume_id, extraction_run_id, source_location, extraction_confidence)
  on public.trace_candidate_skills to trace_ingest_writer;

create policy "candidate profiles ingest writer select" on public.trace_candidate_profiles
  for select to trace_ingest_writer using (owner_user_id = (select trace_review.request_uid()));
create policy "candidate profiles ingest writer update" on public.trace_candidate_profiles
  for update to trace_ingest_writer using (owner_user_id = (select trace_review.request_uid()))
  with check (owner_user_id = (select trace_review.request_uid()));
create policy "resume documents ingest writer select" on public.trace_resume_documents
  for select to trace_ingest_writer using (owner_user_id = (select trace_review.request_uid()));
create policy "resume documents ingest writer update" on public.trace_resume_documents
  for update to trace_ingest_writer using (owner_user_id = (select trace_review.request_uid()))
  with check (owner_user_id = (select trace_review.request_uid()));
create policy "extraction runs ingest writer select" on public.trace_extraction_runs
  for select to trace_ingest_writer using (owner_user_id = (select trace_review.request_uid()));
create policy "extraction runs ingest writer insert" on public.trace_extraction_runs
  for insert to trace_ingest_writer with check (owner_user_id = (select trace_review.request_uid()));
create policy "extraction runs ingest writer update" on public.trace_extraction_runs
  for update to trace_ingest_writer using (owner_user_id = (select trace_review.request_uid()))
  with check (owner_user_id = (select trace_review.request_uid()));
create policy "consent events ingest writer select" on public.trace_consent_events
  for select to trace_ingest_writer using (user_id = (select trace_review.request_uid()));
create policy "consent documents ingest writer select" on public.trace_consent_documents
  for select to trace_ingest_writer using (true);
create policy "candidate employers ingest writer select" on public.trace_candidate_employers
  for select to trace_ingest_writer using (owner_user_id = (select trace_review.request_uid()));
create policy "candidate employers ingest writer insert" on public.trace_candidate_employers
  for insert to trace_ingest_writer with check (owner_user_id = (select trace_review.request_uid()));
create policy "candidate employments ingest writer select" on public.trace_candidate_employments
  for select to trace_ingest_writer using (owner_user_id = (select trace_review.request_uid()));
create policy "candidate employments ingest writer insert" on public.trace_candidate_employments
  for insert to trace_ingest_writer with check (owner_user_id = (select trace_review.request_uid()));
create policy "candidate education ingest writer select" on public.trace_candidate_education
  for select to trace_ingest_writer using (owner_user_id = (select trace_review.request_uid()));
create policy "candidate education ingest writer insert" on public.trace_candidate_education
  for insert to trace_ingest_writer with check (owner_user_id = (select trace_review.request_uid()));
create policy "candidate career activities ingest writer select" on public.trace_candidate_career_activities
  for select to trace_ingest_writer using (owner_user_id = (select trace_review.request_uid()));
create policy "candidate career activities ingest writer insert" on public.trace_candidate_career_activities
  for insert to trace_ingest_writer with check (owner_user_id = (select trace_review.request_uid()));
create policy "candidate career breaks ingest writer select" on public.trace_candidate_career_breaks
  for select to trace_ingest_writer using (owner_user_id = (select trace_review.request_uid()));
create policy "candidate career breaks ingest writer insert" on public.trace_candidate_career_breaks
  for insert to trace_ingest_writer with check (owner_user_id = (select trace_review.request_uid()));
create policy "candidate evidence ingest writer select" on public.trace_candidate_evidence
  for select to trace_ingest_writer using (owner_user_id = (select trace_review.request_uid()));
create policy "candidate evidence ingest writer insert" on public.trace_candidate_evidence
  for insert to trace_ingest_writer with check (owner_user_id = (select trace_review.request_uid()));
create policy "candidate skills ingest writer select" on public.trace_candidate_skills
  for select to trace_ingest_writer using (owner_user_id = (select trace_review.request_uid()));
create policy "candidate skills ingest writer insert" on public.trace_candidate_skills
  for insert to trace_ingest_writer with check (owner_user_id = (select trace_review.request_uid()));

-- ------------------------------------------------------------------ EXECUTE
revoke all on function trace_ingest.assert_ingestion_allowed(uuid, uuid, uuid),
  trace_ingest.assert_allowed_keys(jsonb, text[], text),
  trace_ingest.start_fixture_extraction(uuid, uuid, text),
  trace_ingest.ingest_fixture_extraction(uuid, jsonb),
  trace_ingest.fail_fixture_extraction(uuid, text)
  from public, anon, authenticated, service_role;
grant execute on function trace_ingest.start_fixture_extraction(uuid, uuid, text),
  trace_ingest.ingest_fixture_extraction(uuid, jsonb),
  trace_ingest.fail_fixture_extraction(uuid, text)
  to authenticated;

-- ------------------------------------------------------------------ ownership transfer (the M2 procedure)
-- The helpers are owned by the writer too, so the entry points (writer-owned, SECURITY DEFINER) can
-- call them without any grant; nobody else can.
grant trace_ingest_writer to postgres with set true, inherit false;
grant create on schema trace_ingest to trace_ingest_writer;
alter function trace_ingest.assert_ingestion_allowed(uuid, uuid, uuid) owner to trace_ingest_writer;
alter function trace_ingest.assert_allowed_keys(jsonb, text[], text) owner to trace_ingest_writer;
alter function trace_ingest.start_fixture_extraction(uuid, uuid, text) owner to trace_ingest_writer;
alter function trace_ingest.ingest_fixture_extraction(uuid, jsonb) owner to trace_ingest_writer;
alter function trace_ingest.fail_fixture_extraction(uuid, text) owner to trace_ingest_writer;
revoke create on schema trace_ingest from trace_ingest_writer;
revoke trace_ingest_writer from postgres;

-- ------------------------------------------------------------------ assertions (fail the migration if violated)
do $$
declare
  v_bad text;
begin
  if exists (select 1 from pg_catalog.pg_roles r where r.rolname = 'trace_ingest_writer'
               and (r.rolsuper or r.rolcanlogin or r.rolinherit or r.rolbypassrls or r.rolcreaterole or r.rolcreatedb or r.rolreplication)) then
    raise exception 'trace_ingest_writer must be NOLOGIN, NOINHERIT, NOBYPASSRLS and unprivileged';
  end if;
  if pg_catalog.pg_has_role('postgres', 'trace_ingest_writer', 'SET') or pg_catalog.pg_has_role('postgres', 'trace_ingest_writer', 'USAGE') then
    raise exception 'postgres must not be able to act as trace_ingest_writer';
  end if;
  if exists (select 1 from pg_catalog.pg_auth_members m
              where m.roleid = 'trace_ingest_writer'::regrole and (m.set_option or m.inherit_option)) then
    raise exception 'trace_ingest_writer must have no members that can use it';
  end if;
  if exists (select 1 from pg_catalog.pg_auth_members m where m.member = 'trace_ingest_writer'::regrole) then
    raise exception 'trace_ingest_writer must not be a member of any role';
  end if;
  if pg_catalog.has_schema_privilege('trace_ingest_writer', 'trace_ingest', 'CREATE') then
    raise exception 'trace_ingest_writer must not keep CREATE on trace_ingest';
  end if;
  select string_agg(p.oid::regprocedure::text, '; ') into v_bad
  from pg_catalog.pg_proc p
  where p.pronamespace = 'trace_ingest'::regnamespace and pg_catalog.pg_get_userbyid(p.proowner) <> 'trace_ingest_writer';
  if v_bad is not null then
    raise exception 'trace_ingest functions must be owned by trace_ingest_writer: %', v_bad;
  end if;
end
$$;
