-- v2.33.0 (release, D52): "Clear my career profile and start over".
--
-- The profile itself is cleared by the owner's existing rights: the owner role deletes its own candidate profile row
-- (policy "candidate profiles owner delete") and every dependent record follows by foreign-key cascade, the path account
-- deletion already uses and the delete guards already allow (trace_util.owner_data_gone). Then the owner's resume
-- metadata rows (owner delete policy) go, and a fresh, empty profile is created.
--
-- Two owner-keyed tables have no client grant and no foreign key to the profile, so nothing clears them:
--   trace_private.resume_quarantine      de-identified records held for a reviewer (legacy path, Phase 6)
--   trace_private.resume_processing_log  per-upload outcome codes (no resume content)
-- This migration adds ONE function, acting for the request identity only, that deletes the caller's rows in both.
-- Additive: a new function and two owner-scoped delete policies for trace_import_writer. Nothing existing changes.
-- Requires v2.31.0 (trace_import_writer) and v2.32.0.

grant delete on trace_private.resume_quarantine, trace_private.resume_processing_log to trace_import_writer;
grant select (owner_user_id) on trace_private.resume_quarantine, trace_private.resume_processing_log to trace_import_writer;
create policy resume_quarantine_import_writer_delete on trace_private.resume_quarantine
  for delete to trace_import_writer using (owner_user_id = (select trace_review.request_uid()));
create policy resume_quarantine_import_writer_select on trace_private.resume_quarantine
  for select to trace_import_writer using (owner_user_id = (select trace_review.request_uid()));
create policy resume_processing_log_import_writer_delete on trace_private.resume_processing_log
  for delete to trace_import_writer using (owner_user_id = (select trace_review.request_uid()));
create policy resume_processing_log_import_writer_select on trace_private.resume_processing_log
  for select to trace_import_writer using (owner_user_id = (select trace_review.request_uid()));

-- Delete the caller's quarantined records and processing-log rows. Idempotent: a second call deletes nothing.
create function trace_import.clear_my_import_records()
returns table (quarantined integer, log_entries integer)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  v_q integer;
  v_l integer;
begin
  if v_uid is null then
    raise exception 'no request identity' using errcode = '42501';
  end if;
  delete from trace_private.resume_quarantine q where q.owner_user_id = v_uid;
  get diagnostics v_q = row_count;
  delete from trace_private.resume_processing_log l where l.owner_user_id = v_uid;
  get diagnostics v_l = row_count;
  return query select v_q, v_l;
end;
$$;

revoke all on function trace_import.clear_my_import_records() from public, anon, authenticated, service_role;
grant execute on function trace_import.clear_my_import_records() to trace_app_user;
grant trace_import_writer to postgres with set true, inherit false;
grant create on schema trace_import to trace_import_writer;
alter function trace_import.clear_my_import_records() owner to trace_import_writer;
revoke create on schema trace_import from trace_import_writer;
revoke trace_import_writer from postgres;

do $$
begin
  if pg_catalog.pg_has_role('postgres', 'trace_import_writer', 'SET') or pg_catalog.pg_has_role('postgres', 'trace_import_writer', 'USAGE') then
    raise exception 'postgres must not be able to act as trace_import_writer';
  end if;
  if has_function_privilege('anon', 'trace_import.clear_my_import_records()', 'execute')
     or has_function_privilege('authenticated', 'trace_import.clear_my_import_records()', 'execute')
     or has_function_privilege('service_role', 'trace_import.clear_my_import_records()', 'execute')
     or not has_function_privilege('trace_app_user', 'trace_import.clear_my_import_records()', 'execute') then
    raise exception 'clear_my_import_records must be executable by trace_app_user only';
  end if;
  if has_table_privilege('trace_app_user', 'trace_private.resume_quarantine', 'delete')
     or has_table_privilege('authenticated', 'trace_private.resume_processing_log', 'delete') then
    raise exception 'the quarantine and processing log must have no client delete grant';
  end if;
end
$$;
