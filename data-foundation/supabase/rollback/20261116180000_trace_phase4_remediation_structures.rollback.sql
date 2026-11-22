-- Compensation for migration 20261116180000_trace_phase4_remediation_structures (v2.24.0). NOT a migration.
-- Roll back 20261116210000, 20261116200000 and 20261116190000 first. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261116180000 --status reverted
-- Effect: removes the v2.24.0 tables and columns and restores the earlier checks. It REFUSES to run while any evidence
-- holds an answer to the D35 question (the answers would refer to a question that no longer exists).
begin;
do $$
begin
  if exists (select 1 from public.trace_candidate_evidence where area_qualifiers -> 'data_analysis' ? 'outcome_metrics_influence') then
    raise exception 'answers to the outcome-metrics question exist; not rolled back';
  end if;
end
$$;
drop table public.trace_employer_criterion_rules;
drop table public.trace_education_fields;
drop table public.trace_digital_role_candidates;
drop function trace_util.check_employer_criterion_rule();
drop function trace_util.check_criterion_codes(jsonb);
alter table public.trace_pathway_actions drop column labels;
alter table public.trace_action_templates drop constraint action_templates_labels, drop constraint action_templates_where, drop column labels;
alter table public.trace_action_templates add constraint action_templates_where check (cardinality(where_options) >= 1 and where_options <@
  array['current_job', 'internal_move', 'external_project', 'self_directed_study', 'job_market']::text[]);
alter table public.trace_bridge_roles drop column entry_evidence, drop column progression_evidence, drop column may_be_primary,
  drop column partial_development;
create or replace function trace_util.check_bridge_role()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_bad text;
begin
  select string_agg(k, ', ') into v_bad
    from unnest(new.develops || new.partly_develops || new.entry_signal_requirements) k
   where not exists (select 1 from public.trace_capability_requirements r
                      where r.knowledge_pack = new.requirements_pack and r.requirement_key = k);
  if v_bad is not null then
    raise exception using errcode = 'TR026', message = format('unknown requirements in %s: %s', new.requirements_pack, v_bad);
  end if;
  select string_agg(f, ', ') into v_bad
    from unnest(new.entry_signal_families) f
   where not exists (select 1 from public.trace_role_families r where r.role_family = f);
  if v_bad is not null then
    raise exception using errcode = 'TR026', message = format('unknown role families: %s', v_bad);
  end if;
  select string_agg(k, ', ') into v_bad
    from unnest(new.source_keys) k
   where not exists (select 1 from public.trace_target_role_sources s where s.source_key = k);
  if v_bad is not null then
    raise exception using errcode = 'TR026', message = format('unknown sources: %s', v_bad);
  end if;
  if exists (select 1 from public.trace_role_taxonomy t where t.role_id = new.bridge_role_id and t.role_family = 'project_program_management') then
    raise exception using errcode = 'TR026', message = 'program and project management are not bridge roles (D27)';
  end if;
  return new;
end;
$$;

alter table public.trace_capability_requirements drop column decision_rights, drop column area_condition_fallback;
create or replace function trace_util.check_capability_codes()
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
  select string_agg(c.key || '=' || (c.value #>> '{}'), ', ') into v_bad
    from jsonb_each(new.area_conditions) c
   where jsonb_typeof(c.value) <> 'string'
      or not (c.key = any (new.direct_areas || new.transferable_areas))
      or not exists (select 1 from public.trace_responsibility_areas a where a.responsibility_area = c.key and a.qualifiers ? (c.value #>> '{}'));
  if v_bad is not null then
    raise exception using errcode = 'TR025', message = format('area conditions must name a mapped area and one of its qualifiers: %s', v_bad);
  end if;
  return new;
end;
$$;

update public.trace_responsibility_areas set qualifiers = qualifiers - 'outcome_metrics_influence' where responsibility_area = 'data_analysis';
commit;
