-- Rollback of v2.34.0. Embeddings are derived data and can be regenerated; dropping them loses no source data.
-- The pgvector extension is dropped only if nothing else uses it.
drop function if exists trace_semantic.match_knowledge(real[], text, text, text[], integer);
drop function if exists trace_semantic.match_my_evidence(uuid, real[], text, text, integer);
drop function if exists trace_semantic.store_my_embeddings(uuid, text, text, jsonb);
drop function if exists trace_semantic.my_embedding_sources(uuid, text, text);
drop table if exists trace_ref.knowledge_embeddings;
drop table if exists trace_private.candidate_embeddings;
drop policy if exists candidate_evidence_embedding_writer_select on public.trace_candidate_evidence;
drop policy if exists candidate_activities_embedding_writer_select on public.trace_candidate_career_activities;
revoke select on public.trace_candidate_evidence, public.trace_candidate_career_activities from trace_embedding_writer;
revoke all on schema trace_private, trace_review, extensions from trace_embedding_writer;
revoke all on function trace_review.request_uid() from trace_embedding_writer;
drop schema if exists trace_semantic;
drop role if exists trace_embedding_writer;
do $$
begin
  if not exists (select 1 from pg_depend d join pg_extension e on e.oid = d.refobjid
                  where e.extname = 'vector' and d.deptype = 'n') then
    drop extension if exists vector;
  end if;
end
$$;
