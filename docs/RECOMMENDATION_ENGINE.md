# Trace recommendation engine

The engine is deterministic, independent of any LLM, and testable. This document gives the target design and what
exists today. The detailed audit of today's engine is in `backend/docs/RECOMMENDATION_INTELLIGENCE_AUDIT.md`.

## Today [Built]

`CandidateProfileService.pathway_result` (`backend/app/services/candidate_profile/pathway.py`):

1. **Current position:** the user's latest eligible, reviewed and mapped primary employment.
2. **Observed routes:**
   - every eligible reference profile contributes its own sequence from its first episode in the current role to the
     target;
   - sequences are never stitched together;
   - routes with no move are excluded.
3. **Privacy:**
   - the caller's own contribution is excluded;
   - progression routes only;
   - a route-level minimum of 5 distinct profiles.
4. **Ranking:**
   - a rank key with tie groups (`rank_group`, `tied`);
   - fit from the user's own classified evidence (`fit.limitations`);
   - step directions from the taxonomy.
5. **Guidance:** short `next_steps` (area gaps, route steps, evidence prompts).

It is evaluated by the harness in `backend/evaluation/recommendation/` (`RECOMMENDATION_EVALUATION_BASELINE.md`).

**Limitations against the product vision:**
- it is title-route-centred;
- there is no target-requirement assessment, no four stages and no profile-led route;
- it gives no answer when the reference evidence is insufficient.

## Target design [Planned, Phases 3–4]

### 1. Target-role assessment (Phase 3) [Built, see `backend/docs/CAPABILITY_ASSESSMENT.md`]

The engine reads the structured requirements for the target role from the curated knowledge pack
(`trace_target_role_requirements`; role-agnostic, PM first). For each requirement it assigns one state, with the profile
records that justify it:

| State | Meaning |
|---|---|
| **Demonstrated** | Reviewed profile evidence shows it directly |
| **Transferable** | Reviewed evidence shows a related capability, through a curated, documented transferability map |
| **Gap** | The relevant information was reviewed and the capability is genuinely absent |
| **Unknown** | The profile is incomplete, pending or silent. This is the default; it is never counted as a gap |

The LLM never decides a state.

### 2. Reference-profile retrieval

1. **Structured filters:** confirmed starting role and family, previous roles, responsibility areas, skills, industry,
   qualifications, field of study, reliable seniority.
2. **Semantic retrieval** of similar responsibility text, only after D1 and D2 allow it.
3. **Deterministic filtering:** eligibility, privacy, own exclusion.

Similarity is a retrieval signal, never a fit score. Closely comparable, related and broader histories are labelled
separately.

### 3. Route discovery

Observed transitions come from full individual sequences only:
- direct vs intermediary;
- internal vs external, where recorded;
- lateral vs seniority change, where reliable.

Nothing is inferred between jobs: courses, mentoring and referrals are not visible in a resume.

### 4. Pathway construction

A pathway is:
- a role sequence;
- **four stages** (Build foundation, Gain practical experience, Become application-ready, Get into the target role);
- actions grouped as **Build / Demonstrate / Reposition**, each tied to a gap or requirement, with a deliverable, a
  reason for this user and a source;
- a hiring strategy;
- limitations.

| Pathway basis | When | Label |
|---|---|---|
| Observed | Enough eligible histories follow this role sequence | Observed route, plus an evidence-breadth label |
| Profile-led | Histories are insufficient or absent; built from the confirmed profile and verified role knowledge | "Limited or unavailable comparable career-history evidence"; never implied to be observed |

### 5. Suitability and ranking

- **Personal suitability** is assessed from:
  - starting-role applicability;
  - Demonstrated and Transferable coverage of required items;
  - priority gaps;
  - preferences;
  - **hard constraints** (a filter, never a weight);
  - route feasibility (the number and nature of moves).
- The method is a **transparent ordered rule set**: each rule is named and its outcome is shown in an explanation. There
  is no opaque weighted score.
- A composite score is adopted only with a written rationale, tests against evaluation scenarios, and stated
  limitations.
- **Evidence breadth is separate:**
  - it acts as a gate (the privacy minimum) and a supporting label;
  - it is never a proxy for suitability;
  - a less-observed route may be primary. When it is, the pathway's evidence section ends with a subtle note: *"This
    pathway has fewer comparable career histories than some alternatives. We recommend exploring it because it makes
    better use of the experience already present in your profile."* The note is used only when that is the actual
    reason.
- **Whole-profile relevance** (Phase 8.2 prototype) stays in shadow mode (D6). It may inform explanations and actions,
  but it does not change ranking until review gates pass.

### 6. Runs

Every run is saved in the existing tables (`trace_pathway_runs`, `_recommendations`, `_route_steps`, `_evidence`) with:
- the profile version (Phase 2 fingerprint);
- the target-role pack version;
- the reference snapshot;
- the knowledge sources used;
- the output and its rationale;
- the evidence classifications;
- the prompt and model version, when an LLM explained it.

Runs are never overwritten; a changed profile produces a new run.

## Evaluation gate

Any production ranking change must:
- keep every harness invariant passing;
- show a before/after comparison over the scenario set;
- pass the human review procedure (`backend/docs/RECOMMENDATION_HUMAN_REVIEW.md`) where it changes order.
