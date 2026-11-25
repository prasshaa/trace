-- v2.34.0 (semantic-retrieval workstream, Phase 2; D53): pgvector storage for two SEPARATE corpora.
--
-- 1. Private candidate experience: trace_private.candidate_embeddings. One row per (source record, model, version):
--    a reviewed evidence line or a reviewed career activity. Owner-scoped (RLS on the request identity), no client grant,
--    written and read only through security-definer functions owned by the NOLOGIN role trace_embedding_writer (the
--    v2.29+ pattern). Deleted by foreign-key cascade with its source record, its profile or its account, so clearing or
--    replacing a profile removes them. The text is never stored here; the content hash is, and a row whose source text
--    changed (hash differs), whose review was withdrawn or whose evidence was rejected is never returned.
-- 2. Role knowledge: trace_ref.knowledge_embeddings. Curated, shared role and capability knowledge only; the corpus is
--    constrained to 'role_knowledge' (aggregate research is NOT allowed here: that needs its own migration after the
--    Career Origins release gates). Read by trace_reference_reader; written by the table owner (offline script).
-- No vector index: a person has tens of rows and the knowledge corpus a few hundred; an exact scan is faster and exact.
-- Additive: an extension, a schema, a role, two tables, policies and functions. Nothing existing changes.
-- Requires PostgreSQL 16+, v2.33.0, and the pgvector extension being available.

create extension if not exists vector with schema extensions;
create schema if not exists trace_semantic;
comment on schema trace_semantic is 'Semantic retrieval functions (D53). Owner-scoped candidate embeddings; shared role knowledge.';

create role trace_embedding_writer nologin inherit nosuperuser nocreatedb nocreaterole nobypassrls noreplication;
grant usage on schema trace_private, trace_review, extensions to trace_embedding_writer;
grant execute on function trace_review.request_uid() to trace_embedding_writer;

-- ------------------------------------------------------------------------------------------- 1. candidate embeddings
create table trace_private.candidate_embeddings (
  embedding_id          uuid primary key default gen_random_uuid(),
  owner_user_id         uuid not null references auth.users (id) on delete cascade,
  candidate_profile_id  uuid not null references public.trace_candidate_profiles (candidate_profile_id) on delete cascade,
  candidate_evidence_id uuid references public.trace_candidate_evidence (candidate_evidence_id) on delete cascade,
  candidate_activity_id uuid references public.trace_candidate_career_activities (candidate_activity_id) on delete cascade,
  content_sha256        text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
  model                 text not null check (length(model) between 1 and 120),
  model_version         text not null check (length(model_version) between 1 and 120),
  embedding             extensions.vector(384) not null,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  constraint candidate_embeddings_one_source check (num_nonnulls(candidate_evidence_id, candidate_activity_id) = 1),
  constraint candidate_embeddings_evidence_unique unique (candidate_evidence_id, model, model_version),
  constraint candidate_embeddings_activity_unique unique (candidate_activity_id, model, model_version)
);
create index candidate_embeddings_owner on trace_private.candidate_embeddings (owner_user_id, candidate_profile_id, model, model_version);
comment on table trace_private.candidate_embeddings is
  'Sensitive derived data (D53): embeddings of a candidate''s own reviewed lines. Owner-scoped; deleted with the source; never shared, never research.';
alter table trace_private.candidate_embeddings enable row level security;
revoke all on trace_private.candidate_embeddings from public, anon, authenticated, service_role;
grant select, insert, update, delete on trace_private.candidate_embeddings to trace_embedding_writer;
create policy candidate_embeddings_writer_select on trace_private.candidate_embeddings
  for select to trace_embedding_writer using (owner_user_id = (select trace_review.request_uid()));
create policy candidate_embeddings_writer_insert on trace_private.candidate_embeddings
  for insert to trace_embedding_writer with check (owner_user_id = (select trace_review.request_uid()));
create policy candidate_embeddings_writer_update on trace_private.candidate_embeddings
  for update to trace_embedding_writer using (owner_user_id = (select trace_review.request_uid()))
  with check (owner_user_id = (select trace_review.request_uid()));
create policy candidate_embeddings_writer_delete on trace_private.candidate_embeddings
  for delete to trace_embedding_writer using (owner_user_id = (select trace_review.request_uid()));

-- the writer reads the caller's own source records (text, status) to embed and to check freshness
grant select (candidate_evidence_id, owner_user_id, candidate_profile_id, evidence_text, evidence_status, review_status)
  on public.trace_candidate_evidence to trace_embedding_writer;
grant select (candidate_activity_id, owner_user_id, candidate_profile_id, title_raw, description_raw, review_status)
  on public.trace_candidate_career_activities to trace_embedding_writer;
create policy candidate_evidence_embedding_writer_select on public.trace_candidate_evidence
  for select to trace_embedding_writer using (owner_user_id = (select trace_review.request_uid()));
create policy candidate_activities_embedding_writer_select on public.trace_candidate_career_activities
  for select to trace_embedding_writer using (owner_user_id = (select trace_review.request_uid()));

-- The caller's eligible source records (reviewed; evidence not rejected) with their current text and hash, and whether
-- an embedding for (model, version) and that exact hash exists.
create function trace_semantic.my_embedding_sources(p_profile uuid, p_model text, p_version text)
returns table (source_kind text, source_id uuid, content text, content_sha256 text, embedded boolean)
language sql
stable
security definer
set search_path = ''
as $$
  with src as (
    select 'evidence'::text as kind, e.candidate_evidence_id as id, e.evidence_text as content
      from public.trace_candidate_evidence e
     where e.owner_user_id = trace_review.request_uid() and e.candidate_profile_id = p_profile
       and e.review_status::text in ('user_confirmed', 'user_corrected') and e.evidence_status::text <> 'rejected'
       and length(btrim(coalesce(e.evidence_text, ''))) > 0
    union all
    select 'activity', a.candidate_activity_id, concat_ws('. ', a.title_raw, nullif(btrim(a.description_raw), ''))
      from public.trace_candidate_career_activities a
     where a.owner_user_id = trace_review.request_uid() and a.candidate_profile_id = p_profile
       and a.review_status::text in ('user_confirmed', 'user_corrected')
  ), hashed as (
    select kind, id, content, encode(sha256(convert_to(content, 'UTF8')), 'hex') as sha from src
  )
  select h.kind, h.id, h.content, h.sha,
         exists (select 1 from trace_private.candidate_embeddings c
                  where c.owner_user_id = trace_review.request_uid() and c.model = p_model and c.model_version = p_version
                    and c.content_sha256 = h.sha
                    and (c.candidate_evidence_id = h.id or c.candidate_activity_id = h.id))
    from hashed h
$$;

-- Store embeddings for the caller's own sources: [{"kind": "evidence"|"activity", "id": uuid, "sha": hex, "embedding": [...]}].
-- An item is stored only if its source is the caller's, eligible, in this profile, and its CURRENT text has that hash
-- (a stale or foreign item is skipped, never stored). Upsert per (source, model, version). Returns the number stored.
create function trace_semantic.store_my_embeddings(p_profile uuid, p_model text, p_version text, p_items jsonb)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  v_n integer := 0;
  v_item jsonb;
  v_vec extensions.vector(384);
begin
  if v_uid is null then
    raise exception 'no request identity' using errcode = '42501';
  end if;
  if jsonb_typeof(p_items) <> 'array' or jsonb_array_length(p_items) > 500 then
    raise exception 'invalid embedding batch' using errcode = '22023';
  end if;
  for v_item in select * from jsonb_array_elements(p_items) loop
    if not exists (select 1 from trace_semantic.my_embedding_sources(p_profile, p_model, p_version) s
                    where s.source_kind = v_item ->> 'kind' and s.source_id = (v_item ->> 'id')::uuid
                      and s.content_sha256 = v_item ->> 'sha') then
      continue;
    end if;
    v_vec := (v_item -> 'embedding')::text::extensions.vector(384);
    if v_item ->> 'kind' = 'evidence' then
      insert into trace_private.candidate_embeddings as c (owner_user_id, candidate_profile_id, candidate_evidence_id,
                                                           content_sha256, model, model_version, embedding)
      values (v_uid, p_profile, (v_item ->> 'id')::uuid, v_item ->> 'sha', p_model, p_version, v_vec)
      on conflict (candidate_evidence_id, model, model_version) do update
         set content_sha256 = excluded.content_sha256, embedding = excluded.embedding, updated_at = now();
    else
      insert into trace_private.candidate_embeddings as c (owner_user_id, candidate_profile_id, candidate_activity_id,
                                                           content_sha256, model, model_version, embedding)
      values (v_uid, p_profile, (v_item ->> 'id')::uuid, v_item ->> 'sha', p_model, p_version, v_vec)
      on conflict (candidate_activity_id, model, model_version) do update
         set content_sha256 = excluded.content_sha256, embedding = excluded.embedding, updated_at = now();
    end if;
    v_n := v_n + 1;
  end loop;
  return v_n;
end;
$$;

-- The caller's eligible, CURRENT records most similar to a query vector (cosine). Owner-scoped; stale, unreviewed and
-- rejected records are excluded by joining back to the live sources.
create function trace_semantic.match_my_evidence(p_profile uuid, p_query real[], p_model text, p_version text,
                                                 p_limit integer default 20)
returns table (source_kind text, source_id uuid, similarity double precision)
language sql
stable
security definer
set search_path = ''
as $$
  select s.source_kind, s.source_id, 1 - (c.embedding operator(extensions.<=>) p_query::extensions.vector(384))
    from trace_semantic.my_embedding_sources(p_profile, p_model, p_version) s
    join trace_private.candidate_embeddings c
      on c.owner_user_id = trace_review.request_uid() and c.model = p_model and c.model_version = p_version
     and c.content_sha256 = s.content_sha256
     and (c.candidate_evidence_id = s.source_id or c.candidate_activity_id = s.source_id)
   order by c.embedding operator(extensions.<=>) p_query::extensions.vector(384), s.source_id
   limit least(greatest(coalesce(p_limit, 20), 1), 200)
$$;

revoke all on function trace_semantic.my_embedding_sources(uuid, text, text),
  trace_semantic.store_my_embeddings(uuid, text, text, jsonb),
  trace_semantic.match_my_evidence(uuid, real[], text, text, integer) from public, anon, authenticated, service_role;
grant usage on schema trace_semantic to trace_app_user, trace_embedding_writer, trace_reference_reader;
grant execute on function trace_semantic.my_embedding_sources(uuid, text, text),
  trace_semantic.store_my_embeddings(uuid, text, text, jsonb),
  trace_semantic.match_my_evidence(uuid, real[], text, text, integer) to trace_app_user;
grant trace_embedding_writer to postgres with set true, inherit false;
grant create on schema trace_semantic to trace_embedding_writer;
alter function trace_semantic.my_embedding_sources(uuid, text, text) owner to trace_embedding_writer;
alter function trace_semantic.store_my_embeddings(uuid, text, text, jsonb) owner to trace_embedding_writer;
alter function trace_semantic.match_my_evidence(uuid, real[], text, text, integer) owner to trace_embedding_writer;
revoke create on schema trace_semantic from trace_embedding_writer;
revoke trace_embedding_writer from postgres;

-- ------------------------------------------------------------------------------------------- 2. role knowledge
create table trace_ref.knowledge_embeddings (
  corpus          text not null check (corpus = 'role_knowledge'),
  source_kind     text not null check (source_kind in ('responsibility_area', 'capability_requirement', 'action_template')),
  source_key      text not null check (length(source_key) between 1 and 200),
  source_version  text not null check (length(source_version) between 1 and 120),
  content         text not null check (length(content) between 1 and 4000),
  content_sha256  text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
  model           text not null,
  model_version   text not null,
  embedding       extensions.vector(384) not null,
  created_at      timestamptz not null default now(),
  primary key (corpus, source_kind, source_key, source_version, model, model_version)
);
comment on table trace_ref.knowledge_embeddings is
  'Shared role and capability knowledge only (D53). Never candidate data; never research (corpus is constrained).';
alter table trace_ref.knowledge_embeddings enable row level security;
revoke all on trace_ref.knowledge_embeddings from public, anon, authenticated, service_role;
grant select on trace_ref.knowledge_embeddings to trace_reference_reader;
create policy knowledge_embeddings_reader_select on trace_ref.knowledge_embeddings
  for select to trace_reference_reader using (corpus = 'role_knowledge');

create function trace_semantic.match_knowledge(p_query real[], p_model text, p_version text, p_kinds text[],
                                               p_limit integer default 20)
returns table (source_kind text, source_key text, source_version text, similarity double precision)
language sql
stable
security invoker
set search_path = ''
as $$
  select k.source_kind, k.source_key, k.source_version, 1 - (k.embedding operator(extensions.<=>) p_query::extensions.vector(384))
    from trace_ref.knowledge_embeddings k
   where k.corpus = 'role_knowledge' and k.model = p_model and k.model_version = p_version
     and (p_kinds is null or k.source_kind = any (p_kinds))
   order by k.embedding operator(extensions.<=>) p_query::extensions.vector(384), k.source_key
   limit least(greatest(coalesce(p_limit, 20), 1), 200)
$$;
revoke all on function trace_semantic.match_knowledge(real[], text, text, text[], integer) from public, anon, authenticated, service_role;
grant execute on function trace_semantic.match_knowledge(real[], text, text, text[], integer) to trace_reference_reader;
grant usage on schema extensions to trace_reference_reader;

do $$
begin
  if pg_catalog.pg_has_role('postgres', 'trace_embedding_writer', 'SET') or pg_catalog.pg_has_role('postgres', 'trace_embedding_writer', 'USAGE') then
    raise exception 'postgres must not be able to act as trace_embedding_writer';
  end if;
  if has_table_privilege('authenticated', 'trace_private.candidate_embeddings', 'select')
     or has_table_privilege('trace_app_user', 'trace_private.candidate_embeddings', 'select')
     or has_table_privilege('service_role', 'trace_private.candidate_embeddings', 'select')
     or has_table_privilege('anon', 'trace_private.candidate_embeddings', 'select') then
    raise exception 'candidate_embeddings must have no client grant';
  end if;
  if has_function_privilege('authenticated', 'trace_semantic.match_my_evidence(uuid, real[], text, text, integer)', 'execute')
     or has_function_privilege('anon', 'trace_semantic.store_my_embeddings(uuid, text, text, jsonb)', 'execute')
     or not has_function_privilege('trace_app_user', 'trace_semantic.match_my_evidence(uuid, real[], text, text, integer)', 'execute') then
    raise exception 'the candidate embedding functions must be executable by trace_app_user only';
  end if;
  if has_table_privilege('authenticated', 'trace_ref.knowledge_embeddings', 'select')
     or has_table_privilege('anon', 'trace_ref.knowledge_embeddings', 'select') then
    raise exception 'knowledge_embeddings is read only by trace_reference_reader';
  end if;
end
$$;
