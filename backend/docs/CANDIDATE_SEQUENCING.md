# Candidate career-history sequencing

**Package:** `app/services/career_history/`. It is a pure Python service: no FastAPI, no database, no network.

**What it is.** A port of `data-foundation/scripts/trace_model.py` (Trace Data Foundation **v2.1.0**, episode-aware). That script is the executable specification of these SQL views in `trace_ref`:

- `v_employment_classified`
- `v_career_sequence`
- `v_role_transitions` and `v_episode_transitions`
- `v_career_episode_stints` and `v_career_episode_paths` (segments; **not** the legacy `v_career_stints`/`v_career_paths`)
- `v_profile_sequence_integrity`

**What it does.** It applies those rules to a signed-in candidate's own employment records, the rows of `public.trace_candidate_employments`. The database has no such views for candidate data (see `DATABASE_SCHEMA_MAP.md` §8).

```python
from app.services.career_history import (
    CandidateEmployment, PartialDateInput, SequencingContext, sequence_candidate_history,
)

history = sequence_candidate_history(employments, context, resume_as_of=PartialDateInput(year=2026, month=11, precision="month"))
```

## Rules (identical to `trace_model.py`)

1. **Order comes from `sequence_index`, never from dates.**
   - Input order doesn't matter.
   - Dates that contradict the order are reported as `sequence_order_conflict`. They are never used to reorder records.
2. **Every record is kept and classified.** Parallel activity (`primary_career_sequence = false`) stays in `records` but sits outside the primary chain.
3. **The chain is built first; exclusions are applied afterwards.** An excluded record breaks the chain instead of being skipped. So A → unknown → B yields two unconfirmed transitions, never A → B.
4. **Exclusion reasons per record:**
   - `parallel_activity`;
   - `not_full_time_experience`: only `full_time` and `contract` count. Founder, internship and other types count only with an explicit `counts_as_full_time_experience` plus a `classification_note`;
   - `unresolved_role`;
   - `mapping_not_confirmed`: the status is not `confirmed` or `auto_high_confidence`;
   - `low_mapping_confidence`: below `min_mapping_confidence`. If that setting is missing, the threshold is 1.0;
   - `unknown_start_date`.
5. **A transition is `confirmed`** only when neither end has an exclusion reason and `timing_relation` is `sequential` or `same_month_boundary`.
   - Any overlap beyond a shared boundary month is `overlapping`. There is no notice-period tolerance.
   - A year-only end followed by a year-only start in the same year is `boundary_uncertain`, which is never confirmable.
   - An earlier year-only end before a later start is `sequential`, but `gap_is_precise` is false.
6. **Dates are never invented.**
   - A partial date is a month interval `[lo, hi]`: month precision gives one month, year precision gives January to December, and unknown or ongoing give `None`.
   - Gaps and tenures are min/max ranges. Ongoing tenure is capped at `resume_as_of`; without it, the tenure is `None`.
7. **Change type** (v2.1.0). Every transition carries `role_changed`, `employer_changed` (`None` when an employer isn't known), `change_type` and `is_pathway_move`.

   | `change_type` | Role | Employer | Pathway move when confirmed? |
   |---|---|---|---|
   | `role_change` | changed | same | yes |
   | `employer_change` | same | different | yes |
   | `role_and_employer_change` | changed | different | yes |
   | `role_change_employer_unknown` | changed | not known | yes |
   | `no_change` | same | same | no: a continuation |
   | `employer_unknown` | same | not known | no: a move can't be established |

   The candidate's own `employer_id` (the `candidate_employer_id`) is compared only between consecutive records. It is never serialised.
8. **Segments are episode-aware.** A segment is a maximal run of records joined by confirmed transitions.
   - Inside a segment, consecutive records collapse into one stint **only** on a continuation (`no_change` or `employer_unknown`).
   - PM at A → PM at B is two stints (`entry_change_type = employer_change`); PM at A → PM at A is one.
   - Records with exclusion reasons appear in no stint.
   - Segment numbers keep counting across dropped segments, as in SQL.
   - A single episode is a one-stint segment with no transition.

## Candidate review eligibility (candidate-side only, M1)

`CandidateEmployment.review_status` carries the candidate's own review of the record. It is `None` for reference-side and parity inputs, which then follow `trace_model.py` exactly. The candidate profile bridge (`candidate_profile.employment_inputs`) always sets it.

| Review status | In the sequence? | Transition-eligible? |
|---|---|---|
| `rejected` | **No.** The bridge removes it before sequencing (`CandidateEmployment` does not accept `rejected`). The candidate said it is inaccurate, so its neighbours may become adjacent under the usual rules | – |
| `pending_review` | **Yes, as a barrier.** Kept in the primary chain, even if stated as parallel, because its own flags are unreviewed too | No: exclusion reason `review_pending` |
| `user_confirmed` | Yes | Subject to every existing rule, including role mapping and the `min_mapping_confidence` threshold |
| `user_corrected` (legacy rows only) | Yes | As `user_confirmed` |

**Pending is a barrier, not a filter.** Confirmed A → pending B → confirmed C gives A → B and B → C, both unconfirmed, and no A → C. The segments are `[A]` and `[C]`; B is in no stint and contributes no transition or pathway observation. This is rule 3 applied to an unreviewed record. Removing pending records first would have created false adjacency.

Review never relaxes any other rule: a confirmed record with a `needs_review` mapping or low confidence is still excluded.

**Candidate role mapping (M5).** Two candidate-only inputs, `mapping_basis` and `mapping_is_stale` (both `None`/`False` for reference-side and parity inputs, which follow `trace_model.py` exactly):

| Mapping | Eligibility |
|---|---|
| `alias_match` (automatic) | As any mapping: status `auto_high_confidence`, held to `min_mapping_confidence` (0.80) |
| `candidate_selected` (the candidate's decision) | Status `confirmed`, no confidence, so not held to the threshold. It applies to the candidate's own sequence only; it is never reference evidence |
| Stale (`mapping_is_stale`: the title changed after mapping) | Never eligible: exclusion reason `mapping_stale`, a barrier like any excluded record, until a fresh mapping decision |

Mapping never changes review status: a pending record stays a `review_pending` barrier whatever its mapping.

**Integrity after rejection.** `integrity` describes the records that were sequenced. When a rejected record is removed, `has_sequence_gaps` can become true because its `sequence_index` is missing; the stated order is never renumbered.

## Input contract

**`CandidateEmployment`**
- Mirrors `public.trace_candidate_employments`, with the same defaults: `role_id` `ROLE-UNKNOWN`, `mapping_status` `unresolved`, `mapping_confidence` 0, and `unknown` dates and type. A bare extracted record is therefore accepted and kept.
- Dates are given as `start_year`, `start_month`, `start_precision` and `end_*`, as in the table.
- `employment_id` is the `candidate_employment_id`. `employer_id` is the `candidate_employer_id`; `None` or an empty string means unknown.
- Each record is validated against the table's CHECK constraints:
  - precision must match the year/month values, with years between 1950 and 2100;
  - a start date cannot be `ongoing`;
  - `is_current_as_stated` must be true exactly when the end is `ongoing`;
  - the end cannot be before the start;
  - `unresolved` requires `ROLE-UNKNOWN`, and `ROLE-UNKNOWN` requires `unresolved` or `needs_review`;
  - an explicit full-time override needs a note;
  - confidence must be between 0 and 1, and the title cannot be blank.
- A violation raises a pydantic `ValidationError`.

**`SequencingContext`**
- `roles` (role_id → `RoleInfo`), `seniority_ranks` (code → rank or `None`) and `min_mapping_confidence`.
- The caller supplies it, from the lookup tables or `trace_ref.analytics_settings`. The service does no I/O.

**Cross-record checks.** Duplicate `employment_id` or `sequence_index` values, an unknown `role_id` and an unknown `seniority_code` raise `CandidateHistoryError`. All problems are listed in `.problems`. These are the database's unique and foreign-key constraints.

**Errors and ambiguity.** Errors are raised only for data the database itself would reject. Ambiguous or incomplete data (unknown roles, missing dates, overlaps, low confidence) is never an error and never dropped.

**`resume_as_of`** is optional (`PartialDateInput`) and is used only to cap ongoing tenure.

## Output contract (`CandidateHistory`)

| Field | Contents |
|---|---|
| `ruleset`, `min_mapping_confidence_applied` | Which rules and threshold produced the result |
| `records[]` (`ClassifiedEmployment`) | Every input record, in `sequence_index` order. See below |
| `transitions[]` (`Transition`) | One per consecutive pair of primary records. Fields are identical to `trace_model.episode_transitions` (without `profile_id`): every legacy `transitions` field plus `role_changed`, `employer_changed`, `change_type` and `is_pathway_move` |
| `segments[]` (`Segment`) | `segment_no`, `stints[]` (`role_id`, `entry_change_type`, `start_lo/hi`, `employment_ids`), `role_path` and `entry_change_types`. Identical to `trace_model.career_episode_paths` |
| `integrity` | `employment_count`, `max_sequence_index`, `has_sequence_gaps`, `ongoing_primary_count`, `has_order_conflict` |

**What each record contains.** `ClassifiedEmployment` separates **stated** facts from **derived** values.

*Stated:* `stated` echoes the input exactly, except that `employer_id` is excluded from serialisation. It stays available to the service in Python.

*Derived:*
- `start` and `end`: the stated year, month and precision, plus `lo`/`hi` bounds and `YYYY-MM` labels;
- the role's family, PM-target and resolved flags;
- `seniority_code` with `seniority_code_source` (`stated` or `taxonomy_default`), and `seniority_rank`;
- `counts_as_full_time_experience` with `full_time_basis` (`stated_override` or `employment_type_default`);
- `tenure_months_min/max` and `tenure_is_censored`;
- `exclusion_reasons` and `is_transition_eligible`;
- `in_primary_sequence`, `primary_position` and `prev_employment_id`.

**Reason codes.** Transitions prefix each reason with `from:`, `to:` or `timing:`. `REASON_DESCRIPTIONS` and `CHANGE_TYPE_DESCRIPTIONS` give a plain-English meaning for every code.

## Differences from `trace_model.py`

The rules themselves are unchanged. The differences are in input shape, validation and extra output.

| # | Difference | Effect |
|---|---|---|
| 1 | Dates are given as integer year/month columns (the candidate table's shape), not `"YYYY"`/`"YYYY-MM"` strings | Same bounds. `tests/career_history_support.py` adapts foundation records |
| 2 | Row CHECK constraints are enforced on input. `trace_model.py` assumes data the validator or database has already checked, and its parser doesn't check the 1950–2100 year range | Data the database would reject raises an error instead of being sequenced |
| 3 | An unknown `role_id` raises `CandidateHistoryError` (trace_model raises a bare `KeyError`). An unknown `seniority_code` raises an error (trace_model silently gives rank `None`; SQL would reject it through the foreign key) | Errors are clearer; the rules are unaffected |
| 4 | Fields taken from the SQL views rather than trace_model: `primary_position` and `prev_employment_id` (`v_career_sequence`), and `integrity` (`v_profile_sequence_integrity`) | Additional output only |
| 5 | Provenance fields (`seniority_code_source`, `full_time_basis`), date labels and per-record tenure are added. Tenure is a separate function in trace_model | Additional output only |
| 6 | Candidate-only columns (`participant_confirmed`, `entry_source`, `mapping_note`) are passed through in `stated` but **do not affect any rule**, because trace_model has no equivalent. The candidate's `review_status` does (previous section); it is `None` for every reference and parity input, so parity is unaffected | See "Candidate review eligibility" |
| 7 | Not ported: `route_observation`, `route_summary`, `episode_route_observation`, `episode_route_summary`, `profile_in_mode`, `evidence_class_for` and `percentile_cont` | These are reference-cohort and recommendation logic, out of scope here |
| 8 | **Segments follow `trace_model.career_episode_paths` (v2.1.0), not the legacy `career_paths`.** The legacy rule merged consecutive same-role records whatever the employer. Transition classification, timing and confirmation are the legacy rules unchanged (`transitions` ⊂ `episode_transitions`) | A behaviour change from the previous backend version: PM at A → PM at B is now two stints. `trace_model.py` itself was not modified for this task |
| 9 | `employer_id` is excluded from serialised output | Privacy: no employer identifier in user-facing output |

**Parity is tested, not assumed.** `tests/test_career_sequencing_parity.py` loads `trace_model.py` read-only as an oracle. It compares:
- classification, tenure and order;
- every transition field against both the legacy `transitions` and `episode_transitions`;
- episode segments against `career_episode_paths`.

It runs on:
- all 41 synthetic fixtures;
- 2,000 seeded random (mostly messy) histories;
- about 1,600 seeded, mostly chronological histories.

Together these cover all seven timing relations and all six change types, both confirmed and unconfirmed.

**Synthetic fixtures are test inputs only.** Their results are never career evidence.

## Resolved question

*Should `participant_confirmed = false` (or `entry_source`) make a candidate record ineligible?* Resolved in M1 through the v2.2.0 `review_status` instead: pending records are ineligible barriers and rejected records are removed. The legacy `participant_confirmed` and `entry_source` columns still affect no rule, and the bridge no longer derives `participant_confirmed` from review status.
