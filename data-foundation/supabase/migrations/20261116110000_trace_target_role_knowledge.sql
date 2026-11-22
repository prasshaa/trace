-- Trace data foundation v2.16.0: target-role knowledge packs (product Phase 3; decisions D12, D13, D16–D19, D22–D26).
--
-- Role-agnostic structures for curated target-role knowledge. PM is the first pack (seeded separately, v2.17.0).
-- Additive only:
--
--   trace_target_role_sources (existing; extended)
--     A source in a knowledge pack: kind (framework, national_standard, employer_framework, job_posting), publisher,
--     geography, licence, attribution, level as stated, context tags, last verification and revalidation date, status
--     note. For job postings only: the entry-background stance (D23), the backgrounds accepted or excluded, one short
--     verbatim quotation and its locator. The existing source_status values hold the D26 lifecycle.
--   trace_target_role_requirements (existing; reused unchanged)
--     Employer-specific criteria stated by one source (years, degree, domain), never canonical requirements.
--   trace_capability_requirements (new)
--     Canonical requirements for a role family and level, classified as core, context_dependent or differentiator
--     (D19). It also holds the curated transferability mapping used by the deterministic assessment: direct and
--     transferable responsibility areas and skills, transferable role families, the rationale, and whether absence
--     can ever support a Gap (gap_basis).
--   trace_requirement_source_evidence (new)
--     Many-to-many requirement <-> source, with stance (supports, qualifies, disagrees, silent), locator and note.
--   trace_util.posting_effective_status (D26)
--     A current job posting is current only before its revalidation date; afterwards it reads as
--     current_check_required. Frameworks follow their own policies and are returned unchanged.
--   trace_util.record_posting_verification (D26; administrators only)
--     Applies one verification outcome: open, closed, withdrawn, inaccessible or ambiguous. An inaccessible or
--     ambiguous source is never labelled closed.
--
-- Access: signed-in users may read knowledge (it is public, curated reference content, like the taxonomy). No client
-- role may write; only administrators (the table owner) seed and verify. Withdrawn sources are hidden from clients, as
-- before. No user data is involved.

-- ------------------------------------------------------------------ sources: knowledge-pack metadata
alter table public.trace_target_role_sources
  add column source_key              text,
  add column knowledge_pack          text,
  add column source_kind             text,
  add column publisher               text,
  add column geography               text,
  add column licence                 text,
  add column attribution             text,
  add column level_as_stated         text,
  add column context_tags            text[] not null default '{}',
  add column last_verified_at        timestamptz,
  add column revalidate_by           timestamptz,
  add column status_note             text,
  add column entry_background_stance text,
  add column accepted_backgrounds    text[] not null default '{}',
  add column excluded_backgrounds    text[] not null default '{}',
  add column entry_quote             text,
  add column entry_locator           text;

alter table public.trace_target_role_sources
  add constraint target_sources_key_unique unique (source_key),
  add constraint target_sources_kind check (source_kind in ('framework', 'national_standard', 'employer_framework', 'job_posting')),
  add constraint target_sources_pack_complete check (
    knowledge_pack is null
    or (source_key is not null and source_kind is not null and publisher is not null and licence is not null
        and attribution is not null and last_verified_at is not null)),
  add constraint target_sources_posting_type check (source_kind is distinct from 'job_posting' or source_type = 'official_job_posting'),
  add constraint target_sources_stance check (entry_background_stance in
    ('accepts_adjacent', 'requires_target_role_experience', 'excludes_specified_transitions', 'not_specified')),
  -- The stance and its evidence belong to job postings only, and every pack posting has one.
  add constraint target_sources_stance_postings_only check (
    source_kind is null or (source_kind = 'job_posting') = (entry_background_stance is not null)),
  add constraint target_sources_accepted_only_when_accepting check (
    cardinality(accepted_backgrounds) = 0 or entry_background_stance = 'accepts_adjacent'),
  add constraint target_sources_excluded_only_when_excluding check (
    (cardinality(excluded_backgrounds) > 0) = (entry_background_stance is not distinct from 'excludes_specified_transitions')
    or entry_background_stance is null),
  add constraint target_sources_quote_short check (entry_quote is null or char_length(entry_quote) between 1 and 400),
  add constraint target_sources_quote_has_locator check (entry_quote is null or entry_locator is not null),
  add constraint target_sources_posting_current_has_revalidation check (
    source_kind is distinct from 'job_posting' or source_status <> 'current' or revalidate_by is not null);
create index trace_target_role_sources_pack_idx on public.trace_target_role_sources (knowledge_pack, source_kind);

-- ------------------------------------------------------------------ canonical requirements and the transferability mapping
create table public.trace_capability_requirements (
  capability_requirement_id uuid primary key default gen_random_uuid(),
  knowledge_pack            text not null check (char_length(knowledge_pack) between 1 and 40),
  requirement_key           text not null check (requirement_key ~ '^[A-Z][0-9]{1,2}$'),
  role_family               text not null references public.trace_role_families(role_family),
  applies_to_level          text not null check (applies_to_level in ('pm', 'senior_pm', 'all')),
  -- Employer-specific requirements are not canonical: they stay per source in trace_target_role_requirements (D19).
  requirement_class         text not null check (requirement_class in ('core', 'context_dependent', 'differentiator')),
  title                     text not null check (char_length(title) between 1 and 160),
  description               text not null check (char_length(description) between 1 and 600),
  level_notes               jsonb not null default '{}'::jsonb check (jsonb_typeof(level_notes) = 'object'),
  contexts                  text[] not null default '{}' check (contexts <@ array['commercial_product', 'growth_analytics', 'platform_api',
                                                                                 'data_product', 'regulated_domain', 'ai_product']),
  direct_areas              text[] not null default '{}',
  transferable_areas        text[] not null default '{}',
  direct_skills             text[] not null default '{}',
  transferable_skills       text[] not null default '{}',
  transferable_role_families text[] not null default '{}',
  transfer_rationale        text check (transfer_rationale is null or char_length(transfer_rationale) between 1 and 400),
  -- reviewed_classified_evidence: a Gap may be stated when the user's reviewed evidence is fully classified and none
  -- maps to the requirement. not_inferable: absence never supports a Gap (the result stays Unknown).
  gap_basis                 text not null check (gap_basis in ('reviewed_classified_evidence', 'not_inferable')),
  created_at                timestamptz not null default now(),
  updated_at                timestamptz not null default now(),
  unique (knowledge_pack, requirement_key),
  constraint capability_context_only_for_context_dependent check ((requirement_class = 'context_dependent') = (cardinality(contexts) > 0)),
  constraint capability_differentiators_are_senior check ((requirement_class = 'differentiator') = (applies_to_level = 'senior_pm')),
  constraint capability_transfer_has_rationale check (
    cardinality(transferable_areas) + cardinality(transferable_skills) + cardinality(transferable_role_families) = 0
    or transfer_rationale is not null),
  constraint capability_gap_needs_area_basis check (gap_basis = 'not_inferable' or cardinality(direct_areas) > 0)
);
alter table public.trace_capability_requirements enable row level security;

-- The mapping names taxonomy codes; they must exist (arrays cannot carry foreign keys).
create function trace_util.check_capability_codes()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_bad text;
begin
  select string_agg(x, ', ') into v_bad
    from unnest(new.direct_areas || new.transferable_areas) x
   where not exists (select 1 from public.trace_responsibility_areas a where a.responsibility_area = x);
  if v_bad is not null then
    raise exception using errcode = 'TR025', message = format('unknown responsibility areas: %s', v_bad);
  end if;
  select string_agg(x, ', ') into v_bad
    from unnest(new.direct_skills || new.transferable_skills) x
   where not exists (select 1 from public.trace_skills s where s.skill_id = x);
  if v_bad is not null then
    raise exception using errcode = 'TR025', message = format('unknown skills: %s', v_bad);
  end if;
  select string_agg(x, ', ') into v_bad
    from unnest(new.transferable_role_families) x
   where not exists (select 1 from public.trace_role_families f where f.role_family = x);
  if v_bad is not null then
    raise exception using errcode = 'TR025', message = format('unknown role families: %s', v_bad);
  end if;
  return new;
end;
$$;
create trigger trace_check_capability_codes before insert or update on public.trace_capability_requirements
  for each row execute function trace_util.check_capability_codes();
create trigger set_updated_at before update on public.trace_capability_requirements
  for each row execute function trace_util.set_updated_at();

-- ------------------------------------------------------------------ requirement <-> source evidence (many-to-many)
create table public.trace_requirement_source_evidence (
  capability_requirement_id uuid not null references public.trace_capability_requirements(capability_requirement_id) on delete cascade,
  target_role_source_id     uuid not null references public.trace_target_role_sources(target_role_source_id) on delete cascade,
  stance                    text not null check (stance in ('supports', 'qualifies', 'disagrees', 'silent')),
  locator                   text check (locator is null or char_length(locator) between 1 and 200),
  note                      text check (note is null or char_length(note) between 1 and 300),
  primary key (capability_requirement_id, target_role_source_id)
);
alter table public.trace_requirement_source_evidence enable row level security;
create index trace_requirement_source_evidence_source_idx on public.trace_requirement_source_evidence (target_role_source_id);

-- ------------------------------------------------------------------ D26: posting lifecycle
create function trace_util.posting_effective_status(p_source_kind text, p_source_status text, p_revalidate_by timestamptz,
                                                    p_at timestamptz)
returns text
language sql
immutable
set search_path = ''
as $$
  select case
    when p_source_kind = 'job_posting' and p_source_status = 'current'
         and (p_revalidate_by is null or p_at is null or p_at >= p_revalidate_by)
      then 'current_check_required'
    else p_source_status
  end
$$;

create function trace_util.record_posting_verification(p_source_key text, p_outcome text, p_verified_at timestamptz default now(),
                                                       p_window_days integer default 14)
returns text
language plpgsql
set search_path = ''
as $$
declare
  v_kind text;
  v_status text;
begin
  if p_outcome is null or p_outcome not in ('open', 'closed', 'withdrawn', 'inaccessible', 'ambiguous') then
    raise exception using errcode = 'TR024', message = 'outcome must be open, closed, withdrawn, inaccessible or ambiguous';
  end if;
  if p_window_days is null or p_window_days not between 1 and 90 then
    raise exception using errcode = 'TR024', message = 'the revalidation window is 1 to 90 days';
  end if;
  select s.source_kind, s.source_status into v_kind, v_status
    from public.trace_target_role_sources s where s.source_key = p_source_key for update;
  if not found then
    raise exception using errcode = 'TR024', message = format('unknown source %s', p_source_key);
  end if;
  if v_kind is distinct from 'job_posting' then
    raise exception using errcode = 'TR024', message = 'only job postings follow the posting revalidation policy';
  end if;
  if v_status in ('expired', 'withdrawn') then
    if p_outcome in ('inaccessible', 'ambiguous') then
      return v_status;                                  -- a terminal status is never reopened by an unclear check
    end if;
    raise exception using errcode = 'TR024', message = format('the posting is %s (terminal); a reopened role is a new source', v_status);
  end if;
  if p_outcome = 'open' then
    update public.trace_target_role_sources
       set source_status = 'current', last_verified_at = p_verified_at,
           revalidate_by = p_verified_at + make_interval(days => p_window_days), status_note = null, updated_at = now()
     where source_key = p_source_key;
    return 'current';
  elsif p_outcome in ('closed', 'withdrawn') then
    update public.trace_target_role_sources
       set source_status = case p_outcome when 'closed' then 'expired' else 'withdrawn' end, last_verified_at = p_verified_at,
           revalidate_by = null,
           status_note = format('%s by the employer (verified %s)', initcap(p_outcome), to_char(p_verified_at, 'YYYY-MM-DD')),
           updated_at = now()
     where source_key = p_source_key;
    return case p_outcome when 'closed' then 'expired' else 'withdrawn' end;
  end if;
  -- inaccessible or ambiguous: never closed; not current until a clear check
  update public.trace_target_role_sources
     set source_status = 'current_check_required',
         status_note = format('Source %s on %s; requires revalidation', p_outcome, to_char(p_verified_at, 'YYYY-MM-DD')),
         updated_at = now()
   where source_key = p_source_key;
  return 'current_check_required';
end;
$$;

-- ------------------------------------------------------------------ privileges and RLS
revoke all on public.trace_capability_requirements, public.trace_requirement_source_evidence
  from public, anon, authenticated, service_role;
grant select on public.trace_capability_requirements, public.trace_requirement_source_evidence to authenticated, service_role;
create policy "capability requirements read" on public.trace_capability_requirements
  for select to authenticated using (true);
create policy "requirement source evidence read" on public.trace_requirement_source_evidence
  for select to authenticated using (exists (
    select 1 from public.trace_target_role_sources s
     where s.target_role_source_id = trace_requirement_source_evidence.target_role_source_id and s.source_status <> 'withdrawn'));

revoke all on function trace_util.check_capability_codes(), trace_util.record_posting_verification(text, text, timestamptz, integer)
  from public, anon, authenticated, service_role;
revoke all on function trace_util.posting_effective_status(text, text, timestamptz, timestamptz) from public, anon, service_role;
grant execute on function trace_util.posting_effective_status(text, text, timestamptz, timestamptz) to authenticated;
