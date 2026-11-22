# Trace design system

**Implementation:** `backend/web/css/tokens.css` defines the tokens; `components.css` and `views.css` style components
and views; the JS modules live in `backend/web/js/`. Technical notes are in `backend/docs/FRONTEND.md`.

**Character:** editorial and restrained:
- warm paper, ink text, and one accent (deep teal) for the primary action, the user's route and their own data;
- every text and surface pair meets WCAG AA;
- status colours are always paired with a text label;
- vanilla HTML, CSS and JS with no framework or build step. That stays true unless a clear need is demonstrated.

## Rules for the Career Intelligence Report (Phase 5)

1. **The browser renders; it calculates nothing.** No counts, percentages, evidence labels, rankings or "most common"
   selection on the client. Every value comes from the API.
2. **Above the fold:**
   - current role → target role;
   - recommended direction and why;
   - key strengths and priority gaps;
   - evidence breadth and its source;
   - all four stages, **including stage 4**;
   - whether alternatives exist.
3. **Progressive disclosure:** actions, career-history examples, resources and methodology sit below, in expandable
   sections.
4. **Mobile-first:** no horizontal overflow, and text alternatives for every visual.

## Reusable components (planned)

| Component | Purpose | Form |
|---|---|---|
| Intelligence summary | Opening answer | Compact header block |
| Journey strip | Current role → (intermediary) → target | Progression strip |
| Capability matrix | Demonstrated / Transferable / Gap / Unknown per requirement | Compact table; state shown as text plus colour |
| Priority-gap indicator | The few gaps that matter most | Short list |
| Pathway overview | Four stages at a glance | Timeline of stages |
| Route comparison cards | Primary vs alternatives and their trade-offs | Branching cards |
| Personal-fit marker | "Recommended for your profile", "Worth exploring", "Alternative to consider" | Text badge |
| Evidence badge | Limited, Growing, Broad, Extensive observed evidence; Synthetic test data | Separate text badge, never merged with fit |
| Career-history evidence card | What comparable histories show (privacy-safe) | Card |
| Action milestone | Build, Demonstrate or Reposition action with its deliverable | List item |
| Resource item | A verified resource with its source | Link with publisher and date |
| Sources and methodology | How the result was built | Disclosure section |
| Low-evidence state | A profile-led pathway with no comparable histories | Neutral note, not a warning banner |

## Avoid

- Readiness percentages or success probabilities.
- Radar charts of unvalidated scores.
- Decorative charts, or pie charts where a comparison would be clearer.
- Long text-only sections and repeated cards.
- Desktop-only layouts.
- Small exact profile counts.
