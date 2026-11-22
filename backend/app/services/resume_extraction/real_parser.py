"""Local, deterministic parsing of a user's own resume PDF (Phase 6.1). No external service, no AI, no files written.

parse_resume(pdf_bytes) -> ParsedResume:
  record    the canonical career record (pipeline.FIELDS format), BEFORE de-identification
  identity  identity hints found in the header and contact lines (a likely name, emails, phones, links), used only in
            memory to drive de-identification; the header itself is never part of the record

What it reads: section headings (experience, education, skills, certifications, projects, achievements); experience
entries anchored on a date range ("Jan 2020 - Present", "2018 - 2021", "06/2019 - 03/2021") with title and employer
from the same or the preceding line; bullet lines as evidence; education entries with qualification, institution,
year and grade (CGPA / GPA / percentage); skills split on separators; certifications; projects.
What it does not do: understand every layout. Every extracted item becomes a draft that the user reviews, corrects
or rejects; nothing is confirmed on their behalf. Summaries, objectives, personal details, references and declarations
are not read into the record.
"""
import io
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.services.deidentification.detector import BARE_PROFILE, EMAIL, PHONE_CANDIDATE, URL

PARSER_VERSION = "trace-resume-parser-1"

SECTIONS = {
    "experience": r"(work |professional |employment |career )?(experience|history)|employment|work history|career history",
    "education": r"education|academic(s| background| qualifications)?|qualifications",
    "skills": r"(technical |core |key )?skills|competencies|technologies|tools",
    "certifications": r"certifications?|licen[cs]es?( and certifications)?|certificates?|courses",
    "projects": r"(personal |academic |key )?projects?",
    "achievements": r"achievements|awards|honou?rs|awards and achievements",
    "ignore": r"(professional )?summary|profile|objective|about me|personal (details|information)|contact( details)?|references?|"
              r"declaration|interests|hobbies|languages",
}
HEADING = {k: re.compile(rf"^\s*({v})\s*:?\s*$", re.I) for k, v in SECTIONS.items()}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DATE = rf"(?:{MON}\s*'?\d{{2,4}}|\d{{1,2}}/\d{{4}}|\d{{4}})"
RANGE = re.compile(rf"(?P<start>{DATE})\s*(?:-|–|—|to|until)\s*(?P<end>{DATE}|present|current|now|till date|ongoing)", re.I)
BULLET = re.compile(r"^\s*(?:[•\-\*▪‣●◦·–]|\d+[.)])\s+")
ROLE_WORDS = re.compile(r"\b(manager|engineer|developer|analyst|lead|director|head|intern|consultant|designer|owner|officer|"
                        r"associate|specialist|scientist|architect|executive|founder|co-founder|vp|vice president|president|"
                        r"administrator|coordinator|strategist|researcher|programmer|tester|trainee|principal)\b", re.I)
DEGREE = re.compile(r"\b(b\.?\s?tech|b\.?\s?e\.?|b\.?\s?sc|bsc|bachelor[a-z']*|b\.?\s?com|bba|bca|b\.?\s?a\.?|m\.?\s?tech|m\.?\s?e\.?|"
                    r"m\.?\s?sc|msc|master[a-z']*|mba|pgdm|pgp|m\.?\s?com|mca|ph\.?\s?d|doctorate|diploma)\b", re.I)
INSTITUTION = re.compile(r"\b(university|institute|college|school|iit|iim|nit|iiit|bits|academy|polytechnic)\b", re.I)
GRADE = re.compile(r"(?:(?P<cgpa>\d{1,2}(?:\.\d{1,2})?)\s*/\s*10\b|(?:c?gpa|cpi)\s*[:\-]?\s*(?P<gpa>\d(?:\.\d{1,2})?)(?:\s*/\s*(?P<of>\d+))?|"
                   r"(?P<pct>\d{2}(?:\.\d{1,2})?)\s*%)", re.I)
LEVEL = [(re.compile(r"ph\.?\s?d|doctorate", re.I), "doctorate"), (re.compile(r"\bmba\b|pgdm|pgp", re.I), "mba"),
         (re.compile(r"m\.?\s?tech|m\.?\s?e\.?\b|m\.?\s?sc|msc|master|m\.?\s?com|mca", re.I), "master"),
         (re.compile(r"diploma", re.I), "diploma"),
         (re.compile(r"b\.?\s?tech|b\.?\s?e\.?\b|b\.?\s?sc|bsc|bachelor|b\.?\s?com|bba|bca|b\.?\s?a\.?\b", re.I), "bachelor")]


@dataclass
class ParsedResume:
    record: dict
    identity: Dict[str, List[str]] = field(default_factory=dict)
    text_chars: int = 0


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _date(token: str) -> Tuple[Optional[str], str]:
    t = token.strip().lower().rstrip(".")
    if t in ("present", "current", "now", "till date", "ongoing"):
        return None, "ongoing"
    m = re.match(rf"^{MON}\s*'?(\d{{2,4}})$", t)
    if m:
        year = int(m.group(2)) if len(m.group(2)) == 4 else 2000 + int(m.group(2))
        return f"{year:04d}-{MONTHS[m.group(1)[:3]]:02d}", "month"
    m = re.match(r"^(\d{1,2})/(\d{4})$", t)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}", "month"
    if re.match(r"^\d{4}$", t):
        return t, "year"
    return None, "unknown"


def _sections(lines: List[str]) -> Tuple[List[str], Dict[str, List[str]]]:
    header, current, out = [], None, {}
    for line in lines:
        hit = next((k for k, rx in HEADING.items() if rx.match(line)), None)
        if hit:
            current = hit
            out.setdefault(current, [])
            continue
        if current is None:
            header.append(line)
        else:
            out[current].append(line)
    return header, out


def _identity(header: List[str], text: str) -> Dict[str, List[str]]:
    name = next((l.strip() for l in header[:4] if re.fullmatch(r"[^\W\d_][^\W\d_.'\-]*(?:[.'\-][^\W\d_]*)*(\s+[^\W\d_][^\W\d_.'\-]*(?:[.'\-][^\W\d_]*)*){1,3}\.?", l.strip())
                 and not ROLE_WORDS.search(l)), None)
    return {"names": [name] if name else [], "emails": sorted(set(EMAIL.findall(text))),
            "phones": sorted({m.group(1).strip() for m in PHONE_CANDIDATE.finditer(text) if 10 <= sum(c.isdigit() for c in m.group(1)) <= 13}),
            "urls": sorted({m.group(0) for m in URL.finditer(text)} | {m.group(0) for m in BARE_PROFILE.finditer(text)})}


def _split_title_employer(text: str) -> Tuple[str, Optional[str]]:
    parts = [p.strip(" ,|-–—@") for p in re.split(r"\s+(?:at|@)\s+|\s*[|,–—]\s*|\s+-\s+", text) if p.strip(" ,|-–—@")]
    if not parts:
        return "", None
    if len(parts) == 1:
        return parts[0], None
    titled = [p for p in parts if ROLE_WORDS.search(p)]
    title = titled[0] if titled else parts[0]
    employer = next((p for p in parts if p != title), None)
    return title, employer


def _experience(lines: List[str]) -> Tuple[List[dict], List[dict], List[dict]]:
    employers, jobs, evidence = [], [], []
    current = None
    prev_plain: Optional[str] = None
    for line in lines:
        m = RANGE.search(line)
        if m and not BULLET.match(line):
            rest = (line[:m.start()] + " " + line[m.end():]).strip(" ,|-–—()")
            title, employer = _split_title_employer(rest) if rest else ("", None)
            if prev_plain and (not title or not employer):
                t2, e2 = _split_title_employer(prev_plain)
                if not title:
                    title, employer = t2, employer or e2
                elif not employer:
                    employer = prev_plain if not ROLE_WORDS.search(prev_plain) or not ROLE_WORDS.search(title) else e2
            if not title:
                continue
            start, sp = _date(m.group("start"))
            end, ep = _date(m.group("end"))
            emp_key = None
            if employer:
                existing = next((e for e in employers if e["employer_name"].lower() == employer.lower()), None)
                if existing is None:
                    existing = {"employer_id": f"E{len(employers) + 1}", "employer_name": employer}
                    employers.append(existing)
                emp_key = existing["employer_id"]
            current = {"sequence_index": 0, "employer_id": emp_key, "title": title, "start": start, "start_precision": sp,
                       "end": end, "end_precision": ep, "is_current_as_stated": ep == "ongoing"}
            jobs.append(current)
            prev_plain = None
            continue
        if BULLET.match(line) or (current and len(line) > 60):
            if current is not None:
                text = BULLET.sub("", line).strip()
                if len(text) >= 10:
                    kind = "achievement" if re.search(r"\d+\s*%|\b\d+[kmx]?\b.*\b(increase|grew|reduced|improved|saved|launched)", text, re.I) \
                        else "responsibility"
                    evidence.append({"job": current, "evidence_type": kind, "text": text})
            continue
        prev_plain = line.strip()
    # Most recent first on resumes: number in chronological order.
    def sort_key(j):
        return (j["start"] or "9999")
    for i, j in enumerate(sorted(jobs, key=sort_key), 1):
        j["sequence_index"] = i
    for x in evidence:
        x["employment_seq"] = x.pop("job")["sequence_index"]
    return employers, jobs, evidence


def _education(lines: List[str]) -> List[dict]:
    out, cur = [], None
    for line in lines:
        text = BULLET.sub("", line).strip()
        if DEGREE.search(text) and (cur is None or cur.get("qualification_title")):
            cur = {"qualification_title": DEGREE.search(text).group(0) if len(text) > 80 else text.split(",")[0].split("|")[0].strip()}
            out.append(cur)
            level = next((lv for rx, lv in LEVEL if rx.search(text)), "unknown")
            cur["qualification_level"] = level
            first = re.split(r",|\||–|—| - ", text)[0]
            remainder = DEGREE.sub("", first, count=1).strip(" .()-")
            remainder = re.sub(r"^(in|of)\s+", "", remainder, flags=re.I).strip()
            if remainder and not INSTITUTION.search(remainder) and not re.search(r"\d", remainder):
                cur["field_of_study"] = remainder
        if cur is None:
            continue
        if INSTITUTION.search(text) and not cur.get("institution_name"):
            seg = next((s.strip() for s in re.split(r"[|,–—]| - ", text) if INSTITUTION.search(s)), text)
            cur["institution_name"] = seg
        years = re.findall(r"\b(19[5-9]\d|20\d{2})\b", text)
        if years and not cur.get("end"):
            cur["end"], cur["end_precision"] = years[-1], "year"
        g = GRADE.search(text)
        if g and not cur.get("grade_raw"):
            cur["grade_raw"] = g.group(0).strip()
            if g.group("cgpa"):
                cur["grade_scale"], cur["grade_value"] = "cgpa_10", float(g.group("cgpa"))
            elif g.group("gpa"):
                of = g.group("of")
                value = float(g.group("gpa"))
                cur["grade_scale"] = "gpa_4" if (of == "4" or (not of and value <= 4)) else "cgpa_10"
                cur["grade_value"] = value
            elif g.group("pct"):
                cur["grade_scale"], cur["grade_value"] = "percentage", float(g.group("pct"))
    return [e for e in out if e.get("qualification_title")]


def _skills(lines: List[str]) -> List[dict]:
    items = []
    for line in lines:
        line = re.sub(r"^[^:]{1,30}:\s*", "", BULLET.sub("", line))     # "Tools: SQL, Python"
        for part in re.split(r"[,;|•·]", line):
            p = part.strip(" .")
            if 1 < len(p) <= 60 and p.lower() not in {s["text"].lower() for s in items}:
                items.append({"text": p})
    return items[:60]


def _activities(lines: List[str], kind: str) -> List[dict]:
    out, cur = [], None
    for line in lines:
        text = BULLET.sub("", line).strip()
        if not text:
            continue
        if kind == "certification" or cur is None or not BULLET.match(line):
            if kind != "certification" and cur is not None and BULLET.match(line):
                cur["description"] = (cur.get("description", "") + " " + text).strip()
                continue
            year = re.findall(r"\b(19[5-9]\d|20\d{2})\b", text)
            title = re.split(r"\s[|–—]\s|\s-\s|,", text)[0].strip()
            cur = {"activity_type": kind, "title": title[:200], **({"year": int(year[-1])} if year else {})}
            if kind == "certification" and len(re.split(r"\s[|–—]\s|\s-\s|,", text)) > 1:
                cur["organisation"] = re.split(r"\s[|–—]\s|\s-\s|,", text)[1].strip()[:200]
            out.append(cur)
        else:
            cur["description"] = (cur.get("description", "") + " " + text).strip()
    return out


def parse_resume(data: bytes) -> ParsedResume:
    return parse_text(pdf_text(data))


def parse_text(text: str, *, untitled_is_experience: bool = False) -> ParsedResume:
    """Parse career text (a resume's text, or experience text a user typed or pasted) with the same rules.
    untitled_is_experience: text with no recognised section heading is read as an experience section (pasted text is
    often just job entries). Lines before the first heading are otherwise a header: read only for identity hints and
    never into the record."""
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    lines = [l for l in lines if l]
    header, sections = _sections(lines)
    if untitled_is_experience and "experience" not in sections:
        # Pasted text is often just job entries, perhaps followed by headed sections. Its untitled lines are read as
        # experience, except contact lines (email, phone, link), which never enter the record.
        is_contact = lambda l: bool(EMAIL.search(l) or PHONE_CANDIDATE.search(l) or URL.search(l))   # noqa: E731
        contact = [l for l in header if is_contact(l)]
        # A resume-style header: a name line directly followed by a contact line. The name is dropped too.
        if len(header) > 1 and is_contact(header[1]) and _identity(header[:1], "")["names"]:
            contact.insert(0, header[0])
        sections = {**sections, "experience": [l for l in header if l not in contact]}
        header = contact
    employers, jobs, evidence = _experience(sections.get("experience", []))
    record = {
        "country_code": "IN", "resume_as_of_precision": "unknown",
        "employers": employers, "employments": jobs, "evidence": evidence,
        "education": _education(sections.get("education", [])),
        "skills": _skills(sections.get("skills", [])),
        "activities": _activities(sections.get("certifications", []), "certification")
                      + _activities(sections.get("projects", []), "project")
                      + _activities(sections.get("achievements", []), "award"),
    }
    return ParsedResume(record=record, identity=_identity(header, text), text_chars=len(text))
