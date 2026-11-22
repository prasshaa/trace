-- Compensation for migration 20261111170000_trace_candidate_corrections (v2.5.0, M4).
--
-- NOT a migration: it lives outside supabase/migrations and is never applied automatically.
--
--   1. Deploy the backend without corrections (M3, commit 2a8f9ab) first, or stop calling
--      CandidateProfileService.correct_record. Corrections that commit before this script takes its locks
--      are counted by the acknowledgement check; corrections started later wait for the script and then
--      fail closed (correct_record no longer exists). No other operation is affected beyond waiting.
--   2. Run this script as postgres. It carries its own BEGIN and COMMIT: any error rolls all of it back
--      and leaves the v2.5.0 catalog intact. Do NOT add psql --single-transaction. Use:
--        psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>
--   3. Mark the migration as reverted:
--        supabase migration repair 20261111170000 --status reverted   (add --local for the local stack)
--
-- What it restores: the exact v2.4.0 catalog. It drops correction history, its guards and the deferred
-- check, trace_review.correct_record, the writer's correction grants and policies, and the (id, profile)
-- keys added for history; it restores the v2.3.0/v2.4.0 guard G1 (copied verbatim from migration
-- 20261111150000).
--
-- Data: corrected records keep their latest values and review_status 'user_corrected' (a valid v2.4.0
-- state, sequenced like confirmed). Correction history, including the original values it preserves, is
-- PERMANENTLY DISCARDED: re-applying the migration recreates an empty history, and the originals of
-- already-corrected records cannot be recovered from the database. The script therefore refuses while
-- history exists (checked under the locks above) unless the operator acknowledges the loss explicitly
-- for this session (it cannot be set as a role or database default by postgres), for example:
--   PGOPTIONS='-c trace.rollback_discard_correction_history=on' psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>
-- Export the history first if it must be kept.

begin;

-- Locks, held until COMMIT, taken before the acknowledgement check so that no correction can commit
-- history between the check and the DROP TABLE below:
--   1. the six correctable record tables, ACCESS EXCLUSIVE (the lock the DROP TRIGGERs below take anyway).
--      This waits for every in-flight correction (each holds its record table from its FOR UPDATE onwards)
--      to commit or roll back, and makes every later correction wait until this script has finished,
--      after which correct_record no longer exists (fail closed). Record tables come first because a
--      correction locks its record before it writes history: the reverse order could deadlock.
--   2. correction history, ACCESS EXCLUSIVE (a superset of EXCLUSIVE: it also blocks reads, and it is the
--      lock DROP TABLE takes anyway, so it is never upgraded later, which could deadlock with a reader).
-- Reads and writes of these tables wait while the script runs. Set lock_timeout to bound the wait.
lock table public.trace_candidate_employments, public.trace_candidate_education, public.trace_candidate_career_activities,
           public.trace_candidate_evidence, public.trace_candidate_skills, public.trace_candidate_career_breaks
  in access exclusive mode;
lock table public.trace_candidate_corrections in access exclusive mode;

do $$
begin
  if exists (select 1 from public.trace_candidate_corrections)
     and coalesce(current_setting('trace.rollback_discard_correction_history', true), '') <> 'on' then
    raise exception 'rollback stopped: % correction history entries would be discarded, including original values. '
                    'Export them, then re-run with trace.rollback_discard_correction_history=on.',
                    (select count(*) from public.trace_candidate_corrections);
  end if;
end
$$;

-- ------------------------------------------------------------------ deferred check and history
drop trigger trace_require_correction_history on public.trace_candidate_employments;
drop trigger trace_require_correction_history on public.trace_candidate_education;
drop trigger trace_require_correction_history on public.trace_candidate_career_activities;
drop trigger trace_require_correction_history on public.trace_candidate_evidence;
drop trigger trace_require_correction_history on public.trace_candidate_skills;
drop trigger trace_require_correction_history on public.trace_candidate_career_breaks;
drop function trace_util.require_correction_history();
drop table public.trace_candidate_corrections;        -- its guard triggers and policies go with it
drop function trace_util.guard_correction_history();

-- ------------------------------------------------------------------ correct_record and the writer's correction grants
grant trace_review_writer to postgres with inherit true, set true;
drop function trace_review.correct_record(text, uuid, uuid, jsonb);
revoke trace_review_writer from postgres;
revoke update (sequence_index, title_raw, employment_type, primary_career_sequence, counts_as_full_time_experience, classification_note, start_year, start_month, start_precision, end_year, end_month, end_precision, is_current_as_stated) on public.trace_candidate_employments from trace_review_writer;
revoke update (qualification_title_raw, qualification_level, institution_name, field_of_study, study_mode, completion_status, notes, start_year, start_month, start_precision, end_year, end_month, end_precision) on public.trace_candidate_education from trace_review_writer;
revoke update (activity_type, title_raw, organisation_raw, description_raw, contribution_raw, outcome_raw, link_url, status, credential_identifier_raw, start_year, start_month, start_precision, end_year, end_month, end_precision, expiry_year, expiry_month, expiry_precision, metadata) on public.trace_candidate_career_activities from trace_review_writer;
revoke update (evidence_type, evidence_text) on public.trace_candidate_evidence from trace_review_writer;
revoke update (skill_text_raw, stated_proficiency_raw) on public.trace_candidate_skills from trace_review_writer;
revoke update (break_reason, note, start_year, start_month, start_precision, end_year, end_month, end_precision) on public.trace_candidate_career_breaks from trace_review_writer;

-- ------------------------------------------------------------------ G1 as in v2.3.0 / v2.4.0 (verbatim)
create or replace function trace_util.guard_review_row()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_old jsonb := to_jsonb(OLD);
  v_new jsonb := to_jsonb(NEW);
  v_changed text[] := '{}';
  v_col text;
  v_old_status text := v_old ->> 'review_status';
  v_new_status text := v_new ->> 'review_status';
  v_status_changed boolean;
  v_employer_changed boolean;
  v_parent uuid;
  v_parent_exists boolean;
begin
  foreach v_col in array TG_ARGV loop
    if v_old -> v_col is distinct from v_new -> v_col then
      v_changed := v_changed || v_col;
    end if;
  end loop;
  v_status_changed := v_old_status is distinct from v_new_status;
  v_employer_changed := v_old -> 'candidate_employer_id' is distinct from v_new -> 'candidate_employer_id';
  if v_employer_changed then
    v_changed := v_changed || 'candidate_employer_id'::text;
  end if;

  -- (d) referential action: ON DELETE SET NULL (link) after the parent row was deleted.
  if not v_status_changed and cardinality(v_changed) = 1 and trace_util.in_referential_action(TG_RELID) then
    v_col := v_changed[1];
    if v_new -> v_col = 'null'::jsonb and v_col in ('candidate_employer_id', 'related_employment_id', 'candidate_employment_id',
                                                     'candidate_activity_id', 'candidate_education_id', 'candidate_evidence_id') then
      v_parent := (v_old ->> v_col)::uuid;
      if v_col = 'candidate_employer_id' then
        v_parent_exists := exists (select 1 from public.trace_candidate_employers p where p.candidate_employer_id = v_parent);
      elsif v_col in ('related_employment_id', 'candidate_employment_id') then
        v_parent_exists := exists (select 1 from public.trace_candidate_employments p where p.candidate_employment_id = v_parent);
      elsif v_col = 'candidate_activity_id' then
        v_parent_exists := exists (select 1 from public.trace_candidate_career_activities p where p.candidate_activity_id = v_parent);
      elsif v_col = 'candidate_education_id' then
        v_parent_exists := exists (select 1 from public.trace_candidate_education p where p.candidate_education_id = v_parent);
      else
        v_parent_exists := exists (select 1 from public.trace_candidate_evidence p where p.candidate_evidence_id = v_parent);
      end if;
      if not v_parent_exists then
        if v_old_status in ('user_confirmed', 'user_corrected')
           and not trace_util.owner_data_gone((v_old ->> 'owner_user_id')::uuid, (v_old ->> 'candidate_profile_id')::uuid) then
          raise exception using errcode = 'TR008',
            message = format('deleting the parent would detach a %s record in %s', v_old_status, TG_TABLE_NAME);
        end if;
        return NEW;
      end if;
    end if;
  end if;

  -- (b) review status
  if v_status_changed then
    if current_user <> 'trace_review_writer' then
      raise exception using errcode = 'TR003',
        message = 'review status changes only through trace_review.review_record';
    end if;
    if cardinality(v_changed) > 0 then
      raise exception using errcode = 'TR003', message = 'a review action cannot change record content';
    end if;
    if not ((v_old_status = 'pending_review' and v_new_status = 'user_confirmed')
         or (v_old_status in ('pending_review', 'user_confirmed', 'user_corrected') and v_new_status = 'rejected')
         or (v_old_status = 'rejected' and v_new_status = 'pending_review')) then
      raise exception using errcode = 'TR003',
        message = format('invalid review transition %s -> %s', v_old_status, v_new_status);
    end if;
    return NEW;
  end if;

  if cardinality(v_changed) = 0 then
    return NEW;
  end if;

  -- (c) employer link
  if v_employer_changed and current_user <> 'trace_review_writer' then
    raise exception using errcode = 'TR002',
      message = 'an employment''s employer changes only through trace_review.reassign_employment';
  end if;

  -- (a) draft-only content
  if v_old_status = 'rejected' then
    raise exception using errcode = 'TR001', message = 'a rejected record cannot be edited; reinstate it first';
  end if;
  if v_old_status <> 'pending_review' or (v_old ->> 'information_origin') is distinct from 'user_stated' then
    raise exception using errcode = 'TR002',
      message = format('the record is %s with origin %s; correcting it needs correction history, which is not available yet',
                       v_old_status, coalesce(v_old ->> 'information_origin', 'not recorded'));
  end if;
  return NEW;
end
$$;

-- ------------------------------------------------------------------ keys added for the history foreign keys
alter table public.trace_candidate_skills drop constraint trace_candidate_skills_id_profile_key;
alter table public.trace_candidate_career_breaks drop constraint trace_candidate_career_breaks_id_profile_key;

commit;
