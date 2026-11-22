// Visual building blocks. Charts draw only numbers the API returned (count, denominator, share_pct); nothing is
// computed, estimated or re-derived here (no client-side statistics, so nothing the API withheld can be rebuilt).
// Every bar carries its percentage and "count of denominator" as visible text: nothing relies on colour or hover.
import { h } from "./dom.js";
import { badge } from "./ui.js";

/** Whether a metric has nothing recorded at all in its population (the API's denominator is zero). */
export const nothingRecorded = (metric) => metric.denominator === 0;

/**
 * Horizontal bars for one insight metric from /api/v1/insights.
 * States: shown | insufficient (too few with this recorded) | all_categories_small | nothing recorded in the profiles.
 */
export function metricBars(metric, { limit = null, minCell = 5, cohortNoun = "profiles" } = {}) {
  const head = h("div.metric-head",
    h("h4.metric-title", metric.title),
    h("p.metric-meta", populationText(metric, cohortNoun)));
  if (nothingRecorded(metric)) {
    return h("section.metric.is-empty", head,
      h("p.empty-note", "Not recorded in these profiles, so there is nothing to show."));
  }
  if (metric.status === "insufficient") {
    return h("section.metric.is-empty", head,
      h("p.empty-note", `Too few profiles have this recorded to show a reliable breakdown (fewer than ${minCell}).`));
  }
  if (metric.status === "all_categories_small") {
    return h("section.metric.is-empty", head,
      h("p.empty-note", `Every category here is shared by fewer than ${minCell} profiles, so none is shown individually.`));
  }
  const buckets = limit ? metric.buckets.slice(0, limit) : metric.buckets;
  const list = h("ul.bars", { role: "list" }, buckets.map((b) => barRow(b, metric.denominator)));
  if (!limit && metric.other_count) {
    list.append(barRow({ label: "Other categories combined", count: metric.other_count, share_pct: metric.other_share_pct,
      in_your_profile: null, other: true }, metric.denominator));
  }
  const foot = [];
  if (limit && metric.buckets.length > buckets.length) foot.push(`${metric.buckets.length - buckets.length} more shown in full view.`);
  if (!limit) {
    if (metric.more_categories) foot.push(`${metric.more_categories} further ${metric.more_categories === 1 ? "category" : "categories"} not shown.`);
    if (metric.small_categories) foot.push(`${metric.small_categories} ${metric.small_categories === 1 ? "category is" : "categories are"} shared by fewer than ${minCell} profiles and not shown.`);
    if (metric.multi_valued) foot.push("A profile can count in more than one category, so percentages can add up to more than 100%.");
    if (metric.note) foot.push(metric.note);
  }
  return h("section.metric", head, list,
    foot.length ? h("p.metric-foot", foot.join(" ")) : null,
    limit ? null : h("p.metric-definition", metric.definition));
}

function barRow(b, denominator) {
  const pct = Math.max(0, Math.min(100, b.share_pct));
  return h(`li.bar-row${b.other ? ".is-other" : ""}`,
    h("div.bar-label",
      h("span.bar-name", b.label),
      b.in_your_profile ? badge("In your profile", "accent") : null),
    h("div.bar-track", { "aria-hidden": "true" }, h("span.bar-fill", { style: { width: `${Math.max(pct, 1.5)}%` } })),
    h("div.bar-value", h("strong", `${pct}%`), h("span.bar-count", `${b.count} of ${denominator}`)));
}

function populationText(metric, noun) {
  const parts = [metric.population_label];
  if (metric.status === "shown") {
    parts.push(`${metric.denominator} with this recorded`);
    if (metric.not_recorded) parts.push(`${metric.not_recorded} not recorded`);
  }
  return parts.join(" · ");
}

/** A headline figure: share and count of denominator, or an honest withheld state. */
export function statFigure(item) {
  if (item.status !== "shown") {
    const why = item.status === "suppressed"
      ? "Withheld: together with the figure beside it, it would reveal a group smaller than five."
      : "Not enough profiles to show.";
    return h("div.figure.is-empty", h("p.figure-label", item.label), h("p.figure-empty", why), h("p.figure-def", item.definition));
  }
  return h("div.figure",
    h("p.figure-label", item.label),
    h("p.figure-value", `${item.share_pct}%`),
    h("p.figure-count", `${item.count} of ${item.denominator} profiles`),
    item.in_your_profile ? badge("In your profile", "accent") : null,
    h("p.figure-def", item.definition));
}

/**
 * The route as connected milestones: start (you are here) -> intermediate steps -> target.
 * stepGapIds: roles on the route that are not in the candidate's reviewed history (the API's step_gaps).
 */
export function routeTrack(route, { stepGapIds = [], compact = false, moveLabels = [] } = {}) {
  const last = route.length - 1;
  return h(`ol.route${compact ? ".is-compact" : ""}`, { "aria-label": "Route" }, route.map((r, i) => {
    const kind = i === 0 ? "start" : i === last ? "target" : "step";
    const gap = kind === "step" && stepGapIds.includes(r.role_id);
    const caption = kind === "start" ? "Your current role" : kind === "target" ? "Your target role" : gap ? "Not yet shown in your profile" : "In your history";
    return h(`li.route-node.is-${kind}${gap ? ".is-gap" : ""}`,
      h("span.route-marker", { "aria-hidden": "true" }),
      h("span.route-text",
        h("span.route-role", r.canonical_title),
        compact ? null : h("span.route-caption", caption),
        !compact && i > 0 && moveLabels[i - 1] ? h("span.route-move", moveLabels[i - 1]) : null));
  }));
}
