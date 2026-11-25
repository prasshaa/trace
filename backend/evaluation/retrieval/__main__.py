"""Retrieval evaluation (semantic-retrieval workstream, Phase 1). Offline; never imported by app/.

    .venv/bin/python -m evaluation.retrieval                      tuning set, every retriever
    .venv/bin/python -m evaluation.retrieval --holdout            held-out set (frozen methods only; log the run)
    .venv/bin/python -m evaluation.retrieval --export-review F    CSV of every record and its proposed labels for a reviewer

Task. For each SYNTHETIC case and each query (20 responsibility areas; capabilities C1-C6 and L1-L4), rank the case's
records. Relevance (graded): 2 = the record demonstrates the area (label `clear`), 1 = relevant but not demonstrated
(`related`), 0 = not relevant. For a capability, a record's grade is 2 if it clearly shows a DIRECT area, else 1 if it
relates to a direct area or clearly shows a transferable one. Only confirmed records may be returned (eligibility filter
applied in the retrieval step, as in production; leaks are counted).

Retrievers (each returns only the records it considers relevant, best first):
  keyword    the production reader today (app/services/capability/interpret.py, trace-reading-1.0): clear before partial
  fts        PostgreSQL full-text search (english config, OR of the query's lexemes, ts_rank_cd): the lexical leg a hybrid
             would use. Needs the local database (computed on the fly; no schema change)
  semantic   gte-small (the app's provider), cosine over every eligible record (Phase 2; no cutoff)
  hybrid     Phase 3: the app's Reciprocal Rank Fusion of structured (the reader), fts and semantic RANKS, with its
             rank-based cutoff (app/services/retrieval/fusion.py, frozen config HYBRID)

Metrics (macro over (case, query) pairs that have at least one grade-2 record):
  P@5, R@5          strict relevance (grade 2)
  R@5 (any)         grade >= 1: does retrieval surface related experience for clarification?
  MRR               rank of the first grade-2 record
  nDCG@5            graded gains (2^g - 1)
  FP@5              share of returned top-5 records with grade 0 (all queries with any result)
  missed            grade-2 (case, query, record) triples not returned at any rank
  leak              ineligible records returned (must be 0)
  dr-fp             records returned in the top 5 for a decision-rights area (roadmap, strategy, outcome ownership)
                    whose labels give them no relevance: candidates a judge would have to reject
Capability verdicts (C1-C6, per case; truth from labels, system from the reader's clear readings on eligible records):
  found precision / recall, unsupported decision-rights claims (a record read as clear roadmap, strategy or outcome
  ownership that its labels don't support), latency per query.
"""
import csv
import hashlib
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Sequence, Tuple

from evaluation.retrieval.cases import DECISION_RIGHTS, ELIGIBLE, Case, Record

HERE = Path(__file__).resolve().parent
Q = json.loads((HERE / "queries.json").read_text())
AREA_TEXT = {a["key"]: f'{a["label"]}. {a["description"]} {" ".join(a["examples"])}' for a in Q["areas"]}
CAPS = {c["key"]: c for c in Q["capabilities"]}
CAP_TEXT = {k: f'{c["title"]}. {c["description"]}' for k, c in CAPS.items()}
CORE = ("C1", "C2", "C3", "C4", "C5", "C6")
K = 5


# ------------------------------------------------------------------------------------------------- relevance
def grade(rec: Record, query: str) -> int:
    if query in AREA_TEXT:
        return 2 if query in rec.clear else 1 if query in rec.related else 0
    c = CAPS[query]
    direct, transfer = set(c["direct_areas"]), set(c["transferable_areas"])
    if rec.clear & direct:
        return 2
    return 1 if (rec.related & direct) or (rec.clear & transfer) else 0


# ------------------------------------------------------------------------------------------------- retrievers
Retriever = Callable[[str, Sequence[Record]], List[Tuple[str, float]]]


def keyword_retriever() -> Retriever:
    from app.services.capability.interpret import interpret
    cache: Dict[str, object] = {}

    def read(rec):
        if rec.text not in cache:
            cache[rec.text] = interpret(rec.text)
        return cache[rec.text]

    def run(query, records):
        out = []
        for rec in records:
            r = read(rec)
            if query in AREA_TEXT:
                s = 2.0 if query in r.clear else 1.0 if query in r.partial else 0.0
            else:
                c = CAPS[query]
                direct, transfer = set(c["direct_areas"]), set(c["transferable_areas"])
                s = 2.0 if set(r.clear) & direct else 1.0 if (set(r.partial) & direct) or (set(r.clear) & transfer) else 0.0
            if s:
                out.append((rec.id, s))
        return sorted(out, key=lambda x: -x[1])
    return run


def fts_retriever() -> Retriever:
    import psycopg
    from app.config import get_settings
    conn = psycopg.connect(local_database_url(), autocommit=True)
    sql = ("with q as (select coalesce(nullif(string_agg(distinct lexeme, ' | '), ''), 'zzzz')::tsquery as tq "
           "  from unnest(to_tsvector('english', %(q)s))) "
           "select r.id, ts_rank_cd(to_tsvector('english', r.text), q.tq) as s "
           "from jsonb_to_recordset(%(recs)s::jsonb) as r(id text, text text), q "
           "where to_tsvector('english', r.text) @@ q.tq order by s desc, r.id")

    def run(query, records):
        text = AREA_TEXT.get(query) or CAP_TEXT[query]
        recs = json.dumps([{"id": r.id, "text": r.text} for r in records])
        return [(i, float(s)) for i, s in conn.execute(sql, {"q": text, "recs": recs}).fetchall()]
    return run


def semantic_retriever() -> Retriever:
    """gte-small (local, the app's own provider), cosine over all eligible records, best first. Phase 2: no cutoff yet,
    so every record is ranked (FP@5 is therefore not meaningful for it until Phase 3 sets a cutoff on the tuning set)."""
    import numpy as np
    from app.config import get_settings
    from app.services.embeddings.provider import GteSmallOnnx
    p = GteSmallOnnx(get_settings().trace_embedding_model_dir)
    cache: Dict[str, object] = {}

    def vec(t):
        if t not in cache:
            cache[t] = np.array(p.embed([t])[0])
        return cache[t]

    def run(query, records):
        q = vec(AREA_TEXT.get(query) or CAP_TEXT[query])
        scored = [(r.id, float(vec(r.text) @ q)) for r in records]
        return sorted(scored, key=lambda x: (-x[1], x[0]))
    return run


def _query(key: str):
    from app.services.retrieval.hybrid import Query
    if key in AREA_TEXT:
        return Query(key, AREA_TEXT[key], frozenset({key}))
    c = CAPS[key]
    return Query(key, CAP_TEXT[key], frozenset(c["direct_areas"]), frozenset(c["transferable_areas"]))


def family_of(key: str) -> List[str]:
    return list(AREA_TEXT) if key in AREA_TEXT else list(CAPS)


def two_way_ranks(scores: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, Tuple[int, int]]]:
    """scores: query -> {line: value, higher is better} -> line -> query -> (rank of the line within the query, rank of
    the query within the line). Dense ranks, as trace_semantic.hybrid_candidates computes them in the database."""
    from app.services.retrieval.fusion import dense_ranks
    within_q = {q: dict(dense_ranks(v.items())) for q, v in scores.items()}
    lines = {i for v in scores.values() for i in v}
    out: Dict[str, Dict[str, Tuple[int, int]]] = {}
    for i in lines:
        within_line = dict(dense_ranks([(q, v[i]) for q, v in scores.items() if i in v]))
        out[i] = {q: (within_q[q][i], r) for q, r in within_line.items()}
    return out


def leg_ranks(lexical: Retriever, semantic: Retriever):
    """Per (query, records): the app's Lines (with two-way lexical and semantic ranks over the query's FAMILY) and the
    structured leg. Cached, so a configuration search re-fuses without recomputing anything."""
    from app.services.retrieval.hybrid import Line, structured_leg
    fam_cache: Dict[tuple, list] = {}
    st_cache: Dict[tuple, tuple] = {}

    def lines(query, records):
        ids = tuple(r.id for r in records)
        fkey = (family_of(query)[0], ids)
        if fkey not in fam_cache:
            fam = family_of(query)
            lex = two_way_ranks({q: dict(lexical(q, records)) for q in fam})
            sem = two_way_ranks({q: dict(semantic(q, records)) for q in fam})
            out = []
            for r in records:
                ranks = {}
                for q in fam:
                    d = {}
                    if q in lex.get(r.id, {}):
                        d["lr"], d["lq"] = lex[r.id][q]
                    if q in sem.get(r.id, {}):
                        d["sr"], d["sq"] = sem[r.id][q]
                    if d:
                        ranks[q] = d
                out.append(Line("evidence", r.id, r.text, ranks=ranks))
            fam_cache[fkey] = out
        skey = (query, ids)
        if skey not in st_cache:
            st_cache[skey] = structured_leg(fam_cache[fkey], _query(query))
        return fam_cache[fkey], st_cache[skey]
    return lines


def hybrid_retriever(cfg=None, lines=None) -> Retriever:
    """Phase 3: the app's legs and fusion (app/services/retrieval) over the same ranks the database computes.
    Default configuration: the frozen HYBRID."""
    from app.services.retrieval.fusion import HYBRID, fuse
    from app.services.retrieval.hybrid import legs_for
    cfg = cfg or HYBRID
    lines = lines or leg_ranks(fts_retriever(), semantic_retriever())

    def run(query, records):
        ls, structured = lines(query, records)
        legs, _ = legs_for(ls, _query(query), cfg, structured)
        return [(f.id, -float(f.order)) for f in fuse(legs, cfg)]
    return run


def local_database_url() -> str:
    """The admin URL of the LOCAL stack only. The evaluation never connects to a hosted database."""
    from app.config import get_settings
    s = get_settings()
    url = s.database_url.get_secret_value() if s.database_url else ""
    if "@127.0.0.1:" not in url and "@localhost:" not in url:
        raise SystemExit("refused: the retrieval evaluation runs against the local stack only")
    return url


RETRIEVERS = {"keyword": keyword_retriever, "fts": fts_retriever, "semantic": semantic_retriever, "hybrid": hybrid_retriever}


# ------------------------------------------------------------------------------------------------- metrics
def dcg(gains: Sequence[int]) -> float:
    return sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(gains))


def evaluate(cases: Sequence[Case], retriever: Retriever, queries: Sequence[str]) -> dict:
    p5, r5, r5any, mrr, ndcg, fp5, lat = [], [], [], [], [], [], []
    missed = leak = dr_fp = 0
    for case in cases:
        by_id = {r.id: r for r in case.records}
        eligible = case.eligible
        for q in queries:
            t = time.perf_counter()
            got = retriever(q, eligible)
            lat.append((time.perf_counter() - t) * 1000)
            ids = [i for i, _ in got]
            leak += sum(1 for i in ids if by_id[i].status != ELIGIBLE)
            g = {r.id: grade(r, q) for r in eligible}
            top = ids[:K]
            if top:
                fp5.append(sum(1 for i in top if g[i] == 0) / len(top))
            if q in DECISION_RIGHTS:
                dr_fp += sum(1 for i in top if g[i] == 0)
            rel = [i for i, v in g.items() if v == 2]
            anyrel = [i for i, v in g.items() if v >= 1]
            if not rel:
                continue
            p5.append(sum(1 for i in top if g[i] == 2) / K)
            r5.append(sum(1 for i in top if g[i] == 2) / len(rel))
            r5any.append(sum(1 for i in top if g[i] >= 1) / len(anyrel))
            first = next((n for n, i in enumerate(ids) if g[i] == 2), None)
            mrr.append(0.0 if first is None else 1 / (first + 1))
            ideal = sorted(g.values(), reverse=True)[:K]
            ndcg.append(dcg([g[i] for i in top]) / dcg(ideal) if dcg(ideal) else 0.0)
            missed += sum(1 for i in rel if i not in ids)
    mean = lambda xs: statistics.fmean(xs) if xs else 0.0          # noqa: E731
    lat_sorted = sorted(lat)
    return {"queries_scored": len(p5), "P@5": mean(p5), "R@5": mean(r5), "R@5_any": mean(r5any), "MRR": mean(mrr),
            "nDCG@5": mean(ndcg), "FP@5": mean(fp5), "missed": missed, "leak": leak, "dr_fp": dr_fp,
            "latency_ms_mean": mean(lat), "latency_ms_p95": lat_sorted[int(0.95 * (len(lat_sorted) - 1))] if lat else 0.0}


def capability_verdicts(cases: Sequence[Case]) -> dict:
    """The production reader's capability verdicts (C1-C6) against the labels, plus unsupported decision-rights claims."""
    from app.services.capability.interpret import interpret
    tp = fp = fn = 0
    unsupported, related_as_clear = 0, 0
    per = {}
    for case in cases:
        for c in CORE:
            direct = set(CAPS[c]["direct_areas"])
            truth = any(r.clear & direct for r in case.eligible)
            sys_found = any(set(interpret(r.text).clear) & direct for r in case.eligible)
            tp += truth and sys_found
            fp += sys_found and not truth
            fn += truth and not sys_found
            per.setdefault(c, [0, 0, 0])
            per[c][0] += truth and sys_found
            per[c][1] += sys_found and not truth
            per[c][2] += truth and not sys_found
        for r in case.eligible:
            read = set(interpret(r.text).clear)
            for a in read - r.clear:
                if a in r.related:
                    related_as_clear += 1
                elif a in DECISION_RIGHTS:
                    unsupported += 1
    return {"found_precision": tp / (tp + fp) if tp + fp else 1.0, "found_recall": tp / (tp + fn) if tp + fn else 1.0,
            "tp": tp, "fp": fp, "fn": fn, "unsupported_decision_rights": unsupported, "related_read_as_clear": related_as_clear,
            "per_capability": per}


def fingerprint(cases: Sequence[Case]) -> str:
    blob = json.dumps([[c.id, [[r.id, r.text, sorted(r.clear), sorted(r.related), r.status, r.kind] for r in c.records]]
                       for c in cases], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def summary(cases: Sequence[Case]) -> dict:
    recs = [r for c in cases for r in c.records]
    tags: Dict[str, int] = {}
    for r in recs:
        for t in r.tags:
            tags[t] = tags.get(t, 0) + 1
    return {"cases": len(cases), "records": len(recs), "eligible": sum(r.status == ELIGIBLE for r in recs),
            "label_status": sorted({c.label_status for c in cases}), "tags": dict(sorted(tags.items()))}


def export_review(path: str) -> None:
    from evaluation.retrieval.holdout import H
    from evaluation.retrieval.tuning import T
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["set", "case", "record", "status", "kind", "text", "proposed_clear", "proposed_related",
                    "reviewer_clear", "reviewer_related", "reviewer_notes"])
        for name, cases in (("tuning", T), ("holdout", H)):
            for c in cases:
                for r in c.records:
                    w.writerow([name, c.id, r.id, r.status, r.kind, r.text, ";".join(sorted(r.clear)),
                                ";".join(sorted(r.related)), "", "", ""])
    print(f"wrote {path}")


def main(argv: List[str]) -> int:
    if "--export-review" in argv:
        export_review(argv[argv.index("--export-review") + 1])
        return 0
    if "--holdout" in argv:
        from evaluation.retrieval.holdout import H as cases
        name = "HELD-OUT"
    else:
        from evaluation.retrieval.tuning import T as cases
        name = "TUNING"
    only = [a.split("=", 1)[1] for a in argv if a.startswith("--retriever=")]
    print(f"{name} set {fingerprint(cases)}  {json.dumps(summary(cases))}")
    print(f"queries {Q['version']}: {len(AREA_TEXT)} areas + {len(CAPS)} capabilities  (labels: author-proposed, not yet human-reviewed)")
    groups = {"areas": list(AREA_TEXT), "capabilities": list(CAPS)}
    results = {}
    for rname, make in RETRIEVERS.items():
        if only and rname not in only:
            continue
        ret = make()
        for g, qs in groups.items():
            m = evaluate(cases, ret, qs)
            results[(rname, g)] = m
            print(f"  {rname:8} {g:12} n={m['queries_scored']:3}  P@5 {m['P@5']:.3f}  R@5 {m['R@5']:.3f}  "
                  f"R@5(any) {m['R@5_any']:.3f}  MRR {m['MRR']:.3f}  nDCG@5 {m['nDCG@5']:.3f}  FP@5 {m['FP@5']:.3f}  "
                  f"missed {m['missed']:3}  leak {m['leak']}  dr-fp {m['dr_fp']:2}  {m['latency_ms_mean']:.2f}/{m['latency_ms_p95']:.2f} ms")
    v = capability_verdicts(cases)
    print(f"  capability verdicts (C1-C6, production reader): found precision {v['found_precision']:.3f}  "
          f"recall {v['found_recall']:.3f}  (tp {v['tp']} fp {v['fp']} fn {v['fn']})  unsupported decision-rights "
          f"{v['unsupported_decision_rights']}  related read as clear {v['related_read_as_clear']}")
    print("    per capability [tp, fp, fn]: " + ", ".join(f"{k} {x}" for k, x in v["per_capability"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
