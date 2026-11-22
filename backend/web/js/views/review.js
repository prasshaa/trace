// Profile review: what Trace understood, organised for scanning, with the supported actions only: confirm, edit (a
// correction, kept in the record's history), reject or restore; match each job to a standard role; and, optionally,
// categorise experience into areas of responsibility (improves the comparison; never required to continue). There is no
// bulk confirmation: a detail counts as confirmed only when the user confirms or corrects it, and unconfirmed details are
// simply not used (the API's review rules). Ownership, consent and eligibility are never editable here.
import { manageCard } from "./manage.js";
import { api } from "../api.js";
import { h, mount, uid } from "../dom.js";
import {
  ACTIVITY_TYPE, EMPLOYMENT_TYPE, EVIDENCE_TYPE, QUALIFICATION_LEVEL, dateRange, gradeFromNotes, partialDate, plural, positionText,
} from "../copy.js";
import { badge, busy, button, disclosure, errorState, journey, loading, notice, pageHeader, toast } from "../ui.js";
import { addPanel, proposalsPanel } from "./entry.js";
import { WHY_CATEGORISE, areaPicker } from "../areas.js";

// Synthetic samples name employers with placeholder codes (fictional, unnamed employers); never show them as names.
const PLACEHOLDER_EMPLOYER = /^[A-Z]{3}-\d{3,}-ORG-[A-Z0-9]+$/;
const employerName = (name) => (!name ? "Employer not stated" : PLACEHOLDER_EMPLOYER.test(name) ? "Fictional employer" : name);
const STATUS_TONE = { pending_review: "caution", user_confirmed: "positive", user_corrected: "positive", rejected: "muted" };
const autoMapped = new Set();      // employments already sent for automatic matching in this browser session

export async function render(root, ctx) {
  const content = h("div.review-content");
  const reach = { profile: "#/profile", target: "#/target", pathway: ctx.app.target ? `#/pathway/${encodeURIComponent(ctx.app.target)}` : null };
  mount(root, h("section.page",
    journey("profile", reach),
    pageHeader("Step 1 · Your profile", "Review your profile",
      "Review your experience and correct anything that's wrong. Trace uses the details you confirm to build your career pathway."),
    content));
  root.querySelector(".page-title").focus({ preventScroll: true });
  mount(content, loading("Loading your profile"));
  let areas;
  try {
    areas = await api.areas();
  } catch (err) {
    mount(content, errorState(err, () => render(root, ctx)));
    return;
  }
  await new ReviewPage(content, areas, ctx).load({ autoMap: true });
  if (ctx.state.hasRecords) root.querySelector(".page").append(manageCard(ctx));   // D52: replace or clear
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

class ReviewPage {
  constructor(el, areas, ctx) {
    this.el = el;
    this.ctx = ctx;
    this.areas = areas;
    this.areaLabel = Object.fromEntries(areas.map((a) => [a.code, a.label]));
    this.taxonomyVersion = areas.reduce((m, a) => Math.max(m, a.current_taxonomy_version || 1), 1);
    this.records = null;
    this.proposals = [];
    this.jobOpen = new Map();         // employment id -> the user's own open/closed choice, kept across redraws
    this.position = null;
    this.banner = null;
  }

  async load({ autoMap = false } = {}) {
    try {
      this.records = await api.records();
      if (autoMap) await this.autoMap();
      this.position = await api.currentPosition().catch(() => null);
      this.proposals = await api.proposals().catch(() => []);
    } catch (err) {
      mount(this.el, errorState(err, () => this.load({ autoMap })));
      return;
    }
    this.draw();
  }

  /** Match each unmatched job title to a standard role once (the deterministic alias matcher on the server). */
  async autoMap() {
    const todo = this.records.employments.filter((e) => e.mapping.state === "unmapped" && e.review.status !== "rejected"
      && !autoMapped.has(e.id));
    if (!todo.length) return;
    for (const e of todo) {
      autoMapped.add(e.id);
      try {
        await api.mapRole(e.id);
      } catch {
        /* left unmatched: the card offers a manual choice */
      }
    }
    this.banner = "Trace matched your job titles to standard roles where it could. Check each match below.";
    this.records = await api.records();
  }

  async refresh(message) {
    const y = window.scrollY;
    try {
      this.records = await api.records();
      this.position = await api.currentPosition().catch(() => this.position);
      this.proposals = await api.proposals().catch(() => this.proposals);
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    this.draw();
    window.scrollTo(0, y);
    if (message) toast(message);
  }

  /** A stale version (changed elsewhere, e.g. another tab or device) reloads the current values; nothing is overwritten. */
  async failed(err) {
    if (err.code === "record_changed") {
      await this.refresh();
      toast("This detail changed somewhere else, so it was reloaded. Check it and try again.", "error");
    } else toast(err.message, "error");
  }

  allReviewable() {
    const r = this.records;
    const evidence = [...r.employments.flatMap((e) => e.evidence), ...r.activity_evidence, ...r.unattached_evidence];
    return [
      ...r.employments.map((e) => ({ kind: "employment", id: e.id, review: e.review })),
      ...evidence.map((x) => ({ kind: "evidence", id: x.id, review: x.review })),
      ...r.other_records.map((o) => ({ kind: o.kind, id: o.id, review: o.review })),
    ];
  }

  draw() {
    const r = this.records;
    const all = this.allReviewable();
    const pending = all.filter((x) => x.review.can_confirm);
    const done = all.length - pending.length;
    const untagged = [...r.employments.flatMap((e) => e.evidence), ...r.unattached_evidence]
      .filter((x) => x.review.status !== "rejected" && ["none", "stale"].includes(x.classification.state)).length;
    // Only the job that sets the starting point MUST have a role (the current-position rule); matching the others is
    // optional and only enriches the career history (D50).
    const roleBlocking = Boolean(this.position && this.position.status !== "known"
      && ["unresolved_role_mapping", "stale_mapping", "low_mapping_confidence", "role_unconfirmed"].includes(this.position.reason));
    const optionalUnmatched = r.employments.filter((e) => e.review.status !== "rejected" && (e.mapping.state === "unmapped" || e.mapping.is_stale)
      && e.id !== this.startingJobId()).length;
    const groups = { education: [], activity: [], skill: [], career_break: [] };
    for (const o of r.other_records) groups[o.kind].push(o);

    const attention = [
      pending.length ? h("li", badge(plural(pending.length, "detail remaining", "details remaining"), "caution")) : null,
      roleBlocking ? h("li", badge("Your current job needs a role", "caution")) : null,
      optionalUnmatched ? h("li", badge(`${plural(optionalUnmatched, "earlier job has", "earlier jobs have")} no role match (optional)`, "neutral")) : null,
    ].filter(Boolean);

    const summary = h("section.review-summary", { "aria-label": "Profile summary" },
      this.positionBlock(),
      h("div.summary-progress",
        h("p.summary-label", "Confirmed"),
        h("p.summary-figure", h("strong", String(done)), h("span", ` of ${plural(all.length, "detail", "details")} confirmed`)),
        h("div.summary-bar", { role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(all.length), "aria-valuenow": String(done),
          "aria-label": "Details confirmed" }, h("span", { style: { width: `${all.length ? (100 * done) / all.length : 100}%` } }))),
      h("div.summary-attention",
        h("p.summary-label", "Still to do"),
        attention.length ? h("ul.summary-flags", attention) : h("p.muted.small", "Nothing. Every detail has been checked.")));

    const jobs = [...r.employments].sort((a, b) => b.sequence_index - a.sequence_index);
    const certs = groups.activity.filter((a) => ["certification", "licence"].includes(a.activity_type));
    const otherActs = groups.activity.filter((a) => !["certification", "licence"].includes(a.activity_type));
    const extraEvidence = [...r.activity_evidence, ...r.unattached_evidence];

    const sections = [
      this.banner ? notice(this.banner, { tone: "info" }) : null,
      summary,
      proposalsPanel(this.proposals, { onChanged: (m) => this.refresh(m) }),
      section("Employment history", `Your jobs, most recent first, with the role each matches and the experience recorded for it. ${WHY_CATEGORISE}`,
        jobs.length ? jobs.map((e) => this.employmentCard(e)) : h("p.muted", "No jobs yet. Add one below.")),
      groups.education.length ? section("Education and qualifications", "Degrees, institutions and grades.",
        h("div.record-grid", groups.education.map((o) => this.educationCard(o)))) : null,
      groups.skill.length ? section("Skills", "Your skills.",
        h("ul.skill-list", groups.skill.map((o) => this.skillItem(o)))) : null,
      certs.length || otherActs.length ? section("Certifications, projects and achievements", null,
        h("div.record-grid", [...certs, ...otherActs].map((o) => this.activityCard(o)))) : null,
      extraEvidence.length ? section("Other experience", "Experience not tied to a specific job.",
        h("ul.evidence-list", extraEvidence.map((x) => this.evidenceItem(x)))) : null,
      groups.career_break.length ? section("Career breaks", null,
        h("div.record-grid", groups.career_break.map((o) => this.simpleCard(o, "Career break")))) : null,
      section("Add to your profile", "Anything missing? Add it here. New details start as drafts; confirm them when they're right.",
        addPanel({ jobs: jobs.filter((e) => e.review.status !== "rejected").slice().reverse(), onSaved: (m) => this.refresh(m) })),
    ];

    // No bulk confirmation: continuing never confirms anything. Unconfirmed details are left out until confirmed.
    // Continue needs an established starting point (the API's current position: the latest job confirmed and matched
    // to a standard role); nothing else has to be reviewed first.
    const started = Boolean(this.position && this.position.status === "known");
    const current = jobs.find((e) => e.review.status !== "rejected");
    const goToJob = current ? button("Go to your current job", { variant: "secondary", small: true,
      onClick: () => focusJob(current.id) }) : null;
    const primary = started
      ? h("a.btn.btn-primary", { href: "#/target" }, "Continue to your target")
      : button("Continue to your target", { disabled: true, attrs: { "aria-describedby": "review-gate" } });
    sections.push(h("div.page-actions.review-actions", { "aria-label": "Continue" },
      h("div.review-actions-text", { id: "review-gate" },
        started
          ? h("p", pending.length ? "Unconfirmed details won't be used in your pathway. You can review them later."
            : "Every detail has been checked.")
          : h("p", h("strong", "Confirm your current role to continue. "),
            "Trace needs to know where you're starting before it can map your career path."),
        started ? null : goToJob),
      primary));
    mount(this.el, sections);
    reserveSpace(this.el.querySelector(".review-actions"));
    // Arrived from "Review the role match" (pathway or target page): go straight to the current job's record.
    if (this.ctx.app.flash === "focus_current_job" && current) {
      this.ctx.app.flash = null;
      focusJob(current.id);
    }
  }

  /** The job the current position is read from: the latest job in the main career line (stated order). */
  startingJobId() {
    const jobs = [...this.records.employments].filter((e) => e.review.status !== "rejected" && e.primary_career_sequence)
      .sort((a, b) => b.sequence_index - a.sequence_index);
    return jobs.length ? jobs[0].id : null;
  }

  positionBlock() {
    const p = this.position;
    if (!p) return h("div.summary-position", h("p.summary-label", "Current role"), h("p.muted.small", "Not available right now."));
    return h("div.summary-position",
      h("p.summary-label", "Current role"),
      p.status === "known"
        ? [h("p.summary-role", p.canonical_title), h("p.muted.small", p.title_raw ? `From your most recent job: ${p.title_raw}` : "From your most recent job")]
        : [h("p.summary-role.is-unknown", "Not set yet"), h("p.muted.small", positionText(p))]);
  }

  // --------------------------------------------------------------------- employment
  employmentCard(e) {
    const rejected = e.review.status === "rejected";
    const card = h(`article.card.job${rejected ? ".is-rejected" : ""}`, { id: `job-${e.id}`, tabindex: "-1" });
    const editSlot = h("div");
    // Phase 7.2.1: a role matched by the system (not by the user) must be confirmed by the user before it can be their
    // starting point. When the job itself is still pending, one action confirms both, next to the role it names.
    const roleToConfirm = needsRoleConfirmation(e) ? e.mapping : null;
    const fields = [{ field: "title_raw", label: "Job title", value: e.title_raw }];
    if (e.review.is_own_draft) fields.push({ field: "employer_name", label: "Employer", value: e.employer_name || "" });
    fields.push({ field: "employment_type", label: "Employment type", type: "select", value: e.employment_type,
      options: Object.entries(EMPLOYMENT_TYPE).filter(([v, l]) => l && v !== "unknown") },
    { field: "start", label: "Start", type: "date", value: e.start },
    { field: "end", label: "End", type: "date", value: e.end, ongoing: "I currently work here" });
    const actions = this.reviewActions("employment", e, editSlot, fields, { confirmRole: roleToConfirm });
    const head = h("div.job-head",
      h("div.job-main",
        h("h3.job-title", e.title_raw),
        h("p.job-meta", [employerName(e.employer_name), dateRange(e.start, e.end), EMPLOYMENT_TYPE[e.employment_type] || null]
          .filter(Boolean).join(" · "))),
      h("div.job-status", badge(e.review.label, STATUS_TONE[e.review.status] || "neutral")));
    // Open by default while something needs attention; after that, the user's own choice is kept across redraws, so saving
    // the last category doesn't collapse the list they are working in.
    const needsAttention = e.evidence.some((x) => ["none", "stale"].includes(x.classification.state) || x.review.can_confirm
      || this.needsRecheck(x));
    const evidence = e.evidence.length && !rejected
      ? disclosure(`Experience in this job (${e.evidence.length}) · ${categorised(e.evidence)} of ${e.evidence.filter((x) => x.review.status !== "rejected").length} categorised`,
        h("ul.evidence-list", e.evidence.map((x) => this.evidenceItem(x))),
        { open: this.jobOpen.has(e.id) ? this.jobOpen.get(e.id) : needsAttention })
      : null;
    if (evidence) evidence.addEventListener("toggle", () => this.jobOpen.set(e.id, evidence.open));
    card.append(head, rejected ? null : this.roleLine(e), actions, editSlot, evidence);
    return card;
  }

  roleLine(e) {
    const m = e.mapping;
    const slot = h("div.role-picker-slot");
    let text;
    let tone = "neutral";
    if (m.is_stale) {
      text = "The title changed after it was matched. Match it again.";
      tone = "caution";
    } else if (m.state === "automatic") text = `Matched to ${m.canonical_title} · not confirmed yet`;
    else if (m.state === "candidate_selected") text = `Confirmed: ${m.canonical_title}`;
    else if (m.state === "unmapped") {
      text = "No role matched this title yet.";
      tone = "caution";
    } else text = m.canonical_title ? `Role: ${m.canonical_title}` : "Role set outside the app";
    const change = button(m.state === "unmapped" ? "Choose a role" : "Change", { variant: "link", small: true, attrs: { "aria-expanded": "false" } });
    change.addEventListener("click", () => this.openRolePicker(e, slot, change));
    const again = m.is_stale ? button("Match again", { variant: "secondary", small: true }) : null;
    // The job is already confirmed but its role isn't: confirm the role on its own (the existing role decision).
    const confirmRole = needsRoleConfirmation(e) && !e.review.can_confirm ? button("Confirm role", { variant: "secondary", small: true }) : null;
    if (confirmRole) {
      confirmRole.addEventListener("click", () => busy(confirmRole, "Confirming…", async () => {
        try {
          await api.chooseRole(e.id, m.role_id);
          await this.refresh("Role confirmed");
        } catch (err) {
          toast(err.message, "error");
        }
      }));
    }
    if (again) {
      again.addEventListener("click", () => busy(again, "Matching…", async () => {
        try {
          await api.mapRole(e.id);
          await this.refresh("Role matched");
        } catch (err) {
          toast(err.message, "error");
        }
      }));
    }
    const suggestion = h("div.role-suggestion");
    if (m.state === "unmapped" && e.review.status !== "rejected") this.showSuggestion(e, suggestion);
    return h("div.role-line",
      h("div.role-text", h("span.role-label", "Matched role"), h(`span.role-value.tone-${tone}`, text)),
      h("div.role-actions", confirmRole, again, change), suggestion, slot);
  }

  /** A role suggested from the words of the title (D50): shown with one action to confirm it as the user's own choice. */
  async showSuggestion(e, slot) {
    let options;
    try {
      options = await api.roleOptions(e.id);
    } catch {
      return;
    }
    if (!options.suggested_role_id) return;
    const role = [...options.alias_options, ...options.other_roles].find((o) => o.role_id === options.suggested_role_id);
    if (!role) return;
    const ok = button(`Confirm ${role.canonical_title}`, { variant: "secondary", small: true });
    ok.addEventListener("click", () => busy(ok, "Saving…", async () => {
      try {
        await api.chooseRole(e.id, role.role_id);
        await this.refresh("Role saved. Your job title is unchanged.");
      } catch (err) {
        toast(err.message, "error");
      }
    }));
    mount(slot, h("p.small", `Suggested because your title includes “${options.suggestion_basis}”: `, h("strong", role.canonical_title), ". "), ok);
  }

  async openRolePicker(e, slot, trigger) {
    if (slot.firstChild) {
      mount(slot);
      trigger.setAttribute("aria-expanded", "false");
      return;
    }
    trigger.setAttribute("aria-expanded", "true");
    mount(slot, loading("Loading roles"));
    let options;
    try {
      options = await api.roleOptions(e.id);
    } catch (err) {
      mount(slot, notice(err.message, { tone: "error" }));
      return;
    }
    const all = [...options.alias_options, ...options.other_roles];
    const byId = Object.fromEntries(all.map((o) => [o.role_id, o]));
    const select = h("select", { id: uid("role") },
      h("option", { value: "" }, "Choose a role…"),
      options.alias_options.length ? h("optgroup", { label: "Suggested for this title" },
        options.alias_options.map((o) => h("option", { value: o.role_id }, o.canonical_title))) : null,
      h("optgroup", { label: "All roles" }, options.other_roles.map((o) => h("option", { value: o.role_id }, o.canonical_title))));
    const clarify = h("div.clarify");
    const save = button("Save role", { small: true, disabled: true });
    const status = h("div", { "aria-live": "polite" });
    select.addEventListener("change", () => {
      const opt = byId[select.value];
      save.disabled = !opt;
      mount(clarify);
      if (opt && opt.requires_ownership_clarification) {
        mount(clarify, h("fieldset.chips",
          h("legend", "Which product responsibilities did you own in this job? Choose at least one."),
          options.pm_ownership_areas.map((code) => chip(code, this.areaLabel[code] || code, false))));
      }
    });
    save.addEventListener("click", () => busy(save, "Saving…", async () => {
      const opt = byId[select.value];
      const owned = [...clarify.querySelectorAll("input:checked")].map((i) => i.value);
      if (opt.requires_ownership_clarification && !owned.length) {
        mount(status, notice("Choose at least one responsibility you owned.", { tone: "error" }));
        return;
      }
      try {
        await api.chooseRole(e.id, opt.role_id, opt.requires_ownership_clarification ? owned : null);
        await this.refresh("Role saved");
      } catch (err) {
        mount(status, notice(err.message, { tone: "error" }));
      }
    }));
    mount(slot, h("div.inline-panel",
      h("label", { for: select.id }, "Role for this job"), select, clarify, status,
      h("div.actions", save, button("Cancel", { variant: "ghost", small: true, onClick: () => { mount(slot); trigger.setAttribute("aria-expanded", "false"); trigger.focus(); } }))));
    select.focus();
  }

  // --------------------------------------------------------------------- evidence
  evidenceItem(x) {
    const rejected = x.review.status === "rejected";
    const c = x.classification;
    const tagSlot = h("div");
    const older = this.needsRecheck(x);
    const tags = c.state === "none"
      ? h("span.muted.small", "Not categorised yet")
      : h("span.tag-list",
        c.state === "none_of_these" ? h("span.tag", "None of these areas") : c.areas.map((a) => h("span.tag", this.areaLabel[a] || a)),
        c.state === "stale" ? badge("Text changed: categorise again", "caution") : null,
        older && c.state !== "stale" ? badge("New areas added since: check again", "neutral") : null);
    const tagBtn = button(c.state === "none" ? "Categorise" : "Change", { variant: "link", small: true, attrs: { "aria-expanded": "false" } });
    tagBtn.addEventListener("click", () => this.openTagger(x, tagSlot, tagBtn));
    const clearBtn = c.state === "none" ? null : button("Clear", { variant: "link", small: true });
    if (clearBtn) {
      clearBtn.addEventListener("click", () => busy(clearBtn, "…", async () => {
        try {
          await api.clearClassification(x.id, x.review.version);
          await this.refresh("Categories cleared");
        } catch (err) {
          await this.failed(err);
        }
      }));
    }
    const editSlot = h("div");
    return h(`li.evidence${rejected ? ".is-rejected" : ""}`,
      h("div.evidence-head",
        h("span.evidence-type", EVIDENCE_TYPE[x.evidence_type] || x.evidence_type),
        badge(x.review.label, STATUS_TONE[x.review.status] || "neutral")),
      h("p.evidence-text", x.evidence_text),
      rejected ? null : h("div.evidence-tags", h("span.role-label", "Areas of responsibility"), tags, tagBtn, clearBtn),
      tagSlot, editSlot,
      this.reviewActions("evidence", x, editSlot, [{ field: "evidence_text", label: "Experience", value: x.evidence_text, multiline: true }]));
  }

  /** Categorised against an older list of areas: new areas may fit, so the user is asked to check again (never re-labelled). */
  needsRecheck(x) {
    const c = x.classification;
    return x.review.status !== "rejected" && ["current", "none_of_these"].includes(c.state) && (c.taxonomy_version || 1) < this.taxonomyVersion;
  }

  openTagger(x, slot, trigger) {
    if (slot.firstChild) {
      mount(slot);
      trigger.setAttribute("aria-expanded", "false");
      return;
    }
    trigger.setAttribute("aria-expanded", "true");
    const c = x.classification;
    const initial = c.state === "none" ? null : { areas: c.areas, none: c.state === "none_of_these", qualifiers: c.qualifiers };
    const picker = areaPicker(this.areas, initial);
    const status = h("div", { "aria-live": "polite" });
    const save = button("Save", { small: true });
    save.addEventListener("click", () => busy(save, "Saving…", async () => {
      const choice = picker.value();
      if (!choice) {
        mount(status, notice("Choose at least one area, or \"None of these areas\".", { tone: "error" }));
        return;
      }
      try {
        await api.classify(x.id, choice, x.review.version);
        await this.refresh("Saved");
      } catch (err) {
        if (err.code === "record_changed") await this.failed(err);
        else mount(status, notice(err.message, { tone: "error" }));
      }
    }));
    mount(slot, h("div.inline-panel", picker.el, h("p.hint", "Categorising describes your experience; it doesn't confirm it."), status,
      h("div.actions", save, button("Cancel", { variant: "ghost", small: true, onClick: () => { mount(slot); trigger.setAttribute("aria-expanded", "false"); trigger.focus(); } }))));
    const first = picker.el.querySelector("input");
    if (first) first.focus();
  }

  // --------------------------------------------------------------------- education, skills, activities
  educationCard(o) {
    const grade = gradeFromNotes(o.notes);
    const level = QUALIFICATION_LEVEL[o.qualification_level] || null;
    const editSlot = h("div");
    const studying = o.completion_status === "in_progress" || (o.end && o.end.precision === "ongoing");
    const period = o.start && o.start.year ? `${partialDate(o.start)} – ${studying ? "studying now" : (o.end && o.end.year ? partialDate(o.end) : "?")}`
      : (o.end && o.end.year ? `Completed ${partialDate(o.end)}` : studying ? "Studying now" : null);
    const missing = o.review.status !== "rejected" && (!(o.start && o.start.precision === "month")
      || (!studying && !(o.end && o.end.precision === "month")));
    const meta = [o.institution_name || "Institution not stated", period].filter(Boolean).join(" · ");
    return h(`article.card.record${o.review.status === "rejected" ? ".is-rejected" : ""}`,
      h("div.record-head",
        h("div", h("h3.record-title", o.title || level || "Qualification (title not stated)"), h("p.record-meta", meta)),
        badge(o.review.label, STATUS_TONE[o.review.status] || "neutral")),
      h("dl.record-facts",
        level ? [h("dt", "Level"), h("dd", level)] : null,
        o.field_of_study ? [h("dt", "Field"), h("dd", o.field_of_study)] : null,
        grade ? [h("dt", "Grade"), h("dd", grade)] : null),
      missing ? h("p.small.tone-caution", "Add the start and completion month and year (or tick “Currently studying”). Edit to add them.") : null,
      editSlot,
      this.reviewActions("education", o, editSlot, [
        { field: "qualification_title_raw", label: "Qualification", value: o.title || "" },
        { field: "institution_name", label: "University, college or school", value: o.institution_name || "" },
        { field: "field_of_study", label: "Field of study", value: o.field_of_study || "" },
        { field: "start", label: "Started", type: "date", value: o.start },
        { field: "end", label: "Completed", type: "date", value: o.completion_status === "in_progress" ? { precision: "ongoing" } : o.end,
          ongoing: "Currently studying" },
      ]));
  }

  activityCard(o) {
    const editSlot = h("div");
    const meta = [ACTIVITY_TYPE[o.activity_type] || "Activity", o.organisation, o.end && o.end.year ? partialDate(o.end) : null].filter(Boolean).join(" · ");
    return h(`article.card.record${o.review.status === "rejected" ? ".is-rejected" : ""}`,
      h("div.record-head",
        h("div", h("h3.record-title", o.title || "Activity (title not stated)"), h("p.record-meta", meta)),
        badge(o.review.label, STATUS_TONE[o.review.status] || "neutral")),
      editSlot,
      this.reviewActions("activity", o, editSlot, [
        { field: "title_raw", label: "Title", value: o.title || "" },
        { field: "organisation_raw", label: "Organisation", value: o.organisation || "" },
      ]));
  }

  skillItem(o) {
    const editSlot = h("div");
    return h(`li.skill${o.review.status === "rejected" ? ".is-rejected" : ""}`,
      h("div.skill-head", h("span.skill-name", o.title || "Skill (not stated)"), badge(o.review.label, STATUS_TONE[o.review.status] || "neutral")),
      editSlot,
      this.reviewActions("skill", o, editSlot, [{ field: "skill_text_raw", label: "Skill", value: o.title || "" }], { compact: true }));
  }

  simpleCard(o, fallback) {
    return h(`article.card.record${o.review.status === "rejected" ? ".is-rejected" : ""}`,
      h("div.record-head", h("h3.record-title", o.title ? o.title.replace(/_/g, " ") : fallback),
        badge(o.review.label, STATUS_TONE[o.review.status] || "neutral")),
      this.reviewActions(o.kind, o, h("div"), null));
  }

  // --------------------------------------------------------------------- shared review actions
  reviewActions(kind, rec, editSlot, fields, { compact = false, confirmRole = null } = {}) {
    const r = rec.review;
    const row = h(`div.record-actions${compact ? ".is-compact" : ""}`);
    const act = (label, action, message, variant = "secondary") => {
      const b = button(label, { variant, small: true });
      b.addEventListener("click", () => busy(b, "…", async () => {
        try {
          await api.review(action, kind, rec.id, r.version);
          await this.refresh(message);
        } catch (err) {
          await this.failed(err);
        }
      }));
      return b;
    };
    if (r.can_confirm && confirmRole) {
      // One action, two explicit confirmations: the displayed role (the existing role decision) and the job.
      const b = button("Confirm job and role", { small: true, attrs: { "aria-label": `Confirm job and role: ${confirmRole.canonical_title}` } });
      b.addEventListener("click", () => busy(b, "…", async () => {
        try {
          await api.chooseRole(rec.id, confirmRole.role_id);
          await api.review("confirm", kind, rec.id);          // the role decision just changed the record's version
          await this.refresh("Job and role confirmed");
        } catch (err) {
          toast(err.message, "error");
          await this.refresh();
        }
      }));
      row.append(b);
    } else if (r.can_confirm) row.append(act("Confirm", "confirm", "Confirmed"));
    if (fields && (r.can_correct || r.is_own_draft)) {
      const b = button("Edit", { variant: "ghost", small: true, attrs: { "aria-expanded": "false" } });
      b.addEventListener("click", () => this.openEditor(kind, rec, editSlot, fields, b));
      row.append(b);
    }
    if (r.can_reject) row.append(act("Not accurate", "reject", "Marked as not accurate", "ghost"));
    if (r.can_reinstate) row.append(act("Restore", "reinstate", "Restored", "ghost"));
    return row;
  }

  /** A focused editor for the record's correctable fields. The typed text stays in place if saving fails. */
  openEditor(kind, rec, slot, fields, trigger) {
    if (slot.firstChild) {
      mount(slot);
      trigger.setAttribute("aria-expanded", "false");
      return;
    }
    trigger.setAttribute("aria-expanded", "true");
    const sameDate = (a, b) => JSON.stringify([a && a.year, a && a.month, (a && a.precision) || "unknown"])
      === JSON.stringify([b && b.year, b && b.month, (b && b.precision) || "unknown"]);
    const controls = fields.map((f) => {
      if (f.type === "select") {
        const el = h("select", { id: uid("edit") }, h("option", { value: "" }, "Choose…"), f.options.map(([v, l]) => h("option", { value: v }, l)));
        el.value = f.value && f.value !== "unknown" ? f.value : "";
        return { f, row: h("div.field", h("label", { for: el.id }, f.label), el), first: el,
          read: () => (el.value && el.value !== f.value ? el.value : undefined) };
      }
      if (f.type === "date") {
        // Month and year; an "ongoing" checkbox (current job, still studying) appears only while the date is empty
        // and replaces it when ticked (Phase 5, D50): a missing date is never read as ongoing.
        const v = f.value || {};
        const month = h("select", { id: uid("m"), "aria-label": `${f.label}: month` }, h("option", { value: "" }, "Month"),
          MONTHS.map((m, i) => h("option", { value: String(i + 1) }, m)));
        const year = h("input", { id: uid("y"), type: "text", inputmode: "numeric", maxlength: "4", placeholder: "Year", "aria-label": `${f.label}: year` });
        if (v.year) year.value = String(v.year);
        if (v.month) month.value = String(v.month);
        const on = f.ongoing ? h("input", { type: "checkbox", id: uid("on") }) : null;
        if (on) on.checked = v.precision === "ongoing";
        const inputs = h("div.date-inputs", month, year);
        const check = on ? h("span.check", on, h("label", { for: on.id }, f.ongoing)) : null;
        const sync = () => { if (!on) return; inputs.hidden = on.checked; check.hidden = !on.checked && Boolean(year.value.trim() || month.value); };
        if (on) [month, on].forEach((el) => el.addEventListener("change", sync));
        year.addEventListener("input", sync);
        sync();
        const read = () => {
          let d;
          if (on && on.checked) d = { precision: "ongoing" };
          else if (!year.value.trim()) d = { precision: "unknown" };
          else if (!/^\d{4}$/.test(year.value.trim())) throw new Error(`${f.label}: enter a four-digit year.`);
          else d = month.value ? { year: Number(year.value), month: Number(month.value), precision: "month" } : { year: Number(year.value), precision: "year" };
          if (f.ongoing && d.precision === "unknown") throw new Error(`${f.label}: enter the month and year, or tick “${f.ongoing}”.`);
          return sameDate(d, v) ? undefined : d;
        };
        return { f, row: h("fieldset.field.date-pair", h("legend", f.label), inputs, check), first: month, read };
      }
      const input = f.multiline ? h("textarea", { id: uid("edit"), rows: "3", maxlength: "4000" })
        : h("input", { id: uid("edit"), type: "text", maxlength: "300" });
      input.value = f.value;
      return { f, row: h("div.field", h("label", { for: input.id }, f.label), input), first: input,
        read: () => {
          const value = input.value.trim();
          if (value === f.value.trim()) return undefined;
          if (!value && f === fields[0]) throw new Error(`${f.label} can't be empty.`);
          return value || undefined;
        } };
    });
    const status = h("div", { "aria-live": "polite" });
    const save = button("Save change", { small: true });
    const close = () => { mount(slot); trigger.setAttribute("aria-expanded", "false"); trigger.focus(); };
    save.addEventListener("click", () => busy(save, "Saving…", async () => {
      const changes = {};
      try {
        for (const c of controls) {
          const v = c.read();
          if (v !== undefined) changes[c.f.field] = v;
        }
      } catch (err) {
        mount(status, notice(err.message, { tone: "error" }));
        return;
      }
      if (kind === "education" && changes.end) {
        changes.completion_status = changes.end.precision === "ongoing" ? "in_progress" : "completed";
      }
      if (!Object.keys(changes).length) {
        close();
        return;
      }
      try {
        if (rec.review.is_own_draft) {
          await api.editDraft(kind, rec.id, changes, rec.review.version);
          await this.refresh("Draft updated");
        } else {
          await api.correct(kind, rec.id, changes, rec.review.version);
          await this.refresh("Change saved. The original is kept in the record's history.");
        }
      } catch (err) {
        if (err.code === "record_changed") await this.failed(err);
        else mount(status, notice(err.message, { tone: "error" }));
      }
    }));
    mount(slot, h("div.inline-panel",
      controls.map((c) => c.row),
      kind === "employment" ? h("p.hint", "Changing the title means its role needs checking again. Your original values are kept in the record's history.") : null,
      kind === "evidence" ? h("p.hint", "Keep this to your work. Don't add names, contact details or links.") : null,
      status,
      h("div.actions", save, button("Cancel", { variant: "ghost", small: true, onClick: close }))));
    controls[0].first.focus();
  }
}

function section(title, lead, content) {
  const id = uid("sec");
  return h("section.review-section", { "aria-labelledby": id },
    h("h2.section-title", { id }, title),
    lead ? h("p.section-lead", lead) : null,
    content);
}

/** Lines the user has categorised (areas or "None of these areas"), excluding rejected ones. */
function categorised(evidence) {
  return evidence.filter((x) => x.review.status !== "rejected" && ["current", "none_of_these"].includes(x.classification.state)).length;
}

function chip(value, label, checked) {
  const id = uid("chip");
  return h("span.chip", h("input", { type: "checkbox", id, value, checked }), h("label", { for: id }, label));
}

/** A role set by the system (automatic match, or set outside the app) that the user hasn't confirmed yet. */
function needsRoleConfirmation(e) {
  const m = e.mapping;
  return e.review.status !== "rejected" && !m.is_stale && ["automatic", "set_outside_app"].includes(m.state)
    && Boolean(m.role_id && m.canonical_title) && m.mapping_status !== "unresolved";
}

/** Scroll to a job card and move focus to it (the review page lives on one hash route, so no anchor link). */
function focusJob(id) {
  const card = document.getElementById(`job-${id}`);
  if (!card) return;
  card.scrollIntoView({ behavior: "smooth", block: "start" });
  card.focus({ preventScroll: true });
}

/**
 * The sticky action panel never hides content: the page reserves its height (plus the safe area) at the bottom, and
 * the document scroll padding keeps focused controls above it. Updated whenever the panel's height changes.
 */
let panelObserver = null;
function reserveSpace(panel) {
  if (panelObserver) panelObserver.disconnect();
  const root = document.documentElement;
  const set = () => {
    if (!panel.isConnected) {
      root.style.removeProperty("--review-actions-h");
      if (panelObserver) panelObserver.disconnect();
      return;
    }
    root.style.setProperty("--review-actions-h", `${Math.ceil(panel.getBoundingClientRect().height)}px`);
  };
  if (typeof ResizeObserver === "function") {
    panelObserver = new ResizeObserver(set);
    panelObserver.observe(panel);
  }
  set();
}
