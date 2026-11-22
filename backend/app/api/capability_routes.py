"""/api/v1, product Phase 3: target-role capability (assessment 1) and employer posting metadata (for assessment 2).

Two separate endpoints by design (D25): employer criteria and entry-background stances never appear in, or change,
the capability assessment. Both are read-only, deterministic and use no external AI. Responses are never cached.
"""
from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict

import psycopg

from app.api.deps import db_connection, owner_context
from app.api.ratelimit import limit
from app.services.candidate_profile import OwnerContext
from app.services.capability import CapabilityService

router = APIRouter(prefix="/api/v1", tags=["api"])
Context = Literal["commercial_product", "growth_analytics", "platform_api", "data_product", "regulated_domain", "ai_product"]


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRef(_View):
    key: str
    kind: str
    publisher: str
    title: str
    url: str
    attribution: str
    stance: Literal["supports", "qualifies", "disagrees", "silent"]
    note: Optional[str] = None
    status: str                                   # D26 effective status (a closed posting reads "expired")
    last_verified_at: datetime


class SupportView(_View):
    kind: Literal["evidence", "skill", "employment"]
    record_id: str                                # the caller's own record
    matched: List[str]
    excerpt: Optional[str] = None
    basis: Optional[str] = None                   # how the requirement's state was reached (pm-v1.2)
    read_by: Literal["you", "trace"] = "you"      # Phase 5 (D50): the user's categorisation or Trace's reading of the line
    cue: Optional[str] = None                     # Trace's reading: the words of the line it rests on
    why: Optional[str] = None                     # Trace's reading: what the line shows


class RequirementView(_View):
    key: str
    title: str
    description: str
    requirement_class: Literal["core", "context_dependent", "differentiator"]
    applies_to_level: str
    level_note: Optional[str] = None
    contexts: List[str]
    state: Literal["demonstrated", "transferable", "gap", "unknown", "not_applicable"]
    basis: Optional[str] = None
    reason: Optional[str] = None
    transfer_rationale: Optional[str] = None
    blocking_records: List[str] = []              # unknown: the caller's records to review or classify first
    area_conditions: Dict[str, str] = {}          # area -> scope question that must be answered YES
    decision_rights: bool = False                 # D31: a decision-rights requirement (C2, C4, L1)
    supporting: List[SupportView]
    sources: List[SourceRef]
    # Phase 5 (D50): evidence_found | needs_context | not_yet_demonstrated | cannot_assess_yet | not_applicable
    display_state: Optional[str] = None
    needs_context: Optional[Dict[str, Any]] = None


class CoverageView(_View):
    reviewed_evidence: int
    pending_evidence: int
    unclassified_reviewed_evidence: int
    complete: bool
    reason: Optional[str] = None
    blocking_records: List[str] = []


class EducationView(_View):
    title: Optional[str]
    stored_level: str
    level: str                                    # read-time interpretation (D11): stored 'mba' reads as 'master'
    qualification_type: Optional[str]
    field_of_study: Optional[str]


class CapabilityAssessment(_View):
    assessment: Literal["target_role_capability"] = "target_role_capability"
    target_role_id: str
    target_level: Literal["pm", "senior_pm"]
    knowledge_pack: str
    source_inventory: str
    taxonomy_version: int
    pack_note: str
    contexts: List[str]
    separation_note: str
    coverage: CoverageView
    requirements: List[RequirementView]
    education: List[EducationView]


class PostingView(_View):
    source_key: str
    company_name: str
    role_title: str
    role_id: Optional[str]
    location: Optional[str]
    source_url: str
    attribution: str
    licence: str
    published_at: Optional[date]
    last_verified_at: datetime                    # shown wherever a current opportunity is shown (D26)
    revalidate_by: Optional[datetime]
    source_status: str
    effective_status: str
    status_note: Optional[str]
    is_current_opportunity: bool
    evidence_use: Literal["current_opportunity", "requires_revalidation", "historical_eligibility_evidence"]
    context_tags: List[str]
    entry_background_stance: Literal["accepts_adjacent", "requires_target_role_experience", "excludes_specified_transitions", "not_specified"]
    accepted_backgrounds: List[str]
    excluded_backgrounds: List[str]
    entry_quote: Optional[str]
    entry_locator: Optional[str]
    criteria: List[Dict[str, Any]]


class PostingsView(_View):
    assessment: Literal["employer_application_eligibility_metadata"] = "employer_application_eligibility_metadata"
    note: str = ("Each item is one employer's statement in one posting at one time. It is not a general requirement for the "
                 "role and never a capability gap. Only postings verified within the last 14 days are current opportunities.")
    postings: List[PostingView]


def _service(conn: psycopg.Connection = Depends(db_connection)) -> CapabilityService:
    return CapabilityService(conn)            # the same per-request connection as the owner context (FastAPI caches it)


@router.get("/capability-assessment", response_model=CapabilityAssessment, dependencies=[Depends(limit("read"))])
def capability_assessment(response: Response, target: str = Query(min_length=1, max_length=40, pattern=r"^[A-Z0-9-]+$"),
                          context: List[Context] = Query(default=[]), ctx: OwnerContext = Depends(owner_context),
                          service: CapabilityService = Depends(_service)) -> CapabilityAssessment:
    response.headers["Cache-Control"] = "no-store"
    return CapabilityAssessment(**service.capability_assessment(ctx, target, context))


@router.get("/knowledge/postings", response_model=PostingsView, dependencies=[Depends(limit("read"))])
def postings(response: Response, ctx: OwnerContext = Depends(owner_context),
             service: CapabilityService = Depends(_service)) -> PostingsView:
    response.headers["Cache-Control"] = "no-store"
    return PostingsView(postings=[PostingView(**p) for p in service.postings(ctx)])
