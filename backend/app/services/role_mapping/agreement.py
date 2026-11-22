"""Agreement report: the matcher against the curated mappings of the synthetic fixtures (M5, task 1).

The curated fixture mappings are a comparison baseline only. The matcher is not tuned to them, and
every disagreement is reported. ``compare_fixtures`` and the renderers are pure; ``main`` reads the
alias and taxonomy rows from the local database and the fixtures from the data foundation.

    python -m app.services.role_mapping.agreement --out-dir <directory>
"""
import argparse
import csv
import io
import json
import pathlib
from collections import Counter
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict

from app.services.role_mapping.matcher import AliasIndex, match_title
from app.services.role_mapping.models import RoleInfo, TitleMatch

PROFILES = pathlib.Path(__file__).resolve().parents[4] / "data-foundation/data/synthetic/synthetic_profiles.jsonl"

ROLE_AGREEMENT = {
    "agree": "automatic, same role as curated",
    "disagree": "automatic, DIFFERENT role from curated",
    "decision_curated_in_options": "decision required; curated role is one of the options",
    "decision_curated_not_in_options": "decision required; curated role is NOT one of the options",
    "unresolved_both": "unresolved; curated role is unresolved too",
    "unresolved_curated_resolved": "unresolved; curated mapping names a resolved role",
}


class AgreementRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    profile_id: str
    employment_id: str
    sequence_index: int
    match: TitleMatch
    curated_role_id: str
    curated_seniority_code: Optional[str]          # the fixture's override (usually none)
    curated_effective_seniority_code: str
    curated_mapping_status: str
    curated_mapping_confidence: Decimal
    role_agreement: str
    seniority_agreement: Optional[str]             # automatic matches only: 'agree' / 'disagree'


def _role_agreement(m: TitleMatch, curated: str, roles: Dict[str, RoleInfo]) -> str:
    if m.outcome == "automatic":
        return "agree" if m.proposed_role_id == curated else "disagree"
    if m.outcome == "context_required":
        return ("decision_curated_in_options" if curated in {o.role_id for o in m.options}
                else "decision_curated_not_in_options")
    return "unresolved_both" if not roles[curated].is_resolved else "unresolved_curated_resolved"


def compare_fixtures(profiles: Iterable[dict], index: AliasIndex, roles: Dict[str, RoleInfo]) -> List[AgreementRow]:
    out = []
    for p in profiles:
        for e in sorted(p["employments"], key=lambda x: x["sequence_index"]):
            m = match_title(e["title_raw_redacted"], index)
            curated = e["role_id"]
            curated_eff = e.get("seniority_code") or roles[curated].default_seniority_code
            sen = None
            if m.outcome == "automatic":
                sen = "agree" if (m.effective_seniority_code == curated_eff
                                  and m.proposed_seniority_code == e.get("seniority_code")) else "disagree"
            out.append(AgreementRow(
                profile_id=p["profile_id"], employment_id=e["employment_id"], sequence_index=e["sequence_index"],
                match=m, curated_role_id=curated, curated_seniority_code=e.get("seniority_code"),
                curated_effective_seniority_code=curated_eff, curated_mapping_status=e["mapping_status"],
                curated_mapping_confidence=Decimal(str(e["mapping_confidence"])),
                role_agreement=_role_agreement(m, curated, roles), seniority_agreement=sen))
    return out


def summarize(rows: List[AgreementRow], index: AliasIndex) -> dict:
    by_title: Dict[Tuple[str, str], List[AgreementRow]] = {}
    for r in rows:
        by_title.setdefault((r.match.outcome, r.match.original_title or ""), []).append(r)
    return {
        "profiles": len({r.profile_id for r in rows}),
        "employments": len(rows),
        "outcomes": dict(Counter(r.match.outcome for r in rows)),
        "role_agreement": dict(Counter(r.role_agreement for r in rows)),
        "seniority_agreement": dict(Counter(r.seniority_agreement for r in rows if r.seniority_agreement)),
        "decision_titles": sorted({r.match.original_title for r in rows if r.match.outcome == "context_required"}),
        "unresolved_titles": sorted({r.match.original_title or "" for r in rows if r.match.outcome == "unresolved"}),
        "multi_role_titles": sorted({r.match.original_title for r in rows if len(r.match.options) > 1}),
        "multi_role_aliases_in_table": index.multi_role_aliases(),
    }


def _options(m: TitleMatch) -> str:
    return "; ".join(f"{o.role_id} ({o.default_confidence})" for o in m.options) if m.outcome == "context_required" else ""


COLUMNS = ["profile_id", "employment_id", "seq", "original_title", "lookup_title", "matched_alias_ids", "outcome",
           "proposed_role", "proposed_seniority_override", "proposed_effective_seniority", "mapping_status",
           "confidence", "decision_options", "curated_role", "curated_seniority_override",
           "curated_effective_seniority", "curated_status", "curated_confidence", "role_agreement", "seniority_agreement"]


def _cells(r: AgreementRow) -> List[str]:
    m = r.match
    s = lambda v: "" if v is None else str(v)
    return [r.profile_id, r.employment_id, str(r.sequence_index), s(m.original_title), s(m.lookup_title),
            ",".join(m.matched_alias_ids), m.outcome, s(m.proposed_role_id), s(m.proposed_seniority_code),
            s(m.effective_seniority_code), m.mapping_status, s(m.mapping_confidence), _options(m), r.curated_role_id,
            s(r.curated_seniority_code), r.curated_effective_seniority_code, r.curated_mapping_status,
            s(r.curated_mapping_confidence), r.role_agreement, s(r.seniority_agreement)]


def render_csv(rows: List[AgreementRow]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    for r in rows:
        w.writerow(_cells(r))
    return buf.getvalue()


def render_markdown(rows: List[AgreementRow], summary: dict) -> str:
    esc = lambda v: v.replace("|", "\\|")
    lines = ["# Title-to-role matcher: agreement with the curated synthetic fixture mappings", "",
             "Comparison baseline only: the matcher is not tuned to the fixtures. Synthetic titles were curated "
             "from the alias table, so agreement here overstates coverage of real resume titles.", "",
             f"Profiles: {summary['profiles']}; employments: {summary['employments']}.", "", "## Outcomes", ""]
    lines += [f"- {k}: {v}" for k, v in sorted(summary["outcomes"].items())]
    lines += ["", "## Role agreement", ""]
    lines += [f"- {ROLE_AGREEMENT[k]} (`{k}`): {v}" for k, v in sorted(summary["role_agreement"].items())]
    lines += ["", "## Seniority agreement (automatic matches)", ""]
    lines += [f"- {k}: {v}" for k, v in sorted(summary["seniority_agreement"].items())]
    lines += ["", "## Titles that need a candidate decision", ""] + [f"- {t}" for t in summary["decision_titles"]]
    lines += ["", "## Unresolved titles", ""] + [f"- {t}" for t in summary["unresolved_titles"]]
    lines += ["", "## Titles with several possible roles", ""] + [f"- {t}" for t in summary["multi_role_titles"]]
    lines += ["", "## Every employment", "", "| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    lines += ["| " + " | ".join(esc(c) for c in _cells(r)) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> None:
    from app import db
    from app.config import get_settings
    from app.services.role_mapping.repository import PostgresAliasRepository

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profiles", type=pathlib.Path, default=PROFILES)
    ap.add_argument("--out-dir", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    with db.connect(get_settings()) as conn:
        repo = PostgresAliasRepository(conn)
        index, roles = AliasIndex(repo.alias_rows()), repo.role_rows()
    profiles = [json.loads(line) for line in a.profiles.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = compare_fixtures(profiles, index, roles)
    summary = summarize(rows, index)
    a.out_dir.mkdir(parents=True, exist_ok=True)
    (a.out_dir / "role_mapping_agreement.md").write_text(render_markdown(rows, summary), encoding="utf-8")
    (a.out_dir / "role_mapping_agreement.csv").write_text(render_csv(rows), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
