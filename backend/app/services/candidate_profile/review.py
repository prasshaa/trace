"""Explicit review actions and edit rules for candidate records (M1: backend review guardrails).

Pure rules, no database. The service applies them inside an owner session after locking the row.

Review status changes only through three named actions; there is no free-form status setter and
no "unconfirm":

    action      from                                         to
    confirm     pending_review                               user_confirmed
                user_confirmed, user_corrected               unchanged (no-op)
    reject      pending_review, user_confirmed,              rejected
                user_corrected
                rejected                                     unchanged (no-op)
    reinstate   rejected                                     pending_review

    correct     pending_review (extracted, inferred, legacy),     user_corrected   (M4, with history)
                user_confirmed, user_corrected

Every other combination raises InvalidTransition. In particular a rejected record is never
confirmed or corrected directly (reinstate first), and reinstatement never returns straight to
confirmed. 'user_corrected' is produced only by a correction (trace_review.correct_record), which
also records the previous values in correction history.

Editing is allowed only for the candidate's own unreviewed draft: review_status 'pending_review'
and information_origin 'user_stated'. Editing never changes the review status and keeps no history.
Every other change is a correction (M4): it goes through correct_record, keeps the previous values
in append-only correction history and leaves the record user_corrected.

Since data foundation v2.3.0 (M2) the database enforces the same rules: the matrix lives in
trace_review.next_review_status (parity is tested), and the guard triggers enforce draft-only edits.
These Python rules give early, readable errors; the database is authoritative.
"""
from typing import Dict, Literal, Optional

from app.services.candidate_profile.repository import CorrectionNotAvailable, InvalidTransition, RecordNotEditable

ReviewAction = Literal["confirm", "reject", "reinstate"]
CorrectionAction = Literal["correct"]

_NOOP = object()

TRANSITIONS: Dict[str, Dict[str, object]] = {
    "confirm": {"pending_review": "user_confirmed", "user_confirmed": _NOOP, "user_corrected": _NOOP},
    "reject": {"pending_review": "rejected", "user_confirmed": "rejected", "user_corrected": "rejected",
               "rejected": _NOOP},
    "reinstate": {"rejected": "pending_review"},
    # M4: a correction is a review act; it never applies to rejected records (reinstate first) and never
    # to the candidate's own pending user_stated draft (edited instead, see check_correctable).
    "correct": {"pending_review": "user_corrected", "user_confirmed": "user_corrected", "user_corrected": "user_corrected"},
}

EDITABLE_ORIGIN = "user_stated"
EDITABLE_STATUS = "pending_review"
# Employers have no review status. They are editable only when the candidate entered them and no
# reviewed or rejected employment shows them (that would change reviewed content without history).
# Candidate-entered employers carry attribute_provenance NULL (v2.3.0) or the legacy value
# 'participant_confirmed' (M1 and earlier); every other value names a non-candidate source.
CANDIDATE_EMPLOYER_PROVENANCE = "participant_confirmed"
CANDIDATE_EMPLOYER_PROVENANCES = (None, CANDIDATE_EMPLOYER_PROVENANCE)


def next_review_status(current: str, action: str) -> Optional[str]:
    """The status after `action`, or None when the action is a no-op. Raises InvalidTransition."""
    if action not in TRANSITIONS:
        raise InvalidTransition(f"unknown review action {action!r}")
    target = TRANSITIONS[action].get(current)
    if target is None:
        hint = " (reinstate it first)" if current == "rejected" else ""
        raise InvalidTransition(f"cannot {action} a record that is {current}{hint}")
    return None if target is _NOOP else target  # type: ignore[return-value]


def check_editable(origin: Optional[str], status: str) -> None:
    """Raise unless the record is the candidate's own unreviewed draft."""
    if status == "rejected":
        raise RecordNotEditable("a rejected record cannot be edited; reinstate it first")
    if status != EDITABLE_STATUS:
        raise CorrectionNotAvailable(
            f"the record is {status}; change it with correct_record, which keeps correction history")
    if origin != EDITABLE_ORIGIN:
        raise CorrectionNotAvailable(
            f"the record's origin is {origin or 'not recorded'}; change it with correct_record, which keeps "
            "the original in correction history")


def check_correctable(origin: Optional[str], status: str) -> None:
    """Raise unless the record can be corrected (M4): not rejected, and not the candidate's own pending
    draft, which is edited instead."""
    if status == "rejected":
        raise RecordNotEditable("a rejected record cannot be corrected; reinstate it first")
    if status == EDITABLE_STATUS and origin == EDITABLE_ORIGIN:
        raise CorrectionNotAvailable("a pending user_stated draft is edited, not corrected")
    next_review_status(status, "correct")


def check_employer_editable(attribute_provenance: Optional[str], employment_statuses) -> None:
    """Raise unless the employer was entered by the candidate and every employment that shows it is
    still pending review."""
    if attribute_provenance not in CANDIDATE_EMPLOYER_PROVENANCES:
        raise CorrectionNotAvailable(
            f"the employer's attributes are {attribute_provenance}; changing them would overwrite the original "
            "without correction history, which is not available yet")
    blocking = sorted(set(employment_statuses) - {EDITABLE_STATUS})
    if blocking:
        raise CorrectionNotAvailable(
            f"the employer is shown on employment that is {', '.join(blocking)}; changing it would alter "
            "reviewed content without correction history, which is not available yet")
