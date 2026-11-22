-- v2.29.0: target-company preferences (POC iteration after product Phase 5; D42).
--
-- The companies a user says they would like to work at, per target role. An ASPIRATION only:
--   * never an employment record and never evidence that the user worked anywhere;
--   * never an input to the transition plan (current position, capability, route decision, stages 1-3): plans do not
--     read this table, and changing it never creates a plan run;
--   * a free-text name is stored as typed (normalised); whether Trace has employer research for it is decided at
--     read time against the curated postings, never stored as a claim here.
-- Private schema, no client grant on the table: the owner role reads and replaces its own list only through the two
-- functions below, which act for the current request identity (trace_review.request_uid()). They are owned by
-- trace_preference_writer (NOLOGIN, no BYPASSRLS, no members that can use it), which holds only what they need, under
-- owner-scoped policies. Rows go with the account (cascade).
-- Requires PostgreSQL 16+ (GRANT ... WITH INHERIT / SET options).

do $$
begin
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'trace_preference_writer') then
    create role trace_preference_writer nologin inherit nosuperuser nocreatedb nocreaterole nobypassrls noreplication;
  end if;
end
$$;
alter role trace_preference_writer nologin inherit nocreatedb nocreaterole nobypassrls;

create table trace_private.target_company_preferences (
  user_id        uuid not null references auth.users (id) on delete cascade,
  target_role_id text not null references public.trace_role_taxonomy (role_id),
  company_name   text not null check (char_length(company_name) between 1 and 100 and company_name !~ '[[:cntrl:]]'
                                      and company_name = btrim(company_name)),
  company_key    text not null check (company_key = lower(company_key) and char_length(company_key) between 1 and 100),
  position       smallint not null check (position between 1 and 20),
  added_at       timestamptz not null default now(),
  primary key (user_id, target_role_id, company_key),
  unique (user_id, target_role_id, position)
);
alter table trace_private.target_company_preferences enable row level security;
revoke all on trace_private.target_company_preferences from public, anon, authenticated, service_role;

-- The writer: the caller's own rows only.
grant usage on schema trace_private, trace_review, trace_plan to trace_preference_writer;
grant execute on function trace_review.request_uid() to trace_preference_writer;
grant select, insert, delete on trace_private.target_company_preferences to trace_preference_writer;
create policy target_company_preferences_writer_select on trace_private.target_company_preferences
  for select to trace_preference_writer using (user_id = (select trace_review.request_uid()));
create policy target_company_preferences_writer_insert on trace_private.target_company_preferences
  for insert to trace_preference_writer with check (user_id = (select trace_review.request_uid()));
create policy target_company_preferences_writer_delete on trace_private.target_company_preferences
  for delete to trace_preference_writer using (user_id = (select trace_review.request_uid()));

-- The caller's own list for one target, in the user's order.
create function trace_plan.my_target_companies(p_target_role_id text)
returns table (company_name text, company_key text, "position" smallint, added_at timestamptz)
language sql
stable
security definer
set search_path = ''
as $$
  select p.company_name, p.company_key, p.position, p.added_at
    from trace_private.target_company_preferences p
   where p.user_id = trace_review.request_uid() and p.target_role_id = p_target_role_id
   order by p.position
$$;

-- Replace the caller's list for one target (add, remove and reorder in one call). Names are trimmed and inner
-- whitespace collapsed; duplicates (case-insensitive) keep their first position; at most 20; an empty array clears it.
-- An unknown target role is refused (22023) through the foreign key.
create function trace_plan.set_target_companies(p_target_role_id text, p_company_names text[])
returns table (company_name text, company_key text, "position" smallint, added_at timestamptz)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := trace_review.request_uid();
  v_name text;
  v_key text;
  v_seen text[] := '{}';
  v_pos smallint := 0;
  v_old_keys text[];
  v_old_added timestamptz[];
begin
  if v_uid is null then
    raise exception 'no request identity' using errcode = '42501';
  end if;
  if coalesce(cardinality(p_company_names), 0) > 20 then
    raise exception 'at most 20 companies per target role' using errcode = '22023';
  end if;
  -- an entry kept from the previous list keeps its original added_at
  select coalesce(array_agg(p.company_key order by p.position), '{}'), coalesce(array_agg(p.added_at order by p.position), '{}')
    into v_old_keys, v_old_added
    from trace_private.target_company_preferences p
   where p.user_id = v_uid and p.target_role_id = p_target_role_id;
  delete from trace_private.target_company_preferences p where p.user_id = v_uid and p.target_role_id = p_target_role_id;
  foreach v_name in array coalesce(p_company_names, '{}'::text[]) loop
    v_name := regexp_replace(btrim(coalesce(v_name, '')), '\s+', ' ', 'g');
    if v_name = '' or char_length(v_name) > 100 or v_name ~ '[[:cntrl:]]' then
      raise exception 'invalid company name' using errcode = '22023';
    end if;
    v_key := lower(v_name);
    continue when v_key = any (v_seen);
    v_seen := v_seen || v_key;
    v_pos := v_pos + 1;
    begin
      insert into trace_private.target_company_preferences (user_id, target_role_id, company_name, company_key, position, added_at)
      values (v_uid, p_target_role_id, v_name, v_key, v_pos,
              coalesce(v_old_added[array_position(v_old_keys, v_key)], now()));
    exception when foreign_key_violation then
      raise exception 'unknown target role' using errcode = '22023';
    end;
  end loop;
  return query select * from trace_plan.my_target_companies(p_target_role_id);
end;
$$;

-- Privileges are set while postgres still owns the functions (they move with ownership); then ownership is transferred
-- as for the plan writer: postgres may SET ROLE to the writer only for the transfer.
revoke all on function trace_plan.my_target_companies(text), trace_plan.set_target_companies(text, text[])
  from public, anon, authenticated, service_role;
grant execute on function trace_plan.my_target_companies(text), trace_plan.set_target_companies(text, text[]) to trace_app_user;
grant trace_preference_writer to postgres with set true, inherit false;
grant create on schema trace_plan to trace_preference_writer;
alter function trace_plan.my_target_companies(text) owner to trace_preference_writer;
alter function trace_plan.set_target_companies(text, text[]) owner to trace_preference_writer;
revoke create on schema trace_plan from trace_preference_writer;
revoke trace_preference_writer from postgres;

do $$
begin
  if pg_catalog.pg_has_role('postgres', 'trace_preference_writer', 'SET') or pg_catalog.pg_has_role('postgres', 'trace_preference_writer', 'USAGE') then
    raise exception 'postgres must not be able to act as trace_preference_writer';
  end if;
  if exists (select 1 from pg_catalog.pg_auth_members m
              where m.roleid = 'trace_preference_writer'::regrole and (m.set_option or m.inherit_option)) then
    raise exception 'trace_preference_writer must have no members that can use it';
  end if;
  if has_function_privilege('anon', 'trace_plan.set_target_companies(text, text[])', 'execute')
     or has_function_privilege('authenticated', 'trace_plan.set_target_companies(text, text[])', 'execute')
     or has_function_privilege('service_role', 'trace_plan.set_target_companies(text, text[])', 'execute')
     or not has_function_privilege('trace_app_user', 'trace_plan.set_target_companies(text, text[])', 'execute')
     or not has_function_privilege('trace_app_user', 'trace_plan.my_target_companies(text)', 'execute') then
    raise exception 'the target-company functions must be executable by trace_app_user only';
  end if;
end
$$;
