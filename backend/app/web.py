"""Static frontend serving and browser security headers (Phase 5).

The frontend is plain HTML, CSS and JavaScript modules in backend/web, served under /app/ by this application, so the
browser talks to one origin for the app and the API. The only third-party script is the pinned, vendored Supabase JS
client (served from /app/vendor, checked with Subresource Integrity); nothing is loaded from a CDN at runtime.

Headers on every response:
  Content-Security-Policy  scripts, styles, fonts and images from this origin only; fetch/XHR to this origin and the
                           configured Supabase URL (Auth only: the app never calls the Data API); no framing; no
                           plugins; no <base> rewriting. The OAuth redirect is a top-level navigation, which CSP does
                           not restrict, so the Google flow keeps working.
  X-Content-Type-Options   nosniff
  Referrer-Policy          no-referrer (OAuth codes and tokens never leak through Referer)
  X-Frame-Options          DENY (frame-ancestors for older browsers)
  Permissions-Policy       camera, microphone, geolocation, payment disabled
  Cross-Origin-Opener-Policy same-origin
  Cache-Control            no-store on API responses (personal data); no-cache on app assets
FastAPI's interactive docs (/docs, /redoc) load their own CDN assets and keep every header except the CSP.
"""
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings

DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


def _origin(url) -> str:
    if not url:
        return ""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme in ("http", "https") and parts.netloc else ""


def content_security_policy(supabase_url) -> str:
    connect = " ".join(x for x in ("'self'", _origin(supabase_url)) if x)
    return ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
            f"connect-src {connect}; manifest-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; "
            "object-src 'none'")


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception as exc:                 # pragma: no cover - exercised by tests/test_error_privacy.py
            # Handled here so it never reaches Starlette's ServerErrorMiddleware, which re-raises to the server's own
            # logger with a full traceback (and the exception's arguments, which can quote request content).
            from app.api.errors import unexpected_response
            response = unexpected_response(exc)
        settings = app.dependency_overrides.get(get_settings, get_settings)()
        path = request.url.path
        h = response.headers
        if not path.startswith(DOCS_PATHS):
            h["Content-Security-Policy"] = content_security_policy(settings.supabase_url)
        h["X-Content-Type-Options"] = "nosniff"
        h["Referrer-Policy"] = "no-referrer"
        h["X-Frame-Options"] = "DENY"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        if settings.trace_hsts:                  # behind an HTTPS host only (TRACE_HSTS=1); never on plain-HTTP localhost
            h["Strict-Transport-Security"] = "max-age=31536000"
        if path.startswith("/api/"):
            if "no-store" not in h.get("cache-control", ""):     # a route may add "private"; never weaker than no-store
                h["Cache-Control"] = "no-store"
        elif path.startswith("/app") and "cache-control" not in h:
            h["Cache-Control"] = "no-cache"
        return response

    web_dir: Path = get_settings().trace_web_dir
    if web_dir.is_dir():
        app.mount("/app", StaticFiles(directory=web_dir, html=True), name="web")

        @app.get("/", include_in_schema=False)
        def root() -> RedirectResponse:
            return RedirectResponse("/app/", status_code=307)

        @app.get("/favicon.ico", include_in_schema=False)
        def favicon() -> RedirectResponse:
            return RedirectResponse("/app/favicon.svg", status_code=308)
