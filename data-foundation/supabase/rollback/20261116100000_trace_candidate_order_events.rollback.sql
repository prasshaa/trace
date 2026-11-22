-- Compensation for migration 20261116100000_trace_candidate_order_events (v2.15.0). NOT a migration.
-- Deploy a backend that does not call trace_review.move_employment first. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261116100000 --status reverted
-- Effect: ordering events are deleted with their table. The positions they recorded stay as they are now. The two
-- guards are restored to their v2.14.0 definitions, so a later move of a reviewed role is again possible only as a
-- correction. No other data is touched.
begin;
drop function trace_review.move_employment(uuid, uuid, integer, text, uuid);
CREATE OR REPLACE FUNCTION trace_util.guard_review_row()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path = ''
AS $function$
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
$function$;

CREATE OR REPLACE FUNCTION trace_util.require_correction_history()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path = ''
AS $function$
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
$function$;

drop table public.trace_candidate_order_events;
drop function trace_util.guard_order_events();
commit;
