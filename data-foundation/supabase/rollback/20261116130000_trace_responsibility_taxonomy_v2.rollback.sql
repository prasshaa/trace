-- Compensation for migration 20261116130000_trace_responsibility_taxonomy_v2 (v2.18.0). NOT a migration.
-- Roll back 20261116140000 and 20261116135000 first, and deploy a backend that does not call the v2 classification
-- functions. Run as postgres:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>; supabase migration repair 20261116130000 --status reverted
-- Effect: classifications made with "None of these areas" are cleared (they cannot exist under the v2.7.0 rule);
-- area classifications are kept but lose their taxonomy version, qualifier answers and timestamp. The four v2 areas are
-- removed only if no record uses them (otherwise this script stops: re-classify those records first).
begin;
update public.trace_candidate_evidence
   set area_classification = null, area_classification_basis = null, area_classified_text = null
 where area_classification_none;
drop function trace_review.clear_evidence_classification(uuid, uuid);
drop function trace_review.classify_evidence_v2(uuid, uuid, text[], boolean, jsonb, text);
alter table public.trace_candidate_evidence
  drop constraint candidate_evidence_area_qualifiers_need_areas,
  drop constraint candidate_evidence_area_none_is_versioned,
  drop constraint candidate_evidence_area_none_is_classified,
  drop constraint candidate_evidence_area_classification_nonempty,
  drop column area_classified_at, drop column area_qualifiers, drop column area_classification_taxonomy_version,
  drop column area_classification_none;
alter table public.trace_candidate_evidence
  add constraint candidate_evidence_area_classification_nonempty check (
    area_classification is null or (cardinality(area_classification) >= 1 and array_position(area_classification, null) is null));
drop function trace_util.current_taxonomy_version();
delete from public.trace_responsibility_areas
 where responsibility_area in ('ai_product_work', 'data_governance_quality', 'business_case_economics', 'executive_communication');
alter table public.trace_responsibility_areas
  drop column qualifiers, drop column definition_version, drop column introduced_in_version, drop column examples;
commit;
