-- Compensation for migration 20261111160000_trace_fixture_extraction_ingestion (v2.4.0, M3).
--
-- NOT a migration: it lives outside supabase/migrations and is never applied automatically.
--
--   1. Deploy the backend without ingestion (M2, commit 2b49c62) first, or stop calling
--      CandidateProfileService.ingest_extraction. Until this script has committed, ingestion keeps
--      working; afterwards a call to it fails closed (the trace_ingest functions no longer exist).
--      No other operation is affected: M3 changed no other write path.
--   2. Run this script as postgres. It carries its own BEGIN and COMMIT, so any error rolls all of it
--      back and leaves the M3 catalog intact. Do NOT add psql --single-transaction. Use:
--        psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>
--   3. Mark the migration as reverted:
--        supabase migration repair 20261111160000 --status reverted   (add --local for the local stack)
--
-- What it restores: the exact v2.3.0 catalog. It drops the trace_ingest schema, functions and fixture
-- allowlist, the trace_ingest_writer role, its grants and policies, the run indexes and constraint and
-- trace_extraction_runs.result_sha256; it restores the v2.3.0 insert guard (copied verbatim from
-- migration 20261111150000) and service_role's table-level INSERT/UPDATE on trace_resume_documents.
--
-- Seed: the synthetic seed of v2.4.0 loads the fixture allowlist, a table this script drops. On a
-- database rolled back to v2.3.0, use the v2.3.0 seed (or reset with --no-seed).
--
-- Data: extracted records already ingested stay as they are. They are valid v2.3.0 rows
-- (source_extracted, pending or reviewed by the candidate). The result hashes of runs are dropped
-- with their column; run rows and their status remain.

begin;

-- ------------------------------------------------------------------ writer policies
drop policy "candidate profiles ingest writer select" on public.trace_candidate_profiles;
drop policy "candidate profiles ingest writer update" on public.trace_candidate_profiles;
drop policy "resume documents ingest writer select" on public.trace_resume_documents;
drop policy "resume documents ingest writer update" on public.trace_resume_documents;
drop policy "extraction runs ingest writer select" on public.trace_extraction_runs;
drop policy "extraction runs ingest writer insert" on public.trace_extraction_runs;
drop policy "extraction runs ingest writer update" on public.trace_extraction_runs;
drop policy "consent events ingest writer select" on public.trace_consent_events;
drop policy "consent documents ingest writer select" on public.trace_consent_documents;
drop policy "candidate employers ingest writer select" on public.trace_candidate_employers;
drop policy "candidate employers ingest writer insert" on public.trace_candidate_employers;
drop policy "candidate employments ingest writer select" on public.trace_candidate_employments;
drop policy "candidate employments ingest writer insert" on public.trace_candidate_employments;
drop policy "candidate education ingest writer select" on public.trace_candidate_education;
drop policy "candidate education ingest writer insert" on public.trace_candidate_education;
drop policy "candidate career activities ingest writer select" on public.trace_candidate_career_activities;
drop policy "candidate career activities ingest writer insert" on public.trace_candidate_career_activities;
drop policy "candidate career breaks ingest writer select" on public.trace_candidate_career_breaks;
drop policy "candidate career breaks ingest writer insert" on public.trace_candidate_career_breaks;
drop policy "candidate evidence ingest writer select" on public.trace_candidate_evidence;
drop policy "candidate evidence ingest writer insert" on public.trace_candidate_evidence;
drop policy "candidate skills ingest writer select" on public.trace_candidate_skills;
drop policy "candidate skills ingest writer insert" on public.trace_candidate_skills;

-- ------------------------------------------------------------------ trace_ingest functions, schema and the writer role
grant trace_ingest_writer to postgres with inherit true, set true;
drop function trace_ingest.start_fixture_extraction(uuid, uuid, text);
drop function trace_ingest.ingest_fixture_extraction(uuid, jsonb);
drop function trace_ingest.fail_fixture_extraction(uuid, text);
drop function trace_ingest.assert_ingestion_allowed(uuid, uuid, uuid);
drop function trace_ingest.assert_allowed_keys(jsonb, text[], text);
drop owned by trace_ingest_writer;            -- remaining grants (and the allowlist policy) of the role
revoke trace_ingest_writer from postgres;
drop table trace_ingest.approved_fixture_resumes;
drop schema trace_ingest;
-- The role is cluster-wide. Drop it only if no other database still uses it.
drop role trace_ingest_writer;

-- ------------------------------------------------------------------ the v2.3.0 insert guard (verbatim)
create or replace function trace_util.guard_candidate_insert()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_new jsonb;
  v_expected jsonb;
  v_key text;
begin
  -- The table owner (migrations, administration, test fixtures) may insert any state; it can disable
  -- triggers anyway. Every other role inserts candidate-authored drafts only.
  if trace_util.is_table_owner(TG_RELID) then
    return NEW;
  end if;
  if TG_TABLE_NAME = 'trace_candidate_profiles' then
    v_expected := '{"confirmation_status": "pending", "confirmed_at": null, "extraction_status": "not_started",
                    "is_test_fixture": false, "fixture_profile_id": null, "raw_extraction_json": {}}';
  elsif TG_TABLE_NAME = 'trace_candidate_employers' then
    v_expected := '{"attribute_provenance": null}';
  else
    v_expected := '{"information_origin": "user_stated", "review_status": "pending_review", "verification_status": "unverified",
                    "extraction_confidence": null, "source_resume_id": null, "extraction_run_id": null, "source_location": null}';
    if TG_TABLE_NAME = 'trace_candidate_employments' then
      v_expected := v_expected || '{"role_id": "ROLE-UNKNOWN", "seniority_code": null, "mapping_status": "unresolved",
                                    "mapping_confidence": 0, "mapping_note": null, "participant_confirmed": false}'::jsonb;
    elsif TG_TABLE_NAME = 'trace_candidate_career_activities' then
      v_expected := v_expected || '{"category_code": null, "category_mapping_status": "unresolved",
                                    "category_mapping_confidence": null}'::jsonb;
    elsif TG_TABLE_NAME = 'trace_candidate_evidence' then
      v_expected := v_expected || '{"provenance": null, "evidence_status": "extracted_unverified", "confidence": null}'::jsonb;
    elsif TG_TABLE_NAME = 'trace_candidate_skills' then
      v_expected := v_expected || '{"evidence_source": null, "confidence": null, "verified": false}'::jsonb;
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
    NEW.entry_source := 'manual_entry';   -- restates information_origin = user_stated; no review meaning
  end if;
  return NEW;
end
$$;

-- ------------------------------------------------------------------ service_role on resumes (v2.3.0 state)
do $$
declare
  v_all text;
begin
  select string_agg(quote_ident(c.column_name), ', ' order by c.ordinal_position) into v_all
    from information_schema.columns c where c.table_schema = 'public' and c.table_name = 'trace_resume_documents';
  execute format('revoke insert (%s), update (%s) on public.trace_resume_documents from service_role', v_all, v_all);
end
$$;
grant insert, update on public.trace_resume_documents to service_role;

-- ------------------------------------------------------------------ extraction runs
drop index public.trace_extraction_runs_one_open_per_resume;
drop index public.trace_extraction_runs_one_succeeded_per_resume;
alter table public.trace_extraction_runs drop constraint trace_extraction_runs_succeeded_has_result;
alter table public.trace_extraction_runs drop column result_sha256;

commit;
