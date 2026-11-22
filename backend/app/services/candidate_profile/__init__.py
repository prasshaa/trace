"""Candidate profile service: owner-scoped structured career profile storage (data foundation v2.2.0).

Includes the review guardrails (explicit review actions, draft-only editing, protected system fields,
review-aware sequencing), enforced by the backend (M1) and by the database (M2, data foundation v2.3.0),
corrections with append-only correction history (M4, data foundation v2.5.0), and trusted role mapping
with candidate role decisions (M5, data foundation v2.6.0).
No resume extraction, review UI, recommendations or suitability analysis live here."""
from app.services.candidate_profile.models import (
    CORRECTION_MODELS, SYSTEM_CONTROLLED_FIELDS, ActivityCorrection, ActivityCreate, ActivityUpdate, CandidateProfile,
    CareerBreakCorrection, CareerBreakCreate, CareerBreakUpdate, CorrectionEvent, EducationCorrection, EmploymentCorrection,
    EvidenceCorrection, SkillCorrection, RoleChoice, RoleMappingOptions, RoleMappingResult,
    CurrentPosition, CurrentPositionEvidence,
    DateInput, EducationCreate, EducationUpdate, EmployerCreate, EmployerUpdate, EmploymentCreate, EmploymentUpdate,
    EvidenceCreate, EvidenceUpdate, OwnerContext, ProfileCreate, ProfileUpdate, SkillCreate, SkillUpdate,
)
from app.services.candidate_profile.repository import (
    CandidateProfileError, ClarificationRequired, ClassificationConflict, ConsentRequired, CorrectionHistoryViolation, CorrectionNotAvailable, DeletionBlocked,
    IdentityMismatch, IngestionConflict, MappingConflict,
    IngestionNotAllowed, InvalidRecord, InvalidReference, InvalidTransition,
    PermissionDenied, ConsentConflict, ProfileAlreadyExists, ProfileAmbiguous, OrderConflictsWithDates, ProfileNotFound, RecordChanged, RecordNotEditable, RecordNotFound, load_sequencing_context, owner_session,
)
from app.services.candidate_profile.review import ReviewAction, check_editable, next_review_status
from app.services.candidate_profile.service import (
    CandidateProfileService, IngestionOutcome, assemble_profile, derive_current_position, employment_inputs,
)

__all__ = [
    "ConsentConflict",
    "ProfileAlreadyExists", "ProfileAmbiguous",
    "PathwayResult", "build_pathway_result", "eligible_candidate_evidence",
    "ClassificationConflict",
    "CurrentPosition", "CurrentPositionEvidence", "derive_current_position",
    "ClarificationRequired", "MappingConflict", "RoleChoice", "RoleMappingOptions", "RoleMappingResult",
    "CORRECTION_MODELS", "ActivityCorrection", "CareerBreakCorrection", "CorrectionEvent", "CorrectionHistoryViolation",
    "EducationCorrection", "EmploymentCorrection", "EvidenceCorrection", "SkillCorrection",
    "SYSTEM_CONTROLLED_FIELDS", "ActivityCreate", "ActivityUpdate", "CandidateProfile", "CandidateProfileError",
    "CandidateProfileService", "CareerBreakCreate", "ConsentRequired", "DeletionBlocked", "IdentityMismatch",
    "IngestionConflict", "IngestionNotAllowed", "IngestionOutcome", "CareerBreakUpdate", "CorrectionNotAvailable", "DateInput",
    "EducationCreate", "EducationUpdate", "EmployerCreate", "EmployerUpdate", "EmploymentCreate", "EmploymentUpdate",
    "EvidenceCreate", "EvidenceUpdate", "InvalidRecord", "InvalidReference", "InvalidTransition", "OwnerContext",
    "OrderConflictsWithDates", "PermissionDenied", "ProfileCreate", "ProfileNotFound", "ProfileUpdate", "RecordChanged", "RecordNotEditable", "RecordNotFound",
    "ReviewAction", "SkillCreate", "SkillUpdate", "assemble_profile", "check_editable", "employment_inputs",
    "load_sequencing_context", "next_review_status", "owner_session",
]

from app.services.candidate_profile.pathway import PathwayResult, build_pathway_result, eligible_candidate_evidence  # noqa: E402
