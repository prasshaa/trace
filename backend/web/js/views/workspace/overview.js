// Overview (Phase 5): one story from where the user is to what they do next, with the research at its centre.
//   A. Your starting point            from the confirmed profile (personal)
//   B. Where people like you moved    observed in the research sample (aggregate only; never about the user)
//   C. Routes to the target           Trace's recommendation and the alternatives (the plan; research never changes it)
//   D. How this relates to you        each capability's state, with the user's own records behind it
//   E. What to do next                specific actions with timing
// Three kinds of statement are always labelled apart: observed in the research, from your confirmed profile, and Trace's
// recommendation. Presentation only: every fact, count, share and state comes from the API; nothing is counted, ranked
// or estimated here, and a hidden or banded research figure stays hidden.
import { api } from "../../api.js";
import { h } from "../../dom.js";
import { button, notice } from "../../ui.js";
import { GOAL_NOTE, goalEditor, goalPhrase, icon, lower, pageHead, scopeAnswers, sectionHref, syntheticNote, textLink } from "./common.js";

const STATE_CHIP = { evidence_found: ["Evidence found", ""], needs_context: ["Needs more context", ".is-info"],
  not_yet_demonstrated: ["Not yet demonstrated in the available evidence", ".is-caution"], cannot_assess_yet: ["Can't assess yet", ".is-caution"] };
const FLAG = { descriptive: null, indicative: "Indicative (10–29 people)", insufficient: "Too few to describe" };
const DIMENSION = { progression: "Moving between levels", route: "How they got there", entry: "Same employer or a new one",
  first_level: "Where they started" };
const TIMING = { start_now: "Start now", in_parallel: "In parallel", before_applying: "Before applying", hiring_transition: "Hiring transition" };
const KIND = { build: "Build", demonstrate: "Show it more fully", expand: "Expand the scope", reposition: "Reposition", hiring: "Hiring" };

export function headerActions(ws) {
  const change = button("Change goal", { variant: "secondary", small: true });
  change.prepend(icon("target", "ws-icon-sm"));
  change.addEventListener("click", () => goalEditor(ws.rm, ws.refreshGoal));
  return h("div.ws-actions",
    h("a.btn.btn-secondary.btn-sm", { href: "#/profile" }, icon("pencil", "ws-icon-sm"), "Edit profile"),
    h("a.btn.btn-secondary.btn-sm", { href: "#/target" }, "Change target"), change);
}

const kindLabel = (text) => h("p.ov-kind", text);
const personal = () => kindLabel("From your confirmed profile");
const observed = () => kindLabel("Observed in the research sample · not a prediction for you");
const recommended = () => kindLabel("Trace's recommendation");

function stateChip(state) {
  const [text, tone] = STATE_CHIP[state] || STATE_CHIP.cannot_assess_yet;
  return h(`span.ws-chip${tone}`, text);
}

// ------------------------------------------------------------------------------------------- A. your starting point
function startingPoint(ws) {
  const { rm } = ws;
  const r = rm.readiness || {};
  const ev = r.evidence || {};
  const found = (rm.capabilities || []).filter((c) => c.state === "evidence_found").slice(0, 4);
  const t = rm.timeline;
  return h("section.ws-section.ov-a", { "aria-labelledby": "ov-a" },
    h("div.ws-section-head", h("h2.ws-h2", { id: "ov-a" }, h("span.ov-letter", "A"), "Your starting point")),
    h("article.ws-card.ov-start",
      personal(),
      h("div.ov-start-grid",
        h("div", h("p.ws-eyebrow", "Current role"), h("p.ws-display-sm", rm.current_role || "Not confirmed yet"),
          rm.move && rm.move.text ? h("p.small", rm.move.text) : null),
        h("div", h("p.ws-eyebrow", "Your goal"),
          h("p.ws-strong", goalPhrase(t) ? `${rm.target_role}, ${goalPhrase(t)}` : rm.target_role),
          h("p.muted.small", t.weekly_label ? `Time you can give: ${t.weekly_label}. ` : "", goalPhrase(t) ? GOAL_NOTE : "No timeframe set yet."))),
      ev.confirmed_lines ? h("p.small", `Trace used ${ev.confirmed_lines} confirmed lines of your experience`,
        ev.read_by_trace ? `: ${ev.categorised_by_you ? `${ev.categorised_by_you} you categorised and ` : ""}${ev.read_by_trace} read by Trace from your own words` : "",
        ". ", textLink("See how", sectionHref(ws.target, "profile"))) : null,
      found.length ? h("div.ov-strengths", h("p.ws-eyebrow", "Shown in your confirmed experience"),
        h("ul.ov-chips", found.map((c) => h("li", h("span.ws-chip", c.title))))) : null));
}

// ------------------------------------------------------------------------- B. where people with your start moved
function figure(o) {
  if (o.display === "exact" && o.median_months != null) {
    return h("p.ov-figure", h("strong", `${o.median_months} months`), " median",
      o.iqr_months ? ` · middle half ${o.iqr_months[0]}–${o.iqr_months[1]} months` : "", h("span.muted", ` · ${o.n} people`));
  }
  if (o.display === "exact") return h("p.ov-figure", h("strong", `${o.share_pct}%`), h("span.muted", ` · ${o.count} of ${o.denominator} people`));
  if (o.band === "5–9") return h("p.ov-figure.muted", "5–9 people · too few to describe");
  if (o.band === "fewer than 5") return h("p.ov-figure.muted", "Fewer than 5 people · not shown");
  return h("p.ov-figure.muted", "Number withheld so a smaller group can't be worked out");
}

function pathwayCard(p) {
  return h(`li.ov-path${p.matches_you ? ".is-yours" : ""}`, { "data-key": p.key },
    h("div.ov-path-head", h("p.ws-strong", p.title), p.matches_you ? h("span.ws-chip.is-info", "Like your route") : null),
    figure(p.observed),
    FLAG[p.observed.flag] ? h("p.small.muted", FLAG[p.observed.flag]) : null,
    h("p.small", p.description),
    p.relates_to_you ? h("p.small.ov-relates", icon("arrow", "ws-icon-sm"), p.relates_to_you) : null);
}

function research(ws) {
  const o = ws.origins;
  if (!o) return null;                                   // the research is switched off here: nothing in its place
  const head = h("div.ws-section-head", h("h2.ws-h2", { id: "ov-b" }, h("span.ov-letter", "B"), "Where people with your starting point moved"));
  if (o.status !== "available" || !o.narrative) {
    return h("section.ws-section.ov-b", { "aria-labelledby": "ov-b" }, head,
      h("article.ws-card", observed(), h("p", "The career research isn't available right now. Your plan doesn't depend on it.")));
  }
  const n = o.narrative;
  const dims = ["progression", "route", "entry", "first_level"].map((d) => [d, n.pathways.filter((p) => p.dimension === d)])
    .filter(([, ps]) => ps.length);
  return h("section.ws-section.ov-b", { "aria-labelledby": "ov-b" }, head,
    h("article.ws-card.ov-research",
      observed(),
      h("p", n.basis),
      n.group_label ? h("p.small.muted", `${n.group_label}: ${n.group_n} people in the sample`,
        FLAG[n.group_flag] ? ` · ${FLAG[n.group_flag]}` : "", ". Groups are formed by job titles only.") : null,
      n.your_route ? h("p.ov-yours", icon("compass", "ws-icon-sm"), n.your_route) : null,
      dims.length ? dims.map(([d, ps]) => h("div.ov-dim", h("h3.ws-h4", DIMENSION[d]), h("ul.ov-paths", ps.map(pathwayCard))))
        : h("p.muted", "There's no research group large enough to describe routes from your starting point."),
      h("details.disclosure.subtle", h("summary", h("span.disclosure-label", "What this research can and can't show"),
        h("span.disclosure-icon", { "aria-hidden": "true" })),
      h("div.disclosure-body", h("ul.co-notes.small", [...n.cannot_show, ...(o.research ? o.research.disclosure : [])].map((x) => h("li", x))))),
      textLink("Explore the full career research", sectionHref(ws.target, "origins"))));
}

// ------------------------------------------------------------------------------------------ C. routes to the target
function routeSteps(steps) {
  const label = { current: "Now", intermediate: "Then", target: "Target" };
  const parts = [];
  steps.forEach((s, i) => {
    if (i) parts.push(h("span.ws-route-arrow", { "aria-hidden": "true" }, icon("arrow", "ws-icon-sm")));
    parts.push(h(`div.ws-route-step.is-${s.position}`, { role: "listitem" }, h("p.ws-route-label", label[s.position]), h("p.ws-route-role", s.title)));
  });
  return h("div.ws-route", { role: "list", "aria-label": "Route" }, parts);
}

function routes(ws) {
  const r = ws.rm.recommendation;
  const provisional = r.kind === "resolve_first";
  return h("section.ws-section.ov-c", { "aria-labelledby": "ov-c" },
    h("div.ws-section-head", h("h2.ws-h2", { id: "ov-c" }, h("span.ov-letter", "C"), `Routes to ${ws.rm.target_role}`),
      textLink("Open the roadmap", sectionHref(ws.target, "roadmap"))),
    h("div.ov-routes",
      h("article.ws-card.ws-next",
        recommended(),
        h("div.ws-inline-head.ws-spread", h("h3.ws-h3", r.headline), provisional ? h("span.ws-chip.is-caution", "Provisional") : null),
        r.route.length ? routeSteps(r.route) : null,
        r.reasons.length ? h("ul.ws-check-list", r.reasons.map((x) => h("li", x))) : null,
        r.tradeoff ? h("p.small", h("strong", "Trade-off: "), r.tradeoff) : null,
        provisional && ws.rm.resolve_first.length ? h("p.small.ws-provisional",
          "Provisional because some details are still waiting for your review: ", h("a", { href: "#/profile" }, "review them")) : null),
      ws.rm.alternatives.length ? h("article.ws-card.ov-alts", recommended(), h("h3.ws-h4", "Other routes Trace considered"),
        h("ul.plain-list", ws.rm.alternatives.map((a) => h("li", h("p.ws-strong", a.steps.join(" → ")), h("p.small", a.tradeoff)))))
        : null),
    h("p.small.muted", "Trace's recommendation comes from your confirmed experience and its role knowledge. The research above "
      + "describes other people's routes; it never ranks or changes your plan."));
}

// ---------------------------------------------------------------------------------- D. how this relates to you
function capabilityRow(ws, c) {
  const positioning = c.state === "positioning";
  const e = c.evidence[0];
  return h("li.ov-cap", { "data-key": c.key },
    h("div.ov-cap-head", h("p.ws-strong", c.title), positioning ? h("span.ws-chip", "Positioning") : stateChip(c.state)),
    e ? h("p.small.ov-cap-ev", h("span.muted", e.basis === "read_by_trace" ? "Trace read: " : e.kind === "skill" ? "Your skill: " : e.kind === "role" ? "Your role: " : "You categorised: "),
      `“${e.text}”`, e.source ? h("span.muted", ` · ${e.source}`) : null,
      c.evidence_count > 1 ? h("span.muted", ` · and ${c.evidence_count - 1} more`) : null) : null,
    scopeAnswers(ws, c),
    !positioning && c.state === "needs_context" && c.questions.length && !(c.asks || []).length
      ? h("p.small.ov-cap-q", "To be sure, Trace would need to know: ", c.questions[0]) : null,
    !positioning && c.state === "not_yet_demonstrated" ? h("p.small.muted", "Nothing in your confirmed experience shows this yet. That's about the "
      + "evidence available, not your ability.") : null);
}

function relates(ws) {
  const caps = ws.rm.capabilities || [];
  if (!caps.length) return null;
  const limited = (ws.rm.support || {}).depth === "limited";
  const counts = { evidence_found: 0, needs_context: 0, not_yet_demonstrated: 0, cannot_assess_yet: 0 };
  for (const c of caps) if (counts[c.state] !== undefined) counts[c.state] += 1;
  const summary = limited
    ? "Limited support. These are lines from your experience that sit near this role. Trace is not assigning a capability status."
    : `What ${ws.rm.target_role} roles look for, compared with your confirmed experience: ` + [
        counts.evidence_found ? `${counts.evidence_found} with evidence found` : null,
        counts.needs_context ? `${counts.needs_context} needing more context` : null,
        counts.not_yet_demonstrated ? `${counts.not_yet_demonstrated} not yet demonstrated` : null,
        counts.cannot_assess_yet ? `${counts.cannot_assess_yet} that can't be assessed yet` : null].filter(Boolean).join(", ") + ".";
  return h("section.ws-section.ov-d", { "aria-labelledby": "ov-d" },
    h("div.ws-section-head", h("h2.ws-h2", { id: "ov-d" }, h("span.ov-letter", "D"), limited ? "What in your experience is relevant" : "How this relates to your experience"),
      textLink("See all your evidence", sectionHref(ws.target, "profile"))),
    h("article.ws-card",
      personal(),
      limited && ws.rm.support ? h("p.ws-strong", ws.rm.support.label) : null,
      h("p.small", summary),
      h("ul.ov-caps", caps.map((c) => capabilityRow(ws, c))),
      h("p.small.muted", "Where a line is cited, the words are from your profile. A job title alone is not evidence.")));
}

function peerPanel(ws) {
  const peers = ws.rm.peers;
  if (!peers || !peers.available) return null;
  const patterns = peers.patterns || [];
  const takeaways = peers.takeaways || [];
  return h("section.ws-section.ov-peers", { "aria-labelledby": "ov-peers" },
    h("div.ws-section-head", h("h2.ws-h2", { id: "ov-peers" }, h("span.ov-letter", "A"), peers.headline || "PMs with paths like yours")),
    h("article.ws-card.ov-peers-card",
      h("p.ov-kind", peers.match_line || "Matched by similarity of career path."),
      peers.preface ? h("p", peers.preface) : null,
      peers.neighbour_count ? h("p.ws-display-sm", `${peers.neighbour_count} closest PMs`) : null,
      h("p", peers.disclaimer || "These are patterns across the closest profiles, not a prediction and not a guarantee."),
      patterns.length ? h("ul.ov-caps", patterns.map((item) => h("li.ov-cap", h("p", item.text)))) : null,
      takeaways.length ? h("div", h("h3.ws-h3", "What they did"), h("ul.plain-list", takeaways.map((line) => h("li", line)))) : null,
      h("p.small.muted", peers.note || "")));
}

function cite(ws, ids) {
  const lines = ((ws.rm.ai || {}).lines) || [];
  const by = Object.fromEntries(lines.map((row) => [row.id, row]));
  return (ids || []).map((id) => by[id]).filter(Boolean);
}

function later(a, b) {
  return a > b ? a : b;
}

function aiStatusMessage(elapsed, attempt, phase) {
  if (attempt > 1 || phase === "retrying") return "Still working — trying once more. This will not hang.";
  if (elapsed >= 30 || phase === "still_working") return "Still working. A careful reading can take about a minute and a half.";
  return "Reading your experience. This usually takes about a minute.";
}

function aiPanel(ws) {
  const ai = ws.rm.ai;
  if (!ai || !ai.status) return null;
  const head = h("div.ws-section-head", h("h2.ws-h2", { id: "ov-ai" }, h("span.ov-letter", "A"), "What your experience shows"));
  if (aiPanel.forTarget !== ws.target) {
    aiPanel.forTarget = ws.target;
    aiPanel.started = null;
    aiPanel.gaveUp = false;
    if (aiPanel.timer) { clearInterval(aiPanel.timer); aiPanel.timer = null; }
  }
  if ((ai.status === "pending" || ai.status === "not_started") && !aiPanel.gaveUp) {
    if (!aiPanel.started) aiPanel.started = Date.now() - (Number(ai.elapsed_s) || 0) * 1000;
    const waited = () => later((Date.now() - aiPanel.started) / 1000, Number(ai.elapsed_s) || 0);
    if (!aiPanel.timer) {
      aiPanel.timer = setInterval(async () => {
        const local = (Date.now() - aiPanel.started) / 1000;
        if (local > 240) {
          clearInterval(aiPanel.timer);
          aiPanel.timer = null;
          aiPanel.gaveUp = true;
          aiPanel.started = null;
          if (ws.refreshAi) ws.refreshAi({ status: "retry", detail: "The reading took too long. It can be tried again. Your plan is unchanged." });
          return;
        }
        try {
          const next = await api.aiNarrative(ws.target);
          const label = document.getElementById("ov-ai-status");
          if (!next || next.status === "pending" || next.status === "not_started") {
            const elapsed = later(local, Number(next && next.elapsed_s) || 0);
            if (label) label.textContent = aiStatusMessage(elapsed, (next && next.attempt) || 1, next && next.phase);
            return;
          }
          clearInterval(aiPanel.timer);
          aiPanel.timer = null;
          aiPanel.started = null;
          if (ws.refreshAi) ws.refreshAi(next);
        } catch (_) { /* the next tick tries again */ }
      }, 2000);
    }
    return h("section.ws-section.ov-ai", { "aria-labelledby": "ov-ai", "aria-busy": "true" }, head,
      h("article.ws-card",
        h("div.loading", { role: "status", "aria-live": "polite" },
          h("span.spinner", { "aria-hidden": "true" }),
          h("span", { id: "ov-ai-status" }, aiStatusMessage(waited(), ai.attempt || 1, ai.phase)))));
  }
  if (aiPanel.timer) { clearInterval(aiPanel.timer); aiPanel.timer = null; }
  if (ai.status !== "ready") {
    const env = (ai.env || ["TRACE_AI_ASSESSMENT", "TRACE_LLM_API_KEY", "TRACE_LLM_MODEL", "TRACE_LLM_BASE_URL"]).join(", ");
    return h("section.ws-section.ov-ai", { "aria-labelledby": "ov-ai" }, head,
      h("article.ws-card",
        h("p", ai.status === "not_configured"
          ? "The live reading is off until a model is configured. Trace will not invent one."
          : ai.status === "retry"
            ? (ai.detail || "The reading didn't finish. Open the plan again to try once more. Your saved plan is unchanged.")
            : "The model didn't return a reading this time. Your saved plan below is unchanged."),
        h("p.small.muted", ai.status === "not_configured"
          ? `Set ${env}. The key stays in the environment.`
          : (ai.status === "retry" ? null : (ai.detail || "")))));
  }
  const quote = (ids) => {
    const rows = cite(ws, ids);
    return rows.length ? h("ul.plain-list.small", rows.map((row) => h("li", `“${row.text}”`, h("span.muted", ` · ${row.job}`)))) : null;
  };
  const strengths = ai.strengths || ai.proves || [];
  const gaps = ai.gaps || ai.missing || [];
  return h("section.ws-section.ov-ai", { "aria-labelledby": "ov-ai" }, head,
    h("article.ws-card",
      h("p.ov-kind", "From your own experience"),
      ai.opening ? h("p.ws-display-sm", ai.opening) : null,
      h("div.ov-routes",
        h("div", h("h3.ws-h3", "What stands out"),
          strengths.length ? h("ul.ov-caps", strengths.map((item) => h("li.ov-cap", h("p", item.text), item.why ? h("p.small.muted", item.why) : null, quote(item.evidence_ids))))
            : h("p.muted", "Nothing in the experience shown was strong enough to state.")),
        h("div", h("h3.ws-h3", "The gap that matters"),
          gaps.length ? h("ul.plain-list", gaps.map((item) => h("li", item.text, item.why ? h("p.small.muted", item.why) : null))) : h("p.muted", "No gap was stated."))),
      (ai.transferable || []).length ? h("div", h("h3.ws-h3", "How this carries over"),
        h("ul.ov-caps", ai.transferable.map((item) => h("li.ov-cap", h("p", item.text), quote(item.evidence_ids))))) : null,
      (ai.questions || []).length ? h("div", h("h3.ws-h4", "A question, if you want the reading to be sharper"),
        h("ul.plain-list", ai.questions.map((q) => h("li", q.text)))) : null,
      (ai.steps || []).length ? h("div", h("h3.ws-h3", "Concrete steps"),
        h("ol.ov-actions", ai.steps.map((step) => h("li.ov-action", h("p.ws-strong", step.text), step.why ? h("p.small", step.why) : null, quote(step.evidence_ids))))) : null,
      h("p.small.muted", "Every result quotes your own experience. A figure that was not in that quote is left out.")));
}

// ------------------------------------------------------------------------------------------------ E. what next
function actionCard(a) {
  return h("li.ov-action", { "data-kind": a.kind },
    h("div.ov-action-head", h("span.ws-chip", TIMING[a.timing] || a.timing), h("span.ws-chip.is-info", KIND[a.kind] || a.kind),
      a.capability ? h("span.muted.small", a.capability) : null),
    h("p.ws-strong", a.action),
    h("dl.ws-dl.ov-action-dl",
      h("dt", "Why it matters"), h("dd", a.why),
      a.builds_on.length ? [h("dt", "Builds on"), h("dd", a.builds_on.map((b) => `“${b.text}”${b.source ? ` (${b.source})` : ""}`).join("; "))] : null,
      h("dt", "How at work"), h("dd", a.how),
      h("dt", "Keep as evidence"), h("dd", a.keep),
      h("dt", "Done when"), h("dd", a.done_when)));
}

function nextSteps(ws) {
  const acts = ws.rm.next_actions || [];
  if (!acts.length) return null;
  const order = ["start_now", "in_parallel", "before_applying", "hiring_transition"];
  const groups = order.map((t) => [t, acts.filter((a) => a.timing === t)]).filter(([, xs]) => xs.length);
  return h("section.ws-section.ov-e", { "aria-labelledby": "ov-e" },
    h("div.ws-section-head", h("h2.ws-h2", { id: "ov-e" }, h("span.ov-letter", "E"), "What to do next"),
      textLink("Learning plans", sectionHref(ws.target, "learn"))),
    h("article.ws-card",
      recommended(),
      ws.rm.timeline.weekly_label ? h("p.small.muted", `Organised for ${lower(ws.rm.timeline.weekly_label)} a week. The order is a suggestion, not a prediction.`)
        : h("p.small.muted", "The order is a suggestion, not a prediction."),
      groups.map(([t, xs]) => h("div.ov-timing", h("h3.ws-h4", TIMING[t]), h("ol.ov-actions", xs.map(actionCard))))));
}

export function render(ws) {
  const rm = ws.rm;
  const r = rm.readiness || {};
  const pending = r.profile && r.profile.pending;
  return [
    pageHead("Your career transition", rm.current_role ? `${rm.current_role} → ${rm.target_role}` : `Your way into ${rm.target_role}`,
      "What your experience proves for this role, what is still open, and the next concrete step.", headerActions(ws)),
    syntheticNote(rm),
    (rm.support || {}).depth === "limited" ? notice(rm.support.text, { tone: "info", title: rm.support.label }) : null,
    pending ? notice(`${pending} ${pending === 1 ? "detail is" : "details are"} still waiting for your review, so ${pending === 1 ? "it isn't" : "they aren't"} `
      + "used yet. Everything you've confirmed is already in this plan.", { tone: "info",
      action: h("a.btn.btn-secondary.btn-sm", { href: "#/profile" }, "Review them") }) : null,
    ...numbered([peerPanel(ws), aiPanel(ws), startingPoint(ws), research(ws), routes(ws), relates(ws), nextSteps(ws)]),
  ];
}

function numbered(blocks) {
  const letters = "ABCDEFGHIJ";
  return blocks.filter(Boolean).map((el, i) => {
    const letter = el.querySelector && el.querySelector(".ov-letter");
    if (letter) letter.textContent = letters[i] || "";
    return el;
  });
}
