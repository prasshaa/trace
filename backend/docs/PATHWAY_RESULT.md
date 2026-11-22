# Personalised pathway result (M6)

> **Product direction superseded (2026-11-15).** This documents today's title-route result. The product target is the four-stage Career Intelligence Report (`docs/RECOMMENDATION_ENGINE.md`, `docs/USER_JOURNEY.md`). Use this file for the current implementation only, not as the product specification.

**Entry point:** `CandidateProfileService.pathway_result(ctx, context, target_role_id) -> PathwayResult`. **Logic:** `app/services/candidate_profile/pathway.py` (pure, deterministic, tested without a database).

Read-only. No LLM, no migration, no writes (pathway tables stay empty), no API. Only the nine PM target roles (`InvalidReference` otherwise). Reference evidence is synthetic test data (`synthetic_test`), always labelled.

## What it combines (nothing is recomputed)

| Input | Source |
|---|---|
| Current position | `derive_current_position` over the candidate's sequenced history (M5) |
| Observed routes from the current role to the target | `retrieve_reference_evidence(origin_role_id=current role)`: only the route summaries' routes and `evidence_class` are used |
| Experience recorded before the target on each route | M6 `route_step_experience`, with its four disclosure states |
| The candidate's experience | the candidate's own classified evidence (M6 `classify_evidence`) |

Never used or returned: individual observations, route months, `supporting_profile_count`, `dated_profile_count`, coarse ranges, percentiles, timelines, reference identifiers, the candidate's internal record ids.

## Candidate evidence that counts

Evidence attached to an employment that is in the primary sequence **and** transition-eligible; reviewed (`user_confirmed` or `user_corrected`); with a current (non-stale) `candidate_selected` classification. Everything else is counted by reason in `candidate_evidence.excluded`: `pending_review`, `rejected`, `not_classified`, `classification_stale`, `employment_not_eligible`, `not_attached_to_an_employment`. The legacy `responsibility_area` field, skills and titles are never responsibility-area signals. Each counted area cites the candidate's own record (employment title and role, evidence type and text), never an id.

## Fit

- **Common area** on a route: the route is `areas_disclosed` (at least 5 observations), the area is disclosed (at least 5 profiles) and recorded by **strictly more than half** of `observation_count`. Re-derived from the disclosed figures and checked against the reference service's flag.
- **Tiers** (`fit.tier`, with a `reason`):
  - `aligned`: every common area is in the candidate's eligible evidence;
  - `partial`: some are;
  - `limited`: there are common areas and classified eligible evidence, but none matches;
  - `not_established`: no common area (reason `reference_evidence_suppressed_small_cohort`, `reference_areas_withheld`, `reference_no_recorded_areas` or `no_common_area_on_route`), or no eligible classified evidence (`no_eligible_classified_evidence`). An empty common set is never `aligned`.
- **Gaps:** `area_gaps` are common areas not in the candidate's eligible evidence ("Not recorded in your reviewed profile."). `step_gaps` are intermediate route roles (never the first or last step) not held in the candidate's eligible, mapped history ("not a prerequisite"). No requirement or skill gaps.

## Ranking

`rank` orders routes by fit tier (aligned, partial, limited, not_established), then fewer `step_gaps`, then `route_id` (role ids joined by `>`). Observation counts, supporting counts and route frequency never order anything. No score or probability is produced.

## Guidance

`next_steps` (per route and profile-level) come from fixed templates, each labelled "Generated guidance, not observed fact." and phrased conditionally ("If you have …, add and review evidence … Otherwise, look for an opportunity …"). Templates exist for the four PM-ownership areas, a generic area, an intermediate role, the candidate's own evidence (unclassified, stale, pending, ineligible employment) and each unknown-position reason.

## Result (abridged example, unit-test inputs)

```json
{"current_position": {"status": "known", "role_id": "PM-APM", "canonical_title": "Associate Product Manager", "mapping_basis": "alias_match"},
 "target": {"role_id": "PM-SPM", "canonical_title": "Senior Product Manager"},
 "role_only_notice": "Role-only assessment: company-specific fit is not assessed.",
 "data_mode": "synthetic_test", "is_synthetic_test_data": true, "evidence_notice": "SYNTHETIC TEST DATA. ...",
 "candidate_evidence": {"classified_areas": ["outcome_ownership", "product_discovery"], "supporting_evidence": [...], "excluded": {}},
 "routes": [
  {"rank": 1, "route_id": "PM-APM>PM-SPM", "route": [{"role_id": "PM-APM", ...}, {"role_id": "PM-SPM", ...}],
   "observed": {"data_mode": "synthetic_test", "evidence_class": "synthetic_test_only", "experience_state": "areas_disclosed",
                "observation_count": 6, "disclosed_areas": [{"responsibility_area": "outcome_ownership", "profile_count": 5, "is_common": true}, ...],
                "areas_withheld": false},
   "fit": {"tier": "partial", "reason": "some_common_areas_recorded", "common_areas": ["data_analysis", "outcome_ownership", "roadmap_ownership"],
           "matched_areas": ["outcome_ownership"], "supporting_evidence": [...]},
   "area_gaps": [{"responsibility_area": "roadmap_ownership", "label": "Roadmap ownership", "note": "Not recorded in your reviewed profile."}, ...],
   "step_gaps": [],
   "next_steps": [{"kind": "area_gap", "text": "If you have owned or set a roadmap or product strategy, add and review evidence of that work. Otherwise, ...",
                   "label": "Generated guidance, not observed fact."}]},
  {"rank": 2, "route_id": "PM-APM>PM-PM>PM-SPM",
   "observed": {"experience_state": "suppressed_small_cohort", "observation_count": null, "disclosed_areas": [], "areas_withheld": false},
   "fit": {"tier": "not_established", "reason": "reference_evidence_suppressed_small_cohort", ...},
   "step_gaps": [{"role_id": "PM-PM", "canonical_title": "Product Manager", "note": "... not a prerequisite."}], ...}],
 "next_steps": [...], "limitations": [...],
 "unavailable_assessments": ["Target-role requirements: not assessed ...", "Skills: not assessed ...", "Time on route: not shown ...", "Company-specific fit: not assessed."]}
```

When the current position is unknown, `routes` is empty, `data_mode` is null, a limitation states the reason and `next_steps` starts with the matching guidance. When no route is observed, `routes` is empty and a limitation says so.

## Tests

- `tests/test_pathway_result_unit.py`: common areas, all tiers, the empty-common rule, all four disclosure states, ranking (fit before frequency), gaps, guidance wording, leaks, unknown position and empty results.
- `tests/test_pathway_result_integration.py`: evidence eligibility (pending, rejected, stale, unclassified, legacy field, ineligible employment, activity evidence, duplicates), route parity with M5, disclosure preserved, ranking by missing steps, read-only, unknown position, no routes, PM targets only.
- `tests/test_fixture_ingestion_integration.py`: all 41 fixtures x 9 targets (369 results) against an independent oracle built from `trace_model.py` and the fixture data.
