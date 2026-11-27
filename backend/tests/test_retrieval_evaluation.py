"""Guards for the retrieval evaluation sets (Phase 1): well-formed labels, synthetic content, frozen held-out set."""
import json
import pathlib

from evaluation.retrieval import __main__ as harness
from evaluation.retrieval.cases import ELIGIBLE
from evaluation.retrieval.holdout import H
from evaluation.retrieval.tuning import T

AREAS = {a["key"] for a in harness.Q["areas"]}


def test_labels_use_known_areas_and_ids_are_unique():
    ids = [r.id for c in T + H for r in c.records]
    assert len(ids) == len(set(ids))
    for c in T + H:
        for r in c.records:
            assert r.clear <= AREAS and r.related <= AREAS, r.id
            assert not (r.clear & r.related), r.id
            assert r.status in (ELIGIBLE, "pending", "rejected", "superseded")
            assert r.kind in ("work", "project", "volunteer", "course")
            if r.kind == "course":
                assert not r.clear, r.id                    # a course is never evidence of having done the work


def test_sets_are_disjoint_synthetic_and_author_labelled():
    texts_t = {r.text for c in T for r in c.records}
    assert not texts_t & {r.text for c in H for r in c.records}
    assert {c.label_status for c in T + H} == {"author_proposed"}       # until a human review replaces them
    every = " ".join(r.text for c in T + H for r in c.records).lower()
    for marker in ("@", "http", "linkedin", "www."):
        assert marker not in every


def test_held_out_set_is_frozen():
    # Changing holdout.py makes it a new set: update the fingerprint AND add a line to the run log in
    # docs/RETRIEVAL_EVALUATION.md; never edit it to suit a method.
    assert harness.fingerprint(H) == "90215641cccecbee"


def test_queries_are_frozen_and_complete():
    assert harness.Q["version"] == "retrieval-queries-1.0"
    assert len(AREAS) == 20 and set(harness.CAPS) == {"C1", "C2", "C3", "C4", "C5", "C6", "L1", "L2", "L3", "L4"}


def test_every_category_is_covered_in_both_sets():
    needed = {"wording", "synonym", "implicit_ownership", "explicit_ownership", "seniority", "keyword_overlap", "negative",
              "course", "outdated", "conflicting", "overlapping_roles", "sparse", "non_pm_background", "ineligible"}
    for cases in (T, H):
        assert needed <= set(harness.summary(cases)["tags"])


def test_keyword_baseline_never_leaks_ineligible_records():
    m = harness.evaluate(T, harness.keyword_retriever(), list(harness.AREA_TEXT))
    assert m["leak"] == 0 and m["queries_scored"] > 0


def test_evaluation_is_not_imported_by_the_app():
    import pathlib
    app = pathlib.Path(harness.__file__).resolve().parents[2] / "app"
    assert not any("evaluation.retrieval" in p.read_text() for p in app.rglob("*.py"))


def test_database_access_is_local_only(monkeypatch):
    import pytest
    from pydantic import SecretStr
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "database_url", SecretStr("postgresql://u:p@db.example.supabase.co:5432/postgres"))
    with pytest.raises(SystemExit):
        harness.local_database_url()
    src = (pathlib.Path(harness.__file__).parent / "latency.py").read_text()
    assert "refused: not the local stack" in src
