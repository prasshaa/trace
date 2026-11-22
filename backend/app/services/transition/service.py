"""Transition plan service (product Phase 4). Orchestrates existing services; adds no ranking to /pathway.

    create_plan  assessment 1 (CapabilityService, unchanged) + the user's reviewed history and current position +
                 the observed routes of ONE cohort (CandidateProfileService.pathway_result, unchanged: privacy minimum,
                 own-contribution exclusion) + curated bridges, templates and employer criteria -> feasibility,
                 eligibility (assessment 2) and four-stage actions -> saved through the trusted append-only writer
                 (D30) -> returned as stored.
    plan         the caller's own saved run (latest for a target, or by id). Other users' runs are invisible (RLS).
No external AI, no embeddings, no scores. The deterministic engine works without an LLM.
"""
from datetime import date, datetime, timezone
from typing import Dict, Iterable, List, Optional
from uuid import UUID

import psycopg

from app.services.candidate_profile import CandidateProfileService, OwnerContext, RecordNotFound, owner_session
from app.services.candidate_profile.entries import profile_version
from app.services.capability.assess import interpret_qualification
from app.services.capability.repository import KnowledgeRepository
from app.services.capability.service import PACK, SOURCE_INVENTORY, CapabilityService
from app.services.transition.actions import ActionBuilder
from app.services.transition.composer import compose
from app.services.transition.eligibility import (
    REVIEWED, EligibilityFacts, Job, Qualification, normalise_field, posting_eligibility,
)
from app.services.transition.feasibility import ENGINE_VERSION, decide, direct_support, evaluate_bridges
from app.services.transition.repository import TransitionRepository

BRIDGE_CATALOGUE = "pm-bridges-v2"           # Phase 4 review corrections (D32-D34)
ACTION_TEMPLATES = "pm-actions-v2"
ELIGIBILITY_RULES = "pm-eligibility-v3"       # D36; Phase 4 closure (J12 current APM, J3 ordering)
UNCERTAIN_PATTERN = ("internship", "part_time", "apprenticeship")
VERSIONS = {"bridge_catalogue_version": BRIDGE_CATALOGUE, "action_template_version": ACTION_TEMPLATES,
            "eligibility_rules_version": ELIGIBILITY_RULES}


def readiness(profile, run: dict, rm: dict) -> dict:
    """Five separate facts (Phase 5): they are never folded into one "complete your profile" message.
      profile          every record reviewed (confirmed or corrected), or the number still waiting
      starting_role    the current position is set (the latest main job is confirmed and matched to a Trace role)
      evidence         how the confirmed lines were read: categorised by the user, read by Trace; lines still pending
      plan             ready, or provisional with the reason (only lines waiting for review, or no starting role)
      needs_context    capabilities with related evidence that would be clearer with more context (never blocking)
    The research comparison's availability is reported by the research endpoint itself."""
    reviewed = ("user_confirmed", "user_corrected")
    records = [e for e in profile.employment_history] + list(profile.education) + list(profile.skills)
    evidence = [x for e in profile.employment_history for x in e.responsibilities + e.achievements + e.other_evidence]
    pending = sum(1 for r in records + evidence if r.provenance.review_status == "pending_review")
    live = [x for x in evidence if x.provenance.review_status in reviewed]
    by_you = sum(1 for x in live if x.area_classification is not None and not x.area_classification_is_stale)
    position = ((run.get("decision") or {}).get("current_position") or {})
    plan_state = ("provisional_pending_review" if rm["recommendation"]["kind"] == "resolve_first" and rm["resolve_first"]
                  else "provisional_no_starting_role" if position.get("status") != "known" else "ready")
    caps = rm.get("capabilities") or []
    return {"profile": {"complete": pending == 0, "pending": pending},
            "starting_role": {"set": position.get("status") == "known", "reason": position.get("reason")},
            "evidence": {"confirmed_lines": len(live), "categorised_by_you": by_you, "read_by_trace": len(live) - by_you},
            "plan": {"state": plan_state},
            "needs_context": [c["key"] for c in caps if c["state"] == "needs_context"]}


def describe_move(current: Optional[dict], target: Optional[dict]) -> dict:
    """How the target relates to the confirmed current role, for the plan's wording only (Phase 5, D51). Any valid target
    is allowed: a lateral move, a step up or a step back is the user's decision, never a restriction or a ranking."""
    if not current or not target:
        return {"kind": "unknown", "text": None}
    if current["role_id"] == target["role_id"]:
        return {"kind": "same_role", "text": f"You're already a {target['title']}: this plan is about making that move "
                                             "somewhere new, such as another company or a different product area."}
    same_family = current.get("family") == target.get("family")
    if same_family and current.get("rank") is not None and target.get("rank") is not None:
        if target["rank"] > current["rank"]:
            return {"kind": "step_up", "text": f"A move up from {current['title']} to {target['title']}."}
        if target["rank"] < current["rank"]:
            return {"kind": "step_back", "text": f"A move from {current['title']} to {target['title']}, a role usually "
                                                 "described at an earlier level. That can make sense for a change of "
                                                 "company, domain or scope; the choice is yours."}
        return {"kind": "lateral", "text": f"A move between roles at a similar level: {current['title']} to {target['title']}."}
    return {"kind": "change_of_role", "text": f"A move from {current['title']} into {target['title']}."}


class PlanNotFound(RecordNotFound):
    """No saved plan for this target (or no such run for this user)."""


def _iso(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def eligibility_facts(profile, position, ownership_areas: Iterable[str], as_of: int,
                      field_aliases: Optional[Dict[str, dict]] = None) -> EligibilityFacts:
    """The reviewed profile as eligibility needs it. Only confirmed or corrected records count."""
    own = set(ownership_areas)
    jobs = []
    for e in profile.employment_history:
        if e.provenance.review_status not in REVIEWED:
            continue
        mapped = (e.role_family and e.role_family != "unknown" and not e.mapping_is_stale and e.mapping_status != "unresolved")
        ongoing = e.is_current_as_stated or e.end.precision == "ongoing"
        lines = list(e.responsibilities) + list(e.achievements)
        exposure = tuple(str(x.candidate_evidence_id) for x in lines
                         if x.provenance.review_status in REVIEWED and x.area_classification and not x.area_classification_is_stale
                         and own & set(x.area_classification))
        open_ = tuple(str(x.candidate_evidence_id) for x in lines
                      if x.provenance.review_status == "pending_review"
                      or (x.provenance.review_status in REVIEWED and (x.area_classification is None or x.area_classification_is_stale)))
        jobs.append(Job(record_id=str(e.candidate_employment_id), title=e.title_raw, role_family=e.role_family if mapped else None,
                        start_lo=e.start.lo, start_hi=e.start.hi, end_lo=as_of if ongoing else e.end.lo,
                        end_hi=as_of if ongoing else e.end.hi, counts_as_experience=e.counts_as_full_time_experience is not False,
                        pattern_uncertain=(getattr(e, "employment_type", None) in UNCERTAIN_PATTERN
                                           and e.counts_as_full_time_experience is None),
                        exposure_records=exposure, open_records=open_))
    quals = []
    for d in profile.education:
        if d.provenance.review_status in REVIEWED:
            level, qtype = interpret_qualification(d.qualification_level, d.qualification_title_raw)
            f = (field_aliases or {}).get(normalise_field(d.field_of_study)) if d.field_of_study else None
            quals.append(Qualification(level=level, qtype=qtype, field_code=f["code"] if f else None,
                                       field_category=f["category"] if f else None, field_label=f["label"] if f else None,
                                       field_raw=d.field_of_study))
    current = position.role_family if position.status == "known" else None
    return EligibilityFacts(jobs=tuple(jobs), current_role_family=current, qualifications=tuple(quals),
                            current_role_id=position.role_id if position.status == "known" else None)


def posting_views(postings: List[dict], facts: EligibilityFacts, labels: Dict[str, str]) -> List[dict]:
    """Assessment 2 (employer eligibility) per posting, exactly as plans store it."""
    out = []
    for p in postings:
        current = p["effective_status"] == "current"
        out.append({
            "source_key": p["source_key"], "company_name": p["company_name"], "role_title": p["role_title"], "url": p["source_url"],
            "attribution": p["attribution"], "last_verified_at": _iso(p["last_verified_at"]), "effective_status": p["effective_status"],
            "is_current_opportunity": current,
            "evidence_use": ("current_opportunity" if current else "requires_revalidation"
                             if p["effective_status"] == "current_check_required" else "historical_eligibility_evidence"),
            "entry_background_stance": p["entry_background_stance"], "entry_quote": p["entry_quote"], "entry_locator": p["entry_locator"],
            "accepted_backgrounds": list(p["accepted_backgrounds"]), "excluded_backgrounds": list(p["excluded_backgrounds"]),
            "eligibility": posting_eligibility(p, facts, labels)})
    return out


class TransitionPlanService:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn
        self._profiles = CandidateProfileService(conn)
        self._capability = CapabilityService(conn)

    def build(self, ctx: OwnerContext, sequencing, target_role_id: str, contexts: Iterable[str], evidence_mode: str,
              at: Optional[datetime] = None, portfolio_scope: bool = False) -> dict:
        """The plan payload, before saving. Deterministic for the same profile, knowledge and time."""
        at = at or datetime.now(timezone.utc)
        assessment = self._capability.capability_assessment(ctx, target_role_id, contexts, at=at)   # 422 unless PM / Senior PM
        profile = self._profiles.get_profile(ctx)
        position = self._profiles.current_position(ctx, sequencing)
        observed = []
        if position.status == "known":
            result = self._profiles.pathway_result(ctx, sequencing, target_role_id, evidence_mode=evidence_mode)
            for r in result.routes:
                observed.append({"route_id": r.route_id, "route": [x.role_id for x in r.route], "evidence_class": r.observed.evidence_class,
                                 "observation_count": r.observed.observation_count, "is_synthetic_test_data": r.observed.is_synthetic_test_data})
        with owner_session(self._conn, ctx.owner_user_id):
            repo = TransitionRepository(self._conn)
            target = repo.target(target_role_id)
            bridges = repo.bridges(BRIDGE_CATALOGUE, target_role_id)
            templates = repo.templates(ACTION_TEMPLATES)
            postings = repo.postings(SOURCE_INVENTORY, target_role_id, at, ELIGIBILITY_RULES)
            field_aliases = repo.education_fields()
            source_ids = repo.source_ids([SOURCE_INVENTORY, BRIDGE_CATALOGUE])
            ownership = repo.ownership_areas()
            labels = KnowledgeRepository(self._conn).labels()["family"]
        as_of = at.year * 12 + at.month - 1
        facts = eligibility_facts(profile, position, ownership, as_of, field_aliases)
        postings_assessed = posting_views(postings, facts, labels)
        roles = [{"record_id": j.record_id, "title": j.title, "role_family": j.role_family} for j in facts.jobs if j.role_family]
        for b in bridges:
            b["source_keys"] = list(b["source_keys"])
        senior = assessment["target_level"] == "senior_pm"
        support = direct_support(assessment["requirements"], senior=senior, portfolio_scope=portfolio_scope and senior)
        pm_plan = direct_support(assessment["requirements"], senior=False) if senior else None
        findings = evaluate_bridges(bridges, assessment["requirements"], roles, position.role_id if position.status == "known" else None,
                                    support, pm_plan)
        decision = decide(support, findings, {i["key"]: i["title"] for i in assessment["requirements"]})
        for i in assessment["requirements"]:
            for s in i["sources"]:
                s["last_verified_at"] = _iso(s["last_verified_at"])
        return compose(assessment=assessment, support=support, findings=findings, decision=decision,
                       builder=ActionBuilder(templates, SOURCE_INVENTORY),
                       target={"role_id": target["role_id"], "title": target["canonical_title"], "role_family": target["role_family"]},
                       position={"status": position.status, "role_id": position.role_id, "role_family": position.role_family,
                                 "reason": position.reason},
                       postings=postings_assessed, observed=observed, data_mode=evidence_mode, versions=VERSIONS,
                       profile_version=profile_version(profile), source_ids=source_ids, scope_portfolio=portfolio_scope and senior)

    def preference_contexts(self, ctx: OwnerContext, target_role_id: str) -> List[str]:
        """Product contexts added by the user's "where would you like to move" answer (D51): only kinds of company that
        the curated role knowledge defines requirements for."""
        from app.services.move_preferences import MovePreferenceService, contexts_for
        try:
            return contexts_for(MovePreferenceService(self._conn).get(ctx, target_role_id))
        except RecordNotFound:
            return []

    def create_plan(self, ctx: OwnerContext, sequencing, target_role_id: str, contexts: Iterable[str], evidence_mode: str,
                    portfolio_scope: bool = False) -> dict:
        contexts = sorted(set(contexts) | set(self.preference_contexts(ctx, target_role_id)))
        payload = self.build(ctx, sequencing, target_role_id, contexts, evidence_mode, portfolio_scope=portfolio_scope)
        payload["candidate_profile_id"] = str(ctx.candidate_profile_id)
        with owner_session(self._conn, ctx.owner_user_id):
            run_id = TransitionRepository(self._conn).record(payload)
        return self.plan(ctx, run_id)

    def plan(self, ctx: OwnerContext, run_id: UUID) -> dict:
        with owner_session(self._conn, ctx.owner_user_id):
            run = TransitionRepository(self._conn).run(run_id)
        if run is None:
            raise PlanNotFound("no saved plan with this id")
        return run

    def latest(self, ctx: OwnerContext, target_role_id: str) -> dict:
        with owner_session(self._conn, ctx.owner_user_id):
            run_id = TransitionRepository(self._conn).latest_run_id(target_role_id)
        if run_id is None:
            raise PlanNotFound("no saved plan for this target yet")
        return self.plan(ctx, run_id)

    # ------------------------------------------------------------------ employer research (outside the saved plan)
    def researched_employers(self, ctx: OwnerContext) -> List[dict]:
        """Employers with job-posting research in Trace's source inventory, and the target roles their postings cover."""
        with owner_session(self._conn, ctx.owner_user_id):
            rows = TransitionRepository(self._conn).researched_employers(SOURCE_INVENTORY)
        return [{"company_name": r["company_name"], "role_ids": sorted(r["role_ids"] or [])} for r in rows]

    def employer_postings(self, ctx: OwnerContext, sequencing, target_role_id: str, at: Optional[datetime] = None) -> List[dict]:
        """Assessment 2 for every researched posting for this target, evaluated now against the confirmed profile. Never
        saved and never read by the plan: company research stays separate from the transition decision."""
        at = at or datetime.now(timezone.utc)
        profile = self._profiles.get_profile(ctx)
        position = self._profiles.current_position(ctx, sequencing)
        with owner_session(self._conn, ctx.owner_user_id):
            repo = TransitionRepository(self._conn)
            postings = repo.postings(SOURCE_INVENTORY, target_role_id, at, ELIGIBILITY_RULES)
            field_aliases = repo.education_fields()
            ownership = repo.ownership_areas()
            labels = KnowledgeRepository(self._conn).labels()["family"]
        facts = eligibility_facts(profile, position, ownership, at.year * 12 + at.month - 1, field_aliases)
        views = posting_views(postings, facts, labels)
        for v, p in zip(views, postings):
            v["location"] = p["location"]
        return views

    # ------------------------------------------------------------------ saved-plan freshness
    def freshness(self, ctx: OwnerContext, run: dict, evidence_mode: str, at: Optional[datetime] = None) -> dict:
        """Whether a saved run still reflects every current decision input: the confirmed profile, the reference data mode,
        each knowledge and rule version, and each posting's status. The run's own constraints (target, stated portfolio
        scope, contexts) are carried over by whoever re-plans. Target-company preferences are not decision inputs."""
        at = at or datetime.now(timezone.utc)
        profile = self._profiles.get_profile(ctx)
        with owner_session(self._conn, ctx.owner_user_id):
            repo = TransitionRepository(self._conn)
            _, taxonomy_version = KnowledgeRepository(self._conn).area_versions()
            postings = repo.postings(SOURCE_INVENTORY, run["target_role_id"], at, ELIGIBILITY_RULES)
        current = {"profile_version": profile_version(profile), "data_mode": evidence_mode, "engine_version": ENGINE_VERSION,
                   "knowledge_pack": PACK, "source_inventory": SOURCE_INVENTORY, "taxonomy_version": taxonomy_version, **VERSIONS}
        changed = [k for k, v in current.items() if run.get(k) != v]
        wanted = set(self.preference_contexts(ctx, run["target_role_id"]))
        if not wanted <= set(((run.get("constraints_json") or run.get("constraints") or {}).get("contexts")) or []):
            changed.append("move_preference")
        stored = {p["source_key"]: p["effective_status"] for p in (run.get("eligibility") or {}).get("postings", [])}
        now = {p["source_key"]: p["effective_status"] for p in postings}
        if stored != now:
            changed.append("posting_status")
        return {"current": not changed, "changed": changed}

    # ------------------------------------------------------------------ the career transition roadmap (redesign)
    def roadmap(self, ctx: OwnerContext, run_id: UUID, today: Optional[date] = None) -> dict:
        """The roadmap for one of the caller's saved runs: the stored plan, the records eligible NOW (provenance), the
        approved role knowledge and the user's own goal for that target. Computed at read time; nothing is saved, the
        run is never altered and no new run is created."""
        from app.services.transition.provenance import evidence_index
        from app.services.transition.roadmap import build_roadmap
        run = self.plan(ctx, run_id)
        profile = self._profiles.get_profile(ctx)
        with owner_session(self._conn, ctx.owner_user_id):
            k = KnowledgeRepository(self._conn)
            names, labels, reqs = k.skill_names(), k.labels(), k.requirements(run["knowledge_pack"])
            current_id = ((run.get("decision") or {}).get("current_position") or {}).get("role_id")
            levels = TransitionRepository(self._conn).role_levels([x for x in (current_id, run["target_role_id"]) if x])
        goal = self.goal(ctx, run["target_role_id"])
        evidence = evidence_index(profile, names, labels["area"], labels["family"])
        requirements = {r["requirement_key"]: {"description": r["description"], "transfer_rationale": r["transfer_rationale"]}
                        for r in reqs}
        out = build_roadmap(run, evidence, requirements, goal, today or date.today())
        out["move"] = describe_move(levels.get(current_id) if current_id else None, levels.get(run["target_role_id"]))
        from app.services.transition.roadmap import next_actions
        out["next_actions"] = next_actions(out["capabilities"], out["move"], out["target_role"], (run.get("decision") or {}).get("rule"),
                                           out["recommendation"]["route"])
        if out["move"]["kind"] == "same_role" and out["recommendation"]["kind"] == "direct":
            out["recommendation"]["headline"] = \
                f"Make your next move as a {out['target_role']}, somewhere new."
        out["readiness"] = readiness(profile, run, out)
        return out

    def goal(self, ctx: OwnerContext, target_role_id: str) -> Optional[dict]:
        with owner_session(self._conn, ctx.owner_user_id):
            row = self._conn.execute("select timeframe, target_date, weekly_hours, checkpoint_dates, goal_set_on, updated_at "
                                     "from trace_plan.my_transition_goal(%s)", (target_role_id,)).fetchone()
        if row is None:
            return None
        return {"timeframe": row[0], "target_date": row[1], "weekly_hours": row[2], "checkpoint_dates": row[3] or {},
                "goal_set_on": row[4], "updated_at": row[5]}

    def set_goal(self, ctx: OwnerContext, target_role_id: str, timeframe: Optional[str], target_date: Optional[date],
                 weekly_hours: Optional[str], checkpoint_dates: Dict[str, str]) -> dict:
        """The user's planning goal for a target. Never a plan input: no run is created and no decision changes."""
        from psycopg.types.json import Jsonb
        from app.services.candidate_profile.repository import InvalidReference
        with owner_session(self._conn, ctx.owner_user_id):
            if TransitionRepository(self._conn).target(target_role_id) is None:
                raise InvalidReference("unknown target role")
            try:
                self._conn.execute("select * from trace_plan.set_transition_goal(%s, %s, %s, %s, %s)",
                                   (target_role_id, timeframe, target_date, weekly_hours, Jsonb(checkpoint_dates or {})))
            except psycopg.errors.InvalidParameterValue as exc:
                raise InvalidReference(str(exc).split("\n")[0]) from None
        return self.goal(ctx, target_role_id)

    def latest_any(self, ctx: OwnerContext) -> dict:
        """Where a returning user lands: their most recent saved plan, whatever the target."""
        with owner_session(self._conn, ctx.owner_user_id):
            row = TransitionRepository(self._conn).latest_any()
        if row is None:
            raise PlanNotFound("no saved plan yet")
        return row
