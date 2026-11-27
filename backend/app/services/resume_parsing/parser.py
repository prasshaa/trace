"""Resume parser v2: layout-aware lines -> sections -> entries -> a versioned, evidence-linked draft profile.

Deterministic and local. Stages (each one testable on its own):
  1. sections     headings by vocabulary + typography (short, bold or capitalised); an inline heading
                  ("Skills: Python, SQL") opens its section with its content;
  2. paragraphs   wrapped lines are joined back into one bullet or one paragraph (PDF geometry: same indent, small
                  vertical gap, no new bullet; any format: a line that continues a sentence);
  3. experience   entries anchored on a date range; title, employer and location from the header lines around it; a
                  company line followed by several dated titles is ONE employer with several roles (promotions);
                  bullets are achievements/responsibilities, prose is one description; nothing is invented;
  4. education, certifications, projects and other activities;
  5. skills       vocabulary-normalised and de-duplicated, from the skills section and from experience text, each
                  with its evidence lines;
  6. draft        ResumeDraft (schema DRAFT_SCHEMA) with per-record evidence (line indexes) and review flags for
                  anything uncertain. to_record() maps it to the canonical career record used by de-identification
                  and ingestion. Contact details, summaries, photos, references and personal details are never read.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from app.services.resume_parsing import skills as sk
from app.services.resume_parsing.document import Document, Line

PARSER_VERSION = "trace-resume-parser-2.3"
# /2 added `unresolved` (nothing is dropped silently); /3 keeps a role with no recognisable title as a role (title left for
# the user), adds each role's `source` line, and gives unplaced text the fields it supports (title, employer, dates).
DRAFT_SCHEMA = "trace-resume-draft/3"

SECTIONS = {
    "experience": r"(work |professional |employment |career |relevant |industry )?(experience|history)|employment( history)?|"
                  r"work history|career history|experience summary|professional background",
    "education": r"education( and training| & training| details| background)?|academic (background|qualifications|details)|"
                 r"academics|qualifications|educational qualifications",
    "skills": r"(technical |core |key |professional |relevant )?(skills|competencies|competences)( summary| & tools| and tools)?|"
              r"technologies|tools( & technologies| and technologies)?|tech stack|areas of expertise|expertise|skill set",
    "certifications": r"certifications?( and (courses|training))?|licen[cs]es?( (and|&) certifications)?|certificates?|"
                      r"courses|training|professional development",
    "projects": r"(personal |academic |key |selected |side )?projects?",
    "volunteering": r"volunteer(ing)?( experience| work)?|community( involvement)?|extracurricular( activities)?|leadership( experience)?",
    "awards": r"achievements|awards( (and|&) (achievements|recognition))?|honou?rs|recognition|accomplishments",
    "ignore": r"(professional |career )?(summary|profile|overview)|objective|career objective|about( me)?|personal (details|information|profile)|"
              r"contact( details| information)?|references?|declaration|interests|hobbies|languages( known)?|publications",
}
HEADING = {k: re.compile(rf"^\s*({v})\s*:?\s*$", re.I) for k, v in SECTIONS.items()}
INLINE_HEADING = re.compile(rf"^\s*({SECTIONS['skills']})\s*:\s*(?P<rest>.+)$", re.I)

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
D1 = rf"(?:{MON}\s*[,'’\-]?\s*'?\d{{2,4}}|\d{{1,2}}\s*[/.\-]\s*\d{{4}}|\d{{4}}\s*[/.\-]\s*\d{{1,2}}(?!\d)|(?:19|20)\d{{2}})"
ONGOING = r"present|current(ly)?|now|till date|to date|todate|date|ongoing|till now"
# A full range ("Apr 2021 – Present"), or two months sharing one year ("Jan–Mar 2021": both in 2021).
RANGE = re.compile(rf"(?P<start>{D1})\s*(?:-|–|—|to|till|until|→)\s*(?P<end>{D1}|{ONGOING})\b|"
                   rf"\b(?P<smon>{MON})\s*(?:-|–|—|to)\s*(?P<emon>{MON})\s*[,'’]?\s*'?(?P<syear>(?:19|20)\d{{2}})\b", re.I)
SINCE = re.compile(rf"\b(?:since|from)\s+(?P<start>{D1})|(?P<start2>{D1})\s+(?:onwards|-\s*$|–\s*$)", re.I)
ROLE_WORDS = re.compile(r"\b(manager|engineer|developer|analyst|lead|director|head|intern|internship|consultant|designer|owner|"
                        r"officer|associate|specialist|scientist|architect|executive|founder|co-founder|vp|vice president|"
                        r"president|administrator|coordinator|strategist|researcher|programmer|tester|trainee|principal|"
                        r"partner|advisor|representative|sde|swe|qa|apm|pm|cto|ceo|coo|cpo|staff|member of technical staff|writer|editor|"
                        r"instructor|teacher|tutor|accountant|auditor|recruiter|marketer|operator|technician|supervisor|"
                        r"agent|mentor|volunteer|fellow|apprentice|mgr|asst|exec|assoc|coord|supvr|avp|svp|evp|dgm|agm|gm|"
                        r"dy\.? manager|sr\.? manager|assistant|clerk|nurse|physician|pharmacist|lawyer|attorney|counsel|"
                        r"paralegal|chef|cashier|pilot|artist|photographer|journalist|reporter|producer|planner|buyer|"
                        r"merchandiser|controller|treasurer|banker|trader|underwriter|actuary|economist|statistician|lecturer|"
                        r"professor|faculty|coach|trainer|therapist|counsell?or|receptionist|secretary|technologist|"
                        r"investigator)\b", re.I)
ORG_WORDS = re.compile(r"\b(ltd|limited|pvt|private|inc|llc|llp|corp|corporation|company|co\.|technologies|solutions|labs|"
                       r"bank|group|consultancy|ventures|studio|studios|capital|partners|institute|university|foundation|"
                       r"self-employed|\.com|\.ai|\.io)\b", re.I)
WEAK_ORG = re.compile(r"\b(tech|technology|systems|services|software|consulting|global|india|networks|digital|analytics|media|"
                      r"health|fintech|retail|logistics|mobility|payments|advisory|club|commerce|telecom|saas)\b", re.I)
LOCATION = re.compile(r"^(bengaluru|bangalore|mumbai|delhi|new delhi|ncr|gurugram|gurgaon|noida|hyderabad|chennai|pune|kolkata|"
                      r"ahmedabad|jaipur|kochi|chandigarh|indore|coimbatore|remote|hybrid|india|singapore|london|dubai|"
                      r"san francisco|new york|seattle|berlin|toronto|sydney)(\s*,\s*[a-z .]+)?$", re.I)
DEGREE = re.compile(r"\b(b\.?\s?des|m\.?\s?des|b\.?\s?arch|m\.?\s?arch|llb|llm|mbbs|b\.?\s?pharm|m\.?\s?pharm|bfa|mfa|"
                    r"b\.?\s?ed|m\.?\s?ed|b\.?\s?voc|bms|bbm|pgd(ba|m)?|b\.?\s?tech|b\.?\s?e\b\.?|b\.?\s?sc|bsc|bachelor[a-z']*|b\.?\s?com|bba|bca|b\.?\s?a\b\.?|m\.?\s?tech|"
                    r"m\.?\s?e\b\.?|m\.?\s?sc|msc|master[a-z']*|mba|pgdm|pgp|m\.?\s?com|mca|ph\.?\s?d|doctorate|diploma|"
                    r"post ?graduate|higher secondary|hsc|ssc|class (x|xii|10|12)(th)?|12th|10th|cbse|icse)\b", re.I)
INSTITUTION = re.compile(r"\b(university|universit[äa]t|institute|college|school|iit|iim|nit|iiit|bits|academy|polytechnic|"
                         r"vidyalaya|vidyapeeth)\b", re.I)
LEVEL = [(re.compile(r"ph\.?\s?d|doctorate", re.I), "doctorate"), (re.compile(r"\bmba\b|pgdm|pgp|post ?graduate diploma", re.I), "mba"),
         (re.compile(r"m\.?\s?tech|\bm\.?\s?e\b\.?|m\.?\s?sc|msc|master|m\.?\s?com|mca|m\.?\s?des|m\.?\s?arch|llm|m\.?\s?pharm|mfa|m\.?\s?ed", re.I), "master"),
         (re.compile(r"diploma", re.I), "diploma"),
         (re.compile(r"b\.?\s?tech|\bb\.?\s?e\b\.?|b\.?\s?sc|bsc|bachelor|b\.?\s?com|bba|bca|\bb\.?\s?a\b\.?|b\.?\s?des|b\.?\s?arch|llb|mbbs|"
                    r"b\.?\s?pharm|bfa|b\.?\s?ed|b\.?\s?voc|bms|bbm", re.I), "bachelor"),
         (re.compile(r"higher secondary|hsc|ssc|class (x|xii|10|12)|12th|10th|cbse|icse", re.I), "secondary")]
GRADE = re.compile(r"(?:(?P<cgpa>\d{1,2}(?:\.\d{1,2})?)\s*/\s*10\b|(?:c?gpa|cpi)\s*[:\-]?\s*(?P<gpa>\d(?:\.\d{1,2})?)(?:\s*/\s*(?P<of>\d+))?|"
                   r"(?P<pct>\d{2}(?:\.\d{1,2})?)\s*%)", re.I)
CONTACT = re.compile(r"@|https?://|www\.|linkedin|github\.com|\+?\d[\d\s\-()]{8,}\d", re.I)
ACHIEVEMENT = re.compile(r"\d+(\.\d+)?\s*(%|x\b|k\b|m\b|cr\b|crore|lakh|mn\b|million|bn\b)|₹|\$|\b(increas|grew|grow|reduc|improv|"
                         r"sav|launch|won|award|cut|doubl|tripl|boost|lift|exceed)", re.I)


# Section headings recognised by keyword when a short line is styled as a heading (capitals, bold or larger) but isn't in
# the vocabulary above: "EARLIER EXPERIENCE", "LEADERSHIP & RECOGNITION", "EDUCATION & CERTIFICATIONS". The keyword that
# comes first in the heading decides.
HEADING_WORDS = [
    ("ignore", r"summary|profile|objective|about me|overview|personal|contact|references|declaration|interests|hobbies|languages"),
    ("experience", r"experience|employment|work history|career history|positions held|professional background"),
    ("education", r"education|academic"),
    ("certifications", r"certifications?|certificates?|courses|training|licen[cs]es?"),
    ("skills", r"skills|competenc(?:y|ies)|technologies|tools|expertise|tech stack"),
    ("projects", r"projects?"),
    ("volunteering", r"leadership|volunteer(?:ing)?|community|extracurricular|activities|initiatives"),
    ("awards", r"awards?|recognition|achievements?|honou?rs|accomplishments"),
]
HEADING_WORD = re.compile("|".join(rf"(?P<{k}>\b(?:{v})\b)" for k, v in HEADING_WORDS), re.I)
# Words that describe a kind of business rather than name one ("Payments Platform & Fintech Company").
DESCRIPTOR = re.compile(r"\b(company|firm|startup|start-up|agency|platform|provider|management|adtech|martech|fintech|edtech|"
                        r"healthtech|saas|technology|software|marketing|advertising|campaign|services|solutions|consulting|"
                        r"consultancy|e-?commerce|enterprise|global|leading|programmatic|b2b|b2c|d2c|analytics|automation)\b", re.I)
LEGAL_FORM = re.compile(r"\b(ltd|limited|pvt|private|inc|llc|llp|corp|corporation|gmbh|plc|pte)\b\.?", re.I)
CERT_WORDS = re.compile(r"\b(certifi\w*|certificate|course|program(me)?|bootcamp|nanodegree|speciali[sz]ation|fellowship|cohort|"
                        r"workshop|accelerator)\b", re.I)


# ------------------------------------------------------------------------------------------------------- draft schema
class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DateValue(_M):
    value: Optional[str] = None                        # "YYYY-MM" or "YYYY"
    precision: str = "unknown"                         # month | year | ongoing | unknown
    raw: Optional[str] = None


class Bullet(_M):
    text: str
    kind: str = "responsibility"                       # responsibility | achievement
    evidence: List[int] = Field(default_factory=list)


class Job(_M):
    employer: Optional[str] = None
    title: Optional[str] = None
    location: Optional[str] = None                     # extracted to keep it out of the employer; never stored
    start: DateValue = DateValue()
    end: DateValue = DateValue()
    current: bool = False
    employment_type: str = "unknown"
    description: Optional[str] = None
    bullets: List[Bullet] = Field(default_factory=list)
    group: Optional[int] = None                        # roles under one employer heading (promotions) share a group
    evidence: List[int] = Field(default_factory=list)
    review: List[str] = Field(default_factory=list)    # fields the user should check
    source: Optional[str] = None                       # the header text the role was read from (shown when a field is missing)


class Education(_M):
    qualification: Optional[str] = None
    level: str = "unknown"
    field_of_study: Optional[str] = None
    institution: Optional[str] = None
    start: DateValue = DateValue()
    end: DateValue = DateValue()
    grade: Optional[str] = None
    evidence: List[int] = Field(default_factory=list)
    review: List[str] = Field(default_factory=list)


class Activity(_M):
    kind: str                                          # certification | project | volunteering | award
    title: str
    organisation: Optional[str] = None
    description: Optional[str] = None
    year: Optional[int] = None
    evidence: List[int] = Field(default_factory=list)


class Skill(_M):
    name: str
    category: str
    recognised: bool = True
    sources: List[str] = Field(default_factory=list)   # "skills_section" | "experience"
    evidence: List[int] = Field(default_factory=list)


class Unresolved(_M):
    """Text the parser found but could not place with confidence. Shown to the user on the review screen with its text;
    never turned into a record by the parser and never silently dropped."""
    kind: str                                          # possible_role | unattached_text | employer_unclear
    section: str                                       # experience | summary | top | other
    text: str
    evidence: List[int] = Field(default_factory=list)
    # What the text itself supports, for "Add as a role" (each only when read with confidence; never invented)
    title: Optional[str] = None
    employer: Optional[str] = None
    start: DateValue = DateValue()
    end: DateValue = DateValue()
    description: Optional[str] = None


class ResumeDraft(_M):
    schema_version: str = DRAFT_SCHEMA
    parser_version: str = PARSER_VERSION
    format: str
    layout: str
    pages: int
    work: List[Job] = Field(default_factory=list)
    education: List[Education] = Field(default_factory=list)
    activities: List[Activity] = Field(default_factory=list)
    skills: List[Skill] = Field(default_factory=list)
    unresolved: List[Unresolved] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


# ------------------------------------------------------------------------------------------------------------ dates
def parse_date(token: Optional[str]) -> DateValue:
    if not token:
        return DateValue()
    raw = token.strip()
    t = raw.lower().strip(" .,")
    if re.fullmatch(ONGOING, t):
        return DateValue(precision="ongoing", raw=raw)
    m = re.fullmatch(rf"({MON})\s*[,'’\-]?\s*'?(\d{{2,4}})", t)
    if m:
        y = int(m.group(2))
        if y < 100:
            y += 2000 if y <= date.today().year % 100 + 1 else 1900
        mon = MONTHS[m.group(1)[:3]]
        return DateValue(value=f"{y:04d}-{mon:02d}", precision="month", raw=raw)
    m = re.fullmatch(r"(\d{1,2})\s*[/.\-]\s*(\d{4})", t)
    if m and 1 <= int(m.group(1)) <= 12:
        return DateValue(value=f"{int(m.group(2)):04d}-{int(m.group(1)):02d}", precision="month", raw=raw)
    m = re.fullmatch(r"(\d{4})\s*[/.\-]\s*(\d{1,2})", t)
    if m and 1 <= int(m.group(2)) <= 12:
        return DateValue(value=f"{int(m.group(1)):04d}-{int(m.group(2)):02d}", precision="month", raw=raw)
    if re.fullmatch(r"(19|20)\d{2}", t):
        return DateValue(value=t, precision="year", raw=raw)
    return DateValue(raw=raw)


def range_dates(m: "re.Match") -> Tuple[DateValue, DateValue]:
    """(start, end) of a RANGE match. A shared-year range gets the year on both ends, as written; nothing else is added."""
    if m.group("smon"):
        y = m.group("syear")
        return parse_date(f"{m.group('smon')} {y}"), parse_date(f"{m.group('emon')} {y}")
    return parse_date(m.group("start")), parse_date(m.group("end"))


def compact_tail(text: str) -> Optional[str]:
    """In a one-paragraph entry ("Tallyfox, Associate Product Manager, Jan–Mar 2021. Ran the pricing pilot...")
    the text after the dates is the role's description. Returns it, or None for an ordinary header line."""
    m = RANGE.search(text) or SINCE.search(text)
    if not m or m.start() > 150:
        return None
    mt = re.match(r"^\s*(?:[.:;]|[–—-]\s|\)\s*[.:;]?)\s*(?P<t>\S.*)$", text[m.end():])
    if not mt or len(mt.group("t").split()) < 3 or LOCATION.match(mt.group("t").strip(" .")):
        return None
    return mt.group("t").strip()


# --------------------------------------------------------------------------------------------------------- sections
def _is_heading(l: Line, body_size: float) -> Optional[str]:
    text = l.text.strip().rstrip(":").strip()
    if len(text) > 45 or l.bullet:
        return None
    styled = l.bold or (text.isupper() and any(c.isalpha() for c in text)) or bool(l.size and l.size > body_size + 0.5)
    for k, rx in HEADING.items():
        if rx.match(text):
            # vocabulary match on a short line; typography only breaks ties for generic words ("Training", "Leadership")
            generic = k in ("certifications", "volunteering") and text.lower() in ("training", "leadership", "community")
            if generic and not styled:
                return None
            return k
    # A styled short line naming a section in other words ("EARLIER EXPERIENCE", "LEADERSHIP & RECOGNITION"). Never a
    # line with digits, sentence punctuation, a job title or a company form: those are entries, not headings.
    if not styled or len(text.split()) > 5 or re.search(r"\d|[.!?,;|()@]", text) or ROLE_WORDS.search(text) or LEGAL_FORM.search(text) \
            or ORG_WORDS.search(text) or LOCATION.search(text):
        return None
    m = HEADING_WORD.search(text)
    return m.lastgroup if m else None


def sections(doc: Document) -> Tuple[List[Line], Dict[str, List[Line]]]:
    sizes = sorted(l.size for l in doc.lines if l.size)
    body = sizes[len(sizes) // 2] if sizes else 0.0
    header: List[Line] = []
    out: Dict[str, List[Line]] = {}
    # The open section is tracked per column: a sidebar heading ("Skills") never captures main-column lines read after
    # it. A full-width heading (column 0) applies to every column.
    current: Dict[int, Optional[str]] = {}
    for l in doc.lines:
        k = _is_heading(l, body)
        if k:
            if l.column == 0:
                current = {c: k for c in (0, 1, 2)}
            else:
                current[l.column] = k
            out.setdefault(k, [])
            continue
        m = INLINE_HEADING.match(l.text)
        if m and not l.bullet and len(m.group(1)) < 30:
            out.setdefault("skills", []).append(Line(**{**l.__dict__, "text": m.group("rest")}))
            continue
        sec = current.get(l.column, current.get(0))
        (header if sec is None else out[sec]).append(l)
    return header, out


# ------------------------------------------------------------------------------------------------------- paragraphs
class Para:
    def __init__(self, line: Line):
        self.lines = [line]
        self.bullet = line.bullet

    @property
    def text(self) -> str:
        out = ""
        for l in self.lines:
            t = l.text
            if out.endswith("-") and not out.endswith(" -") and t[:1].islower():
                out = out[:-1] + t                                      # hyphenated word split across lines
            else:
                out = (out + " " + t).strip()
        return out

    @property
    def first(self) -> Line:
        return self.lines[0]

    @property
    def evidence(self) -> List[int]:
        return [l.index for l in self.lines]


def _continues(prev: Para, l: Line, line_h: float, right: float = 0.0, left: float = 0.0) -> bool:
    if l.bullet or RANGE.search(l.text) or l.column != prev.lines[-1].column:
        return False
    last = prev.lines[-1]
    t = l.text
    if last.bold != l.bold and not t[:1].islower():                    # a bold title followed by plain text
        return False
    if (RANGE.search(last.text) or SINCE.search(last.text)) and compact_tail(last.text) is None:
        return False                                                    # a dated header line never runs on into prose
    if last.page != l.page:
        return t[:1].islower()
    if last.top and l.top:                                              # PDF geometry
        gap = l.top - last.bottom
        if gap > max(0.9 * line_h, 6):
            return False
        text_x = prev.first.x0
        if prev.bullet:
            dx = l.x0 - text_x
            # Same column as the bullet text. A hanging indent (the glyph sits left of the words, so the wrap
            # starts further right) is the same bullet even when it resumes with a digit ("91%") or a capital
            # ("Did not"). A line that itself looks like a job header is not pulled into the bullet.
            if abs(dx) <= 4:
                return True
            if 4 < dx <= 28 and not (_header_like(l, loose=False) and not t[:1].islower() and not t[:1].isdigit()):
                return True
            return dx > -2 and t[:1].islower()
        full = right > left and last.x1 >= right - 0.18 * (right - left)          # the previous line ran to the margin
        # A lower-case start is a sentence carried over; otherwise the previous line must have run to the margin.
        return abs(l.x0 - text_x) <= 4 and (t[:1].islower() or (full and not _header_like(l, loose=False)
                                                                 and not re.search(r"[.!?:|]$", last.text)))
    return t[:1].islower() and not re.search(r"[.!?]$", last.text)      # DOCX/plain: a sentence carried over


ABBREV_END = re.compile(r"\b(co|ltd|inc|pvt|corp|bros|jr|sr|st|no|dept|intl|plc)\.$|\b[A-Z]\.$", re.I)


def ends_sentence(t: str) -> bool:
    """A line ending in . ! ? or ; that is not just an abbreviation ("Example Distribution Co.", "Pvt. Ltd.")."""
    t = t.rstrip()
    return bool(re.search(r"[.!?;]$", t)) and not ABBREV_END.search(t)


def _title_case(t: str) -> bool:
    words = [w for w in re.findall(r"[A-Za-z][\w&'.-]*", t) if w.lower() not in ("and", "of", "for", "the", "in", "at", "&")]
    return len(words) >= 2 and sum(1 for w in words if w[0].isupper()) >= 0.75 * len(words)


def _header_like(l: Line, loose: bool = True) -> bool:
    """Could this line be part of a job header? loose (header detection) also accepts "A | B" lines and Title Case
    lines; the strict form (2.0 behaviour) decides whether a line may continue a paragraph."""
    t = l.text
    return (bool(RANGE.search(t)) or (len(t) <= 80 and not ends_sentence(t) and len(t.split()) <= 12 and (
        l.bold or ROLE_WORDS.search(t) is not None or ORG_WORDS.search(t) is not None
        or (loose and ("|" in t or _title_case(t))))))


def paragraphs(lines: List[Line]) -> List[Para]:
    hs = sorted(l.bottom - l.top for l in lines if l.bottom > l.top)
    line_h = hs[len(hs) // 2] if hs else 12.0
    out: List[Para] = []
    for l in lines:
        if out and _continues(out[-1], l, line_h, l.col_right, l.col_left):
            out[-1].lines.append(l)
        else:
            out.append(Para(l))
    return out


# ------------------------------------------------------------------------------------------------------- experience
def _clean(s: Optional[str]) -> Optional[str]:
    """Trim separators and stray brackets, keeping a bracket that has its partner ("Novaline (Novaline Care Labs)")."""
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip(" ,|-–—:@·")
    while s and s[0] in "()" and not (s[0] == "(" and ")" in s):
        s = s[1:].strip(" ,|-–—:@·")
    while s and s[-1] in "()" and not (s[-1] == ")" and s.count("(") >= s.count(")")):
        s = s[:-1].strip(" ,|-–—:@·")
    return s or None


SPLIT = re.compile(r"\s+\|\s+|\s*\|\s*|\s+(?:at|@)\s+|\s*[,–—]\s+|\s+-\s+|\s*·\s*")
TRAILING_LINK_LABEL = re.compile(r"\s+(?:company|website|site|link|profile)$", re.I)


def _parts(text: str) -> List[str]:
    """Header fragments, split at separators that are NOT inside brackets ("Employer (Registered Entity Pvt Ltd)" stays
    whole). A hyperlink label left after a job title ("… Manager Company", where the link glyph was lost) is dropped."""
    out, depth, start = [], 0, 0
    for m in SPLIT.finditer(text):
        depth = text[:m.start()].count("(") - text[:m.start()].count(")")
        if depth > 0:
            continue
        out.append(text[start:m.start()])
        start = m.end()
    out.append(text[start:])
    parts = [p for p in (_clean(x) for x in out) if p]
    return [TRAILING_LINK_LABEL.sub("", p) if ROLE_WORDS.search(p) and len(p.split()) > 2 else p for p in parts]


def _role_score(p: str) -> int:
    role = ROLE_WORDS.search(p) is not None
    return (3 if role else 0) - (4 if ORG_WORDS.search(p) else 0) - (1 if WEAK_ORG.search(p) and not role else 0) \
        - (5 if LOCATION.match(p) else 0)


def is_descriptor(p: str) -> bool:
    """A phrase that describes a kind of business ("Payments Platform & Fintech Company", "a leading SaaS platform")
    rather than naming one. A legal form ("Pvt Ltd") always makes it a name."""
    if LEGAL_FORM.search(p):
        return False
    words = p.split()
    hits = len(DESCRIPTOR.findall(p))
    joined = "&" in p or " and " in p.lower()
    generic_end = re.search(r"\b(company|startup|start-up|firm|agency|platform|provider)$", p, re.I) is not None
    return hits >= 2 and (len(words) >= 4 or joined or p[:1].islower()) \
        or bool(re.match(r"(?i)^(a|an|the)\s", p)) and hits >= 1 \
        or generic_end and (joined or len(words) >= 4)                  # "Design Systems & Developer Tools Startup"


def _employer_score(p: str) -> int:
    return (3 if LEGAL_FORM.search(p) else 0) + (1 if ORG_WORDS.search(p) or WEAK_ORG.search(p) else 0) - (5 if is_descriptor(p) else 0) \
        - (2 if len(p.split()) > 6 else 0)


def _assign(parts: List[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(title, employer, location) from header fragments. Nothing is guessed: unplaced fragments are dropped. A phrase
    describing the business is never taken as the employer's name; if it is all there is, the employer stays empty
    (the job is flagged for the user)."""
    loc = next((p for p in parts if LOCATION.match(p)), None)
    rest = [p for p in parts if p != loc and not CONTACT.search(p)]
    if not rest:
        return None, None, loc
    ranked = sorted(rest, key=_role_score, reverse=True)
    title = ranked[0] if _role_score(ranked[0]) > 0 else None
    others = [p for p in rest if p != title]
    employer = None
    if others:
        best = max(others, key=lambda p: (_employer_score(p), -others.index(p)))
        employer = None if is_descriptor(best) else best
    if title is None and len(rest) == 1:
        employer = None if ROLE_WORDS.search(rest[0]) or is_descriptor(rest[0]) else rest[0]
    return title, employer, loc


def _etype(title: Optional[str]) -> str:
    t = (title or "").lower()
    if re.search(r"\bintern(ship)?\b|\btrainee\b", t):
        return "internship"
    if re.search(r"\bfreelanc", t):
        return "freelance"
    if re.search(r"\b(co-?)?founder\b", t):
        return "founder"
    if re.search(r"\bcontract(or)?\b", t):
        return "contract"
    return "unknown"


def experience(lines: List[Line]) -> List[Job]:
    return experience_with_issues(lines)[0]


def _prose_fragment(text: str) -> bool:
    """A wrapped bullet or a sentence, not a job header. These stay with the role they were written under."""
    t = text.strip()
    if not t:
        return False
    if t[:1].islower() or ends_sentence(t):
        return True
    return re.match(
        r"(?i)^(worked|supported|partnered|collaborated|owned|built|led|helped|raised|improved|did|shipped|launched)\b",
        t) is not None


def _possible_role(text: str) -> bool:
    m = RANGE.search(text) or SINCE.search(text)
    return bool(m) and bool(ROLE_WORDS.search(text) or ORG_WORDS.search(text) or LEGAL_FORM.search(text))


# ----------------------------------------------------------------------------- stage 1: employment-record segmentation
class Segment:
    """One employment record before its fields are read: the header lines above its anchor, the anchor (a dated line, or
    for an undated role the first bullet), the text after the dates on a one-paragraph entry, and its body. Built from
    layout and structure only (dated anchors, header-like lines, bullets, paragraphs); no field is interpreted here."""

    def __init__(self, header: List[Para], anchor: Optional[Para], dated: bool):
        self.header, self.anchor, self.dated = header, anchor, dated
        self.tail: Optional[str] = None
        self.lookahead: Optional[Para] = None          # a short line just below the dates: maybe the title
        self.location: Optional[str] = None
        self.body: List[Para] = []


def segment_experience(paras: List[Para]) -> Tuple[List[Segment], List[Para], List[Para]]:
    """(segments, text before the first record, dated paragraphs inside a record that look like another role).
    Every paragraph of the section ends up in exactly one of the three: nothing is dropped."""
    segments: List[Segment] = []
    orphans: List[Para] = []
    suspects: List[Para] = []
    pending: List[Para] = []
    cur: Optional[Segment] = None

    def flush():
        nonlocal pending
        for q in pending:
            (cur.body if cur is not None else orphans).append(q)
        pending = []

    i = 0
    while i < len(paras):
        p = paras[i]
        text = p.text
        m = RANGE.search(text) or SINCE.search(text)
        tail = compact_tail(text) if m and not p.bullet else None
        head = text[:m.end()] if (m and tail) else text
        if m and not p.bullet and len(head) <= 160:
            # A wrapped bullet that wasn't joined ("Did not own the roadmap") must not become the next job's header.
            stray = [q for q in pending if _prose_fragment(q.text)]
            headers = [q for q in pending if q not in stray]
            if cur is not None:
                cur.body.extend(stray)
            else:
                orphans.extend(stray)
            cur = Segment(headers, p, True)
            pending = []
            cur.tail = tail
            nxt = paras[i + 1] if i + 1 < len(paras) else None
            if tail is None and nxt is not None and not nxt.bullet and not RANGE.search(nxt.text) and len(nxt.text) <= 80 \
                    and not ends_sentence(nxt.text):
                cur.lookahead = nxt
            segments.append(cur)
            i += 1
            continue
        if cur is not None and not cur.body and cur.tail is None and not p.bullet and LOCATION.match(text.strip()):
            cur.location = cur.location or text.strip()                 # "Mumbai" under the job header
            if p is cur.lookahead:
                cur.lookahead = None
            i += 1
            continue
        if m and not p.bullet and _possible_role(text):
            flush()
            suspects.append(p)
            i += 1
            continue
        if p.bullet and pending and (cur is None or cur.body or cur.tail):
            # header lines followed by bullets but no dates: an undated role, if those lines name a title.
            # A sentence fragment ("Partnered with the Product Manager") is not a new role; it stays on this one.
            headers = [h for h in pending if not _prose_fragment(h.text)]
            if headers and _assign([x for h in headers for x in _parts(h.text)])[0]:
                prose = [h for h in pending if h not in headers]
                if cur is not None:
                    cur.body.extend(prose)
                else:
                    orphans.extend(prose)
                cur = Segment(headers, None, False)
                pending = []
                segments.append(cur)
        if p.bullet:
            flush()
            (cur.body if cur is not None else orphans).append(p)
            i += 1
            continue
        if text.endswith(":") and len(text) <= 60:                     # "Key achievements:" — a label inside a job
            i += 1
            continue
        if _header_like(p.first) and len(p.lines) == 1 and len(text) <= 90 and p is not (cur.lookahead if cur else None):
            if pending and cur is not None and not cur.body:
                flush()
            pending.append(p)
            if len(pending) > 3:                                        # not a header after all: description lines
                flush()
            i += 1
            continue
        flush()
        (cur.body if cur is not None else orphans).append(p)
        i += 1
    flush()
    return segments, orphans, suspects


# ------------------------------------------------------------------------------------ stage 2: fields of each record
def experience_with_issues(lines: List[Line], section: str = "experience") -> Tuple[List[Job], List[Unresolved]]:
    """Jobs, plus everything in the section that could not be placed (never dropped silently). Stage 1 separates the
    records; stage 2 reads each record's title, employer, location, dates, description and bullets. A record whose title
    can't be read is still a role (flagged, with its source line), so its description and bullets stay with it."""
    segments, orphans, suspects = segment_experience(paragraphs(lines))
    jobs: List[Job] = []
    issues: List[Unresolved] = []
    group_employer: Optional[str] = None
    group_id = 0
    prev: Optional[Job] = None
    for seg in segments:
        head_lines = [h.text for h in seg.header]
        descriptor = None
        if seg.dated:
            text = seg.anchor.text
            m = RANGE.search(text) or SINCE.search(text)
            head = text[:m.end()] if seg.tail else text
            if m.re is RANGE:
                start, end = range_dates(m)
            else:
                start, end = parse_date(m.group("start") or m.group("start2")), DateValue(precision="ongoing")
            frags = [x for x in _parts(head[:m.start()] + " | " + head[m.end():])
                     if not re.fullmatch(r"\(?\s*\d+\s*(yrs?|years?|mos?|months?)[^)]*\)?", x, re.I)]
            title, employer, loc = _assign(frags)
            descriptor = next((x for x in frags if is_descriptor(x)), None)
        else:
            frags, start, end = [], DateValue(), DateValue()
            title, employer, loc = None, None, None
        if seg.header:
            all_parts = [x for h in head_lines for x in _parts(h)]
            t2, e2, l2 = _assign(all_parts)
            descriptor = descriptor or next((x for x in all_parts if is_descriptor(x)), None)
            if title is None:
                title = t2
                if employer is None:
                    employer = e2
            elif employer is None:
                employer = e2 if e2 else (head_lines[-1] if head_lines and not ROLE_WORDS.search(head_lines[-1])
                                          and not is_descriptor(head_lines[-1]) else None)
            loc = loc or l2
        used_lookahead = False
        if seg.lookahead is not None and (title is None or employer is None):
            t3, e3, l3 = _assign(_parts(seg.lookahead.text))
            if title is None and t3:
                title, employer, used_lookahead = t3, employer or e3, True
            elif employer is None and e3 and not t3:
                employer, used_lookahead = e3, True
            loc = loc or l3
        if not seg.dated and title is None:
            continue                                                    # cannot happen (segment needs a title)
        # several dated titles under one company line: same employer, a role progression
        if employer is None and title and not seg.header and prev is not None and group_employer and descriptor is None:
            employer = group_employer
        if seg.header and title and employer and len(head_lines) == 1 and _assign(_parts(head_lines[0]))[0] is None:
            group_employer, group_id = employer, group_id + 1
        elif employer and employer != group_employer:
            group_employer, group_id = employer, group_id + 1
        if seg.dated and title is None and employer is None:
            # a date with nothing else that names a role: shown, with what it supports
            issues.append(Unresolved(kind="possible_role", section=section, text=_clean(seg.anchor.text)[:600],
                                     evidence=seg.anchor.evidence, start=start, end=end))
            for q in seg.body:
                issues.append(Unresolved(kind="unattached_text", section=section, text=_clean(q.text)[:600] or "",
                                         evidence=q.evidence, description=_clean(q.text)))
            prev = None
            continue
        evidence = [e for h in seg.header for e in h.evidence] + (seg.anchor.evidence if seg.anchor else [])
        source = " · ".join(x for x in [*(h.text for h in seg.header), seg.anchor.text[:200] if seg.anchor else None] if x)
        job = Job(title=title, employer=employer, location=loc or seg.location, start=start, end=end, evidence=evidence,
                  group=group_id, source=_clean(source)[:300] if source else None)
        job.current = job.end.precision == "ongoing"
        job.employment_type = _etype(title)
        if not seg.dated:
            job.review += ["start", "end"]
        if title is None:
            job.review.append("title")
        if employer is None:
            job.review.append("employer")
            if descriptor:
                issues.append(Unresolved(kind="employer_unclear", section=section, text=descriptor[:300], evidence=evidence))
        if job.start.precision == "unknown" and "start" not in job.review:
            job.review.append("start")
        if seg.tail:
            _add_text(job, seg.tail, seg.anchor.evidence)
        for q in seg.body:
            if used_lookahead and q is seg.lookahead:
                continue
            _add_body(job, q)
        jobs.append(job)
        prev = job
    for q in orphans:
        t = _clean(q.text)
        if t and len(t) >= 3 and not (CONTACT.search(t) and len(t) < 60) and not re.fullmatch(r"[\W\d]+", t):
            issues.append(Unresolved(kind="unattached_text", section=section, text=t[:600], evidence=q.evidence, description=t))
    for q in suspects:
        t = _clean(q.text)
        m = RANGE.search(t) or SINCE.search(t)
        st, en = range_dates(m) if m and m.re is RANGE else (DateValue(), DateValue())
        tt, ee, _ = _assign(_parts(t[:m.start()])) if m else (None, None, None)
        rest = _clean(t[m.end():]) if m else None
        issues.append(Unresolved(kind="possible_role", section=section, text=t[:600], evidence=q.evidence, title=tt, employer=ee,
                                 start=st, end=en, description=rest if rest and len(rest.split()) >= 3 else None))
    return jobs, issues


def _add_text(job: Job, text: str, evidence: List[int]) -> None:
    text = _clean(text)
    if text and len(text) >= 3 and not (CONTACT.search(text) and len(text) < 60):
        job.description = (job.description + " " + text).strip() if job.description else text
        job.evidence.extend(evidence)


def _add_body(job: Job, p: Para) -> None:
    text = _clean(p.text) if not p.bullet else p.text.strip()
    if not text or len(text) < 3 or CONTACT.search(text) and len(text) < 60:
        return
    if p.bullet:
        job.bullets.append(Bullet(text=text, kind="achievement" if ACHIEVEMENT.search(text) else "responsibility", evidence=p.evidence))
    else:
        job.description = (job.description + " " + text).strip() if job.description else text
        job.evidence.extend(p.evidence)


# -------------------------------------------------------------------------------------------------------- education
def education(lines: List[Line]) -> List[Education]:
    return education_and_courses(lines)[0]


def education_and_courses(lines: List[Line]) -> Tuple[List[Education], List[Line]]:
    """Education entries, plus the lines of courses and certifications listed in the same section ("Education &
    Certifications"): a line with no degree that names a course, certificate or programme is never attached to a degree."""
    out: List[Education] = []
    courses: List[Line] = []
    cur: Optional[Education] = None
    for p in paragraphs(lines):
        text = p.text.strip()
        deg = DEGREE.search(text)
        if not deg and CERT_WORDS.search(text):
            courses.extend(p.lines)
            continue
        inst = INSTITUTION.search(text)
        starts_new = deg and (cur is None or cur.qualification is not None)
        if inst and not deg and cur is not None and cur.institution and cur.qualification:
            starts_new = True                                            # institution-first entries
        if starts_new:
            cur = Education(evidence=[])
            out.append(cur)
        if cur is None:
            if not inst:
                continue
            cur = Education(evidence=[])
            out.append(cur)
        cur.evidence.extend(p.evidence)
        segs = [s for s in (_clean(x) for x in re.split(r"\s*\|\s*|\s*[,–—]\s+|\s+-\s+", text)) if s]
        if deg and cur.qualification is None:
            q = next((s for s in segs if DEGREE.search(s)), text)
            q = RANGE.sub("", q)
            q = re.sub(r"\b(19|20)\d{2}\b", "", q).strip(" ,-–()")
            fm = re.search(r"\b(?:in|of)\s+([A-Z][\w&/ .]+?)(?:\s*\(|$)", q)
            paren = re.search(r"\(([^)]{3,60})\)", q)
            cur.qualification = _clean(re.sub(r"\(([^)]*)\)", "", q)) if paren else _clean(q)
            if fm and not INSTITUTION.search(fm.group(1)):
                cur.field_of_study = _clean(fm.group(1))
                cur.qualification = _clean(cur.qualification[:cur.qualification.lower().rfind(" in ")]
                                           if " in " in cur.qualification.lower() else cur.qualification)
            elif paren and not GRADE.search(paren.group(1)) and not re.search(r"\d", paren.group(1)):
                cur.field_of_study = _clean(paren.group(1))
            elif len(segs) > 1:
                follow = segs[segs.index(next(s for s in segs if DEGREE.search(s))) + 1] if any(DEGREE.search(s) for s in segs) else None
                if follow and not INSTITUTION.search(follow) and not re.search(r"\d", follow) and not LOCATION.match(follow) \
                        and len(follow.split()) <= 5:
                    cur.field_of_study = follow
            cur.level = next((lv for rx, lv in LEVEL if rx.search(text)), "unknown")
        if inst and cur.institution is None:
            cur.institution = next((s for s in segs if INSTITUTION.search(s)), None)
            if cur.institution:
                cur.institution = _clean(RANGE.sub("", re.sub(r"\b(19|20)\d{2}\b", "", cur.institution)))
        if deg and cur.institution is None and len(segs) >= 3:
            # "Degree, Field, Institution, 2016 – 2020, grade": an institution without a keyword is the first remaining
            # name-like segment after the degree and field. Read from the source, but flagged for the user to check.
            i0 = next(i for i, x in enumerate(segs) if DEGREE.search(x))
            for x in segs[i0 + 1:]:
                if x == cur.field_of_study or re.search(r"\d", x) or GRADE.search(x) or LOCATION.match(x) or len(x.split()) > 6:
                    continue
                if _title_case(x) or (len(x.split()) == 1 and x[:1].isupper()):
                    cur.institution = x
                    cur.review.append("institution")
                break
        r = RANGE.search(text)
        if r:
            cur.start, cur.end = range_dates(r)
        else:
            years = re.findall(r"\b(19[5-9]\d|20\d{2})\b", text)
            if years and cur.end.precision == "unknown":
                cur.end = DateValue(value=years[-1], precision="year", raw=years[-1])
        g = GRADE.search(text)
        if g and cur.grade is None:
            cur.grade = g.group(0).strip()
    for e in out:
        if e.qualification is None:
            e.review.append("qualification")
        if e.institution is None:
            e.review.append("institution")
    return [e for e in out if e.qualification or e.institution], courses


# ------------------------------------------------------------------------------------------------------- activities
CERT_DATES = re.compile(r"\(\s*(?:\d{1,2}\s*/\s*)?(?:19|20)\d{2}(?:\s*[–—-]\s*(?:\d{1,2}\s*/\s*)?(?:19|20)\d{2})?\s*\)")


def _listed_certifications(lines: List[Line]) -> Optional[List[Activity]]:
    """A section written as one "|" list ("Title, Provider (07/2025 – 11/2025) | Title, Provider (06/2024) | …"), possibly
    wrapped across lines: split on the separators, never on line ends. None when the section isn't written that way."""
    if not lines or not any(" | " in l.text for l in lines) or any(l.bullet for l in lines):
        return None
    text = " ".join(l.text for l in lines)
    ev = [l.index for l in lines]
    out = []
    for item in (x.strip(" |") for x in text.split("|")):
        if len(item) < 4:
            continue
        years = re.findall(r"\b(19[5-9]\d|20\d{2})\b", item)
        body = _clean(CERT_DATES.sub("", item))
        if not body:
            continue
        head, _, org = body.partition(", ")
        out.append(Activity(kind="certification", title=_clean(head)[:200], organisation=_clean(org)[:200] if org else None,
                            year=int(years[-1]) if years else None, evidence=ev))
    return out


def activities(lines: List[Line], kind: str) -> List[Activity]:
    if kind == "certification":
        listed = _listed_certifications(lines)
        if listed is not None:
            return listed
    out: List[Activity] = []
    cur: Optional[Activity] = None
    for p in paragraphs(lines):
        text = p.text.strip()
        if not text or CONTACT.search(text) and len(text) < 60:
            continue
        year = re.findall(r"\b(19[5-9]\d|20\d{2})\b", text)
        is_item = kind in ("certification", "award") or cur is None or not p.bullet and len(text) <= 100 and not re.search(r"[.!?]$", text)
        if is_item:
            body = re.sub(r"[,(]?\s*\b(19[5-9]\d|20\d{2})\b\)?\s*$", "", RANGE.sub("", text)).strip(" ,–—-|")
            title, org, desc = body, None, None
            # one-paragraph entries: "Accessibility Guild Lead, Vexa. Ran monthly accessibility reviews ..."
            cm = re.match(r"^(?P<head>[^.]{3,120}?)\.\s+(?P<desc>[A-Z].{10,})$", body) if kind != "certification" else None
            if cm:
                parts = [x for x in (_clean(y) for y in re.split(r"\s*[,|–—]\s+|\s+-\s+", cm.group("head"))) if x]
                title, desc = parts[0], cm.group("desc").strip()
                org = parts[1] if len(parts) > 1 and len(parts[1].split()) <= 6 else None
            if kind == "certification":
                segs = [s for s in (_clean(x) for x in re.split(r"\s*\|\s*|\s+[–—]\s+|\s+-\s+|,\s+(?=[A-Z])", body)) if s]
                if len(segs) > 1 and (ORG_WORDS.search(segs[-1]) or WEAK_ORG.search(segs[-1]) or len(segs[-1].split()) >= 2):
                    org = segs[-1]
                    title = body[:body.rfind(org)].strip(" ,–—-|")
            if not title:
                continue
            cur = Activity(kind=kind, title=title[:200], organisation=org[:200] if org else None, evidence=list(p.evidence),
                           year=int(year[-1]) if year else None, description=desc)
            out.append(cur)
        else:
            cur.description = (cur.description + " " + text).strip() if cur.description else text
            cur.evidence.extend(p.evidence)
    return out


# ----------------------------------------------------------------------------------------------------------- skills
def skills(skill_lines: List[Line], jobs: List[Job]) -> List[Skill]:
    found: Dict[str, Skill] = {}

    def add(name: str, recognised: bool, source: str, ev: List[int]):
        key = name.lower()
        s = found.get(key)
        if s is None:
            s = found[key] = Skill(name=name, category=sk.category(name) if recognised else "Other", recognised=recognised)
        if source not in s.sources:
            s.sources.append(source)
        s.evidence.extend(e for e in ev if e not in s.evidence)

    for line in skill_lines:                     # one line at a time: list items are never joined across lines
        for item in sk.split_items(line.text):
            name, rec = sk.from_list_item(item)
            if name:
                add(name, rec, "skills_section", [line.index])
    for j in jobs:
        texts = [(b.text, b.evidence) for b in j.bullets] + ([(j.description, j.evidence)] if j.description else [])
        for t, ev in texts:
            for name in sk.from_sentence(t):
                add(name, True, "experience", ev)
    return list(found.values())[:80]


# ------------------------------------------------------------------------------------------------------------ parse
def parse_document(doc: Document) -> ResumeDraft:
    header, secs = sections(doc)
    draft = ResumeDraft(format=doc.format, layout=doc.layout, pages=doc.pages)
    exp_lines = secs.get("experience", [])
    if not exp_lines and not secs.get("education"):
        draft.warnings.append("no_sections_found")
    draft.work, issues = experience_with_issues(exp_lines)
    draft.education, courses = education_and_courses(secs.get("education", []))
    draft.activities = (activities(secs.get("certifications", []) + courses, "certification") + activities(secs.get("projects", []), "project")
                        + activities(secs.get("volunteering", []), "volunteering") + activities(secs.get("awards", []), "award"))
    draft.skills = skills(secs.get("skills", []), draft.work)
    # Completeness: a dated line naming a role outside the experience section (above the first heading, or in a
    # summary) may be a job the layout hid from us. It is shown for review, never made into a record.
    for where, ls in (("top", header), ("summary", secs.get("ignore", []))):
        for p in paragraphs(ls):
            if not p.bullet and _possible_role(p.text) and len(p.text) <= 300 and not CONTACT.search(p.text):
                issues.append(Unresolved(kind="possible_role", section=where, text=_clean(p.text)[:600], evidence=p.evidence))
    draft.unresolved = issues
    if not draft.work:
        draft.warnings.append("no_work_experience_found")
    if issues:
        draft.warnings.append("unresolved_text")
    return draft


def identity_hints(doc: Document) -> Dict[str, List[str]]:
    """Contact details found anywhere (header or body), used only in memory to drive de-identification."""
    from app.services.deidentification.detector import BARE_PROFILE, EMAIL, PHONE_CANDIDATE, URL
    text = doc.text
    header = [l.text for l in doc.lines[:6]]
    name = next((h.strip() for h in header if re.fullmatch(r"[A-Z][A-Za-z.'\-]+(\s+[A-Z][A-Za-z.'\-]+){1,3}", h.strip())
                 and not ROLE_WORDS.search(h) and not HEADING["experience"].match(h)), None)
    return {"names": [name] if name else [], "emails": sorted(set(EMAIL.findall(text))),
            "phones": sorted({m.group(1).strip() for m in PHONE_CANDIDATE.finditer(text) if 10 <= sum(c.isdigit() for c in m.group(1)) <= 13}),
            "urls": sorted({m.group(0) for m in URL.finditer(text)} | {m.group(0) for m in BARE_PROFILE.finditer(text)})}


# -------------------------------------------------------------------------------------------- canonical career record
def _split(d: DateValue) -> Tuple[Optional[str], str]:
    if d.precision in ("month", "year"):
        return d.value, d.precision
    return None, d.precision if d.precision == "ongoing" else "unknown"


ACTIVITY_TYPE = {"certification": "certification", "project": "independent_project", "volunteering": "volunteering", "award": "award"}


def to_record(draft: ResumeDraft) -> dict:
    """The canonical career record (de-identification FIELDS). Order: chronological sequence (oldest first)."""
    employers: List[dict] = []
    def emp_id(name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        e = next((x for x in employers if x["employer_name"].lower() == name.lower()), None)
        if e is None:
            e = {"employer_id": f"E{len(employers) + 1}", "employer_name": name}
            employers.append(e)
        return e["employer_id"]
    jobs = list(draft.work)                                            # an untitled role is a role (title for the user)
    order = sorted(range(len(jobs)), key=lambda i: (jobs[i].start.value or "9999", -i))
    seq = {i: n for n, i in enumerate(order, 1)}
    employments, evidence, skills_out = [], [], []
    for i, j in enumerate(jobs):
        s, sp = _split(j.start)
        e, ep = _split(j.end)
        employments.append({"sequence_index": seq[i], "employer_id": emp_id(j.employer), "title": j.title,
                            "employment_type": j.employment_type, "start": s, "start_precision": sp, "end": e,
                            "end_precision": ep, "is_current_as_stated": j.current, "source_text": j.source,
                            "review_flags": list(j.review)})
        if j.description:
            evidence.append({"employment_seq": seq[i], "evidence_type": "responsibility", "text": j.description,
                             "evidence_role": "description"})
        for b in j.bullets:
            evidence.append({"employment_seq": seq[i], "evidence_type": b.kind, "text": b.text, "evidence_role": "bullet"})
    education_out = []
    for d in draft.education:
        if not (d.qualification or d.institution):
            continue
        s, sp = _split(d.start)
        e, ep = _split(d.end)
        row = {"qualification_level": d.level, "qualification_title": d.qualification, "field_of_study": d.field_of_study,
               "institution_name": d.institution, "start": s, "start_precision": sp, "end": e, "end_precision": ep}
        if d.grade:
            row["grade_raw"] = d.grade
        education_out.append({k: v for k, v in row.items() if v is not None})
    for s in draft.skills:
        skills_out.append({"text": s.name})
    acts = [{k: v for k, v in {"activity_type": ACTIVITY_TYPE.get(a.kind, "other"), "title": a.title, "organisation": a.organisation,
             "description": a.description, "year": a.year}.items() if v is not None} for a in draft.activities]
    unresolved = []
    for u in draft.unresolved:
        s, sp = _split(u.start)
        e, ep = _split(u.end)
        unresolved.append({k: v for k, v in {"unresolved_kind": u.kind, "section": u.section, "text": u.text, "title": u.title,
                                             "employer": u.employer, "start": s, "start_precision": sp, "end": e,
                                             "end_precision": ep, "description": u.description}.items() if v is not None})
    return {"country_code": "IN", "resume_as_of_precision": "unknown", "employers": employers, "employments": employments,
            "evidence": evidence, "education": education_out, "skills": skills_out, "activities": acts, "unresolved": unresolved}
