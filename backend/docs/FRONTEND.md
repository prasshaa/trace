# Trace frontend (Phase 7.2)

> **Product Phase 5 (2026-11-17).** The Career Intelligence Report (`js/views/report.js`) replaces My career path and
> Career insights in the journey; see "Career Intelligence Report" below. `views/results.js` and `views/insights.js`
> stay in the repository (and their APIs are unchanged) but are no longer routed. The descriptions of those two screens
> further down are historical.

Plain HTML, CSS and JavaScript modules in `backend/web/`, served by the FastAPI application under `/app/`. No framework,
no build step, no CDN at runtime. The only third-party code is the Supabase JS client, vendored and pinned.

## Structure of the product

Trace helps people find the most relevant paths from where they are today to the role they want to reach. There is
**one** journey; there is no sample-profile, demo or fictional-profile option anywhere in the product:

1. Sign in or create an account.
2. Get started: give the explicit personal-analysis permission, then upload your own resume.
3. Review and correct your career profile.
4. How Trace uses your career information (the onboarding data-use notice, once; one Continue button).
5. Choose your target role.
6. Career Intelligence Report: the plan for the chosen target role (Phase 5).
7. Profile settings → Data & privacy, from the header at any time.

| Area | Route | Uses |
|---|---|---|
| Sign in | `#/` | Supabase Auth: Google (shown only when the Auth server reports it enabled; otherwise not shown), email and password, account creation |
| Get started | `#/start` | `GET /config` (`features.real_resume_uploads`). When upload is offered: `GET /real-resumes/status`, personal-analysis permission (`GET/POST /consent/resume-processing`), `POST /real-resumes`. When it is not: a truthful "Resume upload isn't available here" card; nothing is simulated or substituted |
| Profile | `#/profile` | `GET /profile/records`, `GET /current-position`, role matching and choice, optional categorising, confirm / edit / not accurate / restore |
| Data-use notice | `#/data-use` | `GET /sharing` (the notice text and version), `POST /sharing/notice` |
| Target role | `#/target` | `GET /current-position`, `GET /taxonomy/target-roles` |
| Career report | `#/pathway/<role id>` | `GET /current-position`, `GET /transition-plan?target=`, `POST /transition-plan?target=[&scope=portfolio]` |
| Profile settings | `#/settings` | `GET /sharing`, `PUT /sharing` |

Earlier links (`#/intelligence`, `#/insights`, `#/contribution`, `#/results`, `#/setup`) are redirected to their new routes; `#/insights` and `#/intelligence` open the report. Where a
signed-in user lands is decided from API state (profile, consent, records, sharing preference). The data-use notice is
shown after profile review and before the target role, until the user has a sharing preference; Career insights and
Profile settings are reachable before a profile exists. The chosen target role is kept in the current history entry
(`history.state`, see `js/state.js`), so a reload keeps it without browser storage; it is cleared on sign-out. The
header shows no email address or user id.

**Reference source.** The browser never sends or chooses a data source. The server's data mode decides it (see
API.md): fictional test profiles in internal testing (labelled "Internal testing: these patterns come from fictional
test profiles, not from real people's careers"), profiles shared by members in production. When there aren't enough
profiles the page says "Not enough career profiles yet" and shows no statistic; nothing else is loaded instead.

**Profile review.** "N of M details confirmed", "N details remaining", "N responsibilities need categorising". There
is no bulk confirmation: Continue confirms nothing, and unconfirmed details are left out of the career path until the
user confirms them (the API's existing review rules). Categorising responsibilities is optional; without it the
experience comparison says so and links back to the profile.

**My career path.** The heading and label follow the API's fit assessment exactly: "Your recommended pathway" /
"Recommended path" only when the fit tier is `aligned` (a strong fit); any other tier is "A possible path to your
target" with the API's fit label ("Partial match", "Few matches yet" or "Fit could not be established", the last with
"This career move appears in the available profiles, but there isn't enough comparable experience to determine how
closely it matches your profile."). No route: "No reliable pathway is available yet." with the evidence limitation and
nothing invented. A direct move is shown as a direct move. "How your experience compares" has three groups: experience
you already have; common experience not yet shown in your profile ("Not recorded in your profile doesn't mean you lack
the experience."); where we need more information. "What you can do next" keeps career suggestions (the API's
`area_gap` and `route_step` guidance) separate from "Improve your profile comparison" (its `evidence` tasks).

**Career insights.** "Explore career patterns", the four categories as tabs, plainer headings for the transition
metrics ("What roles came immediately before this one?", "Common ways people reached this role", "Earlier roles in
their careers", "Common career moves"). Every bar shows the API's share and "count of denominator"; unavailable
categories are listed compactly with their reason ("Not enough profiles to show … patterns.", "Not recorded in these
profiles: …", withheld figures); methodology, scope, the privacy threshold and limitations are under "About this data".
The browser computes no statistic (enforced by `tests/test_frontend_phase7.py`).

**Profile settings → Data & privacy.** Two mutually exclusive modes, saved with an explicit button: "Use my career
information to improve recommendations for everyone" and "Use my career information only for my recommendations".
The **sharing preference** and the **profile status** are shown separately; "Included" appears only when the API
returns `eligible` AND `included`. Switching to private asks for confirmation (what stops, what stays, what is kept)
and is shown only after the API confirms. When the preference is shared but no explicit permission exists yet (the
onboarding default), the status says sharing hasn't started and the save button reads "Confirm sharing"; see
`SHARING_PREFERENCE.md` for why. A held profile shows "Your career profile is being checked". The page re-checks a few
times (3 s to 21 s apart) while processing, then stops. No review, approval or retry operation exists in the browser.

**Internal testing.** Test accounts get a private profile from an approved synthetic fixture with
`scripts/qa_test_account.py` (local only; acts as the account itself through the normal API), then use exactly the
screens above. No fixture or sample endpoint is called by the frontend.

## Phase 7.2 refinements

- **Target role.** The current role (same canonical role id as `GET /current-position`) is shown disabled with "Your
  current role. You're already in this role. Choose a different role to explore your next move." The API refuses it as
  well; a stored target that has become the current role shows "This is now your current role" with a link to choose
  another. Without an established current role the target page asks the user to confirm it first.
- **Profile review.** "Continue to your target" is enabled only when the API reports a known current position; until
  then it is disabled with "Confirm your current role to continue…" and a "Go to your current job" button that moves
  focus to that job. Other details may stay unconfirmed. The sticky panel is short ("Unconfirmed details won't be used
  in your pathway. You can review them later."), reserves the safe area, and the page's scroll padding keeps focused
  controls clear of it.
- **Wording.** Counts are "This move appears in N fictional test profiles" (internal testing) or "N career profiles"
  (production), from the server's `is_synthetic_test_data`; records are never called people. Counts below the server's
  `sample_caution_below` / `scope.min_target_cohort` carry "Based on a small number of profiles. Treat this as an
  observed pattern, not a prediction of what will happen in your career." Unavailable insight categories use written
  sentences. When the comparison or insights use the wider sample, a note says so with the API's own counts.
- **Comparison layout.** Two content-sized groups (experience you already have; common experience not yet shown, top
  three per category with "Show the full comparison" for the rest) and a compact "Where we need more information"
  strip.
- **Settings.** "Sharing preference: On / Private" and "Profile status: Not shared yet / Processing / Pending review /
  Manual review / Included / Not eligible / Couldn't be processed / Not shared" are separate; the onboarding notice
  (v2) says nothing is shared until sharing is confirmed and the checks pass.
- **Sign-in.** Google is not shown at all unless the Auth server reports it enabled. On mobile the sign-in card comes
  before the three explanatory steps; desktop keeps the two-column layout.
- **Mobile (below 860 px).** A Menu button (`aria-expanded`, `aria-controls`) opens a full-width list of every
  destination; Escape closes it and returns focus; choosing a destination closes it. Below 640 px the journey is one unit
  ("Step 2 of 3 · Target role", a progress bar, a link back). Below 760 px the pathway's section links become a "Jump to
  section" selector and the insight tabs a "Category" selector (desktop tabs unchanged).

## Phase 7.2.1

- **Role confirmation.** A job whose role was matched automatically shows "Matched to X · not confirmed yet" above its
  actions, and its confirm button reads "Confirm job and role": one click records the displayed role as the user's own
  decision and confirms the job. A job already confirmed with an unconfirmed role offers "Confirm role". Low-confidence,
  unresolved and outdated matches keep the existing choose / match-again controls.
- **Current role as a target.** My career path checks the current position before asking for a pathway, so the expected
  case sends no refused request; it returns to target selection with an inline, focused message ("You're already in this
  role. Choose a different role to explore your next move."). If the server still refuses (`409
  target_is_current_role`, for example after a profile change in another tab) the same happens. Any other 409 or failure
  keeps the normal error state.
- **Mobile menu.** A click or tap outside the open menu closes it (one document listener for the app's lifetime; clicks
  inside the header are left to the menu's own controls; focus is not moved).

## Code layout

```
web/index.html            shell: skip link, landmarks, CSS, vendored Supabase client (SRI), module entry
web/css/tokens.css        design tokens (colour, type, space, radius, shadow)
web/css/base.css          reset, typography, header and navigation, shell layout
web/css/components.css    buttons, forms, cards, badges, notices, disclosure, journey, tabs, segmented control,
                          dialog, bars and figures, route track
web/css/views.css         per-screen layout
web/js/main.js            boot, auth state, routing, header navigation
web/js/state.js           the target role in history.state (no browser storage)
web/js/auth.js            Supabase Auth only (sign-in, session, refresh, sign-out, provider availability)
web/js/api.js             the Trace API client (Bearer token, one refresh-and-retry on 401, friendly errors)
web/js/dom.js             safe element builder: text only, never HTML
web/js/ui.js, copy.js     shared components; plain-language copy for API codes and states
web/js/charts.js          bar lists, headline figures, route track
web/js/views/*.js         signin, setup (get started), review, datause (onboarding notice), target, results
                          (my career path), insights (career insights), settings (profile settings)
web/vendor/               supabase-js 2.119.0 UMD build and its MIT licence
```

## Security properties

- No `innerHTML`, `insertAdjacentHTML`, `document.write`, `eval` or inline script/style; API strings are text nodes.
  A strict Content-Security-Policy (`script-src 'self'`) backs this up (see API.md).
- The browser gets only `SUPABASE_URL`, the public anon key, the data mode and server-controlled feature switches
  (`features.real_resume_uploads`) from `/api/v1/config`; it cannot change any of them.
- Supabase is used for Auth only. Every profile, pathway and insight request goes to the Trace API with the user's
  access token; the app never calls the Supabase Data API, Storage or Functions (checked by a test).
- The session is kept by the Supabase SDK in its own storage key (its supported mechanism); the app never copies tokens.
- Vendored client: `@supabase/supabase-js` 2.119.0 from the npm registry tarball, integrity verified against the
  registry's sha512, no source maps, no `eval`; served with a sha384 Subresource Integrity attribute. OSV lists no
  advisories for 2.119.0 of supabase-js, auth-js, postgrest-js, realtime-js, storage-js or functions-js.

## Run locally

```bash
cd data-foundation && supabase status -o env      # SUPABASE_URL (API_URL) and the anon key (ANON_KEY)
cd ../backend
export SUPABASE_URL=http://127.0.0.1:54321 SUPABASE_ANON_KEY=<anon key> TRACE_DEMO_FIXTURES=1
uvicorn app.main:app --port 8000
open http://127.0.0.1:8000/
```

The local consent documents must be installed once (`python -m scripts.demo_setup`, see API.md "Local demo setup").
Email sign-up works on the local stack without email confirmation. To give a test account a profile while resume
upload is off: `QA_TEST_PASSWORD=... python -m scripts.qa_test_account --email <test account> --fixture <n>`.

## Google sign-in (local)

Google sign-in is implemented with Supabase Auth's OAuth provider, PKCE flow, default scopes only (`openid`, `email`,
`profile`; no Gmail, Drive, Calendar or Contacts). It is **disabled** until a Google OAuth client is configured; the
sign-in screen then says so and email and password keep working.

1. **Google Cloud Console** → APIs & Services:
   - OAuth consent screen: User type *External* (Testing). App name "Trace (local)". Scopes: only
     `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile`. Add your Google account as a test user.
   - Credentials → Create credentials → OAuth client ID → *Web application*.
     - Authorised JavaScript origins: `http://127.0.0.1:8000`, `http://localhost:8000`
     - Authorised redirect URI: `http://127.0.0.1:54321/auth/v1/callback` (Supabase Auth's callback, not the app)
   - Keep the client ID and client secret.
2. **Supabase (local)**, in the shell that runs the CLI (never in the backend `.env`, never in the browser):
   ```bash
   export SUPABASE_AUTH_EXTERNAL_GOOGLE_CLIENT_ID=<client id>
   export SUPABASE_AUTH_EXTERNAL_GOOGLE_SECRET=<client secret>
   ```
   In `data-foundation/supabase/config.toml` set `[auth.external.google] enabled = true`. The app's return URL
   (`http://127.0.0.1:8000/app/`) is already in `additional_redirect_urls`.
3. Restart Auth so the configuration is read: `supabase stop && supabase start` (this keeps the local database; do
   not use `supabase db reset`).
4. Open `http://127.0.0.1:8000/`, choose *Continue with Google*. Supabase exchanges the PKCE code and issues its own
   ES256 access token (issuer `.../auth/v1`, audience and role `authenticated`), which is what the API verifies,
   exactly as for email sign-in.

For a hosted project later: set the Site URL and redirect URLs in the Supabase dashboard, use
`https://<project-ref>.supabase.co/auth/v1/callback` as the Google redirect URI, and keep the secret in the dashboard.

## Verified and not verified

Phase 7.2, headless Chrome, 83 checks at 1366×900, 768×1024 and 360×800, 390×844, 430×932: Google hidden when
unconfigured; sign-in card before the explanation on phones; Continue disabled without a starting point, "Go to your
current job" moves focus there, Continue enabled after confirming only the current job; the v2 notice; the current role
disabled as a target (desktop and phones); a self-target URL and a stored target that became the current role both
explained with no route drawn; insights still explore the current role; counts in fictional test profiles, the
small-sample caution, the wider-sample note; written unavailable sentences; settings "On" / "Not shared yet" with the
explanation; on phones: the Menu exposes all five destinations within the width, highlights the active one, Escape
closes it and returns focus; the compact stepper; a sticky panel under a quarter of the screen with the last profile
control reachable and not under it; target options within the width; "Jump to section" with all four sections and the
heading landing below the sticky bars; "Category" with all four categories; settings options at least 44 px tall; the
private dialog within the viewport, Escape closes it and focus returns to Save; no horizontal overflow anywhere; no
console errors or CSP violations other than the browser's log of the expected 409 refusals.
Not verified: a real screen reader (labels, roles and states were checked through Playwright's accessibility queries,
not VoiceOver or NVDA); the Google round trip; real uploads.

Phase 7.1 (earlier): Verified in headless Chrome (Phase 7.1; desktop 1366×900 and mobile 390×844), 47 checks, against the local stack with
`TRACE_REAL_RESUME_UPLOADS=0`: approved sign-in copy and footer; Google genuinely disabled with a one-line explanation
(provider not configured); email sign-up; Get started states that upload is unavailable, offers no file input, no
sample and no demo; no email or user id in the header; the QA tool prepares the test account through the normal API
and prints no secret; review wording ("N of M details confirmed", "details remaining", "responsibilities need
categorising"), no bulk confirmation, a correction rendered as text (no script runs); the data-use notice after review
and before the target (target and path redirect to it), one button and no checkbox, no anonymity promise; target
copy; a not-established fit shown as "A possible path to your target" without "Recommended path"; a strong fit shown
as "Your recommended pathway" / "Recommended path"; a target with no route shown as "No reliable pathway is available
yet." with nothing invented; the internal-testing source note; the four-section pathway hierarchy; plainer insight
headings and no source chooser; settings: exactly two modes, the onboarding default selected, preference and status
shown separately, never "Included" for a test profile, "Confirm sharing" while no explicit permission exists; the
private dialog (Escape cancels and focus returns to Save), private confirmed only after the API, still private after
a refresh, career path still available; back to shared needs the explicit Save and is re-processed ("Not eligible"
for a test profile); visible focus on the mode options; no horizontal overflow on mobile; touch targets of at least
24 px on settings; no console errors and no CSP violations.

**Not verified:** the Google OAuth round trip (no Google OAuth client is configured on this machine); the own-resume
screens against a server with real uploads enabled (deliberately not enabled); the production data mode in a browser
(covered by API tests only); a screen reader (labels, roles and live regions were checked in code and through the
accessibility tree that Playwright's role queries use, not with VoiceOver or NVDA).

## Career Intelligence Report (product Phase 5)

`js/views/report.js`, at `#/pathway/<role id>`. It presents one saved transition plan and decides nothing itself.

**Loading.** The current position is checked first (the current role as a target returns to the target page). Then the
latest saved plan is read; a new one is saved (`POST /transition-plan`) only when none exists or its
`versions.profile_version` differs from `GET /profile/records`'s `profile_version`. The user's stated portfolio scope
and any stored contexts carry over. "Update report" saves a new run on request. A 422 `target_not_covered` gives an
honest "not available for this role yet" state.

**Sections, top to bottom:**
1. a fictional-test-data notice whenever the reference data mode isn't empirical;
2. the summary (from the server's `summary`): current → target, the decision and its stored reason, the four stage
   previews (stage 4 always), strengths (decision rights first) and priority areas (Gap, Transferable and Unknown apart);
3. resolve-first profile tasks (stage 0 actions), never shown as a stage;
4. the Senior PM portfolio-scope question (`target_level == "senior_pm"` only);
5. routes: selected (and tied), other routes, then those not suited now (collapsed). Each shows why, what it develops
   fully and partly (what remains and what to seek), the way in, moves and trade-offs, limitations and sources;
6. the four stages of the selected route (or the direct route when resolving first), with action labels and cited
   sources; stage 4 states whether a verified current posting exists;
7. capability, grouped by state, with the reason for each Unknown;
8. employer criteria: current openings, those needing re-checking, and past postings as dated examples; required and
   preferred criteria apart;
9. comparable career histories with D4 breadth labels (never counts), and method, sources and versions.

**Never:** counts, percentages, scores, client-side ordering or a "most common" route. The words for codes live in
`copy.js` (`ACTION_WHERE` and `ACTION_LABEL` repeat the action pack exactly; a test checks them).

## POC iteration: target companies and corrections

- **Target step.** Once a role is chosen, an optional "Where would you like to work?" card (`js/companies.js`): an
  accessible combobox (typing, a click or ArrowDown opens suggestions; never focus alone), suggestions from Trace's
  researched employers only, "Add … as your own choice" for anything else (shown as unverified), removable chips, saved
  on every change per target role, and "Continue without selecting companies". Roles without a report say so.
- **Report.** Canonical stage names with subtitles; the bridge summary comes from the route's own development data;
  "Your target companies" shows only chosen companies (posting by posting; "Not assessed yet" otherwise); Stage 4 lists
  verified current openings at chosen companies only, and replaces the stored employer listing in the two Stage 4
  actions that name every researched employer; comparable career histories show the agreed message unless empirical
  routes pass the privacy minimum; each stage has one numbered, de-duplicated source list, and actions cite its numbers.
- **Plan reuse.** The report reuses the latest plan only when its `freshness.current` is true.

## Product redesign: the career transition roadmap (D44)

- **Target step:** role, then an optional goal card (`js/goal.js`): a desired timeframe or a specific date, and optional
  weekly time. Saved per role when the user continues (`PUT /transition-goal`). The company selector is retired from the
  flow (`js/companies.js` is kept, unused).
- **Roadmap (`views/report.js`):** header with the goal ("a planning goal, not a guarantee or forecast"); recommended
  next move (headline, route line, up to two reasons, what the route offers, the trade-off, "Why this path?" and "Other
  possible routes" collapsed); your experience (build on, focus next, related experience with what it doesn't
  establish, a one-line not-assessed note); the four stages with highlighted actions, "How to do this" and "More for this
  stage" collapsed, a checkpoint with an optional user date, and collapsed stage sources; a compact career-history note;
  sources and method collapsed. No employer cards, no posting comparisons, no capability badges, no scores.

## Comprehensive redesign: the detailed career plan (D45)

Order: goal and saved-plan status (with change target or timeframe / edit profile / update plan) → recommended next move
→ experience you can build on (themes; records behind "See the experience in your profile") → what you need to develop →
your learning and development plan (per priority: learn, why, how, a featured verified resource and other options,
practice steps, what to produce, other ways to build it) → transition roadmap (sequence panel, four stages with
learning links, repositioning and hiring guidance, actions, checkpoints) → comparable career histories → sources
(role sources and learning resources, collapsed). After sign-in, a user with a saved plan lands on it.

## The career-planning workspace (frontend pass, 2026-11-18)

`#/pathway/<role>/<section>` renders a five-section workspace (`views/report.js` shell, `views/workspace/*.js`,
`css/workspace.css`): Overview, My Roadmap, Learn & Develop, Experience & Reposition, Profile & evidence. A persistent
left navigation on desktop becomes a section picker below 900px. Data comes only from existing endpoints: the saved
plan (`/transition-plan`), its roadmap projection (`/transition-plan/{id}/roadmap`), the goal (`/transition-goal`, the
goal editor saves through it) and the profile records (`/profile/records`). The loaded plan is reused across sections
while the signed-in user and the confirmed profile are unchanged; a goal change on the Target step refreshes the
roadmap. Nothing is stored locally as if saved, and no illustrative data is shown.

Not available (no backend support yet): plan history (no list-of-runs endpoint), per-action effort estimates, resource
suitability by location, and changing career background from the goal editor (it links to the profile instead).

**Refinement pass (2026-11-18).** Saved-plan status and "Refresh plan" (saves a new plan version from the current confirmed
profile; the earlier one is kept) live once, in the workspace top bar; the breadcrumb is hidden on mobile where the section
picker names the page. Overview is a dashboard (recommendation with what it could help develop, what isn't established and
the trade-off; a goal card sized to its content; experience cards laid out by count). My Roadmap uses selectable roles with
one shared detail panel, compares direct and bridge routes only when the plan holds both, and keeps action detail behind
"Details". Learn & Develop follows Understand, Learn, Apply. Experience & Reposition gives shared guidance once, a
three-step progression and a later, truthful-description section. Profile & evidence adds an evidence map built from the
plan's own data and shows each related inference with its record and what it doesn't establish.

## Career Origins & Transitions (supporting view, D46)

`workspace/origins.js` at `#/pathway/<target>/origins` is reached from a card on My Roadmap. It is shown only when
`/api/v1/career-origins` answers; a 404 hides the card, and the route falls back to the roadmap. It renders the API's
counts, shares, medians, flags and bands as given, never computing them. Bars share one scale through a fixed value
column. In-page jumps use buttons, never hash anchors. Observed findings ("not about you") stay visually separate from
the user's plan ("personal"). Every chart has a "Show as a table" disclosure built from the same API objects and text
helpers. The header chip reads "Internal preview". Nothing names the research source, and nothing is written to
browser storage.

## Resume-first onboarding (D47)

**Get Started** (`views/setup.js`): one action, "Upload your resume" (PDF or DOCX).
- It covers consent first, then the upload, with a "What happens to your file" disclosure that matches the tested
  lifecycle.
- The old Add a job, Add a qualification, Add a certification or project, Add a skill and Paste your experience entry
  points are gone from onboarding. The profile pages keep editing.
- Leftovers of an earlier attempt can be cleared with **Start over**, after a confirmation.

**Review your profile** (`views/import.js`, `#/import`): one page with work experience, education, skills by category,
and certifications and projects, plus changes to existing records in update mode.
- Fields can be edited, and any item can be removed and restored.
- Skills can be renamed, or removed with a keyboard-accessible ×.
- The most recent job asks for its employment type and closest Trace role; both are required and never inferred.
- One action, **Confirm profile and continue**, then moves to the data-use notice and target role.
- **Replace resume** and **Discard this import and start over** both ask first.

**Parser 2.1 / D48 additions:**
- "Text we couldn't place" shows each `unresolved` item with the warning "We found text that may contain another role.
  Please review it before confirming your profile." **Add as a role** opens a form (job title and at least the start year
  are required). **Add a role that's missing** does the same without a source. Text not added isn't saved.
- In update mode the server's `status` is shown: "Already in your profile" (compact, nothing changes) and "Different dates
  from your profile" (kept: a correction with history; removed: the profile stays as it is).
- The disclosure ("What happens to your file and your details") states that a private, de-identified draft is saved
  before confirmation, survives reload and sign-out, and has no automatic deletion date. **Read the full privacy notice**
  opens the notice in a modal `<dialog>` (`ui.infoDialog`): Esc or Close returns focus, and a chosen file is kept.

**Phase 4 (D49):**
- **One role card** (`roleCard`) for found and added roles, with the same order and markers on every card. Fields:
  - Job title *, Employer *;
  - Employment type *: the arrangement. For founder, freelance or part-time roles, "This was my main, full-time work";
  - "This ran alongside my main job";
  - Start month and year *;
  - "I currently work here", which hides the End month and year *;
  - Description * (with the minimum stated), then the role's lines.
- **"Closest Trace role"** appears only on the card the server names as the starting role. `POST …/check` runs as the
  user edits, so the control moves to a later added role and back. The hint explains that it maps the most recent job to
  Trace's role framework and doesn't change the job title.
- **Errors:** a `review_incomplete` response marks each named control (`aria-invalid`, a message linked with
  `aria-describedby`) and lists every problem in an alert summary whose items focus their control. The first invalid
  field receives focus. Edits are kept.
- **Unexpected errors** show the support reference.
- **Unplaced text:** "Add as a role" creates a prefilled card in Work experience. "Leave this out" marks the text as not
  saved; it never blocks confirmation.
- **A role the parser couldn't title** shows "From your resume: …" with its source line.

**Routing:** an open import routes to `#/import` until it is confirmed or discarded. Only reviewed records count as
"has records".

**Landing copy (Phase 4):** "Find your path to your target role.", with "Add your current experience, see what you need to
build next, and the steps that can move your career toward your goal." The tagline "Upload your resume. Choose your
target. See your path." is the three numbered points. The page `<title>` and `meta description` match.

**Google sign-in:** this is unchanged. The button stays hidden until the Auth server reports Google enabled (see
"Google sign-in (local)" above). Before enabling it beyond local testing:
- turn on email confirmation, so an unverified password account can't be linked to a Google identity for the same
  address (account takeover);
- test linking for an existing email address.

## Phase 5: the research-led plan (D50, D51)

Overview A–E (starting point; where people with your starting point moved; routes; how this relates to your experience;
what to do next), each block labelled as profile, research or recommendation. Profile & evidence lists every capability
with the lines behind it. Target page: any role, plus "Where you'd like to move". Review and profile pages: one employment
type, "I currently work here" only without an end date, education dates, optional role matches with labelled title
suggestions, and an editor for dates and types. Browser check: `e2e_phase5.py` (63/63, desktop and mobile).
