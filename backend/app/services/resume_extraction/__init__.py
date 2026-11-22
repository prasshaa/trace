"""Resume extraction (M3): a provider-agnostic extraction contract and a deterministic extractor for
Trace's synthetic fixture resumes. No LLM, no external service, no real candidate data. Ingestion of
a result into a candidate profile is ``CandidateProfileService.ingest_extraction``."""
from app.services.resume_extraction.contract import (
    CONTRACT_VERSION, ExtractedActivity, ExtractedCareerBreak, ExtractedEducation, ExtractedEmployer,
    ExtractedEmployment, ExtractedEvidence, ExtractedSkill, ExtractionResult,
)
from app.services.resume_extraction.fixture import EXTRACTOR_NAME, EXTRACTOR_VERSION, FixtureExtractor, UnknownResume

__all__ = [
    "CONTRACT_VERSION", "EXTRACTOR_NAME", "EXTRACTOR_VERSION", "ExtractedActivity", "ExtractedCareerBreak",
    "ExtractedEducation", "ExtractedEmployer", "ExtractedEmployment", "ExtractedEvidence", "ExtractedSkill",
    "ExtractionResult", "FixtureExtractor", "UnknownResume",
]
