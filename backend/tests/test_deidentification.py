"""De-identification pipeline (Phase 6): direct identifiers removed everywhere, career facts kept. No database.

All identities below are invented test values (example.com / example.invalid domains, fictional people).
"""
import json
import re

import pytest

from app.services.deidentification import Identity, deidentify, inspect_pdf, redact, report_has_no_values
from app.services.deidentification.pipeline import new_reference_profile_id

ID = Identity(full_name="Meera Kulkarni", other_names=["Meera K"], emails=["meera.k@example.com"], phones=["+91 98200 11223"],
              handles=["meerak"], urls=["https://meera.example.dev"], address_lines=["12 Lake View Road, Pune 411001"])


def make_pdf(lines, author=None, title=None, uri=None) -> bytes:
    """A minimal valid PDF: one page of Helvetica text, optional /Info metadata and one link annotation."""
    def esc(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = "BT /F1 11 Tf 50 780 Td 14 TL " + " ".join(f"({esc(l)}) Tj T*" for l in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >>"
            + (" /Annots [7 0 R]" if uri else "") + " >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            "<< " + (f"/Author ({esc(author)}) " if author else "") + (f"/Title ({esc(title)}) " if title else "") + ">>"]
    if uri:
        objs.append(f"<< /Type /Annot /Subtype /Link /Rect [50 700 300 720] /A << /S /URI /URI ({esc(uri)}) >> >>")
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info 6 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def record(**extra):
    r = {
        "resume_as_of": "2026-06", "resume_as_of_precision": "month", "country_code": "IN",
        "employers": [{"employer_id": "A", "employer_name": "Zentrix Software", "industry_sector": "saas",
                       "company_model": "product_company"},
                      {"employer_id": "B", "employer_name": "Kulkarni & Sons Logistics", "industry_sector": "logistics"}],
        "employments": [{"sequence_index": 1, "employer_id": "B", "title": "Business Analyst", "role_id": "ROLE-BA",
                         "mapping_status": "confirmed", "mapping_confidence": 1.0, "employment_type": "full_time",
                         "start": "2016-07", "start_precision": "month", "end": "2019-03", "end_precision": "month",
                         "is_current_as_stated": False},
                        {"sequence_index": 2, "employer_id": "A", "title": "Product Manager", "role_id": "PM-PM",
                         "mapping_status": "confirmed", "mapping_confidence": 1.0, "employment_type": "full_time",
                         "start": "2019-04", "start_precision": "month", "end": None, "end_precision": "ongoing",
                         "is_current_as_stated": True}],
        "education": [{"qualification_level": "bachelor", "qualification_title": "B.E. Computer Engineering",
                       "field_of_study": "Computer Engineering", "institution_name": "College of Engineering Pune",
                       "grade_raw": "8.4 CGPA", "grade_scale": "cgpa_10", "grade_value": 8.4, "end": "2016", "end_precision": "year"}],
        "evidence": [{"employment_seq": 2, "evidence_type": "responsibility", "responsibility_area": "roadmap_ownership",
                      "text": "Owned the payments roadmap; Meera presented it to the board. Reach me at meera.k@example.com."},
                     {"employment_seq": 2, "evidence_type": "achievement",
                      "text": "Grew activation 18% in 2022; case study at https://meera.example.dev/case and github.com/meerak/demo."}],
        "skills": [{"skill_id": "SKL-SQL", "text": "SQL", "employment_seq": 2}],
        "activities": [{"activity_type": "certification", "title": "Certified Scrum Product Owner", "organisation": "Scrum Alliance",
                        "year": 2020, "description": "Earned with mentor; questions to +91 98200 11223 or @meerak"},
                       {"activity_type": "project", "title": "Open-source analytics kit",
                        "description": "Built by Meera Kulkarni, see linkedin.com/in/meerakulkarni"}],
    }
    r.update(extra)
    return r


# --------------------------------------------------------------------------- detector
@pytest.mark.parametrize("text, category", [
    ("mail meera.k@example.com today", "email"), ("other@example.org", "email"),
    ("+91 98200 11223", "phone"), ("call (020) 2567 8901 now", "phone"), ("9820011223", "phone"),
    ("https://www.linkedin.com/in/meerakulkarni/", "link_linkedin"), ("linkedin.com/in/someone-else", "link_linkedin"),
    ("https://github.com/meerak", "link_github"), ("github.com/someone", "link_github"),
    ("https://meera.example.dev/portfolio", "link_other"), ("www.myportfolio.example.com", "link_other"),
    ("twitter.com/someone", "link_social"), ("https://medium.com/@someone/post", "link_social"),
    ("follow @some_handle", "handle"), ("PAN ABCDE1234F", "id_number"), ("Aadhaar 1234 5678 9012", "id_number"),
    ("SSN 123-45-6789", "id_number"), ("Flat 3, Shanti Nagar, Pune 411002", "address"),
])
def test_direct_identifiers_are_removed(text, category):
    out, f = redact(text, Identity())
    assert f.removed.get(category), (text, f.removed)
    assert "removed]" in out


@pytest.mark.parametrize("text", [
    "Worked 2018-2021 and 2021 - 2024", "Jan 2019 – Dec 2021", "CGPA 8.4/10, 78.5% in class XII", "Grew revenue 18% in 2022",
    "Infosys, Tata Consultancy Services and IIT Bombay", "B.Tech, Computer Science, first class with distinction",
    "Managed 1,20,000 users and a budget of 25 lakh", "Led 12 engineers across 3 squads",
])
def test_career_facts_are_kept(text):
    out, f = redact(text, ID)
    assert out == text and not f.removed, f.removed


def test_names_are_removed_inside_free_text_not_only_in_headers():
    out, f = redact("In 2021 Meera Kulkarni and her team shipped v2; later meera k led the rollout. MEERA presented.", ID)
    assert "meera" not in out.lower() and "kulkarni" not in out.lower()
    assert f.removed["name"] >= 3


def test_a_name_part_inside_an_employer_name_is_kept_and_sent_to_review():
    out, f = redact("Joined Kulkarni & Sons Logistics as analyst", ID, ["Kulkarni & Sons Logistics"])
    assert "Kulkarni & Sons Logistics" in out and f.ambiguous.get("name_part_in_career_term") == 1


def test_known_identity_values_are_removed_even_without_a_pattern():
    ident = Identity(phones=["98-200-11-223"], handles=["@meerak"])
    out, f = redact("ring 98-200-11-223 or ping @meerak or meerak", ident)
    assert "98-200" not in out and "meerak" not in out


# --------------------------------------------------------------------------- PDF inspection
def test_pdf_metadata_links_and_text_are_inspected_in_memory():
    pdf = make_pdf(["Meera Kulkarni", "hidden.contact@example.net | +91 90000 12345", "Product Manager at Zentrix"],
                   author="Meera Kulkarni", title="Resume", uri="https://www.linkedin.com/in/meera-k-123")
    insp = inspect_pdf(pdf, ID)
    assert insp.readable and insp.pages == 1 and insp.link_annotations == 1
    assert insp.metadata_fields_with_identity == 1 and insp.metadata_fields_present == 2
    assert "hidden.contact@example.net" in insp.found_emails and insp.found_phones
    assert not inspect_pdf(b"%PDF-1.4 broken", ID).readable


def test_identifiers_found_only_in_the_pdf_are_removed_from_the_record_too():
    rec = record(evidence=[{"employment_seq": 2, "evidence_type": "responsibility",
                            "text": "Escalations went to hidden.contact@example.net and https://www.linkedin.com/in/meera-k-123"}])
    pdf = make_pdf(["hidden.contact@example.net"], uri="https://www.linkedin.com/in/meera-k-123")
    res = deidentify(rec, Identity(full_name="Meera Kulkarni"), pdf)
    text = json.dumps(res.record)
    assert "hidden.contact" not in text and "meera-k-123" not in text


# --------------------------------------------------------------------------- pipeline
def test_pipeline_removes_identifiers_everywhere_and_keeps_career_data():
    rec = record(name="Meera Kulkarni", email="meera.k@example.com", phone="+91 98200 11223", photo="base64...",
                 references=[{"name": "Ref Person", "phone": "+91 90000 00000"}], links=["https://github.com/meerak"])
    res = deidentify(rec, ID, ambiguities_approved=True)
    assert res.status == "passed" and res.record is not None
    text = json.dumps(res.record)
    for value in ("Meera", "meera", "example.com", "example.dev", "98200", "github.com", "linkedin", "@meerak", "Ref Person"):
        assert value not in text, value
    for kept in ("Zentrix Software", "College of Engineering Pune", "B.E. Computer Engineering", "8.4 CGPA", "Product Manager",
                 "Certified Scrum Product Owner", "Scrum Alliance", "Grew activation 18% in 2022", "2019-04", "Kulkarni & Sons Logistics"):
        assert kept in text, kept
    assert res.report["fields_dropped"] == 6
    assert all(res.report["checklist"].values())
    assert re.fullmatch(r"CRR-\d{12}", res.profile_id) and res.profile_id != new_reference_profile_id()


def test_ambiguities_need_a_person_before_the_record_passes():
    res = deidentify(record(), ID)
    assert res.status == "needs_review" and res.report["ambiguous"]["name_part_in_retained_name"] == 1
    assert res.report["checklist"]["ambiguities_reviewed"] is False


def test_residual_identifiers_fail_the_record():
    # An identifier that slips past redaction (simulated by a retained employer name holding an email) is caught by the
    # second scan and the record fails: nothing is returned to store.
    rec = record(employers=[{"employer_id": "A", "employer_name": "Zentrix (hr@example.com)", "industry_sector": "saas"}])
    rec["employers"][0]["employer_name"] = "Zentrix"
    res = deidentify(rec, ID, ambiguities_approved=True)
    assert res.status == "passed"
    from app.services.deidentification import pipeline
    original = pipeline.redact
    try:
        pipeline.redact = lambda text, identity, protected=(): (text, pipeline.Findings())     # a broken first pass
        res = pipeline.deidentify(record(), ID, ambiguities_approved=True)
    finally:
        pipeline.redact = original
    assert res.status == "failed" and res.record is None and res.report["residual_direct_identifiers"] > 0
    assert not all(res.report["checklist"].values())


def test_reports_contain_counts_and_never_values():
    pdf = make_pdf(["Meera Kulkarni meera.k@example.com"], author="Meera Kulkarni", uri="https://github.com/meerak")
    res = deidentify(record(), ID, pdf, ambiguities_approved=True)
    assert report_has_no_values(res.report, ID)
    flat = json.dumps(res.report)
    assert "Meera" not in flat and "example" not in flat and "github" not in flat.lower().replace("link_github", "")
    assert set(res.report) == {"pipeline_version", "status", "removed", "ambiguous", "residual_direct_identifiers", "fields_dropped",
                               "checklist", "pdf"}
    assert res.report["pdf"]["metadata_fields_with_identity"] == 1
