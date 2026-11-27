"""The embedding provider behind one small interface, so the model can change later without touching callers.

GteSmallOnnx runs thenlper/gte-small (MIT; 384 dimensions; English; 512 tokens) IN PROCESS with onnxruntime: no network
call, no external service, no telemetry. Mean pooling over the attention mask and L2 normalisation, as the model card
specifies, so cosine similarity is a dot product. The model files are pinned to one upstream revision and verified by
SHA-256 before loading; a missing or altered file makes the provider unavailable (callers fall back to the lexical
path), never a different model.

Indexing and querying must use the same (model, version): `version` changes whenever the files or the pooling change,
and stored vectors of another version are never compared with new ones.
"""
import hashlib
import threading
from pathlib import Path
from typing import List, Optional, Protocol, Sequence

MODEL_ID = "thenlper/gte-small"
REVISION = "17e1f347d17fe144873b1201da91788898c639cd"
MODEL_FILES = {   # name -> SHA-256 at REVISION
    "model.onnx": "0b01312b59bec0a2558a626f2937be4cbe4bb16d1511560153f598cec488f1f8",
    "tokenizer.json": "da0e79933b9ed51798a3ae27893d3c5fa4a201126cef75586296df9b4d2c62a0",
    "config.json": "00273cdfb3e05c7304d180270bdef8adae45023e7bdfc396338bdd815943dc59",
}
UPSTREAM = {"model.onnx": "onnx/model.onnx", "tokenizer.json": "tokenizer.json", "config.json": "config.json"}
DIMS = 384
MAX_TOKENS = 512


class EmbeddingUnavailable(RuntimeError):
    """The provider can't produce embeddings (switched off, files missing or altered, runtime error)."""


class EmbeddingProvider(Protocol):
    model: str
    version: str
    dims: int

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        ...


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class GteSmallOnnx:
    model = MODEL_ID
    version = f"{REVISION[:12]}-fp32-meanpool-l2"
    dims = DIMS

    def __init__(self, model_dir: Path, threads: int = 1, batch_size: int = 16):
        self._dir = Path(model_dir)
        self._threads = threads
        self._batch = batch_size
        self._lock = threading.Lock()
        self._session = None
        self._tokenizer = None

    def _load(self) -> None:
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            for name, sha in MODEL_FILES.items():
                p = self._dir / name
                if not p.is_file() or _sha256(p) != sha:
                    raise EmbeddingUnavailable(f"model file {name} is missing or does not match the pinned revision")
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer
            except ImportError as exc:                          # pragma: no cover - dependency missing
                raise EmbeddingUnavailable("embedding runtime is not installed") from exc
            tok = Tokenizer.from_file(str(self._dir / "tokenizer.json"))
            tok.enable_truncation(max_length=MAX_TOKENS)
            tok.enable_padding(pad_id=0, pad_token="[PAD]")
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = self._threads
            opts.inter_op_num_threads = 1
            opts.log_severity_level = 3
            self._session = ort.InferenceSession(str(self._dir / "model.onnx"), sess_options=opts,
                                                 providers=["CPUExecutionProvider"])
            self._inputs = {i.name for i in self._session.get_inputs()}
            self._tokenizer = tok

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        import numpy as np
        self._load()
        out: List[List[float]] = []
        for start in range(0, len(texts), self._batch):
            batch = [" ".join((t or "").split()) or "." for t in texts[start:start + self._batch]]
            enc = self._tokenizer.encode_batch(batch)
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            try:
                hidden = self._session.run(None, feed)[0]                       # (batch, tokens, 384)
            except Exception as exc:                                            # never echo the text
                raise EmbeddingUnavailable(f"embedding failed: {type(exc).__name__}") from None
            m = mask[..., None].astype(np.float32)
            pooled = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
            pooled /= np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
            out.extend(pooled.astype(np.float32).tolist())
        return out


_PROVIDER: Optional[GteSmallOnnx] = None


def provider_from(settings) -> Optional[EmbeddingProvider]:
    """The configured provider, or None when embeddings are switched off (TRACE_EMBEDDINGS=0, the default)."""
    global _PROVIDER
    if not settings.trace_embeddings:
        return None
    if _PROVIDER is None or _PROVIDER._dir != Path(settings.trace_embedding_model_dir):
        _PROVIDER = GteSmallOnnx(settings.trace_embedding_model_dir, threads=settings.trace_embedding_threads)
    return _PROVIDER
