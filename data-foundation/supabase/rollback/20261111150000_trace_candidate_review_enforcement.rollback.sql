-- Compensation for migration 20261111150000_trace_candidate_review_enforcement (v2.3.0, M2).
--
-- NOT a migration: it lives outside supabase/migrations and is never applied automatically. The
-- Supabase CLI has no down migrations, so reverting M2 is a forward compensation run by an operator:
--
--   1. Deploy the M1 backend (commit 1e344ef) BEFORE running this script. The M2 backend calls
--      trace_review.review_record and stops working once this script has run.
--      WRITE OUTAGE: between deploying M1 and completing this rollback, candidate writes fail. M1 still
--      uses legacy write paths that M2 no longer permits (it writes legacy trust columns on create,
--      writes review_status directly and updates candidate_employer_id directly), so creates of
--      employers, employments, evidence and skills, review actions and reassignment are refused.
--      This is a temporary fail-closed write outage, not a data-integrity failure: the database keeps
--      enforcing M2 throughout. Normal writes resume once this script has committed and the M1
--      backend and the pre-M2 database are compatible again. Keep the interval short.
--   2. Run this script as postgres. It carries its own BEGIN and COMMIT, so everything below (the
--      precondition check included) is one transaction: any error rolls all of it back and leaves
--      the M2 catalog intact. Do NOT add psql --single-transaction. Use:
--        psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>
--   3. Mark the migration as reverted in the migration history:
--        supabase migration repair 20261111150000 --status reverted   (add --local for the local stack)
--
-- What it restores (the exact pre-M2 state of migrations 20261111120800 and 20261111140000):
-- grants, ON DELETE CASCADE supporting-record links, legacy NOT NULL columns and defaults. It drops the
-- guard triggers, the trace_review schema and functions, the trace_util helpers and the
-- trace_review_writer role.
--
-- Data: nothing is rewritten. Rows created under M2 may carry NULL in the three legacy columns M2
-- made nullable; the pre-M2 schema cannot hold them. If any exist, the script stops and reports the
-- counts, rather than inventing a value (in particular never 'participant_confirmed'). Decide on those
-- rows explicitly before re-running.
-- Review states written under M2 are valid pre-M2 states and are kept as they are.

begin;

-- ------------------------------------------------------------------ preconditions
do $$
declare
  v_skills bigint;
  v_employers bigint;
  v_evidence bigint;
begin
  select count(*) into v_skills from public.trace_candidate_skills where evidence_source is null;
  select count(*) into v_employers from public.trace_candidate_employers where attribute_provenance is null;
  select count(*) into v_evidence from public.trace_candidate_evidence where provenance is null;
  if v_skills + v_employers + v_evidence > 0 then
    raise exception 'rollback stopped: % skills, % employers and % evidence rows have NULL legacy labels written under M2; '
                    'the pre-M2 schema requires a value. Resolve them explicitly first.', v_skills, v_employers, v_evidence;
  end if;
end
$$;

-- ------------------------------------------------------------------ triggers and guard functions
drop trigger trace_guard_insert on public.trace_candidate_profiles;
drop trigger trace_guard_insert on public.trace_candidate_employers;
drop trigger trace_guard_insert on public.trace_candidate_employments;
drop trigger trace_guard_insert on public.trace_candidate_education;
drop trigger trace_guard_insert on public.trace_candidate_career_breaks;
drop trigger trace_guard_insert on public.trace_candidate_career_activities;
drop trigger trace_guard_insert on public.trace_candidate_evidence;
drop trigger trace_guard_insert on public.trace_candidate_skills;
drop trigger trace_guard_employer_update on public.trace_candidate_employers;
drop trigger trace_guard_review_row on public.trace_candidate_employments;
drop trigger trace_guard_review_row on public.trace_candidate_education;
drop trigger trace_guard_review_row on public.trace_candidate_career_breaks;
drop trigger trace_guard_review_row on public.trace_candidate_career_activities;
drop trigger trace_guard_review_row on public.trace_candidate_evidence;
drop trigger trace_guard_review_row on public.trace_candidate_skills;
drop trigger trace_guard_resume_delete on public.trace_resume_documents;
drop trigger trace_guard_employer_delete on public.trace_candidate_employers;
drop trigger trace_guard_employment_delete on public.trace_candidate_employments;
drop trigger trace_guard_activity_delete on public.trace_candidate_career_activities;
drop trigger trace_guard_education_delete on public.trace_candidate_education;
drop trigger trace_guard_evidence_delete on public.trace_candidate_evidence;

drop function trace_util.guard_candidate_insert();
drop function trace_util.guard_review_row();
drop function trace_util.guard_employer_update();
drop function trace_util.guard_resume_delete();
drop function trace_util.guard_employer_delete();
drop function trace_util.guard_employment_delete();
drop function trace_util.guard_activity_delete();
drop function trace_util.guard_education_delete();
drop function trace_util.guard_evidence_delete();

-- ------------------------------------------------------------------ writer policies
drop policy "candidate employers review writer select" on public.trace_candidate_employers;
drop policy "candidate employers review writer update" on public.trace_candidate_employers;
drop policy "candidate employments review writer select" on public.trace_candidate_employments;
drop policy "candidate employments review writer update" on public.trace_candidate_employments;
drop policy "candidate education review writer select" on public.trace_candidate_education;
drop policy "candidate education review writer update" on public.trace_candidate_education;
drop policy "candidate career breaks review writer select" on public.trace_candidate_career_breaks;
drop policy "candidate career breaks review writer update" on public.trace_candidate_career_breaks;
drop policy "candidate career activities review writer select" on public.trace_candidate_career_activities;
drop policy "candidate career activities review writer update" on public.trace_candidate_career_activities;
drop policy "candidate evidence review writer select" on public.trace_candidate_evidence;
drop policy "candidate evidence review writer update" on public.trace_candidate_evidence;
drop policy "candidate skills review writer select" on public.trace_candidate_skills;
drop policy "candidate skills review writer update" on public.trace_candidate_skills;

-- ------------------------------------------------------------------ trace_review schema, functions and the writer role
-- The write functions belong to trace_review_writer. Dropping them (and the role's privileges) needs
-- the role's privileges, so postgres takes a temporary membership and gives it up before the role goes.
grant trace_review_writer to postgres with inherit true, set true;
drop function trace_review.review_record(text, uuid, uuid, text);
drop function trace_review.reassign_employment(uuid, uuid, uuid);
drop owned by trace_review_writer;            -- remaining grants to the role in this database
revoke trace_review_writer from postgres;
drop function trace_review.request_uid();
drop function trace_review.next_review_status(public.trace_record_review_status, text);
drop schema trace_review;
drop function trace_util.in_referential_action(oid);
drop function trace_util.is_table_owner(oid);
drop function trace_util.owner_data_gone(uuid, uuid);
drop function trace_util.skill_detach_conflict(uuid, text, uuid);
-- The role is cluster-wide. Drop it only if no other database still uses it.
drop role trace_review_writer;

-- ------------------------------------------------------------------ supporting-record links: back to ON DELETE CASCADE
alter table public.trace_candidate_evidence
  drop constraint trace_candidate_evidence_candidate_employment_id_candidate_fkey,
  add constraint trace_candidate_evidence_candidate_employment_id_candidate_fkey
    foreign key (candidate_employment_id, candidate_profile_id)
    references public.trace_candidate_employments (candidate_employment_id, candidate_profile_id) on delete cascade;
alter table public.trace_candidate_evidence
  drop constraint trace_candidate_evidence_activity_fkey,
  add constraint trace_candidate_evidence_activity_fkey
    foreign key (candidate_activity_id, candidate_profile_id)
    references public.trace_candidate_career_activities (candidate_activity_id, candidate_profile_id) on delete cascade;
alter table public.trace_candidate_skills
  drop constraint trace_candidate_skills_candidate_employment_id_candidate_p_fkey,
  add constraint trace_candidate_skills_candidate_employment_id_candidate_p_fkey
    foreign key (candidate_employment_id, candidate_profile_id)
    references public.trace_candidate_employments (candidate_employment_id, candidate_profile_id) on delete cascade;
alter table public.trace_candidate_skills
  drop constraint trace_candidate_skills_activity_fkey,
  add constraint trace_candidate_skills_activity_fkey
    foreign key (candidate_activity_id, candidate_profile_id)
    references public.trace_candidate_career_activities (candidate_activity_id, candidate_profile_id) on delete cascade;
alter table public.trace_candidate_skills
  drop constraint trace_candidate_skills_education_fkey,
  add constraint trace_candidate_skills_education_fkey
    foreign key (candidate_education_id, candidate_profile_id)
    references public.trace_candidate_education (candidate_education_id, candidate_profile_id) on delete cascade;
alter table public.trace_candidate_skills
  drop constraint trace_candidate_skills_evidence_fkey,
  add constraint trace_candidate_skills_evidence_fkey
    foreign key (candidate_evidence_id, candidate_profile_id)
    references public.trace_candidate_evidence (candidate_evidence_id, candidate_profile_id) on delete cascade;

-- ------------------------------------------------------------------ legacy columns
alter table public.trace_candidate_skills alter column evidence_source set not null;
alter table public.trace_candidate_employers alter column attribute_provenance set default 'unknown';
alter table public.trace_candidate_employers alter column attribute_provenance set not null;
alter table public.trace_candidate_evidence alter column provenance set default 'resume_claim';
alter table public.trace_candidate_evidence alter column provenance set not null;

-- ------------------------------------------------------------------ grants (pre-M2 state)
do $$
declare
  t text;
  v_all text;
  v_cols text;
begin
  foreach t in array array['trace_candidate_profiles', 'trace_candidate_employers', 'trace_candidate_employments',
                           'trace_candidate_education', 'trace_candidate_career_breaks', 'trace_candidate_career_activities',
                           'trace_candidate_evidence', 'trace_candidate_skills']
  loop
    select string_agg(quote_ident(c.column_name), ', ' order by c.ordinal_position) into v_all
    from information_schema.columns c where c.table_schema = 'public' and c.table_name = t;
    execute format('revoke insert (%s), update (%s) on public.%I from authenticated, service_role', v_all, v_all, t);
    execute format('grant all on public.%I to service_role', t);
  end loop;

  -- 20261111120800: profiles
  grant insert (country_code, headline, resume_as_of_year, resume_as_of_month, resume_as_of_precision)
    on public.trace_candidate_profiles to authenticated;
  grant update (country_code, headline, resume_as_of_year, resume_as_of_month, resume_as_of_precision,
                confirmation_status, confirmed_at)
    on public.trace_candidate_profiles to authenticated;
  -- 20261111120800: employers keep table-level writes
  grant select, insert, update, delete on public.trace_candidate_employers to authenticated;
  -- 20261111140000: the six record tables
  foreach t in array array['trace_candidate_employments', 'trace_candidate_education', 'trace_candidate_career_breaks',
                           'trace_candidate_evidence', 'trace_candidate_skills', 'trace_candidate_career_activities']
  loop
    select string_agg(quote_ident(c.column_name), ', ' order by c.ordinal_position) into v_cols
    from information_schema.columns c
    where c.table_schema = 'public' and c.table_name = t and c.is_generated = 'NEVER'
      and c.column_name not in ('information_origin', 'verification_status', 'extraction_confidence',
                                'source_resume_id', 'extraction_run_id', 'source_location',
                                'evidence_status', 'verified');
    execute format('grant insert (%s), update (%s) on public.%I to authenticated', v_cols, v_cols, t);
  end loop;
end
$$;
grant all on public.trace_resume_documents, public.trace_extraction_runs to service_role;

commit;
