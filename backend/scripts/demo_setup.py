"""Local synthetic demo setup. Safe to rerun. Refuses anything that is not demonstrably local.

    cd backend
    export SUPABASE_SERVICE_ROLE_KEY=...   # from `supabase status` (local only); never stored or printed
    export TRACE_DEMO_PASSWORD=...         # password for the demo accounts; never printed
    .venv/bin/python -m scripts.demo_setup --users 2

What it does:
  * checks that SUPABASE_URL is a loopback URL AND that the actual database connection (DATABASE_URL) is local AND
    that the database holds the 41 approved synthetic fixtures (the synthetic seed); otherwise it stops;
  * installs the synthetic demo consent document (title and SHA-256 of app/demo/consent.py), approved, for
    resume_processing; an existing document with the same version but different content is never overwritten;
  * creates (or, on rerun, updates the password and labels of) demo users demo-candidate-<n>@example.invalid in
    local Supabase Auth, labelled as synthetic demo accounts.

What it never does: record consent on anyone's behalf, designate fixtures, create profiles or touch real data.
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Optional

import psycopg

from app import db
from app.config import get_settings
from app.demo import consent
from app.demo.fixtures import database_is_local

EMAIL = "demo-candidate-{}@example.invalid"
METADATA = {"trace_demo": True, "label": "Synthetic demo account (local only)"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


class SetupRefused(RuntimeError):
    pass


def require_local(supabase_url: Optional[str], conn: psycopg.Connection) -> None:
    host = urllib.parse.urlparse(supabase_url or "").hostname
    if host not in LOCAL_HOSTS:
        raise SetupRefused("SUPABASE_URL must be a local (loopback) URL")
    if not database_is_local(conn):
        raise SetupRefused("the database connection must be local")
    with conn.transaction():
        n = conn.execute("select count(*) from trace_ingest.approved_fixture_resumes").fetchone()[0]
    if n != 41:
        raise SetupRefused("the database does not hold the 41 approved synthetic fixtures (synthetic seed not loaded)")


def install_consent_document(conn: psycopg.Connection) -> str:
    """'installed', 'present' or SetupRefused if the version exists with different content."""
    with conn.transaction():
        row = conn.execute("select title, body_sha256, purpose::text, legal_review_status from public.trace_consent_documents "
                           "where consent_version = %s", (consent.VERSION,)).fetchone()
        if row is None:
            conn.execute("insert into public.trace_consent_documents (consent_version, purpose, title, body_sha256, legal_review_status, "
                         "effective_from) values (%s, 'resume_processing', %s, %s, 'approved', now())",
                         (consent.VERSION, consent.TITLE, consent.BODY_SHA256))
            return "installed"
    if row != (consent.TITLE, consent.BODY_SHA256, "resume_processing", "approved"):
        raise SetupRefused("a consent document with the demo version exists with different content; it was not changed")
    return "present"


def retire_legacy_consent_documents(conn: psycopg.Connection) -> int:
    """Retire earlier local notices that no longer describe what Trace stores (consent.LEGACY_VERSIONS). Decisions recorded
    for them stay as history; an owner is asked to agree to the current notice before the next upload."""
    with conn.transaction():
        return conn.execute("update public.trace_consent_documents set retired_at = now() where consent_version = any(%s) "
                            "and retired_at is null", (list(consent.LEGACY_VERSIONS),)).rowcount


def install_contribution_document(conn: psycopg.Connection) -> str:
    """The local demo notice for the separate shared-contribution purpose. 'installed', 'present' or SetupRefused."""
    with conn.transaction():
        row = conn.execute("select title, body_sha256, purpose::text, legal_review_status from public.trace_consent_documents "
                           "where consent_version = %s", (consent.CONTRIBUTION_VERSION,)).fetchone()
        if row is None:
            conn.execute("insert into public.trace_consent_documents (consent_version, purpose, title, body_sha256, legal_review_status, "
                         "effective_from) values (%s, 'deidentified_shared_dataset', %s, %s, 'approved', now())",
                         (consent.CONTRIBUTION_VERSION, consent.CONTRIBUTION_TITLE, consent.CONTRIBUTION_BODY_SHA256))
            return "installed"
    if row != (consent.CONTRIBUTION_TITLE, consent.CONTRIBUTION_BODY_SHA256, "deidentified_shared_dataset", "approved"):
        raise SetupRefused("a consent document with the demo contribution version exists with different content; it was not changed")
    return "present"


Http = Callable[[str, str, Optional[dict]], dict]


def http_client(api_url: str, service_key: str) -> Http:
    def call(method: str, path: str, body: Optional[dict] = None) -> dict:
        req = urllib.request.Request(api_url.rstrip("/") + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"apikey": service_key, "Authorization": f"Bearer {service_key}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raise SetupRefused(f"Supabase Auth admin request failed with HTTP {exc.code}") from None   # no body, no key
    return call


def _existing(http: Http) -> Dict[str, str]:
    out, page = {}, 1
    while True:
        users = http("GET", f"/auth/v1/admin/users?page={page}&per_page=200", None).get("users", [])
        out.update({u["email"]: u["id"] for u in users if u.get("email")})
        if len(users) < 200:
            return out
        page += 1


def ensure_demo_users(http: Http, password: str, count: int) -> List[tuple]:
    if not password or len(password) < 8:
        raise SetupRefused("TRACE_DEMO_PASSWORD must be at least 8 characters")
    existing, done = _existing(http), []
    for i in range(1, count + 1):
        email = EMAIL.format(i)
        if email in existing:
            http("PUT", f"/auth/v1/admin/users/{existing[email]}", {"password": password, "user_metadata": METADATA})
            done.append((email, "updated"))
        else:
            http("POST", "/auth/v1/admin/users", {"email": email, "password": password, "email_confirm": True, "user_metadata": METADATA})
            done.append((email, "created"))
    return done


def main(argv=None, environ=None) -> int:
    import os
    env = environ if environ is not None else os.environ
    ap = argparse.ArgumentParser(description="Local synthetic demo setup")
    ap.add_argument("--users", type=int, default=2)
    a = ap.parse_args(argv)
    if not 1 <= a.users <= 10:
        print("--users must be between 1 and 10", file=sys.stderr)
        return 2
    settings = get_settings()
    try:
        with db.connect(settings) as conn:
            require_local(settings.supabase_url, conn)
            print(f"consent document {consent.VERSION}: {install_consent_document(conn)}")
            print(f"earlier notices retired: {retire_legacy_consent_documents(conn)}")
            print(f"consent document {consent.CONTRIBUTION_VERSION}: {install_contribution_document(conn)}")
        key, password = env.get("SUPABASE_SERVICE_ROLE_KEY"), env.get("TRACE_DEMO_PASSWORD")
        if not key:
            raise SetupRefused("SUPABASE_SERVICE_ROLE_KEY is not set")
        for email, status in ensure_demo_users(http_client(settings.supabase_url, key), password, a.users):
            print(f"demo user {email}: {status}")
        print("No consent was recorded: each demo user grants it in the app.")
        return 0
    except SetupRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
