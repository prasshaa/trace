# Recommendation Human Review: Procedure (Phase 8.2.1)

## Status

**Pending.** No human label exists. Nothing in this repository was produced by, or should be read as, an expert
judgment.

## Reviewers

Each reviewer works independently and does not see the other's answers.

- **Reviewer A:** an experienced Product Leader, Senior PM or hiring manager familiar with PM career progression.
- **Reviewer B:** an experienced PM recruiter, career advisor, or another senior professional familiar with varied PM
  career paths.
- **Reviewer C (adjudication only):** a third senior reviewer, used only for material disagreements, as defined below.

## Materials

Generate them with `cd backend && .venv/bin/python -m evaluation.recommendation --shadow`. The files are written to
`evaluation/recommendation/output/`:

| File | For |
|---|---|
| `review_pack.md` | everyone: instructions and 36 scenarios (22 core + 14 personalisation, including R14) |
| `review_reviewer_A_pathways.csv` / `_B_` | one row per displayed pathway: Q1–Q5 and notes |
| `review_reviewer_A_scenarios.csv` / `_B_` | one row per scenario: Q6–Q8 |
| `review_adjudication.csv` | pathway level: A's and B's answers to Q1–Q5, the material questions, and reviewer C's decision |
| `review_adjudication_scenarios.csv` | scenario level: A's and B's Q6, materiality, and reviewer C's decision, rationale and missing information |

The A and B forms are identical, with the same neutral scenario and pathway mapping (`pathway_a`, `pathway_b`, … in a
hash order). Reviewers record answers **only in their own forms**; nobody edits the pack or any fixture.

**What reviewers see:**

- the background:
  - current role;
  - earlier roles;
  - responsibilities;
  - skills;
  - industries;
  - qualifications;
  - field of study;
- the target;
- for each pathway: its roles, its intermediate roles, and aggregate evidence (how many test profiles followed it, and
  values shared by at least 5 of them).

**What they don't see:** Trace's rank, tier, fit, limitations, matched/unmatched flags, any score, or any profile or
contributor identity.

## Permitted responses

**Per pathway:**

| Question | Permitted responses |
|---|---|
| Q1 Is the pathway plausible given this person's background? | yes / no / unsure |
| Q2 How relevant is it to their current experience and target? | not_relevant / weakly_relevant / reasonably_relevant / strongly_relevant / cannot_assess |
| Q3 Are the intermediate roles sensible? | yes / no / unsure / not_applicable |
| Q4 Is it meaningfully different from the alternatives? | yes / no / not_applicable |
| Q5 Does the supporting evidence justify presenting it? | yes / no / unsure / cannot_assess |

**Per scenario:**

| Question | Permitted responses |
|---|---|
| Q6 Which pathway would you prioritise? | pathway_a / pathway_b / … / equal / none_of_the_displayed / cannot_assess / no_pathway_shown |
| Q7 Why? | free text |
| Q8 What information is missing to make a better recommendation? | free text |

**Pre-filled answers.** No reviewer is forced to answer a question that can't apply:

- Q3 is `not_applicable` for a direct move (`has_intermediate_roles = no`).
- Q4 is `not_applicable` when only one pathway is shown (`pathways_in_scenario = 1`).
- A reviewer who disagrees with a pre-filled answer may change it and explains why in `notes`.

**Scenarios with no pathway shown.** S11, S14, S15, S21 and R11 have no pathway rows. Their scenario row has Q6
already set to `no_pathway_shown`; Q7 is optional, and Q8 records what the reviewer would expect or what information
would help. The pack gives the reason for each, without revealing any suppressed route, count or contributor:

| Scenario | Reason shown |
|---|---|
| S11 | not enough independent evidence once the person's own contributed profile is excluded |
| S14 | no independent reference evidence |
| S15 | the current role is unconfirmed, so no starting point |
| S21 | no observed route in this evaluation sample (which does not mean the move is impossible) |
| R11 | no route can be shown while meeting the privacy requirement |

## Combining two reviewers and escalating

The adjudicator, who is not reviewer C, fills `review_adjudication.csv` and `review_adjudication_scenarios.csv` from
the two forms. Only **material** disagreements go to reviewer C. Minor ones are recorded and kept, but not escalated.

| Question | Material (→ reviewer C) | Minor (recorded only) |
|---|---|---|
| Q1 | yes vs no | unsure vs yes or no |
| Q2 | levels two or more steps apart (the order is not → weakly → reasonably → strongly); a level vs `cannot_assess` | adjacent levels: the combined label is the lower one, flagged |
| Q3 | yes vs no | unsure vs yes or no. `not_applicable` vs an answer is a factual mismatch, resolved by checking whether the route has intermediate roles (no reviewer C). |
| Q4 | yes vs no, when the scenario has 2 or more pathways | `not_applicable` vs an answer (factual, as for Q3) |
| Q5 | yes vs no; a yes or no vs `cannot_assess` | unsure vs anything |
| Q6 | different specific pathways; a pathway vs `none_of_the_displayed`; anything vs `cannot_assess` | `equal` vs one of the pathways; both agree on `no_pathway_shown` (never material) |

**Recording:**

- **Pathway level.** `material_disagreement_questions` lists the questions with material disagreements (for example
  `q1;q5`). `escalate_to_reviewer_c` is `yes` if any are listed. Reviewer C records `reviewer_c_decision` and
  `reviewer_c_rationale`.
- **Scenario level.** Record A's and B's Q6 and `material_difference`. Reviewer C records `reviewer_c_final_decision`,
  `reviewer_c_rationale` and `missing_information_identified`. The last also collects both reviewers' Q8 points.

**Reporting:**

- percent agreement per question;
- every material disagreement with both reviewers' reasons and reviewer C's resolution;
- the list of minor disagreements.

A weighted kappa may be reported for Q2 but should be read cautiously with 36 scenarios.

**Rules:**

- Labels are stored with the fixture checksum they refer to (`shadow.json`).
- They are kept separate from automated results and never feed a test assertion.
- No weight is tuned to match one reviewer.
- Disagreements with the engine or prototype are investigated scenario by scenario first.
