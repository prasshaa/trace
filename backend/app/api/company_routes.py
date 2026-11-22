"""Target companies and company-specific research (POC iteration after product Phase 5).

    GET  /api/v1/target-companies?target=           the caller's chosen companies for a target role, in their order
    PUT  /api/v1/target-companies?target=           replace that list (add, remove, reorder; [] skips or clears)
    GET  /api/v1/target-companies/search?target=&q= employers Trace holds job-posting research for (not a directory)
    GET  /api/v1/company-research?target=           posting-specific assessments for the chosen companies only

Company choices are aspirations: they never change the current position, capability, routes or stages 1-3, and never
create a transition-plan run. Owner-scoped (trace_plan functions acting for the request identity).
"""
from typing import Any, Dict, List, Literal, Optional

import psycopg
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import db_connection, owner_context
from app.api.pathway_routes import sequencing_context
from app.api.ratelimit import limit
from app.api.transition_routes import TARGET
from app.services.candidate_profile import OwnerContext
from app.services.career_history import SequencingContext
from app.services.target_companies import MAX_COMPANIES, TargetCompanyService

router = APIRouter(prefix="/api/v1", tags=["api"])


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompanyChoice(_View):
    name: str
    position: int
    source: Literal["researched_employer", "user_provided"]    # user_provided: the user's own, unverified choice


class CompanyChoices(_View):
    target_role_id: str
    companies: List[CompanyChoice]
    note: str = ("Optional. Companies you choose only decide which employer research your report shows; they never change "
                 "your plan.")


class CompanyChoiceUpdate(_View):
    companies: List[str] = Field(max_length=MAX_COMPANIES)


class SearchResult(_View):
    name: str
    has_posting_for_target: bool


class CompanySearch(_View):
    query: str
    results: List[SearchResult]
    exact_match: bool
    note: str


class ResearchPosting(_View):
    """One posting, assessed on its own stated criteria (required decide; preferred reported). Never merged."""
    source_key: str
    company_name: str
    role_title: str
    location: Optional[str] = None
    url: Optional[str] = None
    attribution: Optional[str] = None
    last_verified_at: Optional[str] = None
    effective_status: str
    is_current_opportunity: bool
    evidence_use: Literal["current_opportunity", "requires_revalidation", "historical_eligibility_evidence"]
    entry_background_stance: Optional[str] = None
    entry_quote: Optional[str] = None
    entry_locator: Optional[str] = None
    accepted_backgrounds: List[str]
    excluded_backgrounds: List[str]
    eligibility: Dict[str, Any]


class ResearchCompany(CompanyChoice):
    status: Literal["assessed", "not_assessed"]
    not_assessed_reason: Optional[Literal["no_relevant_posting", "not_researched"]] = None
    postings: List[ResearchPosting]


class Opening(_View):
    company_name: str
    role_title: str
    location: Optional[str] = None
    outcome: Literal["meets", "does_not_meet", "unclear"]
    last_verified_at: Optional[str] = None
    source_key: str


class CompanyResearch(_View):
    target_role_id: str
    companies: List[ResearchCompany]
    current_openings: List[Opening]
    note: str


def _service(conn: psycopg.Connection = Depends(db_connection)) -> TargetCompanyService:
    return TargetCompanyService(conn)


@router.get("/target-companies", response_model=CompanyChoices, dependencies=[Depends(limit("read"))])
def get_companies(response: Response, target: str = TARGET, ctx: OwnerContext = Depends(owner_context),
                  service: TargetCompanyService = Depends(_service)) -> CompanyChoices:
    response.headers["Cache-Control"] = "no-store"
    return CompanyChoices(target_role_id=target, companies=service.selected(ctx, target))


@router.put("/target-companies", response_model=CompanyChoices, dependencies=[Depends(limit("mutation"))])
def put_companies(payload: CompanyChoiceUpdate, response: Response, target: str = TARGET,
                  ctx: OwnerContext = Depends(owner_context), service: TargetCompanyService = Depends(_service)) -> CompanyChoices:
    response.headers["Cache-Control"] = "no-store"
    return CompanyChoices(target_role_id=target, companies=service.replace(ctx, target, payload.companies))


@router.get("/target-companies/search", response_model=CompanySearch, dependencies=[Depends(limit("read"))])
def search_companies(response: Response, target: str = TARGET, q: str = Query("", max_length=100),
                     ctx: OwnerContext = Depends(owner_context), service: TargetCompanyService = Depends(_service)) -> CompanySearch:
    response.headers["Cache-Control"] = "no-store"
    return CompanySearch(**service.search(ctx, target, q))


@router.get("/company-research", response_model=CompanyResearch, dependencies=[Depends(limit("read"))])
def company_research(response: Response, target: str = TARGET, ctx: OwnerContext = Depends(owner_context),
                     service: TargetCompanyService = Depends(_service),
                     sequencing: SequencingContext = Depends(sequencing_context)) -> CompanyResearch:
    response.headers["Cache-Control"] = "no-store"
    return CompanyResearch(**service.research(ctx, sequencing, target))


# --------------------------------------------------------------------------- where the user wants to move (Phase 5, D51)
from app.services.move_preferences import LIMITS, MovePreferenceService, options as type_options  # noqa: E402


class CompanyTypeOption(_View):
    key: str
    label: str
    changes_plan: bool          # true only where Trace's role sources define requirements for this kind of company
    note: str


class MovePreference(_View):
    target_role_id: str
    scope: Optional[Literal["specific_companies", "company_types", "open"]] = None    # None: not answered yet
    company_types: List[str] = []
    companies: List[CompanyChoice] = []
    options: List[CompanyTypeOption]
    limits: str = LIMITS


class MovePreferenceUpdate(_View):
    scope: Literal["specific_companies", "company_types", "open"]
    company_types: List[str] = Field(default_factory=list, max_length=3)
    companies: Optional[List[str]] = Field(default=None, max_length=MAX_COMPANIES)


def _move_service(conn: psycopg.Connection = Depends(db_connection)) -> MovePreferenceService:
    return MovePreferenceService(conn)


def _move_view(target: str, pref: Optional[dict], companies: List[dict]) -> MovePreference:
    scope = pref["scope"] if pref else None
    return MovePreference(target_role_id=target, scope=scope, company_types=pref["company_types"] if pref else [],
                          companies=companies if scope == "specific_companies" else [],
                          options=[CompanyTypeOption(**o) for o in type_options()])


@router.get("/move-preference", response_model=MovePreference, dependencies=[Depends(limit("read"))])
def get_move_preference(response: Response, target: str = TARGET, ctx: OwnerContext = Depends(owner_context),
                        service: MovePreferenceService = Depends(_move_service),
                        companies: TargetCompanyService = Depends(_service)) -> MovePreference:
    """The caller's answer to "Where would you like to make this move?" for one target role. A planning preference."""
    response.headers["Cache-Control"] = "no-store"
    return _move_view(target, service.get(ctx, target), companies.selected(ctx, target))


@router.put("/move-preference", response_model=MovePreference, dependencies=[Depends(limit("mutation"))])
def put_move_preference(payload: MovePreferenceUpdate, response: Response, target: str = TARGET,
                        ctx: OwnerContext = Depends(owner_context), service: MovePreferenceService = Depends(_move_service),
                        companies: TargetCompanyService = Depends(_service)) -> MovePreference:
    """Save the scope (and kinds of company); with scope specific_companies, `companies` replaces the named list."""
    response.headers["Cache-Control"] = "no-store"
    if payload.scope == "specific_companies" and payload.companies is not None and not [c for c in payload.companies if c.strip()]:
        from app.services.candidate_profile.repository import InvalidReference
        raise InvalidReference("add at least one company, or choose another option")
    pref = service.set(ctx, target, payload.scope, payload.company_types)
    named = (companies.replace(ctx, target, payload.companies) if payload.scope == "specific_companies" and payload.companies is not None
             else companies.selected(ctx, target))
    return _move_view(target, pref, named)
