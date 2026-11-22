"""Career Origins & Transitions: read-only, versioned aggregates from the frozen India PM research cohort (v3 final).

Separate from every Trace data source: never mixed with users' private profiles, the consented real reference pool or
synthetic fixtures, and never a weight, probability, prediction or plan input. Off unless TRACE_CAREER_ORIGINS=1, and
an internal preview only until the release gates in docs/CAREER_ORIGINS_GOVERNANCE.md are cleared.
"""
from app.services.career_origins.service import (
    ARTIFACT_PATH, FAMILY_TO_FUNCTION, TAXONOMY_VERSION, ArtifactUnavailable, CareerOriginsDisabled, connection,
    load_artifact, public,
)

__all__ = ["ARTIFACT_PATH", "FAMILY_TO_FUNCTION", "TAXONOMY_VERSION", "ArtifactUnavailable", "CareerOriginsDisabled",
           "connection", "load_artifact", "public"]
