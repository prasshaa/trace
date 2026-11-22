"""Authenticated encryption of identity markers (Phase 7 pre-flight).

The markers (a user's names, emails, phones, links and handles) are sealed with AES-256-GCM before they reach the
database and opened only in the backend's memory while de-identifying. The key comes from the environment
(TRACE_IDENTITY_MARKER_KEY: 32 random bytes, URL-safe base64), never from the database or Git. The owner's user id is
bound in as associated data, so a sealed blob cannot be moved to another user. The database keeps a fingerprint of the
key (key_id, the first 8 bytes of its SHA-256), which detects a wrong key without revealing it.

Nothing here logs or returns marker values in an error: a blob that cannot be opened raises MarkersUnavailable, which
callers treat as "markers not known" (the contribution then waits for a person), never as an empty identity.

Key lifecycle (D48): the key must be STABLE across restarts (a secret manager in production; locally a gitignored env
file, see backend/README.md). Every row records the key_id it was sealed with. Rotation: set the new key as
TRACE_IDENTITY_MARKER_KEY and the old one(s) in TRACE_IDENTITY_MARKER_PREVIOUS_KEYS (comma-separated); rows still open
with the old key, and `python -m scripts.privacy_maintenance reseal-markers --apply` re-seals them with the new key, after
which the old key can be retired. A row whose key is not configured at all cannot be opened: the contribution waits for
a person (never treated as "no identifiers"), and `reseal-markers` reports such rows by count.
"""
import base64
import hashlib
import json
import os
from typing import Dict, List, Optional
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AAD_PREFIX = b"trace-identity-markers:v1:"
LIMITS = {"names": 16, "emails": 16, "phones": 16, "urls": 32, "handles": 16}
MAX_VALUE = 320


class MarkerKeyInvalid(ValueError):
    """TRACE_IDENTITY_MARKER_KEY is not 32 bytes of URL-safe base64."""


class MarkersUnavailable(Exception):
    """A sealed blob could not be opened (no key, another key, or tampered data). Carries no content."""


def decode_key(value: str) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(value.strip() + "=" * (-len(value.strip()) % 4))
    except (ValueError, TypeError):
        raise MarkerKeyInvalid("the identity marker key is not valid base64") from None
    if len(raw) != 32:
        raise MarkerKeyInvalid("the identity marker key must be 32 bytes")
    return raw


def new_key() -> str:
    """A fresh key for TRACE_IDENTITY_MARKER_KEY (for operators and tests)."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


def normalise(markers: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Ordered, trimmed, de-duplicated and bounded marker lists (the same rules as the v2.10.0 database merge)."""
    out: Dict[str, List[str]] = {}
    for key, cap in LIMITS.items():
        seen: List[str] = []
        for v in markers.get(key) or []:
            v = str(v).strip() if v is not None else ""
            if v and len(v) <= MAX_VALUE and v not in seen:
                seen.append(v)
        out[key] = seen[:cap]
    return out


def merge(*parts: Dict[str, List[str]]) -> Dict[str, List[str]]:
    combined: Dict[str, List[str]] = {k: [] for k in LIMITS}
    for p in parts:
        for k in LIMITS:
            combined[k] += list(p.get(k) or [])
    return normalise(combined)


def key_id_of(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


class MarkerSealer:
    def __init__(self, key: bytes, previous: tuple = ()):
        if len(key) != 32:
            raise MarkerKeyInvalid("the identity marker key must be 32 bytes")
        self._aead = AESGCM(key)
        self.key_id = key_id_of(key)
        # Older keys, used only to OPEN rows sealed before a rotation (never to seal).
        self._previous = {key_id_of(k): AESGCM(k) for k in previous if len(k) == 32 and key_id_of(k) != self.key_id}

    def can_open(self, key_id: str) -> bool:
        return key_id == self.key_id or key_id in self._previous

    def __repr__(self) -> str:                                       # never shows the key
        return f"MarkerSealer(key_id={self.key_id})"

    @staticmethod
    def _aad(owner: UUID) -> bytes:
        return AAD_PREFIX + str(owner).encode()

    def seal(self, owner: UUID, markers: Dict[str, List[str]]) -> bytes:
        nonce = os.urandom(12)
        body = json.dumps(normalise(markers), sort_keys=True, separators=(",", ":")).encode()
        return nonce + self._aead.encrypt(nonce, body, self._aad(owner))

    def open(self, owner: UUID, sealed: bytes, key_id: str) -> Dict[str, List[str]]:
        aead = self._aead if key_id == self.key_id else self._previous.get(key_id)
        if aead is None:
            raise MarkersUnavailable("sealed with another key")
        try:
            body = aead.decrypt(bytes(sealed[:12]), bytes(sealed[12:]), self._aad(owner))
        except Exception:
            raise MarkersUnavailable("the sealed markers could not be opened") from None
        return normalise(json.loads(body))


def sealer_from(settings) -> Optional[MarkerSealer]:
    """The configured sealer, or None when no key is configured (real-resume processing is then refused)."""
    secret = getattr(settings, "trace_identity_marker_key", None)
    if secret is None:
        return None
    value = secret.get_secret_value() if hasattr(secret, "get_secret_value") else str(secret)
    if not value.strip():
        return None
    old = getattr(settings, "trace_identity_marker_previous_keys", None)
    old = old.get_secret_value() if hasattr(old, "get_secret_value") else (old or "")
    return MarkerSealer(decode_key(value), tuple(decode_key(k) for k in old.split(",") if k.strip()))
