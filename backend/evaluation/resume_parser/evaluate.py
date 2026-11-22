"""Resume parser evaluation: structured output vs hand-labelled ground truth, per field, per layout.

    .venv/bin/python -m evaluation.resume_parser            # prints the report, exit 1 if a release threshold fails

THRESHOLDS were fixed before the first run (2026-11-20) and are not tuned to results. Precision is weighted over
recall for facts that enter a career profile: a missed skill is acceptable, an invented employer is not.

Metrics (micro-averaged over all documents):
  employer / title precision and recall      matched job-by-job (normalised text equality)
  date accuracy                              start and end of matched jobs, month (or ongoing) exact
  segmentation accuracy                      documents whose extracted job count equals the truth and every job matched
  bullet grouping accuracy                   truth bullets that appear as exactly one extracted bullet, whole
  description accuracy                       truth descriptions extracted whole (not split, not merged into bullets)
  education accuracy                         qualification and institution both correct
  activity accuracy                          certifications / projects / volunteering found with the right title
  skill precision, recall, false-positive rate; forbidden fragments accepted (must be 0)
  duplicates                                 the same job or the same skill extracted twice
  fabrication rate                           extracted titles/employers/institutions/skills not present in the source text

Added for parser 2.1 (2026-11-20, after the review failure; the metrics above are unchanged):
  employment record precision / recall       a record counts only when title AND employer both match one truth job
  missing records                            truth jobs with no extracted job of the same title or employer
  silently missing                           missing records that do not appear in any `unresolved` item either
  unsupported records                        extracted jobs that match no truth job at all
  employer substitutions                     matched jobs (same title and start) whose employer is filled but wrong
  bullet attribution                         truth bullets and description found in their own job and in no other job
  earlier-experience recall                  truth jobs listed under an "earlier experience" heading, extracted as records
Splits: corpus.py (development, historical), regression.py (curated gate: complete extraction required),
validation.py (calibration), holdout2.py (fresh held-out, scored once after the parser was frozen).
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict

from app.services.resume_parsing import parse_bytes
from app.services.resume_parsing.document import load
from evaluation.resume_parser.corpus import P
from evaluation.resume_parser.render import ALL_LAYOUTS, LAYOUTS, STRUCTURE_LAYOUTS, render

THRESHOLDS = {
    "employer_precision": 0.95, "employer_recall": 0.90, "title_precision": 0.95, "title_recall": 0.90,
    "date_accuracy": 0.95, "segmentation_accuracy": 0.90, "bullet_grouping_accuracy": 0.95,
    "description_accuracy": 0.90, "education_accuracy": 0.90, "activity_accuracy": 0.85,
    "skill_precision": 0.90, "skill_recall": 0.75, "skill_false_positive_rate_max": 0.10,
    "forbidden_skills_accepted_max": 0, "duplicates_max": 0, "fabrication_rate_max": 0.0,
}
# Parser 2.1 structural thresholds, fixed on 2026-11-20 BEFORE the fresh held-out set (holdout2.py) was run.
STRUCTURE_THRESHOLDS = {
    "record_precision": 0.95, "record_recall": 0.90, "missing_records_rate_max": 0.10, "silently_missing_max": 0,
    "unsupported_records_max": 0, "employer_substitutions_max": 0, "bullet_attribution_accuracy": 0.95,
    "earlier_recall": 0.90, "date_accuracy": 0.95, "employer_precision": 0.95, "title_precision": 0.95,
    "skill_precision": 0.90, "skill_false_positive_rate_max": 0.10, "forbidden_skills_accepted_max": 0, "duplicates_max": 0,
    "fabrication_rate_max": 0.0,
}
# The curated regression fixtures (regression.py) must be extracted completely, in every layout.
REGRESSION_THRESHOLDS = {
    "record_precision": 1.0, "record_recall": 1.0, "missing_records_rate_max": 0.0, "silently_missing_max": 0,
    "unsupported_records_max": 0, "employer_substitutions_max": 0, "bullet_attribution_accuracy": 1.0, "earlier_recall": 1.0,
    "date_accuracy": 1.0, "fabrication_rate_max": 0.0, "duplicates_max": 0, "forbidden_skills_accepted_max": 0,
}


def norm(s):
    return re.sub(r"[^a-z0-9+#/&]+", " ", (s or "").lower()).strip()


def emp_ok(got, t):
    """The employer is right when it is the name, or (Phase 4) the name with its registered entity as written:
    "Novaline (Novaline Care Labs)". Both names are kept, as the source states them."""
    names = {norm(t["employer"])} | ({norm(f"{t['employer']} ({t['entity']})")} if t.get("entity") else set())
    return norm(got) in names


def evaluate_doc(p, layout):
    data = render(p, layout)
    out = parse_bytes(data)
    source = norm(load(data).text)
    d = out.draft
    r = defaultdict(int)
    truth = p["jobs"]
    got = list(d.work)
    used = set()
    pairs = []
    for t in truth:
        best = None
        for i, g in enumerate(got):
            if i in used:
                continue
            score = (norm(g.title) == norm(t["title"])) + emp_ok(g.employer, t)
            if score and (best is None or score > best[0]):
                best = (score, i)
        if best:
            used.add(best[1])
            pairs.append((t, got[best[1]]))
    r["jobs_truth"] += len(truth)
    r["jobs_got"] += len(got)
    for t, g in pairs:
        r["title_ok"] += norm(g.title) == norm(t["title"])
        r["employer_ok"] += emp_ok(g.employer, t)
        r["dates_total"] += 2
        r["dates_ok"] += (g.start.value == t["start"]) + ((g.end.precision == "ongoing") if t["end"] is None else (g.end.value == t["end"]))
        gb = [norm(b.text) for b in g.bullets]
        for b in t["bullets"]:
            r["bullets_total"] += 1
            r["bullets_ok"] += gb.count(norm(b)) == 1
        if t.get("description"):
            r["desc_total"] += 1
            r["desc_ok"] += norm(g.description) == norm(t["description"])
    r["titles_got"] += sum(1 for g in got if g.title)
    r["employers_got"] += sum(1 for g in got if g.employer)
    r["segmentation_docs"] += 1
    r["segmentation_ok"] += len(got) == len(truth) and len(pairs) == len(truth)
    # duplicates
    keys = [(norm(g.title), norm(g.employer), g.start.value) for g in got]
    r["duplicates"] += len(keys) - len(set(keys))
    names = [s.name.lower() for s in d.skills]
    r["duplicates"] += len(names) - len(set(names))
    # education
    for e in p["education"]:
        r["edu_total"] += 1
        r["edu_ok"] += any(norm(x.institution) == norm(e["institution"]) and norm(x.qualification) == norm(e["qualification"])
                           for x in d.education)
    # activities
    from evaluation.resume_parser.render import COMPACT_LAYOUTS
    leadership = p.get("leadership", []) if layout in COMPACT_LAYOUTS else []          # rendered by compact layouts only
    for kind, items in (("certification", p.get("certifications", [])), ("project", p.get("projects", [])),
                        ("volunteering", p.get("volunteering", []) + leadership)):
        for a in items:
            r["act_total"] += 1
            r["act_ok"] += any(x.kind == kind and norm(x.title) == norm(a["title"]) for x in d.activities)
    # skills
    # Labels are compared in the vocabulary's canonical form ("Roadmapping" and "Product roadmaps" are one skill), a
    # scoring change made with parser 2.1; labels without a vocabulary entry are compared as written.
    from app.services.resume_parsing.skills import canonical
    expected = {(canonical(s) or s).lower() for s in p["expected_skills"]}
    got_sk = {s.name.lower() for s in d.skills}
    r["skills_expected"] += len(expected)
    r["skills_got"] += len(got_sk)
    r["skills_tp"] += len(expected & got_sk)
    r["skills_fp"] += len(got_sk - expected)
    r["forbidden"] += sum(1 for f in p.get("forbidden_skills", []) if f.lower() in got_sk)
    # fabrication: an extracted fact whose text isn't in the source document
    facts = [g.title for g in got if g.title] + [g.employer for g in got if g.employer] + \
            [x.institution for x in d.education if x.institution] + [x.qualification for x in d.education if x.qualification]
    r["facts"] += len(facts)
    r["fabricated"] += sum(1 for f in facts if norm(f) not in source)
    r["skill_facts"] += len(d.skills)
    r["fabricated"] += sum(1 for s in d.skills if norm(s.name) not in source and not any(
        norm(a) in source for a in _aliases(s.name)))
    # ---- parser 2.1 structure metrics
    strict = [(t, g) for t, g in pairs if norm(g.title) == norm(t["title"]) and emp_ok(g.employer, t)]
    r["records_ok"] += len(strict)
    r["records_got"] += sum(1 for g in got if g.title)
    matched_any = {id(t) for t, _ in pairs}
    unresolved_text = " ".join(norm(u.text) for u in d.unresolved)
    for t in truth:
        if id(t) not in matched_any:
            r["missing"] += 1
            r["silently_missing"] += not (norm(t["title"]) in unresolved_text or norm(t["employer"]) in unresolved_text
                                          or any(norm(g.source).find(norm(t["employer"])) >= 0 for g in got if g.source))
        if t.get("earlier"):
            r["earlier_total"] += 1
            r["earlier_ok"] += any(t is x for x, _ in strict)
    r["unsupported"] += len(got) - len(pairs)
    for t in truth:                                     # same title and start, wrong (filled) employer
        g = next((g for g in got if norm(g.title) == norm(t["title"]) and g.start.value == t["start"]), None)
        if g is not None and g.employer and not emp_ok(g.employer, t):
            r["employer_substitutions"] += 1
    texts = {id(g): [norm(b.text) for b in g.bullets] + ([norm(g.description)] if g.description else []) for g in got}
    owner = {id(t): g for t, g in pairs}
    for t in truth:
        items = list(t["bullets"]) + ([t["description"]] if t.get("description") else [])
        for item in items:
            r["attribution_total"] += 1
            g = owner.get(id(t))
            mine = g is not None and any(norm(item) == x or (not t["bullets"] and norm(item) in x) for x in texts[id(g)])
            elsewhere = any(norm(item) in x for gg in got if gg is not g for x in texts[id(gg)])
            r["attribution_ok"] += mine and not elsewhere
    misses = {"skills_fp": sorted(got_sk - expected), "skills_fn": sorted(expected - got_sk),
              "jobs": [(g.title, g.employer, g.start.value, g.end.value or g.end.precision) for g in got]}
    return r, misses


def _aliases(name):
    from app.services.resume_parsing.skills import VOCAB
    return [name] + VOCAB.get(name, ("", []))[1]


def summarise(r):
    div = lambda a, b: (a / b) if b else 1.0  # noqa: E731
    return {
        "employer_precision": div(r["employer_ok"], r["employers_got"]), "employer_recall": div(r["employer_ok"], r["jobs_truth"]),
        "title_precision": div(r["title_ok"], r["titles_got"]), "title_recall": div(r["title_ok"], r["jobs_truth"]),
        "date_accuracy": div(r["dates_ok"], r["dates_total"]), "segmentation_accuracy": div(r["segmentation_ok"], r["segmentation_docs"]),
        "bullet_grouping_accuracy": div(r["bullets_ok"], r["bullets_total"]), "description_accuracy": div(r["desc_ok"], r["desc_total"]),
        "education_accuracy": div(r["edu_ok"], r["edu_total"]), "activity_accuracy": div(r["act_ok"], r["act_total"]),
        "skill_precision": div(r["skills_tp"], r["skills_got"]), "skill_recall": div(r["skills_tp"], r["skills_expected"]),
        "skill_false_positive_rate_max": div(r["skills_fp"], r["skills_got"]), "forbidden_skills_accepted_max": r["forbidden"],
        "duplicates_max": r["duplicates"], "fabrication_rate_max": div(r["fabricated"], r["facts"] + r["skill_facts"]),
        "record_precision": div(r["records_ok"], r["records_got"]), "record_recall": div(r["records_ok"], r["jobs_truth"]),
        "missing_records_rate_max": div(r["missing"], r["jobs_truth"]) if r["jobs_truth"] else 0.0,
        "silently_missing_max": r["silently_missing"], "unsupported_records_max": r["unsupported"],
        "employer_substitutions_max": r["employer_substitutions"],
        "bullet_attribution_accuracy": div(r["attribution_ok"], r["attribution_total"]),
        "earlier_recall": div(r["earlier_ok"], r["earlier_total"]),
    }


def passes(name, value, thresholds=None):
    t = (thresholds or THRESHOLDS).get(name)
    if t is None:                                  # a metric without a threshold in this gate
        return True
    return value <= t if name.endswith("_max") else value >= t


def run_split(personas, layouts=STRUCTURE_LAYOUTS):
    """(overall, by_layout, per_document) for one split. per_document lists every document that misses a job, adds an
    unsupported one, substitutes an employer or misattributes a line: no failure hides behind an average."""
    total, by_layout, docs = defaultdict(int), defaultdict(lambda: defaultdict(int)), []
    for p in personas:
        for layout in layouts:
            r, misses = evaluate_doc(p, layout)
            for k, v in r.items():
                total[k] += v
                by_layout[layout][k] += v
            bad = {k: r[k] for k in ("missing", "silently_missing", "unsupported", "employer_substitutions") if r[k]}
            if r["attribution_ok"] < r["attribution_total"]:
                bad["misattributed_lines"] = r["attribution_total"] - r["attribution_ok"]
            if r["dates_ok"] < r["dates_total"]:
                bad["wrong_dates"] = r["dates_total"] - r["dates_ok"]
            if bad:
                docs.append((p["id"], layout, bad, misses["jobs"]))
    return summarise(total), {k: summarise(v) for k, v in by_layout.items()}, docs


def run(verbose=False):
    total, by_layout, failures = defaultdict(int), defaultdict(lambda: defaultdict(int)), []
    for p in P:
        for layout in LAYOUTS:
            r, misses = evaluate_doc(p, layout)
            for k, v in r.items():
                total[k] += v
                by_layout[layout][k] += v
            if verbose and (misses["skills_fp"] or r["segmentation_ok"] == 0 or r["bullets_ok"] < r["bullets_total"]
                            or r["dates_ok"] < r["dates_total"] or r["edu_ok"] < r["edu_total"] or r["act_ok"] < r["act_total"]):
                failures.append((p["id"], layout, dict(r), misses))
    return summarise(total), {k: summarise(v) for k, v in by_layout.items()}, failures


def report_split(name, personas, thresholds, layouts=STRUCTURE_LAYOUTS) -> bool:
    overall, by_layout, docs = run_split(personas, layouts)
    print(f"\n== {name}: {len(personas)} fictional personas x {len(layouts)} layouts = {len(personas) * len(layouts)} documents")
    ok_all = True
    for k in thresholds:
        v = overall[k]
        ok = passes(k, v, thresholds)
        ok_all &= ok
        worst = min(by_layout, key=lambda l: by_layout[l][k] if not k.endswith("_max") else -by_layout[l][k])
        val = f"{v:.3f}" if isinstance(v, float) else str(v)
        print(f"  {k:32} {val:>7}  threshold {thresholds[k]!s:>5}  {'PASS' if ok else 'FAIL'}   (worst layout: {worst})")
    for pid, layout, bad, jobs in docs:
        print(f"  document failure {pid} {layout}: {bad}")
    print(f"  RESULT: {'all thresholds met' if ok_all else 'BELOW THRESHOLDS'}")
    return ok_all


def main(argv=None):
    args = argv or sys.argv[1:]
    if "--splits" in args:
        from evaluation.resume_parser.regression import R
        from evaluation.resume_parser.validation import V
        ok = report_split("regression (curated gate)", R, REGRESSION_THRESHOLDS)
        ok &= report_split("development (corpus.py, historical)", P, STRUCTURE_THRESHOLDS)
        ok &= report_split("validation", V, STRUCTURE_THRESHOLDS)
        from evaluation.resume_parser.holdout2 import H2
        # holdout2 was scored blind once (2026-11-20, parser 2.1); its failures were diagnosed, so it is DEVELOPMENT now.
        ok &= report_split("development (holdout2.py, formerly held-out)", H2, STRUCTURE_THRESHOLDS)
        if "--holdout3" in args:                # the fresh held-out set: run once, after the Phase 4 parser is frozen
            from evaluation.resume_parser.holdout3 import H3
            ok &= report_split("fresh held-out (holdout3.py)", H3, STRUCTURE_THRESHOLDS)
        return 0 if ok else 1
    verbose = "-v" in args
    overall, layouts, failures = run(verbose)
    docs = len(P) * len(LAYOUTS)
    print(f"Resume parser evaluation: {len(P)} fictional personas x {len(LAYOUTS)} layouts = {docs} documents\n")
    print(f"{'metric':34} {'value':>7}  {'threshold':>9}  result   " + "  ".join(f"{l[:12]:>12}" for l in LAYOUTS))
    ok_all = True
    for k, v in ((k, overall[k]) for k in THRESHOLDS):              # the historical table; --splits adds the 2.1 gates
        ok = passes(k, v)
        ok_all &= ok
        fmt = (lambda x: f"{x:7.3f}") if isinstance(v, float) else (lambda x: f"{x:7d}")
        print(f"{k:34} {fmt(v)}  {THRESHOLDS[k]:>9}  {'PASS' if ok else 'FAIL':6}   " +
              "  ".join(f"{(fmt(layouts[l][k])).strip():>12}" for l in LAYOUTS))
    if verbose:
        for pid, layout, r, m in failures:
            print("\n", pid, layout, m)
    print("\nRESULT:", "all release thresholds met" if ok_all else "below the release thresholds")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
