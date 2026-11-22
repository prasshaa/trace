"""Deterministic target-role capability assessment (product Phase 3; decisions D11, D13, D19, D25).

This is ASSESSMENT 1 of 3: target-role capability. It never includes employer-specific application eligibility
(assessment 2: per posting, attributed and dated) or personal transition feasibility (assessment 3: per pathway,
Phase 4). There is no score, no weighting, no readiness percentage and no probability.

For each canonical requirement of the approved knowledge pack, the user's REVIEWED profile (records the user confirmed
or corrected) gives exactly one state, with the records behind it:

  demonstrated   reviewed evidence the user classified with a DIRECT responsibility area of the requirement, or a
                 confirmed skill in its direct skill set (basis shown: classified_evidence or confirmed_skill)
  transferable   no direct support, but a credible connection through the curated mapping: a transferable area, a
                 transferable skill, or a reviewed role in a transferable role family (role_history). The mapping's
                 rationale is returned with it
  gap            ONLY when the requirement allows absence to count (gap_basis reviewed_classified_evidence) AND the
                 user's relevant information has been reviewed: at least one reviewed evidence record, none pending
                 review, every reviewed record classified (areas or the explicit "None of these areas", not stale),
                 every classification made against a taxonomy version that already had this requirement's areas and
                 scope questions (Phase 3.1), and every relevant scope question answered. None maps to the requirement
  unknown        everything else, with the reason: nothing reviewed yet, evidence pending review, evidence not yet
                 classified, or absence that can never support a gap (not_inferable_from_absence)

Context-dependent requirements apply only when the user selected a matching product context; otherwise they are
returned as not_applicable (never a gap). Senior PM differentiators apply only to a Senior PM target.

Unreviewed (pending) and rejected records never count. The user's classification is their own assertion (basis
candidate_selected) and always takes precedence. Phase 5 (D50): a reviewed line the user hasn't categorised is read by
`interpret.py` (deterministic, local; cue and reason kept), so confirmed experience counts without exact wording. A confirmed
skill without a taxonomy id (typed by the user) counts only on an EXACT normalised match with a taxonomy skill name or
curated alias (read time; nothing stored changes).
"""
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

REVIEWED = ("user_confirmed", "user_corrected")
CONTEXTS = ("commercial_product", "growth_analytics", "platform_api", "data_product", "regulated_domain", "ai_product")

# --------------------------------------------------------------------------- D11: MBA at read time
_TYPES = [(re.compile(p, re.I), t) for p, t in (
    (r"\bmba\b|pgdm|\bpgp\b", "MBA"), (r"m\.?\s?tech", "MTech"), (r"\bm\.?\s?sc\b|\bmsc\b", "MSc"), (r"\bmca\b", "MCA"),
    (r"m\.?\s?com", "MCom"), (r"b\.?\s?tech", "BTech"), (r"\bb\.?\s?e\.?\b", "BE"), (r"\bb\.?\s?sc\b|\bbsc\b", "BSc"),
    (r"b\.?\s?com", "BCom"), (r"\bbba\b", "BBA"), (r"\bbca\b", "BCA"), (r"ph\.?\s?d|doctorate", "PhD"), (r"diploma", "Diploma"))]


def interpret_qualification(stored_level: Optional[str], title_raw: Optional[str]) -> Tuple[str, Optional[str]]:
    """(level, qualification type) read from a STORED record, which is never rewritten. An MBA is a type at master's
    level (D11): a stored level 'mba' reads as level 'master', type 'MBA'. The type comes from the stated title when
    recognisable; otherwise None (unknown, never guessed)."""
    level = "master" if stored_level == "mba" else (stored_level or "unknown")
    qtype = "MBA" if stored_level == "mba" else next((t for rx, t in _TYPES if title_raw and rx.search(title_raw)), None)
    return level, qtype


# --------------------------------------------------------------------------- inputs and outputs (plain data)
@dataclass(frozen=True)
class Requirement:
    key: str
    requirement_class: str
    applies_to_level: str
    title: str
    description: str
    level_notes: Dict[str, str]
    contexts: Tuple[str, ...]
    direct_areas: Tuple[str, ...]
    transferable_areas: Tuple[str, ...]
    direct_skills: Tuple[str, ...]
    transferable_skills: Tuple[str, ...]
    transferable_role_families: Tuple[str, ...]
    transfer_rationale: Optional[str]
    gap_basis: str
    # Phase 3.1: {"<area>": "<qualifier>"}: evidence in <area> counts for this requirement only with a YES answer
    area_conditions: Dict[str, str] = field(default_factory=dict)
    # pm-v1.2 (D31, D35): a decision-rights requirement; and conditioned direct areas that still count as Transferable
    # when the user answered the condition "no" ({"data_analysis": "transferable"}). Unanswered never counts.
    decision_rights: bool = False
    area_condition_fallback: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AreaInfo:
    version: int                              # max(introduced_in_version, definition_version)
    qualifier_versions: Dict[str, int]        # qualifier -> the version that introduced it


@dataclass
class Support:
    kind: str                         # evidence | skill | employment
    record_id: str                    # the user's own record handle
    matched: List[str]                # area, skill or role-family codes that matched
    excerpt: Optional[str] = None     # the user's own evidence text (shortened), or the skill or role title
    read_by: str = "you"              # evidence: "you" (the user's categorisation) or "trace" (Trace's reading, D50)
    cue: Optional[str] = None         # Trace's reading: the words of the line it rests on
    why: Optional[str] = None         # Trace's reading: what the line shows, in plain words


@dataclass
class Outcome:
    key: str
    state: str                        # demonstrated | transferable | gap | unknown | not_applicable
    basis: Optional[str] = None       # classified_evidence | confirmed_skill | role_history | reviewed_classified_evidence
    reason: Optional[str] = None      # for unknown and not_applicable (and gap: why it is defensible)
    supporting: List[Support] = field(default_factory=list)
    transfer_rationale: Optional[str] = None
    blocking_records: List[str] = field(default_factory=list)   # unknown: the user's records that must be reviewed first
    needs_context: Optional[str] = None   # related evidence exists but doesn't show the whole capability: what to add


@dataclass
class Coverage:
    reviewed_evidence: int
    pending_evidence: int
    unclassified_reviewed: int
    complete: bool
    reason: Optional[str]
    blocking_records: List[str] = field(default_factory=list)


def _short(text: str, n: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def name_key(text: Optional[str]) -> str:
    return " ".join("".join(ch if ch.isalnum() else " " for ch in (text or "").lower()).split())


def reviewed_records(profile, skill_names: Optional[Dict[str, Tuple[str, str]]] = None) -> dict:
    """Only reviewed records count. Evidence is taken from employments, activities and unattached evidence.

    A reviewed evidence record is CLASSIFIED when the user chose areas or "None of these areas" and the text has not
    changed since (not stale). Unclassified, cleared and stale records are not."""
    evidence = [x for e in profile.employment_history for x in e.responsibilities + e.achievements + e.other_evidence]
    evidence += [x for acts in profile.career_activities.values() for a in acts for x in a.achievements + a.other_evidence]
    evidence += list(profile.unattached_evidence)
    reviewed = [x for x in evidence if x.provenance.review_status in REVIEWED]
    pending = [x for x in evidence if x.provenance.review_status == "pending_review"]
    classified = {x.candidate_evidence_id: (set(x.area_classification), getattr(x, "area_classification_taxonomy_version", None) or 1,
                                            getattr(x, "area_qualifiers", None) or {})
                  for x in reviewed if x.area_classification is not None and not x.area_classification_is_stale}
    # Phase 5 (D50): Trace's reading of every reviewed line the user hasn't categorised (or whose text changed since).
    # The user's own categorisation, including "None of these areas", always takes precedence.
    from app.services.capability.interpret import interpret
    read = {x.candidate_evidence_id: interpret(x.evidence_text, getattr(x, "evidence_type", None))
            for x in reviewed if x.candidate_evidence_id not in classified}
    names = skill_names or {}
    skills = []                                   # (record id, skill id, name)
    for s in profile.skills:
        if s.provenance.review_status not in REVIEWED:
            continue
        if s.taxonomy:
            skills.append((s.candidate_skill_id, s.taxonomy.skill_id, s.taxonomy.canonical_skill))
        elif name_key(s.skill_text_raw) in names:
            sid, canonical = names[name_key(s.skill_text_raw)]
            skills.append((s.candidate_skill_id, sid, canonical))
    roles = [e for e in profile.employment_history if e.provenance.review_status in REVIEWED and e.role_family
             and e.role_family != "unknown" and not e.mapping_is_stale and e.mapping_status != "unresolved"]
    unclassified = [x for x in reviewed if x.candidate_evidence_id not in classified]
    # Every reviewed line is now read (by the user's categorisation or Trace's reading), so only lines still waiting for
    # the user's review keep a requirement from being assessed. `unclassified` still counts lines the USER hasn't
    # categorised, for the profile page; it no longer blocks an assessment.
    if not reviewed:
        reason, blocking = "no_reviewed_evidence", []
    elif pending:
        reason, blocking = "evidence_pending_review", [str(x.candidate_evidence_id) for x in pending]
    else:
        reason, blocking = None, []
    return {"reviewed": reviewed, "classified": classified, "read": read, "skills": skills, "roles": roles,
            "coverage": Coverage(len(reviewed), len(pending), len(unclassified), reason is None, reason, blocking)}


def required_version(req: Requirement, taxonomy: Optional[Dict[str, AreaInfo]]) -> int:
    """The taxonomy version a classification must have been made against before its ABSENCE can support a Gap for
    this requirement: the newest of the requirement's areas and of the scope questions it depends on."""
    if not taxonomy:
        return 1
    versions = [taxonomy[a].version for a in (*req.direct_areas, *req.transferable_areas) if a in taxonomy]
    versions += [taxonomy[a].qualifier_versions.get(q, 1) for a, q in req.area_conditions.items() if a in taxonomy]
    return max(versions or [1])


def assess_requirement(req: Requirement, facts: dict, contexts: Set[str],
                       taxonomy: Optional[Dict[str, AreaInfo]] = None) -> Outcome:
    """One requirement's state, with the records behind it. Order of evidence (Phase 5, D50):

      demonstrated  the user's categorisation in a direct area, then Trace's CLEAR reading of a direct area (with its
                    scope question answered YES where the requirement needs one), then a confirmed skill;
      transferable  the user's categorisation in a transferable area; Trace's reading that is related but doesn't show
                    the whole capability (a partial reading, or a scope question the text doesn't answer: needs_context
                    says what to add); a clear reading in a transferable area; the D35 fallback; a transferable skill;
                    a reviewed role in a transferable family;
      gap           nothing relevant in any reviewed line, every line has been read, and absence is meaningful for
                    this requirement. Shown to the user as "not yet demonstrated in the available evidence", never as
                    proof they lack the experience;
      unknown       lines still waiting for the user's review, absence that can never support a gap, or the user's own
                    categorisation made before this requirement's areas or questions existed.
    """
    if req.requirement_class == "context_dependent" and not (set(req.contexts) & contexts):
        return Outcome(req.key, "not_applicable", reason="not_in_selected_context")
    reviewed = {x.candidate_evidence_id: x for x in facts["reviewed"]}
    read = facts.get("read", {})

    def counts(area: str, quals: dict) -> bool:
        q = req.area_conditions.get(area)
        return q is None or (quals.get(area) or {}).get(q) is True        # a condition needs an explicit YES

    def by_areas(areas: Sequence[str]) -> List[Support]:
        want = set(areas)
        out = []
        for eid, (got, _, quals) in facts["classified"].items():
            hit = sorted(a for a in got & want if counts(a, quals))
            if hit:
                out.append(Support("evidence", str(eid), hit, _short(reviewed[eid].evidence_text)))
        return out

    def by_reading(areas: Sequence[str], clear_only: bool = True, conditioned: bool = True) -> List[Support]:
        want = set(areas)
        out = []
        for eid, r in read.items():
            pool = r.clear if clear_only else {**r.partial, **r.clear}
            hit = sorted(a for a in set(pool) & want if not conditioned or counts(a, r.qualifiers))
            if hit:
                cue, why = pool[hit[0]]
                out.append(Support("evidence", str(eid), hit, _short(reviewed[eid].evidence_text), read_by="trace", cue=cue, why=why))
        return out

    def by_skills(ids: Sequence[str]) -> List[Support]:
        want = set(ids)
        return [Support("skill", str(rid), [sid], name) for rid, sid, name in facts["skills"] if sid in want]

    direct = by_areas(req.direct_areas)
    if direct:
        return Outcome(req.key, "demonstrated", basis="classified_evidence", supporting=direct)
    direct = by_reading(req.direct_areas)
    if direct:
        return Outcome(req.key, "demonstrated", basis="interpreted_evidence", supporting=direct)
    direct = by_skills(req.direct_skills)
    if direct:
        return Outcome(req.key, "demonstrated", basis="confirmed_skill", supporting=direct)

    def by_fallback() -> List[Support]:
        out = []
        for eid, (got, _, quals) in facts["classified"].items():
            hit = sorted(a for a in got if req.area_condition_fallback.get(a) == "transferable"
                         and (quals.get(a) or {}).get(req.area_conditions.get(a)) is False)      # an explicit NO
            if hit:
                out.append(Support("evidence", str(eid), hit, _short(reviewed[eid].evidence_text)))
        return out

    transferable_evidence = by_areas(req.transferable_areas)
    if transferable_evidence:
        return Outcome(req.key, "transferable", basis="classified_evidence", supporting=transferable_evidence,
                       transfer_rationale=req.transfer_rationale)
    # Trace's reading that relates to a DIRECT area but doesn't show the whole capability: needs more context.
    related = [s for s in by_reading(req.direct_areas, clear_only=False, conditioned=False)]
    if related:
        open_q = sorted({req.area_conditions[a] for s_ in related for a in s_.matched if a in req.area_conditions})
        return Outcome(req.key, "transferable", basis="interpreted_evidence", supporting=related,
                       transfer_rationale=req.transfer_rationale,
                       needs_context=("scope_question:" + ",".join(open_q)) if open_q else "partial_reading")
    for basis, found in (("interpreted_evidence", by_reading(req.transferable_areas)),
                         ("classified_evidence", by_fallback()),
                         ("confirmed_skill", by_skills(req.transferable_skills)),
                         ("role_history", [Support("employment", str(e.candidate_employment_id), [e.role_family], e.title_raw)
                                           for e in facts["roles"] if e.role_family in set(req.transferable_role_families)])):
        if found:
            return Outcome(req.key, "transferable", basis=basis, supporting=found, transfer_rationale=req.transfer_rationale)
    # No support. A Gap only where absence is meaningful AND every reviewed line has been read.
    cov: Coverage = facts["coverage"]
    if req.gap_basis == "not_inferable":
        return Outcome(req.key, "unknown", reason="not_inferable_from_absence")
    if not cov.complete:
        return Outcome(req.key, "unknown", reason=cov.reason, blocking_records=list(cov.blocking_records))
    need = required_version(req, taxonomy)
    outdated = [str(eid) for eid, (_, version, _) in facts["classified"].items() if version < need]
    if outdated:            # categorised by the user before this requirement's areas or questions existed: never a Gap
        return Outcome(req.key, "unknown", reason="classified_before_taxonomy_update", blocking_records=outdated)
    unanswered = [str(eid) for eid, (got, _, quals) in facts["classified"].items()
                  for a, q in req.area_conditions.items() if a in got and (quals.get(a) or {}).get(q) is None]
    if unanswered:          # an unanswered scope question is never a "no"
        return Outcome(req.key, "unknown", reason="scope_question_not_answered", blocking_records=sorted(set(unanswered)))
    # The reason says how the lines were read: all by the user (as before), or some by Trace's reading.
    reason = "read_without_a_match" if read else "reviewed_and_classified_without_a_match"
    return Outcome(req.key, "gap", basis="reviewed_classified_evidence", reason=reason)


def assess(profile, requirements: Iterable[Requirement], target_level: str, contexts: Iterable[str],
           skill_names: Optional[Dict[str, Tuple[str, str]]] = None,
           taxonomy: Optional[Dict[str, AreaInfo]] = None) -> Tuple[List[Outcome], Coverage]:
    ctx = set(contexts)
    unknown = ctx - set(CONTEXTS)
    if unknown:
        raise ValueError(f"unknown contexts: {sorted(unknown)}")
    facts = reviewed_records(profile, skill_names)
    applicable = [r for r in requirements if r.applies_to_level == "all" or r.applies_to_level == target_level]
    return [assess_requirement(r, facts, ctx, taxonomy) for r in applicable], facts["coverage"]
