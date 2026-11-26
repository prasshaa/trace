// The career-planning workspace: Overview, My Roadmap, Learn & Develop, Experience & Reposition, Profile & evidence
// (#/pathway/<target>/<section>). It presents ONE saved plan (/api/v1/transition-plan: the route decision, D27-D41), its
// roadmap projection (/api/v1/transition-plan/{id}/roadmap), the user's goal and their profile records. The server
// decides everything; this view only lays it out. It never counts, ranks, scores or forecasts, never stores anything
// locally as if it were saved, and shows no illustrative data: a real user sees only their own plan.
import { api } from "../api.js";
import { h, mount } from "../dom.js";
import { busy, button, disclosure, errorState, loading, notice } from "../ui.js";
import { SECTIONS, dateOf, icon, sectionHref, sourceIndex } from "./workspace/common.js";
import * as overview from "./workspace/overview.js";
import * as roadmapView from "./workspace/roadmap.js";
import * as learn from "./workspace/learn.js";
import * as experience from "./workspace/experience.js";
import * as profile from "./workspace/profile.js";
import * as origins from "./workspace/origins.js";

export { NOT_ENOUGH_HISTORIES } from "./workspace/common.js";
const VIEWS = { overview, roadmap: roadmapView, learn, experience, profile, origins };
// Supporting views: reached from a section, not listed in the navigation; the parent section stays highlighted.
// Phase 5: the research is part of the overview's story; its full view opens from there.
const SUPPORTING = { origins: { key: "origins", label: "Career research", parent: "overview" } };
export const isCurrentRoleRefusal = (err) => Boolean(err && err.status === 409 && err.code === "target_is_current_role");
const isNotCovered = (err) => Boolean(err && err.status === 422 && err.code === "target_not_covered");

// The loaded plan, reused when switching sections while the confirmed profile is unchanged (no extra requests).
let cache = null;

export async function render(root, ctx) {
  const target = ctx.targetId;
  const key = VIEWS[ctx.section] ? ctx.section : "overview";
  const records = ctx.state.records;
  const owner = ctx.state.me && ctx.state.me.user_id;
  if (!(cache && cache.owner === owner && cache.target === target && cache.profileVersion === records.profile_version)) {
    mount(root, h("section.page.ws-page", loading("Opening your career plan")));
    let position = null;
    try {
      position = await api.currentPosition();
    } catch {
      position = null;
    }
    try {
      const loaded = await latestOrNew(target);
      const [rm, originsData] = await Promise.all([api.roadmap(loaded.plan.run_id), loadOrigins(loaded.plan.run_id)]);
      cache = { owner, target, profileVersion: records.profile_version, ...loaded, rm, origins: originsData };
      ctx.app.goalChanged = false;
    } catch (err) {
      cache = null;
      if (isCurrentRoleRefusal(err)) return backToTarget(ctx);
      if (isNotCovered(err)) return notCovered(root);
      mount(root, h("section.page", errorState(err, () => render(root, ctx))));
      return;
    }
  }
  if (ctx.app.goalChanged) {                      // the goal was changed on the Target step: refresh the roadmap only
    ctx.app.goalChanged = false;
    cache.rm = await api.roadmap(cache.plan.run_id);
  }
  if (key === "origins" && !cache.origins) {          // feature off: show the roadmap, and say so in the address
    history.replaceState(null, "", sectionHref(target, "roadmap"));
    draw(root, ctx, "roadmap");
    return;
  }
  draw(root, ctx, key);
}

/** Career Origins & Transitions for this plan: null when the server has the feature off (404); an "unavailable" state on
 * any other failure, so the workspace itself never depends on it. */
async function loadOrigins(runId) {
  try {
    return await api.careerOrigins(runId);
  } catch (err) {
    return err && err.status === 404 ? null : { status: "unavailable" };
  }
}

/** The latest saved plan, reused while every decision input is current; otherwise a new immutable version is saved with
 * the same stated constraints (the earlier one is kept). The goal never creates a version. */
async function latestOrNew(target) {
  let latest = null;
  try {
    latest = await api.latestPlan(target);
  } catch (err) {
    if (err.status !== 404) throw err;
  }
  if (latest && latest.freshness && latest.freshness.current) return { plan: latest, status: "current" };
  const kept = (latest && latest.constraints) || {};
  // Product contexts come from the user's "where would you like to move" answer, added by the server (D51).
  const plan = await api.createPlan(target, { portfolioScope: Boolean(kept.portfolio_scope), contexts: [] });
  return { plan, status: latest ? "updated" : "new", previous: latest };
}

function backToTarget(ctx) {
  cache = null;
  ctx.app.flash = "target_is_current_role";
  ctx.app.target = null;
  ctx.go("target");
}

function notCovered(root) {
  mount(root, h("section.page", h("section.card.state-card",
    h("h1.page-title", { tabindex: "-1" }, "A career plan isn't available for this role yet"),
    h("p", "Trace can read a move into Associate Product Manager or Product Manager, and from Product Manager to Senior "
      + "Product Manager. Other moves on that ladder are labelled Limited support. Roles outside it are not guessed at."),
    h("p.muted", "Your profile is saved. Choose one of the covered roles to see your plan."),
    h("div.actions", h("a.btn.btn-primary", { href: "#/target" }, "Choose another target role")))));
  root.querySelector(".page-title").focus({ preventScroll: true });
}

// --------------------------------------------------------------------------- the workspace shell
function draw(root, ctx, key) {
  const target = cache.target;
  const ws = {
    ...cache, target, app: ctx.app, records: ctx.state.records,
    refreshGoal: async () => {
      cache.rm = await api.roadmap(cache.plan.run_id);
      draw(root, ctx, key);
    },
    replan: (portfolioScope) => async () => {
      const c = cache.plan.constraints || {};
      const plan = await api.createPlan(target, { portfolioScope: portfolioScope !== null ? portfolioScope : Boolean(c.portfolio_scope), contexts: [] });
      const [rm, originsData] = await Promise.all([api.roadmap(plan.run_id), loadOrigins(plan.run_id)]);
      cache = { ...cache, plan, status: "current", previous: null, rm, origins: originsData };
      draw(root, ctx, key);
    },
  };
  const support = SUPPORTING[key] || null;
  const section = SECTIONS.find((s) => s.key === (support ? support.parent : key));
  const nav = h("nav.ws-nav", { "aria-label": "Your workspace" },
    h("p.ws-eyebrow.ws-nav-label", "Your workspace"),
    h("ul", SECTIONS.map((s) => h("li", h(`a.ws-nav-link${s.key === section.key ? ".is-active" : ""}`,
      { href: sectionHref(target, s.key), "aria-current": s.key === key ? "page" : (s.key === section.key ? "location" : null) }, icon(s.icon), h("span", s.label))))));
  const picker = h("select.ws-picker", { "aria-label": "Workspace section" },
    SECTIONS.map((s) => h("option", { value: s.key, selected: s.key === section.key }, s.label)),
    support ? h("option", { value: support.key, selected: true }, `› ${support.label}`) : null);
  picker.addEventListener("change", () => ctx.go(`pathway/${encodeURIComponent(target)}/${picker.value}`));
  // One place for the saved-plan status and the refresh action. "Refresh plan" saves a new version of the plan from the
  // current confirmed profile and role knowledge (the earlier version is kept); the goal is not changed.
  const update = button("Refresh plan", { variant: "ghost", small: true, attrs: { "aria-describedby": "ws-refresh-hint" } });
  update.addEventListener("click", () => busy(update, "Refreshing…", ws.replan(null)));
  const updatedNotice = cache.status === "updated" && cache.previous
    ? notice(`Your profile or Trace's role knowledge changed since your previous plan (saved ${dateOf(cache.previous.created_at)}), `
      + "so Trace saved an updated plan. Your earlier plan is kept.", { tone: "info", role: "status" }) : null;
  mount(root, h("section.ws-page",
    h("aside.ws-side", nav),
    h("div.ws-main",
      h("div.ws-topbar",
        h("nav.ws-crumbs", { "aria-label": "Breadcrumb" }, h("a", { href: sectionHref(target, "overview") }, "My plan"),
          h("span", { "aria-hidden": "true" }, "›"),
          support ? [h("a", { href: sectionHref(target, section.key) }, section.label), h("span", { "aria-hidden": "true" }, "›"),
            h("span", { "aria-current": "page" }, support.label)] : h("span", { "aria-current": "page" }, section.label)),
        h("label.visually-hidden", { for: "ws-picker" }, "Workspace section"),
        Object.assign(picker, { id: "ws-picker" }),
        h("div.ws-plan-meta",
          h("span.ws-saved", h("span.ws-dot", { "aria-hidden": "true" }), "Saved plan · ",
            dateOf(cache.plan.created_at, { day: "numeric", month: "short", year: "numeric" })),
          update,
          h("span.visually-hidden", { id: "ws-refresh-hint" }, "Saves a new version of your plan from your current confirmed profile. "
            + "Your earlier plan is kept and your goal is unchanged."))),
      updatedNotice,
      h("div.ws-content", VIEWS[key].render(ws), key === "overview" ? scopeQuestion(ws) : null, support ? null : method(ws)))));
  const title = root.querySelector(".ws-title");
  if (title) title.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

/** Senior PM only (D31): whether the role the user wants covers several products. It decides one thing: whether "Scope:
 *  several products or a complex, high-risk product" (L1) counts towards the recommended route. Asked in plain words. */
function scopeQuestion(ws) {
  if (ws.plan.assessment.target_level !== "senior_pm") return null;
  const scoped = Boolean(ws.plan.constraints && ws.plan.constraints.portfolio_scope);
  const yes = button("Yes, several products or areas", { variant: scoped ? "primary" : "secondary", small: true, attrs: { "aria-pressed": String(scoped) } });
  const no = button("No, one product, or not sure", { variant: scoped ? "secondary" : "primary", small: true, attrs: { "aria-pressed": String(!scoped) } });
  if (!scoped) yes.addEventListener("click", () => busy(yes, "Updating…", ws.replan(true)));
  if (scoped) no.addEventListener("click", () => busy(no, "Updating…", ws.replan(false)));
  return h("section.ws-card.scope-card", { "aria-labelledby": "scope-title" },
    h("h2.ws-h3", { id: "scope-title" }, "Would the Senior PM role you want be responsible for several products or product areas, "
      + "rather than one?"),
    h("p.muted", "Why it matters: some Senior PM roles are about owning several products, or one complex, high-risk product. If "
      + "yours is, your plan also checks for experience at that scope and shows how to build it. If not, it doesn't."),
    h("div.actions", { role: "group", "aria-label": "Scope of the role you want" }, yes, no));
}

function method(ws) {
  const { plan, rm } = ws;
  const sources = sourceIndex(plan);
  const learning = [];
  for (const p of rm.development) for (const m of p.learn) for (const r of m.resources) if (!learning.some((x) => x.id === r.id)) learning.push(r);
  const v = plan.versions;
  return h("section.ws-method", { "aria-label": "Sources and how this plan was made" },
    disclosure("Sources and how this plan was made", h("div.ws-stack",
      h("ul.plain-list",
        h("li", h("strong", "Your confirmed profile. "), "Statements about you come only from records you reviewed; the areas attached to them are your own choices."),
        h("li", h("strong", "Role knowledge. "), "What the role involves comes from the role sources below."),
        h("li", h("strong", "Trace guidance. "), "Learning plans, exercises, bridge-role descriptions and sequencing are curated by Trace. "
          + "Learning resources were checked on their providers' pages on the date shown.")),
      Object.keys(sources).length ? disclosure("Role sources", h("ul.plain-list.source-list", Object.values(sources).map((s) => h("li",
        s.url ? h("a", { href: s.url, target: "_blank", rel: "noopener noreferrer" }, s.title || s.publisher) : (s.title || s.publisher),
        s.publisher && s.title ? h("span.muted.small", ` · ${s.publisher}`) : null, s.attribution ? h("span.muted.small", ` · ${s.attribution}`) : null))),
      { className: "subtle" }) : null,
      learning.length ? disclosure("Learning resources", h("ul.plain-list.source-list", learning.map((r) => h("li",
        h("a", { href: r.url, target: "_blank", rel: "noopener noreferrer" }, r.title), h("span.muted.small", ` · ${r.provider} · checked ${dateOf(r.verified_on)}`,
          r.licence ? ` · ${r.licence}` : "")))), { className: "subtle" }) : null,
      disclosure("Versions", h("dl.version-list", [["Planner", v.engine], ["Role knowledge", v.knowledge_pack], ["Bridge roles", v.bridge_catalogue],
        ["Actions", v.action_templates], ["Learning guidance", rm.learning_version]].filter(([, x]) => x).map(([k, x]) => [h("dt", k), h("dd", x)])),
      { className: "subtle" })), { className: "subtle" }));
}
