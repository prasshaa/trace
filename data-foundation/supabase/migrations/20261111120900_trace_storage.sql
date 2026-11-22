-- Trace Data Foundation v2 · 10 · private resume storage
--
-- Bucket: trace-resumes (private). Object key: <owner_user_id>/<resume_id>.<pdf|docx>
-- Clients upload into, list, download (signed URLs) and delete only their own folder.
-- No UPDATE policy: an uploaded resume is immutable; re-upload creates a new object.
-- Expired resumes are deleted by a scheduled backend job through the Storage API (not by SQL
-- DELETE on storage.objects), which then sets trace_resume_documents.deleted_at.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'trace-resumes', 'trace-resumes', false, 10485760,
  array['application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document']
)
on conflict (id) do update
  set public = false,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

create policy "trace resumes owner upload" on storage.objects
  for insert to authenticated
  with check (bucket_id = 'trace-resumes' and (storage.foldername(name))[1] = (select auth.uid())::text);

create policy "trace resumes owner read" on storage.objects
  for select to authenticated
  using (bucket_id = 'trace-resumes' and (storage.foldername(name))[1] = (select auth.uid())::text);

create policy "trace resumes owner delete" on storage.objects
  for delete to authenticated
  using (bucket_id = 'trace-resumes' and (storage.foldername(name))[1] = (select auth.uid())::text);
