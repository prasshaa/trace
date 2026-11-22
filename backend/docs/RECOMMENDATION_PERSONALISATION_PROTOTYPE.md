# Whole-Profile Relevance Prototype (Phase 8.2)

**Status: shadow / evaluation mode only.** The prototype lives in `backend/evaluation/recommendation/` and is not
imported by the application (asserted by `tests/test_phase82.py`). Production route discovery, ranking, fit and
labels are unchanged.

Everything below was produced with evaluation fixtures: constructed careers, not real people and not market evidence.

---

## 1. Existing recommendation methodology (unchanged)

- **Route discovery:** distinct observed routes from the user's confirmed current role to the target. These are
  episode sequences from individual reference histories (first origin stint, then the first later target stint).
- **Exclusions:**
  - the user's own contribution;
  - zero-move routes;
  - routes with fewer than 5 distinct supporting profiles.
- **Fit:** whether the user's categorised responsibility areas cover the areas recorded by a majority (and at least 5)
  of the route's supporters.
- **Ranking:** fit tier, then the number of intermediate roles the user hasn't held, then the stable route id. Ties
  are reported (Phase 8.1).

See `RECOMMENDATION_INTELLIGENCE_AUDIT.md` §3–§6.

New in Phase 8.2 is presentation only: `routes[].step_directions`, with direction labels for each move (§6).

## 2. Whole-profile features examined

| Dimension | User side (confirmed only) | Reference side (the route's own supporting profiles) |
|---|---|---|
| Previous roles | standard roles held, excluding the current role | roles held before the route starts in each supporter's history |
| Responsibilities | categorised responsibility areas | areas recorded on the route's pre-destination steps |
| Industries | employers' industry sectors | employers' industry sectors |
| Skills | reviewed skills (unreviewed ignored) | profile skills |
| Qualifications | qualification levels | qualification levels |
| Fields of study | fields of study | fields of study |
| Seniority | none (a property of the route) | the direction of each move (taxonomy, §6) |

## 3. Data quality and coverage

**41-profile synthetic reference pool** [Verified by read-only queries]:

| Field | Coverage | Usable for comparison? |
|---|---|---|
| Earlier roles before the route | 23 of 41 profiles start with a non-PM role. Routes are mostly short, and most backgrounds have 1 profile. | Rarely: few routes reach 5 supporters, and their pre-route roles are diverse. |
| Responsibility areas | 105 of 106 evidence rows, about 2.6 per profile, one line per job | Weak: route-level areas are usually below 5 or withheld. |
| Industries | 81 of 83 employers known; 41 profiles | Yes on the reference side. |
| Skills | 174 rows, 89 distinct taxonomy skill ids | Not comparable with users today (see below). |
| Qualification level | 44 of 44 education rows | Yes. |
| Field of study | 42 of 44 (free text) | Only after normalisation; spelling varies. |
| Employment-level seniority | 1 of 106 employments | No: role-default seniority only (§6). |

**User side** [Verified in code]:

- **Skills are text only.** Candidate skills are stored as raw text: the extraction never maps `skill_id`
  (`resume_extraction/fixture.py`; the real parser likewise). Reference skills are taxonomy ids. A production skill
  comparison therefore needs a skill-mapping step first, which is also why the existing "Skills: not assessed" notice
  exists. The prototype compares normalised text, because the evaluation fixtures use text on both sides.
- **Industry is usually unknown for real resumes.** The real-resume parser leaves it `unknown` unless stated. Industry
  comparison would mostly be "not recorded" for real users today.
- **Qualifications, fields and roles** are captured and reviewable.

**Evaluation fixtures:**

- The Phase 8.1 core pool has no attributes and no pre-route history. There, the prototype correctly reports
  "insufficient reference evidence" for most dimensions.
- The new personalisation pool (`pool_personalisation.json`, 23 profiles) carries all six dimensions, so each one can
  be exercised.

## 4. Relevance calculations

The code is `evaluation/recommendation/relevance.py`. For each route that production shows:

1. **Population:** the distinct supporting profiles of that exact route, from production's own evidence. That means
   after own-contribution exclusion and route privacy. Never profiles that merely share the target role.
2. **Counting:** per dimension, count distinct supporters per value (normalised text; role ids as is).
3. **Disclosure:** a value is shown only if at least 5 supporters share it.
4. **Classification:**

   | Status | When | Contents |
   |---|---|---|
   | `compared` | the user has confirmed values and at least one reference value is disclosed | `matched` (value, count), `reference_not_in_profile` (value, count; "not recorded here", never "missing"), `profile_not_shown_among_supporters` (the user's values not disclosed among supporters) |
   | `not_recorded_in_profile` | the user has nothing confirmed for this dimension | not compared; the disclosed reference patterns are listed only as context |
   | `insufficient_reference_evidence` | the user has values, but no reference value reaches 5 supporters | — |

5. **Seniority:** the per-move labels from §6, or `no_reliable_level`.

There are no weights, no composite score and no ranking. The output is a breakdown with plain counts ("5 of 9
supporting profiles").

**Limitations of the calculation:**

- Set overlap ignores how strongly a value is evidenced, and recency.
- Text normalisation is deliberately minimal.
- Values are counted per profile, not per job.
- Small routes (5–9 profiles) give coarse counts.

## 5. Missing-data handling

- **Nothing confirmed** for a dimension: `not_recorded_in_profile`. It is never compared, never "unmatched" and never
  penalised. Scenario R09 checks responsibilities.
- **Unreviewed skills** are not read. R10 has unreviewed skills only, so skills are `not_recorded_in_profile`.
- **Reference detail too thin:** `insufficient_reference_evidence`, with no guess (R12, S05).
- **Route below the privacy minimum:** no breakdown at all (R11).

## 6. Seniority-direction labels (implemented, presentation only)

`candidate_profile.pathway.move_direction` is used for `routes[].step_directions` and shown on the route track.

| Move | Rule | Label |
|---|---|---|
| Same canonical role family, both levels reliable, higher | rank increases | Move to a more senior role |
| Same family, both reliable, lower | rank decreases; **kept, not filtered** | Move to a more junior role |
| Same family, both reliable, equal | equal rank | Lateral move |
| Different families | levels not comparable | Change of career track |
| Unknown level, missing metadata, fallback `mid`, an unresolved family | — | no label |

A level counts as **reliable** only when its family defines more than one seniority level in the taxonomy (product
management does), and the role has a rank:

- Families whose roles all default to `mid` are excluded, because their `mid` is the fallback for an unqualified title.
- Founder and unknown have no rank.

Titles are never read: aliases resolve to role ids first. Route discovery and ranking don't use these labels.

## 7. Privacy safeguards

- The population comes from production evidence:
  - own contribution excluded first;
  - zero-move routes excluded;
  - routes with fewer than 5 distinct supporters suppressed.
- Every disclosed value needs at least 5 distinct supporters (the existing small-group minimum, unchanged). A user's
  own values that aren't disclosed are reported as "not shown among supporting profiles", which never confirms that a
  rare value exists.
- No profile identifiers appear in any report. Supporter ids are used internally and removed before output.
- **Distinctive-route review (Part A §3):**
  - Route-level suppression already guarantees that at least 5 distinct profiles share the exact complete sequence.
    That is k ≥ 5 on the sequence itself, so a distinctive *sequence* cannot be shown for fewer people. No additional
    route threshold was introduced.
  - The genuinely new exposure is the prototype's attribute disclosure. Its narrowest safeguard is the per-value
    minimum of 5 within the route's supporters, implemented above.
  - **Residual risk:** values are disclosed per dimension (marginals). A conjunction (for example saas *and* Java
    *and* computer science) is not shown, but it could describe fewer than 5 people. Before any of this reaches a
    user-facing surface, a privacy review should decide whether conjunctions need a rule. None is invented here.

## 8. Example comparisons

Personalisation pool, current role Product Manager, target Senior Product Manager. Two routes are discovered for
both users.

**Profile A (R01: Software Engineer → APM → PM; technical engineering, roadmap; Java, SQL; SaaS; bachelor's in
computer science):**

- **`PM → Senior PM`** (9 supporters, *move to a more senior role*):
  - earlier roles matched: APM (9 of 9), Software Engineer (5 of 9);
  - responsibilities matched: roadmap (9), technical engineering (5);
  - industry SaaS (5);
  - skills Java and SQL (5 each); roadmapping (9) not recorded in A's profile;
  - qualification bachelor (5);
  - field computer science (5).
- **`PM → Program Manager → Senior PM`** (6 supporters, *change of career track* twice):
  - earlier roles: none matched (QA and BA common, 6 each);
  - responsibilities: none matched;
  - industry: IT services not recorded here;
  - skills: SQL matched;
  - qualification: bachelor matched.

**Profile B (R02: QA → BA → PM; requirements analysis, quality and testing; SQL, test automation; IT services;
bachelor's in information systems):** the mirror image.

- **Program Manager route:** earlier roles QA and BA (6 of 6), requirements analysis, IT services, SQL, test
  automation and information systems all matched.
- **Direct route:** only SQL and bachelor matched.

**Why they differ:** their earlier roles, responsibilities, industries, skills and fields of study each match a
different route's supporters. Qualification level (bachelor) matches both routes for both users, so it does not
distinguish them. The prototype says this explicitly.

**Own contribution (R13 vs R01):** excluding A's own contributed profile drops the direct route's population from 9
to 8. Software Engineer and SaaS fall to 4 and are no longer disclosed, so those dimensions become "insufficient
reference evidence". Production ranking is unchanged.

**Single-dimension changes (R03–R10):** changing one dimension changes only that dimension's breakdown. This is
checked automatically, for example R05 (industry) changes only `industries`. An irrelevant field (R08: location,
preferred name) changes nothing.

**Phase 8.1 core S16A vs S16B:** the core pool has no supporting attributes or pre-route history. Only
responsibilities differ between the two backgrounds, and every other dimension is "insufficient reference evidence".
This is the honest result for thin data.

## 9. Existing ranking vs prototype output

| Scenario | Production (unchanged) | Prototype adds |
|---|---|---|
| R01 (A) | 1: `PM>SPM` aligned; 2: `PM>Program Manager>SPM` limited | why: A's history matches the direct route's supporters on 6 of 6 compared dimensions; the Program Manager route's supporters share only SQL and bachelor |
| R02 (B) | 1: `PM>Program Manager>SPM` partial; 2: `PM>SPM` limited | why: B's history matches the Program Manager route's supporters on every compared dimension |
| R13 (A, own excluded) | same as R01 | smaller population; some values no longer disclosed |
| Core S05 | `PM>SPM` not established | every dimension: insufficient reference evidence (consistent with production) |

**Finding: the existing engine can already personalise on responsibilities.** It ordered A and B differently in this
pool, because responsibility-area fit works once supporters record majority areas. The 41-profile synthetic pool
never has majority areas, so production looks unpersonalised there. The data, not the method, is the main limiter
for responsibilities.

The prototype's contribution is the other dimensions:

- earlier roles;
- industry;
- skills;
- qualifications and fields of study.

These are explanations today. Whether any of them should influence ordering is not established. That is for the human
review (§11).

## 10. Evaluation results (shadow run, harness `phase-8.2-v1`)

Run with `python -m evaluation.recommendation --shadow`. Outputs go to `evaluation/recommendation/output/`
(`shadow.json`, `shadow_report.md`, review pack). The run covers 35 scenarios (22 core + 13 personalisation).

**Prototype invariants, all passing:**

- disclosed values meet the minimum;
- only shown routes get a breakdown;
- the population equals distinct route support;
- the user's own contribution is never in the population;
- a dimension is compared only when the user has data;
- one changed dimension changes only that dimension;
- unreviewed skills are ignored;
- missing responsibilities are not compared;
- suppressed routes get no breakdown;
- own exclusion changes the population exactly;
- the run is deterministic;
- **production output is identical to the Phase 8.1 baseline**.

**Answers to the evaluation questions:**

| Question | Answer |
|---|---|
| Which dimensions affect relevance signals? | All six, where the reference side records them at 5 or more per value (personalisation pool) |
| Which are ignored because data is missing? | In the 41-profile pool and the core fixtures: earlier roles (diverse, small), responsibilities (thin), seniority at job level. For real users: industry (parser leaves it unknown) and skills (unmapped text). |
| Do different backgrounds produce different signals? | Yes (R01/R02), on the dimensions their histories actually differ in; not on the one they share (bachelor) |
| Stable under irrelevant changes? | Yes (R08, and the dimension-isolation invariant) |
| Can a route be relevant without being the most frequent? | Yes. For B, the Program Manager route (6 supporters) is the strongest match, while the direct route has 9. |
| Own-contribution exclusion? | Changes the population and disclosure exactly (R13) |
| Privacy? | Suppressed routes give nothing; every disclosed value has at least 5 supporters |
| Can it explain itself? | Every signal names the value and its count among the route's supporters |
| Does it invent evidence? | No. Thin data gives "insufficient reference evidence"; missing user data gives "not recorded". |

## 11. Limitations

- **Constructed data.** These are constructed fixtures. The A/B separation is real for the prototype's logic but
  says nothing about real careers.
- **No validation yet.** The prototype is not validated: there are no human judgments yet (§12), and no relevance
  claim follows from the passing tests.
- **Production gaps:**
  - skills need taxonomy mapping on the user side;
  - industry is rarely known for real resumes;
  - a production version needs a new read path for supporters' attributes, with the same privacy rules, behind a
    review.
- **Marginal disclosure** (§7 residual risk).
- **Time:** recency and duration of experience are not considered.

## 12. Human review and next phase

**Human review.** The pack and procedure are in `RECOMMENDATION_HUMAN_REVIEW.md`. Pathways are shown in a neutral
order with aggregate evidence only, with no production rank, tier, fit or prototype match. All labels are `PENDING`.

**Recommendation for the next phase:**

1. Run the two-reviewer review on the 36 scenarios (35 + R14, Phase 8.2.1). Measure agreement, and adjudicate disagreements with a third
   reviewer.
2. Compare reviewers' prioritised pathways with production order and with the prototype's breakdown, scenario by
   scenario. Investigate every disagreement before considering any ordering change.
3. Only then decide whether any dimension should influence ordering, and how (for example as a tie-breaker within a
   rank group, which is the narrowest change), with the evaluation harness as the regression gate.
4. In parallel, close the data gaps that block production use: skill mapping, and industry capture in the real-resume
   flow.

The prototype stays in shadow mode until those decisions are made.

## 13. Phase 8.2.1: human-review readiness corrections

The shadow prototype's logic and the production engine are unchanged:

- no weights and no score;
- not imported by the application;
- production output is identical to the Phase 8.1 baseline, checked by an invariant.

Phase 8.2 itself stays reproducible:

- the module is frozen as `evaluation/recommendation/shadow_phase82.py`;
- its outputs are stored in `evaluation/recommendation/output/phase-8.2/`;
- `python -m evaluation.recommendation --reproduce-phase-8-2 DIR` regenerates them byte for byte, which a test checks.

### 13.1 Qualification semantics

Phase 8.2 used one `qualifications` dimension, which mixed level and type. A pool that declares `"dimension_set": "v2"`
(`pool_personalisation_v2.json`) now uses three separate dimensions:

| Dimension | Meaning | Examples |
|---|---|---|
| `qualification_levels` | level of award | bachelor, master, doctorate |
| `qualification_types` | named award | BTech, BSc, MTech, **MBA** |
| `fields_of_study` | subject | computer science, information systems, business |

An MBA is a type at master level, never a level of its own; a test checks this. The v1 pool, the v1 scenarios and the
v1 dimension list are untouched.

**R07 is now split.** Each variant changes exactly one dimension relative to R01, and an invariant checks this:

- **R07** changes only the qualification level and type (bachelor/BTech to master/MTech, with the same field of
  computer science). The breakdown differs only in `qualification_levels` and `qualification_types`.
- **R14** (new) changes only the field of study (computer science to information systems). The breakdown differs only
  in `fields_of_study`.

### 13.2 Intersection (conjunction) privacy

**The risk.** Each value shown has at least 5 supporters, but showing several values together can describe a smaller
group. The audit helper `relevance.combination_audit` lists every combination of disclosed values (one per dimension,
two or more dimensions) held by more than 0 and fewer than 5 of a route's supporters. Reports print only "fewer than
5"; exact counts stay internal to tests.

**Constructed case** (`pool_intersection.json`). The route `PM-PM>PM-SPM` has 8 supporters, and no database is
involved. SaaS, Java and computer science are each held by 6 supporters; all three together by 4.

| Scenario | Own contribution | Supporters | Flagged combinations |
|---|---|---|---|
| I01 | none | 8 | the triple only (each pair is held by 5) |
| I02 | a profile with all three | 7 | the triple **and** all three pairs; excluding the caller makes the pairs small |
| I03 | a profile with none of them | 7 | the triple only |

**Findings:**

1. The per-value minimum doesn't bound the size of a combination. Three values, each with 6 supporters, can jointly
   describe 4 people.
2. Own-contribution exclusion changes the result for each caller. The same route and the same values can be safe for
   one caller and a small-group combination for another (I01 vs I02). Any check therefore has to run for each caller,
   after exclusion.
3. In the 36 review scenarios, the core and personalisation sets, the audit flags nothing. The risk is real but absent
   from the current fixtures.

**Recommended review policy.** This is not a new production threshold, and nothing about disclosure changes now.

- Before any whole-profile breakdown is shown to users:
  - a privacy review must decide how combinations are handled. Options include showing at most one dimension's
    values per route, or applying the minimum to the combination a user actually sees;
  - this audit must run in CI over the evaluation fixtures, and over a de-identified sample of the real pool, as part
    of that review.
- Until then, breakdowns stay internal to evaluation, and the review pack shows only single values, each shared by at
  least 5.

### 13.3 Direction wording: "career track change"

**Context.** `step_directions` labels Product Owner → PM as `career_track_change`, because `product_ownership` and
`product_management` are separate families.

**Assessment.** The taxonomy can't support the claim that this move is a change of career track.

- `public.trace_role_families` is a flat list with no adjacency or distance data.
- The `product_ownership` description says it is kept separate "until responsibility evidence is reviewed", so the
  separation is provisional.
- For many people, PO → PM is a close move, and "change of career track" overstates it.

**Recommendation.** Use neutral wording for this label pending product approval, for example "Move to a different role
family", or "Different role family" as a short label. This states only what the taxonomy records. The internal value
`career_track_change` can stay. No production text was changed in this phase; this is a product decision.

### 13.4 Review readiness

`RECOMMENDATION_HUMAN_REVIEW.md` now defines:

- the permitted responses for every question;
- the pre-filled `not_applicable` for Q3 and Q4;
- how scenarios with no pathway are recorded;
- the materiality and escalation rules for each question;
- both adjudication files (pathway and scenario level).

The pack explains each no-pathway scenario without revealing any suppressed route, count or contributor:

- S11: insufficient independent evidence;
- S14: no independent evidence;
- S15: unconfirmed starting point;
- S21: no observed route;
- R11: privacy minimum not met.
