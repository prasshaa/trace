-- Compensation for migration 20261111180000_trace_candidate_role_mapping (v2.6.0, M5 role mapping).
--
-- NOT a migration: it lives outside supabase/migrations and is never applied automatically.
--
--   1. Deploy the backend without role mapping (M4, commit eae2f05) first, or stop calling
--      map_employment / choose_role. Mappings in flight when this script starts are waited for and then
--      counted by the acknowledgement check; later calls wait for the script and then fail closed (the
--      functions no longer exist).
--   2. Run this script as postgres. It carries its own BEGIN and COMMIT: any error rolls all of it back and
--      leaves the v2.6.0 catalog intact. Do NOT add psql --single-transaction. Use:
--        psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>
--   3. Mark the migration as reverted:
--        supabase migration repair 20261111180000 --status reverted   (add --local for the local stack)
--
-- What it restores: the exact v2.5.0 catalog. It drops trace_review.apply_alias_mapping and
-- choose_employment_role, trace_util.normalize_title and is_product_owner_title with the alias-table
-- normalisation CHECK, the writer's mapping grants and lookup read policies, the mapping provenance
-- columns and their constraints, and makes mapping_confidence NOT NULL again.
--
-- Data: v2.5.0 has no mapping provenance and no stale-mapping check, so a mapping made under v2.6.0 could
-- not be kept safely (a later title correction would leave it silently eligible). Every employment mapped
-- through the v2.6.0 functions (role_mapping_basis set) is therefore reset to unmapped: ROLE-UNKNOWN,
-- 'unresolved', confidence 0. Candidate role decisions and their PM-ownership clarifications are
-- PERMANENTLY DISCARDED; review status, titles and every other column are unchanged. Mappings that were
-- never made through these functions (no basis) are kept. The script refuses while such mappings exist
-- unless the operator acknowledges the loss for this session, for example:
--   PGOPTIONS='-c trace.rollback_discard_role_mappings=on' psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>

begin;

-- Held until COMMIT, before the acknowledgement check: waits for every in-flight mapping (each holds its
-- employment row) and makes every later one wait. ACCESS EXCLUSIVE is the lock the ALTER TABLEs below take
-- anyway, so it is never upgraded later.
lock table public.trace_candidate_employments in access exclusive mode;

do $$
begin
  if exists (select 1 from public.trace_candidate_employments where role_mapping_basis is not null)
     and coalesce(current_setting('trace.rollback_discard_role_mappings', true), '') <> 'on' then
    raise exception 'rollback stopped: % role mappings (automatic and candidate decisions) would be reset to unmapped. '
                    'Re-run with trace.rollback_discard_role_mappings=on to accept the loss.',
                    (select count(*) from public.trace_candidate_employments where role_mapping_basis is not null);
  end if;
end
$$;

-- The deferred correction check (v2.5.0) would otherwise leave pending events that block ALTER TABLE.
set constraints trace_require_correction_history immediate;
update public.trace_candidate_employments
   set role_id = 'ROLE-UNKNOWN', seniority_code = null, mapping_status = 'unresolved', mapping_confidence = 0, mapping_note = null,
       role_mapping_basis = null, role_mapping_alias_id = null, mapped_title_raw = null, role_mapping_ownership_areas = null
 where role_mapping_basis is not null;

-- ------------------------------------------------------------------ functions, grants and policies
grant trace_review_writer to postgres with inherit true, set true;
drop function trace_review.apply_alias_mapping(uuid, uuid, text, text);
drop function trace_review.choose_employment_role(uuid, uuid, text, text, text[]);
revoke trace_review_writer from postgres;
revoke update (role_id, seniority_code, mapping_status, mapping_confidence, mapping_note, role_mapping_basis,
               role_mapping_alias_id, mapped_title_raw, role_mapping_ownership_areas)
  on public.trace_candidate_employments from trace_review_writer;
drop policy "lookup review writer read" on public.trace_role_aliases;
drop policy "lookup review writer read" on public.trace_role_taxonomy;
drop policy "lookup review writer read" on public.trace_responsibility_areas;
revoke select on public.trace_role_aliases, public.trace_role_taxonomy, public.trace_responsibility_areas from trace_review_writer;
alter table public.trace_role_aliases drop constraint trace_role_aliases_normalized_contract;
drop function trace_util.is_product_owner_title(text);
drop function trace_util.normalize_title(text);

-- ------------------------------------------------------------------ columns and constraints
alter table public.trace_candidate_employments
  drop constraint candidate_employments_mapping_basis_title,
  drop constraint candidate_employments_mapping_alias_match,
  drop constraint candidate_employments_mapping_candidate_selected,
  drop constraint candidate_employments_mapping_unbased,
  drop constraint candidate_employments_mapping_ownership_areas,
  drop column mapping_is_stale,
  drop column role_mapping_ownership_areas,
  drop column mapped_title_raw,
  drop column role_mapping_alias_id,
  drop column role_mapping_basis,
  alter column mapping_confidence set not null;

commit;
