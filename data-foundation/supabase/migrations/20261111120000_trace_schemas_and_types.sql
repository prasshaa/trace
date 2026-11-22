-- Trace Data Foundation v2 · 01 · schemas, privileges and enumerated types
--
-- Schema layout
--   public      Tables the app reaches through the Supabase Data API. Every table has RLS.
--               Only per-user (owner-scoped) tables and read-only lookups live here.
--   trace_ref   De-identified reference cohort, consent-aware analytics views, settings and
--               audit log. NOT exposed through the Data API; service_role only.
--   trace_util  Immutable helper functions and trigger functions. NOT exposed.
--
-- Do not add trace_ref or trace_util to "Exposed schemas" in the Supabase API settings.

create schema trace_ref;
create schema trace_util;

comment on schema trace_ref is
  'Trace de-identified reference cohort and analytics. Never expose through the Data API. service_role only.';
comment on schema trace_util is
  'Trace helper and trigger functions. Not exposed through the Data API.';

revoke all on schema trace_ref from public, anon, authenticated;
revoke all on schema trace_util from public, anon, authenticated;
grant usage on schema trace_ref to service_role;
-- authenticated needs USAGE on trace_util only to evaluate generated columns and CHECK
-- constraints that call the immutable date helpers when inserting its own rows.
grant usage on schema trace_util to authenticated, service_role;

alter default privileges in schema trace_ref grant all on tables to service_role;
alter default privileges in schema trace_ref grant all on sequences to service_role;
alter default privileges in schema trace_ref grant execute on functions to service_role;
alter default privileges in schema trace_ref revoke execute on functions from public;
alter default privileges in schema trace_util revoke execute on functions from public;

-- Dataset classes. uploaded_resume_fixture is kept so the class stays distinguishable in
-- local-only files, but CHECK constraints in trace_ref forbid storing it in this database.
create type public.trace_dataset_class as enum (
  'consented_real_reference',
  'uploaded_resume_fixture',
  'synthetic_test_fixture',
  'public_aggregate_reference'
);

create type public.trace_consent_purpose as enum (
  'resume_processing',
  'deidentified_shared_dataset',
  'external_demo',
  'model_provider_processing',
  'product_research'
);

-- A consent *event*. The current state is the latest event per (user, purpose).
create type public.trace_consent_status as enum ('granted', 'denied', 'withdrawn');

-- 'ongoing' is valid only for end dates and means "Present" as stated at resume_as_of.
create type public.trace_date_precision as enum ('month', 'year', 'unknown', 'ongoing');

create type public.trace_employment_type as enum (
  'full_time', 'part_time', 'contract', 'internship', 'apprenticeship',
  'freelance', 'founder', 'volunteer', 'unknown'
);

create type public.trace_mapping_status as enum (
  'confirmed',             -- participant- or reviewer-confirmed (or curated synthetic)
  'auto_high_confidence',  -- unambiguous alias, confidence >= min_mapping_confidence
  'needs_review',          -- context-required alias or low confidence; preserved, never confirmed
  'unresolved'             -- role cannot be determined; role_id must be ROLE-UNKNOWN
);

create type public.trace_review_status as enum (
  'extracted_unverified', 'participant_confirmed', 'reviewer_confirmed', 'synthetic_curated', 'rejected'
);

create type public.trace_evidence_status as enum (
  'extracted_unverified', 'participant_confirmed', 'document_verified', 'synthetic_curated', 'rejected'
);

-- Which cohort a pathway run may read.
--   empirical       consented, confirmed real profiles only (trace_ref.v_eligible_profiles)
--   synthetic_test  synthetic fixtures only; for automated and manual testing
--   synthetic_demo  synthetic fixtures with synthetic_demo_allowed; for clearly labelled demos
create type public.trace_data_mode as enum ('empirical', 'synthetic_test', 'synthetic_demo');

create type public.trace_evidence_class as enum (
  'strong', 'moderate', 'limited', 'insufficient', 'synthetic_test_only'
);
