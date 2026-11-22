"""Resume-first onboarding: one resume import, reviewed and confirmed in one place (parser v2.1, staged drafts D48).

The data lifecycle, for a new profile and for a profile that already holds records alike:

upload   validate (size; the format is proven by the file's own signature and must agree with the declared type) ->
         server switch and identity-marker key -> current resume-processing consent -> parse in an isolated process
         (resume_parsing.parse_upload; in memory, no disk, no network) -> de-identify (contact details and other direct
         identifiers are removed from every text value) -> save ONE private draft (trace_private.resume_import_drafts via
         trace_import.save_draft): the de-identified extracted record, the parser's warnings and the text it could not
         place, plus metadata (size, SHA-256, format). The original bytes and file name are never stored. NOTHING is
         written to the profile tables: a pending import cannot be read as profile information, used for the current
         position or used for a plan. Identity markers (name, emails, phones, links found in the resume) are used in
         memory; they are kept, sealed, with the draft only when the owner's contribution consent is active.
view     the draft as one consolidated profile with stable item ids (J<seq> jobs, X<n> lines, D<n> education, S<n> skills,
         A<n> activities, U<n> unplaced text). For a profile with records, each item says whether it is new, a date
         update to an existing record, or already in the profile (skipped at confirmation).
confirm  ONE owner transaction, all or nothing: resume metadata row (placeholder path <owner>/<import>.<ext>, no file),
         de-identification admission, then
           new profile:      the kept extracted records are ingested as the resume's own records and confirmed; the
                             user's edits become corrections with history; an employer rename or a role the user adds
                             becomes the user's own statement;
           existing profile: the edited draft is compared with the profile and applied (new records, date corrections
                             with history, new lines for existing roles); what the profile holds is skipped;
         identity markers stored only for an active contribution consent; the draft deleted; processing logged. A retry
         after success finds the metadata row and returns "confirmed" (idempotent, never duplicates). Role mapping then
         runs for the confirmed jobs.
discard  ONE owner transaction: deletes the draft (and its sealed markers), legacy leftovers (see below) and any markers
         nothing needs any more. Confirmed records, the profile and saved plans are never touched.

Legacy imports (made before D48: pending records in the profile tables, or a batch of update proposals) stay viewable,
confirmable and discardable exactly as before, until the user confirms or discards them.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Set, Tuple
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.services.candidate_profile import CandidateProfileService, ConsentRequired, OwnerContext
from app.services.candidate_profile.entries import EmploymentEntry, accept, compare, create_employment
from app.services.candidate_profile.models import (
    EmploymentType, ActivityCorrection, DateInput, EducationCorrection, EmploymentCorrection, EvidenceCorrection, SkillCorrection,
)
from app.services.candidate_profile.repository import CandidateProfileError, RecordNotFound, owner_session
from app.services.candidate_profile.review import check_correctable
from app.services.candidate_profile.service import assemble_profile, content_columns
from app.services.contributions.sealing import sealer_from
from app.services.deidentification import deidentify
from app.services.real_resume import FixtureProfileConflict, RealUploadsDisabled, as_identity
from app.services.resume_extraction.contract import (
    ExtractedActivity, ExtractedEducation, ExtractedEmployer, ExtractedEmployment, ExtractedEvidence, ExtractedSkill,
    ExtractionResult,
)
from app.services.resume_parsing import PARSER_VERSION, DocumentRejected, parse_upload, sniff
from app.services.resume_parsing.skills import VOCAB, category

logger = logging.getLogger(__name__)
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME = {"application/pdf": "pdf", DOCX_MIME: "docx"}
MIME_OF = {"pdf": "application/pdf", "docx": DOCX_MIME}
CONFIDENCE = "0.700"
ACTIVITY = {"certification": "certification", "independent_project": "independent_project", "volunteering": "volunteering",
            "award": "award"}
PENDING_KINDS = ("evidence", "skill", "activity", "education", "employment")      # deletion order (dependents first)
SKILL_ORDER = ["Product", "Programming", "Data and analytics", "Cloud and infrastructure", "Quality and testing", "Design and research",
               "Delivery", "Business and strategy", "Marketing and sales", "Leadership and communication", "Tools", "Other"]


class ImportPending(CandidateProfileError):
    """An unconfirmed import is open: replace it explicitly, or confirm or discard it first (409)."""


class UploadRejected(CandidateProfileError):
    """The file isn't an acceptable resume (size, type, structure). `code` is stable; the message is safe to show."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------------------------------------- edits
class _Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BulletEdit(_Edit):
    id: str = Field(min_length=1, max_length=90)
    text: Optional[str] = Field(default=None, max_length=4000)        # checked by the review contract, not here
    remove: bool = False


class _RoleFields(_Edit):
    """The fields every role in the review shares (one contract for parsed and added roles; checked by `contract`)."""
    title: Optional[str] = Field(default=None, max_length=300)
    employer: Optional[str] = Field(default=None, max_length=300)
    employment_type: Optional[EmploymentType] = None        # stated by the user in the review; never inferred
    role_id: Optional[str] = Field(default=None, max_length=40)   # the user's choice of the closest Trace role
    start: Optional[DateInput] = None
    end: Optional[DateInput] = None                         # precision "ongoing": the user still works there
    description: Optional[str] = Field(default=None, max_length=4000)
    counts_full_time: Optional[bool] = None                 # a founder/freelance role that was the user's full-time work
    side_role: Optional[bool] = None                        # ran alongside the main job (not the career's main line)


class JobEdit(_RoleFields):
    id: str = Field(min_length=1, max_length=90)
    remove: bool = False
    bullets: List[BulletEdit] = Field(default_factory=list, max_length=60)


class NewJob(_RoleFields):
    """A role the user adds in the review (for example from text the parser could not place). The user's own statement."""
    bullets: List[str] = Field(default_factory=list, max_length=30)
    from_unresolved: Optional[str] = Field(default=None, max_length=10)


class EducationEdit(_Edit):
    id: str = Field(min_length=1, max_length=90)
    remove: bool = False
    qualification: Optional[str] = Field(default=None, max_length=200)
    field_of_study: Optional[str] = Field(default=None, max_length=200)
    institution: Optional[str] = Field(default=None, max_length=200)
    start: Optional[DateInput] = None                       # Phase 5: start month and year
    end: Optional[DateInput] = None                         # completion month and year (precision "ongoing": still studying)
    studying: Optional[bool] = None                         # "Currently studying": no completion date


class SkillEdit(_Edit):
    id: str = Field(min_length=1, max_length=90)
    remove: bool = False
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)


class ActivityEdit(_Edit):
    id: str = Field(min_length=1, max_length=90)
    remove: bool = False
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    organisation: Optional[str] = Field(default=None, max_length=200)


class UpdateEdit(_Edit):
    id: str = Field(min_length=1, max_length=90)
    remove: bool = False


class ConfirmRequest(_Edit):
    updates: List[UpdateEdit] = Field(default_factory=list, max_length=60)
    work: List[JobEdit] = Field(default_factory=list, max_length=60)
    added_work: List[NewJob] = Field(default_factory=list, max_length=20)
    ignored: List[str] = Field(default_factory=list, max_length=60)      # unplaced text the user chose to leave out
    education: List[EducationEdit] = Field(default_factory=list, max_length=30)
    skills: List[SkillEdit] = Field(default_factory=list, max_length=150)
    activities: List[ActivityEdit] = Field(default_factory=list, max_length=60)


def _key(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _date(value: Optional[str], precision: Optional[str]) -> DateInput:
    if precision == "ongoing":
        return DateInput(precision="ongoing")
    if value and precision == "month":
        y, m = value.split("-")
        return DateInput(year=int(y), month=int(m), precision="month")
    if value and precision == "year":
        return DateInput(year=int(value[:4]), precision="year")
    return DateInput()


def _record_date(d: DateInput) -> Tuple[Optional[str], str]:
    """A DateInput in the canonical record's form (value, precision)."""
    if d.precision == "month" and d.year and d.month:
        return f"{d.year:04d}-{d.month:02d}", "month"
    if d.precision == "year" and d.year:
        return f"{d.year:04d}", "year"
    return None, "ongoing" if d.precision == "ongoing" else "unknown"


def _date_view(y, m, p) -> dict:
    return {"year": y, "month": m, "precision": p or "unknown"}


def _record_date_view(value: Optional[str], precision: Optional[str]) -> dict:
    d = _date(value, precision)
    return _date_view(d.year, d.month, d.precision)


# ------------------------------------------------------------------------------------- the staged draft's items
class ReviewIncomplete(CandidateProfileError):
    """The review doesn't meet the role contract yet (422). `fields`: [{item, field, message}] for the page to show next
    to each control. Nothing is written."""

    def __init__(self, fields: List[dict]):
        super().__init__("Some details are missing or need checking. Fix the highlighted fields, then confirm again.")
        self.code = "review_incomplete"
        self.fields = fields


class _Draft:
    """The staged record's items by stable key, and the user's edits checked against them (an id that isn't part of
    this draft is refused: RecordNotFound). Every role the parser found is a job, also one whose title it couldn't read."""

    def __init__(self, record: dict):
        self.record = record
        self.employers = {e["employer_id"]: e.get("employer_name") for e in record.get("employers", [])}
        self.jobs = {f"J{j['sequence_index']}": j for j in record.get("employments", [])}
        self.lines = {f"X{n}": x for n, x in enumerate(record.get("evidence", []), 1) if x.get("text")}
        self.education = {f"D{n}": d for n, d in enumerate(record.get("education", []), 1)
                          if d.get("qualification_title") or d.get("institution_name")}
        self.skills = {f"S{n}": s for n, s in enumerate(record.get("skills", []), 1) if s.get("text")}
        self.activities = {f"A{n}": a for n, a in enumerate(record.get("activities", []), 1) if a.get("title")}
        self.unresolved = {f"U{n}": u for n, u in enumerate(record.get("unresolved", []), 1) if u.get("text")}

    def employer_of(self, key: str) -> Optional[str]:
        return self.employers.get(self.jobs[key].get("employer_id"))

    def lines_of(self, job_key: str) -> List[Tuple[str, dict]]:
        seq = self.jobs[job_key]["sequence_index"]
        return [(k, x) for k, x in self.lines.items() if x.get("employment_seq") == seq]

    @staticmethod
    def description_key(lines: List[Tuple[str, dict]]) -> Optional[str]:
        """The line the parser marked as the role's description (draft schema 3); for older drafts, a long first
        responsibility (the rule the review used before)."""
        marked = next((k for k, x in lines if x.get("evidence_role") == "description"), None)
        if marked or any(x.get("evidence_role") for _, x in lines):
            return marked
        if lines and lines[0][1].get("evidence_type") != "achievement" and len(lines[0][1]["text"]) > 160:
            return lines[0][0]
        return None

    def check(self, req: "ConfirmRequest") -> None:
        for e in req.work:
            if e.id not in self.jobs:
                raise RecordNotFound("job is not part of this import")
            own = {k for k, _ in self.lines_of(e.id)}
            if any(b.id not in own for b in e.bullets):
                raise RecordNotFound("bullet is not part of this job")
        for group, items in ((req.education, self.education), (req.skills, self.skills), (req.activities, self.activities)):
            if any(x.id not in items for x in group):
                raise RecordNotFound("item is not part of this import")
        if any(n.from_unresolved and n.from_unresolved not in self.unresolved for n in req.added_work):
            raise RecordNotFound("item is not part of this import")
        if any(u not in self.unresolved for u in req.ignored):
            raise RecordNotFound("item is not part of this import")
        if req.updates:
            raise RecordNotFound("this import has no separate update items")

    def edited_others(self, req: "ConfirmRequest") -> dict:
        """A copy of the record with only education, skills and activities edited, and no employments (an existing
        profile: roles are applied from the plan; education and the rest are compared and applied as before)."""
        rec = copy.deepcopy(self.record)

        def apply(items: List[dict], prefix: str, edits, fields: Dict[str, str]) -> List[dict]:
            by = {f"{prefix}{n}": x for n, x in enumerate(items, 1)}
            gone = set()
            for ed in edits:
                if ed.remove:
                    gone.add(ed.id)
                    continue
                for attr, col in fields.items():
                    v = getattr(ed, attr, None)
                    if v is not None:
                        if isinstance(v, DateInput):
                            by[ed.id][col], by[ed.id][f"{col}_precision"] = _record_date(v)
                        else:
                            by[ed.id][col] = v
                if getattr(ed, "studying", None) is not None:                # education: still studying, or completed
                    by[ed.id]["completion_status"] = "in_progress" if ed.studying else "completed"
                    if ed.studying:
                        by[ed.id]["end"], by[ed.id]["end_precision"] = None, "ongoing"
            return [x for k, x in by.items() if k not in gone]
        rec["education"] = apply(rec.get("education", []), "D", req.education,
                                 {"qualification": "qualification_title", "field_of_study": "field_of_study",
                                  "institution": "institution_name", "start": "start", "end": "end"})

        rec["skills"] = apply(rec.get("skills", []), "S", req.skills, {"name": "text"})
        rec["activities"] = apply(rec.get("activities", []), "A", req.activities, {"title": "title", "organisation": "organisation"})
        rec["employments"], rec["evidence"] = [], []
        rec.pop("unresolved", None)
        return rec


# ------------------------------------------------------------------------------------------- the plan of roles
BULLET_SPLIT = re.compile(r"\s*(?:\n+|(?:^|\s)[•▪●◦‣■►✓✔]\s+)\s*")


def description_lines(text: Optional[str]) -> List[str]:
    """A description as stored lines: one paragraph, or several if the user pasted bulleted lines."""
    return [x.strip(" -–") for x in BULLET_SPLIT.split(text or "") if x and x.strip(" -–")]


@dataclass
class PlannedRole:
    key: str                                   # J<seq> (found in the resume) or N<i> (added in the review)
    title: Optional[str]
    employer: Optional[str]
    employment_type: str
    start: DateInput
    end: DateInput
    description: Optional[str]
    lines: List[Tuple[Optional[str], str, str]]          # kept bullets: (draft key or None, evidence type, text)
    role_id: Optional[str] = None
    counts_full_time: Optional[bool] = None
    side_role: bool = False
    user_stated: bool = False                  # created as the user's own statement, not ingested as extracted
    status: str = "new"                        # existing profile: new | update | present
    from_unresolved: Optional[str] = None
    changed: Dict[str, object] = field(default_factory=dict)   # edits to an extracted role (become corrections)
    description_key: Optional[str] = None
    description_changed: bool = False
    alongside_of: Optional[str] = None         # D50: the main role this one overlaps (derived from type and dates)


def _clean_text(v: Optional[str]) -> Optional[str]:
    v = re.sub(r"\s+", " ", v or "").strip()
    return v or None


def plan(d: _Draft, req: ConfirmRequest, refs: Optional[Dict[str, str]] = None) -> List[PlannedRole]:
    refs = refs or {}
    edits = {e.id: e for e in req.work}
    roles: List[PlannedRole] = []
    for key, j in sorted(d.jobs.items(), key=lambda kv: kv[1]["sequence_index"]):
        e = edits.get(key)
        if e is not None and e.remove:
            continue
        lines = d.lines_of(key)
        dk = d.description_key(lines)
        base_desc = next((x["text"] for k, x in lines if k == dk), None)
        bullet_edits = {b.id: b for b in (e.bullets if e else [])}
        kept = []
        for k, x in lines:
            if k == dk:
                continue
            b = bullet_edits.get(k)
            if b is not None and b.remove:
                continue
            kept.append((k, "achievement" if x.get("evidence_type") == "achievement" else "responsibility",
                         (b.text if b is not None and b.text is not None else x["text"])))
        parsed_title, parsed_employer = _clean_text(j.get("title")), _clean_text(d.employer_of(key))
        title = _clean_text(e.title) if e and e.title is not None else parsed_title
        employer = _clean_text(e.employer) if e and e.employer is not None else parsed_employer
        etype = e.employment_type if e and e.employment_type is not None else (j.get("employment_type") or "unknown")
        start = e.start if e and e.start is not None else _date(j.get("start"), j.get("start_precision"))
        end = e.end if e and e.end is not None else _date(j.get("end"), j.get("end_precision"))
        desc = e.description if e and e.description is not None else base_desc
        r = PlannedRole(key=key, title=title, employer=employer, employment_type=etype, start=start, end=end,
                        description=_clean_text(desc) if desc is not None else None, lines=kept,
                        role_id=e.role_id if e else None, counts_full_time=e.counts_full_time if e else None,
                        side_role=bool(e.side_role) if e else False, status=refs.get(key, "new"), description_key=dk)
        # An extracted employer has no correction history, and a role whose title or employer the parser couldn't read
        # has nothing extracted to correct: such a role becomes the user's own statement.
        r.user_stated = parsed_title is None or parsed_employer is None or _key(employer) != _key(parsed_employer)
        if e is not None:                     # only real changes become corrections (re-sending a value isn't one)
            same = lambda a, b: (a.year, a.month, a.precision) == (b.year, b.month, b.precision)      # noqa: E731
            parsed_start = _date(j.get("start"), j.get("start_precision"))
            parsed_end = _date(j.get("end"), j.get("end_precision"))
            for k, v in (("title_raw", title if title != parsed_title else None), ("employment_type", e.employment_type),
                         ("start", e.start if e.start is not None and not same(e.start, parsed_start) else None),
                         ("end", e.end if e.end is not None and not same(e.end, parsed_end) else None)):
                if v is not None:
                    r.changed[k] = v
        if (j.get("employment_type") or "unknown") == etype:
            r.changed.pop("employment_type", None)
        r.description_changed = (r.description or None) != (_clean_text(base_desc) or None)
        roles.append(r)
    for i, n in enumerate(req.added_work):
        roles.append(PlannedRole(key=f"N{i}", title=_clean_text(n.title), employer=_clean_text(n.employer),
                                 employment_type=n.employment_type or "unknown", start=n.start or DateInput(),
                                 end=n.end or DateInput(), description=_clean_text(n.description),
                                 lines=[(None, "responsibility", t) for t in n.bullets if _clean_text(t)], role_id=n.role_id,
                                 counts_full_time=n.counts_full_time, side_role=bool(n.side_role), user_stated=True,
                                 from_unresolved=n.from_unresolved))
    explicit = {e.id for e in req.work if e.side_role is not None} | {f"N{i}" for i, n in enumerate(req.added_work)
                                                                       if n.side_role is not None}
    mark_alongside(roles, explicit)
    return roles


MAIN_TYPES = ("full_time", "contract")
ALONGSIDE_TYPES = ("part_time", "freelance", "founder", "volunteer")


def _period(r: "PlannedRole") -> Optional[Tuple[int, int]]:
    lo = _lo(r.start)
    hi = MONTH_NOW() if r.end.precision == "ongoing" else _lo(r.end)
    return (lo, hi) if lo is not None and hi is not None and hi >= lo else None


def mark_alongside(roles: List["PlannedRole"], explicit: Set[str] = frozenset()) -> None:
    """Phase 5 (D50): concurrent work needs no checkbox. A part-time, freelance, founder or volunteer role whose dates
    overlap a full-time or contract role runs ALONGSIDE it: it stays in the profile with its own type and dates, and is
    kept out of the main career line (primary_career_sequence = false), so it never becomes the starting point in place
    of the main job. Two full-time or contract roles that overlap are both kept on the main line (the stated order
    decides, as before). A value the user (or an API client) set explicitly is kept. `alongside_of` names the overlap."""
    mains = [(r, _period(r)) for r in roles if r.employment_type in MAIN_TYPES]
    for r in roles:
        if r.key in explicit or r.employment_type not in ALONGSIDE_TYPES:
            continue
        p = _period(r)
        other = next((m for m, q in mains if p and q and m.key != r.key and p[0] <= q[1] and q[0] <= p[1]), None)
        r.side_role = other is not None
        r.alongside_of = other.key if other else None


def _lo(d: DateInput) -> Optional[int]:
    return d.year * 12 + (d.month or 1) if d.year and d.precision in ("month", "year") else None


def replacing(row: Optional[dict]) -> bool:
    """A staged draft uploaded to REPLACE the confirmed profile (D52), not to update it."""
    return bool(row and (row.get("draft") or {}).get("replace_profile"))

def starting_role(roles: List[PlannedRole], existing: Optional[List[Tuple[int, bool, int, Optional[int]]]] = None) -> Optional[str]:
    """The key of the role that will set the user's starting point once confirmed, or None when it is a role already in
    the profile (or none). Mirrors what confirmation stores: roles are kept in stated order (never re-sorted by date);
    extracted roles of a new profile are ingested in the resume's order, and every role created as the user's statement
    is placed before the first role with a later start (entries.place), else last. The starting point is the latest role
    in that order that is in the main career line (not a side role): current-position rules never fall back to an older
    one. `existing`: the profile's roles (sequence, main line, start month index, review-state ok)."""
    rows: List[Tuple[Optional[int], bool, Optional[str]]] = []           # (start lo, main line, key or None)
    for seq, main, lo, _ in sorted(existing or [], key=lambda x: x[0]):
        rows.append((lo, main, None))
    for r in roles:
        if r.status == "present" or r.status == "update" or r.user_stated or existing:
            continue
        rows.append((_lo(r.start), not r.side_role, r.key))
    for r in roles:
        if r.status in ("present", "update") or not (r.user_stated or existing):
            continue
        lo = _lo(r.start)
        at = next((i for i, (rlo, _, _) in enumerate(rows) if lo is not None and rlo is not None and rlo > lo), None)
        item = (lo, not r.side_role, r.key)
        rows.insert(at, item) if at is not None else rows.append(item)
    main = [x for x in rows if x[1]]
    return main[-1][2] if main else None


MONTH_NOW = lambda: date.today().year * 12 + date.today().month          # noqa: E731


def contract(roles: List[PlannedRole], start_key: Optional[str], role_required: bool, req: ConfirmRequest) -> List[dict]:
    """The one data-entry contract for every role the review will save (found or added): title, employer, employment
    type, start month and year, an end month and year unless it is the current role, and a description. Errors name the
    item and field for the page. Roles already in an existing profile ("present", or a date "update") are not re-checked."""
    errors: List[dict] = []

    def err(item, fld, msg):
        errors.append({"item": item, "field": fld, "message": msg})
    seen_sources = set()
    for r in roles:
        if r.status in ("present", "update"):
            continue
        k = r.key
        if not r.title or len(r.title) < 2:
            err(k, "title", "Enter the job title.")
        elif len(r.title) > 200:
            err(k, "title", "Keep the job title under 200 characters.")
        if not r.employer:
            err(k, "employer", "Employer is required.")
        elif len(r.employer) > 200:
            err(k, "employer", "Keep the employer name under 200 characters.")
        if not r.employment_type or r.employment_type == "unknown":
            err(k, "employment_type", "Choose an employment type.")
        s_ok = r.start.precision == "month" and r.start.year and r.start.month
        if not s_ok:
            err(k, "start", "Enter a start month and year.")
        current = r.end.precision == "ongoing"
        e_ok = current or (r.end.precision == "month" and r.end.year and r.end.month)
        if not e_ok:
            err(k, "end", "Enter an end month and year, or tick “I currently work here”.")
        if s_ok and (r.start.year < 1950 or _lo(r.start) > MONTH_NOW()):
            err(k, "start", "The start date can't be in the future.")
        if s_ok and e_ok and not current:
            if _lo(r.end) < _lo(r.start):
                err(k, "end", "The end date is before the start date.")
            elif _lo(r.end) > MONTH_NOW():
                err(k, "end", "The end date is in the future. If you still work here, clear the end date and tick “I currently work here”.")
        desc = description_lines(r.description)
        words = len(re.findall(r"[A-Za-z][A-Za-z'’-]+", " ".join(desc)))
        if not desc or len(" ".join(desc)) < 20 or words < 4:
            err(k, "description", "Add a short description of this role: at least one sentence (20 characters) about what "
                                  "you did.")
        elif any(len(x) > 1000 for x in desc):
            err(k, "description", "Each paragraph or bullet in the description must be under 1,000 characters.")
        elif len(desc) + len(r.lines) > 30:
            err(k, "description", "A role can hold up to 30 lines. Remove or combine some.")
        for bk, _, text in r.lines:
            if not text or len(text.strip()) < 3 or len(text) > 1000:
                err(k, f"bullet:{bk}" if bk else "bullets", "Each line must be 3 to 1,000 characters.")
        if r.from_unresolved:
            if r.from_unresolved in seen_sources:
                err(k, "from_unresolved", "This text has already been added as a role.")
            if r.from_unresolved in req.ignored:
                err(k, "from_unresolved", "This text is marked to be left out. Undo that, or remove this role.")
            seen_sources.add(r.from_unresolved)
        if r.counts_full_time is not None and r.employment_type not in ("founder", "freelance", "part_time"):
            r.counts_full_time = None                                      # only asked for these types; ignored otherwise
    if start_key and role_required and not next((r.role_id for r in roles if r.key == start_key), None):
        err(start_key, "role_id", "Choose the closest Trace role for your starting position.")
    return errors


@dataclass
class PlannedEducation:
    key: str
    qualification: Optional[str]
    institution: Optional[str]
    field_of_study: Optional[str]
    start: DateInput
    end: DateInput
    studying: bool


def plan_education(d: "_Draft", req: ConfirmRequest) -> List[PlannedEducation]:
    """The qualifications the review will save, with the user's edits applied (removed ones left out)."""
    edits = {e.id: e for e in req.education}
    out = []
    for key, x in d.education.items():
        e = edits.get(key)
        if e is not None and e.remove:
            continue
        pick = lambda attr, col: (getattr(e, attr) if e is not None and getattr(e, attr) is not None else x.get(col))  # noqa: E731
        start = e.start if e is not None and e.start is not None else _date(x.get("start"), x.get("start_precision"))
        end = e.end if e is not None and e.end is not None else _date(x.get("end"), x.get("end_precision"))
        studying = bool(e.studying) if e is not None and e.studying is not None else end.precision == "ongoing"
        out.append(PlannedEducation(key=key, qualification=_clean_text(pick("qualification", "qualification_title")),
                                    institution=_clean_text(pick("institution", "institution_name")),
                                    field_of_study=_clean_text(pick("field_of_study", "field_of_study")),
                                    start=start, end=DateInput(precision="ongoing") if studying else end, studying=studying))
    return out


def education_contract(items: List[PlannedEducation]) -> List[dict]:
    """Phase 5: a qualification needs its name, institution, start month and year, and a completion month and year
    unless the user says they are still studying. Field of study stays optional (not every qualification has one).
    Nothing is invented: a year-only date from the resume asks the user for the month."""
    errors: List[dict] = []

    def err(item, fld, msg):
        errors.append({"item": item, "field": fld, "message": msg})
    for q in items:
        if not q.qualification:
            err(q.key, "qualification", "Enter the qualification.")
        if not q.institution:
            err(q.key, "institution", "Enter the university, college or school.")
        s_ok = q.start.precision == "month" and q.start.year and q.start.month
        if not s_ok:
            err(q.key, "start", "Enter the start month and year.")
        elif _lo(q.start) > MONTH_NOW():
            err(q.key, "start", "The start date can't be in the future.")
        if not q.studying:
            e_ok = q.end.precision == "month" and q.end.year and q.end.month
            if not e_ok:
                err(q.key, "end", "Enter the completion month and year, or tick “Currently studying”.")
            elif s_ok and _lo(q.end) < _lo(q.start):
                err(q.key, "end", "The completion date is before the start date.")
            elif _lo(q.end) > MONTH_NOW():
                err(q.key, "end", "The completion date is in the future. If you're still studying, tick “Currently studying”.")
    return errors


def _role_view(o) -> dict:
    """A title's role choices for the review. `suggested_role_id` is the exact alias match (outcome "automatic": mapped
    after confirmation anyway) or, failing that, the role suggested from the title's words (D50: shown preselected and
    labelled, saved only as the user's own choice)."""
    choices = [{"role_id": c.role_id, "title": c.canonical_title, "suggested": c.source == "alias_option"}
               for c in list(o.alias_options) + list(o.other_roles)]
    return {"suggested_role_id": o.automatic_role_id or o.suggested_role_id, "outcome": o.outcome,
            "basis": "alias" if o.automatic_role_id else ("title_words" if o.suggested_role_id else None),
            "matched_words": o.suggestion_basis, "choices": choices}


class ResumeImportService:
    def __init__(self, conn: psycopg.Connection, service: CandidateProfileService, settings: Settings):
        self._conn = conn
        self._service = service
        self._settings = settings

    # ------------------------------------------------------------------------------------------------ queries
    @staticmethod
    def _open_import(repo, ctx: OwnerContext) -> Optional[dict]:
        return repo.open_import(ctx.candidate_profile_id)

    @staticmethod
    def _pending(repo, ctx: OwnerContext, resume_id: UUID) -> Dict[str, List[dict]]:
        return repo.import_rows(ctx.candidate_profile_id, resume_id)

    @staticmethod
    def _has_records(repo, ctx: OwnerContext) -> bool:
        return repo.has_any_records(ctx.candidate_profile_id)

    @staticmethod
    def _open_batch(repo, ctx: OwnerContext) -> List[dict]:
        rows = [r for r in repo.open_proposals(ctx.candidate_profile_id) if r["source"] == "resume_update"]
        if not rows:
            return []
        latest = max(rows, key=lambda r: r["created_at"])["batch_id"]
        return [r for r in rows if r["batch_id"] == latest]

    def _legacy(self, repo, ctx: OwnerContext) -> int:
        """Leftovers of earlier attempts: open pasted-text suggestions and unconfirmed (pending or rejected) records that
        aren't part of the open legacy import. Cleared only by an explicit "start over"."""
        n = sum(1 for r in repo.open_proposals(ctx.candidate_profile_id) if r["source"] != "resume_update")
        imp = self._open_import(repo, ctx)
        return n + repo.unconfirmed_outside(ctx.candidate_profile_id, imp["resume_id"] if imp else None)

    # --------------------------------------------------------------------------------------------------- view
    def view(self, ctx: OwnerContext) -> dict:
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            # Markers that nothing needs any more (no active contribution consent, or nothing admitted) are removed
            # here as well as on every discard and consent change: cleanup never waits for a maintenance run.
            repo.forget_identity_markers()
            row = repo.import_draft(ctx.candidate_profile_id)
            legacy = self._legacy(repo, ctx)
            if row is not None:
                state = self._state(repo, ctx, row["draft"]["record"], replacing(row))
            else:
                imp = self._open_import(repo, ctx)
        if row is not None:
            return self._draft_view(ctx, row, state, legacy)
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            if imp is None:
                batch = self._open_batch(repo, ctx)
                if not batch:
                    return {"status": "none", "legacy_suggestions": legacy}
                titles = {}
                for r in batch:
                    if r["target_record_id"]:
                        for kind in ("employment", "education"):
                            t = repo.record_title(kind, ctx.candidate_profile_id, r["target_record_id"])
                            if t:
                                titles[r["target_record_id"]] = t
                return self._update_view(batch, titles, legacy)
            rows = self._pending(repo, ctx, imp["resume_id"])
        return self._legacy_view(ctx, imp, rows, legacy)

    @staticmethod
    def _state(repo, ctx: OwnerContext, record: dict, replace: bool = False) -> dict:
        """What the review needs from the profile: new, update or replace mode, each item's status against the profile,
        and the profile's own roles (for where a new role will sit in the stated order). A replacement (D52) is reviewed
        like a new profile: nothing is compared with, merged into or placed among the records it will replace."""
        pid = ctx.candidate_profile_id
        if replace:
            return {"mode": "replace", "refs": {}, "existing": [], "replaces": repo.career_profile_counts(pid)}
        if not repo.has_any_records(pid):
            return {"mode": "new", "refs": {}, "existing": []}
        profile = assemble_profile(repo.fetch_profile_rows(pid))
        refs: Dict[str, str] = {}
        compare(record, profile, refs)
        existing = [(e.sequence_index, bool(e.primary_career_sequence), e.start.year * 12 + (e.start.month or 1) if e.start.year else None, True)
                    for e in profile.employment_history if e.provenance.review_status != "rejected"]
        return {"mode": "update", "refs": refs, "existing": existing}

    def _role_options(self, ctx: OwnerContext, title: Optional[str]) -> Optional[dict]:
        return self._roles_options(ctx, [title]).get(title) if title else None

    def _roles_options(self, ctx: OwnerContext, titles: List[Optional[str]]) -> Dict[str, dict]:
        """Role choices per title (alias index and taxonomy read once)."""
        titles = [t for t in titles if t]
        if not titles:
            return {}
        try:
            return {t: _role_view(o) for t, o in self._service.role_options_for_titles(ctx, titles).items()}
        except CandidateProfileError:
            return {}

    def _draft_view(self, ctx: OwnerContext, row: dict, state: dict, legacy: int) -> dict:
        d = _Draft(row["draft"]["record"])
        mode, refs = state["mode"], state["refs"]
        status = lambda k: refs.get(k, "new") if mode == "update" else "new"          # noqa: E731
        roles = plan(d, ConfirmRequest(), refs if mode == "update" else None)
        start_key = starting_role(roles, state["existing"])
        planned = {r.key: r for r in roles}
        work = []
        for k, j in sorted(d.jobs.items(), key=lambda kv: -kv[1]["sequence_index"]):
            lines = d.lines_of(k)
            dk = d.description_key(lines)
            employer = d.employer_of(k)
            review = [f for f in (j.get("review_flags") or []) if f in ("title", "employer", "start", "end")]
            for f, missing in (("title", not j.get("title")), ("employer", not employer),
                               ("start", j.get("start_precision") != "month")):
                if missing and f not in review:
                    review.append(f)
            work.append({
                "id": k, "title": j.get("title"), "employer": employer, "employment_type": j.get("employment_type") or "unknown",
                "start": _record_date_view(j.get("start"), j.get("start_precision")),
                "end": _record_date_view(j.get("end"), j.get("end_precision")), "current": j.get("end_precision") == "ongoing",
                "description": [{"id": x_k, "text": x["text"], "status": status(x_k)} for x_k, x in lines if x_k == dk],
                "bullets": [{"id": x_k, "text": x["text"], "kind": x.get("evidence_type"), "status": status(x_k)}
                            for x_k, x in lines if x_k != dk],
                "review": review, "source": j.get("source_text"), "status": status(k), "starting": k == start_key,
                "alongside_of": planned[k].alongside_of if k in planned else None,
            })
        options = self._roles_options(ctx, [w["title"] for w in work if w["status"] not in ("present",)])
        for w in work:                               # every role can be matched; only the starting role must be (D50)
            if w["title"] in options:
                w["role"] = options[w["title"]]
        groups: Dict[str, List[dict]] = {}
        for k, s in d.skills.items():
            name = s["text"]
            groups.setdefault(category(name), []).append({"id": k, "name": name, "recognised": name in VOCAB, "status": status(k)})
        return {
            "status": "ready", "mode": mode, "staged": True, "import_id": row["import_id"], "uploaded_at": row["created_at"],
            "replaces": state.get("replaces"),
            "format": row["format"], "work": work, "updates": [], "starting_role": start_key,
            "education": [{"id": k, "qualification": e.get("qualification_title"), "level": e.get("qualification_level"),
                           "field_of_study": e.get("field_of_study"), "institution": e.get("institution_name"),
                           "start": _record_date_view(e.get("start"), e.get("start_precision")),
                           "end": _record_date_view(e.get("end"), e.get("end_precision")),
                           "studying": e.get("end_precision") == "ongoing", "status": status(k)}
                          for k, e in d.education.items()],
            "skills": [{"category": c, "items": groups[c]} for c in SKILL_ORDER if c in groups],
            "activities": [{"id": k, "kind": a.get("activity_type"), "title": a["title"], "organisation": a.get("organisation"),
                            "description": a.get("description"), "year": a.get("year"), "status": status(k)}
                           for k, a in d.activities.items()],
            "unresolved": [{"id": k, "kind": u.get("unresolved_kind"), "section": u.get("section"), "text": u["text"],
                            "suggested": {"title": u.get("title"), "employer": u.get("employer"),
                                          "start": _record_date_view(u.get("start"), u.get("start_precision")),
                                          "end": _record_date_view(u.get("end"), u.get("end_precision")),
                                          "description": u.get("description")}}
                           for k, u in d.unresolved.items()],
            "warnings": list(row["warnings"] or []),
            "legacy_suggestions": legacy,
        }

    def check(self, ctx: OwnerContext, import_id: UUID, req: ConfirmRequest) -> dict:
        """Read-only: what confirming these edits now would need. Which role sets the starting point (and its role
        choices), and every field still missing or invalid. The page calls it as the user edits; nothing is written."""
        pid = ctx.candidate_profile_id
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            row = repo.import_draft(pid)
            if row is None or row["import_id"] != import_id:
                raise RecordNotFound("import not found")
            d = _Draft(row["draft"]["record"])
            d.check(req)
            state = self._state(repo, ctx, d.record, replacing(row))
        roles = plan(d, req, state["refs"])
        start_key = starting_role(roles, state["existing"])
        start = next((r for r in roles if r.key == start_key), None)
        live = [r for r in roles if r.status not in ("present", "update") and r.title]
        by_title = self._roles_options(ctx, [r.title for r in live])
        options = by_title.get(start.title) if start and start.title else None
        errors = contract(roles, start_key, bool(options and options["choices"]), req)
        errors += education_contract([q for q in plan_education(d, req) if state["refs"].get(q.key, "new") == "new"])
        return {"starting_role": start_key, "role": options, "errors": errors,
                "role_options": {r.key: by_title[r.title] for r in live if r.title in by_title},
                "alongside": {r.key: r.alongside_of for r in roles if r.alongside_of},
                "roles": sum(1 for r in roles if r.status != "present")}

    def _legacy_view(self, ctx: OwnerContext, imp: dict, rows: Dict[str, List[dict]], legacy: int) -> dict:
        """An import made before D48 (pending records in the profile tables), shown as before until confirmed or discarded."""
        by_job: Dict[UUID, List[dict]] = {}
        for x in rows["evidence"]:
            by_job.setdefault(x["candidate_employment_id"], []).append(x)
        work = []
        for e in rows["employment"]:
            items = by_job.get(e["id"], [])
            desc = [x for x in items if (x["source_location"] or "").endswith("description")]
            work.append({
                "id": e["id"], "title": e["title_raw"], "employer": e["employer_name"], "employment_type": e["employment_type"],
                "start": _date_view(e["start_year"], e["start_month"], e["start_precision"]),
                "end": _date_view(e["end_year"], e["end_month"], e["end_precision"]), "current": bool(e["is_current_as_stated"]),
                "description": [{"id": x["id"], "text": x["evidence_text"]} for x in desc],
                "bullets": [{"id": x["id"], "text": x["evidence_text"], "kind": x["evidence_type"]} for x in items if x not in desc],
                "review": [f for f, missing in (("title", not e["title_raw"]), ("employer", not e["employer_name"]),
                                                ("start", e["start_precision"] in (None, "unknown"))) if missing],
            })
        if work:
            try:
                work[0]["role"] = _role_view(self._service.role_mapping_options(ctx, work[0]["id"]))
            except CandidateProfileError:
                work[0]["role"] = None
        groups: Dict[str, List[dict]] = {}
        for s in rows["skill"]:
            name = s["skill_text_raw"]
            groups.setdefault(category(name), []).append({"id": s["id"], "name": name, "recognised": name in VOCAB})
        return {
            "status": "ready", "mode": "new", "staged": False, "import_id": imp["resume_id"], "uploaded_at": imp["uploaded_at"],
            "work": work, "updates": [], "unresolved": [], "warnings": [],
            "education": [{"id": d["id"], "qualification": d["qualification_title_raw"], "level": d["qualification_level"],
                           "field_of_study": d["field_of_study"], "institution": d["institution_name"],
                           "end": _date_view(d["end_year"], d["end_month"], d["end_precision"])} for d in rows["education"]],
            "skills": [{"category": c, "items": groups[c]} for c in SKILL_ORDER if c in groups],
            "activities": [{"id": a["id"], "kind": a["activity_type"], "title": a["title_raw"], "organisation": a["organisation_raw"],
                            "description": a["description_raw"], "year": a["end_year"]} for a in rows["activity"]],
            "legacy_suggestions": legacy,
        }

    @staticmethod
    def _update_view(batch: List[dict], titles: Dict, legacy: int) -> dict:
        """A legacy batch of update proposals (made before D48)."""
        dv = lambda d: _date_view((d or {}).get("year"), (d or {}).get("month"), (d or {}).get("precision"))  # noqa: E731
        work, extra, updates, education, groups, activities = [], {}, [], [], {}, []
        for r in batch:
            pr, pid = r["proposed"] or {}, str(r["proposal_id"])
            kind, state = r["record_kind"], r["match_state"]
            if kind == "employment" and state == "new":
                lines = [("responsibility", t) for t in pr.get("responsibilities") or []] + \
                        [("achievement", t) for t in pr.get("achievements") or []]
                work.append({"id": pid, "title": pr.get("title_raw"), "employer": pr.get("employer_name"),
                             "employment_type": pr.get("employment_type") or "unknown",
                             "start": dv(pr.get("start")), "end": dv(pr.get("end")), "current": (pr.get("end") or {}).get("precision") == "ongoing",
                             "description": [], "bullets": [{"id": f"{pid}:{i}", "text": t, "kind": k} for i, (k, t) in enumerate(lines)],
                             "review": [], "existing": False})
            elif kind == "evidence":
                tid = str(r["target_record_id"])
                card = extra.setdefault(tid, {"id": f"existing:{tid}", "title": titles.get(r["target_record_id"]), "employer": None,
                                              "existing": True, "description": [], "bullets": [], "review": [],
                                              "start": dv(None), "end": dv(None), "current": False, "employment_type": "unknown"})
                card["bullets"].append({"id": pid, "text": pr.get("evidence_text"), "kind": pr.get("evidence_type")})
            elif state == "possible_update":
                updates.append({"id": pid, "kind": kind, "title": titles.get(r["target_record_id"]),
                                "changes": {k: (dv(v) if isinstance(v, dict) else v) for k, v in pr.items()}})
            elif kind == "education":
                education.append({"id": pid, "qualification": pr.get("qualification_title_raw"), "level": pr.get("qualification_level"),
                                  "field_of_study": pr.get("field_of_study"), "institution": pr.get("institution_name"), "end": dv(pr.get("end"))})
            elif kind == "skill":
                name = pr.get("skill_text_raw")
                groups.setdefault(category(name), []).append({"id": pid, "name": name, "recognised": name in VOCAB})
            elif kind == "activity":
                activities.append({"id": pid, "kind": pr.get("activity_type"), "title": pr.get("title_raw"),
                                   "organisation": pr.get("organisation_raw"), "description": pr.get("description_raw"),
                                   "year": (pr.get("end") or {}).get("year")})
        return {"status": "ready", "mode": "update", "staged": False, "import_id": batch[0]["batch_id"], "uploaded_at": batch[0]["created_at"],
                "work": work + list(extra.values()), "updates": updates, "education": education, "unresolved": [], "warnings": [],
                "skills": [{"category": c, "items": groups[c]} for c in SKILL_ORDER if c in groups], "activities": activities,
                "legacy_suggestions": legacy}

    # ------------------------------------------------------------------------------------------------- upload
    def check_file(self, data: bytes, content_type: Optional[str]) -> str:
        """The file's format, proven by its own content: a PDF signature, or a ZIP holding a Word document (checked again,
        fully, before parsing). The declared type must name the same format; neither the declared type nor a file
        extension is ever trusted alone."""
        if not data:
            raise UploadRejected("empty", "The file is empty.")
        if len(data) > self._settings.max_resume_bytes:
            mb = self._settings.max_resume_bytes / 1_048_576
            raise UploadRejected("too_large", f"The file is larger than {mb:.0f} MB. Upload a smaller resume.")
        declared = MIME.get((content_type or "").split(";")[0].strip().lower())
        actual = sniff(data)
        if declared is None or actual is None or declared != actual:
            raise UploadRejected("unsupported_format", "Upload a PDF or Word (.docx) resume.")
        return actual

    def upload(self, ctx: OwnerContext, data: bytes, content_type: Optional[str], replace: bool = False,
               replace_profile: bool = False) -> dict:
        """replace: replace an import still waiting for review. replace_profile (D52): the new resume is to REPLACE the
        confirmed profile once reviewed and confirmed; until then the confirmed profile is untouched."""
        fmt = self.check_file(data, content_type)
        if not self._settings.real_resume_uploads_available:
            raise RealUploadsDisabled("resume upload is not enabled on this server")
        sealer = sealer_from(self._settings)
        if not self._service.consent_state(ctx.owner_user_id)["active"]:
            raise ConsentRequired("agree to resume analysis before uploading")
        with self._conn.transaction():
            row = self._conn.execute("select is_fixture_profile from trace_contrib.contribution_context(%s)", (ctx.owner_user_id,)).fetchone()
        if row and row[0]:
            raise FixtureProfileConflict("this profile holds a synthetic sample; it cannot take your own resume")
        sha256 = hashlib.sha256(data).hexdigest()
        pid = ctx.candidate_profile_id
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            draft = repo.import_draft(pid)
            open_imp = self._open_import(repo, ctx)
            batch = self._open_batch(repo, ctx)
            contributing = repo.contribution_consent_active()
            replace_profile = replace_profile and repo.has_any_records(pid)    # nothing to replace: an ordinary import
        if (draft is not None or open_imp is not None or batch) and not replace:
            raise ImportPending("you have an import waiting for review; replace it, or confirm or discard it first")
        if draft is not None and draft["content_sha256"] == sha256 and replacing(draft) == replace_profile:
            return {"status": "unchanged", "skipped": {}, "warnings": []}
        try:
            parsed = parse_upload(data)
        except DocumentRejected as exc:
            self._log("failed", ["unreadable"], ctx)
            raise UploadRejected(exc.code, str(exc)) from None
        record = parsed.record
        if not record["employments"] and not record["education"] and not record.get("unresolved"):
            self._log("failed", ["no_career_content"], ctx)
            raise UploadRejected("no_career_content", "No work experience or education could be found in this file. Check that it's your "
                                                      "resume and that its text can be selected, then try again.")
        identity = as_identity(self._conn, ctx.owner_user_id, parsed.identity, sealer)
        # Ambiguities (an employer or institution that contains the owner's own name part) concern the SHARED pool only:
        # here they are the user's own facts in their private profile, reviewed by them before confirmation. The
        # contribution processor de-identifies again, without this approval, and quarantines them for a reviewer (D47).
        # Direct identifiers still fail the import.
        result = deidentify(record, identity, data if fmt == "pdf" else None, ambiguities_approved=True)
        if result.status == "failed":
            self._log("failed", ["deidentification_failed"], ctx)
            raise UploadRejected("deidentification_failed", "This resume couldn't be checked for personal details automatically, so "
                                                            "nothing was stored. Try a version without contact details in the body text.")
        if result.status == "needs_review":
            self._log("failed", ["deidentification_failed"], ctx)
            raise UploadRejected("needs_privacy_review", "Part of this resume looks like personal details we can't safely separate "
                                                         "from your experience, so nothing was stored. Remove names of other people and "
                                                         "contact details from the body text and try again.")
        # Identity markers: sealed and kept with the draft only for an active contribution consent; otherwise used in
        # memory for this de-identification and discarded.
        sealed = key_id = None
        if contributing and sealer is not None:
            ident = result.identity or identity
            sealed = sealer.seal(ctx.owner_user_id, {"names": ident.names(), "emails": list(ident.emails), "phones": list(ident.phones),
                                                     "urls": list(ident.urls), "handles": list(ident.handles)})
            key_id = sealer.key_id
        warnings = list(parsed.draft.warnings)
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            if not replace_profile and repo.has_any_records(pid) and not result.record.get("unresolved"):
                items, present = compare(result.record, assemble_profile(repo.fetch_profile_rows(pid)))
                if not items:                                  # everything is already in the profile: nothing to review
                    return {"status": "nothing_new", "mode": "update", "skipped": {"already_in_profile": present}, "warnings": warnings}
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            if open_imp is not None or batch:                  # replacing a legacy import: removed in the same transaction
                self._discard_legacy_import(repo, ctx, include_leftovers=False)
            repo.save_import_draft(pid, {"record": result.record, "layout": parsed.draft.layout,
                                         **({"replace_profile": True} if replace_profile else {})}, PARSER_VERSION,
                                   result.report["pipeline_version"], fmt, MIME_OF[fmt], len(data), sha256, warnings, sealed, key_id)
            repo.forget_identity_markers()
        return {"status": "imported", "mode": "staged", "skipped": {}, "warnings": warnings}

    def _log(self, outcome: str, reasons: List[str], ctx: OwnerContext) -> None:
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            repo.log_resume_processing(outcome, reasons)

    def _extraction(self, record: dict, sha256: str, keep=lambda key: True) -> Tuple[ExtractionResult, Dict[str, Tuple[str, str]]]:
        """The ingestion payload for the kept items, and where each kept item lands: key -> (kind, source_location)."""
        where: Dict[str, Tuple[str, str]] = {}
        jobs = [j for j in record.get("employments", []) if j.get("title") and keep(f"J{j['sequence_index']}")]
        used = {j.get("employer_id") for j in jobs}
        employers = [ExtractedEmployer(key=e["employer_id"], employer_name=e.get("employer_name"), source_location="Experience, employer line",
                                       extraction_confidence=CONFIDENCE) for e in record.get("employers", []) if e["employer_id"] in used]
        employments = []
        for j in jobs:
            loc = f"Experience, entry {j['sequence_index']}"
            where[f"J{j['sequence_index']}"] = ("employment", loc)
            employments.append(ExtractedEmployment(key=f"J{j['sequence_index']}", sequence_index=j["sequence_index"], employer_key=j.get("employer_id"),
                                                   title_raw=j["title"], employment_type=j.get("employment_type") or "unknown",
                                                   start=_date(j.get("start"), j.get("start_precision")), end=_date(j.get("end"), j.get("end_precision")),
                                                   source_location=loc, extraction_confidence=CONFIDENCE))
        seqs = {e.sequence_index for e in employments}
        evidence, counter = [], {}
        for i, x in enumerate(record.get("evidence", []), 1):
            if not x.get("text") or x.get("employment_seq") not in seqs or not keep(f"X{i}"):
                continue
            n = counter[x["employment_seq"]] = counter.get(x["employment_seq"], 0) + 1
            first_long = n == 1 and len(x["text"]) > 160 and x.get("evidence_type") == "responsibility"
            loc = f"Experience, entry {x['employment_seq']}, " + ("description" if first_long else f"bullet {n}")
            where[f"X{i}"] = ("evidence", loc)
            evidence.append(ExtractedEvidence(key=f"X{i}", evidence_type=x.get("evidence_type", "responsibility"), evidence_text=x["text"],
                                              employment_key=f"J{x['employment_seq']}", source_location=loc, extraction_confidence=CONFIDENCE))
        education = []
        for i, d in enumerate(record.get("education", []), 1):
            if not keep(f"D{i}") or not (d.get("qualification_title") or d.get("institution_name")):
                continue
            where[f"D{i}"] = ("education", f"Education, entry {i}")
            education.append(ExtractedEducation(key=f"D{i}", qualification_title_raw=d.get("qualification_title"),
                                                qualification_level=d.get("qualification_level") or "unknown", institution_name=d.get("institution_name"),
                                                field_of_study=d.get("field_of_study"), notes=f"Grade: {d['grade_raw']}" if d.get("grade_raw") else None,
                                                start=_date(d.get("start"), d.get("start_precision")), end=_date(d.get("end"), d.get("end_precision")),
                                                completion_status=("in_progress" if d.get("end_precision") == "ongoing" else
                                                                   d.get("completion_status") or "unknown"),
                                                source_location=f"Education, entry {i}", extraction_confidence=CONFIDENCE))
        activities = []
        for i, a in enumerate(record.get("activities", []), 1):
            if not a.get("title") or not keep(f"A{i}"):
                continue
            where[f"A{i}"] = ("activity", f"Activities, entry {i:02d}")
            activities.append(ExtractedActivity(key=f"A{i}", activity_type=ACTIVITY.get(a.get("activity_type"), "other"), title_raw=a["title"],
                                                organisation_raw=a.get("organisation"), description_raw=a.get("description"),
                                                end=DateInput(year=a["year"], precision="year") if a.get("year") else DateInput(),
                                                source_location=f"Activities, entry {i:02d}", extraction_confidence=CONFIDENCE))
        skills = []
        for i, k in enumerate(record.get("skills", []), 1):
            if not k.get("text") or not keep(f"S{i}"):
                continue
            where[f"S{i}"] = ("skill", f"Skills, item {i:03d}")
            skills.append(ExtractedSkill(skill_text_raw=k["text"], source_location=f"Skills, item {i:03d}", extraction_confidence=CONFIDENCE))
        return ExtractionResult(extractor_name="trace-resume-parser", extractor_version=PARSER_VERSION, source_sha256=sha256,
                                employers=employers, employments=employments, education=education, career_activities=activities,
                                evidence=evidence, skills=skills), where

    # ------------------------------------------------------------------------------------------------ confirm
    def confirm(self, ctx: OwnerContext, import_id: UUID, req: ConfirmRequest) -> dict:
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            staged = repo.import_draft(ctx.candidate_profile_id)
            is_batch = repo.proposal_batch_exists(ctx.candidate_profile_id, import_id)
            confirmed = any(repo.resume_at_path(ctx.candidate_profile_id, f"{ctx.owner_user_id}/{import_id}.{f}") for f in ("pdf", "docx"))
        if staged is not None and staged["import_id"] == import_id:
            return self._confirm_staged(ctx, import_id, req)
        if confirmed:
            return {"status": "confirmed", "already": True}
        if req.added_work:
            raise RecordNotFound("roles can be added only to a staged import")
        return self._legacy_confirm_update(ctx, import_id, req) if is_batch else self._legacy_confirm_new(ctx, import_id, req)

    def _confirm_staged(self, ctx: OwnerContext, import_id: UUID, req: ConfirmRequest) -> dict:
        """ONE owner transaction. The whole contract is checked BEFORE anything is written (ReviewIncomplete, 422, with
        every field to fix); any later failure rolls everything back and leaves the draft as it was."""
        pid = ctx.candidate_profile_id
        role_choice: Dict[UUID, str] = {}
        confirmed_jobs: List[UUID] = []
        index = self._service._alias_index()                              # read before the transaction opens
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            row = repo.import_draft(pid, lock=True)                       # serialises concurrent confirmations
            if row is None or row["import_id"] != import_id:
                if any(repo.resume_at_path(pid, f"{ctx.owner_user_id}/{import_id}.{f}") for f in ("pdf", "docx")):
                    return {"status": "confirmed", "already": True}
                raise RecordNotFound("import not found")
            d = _Draft(row["draft"]["record"])
            d.check(req)
            replace = replacing(row)
            state = self._state(repo, ctx, d.record, replace)
            roles = plan(d, req, state["refs"])
            start_key = starting_role(roles, state["existing"])
            start = next((r for r in roles if r.key == start_key), None)
            choices = None
            if start is not None and start.title:
                from app.services.candidate_profile.service import match_title, role_mapping_options
                unmapped = {"title_raw": start.title, "role_id": "ROLE-UNKNOWN", "mapping_status": "unresolved",
                            "role_mapping_basis": None, "mapping_is_stale": False}
                opts = role_mapping_options(UUID(int=0), unmapped, match_title(start.title, index), repo.taxonomy_roles(),
                                            repo.pm_ownership_areas())
                choices = list(opts.alias_options) + list(opts.other_roles)
            errors = contract(roles, start_key, bool(choices), req)
            errors += education_contract([q for q in plan_education(d, req) if state["refs"].get(q.key, "new") == "new"])
            if errors:
                raise ReviewIncomplete(errors)
            if replace:
                # D52: the reviewed draft replaces the confirmed profile. In THIS transaction: the old profile and
                # everything derived from it go (cascade), a fresh profile is created, and the draft (already read) is
                # ingested into it as a new profile. Any failure below rolls all of it back: the old profile stays.
                pid = repo.clear_career_profile(pid)
                ctx = OwnerContext(owner_user_id=ctx.owner_user_id, candidate_profile_id=pid)
            sha = row["content_sha256"]
            resume_id = repo.insert_resume(pid, f"{ctx.owner_user_id}/{import_id}.{row['format']}", None, row["byte_size"], sha,
                                           row["mime_type"])
            repo.admit_resume(pid, resume_id, sha, row["pipeline_version"])
            if state["mode"] == "update":
                # An existing profile: education, skills and activities are compared and applied as before; roles come
                # from the plan (new roles created as the user's statements; a matching role's different dates become a
                # correction with history; lines new to a matching role are added to it).
                items, _ = compare(d.edited_others(req), assemble_profile(repo.fetch_profile_rows(pid)))
                rank = {"education": 1, "activity": 2, "skill": 4}
                for it in sorted(items, key=lambda it: rank.get(it["record_kind"], 9)):
                    accept(repo, pid, it, True)
                roles_rec = {"employers": [], "employments": [], "evidence": []}
                for r in roles:
                    if r.status in ("update", "present"):
                        j = d.jobs[r.key]
                        roles_rec["employers"].append({"employer_id": r.key, "employer_name": d.employer_of(r.key)})
                        roles_rec["employments"].append({**j, "employer_id": r.key, "start": _record_date(r.start)[0],
                                                         "start_precision": _record_date(r.start)[1],
                                                         "end": _record_date(r.end)[0], "end_precision": _record_date(r.end)[1]})
                        roles_rec["evidence"] += [{"employment_seq": j["sequence_index"], "evidence_type": t, "text": x}
                                                  for _, t, x in r.lines]
                if roles_rec["employments"]:
                    items, _ = compare(roles_rec, assemble_profile(repo.fetch_profile_rows(pid)))
                    for it in sorted(items, key=lambda it: 0 if it["record_kind"] == "employment" else 3):
                        accept(repo, pid, it, True)
                for r in roles:
                    if r.status == "new":
                        rid = self._create_role(repo, pid, r)
                        confirmed_jobs.append(rid)
                        if r.role_id:
                            role_choice[rid] = r.role_id
            else:
                confirmed_jobs += self._ingest_and_confirm(repo, pid, d, req, roles, resume_id, sha, role_choice)
            if row["identity_sealed"] is not None and repo.contribution_consent_active():
                repo.store_identity_markers(bytes(row["identity_sealed"]), row["identity_key_id"])
            repo.delete_import_draft(pid, import_id)
            repo.log_resume_processing("admitted")
        self._map_roles(ctx, confirmed_jobs, role_choice)
        return {"status": "confirmed", "already": False}

    @staticmethod
    def _flags(r: PlannedRole) -> dict:
        out = {}
        if r.side_role:
            out["primary_career_sequence"] = False
        if r.counts_full_time is not None:
            out["counts_as_full_time_experience"] = r.counts_full_time
        return out

    def _create_role(self, repo, pid: UUID, r: PlannedRole) -> UUID:
        """A role created as the user's own statement (added in the review, renamed employer, or a title or employer the
        parser couldn't read): placed by its start date, confirmed, with the same fields as every other role."""
        lines = description_lines(r.description)
        made = create_employment(repo, pid, EmploymentEntry(
            title_raw=r.title, employer_name=r.employer, employment_type=r.employment_type, start=r.start, end=r.end,
            responsibilities=(lines + [t for _, k, t in r.lines if k != "achievement"])[:30],
            achievements=[t for _, k, t in r.lines if k == "achievement"][:30], confirm=True))
        flags = self._flags(r)
        if flags:
            repo.correct_record("employment", pid, made.record_id,
                                content_columns("employment", EmploymentCorrection(**flags), creating=False))
        return made.record_id

    def _ingest_and_confirm(self, repo, pid: UUID, d: _Draft, req: ConfirmRequest, roles: List[PlannedRole], resume_id: UUID,
                            sha: str, role_choice: Dict[UUID, str]) -> List[UUID]:
        """A new profile: the extracted roles the user kept are ingested with their EXTRACTED values (source_extracted,
        linked to the resume) and the user's edits become corrections with history; then roles that are the user's own
        statements are created (the stated order of entries.place). Education, skills and activities as before."""
        ext = {r.key: r for r in roles if not r.user_stated}
        kept_lines = {k for r in ext.values() for k, _, _ in r.lines if k} | {r.description_key for r in ext.values()
                                                                               if r.description_key}
        removed = {x.id for x in (*req.education, *req.skills, *req.activities) if x.remove}

        def keep(k: str) -> bool:
            if k.startswith("J"):
                return k in ext
            if k.startswith("X"):
                return k in kept_lines
            return k not in removed
        extraction, where = self._extraction(d.record, sha, keep)
        ids: Dict[str, UUID] = {}
        if where:
            run = repo.start_extraction(pid, resume_id, PARSER_VERSION, real=True)
            repo.ingest_extraction(run, extraction.ingestion_payload())
            rows = repo.import_rows(pid, resume_id)
            located = {(kind, r["source_location"]): r["id"] for kind, rs in rows.items() for r in rs}
            ids = {k: located[w] for k, w in where.items() if w in located}
            if len(ids) != len(where):
                raise CandidateProfileError("the import could not be applied completely")
        corrected = set()

        def correct(kind: str, key: str, model) -> None:
            rid = ids[key]
            lock = repo.lock_review_state(kind, pid, rid)
            check_correctable(lock["information_origin"], lock["review_status"])
            values = content_columns(kind, model, creating=False)
            if values:
                repo.correct_record(kind, pid, rid, values)
                corrected.add(key)
        for r in ext.values():
            for k, _, text in r.lines:
                original = d.lines.get(k, {}).get("text") if k else None
                if k and text != original:
                    correct("evidence", k, EvidenceCorrection(evidence_text=text))
            changes = {**r.changed, **self._flags(r)}
            if changes:
                correct("employment", r.key, EmploymentCorrection(**changes))
            if r.description_changed:
                lines = description_lines(r.description)
                if r.description_key and r.description_key in ids and lines:
                    correct("evidence", r.description_key, EvidenceCorrection(evidence_text=lines[0]))
                    lines = lines[1:]
                for text in lines:                                       # the user's own words: their statement
                    from app.services.candidate_profile.entries import _insert
                    from app.services.candidate_profile.models import EvidenceCreate
                    rid = _insert(repo, "evidence", pid, EvidenceCreate(evidence_type="responsibility", evidence_text=text,
                                                                        candidate_employment_id=ids[r.key]))
                    repo.review_record("evidence", pid, rid, "confirm")
        for x in req.education:
            end = DateInput(precision="ongoing") if x.studying else x.end
            status = ("in_progress" if x.studying else "completed" if x.studying is False or (x.end and x.end.year) else None)
            changes = {k: v for k, v in (("qualification_title_raw", x.qualification), ("field_of_study", x.field_of_study),
                                         ("institution_name", x.institution), ("start", x.start), ("end", end),
                                         ("completion_status", status)) if v is not None}
            if changes and not x.remove:
                correct("education", x.id, EducationCorrection(**changes))
        for x in req.skills:
            if x.name and not x.remove:
                correct("skill", x.id, SkillCorrection(skill_text_raw=x.name))
        for x in req.activities:
            changes = {k: v for k, v in (("title_raw", x.title), ("organisation_raw", x.organisation)) if v is not None}
            if changes and not x.remove:
                correct("activity", x.id, ActivityCorrection(**changes))
        order = {"employment": 0, "education": 1, "activity": 2, "evidence": 3, "skill": 4}
        jobs: List[UUID] = []
        for key in sorted(ids, key=lambda k: (order[where[k][0]], k)):
            kind = where[key][0]
            if key not in corrected:
                repo.review_record(kind, pid, ids[key], "confirm")
            if kind == "employment":
                jobs.append(ids[key])
                if ext[key].role_id:
                    role_choice[ids[key]] = ext[key].role_id
        for r in roles:                                                 # the user's statements, placed by start date
            if r.user_stated:
                rid = self._create_role(repo, pid, r)
                jobs.append(rid)
                if r.role_id:
                    role_choice[rid] = r.role_id
        return jobs

    def _map_roles(self, ctx: OwnerContext, jobs: List[UUID], role_choice: Dict[UUID, str]) -> None:
        for jid in dict.fromkeys(jobs):
            try:
                if jid in role_choice:                                  # the user's explicit choice in the review
                    self._service.choose_role(ctx, jid, role_choice[jid])
                else:                                                   # otherwise automatic where it's unambiguous
                    self._service.map_employment(ctx, jid)
            except CandidateProfileError:
                pass                                                    # left for the profile page
            except Exception as exc:                                    # the profile is already saved: never turn
                logger.warning("role mapping after confirmation failed: %s", type(exc).__name__)   # that into an error

    def _legacy_confirm_update(self, ctx: OwnerContext, batch_id: UUID, req: ConfirmRequest) -> dict:
        """A legacy batch of update proposals (before D48): applied in ONE owner transaction, as before."""
        pid = ctx.candidate_profile_id
        made: List[UUID] = []
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            rows = [r for r in repo.open_proposals(pid) if r["batch_id"] == batch_id]
            if not rows:
                return {"status": "confirmed", "already": True}
            props = {str(r["proposal_id"]): repo.lock_proposal(pid, r["proposal_id"]) for r in rows}
            payload = {k: dict(v["proposed"] or {}) for k, v in props.items()}
            dismissed = set()

            def known(i):
                if i not in props:
                    raise RecordNotFound("item is not part of this import")
            for e in req.work:
                if e.id.startswith("existing:"):
                    for bl in e.bullets:
                        known(bl.id)
                        if bl.remove or e.remove:
                            dismissed.add(bl.id)
                        elif bl.text:
                            payload[bl.id]["evidence_text"] = bl.text
                    continue
                known(e.id)
                if e.remove:
                    dismissed.add(e.id)
                    continue
                pl = payload[e.id]
                for k, v in (("title_raw", e.title), ("employer_name", e.employer), ("employment_type", e.employment_type)):
                    if v is not None:
                        pl[k] = v
                for k, v in (("start", e.start), ("end", e.end)):
                    if v is not None:
                        pl[k] = v.model_dump()
                if e.bullets:
                    lines = [("responsibilities", t) for t in pl.get("responsibilities") or []] + \
                            [("achievements", t) for t in pl.get("achievements") or []]
                    edits = {}
                    for bl in e.bullets:
                        pre, _, n = bl.id.partition(":")
                        if pre != e.id or not n.isdigit() or int(n) >= len(lines):
                            raise RecordNotFound("bullet is not part of this job")
                        edits[int(n)] = None if bl.remove else bl.text
                    kept = [(k, edits.get(i, t) if i in edits else t) for i, (k, t) in enumerate(lines) if not (i in edits and edits[i] is None)]
                    pl["responsibilities"] = [t for k, t in kept if k == "responsibilities"]
                    pl["achievements"] = [t for k, t in kept if k == "achievements"]
            for u in req.updates:
                known(u.id)
                if u.remove:
                    dismissed.add(u.id)
            for d in req.education:
                known(d.id)
                if d.remove:
                    dismissed.add(d.id)
                    continue
                for k, v in (("qualification_title_raw", d.qualification), ("field_of_study", d.field_of_study), ("institution_name", d.institution)):
                    if v is not None:
                        payload[d.id][k] = v
                if d.end is not None:
                    payload[d.id]["end"] = d.end.model_dump()
            for sk in req.skills:
                known(sk.id)
                if sk.remove:
                    dismissed.add(sk.id)
                elif sk.name:
                    payload[sk.id]["skill_text_raw"] = sk.name
            for ac in req.activities:
                known(ac.id)
                if ac.remove:
                    dismissed.add(ac.id)
                    continue
                for k, v in (("title_raw", ac.title), ("organisation_raw", ac.organisation)):
                    if v is not None:
                        payload[ac.id][k] = v
            rank = {"employment": 0, "education": 1, "activity": 2, "evidence": 3, "skill": 4}
            for key in sorted(props, key=lambda k: (rank.get(props[k]["record_kind"], 9), k)):
                if key in dismissed:
                    repo.decide_proposal(pid, props[key]["proposal_id"], "dismissed", None)
                    continue
                rid = accept(repo, pid, {**props[key], "proposed": payload[key]}, True)
                repo.decide_proposal(pid, props[key]["proposal_id"], "accepted", rid)
                if props[key]["record_kind"] == "employment" and props[key]["match_state"] == "new":
                    made.append(rid)
        self._map_roles(ctx, made, {})
        return {"status": "confirmed", "already": False}

    def _legacy_confirm_new(self, ctx: OwnerContext, import_id: UUID, req: ConfirmRequest) -> dict:
        """A legacy import (pending records, before D48): edits and confirmation in ONE owner transaction, as before."""
        pid = ctx.candidate_profile_id

        def U(x):
            try:
                return UUID(str(x))
            except ValueError:
                raise RecordNotFound("item is not part of this import") from None
        confirmed_jobs: List[UUID] = []
        role_choice: Dict[UUID, str] = {}
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            if not repo.import_document_exists(pid, import_id):
                raise RecordNotFound("import not found")
            rows = self._pending(repo, ctx, import_id)
            if not any(rows.values()):
                return {"status": "confirmed", "already": True}
            ids = {k: {r["id"] for r in v} for k, v in rows.items()}

            def own(kind, rid):
                if rid not in ids[kind]:
                    raise RecordNotFound(f"{kind} {rid} is not part of this import")

            def delete(kind, rid):
                repo.delete_pending(kind, pid, rid)
                ids[kind].discard(rid)

            def correct(kind, rid, model):
                state = repo.lock_review_state(kind, pid, rid)
                check_correctable(state["information_origin"], state["review_status"])
                values = content_columns(kind, model, creating=False)
                if values:
                    repo.correct_record(kind, pid, rid, values)
                    ids[kind].discard(rid)

            jobs = {r["id"]: r for r in rows["employment"]}
            evidence_of: Dict[UUID, List[dict]] = {}
            for x in rows["evidence"]:
                evidence_of.setdefault(x["candidate_employment_id"], []).append(x)
            for e in req.work:
                own("employment", U(e.id))
                for b in e.bullets:
                    if U(b.id) not in ids["evidence"] or all(x["id"] != U(b.id) for x in evidence_of.get(U(e.id), [])):
                        raise RecordNotFound("bullet is not part of this job")
                if e.remove:
                    for x in evidence_of.get(U(e.id), []):
                        delete("evidence", x["id"])
                    delete("employment", U(e.id))
                    continue
                job = jobs[U(e.id)]
                renamed = e.employer is not None and _key(e.employer) != _key(job["employer_name"])
                if renamed:
                    removed = {U(b.id) for b in e.bullets if b.remove}
                    edited = {U(b.id): b.text for b in e.bullets if b.text}
                    lines = [(x["evidence_type"], edited.get(x["id"], x["evidence_text"])) for x in evidence_of.get(U(e.id), [])
                             if x["id"] not in removed]
                    for x in evidence_of.get(U(e.id), []):
                        delete("evidence", x["id"])
                    delete("employment", U(e.id))
                    entry = EmploymentEntry(
                        title_raw=e.title or job["title_raw"], employer_name=e.employer or None,
                        employment_type=e.employment_type or job["employment_type"] or "unknown",
                        start=e.start or DateInput(year=job["start_year"], month=job["start_month"], precision=job["start_precision"] or "unknown"),
                        end=e.end or DateInput(year=job["end_year"], month=job["end_month"], precision=job["end_precision"] or "unknown"),
                        responsibilities=[t for k, t in lines if k != "achievement"][:30],
                        achievements=[t for k, t in lines if k == "achievement"][:30], confirm=True)
                    made = create_employment(repo, pid, entry)
                    confirmed_jobs.append(made.record_id)
                    if e.role_id:
                        role_choice[made.record_id] = e.role_id
                    continue
                for b in e.bullets:
                    if b.remove:
                        delete("evidence", U(b.id))
                    elif b.text:
                        correct("evidence", U(b.id), EvidenceCorrection(evidence_text=b.text))
                if e.role_id:
                    role_choice[U(e.id)] = e.role_id
                changes = {k: v for k, v in (("title_raw", e.title), ("employment_type", e.employment_type), ("start", e.start),
                                             ("end", e.end)) if v is not None}
                if changes:
                    correct("employment", U(e.id), EmploymentCorrection(**changes))
                    confirmed_jobs.append(U(e.id))
            for d in req.education:
                own("education", U(d.id))
                if d.remove:
                    delete("education", U(d.id))
                    continue
                changes = {k: v for k, v in (("qualification_title_raw", d.qualification), ("field_of_study", d.field_of_study),
                                             ("institution_name", d.institution), ("end", d.end)) if v is not None}
                if changes:
                    correct("education", U(d.id), EducationCorrection(**changes))
            for s in req.skills:
                own("skill", U(s.id))
                if s.remove:
                    delete("skill", U(s.id))
                elif s.name:
                    correct("skill", U(s.id), SkillCorrection(skill_text_raw=s.name))
            for a in req.activities:
                own("activity", U(a.id))
                if a.remove:
                    delete("activity", U(a.id))
                    continue
                changes = {k: v for k, v in (("title_raw", a.title), ("organisation_raw", a.organisation)) if v is not None}
                if changes:
                    correct("activity", U(a.id), ActivityCorrection(**changes))
            repo.drop_unused_employers(pid)
            for kind in ("employment", "education", "activity", "evidence", "skill"):
                for rid in sorted(ids[kind], key=str):
                    repo.review_record(kind, pid, rid, "confirm")
                    if kind == "employment":
                        confirmed_jobs.append(rid)
        self._map_roles(ctx, confirmed_jobs, role_choice)
        return {"status": "confirmed", "already": False}

    # ------------------------------------------------------------------------------------------------ discard
    def _discard_legacy_import(self, repo, ctx: OwnerContext, include_leftovers: bool) -> Dict[str, int]:
        pid = ctx.candidate_profile_id
        removed = {"records": 0, "suggestions": 0}
        imp = self._open_import(repo, ctx)
        if imp is not None:
            rows = self._pending(repo, ctx, imp["resume_id"])
            for kind in PENDING_KINDS:
                for r in rows[kind]:
                    removed["records"] += repo.delete_pending(kind, pid, r["id"])
            repo.drop_unused_employers(pid)
            if not repo.resume_has_records(imp["resume_id"]):
                repo.delete_resume(pid, imp["resume_id"])                # its run and admission go with it (cascade)
        if not include_leftovers:                                         # replacing: the open update batch goes too
            for r in self._open_batch(repo, ctx):
                repo.decide_proposal(pid, r["proposal_id"], "dismissed", None)
        else:
            # Earlier attempts: open pasted-text suggestions are dismissed (decisions are history, never deleted) and
            # every UNCONFIRMED record left in the profile (pending or rejected) is deleted. Reviewed records stay.
            for r in repo.open_proposals(pid):
                repo.decide_proposal(pid, r["proposal_id"], "dismissed", None)
                removed["suggestions"] += 1
            for kind in PENDING_KINDS:
                removed["records"] += repo.delete_unconfirmed(kind, pid)
            repo.drop_unused_employers(pid)
            repo.delete_orphan_resumes(pid)
        return removed

    def discard(self, ctx: OwnerContext, include_legacy: bool = True) -> dict:
        """ONE owner transaction: the staged draft (with any sealed markers), a legacy import and, with include_legacy,
        earlier unconfirmed leftovers; then any identity markers nothing needs. Confirmed records are never touched."""
        pid = ctx.candidate_profile_id
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            drafts = repo.delete_import_draft(pid)
            removed = self._discard_legacy_import(repo, ctx, include_leftovers=include_legacy)
            repo.forget_identity_markers()
        return {"status": "discarded", "drafts": drafts, **removed}
