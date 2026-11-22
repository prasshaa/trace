-- Trace Data Foundation v2 · 03 · reference lookup tables (public, read-only to clients)
--
-- Contents are loaded by migration 20261111121000_trace_lookup_data.sql, which is generated
-- from data/taxonomy, data/skills and data/config by scripts/build_seeds.py.

create table public.trace_seniority_levels (
  seniority_code text primary key check (seniority_code ~ '^[a-z_]+$'),
  label          text not null,
  rank           smallint unique,          -- null = not comparable on the ladder (founder, unknown)
  description    text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);
alter table public.trace_seniority_levels enable row level security;

create table public.trace_role_families (
  role_family           text primary key check (role_family ~ '^[a-z_]+$'),
  label                 text not null,
  is_product_management boolean not null default false,
  description           text,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);
alter table public.trace_role_families enable row level security;

-- role_family is defined once, here. Employment tables store only role_id; every view
-- derives the family by joining this table, so family and role cannot disagree.
create table public.trace_role_taxonomy (
  role_id                text primary key check (role_id ~ '^(PM|ROLE)-[A-Z0-9]+$'),
  role_family            text not null references public.trace_role_families(role_family),
  canonical_title        text not null,
  default_seniority_code text not null references public.trace_seniority_levels(seniority_code),
  is_pm_target           boolean not null default false,
  is_resolved            boolean not null default true,
  active                 boolean not null default true,
  definition             text,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  constraint trace_role_taxonomy_pm_target_family check (not is_pm_target or role_family = 'product_management'),
  constraint trace_role_taxonomy_unresolved_not_target check (is_resolved or not is_pm_target)
);
alter table public.trace_role_taxonomy enable row level security;
create index trace_role_taxonomy_family_idx on public.trace_role_taxonomy (role_family);
create index trace_role_taxonomy_seniority_idx on public.trace_role_taxonomy (default_seniority_code);

-- An alias may map to several roles; every such alias must be context_required, and a
-- context_required match is recorded as mapping_status = 'needs_review' until confirmed.
create table public.trace_role_aliases (
  alias_id            text primary key check (alias_id ~ '^ALS-[0-9]{4}$'),
  alias               text not null,
  alias_normalized    text generated always as (lower(regexp_replace(btrim(alias), '\s+', ' ', 'g'))) stored,
  role_id             text not null references public.trace_role_taxonomy(role_id),
  seniority_hint_code text references public.trace_seniority_levels(seniority_code),
  context_required    boolean not null default false,
  default_confidence  numeric(4,3) not null check (default_confidence between 0 and 1),
  mapping_rule        text,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (alias_normalized, role_id),
  constraint trace_role_aliases_rule_required check (not context_required or mapping_rule is not null)
);
alter table public.trace_role_aliases enable row level security;
create index trace_role_aliases_role_idx on public.trace_role_aliases (role_id);
create index trace_role_aliases_seniority_idx on public.trace_role_aliases (seniority_hint_code);

create table public.trace_responsibility_areas (
  responsibility_area    text primary key check (responsibility_area ~ '^[a-z_]+$'),
  label                  text not null,
  is_pm_ownership_signal boolean not null default false,
  description            text,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now()
);
alter table public.trace_responsibility_areas enable row level security;

create table public.trace_skill_families (
  skill_family text primary key check (skill_family ~ '^[a-z_]+$'),
  label        text not null,
  description  text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
alter table public.trace_skill_families enable row level security;

create table public.trace_skills (
  skill_id        text primary key check (skill_id ~ '^SKL-[A-Z0-9-]+$'),
  canonical_skill text not null unique check (canonical_skill = lower(canonical_skill)),
  skill_family    text not null references public.trace_skill_families(skill_family),
  description     text,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
alter table public.trace_skills enable row level security;
create index trace_skills_family_idx on public.trace_skills (skill_family);

create table public.trace_skill_aliases (
  alias      text primary key check (alias = lower(btrim(alias))),
  skill_id   text not null references public.trace_skills(skill_id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.trace_skill_aliases enable row level security;
create index trace_skill_aliases_skill_idx on public.trace_skill_aliases (skill_id);

create trigger set_updated_at before update on public.trace_seniority_levels for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on public.trace_role_families for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on public.trace_role_taxonomy for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on public.trace_role_aliases for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on public.trace_responsibility_areas for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on public.trace_skill_families for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on public.trace_skills for each row execute function trace_util.set_updated_at();
create trigger set_updated_at before update on public.trace_skill_aliases for each row execute function trace_util.set_updated_at();
