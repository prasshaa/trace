"""Reads the curated transition knowledge (bridge roles, action templates, structured employer criteria) and writes and
reads the caller's own plan runs. Everything runs inside the caller's owner session (role trace_app_user, request
identity = owner), so the database's read policies apply. Runs are written ONLY through the trusted function
trace_plan.record_transition_plan (D30): one call, one transaction, append-only."""
from datetime import datetime
from typing import Dict, List, Optional
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

BRIDGES_SQL = """
select b.*, t.canonical_title as bridge_title
  from public.trace_bridge_roles b join public.trace_role_taxonomy t on t.role_id = b.bridge_role_id
 where b.catalogue_version = %(version)s and b.target_role_id = %(target)s
 order by b.bridge_role_id
"""
TEMPLATES_SQL = "select * from public.trace_action_templates where template_version = %(version)s order by template_key"
POSTINGS_SQL = """
select split_part(s.source_key, ':', 2) as source_key, s.company_name, s.role_title, s.role_id, s.location, s.source_url, s.attribution,
       s.last_verified_at, s.revalidate_by, s.entry_background_stance, s.accepted_backgrounds, s.excluded_backgrounds,
       s.accepted_role_families, s.excluded_role_families, s.entry_quote, s.entry_locator,
       trace_util.posting_effective_status(s.source_kind, s.source_status, s.revalidate_by, %(at)s) as effective_status,
       coalesce((select json_agg(json_build_object('text', q.requirement_text, 'importance', q.importance, 'criterion', v.rule,
                                                   'criteria_version', v.rules_version)
                                 order by q.importance desc, q.requirement_text)
                   from public.trace_target_role_requirements q
                   left join public.trace_employer_criterion_rules v on v.requirement_id = q.requirement_id and v.rules_version = %(rules)s
                  where q.target_role_source_id = s.target_role_source_id), '[]') as criteria
  from public.trace_target_role_sources s
 where s.knowledge_pack = %(pack)s and s.source_kind = 'job_posting' and s.role_id = %(target)s
 order by s.source_key
"""
SOURCE_IDS_SQL = """
select source_key, target_role_source_id from public.trace_target_role_sources where knowledge_pack = any(%(packs)s)
"""
# Employers that Trace holds job-posting research for (any role) in the source inventory: the only "directory" there is.
RESEARCHED_EMPLOYERS_SQL = """
select s.company_name, array_agg(distinct s.role_id) filter (where s.role_id is not null) as role_ids
  from public.trace_target_role_sources s
 where s.knowledge_pack = %(pack)s and s.source_kind = 'job_posting'
 group by s.company_name order by lower(s.company_name)
"""
EDUCATION_FIELDS_SQL = "select field_code, label, category, aliases from public.trace_education_fields order by field_code"
OWNERSHIP_SQL = "select responsibility_area from public.trace_responsibility_areas where is_pm_ownership_signal order by 1"
TARGET_SQL = "select role_id, canonical_title, role_family from public.trace_role_taxonomy where role_id = %(target)s"

RUN_SQL = """
select pathway_run_id, data_mode, target_role_id, constraints_json, status, created_at, completed_at, plan_kind, engine_version,
       profile_version, knowledge_pack, source_inventory, taxonomy_version, bridge_catalogue_version, action_template_version,
       eligibility_rules_version, assessment, eligibility, source_versions, privacy_state, decision
  from public.trace_pathway_runs
 where plan_kind = 'transition_plan' and pathway_run_id = %(run)s
"""
LATEST_SQL = """
select pathway_run_id from public.trace_pathway_runs
 where plan_kind = 'transition_plan' and status = 'completed' and target_role_id = %(target)s
 order by created_at desc, pathway_run_id limit 1
"""
LATEST_ANY_SQL = """
select pathway_run_id, target_role_id, created_at from public.trace_pathway_runs
 where plan_kind = 'transition_plan' and status = 'completed'
 order by created_at desc, pathway_run_id limit 1
"""
ROUTES_SQL = """
select recommendation_id, rank, recommendation_reason, evidence_class, supporting_profile_count, limitations, route_basis, route_kind,
       primary_status, bridge_role_id, explanation, feasibility, tradeoffs, evidence_breadth_label, score_breakdown
  from public.trace_pathway_recommendations where pathway_run_id = %(run)s order by rank
"""
STEPS_SQL = """
select s.recommendation_id, s.step_index, s.role_id, t.canonical_title
  from public.trace_pathway_route_steps s join public.trace_role_taxonomy t using (role_id)
 where s.recommendation_id = any(%(recs)s) order by s.recommendation_id, s.step_index
"""
ACTIONS_SQL = """
select recommendation_id, sequence, stage, kind, template_version, template_key, requirement_key, objective, action_text,
       why_for_you, deliverable, where_options, source_keys, record_refs, labels
  from public.trace_pathway_actions where recommendation_id = any(%(recs)s) order by recommendation_id, sequence
"""
EVIDENCE_SQL = """
select e.recommendation_id, e.evidence_type, e.evidence_summary, s.source_key, s.company_name, s.role_title, s.source_url, s.attribution
  from public.trace_pathway_evidence e left join public.trace_target_role_sources s using (target_role_source_id)
 where e.recommendation_id = any(%(recs)s) order by e.recommendation_id, e.evidence_type, e.evidence_summary
"""


class TransitionRepository:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def _rows(self, sql: str, params: dict) -> List[dict]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    # ------------------------------------------------------------------ knowledge
    def role_levels(self, role_ids: List[str]) -> Dict[str, dict]:
        """{role id: {title, family, rank}}: the taxonomy's own seniority rank for describing a move (never a rule)."""
        rows = self._rows("""select r.role_id, r.canonical_title as title, r.role_family as family, s.rank
                               from public.trace_role_taxonomy r
                               left join public.trace_seniority_levels s on s.seniority_code = r.default_seniority_code
                              where r.role_id = any(%(ids)s)""", {"ids": list(role_ids)})
        return {r["role_id"]: r for r in rows}

    def target(self, target: str) -> Optional[dict]:
        rows = self._rows(TARGET_SQL, {"target": target})
        return rows[0] if rows else None

    def bridges(self, version: str, target: str) -> List[dict]:
        return self._rows(BRIDGES_SQL, {"version": version, "target": target})

    def templates(self, version: str) -> Dict[str, dict]:
        return {r["template_key"]: r for r in self._rows(TEMPLATES_SQL, {"version": version})}

    def postings(self, pack: str, target: str, at: datetime, rules_version: str) -> List[dict]:
        return self._rows(POSTINGS_SQL, {"pack": pack, "target": target, "at": at, "rules": rules_version})

    def researched_employers(self, pack: str) -> List[dict]:
        return self._rows(RESEARCHED_EMPLOYERS_SQL, {"pack": pack})

    def education_fields(self) -> Dict[str, dict]:
        """Normalised alias -> field (D36: whole-value matching through explicit aliases)."""
        from app.services.transition.eligibility import normalise_field
        return {normalise_field(a): {"code": r["field_code"], "label": r["label"], "category": r["category"]}
                for r in self._rows(EDUCATION_FIELDS_SQL, {}) for a in r["aliases"]}

    def source_ids(self, packs: List[str]) -> Dict[str, str]:
        return {r["source_key"]: str(r["target_role_source_id"]) for r in self._rows(SOURCE_IDS_SQL, {"packs": packs})}

    def ownership_areas(self) -> List[str]:
        return [r["responsibility_area"] for r in self._rows(OWNERSHIP_SQL, {})]

    # ------------------------------------------------------------------ runs (D30)
    def record(self, plan: dict) -> UUID:
        return self._conn.execute("select trace_plan.record_transition_plan(%s)", (Jsonb(plan),)).fetchone()[0]

    def latest_run_id(self, target: str) -> Optional[UUID]:
        rows = self._rows(LATEST_SQL, {"target": target})
        return rows[0]["pathway_run_id"] if rows else None

    def latest_any(self) -> Optional[dict]:
        """The caller's most recent completed plan for any target (owner-scoped by RLS and the owner session)."""
        rows = self._rows(LATEST_ANY_SQL, {})
        return rows[0] if rows else None

    def run(self, run_id: UUID) -> Optional[dict]:
        rows = self._rows(RUN_SQL, {"run": run_id})
        if not rows:
            return None
        run = rows[0]
        routes = self._rows(ROUTES_SQL, {"run": run_id})
        ids = [r["recommendation_id"] for r in routes]
        steps, actions, evidence = (self._rows(STEPS_SQL, {"recs": ids}), self._rows(ACTIONS_SQL, {"recs": ids}),
                                    self._rows(EVIDENCE_SQL, {"recs": ids}))
        for r in routes:
            rid = r["recommendation_id"]
            r["steps"] = [{"role_id": s["role_id"], "title": s["canonical_title"]} for s in steps if s["recommendation_id"] == rid]
            r["actions"] = [a for a in actions if a["recommendation_id"] == rid]
            r["evidence"] = [e for e in evidence if e["recommendation_id"] == rid]
        run["routes"] = routes
        return run
