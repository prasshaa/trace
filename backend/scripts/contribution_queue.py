"""Contribution queue operations (admin, local). Shows queue health and retries terminally failed requests.

    python -m scripts.contribution_queue list                                   counts, and every terminally failed request
    python -m scripts.contribution_queue retry <user id> --operator "<name>"    start a fresh series of attempts

A request becomes terminally 'failed' after TRACE_CONTRIBUTION_MAX_ATTEMPTS unsuccessful attempts; it is never retried
automatically after that. 'retry' calls trace_private.retry_contribution, which only the admin identity can execute,
records the operator, and refuses when the user's contribution consent is no longer active. Output holds user ids,
counts, timestamps and error class codes only. Refuses a non-local database.
"""
import argparse
import json
import sys
from uuid import UUID

from app import db
from app.config import get_settings
from app.demo.fixtures import database_is_local


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["list", "retry"])
    ap.add_argument("target", nargs="?")
    ap.add_argument("--operator")
    a = ap.parse_args(argv)
    with db.connect(get_settings()) as admin:
        if not database_is_local(admin):
            print("refused: the database connection is not local", file=sys.stderr)
            return 1
        if a.action == "list":
            counts = admin.execute("select count(*) filter (where failed_at is null and locked_until is null), "
                                   "count(*) filter (where failed_at is null and locked_until is not null), "
                                   "count(*) filter (where failed_at is not null) from trace_private.contribution_requests").fetchone()
            failed = admin.execute("select user_id, attempts, last_error, failed_at from trace_private.contribution_requests "
                                   "where failed_at is not null order by failed_at").fetchall()
            print(json.dumps({"queued": counts[0], "in_progress": counts[1], "failed": counts[2],
                              "failed_requests": [{"user": str(u), "attempts": n, "last_error": e, "failed_at": t.isoformat()}
                                                  for u, n, e, t in failed]}, indent=2))
            return 0
        if not (a.operator and a.operator.strip()):
            print("refused: --operator is required", file=sys.stderr)
            return 1
        try:
            user = UUID(a.target or "")
        except ValueError:
            print("refused: a user id is required", file=sys.stderr)
            return 1
        with admin.transaction():
            outcome = admin.execute("select trace_private.retry_contribution(%s, %s)", (user, a.operator)).fetchone()[0]
        print(outcome)
        return 0 if outcome == "queued" else 1


if __name__ == "__main__":
    sys.exit(main())
