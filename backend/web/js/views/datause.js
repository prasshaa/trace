// How Trace uses your career information: shown once, after profile review and before the target role.
//
// The text is the server's (GET /api/v1/sharing -> notice), so the version recorded is the one shown. There is one
// action, Continue: it records the default (anonymous use to improve recommendations) only when the user has no preference yet
// (POST /api/v1/sharing/notice). It is an acknowledgement of a notice, not a consent decision, and it never starts
// shared-pool processing on its own. The setting can be changed at any time in Profile settings.
import { api } from "../api.js";
import { h, mount } from "../dom.js";
import { busy, button, notice } from "../ui.js";

export function render(root, ctx) {
  const n = ctx.state.sharing.notice;
  const status = h("div", { "aria-live": "polite" });
  const next = button("Continue");
  next.addEventListener("click", () => busy(next, "Continuing…", async () => {
    try {
      await api.acknowledgeNotice(n.version);
      ctx.go("target");
    } catch (err) {
      mount(status, notice(err.message, { tone: "error" }));
    }
  }));
  const [first, ...rest] = n.paragraphs;
  mount(root, h("section.page.page-narrow.data-use",
    h("header.page-header",
      h("p.eyebrow", "Before you continue"),
      h("h1.page-title", { tabindex: "-1" }, n.title),
      first ? h("p.lead", first) : null),
    h("section.card.data-use-card", { "aria-label": "How Trace uses your career information" },
      rest.map((p) => h("p", p))),
    status,
    h("div.page-actions", next)));
  root.querySelector(".page-title").focus({ preventScroll: true });
}
