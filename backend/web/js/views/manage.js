// Release (D52): the user's control over their confirmed career information, on the profile page.
//
// 1. "Update using a new resume" (#/replace): a new PDF or DOCX becomes a fresh private review draft. The confirmed
//    profile stays exactly as it is until the user confirms the reviewed replacement; confirmation replaces it in one
//    transaction (POST /api/v1/resume-import?replace_profile=true, then the ordinary review and confirm). Trace never
//    kept the earlier file, so nothing claims to "replace a stored resume".
// 2. "Clear my career profile and start over": shows what will be deleted (the server's own counts, GET
//    /api/v1/profile/clear) and what is kept, and asks the user to type a confirmation before POST /api/v1/profile/clear.
import { api } from "../api.js";
import { h, mount } from "../dom.js";
import { plural } from "../copy.js";
import { busy, button, journey, notice, pageHeader, toast } from "../ui.js";
import { fileDisclosure, fileType, pickFile, processing, uploadMessage } from "./setup.js";

const PHRASE = "clear my career profile";

export function manageCard(ctx) {
  const update = h("a.btn.btn-secondary", { href: "#/replace" }, "Update using a new resume");
  const clear = button("Clear my career profile and start over", { variant: "danger-ghost" });
  clear.addEventListener("click", () => clearDialog(ctx));
  return h("section.card.manage-card", { "aria-labelledby": "manage-title" },
    h("h2.card-title", { id: "manage-title" }, "Your career information"),
    h("div.manage-option",
      h("div", h("h3.manage-title", "Update using a new resume"),
        h("p.muted", "Upload a newer resume and review what Trace reads from it. When you confirm, it replaces the career "
          + "details in your profile. Until then, nothing you've confirmed changes. Trace didn't keep your earlier file.")),
      update),
    h("div.manage-option",
      h("div", h("h3.manage-title", "Clear my career profile and start over"),
        h("p.muted", "Deletes your confirmed career details and the plans built from them. Your account stays.")),
      clear));
}

// --------------------------------------------------------------------------- update using a new resume
export function renderReplace(root, ctx) {
  const offered = Boolean(ctx.app.config.features && ctx.app.config.features.real_resume_uploads);
  const body = h("div.start-body");
  mount(root, h("section.page.page-narrow",
    journey("profile", { profile: "#/profile" }),
    pageHeader("Your profile", "Update using a new resume",
      "Trace reads your new resume and shows you everything it found. You'll check and correct it, then confirm to replace "
      + "the career details in your profile."),
    body));
  root.querySelector(".page-title").focus({ preventScroll: true });
  if (!offered) {
    mount(body, notice("Resume upload is switched off on this server.", { tone: "warning" }));
    return;
  }
  const status = h("div.upload-status", { "aria-live": "polite" });
  const chosen = h("p.file-chosen.muted", "No file chosen");
  const send = button("Upload and review", { disabled: true });
  let file = null;
  const input = pickFile("replace-profile-file", (f) => {
    file = fileType(f) ? f : null;
    mount(status, file ? null : notice("Choose a PDF or Word (.docx) file.", { tone: "error" }));
    chosen.textContent = file ? `${file.name} · ${Math.max(1, Math.round(file.size / 1024))} KB` : "No file chosen";
    send.disabled = !file;
  });
  send.addEventListener("click", () => busy(send, "Reading…", async () => {
    if (!file) return;
    mount(status, processing("Reading your resume"));
    try {
      await api.importResume(await file.arrayBuffer(), fileType(file), false, true);
      ctx.go("import");
    } catch (err) {
      mount(status, notice(uploadMessage(err), { tone: "error", title: "Your resume wasn't read" }));
    }
  }));
  mount(body,
    h("section.card",
      h("h2.card-title", "What happens"),
      h("ol.steps-list",
        h("li", "Trace reads the new file on its own server and doesn't keep it."),
        h("li", "You review and correct what it found, as a private draft."),
        h("li", "When you confirm, the draft replaces your current jobs, education, skills, certifications and projects, "
          + "and the plans built from them. Nothing is merged with your old details."),
        h("li", "If you discard the draft, or anything fails, your current profile stays exactly as it is."))),
    h("section.card.upload-card",
      h("h2.card-title", "Your new resume"),
      h("p", "Upload a PDF or Word (.docx) file. A text-based file works best."),
      h("div.file-row", h("label.btn.btn-secondary", { for: "replace-profile-file" }, "Choose a file"), input, chosen),
      status,
      h("div.actions", send, h("a.btn.btn-ghost", { href: "#/profile" }, "Cancel")),
      fileDisclosure(ctx.state.consent && ctx.state.consent.document)));
}

// --------------------------------------------------------------------------- clear and start over
const DELETES = [
  ["jobs", "job", "jobs"], ["experience_lines", "experience line", "experience lines"], ["skills", "skill", "skills"],
  ["education", "education entry", "education entries"], ["activities", "certification or project", "certifications and projects"],
  ["saved_plans", "saved plan version", "saved plan versions"], ["pending_imports", "resume waiting for review", "resumes waiting for review"],
];

async function clearDialog(ctx) {
  const opener = document.activeElement;
  let p;
  try {
    p = await api.clearPreview();
  } catch (err) {
    toast(err.message || "That didn't work.");
    return;
  }
  const titleId = "clear-title";
  const typed = h("input", { type: "text", id: "clear-confirm", autocomplete: "off", spellcheck: "false",
    "aria-describedby": "clear-confirm-hint" });
  const go = button("Clear my career profile", { variant: "danger", disabled: true });
  const cancel = button("Cancel", { variant: "ghost" });
  const status = h("div", { "aria-live": "polite" });
  typed.addEventListener("input", () => { go.disabled = typed.value.trim().toLowerCase() !== PHRASE; });
  const listed = DELETES.filter(([k]) => p.deletes[k] > 0).map(([k, one, many]) => h("li", plural(p.deletes[k], one, many)));
  const dialog = h("dialog.dialog", { "aria-labelledby": titleId },
    h("h2.dialog-title", { id: titleId }, "Clear your career profile?"),
    h("div.dialog-body",
      h("p", h("strong", "This permanently deletes:")),
      h("ul", listed.length ? listed : h("li", "Nothing is saved yet."),
        h("li", "the employers, descriptions and role matches for those jobs, and your correction history"),
        h("li", "the capability assessment and plans worked out from them"),
        h("li", "the records of resumes you've uploaded (Trace never kept the files)")),
      h("p", h("strong", "This keeps:")),
      h("ul",
        h("li", "your account, so you can sign in and start again"),
        h("li", "your permission choices, and your target role, goal and where you'd like to move"),
        h("li", p.sharing_active
          ? "your sharing choice. A copy you've already shared, with personal details removed, is only removed when you stop sharing in Settings."
          : "your sharing choice (you aren't sharing anything)")),
      h("p", "This can't be undone."),
      h("div.field", h("label", { for: "clear-confirm" }, `To confirm, type “${PHRASE}”`), typed,
        h("p.hint", { id: "clear-confirm-hint" }, "The button stays off until the words match."))),
    status,
    h("div.dialog-actions", cancel, go));
  const close = () => { dialog.close(); dialog.remove(); if (opener && opener.focus) opener.focus(); };
  cancel.addEventListener("click", close);
  dialog.addEventListener("cancel", (e) => { if (go.getAttribute("aria-busy")) e.preventDefault(); else { e.preventDefault(); close(); } });
  go.addEventListener("click", async () => {
    cancel.disabled = true;
    try {
      await busy(go, "Clearing…", () => api.clearProfile(typed.value));
      dialog.close(); dialog.remove();
      ctx.app.target = null;
      toast("Your career profile was cleared. You can upload a resume to start again.");
      ctx.go("start");
    } catch (err) {
      mount(status, notice((err && err.message) || "That didn't work. Nothing was deleted.", { tone: "error" }));
      cancel.disabled = false;
    }
  });
  document.body.append(dialog);
  dialog.showModal();
  typed.focus();
}
