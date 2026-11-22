-- Rollback of v2.33.0. Removes only what v2.33.0 added; no data is touched (the function deletes rows only when called,
-- and a rollback cannot restore rows it already deleted at an owner's request).
drop function if exists trace_import.clear_my_import_records();
drop policy if exists resume_quarantine_import_writer_delete on trace_private.resume_quarantine;
drop policy if exists resume_quarantine_import_writer_select on trace_private.resume_quarantine;
drop policy if exists resume_processing_log_import_writer_delete on trace_private.resume_processing_log;
drop policy if exists resume_processing_log_import_writer_select on trace_private.resume_processing_log;
revoke delete on trace_private.resume_quarantine, trace_private.resume_processing_log from trace_import_writer;
revoke select (owner_user_id) on trace_private.resume_quarantine, trace_private.resume_processing_log from trace_import_writer;
