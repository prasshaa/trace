"""Resume parser v2: unit, regression and evaluation-gate tests (no database).

Regression cases come from the failures observed in the end-to-end product test (fragments such as "F1)" and
"Confidence-Based" accepted as skills, paragraphs split into fragments) and from failures found while building the
evaluation (unmapped bullet glyphs, titles read as employers, two-column interleaving, abbreviated designations). All
inputs are fictional; nothing here is a real resume.
"""
import io
import socket

import pytest

from app.services.resume_parsing import DocumentRejected, parse_bytes, parse_upload
from app.services.resume_parsing import skills as sk
from app.services.resume_parsing.document import Document, Line
from app.services.resume_parsing.parser import experience, parse_date, parse_document
from evaluation.resume_parser.corpus import P
from evaluation.resume_parser.evaluate import THRESHOLDS, passes, run
from evaluation.resume_parser.holdout import H
from evaluation.resume_parser.render import render


def lines(*texts, bullet_prefix="• "):
    out = []
    for i, t in enumerate(texts):
        b = t.startswith(bullet_prefix)
        out.append(Line(text=t[len(bullet_prefix):] if b else t, bullet=b, index=i))
    return out


# ------------------------------------------------------------------------------------------------- skills
@pytest.mark.parametrize("item", ["F1)", "(precision", "Confidence-Based", "Data-driven", "increased revenue by 20%", "etc",
                                  "https://example.invalid", "and", "Metrics", "led a team of five engineers"])
def test_fragments_metrics_and_sentences_are_never_skills(item):
    name, _ = sk.from_list_item(item)
    assert name is None


def test_brackets_keep_a_skills_item_whole():
    assert sk.split_items("Metrics (precision, recall, F1), Python; SQL") == ["Metrics (precision, recall, F1)", "Python", "SQL"]
    assert sk.from_list_item("Python (Pandas, NumPy)") == ("Python", True)


def test_synonyms_collapse_to_one_name():
    assert {sk.canonical(x) for x in ("JS", "javascript", "JavaScript")} == {"JavaScript"}
    assert sk.canonical("Postgres") == "PostgreSQL" and sk.canonical("A/B tests") == "A/B testing"
    assert sk.canonical("go") is None and sk.canonical("Go") == "Go"                 # the word "go" isn't the language


def test_sentences_give_only_vocabulary_skills():
    found = sk.from_sentence("Introduced confidence-based gating and ran A/B tests in Python with the data team.")
    assert found == ["A/B testing", "Python"] or set(found) == {"A/B testing", "Python"}
    assert sk.from_sentence("We will go to the client site.") == []


# ------------------------------------------------------------------------------------------------- dates
@pytest.mark.parametrize("token,value,precision", [
    ("Jan 2020", "2020-01", "month"), ("January 2020", "2020-01", "month"), ("Apr'19", "2019-04", "month"),
    ("Apr-2019", "2019-04", "month"), ("04/2019", "2019-04", "month"), ("2019-04", "2019-04", "month"),
    ("04.2019", "2019-04", "month"), ("2019", "2019", "year"), ("Present", None, "ongoing"), ("Till date", None, "ongoing"),
    ("13/2019", None, "unknown"),
])
def test_date_formats(token, value, precision):
    d = parse_date(token)
    assert (d.value, d.precision) == (value, precision)


# --------------------------------------------------------------------------------------------- segmentation
def test_promotions_under_one_employer_stay_one_employer():
    jobs = experience(lines("Lumen Health Technologies, Hyderabad", "Product Manager | Feb 2022 – Present", "• Owned the roadmap.",
                            "Associate Product Manager | Jul 2020 – Jan 2022", "• Analysed funnels."))
    assert [(j.title, j.employer) for j in jobs] == [("Product Manager", "Lumen Health Technologies"),
                                                     ("Associate Product Manager", "Lumen Health Technologies")]
    assert jobs[0].group == jobs[1].group


def test_a_job_without_dates_is_kept_but_flagged_never_given_invented_dates():
    jobs = experience(lines("Software Engineer | Example Labs", "• Built things."))
    assert jobs[0].title == "Software Engineer" and jobs[0].start.precision == "unknown" and "start" in jobs[0].review


def test_title_words_that_look_like_company_words_stay_titles():
    jobs = experience(lines("Senior Software Engineer | Nimbus Payments Pvt Ltd | Apr 2021 – Present", "• Did work."))
    assert (jobs[0].title, jobs[0].employer) == ("Senior Software Engineer", "Nimbus Payments Pvt Ltd")
    jobs = experience(lines("Operations Mgr | Banyan Foods Ltd | 03/2021 – Present", "• Ran things."))
    assert jobs[0].title == "Operations Mgr"


def test_wrapped_pdf_bullets_are_joined_into_one_bullet():
    p = dict(P[8])                                                       # P09 has a bullet that wraps over three lines
    d = parse_bytes(render(p, "pdf_classic")).draft
    long = next(b for b in d.work[0].bullets if b.text.startswith("Launched dynamic pricing"))
    assert long.text.endswith("two pilot cities.") and len(d.work[0].bullets) == 3


def test_unmapped_glyph_bullets_are_recognised():
    d = parse_bytes(render(P[0], "pdf_classic")).draft                    # reportlab's bullet comes out as "(cid:127)"
    assert all("(cid:" not in b.text for j in d.work for b in j.bullets) and len(d.work[0].bullets) == 3


def test_two_column_pages_are_read_column_by_column():
    d = parse_bytes(render(P[2], "pdf_two_column")).draft
    assert d.layout == "two_column" and [j.title for j in d.work] == ["Product Manager", "Associate Product Manager", "Product Analyst"]
    assert {"SQL", "Figma"} <= {s.name for s in d.skills}


def test_the_observed_product_test_failures_do_not_recur():
    for layout in ("pdf_classic", "pdf_two_column", "docx_classic"):
        names = {s.name for s in parse_bytes(render(P[3], layout)).draft.skills} | {s.name for s in parse_bytes(render(P[2], layout)).draft.skills}
        assert not names & {"F1)", "Confidence-Based", "recall", "(precision"}


def test_nothing_is_fabricated_from_an_empty_or_unrelated_document():
    d = parse_document(Document("docx", 1, "docx", lines("Grocery list", "Milk, eggs, bread")))
    assert d.work == [] and d.education == [] and "no_work_experience_found" in d.warnings


# ------------------------------------------------------------------------------------------------ documents
@pytest.mark.parametrize("data,code", [(b"not a document", "unsupported_format"), (b"%PDF-1.4 broken", "unreadable"),
                                       (b"PK\x03\x04broken zip", "unreadable")])
def test_malformed_files_are_rejected_with_a_reason(data, code):
    with pytest.raises(DocumentRejected) as e:
        parse_bytes(data)
    assert e.value.code == code


def test_parsing_makes_no_network_connection(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network use during parsing")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    for layout in ("pdf_classic", "docx_table"):
        assert parse_bytes(render(P[1], layout)).draft.work


def test_parsing_writes_no_files(monkeypatch):
    import builtins
    real_open = builtins.open

    def guarded(file, mode="r", *a, **k):
        if any(c in mode for c in "wax+"):
            raise AssertionError(f"file write during parsing: {file}")
        return real_open(file, mode, *a, **k)
    data = render(P[1], "pdf_two_column"), render(P[1], "docx_classic")
    monkeypatch.setattr(builtins, "open", guarded)
    monkeypatch.setattr(io, "open", guarded)
    for d in data:
        assert parse_bytes(d).draft.work


def test_the_isolated_parser_returns_the_same_result_and_enforces_a_time_limit():
    data = render(P[0], "docx_classic")
    assert parse_upload(data).draft == parse_bytes(data).draft
    with pytest.raises(DocumentRejected) as e:
        parse_upload(render(P[0], "pdf_classic"), timeout=0.001)
    assert e.value.code == "timeout"


# ------------------------------------------------------------------------------------------------ evaluation gate
def test_the_parser_meets_every_release_threshold_on_the_controlled_corpus():
    overall, by_layout, _ = run()
    failing = {k: v for k, v in overall.items() if not passes(k, v)}
    assert not failing, failing
    for layout, metrics in by_layout.items():                            # no layout may hide behind the average
        assert metrics["fabrication_rate_max"] == 0 and metrics["forbidden_skills_accepted_max"] == 0, layout
        assert metrics["employer_precision"] >= THRESHOLDS["employer_precision"], layout


def test_the_held_out_set_meets_the_release_thresholds():
    from collections import defaultdict
    from evaluation.resume_parser.evaluate import evaluate_doc, summarise
    from evaluation.resume_parser.render import LAYOUTS
    tot = defaultdict(int)
    for p in H:
        for layout in LAYOUTS:
            r, _ = evaluate_doc(p, layout)
            for k, v in r.items():
                tot[k] += v
    s = summarise(tot)
    assert all(passes(k, v) for k, v in s.items()), {k: v for k, v in s.items() if not passes(k, v)}


# ------------------------------------------------------------------------------- parser 2.1: the 2026-11-20 review
# Structural regressions from a real review (its content is not used: fictional fixtures in evaluation/regression.py).
def test_shared_year_ranges_give_both_months_in_that_year():
    from app.services.resume_parsing.parser import RANGE, range_dates
    s, e = range_dates(RANGE.search("Tallyfox, Associate Product Manager, Jan–Mar 2021. Ran a pilot."))
    assert (s.value, e.value) == ("2021-01", "2021-03")


def test_an_earlier_experience_heading_starts_new_roles_not_a_description():
    from evaluation.resume_parser.regression import R
    for layout in ("pdf_compact", "docx_compact"):
        d = parse_bytes(render(R[0], layout)).draft
        jobs = [(j.title, j.employer, j.start.value) for j in d.work]
        assert ("Associate Product Manager", "Tallyfox", "2021-01") in jobs, (layout, jobs)
        vexa = next(j for j in d.work if j.employer == "Vexa")
        assert "Tallyfox" not in (vexa.description or "") and "EARLIER" not in (vexa.description or "").upper()


def test_a_one_paragraph_entry_keeps_the_start_of_its_description():
    jobs = experience(lines("Brightline Technology Solutions, Software Test Engineer, Aug 2017 – Dec 2020. Wrote and ran regression",
                            "suites for an insurance claims portal."))
    assert jobs[0].description.startswith("Wrote and ran regression suites")


def test_a_tagline_describing_the_business_is_never_taken_as_the_employer():
    jobs = experience(lines("Vexa — Payments Platform & Fintech Company", "Global Product Manager | Apr 2021 – May 2025", "• Did work."))
    assert jobs[0].employer == "Vexa"
    jobs = experience(lines("Payments Platform & Fintech Company", "Global Product Manager | Apr 2021 – May 2025", "• Did work."))
    assert jobs[0].employer is None and "employer" in jobs[0].review                # left for the user, never substituted
    assert experience(lines("Analyst | Brightline Technology Solutions | Aug 2017 – Dec 2020"))[0].employer == "Brightline Technology Solutions"


def test_overlapping_roles_are_all_kept():
    from evaluation.resume_parser.regression import R
    d = parse_bytes(render(R[0], "pdf_compact")).draft
    titles = {j.title for j in d.work}
    assert {"Software Test Engineer", "Co-Founder & Writer"} <= titles               # 2017–2018 overlap: both are roles


def test_text_that_cannot_be_placed_is_shown_never_dropped():
    from app.services.resume_parsing.parser import experience_with_issues
    jobs, issues = experience_with_issues(lines("Built a claims product for hospitals.", "Product Manager | Acme Health | Mar 2026 – Present",
                                                "• Owns the roadmap."))
    assert [i.kind for i in issues] == ["unattached_text"] and "claims product" in issues[0].text
    jobs, issues = experience_with_issues(lines("Mar 2019 – Jun 2020", "• Ran the night shift."))
    assert jobs == [] and {i.kind for i in issues} >= {"possible_role"}
    jobs, issues = experience_with_issues(lines("Acme Health | Mar 2019 – Jun 2020", "• Ran things."))
    # Phase 4: an untitled role stays a ROLE (title left for the user, source line kept), with its bullets
    assert jobs[0].title is None and "title" in jobs[0].review and issues == []
    assert jobs[0].source == "Acme Health | Mar 2019 – Jun 2020" and [b.text for b in jobs[0].bullets] == ["Ran things."]
    from app.services.resume_parsing.parser import ResumeDraft, to_record
    rec = to_record(ResumeDraft(format="docx", layout="docx", pages=1, work=jobs, unresolved=issues))
    assert rec["employments"][0]["title"] is None and rec["evidence"][0]["text"] == "Ran things."


def test_bold_runs_do_not_split_punctuation_and_link_glyphs_are_removed():
    from app.services.resume_parsing.document import _line
    w = lambda t, x0, x1: {"text": t, "x0": x0, "x1": x1, "top": 10, "bottom": 20, "size": 10, "fontname": "Helvetica"}  # noqa: E731
    line = _line([w("Tallyfox", 50, 110), w(",", 110, 113), w("Associate", 116, 160), w("↗", 162, 170)], 0, 0)
    assert line.text == "Tallyfox, Associate"


def test_curated_regression_fixtures_are_extracted_completely_in_every_layout():
    from evaluation.resume_parser.evaluate import REGRESSION_THRESHOLDS, run_split
    from evaluation.resume_parser.regression import R
    overall, by_layout, docs = run_split(R)
    assert not docs, docs                                                           # every document, not an average
    assert all(passes(k, overall[k], REGRESSION_THRESHOLDS) for k in REGRESSION_THRESHOLDS), overall


def test_the_development_and_validation_splits_meet_the_structure_thresholds():
    from evaluation.resume_parser.evaluate import STRUCTURE_THRESHOLDS, run_split
    from evaluation.resume_parser.validation import V
    for split in (P, V):
        overall, _, docs = run_split(split)
        failing = {k: overall[k] for k in STRUCTURE_THRESHOLDS if not passes(k, overall[k], STRUCTURE_THRESHOLDS)}
        assert not failing and not docs, (failing, docs)


# ------------------------------------------------------------------------- Phase 4 (2026-11-22): employer-first headings
def test_a_link_label_after_the_title_is_not_part_of_it_and_the_registered_name_is_kept():
    jobs = experience(lines("Novaline (Novaline Care Labs), Clinical AI Product Manager Company | Nov 2025 – Aug 2026",
                            "Agent platform for clinic billing teams.", "• Cut manual claim checks by 9 hours a day."))
    j = jobs[0]
    assert (j.title, j.employer) == ("Clinical AI Product Manager", "Novaline (Novaline Care Labs)")
    assert j.description == "Agent platform for clinic billing teams." and len(j.bullets) == 1


def test_a_bracketed_link_label_is_removed_from_text():
    from app.services.resume_parsing.document import clean_text
    assert clean_text("and wrote the business case. (Case Study ↗)") == "and wrote the business case."
    assert clean_text("Pallet, PM, Fleet Planning & Logistics Software Company ↗") == "Pallet, PM, Fleet Planning & Logistics Software Company"


def test_employment_records_are_segmented_before_their_fields_are_read():
    from app.services.resume_parsing.parser import paragraphs, segment_experience
    segs, orphans, suspects = segment_experience(paragraphs(lines(
        "Built claims tooling.", "Novaline, Clinical AI Product Manager | Nov 2025 – Aug 2026", "• One.",
        "Pallet, Routing Product Manager | Jun 2021 – Oct 2025", "• Two.", "• Three.")))
    assert [len(s.body) for s in segs] == [1, 2] and len(orphans) == 1 and suspects == []


def test_unplaced_text_carries_only_what_it_supports():
    from app.services.resume_parsing.parser import experience_with_issues
    _, issues = experience_with_issues(lines("Mar 2019 – Jun 2020", "• Ran the night shift."))
    role = next(i for i in issues if i.kind == "possible_role")
    assert (role.start.value, role.end.value, role.title, role.employer) == ("2019-03", "2020-06", None, None)
    text = next(i for i in issues if i.kind == "unattached_text")
    assert text.description == "Ran the night shift."


def test_an_institution_without_a_keyword_is_read_from_its_position_and_flagged():
    from app.services.resume_parsing.parser import education
    e = education(lines("B.Tech, Mechanical Engineering, Kestrel Valley, 2014 – 2018, 8.1 CGPA"))[0]
    assert (e.qualification, e.field_of_study, e.institution) == ("B.Tech", "Mechanical Engineering", "Kestrel Valley")
    assert "institution" in e.review and e.end.value == "2018"


def test_a_wrapped_certification_list_is_split_on_its_separators():
    from app.services.resume_parsing.parser import activities
    acts = activities(lines("Applied ML for PMs, Fictional Product Academy (02/2025 – 05/2025) | Healthcare Data Foundations,",
                            "Example Learning Co (03/2024) | Product Leadership Fellowship, Fictional Fellows (01/2020 – 06/2021)"),
                      "certification")
    assert [(a.title, a.organisation, a.year) for a in acts] == [
        ("Applied ML for PMs", "Fictional Product Academy", 2025), ("Healthcare Data Foundations", "Example Learning Co", 2024),
        ("Product Leadership Fellowship", "Fictional Fellows", 2021)]


def test_a_tagline_ending_in_a_business_noun_is_not_an_employer_but_a_real_name_is():
    from app.services.resume_parsing.parser import is_descriptor
    assert is_descriptor("Design Systems & Developer Tools Startup") and not is_descriptor("Ford Motor Company")


def test_former_held_out_set_now_meets_the_structure_thresholds_as_development():
    from evaluation.resume_parser.evaluate import STRUCTURE_THRESHOLDS, run_split
    from evaluation.resume_parser.holdout2 import H2
    overall, _, docs = run_split(H2)
    assert not docs and all(passes(k, overall[k], STRUCTURE_THRESHOLDS) for k in STRUCTURE_THRESHOLDS)
