// Shared building blocks for the career-planning workspace (Overview, My Roadmap, Learn & Develop, Experience &
// Reposition, Profile & evidence). Presentation only: every fact comes from the saved plan, its roadmap projection,
// the user's goal or their profile records. Nothing here counts, ranks, scores or forecasts.
import { api } from "../../api.js";
import { h, mount, uid } from "../../dom.js";
import { badge, busy, button, notice } from "../../ui.js";
import { TIMEFRAMES, WEEKLY } from "../../goal.js";

export const SECTIONS = [
  { key: "overview", label: "Overview", icon: "compass" },
  { key: "roadmap", label: "My Roadmap", icon: "route" },
  { key: "learn", label: "Learn & Develop", icon: "book" },
  { key: "experience", label: "Experience & Reposition", icon: "briefcase" },
  { key: "profile", label: "Profile & evidence", icon: "file" },
];
export const ACCESS = { free: "Free", paid: "Paid", free_enrolment_paid_certificate: "Free to enrol · certificate paid" };
export const TYPE = { guide: "Guide", video: "Video", book: "Book", course: "Course", course_series: "Course series" };
export const NOT_ENOUGH_HISTORIES = "We don't yet have enough eligible career histories to show a reliable comparison for this "
  + "transition. This doesn't mean the path is uncommon or unavailable.";
export const GOAL_NOTE = "Your timeframe organises your planning. It is not a prediction or guarantee of a career outcome.";

export const lower = (s) => (s ? s[0].toLowerCase() + s.slice(1) : s);
export const dateOf = (iso, opts = { day: "numeric", month: "long", year: "numeric" }) => (iso ? new Date(iso).toLocaleDateString("en-GB", opts) : "");
export const sectionHref = (target, key) => `#/pathway/${encodeURIComponent(target)}/${key}`;

/** "within 12 months", "by 3 March 2027", or null when no goal is set. */
export function goalPhrase(t) {
  if (t.target_date) return `by ${dateOf(t.target_date)}`;
  if (t.timeframe && t.timeframe !== "flexible") return lower(t.timeframe_label);
  if (t.timeframe === "flexible") return "on a flexible timeframe";
  return null;
}

// --------------------------------------------------------------------------- icons (inline SVG, decorative)
const PATHS = {
  compass: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm3.5 5.5-2 5-5 2 2-5 5-2Z",
  route: "M6 5a2 2 0 1 0 0 .01M18 19a2 2 0 1 0 0 .01M6 7v4a3 3 0 0 0 3 3h6a3 3 0 0 1 3 3",
  book: "M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5v-15Zm0 15A2.5 2.5 0 0 1 6.5 18H20v3H6.5A2.5 2.5 0 0 1 4 20.5Z",
  briefcase: "M3 8h18v11H3zM8 8V5h8v3M3 13h18",
  file: "M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6",
  layers: "m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5",
  target: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm0 4a5 5 0 1 0 0 10 5 5 0 0 0 0-10Zm0 4a1 1 0 1 0 0 2 1 1 0 0 0 0-2Z",
  calendar: "M4 6h16v14H4zM4 10h16M8 3v4M16 3v4",
  pencil: "m4 20 4-1 11-11-3-3L5 16l-1 4Z",
  arrow: "M5 12h14m-6-6 6 6-6 6",
  shield: "M12 3 5 6v5c0 5 3 8 7 10 4-2 7-5 7-10V6l-7-3Z",
  help: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm-2.5-12a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5V14m0 3v.01",
  bulb: "M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V16h5v-.1c0-.8.4-1.5 1-2A6 6 0 0 0 12 3Z",
  chat: "M4 5h16v11H8l-4 4V5Z",
};
export function icon(name, cls = "ws-icon") {
  const ns = "http://www.w3.org/2000/svg";
  const s = document.createElementNS(ns, "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("class", cls);
  s.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(ns, "path");
  p.setAttribute("d", PATHS[name] || PATHS.compass);
  s.append(p);
  return s;
}

// --------------------------------------------------------------------------- layout pieces
export function pageHead(eyebrow, title, lead, actions = null, chip = null) {
  return h("header.ws-head",
    h("div.ws-head-text",
      h("p.ws-eyebrow", eyebrow),
      h("h1.ws-title.page-title", { tabindex: "-1" }, title),
      lead ? h("p.ws-lead", lead) : null),
    actions || chip ? h("div.ws-head-side", actions, chip ? h("span.ws-chip", chip) : null) : null);
}

/** A section heading (h2). The eyebrow is optional: used sparingly so it keeps its emphasis. */
export const sectionTitle = (eyebrow, title, action = null, lead = null, id = null) => h("div.ws-section-head",
  h("div", eyebrow ? h("p.ws-eyebrow", eyebrow) : null, h("h2.ws-h2", { id }, title), lead ? h("p.ws-section-lead", lead) : null), action);

// What a kind of experience does NOT establish on its own (Trace guidance, shown with each experience theme).
export const THEME_LIMITS = {
  delivery: "On its own, it doesn't show deciding what to build or owning a product outcome.",
  collaboration: "On its own, it doesn't show owning the product decisions those conversations support.",
  customers: "On its own, it doesn't show turning what users need into product priorities.",
  requirements: "On its own, it doesn't show setting a product's direction.",
  data: "On its own, it doesn't show defining an outcome metric and deciding from it.",
  direction: "How much you owned, rather than supported, is what your records need to make clear.",
};

export function linkButton(label, href, { primary = false } = {}) {
  return h(`a.btn.${primary ? "btn-primary" : "btn-secondary"}.ws-link-btn`, { href }, label, icon("arrow", "ws-icon-sm"));
}

export function textLink(label, href) {
  return h("a.ws-text-link", { href }, label, icon("arrow", "ws-icon-sm"));
}

export function syntheticNote(rm) {
  return rm.data_mode !== "empirical"
    ? notice("Comparable career histories in this environment are fictional test data. They are illustrative only and never "
      + "shape your plan.", { tone: "warning", title: "Fictional test data", role: "note" })
    : null;
}

export function historiesNote(rm) {
  const ch = rm.career_histories;
  return h("section.ws-card.ws-quiet", { "aria-labelledby": uid("hist") },
    h("div.ws-inline-head", icon("shield"), h("h3.ws-h4", "About comparable career histories")),
    ch.status === "shown"
      ? h("ul.plain-list", ch.routes.map((r) => h("li", r.steps.join(" → "), " ", badge(r.breadth, "neutral"))))
      : h("p", NOT_ENOUGH_HISTORIES),
    h("p.muted.small", "No fictional or individual history is used as evidence here."));
}

// --------------------------------------------------------------------------- modal (native <dialog>: focus trap and Escape)
export function openModal({ eyebrow, title, subtitle, body }) {
  const close = h("button.ws-modal-close", { type: "button", "aria-label": "Close" }, "×");
  const titleId = uid("modal");
  const dialog = h("dialog.ws-modal", { "aria-labelledby": titleId },
    close,
    eyebrow ? h("p.ws-eyebrow", eyebrow) : null,
    h("h2.ws-modal-title", { id: titleId }, title),
    subtitle ? h("p.ws-lead", subtitle) : null,
    h("div.ws-modal-body", body));
  const opener = document.activeElement;
  const done = () => {
    dialog.close();
    dialog.remove();
    if (opener && opener.focus) opener.focus();
  };
  close.addEventListener("click", done);
  dialog.addEventListener("cancel", (e) => { e.preventDefault(); done(); });
  dialog.addEventListener("click", (e) => { if (e.target === dialog) done(); });
  document.body.append(dialog);
  dialog.showModal();
  close.focus();
  return { dialog, close: done };
}

// --------------------------------------------------------------------------- learning resources
const fact = (label, value) => h("div.ws-fact", h("p.ws-fact-label", label), h("p.ws-fact-value", value));

export function resourceFacts(r) {
  return [
    fact("Access", r.access_note),
    fact("Cost", ACCESS[r.access] || "Not recorded"),
    fact("Published duration", r.published_length ? r.published_length.note : "Not stated by the provider"),
    fact("Level", r.level ? r.level[0].toUpperCase() + r.level.slice(1) : "Not stated"),
  ];
}

export function resourceDetails(r, context) {
  openModal({
    eyebrow: `${TYPE[r.type] || r.type} · checked ${dateOf(r.verified_on)}`,
    title: r.title,
    subtitle: r.provider,
    body: [
      h("div.ws-facts", resourceFacts(r)),
      h("h3.ws-h4", "Why it may be relevant to you"),
      h("p", r.why, context ? ` ${context}` : ""),
      r.study_time ? h("p.small", r.study_time) : null,
      h("div.ws-two",
        h("div", h("h3.ws-h4", "Covers"), h("ul.ws-check-list", r.topics.map((x) => h("li", x)))),
        h("div", h("h3.ws-h4", "You'll be able to"), h("ul.ws-check-list", r.outcomes.map((x) => h("li", x))))),
      h("div.ws-callout", h("h3.ws-h4", "How to apply it"),
        h("p", "Connect what you learn to a real opportunity at work, or to the practical exercise in this plan. Learning on its "
          + "own is not professional experience.")),
      h("p.ws-foot", h("a", { href: r.url, target: "_blank", rel: "noopener noreferrer" }, "Open the resource on the provider's site"),
        h("span.muted", ` · Details were checked on ${dateOf(r.verified_on)}; check them again when you open it. Trace doesn't record prices.`),
        r.licence ? h("span.muted", ` · ${r.licence}`) : null),
    ],
  });
}

export function resourceRow(r, context) {
  const more = button("View details", { variant: "secondary", small: true, attrs: { "aria-label": `View details: ${r.title}` } });
  more.addEventListener("click", () => resourceDetails(r, context));
  return h("article.ws-card.ws-resource",
    h("div.ws-resource-main",
      h("h4.ws-h3", r.title),
      h("p.muted.small", r.provider),
      h("p", r.why),
      h("div.ws-tags", h("span.ws-tag", TYPE[r.type] || r.type), h(`span.ws-tag${r.access === "free" ? ".is-positive" : ""}`, ACCESS[r.access] || "Cost not recorded"))),
    h("div.ws-resource-side", more));
}

/** A compact map of what the plan is based on: confirmed experience, related experience, areas not assessed yet and the
 * development priorities. Every entry comes from the plan's own data; empty columns say so rather than being filled. */
export function evidenceMap(rm) {
  const col = (cls, title, items, empty) => h(`div.ws-map-col.${cls}`, h("h3.ws-h4", title),
    items.length ? h("ul", items.map((x) => h("li", x))) : h("p.muted.small", empty));
  return h("figure.ws-map", { "aria-label": "How your evidence relates to your priorities" },
    h("div.ws-map-cols",
      col("is-confirmed", "Confirmed by you", rm.experience.themes.map((t) => t.title), "Nothing confirmed and categorised yet"),
      col("is-related", "Related experience", rm.related.map((r) => r.title), "None identified"),
      col("is-unknown", "Not assessed yet", rm.not_assessed.map((u) => u.title), "Nothing outstanding"),
      col("is-priority", "Priorities to develop", rm.development.map((p) => p.title), "No priority for this route")),
    h("figcaption.muted.small", "Built from your confirmed records and your plan. Not assessed means unknown, not missing."));
}

// --------------------------------------------------------------------------- the planning goal (saved through the existing API)
export function goalEditor(rm, onSaved) {
  const t = rm.timeline;
  const tfId = uid("tf");
  const wkId = uid("wk");
  const tf = h("select.ws-select", { id: tfId }, h("option", { value: "" }, "Not set"),
    TIMEFRAMES.map(([v, label]) => h("option", { value: v, selected: !t.target_date && t.timeframe === v }, label)));
  const wk = h("select.ws-select", { id: wkId }, h("option", { value: "" }, "Not set"),
    WEEKLY.map(([v, label]) => h("option", { value: v, selected: t.weekly_hours === v }, label)));
  const status = h("div", { "aria-live": "polite" });
  const save = button("Save planning goal", { variant: "primary" });
  const modal = openModal({
    title: "Adjust your planning goal",
    subtitle: "Changes are saved to your plan's goal. They organise the plan; they never change the recommended route.",
    body: [
      t.target_date ? notice(`You chose a specific date (${dateOf(t.target_date)}). Picking a timeframe below replaces it.`, { tone: "info" }) : null,
      h("div.field", h("label", { for: tfId }, "Desired timeframe"), tf),
      h("div.field", h("label", { for: wkId }, "Time available each week"), wk),
      h("p.muted.small", GOAL_NOTE),
      h("p.small", "To change your career background or target role, ", h("a", { href: "#/profile" }, "edit your profile"),
        " or ", h("a", { href: "#/target" }, "choose another target role"), "."),
      status, save,
    ],
  });
  save.addEventListener("click", () => busy(save, "Saving…", async () => {
    try {
      const keepDate = t.target_date && !tf.value;
      await api.setGoal(rm.target_role_id, { timeframe: keepDate ? null : (tf.value || null), target_date: keepDate ? t.target_date : null,
        weekly_hours: wk.value || null, checkpoint_dates: Object.fromEntries(rm.checkpoints.filter((c) => c.date).map((c) => [String(c.stage), c.date])) });
      modal.close();
      await onSaved();
    } catch (err) {
      mount(status, notice(err.message || "Your goal couldn't be saved.", { tone: "error" }));
    }
  }));
}

/** Source key -> {publisher, title, url, attribution} from the plan's own route evidence. */
export function sourceIndex(plan) {
  const out = {};
  for (const r of plan.routes) {
    for (const e of r.evidence) {
      if (e.source_key && !out[e.source_key]) out[e.source_key] = { publisher: e.publisher, title: e.title, url: e.url, attribution: e.attribution };
    }
  }
  return out;
}

/** Yes / No / Not sure for a scope question. The answer is saved on the line it refers to and the plan is rebuilt,
 *  so the deterministic assessment uses it. "Not sure" is not saved and is never read as no. */
export function scopeAnswers(ws, c) {
  const asks = (c.asks || []).filter((a) => a && a.text);
  if (!asks.length) return null;
  return h("div.ov-asks", asks.map((ask) => {
    if (!ask.evidence_id || !ask.area) {
      return h("p.small.ov-cap-q", ask.text, " ", h("a", { href: "#/profile" }, "Answer this on the line in your profile."));
    }
    const name = uid("ask");
    const status = h("span.small.muted");
    const choose = (label, value) => {
      const id = uid("asko");
      const input = h("input", { type: "radio", name, id });
      input.addEventListener("change", async () => {
        if (value === null) {
          status.textContent = "Left unanswered. Trace does not read that as a no.";
          return;
        }
        status.textContent = "Saving…";
        try {
          await api.classify(ask.evidence_id, {
            areas: ask.areas && ask.areas.length ? ask.areas : [ask.area],
            none: false,
            qualifiers: { [ask.area]: { [ask.key]: value } },
          });
          status.textContent = "Saved. Updating your plan…";
          if (ws.replan) await ws.replan(null)();
        } catch (err) {
          status.textContent = (err && err.message) || "That wasn't saved.";
        }
      });
      return h("label.ask-opt", { for: id }, input, h("span", label));
    };
    return h("fieldset.ov-ask", h("legend", ask.text), choose("Yes", true), choose("No", false), choose("Not sure", null), status);
  }));
}
