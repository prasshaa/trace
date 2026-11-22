"""Resume parser v2 (resume-first onboarding). Local, deterministic, no network, nothing written to disk.

    parse_upload(data) -> Parsed(draft, record, identity)

The document is parsed in a separate, short-lived process with a wall-clock limit (PARSE_TIMEOUT) and, where the
platform allows it, an address-space limit. The bytes go to that process over a pipe and never touch the filesystem;
the process is killed on timeout and exits after one document. A failure returns a reason code and a safe message,
never document content.
"""
from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass
from typing import Dict, List

from app.services.resume_parsing.document import DocumentRejected, load, sniff
from app.services.resume_parsing.parser import DRAFT_SCHEMA, PARSER_VERSION, ResumeDraft, identity_hints, parse_document, to_record

PARSE_TIMEOUT = 20.0
MEMORY_LIMIT = 1024 * 1024 * 1024          # 1 GiB address space for the parsing process (Linux; best effort elsewhere)

__all__ = ["DRAFT_SCHEMA", "PARSER_VERSION", "DocumentRejected", "Parsed", "ResumeDraft", "parse_bytes", "parse_upload", "sniff",
           "to_record"]


@dataclass
class Parsed:
    draft: ResumeDraft
    record: dict
    identity: Dict[str, List[str]]


def parse_bytes(data: bytes) -> Parsed:
    """In-process parse (tests, evaluation). The API uses parse_upload."""
    doc = load(data)
    draft = parse_document(doc)
    return Parsed(draft=draft, record=to_record(draft), identity=identity_hints(doc))


def _child(conn, data: bytes) -> None:                   # runs in the parsing process
    try:
        import resource
        try:
            resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))
        except (ValueError, OSError):
            pass
    except ImportError:
        pass
    try:
        p = parse_bytes(data)
        conn.send(("ok", p.draft.model_dump(), p.record, p.identity))
    except DocumentRejected as exc:
        conn.send(("rejected", exc.code, str(exc)))
    except MemoryError:
        conn.send(("rejected", "too_complex", "This document is too complex to process. Export a simpler version and retry."))
    except Exception:
        conn.send(("rejected", "unreadable", "This file couldn't be read. Export it again as a PDF or .docx and retry."))
    finally:
        conn.close()


def parse_upload(data: bytes, timeout: float = PARSE_TIMEOUT) -> Parsed:
    if sniff(data) is None:
        raise DocumentRejected("unsupported_format", "Upload a PDF or Word (.docx) resume.")
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(child, data), daemon=True)
    proc.start()
    child.close()
    try:
        if not parent.poll(timeout):
            raise DocumentRejected("timeout", "This document took too long to process. Export a simpler version and retry.")
        msg = parent.recv()
    except EOFError:
        raise DocumentRejected("unreadable", "This file couldn't be read. Export it again as a PDF or .docx and retry.") from None
    finally:
        parent.close()
        if proc.is_alive():
            proc.kill()
        proc.join(2)
    if msg[0] == "rejected":
        raise DocumentRejected(msg[1], msg[2])
    _, draft, record, identity = msg
    return Parsed(draft=ResumeDraft.model_validate(draft), record=record, identity=identity)
