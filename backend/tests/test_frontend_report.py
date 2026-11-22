"""Static guards on the career-planning workspace (Overview, My Roadmap, Learn & Develop, Experience & Reposition,
Profile & evidence) and the Target step. The journey itself runs in real Chrome.

The browser only presents the saved plan, its roadmap projection, the user's goal and their records: it never counts,
ranks, scores, forecasts or picks a route, never pretends to save anything locally, and never shows illustrative data
in a real user's workspace. Employer matching and the company selector stay retired (code and data kept, dormant).
"""
import json
import re
from pathlib import Path

from tests.test_frontend_phase71 import JS, code_of, visible_strings

ROOT = Path(__file__).resolve().parents[2]
WS = ["views/report.js"] + [f"views/workspace/{n}.js" for n in ("common", "overview", "roadmap", "learn", "experience", "profile")]
ALL = "\n".join(code_of(n) for n in WS)
TEXT = " ".join(t for n in WS for t in visible_strings(n))


def test_workspace_loads_the_saved_plan_and_its_projection():
    a = code_of("api.js")
    assert "/api/v1/transition-plan/${q(runId)}/roadmap" in a and "/api/v1/transition-goal?target=" in a
    r = code_of("views/report.js")
    assert "api.latestPlan(target)" in r and "api.createPlan(" in r and "api.roadmap(loaded.plan.run_id)" in r
    assert "latest.freshness && latest.freshness.current" in r                   # reused only when every input is current
    assert "cache.owner === owner" in r and "cache.profileVersion === records.profile_version" in r   # never across users or edits


def test_five_sections_are_routed_and_navigable():
    m = code_of("main.js")
    assert "const [path, param, section] = raw.split(\"/\");" in m
    assert 'report.render(root, { ...ctx, targetId: app.target, section: section || "overview" })' in m
    assert 'state.hasRecords && !state.sharing.notice_acknowledged && ["target", "pathway"].includes(path)' in m
    c = code_of("views/workspace/common.js")
    for key, label in (("overview", "Overview"), ("roadmap", "My Roadmap"), ("learn", "Learn & Develop"),
                       ("experience", "Experience & Reposition"), ("profile", "Profile & evidence")):
        assert f'key: "{key}", label: "{label}"' in c
    r = code_of("views/report.js")
    # the current section is "page"; on a supporting view (D46) its parent section is the "location"
    assert '"aria-current": s.key === key ? "page" : (s.key === section.key ? "location" : null)' in r
    assert '"aria-label": "Workspace section"' in r
    assert "views/results.js" not in m and "views/insights.js" not in m


def test_no_client_side_ranking_counting_scoring_or_forecasting():
    for banned in ("Math.max", "observation_count", "profile_count", "toFixed", "uniqueMostObserved", ".sort((a, b) => b"):
        assert banned not in ALL, banned
    # Phase 5: the overview shows research figures exactly as served, in ONE renderer; no arithmetic on them
    o = code_of("views/workspace/overview.js")
    f = o[o.index("function figure(o)"):o.index("function pathwayCard(p)")]
    assert ALL.count("share_pct") == f.count("share_pct") == 1 and not re.search(r"share_pct\s*[-+*/]|[-+*/]\s*o\.share_pct", f)
    assert not re.search(r"\.score|score_|[a-z]Score|fit_pct|readiness_", ALL)
    assert not re.search(r"most common|most popular|fastest|most successful|% |typically takes|on average|you will become|"
                         r"you lack|not ready|unsuitable", TEXT, re.I)


def test_no_illustrative_or_locally_simulated_data():
    # "illustrative only" appears once, about the fictional test career histories; never about the user's plan
    assert ALL.lower().count("illustrative") == 1 and "fictional test data" in ALL.lower()
    for gone in ("Illustrative", "Example date", "version 1", "localStorage", "sessionStorage", "Plan history", "Engineer plan"):
        assert gone not in ALL, gone
    assert "rm.timeline" in ALL and "plan.created_at" in ALL                      # dates come from the saved plan


def test_overview_hierarchy_and_goal_language():
    # Phase 5: one story, A to E, with the three kinds of statement labelled apart every time.
    o = code_of("views/workspace/overview.js")
    for part in ("Your career transition", "Your starting point", "Where people with your starting point moved",
                 "Routes to ", "How this relates to your experience", "What to do next",
                 "From your confirmed profile", "Observed in the research sample · not a prediction for you", "Trace's recommendation",
                 "Explore the full career research", "What this research can and can't show", "Other routes Trace considered",
                 "Not yet demonstrated in the available evidence", "not your ability"):
        assert part in o, part
    assert "planStatus" not in o and "Created " not in o                       # status lives once, in the top bar
    assert "Complete your profile" not in o                                    # never told to complete a complete profile
    c = code_of("views/workspace/common.js")
    assert "Your timeframe organises your planning. It is not a prediction or guarantee of a career outcome." in c
    assert "api.setGoal(rm.target_role_id" in c
    r = code_of("views/report.js")
    assert 'button("Refresh plan"' in r and "Saves a new version of your plan from your current confirmed profile" in r
    assert r.count("Saved plan") == 1
    assert 'origins: { key: "origins", label: "Career research", parent: "overview" }' in r


def test_bridge_is_never_assumed():
    r = code_of("views/workspace/roadmap.js")
    assert '"A direct transition"' in r and '"A provisional pathway"' in r
    assert "if (!direct || !bridge) return null;" in r                          # comparison only when the plan holds both
    assert 'role: "tablist", "aria-label": "Roles on this pathway"' in r and "ws-path-detail" in r
    o = code_of("views/workspace/overview.js")
    assert "Provisional" in o and "intermediate: " in o and "is-${s.position}" in o

def test_roadmap_groups_are_planning_groups_not_durations():
    r = code_of("views/workspace/roadmap.js")
    for part in ("Start now", "In parallel", "Before applying", "Hiring transition", "Planning groups, not durations",
                 '"aria-label": "Planning groups"', "Expected output: ", 'disclosure("Details"', "Evidence to keep",
                 "Depends on access: ", "If workplace access isn't available", "Suggested milestones", "nothing is tracked",
                 "doesn't estimate a duration"):
        assert part in r, part
    text = " ".join(visible_strings("views/workspace/roadmap.js")).lower()
    for gone in ("overdue", "progress", "streak", "% "):
        assert gone not in text, gone

def test_learning_lists_real_resources_unranked_with_details():
    l_ = code_of("views/workspace/learn.js")
    c = code_of("views/workspace/common.js")
    for part in ('step(1, "Understand"', 'step(2, "Learn"', 'step(3, "Apply"', "Knowledge outline", "Suggested starting point: ",
                 "Listed by learning approach, not ranked", "Open the full exercise", "Kind of work", "Only involve users, customers or workplace data"):
        assert part in l_, part
    for part in ("resourceDetails(r, context)", "Published duration", "Not stated by the provider", "checked ${dateOf(r.verified_on)}",
                 "Trace doesn't record prices", 'aria-label": `View details: ${r.title}`'):
        assert part in c, part
    assert "Only describe this as professional experience if you actually do the work" in l_
    assert "historiesNote" not in l_                                             # shown once, in Profile & evidence

def test_experience_distinguishes_learning_practice_and_professional_work():
    e = code_of("views/workspace/experience.js")
    for part in ("Learning", "Independent practice", "Supervised project work", "Professional experience", "A conversation starter",
                 "navigator.clipboard.writeText", "Keep evidence of your contribution", "confidentiality", "Never claim: ",
                 "Observe or contribute", "Take a bounded part", "Own a defined outcome", "Whom to approach", "Permission",
                 "Describe your work accurately", "Opportunities to seek, not completed work"):
        assert part in e, part
    assert e.count("Whom to approach") == 1                                       # shared guidance, not repeated per card
    assert "repositionActions" not in e                                           # no repeated resume rewrites

def test_profile_groups_preserve_provenance():
    # Phase 5: each capability with every record behind it, who read it and why; action only when something needs it.
    p = code_of("views/workspace/profile.js")
    for part in ("rm.capabilities", "Trace read this line", "You categorised this line", "Needs your action",
                 'e.review.status === "pending_review"', "is not evidence that you haven't done it", "A job title is never",
                 "historiesNote(rm)", "takes precedence over Trace's reading"):
        assert part in p, part
    assert "categorised by you" not in ALL and "Transferable" not in ALL


def test_target_step_collects_an_optional_goal_and_where_to_move():
    t = code_of("views/target.js")
    assert "goalPicker(chosen)" in t and "picker.save()" in t and 'button("See my career plan"' in t and "ctx.app.goalChanged = true" in t
    assert "companyPicker" not in t and "movePicker(chosen)" in t and "mover.save()" in t           # D51: where, not who hires
    m = code_of("move.js")
    for part in ("Where would you like to make this move?", "I have specific companies in mind", "I know the kind of company",
                 "I'm open to options", "Trace doesn't check them against a company list or hiring data"):
        assert part in m, part
    g = code_of("goal.js")
    assert "When would you like to make this move?" in g and "How much time can you realistically give this each week?" in g


def test_employer_matching_is_gone_from_the_product_flow():
    for gone in ("companyResearch", "targetCompanies", "searchCompanies", "setTargetCompanies", "companyPicker",
                 "current_openings", "Your target companies", "Required criteria", "eligibility.postings"):
        assert gone not in ALL + code_of("views/target.js") + code_of("main.js"), gone
    assert (ROOT / "backend/web/js/companies.js").exists()


def test_comparable_histories_use_the_agreed_copy():
    c = JS["views/workspace/common.js"]
    assert "We don't yet have enough eligible career histories to show a reliable comparison for this " in c
    assert "transition. This doesn't mean the path is uncommon or unavailable." in c


def test_sources_are_collapsed_and_keep_attribution():
    r = code_of("views/report.js")
    assert 'disclosure("Sources and how this plan was made"' in r and "s.attribution" in r and "r.licence" in r


def test_accessible_controls():
    assert 'role: "tablist"' in code_of("views/workspace/roadmap.js") and 'role: "tablist"' in code_of("views/workspace/learn.js")
    assert "ArrowRight" in code_of("views/workspace/learn.js") and "showModal()" in code_of("views/workspace/common.js")
    css = (ROOT / "backend/web/css/workspace.css").read_text()
    assert css.count(":focus-visible") >= 5


def test_action_copy_matches_the_action_pack():
    pack = json.loads((ROOT / "data-foundation/knowledge_packs/pm-actions-v2.json").read_text())
    c = JS["copy.js"]
    for k, v in pack["where_values"].items():
        assert f'{k}: "{v}"' in c, k


def test_no_html_building():
    for n in WS + ["goal.js"]:
        assert "innerHTML" not in JS[n] and "insertAdjacentHTML" not in JS[n], n


def test_evidence_map_uses_only_plan_data():
    c = code_of("views/workspace/common.js")
    m = c[c.index("export function evidenceMap"):c.index("export function goalEditor")]
    for src in ("rm.experience.themes", "rm.related", "rm.not_assessed", "rm.development"):
        assert src in m, src
    for gone in ("Math.", "%", "score", "progress"):
        assert gone not in m, gone
    p = code_of("views/workspace/profile.js")
    assert "c.evidence.map(" in p and "historiesNote(rm)" in p and "Math." not in p
