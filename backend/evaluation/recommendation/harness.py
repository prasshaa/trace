"""Recommendation evaluation harness (Phase 8.1): runs the CURRENT recommendation engine on evaluation-only fixtures
and records a reproducible baseline. It changes nothing in the engine.

Separation of concerns:
  * automated correctness / rule-consistency invariants: pass or fail here, in code;
  * baseline sensitivity observations: recorded, not judged (for example, whether earlier roles change anything);
  * human relevance judgments: NOT produced here. Review templates are generated with every label pending, for product
    reviewers and career-domain experts to fill in. Nothing here is ground truth about career relevance.

The production composition is used unchanged: reference_evidence.recommendation_evidence (own-contribution exclusion,
zero-move exclusion, route-level privacy) and candidate_profile.pathway.build_pathway_result (fit, gaps, ranking, ties,
guidance). The self-target guard and the user-confirmed starting point live in the service layer and are covered by the
service and API tests; here a scenario either has a current role or names why it has none.
"""
import copy
import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional

from app.services.candidate_profile.models import CurrentPosition
from app.services.candidate_profile.pathway import (
    CandidateEvidenceSummary, RouteRole, build_pathway_result, common_areas,
)
from app.services.reference_evidence import (
    EvidenceRequest, is_zero_move, progression_routes_only, recommendation_evidence, retrieve_reference_evidence, route_support,
)
from evaluation.recommendation.repository import EVAL_MODE, EvaluationRepository

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "recommendation_eval"
HARNESS_VERSION = "phase-8.1-v1"


# --------------------------------------------------------------------------- fixtures
def expand_pool(spec: dict) -> dict:
    """The compact pool spec as explicit EVAL- profiles (one segment each for route groups)."""
    pool = {k: spec[k] for k in ("pool_id", "label", "privacy_minimum", "roles", "areas")}
    if "dimension_set" in spec:                                   # Phase 8.2.1 pools only
        pool["dimension_set"] = spec["dimension_set"]
    profiles = []
    month = 24000
    for g in spec["route_groups"]:
        ids = list(g.get("ids", []))
        counts = dict(g.get("area_counts", {}))
        stint_with_areas = g.get("area_stint", 1)
        # Phase 8.2 (optional, personalisation pool only): sub-populations with earlier roles before the route
        # ("prefix"), profile attributes and per-route-stint areas. Absent in the Phase 8.1 core pool.
        cohorts = [c for c in g.get("subgroups", []) for _ in range(c["count"])]
        for i in range(g["profiles"]):
            pid = ids[i] if i < len(ids) else f"EVAL-{g['id']}-{i + 1:02d}"
            sub = cohorts[i] if i < len(cohorts) else {}
            full = list(sub.get("prefix", [])) + list(g["route"])
            offset = len(sub.get("prefix", []))
            stints = []
            for s, role in enumerate(full, start=1):
                prev = full[s - 2] if s > 1 else None
                change = None if s == 1 else ("employer_change" if role == prev else "role_and_employer_change")
                areas = [a for a, k in sorted(counts.items()) if s == stint_with_areas + offset and i < k]
                areas += [a for a in sub.get("route_stint_areas", {}).get(str(s - offset), []) if a not in areas]
                stints.append({"role": role, "change": change, "start": month + 24 * (s - 1), "areas": areas})
            profile = {"profile_id": pid, "group": g["id"], "segments": [{"segment_no": 1, "stints": stints}]}
            if sub.get("attributes"):
                profile["attributes"] = sub["attributes"]
            profiles.append(profile)
            month += 1
    for p in spec.get("profiles", []):
        profiles.append({**p, "group": "explicit"})
    pool["profiles"] = profiles
    pool["groups"] = {g["id"]: g["purpose"] for g in spec["route_groups"]}
    return pool


def load(fixture_dir: Path = FIXTURE_DIR):
    scenarios = json.loads((fixture_dir / "scenarios.json").read_text())
    pool_spec = json.loads((fixture_dir / scenarios["pool"]).read_text())
    digest = hashlib.sha256((fixture_dir / "scenarios.json").read_bytes() + (fixture_dir / scenarios["pool"]).read_bytes()).hexdigest()
    return expand_pool(pool_spec), scenarios, digest


# --------------------------------------------------------------------------- one run of the current engine
def run_engine(repo: EvaluationRepository, pool: dict, user: dict, target: str):
    """(PathwayResult, evidence) for one user and target through the production composition."""
    roles = pool["roles"]
    current = user.get("current_role")
    if current:
        position = CurrentPosition(status="known", explanation="evaluation fixture", role_id=current,
                                   canonical_title=roles[current]["title"], role_family=roles[current]["family"],
                                   mapping_basis="candidate_selected")
        evidence = recommendation_evidence(repo, target, EVAL_MODE, current, user.get("own_profile_ids", []))
    else:
        position = CurrentPosition(status="unknown", reason=user.get("position_reason", "pending_review"), explanation="evaluation fixture")
        evidence = None
    candidate = CandidateEvidenceSummary(classified_areas=sorted(set(user.get("areas", []))), supporting_evidence=[], excluded={})
    titles = {r: v["title"] for r, v in roles.items()}
    area_info = {a.responsibility_area: (a.label, a.is_pm_ownership_signal)
                 for x in (evidence.route_step_experience if evidence else []) for a in x.areas}
    threshold = repo.min_display_count()
    result = build_pathway_result(position, RouteRole(role_id=target, canonical_title=titles[target]), candidate,
                                  set(user.get("held_roles", [])), evidence, titles, area_info,
                                  threshold=int(threshold) if threshold is not None else 10 ** 9)
    return result, evidence


def direction(pool: dict, route: List[str]) -> List[str]:
    """Per step, by DEFAULT seniority rank of the standard role (reporting only; the engine does not use it)."""
    out = []
    for a, b in zip(route, route[1:]):
        ra, rb = pool["roles"][a].get("rank"), pool["roles"][b].get("rank")
        out.append("unknown" if ra is None or rb is None else "up" if rb > ra else "down" if rb < ra else "lateral")
    return out


def summarise(result) -> List[dict]:
    return [{"rank": r.rank, "rank_group": r.rank_group, "tied": r.tied, "route_id": r.route_id,
             "tier": r.fit.tier, "reason": r.fit.reason, "limitations": list(r.fit.limitations),
             "common_areas": list(r.fit.common_areas), "matched_areas": list(r.fit.matched_areas),
             "observation_count": r.observed.observation_count, "experience_state": r.observed.experience_state,
             "step_gaps": [g.role_id for g in r.step_gaps]} for r in result.routes]


# --------------------------------------------------------------------------- one scenario: baseline and invariants
def evaluate(repo: EvaluationRepository, pool: dict, scen: dict) -> dict:
    user, target = scen["user"], scen["target"]
    current = user.get("current_role")
    own = set(user.get("own_profile_ids", []))
    result, evidence = run_engine(repo, pool, user, target)
    shown = summarise(result)

    discovered, suppressed = [], []
    if current:
        before = progression_routes_only(retrieve_reference_evidence(EvidenceRequest(
            destination_role_id=target, data_mode=EVAL_MODE, origin_role_id=current, exclude_profile_ids=tuple(sorted(own))), repo))
        support = route_support(before)
        shown_ids = {r["route_id"] for r in shown}
        for route, n in sorted(support.items()):
            item = {"route_id": ">".join(route), "distinct_profiles": n}
            discovered.append(item)
            if item["route_id"] not in shown_ids:
                suppressed.append(item)
        without_exclusion = route_support(progression_routes_only(retrieve_reference_evidence(EvidenceRequest(
            destination_role_id=target, data_mode=EVAL_MODE, origin_role_id=current), repo))) if own else support
    else:
        support, without_exclusion = {}, {}

    threshold = repo.min_display_count()
    inv: Dict[str, dict] = {}

    def check(name: str, ok: bool, detail: str = "") -> None:
        inv[name] = {"ok": bool(ok), "detail": detail}

    obs_ids = {o.provenance.profile_id for o in (evidence.observations if evidence else [])}
    shown_support = {r["route_id"]: support.get(tuple(r["route_id"].split(">")), 0) for r in shown}
    check("privacy_minimum_applied", all(n >= threshold for n in shown_support.values()),
          f"shown supports {shown_support}, minimum {threshold}")
    check("no_zero_move_route", all(not is_zero_move(r["route_id"].split(">")) for r in shown))
    check("own_contribution_excluded", not (own & obs_ids), f"own {sorted(own)}")
    check("own_exclusion_never_adds_support", all(support.get(k, 0) <= without_exclusion.get(k, 0) for k in support))
    check("counts_are_distinct_profiles", all(r["observation_count"] in (None, shown_support[r["route_id"]]) for r in shown),
          "observation_count equals distinct supporting profiles")
    check("routes_start_at_current_role", all(r["route_id"].split(">")[0] == current for r in shown) if current else not shown)
    check("routes_end_at_target", all(r["route_id"].split(">")[-1] == target for r in shown))
    groups = {}
    for r in shown:
        groups.setdefault((r["tier"], len(r["step_gaps"])), set()).add(r["rank_group"])
    check("ties_consistent", all(len(v) == 1 for v in groups.values())
          and all(r["tied"] == (sum(1 for x in shown if x["rank_group"] == r["rank_group"]) > 1) for r in shown))
    exp = {tuple(x.route): x for x in (evidence.route_step_experience if evidence else [])}
    fit_ok = True
    for r in shown:
        common = common_areas(exp.get(tuple(r["route_id"].split(">"))), int(threshold))
        areas = set(user.get("areas", []))
        expected = ("not_established" if not common or not areas else "aligned" if set(common) <= areas
                    else "partial" if set(common) & areas else "limited")
        fit_ok &= expected == r["tier"]
    check("fit_follows_existing_rule", fit_ok)
    again, _ = run_engine(repo, pool, copy.deepcopy(user), target)
    check("deterministic", summarise(again) == shown)

    # Consistency tests (metamorphic): change one input, compare.
    irrelevant = {**user, "skills": ["Changed skill"], "industries": ["changed_industry"], "qualifications": ["doctorate"]}
    check("irrelevant_fields_do_not_change_output", summarise(run_engine(repo, pool, irrelevant, target)[0]) == shown,
          "skills, industries and qualifications changed")
    more_areas = {**user, "areas": sorted(set(user.get("areas", [])) | {"roadmap_ownership", "outcome_ownership"})}
    after = summarise(run_engine(repo, pool, more_areas, target)[0])
    check("responsibilities_affect_fit_only", {r["route_id"] for r in after} == {r["route_id"] for r in shown}
          and all(a["observation_count"] == b["observation_count"] for a in after for b in shown if a["route_id"] == b["route_id"]),
          "route set and evidence unchanged when areas are added")
    if current:
        other = "ROLE-QA" if current != "ROLE-QA" else "PM-APM"
        moved = summarise(run_engine(repo, pool, {**user, "current_role": other}, target)[0])
        check("current_role_change_moves_origin", all(r["route_id"].startswith(other + ">") for r in moved),
              f"current role corrected to {other}")
    earlier = {**user, "held_roles": sorted(set(user.get("held_roles", [])) | {"ROLE-PAN", "ROLE-DS", "PM-SPM"})}
    earlier_out = summarise(run_engine(repo, pool, earlier, target)[0])
    sensitivity = {
        "earlier_roles_changed_routes": {r["route_id"] for r in earlier_out} != {r["route_id"] for r in shown},
        "earlier_roles_changed_order": [r["route_id"] for r in earlier_out] != [r["route_id"] for r in shown],
        "earlier_roles_changed_ties": [r["tied"] for r in earlier_out] != [r["tied"] for r in shown],
    }
    return {
        "id": scen["id"], "title": scen["title"], "covers": scen["covers"], "target": target,
        "inputs": {k: user.get(k) for k in ("current_role", "position_reason", "held_roles", "areas", "skills", "industries",
                                            "qualifications", "own_profile_ids")},
        "position": result.current_position.status,
        "eligible_population": len({p["profile_id"] for p in pool["profiles"]} - own),
        "discovered_routes": discovered, "suppressed_routes": suppressed, "shown_routes": shown,
        "directions": {r["route_id"]: direction(pool, r["route_id"].split(">")) for r in shown},
        "tie_groups": sorted({r["rank_group"] for r in shown if r["tied"]}),
        "explanation": {"limitations": list(result.limitations), "next_steps": [s.text for s in result.next_steps]},
        "invariants": inv, "sensitivity": sensitivity,
    }


def run_all(fixture_dir: Path = FIXTURE_DIR) -> dict:
    pool, scenarios, digest = load(fixture_dir)
    repo = EvaluationRepository(pool)
    records = [evaluate(repo, pool, s) for s in scenarios["scenarios"]]
    return {"harness_version": HARNESS_VERSION, "fixture_sha256": digest, "pool_id": pool["pool_id"], "label": pool["label"],
            "privacy_minimum": pool["privacy_minimum"], "profiles": len(pool["profiles"]), "scenarios": records}
