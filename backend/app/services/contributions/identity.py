"""The resume owner's known identity markers, for de-identification only (Phase 6.2; sealed since Phase 7).

Markers (names, emails, phones, links, handles) come from the resume header, the PDF and the authenticated account at
upload time and are kept in trace_private.identity_markers so every later contribution rebuild can find them again in
corrected or edited text, also for email/password accounts without a name. They are stored only as an AES-256-GCM blob
(contributions.sealing; key in the environment), read and written only by the restricted runtime login through
trace_contrib functions, opened in memory while de-identifying, and never logged, returned by the API, written to a
contribution record or report, or used to derive any id.
"""
import json
from typing import Iterable, List, Optional, Tuple
from uuid import UUID

import psycopg

from app.services.candidate_profile.repository import IDENTITY_SQL
from app.services.contributions.sealing import MarkerSealer, MarkersUnavailable, merge
from app.services.deidentification import Identity


class MarkerProtectionUnavailable(Exception):
    """No identity-marker key is configured, so markers cannot be stored (real-resume processing is refused)."""


def _unique(values: Iterable) -> List[str]:
    out: List[str] = []
    for v in values:
        if v and str(v).strip() and str(v).strip() not in out:
            out.append(str(v).strip())
    return out


def _owner_identity(conn: psycopg.Connection, owner: UUID) -> None:
    conn.execute(IDENTITY_SQL, {"sub": str(owner), "claims": json.dumps({"sub": str(owner), "role": "authenticated"})})


def _read(conn: psycopg.Connection, user: UUID):
    with conn.transaction():
        return conn.execute("select email, names, emails, phones, urls, handles, markers_recorded, sealed, key_id "
                            "from trace_contrib.contribution_identity(%s)", (user,)).fetchone()


def _stored(row, user: UUID, sealer: Optional[MarkerSealer]) -> Tuple[dict, bool]:
    """(stored markers, usable). Legacy plaintext rows are usable as they are; a sealed row only with the right key."""
    _, names, emails, phones, urls, handles, recorded, sealed, key_id = row
    legacy = {"names": names or [], "emails": emails or [], "phones": phones or [], "urls": urls or [], "handles": handles or []}
    if not recorded:
        return legacy, False
    if sealed is None:
        return legacy, True
    if sealer is None:
        return legacy, False
    try:
        return merge(legacy, sealer.open(user, sealed, key_id)), True
    except MarkersUnavailable:
        return legacy, False


def load_identity(conn: psycopg.Connection, user: UUID, sealer: Optional[MarkerSealer]) -> Tuple[Identity, bool]:
    """The account identity together with the stored markers. The flag is False when no usable markers exist (none
    recorded, or sealed markers that cannot be opened here): the caller must then not rely on name detection."""
    row = _read(conn, user)
    if row is None:
        return Identity(), False
    stored, usable = _stored(row, user, sealer)
    email = row[0]
    names = _unique(stored["names"])        # the names column already merges the account's own names (database side)
    return Identity(full_name=names[0] if names else "", other_names=names[1:], emails=_unique([email, *stored["emails"]]),
                    phones=_unique(stored["phones"]), urls=_unique(stored["urls"]),
                    handles=_unique(stored["handles"])), usable


def store_identity(conn: psycopg.Connection, owner: UUID, identity: Identity, sealer: Optional[MarkerSealer]) -> None:
    """Seal the owner's markers (merged with any already kept) and store them. Runtime login only."""
    if sealer is None:
        raise MarkerProtectionUnavailable("identity markers cannot be stored without an encryption key")
    row = _read(conn, owner)
    existing = _stored(row, owner, sealer)[0] if row is not None else {}
    markers = merge(existing, {"names": identity.names(), "emails": list(identity.emails), "phones": list(identity.phones),
                               "urls": list(identity.urls), "handles": list(identity.handles)})
    with conn.transaction():
        _owner_identity(conn, owner)
        conn.execute("select trace_contrib.store_identity_markers(%s, %s)", (sealer.seal(owner, markers), sealer.key_id))


def forget_identity(conn: psycopg.Connection, owner: UUID) -> bool:
    """Remove the owner's markers after an ingestion that did not complete, unless an admitted or held resume still
    needs them (the database decides)."""
    with conn.transaction():
        _owner_identity(conn, owner)
        return bool(conn.execute("select trace_contrib.forget_identity_markers()").fetchone()[0])
