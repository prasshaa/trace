// Review your profile: ONE consolidated review of an uploaded resume (GET /api/v1/resume-import), confirmed with ONE
// action (POST /api/v1/resume-import/{id}/confirm). The server decides everything: what was found, what is new, which
// role sets the starting point, and whether the review is complete; the page only collects the user's corrections.
//
// Every role, found in the resume or added here, uses ONE card with one field contract (D49): job title, employer,
// employment type, start month and year, an end month and year unless "I currently work here", and a description. The
// server checks it (POST …/check while editing, and again on confirm) and names each field to fix. "Closest Trace role"
// is asked on the one role that sets the starting point, as the server places it; it moves when a later role is added.
// Text the parser couldn't place can become a role (prefilled with what that text supports) or be left out.
//
// Nothing is saved while the user edits. "Confirm profile and continue" sends every change at once and the server applies
// them in one transaction (all or nothing). "Replace resume" uploads another file in place of this import; "Discard this
// import and start over" deletes the unconfirmed import after a confirmation. Confirmed records are never touched.
import { ApiError, api } from "../api.js";
import { h, mount, uid } from "../dom.js";
import { EMPLOYMENT_TYPE, partialDate, plural } from "../copy.js";
import { busy, button, confirmDialog, journey, notice, pageHeader, toast } from "../ui.js";
import { fileDisclosure, fileType, pickFile, uploadMessage } from "./setup.js";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const KIND = { certification: "Certification", independent_project: "Project", volunteering: "Volunteering and leadership", award: "Award" };
// The nature of the work (D50): one choice, no follow-up questions. Concurrent work needs no checkbox: the server treats a
// part-time, freelance, founder or volunteer role that overlaps a full-time or contract role as running alongside it.
const TYPES = [["full_time", "Full-time"], ["part_time", "Part-time"], ["contract", "Contract"],
  ["freelance", "Freelance or self-employed"], ["internship", "Internship"], ["apprenticeship", "Apprenticeship"],
  ["founder", "Founder or co-founder"], ["volunteer", "Volunteer"]];
const MIN_DESCRIPTION = "At least one sentence (20 characters): what you did and for whom. It describes your experience to Trace.";
const ROLE_HELP = "Trace uses this to map your most recent job to its role framework, which sets your career starting point. "
  + "It doesn't change your job title.";

export async function render(root, ctx) {
  let view;
  try {
    view = await api.resumeImport();
  } catch (err) {
    mount(root, notice((err && err.message) || "Your resume couldn't be loaded.", { tone: "error" }));
    return;
  }
  if (view.status !== "ready") return ctx.go("start");
  draw(root, ctx, view);
}

// ------------------------------------------------------------------------------------------------ edit state
function state() {
  return { work: new Map(), education: new Map(), skills: new Map(), activities: new Map(), updates: new Map(),
    added: [], ignored: new Set(), cards: new Map(), showErrors: false };
}
const entry = (map, id) => (map.has(id) ? map.get(id) : map.set(id, { id }).get(id));
const ROLE_FIELDS = ["remove", "title", "employer", "employment_type", "role_id", "start", "end", "description"];

/** What is sent: only what the user changed or chose on found roles; every field of an added role. */
function toEdits(s) {
  const work = [...s.work.values()].map((e) => {
    const out = { id: e.id };
    for (const k of ROLE_FIELDS) if (e[k] !== undefined) out[k] = e[k];
    if (e.bullets) out.bullets = [...e.bullets.values()];
    return out;
  });
  const live = s.added.filter((a) => !a.removed);
  const out = { work, education: [...s.education.values()], skills: [...s.skills.values()], activities: [...s.activities.values()],
    updates: [...s.updates.values()], ignored: [...s.ignored] };
  if (live.length) {
    out.added_work = live.map((a) => {
      const o = {};
      for (const k of ["title", "employer", "employment_type", "role_id", "start", "end", "description", "from_unresolved"]) {
        if (a[k] !== undefined && a[k] !== null) o[k] = a[k];
      }
      return o;
    });
  }
  return out;
}
/** The server names an added role N<i> by its place among the roles still added: map it back to the card. */
function cardKey(s, item) {
  if (!item.startsWith("N")) return item;
  const live = s.added.filter((a) => !a.removed);
  const a = live[Number(item.slice(1))];
  return a ? a.key : item;
}

// ------------------------------------------------------------------------------------------------ small controls
function textInput(label, value, onChange, { required = false, field = null } = {}) {
  const id = uid("f");
  const input = h("input.input", { id, type: "text", value: value || "", autocomplete: "off", "aria-required": required ? "true" : null });
  input.addEventListener("input", () => onChange(input.value.trim()));
  return h("div.field.imp-field", { "data-field": field },
    h("label", { for: id }, label, required ? h("span.req", { "aria-hidden": "true" }, " *") : h("span.muted", " (optional)")), input);
}

function monthYear(label, d, onChange, { field, required = true } = {}) {
  const id = uid("d");
  const month = h("select.input.imp-month", { id: `${id}-m`, "aria-label": `${label}: month`, "aria-required": required ? "true" : null },
    h("option", { value: "" }, "Month"), MONTHS.map((m, i) => h("option", { value: String(i + 1) }, m)));
  const year = h("input.input.imp-year", { id: `${id}-y`, type: "number", min: "1950", max: "2100", inputmode: "numeric",
    placeholder: "Year", "aria-label": `${label}: year`, "aria-required": required ? "true" : null });
  if (d && d.year) year.value = String(d.year);
  if (d && d.month) month.value = String(d.month);
  const sync = () => {
    const y = parseInt(year.value, 10);
    const m = parseInt(month.value, 10);
    if (y && m) onChange({ year: y, month: m, precision: "month" });
    else if (y) onChange({ year: y, precision: "year" });
    else onChange({ precision: "unknown" });
  };
  [month, year].forEach((el) => el.addEventListener("change", sync));
  return h("fieldset.imp-date", { "data-field": field },
    h("legend", label, required ? h("span.req", { "aria-hidden": "true" }, " *") : null),
    h("div.imp-date-row", month, year));
}

function removable(card, onToggle, labelText) {
  const undo = button("Undo", { variant: "ghost", small: true });
  const remove = button("Remove", { variant: "danger-ghost", small: true, attrs: { "aria-label": `Remove ${labelText}` } });
  const removed = h("p.imp-removed", { hidden: true }, `${labelText} will be left out. `, undo);
  remove.addEventListener("click", () => { card.classList.add("is-removed"); removed.hidden = false; onToggle(true); undo.focus(); });
  undo.addEventListener("click", () => { card.classList.remove("is-removed"); removed.hidden = true; onToggle(false); remove.focus(); });
  return { remove, removed };
}

// ------------------------------------------------------------------------------------------------ bullets
function bulletItem(b, s, job) {
  const text = h("p.imp-bullet-text", b.text, b.status === "present" ? h("span.imp-known", " · already in your profile") : null);
  const li = h("li.imp-bullet");
  const edit = button("Edit", { variant: "ghost", small: true, attrs: { "aria-label": `Edit: ${b.text.slice(0, 60)}` } });
  const del = button("Remove", { variant: "danger-ghost", small: true, attrs: { "aria-label": `Remove: ${b.text.slice(0, 60)}` } });
  const bullets = () => {
    const e = entry(s.work, job.id);
    e.bullets = e.bullets || new Map();
    return e.bullets;
  };
  edit.addEventListener("click", () => {
    const id = uid("b");
    const area = h("textarea.input.imp-bullet-edit", { id, rows: 3, "aria-label": "Edit this line" }, b.text);
    const done = button("Done", { variant: "secondary", small: true });
    done.addEventListener("click", () => {
      const v = area.value.trim();
      if (v.length >= 3 && v !== b.text) bullets().set(b.id, { id: b.id, text: v });
      text.textContent = v.length >= 3 ? v : b.text;
      mount(li, text, h("div.imp-bullet-actions", edit, del));
      edit.focus();
    });
    mount(li, area, h("div.imp-bullet-actions", done));
    area.focus();
  });
  del.addEventListener("click", () => {
    const removed = li.classList.toggle("is-removed");
    del.textContent = removed ? "Undo" : "Remove";
    if (removed) bullets().set(b.id, { id: b.id, remove: true });
    else bullets().delete(b.id);
  });
  mount(li, text, h("div.imp-bullet-actions", edit, del));
  return li;
}


const STATUS = {
  present: ["Already in your profile", "Nothing here will change."],
  update: ["Different dates from your profile", "Keep this role to correct the dates in your profile (your earlier dates are kept "
    + "in its history), or leave it out to keep your profile as it is."],
};
const REVIEW = { title: "the job title", employer: "the employer", start: "the start month", end: "the end date" };

/** ONE card for every role: found in the resume (job from the view) or added here (job = the added model). `edit` is
 *  where its changes go: the sparse edit of a found role, or the added role itself. */
function roleCard(job, s, edit, { added = false, onRemove = null, onChange = () => {} } = {}) {
  const key = added ? edit.key : job.id;
  const card = h("article.card.imp-card.imp-role", { "data-item": key });
  s.cards.set(key, card);
  if (job.status === "present") {
    card.classList.add("imp-card-compact", "is-known");
    mount(card, h("div.imp-card-head", h("p.eyebrow", STATUS.present[0]),
      h("h3.imp-card-title", [job.title, job.employer ? h("span.imp-at", ` · ${job.employer}`) : null]),
      h("p.muted", STATUS.present[1])));
    return card;
  }
  const set = (k, v) => { edit[k] = v; onChange(k); };
  const titleEl = h("h3.imp-card-title");
  const showTitle = () => mount(titleEl, edit.title ?? job.title ?? "Job title needed",
    (edit.employer ?? job.employer) ? h("span.imp-at", ` · ${edit.employer ?? job.employer}`) : null);
  showTitle();
  const head = h("div.imp-card-head",
    added ? h("p.eyebrow", edit.from_unresolved ? "Role added from your resume text" : "Role you're adding")
      : job.status === "update" ? h("p.eyebrow", STATUS.update[0]) : null,
    titleEl,
    job.status === "update" ? h("p.muted", STATUS.update[1]) : null);
  const review = (job.review || []).filter((f) => REVIEW[f]);
  const checks = review.length ? notice([
    h("span", `Check ${review.map((f) => REVIEW[f]).join(" and ")}: Trace couldn't read ${review.length > 1 ? "them" : "it"} `
      + "clearly from your resume, so nothing was filled in for you."),
    job.source ? h("span.imp-source", " From your resume: ", h("q", job.source)) : null], { tone: "warning" }) : null;

  // the nature of the work: one choice
  const typeId = uid("t");
  const type = h("select.input", { id: typeId, "aria-required": "true" }, h("option", { value: "" }, "Choose…"),
    TYPES.map(([v, t]) => h("option", { value: v }, t)));
  const initialType = edit.employment_type ?? job.employment_type;
  if (initialType && initialType !== "unknown") type.value = initialType;
  type.addEventListener("change", () => { set("employment_type", type.value || "unknown"); type.removeAttribute("aria-invalid"); });
  const alongside = h("p.hint.imp-alongside", { hidden: true });
  card.showAlongside = (otherTitle) => {
    alongside.hidden = !otherTitle;
    alongside.textContent = otherTitle ? `Its dates overlap your role "${otherTitle}", so Trace treats it as work alongside that job. `
      + "It stays in your history either way." : "";
  };

  // dates: month and year. "I currently work here" appears only while the end date is empty; ticking it hides the end
  // date. A missing end date is never read as current: the user says so.
  const current = h("input", { type: "checkbox", id: uid("c") });
  const startDate = edit.start ?? job.start;
  const endDate = edit.end ?? (job.current ? { precision: "ongoing" } : job.end);
  current.checked = Boolean(endDate && endDate.precision === "ongoing");
  let lastEnd = endDate && endDate.precision !== "ongoing" ? endDate : { precision: "unknown" };
  const currentRow = h("label.check.imp-current", { "data-field": "current" }, current, h("span", "I currently work here"));
  const emptyEnd = () => !lastEnd || lastEnd.precision === "unknown";
  const showEnd = () => { endBox.hidden = current.checked; currentRow.hidden = !current.checked && !emptyEnd(); };
  const endBox = monthYear("End", lastEnd, (d) => { lastEnd = d; set("end", d); showEnd(); }, { field: "end" });
  current.addEventListener("change", () => { set("end", current.checked ? { precision: "ongoing" } : lastEnd); showEnd(); });
  showEnd();

  // description: required for every role (never generated)
  const descId = uid("dsc");
  const initialDesc = edit.description ?? (job.description && job.description.length ? job.description[0].text : "");
  const desc = h("textarea.input", { id: descId, rows: 3, "aria-required": "true", "aria-describedby": `${descId}-h` }, initialDesc);
  desc.addEventListener("input", () => set("description", desc.value.trim()));

  const roleSlot = h("div.imp-role-slot.imp-span", { "data-field": "role_id" });
  card.roleSlot = roleSlot;
  const fields = h("div.imp-grid",
    textInput("Job title", edit.title ?? job.title, (v) => { set("title", v); showTitle(); }, { required: true, field: "title" }),
    textInput("Employer", edit.employer ?? job.employer, (v) => { set("employer", v); showTitle(); }, { required: true, field: "employer" }),
    h("div.field.imp-field", { "data-field": "employment_type" },
      h("label", { for: typeId }, "Employment type", h("span.req", { "aria-hidden": "true" }, " *")), type, alongside),
    monthYear("Start", startDate, (d) => set("start", d), { field: "start" }),
    h("div.imp-end", endBox, currentRow),
    h("div.field.imp-field.imp-span", { "data-field": "description" },
      h("label", { for: descId }, "Description", h("span.req", { "aria-hidden": "true" }, " *")), desc,
      h("p.hint", { id: `${descId}-h` }, MIN_DESCRIPTION)),
    roleSlot);
  const bullets = job.bullets && job.bullets.length
    ? h("ul.imp-bullets", { "aria-label": "Responsibilities and achievements" }, job.bullets.map((b) => bulletItem(b, s, job))) : null;
  const { remove, removed } = removable(card, (on) => {
    if (added) { edit.removed = on; if (on && onRemove) onRemove(); onChange("remove"); return; }
    if (on) entry(s.work, job.id).remove = true; else delete entry(s.work, job.id).remove;
    onChange("remove");
  }, edit.title ?? job.title ?? "This role");
  mount(card, h("div.imp-card-top", head, remove), removed, checks, fields, bullets);
  return card;
}

/** The closest Trace role on every role card (D50). REQUIRED only on the card the server says sets the starting point;
 *  optional elsewhere. An exact title match is preselected and mapped after confirmation anyway; a role suggested from
 *  the words of the title is preselected, labelled, and saved as the user's own choice only if it is still selected
 *  when they confirm. The original job title never changes. */
function placeRoleChoices(s, startKey, byKey, sendTo) {
  for (const [k, card] of s.cards) {
    if (!card.roleSlot) continue;
    const role = byKey[k];
    const starting = k === startKey;
    card.classList.toggle("is-starting", starting);
    if (!role || !role.choices.length) {
      mount(card.roleSlot);
      delete card.roleSlot.dataset.sig;
      continue;
    }
    const sig = JSON.stringify([starting, role.suggested_role_id, role.basis, role.choices.length]);
    if (card.roleSlot.dataset.sig === sig) continue;
    const target = sendTo(k);
    const id = uid("r");
    const suggested = role.choices.filter((c) => c.suggested);
    const others = role.choices.filter((c) => !c.suggested);
    const sel = h("select.input", { id, "aria-required": starting ? "true" : null, "aria-describedby": `${id}-hint` },
      h("option", { value: "" }, starting ? "Choose…" : "No match yet"),
      suggested.length ? h("optgroup", { label: "Matches for your job title" }, suggested.map((c) => h("option", { value: c.role_id }, c.title))) : null,
      h("optgroup", { label: suggested.length ? "Other roles" : "Roles" }, others.map((c) => h("option", { value: c.role_id }, c.title))));
    const exact = role.basis === "alias";
    if (target.role_touched) sel.value = target.role_id || "";
    else if (role.suggested_role_id) {
      sel.value = role.suggested_role_id;
      // an exact match is mapped after confirmation; a suggestion from the title's words is the user's choice if kept
      if (starting || !exact) target.role_id = role.suggested_role_id; else delete target.role_id;
    } else delete target.role_id;
    sel.addEventListener("change", () => {
      target.role_touched = true;
      target.role_id = sel.value || undefined;
      sel.removeAttribute("aria-invalid");
      mount(hint, hintText());
    });
    const hintText = () => {
      if (!sel.value) return starting ? `${ROLE_HELP} Trace couldn't match your job title on its own, so choose the closest one.`
        : "Optional. Matching this job lets Trace use it in your career history. Your title stays as you wrote it.";
      if (target.role_touched) return starting ? ROLE_HELP : "Your choice. Your title stays as you wrote it.";
      if (exact) return starting ? `${ROLE_HELP} Matched from your job title; change it if another role fits better.`
        : "Matched from your job title. Change it if another role fits better.";
      return `Suggested because your title includes "${role.matched_words}". It's saved only if you keep it selected; `
        + (starting ? ROLE_HELP : "your title stays as you wrote it.");
    };
    const hint = h("p.hint", { id: `${id}-hint` }, hintText());
    mount(card.roleSlot, h("div.field.imp-field",
      h("label", { for: id }, starting ? "Closest Trace role" : "Closest Trace role",
        starting ? h("span.req", { "aria-hidden": "true" }, " *") : h("span.muted", " (optional)")),
      sel, hint, starting ? h("p.hint.imp-starting-note", "This is your most recent main job, so it sets your career starting point.") : null));
    card.roleSlot.dataset.sig = sig;
  }
}

// ------------------------------------------------------------------------------------------------ field errors
function clearErrors(root, summary) {
  root.querySelectorAll(".field-error").forEach((el) => el.remove());
  root.querySelectorAll("[aria-invalid]").forEach((el) => el.removeAttribute("aria-invalid"));
  mount(summary);
}

function showErrors(root, s, errors, summary, { focus = false } = {}) {
  clearErrors(root, summary);
  if (!errors.length) return;
  const items = [];
  let first = null;
  for (const f of errors) {
    const card = s.cards.get(cardKey(s, f.item));
    if (!card) continue;
    const name = card.querySelector(".imp-card-title");
    const field = f.field.startsWith("bullet:") ? card.querySelector(".imp-bullets") : card.querySelector(`[data-field="${f.field}"]`);
    const control = field && (field.querySelector("input:not([type=checkbox]), select, textarea") || field);
    const mid = uid("err");
    if (field) {
      field.append(h("p.field-error", { id: mid }, f.message));
      if (control) { control.setAttribute("aria-invalid", "true"); control.setAttribute("aria-describedby", mid); }
    }
    first = first || control || card;
    const go = h("button.linklike", { type: "button" }, `${name ? name.textContent : "A role"}: ${f.message}`);
    go.addEventListener("click", () => { (control || card).focus(); (control || card).scrollIntoView({ block: "center" }); });
    items.push(h("li", go));
  }
  mount(summary, notice([h("p", plural(errors.length, "thing needs", "things need") + " fixing before your profile can be saved:"),
    h("ul.imp-error-list", items)], { tone: "error", title: "Check your profile", role: "alert" }));
  if (focus && first) { first.focus(); first.scrollIntoView({ block: "center" }); }
}

// ------------------------------------------------------------------------- text the parser couldn't place
const UNPLACED = {
  possible_role: "This looks like a role, but Trace couldn't separate its title, employer or dates.",
  unattached_text: "This text isn't attached to any role.",
  role_without_title: "Trace found the dates of a role, but not its job title.",
  employer_unclear: "This describes a company rather than naming it.",
};

function fromSuggestion(u) {
  const g = u.suggested || {};
  const d = (x) => (x && x.year && x.month ? { year: x.year, month: x.month, precision: "month" }
    : x && x.precision === "ongoing" ? { precision: "ongoing" } : x && x.year ? { year: x.year, precision: "year" } : { precision: "unknown" });
  return { title: g.title || "", employer: g.employer || "", start: d(g.start), end: d(g.end), description: g.description || "",
    employment_type: "unknown", from_unresolved: u.id };
}

function unplacedSection(view, s, addRole) {
  const items = view.unresolved.map((u) => {
    const li = h("li.imp-unplaced", { "data-unplaced": u.id });
    const draw = () => {
      if (s.ignored.has(u.id)) {
        const undo = button("Undo", { variant: "ghost", small: true });
        undo.addEventListener("click", () => { s.ignored.delete(u.id); draw(); });
        return mount(li, h("p.muted", "Left out: this text won't be saved. ", undo));
      }
      const live = s.added.find((a) => a.from_unresolved === u.id && !a.removed);
      if (live) {
        return mount(li, h("p.muted", "Added as a role in Work experience below. Remove that role to undo."));
      }
      const add = button("Add as a role", { variant: "secondary", small: true });
      const ignore = button("Leave this out", { variant: "ghost", small: true });
      add.addEventListener("click", () => { addRole(fromSuggestion(u), () => draw()); draw(); });
      ignore.addEventListener("click", () => { s.ignored.add(u.id); draw(); });
      mount(li, h("p.muted", UNPLACED[u.kind] || "Trace couldn't place this text."), h("blockquote.imp-quote", u.text),
        h("div.imp-bullet-actions", add, ignore));
    };
    draw();
    return li;
  });
  return h("section.imp-section", { "aria-labelledby": "imp-unplaced" },
    h("h2.section-title", { id: "imp-unplaced" }, "Text we couldn't place"),
    notice("We found text that may contain another role. Please review it before confirming your profile.", { tone: "warning", role: "status" }),
    h("p.muted", "Add it as a role (Trace fills in what the text supports), or leave it out. Text you leave out isn't saved "
      + "and doesn't stop you confirming."),
    h("ul.imp-unplaced-list", items));
}

// ------------------------------------------------------------------------------------------------ the page
function draw(root, ctx, view) {
  const s = state();
  const status = h("div.imp-status", { "aria-live": "polite" });
  const summaryBox = h("div.imp-errors");
  const update = view.mode === "update";
  const replacing = view.mode === "replace";            // D52: this draft replaces the confirmed profile on confirmation
  const countEl = h("span");
  const skillsTotal = view.skills.reduce((n, g) => n + g.items.length, 0);
  const setCount = (n) => mount(countEl, plural(n, "role", "roles"));
  setCount(view.work.filter((j) => !j.existing && j.status !== "present").length);

  // edits go to the sparse edit of a found role, or to the added role itself
  const sendTo = (key) => s.added.find((a) => a.key === key) || entry(s.work, key);
  let timer = null;
  let latest = null;
  const recheck = () => {
    if (!view.staged) return;
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        const result = await api.checkImport(view.import_id, toEdits(s));
        latest = result;
        const byKey = {};
        for (const [k, o] of Object.entries(result.role_options || {})) byKey[cardKey(s, k)] = o;
        placeRoleChoices(s, cardKey(s, result.starting_role || ""), byKey, sendTo);
        for (const [k, card] of s.cards) {
          if (!card.showAlongside) continue;
          const serverKey = [...Object.keys(result.alongside || {})].find((x) => cardKey(s, x) === k);
          const other = serverKey ? s.cards.get(cardKey(s, result.alongside[serverKey])) : null;
          const t = other && other.querySelector(".imp-card-title");
          card.showAlongside(t ? t.childNodes[0].textContent : null);
        }
        setCount(result.roles);
        if (s.showErrors) showErrors(root, s, result.errors, summaryBox);
      } catch { /* a check is advisory: confirm checks again */ }
    }, 400);
  };
  const onChange = (k) => { if (["title", "start", "end", "remove", "employment_type", "employer", "description"].includes(k) || s.showErrors) recheck(); };
  s.onEducationChange = () => { if (s.showErrors) recheck(); };

  const workList = h("div.imp-work-list");
  const addRole = (model, onRemoved) => {
    const a = { key: uid("added"), ...model };
    s.added.push(a);
    const card = roleCard({ id: a.key, status: "new", review: [], bullets: [] }, s, a,
      { added: true, onChange, onRemove: () => { onRemoved && onRemoved(); } });
    workList.append(card);
    recheck();
    card.querySelector("input").focus();
  };

  const confirm = button(replacing ? "Replace my profile with this" : "Confirm profile and continue");
  confirm.addEventListener("click", () => busy(confirm, replacing ? "Replacing your profile…" : "Saving your profile…", async () => {
    mount(status);
    try {
      await api.confirmImport(view.import_id, toEdits(s));
      toast(replacing ? "Your profile was replaced with your new resume." : "Your profile is saved.");
      ctx.go("target");                               // next step; the router puts the data-use notice first when it's due
    } catch (err) {
      if (err instanceof ApiError && err.code === "review_incomplete") {
        s.showErrors = true;
        showErrors(root, s, err.fields || [], summaryBox, { focus: true });
        return;
      }
      clearErrors(root, summaryBox);
      const msg = err instanceof ApiError && err.status === 404
        ? "This import changed in another tab. Reload the page to see the current version."
        : err instanceof ApiError && err.reference
          ? `Something went wrong on our side, and nothing was changed. Your edits are still here; try again. If it keeps happening, quote reference ${err.reference}.`
          : (err.message || "Your profile couldn't be saved. Nothing was changed; try again.");
      mount(status, notice(msg, { tone: "error", title: "Not saved" }));
      status.scrollIntoView({ block: "center" });
    }
  }));

  const replaceStatus = h("div", { "aria-live": "polite" });
  const replaceInput = pickFile("replace-file", (file) => {
    if (!fileType(file)) { mount(replaceStatus, notice("Choose a PDF or Word (.docx) file.", { tone: "error" })); return; }
    confirmDialog({
      title: "Replace this import?",
      body: [h("p", `Trace will read ${file.name} instead. Everything in this review, including your edits, will be replaced.`),
        h("p", "Anything already confirmed in your profile isn't affected.")],
      confirmLabel: "Replace", busyLabel: "Reading…", tone: "primary",
      action: async () => { await api.importResume(await file.arrayBuffer(), fileType(file), true, replacing); },
    }).then((ok) => { if (ok) { toast("Your new resume was read."); render(root, ctx); } })
      .catch((err) => mount(replaceStatus, notice(uploadMessage(err), { tone: "error" })));
  });
  const discard = button("Discard this import and start over", { variant: "danger-ghost" });
  discard.addEventListener("click", () => confirmDialog({
    title: "Discard this import?",
    body: [h("p", "Everything Trace found in this resume will be deleted, including your edits on this page."),
      h("p", update || replacing ? "Your confirmed profile stays exactly as it is."
        : "Nothing has been confirmed yet, so your profile will be empty again."),
      h("p", "This can't be undone.")],
    confirmLabel: "Discard import", busyLabel: "Discarding…",
    action: async () => { await api.discardImport(); },
  }).then((done) => { if (done) { toast("Import discarded."); ctx.refresh(); } }));

  for (const j of view.work) workList.append(roleCard(j, s, entry(s.work, j.id), { onChange }));
  const initial = {};
  for (const j of view.work) if (j.role) initial[j.id] = j.role;
  const startKey = view.staged ? view.starting_role : (view.work.find((j) => j.role) || {}).id;
  placeRoleChoices(s, startKey, view.staged ? initial : (startKey ? { [startKey]: initial[startKey] } : {}), sendTo);
  for (const j of view.work) {
    const card = s.cards.get(j.id);
    const other = j.alongside_of && view.work.find((x) => x.id === j.alongside_of);
    if (card && card.showAlongside) card.showAlongside(other ? other.title : null);
  }
  const addMissing = view.staged ? button("Add a role that's missing", { variant: "secondary", small: true }) : null;
  if (addMissing) addMissing.addEventListener("click", () => addRole({ employment_type: "unknown", start: { precision: "unknown" }, end: { precision: "unknown" } }));
  const unplaced = view.staged && view.unresolved && view.unresolved.length ? unplacedSection(view, s, addRole) : null;

  mount(root, h("section.page.imp-page",
    journey("profile", {}),
    pageHeader("Step 1 · Your profile", "Review your profile",
      replacing
        ? "Here's what Trace found in your new resume. Correct anything that's wrong, then confirm to replace your profile."
        : update
        ? "Here's what's new in your resume. Your confirmed profile hasn't changed: correct or remove anything below, then confirm once."
        : "Here's what Trace found in your resume. Correct anything that's wrong, fill in what's missing, then confirm once."),
    h("p.imp-summary.muted", countEl, " · ", [plural(view.education.length, "qualification", "qualifications"),
      plural(skillsTotal, "skill", "skills"), view.activities.length ? plural(view.activities.length, "certification or activity", "certifications and activities") : null]
      .filter(Boolean).join(" · ")),
    h("p.muted.imp-required-note", "Fields marked * are needed for every role."),
    replacing ? replaceNotice(view.replaces || {}) : null,
    !view.staged ? notice("This import was read by an earlier version of Trace. Replace your resume to read it again with the "
      + "current version, or confirm it as it is.", { tone: "info" }) : null,
    view.updates.length ? updatesCard(view.updates, s) : null,
    unplaced,
    h("section.imp-section", { "aria-labelledby": "imp-work" },
      h("h2.section-title", { id: "imp-work" }, "Work experience"), workList,
      addMissing ? h("p.imp-add-missing", addMissing) : null),
    view.education.length ? h("section.imp-section", { "aria-labelledby": "imp-edu" },
      h("h2.section-title", { id: "imp-edu" }, "Education"), view.education.map((d) => educationCard(d, s))) : null,
    view.skills.length ? h("section.imp-section", { "aria-labelledby": "imp-skills" },
      h("h2.section-title", { id: "imp-skills" }, "Skills"),
      h("p.muted", "Remove anything that isn't really a skill of yours. Items marked with a dotted outline weren't recognised, so check them."),
      view.skills.map((g) => h("div.imp-skill-group", h("h3.imp-skill-cat", g.category),
        h("ul.imp-skills", { "aria-label": g.category }, g.items.map((k) => skillChip(k, s)))))) : null,
    view.activities.length ? h("section.imp-section", { "aria-labelledby": "imp-acts" },
      h("h2.section-title", { id: "imp-acts" }, "Certifications and activities"), view.activities.map((a) => activityCard(a, s))) : null,
    h("section.card.imp-finish", { "aria-labelledby": "imp-next" },
      h("h2.card-title", { id: "imp-next" }, replacing ? "Replace your profile" : "Confirm your profile"),
      h("p", replacing
        ? "Your current career details and the plans built from them are deleted, and everything above becomes your profile, "
          + "in one step. If anything goes wrong, nothing changes. Next, you'll choose the role you want to reach."
        : "Everything above is saved to your private profile in one step, and this draft is then deleted. Next, you'll "
          + "choose the role you want to reach."),
      summaryBox, status,
      h("div.actions.imp-actions", confirm,
        h("label.btn.btn-secondary", { for: "replace-file" }, "Replace resume"), replaceInput, discard),
      replaceStatus,
      fileDisclosure(ctx.state.consent && ctx.state.consent.document))));
  root.querySelector(".page-title").focus({ preventScroll: true });
}

/** D52: what confirming this replacement deletes (the server's counts of the current profile). */
function replaceNotice(n) {
  const parts = [n.jobs ? plural(n.jobs, "job", "jobs") : null, n.education ? plural(n.education, "education entry", "education entries") : null,
    n.skills ? plural(n.skills, "skill", "skills") : null, n.activities ? plural(n.activities, "certification or project", "certifications and projects") : null,
    n.saved_plans ? plural(n.saved_plans, "saved plan version", "saved plan versions") : null].filter(Boolean);
  return notice(h("span", "When you confirm, this replaces your current profile",
    parts.length ? ` (${parts.join(", ")})` : "", ". Nothing is merged. Until you confirm, your profile stays as it is, "
    + "and discarding this draft leaves it unchanged."), { tone: "warning", title: "This will replace your profile" });
}

// ------------------------------------------------------------------------------------------------ other sections
function educationCard(d, s) {
  const card = h("article.card.imp-card.imp-edu", { "data-item": d.id });
  s.cards.set(d.id, card);
  const e = () => entry(s.education, d.id);
  const recheck = () => { if (s.onEducationChange) s.onEducationChange(); };
  const { remove, removed } = removable(card, (on) => { e().remove = on; recheck(); }, d.qualification || "This qualification");
  const known = d.status === "present";
  if (known) {
    mount(card, h("div.imp-card-head", h("p.eyebrow", STATUS.present[0]), h("h3.imp-card-title", d.qualification || "Qualification"),
      h("p.muted", STATUS.present[1])));
    return card;
  }
  // Phase 5: start and completion month and year; "Currently studying" replaces the completion date. Months the resume
  // doesn't state are left for the user to fill in (never invented).
  const studying = h("input", { type: "checkbox", id: uid("st") });
  studying.checked = Boolean(d.studying);
  const endBox = monthYear("Completed", d.studying ? null : d.end, (x) => { e().end = x; recheck(); }, { field: "end" });
  const showEnd = () => { endBox.hidden = studying.checked; };
  studying.addEventListener("change", () => { e().studying = studying.checked; showEnd(); recheck(); });
  showEnd();
  mount(card, h("div.imp-card-top", h("h3.imp-card-title", d.qualification || "Qualification"), remove), removed,
    h("div.imp-grid",
      textInput("Qualification", d.qualification, (v) => { e().qualification = v; recheck(); }, { required: true, field: "qualification" }),
      textInput("Field of study", d.field_of_study, (v) => { e().field_of_study = v; }, { field: "field_of_study" }),
      textInput("University, college or school", d.institution, (v) => { e().institution = v; recheck(); }, { required: true, field: "institution" }),
      monthYear("Started", d.start, (x) => { e().start = x; recheck(); }, { field: "start" }),
      h("div.imp-end", endBox, h("label.check.imp-current", { "data-field": "studying" }, studying, h("span", "Currently studying")))));
  return card;
}

function skillChip(k, s) {
  const chip = h("li.imp-skill");
  const name = h("span.imp-skill-name", k.name);
  const edit = h("button.imp-skill-btn", { type: "button", "aria-label": `Rename ${k.name}`, title: "Rename" }, "Edit");
  const del = h("button.imp-skill-btn.imp-skill-x", { type: "button", "aria-label": `Remove ${k.name}`, title: "Remove" }, "×");
  edit.addEventListener("click", () => {
    const input = h("input.input.imp-skill-input", { type: "text", value: name.textContent, "aria-label": `Rename ${k.name}` });
    const commit = () => {
      const v = input.value.trim();
      if (v && v !== k.name) { entry(s.skills, k.id).name = v; name.textContent = v; }
      mount(chip, name, edit, del);
      edit.focus();
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } if (e.key === "Escape") { mount(chip, name, edit, del); edit.focus(); } });
    input.addEventListener("blur", commit);
    mount(chip, input);
    input.focus();
  });
  del.addEventListener("click", () => {
    const off = chip.classList.toggle("is-removed");
    del.textContent = off ? "↺" : "×";
    del.setAttribute("aria-label", off ? `Keep ${k.name}` : `Remove ${k.name}`);
    if (off) entry(s.skills, k.id).remove = true;
    else delete entry(s.skills, k.id).remove;
  });
  if (!k.recognised) chip.classList.add("is-unrecognised");
  mount(chip, name, edit, del);
  return chip;
}

function activityCard(a, s) {
  const card = h("article.card.imp-card.imp-card-compact");
  const { remove, removed } = removable(card, (on) => { entry(s.activities, a.id).remove = on; }, a.title);
  mount(card, h("div.imp-card-top", h("div", h("p.eyebrow", KIND[a.kind] || "Other"), h("h3.imp-card-title", a.title)), remove), removed,
    h("div.imp-grid",
      textInput("Title", a.title, (v) => { if (v) entry(s.activities, a.id).title = v; }, { required: true }),
      textInput(a.kind === "certification" ? "Issued by" : "Organisation", a.organisation, (v) => { entry(s.activities, a.id).organisation = v; })),
    a.description ? h("p.imp-description", a.description) : null);
  return card;
}

function updatesCard(updates, s) {
  return h("section.imp-section", { "aria-labelledby": "imp-updates" },
    h("h2.section-title", { id: "imp-updates" }, "Changes to your existing profile"),
    h("p.muted", "Your resume shows different details for these items. They're applied only if you keep them selected."),
    h("ul.imp-updates", updates.map((u) => {
      const id = uid("u");
      const box = h("input", { type: "checkbox", id, checked: true });
      box.addEventListener("change", () => { if (box.checked) s.updates.delete(u.id); else s.updates.set(u.id, { id: u.id, remove: true }); });
      const changes = Object.entries(u.changes).map(([k, v]) => `${{ start: "start", end: "end", field_of_study: "field of study" }[k] || k}: `
        + (v && typeof v === "object" ? partialDate(v) : String(v))).join(", ");
      return h("li", h("label.check", box, h("span", h("strong", u.title || "An item"), ` · new ${changes}`)));
    })));
}

