// My career path: 1. your pathway, 2. how your experience compares, 3. career patterns behind this path, 4. what you can
// do next. Routes, their order, fit tiers, gaps and guidance are the API's own; nothing is re-ranked, scored or
// estimated here. The wording follows the API's fit assessment exactly:
//   strong fit established (fit.tier "aligned")   -> "Your recommended pathway" / "Recommended path"
//   any other tier                                -> "A possible path to your target" with the API's fit label
//   no route                                      -> "No reliable pathway is available yet."
// A top-ranked route is not called a recommendation unless its fit is strong.
import { api } from "../api.js";
import { h, mount } from "../dom.js";
import { routeTrack } from "../charts.js";
import { EXPERIENCE_STATE, FIT, FIT_LIMITATION, MOVE_DIRECTION, familyChangeLabel, SMALL_SAMPLE, plural, positionText, profilesCount, profilesNoun, sourceOf } from "../copy.js";
import { badge, disclosure, errorState, journey, loading, notice } from "../ui.js";
import { drawLandscape, profileInContext } from "./insights.js";

const isStrong = (route) => route.fit.tier === "aligned";
let cautionBelow = null;           // the response's sample_caution_below (an existing server setting), per render
const SECTIONS = [["routes", "Your pathway"], ["context", "How your experience compares"], ["landscape", "Career patterns"],
  ["next", "What you can do next"]];

function jumpTo(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "start" });         // scroll-margin keeps it below the sticky header
  const heading = el.querySelector("h2");
  if (heading) heading.focus({ preventScroll: true });
}

export async function render(root, ctx) {
  const target = ctx.targetId;
  const headerSlot = h("div.results-header");
  const recSlot = h("section.results-section", { id: "routes", "aria-labelledby": "routes-title" });
  const contextSlot = h("section.results-section", { id: "context", "aria-labelledby": "context-title" });
  const landSlot = h("section.results-section", { id: "landscape", "aria-labelledby": "landscape-title" });
  const nextSlot = h("section.results-section", { id: "next", "aria-labelledby": "next-title" });
  // Desktop: a row of links. Mobile (CSS): a compact "Jump to section" selector with all four sections.
  const jump = h("select.section-jump", { id: "section-jump", "aria-label": "Jump to section" },
    h("option", { value: "" }, "Jump to section…"), SECTIONS.map(([id, label]) => h("option", { value: id }, label)));
  jump.addEventListener("change", () => {
    if (jump.value) jumpTo(jump.value);
  });
  const sectionNav = h("nav.section-nav", { "aria-label": "On this page" },
    h("div.section-links", SECTIONS.map(([id, label]) => h("a", { href: `#/pathway/${encodeURIComponent(target)}`, "data-target": id }, label))),
    h("div.section-jump-wrap", h("label.visually-hidden", { for: "section-jump" }, "Jump to section"), jump));
  sectionNav.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-target]");
    if (!a) return;
    e.preventDefault();                                 // keep the hash route; scroll to the section instead
    jumpTo(a.dataset.target);
  });
  mount(root, h("section.page.results",
    journey("pathway", { profile: "#/profile", target: "#/target", pathway: `#/pathway/${encodeURIComponent(target)}` }),
    headerSlot, sectionNav, recSlot, contextSlot, landSlot, nextSlot));
  mount(headerSlot, loading("Finding your career path"));

  // The expected case (target = current role) is recognised before asking for a pathway, so no refused request is
  // made; the server still enforces it (409 target_is_current_role), handled the same way if the profile changed meanwhile.
  let position = null;
  try {
    position = await api.currentPosition();
  } catch {
    position = null;                                    // the pathway request below reports any real failure
  }
  if (position && position.status === "known" && position.role_id === target) return backToTarget(ctx);
  const [pathwayRes, areasRes] = await Promise.allSettled([api.pathway(target), api.areas()]);
  const areaLabel = areasRes.status === "fulfilled" ? Object.fromEntries(areasRes.value.map((a) => [a.code, a.label])) : {};
  if (pathwayRes.status === "rejected") {
    if (isCurrentRoleRefusal(pathwayRes.reason)) return backToTarget(ctx);
    mount(headerSlot, errorState(pathwayRes.reason, () => render(root, ctx)));   // every other failure, 409s included
    return;
  }
  const p = pathwayRes.value;
  cautionBelow = typeof p.sample_caution_below === "number" ? p.sample_caution_below : null;
  drawHeader(headerSlot, p);
  drawRoutes(recSlot, p, areaLabel, ctx);
  const title = root.querySelector(".page-title");
  if (title) title.focus({ preventScroll: true });

  mount(contextSlot,
    h("div.section-head",
      h("h2.section-title", { id: "context-title", tabindex: "-1" }, "How your experience compares"),
      h("p.section-lead", "See where your experience matches common patterns among people who reached your target role, and "
        + "where we need more information.")),
    loading("Comparing"));
  mount(landSlot,
    h("div.section-head",
      h("h2.section-title", { id: "landscape-title", tabindex: "-1" }, "Career patterns behind this path"),
      h("p.section-lead", `The backgrounds, moves and experience in the available profiles that reached ${p.target.canonical_title}.`)),
    h("div.landscape-slot"),
    h("p.page-foot", h("a", { href: `#/insights/${encodeURIComponent(target)}` }, "Explore all career insights for this role →")));
  drawNext(nextSlot, p);
  drawLandscape(landSlot.querySelector(".landscape-slot"), {
    target,
    onInsights: (insights) => {
      const head = contextSlot.querySelector(".section-head");
      mount(contextSlot, head, insights ? profileInContext(insights, p, areaLabel)
        : notice("A comparison needs enough career profiles for this role. There aren't enough yet.", { tone: "info" }));
    },
  });
}

// The target is the user's current role: a validation state, not an error. Never a self-transition or a substitute:
// the user goes back to target selection, which explains it inline.
export const isCurrentRoleRefusal = (err) => Boolean(err && err.status === 409 && err.code === "target_is_current_role");

function backToTarget(ctx) {
  ctx.app.target = null;
  ctx.app.flash = "target_is_current_role";
  ctx.go("target");
}

// --------------------------------------------------------------------------- header
function drawHeader(slot, p) {
  const known = p.current_position.status === "known";
  const src = sourceOf(p.cohort);
  mount(slot,
    h("p.eyebrow", "Step 3 · Your career path"),
    h("h1.page-title.journey-title", { tabindex: "-1" },
      h("span.journey-from", known ? p.current_position.canonical_title : "Your current role"),
      h("span.journey-arrow", { "aria-hidden": "true" }, "→"),
      h("span.visually-hidden", " to "),
      h("span.journey-to", p.target.canonical_title)),
    h("div.results-meta",
      h("p.journey-actions", h("a", { href: "#/target" }, "Change target role"), h("span.sep", { "aria-hidden": "true" }, "·"),
        h("a", { href: "#/profile" }, "Edit profile")),
      h("p.evidence-from", h("span.control-label", "Based on"), h("span", src.name))),
    p.is_synthetic_test_data
      ? h("div.cohort-note.is-synthetic", { role: "note" }, h("span.cohort-dot", { "aria-hidden": "true" }), h("span", src.note))
      : null);
}

// --------------------------------------------------------------------------- the pathway
function drawRoutes(slot, p, areaLabel, ctx) {
  const heading = (text) => h("h2.section-title", { id: "routes-title", tabindex: "-1" }, text);
  if (p.current_position.status !== "known") {
    mount(slot, h("div.section-head", heading("Your pathway")),
      h("div.card.state-card",
        h("h3.card-title", "Confirm your current role to see a path"),
        h("p", positionText(p.current_position)),
        reviewRoleAction(p.current_position, ctx)));
    return;
  }
  if (!p.routes.length) {
    mount(slot, h("div.section-head", heading("No reliable pathway is available yet.")),
      h("div.card.state-card",
        // Worded the same whether no profile made this move or too few did: a small group is never revealed.
        h("h3.card-title", `Not enough career profiles show a route from ${p.current_position.canonical_title} to ${p.target.canonical_title}`),
        h("p", "Without enough profiles that show this move, Trace can't show a reliable path or say how well it would fit you. "
          + "That doesn't mean the move isn't possible. Your profile and target are saved, so you can come back as more career "
          + "profiles become available."),
        h("p.muted", "The career patterns below still show how the available profiles reached this role."),
        h("a.btn.btn-secondary", { href: "#/target" }, "Try another target role")));
    return;
  }
  const [best, ...rest] = p.routes;
  const tiedTop = p.routes.filter((r) => r.tied && r.rank_group === best.rank_group).length;
  tieGroupOfTop = best.rank_group;
  const heading2 = isStrong(best) ? (tiedTop > 1 ? "Your recommended pathways" : "Your recommended pathway")
    : (tiedTop > 1 ? "Possible paths to your target" : "A possible path to your target");
  mount(slot,
    h("div.section-head", heading(heading2),
      h("p.section-lead", p.routes.length === 1
        ? "One path to your target appears in the available profiles."
        : `${p.routes.length} paths to your target appear in the available profiles. They're ordered by how closely they match `
          + "your confirmed experience, then by fewer roles you haven't held."),
      tiedTop > 1 ? h("p.tie-note", { role: "note" }, "These paths are equally ranked based on the available evidence. "
        + "They're listed in a fixed order, not by preference.") : null),
    routeCard(best, areaLabel),
    rest.length ? alternatives(rest, areaLabel) : null,
    readingGuide(p));
}

// Product Phase 5: no most-observed badge. Choosing the most-observed route in the browser was client-side ranking by
// frequency, which Trace does not do (CLAUDE.md 6 and 8); this view is retired from navigation in favour of the report.

/** The direct action for an unknown starting point: an unconfirmed role match goes straight to that job's record. */
export function reviewRoleAction(position, ctx) {
  if (position.reason !== "role_unconfirmed") return h("a.btn.btn-primary", { href: "#/profile" }, "Go to your profile");
  return h("button.btn.btn-primary", { type: "button", onclick: () => { ctx.app.flash = "focus_current_job"; ctx.go("profile"); } },
    "Review the role match");
}

// The actual reasons fit couldn't be established (API fit.limitations), each in its own words.
function limitationList(fit) {
  const items = (fit.limitations || []).filter((k) => FIT_LIMITATION[k]);
  if (fit.tier !== "not_established" || !items.length) return null;
  return h("ul.fit-limitations", items.map((k) => h("li", FIT_LIMITATION[k],
    k === "incomplete_user_profile" ? [" ", h("a", { href: "#/profile" }, "Categorise your experience")] : null)));
}

/** The API's direction for each move, in words; null (unreliable or unknown seniority) stays unlabelled. */
const moveLabels = (route) => (route.step_directions || []).map((d, i) => (!d ? null
  : d === "career_track_change" ? familyChangeLabel((route.step_family_changes || [])[i]) : MOVE_DIRECTION[d] || null));

function fitBadge(route) {
  if (isStrong(route)) return badge("Recommended path", "positive");
  const fit = FIT[route.fit.tier];
  return badge(fit.label, fit.tone);
}

function routeCard(route, areaLabel) {
  const fit = FIT[route.fit.tier];
  return h("article.card.hero-route",
    h("div.hero-top",
      h("div.hero-badges", fitBadge(route)),
      h("p.hero-steps", route.route.length === 2 ? "Direct move" : plural(route.route.length - 1, "move", "moves"))),
    routeTrack(route.route, { stepGapIds: route.step_gaps.map((s) => s.role_id), moveLabels: moveLabels(route) }),
    h("div.hero-grid",
      h("div.hero-why",
        h("h3.block-title", "Why this path is shown"),
        h("p.fit-line", fit.text),
        limitationList(route.fit),
        bringList(route, areaLabel)),
      h("div.hero-evidence",
        observedBlock(route.observed),
        gapsBlock(route))),
    route.next_steps.length ? h("div.hero-next",
      h("h3.block-title", "Worth considering on this path"),
      h("ul.next-list", route.next_steps.slice(0, 2).map((s) => h("li", s.text))),
      h("p.guidance-label", route.next_steps[0].label)) : null);
}

function bringList(route, areaLabel) {
  const matched = route.fit.matched_areas;
  if (!matched.length) return null;
  return h("div.bring",
    h("h4.mini-title", "Experience you already have"),
    h("ul.tag-list", matched.map((a) => h("li.tag.tag-strong", areaLabel[a] || a))),
    route.fit.supporting_evidence.length ? disclosure("See the details from your profile",
      h("ul.quote-list", route.fit.supporting_evidence.slice(0, 4).map((s) => h("li",
        h("blockquote", s.evidence_text),
        h("p.quote-source", `${s.employment_title} · ${areaLabel[s.responsibility_area] || s.responsibility_area}`)))), { className: "subtle" }) : null);
}

function observedCount(o) {
  const syn = Boolean(o.is_synthetic_test_data);                    // the server's own source classification
  return typeof o.observation_count === "number"
    ? `This move appears in ${profilesCount(o.observation_count, syn)}`
    : `This move appears in fewer than 5 ${profilesNoun(syn)}`;
}

function observedBlock(o) {
  const small = typeof o.observation_count === "number" && cautionBelow !== null && o.observation_count < cautionBelow;
  return h("div.observed",
    h("h4.mini-title", "Appears in the available profiles"),
    h("p.observed-count", observedCount(o)),
    small || o.observation_count === null ? h("p.sample-caution", SMALL_SAMPLE) : null,
    h("p.muted.small", EXPERIENCE_STATE[o.experience_state] || ""),
    o.disclosed_areas.length ? h("ul.observed-areas", o.disclosed_areas.map((a) => h("li",
      h("span", a.label), h("span.muted.small", `${a.profile_count} profiles${a.is_common ? " · most profiles" : ""}`)))) : null);
}

function gapsBlock(route) {
  const items = [
    ...route.area_gaps.map((g) => h("li", h("strong", g.label), h("span.muted.small", g.note))),
    ...route.step_gaps.map((g) => h("li", h("strong", g.canonical_title), h("span.muted.small", g.note))),
  ];
  return h("div.gaps",
    h("h4.mini-title", "Not yet shown in your profile"),
    items.length ? h("ul.gap-list", items) : h("p.muted.small", route.fit.tier === "not_established"
      ? "Not compared for this path, so nothing is listed."
      : "Nothing on this path is missing from your confirmed details."));
}

let tieGroupOfTop = null;
function alternatives(routes, areaLabel) {
  const rows = routes.map((r) => {
    const fit = FIT[r.fit.tier];
    const detail = h("div.alt-detail",
      routeTrack(r.route, { stepGapIds: r.step_gaps.map((s) => s.role_id), moveLabels: moveLabels(r) }),
      h("p.fit-line", fitBadge(r), " ", fit.text), limitationList(r.fit),
      h("div.hero-grid", bringList(r, areaLabel) || h("div"), h("div", observedBlock(r.observed), gapsBlock(r))));
    return disclosure(h("span.alt-summary",
      h("span.alt-route", r.route.map((x) => x.canonical_title).join(" → ")),
      h("span.alt-meta",
        r.tied && r.rank_group === tieGroupOfTop ? badge("Equally ranked", "neutral") : null,
        h("span", r.route.length === 2 ? "Direct move" : plural(r.route.length - 1, "move", "moves")),
        h("span", isStrong(r) ? "Recommended path" : fit.label),
        h("span", typeof r.observed.observation_count === "number" ? profilesCount(r.observed.observation_count, r.observed.is_synthetic_test_data)
          : `Fewer than 5 ${profilesNoun(r.observed.is_synthetic_test_data)}`),
        r.step_gaps.length ? h("span", plural(r.step_gaps.length, "role you haven't held", "roles you haven't held")) : null)),
    detail, { className: "alt-route-item" });
  });
  return h("div.alternatives",
    h("h3.block-title", "Other possible paths"),
    h("p.muted.small", "A common path isn't automatically the best one for you, and neither is a short one."),
    rows);
}

function readingGuide(p) {
  return disclosure("How to read your career path", h("div.reading",
    h("p", p.interpretation_note),
    h("p", p.role_only_notice),
    p.evidence_notice ? h("p", p.evidence_notice) : null,
    p.limitations.length ? [h("h4.mini-title", "Limitations"), h("ul", p.limitations.map((l) => h("li", l)))] : null,
    h("h4.mini-title", "Not assessed"),
    h("ul", p.unavailable_assessments.map((l) => h("li", l)))), { className: "reading-guide" });
}

// --------------------------------------------------------------------------- what you can do next
function drawNext(slot, p) {
  const title = h("h2.section-title", { id: "next-title", tabindex: "-1" }, "What you can do next");
  const all = [...p.next_steps, ...(p.routes[0] ? p.routes[0].next_steps : [])];
  const seen = new Set();
  const unique = all.filter((s) => (seen.has(s.text) ? false : seen.add(s.text)));
  const career = unique.filter((s) => s.kind !== "evidence");          // career suggestions from the route evidence
  const profile = unique.filter((s) => s.kind === "evidence");         // profile enrichment tasks, kept separate
  mount(slot, h("div.section-head", title),
    career.length
      ? h("div.card.next-card",
        h("h3.block-title", "Suggestions from this path"),
        h("ol.next-list.numbered", career.slice(0, 5).map((s) => h("li", s.text))),
        h("p.guidance-label", career[0].label))
      : notice("There are no career suggestions for this target from your confirmed experience yet.", { tone: "info" }),
    profile.length
      ? h("div.card.next-card.is-profile",
        h("h3.block-title", "Improve your profile comparison"),
        h("p.muted.small", "These help Trace compare your experience more completely. They aren't career advice."),
        h("ul.next-list", profile.slice(0, 4).map((s) => h("li", s.text))))
      : null,
    h("div.page-actions", h("a.btn.btn-ghost", { href: "#/target" }, "Explore another target role"),
      h("a.btn.btn-secondary", { href: "#/profile" }, "Review your profile")));
}
