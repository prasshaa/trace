"""What each target is actually hired for, from the PM knowledge pack.

Associate and Product Manager readings do not include coaching other product managers. Senior Product Manager does.
Descriptions come from the pack; the level note is how that requirement applies at this rung.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List

_PACK = Path(__file__).resolve().parents[4] / "data-foundation" / "knowledge_packs" / "pm-v1.2.json"

# C2 (owning direction) is a Product Manager expectation, not an Associate one.
# L1–L4 are senior-only in the pack. Coaching (L2) is never asked of APM or PM.
_APM = ("C1", "C3", "C4", "C5", "C6")
_PM = _APM + ("C2",)
_SPM = _PM + ("L1", "L2", "L3", "L4")

_NOTE = {
    "PM-APM": {
        "C1": "For an associate role: you can show you learned what users need. You are not expected to own the roadmap.",
        "C3": "Writing the requirement and the trade-off is the associate craft.",
        "C4": "Using a measure on your own slice counts. You are not expected to own the company's metric or to coach anyone.",
        "C5": "Working with engineering and design on a decision counts. Managing other product managers does not.",
        "C6": "Shipping one piece of work and saying what you learned is enough.",
    },
    "PM-PM": {
        "C2": "Owning the roadmap for one product area counts. A portfolio, and coaching other product managers, does not.",
        "C4": "Defining a measure and deciding with it, on the area you own.",
        "L2": "Do not treat coaching as a requirement at this level.",
    },
    "PM-SPM": {
        "C2": "Direction across more than one area, not only one list.",
        "L2": "Coaching or mentoring another product person is part of this move.",
        "L3": "A recommendation to senior leaders that got a decision.",
        "L1": "Scope beyond a single product area.",
    },
}


@lru_cache(maxsize=1)
def _pack() -> Dict[str, dict]:
    data = json.loads(_PACK.read_text())
    return {r["key"]: r for r in data["requirements"]}


def requirements_for(target_id: str) -> List[dict]:
    """The requirements the narrative may use for this target. Coaching is only included for Senior Product Manager."""
    keys = _SPM if target_id == "PM-SPM" else _PM if target_id == "PM-PM" else _APM
    notes = _NOTE.get(target_id, _NOTE["PM-APM"])
    rows = _pack()
    out = []
    for key in keys:
        src = rows.get(key)
        if src is None:
            continue
        out.append({
            "requirement_key": key,
            "title": src["title"],
            "description": src["description"],
            "direct_areas": list(src.get("direct_areas") or []),
            "level_note": notes.get(key, ""),
        })
    return out
