"""In-memory reference repository for EVALUATION FIXTURES ONLY.

Implements the ReferenceEvidenceRepository protocol (app/services/reference_evidence/repository.py) over evaluation
pools loaded from tests/fixtures/recommendation_eval/, so the production evidence and pathway code
(recommendation_evidence, build_pathway_result) runs unchanged. It never opens a database connection: evaluation
profiles cannot reach the synthetic or the real reference pool, an API, Career Insights or production.

The emulation follows the foundation's documented semantics (episode stints, one observation per profile per route
query, the first occurrence in the lowest segment, distinct-profile step experience); parity of the real SQL objects
with these semantics is covered separately by tests/test_episode_pathways_db_parity.py and the reference-evidence
integration tests. Evaluation profiles use the synthetic_test data mode and the synthetic_test_fixture class only because
those are the evidence service's supported mode names; every profile id starts with EVAL- and every report labels them
as evaluation fixtures.
"""
from typing import Dict, List, Optional, Sequence

from app.services.reference_evidence.models import (
    AreaRow, EpisodePathRow, RoleRow, RouteObservationRow, RouteTimelineSummaryRow, StepExperienceRow,
)
from app.services.reference_evidence.repository import InvalidEpisodeRoute

EVAL_MODE = "synthetic_test"
EVAL_CLASS = "synthetic_test_fixture"


class EvaluationRepository:
    def __init__(self, pool: dict):
        self.pool = pool
        self.roles: Dict[str, dict] = pool["roles"]
        self.areas: Dict[str, dict] = pool["areas"]
        self.threshold: Optional[float] = pool.get("privacy_minimum")
        self.paths: List[EpisodePathRow] = []
        self.stint_areas: Dict[tuple, List[str]] = {}          # (profile, segment, stint_no 1-based) -> areas
        self.attributes: Dict[str, dict] = {}                   # profile -> industries, skills, qualifications, fields
        for prof in pool["profiles"]:
            if not prof["profile_id"].startswith("EVAL-"):
                raise ValueError("evaluation profile ids must start with EVAL-")
            self.attributes[prof["profile_id"]] = dict(prof.get("attributes", {}))
            for seg in prof["segments"]:
                stints = seg["stints"]
                self.paths.append(EpisodePathRow(
                    profile_id=prof["profile_id"], dataset_class=EVAL_CLASS, segment_no=seg["segment_no"],
                    role_path=[s["role"] for s in stints], entry_change_types=[s.get("change") for s in stints],
                    stint_start_lo=[s["start"] for s in stints], stint_start_hi=[s["start"] for s in stints]))
                for i, s in enumerate(stints, start=1):
                    self.stint_areas[(prof["profile_id"], seg["segment_no"], i)] = list(s.get("areas", []))

    def _mode(self, mode: str) -> None:
        if mode != EVAL_MODE:
            raise ValueError("the evaluation repository serves the evaluation (synthetic_test) mode only")

    # ------------------------------------------------------------------ protocol
    def get_roles(self, role_ids: Sequence[str]) -> Dict[str, RoleRow]:
        levels: Dict[str, set] = {}
        for v in self.roles.values():                       # the family structure of the fixture's (copied) taxonomy
            levels.setdefault(v["family"], set()).add(v.get("seniority"))
        return {r: RoleRow(role_id=r, canonical_title=self.roles[r]["title"], role_family=self.roles[r]["family"],
                           is_pm_target=self.roles[r].get("is_pm_target", False), is_resolved=True, active=True,
                           seniority_code=self.roles[r].get("seniority"), seniority_rank=self.roles[r].get("rank"),
                           family_has_levels=len(levels[self.roles[r]["family"]]) > 1)
                for r in role_ids if r in self.roles}

    # ------------------------------------------------------------------ prototype only (not part of the protocol)
    def profile_facts(self, profile_id: str, segment_no: int, route) -> dict:
        """What the relevance prototype may compare for ONE supporting profile of `route`: roles held before the route
        starts (earlier roles), areas recorded on the route's pre-destination stints, and profile-level industries,
        skills, qualifications and fields of study. The route is located as the engine does: the first stint in the
        origin role, then the first later stint in the destination role. Evaluation fixtures only."""
        path = next(p for p in self.paths if p.profile_id == profile_id and p.segment_no == segment_no)
        rp = path.role_path
        start = next(i for i, r in enumerate(rp) if r == route[0] and route[-1] in rp[i + 1:])
        end = next(j for j in range(start + 1, len(rp)) if rp[j] == route[-1])
        attrs = self.attributes.get(profile_id, {})
        return {
            "earlier_roles": sorted(set(rp[:start])),
            "areas": sorted({a for s in range(start + 1, end + 1) for a in self.stint_areas.get((profile_id, segment_no, s), [])}),
            "industries": sorted(set(attrs.get("industries", []))),
            "skills": sorted(set(attrs.get("skills", []))),
            "qualifications": sorted(set(attrs.get("qualifications", []))),
            "fields": sorted(set(attrs.get("fields", []))),
            "qualification_levels": sorted(set(attrs.get("qualification_levels", []))),     # Phase 8.2.1 (v2 pools)
            "qualification_types": sorted(set(attrs.get("qualification_types", []))),
        }

    def mode_episode_paths_containing(self, role_id: str, data_mode: str) -> List[EpisodePathRow]:
        self._mode(data_mode)
        return [p for p in self.paths if role_id in p.role_path]

    def episode_route_observations(self, route: Sequence[str], data_mode: str) -> List[RouteObservationRow]:
        self._mode(data_mode)
        if len(route) < 2 or any(r not in self.roles for r in route):
            raise InvalidEpisodeRoute(f"invalid route {list(route)}")
        out: Dict[str, RouteObservationRow] = {}
        n = len(route)
        for p in sorted(self.paths, key=lambda p: (p.profile_id, p.segment_no)):
            if p.profile_id in out:
                continue
            for i in range(len(p.role_path) - n + 1):
                if tuple(p.role_path[i:i + n]) == tuple(route):
                    a, b = i, i + n - 1
                    out[p.profile_id] = RouteObservationRow(
                        profile_id=p.profile_id, segment_no=p.segment_no, start_position=i + 1,
                        step_change_types=p.entry_change_types[i + 1:i + n],
                        route_months_min=max(0, p.stint_start_lo[b] - p.stint_start_hi[a]),
                        route_months_max=p.stint_start_hi[b] - p.stint_start_lo[a], is_precise=True)
                    break
        return list(out.values())

    def episode_route_timeline_summary(self, route: Sequence[str], data_mode: str) -> RouteTimelineSummaryRow:
        obs = self.episode_route_observations(route, data_mode)
        return RouteTimelineSummaryRow(
            data_mode=data_mode, route=list(route), supporting_profile_count=len(obs), dated_profile_count=len(obs),
            count_display_allowed=self.threshold is not None and len(obs) >= self.threshold, evidence_class="synthetic_test_only",
            p25_months=None, median_months=None, p75_months=None,
            coarse_min_months=min((o.route_months_min for o in obs), default=None),
            coarse_max_months=max((o.route_months_max for o in obs), default=None),
            timeline_metric="route_start_to_target_start_months", timeline_display_allowed=False)

    def route_step_experience(self, requests, data_mode: str) -> List[StepExperienceRow]:
        self._mode(data_mode)
        out = set()
        for q in requests:
            for stint in range(q.first_stint, q.last_stint + 1):
                for area in self.stint_areas.get((q.profile_id, q.segment_no, stint), []):
                    out.add((q.profile_id, area))
        return [StepExperienceRow(profile_id=a, responsibility_area=b) for a, b in sorted(out)]

    def responsibility_areas(self, areas: Sequence[str]) -> Dict[str, AreaRow]:
        return {a: AreaRow(responsibility_area=a, label=self.areas[a]["label"], is_pm_ownership_signal=self.areas[a].get("pm_signal", False))
                for a in areas if a in self.areas}

    def min_display_count(self) -> Optional[float]:
        return self.threshold
