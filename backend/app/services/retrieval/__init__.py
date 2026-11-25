"""Hybrid retrieval (semantic-retrieval Phase 3): structured + full-text + semantic, fused by rank. Off by default."""
from app.services.retrieval.fusion import HYBRID, FusionConfig, fuse
from app.services.retrieval.hybrid import Candidate, HybridRetriever, Query, Retrieval

__all__ = ["HYBRID", "FusionConfig", "fuse", "Candidate", "HybridRetriever", "Query", "Retrieval"]
