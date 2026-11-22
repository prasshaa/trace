"""Central mapping of service and authentication errors to HTTP responses.

Responses carry only {"error": <code>, "message": <safe text>}. A message is passed through only when it contains
no SQL or schema detail; otherwise a fixed message for the status is used. Unexpected errors become a generic 500.
Exception types are logged (never the message, which may contain data); no stack trace reaches the client.
"""
import logging
import re
import uuid

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from app import db
from app.api.auth import AuthUnavailable, TokenRejected
from app.api.cohorts import CohortNotEnabled
from app.api.consent_routes import ContributionNotOffered, ContributionPolicyUnavailable
from app.api.mode import DataModeUnsupported
from app.api.ratelimit import RateLimited, RateLimiterUnavailable
from app.demo import fixtures as demo
from app.services.candidate_profile import repository as r
from app.services import real_resume as real
from app.services import resume_import as imp
from app.services.reference_evidence import EvidenceIntegrityError, ReferenceEvidenceError

logger = logging.getLogger(__name__)

STATUS = [   # most specific first
    ((demo.UploadTooLarge,), 413),
    ((demo.UnsupportedMediaType,), 415),
    ((demo.UnsupportedDocument,), 422),
    ((demo.DemoFixturesDisabled, real.RealUploadsDisabled), 403),
    ((real.ResumeNotProcessable, imp.UploadRejected), 422),
    ((imp.ImportPending,), 409),
    ((real.FixtureProfileConflict,), 409),
    ((demo.DesignationRefused, r.ConsentConflict), 409),
    ((r.RecordNotFound, r.ProfileNotFound), 404),
    ((r.IdentityMismatch, r.PermissionDenied, r.ConsentRequired, r.IngestionNotAllowed, ContributionNotOffered), 403),
    ((r.InvalidRecord, r.InvalidReference, r.ClarificationRequired), 422),
    ((r.RecordChanged, r.OrderConflictsWithDates, r.RecordNotEditable, r.InvalidTransition, r.CorrectionNotAvailable, r.DeletionBlocked, r.IngestionConflict,
      r.MappingConflict, r.ClassificationConflict, r.CorrectionHistoryViolation, r.ProfileAlreadyExists, r.ProfileAmbiguous,
      r.TargetIsCurrentRole), 409),
]
DEFAULT_MESSAGE = {401: "A valid access token is required.", 403: "This action is not allowed.", 404: "Not found.",
                   409: "The request conflicts with the current state of the record.", 413: "The upload is too large.",
                   415: "Unsupported media type.", 422: "The request is not valid.",
                   429: "Too many requests. Please wait a moment and try again.",
                   500: "An unexpected error occurred.", 503: "The service is temporarily unavailable."}
# Text that suggests database internals: such a message is replaced by the status's fixed message.
UNSAFE = re.compile(r"relation|constraint|column|violates|syntax|sqlstate|detail:|context:|psycopg|postgres|public\.|trace_\w+\.|"
                    r"\bselect\b|\binsert\b|\bupdate\b|\bdelete\b|function |permission denied for|password|secret|key=", re.I)


IDENTIFIER = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)   # never echo record ids


def _code(exc: Exception) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", type(exc).__name__).lower()


def _body(status: int, code: str, message: str = "") -> JSONResponse:
    safe = (message if message and len(message) <= 300 and not UNSAFE.search(message) and not IDENTIFIER.search(message)
            else DEFAULT_MESSAGE[status])
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(status_code=status, content={"error": code, "message": safe}, headers=headers)


def register(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        fields = sorted({".".join(str(p) for p in err.get("loc", ()) if p != "body") or "body" for err in exc.errors()})
        return JSONResponse(status_code=422, content={"error": "invalid_request",
                                                      "message": "The request is not valid.", "fields": fields})

    @app.exception_handler(TokenRejected)
    async def _token(_: Request, exc: TokenRejected):
        return _body(401, "unauthorized")                          # never says which check failed

    @app.exception_handler(RateLimited)
    async def _rate_limited(_: Request, exc: RateLimited):
        resp = _body(429, "rate_limited")
        resp.headers["Retry-After"] = str(exc.retry_after)
        return resp

    @app.exception_handler(RateLimiterUnavailable)
    async def _limiter(_: Request, exc: RateLimiterUnavailable):
        return _body(503, "rate_limiter_unavailable", "The service is temporarily unavailable.")

    @app.exception_handler(CohortNotEnabled)
    async def _cohort(_: Request, exc: CohortNotEnabled):
        return _body(403, "cohort_not_enabled", "That reference cohort is not available on this server.")

    @app.exception_handler(ContributionPolicyUnavailable)
    async def _policy(_: Request, exc: ContributionPolicyUnavailable):
        logger.error("unsupported contribution policy configured")
        return _body(503, "contribution_policy_unsupported", "The service is not configured for a supported contribution policy.")

    @app.exception_handler(DataModeUnsupported)
    async def _mode(_: Request, exc: DataModeUnsupported):
        logger.error("unsupported data mode configured")
        return _body(503, "data_mode_unsupported", "The service is not configured for a supported data mode.")

    @app.exception_handler(AuthUnavailable)
    async def _auth_unavailable(_: Request, exc: AuthUnavailable):
        logger.warning("authentication unavailable: %s", type(exc).__name__)
        return _body(503, "auth_unavailable")

    @app.exception_handler(imp.ReviewIncomplete)
    async def _review(_: Request, exc: imp.ReviewIncomplete):
        # Field-level messages are Trace's own fixed texts (never user or resume content); ids are draft keys.
        body = {"error": "review_incomplete", "message": str(exc),
                "fields": [{"item": f["item"], "field": f["field"], "message": f["message"]} for f in exc.fields]}
        return JSONResponse(status_code=422, content=body)

    @app.exception_handler(PydanticValidationError)
    async def _invalid(_: Request, exc: PydanticValidationError):
        # A value the service rejected (its pydantic models check every write). The error text can quote the input, so
        # neither the message nor the value is logged or returned: only the fact and the model.
        logger.warning("invalid input rejected by %s", getattr(exc, "title", "a model"))
        return _body(422, "invalid_input", "Some of the details aren't in a form Trace can save. Check them and try again.")

    @app.exception_handler(r.CandidateProfileError)
    async def _service(_: Request, exc: r.CandidateProfileError):
        for classes, status in STATUS:
            if isinstance(exc, classes):
                return _body(status, getattr(exc, "code", None) or _code(exc), str(exc))
        logger.warning("unmapped service error: %s", type(exc).__name__)
        return _body(500, "internal_error")

    @app.exception_handler(EvidenceIntegrityError)
    async def _integrity(_: Request, exc: EvidenceIntegrityError):
        logger.error("evidence integrity error")
        return _body(500, "internal_error")

    @app.exception_handler(ReferenceEvidenceError)
    async def _reference(_: Request, exc: ReferenceEvidenceError):
        return _body(422, _code(exc), str(exc))

    @app.exception_handler(db.PrivilegedRuntimeIdentity)
    async def _privileged(_: Request, exc: db.PrivilegedRuntimeIdentity):
        logger.error("refused a privileged runtime database identity")
        return _body(503, "database_unavailable")

    @app.exception_handler(db.DatabaseNotConfigured)
    async def _not_configured(_: Request, exc: db.DatabaseNotConfigured):
        return _body(503, "database_not_configured")

    @app.exception_handler(psycopg.OperationalError)
    async def _unreachable(_: Request, exc: psycopg.OperationalError):
        logger.warning("database unavailable: %s", type(exc).__name__)
        return _body(503, "database_unavailable")

    @app.exception_handler(psycopg.Error)
    async def _database(_: Request, exc: psycopg.Error):
        logger.error("database error: %s", type(exc).__name__)
        return _body(500, "internal_error")

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        return unexpected_response(exc)


def unexpected_response(exc: Exception) -> JSONResponse:
    """An unexpected error: a support reference is logged with the exception CLASS only (never its message, arguments or
    traceback, which can hold request content) and returned to the user."""
    ref = uuid.uuid4().hex[:10]
    logger.error("unexpected error: %s ref=%s", type(exc).__name__, ref)
    return JSONResponse(status_code=500, content={"error": "internal_error", "reference": ref,
                                                  "message": f"Something went wrong on our side. Reference: {ref}"})
