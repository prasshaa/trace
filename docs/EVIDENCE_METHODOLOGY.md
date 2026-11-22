# Trace evidence methodology

## Three intelligence sources

| Source | Answers | Examples |
|---|---|---|
| The user's confirmed profile | What *this person* has done | Reviewed employments, evidence, skills, qualifications |
| Eligible career histories | What *relevant professionals* actually did | Observed role sequences, shared backgrounds |
| Target-role knowledge | What the *role* requires and what helps | Curated requirements, frameworks, verified resources |

A recommendation can draw on all three, but each claim keeps its source category:
- `candidate_provided`;
- `candidate_confirmed`;
- `real_reference`;
- `synthetic_evaluation`;
- `curated_target`;
- `external_knowledge`;
- `trace_generated`.

This is planned for Phase 4. Today's API separates `observed`, `fit` and `next_steps` and labels synthetic data.

## Who counts as evidence

A profile counts as evidence only if all of these hold:
- it is eligible (consented real data, or synthetic in a synthetic mode only, always labelled);
- it is not withdrawn;
- its role mappings pass the confidence and review rules;
- it is not the caller's own contribution.

Synthetic and real profiles are never combined.

## Privacy rules (enforced in the backend and the database)

1. **Minimum of 5** distinct eligible profiles for any route, value or breakdown shown (`min_profiles_limited` = 5).
   Below that, the route's evidence is suppressed and the user is told there is insufficient comparable history. The
   pathway itself is not called unsuitable.
2. **Combinations:** values that each pass the minimum can jointly describe fewer than 5 people. The Phase 8.2.1 audit
   (`backend/evaluation/recommendation/relevance.py::combination_audit`) detects this, and it depends on the caller
   because of own exclusion. Before any whole-profile breakdown is shown, a privacy review must decide how combinations
   are handled.
3. **Own exclusion:** the caller's contributed profile is removed before counting.
4. **Exact counts stay internal.** They are used for calculation, audit and privacy enforcement; the API returns labels.

## Evidence-breadth labels (D4) [Planned, Phase 4]

This is a backend presentation layer over the internal count of distinct eligible supporting profiles. Its thresholds
live in backend configuration, never in the frontend.

| Internal count | User-facing label |
|---|---|
| fewer than 5 | evidence suppressed ("insufficient comparable career-history evidence") |
| 5–9 | Limited observed evidence |
| 10–24 | Growing observed evidence |
| 25–99 | Broad observed evidence |
| 100+ | Extensive observed evidence |

**About these thresholds:**
- They are provisional, for discussion and testing, not statistical standards.
- They must be reviewed against the real reference distribution before production use.
- In synthetic modes the label always says the evidence is synthetic test data.

**Why a separate layer:** the database's existing `trace_evidence_class` (`limited`, `moderate`, `strong`,
`insufficient`, `synthetic_test_only`) and its thresholds (5, 15, 40) are part of stored-recommendation constraints and
stay unchanged.

**Profile-led recommendations** with no comparable evidence are labelled "limited or unavailable comparable
career-history evidence" and never shown as observed transitions.

## What evidence never means

- **Frequency is not probability, effectiveness or likelihood of being hired.**
- No percentiles yet. They need adequate samples, a defined population, consistent inclusion, stable denominators and
  privacy approval. They are never described as a chance of success.
- No claims about effectiveness without outcome data.

## Provenance of profile facts

Each candidate record carries its origin:
- `user_stated` (typed by the user);
- `source_extracted` (from a resume, with the extraction run, the resume hash and the source location);
- `inferred`.

Its review status is one of `pending_review`, `user_confirmed`, `user_corrected` or `rejected`, and corrections keep an
append-only history. Proposals the user accepts (Phase 2) keep a link from the record back to the proposal and its
source.
