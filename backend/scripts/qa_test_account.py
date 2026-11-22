"""Internal QA: give an internal test account a private career profile built from an approved synthetic fixture.

    QA_TEST_PASSWORD=... python -m scripts.qa_test_account --email qa-1@example.invalid --fixture 7

Engineering and QA tooling, not a product feature, and not reachable from the frontend. It exists because resume upload
is switched off in internal environments, and the product does not offer fictional profiles to users.

What it does, as the test account itself (the account's own password, from QA_TEST_PASSWORD; never an admin key):
  1. signs in to the local Supabase Auth server and obtains the account's own access token;
  2. records the account's own personal-analysis decision through POST /api/v1/consent/resume-processing (the same
     endpoint and checks as the product; the operator owns the test account and makes this decision for it);
  3. uploads the approved synthetic fixture's PDF through POST /api/v1/resumes, where the API still enforces everything:
     TRACE_DEMO_FIXTURES=1, data mode synthetic_demo, a local database, the fixture allowlist (hash), the never-merge rule.
The resulting profile is marked as a test fixture in the database and is never eligible for the shared pool.

Refuses unless the API and the Auth server are on a loopback address. Prints no token, password or personal data.
"""
import argparse
import ipaddress
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

from app.config import get_settings


def _loopback(url: str) -> bool:
    host = urlparse(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _call(method: str, url: str, *, token=None, body=None, raw=None, headers=None, apikey=None):
    h = {"Accept": "application/json", **(headers or {})}
    data = None
    if token:
        h["Authorization"] = f"Bearer {token}"
    if apikey:
        h["apikey"] = apikey
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if raw is not None:
        data = raw
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            ctype = resp.headers.get("Content-Type", "")
            payload = resp.read()
            return resp.status, (json.loads(payload) if "json" in ctype else payload)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("error", "error")
        except Exception:
            detail = "error"
        return exc.code, {"error": detail}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--email", required=True)
    ap.add_argument("--fixture", type=int, required=True, help="approved synthetic fixture number (see /api/v1/demo/samples)")
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    a = ap.parse_args(argv)
    settings = get_settings()
    password = os.environ.get("QA_TEST_PASSWORD")
    if not password:
        print("refused: set QA_TEST_PASSWORD to the test account's password", file=sys.stderr)
        return 2
    if not settings.supabase_url or not settings.supabase_anon_key:
        print("refused: SUPABASE_URL and SUPABASE_ANON_KEY are not configured", file=sys.stderr)
        return 2
    if not (_loopback(a.api) and _loopback(settings.supabase_url)):
        print("refused: the API and the Auth server must be local (loopback) for QA test accounts", file=sys.stderr)
        return 1

    status, session = _call("POST", f"{settings.supabase_url.rstrip('/')}/auth/v1/token?grant_type=password",
                            body={"email": a.email, "password": password}, apikey=settings.supabase_anon_key)
    if status != 200 or not isinstance(session, dict) or "access_token" not in session:
        print("refused: could not sign in as the test account", file=sys.stderr)
        return 1
    token = session["access_token"]
    api = a.api.rstrip("/")

    status, me = _call("GET", f"{api}/api/v1/me", token=token)
    if status == 200 and not me.get("has_profile"):
        _call("POST", f"{api}/api/v1/profile", token=token, body={})
    status, consent = _call("GET", f"{api}/api/v1/consent/resume-processing", token=token)
    if status != 200 or not consent.get("document"):
        print("refused: the personal-analysis notice is not installed (run scripts.demo_setup)", file=sys.stderr)
        return 1
    if not consent.get("active"):
        status, consent = _call("POST", f"{api}/api/v1/consent/resume-processing", token=token,
                                body={"decision": "granted", "consent_version": consent["document"]["consent_version"]})
        if status != 200 or not consent.get("active"):
            print(f"refused: the decision was not recorded ({status})", file=sys.stderr)
            return 1
    status, pdf = _call("GET", f"{api}/api/v1/demo/samples/{a.fixture}/resume.pdf", token=token,
                        headers={"Accept": "application/pdf"})
    if status != 200:
        print(f"refused: fixture {a.fixture} is not available here ({status} {pdf.get('error') if isinstance(pdf, dict) else ''})",
              file=sys.stderr)
        return 1
    status, result = _call("POST", f"{api}/api/v1/resumes", token=token, raw=pdf,
                           headers={"Content-Type": "application/pdf", "X-Filename": f"qa-fixture-{a.fixture:03d}.pdf"})
    if status != 201:
        print(f"refused: the upload was not accepted ({status} {result.get('error') if isinstance(result, dict) else ''})",
              file=sys.stderr)
        return 1
    print(json.dumps({"fixture": a.fixture, "status": result.get("status"), "extracted": result.get("extracted")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
