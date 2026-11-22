// Experience & Reposition: how to gain relevant experience in the user's current context and describe it truthfully.
// Workplace-first: opportunities are things to seek or propose, in steps from observing to supervised ownership; Trace
// can't know what is available where the user works. Repositioning is a later, supporting step.
import { h } from "../../dom.js";
import { ACTION_WHERE } from "../../copy.js";
import { button, disclosure, toast } from "../../ui.js";
import { icon, lower, pageHead, sectionHref, sectionTitle, textLink } from "./common.js";

const KINDS = [
  ["Learning", "Courses, guides and reading. Builds understanding; it isn't experience."],
  ["Independent practice", "Exercises and personal projects. Shows how you think; label it as a personal project."],
  ["Supervised project work", "A bounded contribution with someone who owns the area. Real work, with a clear account of your part."],
  ["Professional experience", "Responsibility you actually held in your role. Only this is described as professional ownership."],
];
const PROGRESSION = [
  ["Observe or contribute", "Join the conversations where the work happens, and help with a defined part."],
  ["Take a bounded part", "Take responsibility for one piece, agreed with the person who owns the area."],
  ["Own a defined outcome", "With supervision, own a small outcome and review what happened afterwards."],
];
const DESCRIBE = ["What did you actually do?", "Which decisions did you contribute to, and which did you make?", "What was your scope?",
  "Who else was involved?", "What was the outcome, if you know it?", "Which responsibilities did you genuinely own?"];
const firstSentence = (s) => (s ? s.split(/(?<=\.)\s/)[0] : "");

/** A conversation opener built only from the plan's own guidance ("Ask to ..."); the user adapts it. */
function opener(p) {
  const seek = p.bridge_note && p.bridge_note.seek;
  const ask = seek && /^Ask to /.test(seek) ? `Could I ${seek.replace(/^Ask to /, "").replace(/\.$/, "")}?` : "Could I take on a small, bounded part of this on something our team is working on?";
  return `I'd like to build more hands-on experience in ${lower(p.title)}. ${ask}`;
}

function starterCard(text) {
  const copy = button("Copy example", { variant: "secondary", small: true, attrs: { "aria-label": "Copy the conversation starter" } });
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast("Copied. Adapt it to your situation before you use it.");
    } catch {
      toast("Couldn't copy automatically; select the text instead.", "error");
    }
  });
  return h("aside.ws-starter", h("div.ws-inline-head", icon("chat"), h("h3.ws-h4", "A conversation starter")),
    h("blockquote.ws-quote", `“${text}”`), h("p.muted.small", "An example to adapt, not a script."), copy);
}

function demoFor(p, ws) {
  const stage = ws.rm.stages.find((s) => s.stage === 2);
  return stage ? [...stage.highlighted, ...stage.more].find((a) => a.requirement_key === p.key) : null;
}

function opportunity(p, ws) {
  const demo = demoFor(p, ws);
  return h("article.ws-card.ws-opportunity",
    h("div.ws-opp-main",
      h("h3.ws-h3", demo ? demo.title : p.title),
      h("p.muted.small", `Responsibility: ${p.title}`),
      h("p", firstSentence(p.why)),
      h("h4.ws-h4", "Ways to begin"),
      h("ul.ws-arrow-list", p.practice.slice(0, 3).map((s) => h("li", s))),
      disclosure("More detail", h("dl.ws-dl",
        p.bridge_note && p.bridge_note.seek ? [h("dt", "What to ask for"), h("dd", p.bridge_note.seek)] : null,
        demo ? [h("dt", "A bounded first contribution"), h("dd", demo.what_to_do)] : null,
        demo && demo.where.length ? [h("dt", "Where it might exist"), h("dd", demo.where.map((w) => ACTION_WHERE[w] || w).join("; "))] : null,
        p.output ? [h("dt", "Evidence to keep if you do the work"), h("dd", p.output.good_contains.join("; "))] : null),
      { className: "subtle" })));
}

function sharedGuidance(ws, plans) {
  const where = new Set();
  for (const p of plans) {
    const d = demoFor(p, ws);
    if (d) d.where.forEach((w) => where.add(ACTION_WHERE[w] || w));
  }
  return h("article.ws-card.ws-shared",
    h("h3.ws-h3", "Before you ask: how these opportunities usually start"),
    h("ol.ws-progress", PROGRESSION.map(([t, d], i) => h("li", h("span.ws-step-num", { "aria-hidden": "true" }, String(i + 1)),
      h("div", h("p.ws-strong", t), h("p.small.muted", d))))),
    h("dl.ws-dl",
      h("dt", "Whom to approach"), h("dd", "Your manager, a product manager or product owner, or whoever owns the feature or area you work on."),
      where.size ? [h("dt", "Where they might exist"), h("dd", [...where].join("; "))] : null,
      h("dt", "Permission"), h("dd", "Don't contact customers or use production data or confidential information without permission and appropriate access.")));
}

export function render(ws) {
  const { rm } = ws;
  const plans = rm.development.filter((p) => p.curated);
  const first = plans[0];
  const g = rm.stage_guidance["3"];
  return [
    pageHead("Build experience where you are", "Experience & Reposition",
      "Gain relevant experience in your current context, then describe it truthfully.", null, "Opportunities to seek, not completed work"),
    first ? h("article.ws-card.ws-split",
      h("div",
        h("h2.ws-display-sm", "Start with a small, supervised product contribution"),
        h("p.ws-readable", rm.current_role ? `As ${/^[aeiou]/i.test(rm.current_role) ? "an" : "a"} ${rm.current_role}, you don't need to start over. ` : "",
          `Look for work that gives you exposure to ${plans.map((p) => lower(p.title)).join("; ")}.`)),
      starterCard(opener(first))) : null,
    plans.length ? h("section.ws-section", { "aria-labelledby": "opp-title" },
      sectionTitle(null, "Opportunities in your current role", null, null, "opp-title"),
      sharedGuidance(ws, plans),
      h("div.ws-stack", plans.map((p) => opportunity(p, ws)))) : h("p.ws-empty", "No development priority for this route, so there is nothing new to seek first."),
    h("div.ws-two",
      first ? h("article.ws-card",
        h("h3.ws-h3", "If workplace access isn't available"),
        h("p", "Look for someone who can guide the work and give it context. A personal exercise is useful practice, but it is not professional experience."),
        h("ol.ws-num-list", first.alternatives.filter((a) => a.method !== "workplace").map((a) => h("li", h("strong", `${a.label}. `), a.text)))) : null,
      h("article.ws-card",
        h("h3.ws-h3", "Keep evidence of your contribution"),
        h("p", "A truthful record of what happened and your part in it. It can later support a case study or interview example."),
        h("ul.ws-check-grid", ["Decisions you contributed to", "Scope you owned", "Who you worked with", "How you understood the problem",
          "Trade-offs and constraints", "Outcomes, where known"].map((x) => h("li", x))),
        h("p.muted.small", "Follow your employer's confidentiality policies: don't copy sensitive customer or company information into a portfolio."))),
    h("section.ws-section", { "aria-labelledby": "kinds-title" },
      sectionTitle(null, "Be precise about what you did", null, null, "kinds-title"),
      h("div.ws-grid-4", KINDS.map(([title, text]) => h("article.ws-card.ws-kind", h("h3.ws-h4", title), h("p.small", text))))),
    h("section.ws-section.ws-later", { "aria-labelledby": "describe-title" },
      sectionTitle("Later, before applying", "Describe your work accurately", null,
        "Once you have done the work, describe it as it happened. Start from these questions.", "describe-title"),
      h("article.ws-card",
        h("ul.ws-check-grid", DESCRIBE.map((x) => h("li", x))),
        h("p.small", h("strong", "Never claim: "), g.never_claim.map(lower).join("; "), "."),
        disclosure("A case-study structure", h("ol.ws-steps", g.case_study.map((x) => h("li", x))), { className: "subtle" }),
        disclosure("Interview stories", h("ul.plain-list", g.narratives.map((x) => h("li", x))), { className: "subtle" }),
        h("p.small.muted", "Suggestions for describing specific confirmed lines are in your roadmap, under ",
          h("a", { href: sectionHref(ws.target, "roadmap"), onclick: () => { ws.app.roadmapGroup = "before"; } }, "Before applying"), "."))),
    h("article.ws-card.ws-inline-card",
      h("div", h("h3.ws-h4", "What your plan is based on"), h("p.muted.small", "See what Trace treats as confirmed, related, or still needing clarification.")),
      textLink("Profile & evidence", sectionHref(ws.target, "profile"))),
  ];
}
