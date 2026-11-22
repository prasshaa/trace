# Whole-profile relevance prototype: shadow evaluation (phase-8.2.1-v1)

> Evaluation fixtures only. Constructed careers, not real people and not market evidence. The prototype runs beside the production engine; it does not change production ranking, labels or output.

## Invariants

| Invariant | Result |
|---|---|
| disclosed_values_meet_privacy_minimum | pass |
| only_shown_routes_get_a_breakdown | pass |
| population_equals_distinct_route_support | pass |
| own_contribution_never_in_population | pass |
| compared_only_when_the_user_has_data | pass |
| every_no_path_state_is_explained | pass |
| one_changed_dimension_changes_exactly_that_dimension | pass |
| unreviewed_skills_are_ignored | pass |
| missing_responsibilities_are_not_compared | pass |
| suppressed_routes_get_no_breakdown | pass |
| own_exclusion_changes_the_population_exactly | pass |
| combination_risk_detected | pass |
| mba_is_a_type_not_a_level | pass |
| deterministic | pass |
| production_output_identical_to_phase_8_1_baseline | pass |

## Profile comparisons

- **R01 vs R02** (Profile A vs Profile B (different backgrounds, same roles and target)): production identical: no; relevance differs in `PM-PM>PM-SPM`: previous_roles, responsibilities, industries, skills, qualification_types, fields_of_study; `PM-PM>ROLE-PGM>PM-SPM`: previous_roles, responsibilities, industries, skills, qualification_types, fields_of_study
- **S16A vs S16B** (Phase 8.1 core: backgrounds A and B (core pool has no supporting attributes)): production identical: no; relevance differs in `PM-APM>PM-PM`: responsibilities
- **R01 vs R13** (Profile A with and without its own contribution in the population): production identical: no; relevance differs in `PM-PM>PM-SPM`: previous_roles, responsibilities, industries, skills, qualification_levels, qualification_types, fields_of_study, population; `PM-PM>ROLE-PGM>PM-SPM`: nothing
- **R01 vs R07** (Profile A with a different qualification (level and type)): production identical: yes; relevance differs in `PM-PM>PM-SPM`: qualification_levels, qualification_types; `PM-PM>ROLE-PGM>PM-SPM`: qualification_levels, qualification_types
- **R01 vs R14** (Profile A with a different field of study): production identical: yes; relevance differs in `PM-PM>PM-SPM`: fields_of_study; `PM-PM>ROLE-PGM>PM-SPM`: fields_of_study

## Privacy combination audit (evaluation only; not a disclosure rule)

Combinations of individually disclosed values (each shared by at least the minimum) that fewer than the minimum supporters share. Exact small counts are never printed.

- privacy_audit I01 `PM-PM>PM-SPM`: fields_of_study=computer science + industries=saas + skills=java (fewer than 5)
- privacy_audit I02 `PM-PM>PM-SPM`: fields_of_study=computer science + industries=saas (fewer than 5)
- privacy_audit I02 `PM-PM>PM-SPM`: fields_of_study=computer science + skills=java (fewer than 5)
- privacy_audit I02 `PM-PM>PM-SPM`: industries=saas + skills=java (fewer than 5)
- privacy_audit I02 `PM-PM>PM-SPM`: fields_of_study=computer science + industries=saas + skills=java (fewer than 5)
- privacy_audit I03 `PM-PM>PM-SPM`: fields_of_study=computer science + industries=saas + skills=java (fewer than 5)

## Scenario set: core (`eval-core-v1`)

> EVALUATION FIXTURE: constructed test careers for recommendation-engine evaluation. Not real career histories, not market evidence.

### S01: Strong fit: both common route areas categorised

- `PM-APM>PM-PM`: production rank 1 (group 1), fit aligned; 9 supporting profiles
    - previous roles: not recorded in this profile; not compared
    - responsibilities: matched roadmap_ownership (9 of 9), outcome_ownership (6 of 9); common among supporters but not recorded here: none
    - industries: not enough shared detail among supporting profiles to compare
    - skills: not enough shared detail among supporting profiles to compare
    - qualifications: not enough shared detail among supporting profiles to compare
    - fields of study: not recorded in this profile; not compared
    - seniority: more_senior

### S02: Partial fit: one of two common areas

- `PM-APM>PM-PM`: production rank 1 (group 1), fit partial; 9 supporting profiles
    - previous roles: not recorded in this profile; not compared
    - responsibilities: matched roadmap_ownership (9 of 9); common among supporters but not recorded here: outcome_ownership (6)
    - industries: not enough shared detail among supporting profiles to compare
    - skills: not enough shared detail among supporting profiles to compare
    - qualifications: not enough shared detail among supporting profiles to compare
    - fields of study: not recorded in this profile; not compared
    - seniority: more_senior

### S03: Limited fit: categorised, but no common area

- `PM-APM>PM-PM`: production rank 1 (group 1), fit limited; 9 supporting profiles
    - previous roles: not recorded in this profile; not compared
    - responsibilities: matched none; common among supporters but not recorded here: roadmap_ownership (9), outcome_ownership (6)
    - industries: not enough shared detail among supporting profiles to compare
    - skills: not enough shared detail among supporting profiles to compare
    - qualifications: not enough shared detail among supporting profiles to compare
    - fields of study: not recorded in this profile; not compared
    - seniority: more_senior

### S04: Incomplete user profile: nothing categorised

- `PM-APM>PM-PM`: production rank 1 (group 1), fit not_established; 9 supporting profiles
    - previous roles: not recorded in this profile; not compared
    - responsibilities: not recorded in this profile; not compared (common among supporters: roadmap_ownership (9), outcome_ownership (6))
    - industries: not recorded in this profile; not compared
    - skills: not recorded in this profile; not compared
    - qualifications: not recorded in this profile; not compared
    - fields of study: not recorded in this profile; not compared
    - seniority: more_senior

### S05: Incomplete reference evidence: areas withheld on the route

- `PM-PM>PM-SPM`: production rank 1 (group 1), fit not_established; 12 supporting profiles
    - previous roles: not enough shared detail among supporting profiles to compare
    - responsibilities: not enough shared detail among supporting profiles to compare
    - industries: not enough shared detail among supporting profiles to compare
    - skills: not enough shared detail among supporting profiles to compare
    - qualifications: not enough shared detail among supporting profiles to compare
    - fields of study: not recorded in this profile; not compared
    - seniority: more_senior

### S06: Reference profiles record no areas on the route

- `PM-SPM>PM-DIR`: production rank 1 (group 1), fit not_established; 5 supporting profiles
    - previous roles: not enough shared detail among supporting profiles to compare
    - responsibilities: not enough shared detail among supporting profiles to compare
    - industries: not enough shared detail among supporting profiles to compare
    - skills: not recorded in this profile; not compared
    - qualifications: not enough shared detail among supporting profiles to compare
    - fields of study: not recorded in this profile; not compared
    - seniority: more_senior

### S07: Two genuinely different routes; a distinctive single-profile route suppressed

- `PM-PM>PM-SPM>PM-LEAD`: production rank 1 (group 1), fit partial; 6 supporting profiles
    - previous roles: not recorded in this profile; not compared
    - responsibilities: matched roadmap_ownership (6 of 6); common among supporters but not recorded here: outcome_ownership (6)
    - industries: not enough shared detail among supporting profiles to compare
    - skills: not enough shared detail among supporting profiles to compare
    - qualifications: not enough shared detail among supporting profiles to compare
    - fields of study: not recorded in this profile; not compared
    - seniority: more_senior, more_senior
- `PM-PM>ROLE-PGM>PM-LEAD`: production rank 2 (group 2), fit limited; 5 supporting profiles
    - previous roles: not recorded in this profile; not compared
    - responsibilities: matched none; common among supporters but not recorded here: delivery_execution (5), stakeholder_management (5)
    - industries: not enough shared detail among supporting profiles to compare
    - skills: not enough shared detail among supporting profiles to compare
    - qualifications: not enough shared detail among supporting profiles to compare
    - fields of study: not recorded in this profile; not compared
    - seniority: career_track_change, career_track_change

### S08: Privacy minimum across routes supported by 1, 2, 3, 4, 5 and 6 profiles

- `ROLE-QA>PM-APM`: production rank 1 (group 1), fit not_established; 5 supporting profiles
    - previous roles: not recorded in this profile; not compared
    - responsibilities: not enough shared detail among supporting profiles to compare
    - industries: not enough shared detail among supporting profiles to compare
    - skills: not enough shared detail among supporting profiles to compare
    - qualifications: not enough shared detail among supporting profiles to compare
    - fields of study: not recorded in this profile; not compared
    - seniority: career_track_change
- `ROLE-QA>ROLE-DA>PM-APM`: production rank 2 (group 2), fit not_established; 6 supporting profiles
    - previous roles: not recorded in this profile; not compared
    - responsibilities: not enough shared detail among supporting profiles to compare
    - industries: not enough shared detail among supporting profiles to compare
    - skills: not enough shared detail among supporting profiles to compare
    - qualifications: not enough shared detail among supporting profiles to compare
    - fields of study: not recorded in this profile; not compared
    - seniority: career_track_change, career_track_change

### S09: Two equally ranked routes

- `ROLE-SWE>ROLE-DS>PM-PM`: production rank 1 (group 1, tied), fit not_established; 5 supporting profiles
    - previous roles: not recorded in this profile; not compared
    - responsibilities: not enough shared detail among supporting profiles to compare
    - industries: not enough shared detail among supporting profiles to compare
    - skills: not enough shared detail among supporting profiles to compare
    - qualifications: not enough shared detail among supporting profiles to compare
    - fields of study: not recorded in this profile; not compared
    - seniority: career_track_change, career_track_change
- `ROLE-SWE>ROLE-PAN>PM-PM`: production rank 2 (group 1, tied), fit not_established; 5 supporting profiles
    - previous roles: not recorded in this profile; not compared
    - responsibilities: not enough shared detail among supporting profiles to compare
    - industries: not enough shared detail among supporting profiles to compare
    - skills: not enough shared detail among supporting profiles to compare
    - qualifications: not enough shared detail among supporting profiles to compare
    - fields of study: not recorded in this profile; not compared
    - seniority: career_track_change, career_track_change

### S10: Same routes, tie separated by an earlier role the user held

- `ROLE-SWE>ROLE-PAN>PM-PM`: production rank 1 (group 1), fit not_established; 5 supporting profiles
    - previous roles: not enough shared detail among supporting profiles to compare
    - responsibilities: not enough shared detail among supporting profiles to compare
    - industries: not enough shared detail among supporting profiles to compare
    - skills: not enough shared detail among supporting profiles to compare
    - qualifications: not enough shared detail among supporting profiles to compare
    - fields of study: not recorded in this profile; not compared
    - seniority: career_track_change, career_track_change
- `ROLE-SWE>ROLE-DS>PM-PM`: production rank 2 (group 2), fit not_established; 5 supporting profiles
    - previous roles: not enough shared detail among supporting profiles to compare
    - responsibilities: not enough shared detail among supporting profiles to compare
    - industries: not enough shared detail among supporting profiles to compare
    - skills: not enough shared detail among supporting profiles to compare
    - qualifications: not enough shared detail among supporting profiles to compare
    - fields of study: not recorded in this profile; not compared
    - seniority: career_track_change, career_track_change

### S11: Route meets the minimum only with the user's own contribution

No route shown (insufficient_independent_evidence): There is not enough independent evidence once this person's own contributed profile is excluded. A person's own career history is never used as evidence for their own pathways.

### S12: Another user still gets the contributor's profile as independent evidence

- `ROLE-BA>PM-PM`: production rank 1 (group 1), fit not_established; 5 supporting profiles
    - previous roles: not recorded in this profile; not compared
    - responsibilities: not enough shared detail among supporting profiles to compare
    - industries: not enough shared detail among supporting profiles to compare
    - skills: not enough shared detail among supporting profiles to compare
    - qualifications: not enough shared detail among supporting profiles to compare
    - fields of study: not recorded in this profile; not compared
    - seniority: career_track_change

### S13: Route remains eligible after excluding the user's own contribution

- `ROLE-MKT>PM-PM`: production rank 1 (group 1), fit not_established; 5 supporting profiles
    - previous roles: not recorded in this profile; not compared
    - responsibilities: not recorded in this profile; not compared
    - industries: not enough shared detail among supporting profiles to compare
    - skills: not enough shared detail among supporting profiles to compare
    - qualifications: not enough shared detail among supporting profiles to compare
    - fields of study: not recorded in this profile; not compared
    - seniority: career_track_change

### S14: The user's own profile is the only evidence

No route shown (no_independent_evidence): There is no independent reference evidence for this move. A person's own career history is never used as evidence for their own pathways.

### S15: No established current role

No route shown (starting_point_unconfirmed): This person's current role is not confirmed, so a starting point cannot be established and no pathway can be generated.

### S16A: Background A (engineering) to Product Manager

- `PM-APM>PM-PM`: production rank 1 (group 1), fit partial; 9 supporting profiles
    - previous roles: not enough shared detail among supporting profiles to compare
    - responsibilities: matched roadmap_ownership (9 of 9); common among supporters but not recorded here: outcome_ownership (6)
    - industries: not enough shared detail among supporting profiles to compare
    - skills: not enough shared detail among supporting profiles to compare
    - qualifications: not enough shared detail among supporting profiles to compare
    - fields of study: not recorded in this profile; not compared
    - seniority: more_senior

### S16B: Background B (QA and business analysis) to Product Manager

- `PM-APM>PM-PM`: production rank 1 (group 1), fit limited; 9 supporting profiles
    - previous roles: not enough shared detail among supporting profiles to compare
    - responsibilities: matched none; common among supporters but not recorded here: roadmap_ownership (9), outcome_ownership (6)
    - industries: not enough shared detail among supporting profiles to compare
    - skills: not enough shared detail among supporting profiles to compare
    - qualifications: not enough shared detail among supporting profiles to compare
    - fields of study: not recorded in this profile; not compared
    - seniority: more_senior

### S17: Downward move by default seniority

- `PM-SPM>PM-PM`: production rank 1 (group 1), fit not_established; 5 supporting profiles
    - previous roles: not recorded in this profile; not compared
    - responsibilities: not recorded in this profile; not compared
    - industries: not recorded in this profile; not compared
    - skills: not recorded in this profile; not compared
    - qualifications: not recorded in this profile; not compared
    - fields of study: not recorded in this profile; not compared
    - seniority: more_junior

### S18: Lateral move by default seniority

- `ROLE-OWNER>PM-PM`: production rank 1 (group 1), fit not_established; 5 supporting profiles
    - previous roles: not recorded in this profile; not compared
    - responsibilities: not enough shared detail among supporting profiles to compare
    - industries: not recorded in this profile; not compared
    - skills: not recorded in this profile; not compared
    - qualifications: not recorded in this profile; not compared
    - fields of study: not recorded in this profile; not compared
    - seniority: career_track_change

### S19: Route with an unranked step (founder)

- `PM-DIR>ROLE-ENT>PM-HEAD`: production rank 1 (group 1), fit not_established; 5 supporting profiles
    - previous roles: not recorded in this profile; not compared
    - responsibilities: not recorded in this profile; not compared
    - industries: not recorded in this profile; not compared
    - skills: not recorded in this profile; not compared
    - qualifications: not recorded in this profile; not compared
    - fields of study: not recorded in this profile; not compared
    - seniority: career_track_change, career_track_change

### S20: Mixed down then up

- `ROLE-CONSULT>PM-APM>PM-SPM`: production rank 1 (group 1), fit not_established; 5 supporting profiles
    - previous roles: not recorded in this profile; not compared
    - responsibilities: not recorded in this profile; not compared
    - industries: not enough shared detail among supporting profiles to compare
    - skills: not recorded in this profile; not compared
    - qualifications: not enough shared detail among supporting profiles to compare
    - fields of study: not recorded in this profile; not compared
    - seniority: career_track_change, more_senior

### S21: No route to the target

No route shown (no_observed_route): No observed route was found in this evaluation sample. This does not establish that the career move is impossible.


## Scenario set: personalisation (`eval-personalisation-v2`)

> EVALUATION FIXTURE: constructed test careers for the whole-profile relevance prototype (Phase 8.2.1: qualification level, qualification type and field of study kept separate). Not real career histories, not market evidence.

### R01: Profile A: engineering background to Senior PM

- `PM-PM>PM-SPM`: production rank 1 (group 1), fit aligned; 9 supporting profiles
    - previous roles: matched PM-APM (9 of 9), ROLE-SWE (5 of 9); common among supporters but not recorded here: none
    - responsibilities: matched roadmap_ownership (9 of 9), technical_engineering (5 of 9); common among supporters but not recorded here: none
    - industries: matched saas (5 of 9); common among supporters but not recorded here: none
    - skills: matched java (5 of 9), sql (5 of 9); common among supporters but not recorded here: roadmapping (9)
    - qualification levels: matched bachelor (5 of 9); common among supporters but not recorded here: none
    - qualification types: matched btech (5 of 9); common among supporters but not recorded here: none
    - fields of study: matched computer science (5 of 9); common among supporters but not recorded here: none
    - seniority: more_senior
- `PM-PM>ROLE-PGM>PM-SPM`: production rank 2 (group 2), fit limited; 6 supporting profiles
    - previous roles: matched none; common among supporters but not recorded here: ROLE-BA (6), ROLE-QA (6)
    - responsibilities: matched none; common among supporters but not recorded here: delivery_execution (6), requirements_analysis (6), stakeholder_management (6)
    - industries: matched none; common among supporters but not recorded here: it_services (6)
    - skills: matched sql (6 of 6); common among supporters but not recorded here: requirements (6), test automation (6)
    - qualification levels: matched bachelor (6 of 6); common among supporters but not recorded here: none
    - qualification types: matched none; common among supporters but not recorded here: bsc (6)
    - fields of study: matched none; common among supporters but not recorded here: information systems (6)
    - seniority: career_track_change, career_track_change

### R02: Profile B: QA and business-analysis background to Senior PM

- `PM-PM>ROLE-PGM>PM-SPM`: production rank 1 (group 1), fit partial; 6 supporting profiles
    - previous roles: matched ROLE-BA (6 of 6), ROLE-QA (6 of 6); common among supporters but not recorded here: none
    - responsibilities: matched requirements_analysis (6 of 6); common among supporters but not recorded here: delivery_execution (6), stakeholder_management (6)
    - industries: matched it_services (6 of 6); common among supporters but not recorded here: none
    - skills: matched sql (6 of 6), test automation (6 of 6); common among supporters but not recorded here: requirements (6)
    - qualification levels: matched bachelor (6 of 6); common among supporters but not recorded here: none
    - qualification types: matched bsc (6 of 6); common among supporters but not recorded here: none
    - fields of study: matched information systems (6 of 6); common among supporters but not recorded here: none
    - seniority: career_track_change, career_track_change
- `PM-PM>PM-SPM`: production rank 2 (group 2), fit limited; 9 supporting profiles
    - previous roles: matched none; common among supporters but not recorded here: PM-APM (9), ROLE-SWE (5)
    - responsibilities: matched none; common among supporters but not recorded here: roadmap_ownership (9), technical_engineering (5)
    - industries: matched none; common among supporters but not recorded here: saas (5)
    - skills: matched sql (5 of 9); common among supporters but not recorded here: roadmapping (9), java (5)
    - qualification levels: matched bachelor (5 of 9); common among supporters but not recorded here: none
    - qualification types: matched none; common among supporters but not recorded here: btech (5)
    - fields of study: matched none; common among supporters but not recorded here: computer science (5)
    - seniority: more_senior

### R03: A with different earlier roles

- `PM-PM>PM-SPM`: production rank 1 (group 1), fit aligned; 9 supporting profiles
    - previous roles: matched none; common among supporters but not recorded here: PM-APM (9), ROLE-SWE (5)
    - responsibilities: matched roadmap_ownership (9 of 9), technical_engineering (5 of 9); common among supporters but not recorded here: none
    - industries: matched saas (5 of 9); common among supporters but not recorded here: none
    - skills: matched java (5 of 9), sql (5 of 9); common among supporters but not recorded here: roadmapping (9)
    - qualification levels: matched bachelor (5 of 9); common among supporters but not recorded here: none
    - qualification types: matched btech (5 of 9); common among supporters but not recorded here: none
    - fields of study: matched computer science (5 of 9); common among supporters but not recorded here: none
    - seniority: more_senior
- `PM-PM>ROLE-PGM>PM-SPM`: production rank 2 (group 2), fit limited; 6 supporting profiles
    - previous roles: matched none; common among supporters but not recorded here: ROLE-BA (6), ROLE-QA (6)
    - responsibilities: matched none; common among supporters but not recorded here: delivery_execution (6), requirements_analysis (6), stakeholder_management (6)
    - industries: matched none; common among supporters but not recorded here: it_services (6)
    - skills: matched sql (6 of 6); common among supporters but not recorded here: requirements (6), test automation (6)
    - qualification levels: matched bachelor (6 of 6); common among supporters but not recorded here: none
    - qualification types: matched none; common among supporters but not recorded here: bsc (6)
    - fields of study: matched none; common among supporters but not recorded here: information systems (6)
    - seniority: career_track_change, career_track_change

### R04: A with different responsibilities

- `PM-PM>ROLE-PGM>PM-SPM`: production rank 1 (group 1), fit partial; 6 supporting profiles
    - previous roles: matched none; common among supporters but not recorded here: ROLE-BA (6), ROLE-QA (6)
    - responsibilities: matched stakeholder_management (6 of 6); common among supporters but not recorded here: delivery_execution (6), requirements_analysis (6)
    - industries: matched none; common among supporters but not recorded here: it_services (6)
    - skills: matched sql (6 of 6); common among supporters but not recorded here: requirements (6), test automation (6)
    - qualification levels: matched bachelor (6 of 6); common among supporters but not recorded here: none
    - qualification types: matched none; common among supporters but not recorded here: bsc (6)
    - fields of study: matched none; common among supporters but not recorded here: information systems (6)
    - seniority: career_track_change, career_track_change
- `PM-PM>PM-SPM`: production rank 2 (group 2), fit limited; 9 supporting profiles
    - previous roles: matched PM-APM (9 of 9), ROLE-SWE (5 of 9); common among supporters but not recorded here: none
    - responsibilities: matched none; common among supporters but not recorded here: roadmap_ownership (9), technical_engineering (5)
    - industries: matched saas (5 of 9); common among supporters but not recorded here: none
    - skills: matched java (5 of 9), sql (5 of 9); common among supporters but not recorded here: roadmapping (9)
    - qualification levels: matched bachelor (5 of 9); common among supporters but not recorded here: none
    - qualification types: matched btech (5 of 9); common among supporters but not recorded here: none
    - fields of study: matched computer science (5 of 9); common among supporters but not recorded here: none
    - seniority: more_senior

### R05: A with a different industry background

- `PM-PM>PM-SPM`: production rank 1 (group 1), fit aligned; 9 supporting profiles
    - previous roles: matched PM-APM (9 of 9), ROLE-SWE (5 of 9); common among supporters but not recorded here: none
    - responsibilities: matched roadmap_ownership (9 of 9), technical_engineering (5 of 9); common among supporters but not recorded here: none
    - industries: matched none; common among supporters but not recorded here: saas (5)
    - skills: matched java (5 of 9), sql (5 of 9); common among supporters but not recorded here: roadmapping (9)
    - qualification levels: matched bachelor (5 of 9); common among supporters but not recorded here: none
    - qualification types: matched btech (5 of 9); common among supporters but not recorded here: none
    - fields of study: matched computer science (5 of 9); common among supporters but not recorded here: none
    - seniority: more_senior
- `PM-PM>ROLE-PGM>PM-SPM`: production rank 2 (group 2), fit limited; 6 supporting profiles
    - previous roles: matched none; common among supporters but not recorded here: ROLE-BA (6), ROLE-QA (6)
    - responsibilities: matched none; common among supporters but not recorded here: delivery_execution (6), requirements_analysis (6), stakeholder_management (6)
    - industries: matched none; common among supporters but not recorded here: it_services (6)
    - skills: matched sql (6 of 6); common among supporters but not recorded here: requirements (6), test automation (6)
    - qualification levels: matched bachelor (6 of 6); common among supporters but not recorded here: none
    - qualification types: matched none; common among supporters but not recorded here: bsc (6)
    - fields of study: matched none; common among supporters but not recorded here: information systems (6)
    - seniority: career_track_change, career_track_change

### R06: A with different skills

- `PM-PM>PM-SPM`: production rank 1 (group 1), fit aligned; 9 supporting profiles
    - previous roles: matched PM-APM (9 of 9), ROLE-SWE (5 of 9); common among supporters but not recorded here: none
    - responsibilities: matched roadmap_ownership (9 of 9), technical_engineering (5 of 9); common among supporters but not recorded here: none
    - industries: matched saas (5 of 9); common among supporters but not recorded here: none
    - skills: matched none; common among supporters but not recorded here: roadmapping (9), java (5), sql (5)
    - qualification levels: matched bachelor (5 of 9); common among supporters but not recorded here: none
    - qualification types: matched btech (5 of 9); common among supporters but not recorded here: none
    - fields of study: matched computer science (5 of 9); common among supporters but not recorded here: none
    - seniority: more_senior
- `PM-PM>ROLE-PGM>PM-SPM`: production rank 2 (group 2), fit limited; 6 supporting profiles
    - previous roles: matched none; common among supporters but not recorded here: ROLE-BA (6), ROLE-QA (6)
    - responsibilities: matched none; common among supporters but not recorded here: delivery_execution (6), requirements_analysis (6), stakeholder_management (6)
    - industries: matched none; common among supporters but not recorded here: it_services (6)
    - skills: matched none; common among supporters but not recorded here: requirements (6), sql (6), test automation (6)
    - qualification levels: matched bachelor (6 of 6); common among supporters but not recorded here: none
    - qualification types: matched none; common among supporters but not recorded here: bsc (6)
    - fields of study: matched none; common among supporters but not recorded here: information systems (6)
    - seniority: career_track_change, career_track_change

### R07: A with a different qualification (credential only; field of study unchanged)

- `PM-PM>PM-SPM`: production rank 1 (group 1), fit aligned; 9 supporting profiles
    - previous roles: matched PM-APM (9 of 9), ROLE-SWE (5 of 9); common among supporters but not recorded here: none
    - responsibilities: matched roadmap_ownership (9 of 9), technical_engineering (5 of 9); common among supporters but not recorded here: none
    - industries: matched saas (5 of 9); common among supporters but not recorded here: none
    - skills: matched java (5 of 9), sql (5 of 9); common among supporters but not recorded here: roadmapping (9)
    - qualification levels: matched none; common among supporters but not recorded here: bachelor (5)
    - qualification types: matched none; common among supporters but not recorded here: btech (5)
    - fields of study: matched computer science (5 of 9); common among supporters but not recorded here: none
    - seniority: more_senior
- `PM-PM>ROLE-PGM>PM-SPM`: production rank 2 (group 2), fit limited; 6 supporting profiles
    - previous roles: matched none; common among supporters but not recorded here: ROLE-BA (6), ROLE-QA (6)
    - responsibilities: matched none; common among supporters but not recorded here: delivery_execution (6), requirements_analysis (6), stakeholder_management (6)
    - industries: matched none; common among supporters but not recorded here: it_services (6)
    - skills: matched sql (6 of 6); common among supporters but not recorded here: requirements (6), test automation (6)
    - qualification levels: matched none; common among supporters but not recorded here: bachelor (6)
    - qualification types: matched none; common among supporters but not recorded here: bsc (6)
    - fields of study: matched none; common among supporters but not recorded here: information systems (6)
    - seniority: career_track_change, career_track_change

### R08: A with an irrelevant field changed

- `PM-PM>PM-SPM`: production rank 1 (group 1), fit aligned; 9 supporting profiles
    - previous roles: matched PM-APM (9 of 9), ROLE-SWE (5 of 9); common among supporters but not recorded here: none
    - responsibilities: matched roadmap_ownership (9 of 9), technical_engineering (5 of 9); common among supporters but not recorded here: none
    - industries: matched saas (5 of 9); common among supporters but not recorded here: none
    - skills: matched java (5 of 9), sql (5 of 9); common among supporters but not recorded here: roadmapping (9)
    - qualification levels: matched bachelor (5 of 9); common among supporters but not recorded here: none
    - qualification types: matched btech (5 of 9); common among supporters but not recorded here: none
    - fields of study: matched computer science (5 of 9); common among supporters but not recorded here: none
    - seniority: more_senior
- `PM-PM>ROLE-PGM>PM-SPM`: production rank 2 (group 2), fit limited; 6 supporting profiles
    - previous roles: matched none; common among supporters but not recorded here: ROLE-BA (6), ROLE-QA (6)
    - responsibilities: matched none; common among supporters but not recorded here: delivery_execution (6), requirements_analysis (6), stakeholder_management (6)
    - industries: matched none; common among supporters but not recorded here: it_services (6)
    - skills: matched sql (6 of 6); common among supporters but not recorded here: requirements (6), test automation (6)
    - qualification levels: matched bachelor (6 of 6); common among supporters but not recorded here: none
    - qualification types: matched none; common among supporters but not recorded here: bsc (6)
    - fields of study: matched none; common among supporters but not recorded here: information systems (6)
    - seniority: career_track_change, career_track_change

### R09: A without categorised responsibilities

- `PM-PM>PM-SPM`: production rank 1 (group 1), fit not_established; 9 supporting profiles
    - previous roles: matched PM-APM (9 of 9), ROLE-SWE (5 of 9); common among supporters but not recorded here: none
    - responsibilities: not recorded in this profile; not compared (common among supporters: roadmap_ownership (9), technical_engineering (5))
    - industries: matched saas (5 of 9); common among supporters but not recorded here: none
    - skills: matched java (5 of 9), sql (5 of 9); common among supporters but not recorded here: roadmapping (9)
    - qualification levels: matched bachelor (5 of 9); common among supporters but not recorded here: none
    - qualification types: matched btech (5 of 9); common among supporters but not recorded here: none
    - fields of study: matched computer science (5 of 9); common among supporters but not recorded here: none
    - seniority: more_senior
- `PM-PM>ROLE-PGM>PM-SPM`: production rank 2 (group 2), fit not_established; 6 supporting profiles
    - previous roles: matched none; common among supporters but not recorded here: ROLE-BA (6), ROLE-QA (6)
    - responsibilities: not recorded in this profile; not compared (common among supporters: delivery_execution (6), requirements_analysis (6), stakeholder_management (6))
    - industries: matched none; common among supporters but not recorded here: it_services (6)
    - skills: matched sql (6 of 6); common among supporters but not recorded here: requirements (6), test automation (6)
    - qualification levels: matched bachelor (6 of 6); common among supporters but not recorded here: none
    - qualification types: matched none; common among supporters but not recorded here: bsc (6)
    - fields of study: matched none; common among supporters but not recorded here: information systems (6)
    - seniority: career_track_change, career_track_change

### R10: A whose skills are not reviewed yet

- `PM-PM>PM-SPM`: production rank 1 (group 1), fit aligned; 9 supporting profiles
    - previous roles: matched PM-APM (9 of 9), ROLE-SWE (5 of 9); common among supporters but not recorded here: none
    - responsibilities: matched roadmap_ownership (9 of 9), technical_engineering (5 of 9); common among supporters but not recorded here: none
    - industries: matched saas (5 of 9); common among supporters but not recorded here: none
    - skills: not recorded in this profile; not compared (common among supporters: roadmapping (9), java (5), sql (5))
    - qualification levels: matched bachelor (5 of 9); common among supporters but not recorded here: none
    - qualification types: matched btech (5 of 9); common among supporters but not recorded here: none
    - fields of study: matched computer science (5 of 9); common among supporters but not recorded here: none
    - seniority: more_senior
- `PM-PM>ROLE-PGM>PM-SPM`: production rank 2 (group 2), fit limited; 6 supporting profiles
    - previous roles: matched none; common among supporters but not recorded here: ROLE-BA (6), ROLE-QA (6)
    - responsibilities: matched none; common among supporters but not recorded here: delivery_execution (6), requirements_analysis (6), stakeholder_management (6)
    - industries: matched none; common among supporters but not recorded here: it_services (6)
    - skills: not recorded in this profile; not compared (common among supporters: requirements (6), sql (6), test automation (6))
    - qualification levels: matched bachelor (6 of 6); common among supporters but not recorded here: none
    - qualification types: matched none; common among supporters but not recorded here: bsc (6)
    - fields of study: matched none; common among supporters but not recorded here: information systems (6)
    - seniority: career_track_change, career_track_change

### R11: Insufficient route evidence (suppressed route)

No route shown (privacy_minimum_not_met): No route can be shown while meeting the privacy requirement.

### R12: Route shown, but every supporting attribute is too rare to show

- `PM-PM>PM-DIR`: production rank 1 (group 1), fit not_established; 5 supporting profiles
    - previous roles: not enough shared detail among supporting profiles to compare
    - responsibilities: not enough shared detail among supporting profiles to compare
    - industries: not enough shared detail among supporting profiles to compare
    - skills: not enough shared detail among supporting profiles to compare
    - qualification levels: not enough shared detail among supporting profiles to compare
    - qualification types: not enough shared detail among supporting profiles to compare
    - fields of study: not enough shared detail among supporting profiles to compare
    - seniority: more_senior

### R13: A, excluding A's own contribution from the supporting population

- `PM-PM>PM-SPM`: production rank 1 (group 1), fit aligned; 8 supporting profiles
    - previous roles: matched PM-APM (8 of 8); common among supporters but not recorded here: none
    - responsibilities: matched roadmap_ownership (8 of 8); common among supporters but not recorded here: none
    - industries: not enough shared detail among supporting profiles to compare
    - skills: matched none; common among supporters but not recorded here: roadmapping (8)
    - qualification levels: not enough shared detail among supporting profiles to compare
    - qualification types: not enough shared detail among supporting profiles to compare
    - fields of study: not enough shared detail among supporting profiles to compare
    - seniority: more_senior
- `PM-PM>ROLE-PGM>PM-SPM`: production rank 2 (group 2), fit limited; 6 supporting profiles
    - previous roles: matched none; common among supporters but not recorded here: ROLE-BA (6), ROLE-QA (6)
    - responsibilities: matched none; common among supporters but not recorded here: delivery_execution (6), requirements_analysis (6), stakeholder_management (6)
    - industries: matched none; common among supporters but not recorded here: it_services (6)
    - skills: matched sql (6 of 6); common among supporters but not recorded here: requirements (6), test automation (6)
    - qualification levels: matched bachelor (6 of 6); common among supporters but not recorded here: none
    - qualification types: matched none; common among supporters but not recorded here: bsc (6)
    - fields of study: matched none; common among supporters but not recorded here: information systems (6)
    - seniority: career_track_change, career_track_change

### R14: A with a different field of study only

- `PM-PM>PM-SPM`: production rank 1 (group 1), fit aligned; 9 supporting profiles
    - previous roles: matched PM-APM (9 of 9), ROLE-SWE (5 of 9); common among supporters but not recorded here: none
    - responsibilities: matched roadmap_ownership (9 of 9), technical_engineering (5 of 9); common among supporters but not recorded here: none
    - industries: matched saas (5 of 9); common among supporters but not recorded here: none
    - skills: matched java (5 of 9), sql (5 of 9); common among supporters but not recorded here: roadmapping (9)
    - qualification levels: matched bachelor (5 of 9); common among supporters but not recorded here: none
    - qualification types: matched btech (5 of 9); common among supporters but not recorded here: none
    - fields of study: matched none; common among supporters but not recorded here: computer science (5)
    - seniority: more_senior
- `PM-PM>ROLE-PGM>PM-SPM`: production rank 2 (group 2), fit limited; 6 supporting profiles
    - previous roles: matched none; common among supporters but not recorded here: ROLE-BA (6), ROLE-QA (6)
    - responsibilities: matched none; common among supporters but not recorded here: delivery_execution (6), requirements_analysis (6), stakeholder_management (6)
    - industries: matched none; common among supporters but not recorded here: it_services (6)
    - skills: matched sql (6 of 6); common among supporters but not recorded here: requirements (6), test automation (6)
    - qualification levels: matched bachelor (6 of 6); common among supporters but not recorded here: none
    - qualification types: matched none; common among supporters but not recorded here: bsc (6)
    - fields of study: matched information systems (6 of 6); common among supporters but not recorded here: none
    - seniority: career_track_change, career_track_change


## Scenario set: privacy_audit (`eval-intersection-v1`)

> EVALUATION FIXTURE: a constructed privacy-audit case (Phase 8.2.1). Not real career histories.

### I01: Each value shown (6 of 8), the three-way combination held by 4

- `PM-PM>PM-SPM`: production rank 1 (group 1), fit not_established; 8 supporting profiles
    - previous roles: not recorded in this profile; not compared
    - responsibilities: not recorded in this profile; not compared
    - industries: matched saas (6 of 8); common among supporters but not recorded here: none
    - skills: matched java (6 of 8); common among supporters but not recorded here: none
    - qualification levels: not recorded in this profile; not compared
    - qualification types: not recorded in this profile; not compared
    - fields of study: matched computer science (6 of 8); common among supporters but not recorded here: none
    - seniority: more_senior

### I02: Own contribution inside the combination excluded

- `PM-PM>PM-SPM`: production rank 1 (group 1), fit not_established; 7 supporting profiles
    - previous roles: not recorded in this profile; not compared
    - responsibilities: not recorded in this profile; not compared
    - industries: matched saas (5 of 7); common among supporters but not recorded here: none
    - skills: matched java (5 of 7); common among supporters but not recorded here: none
    - qualification levels: not recorded in this profile; not compared
    - qualification types: not recorded in this profile; not compared
    - fields of study: matched computer science (5 of 7); common among supporters but not recorded here: none
    - seniority: more_senior

### I03: Own contribution outside the combination excluded

- `PM-PM>PM-SPM`: production rank 1 (group 1), fit not_established; 7 supporting profiles
    - previous roles: not recorded in this profile; not compared
    - responsibilities: not recorded in this profile; not compared
    - industries: matched saas (6 of 7); common among supporters but not recorded here: none
    - skills: matched java (6 of 7); common among supporters but not recorded here: none
    - qualification levels: not recorded in this profile; not compared
    - qualification types: not recorded in this profile; not compared
    - fields of study: matched computer science (6 of 7); common among supporters but not recorded here: none
    - seniority: more_senior

