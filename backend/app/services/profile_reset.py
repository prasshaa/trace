"""Clear my career profile and start over (D52).

The owner's candidate profile and everything derived from it are deleted in ONE owner transaction, then a fresh, empty
profile is created so the account can start again at the upload step. What goes, through the owner's own rights:

- the candidate profile row (owner delete policy) and, by foreign-key cascade, every record that depends on it: jobs,
  their employers, experience lines (evidence), skills, education, certifications and projects (career activities),
  career breaks, role classifications (on the jobs), correction and ordering history, extraction runs, the open or
  rejected import draft (with any sealed identity markers), legacy update proposals, identity markers, and saved
  career plans (pathway runs, with their routes, steps, evidence and actions);
- the owner's resume metadata rows (owner delete policy; Trace never stores the file itself);
- the owner's quarantined records and processing-log rows (trace_import.clear_my_import_records, v2.33.0).

Capability assessments are computed from the profile on request and never stored; Trace has no embeddings. What is
kept, and why: the account and sign-in; the consent choices (they record what the user agreed to and when); planning
preferences (target role, goal, where to move), which hold no career facts; and the sharing choice. A de-identified
copy already shared under the sharing choice is removed only by turning sharing off (D42), and the page says so.
Nothing outside the caller's own rows is touched: every statement is owner-scoped and RLS applies.
"""
from typing import Dict
from uuid import UUID

import psycopg

from app.services.candidate_profile import OwnerContext
from app.services.candidate_profile.repository import InvalidRecord, owner_session
from app.services.real_resume import FixtureProfileConflict

KEPT = ("account", "consent_choices", "planning_preferences", "sharing_choice")
CONFIRM_PHRASE = "clear my career profile"


class ClearNotConfirmed(InvalidRecord):
    """The explicit confirmation is missing (422): nothing was deleted."""


class ProfileResetService:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def _fixture(self, owner: UUID) -> bool:
        with self._conn.transaction():
            row = self._conn.execute("select is_fixture_profile from trace_contrib.contribution_context(%s)", (owner,)).fetchone()
        return bool(row and row[0])

    def preview(self, ctx: OwnerContext) -> Dict:
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            counts = repo.career_profile_counts(ctx.candidate_profile_id)
            sharing = repo.contribution_consent_active()
        return {"deletes": counts, "keeps": list(KEPT), "sharing_active": sharing, "confirm_phrase": CONFIRM_PHRASE,
                "empty": not any(counts.values())}

    def clear(self, ctx: OwnerContext, confirm: str) -> Dict:
        if (confirm or "").strip().lower() != CONFIRM_PHRASE:
            raise ClearNotConfirmed("type the confirmation to clear your career profile")
        if self._fixture(ctx.owner_user_id):
            raise FixtureProfileConflict("this profile holds a synthetic sample; it can't be cleared here")
        with owner_session(self._conn, ctx.owner_user_id) as repo:
            counts = repo.career_profile_counts(ctx.candidate_profile_id)
            if not any(counts.values()):                        # idempotent: already clear (a retry, or a new account)
                repo._rows("select * from trace_import.clear_my_import_records()", {})
                return {"status": "already_clear", "deleted": counts}
            repo.clear_career_profile(ctx.candidate_profile_id)
        return {"status": "cleared", "deleted": counts}
