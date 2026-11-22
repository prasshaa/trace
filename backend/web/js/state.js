// The user's working context (the chosen target role id; nothing personal) kept in the current history entry's state,
// so it survives a reload of this tab without using browser storage. Cleared on sign-out.
const VALID_TARGET = (v) => typeof v === "string" && /^[A-Z0-9-]{1,40}$/.test(v);

export function restoreContext(app) {
  const s = history.state && typeof history.state === "object" ? history.state : {};
  if (!app.target && VALID_TARGET(s.target)) app.target = s.target;
}

/** Record the context in the current history entry (optionally moving to `url`, without a new entry). */
export function rememberContext(app, url = window.location.hash || "#/") {
  history.replaceState({ target: app.target || null }, "", url);
}

export function forgetContext() {
  history.replaceState(null, "", window.location.hash || "#/");
}
