-- v2.35.0 (semantic-retrieval workstream, Phase 3): one owner-scoped function that gives the hybrid retriever its
-- candidates, with the lexical and semantic RANKS computed inside the database.
--
-- trace_semantic.hybrid_candidates(profile, queries, model, version). `queries` is one FAMILY of knowledge queries
-- (for example the 20 responsibility areas): [{"key": text, "text": text, "embedding": [384 numbers] | absent}], at most
-- 64. For the caller's own ELIGIBLE records only (reviewed; evidence not rejected: the same set as
-- my_embedding_sources, v2.34.0) it returns one row per record with:
--   * provenance: the source kind and id, the parent employment or activity, the review status, and the text (the
--     caller's own reviewed text, as the profile already returns it);
--   * the user's own categorisation of the line, only if it is CURRENT (a categorisation of older text is not returned);
--   * ranks, per query key: {"lr": the line's rank within the query, "lq": the query's rank within the line's family}
--     for PostgreSQL full-text search (english configuration, OR of the query's lexemes, ts_rank_cd), and {"sr", "sq"}
--     likewise for cosine distance over the line's CURRENT embedding (same model and version; a stale hash is never
--     used). Dense ranks, 1 = best; absent when the line doesn't match or has no current embedding.
-- Similarity values never leave the database: the caller receives ranks only (D53: similarity is not a confidence).
-- Additive: one function and column grants for its owner. Nothing existing changes. Requires v2.34.0.

grant select (area_classification, area_classification_is_stale, area_classification_none, candidate_employment_id,
              candidate_activity_id)
  on public.trace_candidate_evidence to trace_embedding_writer;
grant select (related_employment_id) on public.trace_candidate_career_activities to trace_embedding_writer;

create function trace_semantic.hybrid_candidates(p_profile uuid, p_queries jsonb, p_model text, p_version text)
returns table (source_kind text, source_id uuid, content text, candidate_employment_id uuid, candidate_activity_id uuid,
               review_status text, user_areas text[], user_none boolean, ranks jsonb)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if trace_review.request_uid() is null then
    raise exception 'no request identity' using errcode = '42501';
  end if;
  if jsonb_typeof(p_queries) <> 'array' or jsonb_array_length(p_queries) > 64 then
    raise exception 'invalid query family' using errcode = '22023';
  end if;
  return query
  with src as (
    select * from trace_semantic.my_embedding_sources(p_profile, p_model, p_version)
  ), q as (
    select x ->> 'key' as key,
           case when jsonb_typeof(x -> 'embedding') = 'array' then (x -> 'embedding')::text::extensions.vector(384) end as emb,
           (select string_agg(distinct quote_literal(t.lexeme), ' | ')::tsquery
              from unnest(to_tsvector('english'::regconfig, left(coalesce(x ->> 'text', ''), 4000))) t) as tq
      from jsonb_array_elements(p_queries) x
  ), lex_score as (
    select s.source_id, q.key, ts_rank_cd(to_tsvector('english'::regconfig, s.content), q.tq) as sc
      from src s cross join q
     where q.tq is not null and to_tsvector('english'::regconfig, s.content) @@ q.tq
  ), lex as (
    select l.source_id, l.key, dense_rank() over (partition by l.key order by l.sc desc) as lr,
           dense_rank() over (partition by l.source_id order by l.sc desc) as lq
      from lex_score l
  ), sem_dist as (
    select s.source_id, q.key, c.embedding operator(extensions.<=>) q.emb as d
      from src s
      join trace_private.candidate_embeddings c
        on c.owner_user_id = trace_review.request_uid() and c.model = p_model and c.model_version = p_version
       and c.content_sha256 = s.content_sha256
       and (c.candidate_evidence_id = s.source_id or c.candidate_activity_id = s.source_id)
      cross join q
     where q.emb is not null
  ), sem as (
    select m.source_id, m.key, dense_rank() over (partition by m.key order by m.d) as sr,
           dense_rank() over (partition by m.source_id order by m.d) as sq
      from sem_dist m
  ), r as (
    select coalesce(l.source_id, m.source_id) as sid,
           jsonb_object_agg(coalesce(l.key, m.key), jsonb_strip_nulls(jsonb_build_object('lr', l.lr, 'lq', l.lq, 'sr', m.sr, 'sq', m.sq))) as ranks
      from lex l full join sem m on m.source_id = l.source_id and m.key = l.key
     group by 1
  )
  select s.source_kind, s.source_id, s.content,
         case when s.source_kind = 'evidence' then e.candidate_employment_id else a.related_employment_id end,
         case when s.source_kind = 'evidence' then e.candidate_activity_id else s.source_id end,
         coalesce(e.review_status, a.review_status)::text,
         case when e.area_classification_is_stale then null else e.area_classification end,
         coalesce(e.area_classification_none and not e.area_classification_is_stale, false),
         coalesce(r.ranks, '{}'::jsonb)
    from src s
    left join public.trace_candidate_evidence e
      on s.source_kind = 'evidence' and e.candidate_evidence_id = s.source_id and e.owner_user_id = trace_review.request_uid()
    left join public.trace_candidate_career_activities a
      on s.source_kind = 'activity' and a.candidate_activity_id = s.source_id and a.owner_user_id = trace_review.request_uid()
    left join r on r.sid = s.source_id
   order by s.source_kind, s.source_id;
end;
$$;

revoke all on function trace_semantic.hybrid_candidates(uuid, jsonb, text, text) from public, anon, authenticated, service_role;
grant execute on function trace_semantic.hybrid_candidates(uuid, jsonb, text, text) to trace_app_user;
grant trace_embedding_writer to postgres with set true, inherit false;
grant create on schema trace_semantic to trace_embedding_writer;
alter function trace_semantic.hybrid_candidates(uuid, jsonb, text, text) owner to trace_embedding_writer;
revoke create on schema trace_semantic from trace_embedding_writer;
revoke trace_embedding_writer from postgres;

do $$
begin
  if pg_catalog.pg_has_role('postgres', 'trace_embedding_writer', 'SET') or pg_catalog.pg_has_role('postgres', 'trace_embedding_writer', 'USAGE') then
    raise exception 'postgres must not be able to act as trace_embedding_writer';
  end if;
  if has_function_privilege('authenticated', 'trace_semantic.hybrid_candidates(uuid, jsonb, text, text)', 'execute')
     or has_function_privilege('anon', 'trace_semantic.hybrid_candidates(uuid, jsonb, text, text)', 'execute')
     or not has_function_privilege('trace_app_user', 'trace_semantic.hybrid_candidates(uuid, jsonb, text, text)', 'execute') then
    raise exception 'hybrid_candidates must be executable by trace_app_user only';
  end if;
  if has_column_privilege('trace_embedding_writer', 'public.trace_candidate_evidence', 'area_qualifiers', 'select') then
    raise exception 'the writer reads only the columns it needs';
  end if;
end
$$;
