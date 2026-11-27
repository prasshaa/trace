"""Phase 2 (D53): the local gte-small provider. Skipped when the pinned files aren't present."""
import shutil

import numpy as np
import pytest

from app.config import Settings, get_settings
from app.services.embeddings.provider import DIMS, MODEL_FILES, EmbeddingUnavailable, GteSmallOnnx, provider_from

MODEL_DIR = get_settings().trace_embedding_model_dir
pytestmark = pytest.mark.skipif(not all((MODEL_DIR / f).is_file() for f in MODEL_FILES), reason="model files not present")
P = GteSmallOnnx(MODEL_DIR)


def test_vectors_are_384_unit_length_and_deterministic():
    a, b = P.embed(["Prioritised the backlog with engineering.", "Prioritised the backlog with engineering."])
    assert len(a) == DIMS and abs(np.linalg.norm(a) - 1) < 1e-5 and a == b


def test_related_text_ranks_above_unrelated_text():
    q, rel, unrel = (np.array(v) for v in P.embed(["Interviewed customers to find problems worth solving.",
                                                     "Product and customer discovery that shapes what is built.",
                                                     "Maintained the office printers and ordered supplies."]))
    assert q @ rel > q @ unrel


def test_long_and_empty_texts_are_handled():
    long_v, empty_v = P.embed(["word " * 5000, "   "])
    assert len(long_v) == DIMS and len(empty_v) == DIMS                        # truncated to 512 tokens; empty -> "."


def test_altered_model_files_make_the_provider_unavailable(tmp_path):
    for name in MODEL_FILES:
        shutil.copy(MODEL_DIR / name, tmp_path / name)
    (tmp_path / "config.json").write_text("{}")
    with pytest.raises(EmbeddingUnavailable):
        GteSmallOnnx(tmp_path).embed(["x"])


def test_embeddings_are_off_by_default():
    assert Settings().trace_embeddings is False and provider_from(Settings()) is None
