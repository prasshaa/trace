"""Personalised pathway result (M6): read-only and deterministic.

Combines, without recomputing any of them:
  * the candidate's current position (derive_current_position) and sequenced history (career_history);
  * observed routes from the current role to a PM target (M5 reference evidence, synthetic test data);
  * the safely disclosed experience recorded before the target on each route (M6 route step experience);
  * the candidate's own classified, reviewed evidence (M6 classification).

Three kinds of content are kept apart in the result: OBSERVED evidence (reference data, labelled with its data
mode), the candidate's FIT (from their own reviewed records only) and GENERATED guidance (fixed templates). Nothing
here is a probability, a forecast or a requirement; route frequency never ranks anything. Missing candidate
evidence is reported as "not recorded in your reviewed profile", never as something the candidate lacks.

Everything in this module is pure: build_pathway_result takes plain inputs and is tested without a database.
"""
from typing import Dict, Iterable, List, Literal, Optional, Sequence, Set, Tuple

from pydantic import BaseModel, ConfigDict

from app.services.candidate_profile.models import CandidateProfile, CurrentPosition, EvidenceRecord
from app.services.career_history import CandidateHistory
from app.services.reference_evidence.models import ReferenceEvidenceResult, RoleRow, RouteStepExperience

FitTier = Literal["aligned", "partial", "limited", "not_established"]
TIER_ORDER = {"aligned": 0, "partial": 1, "limited": 2, "not_established": 3}
REVIEWED = ("user_confirmed", "user_corrected")

ROLE_ONLY_NOTICE = "Role-only assessment: company-specific fit is not assessed."
INTERPRETATION_NOTE = (
    "Observed routes describe what reference profiles did. Fit compares the responsibility areas you classified on "
    "your reviewed evidence with areas recorded by most people observed on a route. Neither is a forecast of any "
    "outcome, and route frequency does not order the routes.")
UNAVAILABLE = [
    "Target-role requirements: not assessed in this route view; your Career Intelligence Report assesses them separately.",
    "Skills: not assessed (skills are not mapped to the taxonomy yet).",
    "Time on route: not shown (timelines are never shown for synthetic test data).",
    "Company-specific fit: not assessed.",
]
GUIDANCE_LABEL = "Generated guidance, not observed fact."


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------- result model
class SupportingEvidence(_Frozen):
    """One of the candidate's own reviewed, classified records that supports an area (no internal identifiers)."""
    responsibility_area: str
    employment_title: str
    employment_role_id: str
    evidence_type: str
    evidence_text: str


class CandidateEvidenceSummary(_Frozen):
    """What of the candidate's own evidence counts for fit, and how much was left out (and why)."""
    classified_areas: List[str]                      # areas on eligible evidence, sorted
    supporting_evidence: List[SupportingEvidence]
    excluded: Dict[str, int]                         # reason -> number of the candidate's own records


class AreaView(_Frozen):
    responsibility_area: str
    label: str
    is_pm_ownership_signal: bool
    profile_count: int                               # always at least the display threshold
    is_common: bool


class ObservedRouteEvidence(_Frozen):
    """OBSERVED: reference evidence for one route, as safely disclosed. No identifiers, raw counts or timelines."""
    data_mode: str
    is_synthetic_test_data: bool
    evidence_class: str
    experience_state: Literal["suppressed_small_cohort", "no_recorded_areas", "areas_withheld", "areas_disclosed"]
    observation_count: Optional[int] = None          # None when suppressed (fewer than the threshold)
    disclosed_areas: List[AreaView] = []
    areas_withheld: bool = False


FitLimitation = Literal["incomplete_user_profile", "insufficient_reference_evidence", "insufficient_route_evidence"]


class RouteFit(_Frozen):
    """CANDIDATE FIT, from the candidate's own eligible evidence only.

    limitations (Phase 8.1, additive): why fit could not be established, as distinct categories, so the user is told
    the actual cause: their own profile (no categorised responsibilities), the reference profiles (not enough recorded
    responsibility detail to compare), or the route (too few profiles). Both sides can apply at once. Empty when fit is
    established. Thresholds and tiers are unchanged."""
    tier: FitTier
    reason: str
    common_areas: List[str]
    matched_areas: List[str]
    supporting_evidence: List[SupportingEvidence]
    limitations: List[FitLimitation] = []


class AreaGap(_Frozen):
    responsibility_area: str
    label: str
    note: str = "Not recorded in your reviewed profile."


class StepGap(_Frozen):
    role_id: str
    canonical_title: str
    note: str = "A step on this observed route that is not present in your reviewed profile; not a prerequisite."


class NextStep(_Frozen):
    kind: Literal["area_gap", "route_step", "evidence"]
    text: str
    label: str = GUIDANCE_LABEL


class RouteRole(_Frozen):
    role_id: str
    canonical_title: str


class FamilyChange(_Frozen):
    """Decision D20, presentation only: the taxonomy names of the two role families a 'career_track_change' move
    crosses. A name is None when the taxonomy has none; the client then uses its fallback wording."""
    from_family: Optional[str] = None
    to_family: Optional[str] = None


def family_change(a: Optional[RoleRow], b: Optional[RoleRow], direction: Optional[str]) -> Optional[FamilyChange]:
    if direction != "career_track_change":
        return None
    return FamilyChange(from_family=a.role_family_label if a else None, to_family=b.role_family_label if b else None)


class PathwayRoute(_Frozen):
    rank: int                                        # unique position, for a stable display order
    # Phase 8.1, additive: routes with the same rank_group have identical meaningful ranking inputs (fit tier and the
    # number of intermediate roles not held); their order within the group is only the stable route-id order, not a
    # judgement. tied = the group has more than one route.
    rank_group: int = 0
    tied: bool = False
    # Phase 8.2, additive, presentation only: one entry per move (len(route) - 1). None = no reliable direction.
    step_directions: List[Optional[Literal["more_senior", "more_junior", "lateral", "career_track_change"]]] = []
    # D20, additive, presentation only: parallel to step_directions; set only where the direction is career_track_change.
    step_family_changes: List[Optional[FamilyChange]] = []
    route_id: str                                    # stable: role ids joined by ">"
    route: List[RouteRole]
    observed: ObservedRouteEvidence
    fit: RouteFit
    area_gaps: List[AreaGap]
    step_gaps: List[StepGap]
    next_steps: List[NextStep]


class PathwayResult(_Frozen):
    current_position: CurrentPosition
    target: RouteRole
    role_only_notice: str = ROLE_ONLY_NOTICE
    data_mode: Optional[str] = None
    is_synthetic_test_data: Optional[bool] = None
    evidence_notice: Optional[str] = None
    interpretation_note: str = INTERPRETATION_NOTE
    candidate_evidence: CandidateEvidenceSummary
    routes: List[PathwayRoute]
    next_steps: List[NextStep]                       # profile-level guidance (evidence and position)
    limitations: List[str]
    unavailable_assessments: List[str] = list(UNAVAILABLE)


# --------------------------------------------------------------------------- candidate evidence
def eligible_candidate_evidence(profile: CandidateProfile, history: CandidateHistory) -> CandidateEvidenceSummary:
    """Evidence that counts for fit: attached to an employment that is eligible and in the primary sequence;
    reviewed (confirmed or corrected); with a current (non-stale) candidate_selected classification. The legacy
    responsibility_area column, skills and titles are never used. Everything else is counted by reason."""
    eligible_jobs = {r.stated.employment_id for r in history.records if r.in_primary_sequence and r.is_transition_eligible}
    excluded: Dict[str, int] = {}
    supporting: List[SupportingEvidence] = []

    def skip(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    for _ in profile.unattached_evidence:
        skip("not_attached_to_an_employment")
    for acts in profile.career_activities.values():
        for a in acts:
            for _ in a.achievements + a.other_evidence:
                skip("not_attached_to_an_employment")
    for job in profile.employment_history:
        for x in job.responsibilities + job.achievements + job.other_evidence:
            reason = _exclusion(x, str(job.candidate_employment_id) in eligible_jobs)
            if reason:
                skip(reason)
                continue
            for area in x.area_classification:
                supporting.append(SupportingEvidence(responsibility_area=area, employment_title=job.title_raw,
                                                     employment_role_id=job.role_id, evidence_type=x.evidence_type,
                                                     evidence_text=x.evidence_text))
    supporting.sort(key=lambda s: (s.responsibility_area, s.employment_title, s.evidence_text))
    return CandidateEvidenceSummary(classified_areas=sorted({s.responsibility_area for s in supporting}),
                                    supporting_evidence=supporting, excluded=dict(sorted(excluded.items())))


def _exclusion(x: EvidenceRecord, job_eligible: bool) -> Optional[str]:
    status = x.provenance.review_status
    if status == "rejected":
        return "rejected"
    if status not in REVIEWED:
        return "pending_review"
    if not x.area_classification or x.area_classification_basis != "candidate_selected":
        return "not_classified"
    if x.area_classification_is_stale:
        return "classification_stale"
    if not job_eligible:
        return "employment_not_eligible"
    return None


# --------------------------------------------------------------------------- reference side
def common_areas(exp: Optional[RouteStepExperience], threshold: int = 5) -> List[str]:
    """Areas common on a route: the route is disclosed (at least the threshold of observations), the area itself is
    disclosed (at least the threshold of profiles) and recorded by strictly more than half of observation_count.
    Recomputed from the disclosed figures and checked against the service's own flag."""
    if exp is None or exp.state != "areas_disclosed" or exp.observation_count is None or exp.observation_count < threshold:
        return []
    out = []
    for a in exp.areas:
        common = a.profile_count >= threshold and 2 * a.profile_count > exp.observation_count
        if common != a.is_common:
            raise ValueError(f"inconsistent common flag for {a.responsibility_area} on {exp.route}")
        if common:
            out.append(a.responsibility_area)
    return sorted(out)


def _no_common_reason(exp: Optional[RouteStepExperience]) -> str:
    if exp is None or exp.state == "suppressed_small_cohort":
        return "reference_evidence_suppressed_small_cohort"
    if exp.state == "areas_withheld":
        return "reference_areas_withheld"
    if exp.state == "no_recorded_areas":
        return "reference_no_recorded_areas"
    return "no_common_area_on_route"


# --------------------------------------------------------------------------- fit, gaps, ranking
def fit_for_route(common: Sequence[str], exp: Optional[RouteStepExperience], candidate: CandidateEvidenceSummary) -> RouteFit:
    profile_gap: List[FitLimitation] = [] if candidate.classified_areas else ["incomplete_user_profile"]
    if not common:
        reason = _no_common_reason(exp)
        side: FitLimitation = ("insufficient_route_evidence" if reason == "reference_evidence_suppressed_small_cohort"
                               else "insufficient_reference_evidence")
        return RouteFit(tier="not_established", reason=reason, common_areas=[], matched_areas=[], supporting_evidence=[],
                        limitations=[side, *profile_gap])
    if not candidate.classified_areas:
        return RouteFit(tier="not_established", reason="no_eligible_classified_evidence", common_areas=list(common),
                        matched_areas=[], supporting_evidence=[], limitations=profile_gap)
    matched = sorted(set(common) & set(candidate.classified_areas))
    tier: FitTier = "aligned" if len(matched) == len(common) else "partial" if matched else "limited"
    reason = {"aligned": "all_common_areas_recorded", "partial": "some_common_areas_recorded",
              "limited": "no_common_area_recorded"}[tier]
    return RouteFit(tier=tier, reason=reason, common_areas=list(common), matched_areas=matched,
                    supporting_evidence=[s for s in candidate.supporting_evidence if s.responsibility_area in matched])


def route_step_gaps(route: Sequence[str], held_roles: Set[str]) -> List[str]:
    """Intermediate roles (neither the first nor the last step) not in the candidate's eligible, mapped history,
    in route order, each once."""
    out: List[str] = []
    for role in route[1:-1]:
        if role not in held_roles and role not in out:
            out.append(role)
    return out


def move_direction(a: Optional[RoleRow], b: Optional[RoleRow]) -> Optional[str]:
    """The agreed seniority-direction policy (Phase 8.2), presentation only; never used for discovery or ranking.

    Different canonical role families: 'career_track_change' (levels of different families are not comparable).
    Same family: a direction only when both roles have a reliable level, i.e. the family defines more than one
    seniority level in the taxonomy (product management does; families whose roles all default to 'mid' do not, so
    their 'mid' is a fallback) and both ranks exist (founder and unknown have none). Otherwise None: no label.
    Titles are never read; aliases already resolve to role ids, so they cannot change the result."""
    if a is None or b is None or "unknown" in (a.role_family, b.role_family):
        return None
    if a.role_family != b.role_family:
        return "career_track_change"
    if not (a.family_has_levels and b.family_has_levels) or a.seniority_rank is None or b.seniority_rank is None:
        return None
    return "more_senior" if b.seniority_rank > a.seniority_rank else "more_junior" if b.seniority_rank < a.seniority_rank else "lateral"


def rank_key(tier: str, step_gaps: Sequence[str], route_id: str) -> Tuple[int, int, str]:
    """Fit tier, then fewer intermediate roles not in the reviewed history, then the route id. Never frequency."""
    return (TIER_ORDER[tier], len(step_gaps), route_id)


# --------------------------------------------------------------------------- generated guidance (fixed templates)
AREA_GUIDANCE = {
    "outcome_ownership": "If you have owned measurable product outcomes, add and review evidence of that work. Otherwise, look for an "
                         "opportunity to own a measurable outcome.",
    "roadmap_ownership": "If you have owned or set a roadmap or product strategy, add and review evidence of that work. Otherwise, look "
                         "for an opportunity to own a roadmap for a product area.",
    "prioritisation_backlog": "If you have owned backlog, release or feature prioritisation, add and review evidence of that work. "
                              "Otherwise, look for an opportunity to own prioritisation decisions.",
    "product_discovery": "If you have run product or customer discovery that shaped what was built, add and review evidence of that work. "
                         "Otherwise, look for an opportunity to lead discovery.",
}


def area_guidance(area: str, label: str) -> str:
    return AREA_GUIDANCE.get(area, f"If you have done work in {label.lower()}, add and review evidence of it and classify it. "
                                   f"Otherwise, look for an opportunity to take on {label.lower()} work.")


def step_guidance(title: str) -> str:
    return (f"Observed routes here pass through {title}. If you have held a {title} role, add it to your profile and review it. "
            f"Otherwise, consider whether a {title} role is a step you want to explore.")


def evidence_guidance(candidate: CandidateEvidenceSummary) -> List[NextStep]:
    texts = []
    ex = candidate.excluded
    if not candidate.classified_areas:
        texts.append("Classify your reviewed responsibilities and achievements with responsibility areas, so they can be compared "
                     "with observed routes.")
    if ex.get("not_classified"):
        texts.append("Some of your reviewed evidence is not classified yet. Classify it if it describes responsibilities you held.")
    if ex.get("classification_stale"):
        texts.append("Some evidence changed after you classified it. Classify it again so it can be compared.")
    if ex.get("pending_review"):
        texts.append("Some evidence is not reviewed yet. Confirm or correct it if it is accurate.")
    if ex.get("employment_not_eligible"):
        texts.append("Some classified evidence belongs to employments that are not reviewed and mapped yet. Review and map those "
                     "employments so their evidence can count.")
    return [NextStep(kind="evidence", text=t) for t in texts]


POSITION_GUIDANCE = {
    "no_employment_history": "Add your employment history so a current position can be established.",
    "no_eligible_primary_employment": "Add or reinstate a primary employment so a current position can be established.",
    "pending_review": "Review your most recent employment so it can establish your current position.",
    "stale_mapping": "Your most recent employment's title changed after it was mapped. Map its role again.",
    "unresolved_role_mapping": "Choose the role of your most recent employment so it can establish your current position.",
    "low_mapping_confidence": "Confirm the role of your most recent employment by choosing it.",
    "role_unconfirmed": "Your current job is confirmed, but the standard role matched to it hasn't been confirmed yet. Review the "
                        "role match to continue.",
    "latest_employment_ineligible": "Your most recent employment cannot establish a current position yet; see its reasons and update it.",
}


# --------------------------------------------------------------------------- assembly
def build_pathway_result(position: CurrentPosition, target: RouteRole, candidate: CandidateEvidenceSummary, held_roles: Set[str],
                         evidence: Optional[ReferenceEvidenceResult], role_titles: Dict[str, str],
                         area_info: Dict[str, Tuple[str, bool]], threshold: int = 5,
                         role_meta: Optional[Dict[str, RoleRow]] = None) -> PathwayResult:
    """Pure assembly. evidence is the M5/M6 reference result for (current role -> target), or None when the current
    position is unknown. Only its route summaries' routes and labels and its route step experience are used: never
    individual observations, route months, supporting_profile_count or timelines."""
    position = position.model_copy(update={"candidate_employment_id": None})      # no internal identifiers in the result
    limitations: List[str] = []
    if position.status != "known" or evidence is None:
        steps = [NextStep(kind="evidence", text=POSITION_GUIDANCE.get(position.reason or "", "Establish your current position first."))]
        limitations.append(f"No pathway is assessed: the current position is unknown ({position.reason}).")
        return PathwayResult(current_position=position, target=target, candidate_evidence=candidate, routes=[],
                             next_steps=steps + evidence_guidance(candidate), limitations=limitations)

    # Career progressions only (Phase 7.2.1): a route that starts and ends in the same canonical role is never a
    # route, an alternative or a ranking input here, even if a caller passes unfiltered evidence.
    experience = {tuple(x.route): x for x in evidence.route_step_experience if x.route[0] != x.route[-1]}
    summaries = {tuple(s.route): s for s in evidence.route_summaries if len(s.route) >= 2 and s.route[0] != s.route[-1]}
    routes: List[PathwayRoute] = []
    for route in sorted(summaries):
        exp = experience.get(route)
        common = common_areas(exp, threshold)
        fit = fit_for_route(common, exp, candidate)
        gaps_steps = route_step_gaps(route, held_roles)
        gaps_areas = [a for a in common if a not in fit.matched_areas]          # common areas not recorded
        observed = ObservedRouteEvidence(
            data_mode=evidence.data_mode, is_synthetic_test_data=evidence.is_synthetic_test_data,
            evidence_class=summaries[route].evidence_class,
            experience_state=exp.state if exp else "suppressed_small_cohort",
            observation_count=exp.observation_count if exp else None,
            disclosed_areas=[AreaView(**a.model_dump(include={"responsibility_area", "label", "is_pm_ownership_signal", "profile_count", "is_common"}))
                             for a in sorted(exp.areas if exp else [], key=lambda a: a.responsibility_area)],
            areas_withheld=exp.areas_withheld if exp else False)
        route_id = ">".join(route)
        next_steps = ([NextStep(kind="area_gap", text=area_guidance(a, area_info[a][0])) for a in gaps_areas]
                      + [NextStep(kind="route_step", text=step_guidance(role_titles[r])) for r in gaps_steps])
        routes.append(PathwayRoute(
            rank=0, route_id=route_id, route=[RouteRole(role_id=r, canonical_title=role_titles[r]) for r in route],
            observed=observed, fit=fit,
            area_gaps=[AreaGap(responsibility_area=a, label=area_info[a][0]) for a in gaps_areas],
            step_gaps=[StepGap(role_id=r, canonical_title=role_titles[r]) for r in gaps_steps],
            next_steps=next_steps,
            step_directions=[move_direction((role_meta or {}).get(a), (role_meta or {}).get(b)) for a, b in zip(route, route[1:])],
            step_family_changes=[family_change((role_meta or {}).get(a), (role_meta or {}).get(b),
                                               move_direction((role_meta or {}).get(a), (role_meta or {}).get(b)))
                                 for a, b in zip(route, route[1:])]))
    routes.sort(key=lambda r: rank_key(r.fit.tier, [g.role_id for g in r.step_gaps], r.route_id))
    # Ties (Phase 8.1): the ranking itself is unchanged; routes whose meaningful inputs are equal share a rank group.
    meaningful = lambda r: rank_key(r.fit.tier, [g.role_id for g in r.step_gaps], "")[:2]      # noqa: E731
    groups = {k: i for i, k in enumerate(dict.fromkeys(meaningful(r) for r in routes), start=1)}
    sizes: Dict[Tuple[int, int], int] = {}
    for r in routes:
        sizes[meaningful(r)] = sizes.get(meaningful(r), 0) + 1
    routes = [r.model_copy(update={"rank": i, "rank_group": groups[meaningful(r)], "tied": sizes[meaningful(r)] > 1})
              for i, r in enumerate(routes, start=1)]

    if not routes:
        # Worded so it never reveals whether a route was observed but suppressed (a small group) or not observed at all.
        limitations.append(f"No route from {position.role_id} to {target.role_id} is supported by enough reference profiles to be shown. "
                           "This is an absence of observations, not an assessment of the route.")
    if any(r.observed.experience_state == "suppressed_small_cohort" for r in routes):
        limitations.append("Some routes were observed in too few reference profiles to describe their recorded experience; no fit is "
                           "assessed for them.")
    limitations += [l for l in evidence.limitations if "Destination company" not in l]
    return PathwayResult(current_position=position, target=target, data_mode=evidence.data_mode,
                         is_synthetic_test_data=evidence.is_synthetic_test_data, evidence_notice=evidence.evidence_notice,
                         candidate_evidence=candidate, routes=routes, next_steps=evidence_guidance(candidate), limitations=limitations)
