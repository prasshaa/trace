"""Target companies (POC iteration): the companies a user would like to work at, and posting-specific employer research.

An ASPIRATION, never a constraint or evidence: it never changes the current position, the capability states, the route
decision or stages 1-3, never creates a plan run, and is never stored as employment. It only chooses which employers'
posting-specific assessments (assessment 2, evaluated now from the confirmed profile) the report shows.

There is no company directory in Trace. Search covers only the employers Trace holds job-posting research for, and says
so; any other name can be added as the user's own, unverified choice.
"""
import re
from typing import Dict, List, Optional

from app.services.candidate_profile import OwnerContext, owner_session
from app.services.candidate_profile.repository import InvalidReference
from app.services.transition.repository import TransitionRepository
from app.services.transition.service import TransitionPlanService

MAX_COMPANIES = 20
MAX_NAME = 100
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
DIRECTORY_NOTE = ("Trace has job-posting research for a small number of companies only; this is not a list of all companies. "
                  "You can add any company as your own choice.")
USE_ORDER = {"current_opportunity": 0, "requires_revalidation": 1, "historical_eligibility_evidence": 2}


def normalise(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip())


def company_key(name: str) -> str:
    return normalise(name).lower()


def validate(names: List[str]) -> List[str]:
    if len(names) > MAX_COMPANIES:
        raise InvalidReference(f"choose at most {MAX_COMPANIES} companies for a target role")
    out = []
    for n in names:
        v = normalise(n)
        if not v or len(v) > MAX_NAME or CONTROL.search(v):
            raise InvalidReference(f"each company name must be 1 to {MAX_NAME} characters")
        out.append(v)
    return out


class TargetCompanyService:
    def __init__(self, conn):
        self._conn = conn
        self._plans = TransitionPlanService(conn)

    def _check_target(self, ctx: OwnerContext, target: str) -> None:
        with owner_session(self._conn, ctx.owner_user_id):
            if TransitionRepository(self._conn).target(target) is None:
                raise InvalidReference("unknown target role")

    def _researched(self, ctx: OwnerContext) -> Dict[str, dict]:
        return {company_key(e["company_name"]): e for e in self._plans.researched_employers(ctx)}

    def _rows(self, sql: str, params: tuple) -> List[dict]:
        cur = self._conn.execute(sql, params)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def selected(self, ctx: OwnerContext, target: str) -> List[dict]:
        self._check_target(ctx, target)
        with owner_session(self._conn, ctx.owner_user_id):
            rows = self._rows("select company_name, company_key, position from trace_plan.my_target_companies(%s)", (target,))
        return self._label(ctx, rows)

    def replace(self, ctx: OwnerContext, target: str, names: List[str]) -> List[dict]:
        names = validate(names)
        self._check_target(ctx, target)
        with owner_session(self._conn, ctx.owner_user_id):
            rows = self._rows("select company_name, company_key, position from trace_plan.set_target_companies(%s, %s)", (target, names))
        return self._label(ctx, rows)

    def _label(self, ctx: OwnerContext, rows: List[dict]) -> List[dict]:
        researched = self._researched(ctx)
        return [{"name": r["company_name"], "position": r["position"],
                 "source": "researched_employer" if r["company_key"] in researched else "user_provided"} for r in rows]

    def search(self, ctx: OwnerContext, target: str, q: str) -> dict:
        self._check_target(ctx, target)
        key = company_key(q)
        results = [{"name": e["company_name"], "has_posting_for_target": target in e["role_ids"]}
                   for k, e in self._researched(ctx).items() if not key or key in k]
        return {"query": normalise(q), "results": results,
                "exact_match": any(company_key(r["name"]) == key for r in results), "note": DIRECTORY_NOTE}

    def research(self, ctx: OwnerContext, sequencing, target: str) -> dict:
        """Posting-specific assessments for the user's selected companies only. Each posting is its own assessment;
        nothing is merged into a company-wide result. No research is never evidence about the employer."""
        chosen = self.selected(ctx, target)
        postings = self._plans.employer_postings(ctx, sequencing, target) if chosen else []
        by_company: Dict[str, List[dict]] = {}
        for p in postings:
            by_company.setdefault(company_key(p["company_name"]), []).append(p)
        companies, openings = [], []
        for c in chosen:
            mine = sorted(by_company.get(company_key(c["name"]), []), key=lambda p: (USE_ORDER[p["evidence_use"]], p["source_key"]))
            status = "assessed" if mine else "not_assessed"
            reason: Optional[str] = None
            if not mine:
                reason = "no_relevant_posting" if c["source"] == "researched_employer" else "not_researched"
            companies.append({**c, "status": status, "not_assessed_reason": reason, "postings": mine})
            openings += [{"company_name": p["company_name"], "role_title": p["role_title"], "location": p["location"],
                          "outcome": p["eligibility"]["outcome"], "last_verified_at": p["last_verified_at"], "source_key": p["source_key"]}
                         for p in mine if p["is_current_opportunity"]]
        return {"target_role_id": target, "companies": companies, "current_openings": openings,
                "note": "Each posting is assessed on its own stated criteria against your confirmed profile. This never changes "
                        "your routes, your capability or your plan."}
