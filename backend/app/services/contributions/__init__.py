"""Automatic, idempotent contribution of a consenting user's de-identified profile (Phase 6.1)."""
from app.services.contributions.builder import canonical_record
from app.services.contributions.processor import process_pending, process_user, record_sha256

__all__ = ["canonical_record", "process_pending", "process_user", "record_sha256"]
