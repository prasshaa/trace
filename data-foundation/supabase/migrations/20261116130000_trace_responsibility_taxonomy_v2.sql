-- Trace data foundation v2.18.0: responsibility taxonomy v2, scope qualifiers and versioned classifications (Phase 3.1).
--
-- Taxonomy (public.trace_responsibility_areas):
--   * Four new areas (introduced in taxonomy version 2): ai_product_work, data_governance_quality,
--     business_case_economics, executive_communication.
--   * Every area gets plain-language examples.
--   * Scope QUALIFIERS: an explicit yes/no question the user answers when choosing certain areas, recording that the
--     evidence shows a specific scope. These are not new categories:
--       strategy_portfolio       multi_product_scope  several products or initiatives, or a complex or high-risk product (L1)
--       people_leadership        coaching_mentoring   coaching, mentoring or leading product practitioners (L2)
--       delivery_execution       cross_team           planning across teams: dependencies, coordination, sequencing (L4)
--       business_case_economics  tracks_outcomes      defining, measuring, monitoring or tracking outcomes (C4 transfer)
--   * Versions: introduced_in_version and definition_version per area; each qualifier records its own version. The
--     current taxonomy version is the highest of them (trace_util.current_taxonomy_version()).
--
-- Classification (public.trace_candidate_evidence), still written only by the review writer's trusted functions:
--   * area_classification_none: the user's explicit "None of these areas". It is stored as an empty area list with the
--     classified text, so content-edit stale detection keeps working.
--   * area_qualifiers: the user's yes/no answers, by area.
--   * area_classification_taxonomy_version and area_classified_at: the taxonomy version the user reviewed against, and
--     when. Classifications made before v2.18.0 have no version and read as version 1; they are never rewritten.
--   * trace_review.classify_evidence_v2: areas (scope answers optional; validated when given) or none.
--   * trace_review.clear_evidence_classification: back to unclassified.
-- The v2.7.0 function classify_evidence_areas is unchanged; its classifications record no version (read as 1).
-- No existing classification is rewritten, and no Gap is created by this migration (the assessment requires
-- classifications at or above the relevant version).

-- ------------------------------------------------------------------ taxonomy
alter table public.trace_responsibility_areas
  add column examples              text[] not null default '{}',
  add column introduced_in_version integer not null default 1 check (introduced_in_version >= 1),
  add column definition_version    integer not null default 1 check (definition_version >= 1),
  -- {"<qualifier>": {"question": text, "version": int}}
  add column qualifiers            jsonb not null default '{}'::jsonb check (jsonb_typeof(qualifiers) = 'object');

insert into public.trace_responsibility_areas (responsibility_area, label, is_pm_ownership_signal, description, examples,
                                               introduced_in_version, definition_version, qualifiers)
values
  ('ai_product_work', 'AI and machine-learning product work', false,
   'Specifies, builds, evaluates or launches features powered by machine-learning models or large language models.',
   array['Defined the evaluation set and pass criteria for a support chatbot',
         'Chose between two models based on cost, latency and accuracy',
         'Wrote prompt and guardrail requirements for an AI agent'], 2, 2, '{}'),
  ('data_governance_quality', 'Data quality and governance', false,
   'Defines, certifies or controls data: shared definitions, quality checks, lineage, access and consent controls.',
   array['Defined the single "active customer" definition used across reports',
         'Set up data-quality checks and an access-request process',
         'Owned consent flags in a customer data pipeline'], 2, 2, '{}'),
  ('business_case_economics', 'Business cases and economics', false,
   'Builds business cases, pricing or cost models, or is accountable for a budget, revenue or unit economics.',
   array['Built the business case that funded a new pricing tier',
         'Modelled and reduced cost per transaction',
         'Owned an annual budget for a product area'], 2, 2,
   '{"tracks_outcomes": {"question": "Did this include defining, measuring, monitoring or tracking outcomes (for example adoption, revenue or cost targets)?", "version": 2}}'),
  ('executive_communication', 'Communicating with senior leadership', false,
   'Presents strategy, progress, risks or decisions to senior leaders, or writes decision papers for them.',
   array['Presented the quarterly roadmap to the CTO and CFO',
         'Wrote the decision memo for the leadership committee',
         'Ran the monthly business review with directors'], 2, 2, '{}');

update public.trace_responsibility_areas set examples = v.ex
  from (values
    ('customer_account_management', array['Managed renewals and escalations for enterprise accounts', 'Resolved recurring customer issues with the support team']),
    ('data_analysis', array['Built a funnel dashboard for onboarding', 'Analysed an A/B test and recommended a variant']),
    ('delivery_execution', array['Planned and coordinated a release with engineering and QA', 'Managed launch dependencies and dates']),
    ('design_research', array['Ran usability tests on a new checkout flow', 'Designed wireframes for the settings page']),
    ('go_to_market_growth', array['Planned the launch campaign with marketing', 'Ran acquisition experiments for a new segment']),
    ('operations_process', array['Redesigned the refund process to cut handling time', 'Ran day-to-day operations for a service desk']),
    ('outcome_ownership', array['Accountable for the activation rate of a product area', 'Owned a revenue or retention target']),
    ('people_leadership', array['Managed a team of five analysts', 'Mentored two junior colleagues']),
    ('prioritisation_backlog', array['Prioritised the integrations backlog each sprint', 'Decided what went into each release']),
    ('product_discovery', array['Interviewed customers to validate a redesign', 'Ran problem discovery before committing to a feature']),
    ('quality_testing', array['Wrote and ran regression tests', 'Triaged and tracked defects']),
    ('requirements_analysis', array['Gathered requirements from merchants', 'Wrote specifications and process maps']),
    ('roadmap_ownership', array['Set the six-month roadmap for a product area', 'Owned the product strategy for a feature set']),
    ('stakeholder_management', array['Aligned operations, finance and engineering on a change', 'Ran weekly stakeholder reviews']),
    ('strategy_portfolio', array['Set strategy across three related products', 'Decided investment across a portfolio of initiatives']),
    ('technical_engineering', array['Built and maintained payment APIs', 'Designed a data pipeline'])
  ) as v(code, ex)
 where responsibility_area = v.code;

-- Qualifiers on existing areas: the areas' definitions are unchanged (definition_version stays 1); each qualifier
-- records its own version, so only requirements that need the qualifier wait for re-review.
update public.trace_responsibility_areas set qualifiers = q.j::jsonb
  from (values
    ('strategy_portfolio', '{"multi_product_scope": {"question": "Did this cover several products or initiatives, or a complex or high-risk product?", "version": 2}}'),
    ('people_leadership', '{"coaching_mentoring": {"question": "Did this include coaching, mentoring or leading other product practitioners (not only managing people)?", "version": 2}}'),
    ('delivery_execution', '{"cross_team": {"question": "Did this involve planning across several teams: dependencies, coordination and sequencing?", "version": 2}}')
  ) as q(code, j)
 where responsibility_area = q.code;

create function trace_util.current_taxonomy_version()
returns integer
language sql
stable
set search_path = ''
as $$
  select coalesce(max(greatest(a.introduced_in_version, a.definition_version,
                               coalesce((select max((v.value ->> 'version')::integer) from jsonb_each(a.qualifiers) v), 1))), 1)
    from public.trace_responsibility_areas a
$$;

-- ------------------------------------------------------------------ versioned classifications
alter table public.trace_candidate_evidence
  add column area_classification_none            boolean not null default false,
  add column area_classification_taxonomy_version integer check (area_classification_taxonomy_version >= 1),
  add column area_qualifiers                      jsonb not null default '{}'::jsonb check (jsonb_typeof(area_qualifiers) = 'object'),
  add column area_classified_at                   timestamptz;

-- "None of these areas" is an empty area list (so the classified text and stale detection apply); a list of areas is
-- non-empty as before. Replaces the v2.7.0 non-empty rule with the same rule plus that one explicit case.
alter table public.trace_candidate_evidence
  drop constraint candidate_evidence_area_classification_nonempty,
  add constraint candidate_evidence_area_classification_nonempty check (
    area_classification is null
    or (array_position(area_classification, null) is null
        and (cardinality(area_classification) = 0) = area_classification_none)),
  add constraint candidate_evidence_area_none_is_classified check (not area_classification_none or area_classification is not null),
  add constraint candidate_evidence_area_none_is_versioned check (not area_classification_none or area_classification_taxonomy_version is not null),
  add constraint candidate_evidence_area_qualifiers_need_areas check (area_qualifiers = '{}'::jsonb or area_classification is not null);

create function trace_review.classify_evidence_v2(p_candidate_profile_id uuid, p_evidence_id uuid, p_areas text[], p_none boolean,
                                                  p_qualifiers jsonb, p_expected_text text)
returns text[]
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  e record;
  v_areas text[];
  v_q jsonb := coalesce(p_qualifiers, '{}'::jsonb);
  v_bad text;
begin
  if v_uid is null then
    raise exception using errcode = 'TR004', message = 'no request identity';
  end if;
  select t.evidence_text, t.review_status into e
    from public.trace_candidate_evidence t
   where t.candidate_evidence_id = p_evidence_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
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
  if jsonb_typeof(v_q) <> 'object' then
    raise exception using errcode = 'TR006', message = 'qualifiers must be an object';
  end if;
  if coalesce(p_none, false) then
    if coalesce(cardinality(p_areas), 0) > 0 or v_q <> '{}'::jsonb then
      raise exception using errcode = 'TR006', message = '"None of these areas" cannot be combined with areas';
    end if;
    v_areas := '{}';
  else
    if p_areas is null or cardinality(p_areas) = 0 then
      raise exception using errcode = 'TR006', message = 'choose at least one responsibility area, or "None of these areas"';
    end if;
    if exists (select 1 from unnest(p_areas) k
                where k is null or not exists (select 1 from public.trace_responsibility_areas ra where ra.responsibility_area = k)) then
      raise exception using errcode = 'TR006', message = 'every area must be a responsibility area of the taxonomy';
    end if;
    select array_agg(distinct k order by k) into v_areas from unnest(p_areas) k;
    -- Answers only for chosen areas and their defined qualifiers, booleans only.
    select string_agg(a.key || '.' || coalesce(q.key, ''), ', ') into v_bad
      from jsonb_each(v_q) a left join lateral jsonb_each(case when jsonb_typeof(a.value) = 'object' then a.value else '{}' end) q on true
     where not (a.key = any (v_areas)) or jsonb_typeof(a.value) <> 'object'
        or (q.key is not null and (jsonb_typeof(q.value) <> 'boolean' or not exists (
              select 1 from public.trace_responsibility_areas ra where ra.responsibility_area = a.key and ra.qualifiers ? q.key)));
    if v_bad is not null then
      raise exception using errcode = 'TR006', message = format('unexpected qualifier answers: %s', v_bad);
    end if;
    -- Scope questions may be left unanswered: an unanswered question is stored as absent and is never read as "no"
    -- (the assessment treats it as unknown for any requirement that depends on it).
  end if;

  update public.trace_candidate_evidence t
     set area_classification = v_areas, area_classification_basis = 'candidate_selected', area_classified_text = e.evidence_text,
         area_classification_none = coalesce(p_none, false), area_qualifiers = case when coalesce(p_none, false) then '{}'::jsonb else v_q end,
         area_classification_taxonomy_version = trace_util.current_taxonomy_version(), area_classified_at = now()
   where t.candidate_evidence_id = p_evidence_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid;
  return v_areas;
end
$$;

create function trace_review.clear_evidence_classification(p_candidate_profile_id uuid, p_evidence_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
begin
  if v_uid is null then
    raise exception using errcode = 'TR004', message = 'no request identity';
  end if;
  perform 1 from public.trace_candidate_evidence t
   where t.candidate_evidence_id = p_evidence_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid
     for update;
  if not found then
    raise exception using errcode = 'TR005', message = format('evidence %s not found', p_evidence_id);
  end if;
  update public.trace_candidate_evidence t
     set area_classification = null, area_classification_basis = null, area_classified_text = null, area_classification_none = false,
         area_qualifiers = '{}'::jsonb, area_classification_taxonomy_version = null, area_classified_at = null
   where t.candidate_evidence_id = p_evidence_id and t.candidate_profile_id = p_candidate_profile_id and t.owner_user_id = v_uid;
end
$$;

-- ------------------------------------------------------------------ privileges
grant update (area_classification_none, area_classification_taxonomy_version, area_qualifiers, area_classified_at)
  on public.trace_candidate_evidence to trace_review_writer;
revoke all on function trace_review.classify_evidence_v2(uuid, uuid, text[], boolean, jsonb, text),
  trace_review.clear_evidence_classification(uuid, uuid) from public, anon, authenticated, service_role;
grant execute on function trace_review.classify_evidence_v2(uuid, uuid, text[], boolean, jsonb, text),
  trace_review.clear_evidence_classification(uuid, uuid) to trace_app_user;
revoke all on function trace_util.current_taxonomy_version() from public, anon, service_role;
grant execute on function trace_util.current_taxonomy_version() to authenticated, trace_review_writer;

-- ------------------------------------------------------------------ ownership transfer (the M2 procedure)
grant trace_review_writer to postgres with set true, inherit false;
grant create on schema trace_review to trace_review_writer;
alter function trace_review.classify_evidence_v2(uuid, uuid, text[], boolean, jsonb, text) owner to trace_review_writer;
alter function trace_review.clear_evidence_classification(uuid, uuid) owner to trace_review_writer;
revoke create on schema trace_review from trace_review_writer;
revoke trace_review_writer from postgres;

do $$
declare
  v_bad text;
begin
  if pg_catalog.pg_has_role('postgres', 'trace_review_writer', 'SET') or pg_catalog.pg_has_role('postgres', 'trace_review_writer', 'USAGE') then
    raise exception 'postgres must not be able to act as trace_review_writer';
  end if;
  if pg_catalog.has_schema_privilege('trace_review_writer', 'trace_review', 'CREATE') then
    raise exception 'trace_review_writer must not keep CREATE on trace_review';
  end if;
  select string_agg(r.rolname || ':' || c.col || ':' || x.p, ', ') into v_bad
    from (values ('authenticated'), ('service_role'), ('anon'), ('trace_ingest_writer'), ('trace_app_user')) r(rolname)
   cross join (values ('area_classification_none'), ('area_classification_taxonomy_version'), ('area_qualifiers'), ('area_classified_at')) c(col)
   cross join (values ('INSERT'), ('UPDATE')) x(p)
   where pg_catalog.has_column_privilege(r.rolname, 'public.trace_candidate_evidence', c.col, x.p);
  if v_bad is not null then
    raise exception 'classification columns must be written only through the trusted functions: %', v_bad;
  end if;
end
$$;
