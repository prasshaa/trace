"""Phase 3: hybrid retrieval (structured + full-text + semantic, fused by rank). Unit tests for the fusion rules, and
integration tests on the LOCAL stack with transient users and FICTIONAL resumes (skipped without the pinned model).

Covers: the flag is off by default and the reader is used; hybrid results come only from the caller's eligible records,
with exact provenance and no similarity value; another user's profile id returns nothing; the database's two-way ranks
equal the evaluation's in-memory ranks (so the evaluation measures the code that runs); the user's own categorisation
takes precedence; any failure falls back to the reader.
"""
import json
from dataclasses import asdict, fields

import pytest

from app.config import Settings
from app.services.retrieval.fusion import HYBRID, FusionConfig, dense_ranks, fuse, mutual_leg, structured_grade
from app.services.retrieval.hybrid import Candidate, HybridRetriever, Line, Query, legs_for
from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import candidate, consent_document  # noqa: F401  (fixtures)


# ------------------------------------------------------------------------------------------------- unit: fusion rules
def test_dense_ranks_share_ties_and_order_by_id():
    assert dense_ranks([("b", 2.0), ("a", 2.0), ("c", 1.0)]) == [("a", 1), ("b", 1), ("c", 2)]
    assert dense_ranks([("x", 0.3), ("y", 0.1)], higher_is_better=False) == [("y", 1), ("x", 2)]


def test_single_leg_support_is_dropped_but_the_reader_and_agreement_are_kept():
    cfg = FusionConfig(version="t", alone_max_rank=(("lexical", 0), ("semantic", 0)), max_results=5)
    out = fuse({"structured": [("r", 1)], "lexical": [("both", 1), ("lex_only", 2)], "semantic": [("both", 1), ("sem_only", 1)]}, cfg)
    assert [f.id for f in out] == ["both", "r"]
    assert out[0].ranks == {"lexical": 1, "semantic": 1} and out[1].ranks == {"structured": 1}


def test_max_results_and_unknown_leg():
    cfg = FusionConfig(version="t", max_results=2)
    assert len(fuse({"structured": [(str(i), 1) for i in range(5)]}, cfg)) == 2
    with pytest.raises(ValueError):
        fuse({"cosine": [("a", 1)]}, cfg)


def test_mutual_rank_keeps_a_line_only_when_the_query_is_among_its_best():
    ranks = {"a": (1, 1), "b": (2, 3), "c": (3, 1), "d": (None, None)}
    assert mutual_leg(ranks, 1) == [("a", 1), ("c", 2)]
    assert mutual_leg(ranks, 3) == [("a", 1), ("b", 2), ("c", 3)]
    assert mutual_leg(ranks, 0) == []                                     # leg off


def test_structured_grade_follows_the_assessment_rules():
    assert structured_grade({"roadmap_ownership"}, set(), {"roadmap_ownership"}, set()) == 2
    assert structured_grade(set(), {"roadmap_ownership"}, {"roadmap_ownership"}, set()) == 1
    assert structured_grade({"data_analysis"}, set(), {"outcome_ownership"}, {"data_analysis"}) == 1
    assert structured_grade(set(), set(), {"roadmap_ownership"}, set()) == 0


def test_user_categorisation_takes_precedence_over_the_reader():
    q = Query("roadmap_ownership", "Roadmap", frozenset({"roadmap_ownership"}))
    lines = [Line("evidence", "said_none", "Owned the product roadmap for payments.", user_areas=[]),
             Line("evidence", "said_roadmap", "Ran weekly syncs.", user_areas=["roadmap_ownership"]),
             Line("evidence", "read", "Owned the product roadmap for lending.")]
    legs, read_by = legs_for(lines, q, HYBRID)
    assert dict(legs["structured"]) == {"said_roadmap": 1, "read": 1}
    assert read_by == {"said_roadmap": "you", "read": "trace"}


def test_frozen_configuration_and_no_similarity_in_results():
    assert (HYBRID.version, HYBRID.k, dict(HYBRID.mutual), dict(HYBRID.alone_max_rank), HYBRID.max_results) == \
        ("hybrid-1.0", 60, {"lexical": 1, "semantic": 1}, {"lexical": 0, "semantic": 0}, 3)
    names = {f.name for f in fields(Candidate)}
    assert not names & {"similarity", "score", "confidence", "distance"}
    assert Settings().trace_semantic_retrieval is False                 # off by default


# ------------------------------------------------------------------------------------------------- integration
integration = pytest.mark.integration


def _family():
    from evaluation.retrieval import __main__ as h
    return [h._query(k) for k in h.AREA_TEXT]


@pytest.fixture
def env(base_settings, consent_document):
    from tests.test_embeddings_integration import MODEL_DIR, MODEL_FILES
    if not all((MODEL_DIR / f).is_file() for f in MODEL_FILES):
        pytest.skip("gte-small model files not present")
    from tests.test_api_phase2 import make_client
    with make_client(base_settings, trace_real_resume_uploads=True) as client:
        yield client


def _setup(env, users, i=2):
    from tests.test_embeddings_integration import confirmed_profile, ctx_of
    u, h = confirmed_profile(env, users, i)
    conn, ctx = ctx_of(Settings(), u)
    return u, h, conn, ctx


def _provider():
    from tests.test_embeddings_integration import PROVIDER
    return PROVIDER


def _own_ids(env, h):
    from tests.test_resume_import_api import records
    r = records(env, h)
    ev = {str(x["id"]): str(e["id"]) for e in r["employments"] for x in e["evidence"]}
    return ev, {str(a["id"]) for a in r.get("other_records", [])}


@integration
def test_flag_off_uses_the_reader(env, users):
    u, h, conn, ctx = _setup(env, users)
    got = HybridRetriever(conn, Settings(trace_semantic_retrieval=False), _provider()).retrieve(ctx, _family())
    assert (got.mode, got.reason, got.version) == ("reader", "flag_off", "trace-reading-1.0")
    ev, _ = _own_ids(env, h)
    for cands in got.results.values():
        for c in cands:
            assert c.source_id in ev and set(c.legs) == {"structured"} and c.read_by in ("you", "trace")
    conn.close()


@integration
def test_hybrid_returns_only_own_eligible_lines_with_provenance(env, users):
    u, h, conn, ctx = _setup(env, users)
    got = HybridRetriever(conn, Settings(trace_semantic_retrieval=True, trace_embeddings=True), _provider()).retrieve(ctx, _family())
    assert (got.mode, got.version, got.index["status"]) == ("hybrid", "hybrid-1.0", "ok")
    ev, acts = _own_ids(env, h)
    found = [c for cands in got.results.values() for c in cands]
    assert found, "a confirmed PM profile retrieves something"
    for c in found:
        assert c.source_id in ev or c.source_id in acts
        if c.source_kind == "evidence":
            assert c.candidate_employment_id == ev[c.source_id]                   # exact parent record
        assert c.review_status in ("user_confirmed", "user_corrected")
        assert set(c.legs) <= {"structured", "lexical", "semantic"} and all(isinstance(r, int) for r in c.legs.values())
        assert "structured" in c.legs or {"lexical", "semantic"} <= set(c.legs)   # never one similarity leg alone
    assert "similar" not in json.dumps([asdict(c) for c in found])
    conn.close()


@integration
def test_another_users_profile_id_returns_nothing(env, users):
    from app.services.candidate_profile import OwnerContext
    u1, _, conn, ctx1 = _setup(env, users)
    u2, _, _, _ = _setup(env, users, 0)
    s = Settings(trace_semantic_retrieval=True, trace_embeddings=True)
    HybridRetriever(conn, s, _provider()).retrieve(ctx1, _family())                      # u1's embeddings exist
    foreign = OwnerContext(owner_user_id=u2, candidate_profile_id=ctx1.candidate_profile_id)
    from app.services.candidate_profile.repository import owner_session
    with owner_session(conn, u2) as repo:
        rows = repo._rows("select * from trace_semantic.hybrid_candidates(%(p)s, '[]'::jsonb, %(m)s, %(v)s)",
                          {"p": foreign.candidate_profile_id, "m": _provider().model, "v": _provider().version})
    assert rows == []
    conn.close()


@integration
def test_database_ranks_equal_the_evaluation_ranks(env, users):
    """The evaluation computes two-way ranks in memory; the product computes them in the database. Same numbers."""
    from evaluation.retrieval import __main__ as h
    from evaluation.retrieval.cases import Record
    u, hd, conn, ctx = _setup(env, users)
    r = HybridRetriever(conn, Settings(trace_semantic_retrieval=True, trace_embeddings=True), _provider())
    got = r.retrieve(ctx, _family())
    from app.services.candidate_profile.repository import owner_session
    fam = _family()
    vecs = r._vectors(fam)
    with owner_session(conn, u) as repo:
        rows = repo._rows("select * from trace_semantic.hybrid_candidates(%(p)s, %(q)s::jsonb, %(m)s, %(v)s)",
                          {"p": ctx.candidate_profile_id, "m": _provider().model, "v": _provider().version,
                           "q": json.dumps([{"key": q.key, "text": q.text, "embedding": v} for q, v in zip(fam, vecs)])})
    recs = [Record(id=str(x["source_id"]), text=x["content"], clear=frozenset(), related=frozenset()) for x in rows]
    lines = h.leg_ranks(h.fts_retriever(), h.semantic_retriever())
    mem, _ = lines(fam[0].key, recs)
    mem_ranks = {ln.source_id: ln.ranks for ln in mem}
    for x in rows:
        assert x["ranks"] == mem_ranks[str(x["source_id"])], x["source_id"]
    assert got.mode == "hybrid"                         # same ranks + the same fusion code = the same results
    conn.close()


@integration
def test_any_failure_falls_back_to_the_reader(env, users):
    from app.services.embeddings.provider import EmbeddingUnavailable
    u, h, conn, ctx = _setup(env, users)

    class Broken:
        model, version, dims = "thenlper/gte-small", "broken", 384

        def embed(self, texts):
            raise EmbeddingUnavailable("model missing")
    s = Settings(trace_semantic_retrieval=True, trace_embeddings=True)
    got = HybridRetriever(conn, s, Broken()).retrieve(ctx, _family())
    assert (got.mode, got.reason) == ("reader", "index_unavailable")
    assert HybridRetriever(conn, Settings(trace_semantic_retrieval=True, trace_embeddings=False)).retrieve(ctx, _family()).reason == "embeddings_off"
    with pytest.raises(ValueError):
        HybridRetriever(conn, s, Broken()).retrieve(ctx, _family() + _family()[:1])        # duplicate keys refused
    conn.close()


@integration
def test_users_own_categorisation_is_used_in_hybrid_mode(env, users):
    from tests.test_resume_import_api import records
    u, h, conn, ctx = _setup(env, users)
    line = records(env, h)["employments"][0]["evidence"][0]
    resp = env.put(f"/api/v1/evidence/{line['id']}/classification", headers=h, json={"areas": ["stakeholder_management"]})
    assert resp.status_code == 200, resp.text
    wide = FusionConfig(version="t", mutual=HYBRID.mutual, alone_max_rank=HYBRID.alone_max_rank, max_results=50)
    s = Settings(trace_semantic_retrieval=True, trace_embeddings=True)
    got = HybridRetriever(conn, s, _provider(), wide).retrieve(ctx, _family())
    mine = [c for c in got.results["stakeholder_management"] if c.source_id == str(line["id"])]
    assert mine and mine[0].read_by == "you" and mine[0].legs.get("structured") == 1
    # and the user's categorisation replaces Trace's reading for every other area
    assert all(c.read_by != "trace" for k, cands in got.results.items() for c in cands if c.source_id == str(line["id"]))
    conn.close()
