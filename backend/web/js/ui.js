// Shared presentation pieces: buttons, badges, notices, loading and error states, the journey indicator, disclosures,
// a confirmation dialog and a segmented control. All text is set as text (dom.js); nothing is parsed as HTML.
import { h, mount, uid } from "./dom.js";

export function button(label, { variant = "primary", onClick, type = "button", disabled = false, small = false, attrs = {} } = {}) {
  return h(`button.btn.btn-${variant}${small ? ".btn-sm" : ""}`, { type, disabled, onclick: onClick, ...attrs }, label);
}

/** Runs an async action with the button disabled and a busy label; returns the action's result (or throws). */
export async function busy(btn, busyLabel, action) {
  const original = [...btn.childNodes];
  btn.disabled = true;
  btn.setAttribute("aria-busy", "true");
  btn.textContent = busyLabel;
  try {
    return await action();
  } finally {
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
    mount(btn, original);
  }
}

export function badge(text, tone = "neutral") {
  return h(`span.badge.badge-${tone}`, text);
}

export function notice(text, { tone = "info", title = null, role = null, action = null } = {}) {
  return h(`div.notice.notice-${tone}`, { role: role || (tone === "error" ? "alert" : null) },
    h("div.notice-body", title ? h("strong.notice-title", title) : null, h("span", text)),
    action);
}

export function loading(label = "Loading") {
  return h("div.loading", { role: "status", "aria-live": "polite" }, h("span.spinner", { "aria-hidden": "true" }), h("span", `${label}…`));
}

export function errorState(error, retry) {
  return h("div.error-state", { role: "alert" },
    h("p.error-title", "That didn't load"),
    h("p.muted", (error && error.message) || "Something went wrong."),
    retry ? button("Try again", { variant: "secondary", onClick: retry }) : null);
}

/** A live region for short confirmations ("Saved"). */
export function toast(text, tone = "success") {
  const region = document.getElementById("toast-region");
  if (!region) return;
  while (region.children.length >= 2) region.firstChild.remove();
  const item = h(`div.toast.toast-${tone}`, { role: tone === "error" ? "alert" : "status" }, text);
  region.append(item);
  setTimeout(() => item.classList.add("is-leaving"), 2800);
  setTimeout(() => item.remove(), 3200);
}

export function disclosure(summary, content, { open = false, className = "" } = {}) {
  return h(`details.disclosure${className ? "." + className : ""}`, { open },
    h("summary", h("span.disclosure-label", summary), h("span.disclosure-icon", { "aria-hidden": "true" })),
    h("div.disclosure-body", content));
}

const JOURNEY = [
  { key: "profile", label: "Profile" },
  { key: "target", label: "Target role" },
  { key: "pathway", label: "Career plan" },
];

/** Compact journey progress: profile -> target role -> career path. Reachable steps are links; the current one is marked. */
export function journey(current, reachable) {
  const index = JOURNEY.findIndex((s) => s.key === current);
  const prev = index > 0 ? JOURNEY[index - 1] : null;
  // Mobile (CSS): one compact unit, "Step 2 of 3 · Target role", a progress bar and a link back to the previous step.
  const compact = h("div.journey-compact",
    h("p.journey-compact-label", h("span", `Step ${index + 1} of ${JOURNEY.length}`), h("span", { "aria-hidden": "true" }, " · "),
      h("strong", JOURNEY[index].label)),
    h("div.journey-compact-bar", { role: "progressbar", "aria-label": "Journey progress", "aria-valuemin": "1",
      "aria-valuemax": String(JOURNEY.length), "aria-valuenow": String(index + 1),
      "aria-valuetext": `Step ${index + 1} of ${JOURNEY.length}: ${JOURNEY[index].label}` },
    h("span", { style: { width: `${((index + 1) / JOURNEY.length) * 100}%` } })),
    prev && reachable[prev.key] ? h("a.journey-compact-back", { href: reachable[prev.key] }, `← ${prev.label}`) : null);
  return h("nav.journey", { "aria-label": "Your progress" },
    compact,
    h("ol", JOURNEY.map((s, i) => {
      const state = i < index ? "done" : i === index ? "current" : "todo";
      const inner = [h("span.journey-num", { "aria-hidden": "true" }, i < index ? "✓" : String(i + 1)), h("span.journey-label", s.label)];
      const href = reachable[s.key];
      return h(`li.journey-step.is-${state}`, href && i !== index
        ? h("a", { href }, inner)
        : h("span", { "aria-current": state === "current" ? "step" : null }, inner));
    })));
}

export function pageHeader(eyebrow, title, lead, extra = null) {
  return h("header.page-header",
    eyebrow ? h("p.eyebrow", eyebrow) : null,
    h("h1.page-title", { tabindex: "-1" }, title),
    lead ? h("p.lead", lead) : null,
    extra);
}

export function field(labelText, control, hint) {
  const id = control.id || uid("f");
  control.id = id;
  const hintId = hint ? `${id}-hint` : null;
  if (hintId) control.setAttribute("aria-describedby", hintId);
  return h("div.field", h("label", { for: id }, labelText), control, hint ? h("p.hint", { id: hintId }, hint) : null);
}

export function focusTitle(root) {
  const title = root.querySelector(".page-title");
  if (title) title.focus({ preventScroll: true });
}

/**
 * A segmented control (radio semantics): options [{value, label, detail?, disabled?}]. Arrow keys move the choice.
 * onChange(value) runs when the user picks another option.
 */
export function segmented(name, options, value, onChange, { label } = {}) {
  const group = h("div.segmented", { role: "radiogroup", "aria-label": label || name });
  const buttons = options.map((o) => h("button.segment", {
    type: "button", role: "radio", "aria-checked": String(o.value === value), tabindex: o.value === value ? "0" : "-1",
    disabled: o.disabled || false, "data-value": o.value,
  }, h("span.segment-label", o.label), o.detail ? h("span.segment-detail", o.detail) : null));
  const select = (i, focus) => {
    const o = options[i];
    if (!o || o.disabled) return;
    buttons.forEach((b, j) => {
      b.setAttribute("aria-checked", String(i === j));
      b.tabIndex = i === j ? 0 : -1;
    });
    if (focus) buttons[i].focus();
    if (o.value !== value) {
      value = o.value;
      onChange(o.value);
    }
  };
  buttons.forEach((b, i) => {
    b.addEventListener("click", () => select(i, false));
    b.addEventListener("keydown", (e) => {
      const n = options.length;
      let j = null;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") j = (i + 1) % n;
      if (e.key === "ArrowLeft" || e.key === "ArrowUp") j = (i - 1 + n) % n;
      if (j === null) return;
      e.preventDefault();
      for (let k = 0; k < n && options[j].disabled; k += 1) j = (j + (e.key === "ArrowLeft" || e.key === "ArrowUp" ? n - 1 : 1)) % n;
      select(j, true);
    });
    group.append(b);
  });
  return group;
}

/**
 * A modal confirmation (native <dialog>: focus is trapped and Escape closes it). Resolves true only when the
 * confirming action itself resolves; while it runs the buttons are disabled. Errors stay inside the dialog.
 */
export function confirmDialog({ title, body, confirmLabel, busyLabel = "Working…", tone = "danger", action }) {
  return new Promise((resolve) => {
    const status = h("div", { "aria-live": "polite" });
    const titleId = uid("dlg");
    const confirm = button(confirmLabel, { variant: tone === "danger" ? "danger" : "primary" });
    const cancel = button("Cancel", { variant: "ghost" });
    const dialog = h("dialog.dialog", { "aria-labelledby": titleId },
      h("h2.dialog-title", { id: titleId }, title),
      h("div.dialog-body", body),
      status,
      h("div.dialog-actions", cancel, confirm));
    let done = false;
    const close = (result) => {
      if (done) return;
      done = true;
      dialog.close();
      dialog.remove();
      resolve(result);
    };
    cancel.addEventListener("click", () => close(false));
    dialog.addEventListener("cancel", (e) => {
      if (confirm.disabled) e.preventDefault();
      else close(false);
    });
    confirm.addEventListener("click", async () => {
      cancel.disabled = true;
      try {
        await busy(confirm, busyLabel, action);
        close(true);
      } catch (err) {
        mount(status, notice((err && err.message) || "That didn't work.", { tone: "error" }));
      } finally {
        cancel.disabled = false;
      }
    });
    document.body.append(dialog);
    dialog.showModal();
    cancel.focus();
  });
}

/** A read-only modal (for example the full privacy notice). Esc or "Close" closes it and focus returns to the control
 *  that opened it; nothing on the page underneath changes, so an upload in progress keeps its state. */
export function infoDialog({ title, body, closeLabel = "Close" }) {
  const opener = document.activeElement;
  const titleId = uid("dlg");
  const close = button(closeLabel, { variant: "secondary" });
  const dialog = h("dialog.dialog.dialog-wide", { "aria-labelledby": titleId },
    h("h2.dialog-title", { id: titleId }, title),
    h("div.dialog-body.dialog-scroll", { tabindex: "0" }, body),
    h("div.dialog-actions", close));
  const finish = () => {
    dialog.close();
    dialog.remove();
    if (opener && typeof opener.focus === "function") opener.focus();
  };
  close.addEventListener("click", finish);
  dialog.addEventListener("cancel", (e) => { e.preventDefault(); finish(); });
  document.body.append(dialog);
  dialog.showModal();
  close.focus();
  return dialog;
}
