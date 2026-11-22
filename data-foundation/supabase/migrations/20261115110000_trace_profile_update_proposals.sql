-- Trace data foundation v2.14.0: profile update proposals (product Phase 2; decisions D5, D7).
--
-- ONE pipeline for profile changes that the user must accept before anything enters their profile:
-- * pasted experience text (parsed locally and deterministically);
-- * a resume uploaded into a profile that already holds records (ingestion still never merges: TR015 is unchanged);
-- * later, voice transcripts and the advisor's propose_profile_update.
-- The backend compares each parsed item with the profile and stores only items that are NEW or a POSSIBLE UPDATE of an
-- existing record. Anything already present is not stored.
--
-- Accepting a proposal is a separate, explicit user action:
-- * a new item becomes the user's own pending draft (information_origin user_stated, pending_review), created through
--   the existing candidate write paths;
-- * a possible update of a reviewed or extracted record is applied as a correction, with append-only history.
-- A confirmed record is never changed without the user accepting that specific proposal.
--
-- Rules enforced here:
-- * owner-only (RLS);
-- * the browser (authenticated) can read only; the backend owner role (trace_app_user) inserts and decides, through
--   column grants;
-- * the proposed content exists only while the proposal is open and is cleared when it is accepted or dismissed, so
--   decided proposals keep metadata only;
-- * a decision is final;
-- * an accepted proposal names the record it produced or changed, which must exist in the same profile;
-- * at most one OPEN proposal per profile and content fingerprint, so pasting or uploading the same thing twice never
--   duplicates.
-- No content of the source is stored apart from the proposed items: only the source's SHA-256 is kept, and never a
-- resume or the pasted text. Proposals are deleted with the profile (cascade), and so with the account.

create table public.trace_profile_update_proposals (
  proposal_id          uuid primary key default gen_random_uuid(),
  owner_user_id        uuid not null default auth.uid() references auth.users(id) on delete cascade,
  candidate_profile_id uuid not null,
  batch_id             uuid not null,
  source               text not null check (source in ('pasted_text', 'resume_update')),
  source_sha256        text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  extractor_version    text not null check (char_length(extractor_version) between 1 and 60),
  record_kind          text not null check (record_kind in ('employment', 'evidence', 'education', 'activity', 'skill')),
  match_state          text not null check (match_state in ('new', 'possible_update')),
  target_record_id     uuid,
  fingerprint          text not null check (fingerprint ~ '^[0-9a-f]{64}$'),
  proposed             jsonb,
  status               text not null default 'proposed' check (status in ('proposed', 'accepted', 'dismissed')),
  resulting_record_id  uuid,
  created_at           timestamptz not null default now(),
  decided_at           timestamptz,
  foreign key (candidate_profile_id, owner_user_id)
    references public.trace_candidate_profiles (candidate_profile_id, owner_user_id) on delete cascade,
  constraint update_proposals_content_only_while_open check ((status = 'proposed') = (proposed is not null)),
  constraint update_proposals_decided_at check ((status = 'proposed') = (decided_at is null)),
  constraint update_proposals_accepted_has_record check ((status = 'accepted') = (resulting_record_id is not null)),
  constraint update_proposals_object check (proposed is null or pg_catalog.jsonb_typeof(proposed) = 'object'),
  constraint update_proposals_size check (proposed is null or pg_catalog.octet_length(proposed::text) <= 20000),
  -- Evidence always attaches to an existing employment; a possible update always names the record it would change.
  constraint update_proposals_target check ((match_state = 'possible_update' or record_kind = 'evidence') = (target_record_id is not null)),
  constraint update_proposals_update_kinds check (match_state <> 'possible_update' or record_kind in ('employment', 'education'))
);
alter table public.trace_profile_update_proposals enable row level security;
create unique index trace_profile_update_proposals_open_unique
  on public.trace_profile_update_proposals (candidate_profile_id, fingerprint) where status = 'proposed';
create index trace_profile_update_proposals_owner_idx on public.trace_profile_update_proposals (owner_user_id);
create index trace_profile_update_proposals_profile_idx
  on public.trace_profile_update_proposals (candidate_profile_id, owner_user_id, status, created_at);

-- A record id named by a proposal must exist in the same owner's profile, in the table of its kind.
create function trace_util.proposal_record_exists(p_kind text, p_owner uuid, p_profile uuid, p_record uuid)
returns boolean
language sql
stable
set search_path = ''
as $$
  select case p_kind
    when 'employment' then exists (select 1 from public.trace_candidate_employments t
                                    where t.candidate_employment_id = p_record and t.owner_user_id = p_owner and t.candidate_profile_id = p_profile)
    when 'evidence' then exists (select 1 from public.trace_candidate_evidence t
                                  where t.candidate_evidence_id = p_record and t.owner_user_id = p_owner and t.candidate_profile_id = p_profile)
    when 'education' then exists (select 1 from public.trace_candidate_education t
                                   where t.candidate_education_id = p_record and t.owner_user_id = p_owner and t.candidate_profile_id = p_profile)
    when 'activity' then exists (select 1 from public.trace_candidate_career_activities t
                                  where t.candidate_activity_id = p_record and t.owner_user_id = p_owner and t.candidate_profile_id = p_profile)
    when 'skill' then exists (select 1 from public.trace_candidate_skills t
                               where t.candidate_skill_id = p_record and t.owner_user_id = p_owner and t.candidate_profile_id = p_profile)
    else false
  end
$$;

create function trace_util.guard_update_proposal()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.status <> 'proposed' then
      raise exception using errcode = 'TR022', message = 'a proposal starts open';
    end if;
    if new.target_record_id is not null and not trace_util.proposal_record_exists(
         case when new.record_kind = 'evidence' then 'employment' else new.record_kind end,
         new.owner_user_id, new.candidate_profile_id, new.target_record_id) then
      raise exception using errcode = 'TR022', message = 'the proposal names a record outside this profile';
    end if;
    return new;
  end if;
  -- UPDATE: only open -> accepted | dismissed, once. Identity and content-derived columns never change.
  if old.status <> 'proposed' then
    raise exception using errcode = 'TR022', message = 'this proposal was already decided';
  end if;
  if new.status not in ('accepted', 'dismissed') then
    raise exception using errcode = 'TR022', message = 'a proposal can only be accepted or dismissed';
  end if;
  if (new.proposal_id, new.owner_user_id, new.candidate_profile_id, new.batch_id, new.source, new.source_sha256,
      new.extractor_version, new.record_kind, new.match_state, new.target_record_id, new.fingerprint, new.created_at)
     is distinct from
     (old.proposal_id, old.owner_user_id, old.candidate_profile_id, old.batch_id, old.source, old.source_sha256,
      old.extractor_version, old.record_kind, old.match_state, old.target_record_id, old.fingerprint, old.created_at) then
    raise exception using errcode = 'TR022', message = 'only the decision of a proposal can change';
  end if;
  if new.status = 'accepted' then
    if not trace_util.proposal_record_exists(new.record_kind, new.owner_user_id, new.candidate_profile_id, new.resulting_record_id) then
      raise exception using errcode = 'TR022', message = 'an accepted proposal must name a record of this profile';
    end if;
    if new.match_state = 'possible_update' and new.resulting_record_id is distinct from new.target_record_id then
      raise exception using errcode = 'TR022', message = 'an accepted update changes the record it named';
    end if;
  end if;
  return new;
end;
$$;

create trigger trace_guard_update_proposal
  before insert or update on public.trace_profile_update_proposals
  for each row execute function trace_util.guard_update_proposal();

revoke all on public.trace_profile_update_proposals from public, anon, authenticated, service_role;
grant select on public.trace_profile_update_proposals to authenticated;
grant insert (proposal_id, owner_user_id, candidate_profile_id, batch_id, source, source_sha256, extractor_version, record_kind, match_state,
              target_record_id, fingerprint, proposed)
  on public.trace_profile_update_proposals to trace_app_user;
grant update (status, proposed, resulting_record_id, decided_at) on public.trace_profile_update_proposals to trace_app_user;
create policy "profile update proposals owner all" on public.trace_profile_update_proposals
  for all to authenticated
  using (owner_user_id = (select auth.uid()))
  with check (owner_user_id = (select auth.uid()));

revoke all on function trace_util.proposal_record_exists(text, uuid, uuid, uuid), trace_util.guard_update_proposal()
  from public, anon, authenticated, service_role;
grant execute on function trace_util.proposal_record_exists(text, uuid, uuid, uuid), trace_util.guard_update_proposal()
  to trace_app_user;
