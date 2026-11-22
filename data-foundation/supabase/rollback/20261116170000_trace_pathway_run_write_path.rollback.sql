-- Compensation for migration 20261116170000_trace_pathway_run_write_path (v2.23.0). NOT a migration.
-- Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261116170000 --status reverted
-- Effect: removes the plan writer, its function, the actions table, the immutability guards and the additive run and
-- route columns. It REFUSES to run while any transition-plan run exists: user records are never deleted by a rollback.
-- Reference snapshots written by plan runs are kept only if a run still references them (none, given the refusal).
begin;
do $$
begin
  if exists (select 1 from public.trace_pathway_runs where plan_kind is not null) then
    raise exception 'transition-plan runs exist; export or let owners delete them before rolling back v2.23.0';
  end if;
end
$$;
delete from trace_ref.reference_snapshots s
 where s.notes = 'transition plan run: counts and settings only'
   and not exists (select 1 from public.trace_pathway_runs r where r.reference_snapshot_id = s.snapshot_id);

drop function trace_plan.record_transition_plan(jsonb);
drop schema trace_plan;

drop trigger guard_pathway_child on public.trace_pathway_recommendations;
drop trigger guard_pathway_child on public.trace_pathway_route_steps;
drop trigger guard_pathway_child on public.trace_pathway_evidence;
drop trigger guard_pathway_run_immutable on public.trace_pathway_runs;
drop table public.trace_pathway_actions;
drop function trace_util.guard_pathway_child();
drop function trace_util.guard_pathway_run_immutable();

drop policy "candidate profiles plan writer select" on public.trace_candidate_profiles;
drop policy "reference snapshots plan writer insert" on trace_ref.reference_snapshots;
drop policy "pathway runs plan writer insert" on public.trace_pathway_runs;
drop policy "pathway runs plan writer select" on public.trace_pathway_runs;
drop policy "pathway runs plan writer update" on public.trace_pathway_runs;
drop policy "pathway recommendations plan writer insert" on public.trace_pathway_recommendations;
drop policy "pathway route steps plan writer insert" on public.trace_pathway_route_steps;
drop policy "pathway evidence plan writer insert" on public.trace_pathway_evidence;

alter table public.trace_pathway_recommendations
  drop constraint recommendations_plan_fields_together, drop constraint recommendations_plan_no_scores,
  drop constraint recommendations_profile_led_no_supporters, drop constraint recommendations_basis_kind,
  drop constraint recommendations_bridge_role,
  drop column route_basis, drop column route_kind, drop column primary_status, drop column bridge_role_id, drop column explanation,
  drop column feasibility, drop column tradeoffs, drop column evidence_breadth_label;
alter table public.trace_pathway_runs
  drop constraint pathway_runs_plan_audit_complete,
  drop column plan_kind, drop column engine_version, drop column profile_version, drop column knowledge_pack,
  drop column source_inventory, drop column taxonomy_version, drop column bridge_catalogue_version, drop column action_template_version,
  drop column eligibility_rules_version, drop column assessment, drop column eligibility, drop column source_versions,
  drop column privacy_state, drop column decision, drop column completed_at;

revoke all on public.trace_candidate_profiles, public.trace_pathway_runs, public.trace_pathway_recommendations,
  public.trace_pathway_route_steps, public.trace_pathway_evidence from trace_plan_writer;
revoke all on trace_ref.reference_snapshots from trace_plan_writer;
revoke execute on function trace_review.request_uid() from trace_plan_writer;
revoke usage on schema trace_review from trace_plan_writer;
revoke trace_reference_reader from trace_plan_writer;
drop role trace_plan_writer;
commit;
