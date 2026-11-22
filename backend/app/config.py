"""Application settings, loaded from environment variables and an optional .env file."""
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Trace API"
    # No default: credentials must come from the environment or .env.
    # DATABASE_URL is the ADMIN / migration identity (table owner). Only scripts and tests use it; never configure it on
    # an API server. DATABASE_RUNTIME_URL is the restricted login (trace_app_runtime) and the only one the API opens;
    # the API refuses a runtime connection whose identity is a superuser, has BYPASSRLS or owns application tables.
    database_url: Optional[SecretStr] = None
    database_runtime_url: Optional[SecretStr] = None
    db_connect_timeout: int = 5

    # Supabase Auth (local stack). The anon key is public by design and is the only key ever sent to clients.
    supabase_url: Optional[str] = None                 # e.g. http://127.0.0.1:54321
    supabase_anon_key: Optional[str] = None
    # Access-token verification. Issuer defaults to {supabase_url}/auth/v1. Keys come from the issuer's JWKS
    # endpoint unless supabase_jwks_json supplies a static JSON Web Key Set (tests, offline use).
    supabase_jwt_issuer: Optional[str] = None
    supabase_jwt_audience: str = "authenticated"
    supabase_jwks_json: Optional[str] = None

    # Local synthetic demo (Phase 2). Off by default; only TRACE_DEMO_FIXTURES=1 enables fixture designation, and
    # then only against a local database (checked on the actual connection).
    trace_demo_fixtures: bool = False
    trace_fixture_dir: Path = BACKEND_DIR.parent / "data-foundation" / "data" / "synthetic"
    max_resume_bytes: int = 1_048_576

    # Application data mode (Phase 5). Server-controlled only; the browser can read it (/api/v1/config) but never set
    # it. 'synthetic_demo' is the only supported mode: career insights read the synthetic reference sample through an
    # explicit positive filter. Any other value fails closed (insights are refused).
    trace_data_mode: str = "synthetic_demo"

    # Backend rate limits: "<requests>/<seconds>" per verified user and per client IP, per bucket.
    # Phase 6: shared counters in PostgreSQL (trace_ops), atomic across workers. "memory" is an isolated-test backend
    # only: it is refused unless TRACE_RATE_LIMIT_ALLOW_MEMORY=1. A limiter failure refuses the request (503).
    trace_rate_limits_enabled: bool = True
    trace_rate_limit_backend: str = "postgres"
    trace_rate_limit_allow_memory: bool = False
    trace_rate_limit_namespace: str = "trace"
    rate_limit_upload_user: str = "5/3600"
    rate_limit_upload_ip: str = "20/3600"
    rate_limit_mutation_user: str = "240/60"
    rate_limit_mutation_ip: str = "600/60"
    rate_limit_read_user: str = "60/60"           # pathway and insights (the expensive reads)
    rate_limit_read_ip: str = "240/60"

    # Reference cohorts the API may serve (comma-separated, server-controlled). "synthetic" is the 41-profile synthetic
    # sample; "consented_real" is the consented, de-identified real reference pool. There is never a combined cohort.
    trace_enabled_cohorts: str = "synthetic"

    # Shared career-intelligence contribution policy (server-controlled). "opt_in": an explicit, separate decision;
    # never inferred from uploads or terms. "disabled": contribution consent is not offered. Anything else fails closed.
    trace_contribution_policy: str = "opt_in"

    # A user's own resume (Phase 6.1): POST /api/v1/real-resumes. Off by default; enable deliberately per environment.
    trace_real_resume_uploads: bool = False
    # Automatic contribution processing: an in-process loop draining the durable queue (safe with several workers).
    trace_contribution_worker: bool = True
    trace_contribution_worker_interval: float = 5.0
    # Bounded retries for a request whose processing raises an error: at most this many attempts (1-20), with
    # exponential backoff (base * 2^(attempt-1) seconds, capped), then a terminal 'failed' state until the user changes
    # their profile or an operator retries it. A claim is leased for trace_contribution_lease_seconds (10-900); an
    # attempt whose lease expires (the worker stopped mid-attempt) counts as one attempt.
    trace_contribution_max_attempts: int = Field(default=5, ge=1, le=20)
    trace_contribution_backoff_base_seconds: int = Field(default=30, ge=1, le=3600)
    trace_contribution_backoff_max_seconds: int = Field(default=3600, ge=1, le=86400)
    trace_contribution_lease_seconds: int = Field(default=120, ge=10, le=900)
    # AES-256-GCM key for identity markers (32 random bytes, URL-safe base64; generate with
    # `python -c "from app.services.contributions.sealing import new_key; print(new_key())"`). Held in the environment or
    # a secret manager, never in the database or Git. Without it, real-resume processing is refused (fail closed).
    trace_identity_marker_key: Optional[SecretStr] = None
    # Earlier marker keys, comma-separated, kept only while rows sealed with them are re-sealed after a rotation
    # (scripts/privacy_maintenance.py reseal-markers). Never used to seal.
    trace_identity_marker_previous_keys: Optional[SecretStr] = None
    # Retention of contribution queue events (scripts/privacy_maintenance.py; at least 7 days).
    trace_queue_event_retention_days: int = Field(default=90, ge=7, le=3650)

    @property
    def real_resume_uploads_available(self) -> bool:
        """Real-resume processing is offered only when switched on AND identity markers can be encrypted."""
        if not self.trace_real_resume_uploads:
            return False
        from app.services.contributions.sealing import MarkerKeyInvalid, sealer_from
        try:
            return sealer_from(self) is not None             # an invalid key counts as no key; its value is never shown
        except MarkerKeyInvalid:
            return False

    # Career Origins & Transitions (local proof of concept): read-only aggregates from the frozen India PM research cohort
    # (v3 final). Off by default. Product exposure needs a separate data-use and privacy review; enable only locally.
    trace_career_origins: bool = False
    trace_career_origins_artifact: Optional[Path] = None      # default: the artifact shipped with the service

    # Strict-Transport-Security on every response. Only for a deployment served over HTTPS (release, D52).
    trace_hsts: bool = False
    # Interactive API docs (/docs, /redoc, /openapi.json). Off by default; enable only for local development.
    trace_api_docs_enabled: bool = False

    # The static frontend (vanilla HTML, CSS and JavaScript), served by this application under /app/.
    trace_web_dir: Path = BACKEND_DIR / "web"

    @property
    def jwt_issuer(self) -> Optional[str]:
        if self.supabase_jwt_issuer:
            return self.supabase_jwt_issuer
        return f"{self.supabase_url.rstrip('/')}/auth/v1" if self.supabase_url else None


@lru_cache
def get_settings() -> Settings:
    return Settings()
