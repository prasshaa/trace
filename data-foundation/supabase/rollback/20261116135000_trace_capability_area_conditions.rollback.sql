-- Compensation for migration 20261116135000_trace_capability_area_conditions (v2.19.0). NOT a migration.
-- Roll back 20261116140000 (pack pm-v1.1) first. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261116135000 --status reverted
-- Effect: drops area_conditions and restores the v2.16.0 code check. No other data is touched.
begin;
alter table public.trace_capability_requirements drop column area_conditions;
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
  return new;
end;
$$;
commit;
