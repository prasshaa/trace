"""Four-stage actions from the versioned template library (D28). Pure and deterministic.

State logic (never relaxed):
    Gap           -> Build (stage 1) and Demonstrate (stage 2)
    Transferable  -> Demonstrate (stage 2) and Reposition (stage 3), citing the user's own supporting record
    Demonstrated  -> Reposition only (stage 3): never relearning what the profile already shows
    Unknown       -> resolve first (stage 0): the named records, never a capability action
    Not applicable-> nothing
Stage 4 (get into the role) is always included, whatever the gaps: postings (only verified current ones), positioning,
people already in the role, interview preparation and alternative routes (internal moves; the bridge role first on a
bridge route). Nothing names a course, provider, cost, timeline, placement outcome or opening that is not a verified
posting in the knowledge base.
"""
from typing import Dict, List, Optional, Sequence

from app.services.transition.feasibility import RESOLVABLE

RESOLVE_TEMPLATE = {"evidence_pending_review": "resolve.pending", "evidence_not_classified": "resolve.unclassified",
                    "classified_before_taxonomy_update": "resolve.recheck", "scope_question_not_answered": "resolve.scope_question",
                    "no_reviewed_evidence": "resolve.no_evidence"}


def _fill(text: str, values: Dict[str, str]) -> str:
    for k, v in values.items():
        text = text.replace("{" + k + "}", v)
    return text


def _excerpt(item: dict) -> Optional[str]:
    s = next((s for s in item.get("supporting", []) if s.get("excerpt")), None)
    return s["excerpt"] if s else None


class ActionBuilder:
    def __init__(self, templates: Dict[str, dict], source_inventory: str):
        self.templates = templates
        self.inventory = source_inventory

    def _make(self, key: str, why: str, item: Optional[dict] = None, refs: Sequence[str] = (), values: Optional[Dict[str, str]] = None,
              extra_sources: Sequence[str] = ()) -> dict:
        t = self.templates[key]
        values = dict(values or {})
        if item is not None:
            values.setdefault("requirement_title", item["title"])
            values.setdefault("record_excerpt", _excerpt(item) or "")
        req_sources = [f"{self.inventory}:{s['key']}" for s in (item or {}).get("sources", []) if s.get("stance") in ("supports", "qualifies")]
        return {"stage": t["stage"], "kind": t["kind"], "template_key": key, "requirement_key": item["key"] if item else None,
                "objective": _fill(t["objective"], values), "action": _fill(t["action_text"], values), "why_for_you": why,
                "deliverable": _fill(t["deliverable"], values), "where": list(t["where_options"]), "labels": list(t.get("labels") or []),
                "source_keys": sorted(set(t["basis_sources"]) | set(req_sources) | set(extra_sources)), "record_refs": sorted(set(refs))}

    # ------------------------------------------------------------------ stage 0
    def resolve(self, items: Sequence[dict]) -> List[dict]:
        out = []
        for reason, key in RESOLVE_TEMPLATE.items():
            hit = [i for i in items if i["state"] == "unknown" and i.get("reason") == reason]
            if not hit:
                continue
            refs = sorted({r for i in hit for r in i.get("blocking_records", [])})
            names = ", ".join(i["title"] for i in hit[:4]) + (" and others" if len(hit) > 4 else "")
            out.append(self._make(key, f"Until this is done Trace can't tell whether your experience covers: {names}.", refs=refs))
        for i in items:
            if i["state"] == "unknown" and i.get("reason") == "not_inferable_from_absence":
                out.append(self._make("resolve.not_inferable", f"Trace can't tell from missing information whether you have "
                                      f"\"{i['title']}\", so it doesn't assume a gap.", item=i))
        return out

    # ------------------------------------------------------------------ stages 1-3
    def capability(self, items: Sequence[dict], develops: Sequence[str] = (), bridge_title: Optional[str] = None) -> List[dict]:
        out = []
        for i in items:
            state = i["state"]
            refs = [s["record_id"] for s in i.get("supporting", [])]
            in_bridge = bridge_title and i["key"] in develops
            if state == "gap":
                why = f"\"{i['title']}\" is a gap: your reviewed, categorised experience doesn't show it."
                if in_bridge:
                    why += f" The {bridge_title} role develops it day to day."
                out.append(self._make(f"build.{i['key']}", why, item=i))
                out.append(self._make(f"demonstrate.{i['key']}", f"Employers look for real evidence of \"{i['title']}\"; "
                                      "none of your reviewed experience shows it yet.", item=i))
            elif state == "transferable":
                ex = _excerpt(i)
                why = (f"Your experience carries over to \"{i['title']}\"" + (f" (\"{ex}\")" if ex else "") + " but doesn't show it directly."
                       + (f" {i['transfer_rationale']}" if i.get("transfer_rationale") else ""))
                out.append(self._make(f"demonstrate.{i['key']}", why, item=i, refs=refs))
                out.append(self._make(self._reposition(i, "reposition.transferable"),
                                      f"Show employers how your experience relates to \"{i['title']}\", without overstating it.", item=i, refs=refs))
            elif state == "demonstrated":
                ex = _excerpt(i)
                out.append(self._make(self._reposition(i, "reposition.demonstrated"), f"Your profile already shows \"{i['title']}\""
                                      + (f": \"{ex}\"" if ex else "") + ". You don't need to relearn it; present it accurately as product evidence.",
                                      item=i, refs=refs))
        return out

    @staticmethod
    def _reposition(item: dict, default: str) -> str:
        """The evidence basis decides the template: a stated skill or a role history is not a line of experience (T4)."""
        basis = item.get("basis")
        return "reposition.skill" if basis == "confirmed_skill" else "reposition.role" if basis == "role_history" else default

    # ------------------------------------------------------------------ stage 4 (always)
    def entry(self, items: Sequence[dict], target_title: str, postings: Sequence[dict], bridge: Optional[dict] = None) -> List[dict]:
        out = []
        if bridge is not None:
            out.append(self._make("apply.bridge_role", f"On this route the next move is into a {bridge['bridge_title']} role. "
                                  + " ".join(bridge["reasons"]),
                                  values={"bridge_title": bridge["bridge_title"], "target_title": target_title},
                                  extra_sources=bridge["source_keys"]))
            for p in bridge["partials"]:                                  # D32: what remains and what to seek, never "closed"
                out.append(self._make("apply.bridge_seek", f"The {bridge['bridge_title']} role develops \"{p['title']}\" only partly.",
                                      values={"bridge_title": bridge["bridge_title"], "requirement_title": p["title"], "develops": p["develops"],
                                              "incomplete": p["incomplete"], "seek": p["seek"]}))
                out[-1]["requirement_key"] = p["key"]
        current = [p for p in postings if p["is_current_opportunity"]]
        if bridge is None and current:
            listing = "; ".join(f"{p['company_name']}: {p['eligibility']['outcome'].replace('_', ' ')} (verified "
                                f"{p['last_verified_at'][:10]})" for p in current)
            out.append(self._make("apply.current_postings", f"Verified current postings for {target_title}, with how your confirmed "
                                  f"profile compares with each employer's stated criteria: {listing}.",
                                  extra_sources=[f"{self.inventory}:{p['source_key']}" for p in current]))
            unclear = [p["company_name"] for p in current if p["eligibility"]["outcome"] == "unclear"]
            out.append(self._make("apply.check_criteria", ("Some employers' criteria can't be settled from your profile: " + ", ".join(unclear) + ".")
                                  if unclear else "Each employer sets its own criteria; check them before you apply.",
                                  extra_sources=[f"{self.inventory}:{p['source_key']}" for p in current]))
        elif bridge is None:
            out.append(self._make("apply.no_verified_postings", f"No posting for {target_title} in Trace's knowledge base is verified as "
                                  "currently open, so none is shown as an opportunity."))
        shown = [i["title"] for i in items if i["state"] in ("demonstrated", "transferable")]
        out.append(self._make("apply.position_application", ("Lead with what your profile shows: " + ", ".join(shown) + ".")
                              if shown else "Your stage 2 results will be the core of your application."))
        out.append(self._make("apply.case_study", "A case study lets employers see how you work, beyond your resume.",
                              values={"target_title": target_title},
                              refs=[s["record_id"] for i in items if i["state"] == "demonstrated" for s in i.get("supporting", [])
                                    if s.get("kind", "evidence") == "evidence"]))
        out.append(self._make("apply.referrals", f"People already working as {target_title} can tell you how their teams hire."))
        weak = [i for i in items if i["state"] in ("gap", "transferable")]
        out.append(self._make("apply.interview_preparation",
                              ("Interviewers will probe the requirements your profile doesn't show directly: " + ", ".join(i["title"] for i in weak) + ".")
                              if weak else "Interviewers will still test your product judgement with a case.",
                              refs=[s["record_id"] for i in weak for s in i.get("supporting", [])]))
        out.append(self._make("apply.internal_route", "An internal move is another way in: your organisation already knows your work."))
        out.append(self._make("apply.alternative_routes", "When no verified opening fits, other routes keep you moving."))
        return out
