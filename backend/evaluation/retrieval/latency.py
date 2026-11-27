"""End-to-end latency baseline (Phase 1): capability assessment and plan generation through the real API on the LOCAL
stack, with one transient account and a FICTIONAL resume (evaluation corpus). The account is deleted afterwards.

    .venv/bin/python -m evaluation.retrieval.latency [runs]

Reports per-request wall time (mean, p95) for GET capability assessment and POST transition-plan. Never run against
production.
"""
import statistics
import sys
import time
import uuid

from app import db
from app.config import get_settings
from app.demo import consent as demo_consent
from evaluation.resume_parser.corpus import P
from evaluation.resume_parser.render import render
from tests.test_api_foundation import auth, token
from tests.test_api_phase2 import make_client
from tests.test_resume_import_api import complete


def main(runs: int = 10) -> int:
    settings = get_settings()
    url = settings.database_url.get_secret_value() if settings.database_url else ""
    if "@127.0.0.1:" not in url and "@localhost:" not in url:
        print("refused: not the local stack")
        return 2
    admin = db.connect(settings)
    admin.autocommit = True
    user = uuid.uuid4()
    admin.execute("insert into auth.users (id, email) values (%s, %s)", (user, f"latency-{user}@example.invalid"))
    try:
        with make_client(settings, trace_real_resume_uploads=True) as c:
            h = auth(token(user))
            c.post("/api/v1/profile", headers=h, json={})
            c.post("/api/v1/consent/resume-processing", headers=h, json={"decision": "granted", "consent_version": demo_consent.VERSION})
            v = c.post("/api/v1/resume-import", headers={**h, "Content-Type": "application/pdf"}, content=render(P[2], "pdf_classic")).json()["import"]
            assert c.post(f"/api/v1/resume-import/{v['import_id']}/confirm", headers=h, json=complete(c, h, v)).status_code == 200
            timings = {"capability assessment": [], "create plan": [], "plan roadmap": []}
            for _ in range(runs):
                t = time.perf_counter(); r = c.get("/api/v1/capability-assessment?target=PM-SPM", headers=h)
                timings["capability assessment"].append((time.perf_counter() - t) * 1000)
                t = time.perf_counter(); r = c.post("/api/v1/transition-plan?target=PM-SPM", headers=h)
                timings["create plan"].append((time.perf_counter() - t) * 1000)
                rid = r.json()["run_id"]
                t = time.perf_counter(); c.get(f"/api/v1/transition-plan/{rid}/roadmap", headers=h)
                timings["plan roadmap"].append((time.perf_counter() - t) * 1000)
        for k, xs in timings.items():
            xs = sorted(xs)
            print(f"{k:22} mean {statistics.fmean(xs):7.1f} ms   p95 {xs[int(0.95 * (len(xs) - 1))]:7.1f} ms   (n={len(xs)})")
    finally:
        admin.execute("delete from auth.users where id = %s", (user,))
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 10))
