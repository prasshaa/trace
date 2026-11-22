-- One-off maintenance (Phase 4 closure, approved option A; D37). NOT a migration: it never runs automatically.
-- Removes the legacy per-plan reference snapshots written by the v2.23.0 plan writer, which v2.27.0 replaced with shared,
-- dated snapshots. Run as postgres, AFTER taking the backups described in docs/design/SNAPSHOT_CLEANUP_PROPOSAL.md:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -v expected=640 -f <this file>
-- Safety: one transaction; every precondition is checked first, and anything unexpected aborts with nothing changed.
--   * exactly :expected legacy rows (no fingerprint), all carrying the v2.23.0 writer's fixed note;
--   * none referenced by a plan run (the only foreign key);
--   * every legacy row's state (data mode, settings, both counts) has a shared row with identical values and the
--     identical fingerprint, so no aggregate count, setting or state is lost;
--   * the shared rows keep the EARLIEST first-observed date of their state;
--   * the delete names exactly those rows, and must remove exactly :expected rows.
-- Restoration (exact original rows, from the pre-cleanup backup): 20261117_legacy_reference_snapshot_restore.sql.
\set legacy_note 'transition plan run: counts and settings only'
begin;
create temporary table legacy_cleanup on commit drop as
select l.snapshot_id, l.data_mode, l.settings, l.eligible_profile_count, l.confirmed_transition_count, l.created_at,
       encode(sha256(convert_to(jsonb_build_object('data_mode', l.data_mode::text, 'settings', l.settings,
                                                   'eligible_profile_count', l.eligible_profile_count,
                                                   'confirmed_transition_count', l.confirmed_transition_count)::text, 'UTF8')), 'hex') as fp
  from trace_ref.reference_snapshots l
 where l.state_fingerprint is null and l.notes = :'legacy_note';
select set_config('cleanup.expected', :'expected', true);
do $$
declare
  v_expected integer := current_setting('cleanup.expected')::integer;
  v_n integer;
begin
  select count(*) into v_n from legacy_cleanup;
  if v_n <> v_expected then
    raise exception 'expected % legacy snapshots, found %; nothing changed', v_expected, v_n;
  end if;
  if exists (select 1 from trace_ref.reference_snapshots s where s.state_fingerprint is null
              and not exists (select 1 from legacy_cleanup c where c.snapshot_id = s.snapshot_id)) then
    raise exception 'unfingerprinted snapshots exist that are not legacy plan snapshots; nothing changed';
  end if;
  if exists (select 1 from legacy_cleanup c join public.trace_pathway_runs r on r.reference_snapshot_id = c.snapshot_id) then
    raise exception 'a legacy snapshot is referenced by a plan run; nothing changed';
  end if;
  if exists (select 1 from legacy_cleanup c where not exists (
               select 1 from trace_ref.reference_snapshots s
                where s.state_fingerprint = c.fp and s.data_mode = c.data_mode and s.settings = c.settings
                  and s.eligible_profile_count = c.eligible_profile_count and s.confirmed_transition_count = c.confirmed_transition_count)) then
    raise exception 'a legacy state has no identical shared snapshot; nothing changed';
  end if;
end
$$;
-- Keep the earliest first-observed date of each state (no-op when the shared row is already as early).
update trace_ref.reference_snapshots s
   set first_observed_on = e.earliest, created_at = (e.earliest::timestamp at time zone 'utc')
  from (select fp, min((created_at at time zone 'utc')::date) as earliest from legacy_cleanup group by fp) e
 where s.state_fingerprint = e.fp and e.earliest < s.first_observed_on;
with removed as (
  delete from trace_ref.reference_snapshots s
   using legacy_cleanup c
   where s.snapshot_id = c.snapshot_id and s.state_fingerprint is null and s.notes = :'legacy_note'
     and not exists (select 1 from public.trace_pathway_runs r where r.reference_snapshot_id = s.snapshot_id)
  returning 1)
select set_config('cleanup.removed', count(*)::text, true) from removed;
do $$
begin
  if current_setting('cleanup.removed')::integer <> current_setting('cleanup.expected')::integer then
    raise exception 'removed % rows, expected %; rolled back', current_setting('cleanup.removed'), current_setting('cleanup.expected');
  end if;
  raise notice 'removed % legacy reference snapshots', current_setting('cleanup.removed');
end
$$;
commit;
