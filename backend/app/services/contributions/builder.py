"""The canonical record of a user's PRIVATE profile, for contribution. Reviewed records only.

Rules: every non-rejected record must be reviewed (confirmed or corrected); a pending one means the profile is not
ready (reason 'review_incomplete'). Rejected records are left out. At least one reviewed employment is needed. The
record holds career facts only; it goes through de-identification again before anything is contributed.
"""
import re
from typing import Optional, Tuple

from app.services.resume_extraction.real_parser import GRADE

REVIEWED = ("user_confirmed", "user_corrected")
ACTIVITY = {"certification": "certification", "licence": "certification", "independent_project": "project",
            "portfolio_project": "project", "award": "award", "publication": "publication", "volunteering": "volunteering"}


def _date(d) -> Tuple[Optional[str], str]:
    if d is None or d.precision in (None, "unknown") or (d.precision != "ongoing" and not d.year):
        return None, "unknown" if d is None or d.precision != "ongoing" else "ongoing"
    if d.precision == "ongoing":
        return None, "ongoing"
    if d.precision == "month" and d.month:
        return f"{d.year:04d}-{d.month:02d}", "month"
    return f"{d.year:04d}", "year"


def _grade(notes: Optional[str]) -> dict:
    g = GRADE.search(notes or "")
    if not g:
        return {}
    if g.group("cgpa"):
        return {"grade_raw": g.group(0).strip(), "grade_scale": "cgpa_10", "grade_value": float(g.group("cgpa"))}
    if g.group("gpa"):
        v, of = float(g.group("gpa")), g.group("of")
        return {"grade_raw": g.group(0).strip(), "grade_scale": "gpa_4" if (of == "4" or (not of and v <= 4)) else "cgpa_10", "grade_value": v}
    return {"grade_raw": g.group(0).strip(), "grade_scale": "percentage", "grade_value": float(g.group("pct"))}


def canonical_record(profile) -> Tuple[Optional[dict], Optional[str]]:
    evidence_all = [x for e in profile.employment_history for x in e.responsibilities + e.achievements + e.other_evidence] \
        + list(profile.unattached_evidence)
    activities_all = [a for acts in profile.career_activities.values() for a in acts]
    everything = [r.provenance for r in [*profile.employment_history, *evidence_all, *profile.education, *profile.skills,
                                         *activities_all, *profile.career_breaks]]
    if any(p.review_status == "pending_review" for p in everything):
        return None, "review_incomplete"
    jobs = sorted([e for e in profile.employment_history if e.provenance.review_status in REVIEWED], key=lambda e: e.sequence_index)
    if not jobs:
        return None, "no_reviewed_employment"
    employers, employer_key, employments, evidence = [], {}, [], []
    for seq, e in enumerate(jobs, 1):
        key = None
        if e.employer:
            key = employer_key.get(e.employer.candidate_employer_id)
            if key is None:
                key = employer_key[e.employer.candidate_employer_id] = f"E{len(employers) + 1}"
                employers.append({"employer_id": key, "employer_name": e.employer.employer_name, "industry_raw": e.employer.industry_raw,
                                  "industry_sector": e.employer.industry_sector, "company_model": e.employer.company_model,
                                  "business_model": e.employer.business_model, "company_size_band": e.employer.company_size_band})
        mapped = e.role_id and e.role_id != "ROLE-UNKNOWN" and not e.mapping_is_stale
        start, sp = _date(e.start)
        end, ep = _date(e.end)
        employments.append({
            "sequence_index": seq, "employer_id": key, "title": e.title_raw, "role_id": e.role_id if mapped else "ROLE-UNKNOWN",
            "seniority_code": e.seniority_code if mapped else None,
            "mapping_status": e.mapping_status if mapped else "unresolved",
            "mapping_confidence": float(e.mapping_confidence) if e.mapping_confidence is not None else (1.0 if mapped else 0.0),
            "employment_type": e.employment_type, "primary_career_sequence": e.primary_career_sequence,
            "start": start, "start_precision": sp, "end": end, "end_precision": ep, "is_current_as_stated": ep == "ongoing"})
        for x in e.responsibilities + e.achievements + e.other_evidence:
            if x.provenance.review_status in REVIEWED and len((x.evidence_text or "").strip()) >= 10:
                area = sorted(x.area_classification)[0] if x.area_classification and not x.area_classification_is_stale else None
                evidence.append({"employment_seq": seq, "evidence_type": x.evidence_type, "text": x.evidence_text.strip(),
                                 "responsibility_area": area})
    education = []
    for d in profile.education:
        if d.provenance.review_status in REVIEWED:
            start, sp = _date(d.start)
            end, ep = _date(d.end)
            education.append({"qualification_level": d.qualification_level, "qualification_title": d.qualification_title_raw,
                              "field_of_study": d.field_of_study, "institution_name": d.institution_name, "study_mode": d.study_mode,
                              "completion_status": d.completion_status, "start": start, "start_precision": sp,
                              "end": end, "end_precision": ep, **_grade(d.notes)})
    # The reference pool records taxonomy skills only (trace_ref.profile_skills.skill_id is required); free-text skills
    # that are not mapped to the taxonomy stay in the private profile.
    skills = [{"text": s.skill_text_raw, "skill_id": s.taxonomy.skill_id}
              for s in profile.skills if s.provenance.review_status in REVIEWED and s.skill_text_raw and s.taxonomy]
    activities = []
    for a in activities_all:
        if a.provenance.review_status in REVIEWED and a.title_raw:
            year = (a.end.year if a.end and a.end.year else None) or (a.start.year if a.start and a.start.year else None)
            activities.append({"activity_type": ACTIVITY.get(a.activity_type, "other"), "title": a.title_raw,
                               "organisation": a.organisation_raw, "description": a.description_raw, **({"year": year} if year else {})})
    breaks = []
    for b in profile.career_breaks:
        if b.provenance.review_status in REVIEWED:
            start, sp = _date(b.start)
            end, ep = _date(b.end)
            breaks.append({"break_reason": b.break_reason, "start": start, "start_precision": sp if sp != "ongoing" else "unknown",
                           "end": end, "end_precision": ep})
    as_of, as_of_p = _date(profile.resume_as_of)
    return {"resume_as_of": as_of, "resume_as_of_precision": as_of_p if as_of_p != "ongoing" else "unknown",
            "country_code": profile.country_code, "employers": employers, "employments": employments, "education": education,
            "evidence": evidence, "skills": skills, "activities": activities, "career_breaks": breaks}, None
