import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app import db
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"status": "ok"}


@router.get("/db")
def health_db(settings: Settings = Depends(get_settings)):
    try:
        db.check_connection(settings)
    except db.DatabaseNotConfigured:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "database": "not_configured"},
        )
    except Exception as exc:  # do not leak connection details to clients
        logger.warning("Database health check failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={"status": "error", "database": "unreachable"},
        )
    return {"status": "ok", "database": "ok"}
