"""Run the recommendation evaluation harness on the evaluation-only fixtures and write the baseline.

    cd backend && .venv/bin/python -m evaluation.recommendation [--out evaluation/recommendation/output]

Writes (deterministic: no timestamps; identical inputs give byte-identical files):
  baseline.json                  every scenario's inputs, routes, suppression, ties, fit, explanation, invariants
  baseline_report.md             the same, readable, with coverage and failing invariants
  human_review_template.csv      one row per shown route; every judgment PENDING (for product and domain reviewers)
  pairwise_review_template.csv   one row per pair of shown routes in a scenario; preference PENDING
Never touches a database, an API or the reference pools. Exit code 1 if any automated invariant fails.
"""
import argparse
import csv
import json
import sys
from itertools import combinations
from pathlib import Path

from evaluation.recommendation.harness import run_all

REVIEW_COLUMNS = ["scenario_id", "target", "route_id", "route_plausible_1to5", "relevant_to_background_1to5",
                  "intermediate_roles_make_sense", "explanation_supported_by_evidence", "alternative_meaningfully_different",
                  "user_should_consider", "reviewer", "notes"]
PAIR_COLUMNS = ["scenario_id", "route_a", "route_b", "preferred_for_this_user", "reason", "reviewer"]


def report(b: dict) -> str:
    lines = [f"# Recommendation evaluation baseline ({b['harness_version']})", "",
             f"> {b['label']}", "",
             f"Pool `{b['pool_id']}`: {b['profiles']} evaluation profiles; privacy minimum {b['privacy_minimum']}; "
             f"fixture sha256 `{b['fixture_sha256'][:16]}`.", ""]
    names = sorted({k for s in b["scenarios"] for k in s["invariants"]})
    failing = [(s["id"], k) for s in b["scenarios"] for k, v in s["invariants"].items() if not v["ok"]]
    covers = sorted({c for s in b["scenarios"] for c in s["covers"]})
    tiers = sorted({r["tier"] for s in b["scenarios"] for r in s["shown_routes"]})
    lims = sorted({lim for s in b["scenarios"] for r in s["shown_routes"] for lim in r["limitations"]})
    lines += ["## Summary", "",
              f"- Scenarios: {len(b['scenarios'])}. Coverage tags: {', '.join(covers)}.",
              f"- Automated invariants per scenario: {len(names)} ({', '.join(names)}).",
              f"- Failing invariants: {len(failing)}" + (f" ({failing})" if failing else "") + ".",
              f"- Fit tiers reached: {', '.join(tiers)}. Fit limitations reached: {', '.join(lims) or 'none'}.",
              f"- Scenarios with suppressed routes: {sum(1 for s in b['scenarios'] if s['suppressed_routes'])}; "
              f"with ties: {sum(1 for s in b['scenarios'] if s['tie_groups'])}; with no shown route: "
              f"{sum(1 for s in b['scenarios'] if not s['shown_routes'])}.",
              "- Baseline sensitivity (recorded, not judged): earlier roles changed the order in "
              f"{sum(1 for s in b['scenarios'] if s['sensitivity']['earlier_roles_changed_order'])} scenario(s) and the route set in "
              f"{sum(1 for s in b['scenarios'] if s['sensitivity']['earlier_roles_changed_routes'])}.",
              "- Human relevance judgments: none yet (templates generated, all PENDING).", "",
              "## Scenarios", ""]
    for s in b["scenarios"]:
        i = s["inputs"]
        lines += [f"### {s['id']}: {s['title']}", "",
                  f"- Input: current role `{i['current_role']}`" + (f" (unknown: {i['position_reason']})" if not i["current_role"] else "")
                  + f"; held {i['held_roles']}; areas {i['areas']}; skills {i['skills']}; industries {i['industries']}; "
                  f"qualifications {i['qualifications']}" + (f"; own contribution {i['own_profile_ids']}" if i.get("own_profile_ids") else "")
                  + f"; target `{s['target']}`.",
                  f"- Eligible evaluation population: {s['eligible_population']} profiles.",
                  f"- Discovered routes (after own-contribution and zero-move exclusion): "
                  + (", ".join(f"`{d['route_id']}` ({d['distinct_profiles']})" for d in s["discovered_routes"]) or "none") + ".",
                  f"- Suppressed (below the privacy minimum): "
                  + (", ".join(f"`{d['route_id']}` ({d['distinct_profiles']})" for d in s["suppressed_routes"]) or "none") + ".", ""]
        if s["shown_routes"]:
            lines += ["| Rank | Group | Tied | Route | Tier | Reason | Limitations | Profiles | Step gaps | Direction |",
                      "|---|---|---|---|---|---|---|---|---|---|"]
            for r in s["shown_routes"]:
                lines.append(f"| {r['rank']} | {r['rank_group']} | {'yes' if r['tied'] else 'no'} | `{r['route_id']}` | {r['tier']} | "
                             f"{r['reason']} | {', '.join(r['limitations']) or '-'} | {r['observation_count']} | "
                             f"{', '.join(r['step_gaps']) or '-'} | {' / '.join(s['directions'][r['route_id']])} |")
        else:
            lines.append("No route shown.")
        bad = [k for k, v in s["invariants"].items() if not v["ok"]]
        lines += ["", f"- Invariants: {'all pass' if not bad else 'FAIL: ' + ', '.join(bad)}. Sensitivity: "
                  + ", ".join(f"{k}={v}" for k, v in s["sensitivity"].items()) + ".", ""]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "output"))
    ap.add_argument("--shadow", action="store_true",
                    help="also run the whole-profile relevance prototype (shadow mode, Phase 8.2.1) and write the review pack")
    ap.add_argument("--reproduce-phase-8-2", metavar="DIR",
                    help="write the frozen Phase 8.2 shadow outputs to DIR (for comparison with output/phase-8.2/)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    b = run_all()
    (out / "baseline.json").write_text(json.dumps(b, indent=1, sort_keys=True) + "\n")
    (out / "baseline_report.md").write_text(report(b))
    with open(out / "human_review_template.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(REVIEW_COLUMNS)
        for s in b["scenarios"]:
            for r in s["shown_routes"]:
                w.writerow([s["id"], s["target"], r["route_id"]] + ["PENDING"] * 6 + ["", ""])
    with open(out / "pairwise_review_template.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(PAIR_COLUMNS)
        for s in b["scenarios"]:
            for x, y in combinations([r["route_id"] for r in s["shown_routes"]], 2):
                w.writerow([s["id"], x, y, "PENDING", "", ""])
    failing = sum(1 for s in b["scenarios"] for v in s["invariants"].values() if not v["ok"])
    print(f"{len(b['scenarios'])} scenarios, {failing} failing invariants; written to {out}")
    if a.reproduce_phase_8_2:
        from evaluation.recommendation import shadow_phase82 as old
        d = Path(a.reproduce_phase_8_2)
        d.mkdir(parents=True, exist_ok=True)
        sh = old.run_shadow()
        (d / "shadow.json").write_text(json.dumps(sh, indent=1, sort_keys=True) + "\n")
        (d / "shadow_report.md").write_text(old.report(sh))
        for name, text in old.review_pack(sh).items():
            (d / name).write_text(text)
        print(f"Phase 8.2 shadow outputs reproduced in {d}")
    if a.shadow:
        from evaluation.recommendation.shadow import report as shadow_report, review_pack, run_shadow
        sh = run_shadow()
        (out / "shadow.json").write_text(json.dumps(sh, indent=1, sort_keys=True) + "\n")
        (out / "shadow_report.md").write_text(shadow_report(sh))
        for name, text in review_pack(sh).items():
            (out / name).write_text(text)
        shadow_failing = sum(1 for v in sh["invariants"].values() if not v["ok"])
        print(f"shadow prototype: {sum(len(st['scenarios']) for st in sh['sets'])} scenarios, {shadow_failing} failing invariants")
        failing += shadow_failing
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
