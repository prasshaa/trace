-- v2.31.0: one private staging model for resume imports; identity markers only for contributors; no implied retention.
--
-- 1. trace_private.resume_import_drafts: the owner's unconfirmed resume import (D48). The de-identified draft that the
--    parser extracted (jobs, bullets, education, skills, activities, text it could not place) is held HERE until the
--    owner confirms it, for a new profile and for a profile that already has records alike. Nothing from an import is
--    written to the candidate-profile tables before confirmation, so a pending import can never be read as profile
--    information, used for the current position or used for a plan. At most one open draft per profile. No client
--    grant: the owner role (trace_app_user) reaches it only through four functions acting for the request identity,
--    owned by the NOLOGIN role trace_import_writer under owner-scoped policies (the v2.29.0 pattern).
--    identity_sealed/identity_key_id: the resume's identity markers, sealed by the backend, kept with the draft ONLY
--    when the owner's contribution consent is active at upload; deleted with the draft.
-- 2. trace_resume_documents.retention_until: no default and nullable. No retention period has been approved for resume
--    metadata, and nothing ever acted on the old 30-day default. Existing values are left exactly as they are (nothing
--    is deleted or changed because of them). An automatic retention policy needs an explicit product decision.
-- 3. Identity markers are needed only while the owner's contribution consent is active (and an admitted resume or a
--    reviewed quarantine exists). They can no longer be stored without that consent, and withdrawing or declining the
--    contribution consent deletes them at once (trigger). Withdrawing the resume-processing consent deletes the open
--    import draft (trigger). Confirmed profile records are not touched by either.
-- Requires PostgreSQL 16+ and v2.30.0.

-- --------------------------------------------------------------------------- 1. import drafts
create table trace_private.resume_import_drafts (
  import_id            uuid primary key default gen_random_uuid(),
  owner_user_id        uuid not null references auth.users (id) on delete cascade,
  candidate_profile_id uuid not null,
  draft                jsonb not null check (jsonb_typeof(draft) = 'object'),
  parser_version       text not null check (length(parser_version) between 1 and 80),
  pipeline_version     text not null check (length(pipeline_version) between 1 and 80),
  format               text not null check (format in ('pdf', 'docx')),
  mime_type            text not null check (mime_type in (
                         'application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')),
  byte_size            integer not null check (byte_size between 1 and 10485760),
  content_sha256       text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
  warnings             text[] not null default '{}',
  identity_sealed      bytea,
  identity_key_id      text check (identity_key_id ~ '^[0-9a-f]{16}$'),
  created_at           timestamptz not null default now(),
  constraint resume_import_drafts_one_per_profile unique (owner_user_id, candidate_profile_id),
  constraint resume_import_drafts_format_mime check (
    (format = 'pdf') = (mime_type = 'application/pdf')),
  constraint resume_import_drafts_sealed_pair check ((identity_sealed is null) = (identity_key_id is null)),
  foreign key (candidate_profile_id, owner_user_id)
    references public.trace_candidate_profiles (candidate_profile_id, owner_user_id) on delete cascade
);
alter table trace_private.resume_import_drafts enable row level security;
revoke all on trace_private.resume_import_drafts from public, anon, authenticated, service_role;

create role trace_import_writer nologin inherit nosuperuser nocreatedb nocreaterole nobypassrls noreplication;
grant usage on schema trace_private, trace_review to trace_import_writer;
grant execute on function trace_review.request_uid() to trace_import_writer;
grant select, insert, delete on trace_private.resume_import_drafts to trace_import_writer;
create policy resume_import_drafts_writer_select on trace_private.resume_import_drafts
  for select to trace_import_writer using (owner_user_id = (select trace_review.request_uid()));
create policy resume_import_drafts_writer_insert on trace_private.resume_import_drafts
  for insert to trace_import_writer with check (owner_user_id = (select trace_review.request_uid()));
create policy resume_import_drafts_writer_delete on trace_private.resume_import_drafts
  for delete to trace_import_writer using (owner_user_id = (select trace_review.request_uid()));
create schema if not exists trace_import;
revoke all on schema trace_import from public, anon, authenticated, service_role;
grant usage on schema trace_import to trace_app_user, trace_import_writer;

-- Save (replace) the caller's draft for one of their own profiles (the composite foreign key proves ownership; the
-- backend refuses fixture profiles before parsing). Returns the new import id.
create function trace_import.save_draft(p_profile uuid, p_draft jsonb, p_parser_version text, p_pipeline_version text,
                                        p_format text, p_mime_type text, p_byte_size integer, p_sha256 text, p_warnings text[],
                                        p_sealed bytea, p_key_id text)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  v_id uuid;
begin
  if v_uid is null then
    raise exception 'no request identity' using errcode = '42501';
  end if;
  delete from trace_private.resume_import_drafts d where d.owner_user_id = v_uid and d.candidate_profile_id = p_profile;
  insert into trace_private.resume_import_drafts (owner_user_id, candidate_profile_id, draft, parser_version, pipeline_version,
                                                  format, mime_type, byte_size, content_sha256, warnings, identity_sealed,
                                                  identity_key_id)
  values (v_uid, p_profile, p_draft, p_parser_version, p_pipeline_version, p_format, p_mime_type, p_byte_size, p_sha256,
          coalesce(p_warnings, '{}'), p_sealed, p_key_id)
  returning import_id into v_id;
  return v_id;
end;
$$;

-- The caller's open draft for one profile (at most one row). p_lock: lock it for a confirmation (FOR UPDATE), so two
-- confirmations of one import are serialised and the second finds nothing.
create function trace_import.my_draft(p_profile uuid, p_lock boolean default false)
returns table (import_id uuid, draft jsonb, parser_version text, pipeline_version text, format text, mime_type text,
               byte_size integer, content_sha256 text, warnings text[], identity_sealed bytea, identity_key_id text,
               created_at timestamptz)
language plpgsql
security definer
set search_path = ''
as $$
begin
  if coalesce(p_lock, false) then
    return query select d.import_id, d.draft, d.parser_version, d.pipeline_version, d.format, d.mime_type, d.byte_size,
                        d.content_sha256, d.warnings, d.identity_sealed, d.identity_key_id, d.created_at
                   from trace_private.resume_import_drafts d
                  where d.owner_user_id = trace_review.request_uid() and d.candidate_profile_id = p_profile
                    for update;
  else
    return query select d.import_id, d.draft, d.parser_version, d.pipeline_version, d.format, d.mime_type, d.byte_size,
                        d.content_sha256, d.warnings, d.identity_sealed, d.identity_key_id, d.created_at
                   from trace_private.resume_import_drafts d
                  where d.owner_user_id = trace_review.request_uid() and d.candidate_profile_id = p_profile;
  end if;
end;
$$;

-- Delete the caller's draft (discard, or after a confirmation in the same transaction). Returns rows deleted (0 or 1).
create function trace_import.delete_draft(p_profile uuid, p_import_id uuid)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_count integer;
begin
  delete from trace_private.resume_import_drafts d
   where d.owner_user_id = trace_review.request_uid() and d.candidate_profile_id = p_profile
     and (p_import_id is null or d.import_id = p_import_id);
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke all on function trace_import.save_draft(uuid, jsonb, text, text, text, text, integer, text, text[], bytea, text),
  trace_import.my_draft(uuid, boolean), trace_import.delete_draft(uuid, uuid) from public, anon, authenticated, service_role;
grant execute on function trace_import.save_draft(uuid, jsonb, text, text, text, text, integer, text, text[], bytea, text),
  trace_import.my_draft(uuid, boolean), trace_import.delete_draft(uuid, uuid) to trace_app_user;
grant trace_import_writer to postgres with set true, inherit false;
grant create on schema trace_import to trace_import_writer;
alter function trace_import.save_draft(uuid, jsonb, text, text, text, text, integer, text, text[], bytea, text) owner to trace_import_writer;
alter function trace_import.my_draft(uuid, boolean) owner to trace_import_writer;
alter function trace_import.delete_draft(uuid, uuid) owner to trace_import_writer;
revoke create on schema trace_import from trace_import_writer;
revoke trace_import_writer from postgres;

-- --------------------------------------------------------------------------- 2. no implied retention date
alter table public.trace_resume_documents alter column retention_until drop default;
alter table public.trace_resume_documents alter column retention_until drop not null;
comment on column public.trace_resume_documents.retention_until is
  'Not a retention policy. NULL for new rows (v2.31.0): no retention period is approved for resume metadata and nothing '
  'acts on this column. Rows written before v2.31.0 keep the 30-day value they were given; it is not enforced.';

-- --------------------------------------------------------------------------- 3. identity markers follow contribution consent
create or replace function trace_private.identity_markers_needed(p_owner uuid, p_profile uuid)
returns boolean
language sql
stable
set search_path = ''
as $$
  select trace_private.contribution_consent_active(p_owner)
     and (exists (select 1 from trace_ingest.deidentified_resume_admissions a
                   where a.owner_user_id = p_owner and a.candidate_profile_id = p_profile)
          or exists (select 1 from trace_private.resume_quarantine q
                      where q.owner_user_id = p_owner and q.candidate_profile_id = p_profile
                        and q.status in ('pending_review', 'approved')))
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
  if not trace_private.contribution_consent_active(v_uid) then
    raise exception using errcode = 'TR022', message = 'identity markers are kept only for an active contribution consent';
  end if;
  insert into trace_private.identity_markers (owner_user_id, candidate_profile_id, sealed, key_id)
  values (v_uid, v_profile, p_sealed, p_key_id)
  on conflict (owner_user_id) do update
     set candidate_profile_id = excluded.candidate_profile_id, sealed = excluded.sealed, key_id = excluded.key_id,
         names = '{}', emails = '{}', phones = '{}', urls = '{}', handles = '{}', updated_at = now();
end;
$$;

-- Consent decisions: declining or withdrawing the contribution consent deletes the markers; withdrawing the
-- resume-processing consent deletes the open import draft. Confirmed profile records stay (the user deletes them
-- separately).
create function trace_private.on_consent_event_privacy()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.status <> 'granted' then
    if new.purpose = 'deidentified_shared_dataset' then
      delete from trace_private.identity_markers m where m.owner_user_id = new.user_id;
      update trace_private.resume_import_drafts d set identity_sealed = null, identity_key_id = null
       where d.owner_user_id = new.user_id and d.identity_sealed is not null;
    elsif new.purpose = 'resume_processing' then
      delete from trace_private.resume_import_drafts d where d.owner_user_id = new.user_id;
    end if;
  end if;
  return null;
end;
$$;
create trigger trace_consent_privacy after insert on public.trace_consent_events
  for each row execute function trace_private.on_consent_event_privacy();
revoke all on function trace_private.on_consent_event_privacy() from public, anon, authenticated, service_role;

do $$
begin
  if pg_catalog.pg_has_role('postgres', 'trace_import_writer', 'SET') or pg_catalog.pg_has_role('postgres', 'trace_import_writer', 'USAGE') then
    raise exception 'postgres must not be able to act as trace_import_writer';
  end if;
  if exists (select 1 from pg_catalog.pg_auth_members m
              where m.roleid = 'trace_import_writer'::regrole and (m.set_option or m.inherit_option)) then
    raise exception 'trace_import_writer must have no members that can use it';
  end if;
  if has_function_privilege('anon', 'trace_import.save_draft(uuid, jsonb, text, text, text, text, integer, text, text[], bytea, text)', 'execute')
     or has_function_privilege('authenticated', 'trace_import.my_draft(uuid, boolean)', 'execute')
     or has_function_privilege('service_role', 'trace_import.delete_draft(uuid, uuid)', 'execute')
     or not has_function_privilege('trace_app_user', 'trace_import.my_draft(uuid, boolean)', 'execute') then
    raise exception 'the import-draft functions must be executable by trace_app_user only';
  end if;
  if has_table_privilege('authenticated', 'trace_private.resume_import_drafts', 'select')
     or has_table_privilege('trace_app_user', 'trace_private.resume_import_drafts', 'select') then
    raise exception 'resume_import_drafts must have no client grant';
  end if;
end
$$;
