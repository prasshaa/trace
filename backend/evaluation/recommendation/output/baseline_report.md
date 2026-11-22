# Recommendation evaluation baseline (phase-8.1-v1)

> EVALUATION FIXTURE: constructed test careers for recommendation-engine evaluation. Not real career histories, not market evidence.

Pool `eval-core-v1`: 95 evaluation profiles; privacy minimum 5; fixture sha256 `2f65654c48fa62ca`.

## Summary

- Scenarios: 22. Coverage tags: contribution_supports_others, different_backgrounds_same_target, earlier_role_sensitivity, exact_minimum, fit_aligned, fit_limited, fit_partial, incomplete_reference_evidence, incomplete_user_responsibilities, insufficient_route_support, missing_current_role, multi_step, multiple_routes, no_route, own_contribution_drops_route, own_contribution_route_remains, own_profile_only_evidence, route_privacy_counts, seniority_down, seniority_lateral, seniority_mixed, seniority_unknown, single_profile_route, sufficient_reference_evidence, tied_routes.
- Automated invariants per scenario: 13 (counts_are_distinct_profiles, current_role_change_moves_origin, deterministic, fit_follows_existing_rule, irrelevant_fields_do_not_change_output, no_zero_move_route, own_contribution_excluded, own_exclusion_never_adds_support, privacy_minimum_applied, responsibilities_affect_fit_only, routes_end_at_target, routes_start_at_current_role, ties_consistent).
- Failing invariants: 0.
- Fit tiers reached: aligned, limited, not_established, partial. Fit limitations reached: incomplete_user_profile, insufficient_reference_evidence.
- Scenarios with suppressed routes: 3; with ties: 1; with no shown route: 4.
- Baseline sensitivity (recorded, not judged): earlier roles changed the order in 1 scenario(s) and the route set in 0.
- Human relevance judgments: none yet (templates generated, all PENDING).

## Scenarios

### S01: Strong fit: both common route areas categorised

- Input: current role `PM-APM`; held ['PM-APM']; areas ['roadmap_ownership', 'outcome_ownership']; skills ['SQL', 'Roadmapping']; industries ['saas']; qualifications ['bachelor']; target `PM-PM`.
- Eligible evaluation population: 95 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): `PM-APM>PM-PM` (9).
- Suppressed (below the privacy minimum): none.

| Rank | Group | Tied | Route | Tier | Reason | Limitations | Profiles | Step gaps | Direction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | no | `PM-APM>PM-PM` | aligned | all_common_areas_recorded | - | 9 | - | up |

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S02: Partial fit: one of two common areas

- Input: current role `PM-APM`; held ['PM-APM']; areas ['roadmap_ownership']; skills ['Roadmapping']; industries ['fintech']; qualifications ['master']; target `PM-PM`.
- Eligible evaluation population: 95 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): `PM-APM>PM-PM` (9).
- Suppressed (below the privacy minimum): none.

| Rank | Group | Tied | Route | Tier | Reason | Limitations | Profiles | Step gaps | Direction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | no | `PM-APM>PM-PM` | partial | some_common_areas_recorded | - | 9 | - | up |

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S03: Limited fit: categorised, but no common area

- Input: current role `PM-APM`; held ['PM-APM']; areas ['data_analysis']; skills ['Python']; industries ['ecommerce_marketplace']; qualifications ['bachelor']; target `PM-PM`.
- Eligible evaluation population: 95 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): `PM-APM>PM-PM` (9).
- Suppressed (below the privacy minimum): none.

| Rank | Group | Tied | Route | Tier | Reason | Limitations | Profiles | Step gaps | Direction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | no | `PM-APM>PM-PM` | limited | no_common_area_recorded | - | 9 | - | up |

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S04: Incomplete user profile: nothing categorised

- Input: current role `PM-APM`; held ['PM-APM']; areas []; skills []; industries []; qualifications []; target `PM-PM`.
- Eligible evaluation population: 95 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): `PM-APM>PM-PM` (9).
- Suppressed (below the privacy minimum): none.

| Rank | Group | Tied | Route | Tier | Reason | Limitations | Profiles | Step gaps | Direction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | no | `PM-APM>PM-PM` | not_established | no_eligible_classified_evidence | incomplete_user_profile | 9 | - | up |

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S05: Incomplete reference evidence: areas withheld on the route

- Input: current role `PM-PM`; held ['PM-APM', 'PM-PM']; areas ['roadmap_ownership']; skills ['Roadmapping']; industries ['saas']; qualifications ['bachelor']; target `PM-SPM`.
- Eligible evaluation population: 95 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): `PM-PM>PM-SPM` (12).
- Suppressed (below the privacy minimum): none.

| Rank | Group | Tied | Route | Tier | Reason | Limitations | Profiles | Step gaps | Direction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | no | `PM-PM>PM-SPM` | not_established | reference_areas_withheld | insufficient_reference_evidence | 12 | - | up |

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S06: Reference profiles record no areas on the route

- Input: current role `PM-SPM`; held ['PM-PM', 'PM-SPM']; areas ['roadmap_ownership']; skills []; industries ['saas']; qualifications ['mba']; target `PM-DIR`.
- Eligible evaluation population: 95 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): `PM-SPM>PM-DIR` (5).
- Suppressed (below the privacy minimum): none.

| Rank | Group | Tied | Route | Tier | Reason | Limitations | Profiles | Step gaps | Direction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | no | `PM-SPM>PM-DIR` | not_established | reference_no_recorded_areas | insufficient_reference_evidence | 5 | - | up |

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S07: Two genuinely different routes; a distinctive single-profile route suppressed

- Input: current role `PM-PM`; held ['PM-PM']; areas ['roadmap_ownership']; skills ['Roadmapping']; industries ['saas']; qualifications ['bachelor']; target `PM-LEAD`.
- Eligible evaluation population: 95 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): `PM-PM>PM-SPM>PM-LEAD` (6), `PM-PM>ROLE-ENT>PM-LEAD` (1), `PM-PM>ROLE-PGM>PM-LEAD` (5).
- Suppressed (below the privacy minimum): `PM-PM>ROLE-ENT>PM-LEAD` (1).

| Rank | Group | Tied | Route | Tier | Reason | Limitations | Profiles | Step gaps | Direction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | no | `PM-PM>PM-SPM>PM-LEAD` | partial | some_common_areas_recorded | - | 6 | PM-SPM | up / up |
| 2 | 2 | no | `PM-PM>ROLE-PGM>PM-LEAD` | limited | no_common_area_recorded | - | 5 | ROLE-PGM | lateral / up |

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S08: Privacy minimum across routes supported by 1, 2, 3, 4, 5 and 6 profiles

- Input: current role `ROLE-QA`; held ['ROLE-QA']; areas ['quality_testing']; skills ['Test automation']; industries ['it_services']; qualifications ['bachelor']; target `PM-APM`.
- Eligible evaluation population: 95 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): `ROLE-QA>PM-APM` (5), `ROLE-QA>ROLE-BA>PM-APM` (4), `ROLE-QA>ROLE-CS>PM-APM` (2), `ROLE-QA>ROLE-DA>PM-APM` (6), `ROLE-QA>ROLE-SUPPORT>PM-APM` (3), `ROLE-QA>ROLE-UX>PM-APM` (1).
- Suppressed (below the privacy minimum): `ROLE-QA>ROLE-BA>PM-APM` (4), `ROLE-QA>ROLE-CS>PM-APM` (2), `ROLE-QA>ROLE-SUPPORT>PM-APM` (3), `ROLE-QA>ROLE-UX>PM-APM` (1).

| Rank | Group | Tied | Route | Tier | Reason | Limitations | Profiles | Step gaps | Direction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | no | `ROLE-QA>PM-APM` | not_established | reference_no_recorded_areas | insufficient_reference_evidence | 5 | - | down |
| 2 | 2 | no | `ROLE-QA>ROLE-DA>PM-APM` | not_established | reference_no_recorded_areas | insufficient_reference_evidence | 6 | ROLE-DA | lateral / down |

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S09: Two equally ranked routes

- Input: current role `ROLE-SWE`; held ['ROLE-SWE']; areas ['technical_engineering']; skills ['Java']; industries ['saas']; qualifications ['bachelor']; target `PM-PM`.
- Eligible evaluation population: 95 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): `ROLE-SWE>ROLE-DS>PM-PM` (5), `ROLE-SWE>ROLE-PAN>PM-PM` (5).
- Suppressed (below the privacy minimum): none.

| Rank | Group | Tied | Route | Tier | Reason | Limitations | Profiles | Step gaps | Direction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | yes | `ROLE-SWE>ROLE-DS>PM-PM` | not_established | reference_no_recorded_areas | insufficient_reference_evidence | 5 | ROLE-DS | lateral / lateral |
| 2 | 1 | yes | `ROLE-SWE>ROLE-PAN>PM-PM` | not_established | reference_no_recorded_areas | insufficient_reference_evidence | 5 | ROLE-PAN | lateral / lateral |

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S10: Same routes, tie separated by an earlier role the user held

- Input: current role `ROLE-SWE`; held ['ROLE-PAN', 'ROLE-SWE']; areas ['technical_engineering']; skills ['Java']; industries ['saas']; qualifications ['bachelor']; target `PM-PM`.
- Eligible evaluation population: 95 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): `ROLE-SWE>ROLE-DS>PM-PM` (5), `ROLE-SWE>ROLE-PAN>PM-PM` (5).
- Suppressed (below the privacy minimum): none.

| Rank | Group | Tied | Route | Tier | Reason | Limitations | Profiles | Step gaps | Direction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | no | `ROLE-SWE>ROLE-PAN>PM-PM` | not_established | reference_no_recorded_areas | insufficient_reference_evidence | 5 | - | lateral / lateral |
| 2 | 2 | no | `ROLE-SWE>ROLE-DS>PM-PM` | not_established | reference_no_recorded_areas | insufficient_reference_evidence | 5 | ROLE-DS | lateral / lateral |

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=True, earlier_roles_changed_ties=True.

### S11: Route meets the minimum only with the user's own contribution

- Input: current role `ROLE-BA`; held ['ROLE-BA']; areas ['requirements_analysis']; skills ['SQL']; industries ['banking_financial_services']; qualifications ['bachelor']; own contribution ['EVAL-OWN-01']; target `PM-PM`.
- Eligible evaluation population: 94 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): `ROLE-BA>PM-PM` (4).
- Suppressed (below the privacy minimum): `ROLE-BA>PM-PM` (4).

No route shown.

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S12: Another user still gets the contributor's profile as independent evidence

- Input: current role `ROLE-BA`; held ['ROLE-BA']; areas ['requirements_analysis']; skills ['SQL']; industries ['banking_financial_services']; qualifications ['bachelor']; target `PM-PM`.
- Eligible evaluation population: 95 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): `ROLE-BA>PM-PM` (5).
- Suppressed (below the privacy minimum): none.

| Rank | Group | Tied | Route | Tier | Reason | Limitations | Profiles | Step gaps | Direction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | no | `ROLE-BA>PM-PM` | not_established | reference_no_recorded_areas | insufficient_reference_evidence | 5 | - | lateral |

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S13: Route remains eligible after excluding the user's own contribution

- Input: current role `ROLE-MKT`; held ['ROLE-MKT']; areas []; skills ['SEO']; industries ['consumer_internet']; qualifications ['bachelor']; own contribution ['EVAL-OWN-02']; target `PM-PM`.
- Eligible evaluation population: 94 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): `ROLE-MKT>PM-PM` (5).
- Suppressed (below the privacy minimum): none.

| Rank | Group | Tied | Route | Tier | Reason | Limitations | Profiles | Step gaps | Direction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | no | `ROLE-MKT>PM-PM` | not_established | reference_no_recorded_areas | insufficient_reference_evidence, incomplete_user_profile | 5 | - | lateral |

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S14: The user's own profile is the only evidence

- Input: current role `ROLE-HR`; held ['ROLE-HR']; areas []; skills []; industries ['hr_tech']; qualifications ['bachelor']; own contribution ['EVAL-OWN-03']; target `PM-PM`.
- Eligible evaluation population: 94 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): none.
- Suppressed (below the privacy minimum): none.

No route shown.

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S15: No established current role

- Input: current role `None` (unknown: role_unconfirmed); held []; areas ['roadmap_ownership']; skills []; industries []; qualifications []; target `PM-PM`.
- Eligible evaluation population: 95 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): none.
- Suppressed (below the privacy minimum): none.

No route shown.

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S16A: Background A (engineering) to Product Manager

- Input: current role `PM-APM`; held ['ROLE-SWE', 'PM-APM']; areas ['roadmap_ownership', 'technical_engineering']; skills ['Java', 'Roadmapping']; industries ['saas']; qualifications ['bachelor']; target `PM-PM`.
- Eligible evaluation population: 95 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): `PM-APM>PM-PM` (9).
- Suppressed (below the privacy minimum): none.

| Rank | Group | Tied | Route | Tier | Reason | Limitations | Profiles | Step gaps | Direction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | no | `PM-APM>PM-PM` | partial | some_common_areas_recorded | - | 9 | - | up |

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S16B: Background B (QA and business analysis) to Product Manager

- Input: current role `PM-APM`; held ['ROLE-QA', 'ROLE-BA', 'PM-APM']; areas ['requirements_analysis', 'quality_testing']; skills ['SQL', 'Test automation']; industries ['enterprise_software', 'it_services']; qualifications ['bachelor']; target `PM-PM`.
- Eligible evaluation population: 95 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): `PM-APM>PM-PM` (9).
- Suppressed (below the privacy minimum): none.

| Rank | Group | Tied | Route | Tier | Reason | Limitations | Profiles | Step gaps | Direction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | no | `PM-APM>PM-PM` | limited | no_common_area_recorded | - | 9 | - | up |

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S17: Downward move by default seniority

- Input: current role `PM-SPM`; held ['PM-SPM']; areas []; skills []; industries []; qualifications []; target `PM-PM`.
- Eligible evaluation population: 95 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): `PM-SPM>PM-PM` (5).
- Suppressed (below the privacy minimum): none.

| Rank | Group | Tied | Route | Tier | Reason | Limitations | Profiles | Step gaps | Direction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | no | `PM-SPM>PM-PM` | not_established | reference_no_recorded_areas | insufficient_reference_evidence, incomplete_user_profile | 5 | - | down |

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S18: Lateral move by default seniority

- Input: current role `ROLE-OWNER`; held ['ROLE-OWNER']; areas ['prioritisation_backlog']; skills []; industries []; qualifications []; target `PM-PM`.
- Eligible evaluation population: 95 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): `ROLE-OWNER>PM-PM` (5).
- Suppressed (below the privacy minimum): none.

| Rank | Group | Tied | Route | Tier | Reason | Limitations | Profiles | Step gaps | Direction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | no | `ROLE-OWNER>PM-PM` | not_established | reference_no_recorded_areas | insufficient_reference_evidence | 5 | - | lateral |

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S19: Route with an unranked step (founder)

- Input: current role `PM-DIR`; held ['PM-DIR']; areas []; skills []; industries []; qualifications []; target `PM-HEAD`.
- Eligible evaluation population: 95 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): `PM-DIR>ROLE-ENT>PM-HEAD` (5).
- Suppressed (below the privacy minimum): none.

| Rank | Group | Tied | Route | Tier | Reason | Limitations | Profiles | Step gaps | Direction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | no | `PM-DIR>ROLE-ENT>PM-HEAD` | not_established | reference_no_recorded_areas | insufficient_reference_evidence, incomplete_user_profile | 5 | ROLE-ENT | unknown / unknown |

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S20: Mixed down then up

- Input: current role `ROLE-CONSULT`; held ['ROLE-CONSULT']; areas []; skills []; industries ['consulting']; qualifications ['mba']; target `PM-SPM`.
- Eligible evaluation population: 95 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): `ROLE-CONSULT>PM-APM>PM-SPM` (5).
- Suppressed (below the privacy minimum): none.

| Rank | Group | Tied | Route | Tier | Reason | Limitations | Profiles | Step gaps | Direction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | no | `ROLE-CONSULT>PM-APM>PM-SPM` | not_established | reference_no_recorded_areas | insufficient_reference_evidence, incomplete_user_profile | 5 | PM-APM | down / up |

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

### S21: No route to the target

- Input: current role `PM-PM`; held ['PM-PM']; areas ['roadmap_ownership']; skills []; industries []; qualifications []; target `PM-SDIR`.
- Eligible evaluation population: 95 profiles.
- Discovered routes (after own-contribution and zero-move exclusion): none.
- Suppressed (below the privacy minimum): none.

No route shown.

- Invariants: all pass. Sensitivity: earlier_roles_changed_routes=False, earlier_roles_changed_order=False, earlier_roles_changed_ties=False.

