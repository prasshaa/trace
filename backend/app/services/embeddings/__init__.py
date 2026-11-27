"""Embeddings (semantic-retrieval workstream, Phase 2, D53). Local only: text never leaves the application server."""
from app.services.embeddings.provider import (EmbeddingProvider, EmbeddingUnavailable, GteSmallOnnx, MODEL_FILES,
                                              provider_from)

__all__ = ["EmbeddingProvider", "EmbeddingUnavailable", "GteSmallOnnx", "MODEL_FILES", "provider_from"]
