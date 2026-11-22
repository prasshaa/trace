"""Score Trace's reading of experience lines against the hand-annotated synthetic sets.

    .venv/bin/python -m evaluation.capability_interpretation             development set (used while building)
    .venv/bin/python -m evaluation.capability_interpretation --holdout   held-out set (score once, after a freeze)

Metrics, on CLEAR readings (what counts as "evidence found"):
  precision, recall          per responsibility area and micro-averaged
  false positives            clear readings the annotation doesn't support (a related-only area read as clear counts)
  false negatives            annotated clear areas not read as clear
  false-positive rate        false positives / (lines x areas not annotated clear)
  unsupported claims         clear readings of a DECISION-RIGHTS area (roadmap, strategy, outcome ownership) that the
                             annotation doesn't support at all: the error that would most mislead a plan
  partial-as-found           related-only areas read as clear (over-claiming strength)
And per capability requirement (C1-C6, from the pm-v1.2 direct areas): would the line make it "evidence found"?
"""
import sys
from collections import Counter
from typing import Dict, List, Set, Tuple

from app.services.capability.interpret import CUES, interpret

AREAS = sorted({c.area for c in CUES})
DECISION_RIGHTS = {"roadmap_ownership", "strategy_portfolio", "outcome_ownership"}
CAPABILITIES = {"C1": {"product_discovery", "design_research"}, "C2": {"roadmap_ownership", "strategy_portfolio"},
                "C3": {"requirements_analysis", "prioritisation_backlog"}, "C4": {"outcome_ownership"},
                "C5": {"stakeholder_management", "executive_communication"}, "C6": {"delivery_execution"}}


def score(items: List[Tuple[str, Set[str], Set[str]]], show: bool = True) -> dict:
    tp, fp, fn = Counter(), Counter(), Counter()
    unsupported, partial_as_found, failures = 0, 0, []
    cap = Counter()
    for text, clear, related in items:
        r = interpret(text)
        got = set(r.clear)
        for a in got & clear:
            tp[a] += 1
        for a in got - clear:
            fp[a] += 1
            if a in related:
                partial_as_found += 1
            elif a in DECISION_RIGHTS:
                unsupported += 1
        for a in clear - got:
            fn[a] += 1
        for c, areas in CAPABILITIES.items():
            want, have = bool(areas & clear), bool(areas & got)
            cap[(c, want, have)] += 1
        if got != clear:
            failures.append((text, sorted(got - clear), sorted(clear - got)))
    TP, FP, FN = sum(tp.values()), sum(fp.values()), sum(fn.values())
    negatives = len(items) * len(AREAS) - sum(len(c) for _, c, _ in items)
    out = {"lines": len(items), "precision": TP / (TP + FP) if TP + FP else 1.0, "recall": TP / (TP + FN) if TP + FN else 1.0,
           "false_positives": FP, "false_negatives": FN, "fp_rate": FP / negatives if negatives else 0.0,
           "unsupported_decision_rights_claims": unsupported, "related_read_as_found": partial_as_found,
           "per_area": {a: (tp[a], fp[a], fn[a]) for a in AREAS}, "failures": failures,
           "capability": {c: {"tp": cap[(c, True, True)], "fp": cap[(c, False, True)], "fn": cap[(c, True, False)]}
                          for c in CAPABILITIES}}
    if show:
        print(f"lines {out['lines']}  precision {out['precision']:.3f}  recall {out['recall']:.3f}  "
              f"FP {FP} (rate {out['fp_rate']:.4f})  FN {FN}  unsupported decision-rights claims {unsupported}  "
              f"related read as found {partial_as_found}")
        print("per area (tp, fp, fn):")
        for a, (t, f, n) in out["per_area"].items():
            if t or f or n:
                print(f"  {a:30} {t:3} {f:3} {n:3}")
        print("per capability (evidence found from the line):")
        for c, v in out["capability"].items():
            p = v["tp"] / (v["tp"] + v["fp"]) if v["tp"] + v["fp"] else 1.0
            r_ = v["tp"] / (v["tp"] + v["fn"]) if v["tp"] + v["fn"] else 1.0
            print(f"  {c}: precision {p:.2f} recall {r_:.2f}  (tp {v['tp']}, fp {v['fp']}, fn {v['fn']})")
        if failures:
            print(f"{len(failures)} lines differ (extra clear / missed clear):")
            for t, extra, missed in failures:
                print(f"  - {t[:90]}\n      extra {extra}  missed {missed}")
    return out


def main(argv: List[str]) -> int:
    if "--holdout" in argv:
        from evaluation.capability_interpretation.holdout import H
        print("HELD-OUT set (score once after a freeze; never tune against it)")
        score(H)
    else:
        from evaluation.capability_interpretation.development import D
        print("DEVELOPMENT set")
        score(D)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
