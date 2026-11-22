"""Shadow evaluation of the whole-profile relevance prototype, the privacy combination audit and the human-review pack
(Phase 8.2.1). The Phase 8.2 run is frozen in shadow_phase82.py and stays reproducible.

Runs every scenario through the PRODUCTION composition (unchanged), then the prototype's relevance breakdown for each
route production shows. Production output is recorded as is; the prototype never re-orders, filters or labels it.
"""
import hashlib
import json
from itertools import combinations
from pathlib import Path
from typing import Dict, List

from app.services.reference_evidence import EvidenceRequest, progression_routes_only, recommendation_evidence, retrieve_reference_evidence, route_support
from evaluation.recommendation.harness import FIXTURE_DIR, expand_pool, run_engine, summarise
from evaluation.recommendation.relevance import combination_audit, describe, differences, dimensions_for, route_relevance
from evaluation.recommendation.repository import EVAL_MODE, EvaluationRepository

SETS = (("core", "scenarios.json"), ("personalisation", "scenarios_personalisation_v2.json"),
        ("privacy_audit", "scenarios_intersection.json"))
REVIEW_SETS = ("core", "personalisation")            # the privacy audit set is not for relevance reviewers
VERSION = "phase-8.2.1-v1"

# Why no pathway is shown. Worded so that no suppressed route, count or contributor is revealed.
NO_PATH = {
    "starting_point_unconfirmed": "This person's current role is not confirmed, so a starting point cannot be established and no "
                                  "pathway can be generated.",
    "insufficient_independent_evidence": "There is not enough independent evidence once this person's own contributed profile is "
                                         "excluded. A person's own career history is never used as evidence for their own pathways.",
    "no_independent_evidence": "There is no independent reference evidence for this move. A person's own career history is never "
                               "used as evidence for their own pathways.",
    "privacy_minimum_not_met": "No route can be shown while meeting the privacy requirement.",
    "no_observed_route": "No observed route was found in this evaluation sample. This does not establish that the career move is "
                         "impossible.",
}

# Permitted responses (also in docs/RECOMMENDATION_HUMAN_REVIEW.md; the generated files and the procedure must agree).
RESPONSES = {
    "q1_plausible": ["yes", "no", "unsure"],
    "q2_relevance": ["not_relevant", "weakly_relevant", "reasonably_relevant", "strongly_relevant", "cannot_assess"],
    "q3_intermediate_roles_sensible": ["yes", "no", "unsure", "not_applicable"],
    "q4_meaningfully_different": ["yes", "no", "not_applicable"],
    "q5_evidence_justifies": ["yes", "no", "unsure", "cannot_assess"],
}
Q6 = ["equal", "none_of_the_displayed", "cannot_assess", "no_pathway_shown"]        # plus pathway_a, pathway_b, ...


def _load(fixture_dir: Path, name: str):
    scen = json.loads((fixture_dir / name).read_text())
    pool_bytes = (fixture_dir / scen["pool"]).read_bytes()
    return expand_pool(json.loads(pool_bytes)), scen, hashlib.sha256((fixture_dir / name).read_bytes() + pool_bytes).hexdigest()


def no_path_reason(repo, user: dict, target: str) -> str:
    """The reason a scenario shows no pathway, from the evidence (internal; only the code and its text leave)."""
    origin = user.get("current_role")
    if not origin:
        return "starting_point_unconfirmed"
    own = list(user.get("own_profile_ids", []))
    support = lambda exclude: route_support(progression_routes_only(retrieve_reference_evidence(EvidenceRequest(  # noqa: E731
        destination_role_id=target, data_mode=EVAL_MODE, origin_role_id=origin, exclude_profile_ids=tuple(exclude)), repo)))
    after = support(own)
    if own:
        if recommendation_evidence(repo, target, EVAL_MODE, origin, []).route_summaries:
            return "insufficient_independent_evidence"
        if support([]) and not after:
            return "no_independent_evidence"
    return "privacy_minimum_not_met" if after else "no_observed_route"


def _run(fixture_dir: Path, with_audit: bool = True) -> List[dict]:
    sets = []
    for set_name, file in SETS:
        pool, scen, digest = _load(fixture_dir, file)
        repo = EvaluationRepository(pool)
        dims = dimensions_for(pool)
        threshold = repo.min_display_count()
        records = []
        for s in scen["scenarios"]:
            result, evidence = run_engine(repo, pool, s["user"], s["target"])
            production = summarise(result)
            role_meta = repo.get_roles(sorted({r for x in production for r in x["route_id"].split(">")}))
            rec = {"id": s["id"], "title": s["title"], "target": s["target"], "variant_of": s.get("variant_of"),
                   "changes": s.get("changes"), "user": s["user"], "production": production,
                   "relevance": [route_relevance(repo, evidence, x["route_id"], s["user"], threshold, role_meta, dims) for x in production],
                   "no_path_reason": None if production else no_path_reason(repo, s["user"], s["target"])}
            if with_audit:
                rec["_risks"] = {x["route_id"]: combination_audit(repo, evidence, x["route_id"], threshold, dims) for x in production}
                rec["_supporters"] = {x["route_id"]: sorted({o.provenance.profile_id for o in evidence.observations
                                                             if ">".join(o.route) == x["route_id"]}) for x in production} if evidence else {}
            records.append(rec)
        sets.append({"set": set_name, "pool_id": pool["pool_id"], "label": pool["label"], "fixture_sha256": digest,
                     "privacy_minimum": pool["privacy_minimum"], "dimensions": list(dims), "roles": pool["roles"],
                     "areas": pool["areas"], "scenarios": records})
    return sets


def run_shadow(fixture_dir: Path = FIXTURE_DIR, include_internal: bool = False) -> dict:
    out = {"prototype": f"whole-profile relevance, shadow mode ({VERSION})", "sets": _run(fixture_dir)}
    out["invariants"] = invariants(out, fixture_dir)
    out["comparisons"] = comparisons(out)
    for st in out["sets"]:
        for r in st["scenarios"]:
            risks = r.pop("_risks")
            r["combination_risks"] = {rid: [{"values": x["values"], "supporters_with_all": f"fewer than {st['privacy_minimum']}"}
                                            for x in lst] for rid, lst in risks.items() if lst}
            if include_internal:                         # tests only: exact counts and supporter ids, never written to files
                r["_risks_exact"], r["_supporters"] = risks, r["_supporters"]
            else:
                r.pop("_supporters")
    return out


def _by_id(out: dict) -> Dict[str, dict]:
    return {r["id"]: r for st in out["sets"] for r in st["scenarios"]}


def _dims_of(out: dict, sid: str) -> tuple:
    return next(tuple(st["dimensions"]) for st in out["sets"] if any(r["id"] == sid for r in st["scenarios"]))


def invariants(out: dict, fixture_dir: Path) -> Dict[str, dict]:
    res: Dict[str, dict] = {}

    def check(name, ok, detail=""):
        res[name] = {"ok": bool(ok), "detail": detail}

    disclosed_ok = missing_ok = own_ok = shown_ok = pop_ok = explained_ok = True
    for st in out["sets"]:
        t = st["privacy_minimum"]
        for r in st["scenarios"]:
            own = set(r["user"].get("own_profile_ids", []))
            explained_ok &= bool(r["production"]) or r["no_path_reason"] in NO_PATH
            for prod, rel in zip(r["production"], r["relevance"]):
                shown_ok &= rel["state"] == "compared"
                pop_ok &= rel.get("supporting_profiles") == prod["observation_count"]
                own_ok &= not (own & set(r["_supporters"].get(prod["route_id"], [])))
                for d in st["dimensions"]:
                    x = rel["dimensions"][d]
                    vals = x.get("matched", []) + x.get("reference_not_in_profile", []) + x.get("reference_patterns", [])
                    disclosed_ok &= all(c >= t for _, c in vals)
                    if x["status"] == "compared":
                        missing_ok &= bool(x["matched"] or x["profile_not_shown_among_supporters"])
    check("disclosed_values_meet_privacy_minimum", disclosed_ok)
    check("only_shown_routes_get_a_breakdown", shown_ok)
    check("population_equals_distinct_route_support", pop_ok)
    check("own_contribution_never_in_population", own_ok)
    check("compared_only_when_the_user_has_data", missing_ok)
    check("every_no_path_state_is_explained", explained_ok)
    ids = _by_id(out)
    iso = {}
    for r in ids.values():
        base = ids.get(r.get("variant_of") or "")
        if not base or r["changes"] == "population":
            continue
        dims = _dims_of(out, r["id"])
        theirs = {x["route_id"]: x for x in r["relevance"]}
        changed = set()
        for a in base["relevance"]:
            changed |= set(differences(a, theirs.get(a["route_id"], {}), dims))
        expected = set() if r["changes"] == "none" else set(r["changes"].split(","))
        iso[r["id"]] = (changed == expected, sorted(changed))          # exactly the intended dimensions, no more, no less
    check("one_changed_dimension_changes_exactly_that_dimension", all(ok for ok, _ in iso.values()), json.dumps(iso))
    check("unreviewed_skills_are_ignored", all(x["dimensions"]["skills"]["status"] == "not_recorded_in_profile" for x in ids["R10"]["relevance"]))
    check("missing_responsibilities_are_not_compared",
          all(x["dimensions"]["responsibilities"]["status"] == "not_recorded_in_profile" for x in ids["R09"]["relevance"]))
    check("suppressed_routes_get_no_breakdown", ids["R11"]["production"] == [] and ids["R11"]["relevance"] == [])
    check("own_exclusion_changes_the_population_exactly",
          [x["supporting_profiles"] for x in ids["R13"]["relevance"]]
          == [x["supporting_profiles"] - (1 if x["route_id"] == "PM-PM>PM-SPM" else 0) for x in ids["R01"]["relevance"]])
    i01 = ids["I01"]["_risks"].get("PM-PM>PM-SPM", [])
    check("combination_risk_detected", any(len(x["values"]) == 3 for x in i01) and not any(len(x["values"]) == 2 for x in i01))
    check("mba_is_a_type_not_a_level", all(r["user"].get("qualification_levels", []) != ["mba"] for st in out["sets"][1:] for r in st["scenarios"]))
    again = _run(fixture_dir, with_audit=False)
    strip = lambda sets: [[{k: r[k] for k in ("id", "production", "relevance", "no_path_reason")} for r in st["scenarios"]] for st in sets]  # noqa: E731
    check("deterministic", json.dumps(strip(again), sort_keys=True) == json.dumps(strip(out["sets"]), sort_keys=True))
    base_file = Path(__file__).resolve().parent / "output" / "baseline.json"
    if base_file.exists():
        core = {r["id"]: r["production"] for r in out["sets"][0]["scenarios"]}
        check("production_output_identical_to_phase_8_1_baseline",
              all(core[s["id"]] == s["shown_routes"] for s in json.loads(base_file.read_text())["scenarios"]))
    return res


def comparisons(out: dict) -> List[dict]:
    ids = _by_id(out)
    rows = []
    for a, b, why in (("R01", "R02", "Profile A vs Profile B (different backgrounds, same roles and target)"),
                      ("S16A", "S16B", "Phase 8.1 core: backgrounds A and B (core pool has no supporting attributes)"),
                      ("R01", "R13", "Profile A with and without its own contribution in the population"),
                      ("R01", "R07", "Profile A with a different qualification (level and type)"),
                      ("R01", "R14", "Profile A with a different field of study")):
        ra, rb = ids[a], ids[b]
        dims = _dims_of(out, a)
        theirs = {y["route_id"]: y for y in rb["relevance"]}
        rows.append({"pair": [a, b], "why": why, "production_identical": ra["production"] == rb["production"],
                     "production_order": [[p["route_id"] for p in ra["production"]], [p["route_id"] for p in rb["production"]]],
                     "production_tiers": [[p["tier"] for p in ra["production"]], [p["tier"] for p in rb["production"]]],
                     "relevance_differs_in": {x["route_id"]: differences(x, theirs.get(x["route_id"], {}), dims) for x in ra["relevance"]}})
    return rows


# --------------------------------------------------------------------------- report
def report(out: dict) -> str:
    L = [f"# Whole-profile relevance prototype: shadow evaluation ({VERSION})", "",
         "> Evaluation fixtures only. Constructed careers, not real people and not market evidence. The prototype runs beside "
         "the production engine; it does not change production ranking, labels or output.", "",
         "## Invariants", "", "| Invariant | Result |", "|---|---|"]
    L += [f"| {k} | {'pass' if v['ok'] else 'FAIL ' + v['detail']} |" for k, v in out["invariants"].items()]
    L += ["", "## Profile comparisons", ""]
    for c in out["comparisons"]:
        L.append(f"- **{c['pair'][0]} vs {c['pair'][1]}** ({c['why']}): production identical: {'yes' if c['production_identical'] else 'no'}; "
                 "relevance differs in " + ("; ".join(f"`{k}`: {', '.join(v) or 'nothing'}" for k, v in c["relevance_differs_in"].items())
                                            or "no shown route"))
    L += ["", "## Privacy combination audit (evaluation only; not a disclosure rule)", "",
          "Combinations of individually disclosed values (each shared by at least the minimum) that fewer than the minimum "
          "supporters share. Exact small counts are never printed.", ""]
    risky = [(st["set"], r["id"], rid, x) for st in out["sets"] for r in st["scenarios"] for rid, lst in r["combination_risks"].items() for x in lst]
    L += [f"- {s} {sid} `{rid}`: " + " + ".join(f"{d}={v}" for d, v in x["values"]) + f" ({x['supporters_with_all']})" for s, sid, rid, x in risky] \
        or ["- none"]
    for st in out["sets"]:
        L += ["", f"## Scenario set: {st['set']} (`{st['pool_id']}`)", "", f"> {st['label']}", ""]
        for r in st["scenarios"]:
            L += [f"### {r['id']}: {r['title']}", ""]
            if not r["production"]:
                L += [f"No route shown ({r['no_path_reason']}): {NO_PATH[r['no_path_reason']]}", ""]
                continue
            for p, rel in zip(r["production"], r["relevance"]):
                L.append(f"- `{p['route_id']}`: production rank {p['rank']} (group {p['rank_group']}{', tied' if p['tied'] else ''}), "
                         f"fit {p['tier']}; {p['observation_count']} supporting profiles")
                L += [f"    - {line}" for line in describe(rel, tuple(st["dimensions"]))]
            L.append("")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- review pack
def _title(st: dict, role: str) -> str:
    return st["roles"].get(role, {}).get("title", role)


def _csv(rows: List[List[str]]) -> str:
    return "".join(",".join(f'"{c}"' if ("," in c or '"' in c) else c for c in row) + "\n" for row in rows)


INSTRUCTIONS = """# Trace pathway review pack (evaluation fixtures)

These are constructed test careers, not real people, and the evidence comes from constructed test profiles. Please
judge each pathway as you would for a real person with this background. Pathways are listed in an arbitrary order;
Trace's own ordering, labels and scores are deliberately hidden.

Record every answer in your own review forms only (`review_reviewer_<A|B>_pathways.csv` and
`review_reviewer_<A|B>_scenarios.csv`). Use exactly one of the permitted responses; explain any judgment in `notes`.

## Per pathway (pathways form)

| Question | Permitted responses |
|---|---|
| Q1 Is the pathway plausible given this person's background? | yes / no / unsure |
| Q2 How relevant is it to their current experience and target? | not_relevant / weakly_relevant / reasonably_relevant / strongly_relevant / cannot_assess |
| Q3 Are the intermediate roles sensible? | yes / no / unsure / not_applicable |
| Q4 Is it meaningfully different from the alternatives? | yes / no / not_applicable |
| Q5 Does the supporting evidence justify presenting it? | yes / no / unsure / cannot_assess |

Q3 is already `not_applicable` for a direct move (no intermediate role) and Q4 is already `not_applicable` when only one
pathway is shown. Change a pre-filled answer only if you think it is wrong, and say why.

## Per scenario (scenarios form)

| Question | Permitted responses |
|---|---|
| Q6 Which pathway would you prioritise? | pathway_a / pathway_b / ... (one per displayed pathway) / equal / none_of_the_displayed / cannot_assess / no_pathway_shown |
| Q7 Why? | free text |
| Q8 What information is missing to make a better recommendation? | free text |

## Scenarios with no pathway shown

Some scenarios show no pathway, with the reason. There are no pathway rows for them. In the scenarios form, Q6 is
already `no_pathway_shown`; Q7 is optional; please use Q8 to say what you would have expected or what information would
help (for example, whether you think a pathway should exist).
"""


def review_pack(out: dict) -> Dict[str, str]:
    md = [INSTRUCTIONS]
    path_rows = [["scenario_id", "pathway", "route_id", "has_intermediate_roles", "pathways_in_scenario",
                  "q1_plausible", "q2_relevance", "q3_intermediate_roles_sensible", "q4_meaningfully_different",
                  "q5_evidence_justifies", "notes"]]
    scen_rows = [["scenario_id", "pathways_shown", "q6_prioritised_pathway", "q7_why", "q8_missing_information"]]
    adj_path = [["scenario_id", "pathway", "route_id"] + [f"{q}_{who}" for q in ("q1", "q2", "q3", "q4", "q5") for who in ("a", "b")]
                + ["material_disagreement_questions", "escalate_to_reviewer_c", "reviewer_c_decision", "reviewer_c_rationale"]]
    adj_scen = [["scenario_id", "reviewer_a_prioritised_pathway", "reviewer_b_prioritised_pathway", "material_difference",
                 "reviewer_c_final_decision", "reviewer_c_rationale", "missing_information_identified"]]
    for st in out["sets"]:
        if st["set"] not in REVIEW_SETS:
            continue
        for r in st["scenarios"]:
            u = r["user"]
            quals = u.get("qualifications", []) + u.get("qualification_levels", []) + u.get("qualification_types", [])
            md += [f"## {r['id']}", "",
                   f"- Current role: {_title(st, u['current_role']) if u.get('current_role') else 'not confirmed yet'}",
                   f"- Earlier roles: {', '.join(_title(st, x) for x in u.get('held_roles', []) if x != u.get('current_role')) or 'none recorded'}",
                   f"- Responsibilities: {', '.join(st['areas'].get(a, {}).get('label', a) for a in u.get('areas', [])) or 'none categorised'}",
                   f"- Skills: {', '.join(u.get('skills', [])) or 'none confirmed'}",
                   f"- Industries: {', '.join(u.get('industries', [])) or 'not recorded'}",
                   f"- Qualifications: {', '.join(quals) or 'not recorded'}",
                   f"- Field of study: {', '.join(u.get('fields', [])) or 'not recorded'}",
                   f"- Target role: {_title(st, r['target'])}", ""]
            paths = sorted(zip(r["production"], r["relevance"]),
                           key=lambda pr: hashlib.sha256(f"{r['id']}|{pr[0]['route_id']}".encode()).hexdigest())
            if not paths:
                md += [f"**No pathway shown.** {NO_PATH[r['no_path_reason']]}", ""]
                scen_rows.append([r["id"], "none", "no_pathway_shown", "", "PENDING"])
                adj_scen.append([r["id"], "no_pathway_shown", "no_pathway_shown", "no", "", "", ""])
                continue
            labels = []
            for k, (p, rel) in enumerate(paths, start=1):
                label = f"pathway_{chr(96 + k)}"
                labels.append(label)
                roles = p["route_id"].split(">")
                md.append(f"**Pathway {chr(64 + k)}:** " + " → ".join(_title(st, x) for x in roles))
                md.append(f"- Intermediate roles: {', '.join(_title(st, x) for x in roles[1:-1]) or 'none (a direct move)'}")
                md.append(f"- {p['observation_count']} supporting test profiles followed this route")
                for d in st["dimensions"]:
                    x = rel["dimensions"][d]
                    vals = x.get("matched", []) + x.get("reference_not_in_profile", []) + x.get("reference_patterns", [])
                    if vals:
                        md.append(f"- {d.replace('_', ' ')} common among them: " + ", ".join(
                            f"{_title(st, v) if d == 'previous_roles' else st['areas'].get(v, {}).get('label', v)} ({c})"
                            for v, c in sorted(vals, key=lambda z: (-z[1], z[0]))))
                md.append("")
                path_rows.append([r["id"], label, p["route_id"], "yes" if len(roles) > 2 else "no", str(len(paths)), "PENDING", "PENDING",
                                  "PENDING" if len(roles) > 2 else "not_applicable", "PENDING" if len(paths) > 1 else "not_applicable",
                                  "PENDING", ""])
                adj_path.append([r["id"], label, p["route_id"]] + ["PENDING"] * 10 + ["", "", "", ""])
            scen_rows.append([r["id"], ";".join(labels), "PENDING", "PENDING", "PENDING"])
            adj_scen.append([r["id"], "PENDING", "PENDING", "PENDING", "", "", ""])
    pathways, scenarios = _csv(path_rows), _csv(scen_rows)
    return {"review_pack.md": "\n".join(md) + "\n",
            "review_reviewer_A_pathways.csv": pathways, "review_reviewer_B_pathways.csv": pathways,
            "review_reviewer_A_scenarios.csv": scenarios, "review_reviewer_B_scenarios.csv": scenarios,
            "review_adjudication.csv": _csv(adj_path), "review_adjudication_scenarios.csv": _csv(adj_scen)}
