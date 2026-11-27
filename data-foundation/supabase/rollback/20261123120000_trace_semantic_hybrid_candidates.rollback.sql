-- Rollback of v2.35.0: drops only the function and the column grants it added. No data is touched.
drop function if exists trace_semantic.hybrid_candidates(uuid, jsonb, text, text);
revoke select (area_classification, area_classification_is_stale, area_classification_none, candidate_employment_id,
               candidate_activity_id)
  on public.trace_candidate_evidence from trace_embedding_writer;
revoke select (related_employment_id) on public.trace_candidate_career_activities from trace_embedding_writer;
