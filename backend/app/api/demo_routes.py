"""/api/v1, Phase 5: choose an approved synthetic sample resume (local demo only).

GET /api/v1/demo/samples                     the approved synthetic samples: number, latest title, short summary
GET /api/v1/demo/samples/{number}/resume.pdf the sample's PDF bytes, for the browser to upload through POST /resumes

Only samples that are BOTH in the fixture set AND in the database allowlist (trace_ingest.approved_fixture_resumes,
same (fixture id, hash) pair the upload re-checks) are listed or served. Both routes need a verified bearer token,
TRACE_DEMO_FIXTURES=1 and a demonstrably local database, exactly like the upload; otherwise 403. Serving a PDF does
not ingest anything: the upload path (hash, allowlist, consent, never-merge) is unchanged and still decides.
"""
import hashlib
from typing import List
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Path
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from app.api.deps import current_owner, db_connection
from app.api.ratelimit import limit
from app.api.routes import fixture_extractor
from app.config import Settings, get_settings
from app.demo.fixtures import DemoFixturesDisabled, approved_pair, database_is_local
from app.services.candidate_profile.repository import RecordNotFound
from app.services.resume_extraction import FixtureExtractor

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])


class SampleView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: int
    latest_title: str
    summary: str
    roles: int
    notice: str = "Synthetic sample: a fictional person and career."


def _approved(conn: psycopg.Connection, settings: Settings, extractor: FixtureExtractor) -> List[str]:
    if not settings.trace_demo_fixtures or settings.trace_data_mode != "synthetic_demo":
        raise DemoFixturesDisabled("synthetic sample profiles are disabled (TRACE_DEMO_FIXTURES is not enabled)")
    if not database_is_local(conn):
        raise DemoFixturesDisabled("synthetic sample profiles are only available against a local database")
    # The same database allowlist check the upload uses (approved_pair), per fixture: no new privileged SQL here.
    return [f for f in extractor.fixture_ids
            if f.startswith("SYN-") and approved_pair(conn, f, hashlib.sha256(extractor.resume_bytes(f)).hexdigest())]


def _number(fixture_id: str) -> int:
    return int(fixture_id.split("-", 1)[1])


def _latest_title(fx: dict) -> str:
    jobs = sorted(fx.get("employments", []), key=lambda e: e.get("sequence_index") or 0)
    current = [e for e in jobs if e.get("is_current_as_stated")] or jobs
    return (current[-1].get("title_raw_redacted") or "Title not stated") if current else "No employment recorded"


@router.get("/samples", response_model=List[SampleView], dependencies=[Depends(limit("read"))])
def samples(owner: UUID = Depends(current_owner), conn: psycopg.Connection = Depends(db_connection),
            settings: Settings = Depends(get_settings), extractor: FixtureExtractor = Depends(fixture_extractor)) -> List[SampleView]:
    out = []
    for f in _approved(conn, settings, extractor):
        fx = extractor.fixture(f)
        out.append(SampleView(number=_number(f), latest_title=_latest_title(fx), summary=fx.get("profile_summary") or "",
                              roles=len(fx.get("employments", []))))
    return out


@router.get("/samples/{number}/resume.pdf", dependencies=[Depends(limit("read"))],
            responses={200: {"content": {"application/pdf": {}}}})
def sample_pdf(number: int = Path(ge=1, le=9999), owner: UUID = Depends(current_owner), conn: psycopg.Connection = Depends(db_connection),
               settings: Settings = Depends(get_settings), extractor: FixtureExtractor = Depends(fixture_extractor)) -> Response:
    match = [f for f in _approved(conn, settings, extractor) if _number(f) == number]
    if not match:
        raise RecordNotFound("no approved synthetic sample with that number")
    return Response(content=extractor.resume_bytes(match[0]), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="trace-synthetic-sample-{number:03d}.pdf"',
                             "Cache-Control": "no-store"})
