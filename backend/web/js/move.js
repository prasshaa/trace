// "Where would you like to make this move?" (Phase 5, D51): specific companies, kinds of company, or open to options.
// Saved per target role (PUT /move-preference) when the user continues. A planning PREFERENCE: Trace holds no company
// directory, no hiring data and no company-specific transition research, so this never ranks companies, never says
// where to apply and never changes the plan except where the server says a kind of company adds sourced requirements.
import { api } from "./api.js";
import { h, mount, uid } from "./dom.js";
import { notice } from "./ui.js";

const SCOPES = [
  ["specific_companies", "I have specific companies in mind"],
  ["company_types", "I know the kind of company"],
  ["open", "I'm open to options"],
];
const MAX_TYPES = 3;
const MAX_COMPANIES = 20;

export function movePicker(target) {
  const state = { scope: null, company_types: [], companies: [], options: [], limits: "" };
  const body = h("div.move-body", h("p.muted", "Loading…"));
  const errorSlot = h("div");

  function companiesField() {
    const id = uid("co");
    const input = h("input.input", { id, type: "text", maxlength: "100", autocomplete: "off", placeholder: "Type a company and press Enter" });
    const list = h("ul.move-chips", { "aria-label": "Companies you added" });
    const drawList = () => mount(list, state.companies.map((name) => {
      const remove = h("button.imp-skill-btn.imp-skill-x", { type: "button", "aria-label": `Remove ${name}` }, "×");
      remove.addEventListener("click", () => { state.companies = state.companies.filter((c) => c !== name); drawList(); input.focus(); });
      return h("li.imp-skill", h("span.imp-skill-name", name), remove);
    }));
    const add = () => {
      const v = input.value.trim().replace(/\s+/g, " ");
      if (!v) return;
      if (!state.companies.some((c) => c.toLowerCase() === v.toLowerCase()) && state.companies.length < MAX_COMPANIES) state.companies.push(v);
      input.value = "";
      drawList();
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); } });
    input.addEventListener("blur", add);
    drawList();
    return h("div.field", h("label", { for: id }, "Companies"), input,
      h("p.hint", "Add up to 20. They're your own choices: Trace doesn't check them against a company list or hiring data."), list);
  }

  function typesField() {
    const boxes = state.options.map((o) => {
      const id = uid("ct");
      const box = h("input", { type: "checkbox", id, value: o.key, checked: state.company_types.includes(o.key) });
      box.addEventListener("change", () => {
        if (box.checked && state.company_types.length >= MAX_TYPES) { box.checked = false; return; }
        state.company_types = box.checked ? [...state.company_types, o.key] : state.company_types.filter((k) => k !== o.key);
      });
      return h("label.choice.move-type", { for: id }, box, h("span", h("span.move-type-label", o.label), h("span.hint", o.note)));
    });
    return h("fieldset.choice-group", h("legend", "Which kinds of company? Choose up to three."), h("div.choice-options.move-types", boxes));
  }

  function draw() {
    const group = uid("scope");
    mount(body,
      h("fieldset.choice-group",
        h("legend", "Where would you like to make this move?"),
        h("p.hint", "Optional. This is separate from the role you chose."),
        h("div.choice-options", SCOPES.map(([v, label]) => {
          const id = uid("sc");
          const input = h("input", { type: "radio", name: group, id, value: v, checked: v === state.scope });
          input.addEventListener("change", () => { state.scope = v; draw(); });
          return h("label.choice", { for: id }, input, h("span", label));
        }))),
      state.scope === "specific_companies" ? companiesField() : null,
      state.scope === "company_types" ? typesField() : null,
      state.scope ? h("p.hint.move-limits", state.limits) : null);
  }

  api.movePreference(target).then((p) => {
    Object.assign(state, { scope: p.scope, company_types: p.company_types || [], companies: (p.companies || []).map((c) => c.name),
      options: p.options || [], limits: p.limits || "" });
    draw();
  }).catch(() => draw());

  async function save() {
    mount(errorSlot);
    if (!state.scope) return true;                                  // optional: nothing chosen, nothing saved
    if (state.scope === "specific_companies" && !state.companies.length) {
      mount(errorSlot, notice("Add at least one company, or choose another option.", { tone: "error" }));
      return false;
    }
    if (state.scope === "company_types" && !state.company_types.length) {
      mount(errorSlot, notice("Choose at least one kind of company, or choose another option.", { tone: "error" }));
      return false;
    }
    try {
      await api.setMovePreference(target, { scope: state.scope, company_types: state.company_types,
        companies: state.scope === "specific_companies" ? state.companies : null });
      return true;
    } catch (err) {
      mount(errorSlot, notice(err.message || "Your answer couldn't be saved. Try again.", { tone: "error" }));
      return false;
    }
  }

  const el = h("section.card.move-card", { "aria-labelledby": "move-title" },
    h("h2.card-title", { id: "move-title" }, "Where you'd like to move"),
    body, errorSlot);
  return { el, save };
}
