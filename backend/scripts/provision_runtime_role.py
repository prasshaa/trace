"""Give the restricted runtime login (trace_app_runtime) a password on the LOCAL database and record its URL.

    python -m scripts.provision_runtime_role [--rotate]

Uses the admin connection (DATABASE_URL, the migration identity) only to run ALTER ROLE ... PASSWORD. Refuses any
database that is not demonstrably local (unix socket or loopback address, as reported by the connection). The new
password is generated here, written into backend/.env as DATABASE_RUNTIME_URL (file mode 600) and never printed.
Without --rotate, an existing DATABASE_RUNTIME_URL is left alone. For hosted environments set the password through the
provider's secret store instead; this script never runs against them.
"""
import argparse
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from psycopg import sql

from app.config import BACKEND_DIR, get_settings
from app.demo.fixtures import database_is_local

ENV_FILE = BACKEND_DIR / ".env"
ROLE = "trace_app_runtime"


def runtime_url(admin_url: str, password: str) -> str:
    parts = urlsplit(admin_url)
    host = parts.hostname or "127.0.0.1"
    netloc = f"{ROLE}:{quote(password, safe='')}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def upsert_env(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key:
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue
        out.append(line)
    if not done:
        out += ["# Restricted runtime login used by the API (written by scripts/provision_runtime_role.py).", f"{key}={value}"]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rotate", action="store_true", help="replace an existing runtime password")
    args = parser.parse_args(argv)
    settings = get_settings()
    if settings.database_url is None:
        print("refused: DATABASE_URL (the admin identity) is not set", file=sys.stderr)
        return 1
    if settings.database_runtime_url is not None and not args.rotate:
        print("DATABASE_RUNTIME_URL is already set; use --rotate to replace the password")
        return 0
    admin_url = settings.database_url.get_secret_value()
    with psycopg.connect(admin_url, connect_timeout=settings.db_connect_timeout, autocommit=True) as conn:
        if not database_is_local(conn):
            print("refused: the database connection is not local", file=sys.stderr)
            return 1
        if conn.execute("select 1 from pg_roles where rolname = %s", (ROLE,)).fetchone() is None:
            print(f"refused: role {ROLE} does not exist; apply the migrations first", file=sys.stderr)
            return 1
        password = secrets.token_urlsafe(32)
        conn.execute(sql.SQL("alter role {} password {}").format(sql.Identifier(ROLE), sql.Literal(password)))
    upsert_env(ENV_FILE, "DATABASE_RUNTIME_URL", runtime_url(admin_url, password))
    print(f"{ROLE} password set; DATABASE_RUNTIME_URL written to {ENV_FILE.name} (value not shown)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
