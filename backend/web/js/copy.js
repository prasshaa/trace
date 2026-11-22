// Plain-language copy for API codes. The API returns stable codes; this file only translates them for people.
// No numbers, scores, probabilities or claims are created here.

// Only "aligned" is a strong fit: the only tier shown as a recommendation.
export const FIT = {
  aligned: { label: "Strong match", tone: "positive", text: "Your confirmed experience covers every area of responsibility most profiles on this path show." },
  partial: { label: "Partial match", tone: "accent", text: "Your confirmed experience covers some of the areas of responsibility most profiles on this path show." },
  limited: { label: "Few matches yet", tone: "caution", text: "Your confirmed experience doesn't yet show the areas of responsibility most profiles on this path show." },
  not_established: { label: "Fit could not be established", tone: "neutral",
    text: "This career move appears in the available profiles, but there isn't enough comparable experience to determine how closely it matches your profile." },
};

export const FIT_REASON = {
  reference_evidence_suppressed_small_cohort: "Fewer than five profiles show this path, so their experience isn't shown or compared.",
  reference_areas_withheld: "Too few profiles on this path share the same areas of responsibility to describe them reliably.",
  reference_no_recorded_areas: "The profiles on this path don't record areas of responsibility, so no comparison is possible.",
  no_common_area_on_route: "No single area of responsibility is shared by most profiles on this path, so there's nothing common to compare with.",
  no_eligible_classified_evidence: "We couldn't compare your responsibilities yet. Categorising your experience in your profile will help make this comparison more complete.",
};

export const EXPERIENCE_STATE = {
  suppressed_small_cohort: "Fewer than 5 profiles show this path, so their experience isn't described",
  no_recorded_areas: "No areas of responsibility recorded for this path",
  areas_withheld: "Experience details withheld: too few profiles share them",
  areas_disclosed: "Experience recorded in the profiles on this path",
};

export const POSITION_REASON = {
  no_employment_history: "No employment history is recorded yet.",
  no_eligible_primary_employment: "None of your jobs can be used as a current position yet.",
  pending_review: "Confirm your most recent job in your profile to set your starting point.",
  stale_mapping: "Your most recent job title changed after it was matched to a role. Match it again in your profile.",
  unresolved_role_mapping: "Choose the role that best describes your most recent job in your profile.",
  low_mapping_confidence: "Confirm the role of your most recent job in your profile.",
  latest_employment_ineligible: "Your most recent job can't set a starting point yet. Review it in your profile.",
  role_unconfirmed: "Your current job is confirmed, but the standard role matched to it hasn't been confirmed yet. Review the role match to continue.",
};

// Why the latest job can't set a starting point, by the API's exclusion reasons (more specific than the generic reason).
const EXCLUSION = {
  unknown_start_date: "Add the start date of your most recent job in your profile so it can set your starting point.",
  not_full_time_experience: "Your most recent job isn't counted as full-time experience, so it can't set your starting point. "
    + "Check its employment type in your profile.",
  parallel_activity: "Your most recent job is recorded as a side activity, so it can't set your starting point.",
};

/** The most specific explanation for an unknown current position (API reason, then its exclusion reasons). */
export function positionText(p) {
  if (p.reason === "latest_employment_ineligible") {
    const specific = (p.exclusion_reasons || []).map((r) => EXCLUSION[r]).find(Boolean);
    if (specific) return specific;
  }
  return POSITION_REASON[p.reason] || p.explanation || "";
}

// Direction of each move (API routes[].step_directions, Phase 8.2). Missing or null: no label, never a guess.
export const MOVE_DIRECTION = {
  more_senior: "Move to a more senior role",
  more_junior: "Move to a more junior role",
  lateral: "Lateral move",
  career_track_change: "A change in career direction",          // fallback only: see moveLabel (D20)
};

/** D20: a move across role families, named with the taxonomy's family names from the API (never computed here). */
export function familyChangeLabel(change) {
  return change && change.from_family && change.to_family
    ? `Moving from ${change.from_family} to ${change.to_family}`
    : MOVE_DIRECTION.career_track_change;
}

// Why fit couldn't be established (API fit.limitations, Phase 8.1): the actual cause, never a judgement of the user.
export const FIT_LIMITATION = {
  incomplete_user_profile: "Categorise your responsibilities so Trace can compare your experience with this path.",
  insufficient_reference_evidence: "There's not enough career detail across the available profiles to compare this path reliably.",
  insufficient_route_evidence: "Not enough career profiles show this route for Trace to assess it.",
};

export const EVIDENCE_TYPE = {
  responsibility: "Responsibility", achievement: "Achievement", scope: "Scope", leadership: "Leadership",
  skill_evidence: "Skill evidence", other: "Other",
};

export const ACTIVITY_TYPE = {
  certification: "Certification", licence: "Licence", independent_project: "Project", portfolio_project: "Project",
  award: "Award", publication: "Publication", volunteering: "Volunteering", other: "Activity",
};

export const QUALIFICATION_LEVEL = {
  bachelor: "Bachelor's degree", master: "Master's degree", mba: "MBA", doctorate: "Doctorate", phd: "Doctorate",
  diploma: "Diploma", certificate: "Certificate", secondary: "Secondary school",
};

// The stored employment types (enum trace_employment_type), in the order the forms offer them. "Founder" is the nature of
// the work for a founder or co-founder; the role classification (Founder / Co-founder) is separate.
export const EMPLOYMENT_TYPE = {
  full_time: "Full-time", part_time: "Part-time", contract: "Contract", freelance: "Freelance or self-employed",
  internship: "Internship", apprenticeship: "Apprenticeship", founder: "Founder or co-founder", volunteer: "Volunteer", unknown: null,
};

// Profile status in the shared pool: plain titles for the states the API returns (GET /api/v1/sharing -> status.state).
// "Included" is used only for the API's "eligible" state AND included = true (checked where it is shown).
export const PROFILE_STATUS = {
  not_started: { title: "Not shared yet", tone: "neutral" },
  not_enabled: { title: "Not shared yet", tone: "neutral" },
  awaiting_resume: { title: "Not shared yet", tone: "neutral" },
  awaiting_review: { title: "Pending review", tone: "caution" },
  pending: { title: "Processing", tone: "info" },
  processing: { title: "Processing", tone: "info" },
  eligible: { title: "Included", tone: "positive" },
  quarantined: { title: "Manual review", tone: "caution" },
  not_eligible: { title: "Not eligible", tone: "neutral" },
  failed: { title: "Couldn't be processed", tone: "danger" },
  withdrawn: { title: "Removed", tone: "neutral" },
  private: { title: "Not shared", tone: "neutral" },
};

// Where the career patterns come from. The server decides the source; the browser only describes it.
export const SOURCE = {
  synthetic: {
    name: "Fictional test profiles",
    note: "Internal testing: these patterns come from fictional test profiles, not from real people's careers.",
  },
  consented_real: {
    name: "Career profiles shared with Trace",
    note: "Based on career profiles that members have shared with Trace. Only patterns across groups are shown.",
  },
};
export const sourceOf = (key) => SOURCE[key] || SOURCE.consented_real;

/** How counted records are described: from the server's own source classification, never guessed. Never "people". */
export const profilesNoun = (isSynthetic) => (isSynthetic ? "fictional test profiles" : "career profiles");
export const profilesCount = (n, isSynthetic) => `${n} ${n === 1 ? profilesNoun(isSynthetic).replace(/s$/, "") : profilesNoun(isSynthetic)}`;

// Shown next to counts below the server's moderate-sample setting (sample_caution_below / scope.min_target_cohort).
// Not a reliability measure and no new threshold.
export const SMALL_SAMPLE = "Based on a small number of profiles. Treat this as an observed pattern, not a prediction of what will "
  + "happen in your career.";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** A partial date view {year, month, precision} as short text. */
export function partialDate(d) {
  if (!d) return "Unknown";
  if (d.precision === "ongoing") return "Present";
  if (!d.year || d.precision === "unknown") return "Date unknown";
  if (d.month && d.precision === "month") return `${MONTHS[d.month - 1]} ${d.year}`;
  return String(d.year);
}

export function dateRange(start, end) {
  return `${partialDate(start)} – ${partialDate(end)}`;
}

export function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

/** A grade the extraction kept in an education record's notes ("Grade: ..."), or null. Display only. */
export function gradeFromNotes(notes) {
  const m = /(?:^|\n)\s*Grade:\s*(.+)/i.exec(notes || "");
  return m ? m[1].trim() : null;
}

// --------------------------------------------------------------------------- Career Intelligence Report (Phase 5)
// Words for the saved plan's codes. The decisions, states and outcomes are the server's; nothing here ranks, counts,
// scores or chooses. ACTION_WHERE and ACTION_LABEL repeat the action pack's own words (pm-actions-v2 where_values and
// label_values; a test keeps them identical).
export const ACTION_WHERE = {
  current_job: "In your current job",
  internal_move: "Through an internal move or project in your organisation",
  external_project: "In a self-directed or volunteer project",
  self_directed_study: "Self-directed study of the cited sources",
  job_market: "In the job market",
  trace_profile: "In your Trace profile",
};
export const ACTION_LABEL = {
  learning_exercise: "A learning exercise, not professional evidence",
  personal_project_possible: "If done as a personal project, it is labelled as one, never as professional product ownership",
  factual_accuracy: "Says only what your confirmed records show",
  trace_curated: "Curated by Trace; no cited source",
};
export const ACTION_LABEL_SHORT = {
  learning_exercise: "Learning exercise",
  personal_project_possible: "Label personal projects",
  factual_accuracy: "Confirmed records only",
  trace_curated: "Curated by Trace",
};

// The four capability states (D25). Unknown is never a gap.
export const CAPABILITY_STATE = {
  demonstrated: { label: "Demonstrated", tone: "positive", text: "Your confirmed experience shows this." },
  transferable: { label: "Transferable", tone: "accent", text: "Related experience carries over, but doesn't show the requirement itself yet." },
  gap: { label: "Gap", tone: "caution", text: "Your reviewed, categorised experience doesn't show this." },
  unknown: { label: "Unknown", tone: "muted", text: "Trace can't tell yet. This is never treated as a gap." },
};
export const UNKNOWN_REASON = {
  no_reviewed_evidence: "Nothing in your profile has been reviewed yet.",
  evidence_pending_review: "Some of your experience is still waiting for your review.",
  evidence_not_classified: "Some reviewed lines of experience aren't categorised yet.",
  classified_before_taxonomy_update: "Some lines were categorised before the areas of responsibility were updated. Check them again.",
  scope_question_not_answered: "A question about the scope of this work hasn't been answered yet.",
  not_inferable_from_absence: "Your profile can't show this either way: not having a line about it doesn't mean you lack it. Add it if you have it.",
};

export const DECISION_HEADLINE = {
  resolve_first: "Resolve a few profile items first",
  direct: "Move directly into the role",
  direct_conditional: "Move directly, once you show what's still missing",
  bridge: "Move through a bridge role first",
};
export const ROUTE_STATUS = {
  primary: { label: "Selected for you", tone: "positive" },
  tied_primary: { label: "Selected · equally suited", tone: "positive" },
  alternative: { label: "Alternative", tone: "neutral" },
  not_recommended: { label: "Not suited to your profile now", tone: "muted" },
  not_evaluated: { label: "Not compared yet", tone: "muted" },
};
export const DIRECT_SUPPORT = {
  supported: "Your confirmed experience already shows the core product decisions this role owns.",
  conditional: "Possible, but your confirmed profile doesn't yet show some of the decisions this role owns.",
  undetermined: "Trace can't compare this route until the profile items above are resolved.",
};
export const BRIDGE_ENTRY = {
  evidenced: "A credible way in, shown by your profile",
  source_stated: "Described by the source as an entry route",
  target_plan: "Your Product Manager plan is the way in",
  hypothesis: "A possible way in (a curated hypothesis)",
  unclear: "Depends on records you still need to review",
  not_evidenced: "Your profile doesn't show the entry requirements yet",
};
// D4 breadth bands for observed career-history evidence (labels only; never counts, never used to choose).
export const BREADTH = {
  limited: "Limited evidence",
  growing: "Growing evidence",
  broad: "Broad evidence",
  extensive: "Extensive evidence",
  limited_or_unavailable: "Comparable career-history evidence is limited or unavailable",
  synthetic_test_data: "Fictional test data: illustrative only",
};

export const ELIGIBILITY_OUTCOME = {
  meets: { label: "Meets", tone: "positive" },
  does_not_meet: { label: "Doesn't meet", tone: "caution" },
  unclear: { label: "Unclear", tone: "muted" },
};
export const POSTING_USE = {
  current_opportunity: "Current opening, verified",
  requires_revalidation: "Needs re-checking before it can be treated as open",
  historical_eligibility_evidence: "No longer open: shown only as a dated example of this employer's criteria",
};
export const BACKGROUND_STANCE = {
  requires_target_role_experience: "Asks for experience in the role itself",
  accepts_adjacent: "Accepts related backgrounds",
  excludes_specified_transitions: "Excludes some backgrounds",
  not_specified: "Background not specified",
};
