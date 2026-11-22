-- Trace data foundation v2.6.0: trusted role mapping for candidate employments (M5).
--
-- A candidate's employment gets a taxonomy role in one of two ways, both through trace_review functions:
--   * trace_review.apply_alias_mapping: an automatic mapping from public.trace_role_aliases. The database
--     re-checks the alias itself: exactly one alias row for the record's normalised title, not
--     context-required, its role active and resolved. Role, seniority hint and confidence come from the
--     alias row, never from the caller. Status 'auto_high_confidence', basis 'alias_match'.
--   * trace_review.choose_employment_role: the candidate's own decision. Any active, resolved role
--     (never ROLE-UNKNOWN, retired or unresolved roles). Status 'confirmed', basis 'candidate_selected',
--     mapping_confidence NULL (a candidate decision is not an independently verified confidence).
--     A Product Owner-family title (trace_util.is_product_owner_title: "Product Owner", qualified forms such
--     as "Product Owner (Payments)", or any title whose aliases include a product-ownership role) can be
--     mapped to a product-management role only with at least one PM-ownership responsibility area
--     (data dictionary §9), stored with the mapping (TR020); apply_alias_mapping never does it (TR020).
--   * Titles are compared under one normalisation contract (trace_util.normalize_title, below), the same in
--     the backend; the alias table carries a CHECK that its stored normalisation equals the contract.
--
-- Rules:
--   * Mapping never changes review_status and never makes a pending record eligible (M1 barrier rule).
--     Rejected records cannot be mapped (TR001; reinstate first).
--   * mapped_title_raw keeps the exact title a mapping was based on. When title_raw later changes (a draft
--     edit or an M4 correction), mapping_is_stale becomes true and the mapping is no longer eligible for
--     sequencing until a fresh mapping decision is made. Nothing is silently re-mapped.
--   * An automatic mapping never replaces a current candidate decision or a mapping that was set outside
--     these functions (TR019). A candidate decision may replace any mapping.
--   * Both functions require the caller's expected title to equal the record's title (TR018), so a
--     mapping can never be applied to a title the caller did not see.
--   * Candidate-selected mappings are for the candidate's own sequence only; candidate data never enters
--     trace_ref (the reference cohort) automatically, and no function here touches trace_ref.
--   * Only these functions can write mapping columns: authenticated, anon, service_role and the ingest
--     writer have no UPDATE (or INSERT) privilege on them. The table owner keeps administrative access.
--
-- Error codes (in addition to TR001-TR017): TR018 the title changed since the mapping was proposed;
-- TR019 automatic mapping refused; TR020 PM-ownership clarification required.

-- ------------------------------------------------------------------ columns and constraints
alter table public.trace_candidate_employments
  add column role_mapping_basis text check (role_mapping_basis in ('alias_match', 'candidate_selected')),
  add column role_mapping_alias_id text references public.trace_role_aliases(alias_id),
  add column mapped_title_raw text,
  add column role_mapping_ownership_areas text[],
  add column mapping_is_stale boolean generated always as (mapped_title_raw is not null and mapped_title_raw is distinct from title_raw) stored,
  alter column mapping_confidence drop not null;

alter table public.trace_candidate_employments
  add constraint candidate_employments_mapping_basis_title check ((role_mapping_basis is null) = (mapped_title_raw is null)),
  add constraint candidate_employments_mapping_alias_match check (
    role_mapping_basis is distinct from 'alias_match'
    or (role_mapping_alias_id is not null and mapping_status = 'auto_high_confidence' and mapping_confidence is not null
        and role_mapping_ownership_areas is null)),
  add constraint candidate_employments_mapping_candidate_selected check (
    role_mapping_basis is distinct from 'candidate_selected'
    or (role_mapping_alias_id is null and mapping_status = 'confirmed' and mapping_confidence is null)),
  add constraint candidate_employments_mapping_unbased check (
    role_mapping_basis is not null
    or (role_mapping_alias_id is null and role_mapping_ownership_areas is null and mapping_confidence is not null)),
  add constraint candidate_employments_mapping_ownership_areas check (
    role_mapping_ownership_areas is null or cardinality(role_mapping_ownership_areas) >= 1);

-- ------------------------------------------------------------------ title normalisation contract (v1)
-- One contract, implemented identically here and in the backend (app/services/role_mapping/matcher.py),
-- independent of the database locale, collation or ctype:
--   1. every run of the 29 whitespace code points below becomes one ASCII space (U+0020):
--      U+0009-U+000D, U+001C-U+001F, U+0020, U+0085, U+00A0, U+1680, U+2000-U+200A, U+2028, U+2029,
--      U+202F, U+205F, U+3000;
--   2. leading and trailing spaces are removed;
--   3. only ASCII A-Z are lower-cased; every other character (punctuation, dashes, non-ASCII letters such
--      as the Kelvin sign or dotted capital I) is kept as it is, so no character is folded into ASCII;
--   4. an empty result is NULL.
-- A character outside the contract is never changed, so a title containing it simply matches no alias
-- (aliases are plain text) and goes to a candidate decision: unsupported input fails closed.
create function trace_util.normalize_title(p_title text)
returns text
language sql immutable strict parallel safe
set search_path = ''
as $$
select nullif(pg_catalog.btrim(pg_catalog.translate(
         pg_catalog.regexp_replace(p_title, '[\u0009-\u000d\u001c-\u001f\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+', ' ', 'g'),
         'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), ' '), '')
$$;

-- Product Owner family (data dictionary §9), detected without alias matching: the normalised title
-- contains the words "product owner", bounded on each side by the start or end of the title or by a
-- character other than a-z and 0-9. Recognises "Product Owner", "Senior Product Owner",
-- "Product Owner (Payments)", "Product Owner - Growth"; not "Product Ownership" or "Product-Owner".
-- Detection never maps anything: it only decides whether a PM-ownership clarification is required.
create function trace_util.is_product_owner_title(p_title text)
returns boolean
language sql immutable parallel safe
set search_path = ''
as $$
select coalesce(trace_util.normalize_title(p_title) ~ '(^|[^a-z0-9])product owner($|[^a-z0-9])', false)
$$;

revoke all on function trace_util.normalize_title(text), trace_util.is_product_owner_title(text) from public, anon, authenticated, service_role;
grant execute on function trace_util.normalize_title(text), trace_util.is_product_owner_title(text) to trace_review_writer;

-- The stored alias normalisation must equal the contract, now and for every future alias row.
alter table public.trace_role_aliases
  add constraint trace_role_aliases_normalized_contract check (alias_normalized = trace_util.normalize_title(alias));

-- ------------------------------------------------------------------ apply_alias_mapping
create function trace_review.apply_alias_mapping(p_candidate_profile_id uuid, p_employment_id uuid, p_alias_id text,
                                                 p_expected_title text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  e record;
  a record;
  v_lookup text;
begin
  if v_uid is null then
    raise exception using errcode = 'TR004', message = 'no request identity';
  end if;
  select t.title_raw, t.review_status, t.role_id, t.mapping_status, t.role_mapping_basis, t.mapping_is_stale
    into e
    from public.trace_candidate_employments t
   where t.candidate_employment_id = p_employment_id and t.candidate_profile_id = p_candidate_profile_id
     and t.owner_user_id = v_uid
     for update;
  if not found then
    raise exception using errcode = 'TR005', message = format('employment %s not found', p_employment_id);
  end if;
  if e.review_status = 'rejected' then
    raise exception using errcode = 'TR001', message = 'a rejected record cannot be mapped; reinstate it first';
  end if;
  if p_expected_title is null or e.title_raw is distinct from p_expected_title then
    raise exception using errcode = 'TR018', message = 'the title changed since the mapping was proposed';
  end if;
  if e.role_mapping_basis = 'candidate_selected' and not e.mapping_is_stale then
    raise exception using errcode = 'TR019', message = 'an automatic mapping never replaces the candidate''s own decision';
  end if;
  if e.role_mapping_basis is null and (e.role_id <> 'ROLE-UNKNOWN' or e.mapping_status <> 'unresolved') then
    raise exception using errcode = 'TR019', message = 'an automatic mapping never replaces a mapping set outside this function';
  end if;

  select x.alias_id, x.alias_normalized, x.role_id, x.seniority_hint_code, x.context_required, x.default_confidence,
         r.is_resolved, r.active, r.role_family
    into a
    from public.trace_role_aliases x
    join public.trace_role_taxonomy r on r.role_id = x.role_id
   where x.alias_id = p_alias_id;
  if not found then
    raise exception using errcode = 'TR006', message = format('alias %s does not exist', coalesce(p_alias_id, 'NULL'));
  end if;
  v_lookup := trace_util.normalize_title(e.title_raw);
  if a.alias_normalized is distinct from v_lookup then
    raise exception using errcode = 'TR019', message = 'the alias does not match the title exactly';
  end if;
  if a.context_required then
    raise exception using errcode = 'TR019', message = 'a context-required alias is never mapped automatically';
  end if;
  if exists (select 1 from public.trace_role_aliases x where x.alias_normalized = a.alias_normalized and x.alias_id <> a.alias_id) then
    raise exception using errcode = 'TR019', message = 'the title maps to several roles; a decision is required';
  end if;
  if not (a.is_resolved and a.active) then
    raise exception using errcode = 'TR019', message = format('the alias role %s is not an active, resolved role', a.role_id);
  end if;
  -- A Product Owner-family title reaches a product-management role only through the candidate's decision
  -- with PM-ownership responsibility areas, never automatically.
  if a.role_family = 'product_management' and trace_util.is_product_owner_title(e.title_raw) then
    raise exception using errcode = 'TR020',
      message = 'a product-ownership title is mapped to a product-management role only by a decision with PM-ownership responsibility areas';
  end if;

  update public.trace_candidate_employments t
     set role_id = a.role_id, seniority_code = a.seniority_hint_code, mapping_status = 'auto_high_confidence',
         mapping_confidence = a.default_confidence, mapping_note = null, role_mapping_basis = 'alias_match',
         role_mapping_alias_id = a.alias_id, mapped_title_raw = e.title_raw, role_mapping_ownership_areas = null
   where t.candidate_employment_id = p_employment_id and t.candidate_profile_id = p_candidate_profile_id
     and t.owner_user_id = v_uid;
  return a.role_id;
end
$$;

-- ------------------------------------------------------------------ choose_employment_role
create function trace_review.choose_employment_role(p_candidate_profile_id uuid, p_employment_id uuid, p_role_id text,
                                                    p_expected_title text, p_ownership_areas text[])
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  e record;
  r record;
  v_lookup text;
  v_needs_clarification boolean;
  v_areas text[];
begin
  if v_uid is null then
    raise exception using errcode = 'TR004', message = 'no request identity';
  end if;
  select t.title_raw, t.review_status
    into e
    from public.trace_candidate_employments t
   where t.candidate_employment_id = p_employment_id and t.candidate_profile_id = p_candidate_profile_id
     and t.owner_user_id = v_uid
     for update;
  if not found then
    raise exception using errcode = 'TR005', message = format('employment %s not found', p_employment_id);
  end if;
  if e.review_status = 'rejected' then
    raise exception using errcode = 'TR001', message = 'a rejected record cannot be mapped; reinstate it first';
  end if;
  if p_expected_title is null or e.title_raw is distinct from p_expected_title then
    raise exception using errcode = 'TR018', message = 'the title changed since the mapping was proposed';
  end if;

  select x.role_id, x.role_family, x.is_resolved, x.active into r
    from public.trace_role_taxonomy x where x.role_id = p_role_id;
  if not found or not r.is_resolved or not r.active then
    raise exception using errcode = 'TR006',
      message = format('%s is not an active, resolved taxonomy role', coalesce(p_role_id, 'NULL'));
  end if;

  -- Product Owner rule (data dictionary §9): a Product Owner-family title (detected by its words, with or
  -- without qualifiers) or a title whose aliases include a product-ownership role is mapped to a
  -- product-management role only with PM-ownership responsibility areas.
  v_lookup := trace_util.normalize_title(e.title_raw);
  v_needs_clarification := r.role_family = 'product_management' and (
    trace_util.is_product_owner_title(e.title_raw)
    or exists (select 1 from public.trace_role_aliases x join public.trace_role_taxonomy o on o.role_id = x.role_id
                where x.alias_normalized = v_lookup and o.role_family = 'product_ownership'));
  if p_ownership_areas is not null and cardinality(p_ownership_areas) > 0 then
    if not v_needs_clarification then
      raise exception using errcode = 'TR006', message = 'ownership areas apply only to a product-ownership title mapped to a product-management role';
    end if;
    if exists (select 1 from unnest(p_ownership_areas) k
                where k is null or not exists (select 1 from public.trace_responsibility_areas ra
                                                where ra.responsibility_area = k and ra.is_pm_ownership_signal)) then
      raise exception using errcode = 'TR006', message = 'ownership areas must be PM-ownership responsibility areas';
    end if;
    select array_agg(distinct k order by k) into v_areas from unnest(p_ownership_areas) k;
  end if;
  if v_needs_clarification and v_areas is null then
    raise exception using errcode = 'TR020',
      message = 'mapping a product-ownership title to a product-management role needs at least one PM-ownership responsibility area';
  end if;

  update public.trace_candidate_employments t
     set role_id = r.role_id, seniority_code = null, mapping_status = 'confirmed', mapping_confidence = null,
         mapping_note = null, role_mapping_basis = 'candidate_selected', role_mapping_alias_id = null,
         mapped_title_raw = e.title_raw, role_mapping_ownership_areas = v_areas
   where t.candidate_employment_id = p_employment_id and t.candidate_profile_id = p_candidate_profile_id
     and t.owner_user_id = v_uid;
  return r.role_id;
end
$$;

-- ------------------------------------------------------------------ privileges and RLS
grant update (role_id, seniority_code, mapping_status, mapping_confidence, mapping_note, role_mapping_basis,
              role_mapping_alias_id, mapped_title_raw, role_mapping_ownership_areas)
  on public.trace_candidate_employments to trace_review_writer;
grant select on public.trace_role_aliases, public.trace_role_taxonomy, public.trace_responsibility_areas to trace_review_writer;
create policy "lookup review writer read" on public.trace_role_aliases for select to trace_review_writer using (true);
create policy "lookup review writer read" on public.trace_role_taxonomy for select to trace_review_writer using (true);
create policy "lookup review writer read" on public.trace_responsibility_areas for select to trace_review_writer using (true);

revoke all on function trace_review.apply_alias_mapping(uuid, uuid, text, text),
                       trace_review.choose_employment_role(uuid, uuid, text, text, text[])
  from public, anon, authenticated, service_role;
grant execute on function trace_review.apply_alias_mapping(uuid, uuid, text, text),
                          trace_review.choose_employment_role(uuid, uuid, text, text, text[]) to authenticated;

-- ------------------------------------------------------------------ ownership transfer (the M2 procedure)
grant trace_review_writer to postgres with set true, inherit false;
grant create on schema trace_review to trace_review_writer;
alter function trace_review.apply_alias_mapping(uuid, uuid, text, text) owner to trace_review_writer;
alter function trace_review.choose_employment_role(uuid, uuid, text, text, text[]) owner to trace_review_writer;
revoke create on schema trace_review from trace_review_writer;
revoke trace_review_writer from postgres;

do $$
declare
  v_bad text;
begin
  if pg_catalog.pg_has_role('postgres', 'trace_review_writer', 'SET') or pg_catalog.pg_has_role('postgres', 'trace_review_writer', 'USAGE') then
    raise exception 'postgres must not be able to act as trace_review_writer';
  end if;
  if exists (select 1 from pg_catalog.pg_auth_members m
              where m.roleid = 'trace_review_writer'::regrole and (m.set_option or m.inherit_option)) then
    raise exception 'trace_review_writer must have no members that can use it';
  end if;
  if pg_catalog.has_schema_privilege('trace_review_writer', 'trace_review', 'CREATE') then
    raise exception 'trace_review_writer must not keep CREATE on trace_review';
  end if;
  select string_agg(p.oid::regprocedure::text || ' owned by ' || pg_catalog.pg_get_userbyid(p.proowner), '; ') into v_bad
  from pg_catalog.pg_proc p
  where p.pronamespace = 'trace_review'::regnamespace
    and pg_catalog.pg_get_userbyid(p.proowner) <> case when p.proname in ('review_record', 'reassign_employment', 'correct_record',
                                                                          'apply_alias_mapping', 'choose_employment_role')
                                                       then 'trace_review_writer' else 'postgres' end;
  if v_bad is not null then
    raise exception 'unexpected trace_review function owner: %', v_bad;
  end if;
  select string_agg(r.rolname || ':' || c.col || ':' || x.p, ', ') into v_bad
  from (values ('authenticated'), ('service_role'), ('anon'), ('trace_ingest_writer')) r(rolname)
  cross join (values ('role_id'), ('seniority_code'), ('mapping_status'), ('mapping_confidence'), ('mapping_note'),
                     ('role_mapping_basis'), ('role_mapping_alias_id'), ('mapped_title_raw'), ('role_mapping_ownership_areas')) c(col)
  cross join (values ('INSERT'), ('UPDATE')) x(p)
  where pg_catalog.has_column_privilege(r.rolname, 'public.trace_candidate_employments', c.col, x.p);
  if v_bad is not null then
    raise exception 'mapping columns must be written only through trace_review functions: %', v_bad;
  end if;
end
$$;
