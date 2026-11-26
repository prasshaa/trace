"""Evidence provenance for the career transition roadmap (product redesign).

Every personal statement in the roadmap ("You've recorded experience in ...") must point at a record that is ELIGIBLE
NOW. This module builds that index from the caller's current profile, reusing the capability assessment's own rule
(`reviewed_records`, M1-M6 safeguards unchanged):

  evidence   a responsibility, achievement or other line the user confirmed or corrected (review), that the user
             categorised with areas of responsibility, and whose text has not changed since (not stale). The areas are
             the USER'S OWN categorisation: shown as "categorised by you", never as independently verified expertise
  skill      a skill the user confirmed, resolved to the skill taxonomy
  role       a confirmed job whose role match is current (not stale, not unresolved)

  evidence   (Phase 5, D50) a confirmed or corrected line the user hasn't categorised, with what Trace's reading of
             its text shows (`interpret.py`): "read_by_trace", the areas it CLEARLY shows, the words it rests on and why.
             The user's own categorisation always wins.

Pending, rejected and otherwise ineligible records are simply absent, so nothing can be said from them. Raw resume text
that the user hasn't confirmed is never read.
"""
from typing import Dict, Optional, Tuple

from app.services.capability.assess import _short, reviewed_records


def evidence_index(profile, skill_names: Optional[Dict[str, Tuple[str, str]]] = None,
                   area_labels: Optional[Dict[str, str]] = None, family_labels: Optional[Dict[str, str]] = None) -> Dict[str, dict]:
    facts = reviewed_records(profile, skill_names)
    areas = area_labels or {}
    owner: Dict[str, str] = {}
    for e in profile.employment_history:
        for x in e.responsibilities + e.achievements + e.other_evidence:
            employer = e.employer.employer_name if e.employer and e.employer.employer_name else None
            owner[str(x.candidate_evidence_id)] = f"{e.title_raw}, {employer}" if employer else e.title_raw
    for acts in profile.career_activities.values():
        for a in acts:
            for x in a.achievements + a.other_evidence:
                owner[str(x.candidate_evidence_id)] = getattr(a, "title_raw", None) or "An activity in your profile"
    out: Dict[str, dict] = {}
    for x in facts["reviewed"]:
        rid = str(x.candidate_evidence_id)
        cls = facts["classified"].get(x.candidate_evidence_id)
        if cls is not None:
            chosen, _, _ = cls
            out[rid] = {"kind": "evidence", "text": _short(x.evidence_text, 160), "source": owner.get(rid),
                        "categorised_as": sorted(areas.get(a, a) for a in chosen), "areas": sorted(chosen),
                        "basis": "confirmed_line_categorised_by_you"}
            continue
        reading = facts.get("read", {}).get(x.candidate_evidence_id)
        if reading is None or not (reading.clear or reading.partial):
            continue                                   # nothing in this line relates to a responsibility area
        out[rid] = {"kind": "evidence", "text": _short(x.evidence_text, 160), "source": owner.get(rid),
                    "categorised_as": sorted(areas.get(a, a) for a in reading.clear), "areas": sorted(reading.clear),
                    "related_areas": sorted(reading.partial), "basis": "read_by_trace",
                    "why": {areas.get(a, a): {"cue": c, "why": w} for a, (c, w) in {**reading.partial, **reading.clear}.items()}}
    for rid, _, name in facts["skills"]:
        out[str(rid)] = {"kind": "skill", "text": name, "source": None, "categorised_as": [], "areas": [], "basis": "confirmed_skill"}
    for e in facts["roles"]:
        out[str(e.candidate_employment_id)] = {
            "kind": "role", "text": e.title_raw, "source": None,
            "categorised_as": [(family_labels or {}).get(e.role_family, e.role_family)], "areas": [], "basis": "confirmed_role"}
    return out
