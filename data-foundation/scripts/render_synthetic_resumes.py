#!/usr/bin/env python3
"""Render parser-ready synthetic resume PDFs from data/synthetic/synthetic_profiles.jsonl.

Optional tool: requires reportlab (pip install "reportlab<4.3" on Python 3.9).
Output is deterministic (reportlab invariant mode, fixed zip timestamps), so re-rendering an
unchanged profile reproduces the same bytes and the manifest stays stable.

  python3 scripts/render_synthetic_resumes.py
Then run python3 scripts/build_seeds.py to refresh manifest.json.
"""
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trace_model as tm  # noqa: E402

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
except ImportError:  # pragma: no cover
    sys.exit("reportlab is required: pip install 'reportlab<4.3'")

ROOT = tm.ROOT
OUT_DIR = ROOT / "data/synthetic/synthetic_resume_pdfs"
ZIP_PATH = ROOT / "data/synthetic/synthetic_resume_pdfs.zip"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
FOOTER = ("This document is a fictional test fixture generated for Trace resume-parsing, career-history extraction and "
          "workflow testing. It does not describe a real person, real employer, verified employment history or observed "
          "Indian career transition.")


def human_date(value, precision):
    if precision == "ongoing":
        return "Present (synthetic)"
    if precision == "unknown" or not value:
        return "Dates not stated"
    if precision == "year":
        return value
    y, m = value.split("-")
    return f"{MONTHS[int(m) - 1]} {y}"


def period(start, sp, end, ep):
    if sp == "unknown" and ep == "unknown":
        return "Dates not stated"
    return f"{human_date(start, sp)} - {human_date(end, ep)}"


class Writer:
    def __init__(self, path, profile_id):
        self.c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
        self.c.setTitle(f"{profile_id} synthetic resume")
        self.c.setAuthor("Trace synthetic test data generator")
        self.profile_id = profile_id
        self.page = 0
        self.width, self.height = A4
        self.new_page()

    def new_page(self):
        if self.page:
            self.c.showPage()
        self.page += 1
        self.y = self.height - 15 * mm
        self.c.setFont("Helvetica-Bold", 8)
        self.c.drawString(15 * mm, self.y, f"TRACE SYNTHETIC TEST FIXTURE | {self.profile_id} | NOT A REAL PERSON")
        self.c.drawRightString(self.width - 15 * mm, self.y, f"Page {self.page}")
        self.y -= 10 * mm

    def line(self, text, font="Helvetica", size=10, gap=5 * mm, indent=0):
        max_chars = int((self.width - 30 * mm - indent) / (size * 0.5))
        words, cur = text.split(), ""
        rows = []
        for w in words:
            if len(cur) + len(w) + 1 > max_chars:
                rows.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        rows.append(cur)
        for r in rows:
            if self.y < 25 * mm:
                self.new_page()
            self.c.setFont(font, size)
            self.c.drawString(15 * mm + indent, self.y, r)
            self.y -= gap

    def save(self):
        self.c.save()


def render(profile, path):
    pid = profile["profile_id"]
    num = pid.split("-")[1]
    w = Writer(path, pid)
    w.line("SYNTHETIC TEST RESUME - NOT A REAL PERSON - NOT MARKET EVIDENCE", "Helvetica-Bold", 11)
    w.line(f"Synthetic Candidate {num}", "Helvetica-Bold", 16, gap=7 * mm)
    w.line(f"India | syn-{num}@example.invalid | Contact details intentionally omitted", size=9)
    w.line(profile["profile_summary"], size=10)
    w.y -= 2 * mm
    w.line("PROFESSIONAL EXPERIENCE", "Helvetica-Bold", 11)
    employers = {e["employer_id"]: e for e in profile["employers"]}
    evidence = {}
    for x in profile["evidence"]:
        evidence.setdefault(x["employment_id"], []).append(x["text"])
    for e in sorted(profile["employments"], key=lambda e: e["sequence_index"]):
        emp = employers.get(e["employer_id"]) or {}
        w.line(e["title_raw_redacted"], "Helvetica-Bold", 10)
        w.line(period(e.get("start"), e["start_precision"], e.get("end"), e["end_precision"]), size=9, indent=2 * mm)
        w.line(f"{e['employer_id'] or 'Employer not stated'} | {emp.get('industry_raw') or 'industry not stated'}", size=9, indent=2 * mm)
        for t in evidence.get(e["employment_id"], []):
            w.line(f"- {t}", size=9, indent=2 * mm)
        w.y -= 2 * mm
    if profile.get("career_breaks"):
        w.line("CAREER BREAKS", "Helvetica-Bold", 11)
        for b in profile["career_breaks"]:
            w.line(f"{b['break_reason'].replace('_', ' ').capitalize()}: "
                   f"{period(b.get('start'), b['start_precision'], b.get('end'), b['end_precision'])}"
                   f"{' - ' + b['note_redacted'] if b.get('note_redacted') else ''}", size=9)
    w.line("EDUCATION", "Helvetica-Bold", 11)
    for d in profile.get("education", []):
        label = {"bachelor": "Bachelor's degree", "master": "Master's degree", "mba": "MBA"}.get(d["qualification_level"], d["qualification_level"])
        dates = "" if d["start_precision"] == "unknown" else f" ({period(d.get('start'), d['start_precision'], d.get('end'), d['end_precision'])})"
        w.line(f"{label}{', ' + d['field_of_study'] if d.get('field_of_study') else ''}, fictional Indian institute{dates}", size=9)
    w.line("SKILLS", "Helvetica-Bold", 11)
    w.line(", ".join(s.get("skill_text_raw") or s["skill_id"] for s in profile.get("skills", [])), size=9)
    w.y -= 3 * mm
    w.line(FOOTER, size=7, gap=3.5 * mm)
    w.save()


def main():
    profiles = tm.read_jsonl(ROOT / "data/synthetic/synthetic_profiles.jsonl")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    expected = set()
    for p in profiles:
        path = OUT_DIR / f"{p['profile_id']}_synthetic_resume.pdf"
        expected.add(path.name)
        render(p, path)
    for stale in OUT_DIR.glob("*.pdf"):
        if stale.name not in expected:
            stale.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name in sorted(expected):
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 11, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, (OUT_DIR / name).read_bytes())
    print(f"Rendered {len(expected)} synthetic resume PDFs and {ZIP_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
