// Career insights: patterns across groups of career profiles, around a target role. Every number comes from
// /api/v1/insights (counts, denominators, percentages, "in your profile" markers); nothing is recalculated here, and
// nothing the API withheld is rebuilt. The data source is chosen by the server (its data mode), never by the user:
// fictional test profiles in internal testing, profiles shared by members in production. They are never combined, and
// when there aren't enough profiles the page says so instead of falling back to anything else.
import { api } from "../api.js";
import { h, mount, uid } from "../dom.js";
import { metricBars, nothingRecorded, statFigure } from "../charts.js";
import { SMALL_SAMPLE, sourceOf } from "../copy.js";
import { disclosure, errorState, loading, pageHeader } from "../ui.js";
import { rememberContext } from "../state.js";

const SECTION_SUMMARY = {
  background: "Education levels, fields of study, institutions and grades.",
  employers: "Industries, employers and the kinds of organisation people worked in.",
  transitions: "The roles people held before this one, and the moves they made.",
  experience: "Areas of responsibility, skills and certifications in their careers.",
};

// Plainer headings for some metrics (the API's own title stays the definition's subject).
const TITLE = {
  roles_before_target: "What roles came immediately before this one?",
  move_type: "Common ways people reached this role",
  prior_roles: "Earlier roles in their careers",
  common_moves: "Common career moves",
  responsibility_areas: "Areas of responsibility in their experience",
};
const titled = (m) => (TITLE[m.key] ? { ...m, title: TITLE[m.key] } : m);

// Written-out sentences for categories that can't be shown (no generated grammar).
const TOO_FEW = {
  qualification_level: "Not enough profiles to show qualification patterns.",
  field_of_study: "Not enough profiles to show fields of study.",
  institution: "Not enough profiles to show universities and colleges.",
  grades: "Not enough profiles to show recorded grades.",
  industry: "Not enough profiles to show industry patterns.",
  employer: "Not enough profiles to show employers.",
  company_model: "Not enough profiles to show types of organisation.",
  company_size: "Not enough profiles to show organisation-size patterns.",
  roles_before_target: "Not enough profiles to show which roles came immediately before this one.",
  move_type: "Not enough profiles to show common ways people reached this role.",
  prior_roles: "Not enough profiles to show earlier career roles.",
  common_moves: "Not enough profiles to show common career moves.",
  responsibility_areas: "Not enough profiles to show areas of responsibility.",
  skills: "Not enough profiles to show skill patterns.",
  certifications: "Not enough profiles to show certifications.",
};
const NOT_RECORDED = {
  qualification_level: "qualifications", field_of_study: "fields of study", institution: "universities and colleges",
  grades: "grades", industry: "industries", employer: "employers", company_model: "types of organisation",
  company_size: "organisation size", roles_before_target: "earlier roles", move_type: "how people moved into the role",
  prior_roles: "earlier roles", common_moves: "career moves", responsibility_areas: "areas of responsibility", skills: "skills",
  certifications: "certifications",
};
const FIGURE = {
  postgraduate: ["Not enough profiles to show how many hold a postgraduate qualification.", "The postgraduate figure"],
  mba: ["Not enough profiles to show how many hold an MBA.", "The MBA figure"],
};

/** Whether a count is below the server's moderate-sample setting (scope.min_target_cohort). Not a reliability measure. */
const isSmall = (insights, n) => typeof n === "number" && typeof insights.scope.min_target_cohort === "number"
  && n < insights.scope.min_target_cohort;

/** Which population the patterns describe, from the API's scope (never computed here). */
export function populationNote(insights) {
  const s = insights.scope;
  if (s.scope_used !== "whole_sample") return null;
  const held = s.target_cohort_size != null ? `Only ${s.target_cohort_size} profiles held ${insights.target.label}`
    : `Fewer than ${insights.min_cell} profiles held ${insights.target.label}`;
  const wider = s.sample_size != null ? ` (${s.sample_size} profiles)` : "";
  return h("p.population-note", `Not enough profiles are available for your target role alone, so this uses the wider `
    + `available sample${wider}. ${held}.`);
}

/** The source of the patterns, always next to them. */
export function sourceNote(insights) {
  const src = sourceOf(insights.cohort);
  return h(`div.cohort-note${insights.is_synthetic ? ".is-synthetic" : ".is-real"}`, { role: "note" },
    h("span.cohort-dot", { "aria-hidden": "true" }), h("span", src.note));
}

function notEnough(insights) {
  return h("div.card.state-card",
    h("div.state-icon.is-neutral", { "aria-hidden": "true" }),
    h("h3.card-title", "Not enough career profiles yet"),
    h("p", "We need more career profiles before we can show reliable patterns for this role."),
    insights && insights.is_synthetic ? h("p.muted.small", sourceOf("synthetic").note) : null);
}

/**
 * Loads and draws the career patterns for `target` into `slot`. Calls onInsights(insights|null) when loaded, so the
 * caller can compare the user's experience using the same response.
 */
export async function drawLandscape(slot, { target, onInsights = () => {} }) {
  mount(slot, loading("Loading career patterns"));
  let insights;
  try {
    insights = await api.insights(target);
  } catch (err) {
    mount(slot, errorState(err, () => drawLandscape(slot, { target, onInsights })));
    onInsights(null);
    return;
  }
  if (insights.status !== "ready") {
    mount(slot, notEnough(insights));
    onInsights(null);
    return;
  }
  mount(slot, landscapeView(insights));
  onInsights(insights);
}

function aboutData(insights) {
  const s = insights.scope;
  const size = s.sample_size != null ? `${s.sample_size} profiles` : `fewer than ${insights.min_cell} profiles`;
  return disclosure("About this data", h("div.reading",
    h("p", insights.disclosure),
    h("p", `${s.scope_used === "whole_sample" ? "Based on all available profiles" : `Based on profiles that held ${insights.target.label}`}`
      + ` (${size}). ${s.note}`),
    h("p", `Groups smaller than ${insights.min_cell} profiles are never shown. Percentages are of the profiles that have the `
      + "detail recorded, and each figure shows its count and total."),
    insights.limitations.length ? h("ul", insights.limitations.map((l) => h("li", l))) : null,
    h("p.muted.small", insights.your_profile_basis)), { className: "subtle" });
}

export function landscapeView(insights) {
  const tabs = h("div.tabs", { role: "tablist", "aria-label": "Insight categories" });
  const panel = h("div.tab-panel", { role: "tabpanel", tabindex: "0" });
  const buttons = [];
  // Mobile (CSS): a compact selector with all four categories instead of the tab row.
  const pick = h("select.tab-select", { id: uid("cat"), "aria-label": "Insight category" },
    insights.sections.map((s, i) => h("option", { value: String(i) }, s.title)));
  pick.addEventListener("change", () => show(Number(pick.value)));
  const show = (index) => {
    pick.value = String(index);
    buttons.forEach((b, i) => {
      b.setAttribute("aria-selected", String(i === index));
      b.tabIndex = i === index ? 0 : -1;
    });
    panel.setAttribute("aria-labelledby", buttons[index].id);
    drawSection(panel, insights.sections[index], insights);
  };
  insights.sections.forEach((s, i) => {
    const b = h("button.tab", { type: "button", role: "tab", id: uid("tab"), "aria-selected": "false", tabindex: "-1" }, s.title);
    b.addEventListener("click", () => show(i));
    b.addEventListener("keydown", (e) => {
      const n = insights.sections.length;
      const next = e.key === "ArrowRight" ? (i + 1) % n : e.key === "ArrowLeft" ? (i - 1 + n) % n
        : e.key === "Home" ? 0 : e.key === "End" ? n - 1 : null;
      if (next !== null) {
        e.preventDefault();
        show(next);
        buttons[next].focus();
      }
    });
    buttons.push(b);
    tabs.append(b);
  });
  if (buttons.length) show(0);
  const s = insights.scope;
  return h("div.landscape",
    sourceNote(insights),
    h("p.scope-meta", s.sample_size != null
      ? (s.scope_used === "whole_sample" ? `All available profiles · ${s.sample_size} profiles`
        : `Profiles that held ${insights.target.label} · ${s.target_cohort_size != null ? s.target_cohort_size : s.sample_size} profiles`)
      : `Fewer than ${insights.min_cell} profiles`),
    populationNote(insights),
    h("div.tab-select-wrap", h("label.control-label", { for: pick.id }, "Category"), pick),
    tabs, panel,
    aboutData(insights));
}

function drawSection(panel, section, insights) {
  const figures = section.headlines.filter((x) => x.status === "shown");
  const hiddenFigures = section.headlines.filter((x) => x.status !== "shown");
  const shown = section.metrics.filter((m) => m.status === "shown" && !nothingRecorded(m));
  const tooFew = section.metrics.filter((m) => m.status !== "shown" && !nothingRecorded(m));
  const unrecorded = section.metrics.filter(nothingRecorded);
  const unavailable = [
    ...tooFew.map((m) => TOO_FEW[m.key] || "Not enough profiles to show this category."),
    ...hiddenFigures.map((x) => {
      const [few, name] = FIGURE[x.key] || ["Not enough profiles to show this figure.", "This figure"];
      return x.status === "suppressed"
        ? `${name} is withheld: together with the figure beside it, it would reveal a group smaller than ${insights.min_cell}.`
        : few;
    }),
    unrecorded.length ? `These details aren't recorded in the available profiles: ${unrecorded.map((m) => NOT_RECORDED[m.key] || "other details").join(", ")}.` : null,
  ].filter(Boolean);
  const withCaution = (el, n) => {
    if (isSmall(insights, n)) el.append(h("p.sample-caution", SMALL_SAMPLE));
    return el;
  };
  mount(panel,
    h("p.panel-summary", SECTION_SUMMARY[section.key] || ""),
    figures.length ? h("div.figures", figures.map((x) => withCaution(statFigure(x), x.denominator))) : null,
    shown.length ? h("div.metrics", shown.map((m) => withCaution(metricBars(titled(m), { minCell: insights.min_cell }), m.denominator))) : null,
    unavailable.length ? h("div.unrecorded", { role: "note" },
      h("p.unrecorded-title", "Not shown"),
      h("ul.plain-list.small", unavailable.map((t) => h("li", t)))) : null,
    !figures.length && !shown.length && !unavailable.length ? h("p.muted", "Nothing to show in this category.") : null);
}

// --------------------------------------------------------------------------- how your experience compares
/**
 * Where the confirmed profile matches common patterns, what is common but not yet shown, and where we need more
 * information. The two main groups size to their content (no equal-height cards); the most common items come first
 * (the API's order) and the rest are one click away; limitations are a compact strip. Nothing is computed here.
 */
export function profileInContext(insights, pathway, areaLabel = {}) {
  const pick = (key) => insights.sections.flatMap((s) => s.metrics).find((m) => m.key === key);
  const rows = [["responsibility_areas", "Areas of responsibility"], ["skills", "Skills"], ["industry", "Industries"],
    ["qualification_level", "Qualifications"], ["field_of_study", "Fields of study"]];
  const TOP = 3;
  const have = [];
  const notShown = [];
  const notShownMore = [];
  for (const [key, title] of rows) {
    const m = pick(key);
    if (!m || m.status !== "shown") continue;
    const yes = m.buckets.filter((b) => b.in_your_profile === true);
    const no = m.buckets.filter((b) => b.in_your_profile === false);
    if (yes.length) have.push(group(title, yes, m));
    if (no.length) notShown.push(group(title, no.slice(0, TOP), m));
    if (no.length > TOP) notShownMore.push(group(title, no.slice(TOP), m));
  }
  const top = pathway && pathway.routes && pathway.routes[0];
  const transfers = top ? top.fit.matched_areas : [];
  const uncategorised = pathway && pathway.candidate_evidence ? pathway.candidate_evidence.classified_areas.length === 0 : false;
  const more = [];
  if (uncategorised) more.push("Your responsibilities haven't been categorised yet.");
  for (const [key, title] of rows) {
    const m = pick(key);
    if (m && m.status !== "shown" && !nothingRecorded(m)) more.push(`${title}: not enough profiles to compare.`);
  }
  const basis = insights.scope.scope_used === "whole_sample"
    ? "Compared with the wider available sample, using only the details you've confirmed."
    : `Compared with profiles that held ${insights.target.label}, using only the details you've confirmed.`;
  return h("div.context",
    h("p.context-basis", basis),
    populationNote(insights),
    uncategorised ? h("div.context-alert", { role: "note" },
      h("p", "We couldn't compare your responsibilities yet. Categorising your experience will help make this comparison more complete."),
      h("a", { href: "#/profile" }, "Categorise your experience")) : null,
    h("div.context-main",
      h("section.context-col.is-have", { "aria-labelledby": "ctx-have" },
        h("h3.context-title", { id: "ctx-have" }, "Experience you already have"),
        transfers.length ? h("div.context-group",
          h("p.context-group-title", "Carries over to this path"),
          h("ul.tag-list", transfers.map((a) => h("li.tag.tag-strong", areaLabel[a] || a)))) : null,
        have.length || transfers.length ? have : h("p.empty-note", "None of the common patterns here appear in your confirmed details yet.")),
      h("section.context-col.is-gap", { "aria-labelledby": "ctx-gap" },
        h("h3.context-title", { id: "ctx-gap" }, "Common experience not yet shown in your profile"),
        h("p.muted.small", "Not recorded in your profile doesn't mean you lack the experience."),
        notShown.length ? notShown : h("p.empty-note", "Nothing common here is missing from your confirmed details."),
        notShownMore.length ? disclosure("Show the full comparison", notShownMore, { className: "subtle" }) : null)),
    h("div.context-limits", { role: "note", "aria-labelledby": "ctx-more" },
      h("h3.context-limits-title", { id: "ctx-more" }, "Where we need more information"),
      more.length ? h("ul.plain-list.small", more.map((t) => h("li", t)))
        : h("p.muted.small", "Nothing. Every comparison above could be made.")));
}

function group(title, buckets, metric) {
  return h("div.context-group",
    h("p.context-group-title", title),
    h("ul.context-list", buckets.map((b) => h("li",
      h("span", b.label),
      h("span.muted.small", `${b.share_pct}% · ${b.count} of ${metric.denominator}`)))));
}

// --------------------------------------------------------------------------- the page
export async function render(root, ctx) {
  const body = h("div.stack");
  mount(root, h("section.page",
    pageHeader("Career insights", "Explore career patterns",
      "See the experience, education, industries and career moves commonly found among people who reached your target role."),
    body));
  root.querySelector(".page-title").focus({ preventScroll: true });
  mount(body, loading("Loading roles"));
  let roles;
  try {
    roles = await api.targetRoles();
  } catch (err) {
    mount(body, errorState(err, () => render(root, ctx)));
    return;
  }
  const roleSelect = h("select", { id: uid("role") },
    h("option", { value: "" }, "Choose a role…"),
    roles.map((r) => h("option", { value: r.role_id, selected: r.role_id === ctx.app.target }, r.label)));
  const slot = h("div.landscape-slot");
  const foot = h("div");
  const draw = () => {
    if (!roleSelect.value) {
      mount(slot, h("div.card.state-card", h("h3.card-title", "Choose a role to explore"),
        h("p", "Patterns are shown around a role: who held it, what they did before, and what their careers look like.")));
      mount(foot);
      return;
    }
    drawLandscape(slot, { target: roleSelect.value });
    mount(foot, ctx.state.hasRecords
      ? h("p.page-foot", h("a", { href: `#/pathway/${encodeURIComponent(roleSelect.value)}` }, "See your career path to this role →"))
      : null);
  };
  roleSelect.addEventListener("change", () => {
    if (roleSelect.value) {
      ctx.app.target = roleSelect.value;
      rememberContext(ctx.app, `#/insights/${encodeURIComponent(roleSelect.value)}`);
    }
    draw();
  });
  mount(body,
    h("div.controls", h("div.control", h("label.control-label", { for: roleSelect.id }, "Target role"), roleSelect)),
    slot, foot);
  draw();
}
