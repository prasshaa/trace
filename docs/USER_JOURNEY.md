# Trace user journey

Status markers:
- **[Built]** works today;
- **[Partial]** exists but is incomplete;
- **[Planned]** is not built (see `IMPLEMENTATION_PLAN.md`).

## 1. Sign in and data-use notice [Built]

The user signs in with Supabase Auth (email, or Google locally) and sees the onboarding data-use notice.
- Permission to process a resume and permission to share a de-identified history are separate.
- Sharing is a preference recorded separately from explicit permission (`backend/docs/SHARING_PREFERENCE.md`).

## 2. Intake: three ways in, one pipeline

All inputs feed **one** structured career-profile pipeline: input → drafts or proposals → user review → confirmed
profile. There is never a separate recommendation system per input.

| Input | Status | Notes |
|---|---|---|
| Resume upload (PDF) | **[Partial]** | Parsed locally and deterministically, de-identified, then turned into pending drafts. Real uploads are **disabled** on this server, and there is no sample-profile journey (Phase 7.1). The original bytes are never stored (D5). A second resume into a non-empty profile becomes *update proposals* (Phase 2). |
| Manual entry | **[Built, Phase 2]** | Structured forms for employment, responsibilities, achievements, education, certifications, projects and skills, plus pasted experience text parsed into proposals (D7) |
| Voice | **[Planned, deferred (D7)]** | A transcript will reuse the text proposal pipeline. Voice never bypasses review. |

## 3. Profile review [Built, extended in Phase 2]

The user reviews every draft. They can:
- confirm, correct (append-only history), reject or reinstate a record;
- map a job title to the role taxonomy, or choose it themselves;
- classify evidence into responsibility areas.

Extracted-unverified and user-confirmed records are always distinct, and nothing is confirmed on the user's behalf.

The profile persists in Postgres and is linked to the account. It survives refresh, logout, new sessions, other
devices, target changes and new runs, and is saved as the user acts. Phase 2 adds conflict-safe editing.

## 4. Target selection [Partial]

**Today:** the target is one of nine PM roles.

**Planned:** target seniority, preferred industry, location, internal-mobility preference, openness to intermediary
roles, and optional time or education constraints. Only questions that change the recommendation are asked. Hard
constraints are kept separate from preferences.

**The detailed plan (D45).** The plan explains, for each priority, what to learn, which verified resources are worth
the user's time, how to practise at work or in a labelled personal project, and what to produce, then sequences it
around the goal. A returning user lands on their latest saved plan; earlier versions are kept.

**Planning goal (redesign, D44).** With the target role, the user may say when they'd like to make the move (a
timeframe or a date) and how much time they can give each week. Both are optional and form a planning target, never a
forecast. The roadmap then shows the recommended next move, what to build on and focus on, and four compact stages with
checkpoints. Target companies (below) are retired from the flow.

**Target companies (POC iteration, D42; retired from the flow by D44).** After choosing a role, the user may name companies they would like to work
at (optional, several, searchable; any company can be added as their own unverified choice). They only decide which
employer research the report shows, and never change the plan.

## 5. Career Intelligence Report [Implemented, Phase 5; awaiting review]

**Today (Phase 5):** after target selection the user lands on the report (`#/pathway/<role id>`), built from their
latest saved transition plan; a new plan is saved only when the confirmed profile has changed, or when the user asks.
The earlier results and Career insights screens are retired: no exact counts, no percentages and no browser-side "most
common" route. Covered targets: Product Manager and Senior Product Manager; other targets get an honest "not
available yet" state. Missing information is shown as profile tasks before the plan, never as a gap or a fifth stage.

**The report, top to bottom:**

1. **Above the fold:**
   - current role → target role;
   - recommended direction and why;
   - main strengths and priority gaps;
   - evidence breadth and its source;
   - a preview of all four stages, **including stage 4**;
   - whether alternatives exist.
2. **Pathways:** the primary pathway and genuine alternatives. Each has:
   - a role sequence;
   - personal suitability and evidence breadth as separate attributes;
   - its limitations.
3. **Four stages:** Build foundation → Gain practical experience → Become application-ready → Get into the target role.
   Each stage has:
   - an objective;
   - actions in three kinds: **Build**, **Demonstrate**, **Reposition**;
   - for each action, why it suits this user, the gap it addresses, what to do, the expected deliverable, and the source
     where one applies.
4. **Progressive disclosure:**
   - the capability matrix (Demonstrated, Transferable, Gap, Unknown);
   - supporting career-history evidence;
   - resources;
   - methodology and sources.

## 6. Advisor [Planned, Phase 8; after D1]

A conversational advisor uses approved backend tools only. It can propose profile updates through the same proposal
pipeline, but cannot confirm them. The structured report stays available when the model provider is unavailable.
