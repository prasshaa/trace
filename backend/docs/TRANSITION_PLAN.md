# Transition plan (product Phase 4, remediated after the Phase 4 review)

**Code:** `app/services/transition/`:
- `eligibility.py`: D29 and D36, pure;
- `feasibility.py`: D27 and D31–D34 route rules, pure;
- `actions.py`: D28, pure;
- `composer.py`: pure;
- `repository.py` and `service.py`.

The API is in `app/api/transition_routes.py`.

**Knowledge (current versions):**
- pack `pm-v1.2` (D31, D35);
- `pm-bridges-v2` (bridges and the D33 candidate pool);
- `pm-actions-v2`;
- `pm-eligibility-v3` (rules in `trace_employer_criterion_rules`, with `trace_education_fields`).

Earlier versions stay in the database for audit. Every content file is generated from reviewed JSON
(`scripts/build_knowledge_pack.py`, `scripts/build_transition_content.py`, each with `--check`).

**Persistence:** `trace_plan.record_transition_plan`: append-only, shared dated snapshots (D30, D37).

**Decisions:** D4, D25, D27–D41 (`docs/DECISIONS.md`). Review: `docs/design/PHASE_4_REVIEW.md`.

Everything is deterministic: there is no external AI, no embeddings and no scores. `/api/v1/pathway` and its ranking
are unchanged. The Phase 8.2 relevance prototype stays in shadow.

## Three assessments, kept apart (D25)

| Assessment | Source | Used for |
|---|---|---|
| 1 Target-role capability | `CapabilityService` with pm-v1.2 | Route rules and actions |
| 2 Employer eligibility | `pm-eligibility-v3`, per posting | Shown per employer; never changes routes or capability states |
| 3 Personal transition feasibility | The rules below | Route selection |

Observed evidence breadth is a D4 label beside observed routes and never an input.

## Route selection (D31–D34): requirement-level, no percentages, counts or weights

**Decision-rights requirements** (the pack flag): C2 (strategy and roadmap), C4 (outcome metrics and decisions) and L1
(portfolio scope). C1 is excluded. All other requirements are practice requirements: they can be shown from many
roles, and actions on any route close them.

**G, the decision-rights gaps:**
- **Product Manager target:** flagged requirements in state Gap. Credible Transferable evidence supports a direct move.
- **Senior Product Manager target:** Gap *or only Transferable*; they must be Demonstrated at the senior level.
- **L1** counts only when the user states that the target role has portfolio scope (`scope=portfolio`). Otherwise it
  is reported and planned for, never a barrier.

**The rules:**

| Rule | When | Primary |
|---|---|---|
| R1 | A flagged requirement is Unknown for a reason the user can resolve | None. Resolve the named records first |
| R2 | G is empty | Direct |
| R3 | One or more bridges are eligible | Those not dominated (several are shown as tied). Direct is the alternative |
| R4 | No bridge is eligible | Direct, **conditional**: it names the gaps still to show and how |

**Eligible bridge:**
- a catalogue bridge for the target;
- not the user's current role;
- allowed to be primary (the Product Owner bridge is alternative-only, D34);
- with a credible entry:
  - evidenced by the profile against stated entry requirements;
  - described by the source as an entry route; or
  - the user's own Product Manager plan;
- that develops at least one requirement in G, **fully or partly** (D32).

**Dominance.** One bridge dominates another when, requirement by requirement, it develops each one at least as fully
(full > partly > not at all), and at least one strictly more fully. When the two are equal on G, the same comparison is
made on the practice gaps. Nothing is added up.

**Partial development** is never presented as closing a gap. The route lists, for each partly developed requirement,
what the bridge develops, what remains incomplete and what responsibility to seek. Stage 4 has an `apply.bridge_seek`
action for each.

**Product Analyst.** It is chosen when it develops the user's missing outcome ownership (C4) more fully than the
alternatives and the user has a credible way in (data or analytics roles, or analytics shown). One example: a data
scientist whose strategy evidence carries over but who has no outcome-ownership evidence. Its move to PM is labelled
a curated hypothesis.

## D35: the C4 outcome-metrics question

The Data analysis area asks: "Did you define or own product outcome metrics and use them to influence product
decisions, rather than only analyse or report metrics?"

| Answer | Effect on C4 |
|---|---|
| Yes | The confirmed line may show C4 directly |
| No | Transferable |
| Not sure / unanswered | Unknown, never "no" |

A stated skill never demonstrates C2, C4 or L1 (Transferable only).

## Actions (D28, `pm-actions-v2`)

| State | Actions |
|---|---|
| Gap | Build (stage 1, labelled a learning exercise) and Demonstrate (stage 2; personal projects labelled as personal projects) |
| Transferable | Demonstrate and Reposition |
| Demonstrated | Reposition only |
| Unknown | Resolve first (stage 0, in the Trace profile) |

**Repositioning** says only what confirmed records show, and never adds ownership, research, metrics or outcomes.
Skill-based and role-history evidence have their own reposition templates.

**Stage 4 is always present:**
- verified current postings with each employer's outcome;
- clarifying unclear employer criteria;
- positioning;
- a case study from real work;
- people already in the role;
- interview and product-case preparation;
- an internal route;
- alternative routes;
- on bridge routes, the bridge role first and the responsibility to seek.

Every action carries `labels`: `learning_exercise`, `personal_project_possible`, `factual_accuracy` or
`trace_curated`.

## Employer eligibility (D29, D36)

Each criterion gives meets, does not meet or unclear. Only required criteria decide; preferred ones are reported.

| Area | Rule |
|---|---|
| Years | Partial dates, with overlaps counted once. Internships, part-time work and apprenticeships with no stated work pattern never count towards the certain minimum |
| Equivalent experience | Product Owner or founder experience against product-management tenure gives **unclear**: never counted, never rejected |
| Qualifications | A degree type (a B.Tech meets "B.E./B.Tech"; the discipline is reported separately), or the stated field matched as a whole normalised value through explicit aliases. Never substrings. Missing or unrecognised gives unclear |
| Target (J11) | Relevant years AND product exposure. Product exposure needs categorised product-work lines; a title alone never counts |
| Sarvam (J12) | Exclusions apply only to the current role, so earlier consulting never disqualifies a later PM. For "currently a product manager": a confirmed Product Manager meets; a current Associate PM is **unclear**, because employer equivalence isn't established (D41; J12 only, no global APM rule); an excluded current role does not meet. J12's restriction is never applied to any other posting |
| Razorpay (J3) | The engineering preference counts only experience before the first product management role (E8); imprecise dates give unclear. It is a preference, so it never decides |
| Postings | Only those verified within 14 days are opportunities. A posting with no recorded criteria says so |

## Saved runs (D30, D37, D38)

`POST /api/v1/transition-plan` saves the plan through the trusted writer and returns it as stored. Completed runs are
immutable.

**Snapshots** hold aggregate counts, settings, a state fingerprint and a first-observed date. Plans in the same pool
state share one row, so there is no per-plan time.

**`service_role`** cannot insert, update, delete or truncate the plan or snapshot tables. Deletion by owners and
account cascades is unchanged.

## Limitations

- The bridge catalogue has four entries. The Product Analyst and Product Owner moves into PM are hypotheses.
- The digital candidate pool (D33) is documented content only; it has no routing effect.
- L1 depends on the user stating the target role's scope; there is no source for typical scope per employer.
- Route outcomes for V2 and V4 (Associate PM primary, direct alternative) are intended (D40).
- POC iteration (D42, D43): stage titles Build, Demonstrate, Reposition, Hiring transition; `freshness` on every plan
  response; employer research for the user's chosen companies is computed by `TransitionPlanService.employer_postings`
  (the same assessment 2, evaluated now) and never stored in or read by the plan.
- The report UI is `web/js/views/report.js` (product Phase 5). It reads the plan and its `summary` projection
  (`summary.py`), which repeats the stored decision and adds no rule.

## Product redesign: the roadmap (D44)

`roadmap.py` (pure) projects a saved run into the roadmap shown to users; `provenance.py` indexes the records eligible
now (the assessment's own `reviewed_records` rule: reviewed, categorised, not stale; confirmed skills; confirmed roles
with a current match). A claim or action citing a record that is no longer eligible is dropped. The goal
(`trace_private.transition_goals`) only organises the roadmap: how many actions are highlighted per stage follows the
stated weekly time (1–3 hours: one; 4–6 or varies: two; otherwise three), checkpoints follow the stages, and dates are
only those the user sets. No duration is estimated: plans carry no approved empirical duration (the foundation's
`timeline_display_allowed` gate is empirical-only), and synthetic data never supports one. Route rules are unchanged.

## Comprehensive redesign: the detailed plan (D45)

`learning.py` (pure) adds the detailed layer to the roadmap from `content/pm-learning-v1.json`, a versioned, curated pack:
experience themes (the user's own categorisation of confirmed, eligible records, paraphrased; each record listed once),
development plans for up to three priorities (curated guides for C1–C6; other requirements fall back to role knowledge
and the route's own actions), a sequence, and stage guidance. Every resource records the facts checked on its own page
(title, provider, access, published length when stated, topics) with the check date; prices are never recorded. The
pack is reviewed content, not a database table: moving it into the knowledge-pack pipeline is a follow-up if editors
need it. `/api/v1/transition-plans/latest` (204 when none) tells the app where a returning user lands.
