-- v2.31.1: let trace_import.my_draft(p_lock => true) lock the caller's draft for a confirmation.
-- SELECT ... FOR UPDATE needs the UPDATE privilege. The writer role gets it on one column only, under an owner-scoped
-- policy; no function updates a draft (a draft is only saved whole, locked, or deleted). Requires v2.31.0.
grant update (warnings) on trace_private.resume_import_drafts to trace_import_writer;
create policy resume_import_drafts_writer_update on trace_private.resume_import_drafts
  for update to trace_import_writer using (owner_user_id = (select trace_review.request_uid()))
  with check (owner_user_id = (select trace_review.request_uid()));

do $$
begin
  if has_table_privilege('trace_app_user', 'trace_private.resume_import_drafts', 'update')
     or has_table_privilege('authenticated', 'trace_private.resume_import_drafts', 'select') then
    raise exception 'resume_import_drafts must have no client grant';
  end if;
end
$$;
