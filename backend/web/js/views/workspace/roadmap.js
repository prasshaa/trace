// My Roadmap: what to do, in what sequence, and what it produces. The pathway (current role, a bridge role only when
// the plan justifies one, the desired role) leads, then the planning groups. Groups are a sequence, not durations.
import { api } from "../../api.js";
import { h, mount, uid } from "../../dom.js";
import { ACTION_LABEL, ACTION_LABEL_SHORT, ACTION_WHERE } from "../../copy.js";
import { disclosure } from "../../ui.js";
import { goalPhrase, icon, lower, pageHead, sectionHref, sectionTitle, textLink } from "./common.js";

const STATE_WORDS = { demonstrated: "Confirmed in your records", transferable: "Related experience to build on",
  gap: "Not yet established", unknown: "Not assessed yet" };
const GROUPS = [
  { key: "start", title: "Start now", sub: "Begin with what is accessible", seq: "start_now", stage: 1,
    depends: "Nothing outside your control: learning, conversations and describing your existing work." },
  { key: "parallel", title: "In parallel", sub: "As opportunities allow", seq: "in_parallel", stage: 2, depends: null },
  { key: "before", title: "Before applying", sub: "Once practice produces real evidence", seq: "before_next", stage: 3,
    depends: "At least one output from real work, so your case study describes something you actually did." },
  { key: "hiring", title: "Hiring transition", sub: "When your evidence supports it", seq: null, stage: 4,
    depends: "The evidence and stories from the earlier groups." },
];

// --------------------------------------------------------------------------- pathway: balanced roles, one shared detail panel
function roleDetail(kind, ws, f) {
  const { rm, plan } = ws;
  if (kind === "current") {
    return h("div",
      rm.experience.themes.length ? [h("h4.ws-h4", "What you bring"), h("ul.plain-list", rm.experience.themes.map((t) => h("li", h("strong", `${t.title}. `), t.summary)))]
        : h("p.muted", "No confirmed, categorised experience yet."),
      rm.focus.length ? [h("h4.ws-h4", "Opportunities to look for now"), h("ul.plain-list", rm.focus.map((x) => h("li", x.how ? `${x.title}: ${lower(x.how)}` : x.title)))] : null);
  }
  if (kind === "intermediate") {
    return h("div.ws-two",
      f.responsibilities && f.responsibilities.length ? h("div", h("h4.ws-h4", "What this role usually involves"),
        h("ul.plain-list", f.responsibilities.map((x) => h("li", x))), h("p.muted.small", "Trace's curated description; any particular job may differ.")) : null,
      (f.partial_development || []).length ? h("div", h("h4.ws-h4", "What to ask for in the role"),
        h("ul.plain-list", f.partial_development.map((p) => h("li", h("strong", `${p.title}: `), p.seek)))) : null);
  }
  const items = plan.assessment.items.filter((i) => i.state !== "not_applicable");
  return h("div", h("h4.ws-h4", `What ${rm.target_role} involves, and where your profile stands`),
    h("ul.ws-req-list", items.map((i) => h("li", h("span", i.title), h(`span.ws-req-state.is-${i.state}`, STATE_WORDS[i.state])))),
    h("p.muted.small", "Not assessed means unknown, not missing."));
}

function pathwayCard(ws) {
  const { rm, plan, app } = ws;
  const r = rm.recommendation;
  const chosen = plan.routes.find((x) => x.primary_status === "primary" || x.primary_status === "tied_primary");
  const f = (chosen && chosen.feasibility) || {};
  const steps = r.route.length ? r.route : [{ title: rm.current_role || "Your current role", position: "current" }, { title: rm.target_role, position: "target" }];
  const label = { current: "Your current role", intermediate: "A possible bridge role", target: "Your desired role" };
  const text = { current: "A foundation in your existing work.", intermediate: "A possible next step for expanding product responsibility.",
    target: steps.length > 2 ? "The longer-term direction of this plan." : "The role this plan works towards directly." };
  let current = steps.some((s) => s.position === app.pathRole) ? app.pathRole : (steps.find((s) => s.position === "intermediate") || steps[steps.length - 1]).position;
  const panel = h("div.ws-path-detail", { role: "tabpanel", id: uid("role"), tabindex: "0" });
  const tabs = steps.map((s, i) => h("button.ws-path-role", { type: "button", role: "tab", id: `role-${s.position}`, "aria-selected": String(s.position === current),
    tabindex: s.position === current ? "0" : "-1", "aria-controls": panel.id },
  h("span.ws-inline-head", h(`span.ws-num${s.position === "intermediate" ? ".is-strong" : ""}`, { "aria-hidden": "true" }, String(i + 1)), h("span.ws-path-label", label[s.position])),
  h("span.ws-path-title", s.title), h("span.muted.small", text[s.position])));
  const select = (pos, focus) => {
    current = pos;
    app.pathRole = pos;
    tabs.forEach((b, i) => {
      const on = steps[i].position === pos;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
      if (on && focus) b.focus();
    });
    panel.setAttribute("aria-labelledby", `role-${pos}`);
    mount(panel, roleDetail(pos, ws, f));
  };
  tabs.forEach((b, i) => {
    b.addEventListener("click", () => select(steps[i].position, false));
    b.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      e.preventDefault();
      select(steps[(i + (e.key === "ArrowRight" ? 1 : steps.length - 1)) % steps.length].position, true);
    });
  });
  select(current, false);
  return h("article.ws-card.ws-pathway", { "aria-labelledby": "path-title" },
    h("div.ws-inline-head.ws-spread",
      h("h2.ws-h2", { id: "path-title" }, steps.length > 2 ? "A stepwise transition" : r.kind === "resolve_first" ? "A provisional pathway" : "A direct transition"),
      h("span.ws-chip", "A plan, not a guarantee")),
    h(`div.ws-path${steps.length > 2 ? ".has-bridge" : ""}`, { role: "tablist", "aria-label": "Roles on this pathway" }, tabs),
    panel,
    r.reasons.length ? h("div.ws-path-why", h("h3.ws-h4", "Why this route may be appropriate"), h("ul.plain-list", r.reasons.map((x) => h("li", x)))) : null);
}

/** Direct versus bridge, only when the saved plan itself holds both routes. */
function comparison(ws) {
  const { plan, rm } = ws;
  const evaluated = plan.routes.filter((r) => r.route_kind !== "observed" && ["primary", "tied_primary", "alternative"].includes(r.primary_status));
  const direct = evaluated.find((r) => r.route_kind === "direct");
  const bridge = evaluated.find((r) => r.route_kind === "bridge" && ["primary", "tied_primary"].includes(r.primary_status))
    || evaluated.find((r) => r.route_kind === "bridge");
  if (!direct || !bridge) return null;
  const titles = Object.fromEntries(plan.assessment.items.map((i) => [i.key, i.title]));
  const df = direct.feasibility || {};
  const bf = bridge.feasibility || {};
  const need = [...(df.decision_rights_gaps || []), ...(df.practice_gaps || [])].map((k) => titles[k] || k);
  const develops = [...(bf.develops_fully || []).map((k) => titles[k] || k), ...(bf.develops_partly || []).map((k) => `Parts of "${titles[k] || k}"`)];
  const tr = (route, kind) => (route.tradeoffs || []).filter((t) => kind.includes(t.kind)).map((t) => t.text);
  const recommended = (r) => (["primary", "tied_primary"].includes(r.primary_status) ? h("span.ws-chip.is-accent", "Recommended in your plan") : null);
  return h("section.ws-section", { "aria-labelledby": "cmp-title" },
    sectionTitle(null, "Direct or through a bridge role?", null, "Both routes are in your plan. Here is what each asks of you.", "cmp-title"),
    h("div.ws-two",
      h("article.ws-card",
        h("div.ws-inline-head.ws-spread", h("h3.ws-h3", `Directly to ${rm.target_role}`), recommended(direct)),
        h("h4.ws-h4", "Evidence you would need to show"),
        need.length ? h("ul.plain-list", need.map((x) => h("li", x))) : h("p.muted", "Nothing missing from your confirmed profile."),
        h("h4.ws-h4", "What makes it more demanding"),
        h("p", tr(direct, ["remaining_gaps", "scope"])[0] || "One move, but you build the evidence without a role designed for it.")),
      h("article.ws-card",
        h("div.ws-inline-head.ws-spread", h("h3.ws-h3", `Through ${bridge.steps[1].title}`), recommended(bridge)),
        h("h4.ws-h4", "What the role could help you develop"),
        develops.length ? h("ul.plain-list", develops.map((x) => h("li", x))) : h("p.muted", "Not recorded for this route."),
        h("h4.ws-h4", "What it adds"),
        h("ul.plain-list", tr(bridge, ["moves", "bridge_note"]).map((x) => h("li", x))))));
}

// --------------------------------------------------------------------------- planning groups
function actionCard(a, ws) {
  const labels = (a.labels || []).filter((l) => ACTION_LABEL[l]);
  const plan = ws.rm.development.find((p) => p.key === a.requirement_key && p.curated);
  return h("li.ws-action",
    h("div.ws-inline-head.ws-spread", h("h4.ws-h4", a.title),
      labels.length ? h("span.ws-tags", labels.map((l) => h("span.ws-tag", { title: ACTION_LABEL[l] }, ACTION_LABEL_SHORT[l]))) : null),
    h("p", a.what_to_do),
    a.produce ? h("p.small", h("strong", "Expected output: "), a.produce) : null,
    disclosure("Details", h("dl.ws-dl",
      a.why ? [h("dt", "Why it matters"), h("dd", a.why)] : null,
      a.where.length ? [h("dt", "Where it can happen"), h("dd", a.where.map((w) => ACTION_WHERE[w] || w).join("; "))] : null,
      a.keep ? [h("dt", "Evidence to keep"), h("dd", a.keep, " (Trace guidance)")] : null,
      plan ? [h("dt", "Related learning"), h("dd", h("a", { href: sectionHref(ws.target, "learn"), onclick: () => { ws.app.learnArea = plan.key; } }, plan.heading))] : null),
    { className: "subtle" }));
}

function groupPanel(g, ws) {
  const { rm } = ws;
  const stage = rm.stages.find((s) => s.stage === g.stage);
  const items = g.seq ? rm.sequence[g.seq] : [];
  const first = rm.development.find((p) => p.curated);
  const guide = rm.stage_guidance["4"];
  const depends = g.key === "parallel" ? rm.sequence.main_risk : g.depends;
  return h("div.ws-group-panel",
    h("h3.ws-h3", g.title),
    items.length ? h("ul.ws-check-list", items.map((x) => h("li", x.text))) : null,
    depends ? h("p.ws-depends", h("strong", g.key === "parallel" ? "Depends on access: " : "Depends on: "), depends) : null,
    g.key === "parallel" && first ? disclosure("If workplace access isn't available", h("ul.plain-list",
      first.alternatives.filter((a) => a.method !== "workplace").map((a) => h("li", h("strong", `${a.label}: `), a.text)))) : null,
    g.key === "before" && rm.sequence.can_wait.length ? h("p.small.muted", h("strong", "Can wait: "), rm.sequence.can_wait.map((x) => x.text).join(" ")) : null,
    g.key === "hiring" ? disclosure("Preparing for interviews and product cases", h("div.ws-two",
      h("div", h("h4.ws-h4", "Interview types"), h("ul.plain-list", guide.interviews.map((x) => h("li", x)))),
      h("div", h("h4.ws-h4", "Practising product cases"), h("ul.plain-list", guide.product_case.map((x) => h("li", x)))))) : null,
    stage && (stage.highlighted.length || stage.more.length) ? [
      h("h4.ws-h4", "Main actions"),
      h("ul.ws-actions-list", stage.highlighted.map((a) => actionCard(a, ws))),
      stage.more.length ? disclosure(`More actions (${stage.more.length})`, h("ul.ws-actions-list", stage.more.map((a) => actionCard(a, ws)))) : null,
    ] : h("p.muted", stage && stage.empty_note ? stage.empty_note : "No actions in this group yet."));
}

function groupsCard(ws) {
  const { rm, app } = ws;
  const t = rm.timeline;
  let current = app.roadmapGroup || "start";
  const panel = h("div", { role: "tabpanel", id: uid("grp"), tabindex: "0" });
  const tabs = GROUPS.map((g, i) => h("button.ws-group", { type: "button", role: "tab", id: `tab-${g.key}`, "aria-selected": String(g.key === current),
    tabindex: g.key === current ? "0" : "-1", "aria-controls": panel.id },
  h("span.ws-group-title", h("span.ws-group-num", { "aria-hidden": "true" }, String(i + 1)), g.title), h("span.ws-group-sub", g.sub)));
  const select = (key, focus) => {
    current = key;
    app.roadmapGroup = key;
    tabs.forEach((b, i) => {
      const on = GROUPS[i].key === key;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
      if (on && focus) b.focus();
    });
    panel.setAttribute("aria-labelledby", `tab-${key}`);
    mount(panel, groupPanel(GROUPS.find((g) => g.key === key), ws));
  };
  tabs.forEach((b, i) => {
    b.addEventListener("click", () => select(GROUPS[i].key, false));
    b.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      e.preventDefault();
      select(GROUPS[(i + (e.key === "ArrowRight" ? 1 : GROUPS.length - 1)) % GROUPS.length].key, true);
    });
  });
  select(current, false);
  return h("section.ws-section", { "aria-labelledby": "groups-title" },
    sectionTitle(null, "What to do, and in what order", h("span.ws-chip", "Planning groups, not durations"),
      `${goalPhrase(t) ? `Organised around your goal of moving ${goalPhrase(t)}. ` : ""}${rm.sequence.weekly_advice || ""}`, "groups-title"),
    h("article.ws-card",
      h("div.ws-groups", { role: "tablist", "aria-label": "Planning groups" }, tabs),
      panel));
}

function milestones(ws) {
  const { rm } = ws;
  if (!rm.checkpoints.length) return null;
  return h("section.ws-section", { "aria-labelledby": "ms-title" },
    sectionTitle(null, "Suggested milestones", null, "Optional. Add your own date to any milestone if it helps you plan; nothing is tracked.", "ms-title"),
    h("ol.ws-milestones", rm.checkpoints.map((cp) => {
      const input = h("input.date-input", { type: "date", value: cp.date || "", "aria-label": `Your date for: ${cp.title}`, min: new Date().toISOString().slice(0, 10) });
      const status = h("span.muted.small", { "aria-live": "polite" });
      input.addEventListener("change", async () => {
        const t = rm.timeline;
        const dates = Object.fromEntries(rm.checkpoints.filter((c) => c.date && c.stage !== cp.stage).map((c) => [String(c.stage), c.date]));
        if (input.value) dates[String(cp.stage)] = input.value;
        status.textContent = "Saving…";
        try {
          await api.setGoal(rm.target_role_id, { timeframe: t.timeframe, target_date: t.target_date, weekly_hours: t.weekly_hours, checkpoint_dates: dates });
          await ws.refreshGoal();
        } catch (err) {
          status.textContent = err.message || "That date couldn't be saved.";
        }
      });
      return h("li.ws-milestone",
        h("span.ws-stage-num", { "aria-hidden": "true" }, String(cp.stage)),
        h("div.ws-milestone-text", h("p.ws-strong", cp.title), h("p.small.muted", cp.have.join("; "))),
        h("div.ws-milestone-date", h("label.small", "Your date ", input), status,
          cp.after_goal ? h("p.small.caution-text", "After your goal date; adjust either if it helps.") : null));
    })));
}

/** Career Origins & Transitions (internal preview): a separate, supporting view. Shown only when the server offers it. */
function originsLink(ws) {
  if (!ws.origins) return null;
  return h("article.ws-card.ws-inline-card.co-entry",
    h("div", h("p.ws-eyebrow", "Research sample · separate from your plan"),
      h("h2.ws-h4", "How others moved into product management"),
      h("p.muted.small", "Career origins and transitions from a research sample of India-based product managers, with what each "
        + "figure can and can't tell you.")),
    textLink("Career origins & transitions", sectionHref(ws.target, "origins")));
}

export function render(ws) {
  return [
    pageHead("The route ahead", "My Roadmap", "What to do, in what sequence, and what you'll produce along the way."),
    pathwayCard(ws),
    comparison(ws),
    groupsCard(ws),
    milestones(ws),
    originsLink(ws),
    h("p.muted.small", "Your timeframe and weekly time organise the work; they don't predict or guarantee an outcome. Trace has no "
      + "reliable evidence about how long this move usually takes, so it doesn't estimate a duration."),
  ];
}
