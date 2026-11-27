"""The review accepts a role whose bullets already describe the job, and still refuses an empty one."""
from app.services.candidate_profile.models import DateInput
from app.services.resume_import import ConfirmRequest, PlannedRole, contract

START = DateInput(year=2015, month=1, precision="month")
END = DateInput(year=2016, month=6, precision="month")


def role(**kwargs):
    base = dict(key="J0", title="Support Lead", employer="Palm Retail", employment_type="full_time",
                start=START, end=END, description=None, lines=[])
    base.update(kwargs)
    return PlannedRole(**base)


def descriptions(roles):
    return [e for e in contract(roles, None, False, ConfirmRequest()) if e["field"] == "description"]


def test_bullets_stand_in_for_a_short_or_empty_description():
    bullet = (None, "responsibility", "Owned the support queue and cut first-response time.")
    assert descriptions([role(lines=[bullet])]) == []
    assert descriptions([role(description="Did work.", lines=[bullet])]) == []


def test_an_empty_role_still_needs_a_sentence():
    assert descriptions([role()])
    assert descriptions([role(description="Did work.")])
    assert descriptions([role(description="   ")])
