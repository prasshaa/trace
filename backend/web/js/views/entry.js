// Adding to your profile by hand (product Phase 2, D7): structured forms for a job, a qualification, a certification or
// project, and a skill, plus "paste your experience" (parsed on the server into suggested updates). Nothing here decides
// anything: the server places each job in stated order, stores drafts, and compares pasted text with the profile. Every
// record starts as the user's own draft; "Save and confirm" is the user's explicit confirmation. Suggested updates
// change nothing until the user accepts them one by one.
import { api } from "../api.js";
import { h, mount, uid } from "../dom.js";
import { ACTIVITY_TYPE, EMPLOYMENT_TYPE, QUALIFICATION_LEVEL, partialDate } from "../copy.js";
import { badge, busy, button, notice, toast } from "../ui.js";
import { WHY_CATEGORISE, areaPicker } from "../areas.js";

let areasCache = null;
const loadAreas = async () => (areasCache = areasCache || await api.areas());

/** Optional categorisation of lines before saving (Phase 3.1). Choices are kept by the exact line text, so a line edited
 *  afterwards is simply left uncategorised (never mislabelled). Nothing is pre-selected. */
function lineCategoriser(getLines) {
  const slot = h("div.line-categoriser");
  const pickers = new Map();                      // line text -> picker
  const open = button("Categorise these lines (optional)", { variant: "link", small: true });
  open.addEventListener("click", () => busy(open, "Loading…", async () => {
    const areas = await loadAreas();
    const list = getLines();
    if (!list.length) {
      slot.replaceChildren(h("p.hint", "Add some lines first."));
      return;
    }
    slot.replaceChildren(h("p.hint", WHY_CATEGORISE), ...list.map((line) => {
      const p = pickers.get(line) || areaPicker(areas, null, { legend: line });
      pickers.set(line, p);
      return h("details.line-category", h("summary", line), p.el);
    }));
  }));
  return { el: h("div", open, slot), valuesFor: (list) => list.map((line) => (pickers.has(line) ? pickers.get(line).value() : null)) };
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const KIND_LABEL = { employment: "Job", evidence: "Experience", education: "Qualification", activity: "Certification or project", skill: "Skill" };

/** "Add to your profile": one form at a time. ``onSaved(message)`` reloads the caller's view. */
export function addPanel({ jobs = [], onSaved, intro = null }) {
  const slot = h("div.entry-slot");
  const open = (form, trigger) => {
    for (const b of choices.querySelectorAll("button")) b.setAttribute("aria-expanded", String(b === trigger));
    mount(slot, form);
    const first = slot.querySelector("input, textarea, select");
    if (first) first.focus();
  };
  const close = () => {
    mount(slot);
    for (const b of choices.querySelectorAll("button")) b.setAttribute("aria-expanded", "false");
  };
  const done = async (message) => { close(); await onSaved(message); };
  const choice = (label, make) => {
    const b = button(label, { variant: "secondary", small: true, attrs: { "aria-expanded": "false" } });
    b.addEventListener("click", () => (b.getAttribute("aria-expanded") === "true" ? close() : open(make(done, close), b)));
    return b;
  };
  const choices = h("div.entry-choices",
    choice("Add a job", (d, c) => jobForm(jobs, d, c)),
    choice("Add a qualification", (d, c) => educationForm(d, c)),
    choice("Add a certification or project", (d, c) => activityForm(d, c)),
    choice("Add a skill", (d, c) => skillForm(d, c)),
    choice("Paste your experience", (d, c) => pasteForm(d, c)));
  return h("div.entry-panel", intro ? h("p.section-lead", intro) : null, choices, slot);
}

// --------------------------------------------------------------------------- forms
function input(label, attrs = {}, hint = null) {
  const el = h(attrs.multiline ? "textarea" : "input", { id: uid("in"), ...(attrs.multiline ? { rows: "4" } : { type: "text" }),
    maxlength: attrs.maxlength || "200", ...(attrs.required ? { required: true, "aria-required": "true" } : {}) });
  const hintId = hint ? uid("hint") : null;
  if (hintId) el.setAttribute("aria-describedby", hintId);
  return { el, row: h("div.field", h("label", { for: el.id }, label + (attrs.required ? " (required)" : "")), el, hint ? h("p.hint", { id: hintId }, hint) : null) };
}

function select(label, options, value = "") {
  const el = h("select", { id: uid("sel") }, options.map(([v, l]) => h("option", { value: v, selected: v === value }, l)));
  return { el, row: h("div.field", h("label", { for: el.id }, label), el) };
}

/** Month and year as stated. With `ongoing` (for example "I currently work here"), the checkbox appears only while the
 *  date is empty, and ticking it hides the date: a missing date is never read as ongoing (Phase 5, D50). With `required`,
 *  value() refuses anything but a month and year (or the ticked checkbox). Returns the API's partial date. */
function dateInput(label, { ongoing: ongoingLabel = null, required = false, value: initial = null } = {}) {
  const month = h("select", { id: uid("m"), "aria-label": `${label}: month` }, h("option", { value: "" }, "Month"),
    MONTHS.map((m, i) => h("option", { value: String(i + 1) }, m)));
  const year = h("input", { id: uid("y"), type: "text", inputmode: "numeric", maxlength: "4", placeholder: "Year", "aria-label": `${label}: year` });
  if (initial && initial.year) year.value = String(initial.year);
  if (initial && initial.month) month.value = String(initial.month);
  const ongoing = ongoingLabel ? h("input", { id: uid("on"), type: "checkbox" }) : null;
  if (ongoing && initial && initial.precision === "ongoing") ongoing.checked = true;
  const inputs = h("div.date-inputs", month, year);
  const check = ongoing ? h("span.check", ongoing, h("label", { for: ongoing.id }, ongoingLabel)) : null;
  const sync = () => {
    if (!ongoing) return;
    inputs.hidden = ongoing.checked;
    check.hidden = !ongoing.checked && Boolean(year.value.trim() || month.value);
  };
  if (ongoing) [month, year, ongoing].forEach((el) => el.addEventListener(el === year ? "input" : "change", sync));
  sync();
  const row = h("fieldset.field.date-pair", h("legend", label + (required ? " (required)" : "")), inputs, check);
  const value = () => {
    if (ongoing && ongoing.checked) return { precision: "ongoing" };
    const y = year.value.trim();
    if (!y) {
      if (required) throw new Error(ongoing ? `${label}: enter the month and year, or tick “${ongoingLabel}”.` : `${label}: enter the month and year.`);
      return { precision: "unknown" };
    }
    if (!/^\d{4}$/.test(y)) throw new Error(`${label}: enter a four-digit year.`);
    if (required && !month.value) throw new Error(`${label}: choose the month as well as the year.`);
    return month.value ? { year: Number(y), month: Number(month.value), precision: "month" } : { year: Number(y), precision: "year" };
  };
  return { row, value };
}

const lines = (text) => text.split(/\n+/).map((l) => l.replace(/^\s*[-•*]\s*/, "").trim()).filter(Boolean);

function formShell(title, fields, submit, close, { confirmable = true, hint = null } = {}) {
  const status = h("div", { "aria-live": "polite" });
  const save = button("Save as draft", { variant: confirmable ? "secondary" : "primary", small: true });
  const saveConfirm = confirmable ? button("Save and confirm", { small: true }) : null;
  const run = (b, confirm) => b.addEventListener("click", () => busy(b, "Saving…", async () => {
    mount(status);
    try {
      await submit(confirm);
    } catch (err) {
      mount(status, notice(err.message, { tone: "error" }));
    }
  }));
  run(save, false);
  if (saveConfirm) run(saveConfirm, true);
  return h("form.inline-panel.entry-form", { "aria-label": title, onsubmit: (e) => e.preventDefault() },
    h("h3.mini-title", title), fields, hint ? h("p.hint", hint) : null, status,
    h("div.actions", saveConfirm, save, button("Cancel", { variant: "ghost", small: true, onClick: close })));
}

function jobForm(jobs, done, close) {
  const title = input("Job title", { required: true });
  const employer = input("Employer");
  const type = select("Employment type", Object.entries(EMPLOYMENT_TYPE).filter(([v, l]) => l && v !== "unknown").map(([v, l]) => [v, l]), "full_time");
  const start = dateInput("Start", { required: true });
  const end = dateInput("End", { ongoing: "I currently work here", required: true });
  const resp = input("Responsibilities", { multiline: true, maxlength: "6000" }, "One per line. Describe what you did; leave out names and contact details.");
  const ach = input("Achievements", { multiline: true, maxlength: "6000" }, "One per line, only what you can stand behind.");
  const skills = input("Skills", { maxlength: "1000" }, "Separate with commas.");
  const categoriser = lineCategoriser(() => [...lines(resp.el.value), ...lines(ach.el.value)]);
  const order = jobs.length ? select("Where it goes in your history", [["", "Place it by its start date"],
    ...jobs.map((j) => [j.id, `Before: ${j.title_raw}`])]) : null;
  return formShell("Add a job", [title.row, employer.row, type.row, h("div.date-row", start.row, end.row), resp.row, ach.row,
    categoriser.el, skills.row, order ? order.row : null], async (confirm) => {
    if (!title.el.value.trim()) throw new Error("Enter the job title.");
    const body = {
      title_raw: title.el.value, employer_name: employer.el.value.trim() || null, employment_type: type.el.value,
      start: start.value(), end: end.value(), responsibilities: lines(resp.el.value), achievements: lines(ach.el.value),
      skills: skills.el.value.split(",").map((s) => s.trim()).filter(Boolean), confirm,
    };
    body.responsibility_classifications = categoriser.valuesFor(body.responsibilities);
    body.achievement_classifications = categoriser.valuesFor(body.achievements);
    if (order && order.el.value) body.before_employment_id = order.el.value;
    const out = await api.addEntry("employment", body);
    await done(out.note);
  }, close, { hint: "Roles are listed in the order you state. Without a date or a chosen place, a new job becomes your most recent." });
}

function educationForm(done, close) {
  const title = input("Qualification", { required: true }, "As it appears on your certificate, for example B.Tech or MBA.");
  const level = select("Level", [["unknown", "Choose…"], ...Object.entries(QUALIFICATION_LEVEL).filter(([v]) => v !== "phd")]);
  const inst = input("University, college or school", { required: true });
  const fieldOf = input("Field of study", {}, "Leave empty if it doesn't apply.");
  const start = dateInput("Started", { required: true });
  const end = dateInput("Completed", { ongoing: "Currently studying", required: true });
  return formShell("Add a qualification", [title.row, level.row, inst.row, fieldOf.row, h("div.date-row", start.row, end.row)], async (confirm) => {
    if (!title.el.value.trim()) throw new Error("Enter the qualification.");
    if (!inst.el.value.trim()) throw new Error("Enter the university, college or school.");
    const s0 = start.value();
    const e0 = end.value();
    const out = await api.addEntry("education", { qualification_title_raw: title.el.value, qualification_level: level.el.value,
      institution_name: inst.el.value.trim(), field_of_study: fieldOf.el.value.trim() || null, start: s0, end: e0,
      completion_status: e0.precision === "ongoing" ? "in_progress" : "completed", confirm });
    await done(out.note);
  }, close);
}

function activityForm(done, close) {
  const type = select("Type", [["certification", ACTIVITY_TYPE.certification], ["course", "Course"],
    ["independent_project", ACTIVITY_TYPE.independent_project], ["award", ACTIVITY_TYPE.award]]);
  const title = input("Title", { required: true });
  const org = input("Organisation");
  const desc = input("Description", { multiline: true, maxlength: "1000" });
  const end = dateInput("Completed");
  return formShell("Add a certification or project", [type.row, title.row, org.row, desc.row, end.row], async (confirm) => {
    if (!title.el.value.trim()) throw new Error("Enter the title.");
    const out = await api.addEntry("activity", { activity_type: type.el.value, title_raw: title.el.value,
      organisation_raw: org.el.value.trim() || null, description_raw: desc.el.value.trim() || null, end: end.value(), confirm });
    await done(out.note);
  }, close);
}

function skillForm(done, close) {
  const skill = input("Skill", { required: true, maxlength: "80" });
  return formShell("Add a skill", [skill.row], async (confirm) => {
    if (!skill.el.value.trim()) throw new Error("Enter the skill.");
    const out = await api.addEntry("skill", { skill_text_raw: skill.el.value.trim(), confirm });
    await done(out.note);
  }, close);
}

function pasteForm(done, close) {
  const text = input("Your experience", { multiline: true, maxlength: "20000" },
    "Paste job entries (title, employer and dates on one line, then your responsibilities), or sections from your resume. "
    + "Trace reads them on its own server and suggests additions. Nothing is added until you accept each one; the pasted text "
    + "itself isn't kept.");
  text.el.rows = 10;
  const status = h("div", { "aria-live": "polite" });
  const find = button("Find details", { small: true });
  find.addEventListener("click", () => busy(find, "Reading…", async () => {
    mount(status);
    if (!text.el.value.trim()) {
      mount(status, notice("Paste some text first.", { tone: "error" }));
      return;
    }
    try {
      const out = await api.proposeFromText(text.el.value);
      await done(out.note);
    } catch (err) {
      mount(status, notice(err.message, { tone: "error" }));
    }
  }));
  return h("form.inline-panel.entry-form", { "aria-label": "Paste your experience", onsubmit: (e) => e.preventDefault() },
    h("h3.mini-title", "Paste your experience"), text.row, status,
    h("div.actions", find, button("Cancel", { variant: "ghost", small: true, onClick: close })));
}

// --------------------------------------------------------------------------- suggested updates
const dateText = (d) => (!d || d.precision === "unknown" ? "not stated" : d.precision === "ongoing" ? "present" : partialDate(d));
const valueText = (v) => (v && typeof v === "object" ? dateText(v) : v || "not stated");

/** Open proposals: each is accepted (as a draft, or confirmed) or dismissed on its own. */
export function proposalsPanel(proposals, { onChanged }) {
  if (!proposals.length) return null;
  return h("section.review-section.proposals", { "aria-labelledby": "proposals-title" },
    h("h2.section-title", { id: "proposals-title" }, "Suggested updates"),
    h("p.section-lead", "Found in text you pasted or a resume you uploaded. Your profile hasn't changed: accept the ones that are "
      + "right and dismiss the rest."),
    h("ul.proposal-list", proposals.map((p) => proposalItem(p, onChanged))));
}

function proposalItem(p, onChanged) {
  const update = p.change === "possible_update";
  const lineList = update ? [] : p.kind === "employment" ? [...(p.proposed.responsibilities || []), ...(p.proposed.achievements || [])]
    : p.kind === "evidence" ? [p.proposed.evidence_text] : [];
  const categoriser = lineList.length ? lineCategoriser(() => lineList) : null;
  const classes = () => (categoriser ? categoriser.valuesFor(lineList) : []);
  const act = (label, variant, call, message) => {
    const b = button(label, { variant, small: true });
    b.addEventListener("click", () => busy(b, "…", async () => {
      try {
        await call();
        await onChanged(message);
      } catch (err) {
        toast(err.message, "error");
        await onChanged();
      }
    }));
    return b;
  };
  const detail = update
    ? h("dl.record-facts", Object.keys(p.proposed).map((k) => [h("dt", k.replace(/_/g, " ")),
      h("dd", `${valueText(p.current && p.current[k])} → ${valueText(p.proposed[k])}`)]))
    : p.kind === "employment" && (p.proposed.responsibilities || []).length + (p.proposed.achievements || []).length
      ? h("ul.quote-list", [...(p.proposed.responsibilities || []), ...(p.proposed.achievements || [])].map((t) => h("li", t)))
      : null;
  return h("li.proposal",
    h("div.evidence-head", h("span.evidence-type", update ? `Change to ${KIND_LABEL[p.kind].toLowerCase()}` : `New ${KIND_LABEL[p.kind].toLowerCase()}`),
      badge(p.source === "resume_update" ? "From your resume" : "From pasted text", "neutral")),
    h("p.evidence-text", p.summary),
    p.applies_to ? h("p.muted.small", `${update ? "Changes" : "Adds to"}: ${p.applies_to}`) : null,
    detail,
    categoriser ? categoriser.el : null,
    h("div.record-actions",
      update ? act("Apply change", "primary", () => api.acceptProposal(p.id, false), "Change applied. The previous value is kept in the record's history.")
        : [act("Add and confirm", "primary", () => api.acceptProposal(p.id, true, classes()), "Added and confirmed"),
          act("Add as draft", "secondary", () => api.acceptProposal(p.id, false, classes()), "Added as a draft to review")],
      act("Dismiss", "ghost", () => api.dismissProposal(p.id), "Dismissed")));
}
