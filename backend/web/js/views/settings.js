// Profile settings -> Data & privacy: how Trace uses your career information.
//
// Two mutually exclusive modes, saved with an explicit action (PUT /api/v1/sharing):
//   shared   use my career information to improve recommendations for everyone (the default after onboarding)
//   private  use it only for my recommendations
// The sharing PREFERENCE and the profile's actual STATUS in the shared pool are shown separately, both from
// GET /api/v1/sharing. "Included" appears only when the API says eligible AND included. Nothing is shown as changed
// until the API confirms it. Reviewer and administrative operations are not available here.
//
// Decision D21: a saved preference or permission is never presented as a contribution. Three separate facts are shown
// (preference, permission, contributing now), and unless the API reports the profile eligible AND included, the page
// says plainly that nothing is being contributed. Where this server has resume upload switched off (config), it also
// explains that nothing can be contributed yet. No setting or consent record is changed by this page's text.
import { api } from "../api.js";
import { h, mount, uid } from "../dom.js";
import { PROFILE_STATUS } from "../copy.js";
import { badge, busy, button, confirmDialog, errorState, loading, notice, pageHeader, toast } from "../ui.js";

const POLL_DELAYS = [3000, 5000, 8000, 13000, 21000];      // a few checks while processing, then stop

const MODES = {
  shared: {
    title: "Use my career information to improve recommendations for everyone",
    text: "Trace can use career details from your reviewed profile, after removing direct personal details and completing "
      + "the required checks, to improve career recommendations and insights for other members.",
    summary: "Share when eligible",                 // a preference, never a statement that anything is shared
  },
  private: {
    title: "Use my career information only for my recommendations",
    text: "Keep your career profile out of the shared career pool. Trace can still use it to understand your experience and "
      + "personalise your own career pathway. Your profile and career path stay available.",
    summary: "Private",
  },
};

export async function render(root, ctx) {
  const body = h("div.settings");
  mount(root, h("section.page.page-narrow",
    pageHeader("Profile settings", "Data & privacy", "Choose how Trace uses your career information. You can change this at any time."),
    body));
  root.querySelector(".page-title").focus({ preventScroll: true });
  await load(body, ctx, 0);
}

async function load(body, ctx, pollIndex, message = null) {
  if (pollIndex === 0) mount(body, loading("Loading your settings"));
  let sharing;
  try {
    sharing = await api.sharing();
  } catch (err) {
    mount(body, errorState(err, () => load(body, ctx, 0)));
    return;
  }
  const view = statusView(sharing, !(ctx.app.config.features && ctx.app.config.features.real_resume_uploads));
  mount(body,
    message,
    modeCard(sharing, body, ctx),
    statusCard(sharing, view, ctx),
    analysisCard(ctx));
  if (view.polling && pollIndex < POLL_DELAYS.length) {
    setTimeout(() => {
      if (body.isConnected) load(body, ctx, pollIndex + 1);
    }, POLL_DELAYS[pollIndex]);
  }
}

// --------------------------------------------------------------------------- the two modes
function modeCard(sharing, body, ctx) {
  const saved = sharing.preference;                                // "shared" | "private" | null
  let selected = saved;
  const status = h("div", { "aria-live": "polite" });
  const save = button("Save");
  const confirmNeeded = () => selected === "shared" && sharing.activation === "confirmation_required";
  const refreshButton = () => {
    save.disabled = !selected || (selected === saved && !confirmNeeded());
    save.textContent = confirmNeeded() && selected === saved ? "Confirm sharing" : "Save";
    confirmNote.hidden = !(selected === "shared" && sharing.confirmation_notice && !sharing.explicit_permission);
  };
  const name = uid("mode");
  const options = ["shared", "private"].map((key) => {
    const id = uid("mode");
    const input = h("input", { type: "radio", name, id, value: key, checked: saved === key });
    input.addEventListener("change", () => {
      selected = key;
      refreshButton();
    });
    return h("label.mode-option", { for: id }, input,
      h("span.mode-body",
        h("span.mode-title", MODES[key].title),
        h("span.mode-text", MODES[key].text),
        saved === key ? h("span.mode-current", badge("Current setting", "accent")) : null));
  });
  const doc = sharing.confirmation_notice;
  const confirmNote = h("div.confirm-note",
    h("p.small", "Saving this choice confirms that Trace may use career details from your reviewed profile as described above."),
    doc && doc.text ? h("details.disclosure.subtle", h("summary", h("span.disclosure-label", "Read the full sharing notice")),
      h("div.disclosure-body.consent-text", h("p.consent-doc-title", doc.title), doc.text.split(/\n{2,}/).map((p) => h("p", p)))) : null);

  save.addEventListener("click", async () => {
    if (selected === "private") {
      const ok = await confirmDialog({
        title: "Use your career information only for your recommendations?",
        body: [
          h("ul.plain-list",
            h("li", "Your profile will no longer be used in future shared career recommendations and insights. Its copy in the "
              + "shared pool is deleted now."),
            h("li", "Your private profile remains available."),
            h("li", "Your own career path remains available where there's enough career evidence."),
            h("li", "Your profile won't be shared again unless you choose to share it here.")),
          h("p.muted.small", "A record of your choices is kept so Trace can show what you chose and when. Shared insights are "
            + "calculated when they're viewed, so your details stop counting straight away. Backups may hold earlier copies "
            + "until they expire."),
        ],
        confirmLabel: "Switch to private",
        busyLabel: "Saving…",
        action: async () => {
          const result = await api.chooseSharing("private");
          if (result.preference !== "private") throw new Error("Your setting wasn't saved. Please try again.");
        },
      });
      if (ok) {
        load(body, ctx, 0, notice("Your career information is now used only for your own recommendations.", { tone: "success", title: "Saved" }));
      } else {
        save.focus();
      }
      return;
    }
    busy(save, "Saving…", async () => {
      try {
        const result = await api.chooseSharing("shared", doc && !sharing.explicit_permission ? doc.consent_version : null);
        if (result.preference !== "shared") throw new Error("Your setting wasn't saved. Please try again.");
        toast("Saved");
        load(body, ctx, 0);
      } catch (err) {
        mount(status, notice(err.code === "contribution_not_offered" ? "Sharing isn't available in this environment." : err.message,
          { tone: "error" }));
      }
    });
  });
  refreshButton();
  return h("section.card.mode-card", { "aria-labelledby": "mode-title" },
    h("h2.card-title", { id: "mode-title" }, "How Trace uses your career information"),
    saved ? null : h("p.muted", "You haven't chosen yet. After you review your profile, sharing is on by default."),
    h("fieldset.mode-options", h("legend.visually-hidden", "How Trace uses your career information"), options),
    confirmNote,
    status,
    h("div.actions", save),
    h("p.fine-print", "Some combinations of career details may still make a profile recognisable, so Trace can't promise "
      + "complete anonymity. Other members only ever see patterns across groups of profiles."));
}

// --------------------------------------------------------------------------- preference and actual status, separately
function statusView(sharing, uploadsOff = false) {
  const s = sharing.status;
  const a = sharing.activation;
  if (a === "private") {
    return { key: "private", text: "Your profile isn't used in shared career insights. Your own profile and career path are unaffected." };
  }
  if (a === "not_chosen") return { key: "not_started", text: "Nothing is shared until a setting is in place." };
  if (a === "not_offered") {
    return { key: "not_started", text: "Your preference is set to share, but sharing isn't switched on in this environment yet, "
      + "so your career profile hasn't been added to shared recommendations." };
  }
  if (a === "confirmation_required") {
    if (uploadsOff) {                 // D21: confirming records a permission only; nothing can be processed or contributed here
      return { key: "not_started", text: "Your preference is set to share. Selecting Confirm sharing above records your "
        + "permission; it doesn't contribute anything while resume upload is switched off." };
    }
    return { key: "not_started", text: "Your preference is set to share, but your career profile hasn't been added to shared "
      + "recommendations yet. Select Confirm sharing above to give permission and start the privacy checks." };
  }
  switch (s.state) {
    case "awaiting_resume":
      return { key: "awaiting_resume", text: "Only a profile built from your own resume can be shared." };
    case "awaiting_review":
      return { key: "awaiting_review", text: "Confirm the details in your profile first. Nothing is shared from unconfirmed details, "
        + "and nothing from this version of your profile is included yet.",
        action: h("a.btn.btn-secondary.btn-sm", { href: "#/profile" }, "Review your profile") };
    case "pending":
    case "processing":
      return { key: "processing", polling: true, text: "Trace is removing personal details and running its checks. Nothing is shared until they pass." };
    case "eligible":
      return s.included
        ? { key: "eligible", text: "Your profile is included in shared career insights. It updates when you change your profile." }
        : { key: "processing", polling: true, text: "Trace is finishing its checks. Nothing is shared until they pass." };
    case "quarantined":
      return { key: "quarantined", title: "Your career profile is being checked",
        text: "Your profile needs an additional privacy check before it can be included in shared career insights. Your profile "
          + "will not be shared while this review is pending, and nothing from the version being checked is included.",
        extra: s.label && s.label.includes("recent edit") ? "Sharing is paused after your recent edit until the check is complete." : null };
    case "failed":
      return { key: "failed", text: "Trace couldn't finish preparing your profile, so nothing from this version is shared." };
    case "withdrawn":
      return { key: "withdrawn", text: "Your profile was removed from shared career insights." };
    case "not_eligible":
      return { key: "not_eligible", text: "This profile can't be shared. Only a profile built from a member's own resume, and "
        + "confirmed by them, can be. Test profiles never are." };
    default:
      return { key: "not_started", text: "Nothing is shared yet." };
  }
}

/** Contributing now: only the API's eligible AND included state counts; a preference or permission never does. */
function contributingNow(sharing) {
  return sharing.activation === "active" && sharing.status.state === "eligible" && sharing.status.included === true;
}

function statusCard(sharing, view, ctx) {
  const label = PROFILE_STATUS[view.key] || PROFILE_STATUS.not_started;
  const contributing = contributingNow(sharing);
  const uploadsOff = !(ctx.app.config.features && ctx.app.config.features.real_resume_uploads);
  const updated = sharing.status.updated_at ? new Date(sharing.status.updated_at) : null;
  const pref = sharing.preference ? MODES[sharing.preference].summary : "Not set yet";
  return h(`section.card.status-card.tone-${label.tone}`, { "aria-labelledby": "status-title" },
    h("h2.card-title.visually-hidden", { id: "status-title" }, "Your sharing status"),
    h("dl.status-pair",
      h("div.status-item",
        h("dt", "Sharing preference"),
        h("dd", h("strong", pref))),
      h("div.status-item",
        h("dt", "Permission to contribute"),
        h("dd", h("strong", sharing.explicit_permission ? "Given" : "Not given"))),
      h("div.status-item",
        h("dt", "Profile status"),
        h("dd", badge(label.title, label.tone === "info" ? "accent" : label.tone))),
      h("div.status-item",
        h("dt", "Contributing now"),
        h("dd", h("strong", contributing ? "Yes" : "No")))),
    contributing ? null : h("p.status-heading", "No career information from your profile is currently being contributed."),
    !contributing && uploadsOff && sharing.preference === "shared"
      ? h("p", "In this environment nothing can be contributed yet: sharing needs a profile built from your own uploaded "
        + "resume, and resume upload is switched off here. Your saved preference and permission don't share anything on "
        + "their own.")
      : null,
    view.title ? h("p.status-heading", view.title) : null,
    h("p", view.text),
    view.extra ? h("p.muted", view.extra) : null,
    view.action || null,
    updated && !Number.isNaN(updated.getTime()) && sharing.activation === "active"
      ? h("p.muted.small", `Updated ${updated.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}`) : null,
    view.polling ? h("p.muted.small", { "aria-live": "polite" }, "This page checks again a few times.") : null);
}

function analysisCard(ctx) {
  const on = Boolean(ctx.state.consent && ctx.state.consent.active);
  return h("section.card.analysis-card", { "aria-labelledby": "analysis-title" },
    h("div.status-head",
      h("h2.card-title", { id: "analysis-title" }, "Resume analysis"),
      badge(on ? "On" : "Off", on ? "positive" : "neutral")),
    h("p.muted", "Trace reads your resume to build your private career profile and your own career path. This is separate "
      + "from sharing, and your profile is never shown to other members."));
}
