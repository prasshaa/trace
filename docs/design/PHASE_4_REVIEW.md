# Phase 4 review: route selection, content and persistence (for decision; nothing implemented)

**Date:** 2026-11-16.
**Status:** review only. No code, database or content was changed for this document.

**Resolution (2026-11-16):** decisions D31–D38 resolved O1–O4 and the findings. The remediation is implemented as migrations v2.24.0–v2.27.0, content v2 and engine `transition-planner-2.0.0` (`backend/docs/TRANSITION_PLAN.md`). The snapshot cleanup was done on 2026-11-17 (D39; `SNAPSHOT_CLEANUP_PROPOSAL.md`). Phase 4 closure: D39–D41, pm-eligibility-v3 (E8 implemented), and API-level tests for the three Product Analyst comparisons (A5).
**Scope:** the seven items requested after the Phase 4 report.

Findings are marked:
- **[I]**: needs an implementation change (code or migration, with tests);
- **[C]**: needs a content correction (reviewed JSON, regenerated migration);
- **[D]**: documentation only.

The summary table at the end lists every finding.

Current-engine outcomes quoted below were computed with the existing pure functions on invented profiles (scratch
script, no database writes).

---

## 1. Revised route-selection specification

### 1a. What is wrong with the current rules

| Current rule | Problem |
|---|---|
| "Limited" when Gaps cover at least half of the core requirements | A percentage; treats C1–C6 as equally important |
| A bridge qualifies when it closes at least half of the Gaps | A percentage; counts gaps instead of asking which gaps and why |
| R2: Senior PM target with no reviewed PM-family role means Product Manager first | Decides on job title. A founder with every requirement Demonstrated, and a Product Owner who owned a roadmap and outcomes, are both sent through "Product Manager first" today (scenarios S3 and S4 below) |
| Catalogue "develops" lists | Partly overstated (§2). For example, the source places strategic ownership at "awareness" for an Associate PM, but the catalogue says APM fully develops C2 |

### 1b. The idea behind the revision

A bridge adds a move. It is worth that move only when it develops a capability that the user cannot show without
holding a product role, and the direct route would leave that capability unshown.

Some requirements can be demonstrated from many roles; others need decision rights over a product:
- **Practice requirements** can be demonstrated from many roles. Business analysts write requirements; engineers run
  iterative delivery; researchers and analysts learn about users; anyone can manage stakeholders. Gaps here are closed
  by stage 2 actions on any route.
- **Decision-rights requirements** need authority over a product's direction or outcomes. A bridge is considered only
  for these.

This is a per-requirement **attribute in the approved content** (versioned, reviewable). It is not a weight, a count or
a rule fixed in code.

**Proposed attribute: "needs product decision rights to demonstrate"** (for your approval):

| Requirement | Flag | Rationale |
|---|---|---|
| C1 Understand users and set priorities from their needs | No | Discovery is routinely done by researchers, analysts, designers and business analysts |
| C2 Product vision, strategy and roadmap | **Yes** | Needs authority over a product's direction. F1: "strategic ownership" is "awareness" at associate level, "working" at PM |
| C3 Requirements and priorities | No | Business analysts, Product Owners and engineers do it |
| C4 Outcome metrics and data-informed decisions | **Yes** | Defining a metric needs no decision rights, but deciding from it for a product does. F1: "managing product outcomes" is "awareness" at associate level, "working" at PM |
| C5 Cross-functional collaboration | No | Most roles do it |
| C6 Iterative delivery | No | Engineering and delivery roles do it |
| X1–X7 (context requirements) | No | Each is demonstrable from a specialist role (analytics, marketing, engineering, compliance, finance, data, AI engineering) |
| L1 Several products, or a complex or high-risk product | **Yes** | Portfolio scope is held, not practised |
| L2 Coaching, L3 executive communication, L4 cross-team planning | No | Leads, managers and programme roles do these |

**On the rejected C1/C2/C4 rule:** this is not that rule.
- C1 is out, with a stated reason.
- The set is data, owned by you, and can be changed per pack version.
- The engine still checks which specific gaps a specific bridge develops.

The set happens to resemble that rule, and that resemblance is a decision for you (O1 below).

### 1c. The revised rule sequence

The rules are deterministic, requirement-level and free of counts.

| Step | Rule |
|---|---|
| P0 Material requirements | The requirements that apply: core, the user's chosen contexts and, for Senior PM, the differentiators. Unchanged |
| P1 Resolve first | If a material **decision-rights** requirement is Unknown for a reason the user can resolve, no primary route is chosen. The plan names the records to review. Unknown **practice** requirements produce resolve-first items but don't block the choice, because they cannot change it |
| P2 Decision-rights gaps (G) | Material decision-rights requirements in state Gap. **For a Senior PM target, also those only Transferable.** Source: F1 expects "practitioner" in strategic ownership and managing outcomes at senior level, against "working" at PM. F1 also says a PM role "can be an entry-level role for new product managers who have been working in other digital roles", so Transferable is acceptable at PM entry |
| P3 Direct is supported | If G is empty, direct is the primary route. Practice gaps become stage 1–2 actions on it |
| P4 Bridge candidates | A catalogue bridge for this target, not the user's current role, whose entry is **evidenced** (the user shows an entry requirement the source states) or **source-stated** (the source itself describes the role as an entry route). A bridge whose entry is only a **curated hypothesis** is never primary; it is listed as an alternative, with the label |
| P5 Primary-eligible | A candidate is eligible if it **fully develops** at least one requirement in G. **Or**, if the user has **no confirmed role in a digital role family** (F1's "other digital roles"), it **partly develops every** requirement in G |
| P6 Choose without counting | Among eligible bridges, keep those not dominated. B1 dominates B2 if, for every requirement in G, B1 develops it at least as fully (full > partly > none), and strictly more fully for at least one. Ties on G are broken the same way on the practice gaps. One survivor is primary; several are `tied_primary`, each with the gaps it develops |
| P7 No eligible bridge | Direct is primary, labelled **conditional**: it names the decision-rights gaps still to show, and the stage 2 actions for showing them. Bridges are listed as alternatives, with what each develops and why none is primary |
| P8 Unchanged | Evidence breadth and employer eligibility are never inputs. Observed routes are shown, not evaluated. The number of moves is shown as a trade-off |

**The level step for Senior PM.** The Product Manager bridge is evaluated by the same rules. It is chosen only when it
fully develops a decision-rights gap: C2 or C4, either missing or only Transferable at senior level. Its "entry" is
the user's own Product Manager plan. Holding or not holding a PM job title is never the reason.

**Inputs to define for the "digital role" test:**
- **Digital, from F1's role list:** business analysis, software engineering, QA, data analytics, data science, product
  analytics, design and UX, product ownership, project and programme management, engineering management.
- **Not digital:** customer success, customer support, sales, marketing, operations, finance, HR, consulting.
- **Entrepreneurship** is judged by evidence, never by the label. The mapping is curated and needs your approval (O3).

### 1d. Examples (current engine compared with the proposal)

The proposal column assumes the catalogue corrections in §2 (APM develops C2 and C4 only partly; Product Owner entry
is a hypothesis).

| Profile (target) | Key states | Current | Proposed | Why |
|---|---|---|---|---|
| A Customer success manager (PM, commercial) | C1, C5, C6 Transferable; C2, C3, C4 Gap | R5: APM | **APM**: P5, non-digital background, partly develops both C2 and C4 | A non-digital background has no source-stated direct entry; APM partly develops both decision-rights gaps; Product Analyst and Product Owner entry not evidenced |
| B Data scientist (PM, data product) | C2 Transferable; C3, C5 Demonstrated; C1, C4 Gap | R4: direct | **Product Analyst**: P5, fully develops C4 | G = {C4}. The Product Analyst fully develops outcome metrics; the APM only partly does. Entry evidenced (data science role) |
| C Broad business analyst (PM) | C2 Transferable; the rest Demonstrated | R3: direct | **Direct** (P3) | G is empty |
| D Software engineer (PM, platform) | C1, C2, C4 Gap; C3, C5 Transferable | R5: APM | **Direct, conditional** (P7). APM and Product Owner are alternatives | Digital background: an APM that only partly develops C2 and C4 isn't enough; the Product Analyst develops C4 but entry isn't evidenced; Product Owner entry is a hypothesis |
| E Business analyst (V2) | C2, C6 Gap; C4 Demonstrated | R4: direct | **Direct, conditional** (P7) | G = {C2}; no bridge fully develops C2 below PM level |
| S1 Software engineer (Senior PM) | C2, C4, L1 Gap | R2: PM first | **PM first** (P5, P6) | The PM role fully develops C2 and C4 |
| S2 Product Owner: backlog, discovery, coaching (Senior PM) | C2 Transferable, C4 Gap | R2: PM first | **PM first** | At senior level a Transferable C2 is not enough; the PM role fully develops C2 and C4 |
| S3 Product Owner who owned a roadmap and outcomes (Senior PM) | C1–C6 Demonstrated; L1 Transferable | R2: PM first | **Direct to Senior PM, conditional on L1** | G = {L1}; the PM role doesn't develop portfolio scope, so no extra move is justified |
| S4 Founder: multi-product strategy, outcomes, board (Senior PM) | Every requirement Demonstrated | R2: PM first | **Direct to Senior PM** | G is empty. The title was never the reason |

S1–S4 answer item 2: a PM intermediary appears when evidence of strategy or outcome ownership is missing (S1, S2),
not when the PM title is missing (S3, S4). Employer eligibility stays separate. Senior PM postings asking for years of
"product management" are shown per employer. With finding E4 implemented, Product Owner or founder years read as
**unclear**, not "does not meet".

### 1e. Open decisions

| ID | Question | Recommendation |
|---|---|---|
| O1 | Approve the decision-rights flag (C2, C4, L1) as content | Approve. It is reviewable per pack version |
| O2 | P5: may a bridge that only *partly* develops the gaps be primary for non-digital backgrounds? | Yes (profile A). Otherwise APM is never primary for a PM target, because its source places C2 and C4 at "awareness" |
| O3 | Approve the digital / non-digital family mapping, curated from F1's role list | Approve, labelled curated |
| O4 | Product Owner entry from business analysis: keep it as a labelled hypothesis (alternative only)? | Yes, until a verified source supports it (§2) |

---

## 2. Bridge-role content review

**Legend:**
- **Source fact:** stated by the cited source.
- **Curated reading:** Trace's mapping of source facts to pack requirements; reviewable.
- **Hypothesis:** not supported by any cited source.

### Associate Product Manager (bridge to Product Manager)

| Item | Content | Status |
|---|---|---|
| Definition and responsibilities | Works with PMs to advocate for user needs and prioritise delivery of greatest value; manages a smaller product with support, or a subset of features; learns PM approaches and methods | Source fact (F1, UK DDaT framework, OGL v3.0) |
| Skill levels | Working: agile and lean, user-centred insight, product management, stakeholder relationships. Awareness: value for money, life cycle, managing outcomes, product leadership, strategic ownership | Source fact (F1) |
| Supports the entry route? | Partly. F1 says the level "can be an entry-level role for civil servants who are looking to move into a digital role", and mentions emerging-talent schemes. That is UK public-sector context. Generalising to all employers is a **hypothesis**, labelled "source-stated, public-sector context" |  |
| Leads to the target? | Yes: F1's level ladder (associate, PM, senior, lead, head) | Source fact |
| Develops fully | C1 (user-centred insight, working), C3 (product management, working), C5 (stakeholder, working), C6 (agile, working) | Curated reading |
| Develops partly | C2 (strategic ownership is awareness; roadmapping methods are working), C4 (managing outcomes is awareness), X5 (value for money is awareness), L2 (product leadership is awareness) | Curated reading. **[C] The catalogue says "develops C2" in full; it should say partly. X5 and L2 are missing from "partly"** |
| Gaps it can credibly close | Practice gaps C1, C3, C5, C6 fully; decision-rights gaps C2 and C4 partly (supervised) | |
| Suitable backgrounds | People new to digital or product work. F1 names business analyst and delivery roles as sharing PM skills | Source fact for shared skills; suitability is a curated reading |
| Trade-off | A junior level; a step down in level for experienced people | Curated note |

### Product Analyst (bridge to Product Manager)

| Item | Content | Status |
|---|---|---|
| Definition and responsibilities | Strategic and ad-hoc analysis for PMs, engineers and designers; technical owner of product KPIs; dashboards and metrics that define success; opportunities in adoption, retention, engagement and monetisation; designing and evaluating A/B tests | Source fact (B1, GitLab Product Analyst job family, CC BY-SA 4.0). **One employer's definition** |
| Entry requirements | 3+ years of data analysis; 2+ years on feature and usage metrics or A/B testing; 1+ year designing experiments; experience supporting a product team; advanced SQL; BI dashboards | Source fact, **employer-specific** (GitLab only) |
| Supports the entry route? | It describes GitLab's own requirements. It doesn't say who else can enter. Entry from data analytics and data science roles follows directly from the stated requirements; entry from other backgrounds is a hypothesis |  |
| Leads to the target? | **Not stated by the source.** Product Analyst to PM is a **curated hypothesis** (no cited source describes the move) | **[C] Label it** |
| Develops fully | C4 (owns KPI definition and evaluates experiments; decisions stay with the PM, so this is a curated reading), X1 (hands-on analytics), C1 through data (user journeys, segmentation), C5 (supports product teams) | Curated reading |
| Develops partly | X5 (monetisation analysis) | Curated reading |
| Does not develop | C2, C3, C6 | Curated reading |
| Gaps it can credibly close | C4 and X1 for someone without product-metric evidence; C1 through data | |
| Suitable backgrounds | Data analytics, data science, business intelligence, or anyone with hands-on analytics | |

**Is entry too narrow?** The current entry signals are a data-family role, or X1 Demonstrated or Transferable. That is
already broader than GitLab's statement, because SQL or Python skills count. The narrowness is not the reason the
Product Analyst rarely wins (§6). Widening entry to roles without analytics would contradict the only source. Keep it.

### Product Owner (bridge to Product Manager)

| Item | Content | Status |
|---|---|---|
| Definition and responsibilities | Accountable for maximising the value of the product from the team's work; develops and communicates the Product Goal; creates, communicates and orders backlog items; keeps the backlog transparent; one person, not a committee | Source fact (B2, Scrum Guide 2020, CC BY-SA 4.0). **An accountability within Scrum, not a job title** |
| Entry requirements | **None stated.** The guide states no requirements or certifications | Source fact (absence) |
| Supports the entry route? | **No.** Business analyst to Product Owner is a **curated hypothesis**. Entry signals (business analysis history or C3 shown) are Trace's assumption | **[C] Label it; [I] treat it as hypothesis entry (alternative only, P4)** |
| Leads to the target? | **Not stated.** Product Owner to PM is a **curated hypothesis**. Employers use the title in very different ways | **[C] Label it** |
| Develops fully | C3 (backlog items and ordering), C5 (stakeholders must convince the Product Owner), C6 (iterative delivery within Scrum) | Curated reading |
| Develops partly | C2 (the Product Goal is direction for one product, not strategy) | Curated reading |
| Does not develop | C1 and C4: not part of the accountability as defined | Curated reading |
| Gaps it can credibly close | Practice gaps C3, C5, C6; C2 only partly | |
| Suitable backgrounds | Hypothesis: business analysis, delivery, engineering | |

### Product Manager (bridge to Senior Product Manager)

| Item | Content | Status |
|---|---|---|
| Definition and responsibilities | Leads a product through its life cycle; supports senior and lead PMs; prioritises work and measures outcomes | Source fact (F1); P3 level in F4 |
| Supports the entry route? | Entry is the user's own Product Manager plan; nothing to source |  |
| Leads to the target? | Yes: F1 levels PM then senior PM; F4 levels P3 then P4 | Source fact |
| Develops fully | C1–C6 at PM level (F1: practitioner in user insight, product management, stakeholder and agile; working in outcomes and strategic ownership) | Curated reading of source levels |
| Develops partly | L2 (product leadership at working level), L3, L4 | Curated reading. **[C] Add L2 to "partly"** |
| Does not develop | L1 (portfolio scope comes with the senior level) | |
| When it is the right bridge | Decision-rights evidence (C2, C4) is missing, or only Transferable for a senior target. Never because of a missing title | **[I] Replaces R2** |

**Program Manager:** not a bridge. The database refuses it, and nothing in the review changes that.

---

## 3. Action-template review (all 49)

Each action's *why for you* is written at instantiation from the user's own assessment:

| Kind | How the rationale is personalised |
|---|---|
| build | "<requirement> is a gap: your reviewed, categorised experience doesn't show it." On a bridge route, adds that the bridge develops it day to day |
| demonstrate | Gap: "Employers look for real evidence of <requirement>; none of your reviewed experience shows it yet." Transferable: quotes the user's line and the curated transfer rationale |
| reposition | Quotes the user's own confirmed line (Demonstrated or Transferable) |
| resolve | Names the requirements the records block, and lists the records |
| apply | Names the target and, where relevant, the user's shown requirements, gaps or postings with their outcomes |

### Build (stage 1) (17)

| Template | Intended state | Requirement | Objective | Action | Deliverable | Where | Sources | Review |
|---|---|---|---|---|---|---|---|---|
| `build.C1` | Gap | C1 Understand users and customers and set priorities from their needs | Understand how product teams learn what users need | Study how the cited frameworks describe user-centred insight, then plan five short interviews with users or internal customers of something you work on, with the questions you would ask and what decision each answer could change. | A one-page research plan: who you will speak to, your questions, and the decisions they inform | self_directed_study, current_job | pm-v1:F1 | T1: label deliverable as a learning exercise; the planned interviews happen only in Demonstrate |
| `build.C2` | Gap | C2 Product vision, strategy and roadmap | Understand how a product direction becomes a sequenced roadmap | Study how the cited frameworks describe roadmapping and strategic ownership, then write a short product direction for a product you know: the problem, who it is for, and three outcomes in priority order. | A one-page product direction with a sequenced, outcome-based roadmap | self_directed_study | pm-v1:F1, pm-v1:F4 | T1: label deliverable as a learning exercise |
| `build.C3` | Gap | C3 Turn problems into requirements and priorities | Learn to turn a problem into clear requirements and priorities | Take a problem you have seen at work and write it up as a problem statement, user stories with acceptance criteria, and a short reason for their order. | A written problem statement with prioritised user stories and acceptance criteria | self_directed_study, current_job | pm-v1:F1, pm-bridges-v1:B2 | T1: label deliverable as a learning exercise |
| `build.C4` | Gap | C4 Define outcome metrics and make data-informed decisions | Understand how product outcomes are defined and measured | Study how the cited frameworks describe managing product outcomes, then pick a feature you know and define its outcome, one success metric, its baseline and how you would read the result. | A one-page metric definition: outcome, metric, baseline and decision rule | self_directed_study | pm-v1:F1 | T1: label deliverable as a learning exercise |
| `build.C5` | Gap | C5 Cross-functional collaboration and stakeholder communication | Understand how product work aligns people with different goals | Map the stakeholders of a product or project you know: what each needs, where they disagree, and how a decision between them should be made and communicated. | A stakeholder map with a communication plan for one contested decision | self_directed_study, current_job | pm-v1:F1 | T1: label deliverable as a learning exercise |
| `build.C6` | Gap | C6 Iterative delivery across the product lifecycle | Understand iterative delivery across a product's life cycle | Study how the cited sources describe agile and lean practice and life-cycle management, then outline how one feature you know could have shipped in smaller increments, each with something to learn. | An increment plan for one feature with what each increment tests | self_directed_study | pm-v1:F1, pm-bridges-v1:B2 | T1: label deliverable as a learning exercise |
| `build.X1` | Gap | X1 Hands-on analytics (SQL, dashboards, experimentation tooling) | Build hands-on product analytics skills | Using a public or anonymised dataset, write the queries for a funnel and a retention view, and explain what each shows and what you would test next. | A short analysis with queries, two charts and the next experiment you would run | self_directed_study, external_project | pm-bridges-v1:B1 | T1: label deliverable as a learning exercise |
| `build.X2` | Gap | X2 Owning go-to-market | Understand how products are positioned and launched | For a product you know, write who it is for, the problem it solves, how it is priced or sold, and how a launch would reach its first users. | A one-page positioning and launch plan | self_directed_study | (none) | T1: label deliverable as a learning exercise; T6: no source (Trace-curated) |
| `build.X3` | Gap | X3 Technical depth: APIs, platform and data architecture, security | Build working knowledge of APIs, platforms and data architecture | Read the public API documentation of a product you use, and write how a developer would integrate with it, its main limits and one improvement you would prioritise. | A short API review with an improvement and the reason for it | self_directed_study | (none) | T1: label deliverable as a learning exercise; T6: no source (Trace-curated) |
| `build.X4` | Gap | X4 Domain and regulatory knowledge | Build domain and regulatory knowledge for your chosen context | Identify the regulator and the main rules that apply to products in your chosen domain, and summarise how they constrain one product decision. | A one-page domain brief naming the rules and their product impact | self_directed_study | (none) | T1: label deliverable as a learning exercise; T6: no source (Trace-curated) |
| `build.X5` | Gap | X5 Value, business case and unit economics | Learn to reason about value, business cases and unit economics | Build a simple business case for one feature: who pays or saves, the cost to build and run it, and what would need to be true for it to be worth doing. | A one-page business case with the assumptions stated | self_directed_study | pm-v1:F1 | T1: label deliverable as a learning exercise |
| `build.X6` | Gap | X6 Practical AI and LLM experience | Build practical understanding of AI-powered features | Specify one AI-powered feature: what it should do, how its quality will be evaluated, what it must never do, and how cost and latency limit the choice of model. | A feature specification with an evaluation plan and guardrails | self_directed_study, external_project | (none) | T1: label deliverable as a learning exercise; T6: no source (Trace-curated) |
| `build.X7` | Gap | X7 Data governance and data quality | Understand data quality and governance | For one dataset you know, write its key definitions, the quality checks it needs, who may access it and why. | A short data contract: definitions, checks and access rules | self_directed_study, current_job | (none) | T1: label deliverable as a learning exercise; T6: no source (Trace-curated) |
| `build.L1` | Gap | L1 Scope: several products or a complex, high-risk product | Understand portfolio-level product decisions | Compare two products or initiatives you know: their goals, shared dependencies and how you would divide investment between them. | A short portfolio note with a recommended split and its reasoning | self_directed_study | pm-v1:F1 | T1: label deliverable as a learning exercise |
| `build.L2` | Gap | L2 Coaching, mentoring and product leadership | Prepare to coach and mentor others | Write how you would onboard a new team member to your area: what they need to learn, in which order, and how you would check progress. | An onboarding and coaching plan for one person | self_directed_study, current_job | pm-v1:F1 | T1: label deliverable as a learning exercise |
| `build.L3` | Gap | L3 Executive-level communication | Learn to communicate decisions to senior leaders | Write a one-page decision paper for a senior audience on a decision you know: the situation, the options, your recommendation and the risks. | A one-page decision paper | self_directed_study | pm-v1:F1 | T1: label deliverable as a learning exercise; T6: F1 has no such skill, so mark as Trace-curated |
| `build.L4` | Gap | L4 Planning across teams and functions | Understand planning across teams | Map the dependencies between teams for one initiative you know and propose an order of work that removes the biggest blocker first. | A cross-team dependency map with a proposed sequence | self_directed_study, current_job | pm-v1:F1 | T1: label deliverable as a learning exercise; T6: F1 has no such skill, so mark as Trace-curated |

### Demonstrate (stage 2) (17)

| Template | Intended state | Requirement | Objective | Action | Deliverable | Where | Sources | Review |
|---|---|---|---|---|---|---|---|---|
| `demonstrate.C1` | Gap (after Build) or Transferable | C1 Understand users and customers and set priorities from their needs | Show that you learn from users and act on it | Run the interviews (or analyse existing feedback or support tickets), summarise what users need, and propose one change based on it to the people who decide. | An insight summary with the proposed change and what happened to it | current_job, internal_move, external_project | pm-v1:F1 | T2: label project evidence (self-directed, not professional ownership) |
| `demonstrate.C2` | Gap (after Build) or Transferable | C2 Product vision, strategy and roadmap | Show that you can set and sequence direction for a product area | Offer to draft the next-quarter priorities for a feature area or internal tool you work on, explain the order you chose, and get it reviewed by the person who owns the area. | A reviewed roadmap for one area, with the reasoning behind its order | current_job, internal_move, external_project | pm-v1:F1 | T2: label project evidence (self-directed, not professional ownership); reword the deliverable to "a roadmap draft you prepared, reviewed by the area owner (a contribution, not ownership)" |
| `demonstrate.C3` | Gap (after Build) or Transferable | C3 Turn problems into requirements and priorities | Show that your requirements and priorities are used by a team | Write the requirements for one real piece of work your team takes on, agree the priority order with the team, and follow it through to delivery. | Requirements a team built from, with the agreed order and the delivered result | current_job, internal_move | pm-v1:F1 | OK: professional evidence in the current job or an internal move |
| `demonstrate.C4` | Gap (after Build) or Transferable | C4 Define outcome metrics and make data-informed decisions | Show that you own an outcome metric and decide from it | Agree a success metric for one feature or process change in your team before it ships, track it afterwards, and write up what it showed and what was decided. | A before-and-after metric review with the decision it led to | current_job, internal_move, external_project | pm-v1:F1 | T2: label project evidence (self-directed, not professional ownership); needs influence over a launch; realistic in delivery and analytics roles |
| `demonstrate.C5` | Gap (after Build) or Transferable | C5 Cross-functional collaboration and stakeholder communication | Show that you lead cross-functional decisions | Facilitate one decision that involves at least two other functions (for example engineering and sales): prepare the options, run the discussion, and circulate the decision and its reasons. | A written decision record with the options, the outcome and who agreed | current_job, internal_move | pm-v1:F1 | OK: professional evidence in the current job or an internal move |
| `demonstrate.C6` | Gap (after Build) or Transferable | C6 Iterative delivery across the product lifecycle | Show that you run iterative delivery | Take one piece of work through at least two increments with your team, reviewing what was learned after each before deciding the next. | Short review notes for each increment and the change they led to | current_job, internal_move, external_project | pm-v1:F1 | T2: label project evidence (self-directed, not professional ownership) |
| `demonstrate.X1` | Gap (after Build) or Transferable | X1 Hands-on analytics (SQL, dashboards, experimentation tooling) | Show that you use product data yourself | Answer one open product question in your team with your own query or dashboard, and share the result with the people who decide. | A query or dashboard in use, with the question it answered | current_job, internal_move | pm-bridges-v1:B1 | OK: professional evidence in the current job or an internal move |
| `demonstrate.X2` | Gap (after Build) or Transferable | X2 Owning go-to-market | Show go-to-market ownership | Work with sales or marketing on one launch or adoption push: agree the target users and message, and track adoption afterwards. | A launch brief and the adoption result | current_job, internal_move | (none) | T6: no source (Trace-curated) |
| `demonstrate.X3` | Gap (after Build) or Transferable | X3 Technical depth: APIs, platform and data architecture, security | Show technical depth in product decisions | Contribute to one technical design discussion for your team's product by writing the user and business trade-offs of the options. | A trade-off note used in a technical decision | current_job, internal_move | (none) | T6: no source (Trace-curated) |
| `demonstrate.X4` | Gap (after Build) or Transferable | X4 Domain and regulatory knowledge | Show domain knowledge in real work | Work with compliance or legal on one change in a regulated area and record how the requirement shaped it. | A change record showing the domain requirement and its effect | current_job, internal_move | (none) | T6: no source (Trace-curated) |
| `demonstrate.X5` | Gap (after Build) or Transferable | X5 Value, business case and unit economics | Show that you weigh value against cost in real decisions | Prepare the cost and value case for one real decision in your team and present it to the person who approves it. | A business case used in a real decision | current_job, internal_move | pm-v1:F1 | OK: professional evidence in the current job or an internal move |
| `demonstrate.X6` | Gap (after Build) or Transferable | X6 Practical AI and LLM experience | Show hands-on AI product work | Help define or run the evaluation of one AI or machine-learning feature in your organisation, or build a small prototype in a project, and record the results. | An evaluation record or prototype with its measured results | current_job, internal_move, external_project | (none) | T2: label project evidence (self-directed, not professional ownership); T6: no source (Trace-curated) |
| `demonstrate.X7` | Gap (after Build) or Transferable | X7 Data governance and data quality | Show that you improve data quality or governance | Introduce one shared definition, quality check or access rule for data your team relies on, and get it adopted. | An adopted definition, check or rule, with where it is used | current_job, internal_move | (none) | T6: no source (Trace-curated) |
| `demonstrate.L1` | Gap (after Build) or Transferable | L1 Scope: several products or a complex, high-risk product | Show scope across several products or a complex, high-risk product | Take on a second product area or initiative, or the riskiest part of your current one, and own its decisions alongside your existing work. | Decisions you owned across more than one area, with their results | current_job, internal_move | pm-v1:F1 | T5: assumes a product area; needs a variant for non-PMs |
| `demonstrate.L2` | Gap (after Build) or Transferable | L2 Coaching, mentoring and product leadership | Show coaching and product leadership | Mentor a junior colleague on a real piece of product work, with regular check-ins and feedback. | A mentoring record and the colleague's outcome | current_job, internal_move | pm-v1:F1 | T5: assumes product work; needs a labelled variant |
| `demonstrate.L3` | Gap (after Build) or Transferable | L3 Executive-level communication | Show executive-level communication | Present progress, risks or a decision for your area to a senior leader or leadership meeting, and record what was decided. | The paper or slides used and the decision recorded | current_job, internal_move | pm-v1:F1 | T6: mark as Trace-curated |
| `demonstrate.L4` | Gap (after Build) or Transferable | L4 Planning across teams and functions | Show planning across teams and functions | Coordinate one initiative that needs at least two teams: agree the dependencies and sequence with both, and track it to completion. | A shared cross-team plan and its delivery record | current_job, internal_move | pm-v1:F1 | T6: mark as Trace-curated |

### Reposition (stage 3) (2)

| Template | Intended state | Requirement | Objective | Action | Deliverable | Where | Sources | Review |
|---|---|---|---|---|---|---|---|---|
| `reposition.demonstrated` | Demonstrated | per instance | Present existing evidence as product evidence | Rewrite the line "{record_excerpt}" for applications so it states what you owned, the decision you made and the result, in the language of "{requirement_title}". | A rewritten resume line and a short interview story for this requirement | job_market | (none) | **T3: must not invent ownership, decisions or results; T4: needs skill and role variants** |
| `reposition.transferable` | Transferable | per instance | Explain how related experience carries over | Rewrite the line "{record_excerpt}" to show how it carries over to "{requirement_title}", and once you have done the stage 2 action, add that result next to it. | A rewritten resume line that links your experience to this requirement | job_market | (none) | T3 guardrail; T4 variants |

### Resolve first (stage 0) (6)

| Template | Intended state | Requirement | Objective | Action | Deliverable | Where | Sources | Review |
|---|---|---|---|---|---|---|---|---|
| `resolve.pending` | Unknown | per instance | Confirm your draft records | Review the draft records Trace lists here and confirm, correct or reject each one. | Every listed draft confirmed, corrected or rejected | current_job | (none) | OK; T7: the place should be "your Trace profile" |
| `resolve.unclassified` | Unknown | per instance | Categorise your experience | Categorise the listed lines of experience by area of responsibility, or mark them "None of these areas". | Every listed line categorised | current_job | (none) | OK; T7: the place should be "your Trace profile" |
| `resolve.recheck` | Unknown | per instance | Check older categories against the new areas | Open the listed lines and check whether any of the newer areas fit; save them again even if nothing changes. | Every listed line checked against the current list of areas | current_job | (none) | OK; T7: the place should be "your Trace profile" |
| `resolve.scope_question` | Unknown | per instance | Answer the scope questions | Answer the yes/no scope question on the listed lines (choose "Not sure" only if you really are). | The scope question answered on every listed line | current_job | (none) | OK; T7: the place should be "your Trace profile" |
| `resolve.no_evidence` | Unknown | per instance | Add your experience | Add your jobs and what you did in them, then confirm them, so Trace can compare your experience with the target role. | At least one confirmed job with its responsibilities | current_job | (none) | OK; T7: the place should be "your Trace profile" |
| `resolve.not_inferable` | Unknown | per instance | Add evidence Trace cannot infer | If you have experience of "{requirement_title}", add it to your profile as a responsibility or skill; Trace never assumes it is missing. | Any relevant experience added and confirmed | current_job | (none) | OK: never assumes a gap; T7 |

### Stage 4: getting into the role (7)

| Template | Intended state | Requirement | Objective | Action | Deliverable | Where | Sources | Review |
|---|---|---|---|---|---|---|---|---|
| `apply.current_postings` | Any (always) | per instance | Apply where the stated criteria fit | Review the verified current postings listed with this plan, starting with those whose stated criteria you meet; check each on the employer's own site before applying. | Applications to postings whose stated criteria you meet, each checked on the employer's site | job_market | (none) | OK: verified postings only; add apply.check_criteria (T8) |
| `apply.no_verified_postings` | Any (always) | per instance | Find current openings yourself | Trace has no verified current posting for this role that you can be matched against. Search employers' own career pages and compare each posting's stated criteria with your confirmed experience. | A list of openings you found, each with its stated criteria and the date you checked it | job_market | (none) | OK; pair with apply.alternative_routes (T8) |
| `apply.position_application` | Any (always) | per instance | Position your application for the role | Lead your resume and application with the requirements you have demonstrated, then the ones that carry over, using the lines rewritten in stage 3. | A tailored resume and a short summary statement for this role | job_market | (none) | Needs the T3 guardrail |
| `apply.referrals` | Any (always) | per instance | Learn from people already in the role | Ask people you already work with or know in this role how they got in and how their teams hire; ask for a referral only where they know your work. | Notes from your conversations, and any referrals offered | job_market, current_job | (none) | OK; personalise with the target and current organisation |
| `apply.interview_preparation` | Any (always) | per instance | Prepare for interviews on your weaker areas | Prepare one specific example for each requirement listed here, using your stage 2 results where you have them, and practise explaining what you would do differently. | One prepared example per listed requirement | job_market | (none) | Add product case-study practice (T8) |
| `apply.internal_route` | Any (always) | per instance | Consider an internal route | Ask your manager or the product team in your organisation whether product roles or product projects are open to internal candidates, and what they would want to see from you. | A conversation with the people who decide, and what they said | internal_move | (none) | OK |
| `apply.bridge_role` | Any (always) | per instance | Move into the bridge role first | Look for {bridge_title} roles whose responsibilities match those listed with this route, check their stated entry criteria, and apply where they fit; plan the move to {target_title} from there. | Applications to {bridge_title} roles whose responsibilities develop your gaps | job_market, internal_move | (none) | OK: bridge routes only |

### Cross-cutting findings

| # | Finding | Type |
|---|---|---|
| T1 | **Build is learning, not evidence.** Every Build deliverable should say so: "a learning exercise; it is not professional evidence and is not added to your profile as experience" | [C] |
| T2 | **Project evidence must be labelled.** Five Demonstrate templates allow `external_project` (C1, C2, C4, C6, X6). When done outside a job, the deliverable must read "project evidence (self-directed), not professional product ownership". Instantiation should show the label whenever `external_project` is one of the places | [C] + [I] |
| T3 | **Reposition must not invent.** `reposition.demonstrated` currently says "state what you owned, the decision you made and the result". That can lead a user to claim ownership, decisions or results their confirmed line doesn't show. Rewrite as: "Rewrite the line using only what your confirmed record shows. Add an owned decision or a result only if it happened, and add it to your profile first so it is reviewed." Apply the same guardrail to `reposition.transferable` | [C] |
| T4 | **Skill or role-history evidence.** When Demonstrated or Transferable comes from a confirmed skill or from role history, the reposition text quotes a skill name or a job title as if it were a line. That needs separate templates (`reposition.skill`, `reposition.role`), chosen by the assessment's basis | [C] + [I] |
| T5 | **Unrealistic preconditions.** `demonstrate.L1` ("take on a second product area") and `demonstrate.L2` ("mentor … on product work") assume the user already holds a product role. They need variants for people who don't (for example, contribute to cross-product planning; mentor in your own field, labelled) | [C] |
| T6 | **Uncited templates.** Build and Demonstrate for X2, X3, X4, X6 and X7 cite no source. Mark them "Trace-curated (no source)" in the response. L3 and L4 cite F1, but F1 has no executive-communication or cross-team-planning skill, so they should be Trace-curated too | [C] |
| T7 | **Resolve templates are not about jobs.** Their place is `current_job`, which is wrong. They need a new place value, "your Trace profile" | [I] (migration: add the value to the check) |
| T8 | **Stage 4 is incomplete.** Several areas are missing; see the coverage table below | [C] + [I] |

### Stage 4 coverage

| Required | Covered by | Gap and proposed template |
|---|---|---|
| Identify suitable current opportunities | `apply.current_postings` (verified only), `apply.no_verified_postings` | Covered |
| Understand employer eligibility | Only listed inside `current_postings` | **Missing.** `apply.check_criteria`: for each posting marked unclear, note what decides it (a stated condition, imprecise dates, a missing qualification record) and how to clarify it (complete the profile; ask the recruiter). [C] + [I] |
| Position experience in applications | `apply.position_application` | Covered. It needs the T3 guardrail |
| Tailor resume and portfolio | Resume only | **Missing portfolio.** `apply.case_study`: one case study from real work, written from confirmed records. Self-directed work must be labelled as such. [C] |
| Referrals and networking | `apply.referrals` | Covered. Personalise with the target role and the user's current organisation, where known |
| Interview and case-study preparation | `apply.interview_preparation` | **Missing the product case exercise.** Add: practise a product case (problem, users, options, metric, trade-off) using the requirements listed. [C] |
| Alternative routes when no opening is available | `apply.internal_route`; `apply.bridge_role` on bridge routes | **Partly covered.** `apply.alternative_routes`: internal transfer; a product project or secondment in the current organisation; on direct routes, the catalogued bridge as a fallback. No named programmes unless verified. [C] + [I] |

---

## 4. Employer-eligibility criteria audit (18)

**Outcome rules (all leaves):**

| Leaf | Meets | Does not meet | Unclear |
|---|---|---|---|
| Years | The certain minimum (latest possible start to earliest possible end, overlaps counted once) reaches the stated minimum | The longest reading of the dates is below the minimum, and no confirmed job lacks a role | Anything else. Also: more than a stated range; an unknown date; an unmapped job |
| Product exposure | A confirmed, categorised, current line with a product-ownership area, in a role of the named families | No such lines, and none pending, uncategorised or stale | Lines pending, uncategorised or stale |
| Current role | Current confirmed role in the family | Current confirmed role outside the family | Current role unknown |
| Not transitioning from | Current role outside the excluded families; earlier roles never count | Current role in an excluded family | Current role unknown |
| Qualification | A confirmed qualification at the level, matching a field | Never | Otherwise; also "or equivalent" |
| Stated condition | Never | Never | Always |

**Combining leaves:**
- AND: any "does not meet" gives "does not meet"; all "meets" gives "meets".
- OR: any "meets" gives "meets"; all "does not meet" gives "does not meet".
- Only required criteria decide a posting's outcome; preferred ones are reported.

| # | Posting | Criterion as stored | Status | Rule (logic, ranges) | User evidence needed | Review |
|---|---|---|---|---|---|---|
| 1 | J2 Razorpay | 6+ years of product management, at least 2 focused on growth or PLG | required | (years[product_management] 6+ AND stated: "at least 2 years focused on growth or product-led growth") | confirmed, mapped jobs with dates; none (the employer judges) | E3, E4 |
| 2 | J3 Razorpay | 4 to 8 years of product management, at least 2 owning a platform, API or infrastructure product | required | (years[product_management] 4–8 AND stated: "at least 2 years owning a platform, API or infrastructure product") | confirmed, mapped jobs with dates; none (the employer judges) | E3, E4 |
| 3 | J3 Razorpay | A computer science or engineering degree is not mandatory; ideally 2+ years as an engineer or architect before product | preferred | years[software_engineering] 2+ | confirmed, mapped jobs with dates | E8: order not checked; a preference, not a substitute |
| 4 | J4 Paytm | 4-8 years (from the title), strong product management experience | required | (years[product_management] 4–8 AND stated: "strong product management experience with ownership from concept to launch and scale") | confirmed, mapped jobs with dates; none (the employer judges) | E3, E4 |
| 5 | J4 Paytm | B.E./B.Tech or equivalent technical education | preferred | qualification ≥bachelor in (engineering, technology, computer science) or equivalent | confirmed qualification records | E1: match on qualification type too |
| 6 | J4 Paytm | FinTech, lending, payments or financial services | preferred | stated: "experience in fintech, lending, payments or financial services" | none (the employer judges) | OK |
| 7 | J5 Paytm | 3+ years of product management building consumer-facing digital products | required | (years[product_management] 3+ AND stated: "building consumer-facing digital products") | confirmed, mapped jobs with dates; none (the employer judges) | E3, E4 |
| 8 | J6 Freshworks | 8+ years in global or cross-functional software teams at a software product company | required | (total years 8+ AND stated: "in global or cross-functional software teams at a software product company") | confirmed jobs with dates; none (the employer judges) | OK |
| 9 | J7 Adobe | 8-10 years of product management | required | years[product_management] 8–10 | confirmed, mapped jobs with dates | E3, E4 |
| 10 | J7 Adobe | Bachelor's in Computer Science, Engineering, Business or similar (Master's or equivalent experience preferred) | required | qualification ≥bachelor in (computer science, engineering, business) or equivalent | confirmed qualification records | E9: embedded preference not represented |
| 11 | J8 Adobe | 5+ years of relevant product management | required | (years[product_management] 5+ AND stated: "the product management experience is relevant to the role (judged by the employer)") | confirmed, mapped jobs with dates; none (the employer judges) | E3, E4 |
| 12 | J8 Adobe | Bachelor's or advanced degree in a quantitative field, EE, CS or equivalent | required | qualification ≥bachelor in (mathematics, statistics, economics, physics, electrical, electronics, computer science, engineering) or equivalent | confirmed qualification records | OK |
| 13 | J9 Wayfair | 6+ years in product management; e-commerce valued | required | years[product_management] 6+ | confirmed, mapped jobs with dates | E3, E4 |
| 14 | J9 Wayfair | Bachelor's in CS or CE, or an equivalent combination of education and experience | required | qualification ≥bachelor in (computer science, computer engineering) or equivalent | confirmed qualification records | OK |
| 15 | J10 Sarvam | 4-8 years of product management, significant time in enterprise SaaS or B2B AI | required | (years[product_management] 4–8 AND stated: "significant time in enterprise SaaS or B2B AI products") | confirmed, mapped jobs with dates; none (the employer judges) | E3, E4 |
| 16 | J10 Sarvam | Background in Computer Science or Electrical Engineering | preferred | qualification ≥any in (computer science, electrical) | confirmed qualification records | E9: could mean a work background |
| 17 | J11 Target | 3-5 years in product management, or 5+ years in related data, analytics or engineering roles with product exposure | required | (years[product_management] 3–5 OR (years[data_analytics,data_science,product_analytics,software_engineering] 5+ AND product exposure in [data_analytics,data_science,product_analytics,software_engineering])) | confirmed, categorised product-work lines; confirmed, mapped jobs with dates | E5, E6: two readings for approval; E3, E4 |
| 18 | J12 Sarvam | 3-7 years of product management; currently a product manager | required | (years[product_management] 3–7 AND current role in [product_management] AND not moving now from [consulting,project_program_management]) | confirmed current role; confirmed, mapped jobs with dates | E7: current APM?; E3, E4 |

J1 (Razorpay) has no stored criteria (E10).

### Findings

| # | Finding | Type |
|---|---|---|
| E1 | **B.E. / B.Tech is matched by field words.** J4's "B.E./B.Tech or equivalent technical education" is matched on "engineering", "technology" or "computer science" in the title and field. "B.Tech, Mechanical" does not match, because "b.tech" doesn't contain "technology". Match on the qualification type (`BE`, `BTech`, which the D11 read-time type already recognises) as well as fields | [I] |
| E2 | **Fields are matched by substring.** For example, "economics" matches inside "home economics". Use whole-word matching | [I] |
| E3 | **Part-time and internships count fully.** Experience counts unless the user said it isn't full-time, so an internship or part-time job with no answer adds to the *certain* minimum. Count only full-time (stated, or an explicit "yes") in the minimum; include the others only in the maximum | [I] |
| E4 | **Product management years need a product management title.** Product Owner, founder or engineering-manager years with confirmed product-ownership evidence (C2 or C4 Demonstrated in that job) count as zero, giving "does not meet". Proposal: such years give **unclear** ("the employer decides whether this counts as product management experience"), never "meets". This keeps the employer's exact condition, consistent with item 2 | [I] |
| E5 | **J11, two readings needing approval.** (a) "With product exposure": exposure is required in at least one qualifying role, not across all 5 years. (b) Product management and adjacent years are not added together, because the posting doesn't say they can be | [D] |
| E6 | **J11's exposure areas** are the four product-ownership areas. Requirements analysis and stakeholder work don't count. Curated; needs approval | [D] |
| E7 | **J12 "currently a product manager".** An Associate PM is in the product management family and so meets. That may not be the employer's meaning; consider "unclear" for a current APM | [D] decision |
| E8 | **J3's preferred "2+ years as an engineer or architect before product".** "Before product" (the order) isn't checked; any engineering years count. "Architect" has no family | [I] (minor) or [D] |
| E9 | **J7 and J10.** J7's required education contains a preference ("Master's or equivalent experience preferred") that isn't represented. J10's "background in CS or EE" is read as a qualification but could mean a work background. Both are informational parts | [D] |
| E10 | **J1 has no stored criteria** and reads "unclear" with no explanation. Add the explanation "no criteria recorded for this posting" | [I] (small) |
| E11 | **No criterion is "informational".** Informational clauses (such as J3's "degree is not mandatory") stay in the text only. Acceptable; document it | [D] |

**Checked and correct:**
- overlapping jobs are counted once;
- year-only and unknown dates give "unclear", not a guess;
- ongoing jobs end at the assessment date;
- current against historical roles (J12);
- "or equivalent" never gives "does not meet";
- preferred criteria never decide;
- engineering in J3 is a preference, not a substitute;
- Target's adjacent route needs product exposure, and a title alone gives "does not meet";
- the 14-day current window.

The criterion **texts** are the pack's structured paraphrases (employer licence: paraphrase plus one quotation). The
rules preserve their conditions exactly, except where E1, E3, E4 and E8 change the evaluation.

---

## 5. Product Analyst: why it rarely wins, and three comparisons

### Why it rarely wins today

1. **Its value is already counted as evidence.** In `pm-v1.1`, `data_analysis` is a *direct* area for C4, and SQL is
   transferable to C4 and direct for X1. The people who can enter a Product Analyst role (who have data work) are
   therefore usually already Demonstrated or Transferable on C4 and X1, the two things the role develops best.
2. **APM appears to dominate it.** APM's catalogue entry claims full development of C1, C2, C3, C5 and C6 with an
   "open" entry for everyone. For most gap sets it closes as many gaps or more. Correcting APM (C2 and C4 only partly
   developed) removes most of that apparent dominance.
3. **Counting.** The 50% rule favours the bridge that covers the most gaps, never the one that covers the gap that
   matters.

**Related content finding [C]: C4 may be too generous.** C4 is "define outcome metrics and make data-informed
decisions", but any data analysis line demonstrates it. Proposal for `pm-v1.2`: `data_analysis` counts directly for C4
only with a scope question ("Did this define or track a product or business outcome and inform a decision?"),
otherwise as Transferable. This is your decision. It changes assessments, but under the Phase 3.1 safeguard it creates
no Gaps automatically.

### Three comparisons (proposed rules)

| | Profile | Decision-rights gaps (G) | Associate PM | Product Analyst | Direct | Primary |
|---|---|---|---|---|---|---|
| 1 | **Customer success manager** (PM, commercial): accounts, onboarding, feedback; no analytics, roadmap or requirements | C2, C4 | Partly develops C2 and C4; also C3 (Gap) and practice areas; entry source-stated | Entry not evidenced (no analytics) | Non-digital background: no source-stated direct entry, and G isn't shown | **Associate PM** |
| 2 | **Data scientist** (PM, data product): models, prioritised the modelling backlog, presented results; Python | C4 | Partly develops C4; fully develops C1 (a practice gap) | Fully develops C4 (KPI definition, experiments) and C1 through data; entry evidenced (data science role) | Digital background, but C4 not shown | **Product Analyst** (dominates APM on C4) |
| 3 | **Business analyst** with prioritisation, adoption tracking, discovery, delivery (PM) | none (C2 Transferable is acceptable at PM entry) | Would add a junior move for nothing in G | Nothing in G | Every decision-rights requirement shown or transferable; F1: PM is an entry role from other digital roles | **Direct** |

What each route would develop:
- **Profile 1, Associate PM:** supervised experience of roadmapping and outcomes (C2, C4 partly) plus requirements
  practice (C3). The direct route would rely on project evidence for all three.
- **Profile 2, Product Analyst:** product KPIs, experiments and user-journey analysis inside a product team (C4, C1).
  The APM would give C4 only at awareness level and add a junior step for someone already strong in practice.
- **Profile 3, direct:** stage 2 turns the Transferable C2 into evidence (draft priorities reviewed by the area owner);
  no bridge develops anything they lack.

These show the catalogue responding to profile differences. Under the current rules the outcomes are APM, direct and
direct; the data scientist never gets the Product Analyst.

---

## 6. Persistence and snapshot retention

### The snapshot table (`trace_ref.reference_snapshots`)

| Column | Content |
|---|---|
| `snapshot_id` | Random UUID |
| `data_mode` | `empirical` or `synthetic_test` |
| `settings` | The analytics thresholds at that time |
| `eligible_profile_count` | Profiles in the cohort |
| `confirmed_transition_count` | Confirmed pathway moves in the cohort |
| `notes` | Fixed text |
| `created_at` | Timestamp |

- **No user id, account id, profile id or record id.** The only link to a user is the run's `reference_snapshot_id`,
  which is stored on the run, not on the snapshot.
- **Plan deleted** (owner delete policy): the run and its routes, steps, evidence and actions cascade. The snapshot
  stays, with no remaining link.
- **Account deleted:** `auth.users`, then profile, then runs cascade as above. The snapshot stays, unlinked.
- **Why snapshots are kept:** the run's foreign key prevents deleting a snapshot that is in use, and nothing removes
  unused ones. They were never designed to be removed. Locally there are already **640 unlinked snapshot rows**, all
  from my test runs.

**Risk assessment:**
- **Direct identification:** none.
- **Timing linkage: low, but real.**
  - `created_at` is precise to the microsecond, and one snapshot is written per plan.
  - Anyone holding both the database and request or authentication logs could match a snapshot to a user's request.
    That would reveal *that* the user made a plan at that moment, and the pool counts at that moment. It reveals
    nothing about their profile.
  - After account deletion, logs keyed by user id are the only bridge.
- **Count differencing: low.** Exact cohort counts at many points in time can show *when* a contributor joined or
  withdrew (5 to 4 at time T). It cannot show who. Only administrators can read the table (no client policies or
  grants).

**Safer design [I], proposed:**
1. **Shared, content-addressed snapshots.** One row per distinct (cohort, settings, counts). A run references an
   existing row when the state is unchanged. `created_at` becomes "first observed". There are no per-plan rows, so no
   timing link and no accumulation.
2. **Coarser time.** Store `first_observed_on` as a date, not a timestamp.
3. **Retention.** An administrator-only job removes snapshots no run references after a stated period (for example,
   with each backup cycle). The 640 local test rows go with it.
4. **Exact counts stay**, for reproducibility. They are never exposed to clients; runs expose only the computed band
   (`empty`, `below_minimum`, `at_or_above_minimum`).

### Owner-only access and immutability: how they are enforced

| Protection | Mechanism |
|---|---|
| One write path | `trace_plan.record_transition_plan`, owned by `trace_plan_writer`, which can't log in and can't be used by anyone. EXECUTE for `trace_app_user` only, checked by a self-test in the migration. `authenticated`, `anon` and `service_role` cannot call it |
| Owner checks | The request identity must own the profile (TR005). The writer's insert policies require `owner_user_id` to be the request identity. Reads use the existing owner SELECT policies |
| Values that can't be supplied | Scores (always `{}`), supporter counts for profile-led routes (0), evidence class (from the cohort), snapshot counts (computed in the database), reference profile ids (refused) |
| Immutability | Triggers: no update of a completed or failed run; nothing added to or changed in its children (TR027) |
| Deletion | Owner delete policy (existing); account and profile cascades |

**Findings:**
- **P-1 [I]:** `service_role` still has INSERT, UPDATE, DELETE and **TRUNCATE** on the run tables. This predates
  Phase 4; the backend never uses `service_role`.
  - Triggers stop service_role from updating completed runs.
  - They don't stop it inserting a fake run directly (bypassing the function).
  - TRUNCATE skips row triggers.
  - Proposal: revoke INSERT, UPDATE and TRUNCATE on the plan tables from `service_role`, and keep DELETE for
    administrative erasure.
- **P-2 [D]:** the owner can delete their own runs. "Append-only" means no edits, not no deletion, which privacy
  requires. Document this.

---

## 7. Revised Phase 4 acceptance checklist

| # | Criterion | Status |
|---|---|---|
| A1 | Route selection uses requirement-level rules (P0–P8); no percentages, counts or weights | **Open** (O1–O3, then [I]) |
| A2 | No title-based level step; Senior PM routes follow evidence of strategy and outcome ownership (S1–S4) | **Open** ([I]) |
| A3 | Bridge catalogue corrected and labelled: source fact, curated reading, hypothesis; APM C2/C4 partly; Product Owner entry and progression marked as hypotheses; PM L2 partly | **Open** ([C]) |
| A4 | A hypothesis entry is never primary | **Open** ([I]) |
| A5 | The three Product Analyst comparisons give APM, Product Analyst and direct respectively, as API tests | **Open** |
| A6 | Templates: Build labelled as learning; project evidence labelled; reposition guardrail; skill and role variants; L1/L2 variants; uncited templates marked | **Open** ([C] T1–T6) |
| A7 | Stage 4 covers criteria clarification, case study, case-interview practice and alternative routes | **Open** ([C] + [I]) |
| A8 | Eligibility fixes E1–E4 and E10, with tests; E5–E9 and E11 documented or decided | **Open** |
| A9 | Content-addressed snapshots; date-level first-observed; retention job; `service_role` write revocation | **Open** ([I], migration with rollback and pgTAP) |
| A10 | Unchanged: privacy minimum, reference-profile separation, 8.2 shadow, eligibility separation, confirmation requirements, immutability | Kept; re-verify |
| A11 | 8.1 and 8.2 evaluation baselines byte-identical | Re-verify after changes |
| A12 | Full suites (backend, pgTAP, data-foundation) and mutation tests for every new rule | After changes |
| A13 | Optional: C4 scope question (`pm-v1.2`) | Your decision |

## Summary of findings by type

| Type | Findings |
|---|---|
| **[I] Implementation** | Route rules P0–P8 (replace R2–R5 and both thresholds); hypothesis-entry handling; T2 label at instantiation; T4 template choice by basis; T7 new place value; T8 new Stage 4 templates; eligibility E1, E2, E3, E4, E10 (E8 optional); snapshot redesign and retention; P-1 `service_role` revocation |
| **[C] Content** | APM C2/C4 partly, plus X5 and L2 partly; PM L2 partly; Product Analyst and Product Owner progression and entry labelled as hypotheses; decision-rights flag (O1); digital-family mapping (O3); templates T1, T3, T5, T6 and the new Stage 4 templates; optional C4 scope question |
| **[D] Documentation** | E5, E6, E7, E9, E11; P-2 deletion semantics; this review's labels in the docs |
