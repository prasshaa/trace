"""The server-controlled application data mode (Phase 5; 'production' since Phase 7.1).

The mode comes from backend configuration (TRACE_DATA_MODE) only. No request field, header or query parameter can set
it, and it alone decides the reference source that recommendations and career insights use:

  synthetic_demo   local development and internal QA: the synthetic reference profiles (cohort 'synthetic').
  production       eligible real contributed profiles only (cohort 'consented_real'). Synthetic profiles are never
                   served, never used as a fallback, and synthetic test fixtures cannot be enabled (fail closed).

Anything else fails closed with 503 (DataModeUnsupported).
"""
from fastapi import Depends

from app.config import Settings, get_settings

SUPPORTED_DATA_MODES = ("synthetic_demo", "production")
REFERENCE_SOURCE = {"synthetic_demo": "synthetic", "production": "consented_real"}


class DataModeUnsupported(Exception):
    """The configured data mode is unknown or not supported (503). Nothing is served from an unknown mode."""


def data_mode(settings: Settings = Depends(get_settings)) -> str:
    if settings.trace_data_mode not in SUPPORTED_DATA_MODES:
        raise DataModeUnsupported("the configured data mode is not supported")
    if settings.trace_data_mode == "production" and settings.trace_demo_fixtures:
        raise DataModeUnsupported("synthetic test fixtures cannot be enabled in production mode")
    return settings.trace_data_mode
