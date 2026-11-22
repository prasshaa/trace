-- Trace data foundation v2.15.0: ordering events for employment positions (decision D10).
--
-- An employment's position (sequence_index, its stated order) can change when the user adds an earlier role. Until now
-- such a move of a reviewed or extracted role could only be a CONTENT CORRECTION, which made a confirmed role show as
-- "Corrected". A position is not content. This migration records a position-only move as an append-only ORDERING EVENT
-- instead; the role keeps its review status.
--
--   trace_candidate_order_events   one row per move: old and new position, the role's review status at the move,
--                                  the cause, the role that caused it, when, by whom, and the transaction. Owner-readable;
--                                  written only by trace_review.move_employment; append-only (TR023)
--   trace_review.move_employment   locks the role, refuses a rejected role (TR001), writes the event, then changes
--                                  sequence_index only. Acts only for the request identity (auth.uid())
--   guard_review_row               one new branch (f): the writer may change exactly sequence_index, with no status
--                                  change, when the matching event exists in the same transaction. Everything else is
--                                  unchanged (re-stated below in full from the live definition)
--   require_correction_history     one new exemption: a position-only move of an already corrected role is backed by
--                                  its ordering event instead of correction history. Everything else is unchanged
-- Content changes still go through trace_review.correct_record and correction history. No data is changed.

create table public.trace_candidate_order_events (
  order_event_id          uuid primary key default gen_random_uuid(),
  event_seq               bigint generated always as identity unique,
  owner_user_id           uuid not null references auth.users(id) on delete cascade,
  candidate_profile_id    uuid not null,
  employment_id           uuid not null,
  previous_sequence_index integer not null check (previous_sequence_index >= 1),
  new_sequence_index      integer not null check (new_sequence_index >= 1),
  review_status_at_move   public.trace_record_review_status not null check (review_status_at_move <> 'rejected'),
  cause                   text not null check (cause in ('role_inserted', 'user_reordered')),
  caused_by_employment_id uuid,           -- the role whose insertion caused the move (no foreign key: history outlives it)
  moved_at                timestamptz not null default now(),
  moved_by                uuid not null,
  txid                    bigint not null default txid_current(),
  constraint order_events_position_changes check (previous_sequence_index <> new_sequence_index),
  foreign key (candidate_profile_id, owner_user_id)
    references public.trace_candidate_profiles (candidate_profile_id, owner_user_id) on delete cascade,
  foreign key (employment_id, candidate_profile_id)
    references public.trace_candidate_employments (candidate_employment_id, candidate_profile_id) on delete cascade
);
alter table public.trace_candidate_order_events enable row level security;
create index trace_candidate_order_events_employment_idx on public.trace_candidate_order_events (employment_id, event_seq);
create index trace_candidate_order_events_profile_idx on public.trace_candidate_order_events (candidate_profile_id, owner_user_id);
create index trace_candidate_order_events_tx_idx on public.trace_candidate_order_events (employment_id, txid);

create function trace_util.guard_order_events()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if TG_OP = 'INSERT' and current_user = 'trace_review_writer' then
    return NEW;          -- trace_review.move_employment
  end if;
  if TG_OP = 'DELETE' and trace_util.in_referential_action(TG_RELID) then
    return OLD;          -- the role, profile or account is being deleted (foreign-key cascade)
  end if;
  if TG_OP = 'INSERT' then
    raise exception using errcode = 'TR023', message = 'ordering events are written only by trace_review.move_employment';
  end if;
  raise exception using errcode = 'TR023', message = 'ordering events are append-only';
end
$$;
create trigger trace_guard_order_events before insert or update or delete on public.trace_candidate_order_events
  for each row execute function trace_util.guard_order_events();
create trigger trace_guard_order_events_truncate before truncate on public.trace_candidate_order_events
  for each statement execute function trace_util.guard_order_events();

create function trace_review.move_employment(p_candidate_profile_id uuid, p_employment_id uuid, p_new_index integer,
                                             p_cause text, p_caused_by uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  v_old integer;
  v_status text;
begin
  if v_uid is null then
    raise exception using errcode = 'TR004', message = 'no request identity';
  end if;
  if p_new_index is null or p_new_index < 1 then
    raise exception using errcode = 'TR006', message = 'a position is a whole number from 1';
  end if;
  if p_cause is null or p_cause not in ('role_inserted', 'user_reordered') then
    raise exception using errcode = 'TR006', message = 'unknown reason for a move';
  end if;
  select t.sequence_index, t.review_status::text into v_old, v_status
    from public.trace_candidate_employments t
   where t.candidate_employment_id = p_employment_id and t.candidate_profile_id = p_candidate_profile_id
     and t.owner_user_id = v_uid
     for update;
  if not found then
    raise exception using errcode = 'TR005', message = 'employment not found';
  end if;
  if v_status = 'rejected' then
    raise exception using errcode = 'TR001', message = 'a rejected role cannot be moved; reinstate it first';
  end if;
  if v_old = p_new_index then
    return;
  end if;
  insert into public.trace_candidate_order_events (owner_user_id, candidate_profile_id, employment_id, previous_sequence_index,
                                                   new_sequence_index, review_status_at_move, cause, caused_by_employment_id, moved_by)
  values (v_uid, p_candidate_profile_id, p_employment_id, v_old, p_new_index, v_status::public.trace_record_review_status, p_cause,
          p_caused_by, v_uid);
  update public.trace_candidate_employments set sequence_index = p_new_index
   where candidate_employment_id = p_employment_id and owner_user_id = v_uid;
end
$$;

-- ------------------------------------------------------------------ the two guards, each with one narrow addition
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

  -- (f) v2.15.0: a position-only move (trace_review.move_employment). Allowed only for the writer, only when exactly
  -- sequence_index changes and no review status changes, and only with the ordering event the function wrote for this
  -- exact move in this transaction. Never for a rejected role. Content changes still need (e) and correction history.
  if TG_TABLE_NAME = 'trace_candidate_employments' and current_user = 'trace_review_writer' and not v_status_changed
     and v_changed = array['sequence_index']::text[]
     and exists (select 1 from public.trace_candidate_order_events e
                  where e.employment_id = (v_new ->> 'candidate_employment_id')::uuid
                    and e.txid = pg_catalog.txid_current()
                    and e.previous_sequence_index = (v_old ->> 'sequence_index')::integer
                    and e.new_sequence_index = (v_new ->> 'sequence_index')::integer) then
    if v_old_status = 'rejected' then
      raise exception using errcode = 'TR001', message = 'a rejected role cannot be moved; reinstate it first';
    end if;
    return NEW;
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
  -- v2.15.0: moving an already corrected role is an ordering event (trace_review.move_employment), not a correction.
  if v_kind = 'employment' and v_old ->> 'review_status' = 'user_corrected' and v_changed = array['sequence_index']::text[]
     and exists (select 1 from public.trace_candidate_order_events e
                  where e.employment_id = v_id and e.txid = pg_catalog.txid_current()
                    and e.previous_sequence_index = (v_old ->> 'sequence_index')::integer
                    and e.new_sequence_index = (v_new ->> 'sequence_index')::integer) then
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

-- ------------------------------------------------------------------ privileges and RLS
revoke all on public.trace_candidate_order_events from public, anon, authenticated, service_role;
grant select on public.trace_candidate_order_events to authenticated, service_role;
grant select on public.trace_candidate_order_events to trace_review_writer;     -- the guard reads events as the writer
grant insert (owner_user_id, candidate_profile_id, employment_id, previous_sequence_index, new_sequence_index,
              review_status_at_move, cause, caused_by_employment_id, moved_by)
  on public.trace_candidate_order_events to trace_review_writer;
create policy "candidate order events owner select" on public.trace_candidate_order_events
  for select to authenticated using (owner_user_id = (select auth.uid()));
create policy "candidate order events review writer select" on public.trace_candidate_order_events
  for select to trace_review_writer using (owner_user_id = (select trace_review.request_uid()));
create policy "candidate order events review writer insert" on public.trace_candidate_order_events
  for insert to trace_review_writer with check (owner_user_id = (select trace_review.request_uid())
                                                and moved_by = (select trace_review.request_uid()));

revoke all on function trace_util.guard_order_events(),
  trace_review.move_employment(uuid, uuid, integer, text, uuid) from public, anon, authenticated, service_role;
grant execute on function trace_review.move_employment(uuid, uuid, integer, text, uuid) to trace_app_user;   -- the backend owner role only (v2.8.0 rule)

-- ------------------------------------------------------------------ ownership transfer (the M2 procedure)
grant trace_review_writer to postgres with set true, inherit false;
grant create on schema trace_review to trace_review_writer;
alter function trace_review.move_employment(uuid, uuid, integer, text, uuid) owner to trace_review_writer;
revoke create on schema trace_review from trace_review_writer;
revoke trace_review_writer from postgres;

do $$
begin
  if pg_catalog.pg_has_role('postgres', 'trace_review_writer', 'SET') or pg_catalog.pg_has_role('postgres', 'trace_review_writer', 'USAGE') then
    raise exception 'postgres must not be able to act as trace_review_writer';
  end if;
  if pg_catalog.has_schema_privilege('trace_review_writer', 'trace_review', 'CREATE') then
    raise exception 'trace_review_writer must not keep CREATE on trace_review';
  end if;
  if pg_catalog.pg_get_userbyid((select proowner from pg_catalog.pg_proc
                                  where oid = 'trace_review.move_employment(uuid, uuid, integer, text, uuid)'::regprocedure))
     <> 'trace_review_writer' then
    raise exception 'trace_review.move_employment must be owned by trace_review_writer';
  end if;
end
$$;
