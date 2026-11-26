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
    assert "No MBA." in sentence
    assert "degree" not in sentence.lower()
    assert "not classified" not in sentence
    assert "0 roles" not in sentence


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
        lines, require_citation=False, kind="step")
    assert reason is None and "24" in kept["text"]
    blocked, blocked_why = keep_claim(
        {"text": "24 of the 40 closest PMs moved into a product role from inside the employer.", "evidence_ids": ["PEER-1"]},
        lines, require_citation=True, kind="strength")
    assert blocked is None and blocked_why == "peer_evidence"
    dropped, why = keep_claim(
        {"text": "30 of the 40 closest PMs moved into a product role from inside the employer.", "evidence_ids": ["PEER-1"]},
        lines, require_citation=False, kind="step")
    assert dropped is None and why == "invented_number"
    person, person_why = keep_claim(
        {"text": "She moved into a product role from inside the employer.", "evidence_ids": ["PEER-1"]},
        lines, require_citation=False, kind="step")
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
    assert "PEER" in SYSTEM
    assert "use at least one" not in SYSTEM.lower()
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
    from app.services.peer_matching.aggregate import match_neighbours
    pool = []
    for i in range(80):
        pool.append({**_row(final_primary_prior_function="Software Engineering / QA / IT", final_entry_move="internal",
                            norm_seniority_group="APM"), "embedding": [1.0, 0.0], "profile_key": f"e{i}"})
    for i in range(20):
        pool.append({**_row(final_primary_prior_function="Design / UX", final_entry_move="external",
                            norm_seniority_group="APM"), "embedding": [0.9, 0.435], "profile_key": f"d{i}"})
    swe = {"prior": "Software Engineering / QA / IT", "target_level": "APM", "n_pre": 2, "mba": False, "start_year": 2018}
    design_user = {"prior": "Design / UX", "target_level": "APM", "n_pre": 2, "mba": False, "start_year": 2018}
    swe_set = {row["profile_key"] for row in match_neighbours(pool, [1.0, 0.0], swe)}
    design_set = {row["profile_key"] for row in match_neighbours(pool, [0.92, 0.39], design_user)}
    assert len(swe_set & design_set) < 30
    assert any(key.startswith("d") for key in design_set)
    cohort = rows + [_row(final_entry_move="external", final_primary_prior_function="Business / Consulting / Finance / Operations")
                     for _ in range(80)]
    neighbours = [row for row in rows if row["final_primary_prior_function"].startswith("Software")][:24]
    view = patterns_from_neighbours(neighbours, "APM", cohort, user=swe)
    assert view["available"] is True
    blob = " ".join(item["text"] for item in view["patterns"])
    assert "3 of the" not in blob and "5–9 of the 10" not in blob
    assert "degree" not in blob.lower()
    assert "Software Engineering / QA / IT" in blob
    assert "compared with about" in blob
    assert raw_counts_hidden(view)
    assert "not a guarantee" in view["disclaimer"]
    assert "middle value" not in " ".join(view["takeaways"])
    assert peer_section(None, None, "PM-PM", "Product Manager") == {"available": False}


def _row(**kwargs):
    base = {name: "" for name in COLUMNS}
    base.update({
        "norm_seniority_group": "Product Manager",
        "final_multi_function": "False",
        "final_first_pm_dated": "False",
        "final_has_history": "True",
        "final_dated_role_before_first_pm": "False",
        "final_n_pre_pm_roles": "1",
        "final_n_roles": "2",
        "final_n_pm_roles": "1",
        "norm_edu_has_degree": "True",
        "has_mba": "False",
        "final_entry_move": "external",
        "final_primary_prior_function": "Business / Consulting / Finance / Operations",
    })
    base.update(kwargs)
    return parse_row(base)


def test_labels_line_up_and_unknowns_are_left_out():
    row = _row(norm_seniority_group="Associate Product Manager", final_first_pm_group="VP / CPO",
               norm_edu_has_degree="", final_has_history="False", final_n_pre_pm_roles="0",
               final_entry_move="", final_primary_prior_function="", final_months_to_first_pm_from_earliest_nonintern="")
    assert row["norm_seniority_group"] == "APM"
    assert row["final_first_pm_group"] == "VP/CPO"
    assert row["norm_edu_has_degree"] is None
    assert row["final_n_pre_pm_roles"] is None
    text = cohort_path_text(row)
    assert "APM." in text
    assert "Before PM" not in text
    assert "degree" not in text.lower()
    assert "0 roles" not in text
    assert _row(norm_seniority_group="Junior Product Manager")["norm_seniority_group"] == "Junior PM"
    assert _row(norm_seniority_group="Principal / Staff / Group")["norm_seniority_group"] == "Principal/Staff/Group"


def test_duplicate_rows_keep_distinct_keys_and_backgrounds_match():
    from scripts.load_peer_cohort import surrogate
    row = _row()
    assert surrogate("india-pm-v3", row, 0) != surrogate("india-pm-v3", row, 1)
    from types import SimpleNamespace
    reviewed = SimpleNamespace(review_status="user_confirmed")

    def job(title, role="", family="", year=None, current=False, evidence=""):
        return SimpleNamespace(
            title_raw=title, role_id=role, role_family=family, provenance=reviewed,
            start=SimpleNamespace(year=year), end=SimpleNamespace(year=None, precision="ongoing" if current else "year"),
            is_current_as_stated=current,
            responsibilities=[SimpleNamespace(evidence_text=evidence)] if evidence else [], achievements=[])

    profile = SimpleNamespace(employment_history=[
        job("Product Designer", "PM-PM", "design", 2019, True),
        job("Data Analyst", "", "analytics", 2016),
    ], education=[])
    from app.services.peer_matching.text import user_path_text
    sentence, _level = user_path_text(profile, "PM-APM", "Associate Product Manager")
    assert "Design / UX" in sentence
    assert "Data / Analytics / Research" in sentence
    assert "Current:" not in sentence
    assert "0 roles" not in sentence
    assert "not classified" not in sentence
    sales = SimpleNamespace(employment_history=[job("Sales Manager", "", "sales")], education=[])
    assert "Sales / Marketing / Customer Success" in user_path_text(sales, "PM-PM", "Product Manager")[0]
    founder = SimpleNamespace(employment_history=[job("Founder", "ROLE-ENT", "entrepreneurship")], education=[])
    assert "Entrepreneurship / Founders" in user_path_text(founder, "PM-PM", "Product Manager")[0]
    analyst = SimpleNamespace(employment_history=[job("Business Analyst", "", "analysis")], education=[])
    assert "Business / Consulting / Finance / Operations" in user_path_text(analyst, "PM-PM", "Product Manager")[0]
    data = SimpleNamespace(employment_history=[job("Data Analyst", "", "analysis")], education=[])
    assert "Data / Analytics / Research" in user_path_text(data, "PM-APM", "APM")[0]
    pm = SimpleNamespace(employment_history=[
        job("Associate Product Manager, Payments", "PM-APM", "product_management", 2023, True, "Owned the payments backlog"),
        job("Software Engineer", "ROLE-SWE", "engineering", 2019),
    ], education=[SimpleNamespace(qualification_title_raw="BTech", provenance=reviewed)])
    pm_text, pm_level = user_path_text(pm, "PM-PM", "Product Manager")
    assert pm_level == "Product Manager"
    assert "Current: APM." in pm_text
    assert "1 product role." in pm_text
    assert "years since career start" in pm_text
    assert "Software Engineering / QA / IT" in pm_text
    assert "Scope: Payments." in pm_text
    assert "not classified" not in pm_text


def test_the_level_nudge_is_small_and_patterns_are_the_distinctive_ones():
    sql = (ROOT / "data-foundation/supabase/migrations/20261126230000_trace_peer_rerank_pool.sql").read_text()
    body = sql.split("as $$", 1)[1].split("$$;", 1)[0]
    assert "120" in body and "0.15" not in body and "profile_key" not in body
    assert "candidates" in body
    cohort = [_row(final_entry_move="external", final_primary_prior_function="Business / Consulting / Finance / Operations")
              for _ in range(30)]
    cohort += [_row(final_entry_move="internal", final_primary_prior_function="Design / UX",
                    norm_seniority_group="APM") for _ in range(10)]
    neighbours = [_row(final_entry_move="internal", final_primary_prior_function="Design / UX",
                       norm_seniority_group="APM") for _ in range(20)]
    view = patterns_from_neighbours(neighbours, "APM", cohort)
    texts = [item["text"] for item in view["patterns"]]
    blob = " ".join(texts)
    assert "are at" not in blob
    assert texts[0].find("Design / UX") >= 0 or "inside the employer" in texts[0]
    assert "Design / UX" in blob
    outside = [_row(final_entry_move="external") for _ in range(20)]
    outside += [_row(final_entry_move="internal") for _ in range(6)]
    external_view = patterns_from_neighbours(outside, "Product Manager", outside)
    assert all("inside the same employer" not in line for line in external_view.get("takeaways") or [])
    assert "middle value" not in " ".join(external_view.get("takeaways") or [])
    inside = [_row(final_entry_move="internal") for _ in range(20)]
    inside += [_row(final_entry_move="external") for _ in range(6)]
    inside_view = patterns_from_neighbours(inside, "Product Manager", cohort)
    assert any("inside the same employer" in line for line in inside_view["takeaways"])


def test_a_structured_opening_stays_prose_and_the_limit_ends_on_a_sentence():
    from app.services.ai_assessment.guardrails import trim_at_sentence
    lines = [{"id": "L1", "text": "Shipped the onboarding queue."}]
    reading = apply_guardrails({"opening": {"text": "You shipped the onboarding queue."}}, lines)
    assert reading["opening"].startswith("You shipped")
    assert "{" not in reading["opening"]
    first = " ".join(["alpha"] * 20) + "."
    second = " ".join(["beta"] * 20) + "."
    third = " ".join(["gamma"] * 30) + "."
    assert trim_at_sentence(f"{first} {second} {third}", 45) == f"{first} {second}"
    from app.services.ai_assessment.service import _keep_peer_pattern
    opening = _keep_peer_pattern(
        {"opening": "You shipped the onboarding queue and have not set a success metric."},
        {"available": True, "patterns": [{"id": "PEER-1", "text": "24 of the 40 closest PMs moved internally."}]})
    assert opening["opening"].startswith("You shipped")
    paraphrased = _keep_peer_pattern(
        {"opening": "24 of the 40 people with a similar path moved internally. You shipped the queue."},
        {"available": True, "patterns": [{"id": "PEER-1", "text": "24 of the 40 closest PMs moved internally."}]})
    assert paraphrased["opening"].startswith("24 of the 40")
    zeros = [_row(final_n_pre_pm_roles="0", final_months_to_first_pm_from_earliest_nonintern=str(60 + i)) for i in range(12)]
    real = [_row(final_n_pre_pm_roles=str(2 + (i % 2)), final_months_to_first_pm_from_earliest_nonintern=str(48 + i)) for i in range(12)]
    from app.services.peer_matching.aggregate import stats_from_rows
    assert 0 not in stats_from_rows(zeros + real)["pre"]
    started = [_row(final_first_pm_group="APM", norm_seniority_group="Product Manager", final_n_pm_roles="2") for _ in range(12)]
    started += [_row(final_first_pm_group="APM", norm_seniority_group="APM", final_n_pm_roles="1") for _ in range(4)]
    progressed = patterns_from_neighbours(started, "Product Manager", started, user={"in_pm": True, "first_pm_level": "APM", "target_level": "Product Manager"})
    assert any("started as APM" in item["text"] and "Product Manager or above" in item["text"] for item in progressed["patterns"])
    hidden_start = patterns_from_neighbours(started, "Senior Product Manager", started,
                                           user={"in_pm": True, "first_pm_level": "Product Manager", "target_level": "Senior Product Manager"})
    assert all("started as Product Manager" not in item["text"] for item in hidden_start.get("patterns") or [])
    design_rows = [_row(final_primary_prior_function="Design / UX") for _ in range(2)]
    engineering = [_row(final_primary_prior_function="Software Engineering / QA / IT", final_entry_move="internal") for _ in range(20)]
    base = [_row() for _ in range(20)]
    thin = patterns_from_neighbours(engineering, "APM", design_rows + engineering + base, user={"prior": "Design / UX", "target_level": "APM"})
    assert thin.get("preface", "").startswith("Few PMs in our data came from design")
