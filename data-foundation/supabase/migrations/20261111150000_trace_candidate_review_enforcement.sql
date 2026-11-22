-- Trace data foundation v2.3.0: database-level review enforcement for candidate profiles (M2).
--
-- What this migration enforces, for every role that is not the table owner:
--   * Candidates (authenticated) and server jobs (service_role) insert only candidate-authored content.
--     Review status, role and activity-taxonomy mapping, provenance, verification, extraction and legacy
--     trust columns cannot be supplied (column grants), and G0 rejects any value that reaches them
--     anyway (TR011). Every ordinary insert is pending_review / user_stated / unverified.
--   * Content of a record can change only while it is the candidate's own draft (pending_review and
--     user_stated). Confirmed, corrected, rejected, extracted, inferred and legacy rows are read-only
--     (G1, G2), for every role including the owner.
--   * Review status changes only inside trace_review.review_record, and an employment's employer only
--     inside trace_review.reassign_employment. Both run as the dedicated role trace_review_writer
--     (NOLOGIN, NOBYPASSRLS, no members), act only for the current request's auth.uid(), and follow the
--     M1 transition matrix and lock order.
--   * Deleting a parent never cascade-deletes or silently detaches a reviewed (user_confirmed or
--     user_corrected) supporting record (G4 to G8, TR008). Pending and rejected dependents are detached:
--     the foreign key nulls only the link column. A resume that backs a source_document_supported
--     record cannot be deleted (G3, TR009).
--   * Profile and account deletion still remove everything. Foreign-key actions are recognised only
--     when all of these hold: trigger depth > 1, current_user is the table owner (PostgreSQL runs
--     referential actions as the referencing table's owner), the change is exactly a declared action,
--     and the parent row (or, for cascaded deletes, the whole profile or account) is already gone.
--     Depth alone never relaxes a rule.
--
-- Not in scope (documented limitations): a caller holding raw database credentials can forge request
-- claims with set_config; the table owner can disable triggers; service_role keeps cross-owner SELECT
-- and DELETE. See data-foundation/docs/ACCESS_CONTROL.md.
--
-- Error codes (SQLSTATE class TR):
--   TR001 rejected record is not editable          TR002 correction not available
--   TR003 review status not changeable / invalid   TR004 no request identity
--   TR005 record not found for this owner          TR006 invalid kind or reference
--   TR008 deletion would alter a reviewed record   TR009 resume supports document-verified records
--   TR010 detaching would duplicate a skill        TR011 system-controlled value on insert

-- ------------------------------------------------------------------ writer role and schema
-- Roles are cluster-wide and survive a local database reset, so creation is conditional; the
-- attributes are then (re)asserted unconditionally.
do $$
begin
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'trace_review_writer') then
    create role trace_review_writer nologin noinherit nosuperuser nocreatedb nocreaterole nobypassrls noreplication;
  end if;
end
$$;
alter role trace_review_writer nologin noinherit nocreatedb nocreaterole nobypassrls;

create schema trace_review;
revoke all on schema trace_review from public;
grant usage on schema trace_review to authenticated, trace_review_writer;

-- ------------------------------------------------------------------ helpers (invoker, read-only)
-- True only inside a statement issued from a trigger (depth > 1) while running as the table's
-- owner. Referential actions run as the referencing table's owner; candidates (authenticated),
-- server jobs (service_role) and the review writer never do.
create function trace_util.in_referential_action(p_relid oid)
returns boolean
language sql stable
set search_path = ''
as $$
select pg_catalog.pg_trigger_depth() > 1
   and current_user = (select pg_catalog.pg_get_userbyid(c.relowner) from pg_catalog.pg_class c where c.oid = p_relid)
$$;

create function trace_util.is_table_owner(p_relid oid)
returns boolean
language sql stable
set search_path = ''
as $$
select current_user = (select pg_catalog.pg_get_userbyid(c.relowner) from pg_catalog.pg_class c where c.oid = p_relid)
$$;

-- True when the record's profile or its owner's account no longer exists (profile or account
-- deletion in progress). Called only after in_referential_action(), i.e. as the table owner.
create function trace_util.owner_data_gone(p_owner uuid, p_profile uuid)
returns boolean
language sql stable
set search_path = ''
as $$
select not exists (select 1 from public.trace_candidate_profiles p where p.candidate_profile_id = p_profile)
    or not exists (select 1 from auth.users u where u.id = p_owner)
$$;

-- True when detaching the skills that link to p_parent (through p_link) would collide with an
-- identical unattached skill under trace_candidate_skills_claim_key.
create function trace_util.skill_detach_conflict(p_profile uuid, p_link text, p_parent uuid)
returns boolean
language sql stable
set search_path = ''
as $$
select exists (
  select 1
  from public.trace_candidate_skills s
  join public.trace_candidate_skills t
    on t.candidate_profile_id = s.candidate_profile_id
   and t.candidate_skill_id <> s.candidate_skill_id
   and coalesce(t.skill_id, 'raw:' || lower(btrim(t.skill_text_raw))) = coalesce(s.skill_id, 'raw:' || lower(btrim(s.skill_text_raw)))
   and t.evidence_source is not distinct from s.evidence_source
   and t.candidate_employment_id is null and t.candidate_education_id is null
   and t.candidate_activity_id is null and t.candidate_evidence_id is null
  where s.candidate_profile_id = p_profile
    and case p_link
          when 'candidate_employment_id' then s.candidate_employment_id
          when 'candidate_education_id' then s.candidate_education_id
          when 'candidate_activity_id' then s.candidate_activity_id
          when 'candidate_evidence_id' then s.candidate_evidence_id
        end = p_parent)
$$;

-- ------------------------------------------------------------------ legacy trust columns (decisions B, C)
-- New candidate rows leave these NULL ("no legacy label recorded"); information_origin, review_status
-- and verification_status are authoritative. Existing values are not rewritten.
alter table public.trace_candidate_skills alter column evidence_source drop not null;
alter table public.trace_candidate_employers alter column attribute_provenance drop not null;
alter table public.trace_candidate_employers alter column attribute_provenance drop default;
alter table public.trace_candidate_evidence alter column provenance drop not null;
alter table public.trace_candidate_evidence alter column provenance drop default;

-- ------------------------------------------------------------------ supporting-record links (decision A)
-- Deleting a parent no longer cascade-deletes its supporting records. Only the link column is nulled,
-- and the delete guards below refuse when a dependent is reviewed.
alter table public.trace_candidate_evidence
  drop constraint trace_candidate_evidence_candidate_employment_id_candidate_fkey,
  add constraint trace_candidate_evidence_candidate_employment_id_candidate_fkey
    foreign key (candidate_employment_id, candidate_profile_id)
    references public.trace_candidate_employments (candidate_employment_id, candidate_profile_id)
    on delete set null (candidate_employment_id);
alter table public.trace_candidate_evidence
  drop constraint trace_candidate_evidence_activity_fkey,
  add constraint trace_candidate_evidence_activity_fkey
    foreign key (candidate_activity_id, candidate_profile_id)
    references public.trace_candidate_career_activities (candidate_activity_id, candidate_profile_id)
    on delete set null (candidate_activity_id);
alter table public.trace_candidate_skills
  drop constraint trace_candidate_skills_candidate_employment_id_candidate_p_fkey,
  add constraint trace_candidate_skills_candidate_employment_id_candidate_p_fkey
    foreign key (candidate_employment_id, candidate_profile_id)
    references public.trace_candidate_employments (candidate_employment_id, candidate_profile_id)
    on delete set null (candidate_employment_id);
alter table public.trace_candidate_skills
  drop constraint trace_candidate_skills_activity_fkey,
  add constraint trace_candidate_skills_activity_fkey
    foreign key (candidate_activity_id, candidate_profile_id)
    references public.trace_candidate_career_activities (candidate_activity_id, candidate_profile_id)
    on delete set null (candidate_activity_id);
alter table public.trace_candidate_skills
  drop constraint trace_candidate_skills_education_fkey,
  add constraint trace_candidate_skills_education_fkey
    foreign key (candidate_education_id, candidate_profile_id)
    references public.trace_candidate_education (candidate_education_id, candidate_profile_id)
    on delete set null (candidate_education_id);
alter table public.trace_candidate_skills
  drop constraint trace_candidate_skills_evidence_fkey,
  add constraint trace_candidate_skills_evidence_fkey
    foreign key (candidate_evidence_id, candidate_profile_id)
    references public.trace_candidate_evidence (candidate_evidence_id, candidate_profile_id)
    on delete set null (candidate_evidence_id);

-- ------------------------------------------------------------------ candidate column privileges
-- One list per table. INSERT gets every listed column; UPDATE gets the same list without the
-- identity columns (owner_user_id, candidate_profile_id) and without candidate_employer_id
-- (reassign_employment only). The same UPDATE lists are the guard triggers' arguments below, so the
-- grants and the content checks cannot drift apart (asserted by pgTAP).
create temporary table _trace_candidate_columns (table_name text primary key, insert_cols text[], update_cols text[]);
insert into _trace_candidate_columns values
  ('trace_candidate_profiles',
   array['owner_user_id', 'country_code', 'headline', 'resume_as_of_year', 'resume_as_of_month', 'resume_as_of_precision'],
   array['country_code', 'headline', 'resume_as_of_year', 'resume_as_of_month', 'resume_as_of_precision']),
  ('trace_candidate_employers',
   array['owner_user_id', 'candidate_profile_id', 'employer_name', 'industry_raw', 'industry_sector', 'company_model',
         'business_model', 'company_size_band'],
   array['employer_name', 'industry_raw', 'industry_sector', 'company_model', 'business_model', 'company_size_band']),
  ('trace_candidate_employments',
   array['owner_user_id', 'candidate_profile_id', 'candidate_employer_id', 'sequence_index', 'title_raw', 'employment_type',
         'primary_career_sequence', 'counts_as_full_time_experience', 'classification_note', 'start_year', 'start_month',
         'start_precision', 'end_year', 'end_month', 'end_precision', 'is_current_as_stated'],
   array['sequence_index', 'title_raw', 'employment_type', 'primary_career_sequence', 'counts_as_full_time_experience',
         'classification_note', 'start_year', 'start_month', 'start_precision', 'end_year', 'end_month', 'end_precision',
         'is_current_as_stated']),
  ('trace_candidate_education',
   array['owner_user_id', 'candidate_profile_id', 'qualification_title_raw', 'qualification_level', 'institution_name',
         'field_of_study', 'study_mode', 'completion_status', 'notes', 'start_year', 'start_month', 'start_precision',
         'end_year', 'end_month', 'end_precision'],
   array['qualification_title_raw', 'qualification_level', 'institution_name', 'field_of_study', 'study_mode',
         'completion_status', 'notes', 'start_year', 'start_month', 'start_precision', 'end_year', 'end_month', 'end_precision']),
  ('trace_candidate_career_breaks',
   array['owner_user_id', 'candidate_profile_id', 'break_reason', 'note', 'start_year', 'start_month', 'start_precision',
         'end_year', 'end_month', 'end_precision'],
   array['break_reason', 'note', 'start_year', 'start_month', 'start_precision', 'end_year', 'end_month', 'end_precision']),
  ('trace_candidate_career_activities',
   array['owner_user_id', 'candidate_profile_id', 'activity_type', 'title_raw', 'organisation_raw', 'description_raw',
         'contribution_raw', 'outcome_raw', 'link_url', 'status', 'related_employment_id', 'credential_identifier_raw',
         'start_year', 'start_month', 'start_precision', 'end_year', 'end_month', 'end_precision', 'expiry_year',
         'expiry_month', 'expiry_precision', 'metadata'],
   array['activity_type', 'title_raw', 'organisation_raw', 'description_raw', 'contribution_raw', 'outcome_raw', 'link_url',
         'status', 'related_employment_id', 'credential_identifier_raw', 'start_year', 'start_month', 'start_precision',
         'end_year', 'end_month', 'end_precision', 'expiry_year', 'expiry_month', 'expiry_precision', 'metadata']),
  ('trace_candidate_evidence',
   array['owner_user_id', 'candidate_profile_id', 'evidence_type', 'responsibility_area', 'evidence_text',
         'candidate_employment_id', 'candidate_activity_id'],
   array['evidence_type', 'responsibility_area', 'evidence_text', 'candidate_employment_id', 'candidate_activity_id']),
  ('trace_candidate_skills',
   array['owner_user_id', 'candidate_profile_id', 'skill_id', 'skill_text_raw', 'stated_proficiency_raw',
         'candidate_employment_id', 'candidate_education_id', 'candidate_activity_id', 'candidate_evidence_id'],
   array['skill_id', 'skill_text_raw', 'stated_proficiency_raw', 'candidate_employment_id', 'candidate_education_id',
         'candidate_activity_id', 'candidate_evidence_id']);

do $$
declare
  r record;
  v_all text;
begin
  for r in select * from _trace_candidate_columns loop
    -- Table-level grants would override column grants, and earlier column grants would survive a
    -- table-level revoke, so both are cleared before the lists are granted.
    execute format('revoke insert, update, truncate, references, trigger, maintain on public.%I from authenticated, service_role, anon, public',
                   r.table_name);
    select string_agg(quote_ident(c.column_name), ', ' order by c.ordinal_position) into v_all
    from information_schema.columns c
    where c.table_schema = 'public' and c.table_name = r.table_name;
    execute format('revoke insert (%s), update (%s), references (%s) on public.%I from authenticated, service_role, anon, public',
                   v_all, v_all, v_all, r.table_name);
    execute format('grant insert (%s) on public.%I to authenticated, service_role',
                   (select string_agg(quote_ident(x), ', ') from unnest(r.insert_cols) x), r.table_name);
    execute format('grant update (%s) on public.%I to authenticated, service_role',
                   (select string_agg(quote_ident(x), ', ') from unnest(r.update_cols) x), r.table_name);
    execute format('grant select, delete on public.%I to authenticated, service_role', r.table_name);
  end loop;
end
$$;

-- Resumes and extraction runs carry no review fields; service_role only loses the privileges that
-- bypass row triggers or attach new ones.
revoke truncate, references, trigger, maintain on public.trace_resume_documents, public.trace_extraction_runs from service_role;

-- ------------------------------------------------------------------ G0: inserts carry no system-controlled values
create function trace_util.guard_candidate_insert()
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

-- ------------------------------------------------------------------ G1: review rows
-- Arguments: the table's candidate UPDATE columns. Rules, in order:
--   (d) a referential action that nulls exactly one link column whose parent row is gone is allowed;
--       for a reviewed row only while its profile or account is being deleted (TR008 otherwise);
--   (b) review_status changes only as trace_review_writer, alone, and only along the M1 matrix (TR003);
--   (c) candidate_employer_id changes only as trace_review_writer (TR002);
--   (a) any content change needs a pending_review, user_stated row (TR001 rejected, TR002 otherwise).
-- System columns (origin, verification, mapping, sources) are governed by the column grants.
create function trace_util.guard_review_row()
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

-- ------------------------------------------------------------------ G2: employer edits (M1 lock protocol)
-- Arguments: the employer content columns. Lock order: the employer row (FOR UPDATE, upgrading the
-- statement's own lock), then its employments by id. Editable only when the employer's attributes
-- were entered by the candidate (NULL, or the legacy participant_confirmed) and every employment that
-- shows it is still pending review.
create function trace_util.guard_employer_update()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_old jsonb := to_jsonb(OLD);
  v_new jsonb := to_jsonb(NEW);
  v_col text;
  v_changed boolean := false;
  v_status text;
  v_blocking text[] := '{}';
begin
  foreach v_col in array TG_ARGV loop
    if v_old -> v_col is distinct from v_new -> v_col then
      v_changed := true;
    end if;
  end loop;
  if not v_changed then
    return NEW;
  end if;
  perform 1 from public.trace_candidate_employers e
   where e.candidate_employer_id = OLD.candidate_employer_id
   for update;
  for v_status in
    select m.review_status::text
      from public.trace_candidate_employments m
     where m.candidate_employer_id = OLD.candidate_employer_id
       and m.candidate_profile_id = OLD.candidate_profile_id
     order by m.candidate_employment_id
       for update
  loop
    if v_status <> 'pending_review' and not v_status = any (v_blocking) then
      v_blocking := v_blocking || v_status;
    end if;
  end loop;
  if OLD.attribute_provenance is not null and OLD.attribute_provenance <> 'participant_confirmed' then
    raise exception using errcode = 'TR002',
      message = format('the employer''s attributes are %s; correcting them needs correction history, which is not available yet',
                       OLD.attribute_provenance);
  end if;
  if cardinality(v_blocking) > 0 then
    raise exception using errcode = 'TR002',
      message = format('the employer is shown on employment that is %s; changing it would alter reviewed content',
                       array_to_string(v_blocking, ', '));
  end if;
  return NEW;
end
$$;

-- ------------------------------------------------------------------ G3: resume deletion
create function trace_util.guard_resume_delete()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  -- Account deletion (the only cascade that deletes resumes) removes the resume with everything else.
  if trace_util.in_referential_action(TG_RELID) then
    if not exists (select 1 from auth.users u where u.id = OLD.owner_user_id) then
      return OLD;
    end if;
  end if;
  if exists (select 1 from public.trace_candidate_employments t where t.source_resume_id = OLD.resume_id and t.verification_status = 'source_document_supported')
     or exists (select 1 from public.trace_candidate_education t where t.source_resume_id = OLD.resume_id and t.verification_status = 'source_document_supported')
     or exists (select 1 from public.trace_candidate_career_breaks t where t.source_resume_id = OLD.resume_id and t.verification_status = 'source_document_supported')
     or exists (select 1 from public.trace_candidate_career_activities t where t.source_resume_id = OLD.resume_id and t.verification_status = 'source_document_supported')
     or exists (select 1 from public.trace_candidate_evidence t where t.source_resume_id = OLD.resume_id and t.verification_status = 'source_document_supported')
     or exists (select 1 from public.trace_candidate_skills t where t.source_resume_id = OLD.resume_id and t.verification_status = 'source_document_supported') then
    raise exception using errcode = 'TR009',
      message = 'the resume supports records marked source_document_supported; their verification must be withdrawn first';
  end if;
  return OLD;
end
$$;

-- ------------------------------------------------------------------ G4 to G8: parent deletion
-- Each locks the dependents FOR UPDATE by id, in the global order (employments, activities, evidence,
-- skills), refuses when one is reviewed, and checks that detached skills stay unique. During profile
-- or account deletion (recognised as above) everything is being removed and the checks are skipped.
create function trace_util.guard_employer_delete()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_status text;
  v_reviewed boolean := false;
begin
  if trace_util.in_referential_action(TG_RELID) then
    if trace_util.owner_data_gone(OLD.owner_user_id, OLD.candidate_profile_id) then
      return OLD;
    end if;
  end if;
  for v_status in
    select m.review_status::text from public.trace_candidate_employments m
     where m.candidate_employer_id = OLD.candidate_employer_id and m.candidate_profile_id = OLD.candidate_profile_id
     order by m.candidate_employment_id for update
  loop
    v_reviewed := v_reviewed or v_status in ('user_confirmed', 'user_corrected');
  end loop;
  if v_reviewed then
    raise exception using errcode = 'TR008',
      message = 'the employer is shown on confirmed or corrected employment; deleting it would detach a reviewed record';
  end if;
  return OLD;
end
$$;

create function trace_util.guard_employment_delete()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_status text;
  v_reviewed boolean := false;
begin
  if trace_util.in_referential_action(TG_RELID) then
    if trace_util.owner_data_gone(OLD.owner_user_id, OLD.candidate_profile_id) then
      return OLD;
    end if;
  end if;
  for v_status in
    select a.review_status::text from public.trace_candidate_career_activities a
     where a.related_employment_id = OLD.candidate_employment_id and a.candidate_profile_id = OLD.candidate_profile_id
     order by a.candidate_activity_id for update
  loop
    v_reviewed := v_reviewed or v_status in ('user_confirmed', 'user_corrected');
  end loop;
  for v_status in
    select e.review_status::text from public.trace_candidate_evidence e
     where e.candidate_employment_id = OLD.candidate_employment_id and e.candidate_profile_id = OLD.candidate_profile_id
     order by e.candidate_evidence_id for update
  loop
    v_reviewed := v_reviewed or v_status in ('user_confirmed', 'user_corrected');
  end loop;
  for v_status in
    select s.review_status::text from public.trace_candidate_skills s
     where s.candidate_employment_id = OLD.candidate_employment_id and s.candidate_profile_id = OLD.candidate_profile_id
     order by s.candidate_skill_id for update
  loop
    v_reviewed := v_reviewed or v_status in ('user_confirmed', 'user_corrected');
  end loop;
  if v_reviewed then
    raise exception using errcode = 'TR008',
      message = 'a confirmed or corrected activity, evidence or skill depends on this employment; deleting it would detach a reviewed record';
  end if;
  if trace_util.skill_detach_conflict(OLD.candidate_profile_id, 'candidate_employment_id', OLD.candidate_employment_id) then
    raise exception using errcode = 'TR010',
      message = 'detaching this employment''s skills would duplicate an identical unattached skill; delete the duplicate first';
  end if;
  return OLD;
end
$$;

create function trace_util.guard_activity_delete()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_status text;
  v_reviewed boolean := false;
begin
  if trace_util.in_referential_action(TG_RELID) then
    if trace_util.owner_data_gone(OLD.owner_user_id, OLD.candidate_profile_id) then
      return OLD;
    end if;
  end if;
  for v_status in
    select e.review_status::text from public.trace_candidate_evidence e
     where e.candidate_activity_id = OLD.candidate_activity_id and e.candidate_profile_id = OLD.candidate_profile_id
     order by e.candidate_evidence_id for update
  loop
    v_reviewed := v_reviewed or v_status in ('user_confirmed', 'user_corrected');
  end loop;
  for v_status in
    select s.review_status::text from public.trace_candidate_skills s
     where s.candidate_activity_id = OLD.candidate_activity_id and s.candidate_profile_id = OLD.candidate_profile_id
     order by s.candidate_skill_id for update
  loop
    v_reviewed := v_reviewed or v_status in ('user_confirmed', 'user_corrected');
  end loop;
  if v_reviewed then
    raise exception using errcode = 'TR008',
      message = 'a confirmed or corrected evidence or skill depends on this activity; deleting it would detach a reviewed record';
  end if;
  if trace_util.skill_detach_conflict(OLD.candidate_profile_id, 'candidate_activity_id', OLD.candidate_activity_id) then
    raise exception using errcode = 'TR010',
      message = 'detaching this activity''s skills would duplicate an identical unattached skill; delete the duplicate first';
  end if;
  return OLD;
end
$$;

create function trace_util.guard_education_delete()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_status text;
  v_reviewed boolean := false;
begin
  if trace_util.in_referential_action(TG_RELID) then
    if trace_util.owner_data_gone(OLD.owner_user_id, OLD.candidate_profile_id) then
      return OLD;
    end if;
  end if;
  for v_status in
    select s.review_status::text from public.trace_candidate_skills s
     where s.candidate_education_id = OLD.candidate_education_id and s.candidate_profile_id = OLD.candidate_profile_id
     order by s.candidate_skill_id for update
  loop
    v_reviewed := v_reviewed or v_status in ('user_confirmed', 'user_corrected');
  end loop;
  if v_reviewed then
    raise exception using errcode = 'TR008',
      message = 'a confirmed or corrected skill depends on this education record; deleting it would detach a reviewed record';
  end if;
  if trace_util.skill_detach_conflict(OLD.candidate_profile_id, 'candidate_education_id', OLD.candidate_education_id) then
    raise exception using errcode = 'TR010',
      message = 'detaching this education record''s skills would duplicate an identical unattached skill; delete the duplicate first';
  end if;
  return OLD;
end
$$;

create function trace_util.guard_evidence_delete()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_status text;
  v_reviewed boolean := false;
begin
  if trace_util.in_referential_action(TG_RELID) then
    if trace_util.owner_data_gone(OLD.owner_user_id, OLD.candidate_profile_id) then
      return OLD;
    end if;
  end if;
  for v_status in
    select s.review_status::text from public.trace_candidate_skills s
     where s.candidate_evidence_id = OLD.candidate_evidence_id and s.candidate_profile_id = OLD.candidate_profile_id
     order by s.candidate_skill_id for update
  loop
    v_reviewed := v_reviewed or v_status in ('user_confirmed', 'user_corrected');
  end loop;
  if v_reviewed then
    raise exception using errcode = 'TR008',
      message = 'a confirmed or corrected skill depends on this evidence; deleting it would detach a reviewed record';
  end if;
  if trace_util.skill_detach_conflict(OLD.candidate_profile_id, 'candidate_evidence_id', OLD.candidate_evidence_id) then
    raise exception using errcode = 'TR010',
      message = 'detaching this evidence''s skills would duplicate an identical unattached skill; delete the duplicate first';
  end if;
  return OLD;
end
$$;

-- ------------------------------------------------------------------ attach triggers
do $$
declare
  r record;
  v_args text;
begin
  for r in select * from _trace_candidate_columns loop
    execute format('create trigger trace_guard_insert before insert on public.%I for each row execute function trace_util.guard_candidate_insert()',
                   r.table_name);
    v_args := (select string_agg(quote_literal(x), ', ') from unnest(r.update_cols) x);
    if r.table_name = 'trace_candidate_employers' then
      execute format('create trigger trace_guard_employer_update before update on public.%I for each row execute function trace_util.guard_employer_update(%s)',
                     r.table_name, v_args);
    elsif r.table_name <> 'trace_candidate_profiles' then
      execute format('create trigger trace_guard_review_row before update on public.%I for each row execute function trace_util.guard_review_row(%s)',
                     r.table_name, v_args);
    end if;
  end loop;
end
$$;
create trigger trace_guard_resume_delete before delete on public.trace_resume_documents
  for each row execute function trace_util.guard_resume_delete();
create trigger trace_guard_employer_delete before delete on public.trace_candidate_employers
  for each row execute function trace_util.guard_employer_delete();
create trigger trace_guard_employment_delete before delete on public.trace_candidate_employments
  for each row execute function trace_util.guard_employment_delete();
create trigger trace_guard_activity_delete before delete on public.trace_candidate_career_activities
  for each row execute function trace_util.guard_activity_delete();
create trigger trace_guard_education_delete before delete on public.trace_candidate_education
  for each row execute function trace_util.guard_education_delete();
create trigger trace_guard_evidence_delete before delete on public.trace_candidate_evidence
  for each row execute function trace_util.guard_evidence_delete();

-- ------------------------------------------------------------------ identity helper and transition matrix
-- request_uid: the only postgres-owned object in trace_review. It returns the real auth.uid() for the
-- current request (the writer role cannot be granted access to the auth schema) and nothing else.
create function trace_review.request_uid()
returns uuid
language sql stable
security definer
set search_path = ''
as $$
select auth.uid()
$$;

-- The M1 transition matrix. NULL means a no-op; anything else not listed raises TR003.
create function trace_review.next_review_status(p_current public.trace_record_review_status, p_action text)
returns public.trace_record_review_status
language plpgsql immutable
set search_path = ''
as $$
begin
  if p_action = 'confirm' then
    if p_current = 'pending_review' then
      return 'user_confirmed';
    elsif p_current in ('user_confirmed', 'user_corrected') then
      return null;
    end if;
  elsif p_action = 'reject' then
    if p_current in ('pending_review', 'user_confirmed', 'user_corrected') then
      return 'rejected';
    elsif p_current = 'rejected' then
      return null;
    end if;
  elsif p_action = 'reinstate' then
    if p_current = 'rejected' then
      return 'pending_review';
    end if;
  else
    raise exception using errcode = 'TR003', message = format('unknown review action %s', coalesce(p_action, 'NULL'));
  end if;
  raise exception using errcode = 'TR003',
    message = format('cannot %s a record that is %s%s', p_action, p_current,
                     case when p_current = 'rejected' then ' (reinstate it first)' else '' end);
end
$$;

-- ------------------------------------------------------------------ trusted write functions (owned by trace_review_writer)
create function trace_review.review_record(p_kind text, p_candidate_profile_id uuid, p_record_id uuid, p_action text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  v_current public.trace_record_review_status;
  v_target public.trace_record_review_status;
begin
  if v_uid is null then
    raise exception using errcode = 'TR004', message = 'no request identity';
  end if;
  if p_kind = 'employment' then
    select t.review_status into v_current from public.trace_candidate_employments t
     where t.candidate_employment_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
       for update;
  elsif p_kind = 'education' then
    select t.review_status into v_current from public.trace_candidate_education t
     where t.candidate_education_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
       for update;
  elsif p_kind = 'activity' then
    select t.review_status into v_current from public.trace_candidate_career_activities t
     where t.candidate_activity_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
       for update;
  elsif p_kind = 'evidence' then
    select t.review_status into v_current from public.trace_candidate_evidence t
     where t.candidate_evidence_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
       for update;
  elsif p_kind = 'skill' then
    select t.review_status into v_current from public.trace_candidate_skills t
     where t.candidate_skill_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
       for update;
  elsif p_kind = 'career_break' then
    select t.review_status into v_current from public.trace_candidate_career_breaks t
     where t.candidate_break_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
       for update;
  else
    raise exception using errcode = 'TR006', message = format('%s records have no review status', coalesce(p_kind, 'NULL'));
  end if;
  if not found then
    raise exception using errcode = 'TR005', message = format('%s %s not found', p_kind, p_record_id);
  end if;

  v_target := trace_review.next_review_status(v_current, p_action);
  if v_target is null then
    return v_current::text;
  end if;

  if p_kind = 'employment' then
    update public.trace_candidate_employments t set review_status = v_target
     where t.candidate_employment_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid;
  elsif p_kind = 'education' then
    update public.trace_candidate_education t set review_status = v_target
     where t.candidate_education_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid;
  elsif p_kind = 'activity' then
    update public.trace_candidate_career_activities t set review_status = v_target
     where t.candidate_activity_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid;
  elsif p_kind = 'evidence' then
    update public.trace_candidate_evidence t set review_status = v_target
     where t.candidate_evidence_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid;
  elsif p_kind = 'skill' then
    update public.trace_candidate_skills t set review_status = v_target
     where t.candidate_skill_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid;
  else
    update public.trace_candidate_career_breaks t set review_status = v_target
     where t.candidate_break_id = p_record_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid;
  end if;
  return v_target::text;
end
$$;

-- Lock order (M1): the old and new employer rows by id (FOR KEY SHARE), then the employment row.
create function trace_review.reassign_employment(p_candidate_profile_id uuid, p_employment_id uuid, p_employer_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  v_old uuid;
  v_locked uuid;
  v_new_found boolean := false;
  v_origin text;
  v_status text;
begin
  if v_uid is null then
    raise exception using errcode = 'TR004', message = 'no request identity';
  end if;
  select t.candidate_employer_id into v_old from public.trace_candidate_employments t
   where t.candidate_employment_id = p_employment_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid;
  if not found then
    raise exception using errcode = 'TR005', message = format('employment %s not found', p_employment_id);
  end if;
  for v_locked in
    select e.candidate_employer_id from public.trace_candidate_employers e
     where e.candidate_employer_id = any (array[v_old, p_employer_id])
       and e.candidate_profile_id = p_candidate_profile_id and e.owner_user_id = v_uid
     order by e.candidate_employer_id
       for key share
  loop
    v_new_found := v_new_found or v_locked = p_employer_id;
  end loop;
  if p_employer_id is not null and not v_new_found then
    raise exception using errcode = 'TR006', message = format('employer %s not found in this profile', p_employer_id);
  end if;
  select t.information_origin::text, t.review_status::text into v_origin, v_status from public.trace_candidate_employments t
   where t.candidate_employment_id = p_employment_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
     for update;
  if not found then
    raise exception using errcode = 'TR005', message = format('employment %s not found', p_employment_id);
  end if;
  if v_status = 'rejected' then
    raise exception using errcode = 'TR001', message = 'a rejected record cannot be edited; reinstate it first';
  end if;
  if v_status <> 'pending_review' or v_origin is distinct from 'user_stated' then
    raise exception using errcode = 'TR002',
      message = format('the employment is %s with origin %s; changing its employer needs correction history, which is not available yet',
                       v_status, coalesce(v_origin, 'not recorded'));
  end if;
  update public.trace_candidate_employments t set candidate_employer_id = p_employer_id
   where t.candidate_employment_id = p_employment_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid;
end
$$;

-- ------------------------------------------------------------------ writer privileges and RLS
grant select on public.trace_candidate_employers, public.trace_candidate_employments, public.trace_candidate_education,
  public.trace_candidate_career_breaks, public.trace_candidate_career_activities, public.trace_candidate_evidence,
  public.trace_candidate_skills to trace_review_writer;
grant update (review_status) on public.trace_candidate_employments, public.trace_candidate_education,
  public.trace_candidate_career_breaks, public.trace_candidate_career_activities, public.trace_candidate_evidence,
  public.trace_candidate_skills to trace_review_writer;
grant update (candidate_employer_id) on public.trace_candidate_employments to trace_review_writer;
-- FOR KEY SHARE requires UPDATE privilege on some column; the writer never updates employers.
grant update (updated_at) on public.trace_candidate_employers to trace_review_writer;
-- Stored generated columns and CHECK constraints are re-evaluated on UPDATE as the updating role.
grant usage on schema trace_util to trace_review_writer;
grant execute on function trace_util.month_lo(integer, integer, public.trace_date_precision),
  trace_util.month_hi(integer, integer, public.trace_date_precision),
  trace_util.valid_partial_date(integer, integer, public.trace_date_precision),
  trace_util.feasible_period(integer, integer, public.trace_date_precision, integer, integer, public.trace_date_precision)
  to trace_review_writer;

create policy "candidate employers review writer select" on public.trace_candidate_employers
  for select to trace_review_writer using (owner_user_id = (select trace_review.request_uid()));
create policy "candidate employers review writer update" on public.trace_candidate_employers
  for update to trace_review_writer using (owner_user_id = (select trace_review.request_uid()))
  with check (owner_user_id = (select trace_review.request_uid()));
create policy "candidate employments review writer select" on public.trace_candidate_employments
  for select to trace_review_writer using (owner_user_id = (select trace_review.request_uid()));
create policy "candidate employments review writer update" on public.trace_candidate_employments
  for update to trace_review_writer using (owner_user_id = (select trace_review.request_uid()))
  with check (owner_user_id = (select trace_review.request_uid()));
create policy "candidate education review writer select" on public.trace_candidate_education
  for select to trace_review_writer using (owner_user_id = (select trace_review.request_uid()));
create policy "candidate education review writer update" on public.trace_candidate_education
  for update to trace_review_writer using (owner_user_id = (select trace_review.request_uid()))
  with check (owner_user_id = (select trace_review.request_uid()));
create policy "candidate career breaks review writer select" on public.trace_candidate_career_breaks
  for select to trace_review_writer using (owner_user_id = (select trace_review.request_uid()));
create policy "candidate career breaks review writer update" on public.trace_candidate_career_breaks
  for update to trace_review_writer using (owner_user_id = (select trace_review.request_uid()))
  with check (owner_user_id = (select trace_review.request_uid()));
create policy "candidate career activities review writer select" on public.trace_candidate_career_activities
  for select to trace_review_writer using (owner_user_id = (select trace_review.request_uid()));
create policy "candidate career activities review writer update" on public.trace_candidate_career_activities
  for update to trace_review_writer using (owner_user_id = (select trace_review.request_uid()))
  with check (owner_user_id = (select trace_review.request_uid()));
create policy "candidate evidence review writer select" on public.trace_candidate_evidence
  for select to trace_review_writer using (owner_user_id = (select trace_review.request_uid()));
create policy "candidate evidence review writer update" on public.trace_candidate_evidence
  for update to trace_review_writer using (owner_user_id = (select trace_review.request_uid()))
  with check (owner_user_id = (select trace_review.request_uid()));
create policy "candidate skills review writer select" on public.trace_candidate_skills
  for select to trace_review_writer using (owner_user_id = (select trace_review.request_uid()));
create policy "candidate skills review writer update" on public.trace_candidate_skills
  for update to trace_review_writer using (owner_user_id = (select trace_review.request_uid()))
  with check (owner_user_id = (select trace_review.request_uid()));

-- ------------------------------------------------------------------ EXECUTE
revoke all on function trace_util.in_referential_action(oid), trace_util.is_table_owner(oid),
  trace_util.owner_data_gone(uuid, uuid), trace_util.skill_detach_conflict(uuid, text, uuid),
  trace_util.guard_candidate_insert(), trace_util.guard_review_row(), trace_util.guard_employer_update(),
  trace_util.guard_resume_delete(), trace_util.guard_employer_delete(), trace_util.guard_employment_delete(),
  trace_util.guard_activity_delete(), trace_util.guard_education_delete(), trace_util.guard_evidence_delete(),
  trace_review.request_uid(), trace_review.next_review_status(public.trace_record_review_status, text),
  trace_review.review_record(text, uuid, uuid, text), trace_review.reassign_employment(uuid, uuid, uuid)
  from public, anon, authenticated, service_role;
-- Helpers called from guard triggers run as the statement's role.
grant execute on function trace_util.in_referential_action(oid), trace_util.is_table_owner(oid)
  to authenticated, service_role, trace_review_writer;
grant execute on function trace_util.skill_detach_conflict(uuid, text, uuid) to authenticated, service_role;
grant execute on function trace_review.request_uid(), trace_review.next_review_status(public.trace_record_review_status, text)
  to trace_review_writer;
grant execute on function trace_review.review_record(text, uuid, uuid, text), trace_review.reassign_employment(uuid, uuid, uuid)
  to authenticated;

-- ------------------------------------------------------------------ ownership transfer
-- ALTER ... OWNER TO needs SET on the new owner and CREATE for it on the schema. Both are granted
-- only for the transfer and removed straight after. postgres keeps only the ADMIN OPTION it received
-- when it created the role; it cannot act as the role.
grant trace_review_writer to postgres with set true, inherit false;
grant create on schema trace_review to trace_review_writer;
alter function trace_review.review_record(text, uuid, uuid, text) owner to trace_review_writer;
alter function trace_review.reassign_employment(uuid, uuid, uuid) owner to trace_review_writer;
revoke create on schema trace_review from trace_review_writer;
revoke trace_review_writer from postgres;

-- ------------------------------------------------------------------ assertions (fail the migration if violated)
do $$
declare
  v_bad text;
begin
  if exists (select 1 from pg_catalog.pg_roles r where r.rolname = 'trace_review_writer'
               and (r.rolsuper or r.rolcanlogin or r.rolinherit or r.rolbypassrls or r.rolcreaterole or r.rolcreatedb or r.rolreplication)) then
    raise exception 'trace_review_writer must be NOLOGIN, NOINHERIT, NOBYPASSRLS and unprivileged';
  end if;
  if pg_catalog.pg_has_role('postgres', 'trace_review_writer', 'SET') or pg_catalog.pg_has_role('postgres', 'trace_review_writer', 'USAGE') then
    raise exception 'postgres must not be able to act as trace_review_writer';
  end if;
  if exists (select 1 from pg_catalog.pg_auth_members m
              where m.roleid = 'trace_review_writer'::regrole and (m.set_option or m.inherit_option)) then
    raise exception 'trace_review_writer must have no members that can use it';
  end if;
  if exists (select 1 from pg_catalog.pg_auth_members m where m.member = 'trace_review_writer'::regrole) then
    raise exception 'trace_review_writer must not be a member of any role';
  end if;
  if pg_catalog.has_schema_privilege('trace_review_writer', 'trace_review', 'CREATE') then
    raise exception 'trace_review_writer must not keep CREATE on trace_review';
  end if;
  select string_agg(p.oid::regprocedure::text || ' owned by ' || pg_catalog.pg_get_userbyid(p.proowner), '; ') into v_bad
  from pg_catalog.pg_proc p
  where p.pronamespace = 'trace_review'::regnamespace
    and pg_catalog.pg_get_userbyid(p.proowner) <> case when p.proname in ('review_record', 'reassign_employment')
                                                       then 'trace_review_writer' else 'postgres' end;
  if v_bad is not null then
    raise exception 'unexpected trace_review function owner: %', v_bad;
  end if;
  select string_agg(c.relname || ':' || r.rolname || ':' || x.p, ', ') into v_bad
  from pg_catalog.pg_class c
  cross join (values ('authenticated'), ('service_role'), ('anon'), ('public')) r(rolname)
  cross join (values ('INSERT'), ('UPDATE'), ('TRUNCATE'), ('TRIGGER')) x(p)
  where c.oid in (select ('public.' || t.table_name)::regclass from _trace_candidate_columns t)
    and case when r.rolname = 'public'
             then exists (select 1 from pg_catalog.aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
                           where a.grantee = 0 and a.privilege_type = x.p)
             else pg_catalog.has_table_privilege(r.rolname, c.oid, x.p) end;
  if v_bad is not null then
    raise exception 'table-level write privilege remains on candidate tables: %', v_bad;
  end if;
end
$$;

drop table _trace_candidate_columns;
