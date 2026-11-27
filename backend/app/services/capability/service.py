"""Capability assessment and posting eligibility metadata (product Phase 3). Read-only; deterministic; no external AI.

The two results are deliberately separate (D25):
  capability_assessment()  assessment 1: what the role generally requires and where this user stands
  postings()               the employer-specific metadata for assessment 2 (who said what, when, and whether the
                           posting is a current opportunity under D26). It is NOT matched against the user here, and it
                           never feeds the capability states. Personal transition feasibility (assessment 3) is Phase 4.
"""
from datetime import datetime, timezone
from typing import Iterable, List, Optional

import psycopg

from app.services.candidate_profile import CandidateProfileService, InvalidReference, OwnerContext, owner_session
from app.services.capability.assess import CONTEXTS, Requirement, assess, interpret_qualification
from app.services.capability.repository import KnowledgeRepository

PACK = "pm-v1.2"                 # requirements revision (Phase 4 review, D31/D35): decision rights; C4 outcome-metrics question
SOURCE_INVENTORY = "pm-v1"       # the approved source inventory, unchanged by the revision
PACK_NOTE = ("Curated evidence base of 15 sources (approved 2026-11-16), not a representative survey of the job market. "
             "No single source defines the profession.")
# "apm" uses the shared requirements (applies_to_level "all") only. Senior-only requirements stay on senior_pm.
# Associate Product Manager is a real target: Non-PM → APM is a supported move. Roles outside this set have no pack.
TARGET_LEVELS = {"PM-APM": "apm", "PM-PM": "pm", "PM-SPM": "senior_pm"}
SEPARATION_NOTE = ("This is the target-role capability view only. It is not an employer's application criteria and not a "
                   "judgement of how feasible a move is for you; those are shown separately.")


def display_state(o) -> str:
    """evidence_found | needs_context | not_yet_demonstrated | cannot_assess_yet | not_applicable. Missing evidence is
    never shown as missing experience: "not yet demonstrated in the available evidence"."""
    if o.state == "demonstrated":
        return "evidence_found"
    if o.state == "transferable":
        return "needs_context"
    if o.state == "gap" or o.reason == "not_inferable_from_absence":
        return "not_yet_demonstrated"
    if o.state == "not_applicable":
        return "not_applicable"
    return "cannot_assess_yet"


def context_note(o, questions: dict) -> Optional[dict]:
    """For related evidence: what would show the whole capability. A scope question the text doesn't answer is asked
    in the question's own words; a partial reading says the line doesn't show ownership or the full scope."""
    if o.state != "transferable":
        return None
    if o.needs_context and o.needs_context.startswith("scope_question:"):
        keys = [k for k in o.needs_context.split(":", 1)[1].split(",") if k in questions]
        return {"kind": "scope_question", "questions": [questions[k] for k in keys], "question_keys": keys}
    if o.needs_context == "partial_reading":
        return {"kind": "partial", "questions": []}
    return {"kind": "related", "questions": []}


class TargetNotCovered(InvalidReference):
    """No approved knowledge pack covers this target role yet."""


def _requirement(r: dict) -> Requirement:
    return Requirement(key=r["requirement_key"], requirement_class=r["requirement_class"], applies_to_level=r["applies_to_level"],
                       title=r["title"], description=r["description"], level_notes=r["level_notes"] or {},
                       contexts=tuple(r["contexts"]), direct_areas=tuple(r["direct_areas"]), transferable_areas=tuple(r["transferable_areas"]),
                       direct_skills=tuple(r["direct_skills"]), transferable_skills=tuple(r["transferable_skills"]),
                       transferable_role_families=tuple(r["transferable_role_families"]), transfer_rationale=r["transfer_rationale"],
                       gap_basis=r["gap_basis"], area_conditions=dict(r.get("area_conditions") or {}),
                       decision_rights=bool(r.get("decision_rights")), area_condition_fallback=dict(r.get("area_condition_fallback") or {}))


class CapabilityService:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn
        self._profiles = CandidateProfileService(conn)

    def capability_assessment(self, ctx: OwnerContext, target_role_id: str, contexts: Iterable[str],
                              at: Optional[datetime] = None) -> dict:
        level = TARGET_LEVELS.get(target_role_id)
        if level is None:
            raise TargetNotCovered("no approved knowledge pack covers this target role yet "
                                    "(Associate Product Manager, Product Manager and Senior Product Manager)")
        contexts = sorted(set(contexts))
        bad = set(contexts) - set(CONTEXTS)
        if bad:
            raise InvalidReference(f"unknown product contexts: {sorted(bad)}")
        at = at or datetime.now(timezone.utc)
        profile = self._profiles.get_profile(ctx)
        with owner_session(self._conn, ctx.owner_user_id):
            repo = KnowledgeRepository(self._conn)
            rows, evidence, labels, names = repo.requirements(PACK), repo.evidence(PACK, at), repo.labels(), repo.skill_names()
            taxonomy, taxonomy_version = repo.area_versions()
            questions = repo.scope_questions()
        reqs = [_requirement(r) for r in rows]
        outcomes, coverage = assess(profile, reqs, level, contexts, names, taxonomy)
        by_key = {r.key: r for r in reqs}
        sources = {}
        for e in evidence:
            sources.setdefault(e["requirement_key"], []).append(e)
        items = []
        for o in outcomes:
            r = by_key[o.key]
            items.append({
                "key": r.key, "title": r.title, "description": r.description, "requirement_class": r.requirement_class,
                "applies_to_level": r.applies_to_level, "level_note": r.level_notes.get(level), "contexts": list(r.contexts),
                "state": o.state, "basis": o.basis, "reason": o.reason, "transfer_rationale": o.transfer_rationale,
                "blocking_records": list(o.blocking_records), "area_conditions": dict(r.area_conditions),
                "decision_rights": r.decision_rights,
                "supporting": [{"kind": s.kind, "record_id": s.record_id, "basis": o.basis,
                                "matched": ([s.excerpt] if s.kind == "skill" else
                                            [labels["area"].get(m) or labels["family"].get(m) or m for m in s.matched]),
                                "excerpt": s.excerpt, "read_by": s.read_by, "cue": s.cue, "why": s.why} for s in o.supporting],
                # Phase 5 (D50): the three plain-language states the product shows, plus "can't assess yet"
                "display_state": display_state(o),
                "needs_context": context_note(o, questions),
                "sources": [{"key": e["source_key"], "kind": e["source_kind"], "publisher": e["company_name"], "title": e["role_title"],
                             "url": e["source_url"], "attribution": e["attribution"], "stance": e["stance"], "note": e["note"],
                             "status": e["effective_status"], "last_verified_at": e["last_verified_at"]}
                            for e in sources.get(r.key, [])],
            })
        education = [{"title": d.qualification_title_raw, "stored_level": d.qualification_level,
                      "level": interpret_qualification(d.qualification_level, d.qualification_title_raw)[0],
                      "qualification_type": interpret_qualification(d.qualification_level, d.qualification_title_raw)[1],
                      "field_of_study": d.field_of_study}
                     for d in profile.education if d.provenance.review_status in ("user_confirmed", "user_corrected")]
        return {"target_role_id": target_role_id, "target_level": level, "knowledge_pack": PACK, "pack_note": PACK_NOTE,
                "source_inventory": SOURCE_INVENTORY, "taxonomy_version": taxonomy_version,
                "contexts": contexts, "separation_note": SEPARATION_NOTE,
                "coverage": {"reviewed_evidence": coverage.reviewed_evidence, "pending_evidence": coverage.pending_evidence,
                             "unclassified_reviewed_evidence": coverage.unclassified_reviewed, "complete": coverage.complete,
                             "reason": coverage.reason, "blocking_records": list(coverage.blocking_records)},
                "requirements": items, "education": education}

    def postings(self, ctx: OwnerContext, at: Optional[datetime] = None) -> List[dict]:
        """Employer-specific posting metadata (D23, D26). Dated, attributed; a posting is a current opportunity only
        when verified within the window. Not matched against the user and never used as a capability gap."""
        at = at or datetime.now(timezone.utc)
        with owner_session(self._conn, ctx.owner_user_id):
            rows = KnowledgeRepository(self._conn).postings(SOURCE_INVENTORY, at)
        out = []
        for p in rows:
            current = p["effective_status"] == "current"
            out.append({**p, "is_current_opportunity": current,
                        "evidence_use": ("current_opportunity" if current else
                                         "requires_revalidation" if p["effective_status"] == "current_check_required" else
                                         "historical_eligibility_evidence")})
        return out
