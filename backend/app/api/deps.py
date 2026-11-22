"""Request dependencies: verified owner, per-request database connection, trusted owner context.

The owner identity comes only from the verified access token (TokenVerifier.owner -> sub). No endpoint accepts an
owner or profile id from the client: the candidate profile is resolved for the owner, inside an owner session
(role authenticated, RLS), by CandidateProfileService.owner_profile_id.
"""
from functools import lru_cache
from typing import Iterator, Optional
from uuid import UUID

import psycopg
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import db
from app.api.auth import TokenRejected, TokenVerifier
from app.config import Settings, get_settings
from app.services.candidate_profile import CandidateProfileService, OwnerContext, ProfileNotFound

bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=8)
def _verifier(issuer: Optional[str], audience: str, jwks_json: Optional[str]) -> TokenVerifier:
    return TokenVerifier(issuer, audience, jwks_json)


def get_verifier(settings: Settings = Depends(get_settings)) -> TokenVerifier:
    return _verifier(settings.jwt_issuer, settings.supabase_jwt_audience, settings.supabase_jwks_json)


def current_owner(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
                  verifier: TokenVerifier = Depends(get_verifier)) -> UUID:
    """The authenticated owner (verified token sub). 401 for a missing, malformed, invalid or expired token."""
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise TokenRejected("missing bearer token")
    return verifier.owner(credentials.credentials)


def db_connection(settings: Settings = Depends(get_settings)) -> Iterator[psycopg.Connection]:
    """One restricted runtime connection per request (never the admin identity), closed when the request ends."""
    conn = db.connect_runtime(settings)
    try:
        yield conn
    finally:
        conn.close()


def profile_service(conn: psycopg.Connection = Depends(db_connection)) -> CandidateProfileService:
    return CandidateProfileService(conn)


def owner_context(owner: UUID = Depends(current_owner),
                  service: CandidateProfileService = Depends(profile_service)) -> OwnerContext:
    """The trusted context for profile operations: the verified owner and their own profile (404 if none yet)."""
    profile_id = service.owner_profile_id(owner)
    if profile_id is None:
        raise ProfileNotFound("no candidate profile exists for this account yet")
    return OwnerContext(owner_user_id=owner, candidate_profile_id=profile_id)
