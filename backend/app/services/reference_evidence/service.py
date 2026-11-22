"""Deterministic retrieval of reference career-history evidence for a destination role.

Episode-aware (foundation v2.1.0). A route is a sequence of employment episodes; consecutive
identical role ids mean a confirmed move to a different employer (PM at A -> PM at B is
{PM-PM, PM-PM}). The legacy role-code objects are not used.

How a destination maps onto the foundation's route functions
-------------------------------------------------------------
The functions are route-based, not destination-based. For each profile in the requested mode
(v_career_episode_paths joined to v_mode_profiles), the observed route is the run of episode
stints from the start of a confirmed segment up to the destination: the destination's first
occurrence at stint position >= 2, in the lowest segment. Each stint after the first was entered
by a confirmed pathway move (role change, employer change, or both), so a person whose first
recorded role is already the destination role is included once they move employer in it. A
single episode in the destination role (nothing confirmed leads into it) is not a pathway.

Then, for every distinct observed route:
  * trace_ref.episode_route_observations supplies each individual's route months, precision and
    step change types; the service checks it reports the same segment, start position and change
    types as the path row for that profile;
  * trace_ref.episode_route_timeline_summary supplies the cohort figures and evidence labels.
No counts, evidence classes or timelines are computed here.

Mode isolation: exactly one data_mode per call, passed to every query; no fallback between
modes; every returned row is checked against the mode's dataset class.
"""
from typing import Dict, List, Optional, Sequence, Tuple

from app.services.career_history.dates import month_label
from app.services.reference_evidence.models import (
    EMPIRICAL_NOTICE,
    EXPECTED_DATASET_CLASS,
    SUPPORTED_MODES,
    SYNTHETIC_NOTICE,
    DestinationRole,
    EpisodePathRow,
    EvidenceRequest,
    ObservationProvenance,
    ObservedPathway,
    ReferenceEvidenceResult,
    AreaExperience,
    RouteCohortSummary,
    RouteStep,
    RouteStepExperience,
    StepRequest,
)
from app.services.reference_evidence.repository import InvalidEpisodeRoute, ReferenceEvidenceRepository

LIMITATIONS = [
    "Destination company cannot be filtered: reference profiles store only per-profile employer "
    "pseudonyms, not company names.",
    "Only routes with at least one confirmed move (role change, employer change, or both) into the "
    "destination role are observed pathways; a single episode in the destination role is not.",
    "Each individual contributes at most one observation: the first confirmed route into the "
    "destination role in their history.",
    "A seniority change within the same role id at the same employer is not an episode boundary "
    "(pending a taxonomy decision on levels such as PM II).",
]


class ReferenceEvidenceError(ValueError):
    """Base class for requests the service will not answer."""


class UnknownDestinationRole(ReferenceEvidenceError):
    pass


class UnsupportedEvidenceMode(ReferenceEvidenceError):
    pass


class UnsupportedFilter(ReferenceEvidenceError):
    pass


class UnknownOriginRole(ReferenceEvidenceError):
    pass


STEP_EXPERIENCE_LIMITATION = (
    "Route step experience lists the responsibility areas recorded on the observed people's employments before the "
    "destination role, counted once per person. Routes observed fewer times than the display threshold (five by default) "
    "disclose no areas, and an area is listed only when at least that many people recorded it. Areas describe what was recorded, not what was required "
    "to make the move.")

ORIGIN_LIMITATION = (
    "With an origin role, each individual contributes at most one observation: the route from their first "
    "episode in the origin role to their first later episode in the destination role, within one confirmed "
    "segment. People who reached the destination from elsewhere are not included.")


class EvidenceIntegrityError(RuntimeError):
    """Foundation outputs disagree with each other or with the requested mode. Never returned
    as evidence; indicates a bug or a schema change."""


# --------------------------------------------------------------------------- career progression (Phase 7.2.1)
ZERO_MOVE_LIMITATION = (
    "Routes that start and end in the same standard role (for example a move to another employer in the same role) "
    "are employment history, not career progressions: they are not returned as routes.")


def is_zero_move(route: Sequence[str]) -> bool:
    """A route whose first and last canonical role ids are equal: no net role change, so not a career progression.
    Role ids, never titles: a title alias is resolved to its role id before anything reaches the evidence layer."""
    return len(route) < 2 or route[0] == route[-1]


def progression_routes_only(result: ReferenceEvidenceResult) -> ReferenceEvidenceResult:
    """The evidence restricted to career progressions, for every consumer that presents or ranks routes (pathways,
    alternatives, ranking inputs, route evidence). Zero-move routes (same start and end role) are removed from the
    observations, the route summaries and the step experience together, so no consumer can rebuild one. Nothing is
    deleted from the reference data, and routes that pass through a same-role employer change on the way to a
    different role are kept unchanged (their identity, counts and ranking inputs are the foundation's)."""
    keep = lambda route: not is_zero_move(route)                                     # noqa: E731
    observations = [o for o in result.observations if keep(o.route)]
    return result.model_copy(update={
        "observations": observations,
        "route_summaries": [s for s in result.route_summaries if keep(s.route)],
        "route_step_experience": [x for x in result.route_step_experience if keep(x.route)],
        "has_observed_pathways": bool(observations),
        "limitations": [*result.limitations, ZERO_MOVE_LIMITATION] if ZERO_MOVE_LIMITATION not in result.limitations
        else list(result.limitations),
    })


# --------------------------------------------------------------------------- route-level privacy (Phase 8.1)
SUPPRESSED_ROUTE_LIMITATION = (
    "Routes supported by fewer distinct reference profiles than the privacy minimum are never shown, described, counted "
    "or used for guidance: a distinctive sequence of roles could identify someone.")


def route_support(result: ReferenceEvidenceResult) -> Dict[Tuple[str, ...], int]:
    """Distinct reference profiles observed on each complete route (one observation per profile per query; a profile
    is never counted twice, even if its history contains the route more than once)."""
    out: Dict[Tuple[str, ...], set] = {}
    for o in result.observations:
        out.setdefault(tuple(o.route), set()).add(o.provenance.profile_id)
    return {route: len(pids) for route, pids in out.items()}


def privacy_safe_routes(result: ReferenceEvidenceResult, threshold) -> ReferenceEvidenceResult:
    """Only routes supported by at least `threshold` distinct profiles (the foundation's min_profiles_limited, the same
    small-group minimum used everywhere else; no setting = nothing is shown). A suppressed route is removed from the
    observations, summaries and step experience together, so no consumer (pathway, alternatives, ranking, guidance,
    route evidence) can expose its sequence, its count or anything derived from it. Source records are untouched."""
    support = route_support(result)
    keep = lambda route: threshold is not None and support.get(tuple(route), 0) >= threshold   # noqa: E731
    observations = [o for o in result.observations if keep(o.route)]
    limitations = list(result.limitations)
    if SUPPRESSED_ROUTE_LIMITATION not in limitations:
        limitations.append(SUPPRESSED_ROUTE_LIMITATION)
    return result.model_copy(update={
        "observations": observations,
        "route_summaries": [s for s in result.route_summaries if keep(s.route)],
        "route_step_experience": [x for x in result.route_step_experience if keep(x.route)],
        "has_observed_pathways": bool(observations),
        "limitations": limitations,
    })


def recommendation_evidence(repo: ReferenceEvidenceRepository, destination_role_id: str, data_mode: str,
                            origin_role_id: Optional[str] = None, exclude_profile_ids: Sequence[str] = ()) -> ReferenceEvidenceResult:
    """The one composition every user-facing route consumer uses (pathway, route evidence, the evaluation harness):
    the caller's own contribution excluded first, then career progressions only, then route-level privacy."""
    raw = retrieve_reference_evidence(EvidenceRequest(destination_role_id=destination_role_id, data_mode=data_mode,
                                                      origin_role_id=origin_role_id,
                                                      exclude_profile_ids=tuple(sorted(set(exclude_profile_ids)))), repo)
    return privacy_safe_routes(progression_routes_only(raw), repo.min_display_count())


# --------------------------------------------------------------------------- public API
def retrieve_reference_evidence(request: EvidenceRequest,
                                repo: ReferenceEvidenceRepository) -> ReferenceEvidenceResult:
    if request.data_mode not in SUPPORTED_MODES:
        raise UnsupportedEvidenceMode(
            f"data_mode {request.data_mode!r} is not enabled for evidence retrieval; "
            f"use one of {SUPPORTED_MODES}")
    if request.destination_company is not None:
        raise UnsupportedFilter(LIMITATIONS[0])

    mode = request.data_mode
    role = repo.get_roles([request.destination_role_id]).get(request.destination_role_id)
    if role is None:
        raise UnknownDestinationRole(f"unknown role_id {request.destination_role_id!r}")

    origin = None
    if request.origin_role_id is not None:
        origin = repo.get_roles([request.origin_role_id]).get(request.origin_role_id)
        if origin is None:
            raise UnknownOriginRole(f"unknown role_id {request.origin_role_id!r}")

    excluded = set(request.exclude_profile_ids)
    paths = repo.mode_episode_paths_containing(role.role_id, mode)
    _check_dataset_class(paths, mode)
    paths = [p for p in paths if p.profile_id not in excluded]          # removed before any route is picked
    picked = (_first_route_per_profile(paths, role.role_id) if origin is None
              else _first_route_from_origin(paths, origin.role_id, role.role_id))

    routes = sorted({tuple(route) for route, _, _, _ in picked.values()})
    families = _families(repo, {r for route in routes for r in route})
    observations: List[ObservedPathway] = []
    summaries: List[RouteCohortSummary] = []

    for route in routes:
        try:
            source_obs = {o.profile_id: o for o in repo.episode_route_observations(route, mode)}
            s = repo.episode_route_timeline_summary(route, mode)
        except InvalidEpisodeRoute as exc:
            # Routes are built from the foundation's own paths, so a rejection means the two disagree.
            raise EvidenceIntegrityError(f"foundation rejected observed route {list(route)}: {exc}") from exc

        for profile_id, (p_route, path, start, end) in sorted(picked.items()):
            if tuple(p_route) != route:
                continue
            o = source_obs.get(profile_id)
            expected_changes = list(path.entry_change_types[start + 1:end])
            if (o is None or o.segment_no != path.segment_no or o.start_position != start + 1
                    or list(o.step_change_types) != expected_changes):
                raise EvidenceIntegrityError(
                    f"episode_route_observations disagrees with v_career_episode_paths for route "
                    f"{list(route)} in mode {mode}")
            observations.append(ObservedPathway(
                route=list(route),
                steps=[_step(path, i, families, first=i == start) for i in range(start, end)],
                step_change_types=list(o.step_change_types),
                route_months_min=o.route_months_min,
                route_months_max=o.route_months_max,
                is_precise=o.is_precise,
                provenance=ObservationProvenance(profile_id=profile_id, dataset_class=path.dataset_class,
                                                 segment_no=path.segment_no),
            ))

        if s is None:
            raise EvidenceIntegrityError(f"episode_route_timeline_summary returned no row for {list(route)}")
        _check_summary(s, route, mode)
        summary = RouteCohortSummary(**s.model_dump())
        if excluded & set(source_obs):
            # The foundation's cohort figures included an excluded profile: recompute the counts exactly from the
            # per-profile rows without it. Percentiles cannot be recomputed from these rows, so they are withheld.
            kept = [o for pid, o in source_obs.items() if pid not in excluded]
            threshold = repo.min_display_count()
            summary = summary.model_copy(update={
                "supporting_profile_count": len(kept), "dated_profile_count": sum(o.is_precise for o in kept),
                "count_display_allowed": threshold is not None and len(kept) >= threshold,
                "p25_months": None, "median_months": None, "p75_months": None, "timeline_display_allowed": False,
                "coarse_min_months": min((o.route_months_min for o in kept), default=None),
                "coarse_max_months": max((o.route_months_max for o in kept), default=None)})
        summaries.append(summary)

    step_experience = _route_step_experience(repo, picked, mode)

    return ReferenceEvidenceResult(
        data_mode=mode,
        is_synthetic_test_data=mode != "empirical",
        evidence_notice=EMPIRICAL_NOTICE if mode == "empirical" else SYNTHETIC_NOTICE,
        destination=DestinationRole(role_id=role.role_id, canonical_title=role.canonical_title,
                                    role_family=role.role_family, is_pm_target=role.is_pm_target),
        origin=(DestinationRole(role_id=origin.role_id, canonical_title=origin.canonical_title,
                                role_family=origin.role_family, is_pm_target=origin.is_pm_target) if origin else None),
        has_observed_pathways=bool(observations),
        observations=observations,
        route_summaries=summaries,
        route_step_experience=step_experience,
        limitations=list(LIMITATIONS) + ([ORIGIN_LIMITATION] if origin else []) + [STEP_EXPERIENCE_LIMITATION],
    )


# --------------------------------------------------------------------------- helpers
def _route_step_experience(repo: ReferenceEvidenceRepository, picked, mode: str) -> List[RouteStepExperience]:
    """Responsibility areas recorded before the destination, per observed route, by distinct reference profile.

    Uses exactly the observations in this result (picked): for each, the stints from the route's first step up
    to, but not including, the destination step. One read-only query in the same mode; every returned profile
    must be one of the requested observations (otherwise EvidenceIntegrityError).

    Disclosure (threshold = the foundation's min_profiles_limited, 5 by default; missing = disclose nothing):
    a route with fewer observations than the threshold is suppressed entirely; otherwise only areas recorded by
    at least the threshold of distinct profiles are listed, with their count, and an area is common when it is
    also recorded by more than half of the observations. Common is never derived from a withheld area."""
    requests = [StepRequest(profile_id=pid, segment_no=path.segment_no, first_stint=start + 1, last_stint=end - 1)
                for pid, (_, path, start, end) in sorted(picked.items())]
    rows = repo.route_step_experience(requests, mode)
    by_profile: Dict[str, set] = {}
    for r in rows:
        if r.profile_id not in picked:
            raise EvidenceIntegrityError(f"step experience returned a profile outside the observations in mode {mode}")
        by_profile.setdefault(r.profile_id, set()).add(r.responsibility_area)
    labels = repo.responsibility_areas(sorted({a for areas in by_profile.values() for a in areas}))
    unknown = {a for areas in by_profile.values() for a in areas} - set(labels)
    if unknown:
        raise EvidenceIntegrityError(f"responsibility areas missing from the lookup: {sorted(unknown)}")
    threshold = repo.min_display_count()
    shown = lambda n: threshold is not None and n >= threshold      # a missing threshold discloses nothing

    routes: Dict[Tuple[str, ...], List[str]] = {}
    for pid, (route, _, _, _) in picked.items():
        routes.setdefault(tuple(route), []).append(pid)
    out = []
    for route in sorted(routes):
        n = len(routes[route])                                       # observation_count: the denominator
        base = dict(data_mode=mode, route=list(route), pre_destination_steps=list(route[:-1]))
        if not shown(n):
            out.append(RouteStepExperience(state="suppressed_small_cohort", **base))
            continue
        counts: Dict[str, int] = {}
        for pid in routes[route]:
            for area in by_profile.get(pid, ()):
                counts[area] = counts.get(area, 0) + 1              # once per distinct profile
        listed = [AreaExperience(responsibility_area=a, label=labels[a].label, is_pm_ownership_signal=labels[a].is_pm_ownership_signal,
                                 profile_count=c, is_common=2 * c > n)
                  for a, c in sorted(counts.items()) if shown(c)]
        withheld = len(listed) < len(counts)
        state = ("no_recorded_areas" if not counts else "areas_disclosed" if listed else "areas_withheld")
        out.append(RouteStepExperience(state=state, observation_count=n, areas_withheld=withheld, areas=listed, **base))
    return out


def _first_route_per_profile(paths: Sequence[EpisodePathRow], destination: str
                             ) -> Dict[str, Tuple[List[str], EpisodePathRow, int, int]]:
    """profile_id -> (route, path row, first stint index (0-based), end index (exclusive)). Lowest segment
    first, then the destination's first position >= 2 (1-based) within it. Exact role_id equality;
    repeated role ids are kept (they are separate episodes)."""
    picked: Dict[str, Tuple[List[str], EpisodePathRow, int, int]] = {}
    for path in sorted(paths, key=lambda p: (p.profile_id, p.segment_no)):
        if path.profile_id in picked:
            continue
        for i, role_id in enumerate(path.role_path[1:], start=2):
            if role_id == destination:
                picked[path.profile_id] = (path.role_path[:i], path, 0, i)
                break
    return picked


def _first_route_from_origin(paths: Sequence[EpisodePathRow], origin: str, destination: str
                             ) -> Dict[str, Tuple[List[str], EpisodePathRow, int, int]]:
    """As _first_route_per_profile, but the route starts at the first stint in the origin role that is
    followed, later in the same segment, by a stint in the destination role (the first such stint).
    That is also the first place the route occurs in the person's history, which is what
    episode_route_observations reports (checked by the caller). Origin may equal destination: then
    the route is a move between two episodes in the same role (for example a new employer)."""
    picked: Dict[str, Tuple[List[str], EpisodePathRow, int, int]] = {}
    for path in sorted(paths, key=lambda p: (p.profile_id, p.segment_no)):
        if path.profile_id in picked:
            continue
        rp = path.role_path
        for s, role_id in enumerate(rp):
            if role_id != origin:
                continue
            end = next((j + 1 for j in range(s + 1, len(rp)) if rp[j] == destination), None)
            if end is not None:
                picked[path.profile_id] = (rp[s:end], path, s, end)
                break
    return picked


def _families(repo: ReferenceEvidenceRepository, role_ids) -> Dict[str, str]:
    roles = repo.get_roles(sorted(role_ids))
    missing = set(role_ids) - set(roles)
    if missing:
        raise EvidenceIntegrityError(f"route roles missing from the taxonomy: {sorted(missing)}")
    return {rid: r.role_family for rid, r in roles.items()}


def _step(path: EpisodePathRow, i: int, families: Dict[str, str], first: bool = False) -> RouteStep:
    lo, hi = path.stint_start_lo[i], path.stint_start_hi[i]
    role_id = path.role_path[i]
    return RouteStep(role_id=role_id, role_family=families[role_id],
                     entry_change_type=None if first else path.entry_change_types[i],
                     start_lo=lo, start_hi=hi,
                     start_lo_label=month_label(lo), start_hi_label=month_label(hi),
                     start_is_month_precise=lo == hi)


def _check_dataset_class(paths: Sequence[EpisodePathRow], mode: str) -> None:
    expected = EXPECTED_DATASET_CLASS[mode]
    wrong = {p.dataset_class for p in paths if p.dataset_class != expected}
    if wrong:
        raise EvidenceIntegrityError(f"mode {mode} returned dataset classes {sorted(wrong)}")
    for p in paths:
        if not p.entry_change_types or p.entry_change_types[0] is not None:
            raise EvidenceIntegrityError("episode path rows must start with a null entry change type")


def _check_summary(s, route: Tuple[str, ...], mode: str) -> None:
    if s.data_mode != mode or tuple(s.route) != route:
        raise EvidenceIntegrityError(
            f"episode_route_timeline_summary returned {s.data_mode}/{s.route} for {mode}/{list(route)}")
    synthetic_label = s.evidence_class == "synthetic_test_only"
    if synthetic_label != (mode != "empirical"):
        raise EvidenceIntegrityError(f"evidence_class {s.evidence_class} does not belong to mode {mode}")
    if mode != "empirical" and s.timeline_display_allowed:
        raise EvidenceIntegrityError("synthetic summary allows timeline display")
