// Career Origins & Transitions: how people in a research sample of India-based product managers moved into and through
// product management, beside (never inside) the user's own plan. Presentation only: every count, share, median, flag
// and suppression state comes from /api/v1/career-origins; nothing is counted, estimated or re-derived here. Hidden and
// banded groups stay hidden. What was observed in the sample is always kept apart from the user's personal plan.
// Every chart has a table alternative built from the same API objects and the same text helpers (cellText, hiddenText),
// so the chart and its table can't disagree. Nothing here names where the research came from.
import { h } from "../../dom.js";
import { disclosure } from "../../ui.js";
import { icon, pageHead, sectionHref, sectionTitle, textLink } from "./common.js";

const FLAG = { descriptive: "Descriptive", indicative: "Indicative", insufficient: "Too few to describe" };
const STATEMENT = { observed_fact: "Observed in the research sample", derived_pattern: "Pattern derived from dated histories" };
const months = (m) => `${m} months`;

/** In-page navigation without touching the URL hash (the hash is the app's router). */
function jump(label, id) {
  return h("button.co-jump", { type: "button", onclick: () => {
    const el = document.getElementById(id);
    if (!el) return;
    el.scrollIntoView({ block: "start" });
    el.setAttribute("tabindex", "-1");
    el.focus({ preventScroll: true });
  } }, label);
}

function flagChip(f) {
  return f === "descriptive" ? null : h(`span.ws-chip.co-flag${f === "insufficient" ? ".is-caution" : ""}`, FLAG[f]);
}

function hiddenText(b, noun = "profiles") {
  if (b.display === "band" && b.band === "5–9") return `5–9 ${noun} · too few to describe`;
  if (b.display === "band") return "Number withheld so a smaller group can't be worked out";
  return `Fewer than 5 ${noun} · not shown`;
}

/** The value of one group as text, identical in a chart and in its table. */
const cellText = (b, denominator) => (b.display === "exact" ? { share: `${b.share_pct}%`, count: `${b.count} of ${denominator}` } : null);

/** The accessible table alternative for a categorical chart: the same buckets, the same wording, no extra detail. */
function bucketTable(m) {
  return disclosure("Show as a table", h("div.co-table-wrap", h("table.co-table", { "data-co-table": m.key },
    h("caption.visually-hidden", `${m.title}. ${m.population_label}: ${m.denominator}.`),
    h("thead", h("tr", h("th", { scope: "col" }, "Group"), h("th", { scope: "col" }, "Share"),
      h("th", { scope: "col" }, "Count"), h("th", { scope: "col" }, "Sample size"))),
    h("tbody", m.buckets.map((b) => {
      const v = cellText(b, m.denominator);
      return h("tr", { "data-key": b.key }, h("th", { scope: "row" }, b.label),
        v ? [h("td", v.share), h("td", v.count)] : h("td", { colspan: "2" }, hiddenText(b)),
        h("td", FLAG[b.flag]));
    })))), { className: "subtle.co-table-toggle" });
}

/** One category: an exact count and share with a bar, or a stated band with no bar. */
function barRow(b, denominator) {
  const exact = b.display === "exact";
  const v = cellText(b, denominator);
  return h(`li.co-bar${exact ? "" : ".is-hidden"}`, { "data-key": b.key },
    h("div.co-bar-label", h("span.co-bar-name", b.label), flagChip(b.flag)),
    exact ? h("div.co-bar-track", { "aria-hidden": "true" }, h("span.co-bar-fill", { style: { width: `${Math.max(b.share_pct, 1)}%` } })) : null,
    h("div.co-bar-value", exact ? [h("strong", v.share), h("span.muted.small", ` ${v.count}`)]
      : h("span.muted.small", hiddenText(b))),
    b.explanation ? h("p.co-bar-why.small.muted", b.explanation) : null);
}

function metricCard(m, { lead = null } = {}) {
  return h("article.ws-card.co-metric", { "aria-labelledby": `co-${m.key}` },
    h("p.ws-eyebrow", STATEMENT[m.statement]),
    h("h3.ws-h3", { id: `co-${m.key}` }, m.title),
    lead ? h("p.small", lead) : null,
    h("p.muted.small", `${m.population_label}: ${m.denominator}`, m.denominator_flag !== "descriptive" ? ` · ${FLAG[m.denominator_flag]}` : ""),
    h("ul.co-bars", { role: "list", "data-co-chart": m.key }, m.buckets.map((b) => barRow(b, m.denominator))),
    bucketTable(m),
    m.notes.length ? h("ul.co-notes.small", m.notes.map((n) => h("li", n))) : null,
    disclosure("Definition", h("p.small", m.definition), { className: "subtle" }));
}

function factLine(f) {
  return h("li.co-fact",
    h("span", f.label),
    f.display === "exact" ? h("strong", `${f.count} of ${f.denominator}`, h("span.muted.small", ` · ${f.share_pct}%`))
      : h("span.muted.small", hiddenText(f)));
}

/** A stacked two-part bar (same employer vs new employer): widths are the API's own shares. */
function splitBar(m) {
  const parts = m.buckets.filter((b) => b.display === "exact");
  return h("article.ws-card.co-metric", { "aria-labelledby": `co-${m.key}` },
    h("p.ws-eyebrow", STATEMENT[m.statement]),
    h("h3.ws-h3", { id: `co-${m.key}` }, m.title),
    h("p.muted.small", `${m.population_label}: ${m.denominator}`),
    h("div.co-split", { "aria-hidden": "true" }, parts.map((b, i) => h(`span.co-split-part.is-${i}`, { style: { width: `${b.share_pct}%` } }))),
    h("ul.co-legend", { role: "list", "data-co-chart": m.key }, m.buckets.map((b, i) => {
      const v = cellText(b, m.denominator);
      return h("li", { "data-key": b.key }, v ? h(`span.co-swatch.is-${parts.indexOf(b)}`, { "aria-hidden": "true" }) : null,
        h("span", b.label), v ? [h("strong", ` ${v.share}`), h("span.muted.small", ` ${v.count}`)] : h("span.muted.small", ` ${hiddenText(b)}`));
    })),
    bucketTable(m),
    m.notes.length ? h("ul.co-notes.small", m.notes.map((n) => h("li", n))) : null,
    disclosure("Definition", h("p.small", m.definition), { className: "subtle" }));
}

function durationRow(d) {
  return h("tr",
    h("th", { scope: "row" }, d.label),
    d.display === "exact"
      ? [h("td", String(d.n)), h("td", months(d.median_months)), h("td", d.iqr_months ? `${d.iqr_months[0]}–${d.iqr_months[1]} months` : "—"),
        h("td", FLAG[d.flag])]
      : [h("td", d.band === "5–9" ? "5–9" : "Fewer than 5"), h("td", { colspan: "2" }, "Not shown: too few to describe"), h("td", FLAG[d.flag])]);
}

function timeCard(t) {
  const o = t.overall;
  return h("article.ws-card.co-metric", { "aria-labelledby": "co-time" },
    h("p.ws-eyebrow", STATEMENT[t.statement]),
    h("h3.ws-h3", { id: "co-time" }, t.title),
    h("p.co-headline", h("strong", months(o.median_months)), " median, with the middle half between ",
      `${o.iqr_months[0]} and ${o.iqr_months[1]} months`, h("span.muted.small", ` · ${o.n} profiles`)),
    h("div.co-table-wrap.co-table-scroll", { role: "region", tabindex: "0", "aria-label": "Time to the first PM role by primary background (scrolls sideways on small screens)" },
      h("table.co-table",
      h("caption.visually-hidden", "Time to the first PM role by primary background"),
      h("thead", h("tr", h("th", { scope: "col" }, "Primary background"), h("th", { scope: "col" }, "Profiles"),
        h("th", { scope: "col" }, "Median"), h("th", { scope: "col" }, "Middle half"), h("th", { scope: "col" }, "Sample"))),
      h("tbody", t.by_background.map(durationRow)))),
    h("p.co-scroll-hint.small.muted", { "aria-hidden": "true" }, "Scroll the table sideways to see every column."),
    h("ul.co-notes.small", t.notes.map((n) => h("li", n))),
    disclosure("Definition", h("p.small", t.definition), { className: "subtle" }));
}

const stepText = (s) => (s.display === "exact"
  ? { median: months(s.median_months), range: s.iqr_months ? `${s.iqr_months[0]}–${s.iqr_months[1]} months` : null, n: `${s.n} profiles` }
  : null);

function progressionTable(p) {
  return disclosure("Show as a table", h("div.co-table-wrap", h("table.co-table", { "data-co-table": "progression" },
    h("caption.visually-hidden", `${p.title}. ${p.population_label}.`),
    h("thead", h("tr", h("th", { scope: "col" }, "Step"), h("th", { scope: "col" }, "Median"),
      h("th", { scope: "col" }, "Middle half"), h("th", { scope: "col" }, "Profiles"), h("th", { scope: "col" }, "Sample size"))),
    h("tbody", p.steps.map((s) => {
      const v = stepText(s);
      return h("tr", { "data-key": s.key }, h("th", { scope: "row" }, `${s.from} → ${s.to}`),
        v ? [h("td", v.median), h("td", v.range || "Not shown (fewer than 30)"), h("td", v.n)] : h("td", { colspan: "3" }, hiddenText(s)),
        h("td", FLAG[s.flag]));
    })))), { className: "subtle.co-table-toggle" });
}

function progressionCard(p) {
  return h("article.ws-card.co-metric",                      // the section heading names it; no duplicate title
    h("p.ws-eyebrow", STATEMENT[p.statement]),
    h("p.muted.small", p.population_label),
    h("ol.co-timeline", { "data-co-chart": "progression" }, p.steps.map((s) => {
      const v = stepText(s);
      return h(`li${v ? "" : ".is-hidden"}`, { "data-key": s.key },
        h("span.co-tl-dot", { "aria-hidden": "true" }),
        h("div",
          h("p.ws-strong", `${s.from} → ${s.to}`),
          v ? h("p.small", h("strong", `${v.median} median`), v.range ? ` · middle half ${v.range}` : "",
            h("span.muted", ` · ${v.n}`), " ", flagChip(s.flag))
            : h("p.small.muted", hiddenText(s))));
    })),
    progressionTable(p),
    h("ul.co-notes.small", p.notes.map((n) => h("li", n))),
    disclosure("Definition", h("p.small", p.definition), { className: "subtle" }));
}

/** Routes by background (artifact 2.1.0): for each background group of 10 or more, how its people moved into their first
 *  PM role. Same bucket rules and table alternative as every other chart. */
function routesCard(r) {
  return h("div.ws-stack", r.groups.map((g) => h("article.ws-card.co-metric", { "aria-labelledby": `co-rb-${g.key}`, "data-co-group": g.key },
    h("p.ws-eyebrow", STATEMENT[r.statement]),
    h("h3.ws-h3", { id: `co-rb-${g.key}` }, g.label),
    h("p.muted.small", `${g.n} people with this main background`, g.flag !== "descriptive" ? ` · ${FLAG[g.flag]}` : ""),
    g.breakdowns.map((b) => h("div.co-breakdown",
      h("h4.ws-h5", b.title),
      h("ul.co-bars", { role: "list", "data-co-chart": `${g.key}-${b.key}` }, b.buckets.map((x) => barRow(x, b.denominator))),
      bucketTable({ key: `${g.key}-${b.key}`, title: `${g.label}: ${b.title}`, population_label: "People in this group",
        denominator: b.denominator, buckets: b.buckets })))))
    .concat([h("p.small.muted", r.not_listed), h("ul.co-notes.small", r.notes.map((n) => h("li", n)))]));
}

// --------------------------------------------------------------------------- C. the personal connection
function connectionCard(ws, c) {
  const rm = ws.rm;
  const plan = h("div.co-plan",
    h("p.ws-eyebrow", "Your plan · personal"),
    h("p.ws-strong", rm.recommendation.headline),
    h("p.small.muted", "Built from your confirmed profile and Trace's role knowledge. The research doesn't change it."),
    textLink("Open your roadmap", sectionHref(ws.target, "roadmap")));
  let research;
  if (!c) {
    research = h("p", "The research isn't connected to your plan right now.");
  } else if (c.status === "mapped") {
    const p = c.primary_background;
    const t = c.time_to_first_pm;
    research = [
      h("p", c.mapping_basis),
      h("ul.co-facts", { role: "list" },
        factLine({ ...p, label: `Had '${c.function_label}' as their primary background` }),
        factLine({ ...c.preceding_role, label: "Moved into their first PM role directly from this group" }),
        t && t.display === "exact"
          ? h("li.co-fact", h("span", "Median time from earliest recorded role to first PM role"),
            h("strong", months(t.median_months), h("span.muted.small", ` · ${t.n} profiles`)))
          : h("li.co-fact", h("span", "Time to first PM role"), h("span.muted.small", "Too few profiles to describe"))),
      c.separate_note ? h("p.small", c.separate_note) : null,
      h("p.small.muted", c.text)];
  } else if (c.status === "already_in_field") {
    research = [h("p", c.text), jump("See progression within product management", "co-progression")];
  } else {
    research = h("p", c.text);
  }
  return h("div.co-connect",
    h("article.ws-card.co-observed", h("p.ws-eyebrow", "Observed in the research sample · not about you"), research),
    h("article.ws-card.co-personal", plan));
}

// --------------------------------------------------------------------------- the view
function aboutCard(o) {
  const r = o.research;
  return h("article.ws-card.co-about",
    h("div.ws-inline-head", icon("shield"), h("h2.ws-h4", r.label)),
    r.disclosure.map((x) => h("p", x)),
    h("p.small.muted", `${r.sample} ${r.histories_as_of}. Only group totals are shown: no names, contact details, `,
      "employers or individual career histories. Kept separate from your profile and from other Trace users' data."));
}

function methodCard(o) {
  const r = o.research;
  return h("section.ws-section", { "aria-labelledby": "co-method" },
    sectionTitle(null, "Limitations and method", null, null, "co-method"),
    h("article.ws-card",
      h("ul.co-notes", o.limitations.map((x) => h("li", x))),
      h("p.small", h("strong", "How numbers are shown. "), o.display_policy.suppression, " ", o.display_policy.statistics),
      disclosure("Definitions", h("dl.ws-dl", Object.entries(o.definitions).map(([k, v]) => [h("dt", k.replace(/_/g, " ")), h("dd", v)])), { className: "subtle" }),
      disclosure("Version", h("p.small",
        `Research version ${r.research_version} · classification ${r.taxonomy_version} · summary ${o.artifact_version}, generated ${o.generated_at.slice(0, 10)}.`),
        { className: "subtle" })));
}

export function render(ws) {
  const o = ws.origins;
  const head = pageHead("India PM career research", "Career origins & transitions",
    "How people in a research sample moved into product management, shown beside your own plan.", null, "Internal preview");
  if (!o || o.status !== "available") {
    return [head, h("article.ws-card", h("h2.ws-h3", "This research isn't available right now"),
      h("p", "Trace couldn't load the research summary, so nothing is shown in its place. Your plan isn't affected."),
      textLink("Back to your overview", sectionHref(ws.target, "overview")))];
  }
  const m = o.metrics;
  const f = o.facts;
  return [
    head,
    aboutCard(o),
    h("nav.co-toc", { "aria-label": "On this page" },
      jump("A. Where people came from", "co-origins"), jump("B. How they moved into PM", "co-transitions"),
      o.routes_by_background ? jump("Routes by background", "co-routes") : null,
      jump("Moving up", "co-progression"), jump("C. Your pathway", "co-yours")),
    h("section.ws-section", { "aria-labelledby": "co-origins" },
      sectionTitle("A", "Where people came from", null, null, "co-origins"),
      h("article.ws-card", h("h3.ws-h4", "What the histories contain"), h("ul.co-facts", { role: "list" }, o.coverage.map(factLine))),
      metricCard(m.primary_background),
      h("article.ws-card", h("p.ws-eyebrow", STATEMENT.observed_fact), h("h3.ws-h4", "Mixed and adjacent backgrounds"),
        h("ul.co-facts", { role: "list" }, factLine(f.multi_function), factLine(f.product_ownership_any), factLine(f.agile_delivery_any)),
        h("p.small.muted", "Product Ownership and Agile Delivery are counted separately from product management and from each other.")),
      metricCard(m.background_status)),
    h("section.ws-section", { "aria-labelledby": "co-transitions" },
      sectionTitle("B", "How they moved into product management", null, null, "co-transitions"),
      metricCard(m.preceding_function),
      metricCard(m.preceding_vs_primary),
      splitBar(m.entry_move),
      metricCard(m.pre_pm_role_count),
      metricCard(m.first_pm_level),
      timeCard(o.time_to_first_pm)),
    o.routes_by_background ? h("section.ws-section", { "aria-labelledby": "co-routes" },
      sectionTitle("B, by background", o.routes_by_background.title, null, o.routes_by_background.definition, "co-routes"),
      routesCard(o.routes_by_background)) : null,
    h("section.ws-section", { "aria-labelledby": "co-progression" },
      sectionTitle("B, continued", "Moving up within product management", null, null, "co-progression"),
      progressionCard(o.progression)),
    h("section.ws-section", { "aria-labelledby": "co-yours" },
      sectionTitle("C", "Your pathway", null, "The research and your plan are separate. Only your confirmed current "
        + "role is used to point at a research group, and only where the research's job-title groups clearly cover it.", "co-yours"),
      connectionCard(ws, o.connection)),
    methodCard(o),
  ];
}
