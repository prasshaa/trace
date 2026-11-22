"""Supabase access-token verification.

Accepted only when every check passes:
  * the signature verifies with a key from the issuer's JSON Web Key Set, selected by the token's kid;
  * the algorithm is ES256 (pinned: the token's own alg header never widens it; no 'none', no HS256);
  * exp, sub, iss and aud are present; the token is not expired; iss equals the configured issuer; aud equals the
    configured audience ('authenticated');
  * the role claim is 'authenticated';
  * sub is a UUID. It is the only source of the owner identity.

Any failure is a TokenRejected (HTTP 401) with a generic message: no claim, key or library detail reaches the client.
"""
import json
import logging
from typing import Optional
from uuid import UUID

import jwt
from jwt import PyJWKClient, PyJWKSet

logger = logging.getLogger(__name__)
ALGORITHMS = ["ES256"]
REQUIRED_CLAIMS = ["exp", "sub", "iss", "aud"]


class TokenRejected(Exception):
    """The access token is missing, malformed, unverifiable, expired or not for this API."""


class AuthUnavailable(Exception):
    """Verification keys could not be obtained (configuration or key endpoint). Nothing is accepted."""


class TokenVerifier:
    def __init__(self, issuer: Optional[str], audience: str, jwks_json: Optional[str] = None):
        if not issuer:
            raise AuthUnavailable("token issuer is not configured")
        self.issuer, self.audience = issuer, audience
        self._static = PyJWKSet.from_dict(json.loads(jwks_json)) if jwks_json else None
        self._client = None if self._static else PyJWKClient(f"{issuer.rstrip('/')}/.well-known/jwks.json", cache_keys=True,
                                                             lifespan=300, timeout=5)

    def _key(self, token: str):
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError as exc:
            raise TokenRejected("malformed token") from exc
        if not kid:
            raise TokenRejected("token has no key id")
        if self._static is not None:
            for k in self._static.keys:
                if k.key_id == kid:
                    return k.key
            raise TokenRejected("unknown key id")
        try:
            return self._client.get_signing_key(kid).key
        except jwt.PyJWKClientConnectionError as exc:
            raise AuthUnavailable("key endpoint unreachable") from exc
        except jwt.PyJWKClientError as exc:
            raise TokenRejected("unknown key id") from exc

    def owner(self, token: str) -> UUID:
        """The verified owner identity (sub). Raises TokenRejected or AuthUnavailable."""
        key = self._key(token)
        try:
            claims = jwt.decode(token, key, algorithms=ALGORITHMS, audience=self.audience, issuer=self.issuer,
                                options={"require": REQUIRED_CLAIMS, "verify_signature": True, "verify_exp": True,
                                         "verify_aud": True, "verify_iss": True})
        except jwt.PyJWTError as exc:
            logger.info("access token rejected: %s", type(exc).__name__)
            raise TokenRejected("invalid token") from exc
        if claims.get("role") != "authenticated":
            raise TokenRejected("token role is not authenticated")
        try:
            return UUID(str(claims["sub"]))
        except ValueError as exc:
            raise TokenRejected("subject is not a user id") from exc
