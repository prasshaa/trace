-- v2.32.0 (Phase 5, D50/D51): common job-title aliases, and where the user wants to make their move.
--
-- 1. Role aliases. Automatic mapping stays an EXACT match of the normalised title with an alias (M5). These rows add
--    common wordings of roles the taxonomy already has, found missing in review: QA titles with a "software" or
--    "quality assurance" wording, founder titles with a second capacity, and "global product manager". None is
--    context-required, and none carries a seniority hint. Nothing existing changes; no mapping is rewritten (a job
--    already mapped, or decided by its owner, keeps its mapping: the mapping functions never replace one).
-- 2. Move preferences. For one target role, the user says where they'd like to make the move: specific companies
--    (kept, as before, in trace_private.target_company_preferences), kinds of company, or open to options. A planning
--    preference only: never evidence about an employer, never a ranking, never a forecast. Same pattern as v2.29.0 and
--    v2.30.0: private table, no client grant; the owner role reads and sets only its own row through two functions
--    acting for the request identity, owned by trace_preference_writer under owner-scoped policies.
-- Requires PostgreSQL 16+ and v2.30.0.

insert into public.trace_role_aliases (alias_id, alias, role_id, seniority_hint_code, context_required, default_confidence, mapping_rule) values
  ('ALS-0107', 'Software QA Engineer', 'ROLE-QA', null, false, 0.9, null),
  ('ALS-0108', 'Software Test Engineer', 'ROLE-QA', null, false, 0.9, null),
  ('ALS-0109', 'Quality Assurance Engineer', 'ROLE-QA', null, false, 0.9, null),
  ('ALS-0110', 'QA Analyst', 'ROLE-QA', null, false, 0.85, null),
  ('ALS-0111', 'Automation Test Engineer', 'ROLE-QA', null, false, 0.9, null),
  ('ALS-0112', 'Cofounder', 'ROLE-ENT', null, false, 0.95, null),
  ('ALS-0113', 'Co founder', 'ROLE-ENT', null, false, 0.95, null),
  ('ALS-0114', 'Founder & CEO', 'ROLE-ENT', null, false, 0.9, null),
  ('ALS-0115', 'Co-founder & CEO', 'ROLE-ENT', null, false, 0.9, null),
  ('ALS-0116', 'Global Product Manager', 'PM-PM', null, false, 0.85, null);

create table trace_private.target_move_preferences (
  user_id          uuid not null references auth.users (id) on delete cascade,
  target_role_id   text not null references public.trace_role_taxonomy (role_id),
  scope            text not null check (scope in ('specific_companies', 'company_types', 'open')),
  company_types    text[] not null default '{}'::text[],
  updated_at       timestamptz not null default now(),
  primary key (user_id, target_role_id),
  constraint target_move_preferences_types check (
    company_types <@ array['ai_native', 'early_stage_startup', 'scale_up', 'large_tech', 'b2b_saas', 'consumer_marketplace',
                           'platform_data', 'fintech_regulated', 'services_consulting']::text[]
    and cardinality(company_types) <= 3
    and (scope = 'company_types') = (cardinality(company_types) > 0))
);
comment on table trace_private.target_move_preferences is
  'Where the user wants to move into a target role (D51): specific companies (names in target_company_preferences), kinds of company, or open. A planning preference; never employer evidence.';
alter table trace_private.target_move_preferences enable row level security;
revoke all on trace_private.target_move_preferences from public, anon, authenticated, service_role;

grant select, insert, update, delete on trace_private.target_move_preferences to trace_preference_writer;
create policy target_move_preferences_writer_select on trace_private.target_move_preferences
  for select to trace_preference_writer using (user_id = (select trace_review.request_uid()));
create policy target_move_preferences_writer_insert on trace_private.target_move_preferences
  for insert to trace_preference_writer with check (user_id = (select trace_review.request_uid()));
create policy target_move_preferences_writer_update on trace_private.target_move_preferences
  for update to trace_preference_writer using (user_id = (select trace_review.request_uid()))
  with check (user_id = (select trace_review.request_uid()));
create policy target_move_preferences_writer_delete on trace_private.target_move_preferences
  for delete to trace_preference_writer using (user_id = (select trace_review.request_uid()));

create function trace_plan.my_move_preference(p_target_role_id text)
returns table (scope text, company_types text[], updated_at timestamptz)
language sql
stable
security definer
set search_path = ''
as $$
  select m.scope, m.company_types, m.updated_at
    from trace_private.target_move_preferences m
   where m.user_id = trace_review.request_uid() and m.target_role_id = p_target_role_id
$$;

-- Set (or replace) the caller's preference for one target. Kinds of company apply only to scope 'company_types'.
create function trace_plan.set_move_preference(p_target_role_id text, p_scope text, p_company_types text[])
returns table (scope text, company_types text[], updated_at timestamptz)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  v_types text[] := coalesce((select array_agg(distinct t order by t) from unnest(coalesce(p_company_types, '{}'::text[])) t), '{}'::text[]);
begin
  if v_uid is null then
    raise exception 'no request identity' using errcode = '42501';
  end if;
  if p_scope is distinct from 'company_types' then
    v_types := '{}'::text[];
  end if;
  begin
    insert into trace_private.target_move_preferences as m (user_id, target_role_id, scope, company_types)
    values (v_uid, p_target_role_id, p_scope, v_types)
    on conflict (user_id, target_role_id) do update
       set scope = excluded.scope, company_types = excluded.company_types, updated_at = now();
  exception
    when foreign_key_violation then raise exception 'unknown target role' using errcode = '22023';
    when check_violation or not_null_violation then raise exception 'invalid move preference' using errcode = '22023';
  end;
  return query select * from trace_plan.my_move_preference(p_target_role_id);
end;
$$;

revoke all on function trace_plan.my_move_preference(text), trace_plan.set_move_preference(text, text, text[])
  from public, anon, authenticated, service_role;
grant execute on function trace_plan.my_move_preference(text), trace_plan.set_move_preference(text, text, text[])
  to trace_app_user;
grant trace_preference_writer to postgres with set true, inherit false;
grant create on schema trace_plan to trace_preference_writer;
alter function trace_plan.my_move_preference(text) owner to trace_preference_writer;
alter function trace_plan.set_move_preference(text, text, text[]) owner to trace_preference_writer;
revoke create on schema trace_plan from trace_preference_writer;
revoke trace_preference_writer from postgres;

do $$
begin
  if pg_catalog.pg_has_role('postgres', 'trace_preference_writer', 'SET') or pg_catalog.pg_has_role('postgres', 'trace_preference_writer', 'USAGE') then
    raise exception 'postgres must not be able to act as trace_preference_writer';
  end if;
  if has_function_privilege('anon', 'trace_plan.set_move_preference(text, text, text[])', 'execute')
     or has_function_privilege('authenticated', 'trace_plan.set_move_preference(text, text, text[])', 'execute')
     or has_function_privilege('service_role', 'trace_plan.set_move_preference(text, text, text[])', 'execute')
     or not has_function_privilege('trace_app_user', 'trace_plan.set_move_preference(text, text, text[])', 'execute')
     or not has_function_privilege('trace_app_user', 'trace_plan.my_move_preference(text)', 'execute') then
    raise exception 'the move-preference functions must be executable by trace_app_user only';
  end if;
  if (select count(*) from public.trace_role_aliases where alias_id between 'ALS-0107' and 'ALS-0116') <> 10 then
    raise exception 'the v2.32.0 aliases were not all added';
  end if;
end
$$;
