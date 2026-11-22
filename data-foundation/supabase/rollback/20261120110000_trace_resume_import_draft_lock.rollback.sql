-- Compensation for migration 20261120110000_trace_resume_import_draft_lock (v2.31.1). NOT a migration.
-- Without it a confirmation cannot lock its draft (permission denied). Roll back v2.31.0 too, or deploy a backend that
-- does not lock. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261120110000 --status reverted
begin;
drop policy resume_import_drafts_writer_update on trace_private.resume_import_drafts;
revoke update (warnings) on trace_private.resume_import_drafts from trace_import_writer;
commit;
