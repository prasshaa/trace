// Get started: resume-first onboarding. One primary action, "Upload your resume". Personal-analysis permission comes
// first (explicit, never pre-selected); then a PDF or Word (.docx) resume goes to POST /api/v1/resume-import, which reads
// it on Trace's own server and creates ONE import that the user reviews and confirms in one place (#/import).
//
// Manual entry and pasted text are no longer onboarding entry points (decision D47). Leftovers of an earlier attempt
// (unconfirmed drafts, pasted-text suggestions) can be cleared here with "Start over", after a confirmation.
// When the server doesn't offer resume upload, this page says so plainly: nothing is simulated.
import { ApiError, api } from "../api.js";
import { h, mount } from "../dom.js";
import { plural } from "../copy.js";
import { busy, button, confirmDialog, infoDialog, journey, notice, pageHeader, toast } from "../ui.js";

const TYPES = ".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";
export const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

export async function render(root, ctx) {
  const offered = Boolean(ctx.app.config.features && ctx.app.config.features.real_resume_uploads);
  const body = h("div.start-body");
  const leftovers = h("div");
  mount(root, h("section.page.page-narrow",
    journey("profile", {}),
    pageHeader("Step 1 · Your profile", "Upload your resume",
      "Trace reads your resume to understand the experience you already have. You'll check and correct everything before "
      + "it's saved."),
    leftovers, body));
  root.querySelector(".page-title").focus({ preventScroll: true });
  const imp = ctx.state.resumeImport || { status: "none" };
  if (imp.legacy_suggestions > 0) mount(leftovers, startOver(ctx, imp.legacy_suggestions));
  if (!offered) return unavailable(body);
  if (!ctx.state.consent.active) return consentStep(body, ctx, () => ctx.refresh());
  return upload(body, ctx);
}

// --------------------------------------------------------------------------- an earlier attempt left drafts behind
function startOver(ctx, n) {
  const btn = button("Start over", { variant: "secondary", small: true });
  btn.addEventListener("click", () => confirmDialog({
    title: "Clear the earlier attempt?",
    body: [h("p", `This removes ${plural(n, "unconfirmed item", "unconfirmed items")} left from an earlier attempt, such as `
      + "suggestions from pasted text. Anything you've already confirmed stays in your profile."),
    h("p", "This can't be undone.")],
    confirmLabel: "Clear and start over", busyLabel: "Clearing…",
    action: async () => { await api.discardImport(); },
  }).then((done) => { if (done) { toast("Cleared. You can upload your resume."); ctx.refresh(); } }));
  return notice(h("span", "Some unconfirmed items from an earlier attempt are still saved. ", btn),
    { tone: "info", title: "Earlier attempt" });
}

// --------------------------------------------------------------------------- upload not offered here
function unavailable(body) {
  mount(body, h("section.card.state-card",
    h("div.state-icon.is-neutral", { "aria-hidden": "true" }),
    h("h2.card-title", "Resume upload is switched off on this server"),
    h("p", "Resume upload has to be switched on by whoever runs this Trace server before you can build your profile here."),
    h("p.muted", "Nothing has been uploaded or stored.")));
}

// --------------------------------------------------------------------------- personal-analysis permission
function consentStep(body, ctx, done) {
  const doc = ctx.state.consent.document;
  if (!doc) {
    mount(body, notice("The resume-analysis notice isn't installed on this server yet, so resumes can't be analysed.",
      { tone: "warning", title: "Not ready" }));
    return;
  }
  const agree = h("input", { type: "checkbox", id: "consent-agree" });                 // never pre-selected
  const grant = button("Agree and continue", { disabled: true });
  agree.addEventListener("change", () => { grant.disabled = !agree.checked || !doc.text_verified; });
  const status = h("div", { "aria-live": "polite" });
  grant.addEventListener("click", () => busy(grant, "Saving…", async () => {
    try {
      const result = await api.decideConsent("granted", doc.consent_version);
      if (result.active) {
        toast("Saved");
        done();
      }
    } catch (err) {
      mount(status, notice(err.message, { tone: "error" }));
    }
  }));
  mount(body, h("section.card.consent-card",
    h("h2.card-title", "Let Trace read your resume"),
    h("p", "Trace reads your resume to build your private career profile. Nothing is added to your profile or used for "
      + "your plan until you've checked it and confirmed it."),
    h("ul.assurances",
      h("li", "Your file is read on Trace's own server and isn't kept. It isn't sent to any other company or AI service."),
      h("li", "Until you confirm, the career details Trace found are kept as a private draft that only you can see. You "
        + "can discard it at any time."),
      h("li", "Using your career patterns, with personal details removed, to help other people is a separate choice. Agreeing here doesn't include it.")),
    doc.text_verified && doc.text
      ? noticeButton(doc)
      : notice("The notice text couldn't be verified, so you can't agree to it until it is.", { tone: "warning" }),
    h("label.check", agree, h("span", "I agree to Trace reading my resume to build my career profile.")),
    status,
    h("div.actions", grant),
    h("p.fine-print", "You can withdraw this permission at any time.")));
}

// --------------------------------------------------------------------------- your resume
export function processing(text) {
  return h("div.progress", h("div.progress-bar", { role: "progressbar", "aria-label": text }), h("span", `${text}…`));
}

/** The full notice in a dialog: the page underneath (and any chosen file) is left exactly as it was. */
export function noticeButton(doc) {
  const open = button("Read the full privacy notice", { variant: "ghost", small: true });
  open.addEventListener("click", () => infoDialog({
    title: doc.title,
    body: [/^trace-local-|-demo-/.test(doc.consent_version || "") ? h("p.muted", "Local product testing. This notice hasn't been reviewed by a lawyer.") : null,
      ...doc.text.split(/\n{2,}/).map((p) => h("p", p))],
  }));
  return h("p.notice-link", open);
}

/** What is processed and what is kept. Each sentence matches the tested lifecycle (tests/test_resume_import_lifecycle.py,
 *  tests/test_resume_import_api.py): the file isn't kept; a private, de-identified draft is saved until confirmed. */
export function fileDisclosure(doc = null) {
  return h("details.disclosure.subtle.file-disclosure",
    h("summary", h("span.disclosure-label", "What happens to your file and your details")),
    h("div.disclosure-body",
      h("p", h("strong", "Your file. "), "Trace reads it on its own server, in memory, in a short-lived process. The file "
        + "and its name aren't saved anywhere, and it isn't sent to any other company or AI service. Trace keeps its size, "
        + "type and a fingerprint, which shows whether the same file is uploaded again but can't be turned back into the file. "
        + "It also keeps a short processing record when reading fails or you confirm: the date and the outcome, with no content."),
      h("p", h("strong", "Your draft. "), "Before you confirm, Trace saves the career details it found as a private draft: "
        + "your jobs, dates, descriptions, education, skills, certifications and projects. Your name, email address, phone "
        + "number and links are removed from them first. Only you can see the draft, and it stays when you reload or sign "
        + "out. It isn't part of your profile and isn't used for your plan."),
      h("p", h("strong", "Your choices. "), "Confirm adds what you kept to your profile and deletes the draft. If you chose "
        + "to update using a new resume, confirming replaces your career details instead. Discard deletes the draft. Replace "
        + "swaps it for a draft of the new file. Discarding or replacing a draft never changes anything you confirmed. "
        + "There's no automatic deletion date. You can clear your career profile at any time from your profile page."),
      h("p", h("strong", "Your contact details. "), "These aren't kept, unless you've separately chosen to share "
        + "your career patterns with personal details removed. In that case, an encrypted copy of the name, email addresses, phone numbers and "
        + "links in your resume is kept to check they never appear in what you share. It's deleted when you stop sharing."),
      doc && doc.text_verified && doc.text ? noticeButton(doc) : null));
}

export function pickFile(id, onPicked) {
  const input = h("input.visually-hidden", { type: "file", accept: TYPES, id });
  input.addEventListener("change", () => {
    const f = input.files && input.files[0];
    if (f) onPicked(f);
    input.value = "";
  });
  return input;
}

export function fileType(file) {
  const name = (file.name || "").toLowerCase();
  if (file.type === "application/pdf" || name.endsWith(".pdf")) return "application/pdf";
  if (file.type === DOCX || name.endsWith(".docx")) return DOCX;
  return null;
}

function upload(body, ctx) {
  const status = h("div.upload-status", { "aria-live": "polite" });
  const chosen = h("p.file-chosen.muted", "No file chosen");
  const send = button("Upload and read my resume", { disabled: true });
  let file = null;
  const input = pickFile("own-file", (f) => {
    file = f;
    mount(status);
    if (!fileType(f)) {
      mount(status, notice("Choose a PDF or Word (.docx) file.", { tone: "error" }));
      file = null;
    }
    chosen.textContent = file ? `${file.name} · ${Math.max(1, Math.round(file.size / 1024))} KB` : "No file chosen";
    send.disabled = !file;
  });
  send.addEventListener("click", () => busy(send, "Reading…", async () => {
    if (!file) return;
    mount(status, processing("Reading your resume"));
    try {
      await api.importResume(await file.arrayBuffer(), fileType(file));
      ctx.go("import");
    } catch (err) {
      mount(status, notice(uploadMessage(err), { tone: "error", title: "Your resume wasn't read" }));
    }
  }));
  mount(body, h("section.card.upload-card",
    h("h2.card-title", "Your resume"),
    h("p", "Upload a PDF or Word (.docx) file. A text-based file works best; scanned images can't be read yet."),
    h("div.file-row", h("label.btn.btn-secondary", { for: "own-file" }, "Choose a file"), input, chosen),
    status,
    h("div.actions", send),
    fileDisclosure(ctx.state.consent && ctx.state.consent.document)));
}

export function uploadMessage(err) {
  if (!(err instanceof ApiError)) return "That didn't work. Check your connection and try again.";
  switch (err.code) {
    case "real_uploads_disabled": return "Resume upload is switched off on this server.";
    case "upload_too_large": case "too_large": return "That file is too large. Export a smaller PDF (without large images) or a .docx.";
    case "consent_required": return "Permission to read your resume is needed first.";
    case "rate_limited": return "You've uploaded several times recently. Wait a little and try again.";
    case "fixture_profile_conflict": return "This account holds a test profile, so it can't take a resume.";
    case "import_pending": return "You already have a resume waiting for review. Open it to confirm, replace or discard it.";
    default: return err.message || "That didn't work. Try again.";
  }
}
