"""Where the user wants to make their move (Phase 5, D51): specific companies, kinds of company, or open to options.

A PLANNING PREFERENCE, never evidence about an employer. Trace holds no company directory, no hiring-demand data and no
employer-specific transition research, so a preference never ranks routes, never changes capability states for a named
company and is never presented as a recommendation of where to apply.

What it does do, and only where Trace has sourced information: a kind of company that the curated role knowledge pack
describes as a product CONTEXT (for example AI products, platforms and APIs, regulated domains) adds that context's
requirements to the plan (`contexts_for`). Kinds with no sourced context (stage or size of company) change nothing in the
plan, and the plan says so. Named companies keep using the v2.29.0 list (`TargetCompanyService`): employers Trace holds
dated posting research for are assessed on their own postings; any other name is the user's own, unverified choice.
"""
from typing import Dict, List, Optional

from app.services.candidate_profile import OwnerContext, owner_session
from app.services.candidate_profile.repository import InvalidReference
from app.services.transition.repository import TransitionRepository

SCOPES = ("specific_companies", "company_types", "open")
MAX_TYPES = 3
# key: (label, product contexts the curated pack defines for this kind of company, what Trace can say about it)
COMPANY_TYPES: Dict[str, dict] = {
    "ai_native": {"label": "AI-native or AI-first company", "contexts": ["ai_product"],
                  "note": "Trace's role sources include requirements for AI products, so your plan includes them."},
    "b2b_saas": {"label": "B2B SaaS or enterprise software", "contexts": ["commercial_product"],
                 "note": "Trace's role sources include requirements for commercial products (go-to-market, business case), so "
                         "your plan includes them."},
    "platform_data": {"label": "Platform, API or data-product company", "contexts": ["platform_api", "data_product"],
                      "note": "Trace's role sources include requirements for platform, API and data products, so your plan "
                              "includes them."},
    "fintech_regulated": {"label": "Fintech or another regulated domain", "contexts": ["regulated_domain"],
                          "note": "Trace's role sources include domain and regulatory knowledge for regulated products, so "
                                  "your plan includes it."},
    "consumer_marketplace": {"label": "Consumer app or marketplace", "contexts": [],
                             "note": "Trace doesn't yet have sourced requirements specific to consumer products, so this "
                                     "doesn't change your plan."},
    "early_stage_startup": {"label": "Early-stage startup", "contexts": [],
                            "note": "Trace doesn't have sourced information on how the role differs by company stage, so "
                                    "this doesn't change your plan."},
    "scale_up": {"label": "Growth-stage startup or scale-up", "contexts": [],
                 "note": "Trace doesn't have sourced information on how the role differs by company stage, so this "
                         "doesn't change your plan."},
    "large_tech": {"label": "Large, established technology company", "contexts": [],
                   "note": "Trace doesn't have sourced information on how the role differs by company size, so this "
                           "doesn't change your plan."},
    "services_consulting": {"label": "IT services or consulting firm", "contexts": [],
                            "note": "Trace doesn't have sourced information on product roles in services firms, so this "
                                    "doesn't change your plan."},
}
LIMITS = ("Trace doesn't track job openings, hiring demand or how people moved into particular companies. It never ranks "
          "companies or tells you where to apply.")


def contexts_for(preference: Optional[dict]) -> List[str]:
    """The product contexts a saved preference adds to a plan: only from kinds of company with sourced requirements."""
    if not preference or preference.get("scope") != "company_types":
        return []
    return sorted({c for t in preference.get("company_types") or [] for c in COMPANY_TYPES.get(t, {}).get("contexts", [])})


def options() -> List[dict]:
    return [{"key": k, "label": v["label"], "changes_plan": bool(v["contexts"]), "note": v["note"]} for k, v in COMPANY_TYPES.items()]


class MovePreferenceService:
    def __init__(self, conn):
        self._conn = conn

    def _check_target(self, target: str) -> None:
        if TransitionRepository(self._conn).target(target) is None:
            raise InvalidReference("unknown target role")

    def get(self, ctx: OwnerContext, target: str) -> Optional[dict]:
        with owner_session(self._conn, ctx.owner_user_id):
            self._check_target(target)
            row = self._conn.execute("select scope, company_types, updated_at from trace_plan.my_move_preference(%s)",
                                     (target,)).fetchone()
        if row is None:
            return None
        return {"scope": row[0], "company_types": list(row[1] or []), "updated_at": row[2]}

    def set(self, ctx: OwnerContext, target: str, scope: str, company_types: List[str]) -> dict:
        if scope not in SCOPES:
            raise InvalidReference("choose specific companies, kinds of company, or open to options")
        types = list(dict.fromkeys(company_types or [])) if scope == "company_types" else []
        if scope == "company_types" and not types:
            raise InvalidReference("choose at least one kind of company")
        if len(types) > MAX_TYPES:
            raise InvalidReference(f"choose up to {MAX_TYPES} kinds of company")
        if set(types) - set(COMPANY_TYPES):
            raise InvalidReference("unknown kind of company")
        with owner_session(self._conn, ctx.owner_user_id):
            self._check_target(target)
            row = self._conn.execute("select scope, company_types, updated_at from trace_plan.set_move_preference(%s, %s, %s)",
                                     (target, scope, types)).fetchone()
        return {"scope": row[0], "company_types": list(row[1] or []), "updated_at": row[2]}
