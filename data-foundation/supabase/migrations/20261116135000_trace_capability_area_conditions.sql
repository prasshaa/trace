-- Trace data foundation v2.19.0: area conditions on canonical requirements (Phase 3.1, pack pm-v1.1).
--
-- trace_capability_requirements.area_conditions {"<area>": "<qualifier>"}: for this requirement, evidence classified
-- with <area> counts only when the user answered YES to that area's <qualifier>. For example, L2 coaching needs
-- people_leadership with coaching_mentoring, so generic people management alone is not enough. A condition names an area
-- of the requirement's own mapping and a qualifier that area defines (checked; TR025).

alter table public.trace_capability_requirements
  add column area_conditions jsonb not null default '{}'::jsonb check (jsonb_typeof(area_conditions) = 'object');

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
