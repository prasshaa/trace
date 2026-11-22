"""Install the production resume-processing notice (app/demo/consent.py PRODUCTION_*) in the PRODUCTION database.

    DATABASE_URL=<production admin URL> .venv/bin/python scripts/install_production_notice.py

Installs the title and SHA-256 only (the text stays in the code and is shown only when the hash matches), marked
approved after the owner's privacy and legal review (D52). Idempotent: 'present' when already installed with the same
content; refuses (exit 2) if the version exists with different content. Never records consent for anyone, never
creates users, profiles or fixtures, and never installs the local-testing or demo notices.
"""
import os
import sys

import psycopg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.demo import consent  # noqa: E402


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1
    with psycopg.connect(url) as conn, conn.transaction():
        row = conn.execute("select title, body_sha256, purpose::text, legal_review_status from public.trace_consent_documents "
                           "where consent_version = %s", (consent.PRODUCTION_VERSION,)).fetchone()
        if row is None:
            conn.execute("insert into public.trace_consent_documents (consent_version, purpose, title, body_sha256, legal_review_status, "
                         "effective_from) values (%s, 'resume_processing', %s, %s, 'approved', now())",
                         (consent.PRODUCTION_VERSION, consent.PRODUCTION_TITLE, consent.PRODUCTION_BODY_SHA256))
            print(f"{consent.PRODUCTION_VERSION}: installed")
        elif row != (consent.PRODUCTION_TITLE, consent.PRODUCTION_BODY_SHA256, "resume_processing", "approved"):
            print(f"{consent.PRODUCTION_VERSION} exists with different content; not changed", file=sys.stderr)
            return 2
        else:
            print(f"{consent.PRODUCTION_VERSION}: present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
