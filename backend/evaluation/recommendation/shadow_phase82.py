"""FROZEN COPY (Phase 8.2.1): the Phase 8.2 shadow run exactly as delivered, kept only so its outputs stay reproducible
(python -m evaluation.recommendation --reproduce-phase-8-2 DIR). Do not change; new work goes in shadow.py.

Shadow evaluation of the Phase 8.2 whole-profile relevance prototype, and the human-review pack.

Runs every scenario of the Phase 8.1 core set and the Phase 8.2 personalisation set through the PRODUCTION
composition (unchanged), then computes the prototype's relevance breakdown for each route production shows. The
production output is recorded as is; the prototype never re-orders, filters or labels it. The Phase 8.1 baseline files
are not touched (python -m evaluation.recommendation without --shadow writes exactly what it always did).
"""
import hashlib
import json
from pathlib import Path
from typing import Dict, List

from evaluation.recommendation.harness import FIXTURE_DIR, expand_pool, run_engine, summarise
from evaluation.recommendation.relevance import DIMENSIONS, describe, differences, route_relevance
from evaluation.recommendation.repository import EvaluationRepository

SETS = (("core", "scenarios.json"), ("personalisation", "scenarios_personalisation.json"))
RUBRIC = ["not_relevant", "weakly_relevant", "reasonably_relevant", "strongly_relevant", "cannot_assess"]


def _load(fixture_dir: Path, name: str):
    scen = json.loads((fixture_dir / name).read_text())
    pool_bytes = (fixture_dir / scen["pool"]).read_bytes()
    return expand_pool(json.loads(pool_bytes)), scen, hashlib.sha256((fixture_dir / name).read_bytes() + pool_bytes).hexdigest()


def run_shadow(fixture_dir: Path = FIXTURE_DIR) -> dict:
    out = {"prototype": "whole-profile relevance, shadow mode (phase-8.2-v1)", "sets": []}
    for set_name, file in SETS:
        pool, scen, digest = _load(fixture_dir, file)
        repo = EvaluationRepository(pool)
        threshold = repo.min_display_count()
        records = []
        for s in scen["scenarios"]:
            result, evidence = run_engine(repo, pool, s["user"], s["target"])
            production = summarise(result)
            role_meta = repo.get_roles(sorted({r for x in production for r in x["route_id"].split(">")}))
            relevance = [route_relevance(repo, evidence, x["route_id"], s["user"], threshold, role_meta) for x in production]
            supporters = {x["route_id"]: sorted({o.provenance.profile_id for o in evidence.observations if ">".join(o.route) == x["route_id"]})
                          for x in production} if evidence else {}
            records.append({"id": s["id"], "title": s["title"], "target": s["target"], "variant_of": s.get("variant_of"),
                            "changes": s.get("changes"), "user": s["user"], "production": production, "relevance": relevance,
                            "_supporters": supporters})
        out["sets"].append({"set": set_name, "pool_id": pool["pool_id"], "label": pool["label"], "fixture_sha256": digest,
                            "privacy_minimum": pool["privacy_minimum"], "roles": pool["roles"], "areas": pool["areas"],
                            "scenarios": records})
    out["invariants"] = invariants(out)
    out["comparisons"] = comparisons(out)
    for st in out["sets"]:                      # supporter ids are evaluation fixtures, but no report needs them
        for r in st["scenarios"]:
            r.pop("_supporters")
    return out


def _by_id(out: dict) -> Dict[str, dict]:
    return {r["id"]: r for st in out["sets"] for r in st["scenarios"]}


def invariants(out: dict) -> Dict[str, dict]:
    res: Dict[str, dict] = {}

    def check(name, ok, detail=""):
        res[name] = {"ok": bool(ok), "detail": detail}

    disclosed_ok, missing_ok, own_ok, shown_ok, pop_ok = True, True, True, True, True
    for st in out["sets"]:
        t = st["privacy_minimum"]
        for r in st["scenarios"]:
            own = set(r["user"].get("own_profile_ids", []))
            for prod, rel in zip(r["production"], r["relevance"]):
                shown_ok &= rel["state"] == "compared"                      # production shows only routes >= minimum
                pop_ok &= rel.get("supporting_profiles") == prod["observation_count"]
                own_ok &= not (own & set(r["_supporters"].get(prod["route_id"], [])))
                for d in DIMENSIONS:
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
    ids = _by_id(out)
    iso = {}
    for r in ids.values():
        base = ids.get(r.get("variant_of") or "")
        if not base or r["changes"] == "population":
            continue
        changed = set()
        theirs = {x["route_id"]: x for x in r["relevance"]}
        for a in base["relevance"]:                        # matched by route id: production order may differ
            changed |= set(differences(a, theirs.get(a["route_id"], {})))
        expected = set() if r["changes"] == "none" else set(r["changes"].split(","))
        iso[r["id"]] = (changed <= expected, sorted(changed))
    check("one_changed_dimension_changes_only_that_dimension", all(ok for ok, _ in iso.values()), json.dumps(iso))
    check("unreviewed_skills_are_ignored", all(x["dimensions"]["skills"]["status"] == "not_recorded_in_profile"
                                               for x in ids["R10"]["relevance"]))
    check("missing_responsibilities_are_not_compared", all(x["dimensions"]["responsibilities"]["status"] == "not_recorded_in_profile"
                                                           for x in ids["R09"]["relevance"]))
    check("suppressed_routes_get_no_breakdown", ids["R11"]["production"] == [] and ids["R11"]["relevance"] == [])
    r13, r01 = ids["R13"], ids["R01"]
    check("own_exclusion_changes_the_population_exactly",
          [x["supporting_profiles"] for x in r13["relevance"]] == [x["supporting_profiles"] - (1 if x["route_id"] == "PM-PM>PM-SPM" else 0)
                                                                    for x in r01["relevance"]])
    again = run_shadow_without_checks()
    check("deterministic", json.dumps(again, sort_keys=True) == json.dumps(strip(out), sort_keys=True))
    baseline = json.loads((Path(__file__).resolve().parent / "output" / "baseline.json").read_text()) \
        if (Path(__file__).resolve().parent / "output" / "baseline.json").exists() else None
    if baseline:
        core = {r["id"]: r["production"] for r in out["sets"][0]["scenarios"]}
        check("production_output_identical_to_phase_8_1_baseline",
              all(core[s["id"]] == s["shown_routes"] for s in baseline["scenarios"]))
    return res


def strip(out: dict) -> dict:
    return {"sets": [{"set": st["set"], "scenarios": [{"id": r["id"], "production": r["production"], "relevance": r["relevance"]}
                                                     for r in st["scenarios"]]} for st in out["sets"]]}


def run_shadow_without_checks(fixture_dir: Path = FIXTURE_DIR) -> dict:
    sets = []
    for set_name, file in SETS:
        pool, scen, _ = _load(fixture_dir, file)
        repo = EvaluationRepository(pool)
        recs = []
        for s in scen["scenarios"]:
            result, evidence = run_engine(repo, pool, s["user"], s["target"])
            production = summarise(result)
            role_meta = repo.get_roles(sorted({r for x in production for r in x["route_id"].split(">")}))
            recs.append({"id": s["id"], "production": production,
                         "relevance": [route_relevance(repo, evidence, x["route_id"], s["user"], repo.min_display_count(), role_meta)
                                       for x in production]})
        sets.append({"set": set_name, "scenarios": recs})
    return {"sets": sets}


def comparisons(out: dict) -> List[dict]:
    ids = _by_id(out)
    rows = []
    for a, b, why in (("R01", "R02", "Profile A vs Profile B (different backgrounds, same roles and target)"),
                      ("S16A", "S16B", "Phase 8.1 core: backgrounds A and B (core pool has no supporting attributes)"),
                      ("R01", "R13", "Profile A with and without its own contribution in the population")):
        ra, rb = ids[a], ids[b]
        per_route = {}
        theirs = {y["route_id"]: y for y in rb["relevance"]}
        for x in ra["relevance"]:                          # matched by route id
            per_route[x["route_id"]] = differences(x, theirs.get(x["route_id"], {}))
        rows.append({"pair": [a, b], "why": why,
                     "production_identical": ra["production"] == rb["production"],
                     "production_order": [[p["route_id"] for p in ra["production"]], [p["route_id"] for p in rb["production"]]],
                     "production_tiers": [[p["tier"] for p in ra["production"]], [p["tier"] for p in rb["production"]]],
                     "production_routes": [p["route_id"] for p in ra["production"]],
                     "relevance_differs_in": per_route})
    return rows


# --------------------------------------------------------------------------- report and review pack
def report(out: dict) -> str:
    L = ["# Whole-profile relevance prototype: shadow evaluation (phase-8.2-v1)", "",
         "> Evaluation fixtures only. Constructed careers, not real people and not market evidence. The prototype runs "
         "beside the production engine; it does not change production ranking, labels or output.", ""]
    inv = out["invariants"]
    L += ["## Invariants", "", "| Invariant | Result |", "|---|---|"]
    L += [f"| {k} | {'pass' if v['ok'] else 'FAIL ' + v['detail']} |" for k, v in inv.items()]
    L += ["", "## Profile comparisons", ""]
    for c in out["comparisons"]:
        L.append(f"- **{c['pair'][0]} vs {c['pair'][1]}** ({c['why']}): production output identical: "
                 f"{'yes' if c['production_identical'] else 'no'}; routes {c['production_routes']}; relevance differs in "
                 + ("; ".join(f"`{k}`: {', '.join(v) or 'nothing'}" for k, v in c["relevance_differs_in"].items()) or "no shown route"))
    for st in out["sets"]:
        L += ["", f"## Scenario set: {st['set']} (`{st['pool_id']}`)", "", f"> {st['label']}", ""]
        for r in st["scenarios"]:
            L += [f"### {r['id']}: {r['title']}", ""]
            if not r["production"]:
                L += ["Production shows no route; the prototype produces nothing.", ""]
                continue
            for p, rel in zip(r["production"], r["relevance"]):
                L.append(f"- `{p['route_id']}`: production rank {p['rank']} (group {p['rank_group']}{', tied' if p['tied'] else ''}), "
                         f"fit {p['tier']}; {p['observation_count']} supporting profiles")
                L += [f"    - {line}" for line in describe(rel)]
            L.append("")
    return "\n".join(L) + "\n"


def _title(st: dict, role: str) -> str:
    return st["roles"].get(role, {}).get("title", role)


def review_pack(out: dict) -> Dict[str, str]:
    """Reviewer-facing material: backgrounds and candidate pathways in a NEUTRAL order (hash of scenario and route),
    with aggregate supporting evidence. No production rank, tier, fit, limitation or prototype match is shown."""
    md = ["# Trace pathway review pack (evaluation fixtures)", "",
          "These are constructed test careers, not real people, and the evidence is from constructed test profiles. "
          "Please judge each pathway as you would for a real person with this background.", "",
          "Pathways are listed in an arbitrary order. Trace's own ordering and labels are deliberately hidden.", "",
          "For each pathway answer, in your own reviewer file:",
          "1. Is it plausible given this person's background? 2. Is it relevant to their experience and target? "
          "3. Are the intermediate roles sensible? 4. Is it meaningfully different from the alternatives? "
          "5. Does the supporting evidence justify presenting it?", "",
          "For each scenario: 6. Which pathway would you prioritise? 7. Why? 8. What information is missing to make a better "
          "recommendation?", "",
          f"Relevance rubric: {', '.join(RUBRIC)}. Explain any judgment in the notes column.", ""]
    route_rows, scenario_rows = [], []
    for st in out["sets"]:
        for r in st["scenarios"]:
            u = r["user"]
            md += [f"## {r['id']}", "",
                   f"- Current role: {_title(st, u['current_role']) if u.get('current_role') else 'not confirmed yet'}",
                   f"- Earlier roles: {', '.join(_title(st, x) for x in u.get('held_roles', []) if x != u.get('current_role')) or 'none recorded'}",
                   f"- Responsibilities: {', '.join(st['areas'].get(a, {}).get('label', a) for a in u.get('areas', [])) or 'none categorised'}",
                   f"- Skills: {', '.join(u.get('skills', [])) or 'none confirmed'}",
                   f"- Industries: {', '.join(u.get('industries', [])) or 'not recorded'}",
                   f"- Qualifications: {', '.join(u.get('qualifications', []) + u.get('fields', [])) or 'not recorded'}",
                   f"- Target role: {_title(st, r['target'])}", ""]
            paths = sorted(zip(r["production"], r["relevance"]),
                           key=lambda pr: hashlib.sha256(f"{r['id']}|{pr[0]['route_id']}".encode()).hexdigest())
            if not paths:
                md += ["No pathway has enough supporting evidence to be shown. (Would you expect one? Note it in the scenario file.)", ""]
            for k, (p, rel) in enumerate(paths, start=1):
                label = f"Pathway {chr(64 + k)}"
                md.append(f"**{label}:** " + " → ".join(_title(st, x) for x in p["route_id"].split(">")))
                ev = [f"{p['observation_count']} supporting test profiles followed this route"]
                for d in DIMENSIONS:
                    x = rel["dimensions"][d]
                    vals = x.get("matched", []) + x.get("reference_not_in_profile", []) + x.get("reference_patterns", [])
                    if vals:
                        ev.append(f"{d.replace('_', ' ')} common among them: " + ", ".join(
                            f"{_title(st, v) if d == 'previous_roles' else st['areas'].get(v, {}).get('label', v)} ({c})" for v, c in sorted(vals, key=lambda z: (-z[1], z[0]))))
                md += [f"- {e}" for e in ev] + [""]
                route_rows.append([r["id"], label, p["route_id"]])
            scenario_rows.append([r["id"], ";".join(f"Pathway {chr(64 + k)}" for k in range(1, len(paths) + 1))])
    head = "scenario_id,pathway,route_id,q1_plausible_yes_no_unsure,q2_relevance_rubric,q3_intermediate_roles_sensible,q4_meaningfully_different,q5_evidence_justifies,notes\n"
    route_csv = head + "".join(f"{s},{p},{rid},PENDING,PENDING,PENDING,PENDING,PENDING,\n" for s, p, rid in route_rows)
    scen_head = "scenario_id,pathways,q6_prioritised_pathway,q7_why,q8_missing_information\n"
    scen_csv = scen_head + "".join(f"{s},{ps},PENDING,PENDING,PENDING\n" for s, ps in scenario_rows)
    adj = ("scenario_id,pathway,route_id,reviewer_a_rubric,reviewer_b_rubric,material_disagreement,third_reviewer_rubric,"
           "resolution_notes\n" + "".join(f"{s},{p},{rid},PENDING,PENDING,PENDING,,\n" for s, p, rid in route_rows))
    return {"review_pack.md": "\n".join(md) + "\n", "review_reviewer_A_pathways.csv": route_csv, "review_reviewer_B_pathways.csv": route_csv,
            "review_reviewer_A_scenarios.csv": scen_csv, "review_reviewer_B_scenarios.csv": scen_csv, "review_adjudication.csv": adj}
