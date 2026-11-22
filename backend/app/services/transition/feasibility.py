"""Personal transition feasibility and route selection (D27, D31-D34; assessment 3 of 3). Pure and deterministic.

Requirement-level rules. No percentages, weights, scores or counts decide anything (Phase 4 review, D31-D32).

Decision-rights requirements (D31) are those flagged in the approved pack (pm-v1.2: C2 strategy and roadmap, C4 outcome
metrics and decisions, L1 portfolio scope). Their evidence needs authority over a product; every other requirement is a
"practice" requirement that can be shown from many roles and is closed by actions on any route.

    G (decision-rights gaps): flagged requirements that apply and are
        - Gap, for a Product Manager target (credible Transferable evidence supports a direct move, D31);
        - Gap or only Transferable, for a Senior Product Manager target (they must be Demonstrated at that level, D31).
      L1 counts only when the user states that the target role has portfolio scope; otherwise it is reported and
      planned for, but never a barrier (D31).

    R1  A flagged requirement is Unknown for a reason the user can resolve  -> no primary route; resolve first.
    R2  G is empty                                                       -> direct is primary.
    R3  Some bridge is eligible                                          -> the eligible bridges no other eligible
        bridge dominates are primary (several: tied). Direct is the alternative.
    R4  No bridge is eligible                                            -> direct is primary, "conditional": it names
        the gaps still to show and how.

    Eligible bridge: a catalogue bridge for this target that the user does not already hold, that may be primary (the
    Product Owner bridge may not, D34), whose entry is credible (evidenced by the user's profile against stated entry
    requirements, stated by the source as an entry route, or the user's own Product Manager plan), and that develops
    at least one requirement in G fully or partly (D32). Partial development is never presented as closing a gap: it
    states what is developed, what remains incomplete and what responsibility to seek.

    Dominance, per requirement and without counting: B1 dominates B2 if it develops every requirement in G at least as
    fully (full > partly > not at all) and one strictly more fully; equal on G, the same comparison on practice gaps.

Employer eligibility and observed evidence breadth are never inputs. The number of moves is a trade-off.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

ENGINE_VERSION = "transition-planner-3.0.0"      # Phase 5 (D50): Trace reads confirmed lines; same-role targets
RESOLVABLE = ("no_reviewed_evidence", "evidence_pending_review", "evidence_not_classified", "classified_before_taxonomy_update",
              "scope_question_not_answered")
SHOWN_STATES = ("demonstrated", "transferable")
CREDIBLE_ENTRY = ("evidenced", "source_stated", "target_plan")
LEVEL = {"full": 2, "partial": 1, "none": 0}
# D4 presentation bands for OBSERVED evidence breadth (provisional; reviewed before production). Never used for ordering.
BREADTH_BANDS = ((100, "extensive"), (25, "broad"), (10, "growing"), (5, "limited"))
PROFILE_LED_BREADTH = "limited_or_unavailable"


def breadth_label(count: Optional[int], synthetic: bool) -> Optional[str]:
    if synthetic:
        return "synthetic_test_data"
    if count is None:
        return None
    return next((label for floor, label in BREADTH_BANDS if count >= floor), None)


@dataclass
class Support:
    level: str                                  # undetermined | supported | conditional
    decision_rights: List[str]                  # flagged requirements that apply (and count)
    gaps: List[str]                             # G: decision-rights gaps for this target
    practice_gaps: List[str]                    # other material requirements in state Gap
    blocked: List[str]                          # flagged requirements Unknown for a resolvable reason
    scope_dependent: List[str]                  # L1 when the target's portfolio scope was not stated
    explanation: str


def direct_support(items: Sequence[dict], senior: bool = False, portfolio_scope: bool = False) -> Support:
    material = [i for i in items if i["state"] != "not_applicable"]
    titles = {i["key"]: i["title"] for i in material}
    scope_dependent = [i["key"] for i in material if i["key"] == "L1" and i.get("decision_rights") and not portfolio_scope]
    dr = [i for i in material if i.get("decision_rights") and i["key"] not in scope_dependent]
    blocked = [i["key"] for i in dr if i["state"] == "unknown" and i.get("reason") in RESOLVABLE]
    shortfall = ("gap", "transferable") if senior else ("gap",)
    gaps = [i["key"] for i in dr if i["state"] in shortfall]
    practice = [i["key"] for i in material if not i.get("decision_rights") and i["state"] == "gap"]
    names = lambda keys: ", ".join(f"\"{titles[k]}\"" for k in keys)   # noqa: E731
    if blocked:
        return Support("undetermined", [i["key"] for i in dr], gaps, practice, blocked, scope_dependent,
                       f"Trace can't compare routes until it can assess {names(blocked)}: review the records listed.")
    if not gaps:
        why = ("Your confirmed experience shows" if senior else "Your confirmed experience shows, or credibly carries over to,")
        return Support("supported", [i["key"] for i in dr], gaps, practice, blocked, scope_dependent,
                       f"{why} the decision-rights requirements of this role ({names([i['key'] for i in dr])}).")
    level_note = " at the level a senior role needs" if senior else ""
    return Support("conditional", [i["key"] for i in dr], gaps, practice, blocked, scope_dependent,
                   f"Your confirmed experience doesn't yet show {names(gaps)}{level_note}.")


@dataclass(eq=False)                            # compared by identity
class BridgeFinding:
    bridge: dict
    levels: Dict[str, str]                      # requirement (G and practice gaps) -> full | partial | none
    entry: str                                  # evidenced | source_stated | target_plan | hypothesis | not_evidenced | unclear
    entry_explanation: str
    entry_records: List[str]
    already_held: bool
    status: str = "not_recommended"
    reasons: List[str] = field(default_factory=list)

    def develops(self, keys: Sequence[str], level: str) -> List[str]:
        return [k for k in keys if self.levels.get(k) == level]

    @property
    def eligible_entry(self) -> bool:
        return self.entry in CREDIBLE_ENTRY


def _entry(bridge: dict, states: Dict[str, dict], roles: Sequence[dict], target_plan_support: Optional[Support]) -> tuple:
    kind = bridge.get("entry_evidence")
    if kind == "target_plan":
        return "target_plan", ("Moving into a Product Manager role is the next goal; your Product Manager plan shows how."
                               + (f" {target_plan_support.explanation}" if target_plan_support else "")), []
    if kind == "source_stated_context":
        return "source_stated", bridge["entry_requirements_stated"], []
    held = [r for r in roles if r["role_family"] in set(bridge["entry_signal_families"])]
    shown = [k for k in bridge["entry_signal_requirements"] if states.get(k, {}).get("state") in SHOWN_STATES]
    if held or shown:
        parts = []
        if held:
            parts.append("you have held a related role (" + ", ".join(sorted({r["title"] for r in held})) + ")")
        if shown:
            parts.append("your profile shows " + ", ".join(f"\"{states[k]['title']}\"" for k in shown))
        refs = [r["record_id"] for r in held] + [s["record_id"] for k in shown for s in states[k].get("supporting", [])]
        if kind == "hypothesis":
            return "hypothesis", ("A possible way in (a curated hypothesis, not established by the source): " + " and ".join(parts) + "."), sorted(set(refs))
        return "evidenced", "A credible way in against the stated entry requirements: " + " and ".join(parts) + ".", sorted(set(refs))
    if any(states.get(k, {}).get("state") == "unknown" and states[k].get("reason") in RESOLVABLE for k in bridge["entry_signal_requirements"]):
        return "unclear", "Whether you have a way in depends on records you still need to review.", []
    return "not_evidenced", "Your confirmed profile doesn't show what this role asks for at entry: " + bridge["suitable_backgrounds"], []


def evaluate_bridges(bridges: Sequence[dict], items: Sequence[dict], roles: Sequence[dict], current_role_id: Optional[str],
                     support: Support, target_plan_support: Optional[Support] = None) -> List[BridgeFinding]:
    states = {i["key"]: i for i in items}
    keys = list(support.gaps) + list(support.practice_gaps)
    out = []
    for b in bridges:
        entry, why, refs = _entry(b, states, roles, target_plan_support)
        levels = {k: "full" if k in b["develops"] else "partial" if k in b["partly_develops"] else "none" for k in keys}
        out.append(BridgeFinding(bridge=b, levels=levels, entry=entry, entry_explanation=why, entry_records=refs,
                                 already_held=current_role_id == b["bridge_role_id"]))
    return out


def _dominates(a: BridgeFinding, b: BridgeFinding, keys: Sequence[str]) -> Optional[bool]:
    """True if a dominates b on keys, False if b dominates a, None if equal or incomparable."""
    ge = all(LEVEL[a.levels[k]] >= LEVEL[b.levels[k]] for k in keys)
    le = all(LEVEL[a.levels[k]] <= LEVEL[b.levels[k]] for k in keys)
    if ge and not le:
        return True
    if le and not ge:
        return False
    return None


def _undominated(cands: List[BridgeFinding], support: Support) -> List[BridgeFinding]:
    keep = []
    for a in cands:
        beaten = False
        for b in cands:
            if a is b:
                continue
            d = _dominates(b, a, support.gaps)
            if d is None and all(a.levels[k] == b.levels[k] for k in support.gaps):
                d = _dominates(b, a, support.practice_gaps)
            if d is True:
                beaten = True
                break
        if not beaten:
            keep.append(a)
    return keep


def describe(f: BridgeFinding, support: Support, titles: Dict[str, str]) -> List[str]:
    """What the bridge develops for this user, in words. Partial development never reads as closing a gap (D32)."""
    out = []
    full = f.develops(support.gaps, "full")
    part = f.develops(support.gaps, "partial")
    if full:
        out.append("Develops " + ", ".join(f"\"{titles[k]}\"" for k in full) + ".")
    if part:
        out.append("Develops only partly " + ", ".join(f"\"{titles[k]}\"" for k in part) + ": see what remains and what to seek.")
    prac = f.develops(support.practice_gaps, "full") + f.develops(support.practice_gaps, "partial")
    if prac:
        out.append("Also helps with " + ", ".join(f"\"{titles[k]}\"" for k in prac) + ".")
    return out


@dataclass
class Decision:
    rule: str
    primary: List[str]                          # route ids ("direct" or a bridge role id); several when tied
    explanation: str
    trace: List[dict]


def decide(support: Support, findings: List[BridgeFinding], titles: Dict[str, str]) -> Decision:
    trace = [{"rule": "support", "result": support.level, "explanation": support.explanation, "decision_rights": support.decision_rights,
              "decision_rights_gaps": support.gaps, "practice_gaps": support.practice_gaps, "blocked": support.blocked,
              "scope_dependent": support.scope_dependent}]
    live = [f for f in findings if not f.already_held]
    for f in findings:
        if f.already_held:
            f.status, f.reasons = "not_evaluated", ["This is your current role, so it isn't a step on the way."]
    if support.level == "undetermined":
        for f in live:
            f.status, f.reasons = "not_evaluated", ["Not compared until the listed records are reviewed."]
        trace.append({"rule": "R1", "result": "no_primary"})
        return Decision("R1", [], support.explanation, trace)
    if support.level == "supported":
        for f in live:
            f.status, f.reasons = "not_recommended", ["It would add a move: your confirmed experience already supports a direct move."]
        trace.append({"rule": "R2", "result": "direct"})
        return Decision("R2", ["direct"], support.explanation + " A direct move is the best supported route.", trace)
    eligible, why_not = [], {}                  # keyed by id(finding)
    for f in live:
        reaches = [k for k in support.gaps if f.levels[k] != "none"]
        if not reaches:
            why_not[id(f)] = "It doesn't develop the decision-rights requirements you are missing."
        elif not f.bridge.get("may_be_primary", True):
            why_not[id(f)] = "Shown as an alternative only: the move into and out of this role is a curated hypothesis (D34)."
        elif not f.eligible_entry:
            why_not[id(f)] = f.entry_explanation
        else:
            eligible.append(f)
    top = _undominated(eligible, support)
    for f in live:
        if f in top:
            f.status = "primary" if len(top) == 1 else "tied_primary"
            f.reasons = describe(f, support, titles) + ["You have a credible way in."]
        elif f in eligible:
            f.status = "alternative"
            f.reasons = describe(f, support, titles) + ["Another bridge develops your missing requirements more fully."]
        else:
            reaches = [k for k in support.gaps + support.practice_gaps if f.levels[k] != "none"]
            f.status = "alternative" if reaches and f.entry != "not_evidenced" else "not_recommended"
            f.reasons = describe(f, support, titles) + [why_not[id(f)]]
    if not top:
        trace.append({"rule": "R4", "result": "direct_conditional", "why": "no eligible bridge develops the missing decision-rights requirements"})
        return Decision("R4", ["direct"], support.explanation + " No catalogued bridge you have a credible way into develops them, so the "
                                          "direct move stays the main route and the plan shows how to build that evidence.", trace)
    trace.append({"rule": "R3", "result": "bridge", "bridges": [f.bridge["bridge_role_id"] for f in top],
                  "levels": {f.bridge["bridge_role_id"]: {k: f.levels[k] for k in support.gaps} for f in eligible}})
    return Decision("R3", [f.bridge["bridge_role_id"] for f in top],
                    support.explanation + (" This bridge role develops them in the role itself." if len(top) == 1
                                           else " These bridge roles each develop them, in different ways."), trace)
