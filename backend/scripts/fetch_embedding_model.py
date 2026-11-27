"""Download the pinned gte-small files (public model weights; no user data) and verify their SHA-256.

    python -m scripts.fetch_embedding_model [target_dir]

Idempotent: files that already match are kept. A mismatch is an error and the file is removed. Used locally and in the
Docker build, so the image always holds exactly the pinned revision.
"""
import hashlib
import sys
import urllib.request
from pathlib import Path

from app.services.embeddings.provider import MODEL_FILES, MODEL_ID, REVISION, UPSTREAM


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main(target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True)
    for name, want in MODEL_FILES.items():
        p = target / name
        if p.is_file() and sha(p) == want:
            print(f"{name}: present")
            continue
        url = f"https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{UPSTREAM[name]}"
        urllib.request.urlretrieve(url, p)
        if sha(p) != want:
            p.unlink()
            print(f"{name}: checksum mismatch; removed", file=sys.stderr)
            return 1
        print(f"{name}: downloaded and verified")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / ".models" / "gte-small"))
