// Profile & evidence: the background the plan uses, grouped in plain language. Provenance and eligibility come from the
// server (the plan's records eligible now and the profile records); related experience is never presented as confirmed
// target-role experience, and missing information is never a sign that experience is absent.
import { h } from "../../dom.js";
import { dateRange } from "../../copy.js";
import { goalPhrase, historiesNote, icon, pageHead } from "./common.js";

const KIND_LABEL = { education: "Education", activity: "Activity", skill: "Skill", career_break: "Career break" };

const STATE = { evidence_found: ["Evidence found", ""], needs_context: ["Needs more context", ".is-info"],
  not_yet_demonstrated: ["Not yet demonstrated in the available evidence", ".is-caution"], cannot_assess_yet: ["Can't assess yet", ".is-caution"] };
const BASIS = { read_by_trace: "Trace read this line", confirmed_line_categorised_by_you: "You categorised this line",
  confirmed_skill: "A skill you confirmed", confirmed_role: "A role you confirmed" };

/** One capability with every record behind it: the line, the job it came from, who read it and why it counts. */
function capabilityCard(c) {
  const [text, tone] = STATE[c.state] || STATE.cannot_assess_yet;
  return h("article.ws-card.pe-cap", { id: `cap-${c.key}`, "data-key": c.key },
    h("div.ws-inline-head.ws-spread", h("h3.ws-h3", c.title), h(`span.ws-chip${tone}`, text)),
    c.description ? h("p.small.muted", c.description) : null,
    c.evidence.length ? h("ul.pe-evidence", c.evidence.map((e) => h("li",
      h("p", `“${e.text}”`, e.source ? h("span.muted.small", ` · ${e.source}`) : null),
      h("p.small.muted", BASIS[e.basis] || "", e.why && e.why.length
        ? `: ${e.why.map((w) => `${w.why} (from “${w.cue}”)`).filter((x, i2, all) => all.indexOf(x) === i2).slice(0, 2).join("; ")}` : "")))) : null,
    c.evidence_count > c.evidence.length ? h("p.small.muted", `And ${c.evidence_count - c.evidence.length} more.`) : null,
    c.state === "needs_context" && c.questions.length ? h("p.small", h("strong", "What would make it clear: "), c.questions.join(" "),
      " If it applies, say so in the line in your profile.") : null,
    c.state === "needs_context" && !c.questions.length ? h("p.small", "Your related experience doesn't show the whole capability on its own. "
      + "If you did more than the line says, add it to the line in your profile.") : null,
    c.state === "not_yet_demonstrated" ? h("p.small", "Nothing in your confirmed experience shows this yet. If you've done it, add "
      + "a line describing it to the relevant job; otherwise your plan shows how to build it.") : null,
    c.state === "cannot_assess_yet" ? h("p.small", "Some details this depends on are waiting for your review.") : null);
}

export function render(ws) {
  const { rm, records } = ws;
  const t = rm.timeline;
  const jobs = [...(records.employments || [])].sort((a, b) => a.sequence_index - b.sequence_index);
  const allLines = [...jobs.flatMap((j) => j.evidence.map((e) => ({ ...e, job: j.title_raw }))), ...(records.unattached_evidence || []).map((e) => ({ ...e, job: null }))];
  const pending = allLines.filter((e) => e.review.status === "pending_review");
  const others = (records.other_records || []).filter((r) => r.review.status !== "rejected");
  const ev = (rm.readiness && rm.readiness.evidence) || {};
  const caps = rm.capabilities || [];
  return [
    pageHead("Your experience & evidence", "Profile & evidence",
      "Which parts of your experience Trace recognised, what each one supports, and anything that needs your action.",
      h("a.btn.btn-secondary.btn-sm", { href: "#/profile" }, icon("pencil", "ws-icon-sm"), "Edit profile")),
    h("article.ws-card",
      h("p.ws-eyebrow", "Profile summary"),
      h("h2.ws-display-sm", rm.current_role ? `${rm.current_role} → ${rm.target_role}` : rm.target_role),
      h("div.ws-grid-3.ws-summary",
        h("div", h("p.ws-eyebrow", "Current role"), h("p.ws-strong", rm.current_role || "Not confirmed yet")),
        h("div", h("p.ws-eyebrow", "Desired role"), h("p.ws-strong", rm.target_role)),
        h("div", h("p.ws-eyebrow", "Target timeframe"), h("p.ws-strong", goalPhrase(t) ? `${goalPhrase(t)[0].toUpperCase()}${goalPhrase(t).slice(1)} · planning goal` : "Not set"))),
      ev.confirmed_lines ? h("p.small", `${ev.confirmed_lines} confirmed lines of experience: `,
        `${ev.categorised_by_you || 0} you categorised yourself, ${ev.read_by_trace || 0} read by Trace from your own words. `,
        "Categorising a line yourself (on your profile page) always takes precedence over Trace's reading.") : null),
    pending.length ? h("section.ws-section", { "aria-labelledby": "pe-action" },
      h("div.ws-section-head", h("h2.ws-h2", { id: "pe-action" }, "Needs your action")),
      h("article.ws-card", h("p", `${pending.length} ${pending.length === 1 ? "line is" : "lines are"} waiting for your review. Until you `
        + "confirm them, they aren't used in your plan."),
      h("ul.ws-record-list", pending.map((e) => h("li", e.evidence_text, e.job ? h("span.muted.small", ` · ${e.job}`) : null))),
      h("a.btn.btn-secondary.btn-sm", { href: "#/profile" }, "Review in your profile"))) : null,
    h("section.ws-section", { "aria-labelledby": "pe-caps" },
      h("div.ws-section-head", h("h2.ws-h2", { id: "pe-caps" }, `What ${rm.target_role} roles look for, and your evidence`)),
      caps.length ? h("nav.pe-toc", { "aria-label": "Capabilities" }, h("ul.ov-chips", caps.map((c) => h("li",
        h("button.co-jump", { type: "button", onclick: () => { const el = document.getElementById(`cap-${c.key}`); if (el) { el.scrollIntoView({ block: "start" }); el.setAttribute("tabindex", "-1"); el.focus({ preventScroll: true }); } } }, c.title)))))
        : null,
      h("div.ws-stack", caps.map(capabilityCard))),
    h("section.ws-section", { "aria-label": "Your professional background" },
      h("div.ws-section-head", h("h2.ws-h2", "Your professional background")),
      h("article.ws-card",
        jobs.length ? h("ol.ws-timeline", jobs.map((j) => h("li",
          h("span.ws-tl-dot", { "aria-hidden": "true" }),
          h("div", h("p.ws-strong", j.title_raw, j.employer_name ? ` · ${j.employer_name}` : ""),
            h("p.muted.small", dateRange(j.start, j.end), " · ", j.review.label))))) : h("p.ws-empty", "No jobs added yet. ", h("a", { href: "#/profile" }, "Add your experience")),
        others.length ? h("div.ws-others", h("p.ws-h5", "Education, activities and skills"),
          h("ul.plain-list", others.map((r) => h("li", `${KIND_LABEL[r.kind]}: ${r.title || "Untitled"}`, h("span.muted.small", ` · ${r.review.label}`))))) : null)),
    historiesNote(rm),
    h("article.ws-card.ws-quiet",
      h("div.ws-inline-head", icon("shield"), h("h3.ws-h4", "How your evidence is read")),
      h("p.small", "Only experience you've confirmed is used. Trace reads what each line describes, not just its exact words, using "
        + "a fixed vocabulary on its own server (no outside AI service), and keeps the words it relied on. A job title is never "
        + "evidence on its own. Something that isn't shown here is not evidence that you haven't done it.")),
  ];
}
