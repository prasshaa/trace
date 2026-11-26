"""Peer path matching: the cohort file, the group wording, and the prompt rules. No database."""
import csv
import re
from pathlib import Path

import pytest

from app.services.ai_assessment.guardrails import apply_guardrails, keep_claim
from app.services.ai_assessment.service import SYSTEM, _user_prompt
from app.services.peer_matching.aggregate import band_count, nearest, patterns_from_neighbours, raw_counts_hidden
from app.services.peer_matching.service import _from_payload, peer_section
from app.services.peer_matching.text import COLUMNS, CohortFileError, cohort_path_text, parse_row

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "backend" / "fixtures" / "peer_cohort_synthetic.csv"
MIGRATION = ROOT / "data-foundation" / "supabase" / "migrations" / "20261126200000_trace_peer_cohort.sql"


def _blank():
    return {name: "" for name in COLUMNS}


def test_the_loader_rejects_a_missing_column_and_a_bad_bool_and_keeps_a_blank_int():
    raw = _blank()
    raw.pop("has_mba")
    with pytest.raises(CohortFileError):
        parse_row(raw)
    raw = _blank()
    raw.update({
        "norm_seniority_group": "Product Manager",
        "final_multi_function": "False",
        "final_first_pm_dated": "True",
        "final_has_history": "True",
        "final_dated_role_before_first_pm": "True",
        "final_n_pre_pm_roles": "3",
        "final_n_roles": "5",
        "final_n_pm_roles": "2",
        "norm_edu_has_degree": "True",
        "has_mba": "maybe",
        "final_entry_move": "internal",
        "final_months_to_first_pm_from_earliest_nonintern": "",
        "norm_career_start_year": "",
    })
    with pytest.raises(CohortFileError, match="has_mba"):
        parse_row(raw)
    raw["has_mba"] = "False"
    parsed = parse_row(raw)
    assert parsed["final_months_to_first_pm_from_earliest_nonintern"] is None
    assert parsed["norm_career_start_year"] is None
    sentence = cohort_path_text(parsed)
    assert sentence.startswith("Product Manager.")
    assert "Moved into PM internally." in sentence
    assert "no MBA" in sentence


def test_counts_under_five_are_hidden_and_five_to_nine_are_a_band():
    assert band_count(3) is None
    assert band_count(4) is None
    assert band_count(7) == "5–9"
    assert band_count(24) == "24"


def test_the_function_returns_counts_and_not_rows():
    sql = MIGRATION.read_text(encoding="utf-8")
    body = sql.split("jsonb_build_object", 1)[1].split("$$", 1)[0]
    for hidden in ("profile_key", "path_text", "embedding", "norm_city"):
        assert hidden not in body
    assert "enable row level security" in sql
    assert "revoke all on schema trace_peer_cohort from public, anon, authenticated, service_role" in sql
    assert "security definer" in sql
    assert "set search_path = ''" in sql
    assert "grant execute on function trace_peer_cohort.peer_patterns" in sql
    assert "to trace_app_runtime" in sql
    assert not re.search(r"grant [^;]* to [^;]*anon", sql)
    refused = _from_payload({"neighbour_count": 40, "profile_key": "abc", "internal": 30}, "Product Manager")
    assert refused["available"] is False


def test_peer_numbers_must_match_the_pattern_and_a_person_is_dropped():
    lines = {"PEER-1": {"id": "PEER-1", "text": "24 of the 40 closest PMs moved into a product role from inside the employer."},
             "L1": {"id": "L1", "text": "Shipped the onboarding queue."}}
    kept, reason = keep_claim(
        {"text": "24 of the 40 closest PMs moved into a product role from inside the employer.", "evidence_ids": ["PEER-1"]},
        lines, require_citation=True, kind="strength")
    assert reason is None and "24" in kept["text"]
    dropped, why = keep_claim(
        {"text": "30 of the 40 closest PMs moved into a product role from inside the employer.", "evidence_ids": ["PEER-1"]},
        lines, require_citation=True, kind="strength")
    assert dropped is None and why == "invented_number"
    person, person_why = keep_claim(
        {"text": "She moved into a product role from inside the employer.", "evidence_ids": ["PEER-1"]},
        lines, require_citation=True, kind="strength")
    assert person is None and person_why == "peer_individual"
    resume, resume_why = keep_claim(
        {"text": "You shipped the onboarding queue.", "evidence_ids": ["L1"]},
        lines, require_citation=True, kind="strength")
    assert resume_why is None and "shipped" in resume["text"].lower()
    reading = apply_guardrails({
        "opening": "30 of the 40 closest PMs moved internally.",
        "strengths": [{"text": "You shipped the onboarding queue.", "evidence_ids": ["L1"], "why": ""}],
    }, list(lines.values()))
    assert "30" not in reading["opening"]
    assert reading["strengths"]
    assert "peer patterns" in SYSTEM.lower() or "PEER" in SYSTEM
    prompt = _user_prompt("Product Manager", "step up", [{"id": "L1", "text": "Shipped the queue.", "job": "PM"}],
                          [{"title": "Discovery", "description": "Talk to users"}], None, "in_process",
                          {"available": True, "patterns": [{"id": "PEER-1", "text": lines["PEER-1"]["text"]}]})
    assert "PEER-1" in prompt
    assert "24 of the 40" in prompt


def test_the_synthetic_cohort_matches_by_path_and_hides_a_group_of_three():
    with FIXTURE.open(newline="", encoding="utf-8") as handle:
        rows = [parse_row(dict(row)) for row in csv.DictReader(handle)]
    assert len(rows) == 40
    assert rows[0]["norm_seniority_group"] in {
        "Junior PM", "APM", "Product Manager", "Senior Product Manager", "Lead",
        "Principal/Staff/Group", "Head", "Director", "VP/CPO"}
    for index, row in enumerate(rows):
        # Two directions, so a product-manager path is near the engineering rows and far from the rest.
        row["embedding"] = [1.0, 0.0] if row["final_primary_prior_function"].startswith("Software") else [0.0, 1.0]
        row["profile_key"] = f"s{index}"
    chosen = nearest(rows, [1.0, 0.0], "Product Manager", k=40)
    view = patterns_from_neighbours(chosen, "Product Manager")
    assert view["available"] is True
    assert view["headline"] == "PMs with paths like yours"
    assert view["match_line"] == "Matched by similarity of career path."
    blob = " ".join(item["text"] for item in view["patterns"])
    assert "3 of the" not in blob
    assert "VP/CPO" not in blob or "5–9" in blob or "3 of" not in blob
    assert "inside the employer" in blob
    assert "Software Engineering / QA / IT" in blob
    assert raw_counts_hidden(view)
    assert "LinkedIn" not in blob
    assert "not a guarantee" in view["disclaimer"]
    hidden = patterns_from_neighbours(chosen, "VP/CPO")
    assert "3 of the" not in " ".join(item["text"] for item in hidden["patterns"])
    assert view["takeaways"]
    assert peer_section(None, None, "PM-PM", "Product Manager") == {"available": False}
