-- Trace Data Foundation v2 · 04 · consent documents and consent events
--
-- Consent is purpose-specific and recorded as an append-only event log per user.
-- The current state for (user, purpose) is the latest event (trace_ref.v_current_consent).
-- A grant is only effective when the consent document it references has been approved
-- (legal_review_status = 'approved'). No consent document is seeded: the drafts in
-- docs/INTAKE_AND_CONSENT.md must be reviewed by counsel first.

create table public.trace_consent_documents (
  consent_version     text not null check (consent_version ~ '^[a-z0-9][a-z0-9._-]*$'),
  purpose             public.trace_consent_purpose not null,
  title               text not null,
  body_sha256         text not null check (body_sha256 ~ '^[0-9a-f]{64}$'),
  legal_review_status text not null default 'draft_not_reviewed'
                      check (legal_review_status in ('draft_not_reviewed', 'approved', 'retired')),
  effective_from      timestamptz,
  retired_at          timestamptz,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  primary key (consent_version, purpose),
  constraint trace_consent_documents_approved_has_date check (legal_review_status <> 'approved' or effective_from is not null)
);
alter table public.trace_consent_documents enable row level security;
create trigger set_updated_at before update on public.trace_consent_documents
  for each row execute function trace_util.set_updated_at();

create table public.trace_consent_events (
  consent_event_id uuid primary key default gen_random_uuid(),
  user_id          uuid not null default auth.uid() references auth.users(id) on delete cascade,
  purpose          public.trace_consent_purpose not null,
  status           public.trace_consent_status not null,
  consent_version  text not null,
  recorded_at      timestamptz not null default now(),
  source           text not null default 'in_app' check (source in ('in_app', 'support_request', 'admin_correction')),
  notes            text,
  foreign key (consent_version, purpose) references public.trace_consent_documents(consent_version, purpose)
);
alter table public.trace_consent_events enable row level security;
create index trace_consent_events_current_idx on public.trace_consent_events (user_id, purpose, recorded_at desc);
create index trace_consent_events_document_idx on public.trace_consent_events (consent_version, purpose);

-- Append-only: corrections are new events. Rows are removed only by the auth.users cascade.
create trigger consent_events_append_only before update on public.trace_consent_events
  for each row execute function trace_util.prevent_update();

comment on table public.trace_consent_events is
  'Append-only consent log. Latest event per (user_id, purpose) is the current state. Withdrawal = new event with status withdrawn.';
