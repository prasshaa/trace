// Target companies (POC iteration): an optional, searchable, multi-select list of companies the user would like to work
// at, per target role. Saved as soon as it changes (PUT /target-companies), so it is there when the user returns.
//
// There is no company directory: search covers only the employers Trace holds job-posting research for, and the API's
// note says so. Any other name can be added as the user's own choice, shown as unverified. Choosing companies never
// changes the plan; it only decides which employer research the report shows.
import { api } from "./api.js";
import { h, mount, uid } from "./dom.js";
import { badge, notice } from "./ui.js";

const SEARCH_DELAY = 200;

export function companyPicker(target, { onChange = () => {} } = {}) {
  const listId = uid("company-list");
  const hintId = uid("company-hint");
  let chosen = [];                         // [{name, source}] as the server returned them
  let options = [];                        // [{name, kind: "researched" | "own", detail}]
  let active = -1;
  let timer = null;
  let seq = 0;

  const live = h("p.visually-hidden", { "aria-live": "polite" });
  const errorSlot = h("div");
  const chips = h("ul.company-chips", { "aria-label": "Companies you've chosen" });
  const input = h("input.search.company-input", {
    type: "text", role: "combobox", autocomplete: "off", "aria-autocomplete": "list", "aria-expanded": "false",
    "aria-controls": listId, "aria-describedby": hintId, placeholder: "Search or type a company name",
    "aria-label": "Search companies",
  });
  const list = h("ul.company-options", { id: listId, role: "listbox", "aria-label": "Matching companies", hidden: true });
  const hint = h("p.hint", { id: hintId }, "Loading…");

  const isChosen = (name) => chosen.some((c) => c.name.toLowerCase() === name.trim().replace(/\s+/g, " ").toLowerCase());

  function drawChips() {
    mount(chips, chosen.length ? chosen.map((c) => h("li.company-chip",
      h("span.company-chip-name", c.name),
      c.source === "user_provided" ? h("span.company-chip-tag", "Your own choice") : null,
      h("button.company-chip-remove", { type: "button", "aria-label": `Remove ${c.name}`, onclick: () => remove(c.name) },
        h("span", { "aria-hidden": "true" }, "×")))) : h("li.company-chips-empty.muted.small", "No companies chosen."));
  }

  async function save(names, message) {
    mount(errorSlot);
    try {
      const res = await api.setTargetCompanies(target, names);
      chosen = res.companies;
      drawChips();
      live.textContent = message;
      onChange(chosen);
    } catch (err) {
      mount(errorSlot, notice(err.message || "Your companies couldn't be saved. Try again.", { tone: "error" }));
    }
  }

  function add(name) {
    const clean = name.trim().replace(/\s+/g, " ");
    if (!clean || isChosen(clean)) return;
    input.value = "";
    closeList();
    save([...chosen.map((c) => c.name), clean], `${clean} added.`);
  }

  function remove(name) {
    save(chosen.map((c) => c.name).filter((n) => n !== name), `${name} removed.`);
    input.focus();
  }

  function closeList() {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  }

  function drawOptions() {
    if (!options.length) return closeList();
    mount(list, options.map((o, i) => h(`li.company-option${i === active ? ".is-active" : ""}`, {
      id: `${listId}-${i}`, role: "option", "aria-selected": String(i === active),
      onmousedown: (e) => { e.preventDefault(); add(o.name); },
    },
    h("span.company-option-name", o.kind === "own" ? `Add "${o.name}" as your own choice` : o.name),
    h("span.company-option-detail", o.detail))));
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    if (active >= 0) input.setAttribute("aria-activedescendant", `${listId}-${active}`);
    else input.removeAttribute("aria-activedescendant");
  }

  async function search() {
    const q = input.value.trim();
    const mine = ++seq;
    let res;
    try {
      res = await api.searchCompanies(target, q);
    } catch {
      return;                                          // search is a convenience: typing a name still works
    }
    if (mine !== seq) return;
    hint.textContent = res.note;
    options = res.results.filter((r) => !isChosen(r.name)).map((r) => ({
      name: r.name, kind: "researched",
      detail: r.has_posting_for_target ? "Trace has posting research for this role" : "Trace's research covers other roles only",
    }));
    if (q && !res.exact_match && !isChosen(q)) options.push({ name: q, kind: "own", detail: "Not verified by Trace; nothing is assumed about it" });
    if (!q) options = options.slice(0, 8);
    active = -1;
    if (document.activeElement === input) drawOptions();
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(search, SEARCH_DELAY);
  });
  // Suggestions open on typing, a click in the box or ArrowDown, never on focus alone: after a removal, focus returns
  // here without a list covering the page's own buttons.
  input.addEventListener("click", () => search());
  input.addEventListener("blur", () => setTimeout(closeList, 120));
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" && list.hidden) {
      e.preventDefault();
      search();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!options.length) return;
      e.preventDefault();
      active = e.key === "ArrowDown" ? (active + 1) % options.length : (active - 1 + options.length) % options.length;
      drawOptions();
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (active >= 0 && options[active]) add(options[active].name);
      else if (input.value.trim()) add(input.value);
    } else if (e.key === "Escape" && !list.hidden) {
      e.preventDefault();
      closeList();
    }
  });

  const el = h("div.company-picker",
    h("label.control-label", { for: input.id || (input.id = uid("company")) }, "Search companies"),
    h("div.company-combo", input, list),
    hint, live, errorSlot,
    h("div.company-chosen", h("p.mini-title", "Your companies"), chips));

  api.targetCompanies(target).then((res) => {
    chosen = res.companies;
    drawChips();
    hint.textContent = "Trace has job-posting research for a small number of companies only; this is not a list of all "
      + "companies. You can add any company as your own choice.";
    onChange(chosen);
  }).catch((err) => {
    hint.textContent = "";
    mount(errorSlot, notice(err.message || "Your companies couldn't be loaded.", { tone: "error" }));
  });

  return { el, chosen: () => chosen };
}

export const companySourceBadge = (source) => (source === "user_provided" ? badge("Your own choice · not verified", "muted") : null);
