# Trace product principles

These principles are non-negotiable. A change that breaks one is a defect, however good the result looks.

## Recommendations

1. **The profile decides personal relevance; evidence supports, it does not decide.** A route that more people took is
   not automatically a better recommendation.
2. **Personal suitability and observed evidence breadth are separate attributes.** They are never combined into one
   score or badge. A route with limited observed evidence can be the primary recommendation.
3. **Intermediary roles are never mandatory.** One is suggested only when the extra experience has meaningful value for
   this person, with an explanation of how it reaches the target.
4. **Learning, shadowing, courses and portfolios are activities inside a pathway, not pathways.**
5. **No generic checklist.** Activities follow from this user's gaps, constraints and target. A user whose profile
   already shows something isn't told to start learning it.
6. **Hard constraints are never silently violated.** Preferences shape ranking; constraints limit it.
7. **Alternatives must offer a real trade-off.** They are never generated to fill a fixed number of cards.
8. **Insufficient reference evidence never means "no advice".** Trace still gives a profile-led pathway from the
   confirmed profile and verified role knowledge, clearly labelled.

## Evidence and honesty

9. **Every claim has a source category:**
   - `candidate_provided`;
   - `candidate_confirmed`;
   - `real_reference`;
   - `synthetic_evaluation`;
   - `curated_target`;
   - `external_knowledge`;
   - `trace_generated`.

   A generated explanation is not evidence.
10. **Synthetic data is never presented as real career history.**
11. **No invented evidence:**
    - no invented durations, costs, placement rates, success probabilities, readiness percentages or citations;
    - no claims about whether something is effective, since there is no outcome data.
12. **Missing is not lacking.** Capability states are **Demonstrated**, **Transferable**, **Gap** and **Unknown**. A
    gap needs reviewed information that genuinely shows its absence; otherwise the state is Unknown.
13. **Resumes don't record what people did between jobs.** Courses, mentoring, referrals or portfolios are never
    inferred from title changes.
14. **Honest evidence types.** A self-directed case study is not professional product ownership, and the report says so.

## Privacy

15. **At least 5 distinct eligible profiles** stand behind any route-level evidence. Attribute combinations are
    checked too (`EVIDENCE_METHODOLOGY.md`).
16. **Small exact counts are never shown.** Users see evidence-breadth labels; exact counts stay internal.
17. **A user's own contributed profile never counts as evidence for them.**
18. **Processing permission and sharing permission are separate.** A personal profile never automatically becomes
    reference data.
19. **De-identified is not anonymous.** It is never described as anonymous.

## Engineering

20. **All ranking, suitability, evidence, statistics and privacy decisions happen in the backend.** The browser renders
    structured results and calculates nothing.
21. **The deterministic engine works without an LLM.** The LLM explains and converses; it does not decide, count or
    confirm.
22. **The database is the memory.** The model's context window is temporary working context.
23. **Additive, tested change.** Working systems are not rebuilt because the vision moved. No new framework, database or
    infrastructure without a demonstrated need.
