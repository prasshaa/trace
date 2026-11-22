# Reference career-history evidence retrieval

**Package:** `app/services/reference_evidence/` (`models.py`, `repository.py`, `service.py`). There is no API route.

**What it does.** Given a destination role and an explicit data mode, it retrieves de-identified reference career routes into that role, plus the foundation's cohort summary for each route.

**What it does not do.** Evidence retrieval only: no ranking, suitability scoring or recommendations.

**Built on data foundation v2.1.0**, which adds episode-aware pathways (migration `20261111130000_trace_episode_pathways.sql`, `data-foundation/docs/DATA_DICTIONARY.md` §6a). A route is a sequence of **employment episodes** (role + employer). Consecutive identical role ids are separate episodes entered by an employer change. For example, `{PM-PM, PM-PM}` is PM at one employer followed by PM at another.

> **Development-only connection.** The local `DATABASE_URL` connects as `postgres`, which bypasses RLS. This is not a production access model.

## Foundation objects used

| Object | Why |
|---|---|
| `public.trace_role_taxonomy` | Validates the destination `role_id` and provides role families for route steps |
| `trace_ref.v_career_episode_paths` **joined to** `trace_ref.v_mode_profiles` | Confirmed episode segments containing the destination, with `entry_change_types` per stint. The view includes every dataset class, so the join on `data_mode` is mandatory. For `empirical`, `v_mode_profiles` is exactly `v_eligible_profiles` |
| `trace_ref.episode_route_observations(p_route text[], p_data_mode)` | Each individual's `step_change_types`, `route_months_min/max` and `is_precise`. Filters by mode itself |
| `trace_ref.episode_route_timeline_summary(p_route text[], p_data_mode)` | Cohort figures and labels: `supporting_profile_count`, `dated_profile_count`, `count_display_allowed`, `evidence_class`, percentiles, coarse range, `timeline_metric`, `timeline_display_allowed`. Passed through unchanged; the route is echoed exactly as given |

**Replaced in v2.1.0.** These legacy role-code objects are no longer queried, not even as a fallback:

| Legacy object | Replaced by |
|---|---|
| `v_career_paths` | `v_career_episode_paths` |
| `route_observations` | `episode_route_observations` |
| `route_timeline_summary` | `episode_route_timeline_summary` |

The legacy objects merged consecutive same-role records whatever the employer, so same-role employer moves were lost. These are SYN-018, 019, 025, 026, 027, 029, 030 (PM-PM) and SYN-032 (PM-DIR).

## From a destination to a route

1. Read the mode's episode segments that contain the destination (exact `role_id` match).
2. For each individual, take the **first** confirmed route into the destination: the lowest segment where the destination is at stint position ≥ 2. The route runs from that segment's start up to the destination.
   - Every stint after the first was entered by a confirmed pathway move: `role_change`, `employer_change`, `role_and_employer_change` or `role_change_employer_unknown`.
   - So a person whose first recorded role is already the destination role is included as soon as they move employer in it.
   - Each person contributes at most one observation.
3. For each distinct route, call `episode_route_observations`. The service checks that each person's segment, start position and change types match the path row.
4. For each distinct route, call `episode_route_timeline_summary`.

If the foundation sources disagree with each other or with the mode, or the foundation rejects a route the service built from the foundation's own paths, the service raises `EvidenceIntegrityError`. It never returns evidence in that case.

## Mode isolation and eligibility

These rules are unchanged from the previous version.

- `data_mode` is required and has no default. Allowed values are `empirical` and `synthetic_test`; `synthetic_demo` is rejected.
- Every query in a call uses that one mode, with no fallback.
- Every path row must have the mode's dataset class, and every summary must have the same mode. Only synthetic modes may carry `synthetic_test_only`, and synthetic summaries must not allow timeline display.
- One read-only `REPEATABLE READ` transaction (`reference_snapshot`) per retrieval means consent and eligibility are read once, at retrieval time. A withdrawal applies to the next retrieval.
- No eligibility rules are added beyond the foundation's own.

## Invalid routes

The repository maps the foundation's SQLSTATE `22023` to `InvalidEpisodeRoute`. The foundation raises it for:
- fewer than two steps;
- a null step;
- a multi-dimensional array;
- an unknown or unresolved role id.

Inside retrieval, a rejection is an `EvidenceIntegrityError`, never an empty or zero-count result.

## Origin role (M5)

`EvidenceRequest(destination_role_id=..., data_mode=..., origin_role_id="PM-APM")` restricts evidence to routes that start in the origin role: for each individual, the route from their first episode in the origin role to their first later episode in the destination, within one confirmed segment (lowest segment first). That is also the first place the route occurs in their history, so `episode_route_observations` must report the same segment and start position (checked; otherwise `EvidenceIntegrityError`). The first step's `entry_change_type` is `null` (the route starts there). Origin may equal the destination: the route is then a move between two episodes in that role. Cohort summaries come from `episode_route_timeline_summary` unchanged. An unknown origin raises `UnknownOriginRole`. Without an origin, behaviour is unchanged. The candidate-facing caller is `CandidateProfileService.route_evidence` (current role as origin, nine PM targets, synthetic test data only).

## Route step experience (M6)

`ReferenceEvidenceResult.route_step_experience` lists, for every observed route in the result, the responsibility areas recorded by the observed reference profiles on the steps **before** the destination. The destination step is excluded; with an origin role, so are steps before the origin.

- **Query** (`STEP_EXPERIENCE_SQL`, read-only, one call per retrieval): for each observation in the result, its profile, segment and pre-destination stint range go to `v_career_episode_stints`, joined to `v_mode_profiles` for the requested mode (the same isolation as the paths) and to `trace_ref.experience_evidence` through the stints' employment ids. It returns distinct (profile, area) rows. Evidence with status `rejected` and evidence without an area are ignored.
- **Aggregation** (service): counted once per distinct profile, however many evidence rows or steps record an area; the service de-duplicates itself and does not rely on the query doing so. Every returned profile must be one of the result's observations, and every area must exist in `trace_responsibility_areas` (`EvidenceIntegrityError` otherwise).
- **Disclosure rules** (threshold = the foundation's `min_profiles_limited`, 5 by default; a missing setting discloses nothing). `observation_count` (this result's distinct observations of the route) is the denominator.

  | `state` | When | Disclosed |
  |---|---|---|
  | `suppressed_small_cohort` | fewer observations than the threshold | nothing about the route's areas, not even whether any were recorded; `observation_count` is `null` |
  | `no_recorded_areas` | enough observations, none recorded an area | `observation_count` (genuinely empty) |
  | `areas_withheld` | enough observations; areas were recorded, each by fewer profiles than the threshold | `observation_count`; no area is named |
  | `areas_disclosed` | at least one area reached the threshold | only those areas, with their exact `profile_count` (always at least the threshold); `areas_withheld` says whether others were recorded below it |

  `is_common` is true only for a listed area recorded by **more than half** of the observations (so 5 of 10 is not common); it is never derived from a withheld area. Areas are listed in code order, never by count. No profile identifiers, segment numbers, dates or timelines are included.

Local synthetic data, origin `PM-APM`, destination `PM-PM`:

```json
{"level": "cohort_summary", "data_mode": "synthetic_test", "route": ["PM-APM", "PM-PM"], "pre_destination_steps": ["PM-APM"],
 "state": "areas_disclosed", "observation_count": 13, "areas_withheld": true,
 "areas": [{"responsibility_area": "outcome_ownership", "label": "Outcome ownership", "is_pm_ownership_signal": true,
            "profile_count": 5, "is_common": false},
           {"responsibility_area": "prioritisation_backlog", "label": "Prioritisation and backlog", "is_pm_ownership_signal": true,
            "profile_count": 5, "is_common": false}]}
```

`observation_count` counts this result's observations of the route (one per person, as picked above). It can be lower than the route summary's `supporting_profile_count`, which counts everyone with the route anywhere in their history. Areas describe what was recorded, not what was required to make the move. The M5 route summaries and observations are unchanged by these rules (see Limitations, item 7).

## Input contract

```python
EvidenceRequest(destination_role_id="PM-PM", data_mode="synthetic_test", destination_company=None)
```

**Usage:**

```python
with db.connect(settings) as conn, reference_snapshot(conn) as repo:
    result = retrieve_reference_evidence(request, repo)
```

**Errors:**

| Error | Raised when |
|---|---|
| `UnknownDestinationRole` | The `role_id` is not in the taxonomy |
| `UnsupportedEvidenceMode` | `synthetic_demo` is requested |
| `UnsupportedFilter` | A destination company is given |
| `EvidenceIntegrityError` | The foundation sources are inconsistent |
| `InvalidEpisodeRoute` | The repository is called directly with an invalid route |

## Output contract (`ReferenceEvidenceResult`)

**Result-level fields:**
- `data_mode`;
- `is_synthetic_test_data`;
- `evidence_notice`;
- `interpretation_note`: frequency is not a probability of success;
- `destination`;
- `has_observed_pathways`;
- `limitations`.

**`observations[]`** (`level = "individual_observation"`), one per person:
- `route`;
- `steps[]`, each with `role_id`, `role_family`, `entry_change_type` (`null` for the first step), `start_lo/hi`, `YYYY-MM` labels and `start_is_month_precise`;
- `step_change_types` from the foundation (one per step after the first);
- `timeline_metric`, plus `route_months_min/max` and `is_precise`;
- `provenance` (`profile_id`, `dataset_class`, `segment_no`). It is **excluded from serialisation and `repr`** and kept for internal auditability.

**No employer identifier exists anywhere in the contract.**

**`route_summaries[]`** (`level = "cohort_summary"`) carries the `episode_route_timeline_summary` fields verbatim.

**Example** (`PM-PM`, `synthetic_test`, local data; 31 observations across 20 routes, one of each shown):

```json
{"level": "individual_observation", "route": ["PM-PM", "PM-PM"],
 "steps": [
   {"role_id": "PM-PM", "role_family": "product_management", "entry_change_type": null,
    "start_lo": 24240, "start_hi": 24240, "start_lo_label": "2020-01", "start_hi_label": "2020-01", "start_is_month_precise": true},
   {"role_id": "PM-PM", "role_family": "product_management", "entry_change_type": "employer_change",
    "start_lo": 24279, "start_hi": 24279, "start_lo_label": "2023-04", "start_hi_label": "2023-04", "start_is_month_precise": true}],
 "step_change_types": ["employer_change"],
 "timeline_metric": "route_start_to_target_start_months", "route_months_min": 39, "route_months_max": 39, "is_precise": true}

{"level": "cohort_summary", "data_mode": "synthetic_test", "route": ["PM-PM", "PM-PM"],
 "supporting_profile_count": 7, "dated_profile_count": 7, "count_display_allowed": true,
 "evidence_class": "synthetic_test_only", "p25_months": null, "median_months": null, "p75_months": null,
 "coarse_min_months": 34, "coarse_max_months": 42,
 "timeline_metric": "route_start_to_target_start_months", "timeline_display_allowed": false}
```

For a same-role route, `route_months` measures the time from the start of the first episode to the start of the episode at the new employer.

## Limitations and open decisions

1. **Destination company is not supported by the schema.** The filter is refused, not ignored.
2. **A single episode in the destination role is not an observed pathway.**
3. **One observation per person.** Each person contributes only their first route into the destination role. For example, PM → PM → PM yields the route `{PM-PM, PM-PM}`, the first arrival in a PM episode by a move.
4. **Seniority within one role id is not an episode boundary** (pending the PM II decision).
5. **An unstated employer merges conservatively.** PM at A → PM with no employer stated → PM at B is one episode, because the move can't be placed.
6. **Summaries don't separate change types.** They match routes by role sequence only, so `{PM-APM, PM-PM}` counts internal and external moves together. Each observation carries its own change types.
7. **Raw counts and individual timing are still returned.** `count_display_allowed` (below 5) is passed through unchanged, and individual route months are returned per observation. Showing either to users is a separate display and privacy decision. No user-facing API exists.
8. **Ineligible and withdrawn real profiles aren't tested against the database here.** They are covered by the in-memory repository in this backend, and against the real database by the foundation's pgTAP suite (`trace_episode_pathways.test.sql`).
