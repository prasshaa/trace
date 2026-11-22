"""Static checks on the Supabase migrations and seed SQL. Nothing is executed against a database.

If pglast (the PostgreSQL 17 parser) is installed, every SQL file is also parsed for syntax."""
import re
import unittest

from _support import ROOT

MIGRATIONS = sorted((ROOT / "supabase/migrations").glob("*.sql"))
ALL_SQL = MIGRATIONS + sorted((ROOT / "supabase/seeds").glob("*.sql")) + sorted((ROOT / "supabase/tests").rglob("*.sql"))
SCHEMA_SQL = "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS)
LOOKUP_TABLES = {"trace_seniority_levels", "trace_role_families", "trace_role_taxonomy", "trace_role_aliases",
                 "trace_responsibility_areas", "trace_skill_families", "trace_skills", "trace_skill_aliases",
                 "trace_consent_documents"}

try:
    import pglast  # type: ignore
except ImportError:  # pragma: no cover
    pglast = None


def tables(schema):
    return re.findall(rf"create table {schema}\.(\w+)", SCHEMA_SQL)


class MigrationLayout(unittest.TestCase):
    def test_versioned_file_names_in_strict_order(self):
        names = [p.name for p in MIGRATIONS]
        self.assertTrue(names)
        for n in names:
            self.assertRegex(n, r"^\d{14}_[a-z0-9_]+\.sql$")
        stamps = [n[:14] for n in names]
        self.assertEqual(stamps, sorted(set(stamps)), "timestamps must be unique and ascending")

    def test_no_manual_rerun_guards_on_ddl(self):
        # Migration history is tracked by Supabase; DDL is not wrapped in IF NOT EXISTS re-run hacks
        # (schemas excepted, because Supabase may pre-create none of them but harmless either way).
        self.assertNotRegex(SCHEMA_SQL, r"create table if not exists")
        self.assertNotRegex(SCHEMA_SQL, r"create type if not exists")

    def test_lookup_data_runs_after_all_tables(self):
        # Lookup rows must load after every LOOKUP table exists. Later migrations (v2.1.0+) may add
        # views, functions and non-lookup tables (e.g. v2.2.0 candidate tables), never a lookup table.
        lookup = "20261111121000_trace_lookup_data.sql"
        names = [p.name for p in MIGRATIONS]
        self.assertIn(lookup, names)
        for p in MIGRATIONS:
            if p.name > lookup:
                created = set(re.findall(r"create table public\.(\w+)", p.read_text(encoding="utf-8")))
                with self.subTest(migration=p.name):
                    self.assertFalse(created & LOOKUP_TABLES, created & LOOKUP_TABLES)


class SecurityModel(unittest.TestCase):
    def test_every_table_has_rls_enabled(self):
        for schema in ("public", "trace_ref"):
            for t in tables(schema):
                with self.subTest(table=f"{schema}.{t}"):
                    self.assertRegex(SCHEMA_SQL, rf"alter table {schema}\.{t} enable row level security")

    def test_every_public_table_has_a_policy(self):
        for t in tables("public"):
            with self.subTest(table=t):
                self.assertRegex(SCHEMA_SQL, rf'create policy "[^"]+" on public\.{t}\b')

    def test_no_client_policies_on_reference_cohort(self):
        # v2.8.0: the restricted backend runtime reads the reference cohort through trace_reference_reader (a NOLOGIN,
        # read-only role that no client can assume). Every trace_ref policy must be exactly that: SELECT, that role only.
        pols = re.findall(r"create policy [^;]* on trace_ref\.[^;]*;", SCHEMA_SQL)
        self.assertTrue(pols)
        # v2.23.0 (D30): the single exception is the plan writer recording a reference snapshot (counts and settings only);
        # it is a NOLOGIN role that nobody can use, called only through trace_plan.record_transition_plan.
        # v2.27.0 (D37): and it reads back the shared snapshot it reuses (fingerprinted rows only).
        snapshot_writer = ('create policy "reference snapshots plan writer insert" on trace_ref.reference_snapshots\n'
                           '  for insert to trace_plan_writer with check (true);',
                           'create policy "reference snapshots plan writer select" on trace_ref.reference_snapshots\n'
                           '  for select to trace_plan_writer using (state_fingerprint is not null);')
        for expected in snapshot_writer:
            self.assertEqual(sum(1 for p in pols if p == expected), 1)
        for pol in pols:
            if pol in snapshot_writer:
                continue
            with self.subTest(policy=pol[:60]):
                self.assertRegex(pol, r"for select to trace_reference_reader using \(true\);$")
        self.assertNotRegex(SCHEMA_SQL, r"grant [^;]*trace_reference_reader to [^;]*(anon|authenticated|authenticator|service_role)")

    def test_every_view_is_security_invoker(self):
        views = re.findall(r"create view (\S+)( with \(security_invoker = true\))?", SCHEMA_SQL)
        self.assertTrue(views)
        for name, opt in views:
            with self.subTest(view=name):
                self.assertTrue(opt, f"{name} must be created with (security_invoker = true)")
                self.assertTrue(name.startswith("trace_ref."), f"{name} must live in the non-exposed trace_ref schema")

    def test_anon_only_reads_lookups(self):
        for stmt in re.findall(r"grant [^;]*? to [^;]*anon[^;]*;", SCHEMA_SQL):
            with self.subTest(stmt=stmt[:80]):
                self.assertTrue(stmt.startswith("grant select on"), stmt)
                granted = set(re.findall(r"public\.(\w+)", stmt))
                self.assertTrue(granted <= LOOKUP_TABLES, granted - LOOKUP_TABLES)

    def test_reference_schema_never_granted_to_clients(self):
        self.assertNotRegex(SCHEMA_SQL, r"grant [^;]* on (schema )?trace_ref[^;]* to [^;]*(anon|authenticated)")
        self.assertRegex(SCHEMA_SQL, r"revoke all on schema trace_ref from public, anon, authenticated")

    def test_reference_profile_ids_hidden_from_clients(self):
        grant = re.search(r"grant select \(([^)]*)\)\s+on public\.trace_pathway_evidence to authenticated", SCHEMA_SQL)
        self.assertIsNotNone(grant)
        self.assertNotIn("reference_profile_id", grant.group(1))
        self.assertNotIn("reference_dataset_class", grant.group(1))

    def test_owner_policies_use_auth_uid(self):
        for t in tables("public"):
            if t in LOOKUP_TABLES or t in ("trace_target_role_sources", "trace_target_role_requirements",
                                           # v2.16.0: curated knowledge, readable by signed-in users (no owner)
                                           "trace_capability_requirements", "trace_requirement_source_evidence",
                                           # v2.21.0: curated bridge roles and action templates (no owner)
                                           "trace_bridge_roles", "trace_action_templates",
                                           # v2.24.0: curated candidate pool, education fields, versioned employer rules (no owner)
                                           "trace_digital_role_candidates", "trace_education_fields", "trace_employer_criterion_rules"):
                continue
            with self.subTest(table=t):
                pols = re.findall(rf'create policy "[^"]+" on public\.{t}\b[^;]*;', SCHEMA_SQL)
                self.assertTrue(pols)
                for pol in pols:
                    # v2.3.0 / v2.4.0: the review and ingest writers cannot be granted the auth schema; their
                    # policies use the postgres-owned wrapper around auth.uid() and apply to that role only.
                    if " to trace_review_writer " in pol or " to trace_ingest_writer " in pol or " to trace_plan_writer " in pol:
                        self.assertIn("(select trace_review.request_uid())", pol)
                        self.assertNotIn("auth.uid()", pol)
                    else:
                        self.assertIn("(select auth.uid())", pol)

    def test_no_functions_in_exposed_schema(self):
        self.assertNotRegex(SCHEMA_SQL, r"create (or replace )?function public\.")

    def test_security_definer_functions_pin_search_path(self):
        for fn in re.findall(r"create function [^;]*?\$\$", SCHEMA_SQL, flags=re.S):
            if "security definer" in fn:
                with self.subTest(fn=fn[:60]):
                    self.assertIn("set search_path = ''", fn)

    def test_resume_bucket_is_private_with_owner_folder_policies(self):
        sql = (ROOT / "supabase/migrations/20261111120900_trace_storage.sql").read_text(encoding="utf-8")
        self.assertRegex(sql, r"'trace-resumes', 'trace-resumes', false")
        self.assertEqual(len(re.findall(r"storage\.foldername\(name\)\)\[1\] = \(select auth\.uid\(\)\)::text", sql)), 3)
        self.assertNotIn("for update", sql)

    def test_uploaded_fixtures_rejected_by_constraint(self):
        self.assertIn("check (dataset_class <> 'uploaded_resume_fixture')", SCHEMA_SQL)

    def test_synthetic_cannot_masquerade_as_empirical(self):
        self.assertIn("(data_mode = 'empirical') = (evidence_class <> 'synthetic_test_only')", SCHEMA_SQL)
        self.assertIn("reference_dataset_class = 'consented_real_reference'", SCHEMA_SQL)
        self.assertIn("data_mode = 'empirical' or not timeline_display_allowed", SCHEMA_SQL)

    def test_role_family_is_not_stored_on_employments(self):
        for t in ("trace_ref.employments", "public.trace_candidate_employments"):
            body = re.search(rf"create table {re.escape(t)} \((.*?)\n\);", SCHEMA_SQL, flags=re.S).group(1)
            self.assertNotRegex(body, r"\brole_family\b")


class EpisodePathways(unittest.TestCase):
    """v2.1.0: episode-aware objects are additive and never expose employer pseudonyms."""

    MIGRATION = ROOT / "supabase/migrations/20261111130000_trace_episode_pathways.sql"
    VIEWS = ("v_episode_transitions", "v_career_episode_stints", "v_career_episode_paths")
    FUNCTIONS = ("assert_valid_episode_route", "episode_route_observations", "episode_route_timeline_summary")

    def setUp(self):
        self.sql = self.MIGRATION.read_text(encoding="utf-8")

    def test_objects_are_created(self):
        for v in self.VIEWS:
            self.assertIn(f"create view trace_ref.{v} with (security_invoker = true)", self.sql)
        for f in self.FUNCTIONS:
            self.assertRegex(self.sql, rf"create function trace_ref\.{f}\(")

    def test_existing_pathway_objects_are_not_redefined(self):
        for name in ("v_role_transitions", "v_career_stints", "v_career_paths", "route_observations",
                     "route_timeline_summary", "v_career_sequence", "v_employment_classified", "v_mode_profiles"):
            with self.subTest(name=name):
                self.assertNotRegex(self.sql, rf"(create or replace|drop|alter) (view|function) trace_ref\.{name}\b")

    def test_no_employer_pseudonym_in_episode_objects(self):
        self.assertNotIn("employer_id", self.sql)

    def test_functions_are_service_role_only(self):
        self.assertRegex(self.sql, r"revoke execute on function trace_ref\.assert_valid_episode_route[^;]*from public, anon, authenticated;")
        self.assertRegex(self.sql, r"grant execute on function trace_ref\.assert_valid_episode_route[^;]*to service_role;")
        self.assertNotRegex(self.sql, r"grant [^;]* to [^;]*(anon|authenticated)")

    def test_invalid_routes_raise(self):
        self.assertIn("errcode = '22023'", self.sql)
        self.assertEqual(self.sql.count("perform trace_ref.assert_valid_episode_route(p_route);"), 2)


class CandidateCareerProfile(unittest.TestCase):
    """v2.2.0: candidate-side structured storage is additive, private and separate from trace_ref."""

    MIGRATION = ROOT / "supabase/migrations/20261111140000_trace_candidate_career_profile.sql"

    def setUp(self):
        self.sql = self.MIGRATION.read_text(encoding="utf-8")

    def test_additive_only(self):
        self.assertNotRegex(self.sql, r"drop (table|column|type|view|function)\b")
        self.assertNotIn("raw_extraction_json", re.sub(r"--[^\n]*", "", self.sql))
        for name in re.findall(r"create table (\S+)", self.sql):
            self.assertEqual(name, "public.trace_candidate_career_activities")

    def test_never_touches_reference_schema(self):
        code = re.sub(r"--[^\n]*", "", self.sql)
        code = re.sub(r"'[^']*'", "''", code)  # ignore text in comments and string literals
        self.assertNotRegex(code, r"trace_ref\.\w")

    def test_new_table_is_owner_only(self):
        self.assertIn("alter table public.trace_candidate_career_activities enable row level security", self.sql)
        self.assertIn("revoke all on public.trace_candidate_career_activities from anon, authenticated, public;", self.sql)
        self.assertRegex(self.sql, r'create policy "candidate career activities owner all" on public\.trace_candidate_career_activities\s+'
                                   r"for all to authenticated using \(owner_user_id = \(select auth\.uid\(\)\)\) "
                                   r"with check \(owner_user_id = \(select auth\.uid\(\)\)\)")
        self.assertNotRegex(self.sql, r"grant [^;]* to [^;]*anon")

    def test_backend_managed_columns_are_not_client_writable(self):
        block = re.search(r"column_name not in \(([^)]*)\)", self.sql).group(1)
        for col in ("information_origin", "verification_status", "extraction_confidence", "source_resume_id",
                    "extraction_run_id", "source_location", "evidence_status", "verified"):
            self.assertIn(f"'{col}'", block)
        self.assertNotIn("'review_status'", block)

    def test_links_are_ownership_safe(self):
        # Every new link to another candidate record carries candidate_profile_id; every link to a
        # source document or extraction run carries owner_user_id.
        for fk in re.findall(r"foreign key \(([^)]*)\)\s+references public\.(\w+)", self.sql):
            cols, target = fk
            with self.subTest(fk=cols):
                if target in ("trace_resume_documents", "trace_extraction_runs", "trace_candidate_profiles"):
                    self.assertIn("owner_user_id", cols)
                else:
                    self.assertIn("candidate_profile_id", cols)


class SeedSql(unittest.TestCase):
    def setUp(self):
        self.seed = (ROOT / "supabase/seeds/synthetic_fixtures.sql").read_text(encoding="utf-8")
        self.lookup = (ROOT / "supabase/migrations/20261111121000_trace_lookup_data.sql").read_text(encoding="utf-8")

    def test_seed_is_transactional_and_guarded(self):
        self.assertTrue(self.seed.split("\n\n", 2)[-1].lstrip().startswith("begin;") or "begin;" in self.seed[:800])
        self.assertTrue(self.seed.rstrip().endswith("commit;"))
        self.assertIn("Refusing to load synthetic fixtures", self.seed)

    def test_seed_uses_only_upserts_and_scoped_deletes(self):
        body = self.seed.split("end\n$$;", 1)[1]
        for stmt in [s.strip() for s in re.split(r";\n", body) if s.strip() and not s.strip().startswith("--")]:
            first = stmt.split()[0].lower()
            with self.subTest(stmt=stmt[:60]):
                self.assertIn(first, ("insert", "delete", "commit"))
                if first == "insert":
                    self.assertIn("on conflict", stmt)
                if first == "delete":
                    self.assertTrue("dataset_class = 'synthetic_test_fixture'" in stmt or "profile_id = any (array['SYN-" in stmt)

    def test_no_uploaded_fixture_data_anywhere_in_supabase(self):
        for p in ALL_SQL:
            with self.subTest(file=p.name):
                text = p.read_text(encoding="utf-8")
                self.assertNotRegex(text, r"UPL-\d{3}")
                self.assertNotIn("SRC-UPLOADS", text)

    def test_pgtap_plan_matches_assertion_count(self):
        for p in sorted((ROOT / "supabase/tests").rglob("*.test.sql")):
            text = p.read_text(encoding="utf-8")
            planned = int(re.search(r"select plan\((\d+)\);", text).group(1))
            asserted = len(re.findall(r"^select (ok|is|isnt|throws_ok|lives_ok|results_eq)\(", text, flags=re.M))
            with self.subTest(file=p.name):
                self.assertEqual(planned, asserted)
                self.assertIn("rollback;", text)

    def test_lookup_migration_contains_no_synthetic_rows(self):
        self.assertNotIn("SYN-", self.lookup)
        self.assertNotIn("synthetic_test_fixture", self.lookup)
        self.assertIn("on conflict", self.lookup)


class ReviewEnforcementMigration(unittest.TestCase):
    """v2.3.0 (M2): database-level review enforcement."""
    MIGRATION = ROOT / "supabase/migrations/20261111150000_trace_candidate_review_enforcement.sql"

    def setUp(self):
        self.sql = self.MIGRATION.read_text(encoding="utf-8")
        self.functions = re.findall(r"create function (\S+)\((.*?)\$\$(.*?)\$\$;", self.sql, flags=re.S)

    def test_never_touches_reference_schema_or_rewrites_rows(self):
        self.assertNotRegex(self.sql, r"trace_ref\.")
        # Top-level statements only; function bodies (indented) legitimately update rows at run time.
        self.assertNotRegex(self.sql, r"(?m)^(update|delete from|insert into) public\.")

    def test_functions_pin_search_path_and_avoid_dynamic_sql(self):
        self.assertTrue(self.functions)
        for name, header, body in self.functions:
            with self.subTest(fn=name):
                self.assertIn("set search_path = ''", header)
                self.assertNotRegex(body, r"\bexecute\b")
                self.assertNotRegex(body, r"\bformat\(\s*'[^']*%I")

    def test_only_expected_security_definer_functions(self):
        definers = sorted(name for name, header, _ in self.functions if "security definer" in header)
        self.assertEqual(definers, ["trace_review.reassign_employment", "trace_review.request_uid", "trace_review.review_record"])

    def test_writer_owns_the_write_functions_and_temporary_grants_are_removed(self):
        self.assertIn("alter function trace_review.review_record(text, uuid, uuid, text) owner to trace_review_writer;", self.sql)
        self.assertIn("alter function trace_review.reassign_employment(uuid, uuid, uuid) owner to trace_review_writer;", self.sql)
        grant = self.sql.index("grant trace_review_writer to postgres with set true, inherit false;")
        create = self.sql.index("grant create on schema trace_review to trace_review_writer;")
        self.assertLess(grant, self.sql.index("revoke trace_review_writer from postgres;"))
        self.assertLess(create, self.sql.index("revoke create on schema trace_review from trace_review_writer;"))
        self.assertNotRegex(self.sql, r"grant trace_review_writer to (authenticated|anon|service_role)")

    def test_schema_stays_unexposed(self):
        config = (ROOT / "supabase/config.toml").read_text(encoding="utf-8")
        self.assertNotIn("trace_review", config)
        self.assertRegex(self.sql, r"revoke all on schema trace_review from public;")
        self.assertNotRegex(self.sql, r"grant usage on schema trace_review to [^;]*(anon|service_role)")
        self.assertNotRegex(self.sql, r"comment on schema trace_review")


class FixtureIngestionMigration(unittest.TestCase):
    """v2.4.0 (M3): trusted ingestion of extracted drafts, synthetic fixtures only."""
    MIGRATION = ROOT / "supabase/migrations/20261111160000_trace_fixture_extraction_ingestion.sql"

    def setUp(self):
        self.sql = self.MIGRATION.read_text(encoding="utf-8")
        self.functions = re.findall(r"create (?:or replace )?function (\S+)\((.*?)\$\$(.*?)\$\$;", self.sql, flags=re.S)

    def test_never_touches_reference_schema_or_rewrites_rows(self):
        # The only reference to trace_ref is the allowlist's foreign key to the synthetic fixture profiles.
        code = "\n".join(line.split("--")[0] for line in self.sql.splitlines())
        self.assertEqual(re.findall(r"trace_ref\.\w+(?:\(\w+\))?", code), ["trace_ref.profiles(profile_id)"])
        self.assertRegex(code, r"references trace_ref\.profiles\(profile_id\) on delete cascade")
        self.assertNotRegex(self.sql, r"(?m)^(update|delete from|insert into) public\.")

    def test_fixture_allowlist_is_owner_written_and_writer_read(self):
        self.assertIn("create table trace_ingest.approved_fixture_resumes", self.sql)
        self.assertIn("revoke all on trace_ingest.approved_fixture_resumes from public, anon, authenticated, service_role;", self.sql)
        self.assertIn("grant select on trace_ingest.approved_fixture_resumes to trace_ingest_writer;", self.sql)
        self.assertNotRegex(self.sql, r"grant (insert|update|delete|all)[^;]*approved_fixture_resumes")
        body = dict((n, b) for n, _, b in self.functions)["trace_ingest.assert_ingestion_allowed"]
        self.assertRegex(body, r"a\.fixture_profile_id = v_fixture_id and a\.pdf_sha256 = v_resume_sha")

    def test_seed_fills_the_allowlist_from_the_pdf_archive(self):
        import hashlib
        import zipfile
        seed = (ROOT / "supabase/seeds/synthetic_fixtures.sql").read_text(encoding="utf-8")
        rows = dict(re.findall(r"\('(SYN-\d{3})', '([0-9a-f]{64})'\)", seed.split("trace_ingest.approved_fixture_resumes", 1)[1]))
        with zipfile.ZipFile(ROOT / "data/synthetic/synthetic_resume_pdfs.zip") as z:
            expected = {n.split("_")[0]: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist()}
        self.assertEqual(len(expected), 41)
        self.assertEqual(rows, expected)
        self.assertNotIn("approved_fixture_resumes", (ROOT / "supabase/migrations/20261111121000_trace_lookup_data.sql").read_text(encoding="utf-8"))

    def test_functions_pin_search_path_and_avoid_dynamic_sql(self):
        self.assertEqual(len(self.functions), 6)
        for name, header, body in self.functions:
            with self.subTest(fn=name):
                self.assertIn("set search_path = ''", header)
                self.assertNotRegex(body, r"\bexecute\b")

    def test_only_the_entry_points_are_security_definer_and_writer_owned(self):
        definers = sorted(name for name, header, _ in self.functions if "security definer" in header)
        self.assertEqual(definers, ["trace_ingest.fail_fixture_extraction", "trace_ingest.ingest_fixture_extraction",
                                    "trace_ingest.start_fixture_extraction"])
        for name, _, _ in self.functions:
            if name.startswith("trace_ingest."):
                with self.subTest(fn=name):
                    self.assertRegex(self.sql, rf"alter function {re.escape(name)}\([^)]*\) owner to trace_ingest_writer;")
        grant = self.sql.index("grant trace_ingest_writer to postgres with set true, inherit false;")
        self.assertLess(grant, self.sql.index("revoke trace_ingest_writer from postgres;"))
        self.assertLess(self.sql.index("grant create on schema trace_ingest to trace_ingest_writer;"),
                        self.sql.index("revoke create on schema trace_ingest from trace_ingest_writer;"))
        self.assertNotRegex(self.sql, r"grant trace_ingest_writer to (authenticated|anon|service_role)")

    def test_entry_points_are_for_authenticated_only_and_the_schema_stays_unexposed(self):
        self.assertRegex(self.sql, r"from public, anon, authenticated, service_role;")
        self.assertNotRegex(self.sql, r"grant execute on function trace_ingest\.[^;]* to [^;]*(anon|service_role|public)")
        self.assertNotRegex(self.sql, r"grant usage on schema trace_ingest to [^;]*(anon|service_role)")
        self.assertNotIn("trace_ingest", (ROOT / "supabase/config.toml").read_text(encoding="utf-8"))

    def test_extracted_drafts_are_pending_unverified_and_unmapped(self):
        body = dict((n, b) for n, _, b in self.functions)["trace_ingest.ingest_fixture_extraction"]
        self.assertNotRegex(body, r"\b(review_status|verification_status|role_id|mapping_status|mapping_confidence|skill_id|"
                                  r"responsibility_area|category_code)\b\s*[,)]")
        self.assertIn("'source_extracted'", body)
        self.assertIn("'resume_stated'", body)


class CorrectionHistoryMigration(unittest.TestCase):
    """v2.5.0 (M4): corrections with append-only correction history."""
    MIGRATION = ROOT / "supabase/migrations/20261111170000_trace_candidate_corrections.sql"
    TABLES = {"employment": "trace_candidate_employments", "education": "trace_candidate_education",
              "activity": "trace_candidate_career_activities", "evidence": "trace_candidate_evidence",
              "skill": "trace_candidate_skills", "career_break": "trace_candidate_career_breaks"}
    NEVER = {"candidate_employer_id", "related_employment_id", "candidate_employment_id", "candidate_activity_id",
             "candidate_education_id", "candidate_evidence_id", "skill_id", "responsibility_area", "review_status",
             "information_origin", "verification_status", "extraction_confidence", "source_resume_id", "extraction_run_id",
             "source_location", "role_id", "seniority_code", "mapping_status", "mapping_confidence", "mapping_note",
             "category_code", "category_mapping_status", "category_mapping_confidence", "owner_user_id",
             "candidate_profile_id", "created_at", "updated_at"}

    def setUp(self):
        self.sql = self.MIGRATION.read_text(encoding="utf-8")
        self.functions = re.findall(r"create (?:or replace )?function (\S+)\((.*?)\$\$(.*?)\$\$;", self.sql, flags=re.S)

    def test_never_touches_reference_schema_or_rewrites_rows(self):
        self.assertNotRegex(self.sql, r"trace_ref\.")
        self.assertNotRegex(self.sql, r"(?m)^(update|delete from|insert into) public\.")

    def test_functions_pin_search_path_avoid_dynamic_sql_and_only_correct_record_is_definer(self):
        self.assertEqual(sorted(n for n, _, _ in self.functions), ["trace_review.correct_record", "trace_util.guard_correction_history",
                                                                    "trace_util.guard_review_row", "trace_util.require_correction_history"])
        for name, header, body in self.functions:
            with self.subTest(fn=name):
                self.assertIn("set search_path = ''", header)
                self.assertNotRegex(body, r"\bexecute\b")
                self.assertEqual("security definer" in header, name == "trace_review.correct_record")

    def test_correct_record_is_writer_owned_and_temporary_grants_are_removed(self):
        self.assertIn("alter function trace_review.correct_record(text, uuid, uuid, jsonb) owner to trace_review_writer;", self.sql)
        self.assertLess(self.sql.index("grant trace_review_writer to postgres with set true, inherit false;"),
                        self.sql.index("revoke trace_review_writer from postgres;"))
        self.assertLess(self.sql.index("grant create on schema trace_review to trace_review_writer;"),
                        self.sql.index("revoke create on schema trace_review from trace_review_writer;"))
        self.assertIn("grant execute on function trace_review.correct_record(text, uuid, uuid, jsonb) to authenticated;", self.sql)
        self.assertNotRegex(self.sql, r"grant execute on function trace_review\.correct_record[^;]*(anon|service_role|public)")

    def test_allowlists_triggers_and_writer_grants_agree_and_exclude_protected_fields(self):
        body = dict((n, b) for n, _, b in self.functions)["trace_review.correct_record"]
        for kind, table in self.TABLES.items():
            with self.subTest(kind=kind):
                allowed = re.search(rf"p_kind = '{kind}' then\n    v_allowed := array\[(.*?)\];", body).group(1)
                allowed = [x.strip(" '") for x in allowed.split(",")]
                trigger = re.search(rf"on public\.{table}\n.*?require_correction_history\('{kind}', '\w+', (.*?)\);", self.sql, flags=re.S).group(1)
                grant = re.search(rf"grant update \((.*?)\) on public\.{table} to trace_review_writer;", self.sql).group(1)
                self.assertEqual(allowed, [x.strip(" '") for x in trigger.split(",")])
                self.assertEqual(allowed, [x.strip() for x in grant.split(",")])
                self.assertFalse(set(allowed) & self.NEVER, set(allowed) & self.NEVER)

    def test_history_is_append_only(self):
        self.assertIn("revoke all on public.trace_candidate_corrections from public, anon, authenticated, service_role;", self.sql)
        self.assertNotRegex(self.sql, r"grant (update|delete|truncate|all)[^;]*trace_candidate_corrections")
        self.assertRegex(self.sql, r"before insert or update or delete on public\.trace_candidate_corrections")
        self.assertRegex(self.sql, r"before truncate on public\.trace_candidate_corrections")
        self.assertIn("deferrable initially deferred", self.sql)

    def test_history_is_written_only_by_the_review_writer(self):
        body = dict((n, b) for n, _, b in self.functions)["trace_util.guard_correction_history"]
        self.assertIn("if TG_OP = 'INSERT' and current_user = 'trace_review_writer' then\n    return NEW;", body)
        self.assertEqual(body.count("return NEW;"), 1)

    def test_rollback_locks_before_the_acknowledgement_check(self):
        rb = (ROOT / "supabase/rollback/20261111170000_trace_candidate_corrections.rollback.sql").read_text(encoding="utf-8")
        records = rb.index("lock table public.trace_candidate_employments, public.trace_candidate_education, "
                           "public.trace_candidate_career_activities,\n           public.trace_candidate_evidence, "
                           "public.trace_candidate_skills, public.trace_candidate_career_breaks\n  in access exclusive mode;")
        history = rb.index("lock table public.trace_candidate_corrections in access exclusive mode;")
        check = rb.index("current_setting('trace.rollback_discard_correction_history', true)")
        self.assertLess(rb.index("\nbegin;\n"), records)
        self.assertLess(records, history)
        self.assertLess(history, check)
        self.assertLess(check, rb.index("drop table public.trace_candidate_corrections;"))
        self.assertTrue(rb.rstrip().endswith("commit;"))


class RoleMappingMigration(unittest.TestCase):
    """v2.6.0 (M5): trusted role mapping and candidate role decisions."""
    MIGRATION = ROOT / "supabase/migrations/20261111180000_trace_candidate_role_mapping.sql"
    ROLLBACK = ROOT / "supabase/rollback/20261111180000_trace_candidate_role_mapping.rollback.sql"
    MAPPING_COLS = ("role_id", "seniority_code", "mapping_status", "mapping_confidence", "mapping_note", "role_mapping_basis",
                    "role_mapping_alias_id", "mapped_title_raw", "role_mapping_ownership_areas")

    def setUp(self):
        self.sql = self.MIGRATION.read_text(encoding="utf-8")
        self.functions = re.findall(r"create (?:or replace )?function (\S+)\((.*?)\$\$(.*?)\$\$;", self.sql, flags=re.S)

    def test_never_touches_reference_data_or_rewrites_rows(self):
        code = re.sub(r"--[^\n]*", "", self.sql)                  # SQL without comments
        self.assertNotRegex(code, r"trace_ref\.")
        self.assertNotRegex(code, r"(?m)^(update|delete from|insert into) public\.")
        self.assertNotIn("guard_review_row", code)                 # G1 unchanged
        self.assertNotIn("guard_candidate_insert", code)           # G0 unchanged

    def test_functions_are_writer_owned_definers_with_fixed_sql(self):
        self.assertEqual(sorted(n for n, _, _ in self.functions),
                         ["trace_review.apply_alias_mapping", "trace_review.choose_employment_role",
                          "trace_util.is_product_owner_title", "trace_util.normalize_title"])
        for name, header, body in self.functions:
            if name.startswith("trace_util."):
                with self.subTest(fn=name):
                    self.assertIn("immutable", header)
                    self.assertIn("set search_path = ''", header)
                    self.assertNotIn("security definer", header)
                continue
            with self.subTest(fn=name):
                self.assertIn("security definer", header)
                self.assertIn("set search_path = ''", header)
                self.assertNotRegex(body, r"\bexecute\b")
                self.assertIn("v_uid uuid := trace_review.request_uid();", body)
                self.assertIn("t.owner_user_id = v_uid\n     for update;", body)
                self.assertNotIn("review_status =", body.split("update public.trace_candidate_employments t")[1])
                self.assertIn(f"alter function {name}(", self.sql)
        self.assertLess(self.sql.index("grant trace_review_writer to postgres with set true, inherit false;"),
                        self.sql.index("revoke trace_review_writer from postgres;"))

    def test_title_contract_is_explicit_ascii_and_matches_the_backend(self):
        body = dict((n, b) for n, _, b in self.functions)["trace_util.normalize_title"]
        self.assertTrue(body.isascii(), "the contract must be written with escapes, not raw characters")
        cls = re.search(r"regexp_replace\(p_title, '\[(.*?)\]\+'", body).group(1)
        self.assertEqual(cls, "\\u0009-\\u000d\\u001c-\\u001f\\u0020\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000")
        self.assertIn("'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'", body)
        self.assertNotIn("lower(", body)
        self.assertIn("check (alias_normalized = trace_util.normalize_title(alias))", self.sql)
        matcher = (ROOT.parent / "backend/app/services/role_mapping/matcher.py").read_text(encoding="utf-8")
        ranges = [(0x09, 0x0D), (0x1C, 0x1F), (0x20, 0x20), (0x85, 0x85), (0xA0, 0xA0), (0x1680, 0x1680), (0x2000, 0x200A),
                  (0x2028, 0x2029), (0x202F, 0x202F), (0x205F, 0x205F), (0x3000, 0x3000)]
        expected = "".join("\\u%04x" % c for a, b in ranges for c in range(a, b + 1))
        declared = re.search(r'WHITESPACE = \((.*?)\)\n', matcher, flags=re.S).group(1)
        self.assertEqual("".join(re.findall(r"\\u[0-9a-f]{4}", declared)), expected)
        po = re.search(r"~ '(.*?)'", dict((n, b) for n, _, b in self.functions)["trace_util.is_product_owner_title"]).group(1)
        self.assertEqual(po, "(^|[^a-z0-9])product owner($|[^a-z0-9])")
        self.assertIn('_PRODUCT_OWNER = re.compile(r"(?:^|[^a-z0-9])product owner(?:$|[^a-z0-9])")', matcher)

    def test_mapping_columns_are_granted_to_the_writer_only(self):
        grant = re.search(r"grant update \((.*?)\)\s+on public\.trace_candidate_employments to trace_review_writer;", self.sql, flags=re.S).group(1)
        self.assertEqual([c.strip() for c in grant.replace("\n", " ").split(",")], list(self.MAPPING_COLS))
        self.assertNotRegex(self.sql, r"grant (update|insert)[^;]*trace_candidate_employments[^;]*to (authenticated|service_role|anon|trace_ingest_writer)")
        self.assertRegex(self.sql, r"grant execute on function trace_review\.apply_alias_mapping[^;]*to authenticated;")
        self.assertNotRegex(self.sql, r"grant execute on function[^;]*(anon|service_role|public)\s*;")

    def test_rollback_locks_before_the_acknowledgement_check_and_resets_mappings(self):
        rb = self.ROLLBACK.read_text(encoding="utf-8")
        lock = rb.index("lock table public.trace_candidate_employments in access exclusive mode;")
        check = rb.index("current_setting('trace.rollback_discard_role_mappings', true)")
        reset = rb.index("set role_id = 'ROLE-UNKNOWN'")
        self.assertLess(rb.index("\nbegin;\n"), lock)
        self.assertLess(lock, check)
        self.assertLess(check, reset)
        self.assertLess(rb.index("set constraints trace_require_correction_history immediate;"), reset)
        self.assertLess(reset, rb.index("alter column mapping_confidence set not null;"))
        self.assertTrue(rb.rstrip().endswith("commit;"))


class EvidenceClassificationMigration(unittest.TestCase):
    """v2.7.0 (M6): candidate classification of evidence responsibility areas."""
    MIGRATION = ROOT / "supabase/migrations/20261111190000_trace_candidate_evidence_classification.sql"
    ROLLBACK = ROOT / "supabase/rollback/20261111190000_trace_candidate_evidence_classification.rollback.sql"
    COLS = ("area_classification", "area_classification_basis", "area_classified_text")

    def setUp(self):
        self.sql = self.MIGRATION.read_text(encoding="utf-8")
        self.code = re.sub(r"--[^\n]*", "", self.sql)
        self.functions = re.findall(r"create (?:or replace )?function (\S+)\((.*?)\$\$(.*?)\$\$;", self.sql, flags=re.S)

    def test_one_writer_owned_definer_with_fixed_sql_that_never_touches_review_or_content(self):
        self.assertEqual([n for n, _, _ in self.functions], ["trace_review.classify_evidence_areas"])
        (_, header, body), = self.functions
        self.assertIn("security definer", header)
        self.assertIn("set search_path = ''", header)
        self.assertNotRegex(body, r"\bexecute\b")
        self.assertIn("v_uid uuid := trace_review.request_uid();", body)
        self.assertIn("t.owner_user_id = v_uid\n     for update;", body)
        update = body.split("update public.trace_candidate_evidence t")[1].split(";")[0]
        self.assertEqual(sorted(re.findall(r"(\w+) =", update.split("where")[0])), sorted(self.COLS))
        self.assertIn("alter function trace_review.classify_evidence_areas(uuid, uuid, text[], text) owner to trace_review_writer;", self.sql)
        self.assertTrue(self.sql.isascii())

    def test_no_reference_data_guard_or_row_rewrites(self):
        self.assertNotRegex(self.code, r"trace_ref\.")
        self.assertNotRegex(self.code, r"(?m)^(update|delete from|insert into) public\.")
        self.assertNotIn("guard_review_row", self.code)
        self.assertNotIn("guard_candidate_insert", self.code)

    def test_classification_columns_are_granted_to_the_writer_only(self):
        grant = re.search(r"grant update \((.*?)\)\s+on public\.trace_candidate_evidence to trace_review_writer;", self.sql, flags=re.S).group(1)
        self.assertEqual([c.strip() for c in grant.split(",")], list(self.COLS))
        self.assertNotRegex(self.sql, r"grant (update|insert)[^;]*trace_candidate_evidence[^;]*to (authenticated|service_role|anon|trace_ingest_writer)")
        self.assertIn("grant execute on function trace_review.classify_evidence_areas(uuid, uuid, text[], text) to authenticated;", self.sql)
        self.assertIn("check (area_classification_basis = 'candidate_selected')", self.sql)

    def test_rollback_locks_before_the_acknowledgement_check(self):
        rb = self.ROLLBACK.read_text(encoding="utf-8")
        lock = rb.index("lock table public.trace_candidate_evidence in access exclusive mode;")
        check = rb.index("current_setting('trace.rollback_discard_area_classifications', true)")
        self.assertLess(rb.index("\nbegin;\n"), lock)
        self.assertLess(lock, check)
        self.assertLess(check, rb.index("drop function trace_review.classify_evidence_areas"))
        self.assertTrue(rb.rstrip().endswith("commit;"))


@unittest.skipIf(pglast is None, "pglast not installed; syntax parsing skipped")
class ParseWithPostgresGrammar(unittest.TestCase):
    def test_every_sql_file_parses(self):
        for p in ALL_SQL:
            with self.subTest(file=p.relative_to(ROOT).as_posix()):
                pglast.parser.parse_sql(p.read_text(encoding="utf-8"))

    def test_function_bodies_parse(self):
        bodies = re.findall(r"as \$\$\n(.*?)\n\$\$;", SCHEMA_SQL, flags=re.S)
        self.assertTrue(bodies)
        for b in bodies:
            with self.subTest(body=b.strip()[:60]):
                if b.lstrip().startswith(("declare", "begin")):
                    # Full PL/pgSQL parse, including the statements inside the block.
                    pglast.parse_plpgsql(f"create function f() returns trigger language plpgsql as $x$\n{b}\n$x$;")
                else:
                    pglast.parser.parse_sql(b)


if __name__ == "__main__":
    unittest.main()
