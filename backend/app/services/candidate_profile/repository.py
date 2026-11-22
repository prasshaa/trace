"""Database access for candidate profiles, always in a trusted owner context.

Every operation runs inside :func:`owner_session`: one transaction in which the connection switches
to the ``authenticated`` role with the owner as the request identity (``SET LOCAL ROLE`` plus the
transaction-local settings ``request.jwt.claim.sub``, ``request.jwt.claim.role`` and
``request.jwt.claims``), and then checks that ``auth.uid()`` is that owner before any other statement.
The database itself enforces (data foundation v2.3.0, M2):

* RLS: only the owner's rows are visible or writable;
* column privileges: only candidate-authored content columns can be inserted or updated; review
  status, role and activity-taxonomy mapping, provenance, verification, extraction and legacy trust
  columns, and profile confirmation cannot be written;
* guard triggers: content changes only on the owner's own pending ``user_stated`` drafts; the
  employer-edit lock protocol; deletion never removes or silently detaches a reviewed record;
* review status changes only through ``trace_review.review_record`` and an employment's employer
  only through ``trace_review.reassign_employment`` (both act for ``auth.uid()`` only);
* composite foreign keys: links resolve only within the same candidate profile / owner.

In addition every query filters on ``owner_user_id`` and ``candidate_profile_id`` explicitly, so the
scoping does not rest on RLS alone. The role switch and identity are transaction-local and change no
role or RLS configuration. The local development connection itself (``postgres``, the table owner) is
NOT a production access model: code that bypasses owner_session is not governed by these rules.
"""
import json
from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional, Sequence
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.services.career_history.models import RoleInfo, SequencingContext


class CandidateProfileError(Exception):
    """Base class for candidate profile service errors."""


class ProfileNotFound(CandidateProfileError):
    """No such profile for this owner (also raised for another owner's profile; nothing is revealed)."""


class RecordNotFound(CandidateProfileError):
    """No such record in this owner's profile."""


class InvalidReference(CandidateProfileError):
    """A link points outside the owner's profile, or to an unknown taxonomy/lookup value."""


class InvalidRecord(CandidateProfileError):
    """The database rejected the record (CHECK, NOT NULL, uniqueness or enum value)."""


class PermissionDenied(CandidateProfileError):
    """The database refused a write the owner is not permitted to make (e.g. a provenance column)."""


class InvalidTransition(CandidateProfileError):
    """The review action is not allowed from the record's current review status."""


class RecordNotEditable(CandidateProfileError):
    """The record cannot be edited in its current state (e.g. it is rejected; reinstate it first)."""


class CorrectionNotAvailable(RecordNotEditable):
    """The edit would correct a reviewed, extracted, inferred or legacy record. Corrections need
    structured correction history, which does not exist yet, so they are refused rather than
    overwriting the original value."""


class DeletionBlocked(CandidateProfileError):
    """The database refused a delete that would remove or detach a reviewed record (TR008), orphan a
    document-verified record's source (TR009), or duplicate a skill on detachment (TR010)."""


class IdentityMismatch(CandidateProfileError):
    """The owner session's request identity (auth.uid()) is missing or is not the trusted owner."""


class CorrectionHistoryViolation(CandidateProfileError):
    """Correction history is append-only (TR016), or a record became user_corrected without its
    history entry (TR017)."""


class IngestionNotAllowed(CandidateProfileError):
    """The database refused an extraction outside the allowed scope (TR012: synthetic test-fixture
    profiles and resumes only, result bound to that fixture and resume)."""


class ConsentRequired(CandidateProfileError):
    """No current resume_processing grant under an approved consent document (TR013)."""


class IngestionConflict(CandidateProfileError):
    """The extraction run or profile is in a state that forbids this ingestion (TR014: run already
    ingested with a different result, or failed; TR015: the profile already holds records)."""


class MappingConflict(CandidateProfileError):
    """A role mapping was refused (M5): the title changed since the mapping was proposed (TR018), or an
    automatic mapping is not allowed (TR019: context-required or non-matching alias, or it would replace
    the candidate's own decision or a mapping set outside the mapping functions)."""


class ClarificationRequired(CandidateProfileError):
    """Mapping a product-ownership title (Product Owner) to a product-management role needs at least one
    PM-ownership responsibility area (TR020)."""


class TargetIsCurrentRole(CandidateProfileError):
    """The requested target is the candidate's current standard role (same canonical role id): a career path needs a
    different destination, so no pathway is built (Phase 7.2)."""


class ConsentConflict(CandidateProfileError):
    """A consent decision named a document that is not the current approved version."""


class ProfileAlreadyExists(CandidateProfileError):
    """The owner already has a candidate profile (one profile per owner, enforced by the API layer's service)."""


class ProfileAmbiguous(CandidateProfileError):
    """The owner has more than one candidate profile, so none is chosen silently."""


class RecordChanged(CandidateProfileError):
    """Optimistic concurrency: the record changed since the caller read it (its version no longer matches the
    expected version). Nothing was written; the caller re-reads and decides again."""


class OrderConflictsWithDates(CandidateProfileError):
    """An explicit placement would put a role before or after another in a way their clearly established start dates
    contradict (decision D10). Nothing was written; the user changes the dates or chooses another position."""


class ClassificationConflict(CandidateProfileError):
    """An evidence classification was refused because the evidence text changed since it was proposed (TR018 from
    trace_review.classify_evidence_areas)."""


# Table, primary key and whether the table carries the v2.2.0 provenance columns.
TABLES: Dict[str, tuple] = {
    "employer": ("trace_candidate_employers", "candidate_employer_id", False),
    "employment": ("trace_candidate_employments", "candidate_employment_id", True),
    "education": ("trace_candidate_education", "candidate_education_id", True),
    "activity": ("trace_candidate_career_activities", "candidate_activity_id", True),
    "evidence": ("trace_candidate_evidence", "candidate_evidence_id", True),
    "skill": ("trace_candidate_skills", "candidate_skill_id", True),
    "career_break": ("trace_candidate_career_breaks", "candidate_break_id", True),
}

PROVENANCE_COLS = ("information_origin", "review_status", "verification_status", "extraction_confidence",
                   "source_resume_id", "extraction_run_id", "source_location")


# SQLSTATEs raised by the v2.3.0 guard triggers and trace_review functions.
GUARD_ERRORS = {
    "TR001": RecordNotEditable,
    "TR022": InvalidTransition,          # v2.14.0 proposal guard (decided, foreign record, rewrite)
    "TR023": CorrectionHistoryViolation,  # v2.15.0 ordering events are append-only
    "TR027": InvalidTransition,          # v2.23.0 a completed transition-plan run is immutable
    "TR028": InvalidRecord,              # v2.23.0 an invalid transition-plan payload
    "TR002": CorrectionNotAvailable,
    "TR003": InvalidTransition,
    "TR004": IdentityMismatch,
    "TR005": RecordNotFound,
    "TR006": InvalidReference,
    "TR008": DeletionBlocked,
    "TR009": DeletionBlocked,
    "TR010": DeletionBlocked,
    "TR011": PermissionDenied,
    "TR012": IngestionNotAllowed,
    "TR013": ConsentRequired,
    "TR014": IngestionConflict,
    "TR015": IngestionConflict,
    "TR016": CorrectionHistoryViolation,
    "TR017": CorrectionHistoryViolation,
    "TR018": MappingConflict,
    "TR019": MappingConflict,
    "TR020": ClarificationRequired,
}

# One statement sets every identity setting auth.uid()/auth.role() read, transaction-locally.
IDENTITY_SQL = ("select set_config('request.jwt.claim.sub', %(sub)s, true), "
                "set_config('request.jwt.claim.role', 'authenticated', true), "
                "set_config('request.jwt.claims', %(claims)s, true)")


def _map_db_error(exc: psycopg.Error) -> CandidateProfileError:
    msg = (exc.diag.message_primary if getattr(exc, "diag", None) else None) or str(exc)
    guard = GUARD_ERRORS.get(getattr(exc, "sqlstate", None) or "")
    if guard is not None:
        return guard(msg)
    if isinstance(exc, psycopg.errors.ForeignKeyViolation):
        return InvalidReference(msg)
    if isinstance(exc, psycopg.errors.InsufficientPrivilege):
        return PermissionDenied(msg)
    if isinstance(exc, (psycopg.errors.CheckViolation, psycopg.errors.NotNullViolation, psycopg.errors.UniqueViolation,
                        psycopg.errors.InvalidTextRepresentation, psycopg.errors.InvalidParameterValue,
                        psycopg.errors.StringDataRightTruncation, psycopg.errors.NumericValueOutOfRange)):
        return InvalidRecord(msg)
    return CandidateProfileError(msg)


@contextmanager
def owner_session(conn: psycopg.Connection, owner_user_id: UUID) -> Iterator["CandidateProfileRepository"]:
    """One transaction running as the owner (role ``trace_app_user``, a member of ``authenticated`` that alone holds the
    candidate write grants; request identity = owner).
    Commits on success, rolls back on any error. Database errors surface as CandidateProfileError
    subclasses.

    The owner comes only from the trusted caller. All identity settings are transaction-local, so a
    session-level value left on a reused connection can never become the identity, and the session
    refuses to run anything if ``auth.uid()`` is not exactly the owner (IdentityMismatch)."""
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        raise RuntimeError("owner_session needs a connection with no open transaction")
    previous_autocommit = conn.autocommit
    conn.autocommit = False
    try:
        with conn.transaction():
            conn.execute("set local role trace_app_user")             # the backend's owner role (not the Data API's)
            conn.execute(IDENTITY_SQL, {"sub": str(owner_user_id),
                                        "claims": json.dumps({"sub": str(owner_user_id), "role": "authenticated"})})
            uid = conn.execute("select auth.uid()").fetchone()[0]
            if uid != owner_user_id:
                raise IdentityMismatch("the request identity does not match the trusted owner")
            yield CandidateProfileRepository(conn, owner_user_id)
    except psycopg.Error as exc:
        raise _map_db_error(exc) from exc
    finally:
        conn.autocommit = previous_autocommit


class CandidateProfileRepository:
    """SQL for one owner. Use only through :func:`owner_session`."""

    def __init__(self, conn: psycopg.Connection, owner_user_id: UUID):
        self._conn = conn
        self.owner_user_id = owner_user_id

    # ------------------------------------------------------------------ helpers
    def _rows(self, sql: str, params: dict) -> List[dict]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def _scope(self, profile_id: UUID) -> dict:
        return {"owner": self.owner_user_id, "profile": profile_id}

    @staticmethod
    def _adapt(value):
        return Jsonb(value) if isinstance(value, dict) else value

    # ------------------------------------------------------------------ profiles
    def insert_profile(self, values: dict) -> UUID:
        cols = list(values)
        sql = (f"insert into public.trace_candidate_profiles ({', '.join(cols)}) "
               f"values ({', '.join('%(' + c + ')s' for c in cols)}) returning candidate_profile_id")
        return self._rows(sql, values)[0]["candidate_profile_id"]

    # ------------------------------------------------------------------ consent and resume metadata (Phase 2)
    def own_reference_profile_ids(self) -> List[str]:
        """The caller's own contributed reference profile ids (request identity), for server-side exclusion only."""
        return [r["profile_id"] for r in self._rows(
            "select p as profile_id from trace_contrib.my_reference_profile_ids() as p", {})]

    def consent_documents(self, purpose: str) -> List[dict]:
        """Approved, effective, unretired consent documents for a purpose, newest first."""
        return self._rows(
            "select consent_version, title, body_sha256, effective_from from public.trace_consent_documents "
            "where purpose = %(purpose)s::public.trace_consent_purpose and legal_review_status = 'approved' "
            "and effective_from <= now() and retired_at is null order by effective_from desc, consent_version desc",
            {"purpose": purpose})

    def latest_consent_event(self, purpose: str) -> Optional[dict]:
        """The owner's latest decision for a purpose (same order as trace_ref.v_current_consent)."""
        rows = self._rows(
            "select status::text, consent_version, recorded_at from public.trace_consent_events where user_id = %(owner)s "
            "and purpose = %(purpose)s::public.trace_consent_purpose order by recorded_at desc, consent_event_id desc limit 1",
            {"owner": self.owner_user_id, "purpose": purpose})
        return rows[0] if rows else None

    def insert_consent_event(self, purpose: str, status: str, version: str) -> None:
        self._rows("insert into public.trace_consent_events (purpose, status, consent_version) values "
                   "(%(purpose)s::public.trace_consent_purpose, %(status)s::public.trace_consent_status, %(version)s) returning 1",
                   {"purpose": purpose, "status": status, "version": version})

    def insert_resume(self, profile_id: UUID, storage_object_path: str, filename: Optional[str], byte_size: int, sha256: str,
                      mime_type: str = "application/pdf") -> UUID:
        """Metadata only. mime_type is the format proven by the file's own content (never the declared header alone).
        retention_until is left NULL: no retention period is approved for resume metadata (v2.31.0)."""
        return self._rows(
            "insert into public.trace_resume_documents (candidate_profile_id, storage_object_path, original_filename, mime_type, "
            "byte_size, content_sha256) values (%(profile)s, %(path)s, %(name)s, %(mime)s, %(size)s, %(sha)s) returning resume_id",
            {"profile": profile_id, "path": storage_object_path, "name": filename, "mime": mime_type, "size": byte_size,
             "sha": sha256})[0]["resume_id"]

    def resume_at_path(self, profile_id: UUID, storage_object_path: str) -> Optional[UUID]:
        rows = self._rows("select resume_id from public.trace_resume_documents where storage_object_path = %(path)s "
                          "and owner_user_id = %(owner)s and candidate_profile_id = %(profile)s",
                          {"path": storage_object_path, **self._scope(profile_id)})
        return rows[0]["resume_id"] if rows else None

    def delete_resume(self, profile_id: UUID, resume_id: UUID) -> None:
        self._rows("delete from public.trace_resume_documents where resume_id = %(resume)s and candidate_profile_id = %(profile)s "
                   "and owner_user_id = %(owner)s returning 1", {"resume": resume_id, **self._scope(profile_id)})

    def owner_profile_ids(self) -> List[UUID]:
        """Every profile of the session's owner, oldest first."""
        return [r["candidate_profile_id"] for r in self._rows(
            "select candidate_profile_id from public.trace_candidate_profiles where owner_user_id = %(owner)s "
            "order by created_at, candidate_profile_id", {"owner": self.owner_user_id})]

    def lock_owner_profiles(self) -> None:
        """Transaction-scoped advisory lock per owner: serialises profile creation for one owner."""
        self._rows("select pg_advisory_xact_lock(hashtextextended('trace.candidate_profile:' || %(owner)s::text, 0)) as locked",
                   {"owner": self.owner_user_id})

    def profile_exists(self, profile_id: UUID) -> bool:
        return bool(self._rows(
            "select 1 from public.trace_candidate_profiles where candidate_profile_id = %(profile)s and owner_user_id = %(owner)s",
            self._scope(profile_id)))

    def update_profile(self, profile_id: UUID, values: dict) -> None:
        sets = ", ".join(f"{c} = %({c})s" for c in values)
        rows = self._rows(
            f"update public.trace_candidate_profiles set {sets} "
            f"where candidate_profile_id = %(profile)s and owner_user_id = %(owner)s returning candidate_profile_id",
            {**values, **self._scope(profile_id)})
        if not rows:
            raise ProfileNotFound(str(profile_id))

    # ------------------------------------------------------------------ records
    def insert_record(self, kind: str, profile_id: UUID, values: dict) -> UUID:
        table, pk, _ = TABLES[kind]
        values = {k: self._adapt(v) for k, v in values.items()}
        values.update({"owner_user_id": self.owner_user_id, "candidate_profile_id": profile_id})
        cols = list(values)
        sql = (f"insert into public.{table} ({', '.join(cols)}) "
               f"values ({', '.join('%(' + c + ')s' for c in cols)}) returning {pk}")
        return self._rows(sql, values)[0][pk]

    def update_record(self, kind: str, profile_id: UUID, record_id: UUID, values: dict) -> None:
        """Update content columns of one record of this owner's profile. Never touches review
        status or provenance; the service decides beforehand whether the edit is allowed."""
        table, pk, _ = TABLES[kind]
        values = {k: self._adapt(v) for k, v in values.items()}
        sets = [f"{c} = %({c})s" for c in values]
        rows = self._rows(
            f"update public.{table} set {', '.join(sets)} "
            f"where {pk} = %(record_id)s and owner_user_id = %(owner)s and candidate_profile_id = %(profile)s returning {pk}",
            {**values, "record_id": record_id, **self._scope(profile_id)})
        if not rows:
            raise RecordNotFound(f"{kind} {record_id}")

    def lock_review_state(self, kind: str, profile_id: UUID, record_id: UUID) -> dict:
        """Lock one record (FOR UPDATE, for the rest of the owner transaction) and return its
        information_origin and review_status. Raises RecordNotFound outside this owner's profile."""
        table, pk, has_provenance = TABLES[kind]
        if not has_provenance:
            raise InvalidRecord(f"{kind} records have no review status")
        rows = self._rows(
            f"select information_origin::text, review_status::text, updated_at from public.{table} "
            f"where {pk} = %(record_id)s and owner_user_id = %(owner)s and candidate_profile_id = %(profile)s for update",
            {"record_id": record_id, **self._scope(profile_id)})
        if not rows:
            raise RecordNotFound(f"{kind} {record_id}")
        return rows[0]

    def lock_employer_state(self, profile_id: UUID, employer_id: UUID) -> dict:
        """Lock one employer and every employment that shows it, and return the employer's
        attribute_provenance and those employments' review statuses as read under the locks.

        Lock order is always: employer row, then its employment rows in candidate_employment_id
        order. Coordination with the other operations:
        * Review actions lock the employment row (lock_review_state, FOR UPDATE). Whichever side
          locks it first wins; the other waits. Under READ COMMITTED a FOR UPDATE that waited
          returns the row as committed by the other transaction, so the statuses returned here are
          the current ones, never a stale pre-lock read.
        * Pointing an employment at this employer runs the foreign-key check, which takes
          FOR KEY SHARE on the employer row and therefore waits for this FOR UPDATE. No employment can
          join the locked set while the edit is in progress.
        Review actions take no employer lock, so the two operations cannot deadlock."""
        p = {"record_id": employer_id, **self._scope(profile_id)}
        rows = self._rows(
            "select attribute_provenance, updated_at from public.trace_candidate_employers "
            "where candidate_employer_id = %(record_id)s and owner_user_id = %(owner)s "
            "and candidate_profile_id = %(profile)s for update", p)
        if not rows:
            raise RecordNotFound(f"employer {employer_id}")
        statuses = [r["review_status"] for r in self._rows(
            "select review_status::text from public.trace_candidate_employments "
            "where candidate_employer_id = %(record_id)s and owner_user_id = %(owner)s "
            "and candidate_profile_id = %(profile)s order by candidate_employment_id for update", p)]
        return {"attribute_provenance": rows[0]["attribute_provenance"], "employment_statuses": statuses,
                "updated_at": rows[0]["updated_at"]}

    def review_record(self, kind: str, profile_id: UUID, record_id: UUID, action: str) -> str:
        """Apply one review action through trace_review.review_record (the only path that can change
        review_status). The function locks the row FOR UPDATE, applies the M1 transition matrix and
        acts only for auth.uid(). Returns the resulting status."""
        return self._rows("select trace_review.review_record(%(kind)s, %(profile)s, %(record_id)s, %(action)s) as status",
                          {"kind": kind, "record_id": record_id, "action": action, **self._scope(profile_id)})[0]["status"]

    def reassign_employment(self, profile_id: UUID, employment_id: UUID, employer_id: Optional[UUID]) -> None:
        """Change an employment's employer through trace_review.reassign_employment (the only path that
        can). Lock order inside the function: the old and new employer rows by id (FOR KEY SHARE), then
        the employment row (FOR UPDATE). Allowed only for the owner's pending user_stated draft."""
        self._rows("select trace_review.reassign_employment(%(profile)s, %(record_id)s, %(employer)s) as done",
                   {"record_id": employment_id, "employer": employer_id, **self._scope(profile_id)})

    # ------------------------------------------------------------------ corrections (M4)
    def correct_record(self, kind: str, profile_id: UUID, record_id: UUID, changes: dict) -> str:
        """trace_review.correct_record: locks the record, applies the allow-listed scalar changes, sets
        user_corrected and appends the history entry, in one statement. Returns the new status."""
        return self._rows("select trace_review.correct_record(%(kind)s, %(profile)s, %(record_id)s, %(changes)s) as status",
                          {"kind": kind, "record_id": record_id, "changes": Jsonb(changes), **self._scope(profile_id)})[0]["status"]

    def correction_history(self, profile_id: UUID, kind: str, record_id: UUID) -> List[dict]:
        return self._rows(
            "select correction_id, correction_seq, record_kind, record_id, changed_fields, previous_values, corrected_values, "
            "prior_review_status::text, corrected_at, corrected_by from public.trace_candidate_corrections "
            "where owner_user_id = %(owner)s and candidate_profile_id = %(profile)s and record_kind = %(kind)s "
            "and record_id = %(record_id)s order by correction_seq",
            {"kind": kind, "record_id": record_id, **self._scope(profile_id)})

    # ------------------------------------------------------------------ role mapping (M5)
    def lock_mapping_state(self, profile_id: UUID, employment_id: UUID) -> dict:
        """Lock one employment (FOR UPDATE) and return its title, review status and current mapping."""
        rows = self._rows(
            "select title_raw, review_status::text, role_id, mapping_status::text, role_mapping_basis, mapping_is_stale "
            "from public.trace_candidate_employments where candidate_employment_id = %(record_id)s "
            "and owner_user_id = %(owner)s and candidate_profile_id = %(profile)s for update",
            {"record_id": employment_id, **self._scope(profile_id)})
        if not rows:
            raise RecordNotFound(f"employment {employment_id}")
        return rows[0]

    def taxonomy_roles(self) -> Dict[str, dict]:
        """Every taxonomy role (lookup table): id, canonical title, family, resolved and active flags."""
        return {r["role_id"]: r for r in self._rows(
            "select role_id, canonical_title, role_family, is_resolved, active from public.trace_role_taxonomy order by role_id", {})}

    def target_roles(self) -> List[dict]:
        """The active PM target roles, in ladder order (seniority rank, then id)."""
        return self._rows(
            "select t.role_id, t.canonical_title from public.trace_role_taxonomy t "
            "left join public.trace_seniority_levels s on s.seniority_code = t.default_seniority_code "
            "where t.is_pm_target and t.active order by s.rank nulls last, t.role_id", {})

    def responsibility_area_rows(self) -> List[dict]:
        return self._rows("select responsibility_area, label, is_pm_ownership_signal from public.trace_responsibility_areas "
                          "order by responsibility_area", {})

    def pm_ownership_areas(self) -> List[str]:
        return [r["responsibility_area"] for r in self._rows(
            "select responsibility_area from public.trace_responsibility_areas where is_pm_ownership_signal "
            "order by responsibility_area", {})]

    # ------------------------------------------------------------------ evidence classification (M6)
    def lock_evidence_state(self, profile_id: UUID, evidence_id: UUID) -> dict:
        """Lock one evidence record (FOR UPDATE) and return its text and review status."""
        rows = self._rows(
            "select evidence_text, review_status::text, updated_at from public.trace_candidate_evidence where candidate_evidence_id = %(record_id)s "
            "and owner_user_id = %(owner)s and candidate_profile_id = %(profile)s for update",
            {"record_id": evidence_id, **self._scope(profile_id)})
        if not rows:
            raise RecordNotFound(f"evidence {evidence_id}")
        return rows[0]

    def taxonomy_rows(self) -> List[dict]:
        """Phase 3.1: the responsibility-area taxonomy with definitions, examples, scope questions and versions."""
        return self._rows("select responsibility_area, label, is_pm_ownership_signal, description, examples, qualifiers, "
                          "introduced_in_version, definition_version, trace_util.current_taxonomy_version() as current_version "
                          "from public.trace_responsibility_areas order by introduced_in_version, label", {})

    def classify_evidence_v2(self, profile_id: UUID, evidence_id: UUID, areas: List[str], none: bool, qualifiers: dict,
                             expected_text: str) -> List[str]:
        """trace_review.classify_evidence_v2 (v2.18.0): areas with optional scope answers, or "None of these areas"; records
        the taxonomy version and time. TR018: the text changed since it was shown (ClassificationConflict)."""
        try:
            return self._rows("select trace_review.classify_evidence_v2(%(profile)s, %(record_id)s, %(areas)s::text[], %(none)s, "
                              "%(qualifiers)s, %(text)s) as areas",
                              {"record_id": evidence_id, "areas": areas, "none": none, "qualifiers": Jsonb(qualifiers or {}),
                               "text": expected_text, **self._scope(profile_id)})[0]["areas"]
        except psycopg.Error as exc:
            if getattr(exc, "sqlstate", None) == "TR018":
                raise ClassificationConflict(exc.diag.message_primary or str(exc)) from exc
            raise

    def clear_evidence_classification(self, profile_id: UUID, evidence_id: UUID) -> None:
        self._rows("select trace_review.clear_evidence_classification(%(profile)s, %(record_id)s) as done",
                   {"record_id": evidence_id, **self._scope(profile_id)})

    def responsibility_area_codes(self) -> List[str]:
        return [r["responsibility_area"] for r in self._rows(
            "select responsibility_area from public.trace_responsibility_areas order by responsibility_area", {})]

    def classify_evidence_areas(self, profile_id: UUID, evidence_id: UUID, areas: List[str], expected_text: str) -> List[str]:
        """trace_review.classify_evidence_areas: the candidate's classification (candidate_selected). TR018 here means
        the evidence text changed, so it is raised as ClassificationConflict rather than MappingConflict."""
        try:
            return self._rows("select trace_review.classify_evidence_areas(%(profile)s, %(record_id)s, %(areas)s::text[], %(text)s) as areas",
                              {"record_id": evidence_id, "areas": areas, "text": expected_text, **self._scope(profile_id)})[0]["areas"]
        except psycopg.Error as exc:
            if getattr(exc, "sqlstate", None) == "TR018":
                raise ClassificationConflict(exc.diag.message_primary or str(exc)) from exc
            raise

    def apply_alias_mapping(self, profile_id: UUID, employment_id: UUID, alias_id: str, expected_title: str) -> str:
        """trace_review.apply_alias_mapping: the database re-checks the alias against the record's title and
        takes role, seniority hint and confidence from the alias row."""
        return self._rows("select trace_review.apply_alias_mapping(%(profile)s, %(record_id)s, %(alias)s, %(title)s) as role_id",
                          {"record_id": employment_id, "alias": alias_id, "title": expected_title,
                           **self._scope(profile_id)})[0]["role_id"]

    def choose_employment_role(self, profile_id: UUID, employment_id: UUID, role_id: str, expected_title: str,
                               ownership_areas: Optional[List[str]]) -> str:
        """trace_review.choose_employment_role: the candidate's own decision (candidate_selected)."""
        return self._rows("select trace_review.choose_employment_role(%(profile)s, %(record_id)s, %(role)s, %(title)s, %(areas)s::text[]) as role_id",
                          {"record_id": employment_id, "role": role_id, "title": expected_title, "areas": ownership_areas,
                           **self._scope(profile_id)})[0]["role_id"]

    # ------------------------------------------------------------------ extraction ingestion (M3)
    def start_extraction(self, profile_id: UUID, resume_id: UUID, extractor_version: str, real: bool = False) -> UUID:
        """trace_ingest.start_fixture_extraction (or, for a user's own de-identified resume, start_real_extraction):
        checks ownership, fixture scope or de-identification admission, and consent, and returns the resume's
        extraction run (an existing succeeded or open run, or a new 'queued' one)."""
        fn = "trace_ingest.start_real_extraction" if real else "trace_ingest.start_fixture_extraction"
        return self._rows(f"select {fn}(%(profile)s, %(resume)s, %(version)s) as run",
                          {"resume": resume_id, "version": extractor_version, **self._scope(profile_id)})[0]["run"]

    def ingest_extraction(self, run_id: UUID, payload: dict) -> str:
        """trace_ingest.ingest_fixture_extraction: inserts every extracted record as a source_extracted,
        pending_review draft in one statement, or nothing. Re-checks scope and consent."""
        return self._rows("select trace_ingest.ingest_fixture_extraction(%(run)s, %(payload)s) as status",
                          {"run": run_id, "payload": Jsonb(payload)})[0]["status"]

    def fail_extraction(self, run_id: UUID, error_code: str) -> str:
        """trace_ingest.fail_fixture_extraction: marks a queued run failed (nothing was ingested)."""
        return self._rows("select trace_ingest.fail_fixture_extraction(%(run)s, %(code)s) as status",
                          {"run": run_id, "code": error_code})[0]["status"]

    def extraction_run(self, run_id: UUID) -> Optional[dict]:
        rows = self._rows(
            "select extraction_run_id, resume_id, candidate_profile_id, status, error_code, result_sha256, provider_name, "
            "model_name, prompt_version from public.trace_extraction_runs "
            "where extraction_run_id = %(run)s and owner_user_id = %(owner)s", {"run": run_id, "owner": self.owner_user_id})
        return rows[0] if rows else None

    # ------------------------------------------------------------------ retrieval
    # ------------------------------------------------------------------ manual entry and update proposals (Phase 2)
    def lock_employment_order(self, profile_id: UUID) -> List[dict]:
        """Every employment of the profile (all review states), locked FOR UPDATE in candidate_employment_id order, with
        what placement needs: stated order, start bounds, origin and status. Used before an entry changes the order."""
        return self._rows(
            "select candidate_employment_id, sequence_index, title_raw, start_lo, start_hi, information_origin::text, "
            "review_status::text, updated_at "
            "from public.trace_candidate_employments where owner_user_id = %(owner)s and candidate_profile_id = %(profile)s "
            "order by candidate_employment_id for update", self._scope(profile_id))

    def employer_by_name(self, profile_id: UUID, name_key: str) -> Optional[UUID]:
        """An existing employer of this profile whose name normalises to name_key (reused, never edited), or None."""
        for r in self._rows("select candidate_employer_id, employer_name from public.trace_candidate_employers "
                            "where owner_user_id = %(owner)s and candidate_profile_id = %(profile)s "
                            "order by created_at, candidate_employer_id", self._scope(profile_id)):
            if r["employer_name"] and " ".join(r["employer_name"].lower().split()) == name_key:
                return r["candidate_employer_id"]
        return None

    def move_employment(self, profile_id: UUID, employment_id: UUID, new_index: int, cause: str,
                        caused_by: Optional[UUID]) -> None:
        """trace_review.move_employment (v2.15.0): a position-only move with its ordering event; the review status never
        changes. A rejected role is refused (TR001)."""
        self._rows("select trace_review.move_employment(%(profile)s, %(record_id)s, %(index)s, %(cause)s, %(caused_by)s) as done",
                   {"record_id": employment_id, "index": new_index, "cause": cause, "caused_by": caused_by, **self._scope(profile_id)})

    def order_events(self, profile_id: UUID, employment_id: UUID) -> List[dict]:
        return self._rows(
            "select event_seq, previous_sequence_index, new_sequence_index, review_status_at_move::text, cause, "
            "caused_by_employment_id, moved_at from public.trace_candidate_order_events where owner_user_id = %(owner)s "
            "and candidate_profile_id = %(profile)s and employment_id = %(record_id)s order by event_seq",
            {"record_id": employment_id, **self._scope(profile_id)})

    def share_lock_employer(self, profile_id: UUID, employer_id: UUID) -> None:
        """FOR KEY SHARE on one of this profile's employers, taken before any employment lock (the global order)."""
        self._rows("select 1 from public.trace_candidate_employers where candidate_employer_id = %(record_id)s "
                   "and owner_user_id = %(owner)s and candidate_profile_id = %(profile)s for key share",
                   {"record_id": employer_id, **self._scope(profile_id)})

    def employment_employer_id(self, profile_id: UUID, employment_id: UUID) -> Optional[UUID]:
        """The employer of one of this profile's employments (read without a lock). RecordNotFound outside the profile."""
        rows = self._rows("select candidate_employer_id from public.trace_candidate_employments where candidate_employment_id = %(record_id)s "
                          "and owner_user_id = %(owner)s and candidate_profile_id = %(profile)s",
                          {"record_id": employment_id, **self._scope(profile_id)})
        if not rows:
            raise RecordNotFound(f"employment {employment_id}")
        return rows[0]["candidate_employer_id"]

    def insert_proposal(self, profile_id: UUID, values: dict) -> Optional[UUID]:
        """One open proposal. An identical open proposal (same fingerprint) already exists: None (never a duplicate)."""
        values = {**{k: self._adapt(v) for k, v in values.items()}, "owner_user_id": self.owner_user_id,
                  "candidate_profile_id": profile_id}
        cols = list(values)
        rows = self._rows(
            f"insert into public.trace_profile_update_proposals ({', '.join(cols)}) "
            f"values ({', '.join('%(' + c + ')s' for c in cols)}) "
            "on conflict (candidate_profile_id, fingerprint) where status = 'proposed' do nothing returning proposal_id", values)
        return rows[0]["proposal_id"] if rows else None

    def open_proposals(self, profile_id: UUID) -> List[dict]:
        return self._rows(
            "select proposal_id, batch_id, source, record_kind, match_state, target_record_id, proposed, created_at "
            "from public.trace_profile_update_proposals where owner_user_id = %(owner)s and candidate_profile_id = %(profile)s "
            "and status = 'proposed' order by created_at, proposal_id", self._scope(profile_id))

    def lock_proposal(self, profile_id: UUID, proposal_id: UUID) -> dict:
        rows = self._rows(
            "select proposal_id, source, source_sha256, extractor_version, record_kind, match_state, target_record_id, proposed, status "
            "from public.trace_profile_update_proposals where proposal_id = %(record_id)s and owner_user_id = %(owner)s "
            "and candidate_profile_id = %(profile)s for update", {"record_id": proposal_id, **self._scope(profile_id)})
        if not rows:
            raise RecordNotFound(f"proposal {proposal_id}")
        return rows[0]

    def decide_proposal(self, profile_id: UUID, proposal_id: UUID, status: str, resulting_record_id: Optional[UUID]) -> None:
        self._rows(
            "update public.trace_profile_update_proposals set status = %(status)s, proposed = null, decided_at = now(), "
            "resulting_record_id = %(result)s where proposal_id = %(record_id)s and owner_user_id = %(owner)s "
            "and candidate_profile_id = %(profile)s and status = 'proposed' returning proposal_id",
            {"status": status, "result": resulting_record_id, "record_id": proposal_id, **self._scope(profile_id)})

    def source_already_proposed(self, profile_id: UUID, source_sha256: str) -> bool:
        return bool(self._rows(
            "select 1 from public.trace_profile_update_proposals where owner_user_id = %(owner)s "
            "and candidate_profile_id = %(profile)s and source_sha256 = %(sha)s limit 1",
            {"sha": source_sha256, **self._scope(profile_id)}))

    # ------------------------------------------------------------------ resume imports (resume-first onboarding, D47)
    # An import is one resume row and the PENDING source-extracted drafts that came from it. Every statement is scoped
    # to the session's owner and profile (RLS applies as well); deletions touch pending (or, for "start over",
    # rejected) records only, so a reviewed record can never be removed here.
    _IMPORT_TABLES = ("trace_candidate_employments", "trace_candidate_education", "trace_candidate_skills",
                      "trace_candidate_career_activities", "trace_candidate_evidence")

    def open_import(self, profile_id: UUID) -> Optional[dict]:
        pending = " or ".join(f"exists (select 1 from public.{t} x where x.source_resume_id = r.resume_id and x.review_status = 'pending_review')"
                              for t in self._IMPORT_TABLES)
        rows = self._rows(f"""select r.resume_id, r.uploaded_at, r.byte_size from public.trace_resume_documents r
                              where r.owner_user_id = %(owner)s and r.candidate_profile_id = %(profile)s and r.deleted_at is null
                                and not r.is_test_fixture and ({pending})
                              order by r.uploaded_at desc limit 1""", self._scope(profile_id))
        return rows[0] if rows else None

    def import_document_exists(self, profile_id: UUID, resume_id: UUID) -> bool:
        return bool(self._rows("select 1 from public.trace_resume_documents where resume_id = %(r)s and owner_user_id = %(owner)s "
                               "and candidate_profile_id = %(profile)s", {"r": resume_id, **self._scope(profile_id)}))

    def resume_sha_is(self, profile_id: UUID, resume_id: UUID, sha256: str) -> bool:
        return bool(self._rows("select 1 from public.trace_resume_documents where resume_id = %(r)s and content_sha256 = %(sha)s "
                               "and owner_user_id = %(owner)s and candidate_profile_id = %(profile)s",
                               {"r": resume_id, "sha": sha256, **self._scope(profile_id)}))

    def import_rows(self, profile_id: UUID, resume_id: UUID) -> Dict[str, List[dict]]:
        p = {**self._scope(profile_id), "resume": resume_id}
        scope = ("owner_user_id = %(owner)s and candidate_profile_id = %(profile)s and source_resume_id = %(resume)s "
                 "and review_status = 'pending_review'")
        return {
            "employment": self._rows("""select e.candidate_employment_id as id, e.title_raw, e.employment_type, e.sequence_index,
                    e.start_year, e.start_month, e.start_precision::text, e.end_year, e.end_month, e.end_precision::text,
                    e.is_current_as_stated, e.candidate_employer_id, er.employer_name, e.source_location
                  from public.trace_candidate_employments e left join public.trace_candidate_employers er
                    on er.candidate_employer_id = e.candidate_employer_id
                 where e.owner_user_id = %(owner)s and e.candidate_profile_id = %(profile)s and e.source_resume_id = %(resume)s
                   and e.review_status = 'pending_review' order by e.sequence_index desc""", p),
            "evidence": self._rows(f"""select candidate_evidence_id as id, candidate_employment_id, evidence_type::text, evidence_text,
                    source_location from public.trace_candidate_evidence where {scope} order by source_location, candidate_evidence_id""", p),
            "education": self._rows(f"""select candidate_education_id as id, qualification_title_raw, qualification_level, field_of_study,
                    institution_name, start_year, start_month, start_precision::text, end_year, end_month, end_precision::text,
                    source_location from public.trace_candidate_education where {scope} order by end_year desc nulls last""", p),
            "skill": self._rows(f"""select candidate_skill_id as id, skill_text_raw, candidate_employment_id, source_location
                  from public.trace_candidate_skills where {scope} order by source_location, candidate_skill_id""", p),
            "activity": self._rows(f"""select candidate_activity_id as id, activity_type::text, title_raw, organisation_raw,
                    description_raw, end_year, source_location from public.trace_candidate_career_activities where {scope}
                  order by source_location""", p),
        }

    # ------------------------------------------------------------------ staged resume imports (D48, v2.31.0)
    @contextmanager
    def as_runtime(self) -> Iterator[None]:
        """Call a runtime-only function (trace_contrib, trace_ingest admission) inside THIS owner transaction, with the
        same request identity, so it commits or rolls back with everything else. The owner role is restored after."""
        self._conn.execute("set local role trace_app_runtime")
        yield
        self._conn.execute("set local role trace_app_user")

    def save_import_draft(self, profile_id: UUID, draft: dict, parser_version: str, pipeline_version: str, fmt: str, mime_type: str,
                          byte_size: int, sha256: str, warnings: List[str], sealed: Optional[bytes], key_id: Optional[str]) -> UUID:
        return self._rows("""select trace_import.save_draft(%(profile)s, %(draft)s, %(pv)s, %(dv)s, %(fmt)s, %(mime)s, %(size)s,
                                    %(sha)s, %(warn)s, %(sealed)s, %(key)s) as id""",
                          {"profile": profile_id, "draft": Jsonb(draft), "pv": parser_version, "dv": pipeline_version, "fmt": fmt,
                           "mime": mime_type, "size": byte_size, "sha": sha256, "warn": list(warnings), "sealed": sealed,
                           "key": key_id})[0]["id"]

    def import_draft(self, profile_id: UUID, lock: bool = False) -> Optional[dict]:
        rows = self._rows("select * from trace_import.my_draft(%(profile)s, %(lock)s)", {"profile": profile_id, "lock": lock})
        return rows[0] if rows else None

    def delete_import_draft(self, profile_id: UUID, import_id: Optional[UUID] = None) -> int:
        return self._rows("select trace_import.delete_draft(%(profile)s, %(id)s) as n", {"profile": profile_id, "id": import_id})[0]["n"]

    def admit_resume(self, profile_id: UUID, resume_id: UUID, sha256: str, pipeline_version: str) -> None:
        with self.as_runtime():
            self._rows("select trace_ingest.admit_deidentified_resume(%(profile)s, %(resume)s, %(sha)s, %(pv)s) as x",
                       {"profile": profile_id, "resume": resume_id, "sha": sha256, "pv": pipeline_version})

    def log_resume_processing(self, outcome: str, reasons: Sequence[str] = ()) -> None:
        with self.as_runtime():
            self._rows("select trace_contrib.log_resume_processing(%(o)s, %(r)s) as x", {"o": outcome, "r": list(reasons)})

    def contribution_consent_active(self) -> bool:
        with self.as_runtime():
            rows = self._rows("select consent_active from trace_contrib.contribution_context(%(owner)s)", {"owner": self.owner_user_id})
        return bool(rows and rows[0]["consent_active"])

    def store_identity_markers(self, sealed: bytes, key_id: str) -> None:
        with self.as_runtime():
            self._rows("select trace_contrib.store_identity_markers(%(s)s, %(k)s) as x", {"s": sealed, "k": key_id})

    def forget_identity_markers(self) -> bool:
        """Delete the owner's markers unless an active contribution consent still needs them (the database decides)."""
        with self.as_runtime():
            return bool(self._rows("select trace_contrib.forget_identity_markers() as x", {})[0]["x"])

    # ------------------------------------------------------------------ clear the career profile (D52)
    _CLEAR_COUNTS = (("jobs", "trace_candidate_employments"), ("employers", "trace_candidate_employers"),
                     ("experience_lines", "trace_candidate_evidence"), ("skills", "trace_candidate_skills"),
                     ("education", "trace_candidate_education"), ("activities", "trace_candidate_career_activities"),
                     ("career_breaks", "trace_candidate_career_breaks"), ("corrections", "trace_candidate_corrections"),
                     ("saved_plans", "trace_pathway_runs"))

    def career_profile_counts(self, profile_id: UUID) -> Dict[str, int]:
        """What clearing would delete, counted as the owner (RLS limits every count to the owner's own rows)."""
        out = {}
        for key, table in self._CLEAR_COUNTS:
            out[key] = self._rows(f"select count(*)::int as n from public.{table} where owner_user_id = %(owner)s "
                                  f"and candidate_profile_id = %(profile)s", self._scope(profile_id))[0]["n"]
        out["pending_imports"] = 1 if self.import_draft(profile_id) is not None else 0
        out["resume_records"] = self._rows("select count(*)::int as n from public.trace_resume_documents where owner_user_id = %(owner)s",
                                           self._scope(profile_id))[0]["n"]
        return out

    def clear_career_profile(self, profile_id: UUID) -> UUID:
        """Delete the owner's candidate profile and everything that depends on it, then create a fresh, empty profile.
        Runs inside the caller's owner transaction (all or nothing). The profile row goes through the owner's own delete
        policy and every dependent record by foreign-key cascade (the account-deletion path the guards allow); the
        resume metadata rows by the owner delete policy; the quarantine and processing log by
        trace_import.clear_my_import_records (v2.33.0). Returns the new profile id."""
        row = self._rows("select country_code from public.trace_candidate_profiles where candidate_profile_id = %(profile)s "
                         "and owner_user_id = %(owner)s for update", self._scope(profile_id))
        if not row:
            raise LookupError("profile not found")
        self._rows("delete from public.trace_agent_threads where owner_user_id = %(owner)s and candidate_profile_id = %(profile)s "
                   "returning 1", self._scope(profile_id))
        self._rows("delete from public.trace_candidate_profiles where candidate_profile_id = %(profile)s and owner_user_id = %(owner)s "
                   "returning 1", self._scope(profile_id))
        self._rows("delete from public.trace_resume_documents where owner_user_id = %(owner)s returning 1", self._scope(profile_id))
        self._rows("select * from trace_import.clear_my_import_records()", {})
        return self.insert_profile({"owner_user_id": self.owner_user_id, "country_code": row[0]["country_code"]})

    def has_any_records(self, profile_id: UUID) -> bool:
        for t in self._IMPORT_TABLES + ("trace_candidate_career_breaks",):
            if self._rows(f"select 1 from public.{t} where owner_user_id = %(owner)s and candidate_profile_id = %(profile)s limit 1",
                          self._scope(profile_id)):
                return True
        return False

    def unconfirmed_outside(self, profile_id: UUID, resume_id: Optional[UUID]) -> int:
        """Pending or rejected records that aren't part of the open import (leftovers of an earlier attempt)."""
        n = 0
        for t in self._IMPORT_TABLES:
            n += self._rows(f"""select count(*) as n from public.{t} where owner_user_id = %(owner)s and candidate_profile_id = %(profile)s
                                 and review_status in ('pending_review', 'rejected')
                                 and (source_resume_id is distinct from %(imp)s or review_status = 'rejected')""",
                            {**self._scope(profile_id), "imp": resume_id})[0]["n"]
        return n

    def delete_pending(self, kind: str, profile_id: UUID, record_id: UUID) -> int:
        table, pk, _ = TABLES[kind]
        return len(self._rows(f"delete from public.{table} where {pk} = %(id)s and owner_user_id = %(owner)s "
                              f"and candidate_profile_id = %(profile)s and review_status = 'pending_review' returning 1",
                              {"id": record_id, **self._scope(profile_id)}))

    def delete_unconfirmed(self, kind: str, profile_id: UUID) -> int:
        table, _, _ = TABLES[kind]
        return len(self._rows(f"delete from public.{table} where owner_user_id = %(owner)s and candidate_profile_id = %(profile)s "
                              f"and review_status in ('pending_review', 'rejected') returning 1", self._scope(profile_id)))

    def drop_unused_employers(self, profile_id: UUID) -> None:
        self._rows("""delete from public.trace_candidate_employers er
                       where er.owner_user_id = %(owner)s and er.candidate_profile_id = %(profile)s
                         and not exists (select 1 from public.trace_candidate_employments e where e.candidate_employer_id = er.candidate_employer_id)
                      returning 1""", self._scope(profile_id))

    def resume_has_records(self, resume_id: UUID) -> bool:
        return any(self._rows(f"select 1 from public.{t} where source_resume_id = %(r)s and owner_user_id = %(owner)s limit 1",
                              {"r": resume_id, "owner": self.owner_user_id}) for t in self._IMPORT_TABLES)

    def delete_orphan_resumes(self, profile_id: UUID) -> None:
        none = " and ".join(f"not exists (select 1 from public.{t} x where x.source_resume_id = r.resume_id)" for t in self._IMPORT_TABLES)
        self._rows(f"""delete from public.trace_resume_documents r where r.owner_user_id = %(owner)s
                        and r.candidate_profile_id = %(profile)s and not r.is_test_fixture and {none} returning 1""", self._scope(profile_id))

    def any_resume_documents(self, profile_id: UUID) -> bool:
        return bool(self._rows("select 1 from public.trace_resume_documents where owner_user_id = %(owner)s "
                               "and candidate_profile_id = %(profile)s limit 1", self._scope(profile_id)))

    def proposal_batch_exists(self, profile_id: UUID, batch_id: UUID, sha256: Optional[str] = None) -> bool:
        return bool(self._rows("select 1 from public.trace_profile_update_proposals where batch_id = %(b)s and owner_user_id = %(owner)s "
                               "and candidate_profile_id = %(profile)s and (%(sha)s::text is null or source_sha256 = %(sha)s) limit 1",
                               {"b": batch_id, "sha": sha256, **self._scope(profile_id)}))

    def record_title(self, kind: str, profile_id: UUID, record_id: UUID) -> Optional[str]:
        table, pk, _ = TABLES[kind]
        col = {"employment": "title_raw", "education": "qualification_title_raw"}[kind]
        rows = self._rows(f"select {col} as t from public.{table} where {pk} = %(id)s and owner_user_id = %(owner)s "
                          f"and candidate_profile_id = %(profile)s", {"id": record_id, **self._scope(profile_id)})
        return rows[0]["t"] if rows else None

    def resume_hash_registered(self, profile_id: UUID, sha256: str) -> bool:
        return bool(self._rows(
            "select 1 from public.trace_resume_documents where owner_user_id = %(owner)s and candidate_profile_id = %(profile)s "
            "and content_sha256 = %(sha)s and deleted_at is null limit 1", {"sha": sha256, **self._scope(profile_id)}))

    def fetch_profile_rows(self, profile_id: UUID) -> Optional[Dict[str, List[dict]]]:
        p = self._scope(profile_id)
        profile = self._rows("""
            select candidate_profile_id, headline, country_code, confirmation_status,
                   resume_as_of_year, resume_as_of_month, resume_as_of_precision
            from public.trace_candidate_profiles
            where candidate_profile_id = %(profile)s and owner_user_id = %(owner)s""", p)
        if not profile:
            return None
        scoped = "where t.owner_user_id = %(owner)s and t.candidate_profile_id = %(profile)s"
        prov = ", ".join(f"t.{c}" for c in PROVENANCE_COLS)
        return {
            "profile": profile,
            "employers": self._rows(f"""
                select t.candidate_employer_id, t.employer_name, t.industry_raw, t.industry_sector, t.company_model,
                       t.business_model, t.company_size_band, t.attribute_provenance,
                       t.updated_at as version
                from public.trace_candidate_employers t {scoped} order by t.created_at, t.candidate_employer_id""", p),
            "employments": self._rows(f"""
                select t.candidate_employment_id, t.sequence_index, t.candidate_employer_id, t.title_raw, t.role_id,
                       r.canonical_title as normalised_title, r.role_family, t.seniority_code, t.mapping_status::text,
                       t.mapping_confidence, t.mapping_note, t.role_mapping_basis, t.role_mapping_alias_id, t.mapped_title_raw,
                       t.role_mapping_ownership_areas, t.mapping_is_stale, t.employment_type::text, t.primary_career_sequence,
                       t.counts_as_full_time_experience, t.classification_note,
                       t.start_year, t.start_month, t.start_precision::text, t.end_year, t.end_month, t.end_precision::text,
                       t.is_current_as_stated, t.entry_source, t.participant_confirmed, {prov}, t.updated_at
                from public.trace_candidate_employments t
                left join public.trace_role_taxonomy r on r.role_id = t.role_id
                {scoped} order by t.sequence_index""", p),
            "education": self._rows(f"""
                select t.candidate_education_id, t.qualification_title_raw, t.qualification_level, t.institution_name,
                       t.field_of_study, t.study_mode, t.completion_status, t.notes,
                       t.start_year, t.start_month, t.start_precision::text, t.end_year, t.end_month, t.end_precision::text, {prov}, t.updated_at
                from public.trace_candidate_education t {scoped} order by t.start_lo nulls last, t.created_at, t.candidate_education_id""", p),
            "activities": self._rows(f"""
                select t.candidate_activity_id, t.activity_type::text, t.title_raw, t.category_code,
                       t.category_mapping_status::text, t.category_mapping_confidence, t.organisation_raw, t.description_raw,
                       t.contribution_raw, t.outcome_raw, t.link_url, t.status, t.related_employment_id,
                       t.credential_identifier_raw, t.metadata,
                       t.start_year, t.start_month, t.start_precision::text, t.end_year, t.end_month, t.end_precision::text,
                       t.expiry_year, t.expiry_month, t.expiry_precision::text, {prov}, t.updated_at
                from public.trace_candidate_career_activities t {scoped} order by t.start_lo nulls last, t.created_at, t.candidate_activity_id""", p),
            "evidence": self._rows(f"""
                select t.candidate_evidence_id, t.evidence_type, t.responsibility_area, t.evidence_text,
                       t.candidate_employment_id, t.candidate_activity_id, t.area_classification, t.area_classification_basis,
                       t.area_classified_text, t.area_classification_is_stale, t.area_classification_none, t.area_qualifiers,
                       t.area_classification_taxonomy_version, {prov}, t.updated_at
                from public.trace_candidate_evidence t {scoped} order by t.created_at, t.candidate_evidence_id""", p),
            "skills": self._rows(f"""
                select t.candidate_skill_id, t.skill_id, t.skill_text_raw, s.canonical_skill, s.skill_family,
                       t.stated_proficiency_raw, t.evidence_source, t.candidate_employment_id, t.candidate_education_id,
                       t.candidate_activity_id, t.candidate_evidence_id, {prov}, t.updated_at
                from public.trace_candidate_skills t
                left join public.trace_skills s on s.skill_id = t.skill_id
                {scoped} order by t.created_at, t.candidate_skill_id""", p),
            "career_breaks": self._rows(f"""
                select t.candidate_break_id, t.break_reason, t.note,
                       t.start_year, t.start_month, t.start_precision::text, t.end_year, t.end_month, t.end_precision::text, {prov}, t.updated_at
                from public.trace_candidate_career_breaks t {scoped} order by t.start_lo nulls last, t.created_at, t.candidate_break_id""", p),
        }


def load_sequencing_context(conn: psycopg.Connection) -> SequencingContext:
    """Taxonomy lookups and min_mapping_confidence for career_history sequencing. Reads shared,
    non-personal configuration with the service connection (trace_ref is not readable by owners).
    A missing setting fails closed inside career_history (threshold 1.0)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("select role_id, role_family, default_seniority_code, is_resolved, is_pm_target from public.trace_role_taxonomy")
        roles = {r["role_id"]: RoleInfo(**{k: v for k, v in r.items() if k != "role_id"}) for r in cur.fetchall()}
        cur.execute("select seniority_code, rank from public.trace_seniority_levels")
        ranks = {r["seniority_code"]: r["rank"] for r in cur.fetchall()}
        cur.execute("select value_numeric from trace_ref.analytics_settings where setting_key = 'min_mapping_confidence'")
        row = cur.fetchone()
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE and not conn.autocommit:
        conn.rollback()
    return SequencingContext(roles=roles, seniority_ranks=ranks,
                             min_mapping_confidence=float(row["value_numeric"]) if row else None)


def known_kinds() -> Sequence[str]:
    return tuple(TABLES)
