"""Phase 2 (D53): candidate embeddings lifecycle and isolation on the LOCAL stack, with transient users and FICTIONAL
resumes. Needs the pinned gte-small files (python -m scripts.fetch_embedding_model); skipped without them.

Covers: only eligible reviewed records are embedded; indexing is idempotent; matching is owner-scoped (another user's
profile id returns nothing); an edited line's old vector is never returned; a rejected line is excluded; clearing and
replacing the profile delete the vectors (cascade); the database ranking equals the in-memory cosine ranking; an
unavailable model degrades to a status, never an error; shared knowledge is readable by the runtime only.
"""
import numpy as np
import pytest

from app import db
from app.config import Settings, get_settings
from app.services.candidate_profile import OwnerContext
from app.services.candidate_profile.repository import owner_session
from app.services.embeddings.index import EmbeddingIndex, match_knowledge
from app.services.embeddings.provider import GteSmallOnnx, MODEL_FILES
from tests.test_api_foundation import auth, base_settings, token, users  # noqa: F401  (fixtures)
from tests.test_api_phase2 import candidate, consent_document, make_client  # noqa: F401  (fixtures)
from tests.test_resume_import_api import complete, pdf, records, up

pytestmark = pytest.mark.integration
MODEL_DIR = get_settings().trace_embedding_model_dir
if not all((MODEL_DIR / f).is_file() for f in MODEL_FILES):
    pytest.skip("gte-small model files not present", allow_module_level=True)
PROVIDER = GteSmallOnnx(MODEL_DIR)


@pytest.fixture
def on(base_settings, consent_document):
    with make_client(base_settings, trace_real_resume_uploads=True) as client:
        yield client


def confirmed_profile(c, users, i=2):
    u, h = candidate(c, users)
    v = up(c, h, pdf(i)).json()["import"]
    assert c.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=complete(c, h, v)).status_code == 200
    return u, h


def ctx_of(settings, user):
    from app.services.candidate_profile import CandidateProfileService
    conn = db.connect_runtime(settings)
    pid = CandidateProfileService(conn).owner_profile_id(user)
    return conn, OwnerContext(owner_user_id=user, candidate_profile_id=pid)


def stored(settings, user):
    conn = db.connect(settings)
    try:
        return conn.execute("select count(*) from trace_private.candidate_embeddings where owner_user_id = %s", (user,)).fetchone()[0]
    finally:
        conn.close()


def test_indexing_embeds_eligible_records_once_and_matching_is_owner_scoped(on, users, base_settings):
    s = Settings()
    u1, h1 = confirmed_profile(on, users)
    u2, _ = confirmed_profile(on, users, 0)
    conn, ctx1 = ctx_of(s, u1)
    _, ctx2 = ctx_of(s, u2)
    idx = EmbeddingIndex(conn, PROVIDER)
    first = idx.index_profile(ctx1)
    lines = sum(len(e["evidence"]) for e in records(on, h1)["employments"])
    assert first["status"] == "ok" and first["stored"] >= lines > 0 and stored(s, u1) == first["stored"]
    assert idx.index_profile(ctx1)["stored"] == 0                                   # idempotent
    got = idx.match(ctx1, "Decided what the team built next and in what order")
    assert got and all(str(r["source_id"]) in {str(x["id"]) for e in records(on, h1)["employments"] for x in e["evidence"]}
                       | {str(a["id"]) for a in records(on, h1).get("other_records", [])} for r in got)
    # user 2 asking with user 1's profile id gets nothing (the function scopes by the verified request identity)
    foreign = EmbeddingIndex(conn, PROVIDER).match(OwnerContext(owner_user_id=u2, candidate_profile_id=ctx1.candidate_profile_id), "roadmap")
    assert foreign == []
    assert stored(s, u2) == 0                                                         # never indexed, nothing shared
    conn.close()


def test_database_ranking_equals_in_memory_cosine(on, users):
    s = Settings()
    u, _ = confirmed_profile(on, users)
    conn, ctx = ctx_of(s, u)
    idx = EmbeddingIndex(conn, PROVIDER)
    idx.index_profile(ctx)
    with owner_session(conn, u) as repo:
        src = repo._rows("select * from trace_semantic.my_embedding_sources(%(p)s, %(m)s, %(v)s)",
                         {"p": ctx.candidate_profile_id, "m": PROVIDER.model, "v": PROVIDER.version})
    q = "Interviewed customers to understand their problems"
    qv = np.array(PROVIDER.embed([q])[0])
    mem = sorted(((float(np.array(PROVIDER.embed([x["content"]])[0]) @ qv), str(x["source_id"])) for x in src), reverse=True)
    dbr = idx.match(ctx, q, limit=200)
    assert [str(r["source_id"]) for r in dbr][:5] == [i for _, i in mem][:5]
    assert abs(dbr[0]["similarity"] - mem[0][0]) < 1e-4
    conn.close()


def test_edited_and_rejected_lines_never_return_stale_vectors(on, users, base_settings):
    s = Settings()
    u, h = confirmed_profile(on, users)
    conn, ctx = ctx_of(s, u)
    idx = EmbeddingIndex(conn, PROVIDER)
    idx.index_profile(ctx)
    line = records(on, h)["employments"][0]["evidence"][0]
    admin = db.connect(s)
    admin.autocommit = True
    # simulate a text change and a rejection directly (the API's correction path is tested elsewhere)
    admin.execute("alter table public.trace_candidate_evidence disable trigger user")
    try:
        admin.execute("update public.trace_candidate_evidence set evidence_text = evidence_text || ' (edited)' where candidate_evidence_id = %s", (line["id"],))
        other = records(on, h)["employments"][0]["evidence"][1]
        admin.execute("update public.trace_candidate_evidence set evidence_status = 'rejected' where candidate_evidence_id = %s", (other["id"],))
    finally:
        admin.execute("alter table public.trace_candidate_evidence enable trigger user")
        admin.close()
    ids = {str(r["source_id"]) for r in idx.match(ctx, "anything at all", limit=200)}
    assert str(line["id"]) not in ids and str(other["id"]) not in ids               # stale and rejected excluded
    again = idx.index_profile(ctx)
    assert again["stored"] == 1                                                       # only the edited line re-embedded
    assert str(line["id"]) in {str(r["source_id"]) for r in idx.match(ctx, "anything at all", limit=200)}
    conn.close()


def test_clearing_and_replacing_the_profile_delete_embeddings(on, users):
    s = Settings()
    u, h = confirmed_profile(on, users)
    conn, ctx = ctx_of(s, u)
    EmbeddingIndex(conn, PROVIDER).index_profile(ctx)
    assert stored(s, u) > 0
    view = on.post("/api/v1/resume-import?replace_profile=true", headers={**h, "Content-Type": "application/pdf"}, content=pdf(1)).json()["import"]
    assert on.post(f"/api/v1/resume-import/{view['import_id']}/confirm", headers=h, json=complete(on, h, view)).status_code == 200
    assert stored(s, u) == 0                                                           # replaced: old vectors gone
    conn2, ctx2 = ctx_of(s, u)
    EmbeddingIndex(conn2, PROVIDER).index_profile(ctx2)
    assert stored(s, u) > 0
    assert on.post("/api/v1/profile/clear", headers=h, json={"confirm": "clear my career profile"}).status_code == 200
    assert stored(s, u) == 0                                                           # cleared: gone
    conn.close(); conn2.close()


def test_unavailable_model_degrades_to_a_status(on, users, tmp_path):
    s = Settings()
    u, h = confirmed_profile(on, users)
    conn, ctx = ctx_of(s, u)
    broken = EmbeddingIndex(conn, GteSmallOnnx(tmp_path))                              # no files: unavailable
    assert broken.index_profile(ctx) == {"status": "unavailable"} and broken.match(ctx, "x") is None
    assert EmbeddingIndex(conn, None).index_profile(ctx) == {"status": "disabled"}
    assert on.get("/api/v1/capability-assessment?target=PM-PM", headers=h).status_code == 200   # product unaffected
    conn.close()


def test_role_knowledge_is_shared_and_separate(on, users):
    s = Settings()
    u, _ = confirmed_profile(on, users)
    conn, _ = ctx_of(s, u)
    got = match_knowledge(conn, PROVIDER, u, "Talked to customers to understand their problems", ["responsibility_area"], 3)
    if not got:
        pytest.skip("role knowledge not embedded locally (python -m scripts.embed_knowledge)")
    assert got[0]["source_key"] in ("product_discovery", "design_research", "customer_account_management")
    with owner_session(conn, u) as repo:                                               # the owner role can't read the table
        with pytest.raises(Exception):
            repo._rows("select count(*) from trace_ref.knowledge_embeddings", {})
    conn.close()
