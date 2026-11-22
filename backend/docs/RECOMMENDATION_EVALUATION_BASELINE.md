# Recommendation Evaluation Baseline (Phase 8.1)

This is a repeatable baseline of the **current** recommendation engine, run on **evaluation-only fixtures**: 95
constructed test careers and 22 constructed users.

- These are not real career histories and not market evidence.
- Nothing here measures recommendation quality.
- The baseline records what the engine does, checks it against its own rules, and prepares the material for human
  relevance review. That review has not happened: every judgment is pending.

Harness version `phase-8.1-v1`. The fixture checksum appears in every report.

---

## 1. Evaluation architecture

```
tests/fixtures/recommendation_eval/          evaluation-only inputs (JSON)
  pool_core.json                             compact pool spec: roles, areas, route groups, explicit profiles
  scenarios.json                             22 constructed users and targets
evaluation/recommendation/                   the harness (never imported by app/; never opens a database)
  repository.py                              EvaluationRepository: in-memory ReferenceEvidenceRepository
  harness.py                                 fixture expansion, engine runs, invariants, consistency tests
  __main__.py                                CLI: baseline.json, baseline_report.md, review templates
  output/                                    the latest baseline (deterministic; regenerate any time)
tests/test_recommendation_evaluation.py      invariants, reproducibility, coverage, isolation
```

**Engine code under test (unchanged by the harness):**

- `reference_evidence.recommendation_evidence`: own-contribution exclusion, zero-move exclusion, route-level
  privacy.
- `candidate_profile.pathway.build_pathway_result`: fit, gaps, ranking, ties, guidance.

The service layer holds two further rules: the self-target guard and the user-confirmed starting point. These are
covered by the service and API tests (`test_phase72_starting_point.py`, `test_phase721.py`). In the harness, a
scenario either has a current role or states why it has none.

**Database layer.** The SQL functions are emulated by `EvaluationRepository` with the foundation's documented
semantics:

- episode stints;
- one observation per profile per route query (first occurrence, lowest segment);
- distinct-profile step experience.

The real SQL objects are checked against these semantics by the existing parity and integration tests
(`test_episode_pathways_db_parity.py`, `test_reference_evidence_integration.py`).

## 2. Fixture structure

`pool_core.json` declares:

- **Roles:** the real taxonomy titles, families and default seniority ranks, copied for the roles used.
- **Areas:** the real responsibility-area labels.
- **Route groups:** `route`, number of `profiles`, optional explicit `ids` (the "own contribution" profiles), and
  `area_counts` on `area_stint`. The first *k* profiles of a group record that area on that stint.
- **One explicit two-segment profile:** `EVAL-DUP-01`, for distinct counting.

`harness.expand_pool` expands this into `EVAL-<group>-NN` profiles. Every profile id must start with `EVAL-`, and the
repository refuses anything else. Groups were designed not to interfere. For example, the areas on the Lead PM
routes sit on stint 2, so they don't add evidence to `PM-PM>PM-SPM`.

| Group | Route | Profiles | Purpose |
|---|---|---|---|
| G1 + DUP | APM → PM | 8 + 1 | Enough responsibility evidence for the existing fit rule (two common areas) |
| G2 (+G7A) | PM → SPM | 6 (+6) | Route shown; each area below the minimum, so reference evidence is incomplete |
| G2B | SPM → Director | 5 | No areas recorded |
| G3 | PM → Founder → Lead PM | 1 | Distinctive single-profile route |
| G4A–F | QA → (…) → APM | 5, 4, 3, 2, 1, 6 | Privacy minimum across 1–6 profiles |
| G5A/B | SWE → Data Scientist / Product Analyst → PM | 5 / 5 | Tie |
| G6A/B/C | BA, Marketing, HR → PM | 5 / 6 / 1 | Includes the requesting user's own contribution (`EVAL-OWN-01/02/03`) |
| G7A/B | PM → SPM → Lead / PM → Program Manager → Lead | 6 / 5 | Two genuinely different routes |
| G8A–D | SPM → PM; Owner → PM; Director → Founder → Head; Consultant → APM → SPM | 5 each | Down, lateral, unknown, mixed |

## 3. Scenario matrix

The 22 scenarios cover:

- the fit tiers;
- both limitation categories;
- privacy counts from 1 to 6 profiles;
- the single-profile route;
- ties, and a tie broken by an earlier role;
- the own-contribution cases (drops below the minimum, remains eligible, only evidence, still counts for others);
- a missing current role;
- different backgrounds with the same target;
- one-step and multi-step routes;
- multiple routes;
- no route;
- upward, lateral, downward, unknown and mixed seniority.

Skills, industries and qualifications vary across users (see §5).

## 4. How to run

```bash
cd backend
.venv/bin/python -m evaluation.recommendation                 # writes evaluation/recommendation/output/
.venv/bin/python -m pytest tests/test_recommendation_evaluation.py
```

The run is deterministic: no timestamps, and two runs give byte-identical files (verified). The exit code is 1 if any
automated invariant fails. The harness reads only the JSON fixtures; it never touches a database, an API, the
synthetic pool, the real pool or Career Insights.

## 5. Baseline outputs (current engine, harness `phase-8.1-v1`)

The full output, with per-scenario discovered routes, explanations and invariant details, is in
`evaluation/recommendation/output/baseline_report.md` and `baseline.json`.

| Scenario | Input (current role → target; areas; own contribution) | Shown routes (rank/group, tier, limitations, profiles) | Suppressed | Notes |
|---|---|---|---|---|
| S01 | PM-APM → PM-PM; roadmap_ownership, outcome_ownership | `PM-APM>PM-PM` (1/1, aligned, 9) | - | - |
| S02 | PM-APM → PM-PM; roadmap_ownership | `PM-APM>PM-PM` (1/1, partial, 9) | - | - |
| S03 | PM-APM → PM-PM; data_analysis | `PM-APM>PM-PM` (1/1, limited, 9) | - | - |
| S04 | PM-APM → PM-PM; none | `PM-APM>PM-PM` (1/1, not_established, incomplete_user_profile, 9) | - | - |
| S05 | PM-PM → PM-SPM; roadmap_ownership | `PM-PM>PM-SPM` (1/1, not_established, insufficient_reference_evidence, 12) | - | - |
| S06 | PM-SPM → PM-DIR; roadmap_ownership | `PM-SPM>PM-DIR` (1/1, not_established, insufficient_reference_evidence, 5) | - | - |
| S07 | PM-PM → PM-LEAD; roadmap_ownership | `PM-PM>PM-SPM>PM-LEAD` (1/1, partial, 6); `PM-PM>ROLE-PGM>PM-LEAD` (2/2, limited, 5) | `PM-PM>ROLE-ENT>PM-LEAD` (1) | - |
| S08 | ROLE-QA → PM-APM; quality_testing | `ROLE-QA>PM-APM` (1/1, not_established, insufficient_reference_evidence, 5); `ROLE-QA>ROLE-DA>PM-APM` (2/2, not_established, insufficient_reference_evidence, 6) | routes with 4, 3, 2 and 1 profiles | - |
| S09 | ROLE-SWE → PM-PM; technical_engineering | `ROLE-SWE>ROLE-DS>PM-PM` (1/1, tied, …, 5); `ROLE-SWE>ROLE-PAN>PM-PM` (2/1, tied, …, 5) | - | tie |
| S10 | ROLE-SWE (also held Product Analyst) → PM-PM | `ROLE-SWE>ROLE-PAN>PM-PM` (1/1); `ROLE-SWE>ROLE-DS>PM-PM` (2/2) | - | earlier role broke the tie |
| S11 | ROLE-BA → PM-PM; own EVAL-OWN-01 | none | `ROLE-BA>PM-PM` (4) | 5 with own, 4 without |
| S12 | ROLE-BA → PM-PM (another user) | `ROLE-BA>PM-PM` (1/1, not_established, insufficient_reference_evidence, 5) | - | contribution counts for others |
| S13 | ROLE-MKT → PM-PM; own EVAL-OWN-02 | `ROLE-MKT>PM-PM` (1/1, not_established, both limitations, 5) | - | 6 with own, still eligible |
| S14 | ROLE-HR → PM-PM; own EVAL-OWN-03 | none | - | own profile was the only evidence |
| S15 | unknown (role_unconfirmed) → PM-PM | none | - | - |
| S16A | PM-APM (engineering background) → PM-PM; roadmap, technical | `PM-APM>PM-PM` (partial, 9) | - | same route as S16B |
| S16B | PM-APM (QA and BA background) → PM-PM; requirements, quality | `PM-APM>PM-PM` (limited, 9) | - | differs only in fit |
| S17 | PM-SPM → PM-PM | `PM-SPM>PM-PM` (not_established, both, 5) | - | direction down |
| S18 | ROLE-OWNER → PM-PM; prioritisation | `ROLE-OWNER>PM-PM` (not_established, 5) | - | direction lateral |
| S19 | PM-DIR → PM-HEAD | `PM-DIR>ROLE-ENT>PM-HEAD` (not_established, both, 5) | - | direction unknown / unknown |
| S20 | ROLE-CONSULT → PM-SPM | `ROLE-CONSULT>PM-APM>PM-SPM` (not_established, both, 5) | - | direction down / up |
| S21 | PM-PM → PM-SDIR | none | - | no route |

**What the baseline shows about the current engine:**

- Routes come from the current role and target only. S16A and S16B (different backgrounds, skills, industries) get
  the same route; only fit differs, and only through categorised areas.
- Earlier roles change nothing except intermediate-step gaps: S10 is the only scenario where they changed the output
  (order and ties).
- Skills, industries and qualifications never change the output: the `irrelevant_fields_do_not_change_output`
  invariant holds in all 22 scenarios.
- The `insufficient_route_evidence` limitation is never reached by a **shown** route. Route-level privacy now
  removes every route below the minimum, which is the same number as the experience-disclosure minimum. The category
  stays in the API for completeness; a route without enough support shows up as the "no reliable pathway" state
  instead.

## 6. Automated invariants

These are checked in every scenario, and all of them pass:

| Invariant | Meaning |
|---|---|
| `privacy_minimum_applied` | Every shown route has at least the privacy minimum of distinct supporting profiles |
| `counts_are_distinct_profiles` | The disclosed count equals the number of distinct supporting profiles (`EVAL-DUP-01` counts once) |
| `no_zero_move_route` | No route starts and ends in the same role |
| `own_contribution_excluded` | The user's own contribution is in no observation |
| `own_exclusion_never_adds_support` | Excluding it never increases any route's support |
| `routes_start_at_current_role` / `routes_end_at_target` | Origin and target are respected; nothing without a current role |
| `ties_consistent` | Same tier and step-gap count ⇔ same rank group; `tied` ⇔ the group has more than one route |
| `fit_follows_existing_rule` | The tier recomputed from disclosed areas and the user's areas matches the engine |
| `deterministic` | The same input gives the same output |
| `irrelevant_fields_do_not_change_output` | Changing skills, industries and qualifications changes nothing (true today, because the engine ignores them) |
| `responsibilities_affect_fit_only` | Adding areas changes fit, never the route set or evidence |
| `current_role_change_moves_origin` | Correcting the current role moves every route's origin |

**Recorded, not judged (baseline sensitivity):** whether adding earlier roles changes the routes, the order or the
ties. Today the route set never changes; the order and ties change in 1 of 22 scenarios.

## 7. Human-review template

The CLI writes two templates, with every judgment `PENDING`:

- `human_review_template.csv`: one row per shown route. Columns:
  - `route_plausible_1to5`
  - `relevant_to_background_1to5`
  - `intermediate_roles_make_sense`
  - `explanation_supported_by_evidence`
  - `alternative_meaningfully_different`
  - `user_should_consider`
  - `reviewer`
  - `notes`
- `pairwise_review_template.csv`: one row per pair of shown routes in a scenario, with `preferred_for_this_user` set
  to A, B, equal or neither, plus a reason.

**Suggested protocol (proposed):**

1. At least two reviewers, at least one with career-domain expertise.
2. Reviewers see the user's inputs and the shown routes, but not the engine's tier labels.
3. Measure agreement before using any label.
4. Store completed labels next to the fixture checksum they refer to.

No label was produced by the engine or by Claude, and none should be treated as ground truth until reviewers
provide it.

## 8. Known limitations

- **Constructed data.** These are constructed test careers. They exercise the rules and edge cases; they say nothing
  about real careers or real recommendation quality.
- **Emulated database layer.** It follows the documented foundation semantics; real SQL parity is tested separately.
- **Service-layer rules not in the harness.** The self-target guard and the user-confirmed starting point are tested
  in the service and API suites instead.
- **Seniority directions are reporting only**, from default role seniority (see the audit follow-up, §19.4); the
  engine doesn't use them.
- **No relevance measure exists yet.** The harness can only record that, for example, S16A and S16B get the same
  route. Whether that's wrong needs human review.

## 9. Separation from normal reference data

| Rule | How it is enforced |
|---|---|
| Never in the 41-profile synthetic pool or the real pool | Fixtures are JSON files read only by the harness; nothing writes them anywhere. `test_fixture_profiles_are_namespaced_and_never_in_the_database` asserts that there are no `EVAL-` profiles in `trace_ref.profiles`, that there are still exactly 41 synthetic profiles, and that there are 0 real ones. |
| Never reachable through user APIs, Career Insights or production | The harness package is not imported by `app/` (asserted). It uses no database connection, settings, HTTP client or API (asserted by a source scan). |
| Never a fallback for real-data queries | The evaluation repository serves only its own mode and refuses any other (asserted). Production source selection is unchanged. |
| Clearly labelled | Every report starts with the fixture's label: "EVALUATION FIXTURE … Not real career histories, not market evidence". |

---

## 10. Phase 8.2 additions (the Phase 8.1 baseline above is unchanged)

- **The Phase 8.1 baseline is unchanged:**
  - `python -m evaluation.recommendation` (without flags) writes the same four files as before, byte for byte
    (verified);
  - the core fixtures are untouched (checksum `2f65654c48fa62ca…`);
  - `tests/test_phase82.py::test_the_phase_8_1_baseline_is_unchanged` asserts this.
- **New fixtures:**
  - `tests/fixtures/recommendation_eval/pool_personalisation.json`: 23 `EVAL-` profiles with earlier roles,
    industries, skills, qualifications and fields of study;
  - `scenarios_personalisation.json`: 13 scenarios R01–R13 covering profiles A and B, single-dimension variants, an
    irrelevant field, missing responsibilities, unreviewed skills, a suppressed route, suppressed dimension evidence,
    and own-contribution exclusion.
- **New flag:** `python -m evaluation.recommendation --shadow` also writes:
  - `shadow.json` and `shadow_report.md`: the production output (unchanged) plus the prototype's per-route relevance
    breakdown;
  - the human-review pack (`review_pack.md`, reviewer A and B forms, adjudication form).
- **Result:**
  - all 12 prototype invariants pass across the 35 scenarios, including `production_output_identical_to_phase_8_1_baseline`;
  - A and B differ on earlier roles, responsibilities, industries, skills and fields of study, and match on
    qualification level;
  - the existing engine already orders A and B differently in the personalisation pool, through responsibility-area
    fit.

  Details: `RECOMMENDATION_PERSONALISATION_PROTOTYPE.md`.
- **Human review:** still pending (`RECOMMENDATION_HUMAN_REVIEW.md`).

## 11. Phase 8.2.1 additions (the Phase 8.1 and 8.2 baselines are unchanged)

**Reproducibility:**

- `output/baseline.json` and `baseline.md` (Phase 8.1) are regenerated byte-identical by
  `python -m evaluation.recommendation`.
- The Phase 8.2 shadow outputs are stored in `output/phase-8.2/` and regenerated byte for byte by `--reproduce-phase-8-2
  DIR`, using the frozen `shadow_phase82.py`.

**New fixtures.** The originals are unchanged.

| File | Purpose |
|---|---|
| `pool_personalisation_v2.json` | the v1 personalisation pool with qualification level, type and field separated (`dimension_set: v2`) |
| `scenarios_personalisation_v2.json` | R01–R13 with v2 qualifications; R07 changes level and type only; new R14 changes field only |
| `pool_intersection.json`, `scenarios_intersection.json` | privacy audit only (I01–I03), 8 profiles on one route; not part of the review pack |

**Shadow run (`phase-8.2.1-v1`).** 39 scenarios: 22 core, 14 personalisation and 3 privacy audit. 0 failing
invariants.

The new invariants:

- `every_no_path_state_is_explained`;
- `combination_risk_detected`;
- `mba_is_a_type_not_a_level`;
- `one_changed_dimension_changes_exactly_that_dimension`, which now covers R07 and R14.

**Review materials.** There are 36 review scenarios. The reviewer forms are split into pathway and scenario files, and
adjudication into `review_adjudication.csv` and `review_adjudication_scenarios.csv`. See
`RECOMMENDATION_HUMAN_REVIEW.md`.

**Tests.** `tests/test_phase821.py` covers this phase. `tests/test_phase82.py` now imports the frozen
`shadow_phase82`.
