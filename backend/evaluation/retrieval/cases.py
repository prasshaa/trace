"""Shared structure for the retrieval evaluation (Phase 1 of the semantic-retrieval workstream).

SYNTHETIC: every profile, employer, product and number in tuning.py and holdout.py is invented. No real person's
resume, no research profile and no production data is used, and none may be added.

A case is a fictional candidate profile: a short list of experience records as Trace would hold them after import
and review. Each record carries the labels a reviewer gives it:

  clear    responsibility areas the record DEMONSTRATES on its own (the person did the work; action + object; decision
           rights such as roadmap, strategy or outcome ownership only when explicitly owned or decided)
  related  areas the record is relevant to but does NOT demonstrate (participation, assistance, collaboration,
           implied or unclear ownership): the record should be RETRIEVED for these and flagged "needs more context"
  (none)   anything else must not be retrieved and must never count as evidence

Eligibility is structural, not a label: only records whose status is "confirmed" may be retrieved at all. Pending,
rejected and superseded records are present so the pipeline's filter can be tested.

Labels are AUTHOR-PROPOSED (written by the engineer building the retrieval, 2026-11-23) and have NOT yet been reviewed
by an independent person: label_status says so. scripts: `python -m evaluation.retrieval --export-review` writes a CSV
for a human reviewer; reviewed labels replace these and the status changes. Until then every result is reported as
measured against author-proposed labels.
"""
from dataclasses import dataclass, field
from typing import FrozenSet, Tuple

# area keys (taxonomy v2)
PD, DR, RA, PB = "product_discovery", "design_research", "requirements_analysis", "prioritisation_backlog"
RO, SP, OO, DA = "roadmap_ownership", "strategy_portfolio", "outcome_ownership", "data_analysis"
SM, EC, DE, QT = "stakeholder_management", "executive_communication", "delivery_execution", "quality_testing"
OP, TE, CA, GM = "operations_process", "technical_engineering", "customer_account_management", "go_to_market_growth"
BC, PL, AI, DG = "business_case_economics", "people_leadership", "ai_product_work", "data_governance_quality"
DECISION_RIGHTS = frozenset({RO, SP, OO})

ELIGIBLE = "confirmed"


@dataclass(frozen=True)
class Record:
    id: str
    text: str
    clear: FrozenSet[str] = frozenset()
    related: FrozenSet[str] = frozenset()
    job: str = "J1"
    kind: str = "work"             # work | project | volunteer | course
    status: str = ELIGIBLE         # confirmed | pending | rejected | superseded
    tags: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Case:
    id: str
    background: str
    records: Tuple[Record, ...]
    label_status: str = "author_proposed"
    notes: str = ""

    @property
    def eligible(self) -> Tuple[Record, ...]:
        return tuple(r for r in self.records if r.status == ELIGIBLE)


def R(id, text, clear=(), related=(), **kw) -> Record:
    return Record(id=id, text=text, clear=frozenset(clear), related=frozenset(related), **kw)


TAGS = ("wording", "synonym", "implicit_ownership", "explicit_ownership", "seniority", "keyword_overlap", "negative",
        "course", "outdated", "conflicting", "overlapping_roles", "sparse", "non_pm_background", "ineligible")
