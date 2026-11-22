-- Compensation for migration 20261111190000_trace_candidate_evidence_classification (v2.7.0, M6).
--
-- NOT a migration: it lives outside supabase/migrations and is never applied automatically.
--
--   1. Deploy the backend without evidence classification (M5, commit 5553446) first, or stop calling
--      classify_evidence. Classifications in flight when this script starts are waited for and then counted by
--      the acknowledgement check; later calls wait for the script and then fail closed (the function no
--      longer exists).
--   2. Run this script as postgres. It carries its own BEGIN and COMMIT: any error rolls all of it back and
--      leaves the v2.7.0 catalog intact. Do NOT add psql --single-transaction. Use:
--        psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>
--   3. Mark the migration as reverted:
--        supabase migration repair 20261111190000 --status reverted   (add --local for the local stack)
--
-- What it restores: the exact v2.6.0 catalog. It drops trace_review.classify_evidence_areas, the writer's
-- classification grants, and the classification columns and their constraints.
--
-- Data: every candidate classification (areas, basis and the classified text) is PERMANENTLY DISCARDED with
-- its columns. Evidence text, review status and every other column are unchanged. The script refuses while
-- classifications exist unless the operator acknowledges the loss for this session, for example:
--   PGOPTIONS='-c trace.rollback_discard_area_classifications=on' psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>

begin;

-- Held until COMMIT, before the acknowledgement check: waits for every in-flight classification (each holds its
-- evidence row) and makes every later one wait. ACCESS EXCLUSIVE is the lock the ALTER TABLE below takes anyway.
lock table public.trace_candidate_evidence in access exclusive mode;

do $$
begin
  if exists (select 1 from public.trace_candidate_evidence where area_classification is not null)
     and coalesce(current_setting('trace.rollback_discard_area_classifications', true), '') <> 'on' then
    raise exception 'rollback stopped: % evidence classifications would be discarded. '
                    'Re-run with trace.rollback_discard_area_classifications=on to accept the loss.',
                    (select count(*) from public.trace_candidate_evidence where area_classification is not null);
  end if;
end
$$;

grant trace_review_writer to postgres with inherit true, set true;
drop function trace_review.classify_evidence_areas(uuid, uuid, text[], text);
revoke trace_review_writer from postgres;
revoke update (area_classification, area_classification_basis, area_classified_text)
  on public.trace_candidate_evidence from trace_review_writer;

alter table public.trace_candidate_evidence
  drop constraint candidate_evidence_area_classification_complete,
  drop constraint candidate_evidence_area_classification_nonempty,
  drop column area_classification_is_stale,
  drop column area_classified_text,
  drop column area_classification_basis,
  drop column area_classification;

commit;
