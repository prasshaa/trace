-- Trace data foundation v2.5.0: correction history for candidate records (M4).
--
-- A candidate corrects a reviewed, extracted, inferred or legacy record through one controlled path,
-- trace_review.correct_record. The record keeps the latest value; every correction is an append-only
-- event in public.trace_candidate_corrections holding the changed fields' previous and corrected
-- values, so the original value of every field stays recoverable.
--
-- Rules:
--   * Records: employments, education, career activities, evidence, skills and career breaks. Fields:
--     scalar content only. Employers, links between records and taxonomy references (skill_id,
--     responsibility_area) are not correctable; review, mapping, provenance, verification, identity and
--     timestamps never are. information_origin, source_resume_id, extraction_run_id, source_location and
--     extraction_confidence stay as they were: they describe where the record came from.
--   * A correction always results in review_status = 'user_corrected' (a review act by the candidate).
--     Allowed from pending_review (extracted, inferred or legacy origin), user_confirmed and
--     user_corrected. Rejected records must be reinstated first (TR001). The candidate's own pending
--     user_stated drafts keep the ordinary draft edit (no history, still pending) (TR002).
--   * correct_record is owned by trace_review_writer, SECURITY DEFINER, empty search_path, fixed per-kind
--     field allow-lists and fixed UPDATE statements, no dynamic SQL, executable by authenticated only, and
--     acts for auth.uid() only. Unknown or protected fields are rejected (TR006).
--   * The guard G1 allows content changes on non-draft rows only in that shape (as trace_review_writer,
--     together with user_corrected). A deferred constraint trigger requires, for every such update, a
--     history row of the same record, profile, owner and transaction whose changed fields, previous and
--     corrected values match the update exactly (TR017).
--   * History is written only by trace_review_writer, i.e. by correct_record (TR016 for any other role,
--     including the table owner), and is append-only for every role including the table owner (TR016):
--     no UPDATE, no TRUNCATE, and DELETE only through the cascades of record, profile or account deletion.
--     These guards are triggers: the table owner can still disable them (administrative access).
--
-- Error codes (in addition to TR001-TR015): TR016 history is append-only; TR017 correction without history.

-- ------------------------------------------------------------------ (id, profile) keys for the history foreign keys
alter table public.trace_candidate_skills add constraint trace_candidate_skills_id_profile_key unique (candidate_skill_id, candidate_profile_id);
alter table public.trace_candidate_career_breaks add constraint trace_candidate_career_breaks_id_profile_key unique (candidate_break_id, candidate_profile_id);

-- ------------------------------------------------------------------ correction history
create table public.trace_candidate_corrections (
  correction_id        uuid primary key default gen_random_uuid(),
  correction_seq       bigint generated always as identity unique,     -- total order, also within one transaction
  owner_user_id        uuid not null references auth.users(id) on delete cascade,
  candidate_profile_id uuid not null,
  record_kind          text not null check (record_kind in ('employment', 'education', 'activity', 'evidence', 'skill', 'career_break')),
  record_id            uuid not null,
  changed_fields       text[] not null check (cardinality(changed_fields) >= 1),
  previous_values      jsonb not null check (jsonb_typeof(previous_values) = 'object'),
  corrected_values     jsonb not null check (jsonb_typeof(corrected_values) = 'object'),
  prior_review_status  public.trace_record_review_status not null check (prior_review_status <> 'rejected'),
  corrected_at         timestamptz not null default now(),
  corrected_by         uuid not null,
  txid                 bigint not null default txid_current(),          -- the correcting transaction
  -- One generated link per record kind, so that deleting the record removes its history (and only then).
  employment_id        uuid generated always as (case when record_kind = 'employment' then record_id end) stored,
  education_id         uuid generated always as (case when record_kind = 'education' then record_id end) stored,
  activity_id          uuid generated always as (case when record_kind = 'activity' then record_id end) stored,
  evidence_id          uuid generated always as (case when record_kind = 'evidence' then record_id end) stored,
  skill_record_id      uuid generated always as (case when record_kind = 'skill' then record_id end) stored,
  career_break_id      uuid generated always as (case when record_kind = 'career_break' then record_id end) stored,
  foreign key (candidate_profile_id, owner_user_id) references public.trace_candidate_profiles(candidate_profile_id, owner_user_id) on delete cascade,
  foreign key (employment_id, candidate_profile_id) references public.trace_candidate_employments(candidate_employment_id, candidate_profile_id) on delete cascade,
  foreign key (education_id, candidate_profile_id) references public.trace_candidate_education(candidate_education_id, candidate_profile_id) on delete cascade,
  foreign key (activity_id, candidate_profile_id) references public.trace_candidate_career_activities(candidate_activity_id, candidate_profile_id) on delete cascade,
  foreign key (evidence_id, candidate_profile_id) references public.trace_candidate_evidence(candidate_evidence_id, candidate_profile_id) on delete cascade,
  foreign key (skill_record_id, candidate_profile_id) references public.trace_candidate_skills(candidate_skill_id, candidate_profile_id) on delete cascade,
  foreign key (career_break_id, candidate_profile_id) references public.trace_candidate_career_breaks(candidate_break_id, candidate_profile_id) on delete cascade
);
create index trace_candidate_corrections_record_idx on public.trace_candidate_corrections (record_kind, record_id, correction_seq);
create index trace_candidate_corrections_profile_idx on public.trace_candidate_corrections (candidate_profile_id, owner_user_id);
alter table public.trace_candidate_corrections enable row level security;

-- Written only by trace_review_writer (trace_review.correct_record is the writer's only function that
-- inserts here) and append-only, for every role including the table owner while this trigger is enabled.
-- The owner can still disable triggers (administrative access, as for every guard since v2.3.0).
create function trace_util.guard_correction_history()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if TG_OP = 'INSERT' and current_user = 'trace_review_writer' then
    return NEW;          -- trace_review.correct_record
  end if;
  if TG_OP = 'DELETE' and trace_util.in_referential_action(TG_RELID) then
    return OLD;          -- the record, profile or account is being deleted (foreign-key cascade)
  end if;
  if TG_OP = 'INSERT' then
    raise exception using errcode = 'TR016', message = 'correction history is written only by trace_review.correct_record';
  end if;
  raise exception using errcode = 'TR016', message = 'correction history is append-only';
end
$$;
create trigger trace_guard_correction_history before insert or update or delete on public.trace_candidate_corrections
  for each row execute function trace_util.guard_correction_history();
create trigger trace_guard_correction_history_truncate before truncate on public.trace_candidate_corrections
  for each statement execute function trace_util.guard_correction_history();

-- ------------------------------------------------------------------ G1: review rows
-- Arguments: the table's candidate UPDATE columns. Rules, in order:
--   (d) a referential action that nulls exactly one link column whose parent row is gone is allowed;
--       for a reviewed row only while its profile or account is being deleted (TR008 otherwise);
--   (e) v2.5.0: a correction (content change together with review_status = 'user_corrected') only as
--       trace_review_writer (trace_review.correct_record), never on a rejected record or on the
--       candidate's own pending user_stated draft, never on links or taxonomy references;
--   (b) review_status changes only as trace_review_writer, alone, and only along the M1 matrix (TR003);
--   (c) candidate_employer_id changes only as trace_review_writer (TR002);
--   (a) any content change needs a pending_review, user_stated row (TR001 rejected, TR002 otherwise).
-- System columns (origin, verification, mapping, sources) are governed by the column grants.
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

  -- (e) correction (v2.5.0)
  if current_user = 'trace_review_writer' and v_new_status = 'user_corrected' and cardinality(v_changed) > 0 then
    if v_old_status = 'rejected' then
      raise exception using errcode = 'TR001', message = 'a rejected record cannot be corrected; reinstate it first';
    end if;
    if v_old_status = 'pending_review' and (v_old ->> 'information_origin') is not distinct from 'user_stated' then
      raise exception using errcode = 'TR002', message = 'a pending user_stated draft is edited, not corrected';
    end if;
    if v_old_status not in ('pending_review', 'user_confirmed', 'user_corrected') then
      raise exception using errcode = 'TR003', message = format('a %s record cannot be corrected', v_old_status);
    end if;
    if v_changed && array['candidate_employer_id', 'related_employment_id', 'candidate_employment_id', 'candidate_activity_id',
                          'candidate_education_id', 'candidate_evidence_id', 'skill_id', 'responsibility_area'] then
      raise exception using errcode = 'TR002', message = 'links and taxonomy references are not correctable';
    end if;
    return NEW;
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

-- ------------------------------------------------------------------ correct_record
create function trace_review.correct_record(p_kind text, p_candidate_profile_id uuid, p_record_id uuid, p_changes jsonb)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  v_allowed text[];
  v_bad text;
  v_old jsonb;
  v_new jsonb;
  v_after jsonb;
  v_changed text[];
begin
  if v_uid is null then
    raise exception using errcode = 'TR004', message = 'no request identity';
  end if;
  if p_kind = 'employment' then
    v_allowed := array['sequence_index', 'title_raw', 'employment_type', 'primary_career_sequence', 'counts_as_full_time_experience', 'classification_note', 'start_year', 'start_month', 'start_precision', 'end_year', 'end_month', 'end_precision', 'is_current_as_stated'];
  elsif p_kind = 'education' then
    v_allowed := array['qualification_title_raw', 'qualification_level', 'institution_name', 'field_of_study', 'study_mode', 'completion_status', 'notes', 'start_year', 'start_month', 'start_precision', 'end_year', 'end_month', 'end_precision'];
  elsif p_kind = 'activity' then
    v_allowed := array['activity_type', 'title_raw', 'organisation_raw', 'description_raw', 'contribution_raw', 'outcome_raw', 'link_url', 'status', 'credential_identifier_raw', 'start_year', 'start_month', 'start_precision', 'end_year', 'end_month', 'end_precision', 'expiry_year', 'expiry_month', 'expiry_precision', 'metadata'];
  elsif p_kind = 'evidence' then
    v_allowed := array['evidence_type', 'evidence_text'];
  elsif p_kind = 'skill' then
    v_allowed := array['skill_text_raw', 'stated_proficiency_raw'];
  elsif p_kind = 'career_break' then
    v_allowed := array['break_reason', 'note', 'start_year', 'start_month', 'start_precision', 'end_year', 'end_month', 'end_precision'];
  else
    raise exception using errcode = 'TR006', message = format('%s records cannot be corrected', coalesce(p_kind, 'NULL'));
  end if;
  if p_changes is null or pg_catalog.jsonb_typeof(p_changes) <> 'object' or p_changes = '{}'::jsonb then
    raise exception using errcode = 'TR006', message = 'a correction names at least one field';
  end if;
  select string_agg(k, ', ' order by k) into v_bad from pg_catalog.jsonb_object_keys(p_changes) k where k <> all (v_allowed);
  if v_bad is not null then
    raise exception using errcode = 'TR006', message = format('field(s) not correctable for %s records: %s', p_kind, v_bad);
  end if;

  if p_kind = 'employment' then
    select to_jsonb(t) into v_old from public.trace_candidate_employments t
     where t.candidate_employment_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
       for update;
  elsif p_kind = 'education' then
    select to_jsonb(t) into v_old from public.trace_candidate_education t
     where t.candidate_education_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
       for update;
  elsif p_kind = 'activity' then
    select to_jsonb(t) into v_old from public.trace_candidate_career_activities t
     where t.candidate_activity_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
       for update;
  elsif p_kind = 'evidence' then
    select to_jsonb(t) into v_old from public.trace_candidate_evidence t
     where t.candidate_evidence_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
       for update;
  elsif p_kind = 'skill' then
    select to_jsonb(t) into v_old from public.trace_candidate_skills t
     where t.candidate_skill_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
       for update;
  elsif p_kind = 'career_break' then
    select to_jsonb(t) into v_old from public.trace_candidate_career_breaks t
     where t.candidate_break_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
       for update;
  end if;
  if v_old is null then
    raise exception using errcode = 'TR005', message = format('%s %s not found', p_kind, p_record_id);
  end if;
  if v_old ->> 'review_status' = 'rejected' then
    raise exception using errcode = 'TR001', message = 'a rejected record cannot be corrected; reinstate it first';
  end if;
  if v_old ->> 'review_status' = 'pending_review' and (v_old ->> 'information_origin') is not distinct from 'user_stated' then
    raise exception using errcode = 'TR002', message = 'a pending user_stated draft is edited, not corrected';
  end if;
  if not exists (select 1 from pg_catalog.jsonb_each(p_changes) e
                  where case when e.key = 'metadata' then v_old -> e.key is distinct from e.value
                             else v_old ->> e.key is distinct from p_changes ->> e.key end) then
    raise exception using errcode = 'TR006', message = 'the correction changes nothing';
  end if;

  -- The corrected row, typed exactly as the UPDATE below casts it. History is written first, so that the
  -- deferred check is satisfied whether it fires at commit or immediately after the UPDATE.
  if p_kind = 'employment' then
    v_new := to_jsonb(pg_catalog.jsonb_populate_record(null::public.trace_candidate_employments, v_old || p_changes));
  elsif p_kind = 'education' then
    v_new := to_jsonb(pg_catalog.jsonb_populate_record(null::public.trace_candidate_education, v_old || p_changes));
  elsif p_kind = 'activity' then
    v_new := to_jsonb(pg_catalog.jsonb_populate_record(null::public.trace_candidate_career_activities, v_old || p_changes));
  elsif p_kind = 'evidence' then
    v_new := to_jsonb(pg_catalog.jsonb_populate_record(null::public.trace_candidate_evidence, v_old || p_changes));
  elsif p_kind = 'skill' then
    v_new := to_jsonb(pg_catalog.jsonb_populate_record(null::public.trace_candidate_skills, v_old || p_changes));
  elsif p_kind = 'career_break' then
    v_new := to_jsonb(pg_catalog.jsonb_populate_record(null::public.trace_candidate_career_breaks, v_old || p_changes));
  end if;
  select array_agg(k order by k) into v_changed from unnest(v_allowed) k where v_old -> k is distinct from v_new -> k;
  if v_changed is null then
    raise exception using errcode = 'TR006', message = 'the correction changes nothing';
  end if;
  insert into public.trace_candidate_corrections (owner_user_id, candidate_profile_id, record_kind, record_id, changed_fields,
    previous_values, corrected_values, prior_review_status, corrected_by)
  values (v_uid, p_candidate_profile_id, p_kind, p_record_id, v_changed,
          (select pg_catalog.jsonb_object_agg(k, v_old -> k) from unnest(v_changed) k),
          (select pg_catalog.jsonb_object_agg(k, v_new -> k) from unnest(v_changed) k),
          (v_old ->> 'review_status')::public.trace_record_review_status, v_uid);

  if p_kind = 'employment' then
    update public.trace_candidate_employments t set
           sequence_index = case when p_changes ? 'sequence_index' then (p_changes ->> 'sequence_index')::integer else t.sequence_index end,
           title_raw = case when p_changes ? 'title_raw' then p_changes ->> 'title_raw' else t.title_raw end,
           employment_type = case when p_changes ? 'employment_type' then (p_changes ->> 'employment_type')::public.trace_employment_type else t.employment_type end,
           primary_career_sequence = case when p_changes ? 'primary_career_sequence' then (p_changes ->> 'primary_career_sequence')::boolean else t.primary_career_sequence end,
           counts_as_full_time_experience = case when p_changes ? 'counts_as_full_time_experience' then (p_changes ->> 'counts_as_full_time_experience')::boolean else t.counts_as_full_time_experience end,
           classification_note = case when p_changes ? 'classification_note' then p_changes ->> 'classification_note' else t.classification_note end,
           start_year = case when p_changes ? 'start_year' then (p_changes ->> 'start_year')::integer else t.start_year end,
           start_month = case when p_changes ? 'start_month' then (p_changes ->> 'start_month')::integer else t.start_month end,
           start_precision = case when p_changes ? 'start_precision' then (p_changes ->> 'start_precision')::public.trace_date_precision else t.start_precision end,
           end_year = case when p_changes ? 'end_year' then (p_changes ->> 'end_year')::integer else t.end_year end,
           end_month = case when p_changes ? 'end_month' then (p_changes ->> 'end_month')::integer else t.end_month end,
           end_precision = case when p_changes ? 'end_precision' then (p_changes ->> 'end_precision')::public.trace_date_precision else t.end_precision end,
           is_current_as_stated = case when p_changes ? 'is_current_as_stated' then (p_changes ->> 'is_current_as_stated')::boolean else t.is_current_as_stated end,
           review_status = 'user_corrected'
     where t.candidate_employment_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
    returning to_jsonb(t) into v_after;
  elsif p_kind = 'education' then
    update public.trace_candidate_education t set
           qualification_title_raw = case when p_changes ? 'qualification_title_raw' then p_changes ->> 'qualification_title_raw' else t.qualification_title_raw end,
           qualification_level = case when p_changes ? 'qualification_level' then p_changes ->> 'qualification_level' else t.qualification_level end,
           institution_name = case when p_changes ? 'institution_name' then p_changes ->> 'institution_name' else t.institution_name end,
           field_of_study = case when p_changes ? 'field_of_study' then p_changes ->> 'field_of_study' else t.field_of_study end,
           study_mode = case when p_changes ? 'study_mode' then p_changes ->> 'study_mode' else t.study_mode end,
           completion_status = case when p_changes ? 'completion_status' then p_changes ->> 'completion_status' else t.completion_status end,
           notes = case when p_changes ? 'notes' then p_changes ->> 'notes' else t.notes end,
           start_year = case when p_changes ? 'start_year' then (p_changes ->> 'start_year')::integer else t.start_year end,
           start_month = case when p_changes ? 'start_month' then (p_changes ->> 'start_month')::integer else t.start_month end,
           start_precision = case when p_changes ? 'start_precision' then (p_changes ->> 'start_precision')::public.trace_date_precision else t.start_precision end,
           end_year = case when p_changes ? 'end_year' then (p_changes ->> 'end_year')::integer else t.end_year end,
           end_month = case when p_changes ? 'end_month' then (p_changes ->> 'end_month')::integer else t.end_month end,
           end_precision = case when p_changes ? 'end_precision' then (p_changes ->> 'end_precision')::public.trace_date_precision else t.end_precision end,
           review_status = 'user_corrected'
     where t.candidate_education_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
    returning to_jsonb(t) into v_after;
  elsif p_kind = 'activity' then
    update public.trace_candidate_career_activities t set
           activity_type = case when p_changes ? 'activity_type' then (p_changes ->> 'activity_type')::public.trace_career_activity_type else t.activity_type end,
           title_raw = case when p_changes ? 'title_raw' then p_changes ->> 'title_raw' else t.title_raw end,
           organisation_raw = case when p_changes ? 'organisation_raw' then p_changes ->> 'organisation_raw' else t.organisation_raw end,
           description_raw = case when p_changes ? 'description_raw' then p_changes ->> 'description_raw' else t.description_raw end,
           contribution_raw = case when p_changes ? 'contribution_raw' then p_changes ->> 'contribution_raw' else t.contribution_raw end,
           outcome_raw = case when p_changes ? 'outcome_raw' then p_changes ->> 'outcome_raw' else t.outcome_raw end,
           link_url = case when p_changes ? 'link_url' then p_changes ->> 'link_url' else t.link_url end,
           status = case when p_changes ? 'status' then p_changes ->> 'status' else t.status end,
           credential_identifier_raw = case when p_changes ? 'credential_identifier_raw' then p_changes ->> 'credential_identifier_raw' else t.credential_identifier_raw end,
           start_year = case when p_changes ? 'start_year' then (p_changes ->> 'start_year')::integer else t.start_year end,
           start_month = case when p_changes ? 'start_month' then (p_changes ->> 'start_month')::integer else t.start_month end,
           start_precision = case when p_changes ? 'start_precision' then (p_changes ->> 'start_precision')::public.trace_date_precision else t.start_precision end,
           end_year = case when p_changes ? 'end_year' then (p_changes ->> 'end_year')::integer else t.end_year end,
           end_month = case when p_changes ? 'end_month' then (p_changes ->> 'end_month')::integer else t.end_month end,
           end_precision = case when p_changes ? 'end_precision' then (p_changes ->> 'end_precision')::public.trace_date_precision else t.end_precision end,
           expiry_year = case when p_changes ? 'expiry_year' then (p_changes ->> 'expiry_year')::integer else t.expiry_year end,
           expiry_month = case when p_changes ? 'expiry_month' then (p_changes ->> 'expiry_month')::integer else t.expiry_month end,
           expiry_precision = case when p_changes ? 'expiry_precision' then (p_changes ->> 'expiry_precision')::public.trace_date_precision else t.expiry_precision end,
           metadata = case when p_changes ? 'metadata' then p_changes -> 'metadata' else t.metadata end,
           review_status = 'user_corrected'
     where t.candidate_activity_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
    returning to_jsonb(t) into v_after;
  elsif p_kind = 'evidence' then
    update public.trace_candidate_evidence t set
           evidence_type = case when p_changes ? 'evidence_type' then p_changes ->> 'evidence_type' else t.evidence_type end,
           evidence_text = case when p_changes ? 'evidence_text' then p_changes ->> 'evidence_text' else t.evidence_text end,
           review_status = 'user_corrected'
     where t.candidate_evidence_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
    returning to_jsonb(t) into v_after;
  elsif p_kind = 'skill' then
    update public.trace_candidate_skills t set
           skill_text_raw = case when p_changes ? 'skill_text_raw' then p_changes ->> 'skill_text_raw' else t.skill_text_raw end,
           stated_proficiency_raw = case when p_changes ? 'stated_proficiency_raw' then p_changes ->> 'stated_proficiency_raw' else t.stated_proficiency_raw end,
           review_status = 'user_corrected'
     where t.candidate_skill_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
    returning to_jsonb(t) into v_after;
  elsif p_kind = 'career_break' then
    update public.trace_candidate_career_breaks t set
           break_reason = case when p_changes ? 'break_reason' then p_changes ->> 'break_reason' else t.break_reason end,
           note = case when p_changes ? 'note' then p_changes ->> 'note' else t.note end,
           start_year = case when p_changes ? 'start_year' then (p_changes ->> 'start_year')::integer else t.start_year end,
           start_month = case when p_changes ? 'start_month' then (p_changes ->> 'start_month')::integer else t.start_month end,
           start_precision = case when p_changes ? 'start_precision' then (p_changes ->> 'start_precision')::public.trace_date_precision else t.start_precision end,
           end_year = case when p_changes ? 'end_year' then (p_changes ->> 'end_year')::integer else t.end_year end,
           end_month = case when p_changes ? 'end_month' then (p_changes ->> 'end_month')::integer else t.end_month end,
           end_precision = case when p_changes ? 'end_precision' then (p_changes ->> 'end_precision')::public.trace_date_precision else t.end_precision end,
           review_status = 'user_corrected'
     where t.candidate_break_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
    returning to_jsonb(t) into v_after;
  end if;
  if exists (select 1 from unnest(v_allowed) k where v_after -> k is distinct from v_new -> k) then
    raise exception using errcode = 'TR017', message = 'the stored correction differs from its history entry';
  end if;
  return 'user_corrected';
end
$$;

-- ------------------------------------------------------------------ deferred check: no correction without its history
-- Arguments: record kind, primary-key column, correctable fields. Queued only for updates that leave a
-- record user_corrected (WHEN clause); at commit it requires history for a transition into that status
-- or a change of a correctable field.
create function trace_util.require_correction_history()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_old jsonb := to_jsonb(OLD);
  v_new jsonb := to_jsonb(NEW);
  v_kind text := TG_ARGV[0];
  v_id uuid := (to_jsonb(NEW) ->> TG_ARGV[1])::uuid;
  v_changed text[];
  v_exists boolean;
  i integer;
begin
  for i in 2 .. TG_NARGS - 1 loop
    if v_old -> TG_ARGV[i] is distinct from v_new -> TG_ARGV[i] then
      v_changed := pg_catalog.array_append(v_changed, TG_ARGV[i]);
    end if;
  end loop;
  if v_new ->> 'review_status' is distinct from 'user_corrected'
     or (v_old ->> 'review_status' = 'user_corrected' and v_changed is null) then
    return null;
  end if;
  -- A record deleted later in the same transaction took its history with it.
  if v_kind = 'employment' then
    v_exists := exists (select 1 from public.trace_candidate_employments t where t.candidate_employment_id = v_id);
  elsif v_kind = 'education' then
    v_exists := exists (select 1 from public.trace_candidate_education t where t.candidate_education_id = v_id);
  elsif v_kind = 'activity' then
    v_exists := exists (select 1 from public.trace_candidate_career_activities t where t.candidate_activity_id = v_id);
  elsif v_kind = 'evidence' then
    v_exists := exists (select 1 from public.trace_candidate_evidence t where t.candidate_evidence_id = v_id);
  elsif v_kind = 'skill' then
    v_exists := exists (select 1 from public.trace_candidate_skills t where t.candidate_skill_id = v_id);
  elsif v_kind = 'career_break' then
    v_exists := exists (select 1 from public.trace_candidate_career_breaks t where t.candidate_break_id = v_id);
  end if;
  if not v_exists then
    return null;
  end if;
  if v_changed is null or not exists (
       select 1 from public.trace_candidate_corrections h
        where h.record_kind = v_kind and h.record_id = v_id
          and h.candidate_profile_id = (v_new ->> 'candidate_profile_id')::uuid
          and h.owner_user_id = (v_new ->> 'owner_user_id')::uuid
          and h.txid = pg_catalog.txid_current()
          and h.prior_review_status::text = v_old ->> 'review_status'
          and (select array_agg(x order by x) from unnest(h.changed_fields) x) = (select array_agg(x order by x) from unnest(v_changed) x)
          and (select count(*) from pg_catalog.jsonb_object_keys(h.previous_values)) = cardinality(v_changed)
          and (select count(*) from pg_catalog.jsonb_object_keys(h.corrected_values)) = cardinality(v_changed)
          and not exists (select 1 from unnest(v_changed) k
                           where h.previous_values -> k is distinct from v_old -> k or h.corrected_values -> k is distinct from v_new -> k)) then
    raise exception using errcode = 'TR017',
      message = format('%s %s became user_corrected without its correction history', v_kind, v_id);
  end if;
  return null;
end
$$;
create constraint trigger trace_require_correction_history after update on public.trace_candidate_employments
  deferrable initially deferred for each row
  when (NEW.review_status = 'user_corrected')
  execute function trace_util.require_correction_history('employment', 'candidate_employment_id', 'sequence_index', 'title_raw', 'employment_type', 'primary_career_sequence', 'counts_as_full_time_experience', 'classification_note', 'start_year', 'start_month', 'start_precision', 'end_year', 'end_month', 'end_precision', 'is_current_as_stated');
create constraint trigger trace_require_correction_history after update on public.trace_candidate_education
  deferrable initially deferred for each row
  when (NEW.review_status = 'user_corrected')
  execute function trace_util.require_correction_history('education', 'candidate_education_id', 'qualification_title_raw', 'qualification_level', 'institution_name', 'field_of_study', 'study_mode', 'completion_status', 'notes', 'start_year', 'start_month', 'start_precision', 'end_year', 'end_month', 'end_precision');
create constraint trigger trace_require_correction_history after update on public.trace_candidate_career_activities
  deferrable initially deferred for each row
  when (NEW.review_status = 'user_corrected')
  execute function trace_util.require_correction_history('activity', 'candidate_activity_id', 'activity_type', 'title_raw', 'organisation_raw', 'description_raw', 'contribution_raw', 'outcome_raw', 'link_url', 'status', 'credential_identifier_raw', 'start_year', 'start_month', 'start_precision', 'end_year', 'end_month', 'end_precision', 'expiry_year', 'expiry_month', 'expiry_precision', 'metadata');
create constraint trigger trace_require_correction_history after update on public.trace_candidate_evidence
  deferrable initially deferred for each row
  when (NEW.review_status = 'user_corrected')
  execute function trace_util.require_correction_history('evidence', 'candidate_evidence_id', 'evidence_type', 'evidence_text');
create constraint trigger trace_require_correction_history after update on public.trace_candidate_skills
  deferrable initially deferred for each row
  when (NEW.review_status = 'user_corrected')
  execute function trace_util.require_correction_history('skill', 'candidate_skill_id', 'skill_text_raw', 'stated_proficiency_raw');
create constraint trigger trace_require_correction_history after update on public.trace_candidate_career_breaks
  deferrable initially deferred for each row
  when (NEW.review_status = 'user_corrected')
  execute function trace_util.require_correction_history('career_break', 'candidate_break_id', 'break_reason', 'note', 'start_year', 'start_month', 'start_precision', 'end_year', 'end_month', 'end_precision');

-- ------------------------------------------------------------------ privileges and RLS
revoke all on public.trace_candidate_corrections from public, anon, authenticated, service_role;
grant select on public.trace_candidate_corrections to authenticated, service_role;
grant insert (owner_user_id, candidate_profile_id, record_kind, record_id, changed_fields, previous_values, corrected_values,
              prior_review_status, corrected_by) on public.trace_candidate_corrections to trace_review_writer;
-- The deferred check runs as whichever role is current when it fires: authenticated at commit, or the
-- writer inside correct_record when constraints are immediate. Both must see the owner's history.
grant select on public.trace_candidate_corrections to trace_review_writer;
create policy "candidate corrections owner select" on public.trace_candidate_corrections
  for select to authenticated using (owner_user_id = (select auth.uid()));
create policy "candidate corrections review writer select" on public.trace_candidate_corrections
  for select to trace_review_writer using (owner_user_id = (select trace_review.request_uid()));
create policy "candidate corrections review writer insert" on public.trace_candidate_corrections
  for insert to trace_review_writer with check (owner_user_id = (select trace_review.request_uid())
                                                and corrected_by = (select trace_review.request_uid()));
grant update (sequence_index, title_raw, employment_type, primary_career_sequence, counts_as_full_time_experience, classification_note, start_year, start_month, start_precision, end_year, end_month, end_precision, is_current_as_stated) on public.trace_candidate_employments to trace_review_writer;
grant update (qualification_title_raw, qualification_level, institution_name, field_of_study, study_mode, completion_status, notes, start_year, start_month, start_precision, end_year, end_month, end_precision) on public.trace_candidate_education to trace_review_writer;
grant update (activity_type, title_raw, organisation_raw, description_raw, contribution_raw, outcome_raw, link_url, status, credential_identifier_raw, start_year, start_month, start_precision, end_year, end_month, end_precision, expiry_year, expiry_month, expiry_precision, metadata) on public.trace_candidate_career_activities to trace_review_writer;
grant update (evidence_type, evidence_text) on public.trace_candidate_evidence to trace_review_writer;
grant update (skill_text_raw, stated_proficiency_raw) on public.trace_candidate_skills to trace_review_writer;
grant update (break_reason, note, start_year, start_month, start_precision, end_year, end_month, end_precision) on public.trace_candidate_career_breaks to trace_review_writer;

revoke all on function trace_util.guard_correction_history(), trace_util.require_correction_history(),
  trace_review.correct_record(text, uuid, uuid, jsonb) from public, anon, authenticated, service_role;
grant execute on function trace_review.correct_record(text, uuid, uuid, jsonb) to authenticated;

-- ------------------------------------------------------------------ ownership transfer (the M2 procedure)
grant trace_review_writer to postgres with set true, inherit false;
grant create on schema trace_review to trace_review_writer;
alter function trace_review.correct_record(text, uuid, uuid, jsonb) owner to trace_review_writer;
revoke create on schema trace_review from trace_review_writer;
revoke trace_review_writer from postgres;

do $$
declare
  v_bad text;
begin
  if pg_catalog.pg_has_role('postgres', 'trace_review_writer', 'SET') or pg_catalog.pg_has_role('postgres', 'trace_review_writer', 'USAGE') then
    raise exception 'postgres must not be able to act as trace_review_writer';
  end if;
  if exists (select 1 from pg_catalog.pg_auth_members m
              where m.roleid = 'trace_review_writer'::regrole and (m.set_option or m.inherit_option)) then
    raise exception 'trace_review_writer must have no members that can use it';
  end if;
  if pg_catalog.has_schema_privilege('trace_review_writer', 'trace_review', 'CREATE') then
    raise exception 'trace_review_writer must not keep CREATE on trace_review';
  end if;
  select string_agg(p.oid::regprocedure::text || ' owned by ' || pg_catalog.pg_get_userbyid(p.proowner), '; ') into v_bad
  from pg_catalog.pg_proc p
  where p.pronamespace = 'trace_review'::regnamespace
    and pg_catalog.pg_get_userbyid(p.proowner) <> case when p.proname in ('review_record', 'reassign_employment', 'correct_record')
                                                       then 'trace_review_writer' else 'postgres' end;
  if v_bad is not null then
    raise exception 'unexpected trace_review function owner: %', v_bad;
  end if;
  if exists (select 1 from pg_catalog.pg_class c cross join (values ('authenticated'), ('service_role'), ('anon'), ('trace_review_writer')) r(rolname)
              cross join (values ('UPDATE'), ('DELETE'), ('TRUNCATE')) x(p)
              where c.oid = 'public.trace_candidate_corrections'::regclass and pg_catalog.has_table_privilege(r.rolname, c.oid, x.p)) then
    raise exception 'correction history must not be updatable, deletable or truncatable by any client or writer role';
  end if;
end
$$;
