-- Trace data foundation v2.7.0: candidate classification of evidence responsibility areas (M6).
--
-- A candidate classifies an evidence record they own (extracted or entered) with one or more responsibility
-- areas from public.trace_responsibility_areas, through one trusted path, trace_review.classify_evidence_areas.
-- The classification is the candidate's assertion (basis 'candidate_selected'): it is not inferred, verified
-- or independently confirmed.
--
-- Rules:
--   * Classification never changes review_status, verification_status, information_origin, provenance,
--     source references, evidence_text, evidence_type, links, the candidate-content responsibility_area
--     column, or any employment. It never makes evidence eligible: rejected evidence is refused (TR001;
--     reinstate it through the review workflow first), and pending evidence stays pending.
--   * area_classified_text keeps the exact evidence text that was classified. When evidence_text later changes
--     (a draft edit or an M4 correction), area_classification_is_stale becomes true; a later consumer must not
--     use a stale classification until the candidate classifies the new text.
--   * Areas must exist in trace_responsibility_areas (TR006); duplicates are removed and the list is stored
--     sorted, so the same request always stores the same value. A classification replaces the previous one.
--   * The caller's expected text must equal the record's evidence_text (TR018), so a classification is never
--     applied to a text the caller did not see.
--   * classify_evidence_areas is owned by trace_review_writer, SECURITY DEFINER, empty search_path, fixed SQL,
--     executable by authenticated only, acts for auth.uid() only and locks the evidence row. Only the writer can
--     write the classification columns: authenticated, anon, service_role and the ingest writer cannot.
--     The table owner keeps administrative access (within the CHECK constraints below).

-- ------------------------------------------------------------------ columns and constraints
alter table public.trace_candidate_evidence
  add column area_classification text[],
  add column area_classification_basis text check (area_classification_basis = 'candidate_selected'),
  add column area_classified_text text,
  add column area_classification_is_stale boolean generated always as
    (area_classified_text is not null and area_classified_text is distinct from evidence_text) stored;

alter table public.trace_candidate_evidence
  add constraint candidate_evidence_area_classification_complete check (
    (area_classification is null) = (area_classification_basis is null)
    and (area_classification is null) = (area_classified_text is null)),
  add constraint candidate_evidence_area_classification_nonempty check (
    area_classification is null or (cardinality(area_classification) >= 1 and array_position(area_classification, null) is null));

-- ------------------------------------------------------------------ classify_evidence_areas
create function trace_review.classify_evidence_areas(p_candidate_profile_id uuid, p_evidence_id uuid, p_areas text[],
                                                     p_expected_text text)
returns text[]
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  e record;
  v_areas text[];
begin
  if v_uid is null then
    raise exception using errcode = 'TR004', message = 'no request identity';
  end if;
  select t.evidence_text, t.review_status
    into e
    from public.trace_candidate_evidence t
   where t.candidate_evidence_id = p_evidence_id and t.candidate_profile_id = p_candidate_profile_id
     and t.owner_user_id = v_uid
     for update;
  if not found then
    raise exception using errcode = 'TR005', message = format('evidence %s not found', p_evidence_id);
  end if;
  if e.review_status = 'rejected' then
    raise exception using errcode = 'TR001', message = 'rejected evidence cannot be classified; reinstate it first';
  end if;
  if p_expected_text is null or e.evidence_text is distinct from p_expected_text then
    raise exception using errcode = 'TR018', message = 'the evidence text changed since the classification was proposed';
  end if;
  if p_areas is null or cardinality(p_areas) = 0 then
    raise exception using errcode = 'TR006', message = 'a classification names at least one responsibility area';
  end if;
  if exists (select 1 from unnest(p_areas) k
              where k is null or not exists (select 1 from public.trace_responsibility_areas ra where ra.responsibility_area = k)) then
    raise exception using errcode = 'TR006', message = 'every area must be a responsibility area of the taxonomy';
  end if;
  select array_agg(distinct k order by k) into v_areas from unnest(p_areas) k;

  update public.trace_candidate_evidence t
     set area_classification = v_areas, area_classification_basis = 'candidate_selected', area_classified_text = e.evidence_text
   where t.candidate_evidence_id = p_evidence_id and t.candidate_profile_id = p_candidate_profile_id
     and t.owner_user_id = v_uid;
  return v_areas;
end
$$;

-- ------------------------------------------------------------------ privileges
grant update (area_classification, area_classification_basis, area_classified_text)
  on public.trace_candidate_evidence to trace_review_writer;
revoke all on function trace_review.classify_evidence_areas(uuid, uuid, text[], text) from public, anon, authenticated, service_role;
grant execute on function trace_review.classify_evidence_areas(uuid, uuid, text[], text) to authenticated;

-- ------------------------------------------------------------------ ownership transfer (the M2 procedure)
grant trace_review_writer to postgres with set true, inherit false;
grant create on schema trace_review to trace_review_writer;
alter function trace_review.classify_evidence_areas(uuid, uuid, text[], text) owner to trace_review_writer;
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
                                                                          'apply_alias_mapping', 'choose_employment_role',
                                                                          'classify_evidence_areas')
                                                       then 'trace_review_writer' else 'postgres' end;
  if v_bad is not null then
    raise exception 'unexpected trace_review function owner: %', v_bad;
  end if;
  select string_agg(r.rolname || ':' || c.col || ':' || x.p, ', ') into v_bad
  from (values ('authenticated'), ('service_role'), ('anon'), ('trace_ingest_writer')) r(rolname)
  cross join (values ('area_classification'), ('area_classification_basis'), ('area_classified_text')) c(col)
  cross join (values ('INSERT'), ('UPDATE')) x(p)
  where pg_catalog.has_column_privilege(r.rolname, 'public.trace_candidate_evidence', c.col, x.p);
  if v_bad is not null then
    raise exception 'classification columns must be written only through trace_review.classify_evidence_areas: %', v_bad;
  end if;
end
$$;
