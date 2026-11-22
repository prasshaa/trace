"""Reads the approved target-role knowledge (curated public content; no user data). Runs inside the caller's owner
session, so the database's own read policies apply (withdrawn sources and their evidence are not visible)."""
from datetime import datetime
from typing import Dict, List, Tuple

import psycopg
from psycopg.rows import dict_row

REQUIREMENTS_SQL = """
select requirement_key, requirement_class, applies_to_level, title, description, level_notes, contexts, direct_areas,
       transferable_areas, direct_skills, transferable_skills, transferable_role_families, transfer_rationale, gap_basis,
       area_conditions, decision_rights, area_condition_fallback
  from public.trace_capability_requirements where knowledge_pack = %(pack)s order by requirement_key
"""
EVIDENCE_SQL = """
select r.requirement_key, split_part(s.source_key, ':', 2) as source_key, s.source_kind, s.company_name, s.role_title,
       s.source_url, s.attribution, e.stance, e.note, s.last_verified_at,
       trace_util.posting_effective_status(s.source_kind, s.source_status, s.revalidate_by, %(at)s) as effective_status
  from public.trace_requirement_source_evidence e
  join public.trace_capability_requirements r using (capability_requirement_id)
  join public.trace_target_role_sources s using (target_role_source_id)
 where r.knowledge_pack = %(pack)s
 order by r.requirement_key, s.source_kind, s.source_key
"""
POSTINGS_SQL = """
select split_part(s.source_key, ':', 2) as source_key, s.company_name, s.role_title, s.role_id, s.location, s.source_url,
       s.attribution, s.licence, s.published_at, s.last_verified_at, s.revalidate_by, s.source_status, s.status_note,
       s.context_tags, s.entry_background_stance, s.accepted_backgrounds, s.excluded_backgrounds, s.entry_quote, s.entry_locator,
       trace_util.posting_effective_status(s.source_kind, s.source_status, s.revalidate_by, %(at)s) as effective_status,
       coalesce((select json_agg(json_build_object('type', q.requirement_type, 'text', q.requirement_text,
                                                   'min_years', q.min_years_experience, 'importance', q.importance)
                                 order by q.requirement_type, q.requirement_text)
                   from public.trace_target_role_requirements q where q.target_role_source_id = s.target_role_source_id), '[]') as criteria
  from public.trace_target_role_sources s
 where s.knowledge_pack = %(pack)s and s.source_kind = 'job_posting'
 order by s.source_key
"""
SKILL_NAMES_SQL = """
select s.skill_id, s.canonical_skill as name, s.canonical_skill from public.trace_skills s
union all
select a.skill_id, a.alias as name, s.canonical_skill from public.trace_skill_aliases a join public.trace_skills s using (skill_id)
"""
AREA_VERSIONS_SQL = """
select responsibility_area, greatest(introduced_in_version, definition_version) as version, qualifiers,
       trace_util.current_taxonomy_version() as current_version
  from public.trace_responsibility_areas
"""
LABELS_SQL = """
select 'area' as kind, responsibility_area as code, label from public.trace_responsibility_areas
union all select 'family', role_family, label from public.trace_role_families
"""


class KnowledgeRepository:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def _rows(self, sql: str, params: dict) -> List[dict]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def requirements(self, pack: str) -> List[dict]:
        return self._rows(REQUIREMENTS_SQL, {"pack": pack})

    def evidence(self, pack: str, at: datetime) -> List[dict]:
        return self._rows(EVIDENCE_SQL, {"pack": pack, "at": at})

    def postings(self, pack: str, at: datetime) -> List[dict]:
        return self._rows(POSTINGS_SQL, {"pack": pack, "at": at})

    def skill_names(self) -> Dict[str, Tuple[str, str]]:
        """Exact-match lookup: normalised taxonomy skill name or curated alias -> (skill id, canonical name)."""
        from app.services.capability.assess import name_key
        out: Dict[str, Tuple[str, str]] = {}
        for row in self._rows(SKILL_NAMES_SQL, {}):
            out.setdefault(name_key(row["name"]), (row["skill_id"], row["canonical_skill"]))
        return out

    def area_versions(self) -> Tuple[Dict[str, "AreaInfo"], int]:
        from app.services.capability.assess import AreaInfo
        rows = self._rows(AREA_VERSIONS_SQL, {})
        info = {r["responsibility_area"]: AreaInfo(version=r["version"],
                                                   qualifier_versions={k: int(v.get("version", 1)) for k, v in (r["qualifiers"] or {}).items()})
                for r in rows}
        return info, (rows[0]["current_version"] if rows else 1)

    def scope_questions(self) -> Dict[str, str]:
        """{qualifier key: its question} from the responsibility-area taxonomy (v2/v3 scope questions)."""
        out: Dict[str, str] = {}
        for r in self._rows(AREA_VERSIONS_SQL, {}):
            for k, v in (r["qualifiers"] or {}).items():
                if v.get("question"):
                    out[k] = v["question"]
        return out

    def labels(self) -> Dict[str, Dict[str, str]]:
        out: Dict[str, Dict[str, str]] = {"area": {}, "family": {}}
        for r in self._rows(LABELS_SQL, {}):
            out[r["kind"]][r["code"]] = r["label"]
        return out
