"""Embed Trace's shared role knowledge (corpus 'role_knowledge') into trace_ref.knowledge_embeddings.

    DATABASE_URL=<admin> python -m scripts.embed_knowledge

Sources (curated, public product knowledge only): responsibility areas (label, description, examples), the approved
pm-v1.2 capability requirements (title, description) and the pm-actions-v2 action templates (objective, action).
Idempotent per (source, source version, model, model version); a changed text gets a new hash and is replaced. Never
reads candidate data and never writes the research corpus (the table's check constraint refuses it).
"""
import hashlib
import json
import sys
from pathlib import Path

import psycopg

from app.config import get_settings
from app.services.embeddings.provider import GteSmallOnnx

ROOT = Path(__file__).resolve().parents[2]


def items(conn):
    for key, label, desc, ex in conn.execute("select responsibility_area, label, description, examples from public.trace_responsibility_areas"):
        yield "responsibility_area", key, "taxonomy-v2", f"{label}. {desc} {' '.join(ex or [])}".strip()
    for r in json.loads((ROOT / "data-foundation/knowledge_packs/pm-v1.2.json").read_text())["requirements"]:
        yield "capability_requirement", r["key"], "pm-v1.2", f'{r["title"]}. {r["description"]}'
    for key, ver, obj, act in conn.execute("select template_key, template_version, objective, action_text from public.trace_action_templates "
                                           "where template_version = 'pm-actions-v2'"):
        yield "action_template", key, ver, f"{obj or ''} {act or ''}".strip()


def main() -> int:
    s = get_settings()
    p = GteSmallOnnx(s.trace_embedding_model_dir)
    with psycopg.connect(s.database_url.get_secret_value()) as conn:
        rows = list(items(conn))
        vecs = p.embed([r[3] for r in rows])
        with conn.transaction():
            for (kind, key, ver, text), v in zip(rows, vecs):
                conn.execute("""insert into trace_ref.knowledge_embeddings (corpus, source_kind, source_key, source_version, content,
                                  content_sha256, model, model_version, embedding)
                                values ('role_knowledge', %s, %s, %s, %s, %s, %s, %s, %s::real[]::extensions.vector(384))
                                on conflict (corpus, source_kind, source_key, source_version, model, model_version) do update
                                  set content = excluded.content, content_sha256 = excluded.content_sha256, embedding = excluded.embedding""",
                             (kind, key, ver, text, hashlib.sha256(text.encode()).hexdigest(), p.model, p.version, v))
        print(f"role knowledge embedded: {len(rows)} items ({p.model} {p.version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
