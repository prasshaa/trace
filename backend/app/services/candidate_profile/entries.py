"""Manual entry and update proposals (product Phase 2; decisions D5 and D7).

Manual entry. The user types an employment (with its employer, responsibilities, achievements and skills), a
qualification, a certification or project, or a skill. Every record is created through the existing candidate write
path as the user's own pending draft:
- information_origin user_stated, review_status pending_review;
- nothing is confirmed on the user's behalf. "confirm" is the user's explicit "save and confirm" and goes through the
  same review function as every other confirmation.

Stated order. Employments are ordered by sequence_index (stated order, never dates). A new employment goes:
- before the role the user names; or
- by default, before the first role in stated order whose known start is later than its own; or
- otherwise, after the latest role.
An existing gap in the numbering is used when there is one. Otherwise the later roles move down one place, each move
recorded as an ORDERING EVENT (decision D10, trace_review.move_employment). A move never changes a review status and is
never a content correction. A rejected role is never moved or used as an anchor (reinstate it first). An explicit
position that clearly established start dates contradict is refused (OrderConflictsWithDates); unknown or ambiguous
dates keep the user's explicit order.

Update proposals (one pipeline, several sources). Parsed career text is compared with the profile:
- an item already present is not proposed;
- a new item is proposed as new;
- a changed date or detail of an existing role or qualification is proposed as a possible update of that record.
Accepting creates the draft, or applies the update as an edit (own draft) or a correction (reviewed or extracted
record, with history). Dismissing clears the proposed content. Sources are pasted text (deterministic local parser),
and a resume uploaded into a profile that already holds records. Later, voice transcripts and the advisor use the same
path.

Every write runs in ONE owner-session transaction. Lock order: employer rows, then employment rows (as everywhere).
"""
import hashlib
import json
from typing import Dict, List, Literal, Optional, Tuple
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.candidate_profile.models import (
    UNKNOWN_DATE, ActivityCreate, ActivityType, CandidateProfile, DateInput, EducationCorrection, EducationCreate,
    EducationUpdate, EmployerCreate, EmploymentCorrection, EmploymentCreate, EmploymentType, EmploymentUpdate, EvidenceCreate,
    OwnerContext, SkillCreate,
)
from app.services.candidate_profile.repository import (
    InvalidRecord, InvalidTransition, OrderConflictsWithDates, ProfileNotFound, RecordNotEditable, RecordNotFound, owner_session,
)
from app.services.candidate_profile.review import check_correctable, check_editable, check_employer_editable
from app.services.career_history import dates

QualificationLevel = Literal["secondary", "diploma", "bachelor", "master", "mba", "doctorate", "certificate", "other", "unknown"]
Text = Field(min_length=1, max_length=200)


def _clean(v: Optional[str]) -> Optional[str]:
    v = " ".join((v or "").split())
    return v or None


def key(v: Optional[str]) -> str:
    """Comparison key: case, spacing and punctuation never make two items different."""
    return " ".join("".join(ch if ch.isalnum() else " " for ch in (v or "").lower()).split())


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LineClassification(_Entry):
    """The user's own classification of one responsibility line, given while entering it (Phase 3.1): areas with optional
    yes/no scope answers, or "None of these areas". Never inferred; omitted lines stay uncategorised."""
    areas: List[str] = Field(default_factory=list, max_length=32)
    none: bool = False
    qualifiers: Dict[str, Dict[str, bool]] = Field(default_factory=dict)


class EmploymentEntry(_Entry):
    title_raw: str = Text
    employer_name: Optional[str] = Field(default=None, max_length=200)
    employment_type: EmploymentType = "unknown"
    start: DateInput = UNKNOWN_DATE
    end: DateInput = UNKNOWN_DATE
    responsibilities: List[str] = Field(default_factory=list, max_length=30)
    achievements: List[str] = Field(default_factory=list, max_length=30)
    skills: List[str] = Field(default_factory=list, max_length=50)
    before_employment_id: Optional[UUID] = None        # place before this role; None: by start date, else latest
    confirm: bool = False                              # the user's explicit "save and confirm"
    # Optional, parallel to responsibilities / achievements (null = leave that line uncategorised)
    responsibility_classifications: List[Optional[LineClassification]] = Field(default_factory=list, max_length=30)
    achievement_classifications: List[Optional[LineClassification]] = Field(default_factory=list, max_length=30)

    @field_validator("title_raw", "employer_name")
    @classmethod
    def _text(cls, v):
        return _clean(v)

    @field_validator("responsibilities", "achievements")
    @classmethod
    def _lines(cls, v):
        out = [_clean(x) for x in v]
        if any(x is not None and not 3 <= len(x) <= 1000 for x in out):
            raise ValueError("each line is 3 to 1000 characters")
        return [x for x in out if x]

    @field_validator("skills")
    @classmethod
    def _skills(cls, v):
        out = [_clean(x) for x in v]
        if any(x is not None and len(x) > 80 for x in out):
            raise ValueError("a skill is at most 80 characters")
        return list(dict.fromkeys(x for x in out if x))

    @field_validator("start")
    @classmethod
    def _start(cls, v):
        if v.precision == "ongoing":
            raise ValueError("a start date cannot be 'ongoing'")
        return v


class EducationEntry(_Entry):
    qualification_title_raw: str = Text
    qualification_level: QualificationLevel = "unknown"
    institution_name: Optional[str] = Field(default=None, max_length=200)
    field_of_study: Optional[str] = Field(default=None, max_length=200)
    start: DateInput = UNKNOWN_DATE                    # Phase 5: when the qualification started
    end: DateInput = UNKNOWN_DATE                      # completion; precision "ongoing" while still studying
    completion_status: Literal["completed", "in_progress", "incomplete", "unknown"] = "unknown"
    confirm: bool = False

    @model_validator(mode="after")
    def _studying(self):
        if self.start.precision == "ongoing":
            raise ValueError("a start date cannot be 'ongoing'")
        if self.end.precision == "ongoing" and self.completion_status not in ("in_progress", "unknown"):
            raise ValueError("a qualification still being studied has no completion date")
        if self.completion_status == "in_progress" and self.end.precision not in ("ongoing", "unknown"):
            raise ValueError("a qualification still being studied has no completion date")
        return self


class ActivityEntry(_Entry):
    activity_type: ActivityType
    title_raw: str = Text
    organisation_raw: Optional[str] = Field(default=None, max_length=200)
    description_raw: Optional[str] = Field(default=None, max_length=1000)
    end: DateInput = UNKNOWN_DATE
    confirm: bool = False


class SkillEntry(_Entry):
    skill_text_raw: str = Field(min_length=1, max_length=80)
    confirm: bool = False


class EmploymentDraftEdit(_Entry):
    """An edit of the user's own pending employment draft. The employer is changed by name (the API exposes no
    employer ids). Order is changed with before_employment_id on a new entry, never by editing the index."""
    title_raw: Optional[str] = Field(default=None, min_length=1, max_length=200)
    employer_name: Optional[str] = Field(default=None, max_length=200)
    employment_type: Optional[EmploymentType] = None
    start: Optional[DateInput] = None
    end: Optional[DateInput] = None


class EntryResult(BaseModel):
    kind: str
    record_id: UUID
    created: Dict[str, int]
    confirmed: bool
    placement: Optional[str] = None                    # employments: "latest", "by_start_date" or "before_selected"
    reordered: int = 0                                 # later roles moved down one place


# --------------------------------------------------------------------------- placement (pure)
def place(order: List[dict], before_id: Optional[UUID], start: Tuple[Optional[int], Optional[int]]
          ) -> Tuple[int, List[Tuple[dict, int]], str]:
    """(new sequence_index, [(row, new index) to move], placement) under decision D10. ``order`` rows carry
    candidate_employment_id, sequence_index, title_raw, start_lo, start_hi and review_status; ``start`` is the new
    role's start bounds (month numbers, None when unknown).

    - The user's explicit position (before_id) wins over dates, unless clearly established start dates contradict
      it: the new role starts after the chosen role's latest possible start, or the role just before the position
      starts after the new role's latest possible start. Unknown or overlapping (ambiguous) dates never contradict.
    - Otherwise: before the first role in stated order whose known start is later; otherwise after the latest.
    - A rejected role is never an anchor and is never moved: it must be reinstated first."""
    rows = sorted(order, key=lambda r: r["sequence_index"])
    lo, hi = start
    target, how = None, "latest"
    if before_id is not None:                      # checked first: an id outside this profile is never a position
        target = next((r for r in rows if r["candidate_employment_id"] == before_id), None)
        if target is None:
            raise RecordNotFound(f"employment {before_id}")
        if target["review_status"] == "rejected":
            raise RecordNotEditable("a role you rejected can't be used as a position; reinstate it first")
        how = "before_selected"
        before = [r for r in rows if r["sequence_index"] < target["sequence_index"] and r["review_status"] != "rejected"]
        prev = before[-1] if before else None
        if lo is not None and target["start_hi"] is not None and lo > target["start_hi"]:
            raise OrderConflictsWithDates(f"this role starts after '{target['title_raw']}' started, so it can't come before it; "
                                          "check the dates or choose another position")
        if prev is not None and prev["start_lo"] is not None and hi is not None and prev["start_lo"] > hi:
            raise OrderConflictsWithDates(f"'{prev['title_raw']}' started after this role, so this role can't come after it; "
                                          "check the dates or choose another position")
    elif rows and lo is not None:
        target = next((r for r in rows if r["review_status"] != "rejected" and r["start_lo"] is not None
                       and r["start_lo"] > lo), None)
        how = "by_start_date" if target is not None else "latest"
    if target is None:
        return (rows[-1]["sequence_index"] + 1 if rows else 1), [], "latest"
    prev_index = max((r["sequence_index"] for r in rows if r["sequence_index"] < target["sequence_index"]), default=0)
    if target["sequence_index"] - prev_index >= 2:                 # a free place already exists: nothing moves
        return prev_index + 1, [], how
    moves = [(r, r["sequence_index"] + 1) for r in rows if r["sequence_index"] >= target["sequence_index"]]
    if any(r["review_status"] == "rejected" for r, _ in moves):
        raise RecordNotEditable("a role you rejected comes after this position and can't be moved; reinstate it first, "
                                "or place this role elsewhere")
    return target["sequence_index"], moves, how


# --------------------------------------------------------------------------- writes inside one owner session
def _insert(repo, kind: str, pid: UUID, payload: BaseModel) -> UUID:
    from app.services.candidate_profile.service import content_columns
    return repo.insert_record(kind, pid, content_columns(kind, payload, creating=True))


def _employer(repo, pid: UUID, name: Optional[str]) -> Optional[UUID]:
    """Reuse an employer of this profile with the same name (locked FOR KEY SHARE before any employment lock, the
    global order), or create one. None when no name is given."""
    if not name:
        return None
    found = repo.employer_by_name(pid, key(name))
    if found is not None:
        repo.share_lock_employer(pid, found)
        return found
    return _insert(repo, "employer", pid, EmployerCreate(employer_name=name))


def create_employment(repo, pid: UUID, entry: EmploymentEntry) -> EntryResult:
    employer_id = _employer(repo, pid, entry.employer_name)
    order = repo.lock_employment_order(pid)
    seq, moves, how = place(order, entry.before_employment_id,
                            dates.bounds(entry.start.year, entry.start.month, entry.start.precision))
    emp = _insert(repo, "employment", pid, EmploymentCreate(
        sequence_index=seq, title_raw=entry.title_raw, candidate_employer_id=employer_id, employment_type=entry.employment_type,
        start=entry.start, end=entry.end))
    # Position-only moves (D10): each is an ordering event caused by this new role; review statuses never change and
    # nothing is recorded as a content correction. The unique order constraint is checked at commit.
    for row, new_index in sorted(moves, key=lambda m: -m[1]):
        repo.move_employment(pid, row["candidate_employment_id"], new_index, "role_inserted", emp)
    made = [("employment", emp)]
    for etype, lines, cls in (("responsibility", entry.responsibilities, entry.responsibility_classifications),
                              ("achievement", entry.achievements, entry.achievement_classifications)):
        if len(cls) > len(lines):
            raise InvalidRecord("more classifications than lines")
        for i, text in enumerate(lines):
            eid = _insert(repo, "evidence", pid, EvidenceCreate(evidence_type=etype, evidence_text=text, candidate_employment_id=emp))
            made.append(("evidence", eid))
            classify_line(repo, pid, eid, text, cls[i] if i < len(cls) else None)
    for s in entry.skills:
        made.append(("skill", _insert(repo, "skill", pid, SkillCreate(skill_text_raw=s, candidate_employment_id=emp))))
    if entry.confirm:
        for kind, rid in made:
            repo.review_record(kind, pid, rid, "confirm")
    counts: Dict[str, int] = {}
    for kind, _ in made:
        counts[kind] = counts.get(kind, 0) + 1
    return EntryResult(kind="employment", record_id=emp, created=counts, confirmed=entry.confirm, placement=how,
                       reordered=len(moves))


def classify_line(repo, pid: UUID, evidence_id: UUID, text: str, c: Optional[LineClassification]) -> None:
    """The user's explicit choice for a line they just entered, through the same trusted function as the review page."""
    if c is None:
        return
    if c.none and (c.areas or c.qualifiers):
        raise InvalidRecord('"None of these areas" cannot be combined with areas')
    if not c.none and not c.areas:
        return                                          # nothing chosen: the line stays uncategorised
    repo.classify_evidence_v2(pid, evidence_id, list(c.areas), c.none, c.qualifiers, text)


def _create_simple(repo, pid: UUID, kind: str, payload: BaseModel, confirm: bool) -> EntryResult:
    rid = _insert(repo, kind, pid, payload)
    if confirm:
        repo.review_record(kind, pid, rid, "confirm")
    return EntryResult(kind=kind, record_id=rid, created={kind: 1}, confirmed=confirm)


def education_payload(e: EducationEntry) -> EducationCreate:
    status = "in_progress" if e.end.precision == "ongoing" else e.completion_status
    return EducationCreate(qualification_title_raw=_clean(e.qualification_title_raw), qualification_level=e.qualification_level,
                           institution_name=_clean(e.institution_name), field_of_study=_clean(e.field_of_study), start=e.start,
                           end=e.end, completion_status=status)


def activity_payload(a: ActivityEntry) -> ActivityCreate:
    return ActivityCreate(activity_type=a.activity_type, title_raw=_clean(a.title_raw), organisation_raw=_clean(a.organisation_raw),
                          description_raw=_clean(a.description_raw), end=a.end)


# --------------------------------------------------------------------------- comparison (pure)
def _date(value: Optional[str], precision: Optional[str]) -> DateInput:
    if precision == "ongoing":
        return DateInput(precision="ongoing")
    if not value or precision in (None, "unknown"):
        return DateInput()
    parts = str(value).split("-")
    return DateInput(year=int(parts[0]), month=int(parts[1]) if precision == "month" and len(parts) > 1 else None,
                     precision=precision)


def _known(d: DateInput) -> bool:
    return d.precision in ("month", "year", "ongoing")


def _same_date(d: DateInput, view) -> bool:
    """Whether a date read from a resume agrees with the stored one. A LESS precise statement that matches at its own
    precision is not a change (Phase 5: the review asks for months a resume may not state, so "2016" agrees with a
    confirmed "May 2016"). A more precise or different statement is."""
    if d.precision == "year" and view.year is not None and view.precision in ("year", "month"):
        return d.year == view.year
    return (d.year, d.month, d.precision) == (view.year, view.month, view.precision)


def _fingerprint(kind: str, state: str, target: Optional[UUID], payload: dict) -> str:
    blob = json.dumps([kind, state, str(target) if target else None, payload], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def compare(record: dict, profile: CandidateProfile, refs: Optional[Dict[str, str]] = None) -> Tuple[List[dict], int]:
    """Parsed career record (parser / de-identification format) vs the profile -> (proposal items, already present).
    Rejected records are ignored as matches (the user rejected them; a new item is proposed again).
    refs (optional, filled in): the state of each record item by its draft key -- J<seq> jobs, X<n> evidence lines,
    D<n> education, A<n> activities, S<n> skills (n counts from 1 in the record's own lists) -- "new", "update" or
    "present"; each proposal item also carries its key as "ref"."""
    refs = {} if refs is None else refs
    live = lambda r: r.provenance.review_status != "rejected"     # noqa: E731
    jobs = {(key(e.title_raw), key(e.employer.employer_name if e.employer else None)): e
            for e in profile.employment_history if live(e)}
    employers = {e["employer_id"]: e.get("employer_name") for e in record.get("employers", [])}
    bullets: Dict[int, List[dict]] = {}
    for n, x in enumerate(record.get("evidence", []), 1):
        if x.get("text"):
            bullets.setdefault(x.get("employment_seq"), []).append({**x, "_ref": f"X{n}"})
    items, present = [], 0
    current_ref: List[str] = [""]

    def add(kind, state, target, payload):
        items.append({"record_kind": kind, "match_state": state, "target_record_id": target, "proposed": payload,
                      "fingerprint": _fingerprint(kind, state, target, payload), "ref": current_ref[0]})
        refs[current_ref[0]] = "update" if state == "possible_update" else "new"

    for j in sorted(record.get("employments", []), key=lambda j: j.get("sequence_index", 0)):
        if not j.get("title"):
            continue
        employer = employers.get(j.get("employer_id"))
        start, end = _date(j.get("start"), j.get("start_precision")), _date(j.get("end"), j.get("end_precision"))
        lines = bullets.get(j.get("sequence_index"), [])
        match = jobs.get((key(j["title"]), key(employer)))
        current_ref[0] = f"J{j.get('sequence_index')}"
        if match is None:
            for x in lines:
                refs[x["_ref"]] = "new"
            add("employment", "new", None, {
                "title_raw": _clean(j["title"]), "employer_name": _clean(employer),
                "start": start.model_dump(), "end": end.model_dump(),
                "responsibilities": [_clean(x["text"]) for x in lines if x.get("evidence_type") != "achievement"],
                "achievements": [_clean(x["text"]) for x in lines if x.get("evidence_type") == "achievement"]})
            continue
        changes = {}
        if _known(start) and not _same_date(start, match.start):
            changes["start"] = start.model_dump()
        if _known(end) and not _same_date(end, match.end):
            changes["end"] = end.model_dump()
        if changes:
            add("employment", "possible_update", match.candidate_employment_id, changes)
        else:
            present += 1
            refs[current_ref[0]] = "present"
        have = {key(x.evidence_text) for x in match.responsibilities + match.achievements + match.other_evidence if live(x)}
        for x in lines:
            current_ref[0] = x["_ref"]
            if key(x["text"]) in have:
                present += 1
                refs[x["_ref"]] = "present"
            else:
                add("evidence", "new", match.candidate_employment_id,
                    {"evidence_type": "achievement" if x.get("evidence_type") == "achievement" else "responsibility",
                     "evidence_text": _clean(x["text"])})

    quals = {(key(d.qualification_title_raw), key(d.institution_name)): d for d in profile.education if live(d)}
    for n, d in enumerate(record.get("education", []), 1):
        current_ref[0] = f"D{n}"
        title = d.get("qualification_title") or d.get("qualification_title_raw")
        if not title:
            continue
        end = _date(d.get("end"), d.get("end_precision"))
        match = quals.get((key(title), key(d.get("institution_name"))))
        if match is None:
            add("education", "new", None, {"qualification_title_raw": _clean(title),
                                           "qualification_level": d.get("qualification_level") or "unknown",
                                           "institution_name": _clean(d.get("institution_name")),
                                           "field_of_study": _clean(d.get("field_of_study")), "end": end.model_dump()})
            continue
        changes = {}
        if d.get("field_of_study") and key(d["field_of_study"]) != key(match.field_of_study):
            changes["field_of_study"] = _clean(d["field_of_study"])
        if _known(end) and not _same_date(end, match.end):
            changes["end"] = end.model_dump()
        if changes:
            add("education", "possible_update", match.candidate_education_id, changes)
        else:
            present += 1
            refs[current_ref[0]] = "present"

    acts = {(a.activity_type, key(a.title_raw)) for group in profile.career_activities.values() for a in group if live(a)}
    kinds = {"certification": "certification", "project": "independent_project", "independent_project": "independent_project",
             "award": "award", "volunteering": "volunteering"}
    for n, a in enumerate(record.get("activities", []), 1):
        current_ref[0] = f"A{n}"
        if not a.get("title"):
            continue
        atype = kinds.get(a.get("activity_type"), "other")
        if (atype, key(a["title"])) in acts:
            present += 1
            refs[current_ref[0]] = "present"
            continue
        add("activity", "new", None, {"activity_type": atype, "title_raw": _clean(a["title"]),
                                      "organisation_raw": _clean(a.get("organisation")),
                                      "description_raw": _clean(a.get("description")),
                                      "end": (DateInput(year=a["year"], precision="year") if a.get("year") else DateInput()).model_dump()})

    skills = {key(s.skill_text_raw) for s in profile.skills if live(s)} | {
        key(s.taxonomy.canonical_skill) for s in profile.skills if live(s) and s.taxonomy}
    for n, s in enumerate(record.get("skills", []), 1):
        current_ref[0] = f"S{n}"
        text = _clean(s.get("text"))
        if not text or len(text) > 80:
            continue
        if key(text) in skills:
            present += 1
            refs[current_ref[0]] = "present"
            continue
        skills.add(key(text))
        add("skill", "new", None, {"skill_text_raw": text})
    return items, present


def store_proposals(repo, pid: UUID, items: List[dict], source: str, source_sha256: str, extractor_version: str) -> Tuple[UUID, int]:
    batch = uuid4()
    stored = 0
    for it in items:
        if repo.insert_proposal(pid, {**{k: v for k, v in it.items() if k != "ref"}, "batch_id": batch, "source": source, "source_sha256": source_sha256,
                                      "extractor_version": extractor_version}) is not None:
            stored += 1
    return batch, stored


def accept(repo, pid: UUID, proposal: dict, confirm: bool, classifications: Optional[List[Optional[LineClassification]]] = None) -> UUID:
    """Apply one open proposal inside the caller's owner session; returns the record created or changed. Optional
    classifications are the user's explicit choices for the proposal's lines (employment: responsibilities then
    achievements; evidence: one)."""
    classifications = classifications or []
    kind, state, target, payload = proposal["record_kind"], proposal["match_state"], proposal["target_record_id"], proposal["proposed"]
    if state == "possible_update":
        model = EmploymentCorrection if kind == "employment" else EducationCorrection
        edit = EmploymentUpdate if kind == "employment" else EducationUpdate
        from app.services.candidate_profile.service import content_columns
        lock = repo.lock_review_state(kind, pid, target)
        if lock["review_status"] == "pending_review" and lock["information_origin"] == "user_stated":
            check_editable(lock["information_origin"], lock["review_status"])
            repo.update_record(kind, pid, target, content_columns(kind, edit(**payload), creating=False))
        else:
            check_correctable(lock["information_origin"], lock["review_status"])
            repo.correct_record(kind, pid, target, content_columns(kind, model(**payload), creating=False))
        return target
    if kind == "employment":
        n = len(payload.get("responsibilities") or [])
        return create_employment(repo, pid, EmploymentEntry(**payload, confirm=confirm, responsibility_classifications=classifications[:n],
                                                            achievement_classifications=classifications[n:])).record_id
    if kind == "evidence":
        if repo.lock_review_state("employment", pid, target)["review_status"] == "rejected":
            raise RecordNotEditable("the role this item belongs to was rejected; reinstate it first")
        rid = _insert(repo, "evidence", pid, EvidenceCreate(**payload, candidate_employment_id=target))
        if classifications:
            classify_line(repo, pid, rid, payload["evidence_text"], classifications[0])
    elif kind == "education":
        rid = _insert(repo, "education", pid, EducationCreate(**payload))
    elif kind == "activity":
        rid = _insert(repo, "activity", pid, ActivityCreate(**payload))
    elif kind == "skill":
        rid = _insert(repo, "skill", pid, SkillCreate(**payload))
    else:
        raise InvalidRecord(f"unknown proposal kind {kind!r}")
    if confirm:
        repo.review_record(kind, pid, rid, "confirm")
    return rid


# --------------------------------------------------------------------------- service mixin
class EntriesMixin:
    """Manual entry, draft edits and update proposals on CandidateProfileService."""

    def _session(self, ctx: OwnerContext):
        return owner_session(self._conn, ctx.owner_user_id)

    def _require_profile(self, repo, ctx: OwnerContext) -> None:
        if not repo.profile_exists(ctx.candidate_profile_id):
            raise ProfileNotFound(str(ctx.candidate_profile_id))

    def add_employment_entry(self, ctx: OwnerContext, entry: EmploymentEntry) -> EntryResult:
        with self._session(ctx) as repo:
            self._require_profile(repo, ctx)
            return create_employment(repo, ctx.candidate_profile_id, entry)

    def add_education_entry(self, ctx: OwnerContext, entry: EducationEntry) -> EntryResult:
        with self._session(ctx) as repo:
            self._require_profile(repo, ctx)
            return _create_simple(repo, ctx.candidate_profile_id, "education", education_payload(entry), entry.confirm)

    def add_activity_entry(self, ctx: OwnerContext, entry: ActivityEntry) -> EntryResult:
        with self._session(ctx) as repo:
            self._require_profile(repo, ctx)
            return _create_simple(repo, ctx.candidate_profile_id, "activity", activity_payload(entry), entry.confirm)

    def add_skill_entry(self, ctx: OwnerContext, entry: SkillEntry) -> EntryResult:
        with self._session(ctx) as repo:
            self._require_profile(repo, ctx)
            return _create_simple(repo, ctx.candidate_profile_id, "skill",
                                  SkillCreate(skill_text_raw=_clean(entry.skill_text_raw)), entry.confirm)

    def edit_employment_draft(self, ctx: OwnerContext, employment_id: UUID, edit: EmploymentDraftEdit, expected_version) -> None:
        """Edit the user's own pending employment draft (and, by name, its employer) in one transaction. Locks: the
        employer row first, then the employment rows (lock_employer_state), as for every employer edit."""
        from app.services.candidate_profile.service import check_version, content_columns
        changes = edit.model_dump(exclude_unset=True)
        name_given = "employer_name" in changes
        name = _clean(changes.pop("employer_name", None))
        with self._session(ctx) as repo:
            self._require_profile(repo, ctx)
            pid = ctx.candidate_profile_id
            employer_id = repo.employment_employer_id(pid, employment_id)
            if name_given and employer_id is not None:
                state = repo.lock_employer_state(pid, employer_id)
                check_employer_editable(state["attribute_provenance"], state["employment_statuses"])
            lock = repo.lock_review_state("employment", pid, employment_id)
            check_version(lock["updated_at"], expected_version)
            check_editable(lock["information_origin"], lock["review_status"])
            if name_given:
                if employer_id is not None:
                    repo.update_record("employer", pid, employer_id, {"employer_name": name})
                elif name:            # a new employer is invisible to every other transaction: no lock-order risk
                    repo.reassign_employment(pid, employment_id, _insert(repo, "employer", pid, EmployerCreate(employer_name=name)))
            if changes:
                repo.update_record("employment", pid, employment_id,
                                   content_columns("employment", EmploymentUpdate(**{k: getattr(edit, k) for k in changes}), creating=False))

    def order_history(self, ctx: OwnerContext, employment_id: UUID) -> List[dict]:
        """The role's ordering events (position-only moves), oldest first. Separate from its correction history."""
        with self._session(ctx) as repo:
            self._require_profile(repo, ctx)
            repo.employment_employer_id(ctx.candidate_profile_id, employment_id)       # RecordNotFound outside the profile
            return repo.order_events(ctx.candidate_profile_id, employment_id)

    # ------------------------------------------------------------------ proposals
    def propose_from_record(self, ctx: OwnerContext, record: dict, source: str, source_sha256: str, extractor_version: str) -> dict:
        profile = self.get_profile(ctx)
        items, present = compare(record, profile)
        with self._session(ctx) as repo:
            self._require_profile(repo, ctx)
            batch, stored = store_proposals(repo, ctx.candidate_profile_id, items, source, source_sha256, extractor_version)
        return {"batch_id": batch, "proposed": stored, "already_open": len(items) - stored, "already_in_profile": present}

    def open_proposals(self, ctx: OwnerContext) -> List[dict]:
        with self._session(ctx) as repo:
            self._require_profile(repo, ctx)
            return repo.open_proposals(ctx.candidate_profile_id)

    def accept_proposal(self, ctx: OwnerContext, proposal_id: UUID, confirm: bool = False,
                        classifications: Optional[List[Optional[LineClassification]]] = None) -> UUID:
        with self._session(ctx) as repo:
            self._require_profile(repo, ctx)
            p = repo.lock_proposal(ctx.candidate_profile_id, proposal_id)
            if p["status"] != "proposed":
                raise InvalidTransition("this proposal was already decided")
            rid = accept(repo, ctx.candidate_profile_id, p, confirm, classifications)
            repo.decide_proposal(ctx.candidate_profile_id, proposal_id, "accepted", rid)
            return rid

    def dismiss_proposal(self, ctx: OwnerContext, proposal_id: UUID) -> None:
        with self._session(ctx) as repo:
            self._require_profile(repo, ctx)
            p = repo.lock_proposal(ctx.candidate_profile_id, proposal_id)
            if p["status"] != "proposed":
                raise InvalidTransition("this proposal was already decided")
            repo.decide_proposal(ctx.candidate_profile_id, proposal_id, "dismissed", None)

    def source_seen(self, ctx: OwnerContext, source_sha256: str) -> bool:
        """The same document was already ingested or already turned into proposals for this profile."""
        with self._session(ctx) as repo:
            return (repo.resume_hash_registered(ctx.candidate_profile_id, source_sha256)
                    or repo.source_already_proposed(ctx.candidate_profile_id, source_sha256))


def profile_version(profile: CandidateProfile) -> str:
    """A deterministic fingerprint of the profile's content and review state (record versions excluded). The same
    profile always gives the same value; any content or review change gives a new one. Recorded by recommendation runs
    (Phase 4) to state which profile they used."""
    return hashlib.sha256(profile.model_dump_json().encode()).hexdigest()[:16]
