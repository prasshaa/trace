"""Guardrails, parser wraps, sharing copy, and the demo narrative contract. No database and no network."""
import json

import pytest

from app.api.ratelimit import client_ip
from app.config import Settings
from app.services.ai_assessment.client import OpenAICompatible, _json_object, client_from_settings
from app.services.ai_assessment.guardrails import apply_guardrails, collaboration_as_ownership, invented_number, negation_as_positive
from app.services.capability.interpret import interpret
from app.services.resume_import import DateInput, PlannedEducation, education_contract
from app.services.resume_parsing.document import Document, Line
from app.services.resume_parsing.parser import experience, parse_document
from app.services.transition.service import support_depth
from evaluation.ai_demo.personas import PERSONAS, lines_for


class _Req:
    def __init__(self, headers):
        self.headers = headers
        self.client = type("C", (), {"host": "10.0.0.8"})()


def test_wrapped_bullet_keeps_the_figure_and_the_negation():
    rows = [
        Line(text="Software Engineer | Northwind Labs | Jan 2021 – Present", index=0, x0=72, x1=460, top=80, bottom=92,
             col_left=72, col_right=540),
        Line(text="Raised onboarding completion from", bullet=True, index=1, x0=72, x1=400, top=100, bottom=112,
             col_left=72, col_right=540),
        Line(text="91% to 96%. Did not own the roadmap.", index=2, x0=90, x1=420, top=116, bottom=128,
             col_left=72, col_right=540),
        Line(text="Product Manager | Lumen Health | Feb 2023 – Present", index=3, x0=72, x1=460, top=150, bottom=162,
             col_left=72, col_right=540),
        Line(text="Owned the claims roadmap.", bullet=True, index=4, x0=72, x1=300, top=170, bottom=182,
             col_left=72, col_right=540),
    ]
    jobs = experience(rows)
    assert [j.title for j in jobs] == ["Software Engineer", "Product Manager"]
    first = " ".join(b.text for b in jobs[0].bullets) + " " + (jobs[0].description or "")
    assert "91% to 96%" in first and "did not own the roadmap" in first.lower()
    assert "91%" not in " ".join(b.text for b in jobs[1].bullets)


def test_a_collaboration_sentence_does_not_become_the_next_role():
    rows = [
        Line(text="QA Engineer | Brightline | Mar 2019 – Dec 2021", index=0, x0=72, x1=400, top=40, bottom=52),
        Line(text="Wrote regression suites.", bullet=True, index=1, x0=72, x1=300, top=60, bottom=72),
        Line(text="Partnered with the Product Manager on acceptance criteria.", index=2, x0=72, x1=460, top=80, bottom=92),
        Line(text="Filed the weekly defect note.", bullet=True, index=3, x0=72, x1=320, top=100, bottom=112),
    ]
    jobs = experience(rows)
    assert len(jobs) == 1
    blob = " ".join(b.text for b in jobs[0].bullets) + " " + (jobs[0].description or "")
    assert "Partnered with the Product Manager" in blob
    assert "Filed the weekly defect note." in blob


def test_year_only_education_is_accepted():
    item = PlannedEducation(key="D1", qualification="B.Tech", institution="Kestrel Valley Institute",
                            field_of_study="Computer Science",
                            start=DateInput(year=2016, precision="year"), end=DateInput(year=2020, precision="year"),
                            studying=False)
    assert education_contract([item]) == []


def test_collaboration_is_not_ownership_and_counts_are_not_outcomes():
    worked = interpret("Worked with the product manager on the roadmap.")
    partnered = interpret("Partnered with design on the product roadmap.")
    supported = interpret("Supported the product roadmap.")
    merchants = interpret("Supported 12 merchants during the pilot.")
    negated = interpret("Didn’t own the roadmap.")
    assert "roadmap_ownership" not in worked.clear
    assert "roadmap_ownership" not in partnered.clear
    assert "roadmap_ownership" not in supported.clear
    assert "roadmap_ownership" not in supported.partial
    assert "outcome_ownership" not in merchants.clear and "outcome_ownership" not in merchants.partial
    assert "roadmap_ownership" not in negated.clear and "roadmap_ownership" not in negated.partial


def test_a_later_denial_removes_the_roadmap_claim():
    reading = interpret("Built the product roadmap for the team but did not own the roadmap.")
    assert "roadmap_ownership" not in reading.clear


def test_guardrails_drop_invented_numbers_negation_and_unowned_collaboration():
    lines = [{"id": "L1", "text": "Worked with the product manager on the roadmap. Did not own the roadmap.", "job": "SWE, Northwind"},
             {"id": "L2", "text": "Raised onboarding completion from 91% to 96%.", "job": "SWE, Northwind"}]
    raw = {"opening": "They improved completion by 40%.",
           "proves": [
               {"text": "They owned the roadmap.", "evidence_ids": ["L1"], "why": "the line says so"},
               {"text": "Onboarding completion moved from 91% to 96%.", "evidence_ids": ["L2"], "why": "the line states both figures"},
               {"text": "They reached 99% completion.", "evidence_ids": ["L2"], "why": "guess"},
               {"text": "A claim with no line.", "evidence_ids": [], "why": ""},
           ],
           "missing": [{"text": "No evidence of coaching another product manager.", "why": "the lines do not mention it"},
                       {"text": "They missed a 50% target.", "why": "invented"}],
           "questions": [{"text": "Did you choose the order of that work?", "evidence_id": "L1"},
                         {"text": "What about the secret 12%?", "evidence_id": "nope"}],
           "steps": [{"text": "Write up the onboarding change from 91% to 96% in your own words.", "evidence_ids": ["L2"], "why": "it is already in the line"}]}
    out = apply_guardrails(raw, lines)
    assert out["opening"] == ""
    assert [p["evidence_ids"] for p in out["proves"]] == [["L2"]]
    assert "50%" not in json.dumps(out["missing"])
    assert out["questions"] == [{"text": "Did you choose the order of that work?", "evidence_id": "L1"}]
    assert out["steps"][0]["evidence_ids"] == ["L2"]
    assert collaboration_as_ownership("They owned the roadmap.", lines[0]["text"])
    assert negation_as_positive("They owned the roadmap.", lines[0]["text"])
    assert invented_number("They reached 99%.", lines[1]["text"])


def test_support_depth_matches_the_three_moves_and_limits_the_rest():
    assert support_depth("ROLE-SWE", "PM-APM") == "deep"
    assert support_depth("ROLE-QA", "PM-PM") == "deep"
    assert support_depth("PM-APM", "PM-PM") == "deep"
    assert support_depth("PM-PM", "PM-SPM") == "deep"
    assert support_depth("PM-PM", "PM-PM") == "limited"
    assert support_depth("PM-PM", "PM-APM") == "limited"
    assert support_depth("ROLE-SWE", "PM-SPM") == "limited"
    assert support_depth("PM-APM", "PM-SPM") == "limited"


def test_client_ip_uses_fly_or_the_appended_hop_and_never_the_leftmost():
    assert client_ip(_Req({"fly-client-ip": "203.0.113.9", "x-forwarded-for": "1.2.3.4"})) == "203.0.113.9"
    assert client_ip(_Req({"x-forwarded-for": "1.2.3.4, 198.51.100.20"})) == "10.0.0.8"
    monkey = pytest.MonkeyPatch()
    monkey.setenv("TRACE_TRUST_APPENDED_FORWARDED", "1")
    try:
        assert client_ip(_Req({"x-forwarded-for": "1.2.3.4, 198.51.100.20"})) == "198.51.100.20"
    finally:
        monkey.undo()


def test_the_model_client_is_absent_without_a_key_and_json_fences_parse():
    assert client_from_settings(Settings(trace_ai_assessment=False)) is None
    assert client_from_settings(Settings(trace_ai_assessment=True, trace_llm_model="demo-model")) is None
    assert _json_object('```json\n{"opening": "ok"}\n```') == {"opening": "ok"}
    client = OpenAICompatible(provider="openai", base_url="https://example.invalid/v1", model="demo-model", api_key="secret-not-logged")
    assert client.timeout == 120
    assert "secret-not-logged" not in repr(client)
    assert "secret-not-logged" not in client.base_url
    assert Settings().trace_llm_timeout == 120


def test_guardrail_probes_from_the_review():
    collab = "Worked with the product manager on the roadmap. Did not own the roadmap."
    metric = "Supported 12 merchants during a pilot. I did not set the success metric."
    onboard = "Raised onboarding completion from 91% to 96%."
    owned = "Built and shipped a scheduling tool and owned its short roadmap."
    lines = [{"id": "F01-L1", "text": collab}, {"id": "F01-L2", "text": metric}, {"id": "F01-L3", "text": onboard},
             {"id": "F08-L1", "text": owned}]
    by = {row["id"]: row for row in lines}

    def one(text, cited_id, kind="strength"):
        kept, reason = keep_claim({"text": text, "evidence_ids": [cited_id], "why": ""}, by, require_citation=True, kind=kind)
        return kept, reason

    from app.services.ai_assessment.guardrails import keep_claim
    for word in ("Owned the roadmap.", "Led the roadmap.", "Drove the roadmap.", "Ran the roadmap."):
        kept, reason = one(word, "F01-L1")
        assert kept is None and reason in {"negation", "collaboration_as_ownership"}
    for word in ("Managed the pilot and set its success metric.", "Set the pilot's success metric.", "Headed the merchant pilot."):
        kept, reason = one(word, "F01-L2")
        assert kept is None and reason in {"negation", "collaboration_as_ownership"}, word
    assert one("Doubled merchant retention in the pilot.", "F01-L2")[0] is None
    assert one("Improved onboarding by five percentage points.", "F01-L3")[0] is None
    assert one("Improved onboarding by 5 points.", "F01-L3")[0] is None
    kept, reason = keep_claim(
        {"text": "You owned the short roadmap.", "evidence_ids": ["F08-L1"], "why": "F08-L1 shows you owned the short roadmap."},
        by, require_citation=True, kind="strength")
    assert kept and kept["evidence_ids"] == ["F08-L1"] and "F08-L1" not in kept["why"]
    uid = "123e4567-e89b-12d3-a456-426614174000"
    kept, reason = keep_claim(
        {"text": "You owned the short roadmap.", "evidence_ids": [uid], "why": f"{uid} shows the roadmap."},
        {uid: {"id": uid, "text": owned}}, require_citation=True, kind="strength")
    assert kept and uid not in kept["text"] and uid not in kept["why"]
    kept, reason = keep_claim(
        {"text": "Ask to own the order of work for one slice of the checklist.", "evidence_ids": ["F01-L1"], "why": "A next step, not a claim of ownership."},
        by, require_citation=False, kind="step")
    assert kept and reason is None
    kept, reason = keep_claim(
        {"text": "In a 30-minute session, rewrite 3-4 items on the checklist you already run.", "evidence_ids": ["F01-L3"], "why": ""},
        by, require_citation=False, kind="step")
    assert kept and reason is None


def test_short_profiles_keep_every_excerpt_and_long_ones_keep_a_denial():
    from app.services.ai_assessment.rubric import requirements_for
    from app.services.ai_assessment.select import select_lines
    short = [{"id": f"S{i}", "text": f"Did task {i}.", "job": "Role"} for i in range(3)]
    shown, info = select_lines(short, requirements_for("PM-APM"), None, None)
    assert info["mode"] == "all_lines" and len(shown) == 3
    long = [{"id": f"L{i}", "text": f"Updated an internal document with the team on day {i}.", "job": "Role"} for i in range(12)]
    long[4] = {"id": "Lneg", "text": "Worked with the product manager. Did not own the roadmap.", "job": "Role"}
    long[9] = {"id": "Lmet", "text": "Raised completion from 70% to 81%.", "job": "Role"}
    shown, info = select_lines(long, requirements_for("PM-PM"), None, None)
    ids = {row["id"] for row in shown}
    assert "Lneg" in ids and "Lmet" in ids
    assert "L2" not in requirements_for("PM-APM") and "L2" not in {r["requirement_key"] for r in requirements_for("PM-PM")}
    assert "L2" in {r["requirement_key"] for r in requirements_for("PM-SPM")}
    assert "C2" not in {r["requirement_key"] for r in requirements_for("PM-APM")}
    assert "C2" in {r["requirement_key"] for r in requirements_for("PM-PM")}


def test_twenty_fictional_personas_cover_the_three_moves():
    assert len(PERSONAS) == 25
    long = [p for p in PERSONAS if len(lines_for(p)) >= 10]
    assert len(long) >= 5
    assert sum(1 for p in long if p["move"].startswith("non_pm")) >= 2
    moves = {p["move"] for p in PERSONAS}
    assert moves == {"non_pm_to_apm", "non_pm_to_pm", "apm_to_pm", "pm_to_spm"}
    assert all(p["fictional"] and "not a real person" in p["header"] for p in PERSONAS)
    assert all(p["city"] for p in PERSONAS)
    for persona in PERSONAS:
        lines = lines_for(persona)
        assert lines and all(line["id"].startswith(persona["id"]) for line in lines)
    doc = parse_document(Document("docx", 1, "docx", [
        Line(text="FICTIONAL RESUME — Asha Menon is not a real person.", index=0),
        Line(text="Professional Experience", index=1, bold=True),
        Line(text="Software Engineer | Northwind Labs | Jan 2022 – Present", index=2),
        Line(text="Raised onboarding completion from 91% to 96%.", bullet=True, index=3),
        Line(text="Education", index=4, bold=True),
        Line(text="B.Tech, Computer Science, Kestrel Valley Institute, 2016 – 2020", index=5),
    ]))
    assert doc.work and doc.education
    assert doc.education[0].start.precision == "year"
