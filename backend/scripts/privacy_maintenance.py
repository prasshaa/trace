"""Privacy maintenance (admin, local): orphaned identity markers and old contribution queue events.

    python -m scripts.privacy_maintenance orphan-markers [--min-age-hours 24] [--apply]
    python -m scripts.privacy_maintenance queue-events [--retention-days N] [--apply]
    python -m scripts.privacy_maintenance reseal-markers [--apply]

Both are dry runs unless --apply is given; output is counts only.

orphan-markers   removes identity-marker rows whose private profile has no admitted real resume and no resume held
                 for review (pending or approved), and that are older than the grace period (at least 1 hour, so an
                 upload in progress is never affected). Markers of a profile with an admitted resume are never removed
                 (trace_private.purge_orphan_identity_markers decides).
reseal-markers   after a key rotation: re-seals marker rows sealed with a key in TRACE_IDENTITY_MARKER_PREVIOUS_KEYS with the
                 current TRACE_IDENTITY_MARKER_KEY. Reports counts: re-sealed, already current, and unavailable (sealed
                 with a key that is not configured; those stay as they are and their contributions wait for a person).
queue-events     deletes contribution queue events older than the retention period (default
                 TRACE_QUEUE_EVENT_RETENTION_DAYS, 90; at least 7). Queued requests themselves are not touched.

Run it on a schedule (for example daily). Refuses a non-local database.
"""
import argparse
import json
import sys

from app import db
from app.config import get_settings
from app.demo.fixtures import database_is_local


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["orphan-markers", "queue-events", "reseal-markers"])
    ap.add_argument("--min-age-hours", type=int, default=24)
    ap.add_argument("--retention-days", type=int)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    settings = get_settings()
    with db.connect(settings) as admin:
        if not database_is_local(admin):
            print("refused: the database connection is not local", file=sys.stderr)
            return 1
        if a.action == "reseal-markers":
            return _reseal(admin, settings, a.apply)
        try:
            with admin.transaction():
                if a.action == "orphan-markers":
                    n = admin.execute("select trace_private.purge_orphan_identity_markers(make_interval(hours => %s), %s)",
                                      (a.min_age_hours, a.apply)).fetchone()[0]
                else:
                    days = a.retention_days if a.retention_days is not None else settings.trace_queue_event_retention_days
                    n = admin.execute("select trace_private.purge_contribution_queue_events(%s, %s)", (days, a.apply)).fetchone()[0]
        except Exception as exc:                                          # parameter refused by the database
            print(f"refused: {type(exc).__name__}", file=sys.stderr)
            return 1
    print(json.dumps({"action": a.action, "applied": a.apply, ("removed" if a.apply else "would_remove"): n}))
    return 0


def _reseal(admin, settings, apply: bool) -> int:
    from app.services.contributions.sealing import MarkerKeyInvalid, sealer_from
    try:
        sealer = sealer_from(settings)
    except MarkerKeyInvalid:
        print("refused: an identity marker key is not valid", file=sys.stderr)
        return 1
    if sealer is None:
        print("refused: TRACE_IDENTITY_MARKER_KEY is not set", file=sys.stderr)
        return 1
    counts = {"resealed" if apply else "would_reseal": 0, "current": 0, "unavailable": 0}
    with admin.transaction():
        rows = admin.execute("select owner_user_id, sealed, key_id from trace_private.identity_markers "
                             "where sealed is not null for update").fetchall()
        for owner, sealed, key_id in rows:
            if key_id == sealer.key_id:
                counts["current"] += 1
            elif not sealer.can_open(key_id):
                counts["unavailable"] += 1
            else:
                counts["resealed" if apply else "would_reseal"] += 1
                if apply:
                    markers = sealer.open(owner, bytes(sealed), key_id)
                    admin.execute("update trace_private.identity_markers set sealed = %s, key_id = %s, updated_at = now() "
                                  "where owner_user_id = %s", (sealer.seal(owner, markers), sealer.key_id, owner))
    print(json.dumps({"action": "reseal-markers", "applied": apply, **counts}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
