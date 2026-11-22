"""Unit tests for the extraction contract and the deterministic fixture extractor (M3). No database."""
import hashlib
import json
import re
import zipfile
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.services.resume_extraction import (
    ExtractedEmployment, ExtractedEvidence, ExtractedSkill, ExtractionResult, FixtureExtractor, UnknownResume,
)
from tests.career_history_support import FOUNDATION, requires_foundation

SYNTHETIC = FOUNDATION / "data" / "synthetic"
MIGRATION = FOUNDATION / "supabase/migrations/20261111160000_trace_fixture_extraction_ingestion.sql"
pytestmark = requires_foundation

# Fields that are mapping, review, verification or fixture curation, never extraction.
NOT_EXTRACTION = {"role_id", "seniority_code", "mapping_status", "mapping_confidence", "mapping_note", "skill_id",
                  "responsibility_area", "category_code", "category_mapping_status", "category_mapping_confidence",
                  "review_status", "information_origin", "verification_status", "provenance", "evidence_status",
                  "evidence_source", "attribute_provenance", "confidence", "verified", "participant_confirmed",
                  "entry_source", "data_quality_flags", "owner_user_id", "candidate_profile_id", "source_resume_id",
                  "extraction_run_id"}


@pytest.fixture(scope="module")
def extractor():
    return FixtureExtractor(SYNTHETIC / "synthetic_profiles.jsonl", SYNTHETIC / "synthetic_resume_pdfs.zip")


def _keys(obj):
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys(v)}
    return set()


# --------------------------------------------------------------------------- the fixture extractor
def test_each_of_the_41_pdfs_maps_to_exactly_its_own_fixture(extractor):
    with zipfile.ZipFile(SYNTHETIC / "synthetic_resume_pdfs.zip") as z:
        names = sorted(z.namelist())
        hashes = {n: hashlib.sha256(z.read(n)).hexdigest() for n in names}
    assert len(names) == len(set(hashes.values())) == len(extractor.fixture_ids) == 41
    for fid in extractor.fixture_ids:
        pdf = extractor.resume_bytes(fid)
        assert hashlib.sha256(pdf).hexdigest() == hashes[f"{fid}_synthetic_resume.pdf"]
        assert extractor.identify(pdf) == fid
        result = extractor.extract(pdf)
        assert (result.fixture_profile_id, result.source_sha256) == (fid, hashlib.sha256(pdf).hexdigest())


def test_extraction_is_deterministic_and_unknown_documents_are_refused(extractor):
    pdf = extractor.resume_bytes("SYN-010")
    a, b = extractor.extract(pdf), extractor.extract(pdf)
    assert json.dumps(a.ingestion_payload(), sort_keys=True) == json.dumps(b.ingestion_payload(), sort_keys=True)
    with pytest.raises(UnknownResume):
        extractor.extract(pdf + b" ")                       # one byte different: not a fixture
    with pytest.raises(UnknownResume):
        extractor.extract(b"%PDF-1.4 a real resume would go here")


def test_extraction_keeps_values_as_stated_and_carries_no_mapping_or_trust_fields(extractor):
    for fid in extractor.fixture_ids:
        fx, result = extractor.fixture(fid), extractor.extract(extractor.resume_bytes(fid))
        payload = result.ingestion_payload()
        assert not _keys(payload) & NOT_EXTRACTION, fid
        assert [e.title_raw for e in result.employments] == [e["title_raw_redacted"] for e in sorted(fx["employments"], key=lambda e: e["sequence_index"])]
        assert sorted(x.evidence_text for x in result.evidence) == sorted(x["text"] for x in fx["evidence"])
        assert [s.skill_text_raw for s in result.skills] == [s["skill_text_raw"] for s in fx["skills"]]
        assert all(r.extraction_confidence == Decimal("1.000") for r in result.employments + result.evidence + result.skills)
        for e, src in zip(result.employments, sorted(fx["employments"], key=lambda e: e["sequence_index"])):
            if src["start_precision"] == "unknown":
                assert (e.start.year, e.start.month, e.start.precision) == (None, None, "unknown")   # unknown stays unknown


def test_payload_fields_match_the_database_allow_lists(extractor):
    """Drift guard: every field the payload sends is in the ingestion function's allow-list, and every
    allow-listed field can be sent."""
    sql = MIGRATION.read_text(encoding="utf-8")
    allowed = {kind: set(re.findall(r"'([a-z_]+)'", body)) for body, kind in
               re.findall(r"assert_allowed_keys\(p_result -> '\w+',\s*array\[(.*?)\], '(\w+)'\)", sql, flags=re.S)}
    assert set(allowed) == {"employers", "employments", "education", "career_activities", "career_breaks", "evidence", "skills"}
    sent = {}
    for fid in extractor.fixture_ids:
        for kind, rows in extractor.extract(extractor.resume_bytes(fid)).ingestion_payload().items():
            if isinstance(rows, list):
                for row in rows:
                    sent.setdefault(kind, set()).update(row)
    for kind, fields in sent.items():
        assert fields <= allowed[kind], (kind, fields - allowed[kind])
    top = set(re.search(r"k <> all \(array\[(.*?)\]\)\) then\s+raise exception using errcode = 'TR006', message = 'the extraction result",
                        sql, flags=re.S).group(1).replace("'", "").replace(" ", "").replace("\n", "").split(","))
    assert set(extractor.extract(extractor.resume_bytes("SYN-001")).ingestion_payload()) <= top


# --------------------------------------------------------------------------- the contract
def _result(extractor, **update):
    base = extractor.extract(extractor.resume_bytes("SYN-001")).model_dump()
    base.update(update)
    return base


def test_contract_forbids_fields_it_does_not_define(extractor):
    for level, forged in (("employments", {"review_status": "user_confirmed"}), ("employments", {"role_id": "PM-PM"}),
                          ("skills", {"skill_id": "SKL-SQL"}), ("evidence", {"responsibility_area": "roadmap_ownership"}),
                          ("employers", {"attribute_provenance": "participant_confirmed"}), (None, {"verification_status": "x"})):
        data = _result(extractor)
        (data[level][0] if level else data).update(forged)
        with pytest.raises(ValidationError):
            ExtractionResult.model_validate(data)


def test_contract_checks_references_keys_and_values(extractor):
    data = _result(extractor)
    data["employments"][0]["employer_key"] = "NOT-AN-EMPLOYER"
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(data)
    data = _result(extractor)
    data["employments"][1]["key"] = data["employments"][0]["key"]
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(data)
    data = _result(extractor)
    data["employments"][1]["sequence_index"] = data["employments"][0]["sequence_index"]
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(data)
    loc = {"source_location": "Experience, entry 1", "extraction_confidence": Decimal("0.5")}
    with pytest.raises(ValidationError):
        ExtractedEmployment(key="k", sequence_index=1, title_raw="PM", start={"precision": "ongoing"}, **loc)
    with pytest.raises(ValidationError):
        ExtractedEmployment(key="k", sequence_index=1, title_raw="PM", source_location="  ", extraction_confidence=Decimal("0.5"))
    with pytest.raises(ValidationError):
        ExtractedEmployment(key="k", sequence_index=1, title_raw="PM", source_location="x", extraction_confidence=Decimal("1.5"))
    with pytest.raises(ValidationError):
        ExtractedEvidence(key="e", evidence_type="achievement", evidence_text="x", employment_key="a", activity_key="b", **loc)
    with pytest.raises(ValidationError):
        ExtractedSkill(skill_text_raw="SQL", employment_key="a", education_key="b", **loc)
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(_result(extractor, source_sha256="not-a-hash"))
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(_result(extractor, fixture_profile_id="REAL-001"))
