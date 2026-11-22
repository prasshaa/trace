"""Render a corpus persona into resume documents of different layouts (PDF via reportlab, DOCX via python-docx).

Layouts: pdf_classic (one column, dates right-aligned on the title row), pdf_grouped (an employer heading with several
dated roles beneath it), pdf_two_column (a sidebar with skills, education and certifications beside the experience
column), docx_classic (heading styles and list bullets), docx_table (a two-column layout table). Long bullets wrap
across lines exactly as a word processor would. All content comes from the persona; nothing is real.

Added for parser 2.1 (COMPACT_LAYOUTS: pdf_compact, docx_compact), the structure behind the 2026-11-20 review failure:
an employer line with a tagline describing the business ("Vexa — Payments Platform & Fintech Company"); a separate
"Earlier Experience" heading whose entries are one paragraph each with the employer in bold ("Tallyfox, Associate
Product Manager, Jan–Mar 2021. Ran ..."); a "Leadership & Recognition" section; "Education & Certifications" in one
section; and a summary that mentions earlier employers (which must never become records).
"""
from __future__ import annotations

import io
from itertools import groupby

MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
ONGOING = {"mon_yyyy": "Present", "mm_yyyy": "Present", "mon_apos": "Till date", "month_yyyy": "Present", "yyyy_mm": "Current"}
LAYOUTS = ["pdf_classic", "pdf_grouped", "pdf_two_column", "docx_classic", "docx_table"]
COMPACT_LAYOUTS = ["pdf_compact", "docx_compact"]
# Phase 4 (2026-11-22): employer-first headings with a registered-entity name in brackets and a hyperlink label after the
# title ("Employer (Entity Pvt Ltd), Title Company ↗ | Feb 2026 – Oct 2026"), one-sentence earlier entries, education as
# "Degree, Field, Institution, YYYY – YYYY, grade" and certifications as one wrapped "|" list.
EMPLOYER_FIRST_LAYOUTS = ["pdf_employer_first", "docx_employer_first"]
ALL_LAYOUTS = LAYOUTS + COMPACT_LAYOUTS
STRUCTURE_LAYOUTS = ALL_LAYOUTS + EMPLOYER_FIRST_LAYOUTS


def short_dates(j, style):
    """"Jan–Mar 2021" when both ends share a year (as people write them), otherwise the persona's normal range."""
    if j.get("shared_year") and j["end"] and j["start"][:4] == j["end"][:4]:
        return f"{MONTH[int(j['start'][5:])-1]}–{MONTH[int(j['end'][5:])-1]} {j['start'][:4]}"
    return dates(j, style)


def compact_summary(p):
    return p.get("summary") or summary(p)


def fmt(d, style):
    if d is None:
        return ONGOING[style]
    y, m = d.split("-")
    m = int(m)
    return {"mon_yyyy": f"{MONTH[m-1]} {y}", "mm_yyyy": f"{m:02d}/{y}", "mon_apos": f"{MONTH[m-1]}'{y[2:]}",
            "month_yyyy": f"{MONTH_LONG[m-1]} {y}", "yyyy_mm": f"{y}-{m:02d}"}[style]


def dates(j, style):
    return f"{fmt(j['start'], style)} – {fmt(j['end'], style)}"


def contact(p):
    return f"{p['name'].split()[0].lower()}@example.invalid | +91 90000 00000 | Bengaluru"


def summary(p):
    return ("Professional with experience across " + ", ".join(sorted({j['title'] for j in p['jobs']})[:2]) +
            " roles, interested in building useful products.")


def edu_line(e):
    q = e["qualification"] + (f" in {e['field']}" if e.get("field") else "")
    return q, f"{e['institution']} | {e['end']}"


# ----------------------------------------------------------------------------------------------------------------- PDF
def _pdf(p, layout):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    W, H = A4
    style = p["date_style"]
    state = {"y": H - 50}

    def newpage_if(need=40):
        if state["y"] < need + 40:
            c.showPage()
            state["y"] = H - 50

    def text(x, s, size=10, bold=False, right=None):
        newpage_if()
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(x, state["y"], s)
        if right:
            c.setFont("Helvetica", size)
            c.drawRightString(right[0], state["y"], right[1])
        state["y"] -= size + 4

    def para(x, width, s, size=10, bullet=False):
        indent = 12 if bullet else 0
        lines = simpleSplit(s, "Helvetica", size, width - indent)
        for i, ln in enumerate(lines):
            newpage_if()
            c.setFont("Helvetica", size)
            if bullet and i == 0:
                c.drawString(x, state["y"], "•")
            c.drawString(x + indent, state["y"], ln)
            state["y"] -= size + 3

    def heading(x, s):
        state["y"] -= 6
        text(x, s.upper(), size=11, bold=True)

    def experience(x, width, right):
        heading(x, "Professional Experience")
        jobs = p["jobs"]
        if layout == "pdf_grouped":
            for emp, grp in groupby(jobs, key=lambda j: j["employer"]):
                grp = list(grp)
                loc = grp[0].get("location")
                text(x, emp + (f", {loc}" if loc else ""), size=10.5, bold=True)
                for j in grp:
                    text(x, j["title"], size=10, bold=False, right=(right, dates(j, style)))
                    if j.get("description"):
                        para(x, width, j["description"])
                    for b in j["bullets"]:
                        para(x, width, b, bullet=True)
                    state["y"] -= 4
        else:
            for j in jobs:
                if layout == "pdf_two_column":           # a narrow column: dates on their own line, as real resumes do
                    text(x, f"{j['title']} | {j['employer']}", size=10.5, bold=True)
                    text(x, (f"{j['location']} | " if j.get("location") else "") + dates(j, style), size=9)
                else:
                    text(x, f"{j['title']} | {j['employer']}", size=10.5, bold=True, right=(right, dates(j, style)))
                    if j.get("location"):
                        text(x, j["location"], size=9)
                if j.get("description"):
                    para(x, width, j["description"])
                for b in j["bullets"]:
                    para(x, width, b, bullet=True)
                state["y"] -= 4

    def education_block(x, width=495):
        heading(x, "Education")
        for e in p["education"]:
            q, rest = edu_line(e)
            para(x, width, q)
            para(x, width, rest)

    def certs(x, width):
        if p.get("certifications"):
            heading(x, "Certifications")
            for ce in p["certifications"]:
                para(x, width, f"{ce['title']} – {ce['organisation']}, {ce['year']}", bullet=True)

    def projects(x, width):
        if p.get("projects"):
            heading(x, "Projects")
            for pr in p["projects"]:
                text(x, pr["title"], bold=True)
                para(x, width, pr["description"])
        if p.get("volunteering"):
            heading(x, "Volunteering")
            for v in p["volunteering"]:
                text(x, v["title"], bold=True)
                para(x, width, v["description"])

    text(50, p["name"], size=18, bold=True)
    text(50, contact(p), size=9)
    if layout == "pdf_two_column":
        top = state["y"] - 6
        # sidebar
        state["y"] = top
        heading(40, "Skills")
        for s in p["skills_section"]:
            text(40, s, size=9)
        education_block(40, 150)
        certs(40, 150)
        # main column
        state["y"] = top
        heading(220, "Summary")
        para(220, 335, summary(p))
        experience(220, 335, 555)
        projects(220, 335)
    else:
        heading(50, "Summary")
        para(50, 495, summary(p))
        experience(50, 495, 545)
        education_block(50)
        heading(50, "Skills")
        para(50, 495, ", ".join(p["skills_section"]))
        certs(50, 495)
        projects(50, 495)
    c.save()
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------------------------- DOCX
def _docx(p, layout):
    import docx
    d = docx.Document()
    style = p["date_style"]

    def exp(target):
        target.add_paragraph("Work Experience", style="Heading 1")
        for j in p["jobs"]:
            para = target.add_paragraph()
            r = para.add_run(f"{j['title']} | {j['employer']}" + (f" | {j['location']}" if j.get("location") else "") + f" | {dates(j, style)}")
            r.bold = True
            if j.get("description"):
                target.add_paragraph(j["description"])
            for b in j["bullets"]:
                target.add_paragraph(b, style="List Bullet")

    def side(target):
        target.add_paragraph("Skills", style="Heading 1")
        target.add_paragraph(" • ".join(p["skills_section"]) if layout == "docx_classic" else ", ".join(p["skills_section"]))
        target.add_paragraph("Education", style="Heading 1")
        for e in p["education"]:
            q, rest = edu_line(e)
            target.add_paragraph(f"{q}, {rest.replace(' | ', ', ')}")
        if p.get("certifications"):
            target.add_paragraph("Certifications", style="Heading 1")
            for ce in p["certifications"]:
                target.add_paragraph(f"{ce['title']} – {ce['organisation']}, {ce['year']}", style="List Bullet")

    def extra(target):
        if p.get("projects"):
            target.add_paragraph("Projects", style="Heading 1")
            for pr in p["projects"]:
                target.add_paragraph().add_run(pr["title"]).bold = True
                target.add_paragraph(pr["description"])
        if p.get("volunteering"):
            target.add_paragraph("Volunteering", style="Heading 1")
            for v in p["volunteering"]:
                target.add_paragraph().add_run(v["title"]).bold = True
                target.add_paragraph(v["description"])

    d.add_paragraph(p["name"], style="Title")
    d.add_paragraph(contact(p))
    if layout == "docx_table":
        t = d.add_table(rows=1, cols=2)
        left, right = t.rows[0].cells
        left.paragraphs[0].text = ""
        side(left)
        right.paragraphs[0].text = ""
        right.add_paragraph("Summary", style="Heading 1")
        right.add_paragraph(summary(p))
        exp(right)
        extra(right)
    else:
        d.add_paragraph("Summary", style="Heading 1")
        d.add_paragraph(summary(p))
        exp(d)
        side(d)
        extra(d)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------------------------------------- compact layouts
def _compact_entry(j, style):
    return f", {j['title']}, {short_dates(j, style)}. {j.get('description') or ''}".rstrip()


def _pdf_compact(p):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    W, H = A4
    X, WIDTH, RIGHT = 50, 495, 545
    style = p["date_style"]
    st = {"y": H - 50}

    def room(need=40):
        if st["y"] < need + 40:
            c.showPage()
            st["y"] = H - 50

    def line(s, size=10, bold=False, right=None, x=X):
        room()
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(x, st["y"], s)
        if right:
            c.setFont("Helvetica", size)
            c.drawRightString(RIGHT, st["y"], right)
        st["y"] -= size + 4

    def para(s, size=10, bullet=False):
        indent = 12 if bullet else 0
        for i, ln in enumerate(simpleSplit(s, "Helvetica", size, WIDTH - indent)):
            room()
            c.setFont("Helvetica", size)
            if bullet and i == 0:
                c.drawString(X, st["y"], "•")
            c.drawString(X + indent, st["y"], ln)
            st["y"] -= size + 3

    def mixed(bold_text, rest, size=10):
        """A paragraph starting with a bold run and continuing in regular text, wrapped like a word processor."""
        room()
        bw = stringWidth(bold_text, "Helvetica-Bold", size)
        first = simpleSplit(rest, "Helvetica", size, WIDTH - bw)
        head = first[0] if first else ""
        c.setFont("Helvetica-Bold", size)
        c.drawString(X, st["y"], bold_text)
        c.setFont("Helvetica", size)
        c.drawString(X + bw, st["y"], head)
        st["y"] -= size + 3
        remaining = rest[len(head):].strip()
        for ln in simpleSplit(remaining, "Helvetica", size, WIDTH):
            room()
            c.setFont("Helvetica", size)
            c.drawString(X, st["y"], ln)
            st["y"] -= size + 3

    def heading(s):
        st["y"] -= 6
        line(s.upper(), size=11, bold=True)

    line(p["name"], size=18, bold=True)
    line(contact(p), size=9)
    heading("Summary")
    para(compact_summary(p))
    heading("Experience")
    earlier_done = False
    for j in p["jobs"]:
        if j.get("earlier"):
            if not earlier_done:
                heading(p.get("earlier_heading", "Earlier Experience"))
                earlier_done = True
            mixed(j["employer"], _compact_entry(j, style))
            st["y"] -= 3
            continue
        line(j["employer"] + (f" — {j['tagline']}" if j.get("tagline") else ""), size=10.5, bold=True)
        line(j["title"], size=10, right=short_dates(j, style))
        if j.get("description"):
            para(j["description"])
        for b in j["bullets"]:
            para(b, bullet=True)
        st["y"] -= 4
    if p.get("leadership"):
        heading(p.get("leadership_heading", "Leadership & Recognition"))
        for v in p["leadership"]:
            mixed(v["title"], f", {v['organisation']}. {v['description']}")
    heading("Education & Certifications")
    for e in p["education"]:
        q, rest = edu_line(e)
        para(f"{q}, {rest.replace(' | ', ', ')}")
    for ce in p.get("certifications", []):
        para(f"{ce['title']} – {ce['organisation']}, {ce['year']}")
    heading("Skills")
    para(", ".join(p["skills_section"]))
    c.save()
    return buf.getvalue()


def _docx_compact(p):
    import docx
    d = docx.Document()
    style = p["date_style"]
    d.add_paragraph(p["name"], style="Title")
    d.add_paragraph(contact(p))
    d.add_paragraph("SUMMARY").runs[0].bold = True
    d.add_paragraph(compact_summary(p))
    d.add_paragraph("EXPERIENCE").runs[0].bold = True
    earlier_done = False
    for j in p["jobs"]:
        if j.get("earlier"):
            if not earlier_done:
                d.add_paragraph(p.get("earlier_heading", "Earlier Experience").upper()).runs[0].bold = True
                earlier_done = True
            para = d.add_paragraph()
            para.add_run(j["employer"]).bold = True
            para.add_run(_compact_entry(j, style))
            continue
        para = d.add_paragraph()
        para.add_run(j["employer"] + (" ↗" if j.get("tagline") else "")).bold = True
        if j.get("tagline"):
            para.add_run(f" | {j['tagline']} ↗")
        para = d.add_paragraph()
        para.add_run(j["title"]).bold = True
        para.add_run(f" | {short_dates(j, style)}")
        if j.get("description"):
            d.add_paragraph(j["description"])
        for b in j["bullets"]:
            d.add_paragraph(b, style="List Bullet")
    if p.get("leadership"):
        d.add_paragraph(p.get("leadership_heading", "Leadership & Recognition").upper()).runs[0].bold = True
        for v in p["leadership"]:
            para = d.add_paragraph()
            para.add_run(v["title"]).bold = True
            para.add_run(f", {v['organisation']}. {v['description']}")
    d.add_paragraph("EDUCATION & CERTIFICATIONS").runs[0].bold = True
    for e in p["education"]:
        q, rest = edu_line(e)
        d.add_paragraph(f"{q}, {rest.replace(' | ', ', ')}")
    for ce in p.get("certifications", []):
        d.add_paragraph(f"{ce['title']} – {ce['organisation']}, {ce['year']}")
    d.add_paragraph("SKILLS").runs[0].bold = True
    d.add_paragraph(", ".join(p["skills_section"]))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------------------------------- employer-first layouts
UNICODE_FONT = next((f for f in ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf")
                     if __import__("os").path.exists(f)), None)


def _ef_heading(j, style):
    emp = j["employer"] + (f" ({j['entity']})" if j.get("entity") else "")
    return emp, f", {j['title']}" + (f", {j['tagline']}" if j.get("tagline") else ""), j.get("link_label"), f" | {short_dates(j, style)}"


def _ef_cert_line(ce):
    span = ce.get("span") or str(ce["year"])
    return f"{ce['title']}, {ce['organisation']} ({span})"


def _pdf_employer_first(p):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfbase.pdfmetrics import registerFont, stringWidth
    from reportlab.pdfgen import canvas
    glyph_font = None
    if UNICODE_FONT:
        from reportlab.pdfbase.ttfonts import TTFont
        registerFont(TTFont("TraceUnicode", UNICODE_FONT))
        glyph_font = "TraceUnicode"
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    W, H = A4
    X, WIDTH = 40, 515
    style = p["date_style"]
    st = {"y": H - 50}

    def room():
        if st["y"] < 70:
            c.showPage()
            st["y"] = H - 50

    def run(x, text, font="Helvetica", size=10):
        c.setFont(font, size)
        c.drawString(x, st["y"], text)
        return x + stringWidth(text, font, size)

    def para(text, size=10, bullet=False):
        indent = 13 if bullet else 0
        for i, ln in enumerate(simpleSplit(text, "Helvetica", size, WIDTH - indent)):
            room()
            if bullet and i == 0:
                run(X, "•", size=size)
            run(X + indent, ln, size=size)
            st["y"] -= size + 3

    def heading(text):
        st["y"] -= 5
        room()
        run(X, text.upper(), "Helvetica-Bold", 10.5)
        st["y"] -= 14

    run(X, p["name"].upper(), "Helvetica-Bold", 18)
    st["y"] -= 22
    run(X, contact(p), size=9)
    st["y"] -= 14
    heading("Summary")
    para(compact_summary(p))
    heading("Professional Experience")
    earlier = False
    for j in p["jobs"]:
        if j.get("earlier"):
            if not earlier:
                heading(p.get("earlier_heading", "Earlier Experience"))
                earlier = True
            para(f"{j['employer']}, {j['title']}, {short_dates(j, style)}. {j.get('description') or ''}".rstrip())
            continue
        room()
        emp, title, label, dates = _ef_heading(j, style)
        x = run(X, emp, "Helvetica-Bold", 10.5)
        x = run(x, title, "Helvetica", 10.5)
        if label:
            x = run(x, " " + label, "Helvetica", 10.5)
            if glyph_font:
                x = run(x, " \u2197", glyph_font, 10.5)
        run(x, dates, "Helvetica", 10.5)
        st["y"] -= 14
        if j.get("description"):
            para(j["description"])
        for b in j["bullets"]:
            para(b, bullet=True)
        st["y"] -= 3
    if p.get("leadership"):
        heading(p.get("leadership_heading", "Leadership & Recognition"))
        for v in p["leadership"]:
            para(f"{v['title']}, {v['organisation']}. {v['description']}")
    heading("Education")
    for e in p["education"]:
        span = f"{e['start']} – {e['end']}" if e.get("start") else e["end"]
        para(", ".join(x for x in (e["qualification"], e.get("field"), e["institution"], span, e.get("grade")) if x))
    if p.get("certifications"):
        heading("Certifications & Fellowships")
        para(" | ".join(_ef_cert_line(ce) for ce in p["certifications"]))
    heading("Skills")
    para(", ".join(p["skills_section"]))
    c.save()
    return buf.getvalue()


def _docx_employer_first(p):
    import docx
    d = docx.Document()
    style = p["date_style"]
    d.add_paragraph(p["name"], style="Title")
    d.add_paragraph(contact(p))
    d.add_paragraph("SUMMARY").runs[0].bold = True
    d.add_paragraph(compact_summary(p))
    d.add_paragraph("PROFESSIONAL EXPERIENCE").runs[0].bold = True
    earlier = False
    for j in p["jobs"]:
        if j.get("earlier"):
            if not earlier:
                d.add_paragraph(p.get("earlier_heading", "Earlier Experience").upper()).runs[0].bold = True
                earlier = True
            d.add_paragraph(f"{j['employer']}, {j['title']}, {short_dates(j, style)}. {j.get('description') or ''}".rstrip())
            continue
        emp, title, label, dates = _ef_heading(j, style)
        para = d.add_paragraph()
        para.add_run(emp).bold = True
        para.add_run(title)
        if label:
            para.add_run(f" {label} \u2197")
        para.add_run(dates)
        if j.get("description"):
            d.add_paragraph(j["description"])
        for b in j["bullets"]:
            d.add_paragraph(b, style="List Bullet")
    if p.get("leadership"):
        d.add_paragraph(p.get("leadership_heading", "Leadership & Recognition").upper()).runs[0].bold = True
        for v in p["leadership"]:
            d.add_paragraph(f"{v['title']}, {v['organisation']}. {v['description']}")
    d.add_paragraph("EDUCATION").runs[0].bold = True
    for e in p["education"]:
        span = f"{e['start']} – {e['end']}" if e.get("start") else e["end"]
        d.add_paragraph(", ".join(x for x in (e["qualification"], e.get("field"), e["institution"], span, e.get("grade")) if x))
    if p.get("certifications"):
        d.add_paragraph("CERTIFICATIONS & FELLOWSHIPS").runs[0].bold = True
        d.add_paragraph(" | ".join(_ef_cert_line(ce) for ce in p["certifications"]))
    d.add_paragraph("SKILLS").runs[0].bold = True
    d.add_paragraph(", ".join(p["skills_section"]))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def render(p, layout):
    if layout == "pdf_employer_first":
        return _pdf_employer_first(p)
    if layout == "docx_employer_first":
        return _docx_employer_first(p)
    if layout == "pdf_compact":
        return _pdf_compact(p)
    if layout == "docx_compact":
        return _docx_compact(p)
    return _pdf(p, layout) if layout.startswith("pdf") else _docx(p, layout)
