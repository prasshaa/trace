-- Compensation for migration 20261120100000_trace_resume_import_drafts (v2.31.0). NOT a migration.
-- Deploy a backend without the staged resume import (D48) first. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261120100000 --status reverted
-- Refuses while any import draft exists: a user's unconfirmed import is never discarded silently (they confirm or
-- discard it first). Refuses while any resume row has no retention date, because restoring NOT NULL would need a date
-- that no decision has approved. The v2.10.0 marker functions are restored as they were.
begin;
do $$
begin
  if exists (select 1 from trace_private.resume_import_drafts) then
    raise exception 'resume import drafts exist; have users confirm or discard them before rolling back';
  end if;
  if exists (select 1 from public.trace_resume_documents where retention_until is null) then
    raise exception 'resume rows without a retention date exist; restoring NOT NULL needs an approved retention date';
  end if;
end;
$$;
drop trigger trace_consent_privacy on public.trace_consent_events;
drop function trace_private.on_consent_event_privacy();
drop function trace_import.save_draft(uuid, jsonb, text, text, text, text, integer, text, text[], bytea, text);
drop function trace_import.my_draft(uuid, boolean);
drop function trace_import.delete_draft(uuid, uuid);
drop table trace_private.resume_import_drafts;          -- its policies go with it
drop schema trace_import;
revoke all on schema trace_private, trace_review from trace_import_writer;
revoke execute on function trace_review.request_uid() from trace_import_writer;
drop role trace_import_writer;
alter table public.trace_resume_documents alter column retention_until set default (now() + interval '30 days');
alter table public.trace_resume_documents alter column retention_until set not null;
comment on column public.trace_resume_documents.retention_until is null;
create or replace function trace_private.identity_markers_needed(p_owner uuid, p_profile uuid)
returns boolean
language sql
stable
set search_path = ''
as $$
  select exists (select 1 from trace_ingest.deidentified_resume_admissions a
                  where a.owner_user_id = p_owner and a.candidate_profile_id = p_profile)
      or exists (select 1 from trace_private.resume_quarantine q
                  where q.owner_user_id = p_owner and q.candidate_profile_id = p_profile
                    and q.status in ('pending_review', 'approved'))
$$;
create or replace function trace_contrib.store_identity_markers(p_sealed bytea, p_key_id text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  v_profile uuid;
begin
  select p.candidate_profile_id into v_profile from public.trace_candidate_profiles p
   where p.owner_user_id = v_uid and not coalesce(p.is_test_fixture, false) and p.fixture_profile_id is null;
  if v_uid is null or v_profile is null then
    raise exception using errcode = 'TR021', message = 'identity markers need the owner''s own non-fixture profile';
  end if;
  insert into trace_private.identity_markers (owner_user_id, candidate_profile_id, sealed, key_id)
  values (v_uid, v_profile, p_sealed, p_key_id)
  on conflict (owner_user_id) do update
     set candidate_profile_id = excluded.candidate_profile_id, sealed = excluded.sealed, key_id = excluded.key_id,
         names = '{}', emails = '{}', phones = '{}', urls = '{}', handles = '{}', updated_at = now();
end;
$$;
commit;
