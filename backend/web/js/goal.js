// The planning goal (product redesign): when the user would like to make the move and, optionally, how much time they
// can give each week. Both optional. Saved per target role (PUT /transition-goal) when the user continues.
//
// The goal is the user's AMBITION. It organises the roadmap into a sequence with checkpoints; it never changes the
// route or capability, and Trace never presents it, or anything derived from it, as a forecast.
import { api } from "./api.js";
import { h, mount, uid } from "./dom.js";
import { notice } from "./ui.js";

export const TIMEFRAMES = [
  ["within_3_months", "Within 3 months"], ["within_6_months", "Within 6 months"], ["within_12_months", "Within 12 months"],
  ["within_18_24_months", "Within 18–24 months"], ["flexible", "Flexible / not sure yet"],
];
export const WEEKLY = [["1_3", "1–3 hours"], ["4_6", "4–6 hours"], ["7_10", "7–10 hours"], ["more_than_10", "More than 10 hours"],
  ["varies", "It varies / not sure"]];

function radioGroup(name, legend, hint, options, value, onChange) {
  const group = uid(name);
  return h("fieldset.choice-group",
    h("legend", legend), hint ? h("p.hint", hint) : null,
    h("div.choice-options", options.map(([v, label]) => {
      const id = uid(name);
      const input = h("input", { type: "radio", name: group, id, value: v, checked: v === value });
      input.addEventListener("change", () => onChange(v));
      return h("label.choice", { for: id }, input, h("span", label));
    })));
}

export function goalPicker(target) {
  const state = { timeframe: null, target_date: null, weekly_hours: null, checkpoint_dates: {} };
  const errorSlot = h("div");
  const body = h("div.goal-body", h("p.muted", "Loading…"));
  const today = new Date().toISOString().slice(0, 10);
  const max = new Date(Date.now() + 5 * 365 * 864e5).toISOString().slice(0, 10);

  function draw() {
    const dateInput = h("input.date-input", { type: "date", id: uid("goal-date"), min: today, max, value: state.target_date || "" });
    dateInput.addEventListener("change", () => {
      state.target_date = dateInput.value || null;
      if (state.target_date && state.timeframe === "flexible") state.timeframe = null;
      draw();
    });
    mount(body,
      radioGroup("timeframe", "When would you like to make this move?", "Optional. This is your goal, not a prediction.",
        TIMEFRAMES, state.target_date ? null : state.timeframe, (v) => { state.timeframe = v; state.target_date = null; draw(); }),
      h("details.disclosure.subtle.goal-date", { open: Boolean(state.target_date) },
        h("summary", h("span.disclosure-label", "Or choose a specific date"), h("span.disclosure-icon", { "aria-hidden": "true" })),
        h("div.disclosure-body", h("label.control-label", { for: dateInput.id }, "Your target date"), dateInput,
          state.target_date ? h("button.btn.btn-link.btn-sm", { type: "button", onclick: () => { state.target_date = null; draw(); } },
            "Clear the date") : null)),
      radioGroup("weekly", "How much time can you realistically give this each week?", "Optional. It only changes how many actions "
        + "the roadmap highlights at a time.", WEEKLY, state.weekly_hours, (v) => { state.weekly_hours = v; }),
      state.timeframe || state.target_date || state.weekly_hours
        ? h("button.btn.btn-link.btn-sm", { type: "button", onclick: () => {
          state.timeframe = null; state.target_date = null; state.weekly_hours = null; draw();
        } }, "Clear my answers") : null);
  }

  api.goal(target).then((g) => {
    Object.assign(state, { timeframe: g.timeframe, target_date: g.target_date, weekly_hours: g.weekly_hours,
      checkpoint_dates: g.checkpoint_dates || {} });
    draw();
  }).catch(() => draw());

  async function save() {
    mount(errorSlot);
    try {
      await api.setGoal(target, { timeframe: state.target_date ? null : state.timeframe, target_date: state.target_date,
        weekly_hours: state.weekly_hours, checkpoint_dates: state.checkpoint_dates });
      return true;
    } catch (err) {
      mount(errorSlot, notice(err.message || "Your goal couldn't be saved. Try again.", { tone: "error" }));
      return false;
    }
  }

  const el = h("section.card.goal-card", { "aria-labelledby": "goal-title" },
    h("h2.card-title", { id: "goal-title" }, "Your goal"),
    h("p.muted", "Tell Trace when you'd like to make this move. Both questions are optional, and you can change them later."),
    body, errorSlot);
  return { el, save };
}
