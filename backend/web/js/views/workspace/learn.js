// Learn & Develop: what to understand, how to learn it and how to apply it, for each development area. Resources are
// real, checked on the provider's page and listed by learning approach, never ranked. Exercises are practice; they
// become experience only when done as real work with permission.
import { h, mount, uid } from "../../dom.js";
import { button, disclosure } from "../../ui.js";
import { goalPhrase, lower, openModal, pageHead, resourceRow, sectionHref } from "./common.js";

const PERMISSION = "Only involve users, customers or workplace data where you have permission and the access is appropriate.";

function kindOf(p) {
  return p.recommended_first.method === "personal_project"
    ? "Independent practice: a personal project, labelled as one."
    : "Supervised project work if you do it at work with the area's owner; independent practice if you do it on your own.";
}

function exerciseModal(p, ws) {
  openModal({
    eyebrow: "Practical exercise",
    title: p.output ? `Produce your ${p.output.name.toLowerCase()}` : p.heading,
    subtitle: ws.rm.current_role ? `For your move from ${ws.rm.current_role} to ${ws.rm.target_role}.` : null,
    body: [
      h("h3.ws-h4", "Objective"), h("p", p.concept),
      h("h3.ws-h4", "Steps"),
      h("ol.ws-steps", p.practice.map((s) => h("li", s))),
      p.output ? h("div.ws-callout", h("h3.ws-h4", "Expected output: what a good version contains"),
        h("ul.ws-check-list", p.output.good_contains.map((x) => h("li", x)))) : null,
      h("h3.ws-h4", "How it relates to the role"), h("p", p.why),
      p.use_output.length ? h("p", h("strong", "How to use it: "), p.use_output.map((x, i) => (i ? lower(x) : x)).join("; "), ".") : null,
      h("p.small", h("strong", "What kind of work this is: "), kindOf(p)),
      h("p.small.ws-honest", `${PERMISSION} Only describe this as professional experience if you actually do the work in a professional `
        + "context, with a truthful account of your contribution."),
    ],
  });
}

const step = (n, title, ...body) => h("section.ws-step", { "aria-labelledby": `step-${n}` },
  h("div.ws-step-head", h("span.ws-step-num", { "aria-hidden": "true" }, String(n)), h("h3.ws-h3", { id: `step-${n}` }, title)), ...body);

function areaPanel(p, ws) {
  const { rm } = ws;
  const t = rm.timeline;
  const context = `It also fits your ${rm.current_role ? `${rm.current_role} ` : ""}background and your goal of moving into ${rm.target_role}`
    + (goalPhrase(t) ? ` ${goalPhrase(t)}` : "") + (t.weekly_label ? ` with ${lower(t.weekly_label)}` : "") + ".";
  if (!p.curated) {
    return h("article.ws-card",
      h("h2.ws-display-sm", p.title),
      p.concept ? h("p.ws-readable", p.concept) : null,
      h("p.muted.small", "Trace doesn't have a curated learning guide for this area yet. These actions come from your plan."),
      h("ul.plain-list", p.route_actions.map((a) => h("li", h("strong", `${a.title}: `), a.what_to_do))));
  }
  const resources = [];
  for (const m of p.learn) for (const r of m.resources) if (!resources.some((x) => x.id === r.id)) resources.push(r);
  const open = button("Open the full exercise", { variant: "secondary" });
  open.addEventListener("click", () => exerciseModal(p, ws));
  return h("article.ws-card.ws-area-plan",
    h("h2.ws-display-sm", p.heading),
    step(1, "Understand",
      h("p.ws-readable", p.why),
      h("p.ws-readable", h("strong", "What you need to understand. "), p.concept),
      p.bridge_note ? h("div.ws-callout.ws-readable",
        h("p", p.bridge_note.text),
        p.bridge_note.seek ? h("p.small", h("strong", "What to ask for in that role: "), p.bridge_note.seek) : null) : null,
      disclosure("Knowledge outline", h("ul.ws-check-list", p.subtopics.map((x) => h("li", x))), { className: "subtle" })),
    step(2, "Learn",
      h("p.ws-readable", h("strong", `Suggested starting point: ${p.recommended_first.label}. `), p.recommended_first.reason),
      p.learn.length ? disclosure("All learning approaches for this area", h("ul.plain-list", p.learn.map((m) => h("li", h("strong", `${m.label}: `), m.summary))),
        { className: "subtle" }) : null,
      resources.length ? [h("p.muted.small", "Listed by learning approach, not ranked. Details were checked on each provider's page; Trace doesn't record prices."),
        h("div.ws-resource-list", resources.map((r) => resourceRow(r, context)))] : null),
    p.output ? step(3, "Apply",
      h("dl.ws-dl.ws-readable",
        h("dt", "Objective"), h("dd", `Produce your ${p.output.name.toLowerCase()} from real work, or a clearly labelled personal project.`),
        h("dt", "Key steps"), h("dd", h("ol.ws-steps", p.practice.slice(0, 3).map((s) => h("li", s)))),
        h("dt", "Expected output"), h("dd", p.output.good_contains.slice(0, 3).join("; "), p.output.good_contains.length > 3 ? "; and more" : ""),
        h("dt", "Kind of work"), h("dd", kindOf(p))),
      h("p.small.ws-readable", PERMISSION),
      open) : null);
}

export function render(ws) {
  const { rm, app } = ws;
  const dev = rm.development;
  const t = rm.timeline;
  const head = pageHead("Learn with intention", "Learn & Develop",
    dev.length ? `What to understand, how to learn it and how to practise it, for ${dev.length === 1 ? "one area" : `${["", "", "two", "three"][dev.length] || dev.length} areas`}`
      + `${t.weekly_label ? `, organised around ${lower(t.weekly_label)}` : ""}.` : null);
  if (!dev.length) {
    return [head, h("p.ws-empty", "Your confirmed profile doesn't leave a priority to develop for this route. ",
      h("a", { href: sectionHref(ws.target, "experience") }, "See how to build and describe your experience"))];
  }
  let current = dev.some((p) => p.key === app.learnArea) ? app.learnArea : dev[0].key;
  const panel = h("div", { role: "tabpanel", id: uid("area"), tabindex: "-1" });
  const tabs = dev.map((p, i) => h("button.ws-area", { type: "button", role: "tab", id: `area-${p.key}`, "aria-selected": String(p.key === current),
    "aria-controls": panel.id, tabindex: p.key === current ? "0" : "-1" },
  h("span.ws-area-num", { "aria-hidden": "true" }, String(i + 1)), h("span.ws-area-title", p.title)));
  const select = (key, focus) => {
    current = key;
    app.learnArea = key;
    tabs.forEach((b, i) => {
      const on = dev[i].key === key;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
      if (on && focus) b.focus();
    });
    panel.setAttribute("aria-labelledby", `area-${key}`);
    mount(panel, areaPanel(dev.find((p) => p.key === key), ws));
  };
  tabs.forEach((b, i) => {
    b.addEventListener("click", () => select(dev[i].key, false));
    b.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      e.preventDefault();
      select(dev[(i + (e.key === "ArrowRight" ? 1 : dev.length - 1)) % dev.length].key, true);
    });
  });
  select(current, false);
  return [head, h("div.ws-areas", { role: "tablist", "aria-label": "Development areas" }, tabs), panel];
}
