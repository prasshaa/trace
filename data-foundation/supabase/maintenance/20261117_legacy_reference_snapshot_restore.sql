-- Restoration for 20261117_legacy_reference_snapshot_cleanup.sql. NOT a migration.
-- It reloads the EXACT original rows from the backup taken immediately before the cleanup
-- (a CSV of exactly those rows with its SHA-256; where it is kept is recorded in docs/design/SNAPSHOT_CLEANUP_PROPOSAL.md,
-- outside the shareable package). It never fabricates rows: without that backup file there is nothing to restore, and
-- the script fails. Copy the CSV into the local container as /tmp/legacy_snapshots.csv, then:
--   psql -U postgres -v ON_ERROR_STOP=1 -v expected=640 -f <this file>
-- (psql does not expand variables inside \copy, so the file path is fixed: /tmp/legacy_snapshots.csv in the container.)
-- It refuses if any row to restore already exists, or if the file does not hold exactly :expected rows.
begin;
create temporary table snapshot_restore (
  snapshot_id uuid, data_mode public.trace_data_mode, settings jsonb, eligible_profile_count integer,
  confirmed_transition_count integer, notes text, created_at timestamptz) on commit drop;
\copy snapshot_restore from '/tmp/legacy_snapshots.csv' with (format csv, header true)
select set_config('restore.expected', :'expected', true);
do $$
begin
  if (select count(*) from snapshot_restore) <> current_setting('restore.expected')::integer then
    raise exception 'the backup holds % rows, expected %', (select count(*) from snapshot_restore), current_setting('restore.expected');
  end if;
  if exists (select 1 from snapshot_restore r join trace_ref.reference_snapshots s using (snapshot_id)) then
    raise exception 'some rows already exist; nothing restored';
  end if;
end
$$;
insert into trace_ref.reference_snapshots (snapshot_id, data_mode, settings, eligible_profile_count, confirmed_transition_count, notes, created_at)
select snapshot_id, data_mode, settings, eligible_profile_count, confirmed_transition_count, notes, created_at from snapshot_restore;
commit;
