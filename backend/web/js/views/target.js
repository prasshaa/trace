// Starting point and destination. The current position comes from the API (derived from the reviewed profile);
// it is corrected in the profile (job role match or review), never typed in here.
import { api } from "../api.js";
import { h, mount, uid } from "../dom.js";
import { positionText } from "../copy.js";
import { button, errorState, journey, loading, notice, pageHeader } from "../ui.js";
import { goalPicker } from "../goal.js";
import { movePicker } from "../move.js";

export async function render(root, ctx) {
  const body = h("div.stack");
  mount(root, h("section.page",
    journey("target", { profile: "#/profile", target: "#/target",
      pathway: ctx.app.target ? `#/pathway/${encodeURIComponent(ctx.app.target)}` : null }),
    pageHeader("Step 2 · Target role", "Where do you want to go?",
      "Your current role is based on the experience you've confirmed. Choose the role you want to reach."),
    body));
  root.querySelector(".page-title").focus({ preventScroll: true });
  mount(body, loading("Loading roles"));
  let position;
  let roles;
  try {
    [position, roles] = await Promise.all([api.currentPosition(), api.targetRoles()]);
  } catch (err) {
    mount(body, errorState(err, () => render(root, ctx)));
    return;
  }

  const known = position.status === "known";
  ctx.app.flash = null;
  // Phase 5 (D51): any valid target can be chosen, including the current role (the same role somewhere new) or a role at
  // an earlier level. Trace may describe the move; it never blocks it.
  const isCurrent = (id) => known && position.role_id === id;
  let chosen = ctx.app.target && roles.some((r) => r.role_id === ctx.app.target) ? ctx.app.target : null;
  const startCard = h("div.endpoint.is-start",
    h("p.endpoint-label", "Your current role"),
    known
      ? [h("p.endpoint-role", position.canonical_title),
        h("p.endpoint-meta", position.title_raw && position.title_raw !== position.canonical_title
          ? `From your most recent job: ${position.title_raw}` : "From your most recent job")]
      : [h("p.endpoint-role.is-unknown", "Not set yet"),
        h("p.endpoint-meta", positionText(position))],
    h("a.endpoint-link", { href: "#/profile" }, known ? "Not right? Edit in your profile" : "Fix this in your profile"));

  const targetRole = h("p.endpoint-role", chosen ? labelOf(roles, chosen) : "Choose below");
  const endCard = h("div.endpoint.is-target", h("p.endpoint-label", "Your target role"), targetRole);
  const connector = h("div.endpoint-connector", { "aria-hidden": "true" }, h("span"));

  const filter = h("input.search", { type: "search", id: uid("q"), placeholder: "Search roles", "aria-label": "Search target roles" });
  const list = h("div.role-options", { role: "radiogroup", "aria-label": "Target role" });
  const explore = button("See my career plan", { disabled: !chosen });
  // The planning goal (redesign): an optional desired timeframe and weekly time, per target role. It organises the
  // roadmap; it never changes the route or capability, and it is never a forecast.
  const goalSlot = h("div");
  const moveSlot = h("div");
  let picker = null;
  let mover = null;
  const drawGoal = () => {
    if (!chosen) { mount(goalSlot); mount(moveSlot); return; }
    mover = movePicker(chosen);
    picker = goalPicker(chosen);
    mount(moveSlot, mover.el);
    mount(goalSlot, picker.el);
  };
  const go = async () => {
    if (!chosen) return;
    if (mover && !(await mover.save())) return;
    if (picker) {
      const ok = await picker.save();
      if (!ok) return;
    }
    ctx.app.target = chosen;
    ctx.app.goalChanged = true;
    ctx.go(`pathway/${encodeURIComponent(chosen)}`);
  };
  const draw = () => {
    const q = filter.value.trim().toLowerCase();
    const shown = roles.filter((r) => !q || r.label.toLowerCase().includes(q));
    mount(list, shown.length ? shown.map((r) => {
      const id = uid("role");
      const input = h("input", { type: "radio", name: "target", id, value: r.role_id, checked: r.role_id === chosen });
      input.addEventListener("change", () => {
        chosen = r.role_id;
        targetRole.textContent = r.label;
        explore.disabled = false;
        drawGoal();
      });
      const note = r.report_available ? null : uid("na");
      const current = isCurrent(r.role_id) ? uid("cur") : null;
      if (note || current) input.setAttribute("aria-describedby", [note, current].filter(Boolean).join(" "));
      // Every listed role is selectable, the current one included (same-role moves, D51): it carries a label, never a
      // disabled look. Disabled styling is reserved for options that genuinely can't be chosen.
      return h(`label.role-option${current ? ".is-current" : ""}`, { for: id }, input,
        h("span.role-option-body", h("span.role-option-title", r.label,
          current ? h("span.badge.role-option-badge", "Your current role") : null),
          current ? h("span.role-option-note", { id: current },
            "You can choose the same role if you're exploring a move to another company.") : null,
          note ? h("span.role-option-note", { id: note }, "Career plan not available yet for this role") : null));
    }) : h("p.muted", "No roles match that search."));
  };
  filter.addEventListener("input", draw);
  draw();

  explore.addEventListener("click", go);

  if (!known) {
    // A career path needs a starting point: no target is chosen until the current role is established.
    mount(body,
      h("div.endpoints", startCard, connector, endCard),
      h("section.card.state-card",
        h("h2.card-title", "Confirm your current role to continue"),
        h("p", positionText(position)),
        h("p.muted", "Trace needs to know where you're starting before it can map your career path."),
        h("div.actions", h("button.btn.btn-primary", { type: "button",
          onclick: () => { ctx.app.flash = "focus_current_job"; ctx.go("profile"); } },
        position.reason === "role_unconfirmed" ? "Review the role match" : "Go to your current job"))));
    return;
  }
  mount(body,
    h("div.endpoints", startCard, connector, endCard),
    h("section.card.target-picker",
      h("h2.card-title", "Choose your target role"),
      h("p.muted", "Choose any role, including the one you have now. Trace describes how the move relates to your current "
        + "role; it doesn't decide which moves you can make."),
      filter, list),
    moveSlot,
    goalSlot,
    h("div.page-actions", h("a.btn.btn-ghost", { href: "#/profile" }, "Back to profile"), explore));
  drawGoal();
}

function labelOf(roles, id) {
  const r = roles.find((x) => x.role_id === id);
  return r ? r.label : "";
}
