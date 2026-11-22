// Trace frontend entry point: configuration, authentication, the app shell and hash routing.
//
// One journey: sign in -> get started (personal-analysis permission, your resume) -> profile review -> how Trace uses
// your career information (onboarding notice, once) -> target role -> Career Intelligence Report (product Phase 5).
// Profile settings is reachable from the header at any time. The earlier "My career path" and "Career insights" screens
// are retired: their links (#/results, #/insights, #/intelligence) open the report, which is the one coherent view.
// Where a signed-in user lands is decided from the API's state, never from
// browser flags. There is no sample-profile or demo journey: internal test accounts use these same screens.
import { ApiError, api, publicConfig, setUnauthorizedHandler } from "./api.js";
import { currentSession, initAuth, onAuthChange, signOut, takeOAuthError } from "./auth.js";
import { h, mount } from "./dom.js";
import { errorState, loading } from "./ui.js";
import { forgetContext, rememberContext, restoreContext } from "./state.js";
import * as signin from "./views/signin.js";
import * as start from "./views/setup.js";
import * as review from "./views/review.js";
import * as importReview from "./views/import.js";
import * as target from "./views/target.js";
import * as report from "./views/report.js";
import * as dataUse from "./views/datause.js";
import * as settings from "./views/settings.js";
import * as manage from "./views/manage.js";

const app = {
  config: null,
  session: null,
  oauthError: null,
  me: null,
  target: null,           // chosen target role id (also in the URL: #/pathway/<role id>)
  hasRecords: false,
  renderToken: 0,
};


const shell = {
  header: document.getElementById("app-header"),
  main: document.getElementById("main"),
};

// Mobile menu: a click or tap outside the open menu closes it. One listener for the app's lifetime (the header is
// re-rendered on every route, so per-render listeners would pile up); it reads the current header state. Clicks inside
// the header are left to the menu's own handlers (a destination closes it after navigating; the toggle toggles), and
// focus is not moved: the user has chosen where to interact next.
document.addEventListener("click", (e) => {
  if (!shell.header.classList.contains("is-menu-open") || shell.header.contains(e.target)) return;
  shell.header.classList.remove("is-menu-open");
  const toggle = shell.header.querySelector(".nav-toggle");
  if (toggle) toggle.setAttribute("aria-expanded", "false");
});

function parseHash() {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [path, param, section] = raw.split("/");
  return { path: path || "", param: param ? decodeURIComponent(param) : null, section: section || null };
}

export function go(path) {
  const next = `#/${path}`;
  if (window.location.hash === next) route();
  else window.location.hash = next;
}

const NAV = [
  { key: "profile", label: "Profile", needsRecords: true },
  { key: "target", label: "Target role", needsRecords: true },
  { key: "pathway", label: "My career plan", needsRecords: true },
  { key: "settings", label: "Profile settings" },
];
// Earlier links. Career insights is retired (Phase 5): its raw synthetic distributions are not shown; the report replaces it.
const ALIASES = { results: "pathway", setup: "start", intelligence: "pathway", insights: "pathway", contribution: "settings" };

function navHref(key) {
  if (key === "pathway") return app.target ? `#/pathway/${encodeURIComponent(app.target)}` : "#/target";
  return `#/${key}`;
}

function renderHeader(active) {
  const items = app.session && app.me
    ? NAV.filter((n) => !n.needsRecords || app.hasRecords)
    : [];
  if (app.session && app.me && app.pendingImport) items.unshift({ key: "import", label: "Review your resume" });
  else if (app.session && app.me && !app.hasRecords) items.unshift({ key: "start", label: "Get started" });
  // Mobile (CSS, below 860px): the links collapse behind an accessible Menu button; desktop is unchanged.
  const nav = items.length ? h("nav.main-nav", { id: "main-nav", "aria-label": "Main" },
    h("ul", items.map((n) => h("li", h("a", {
      href: navHref(n.key), "aria-current": active === n.key ? "page" : null,
    }, n.label))))) : null;
  const toggle = nav ? h("button.nav-toggle", { type: "button", "aria-expanded": "false", "aria-controls": "main-nav" },
    h("span.nav-toggle-icon", { "aria-hidden": "true" }), h("span", "Menu")) : null;
  const setOpen = (open, { focusToggle = false } = {}) => {
    shell.header.classList.toggle("is-menu-open", open);
    toggle.setAttribute("aria-expanded", String(open));
    if (open) {
      const first = nav.querySelector("a[aria-current]") || nav.querySelector("a");
      if (first) first.focus();
    } else if (focusToggle) {
      toggle.focus();
    }
  };
  if (nav) {
    shell.header.classList.remove("is-menu-open");
    toggle.addEventListener("click", () => setOpen(toggle.getAttribute("aria-expanded") !== "true"));
    nav.addEventListener("click", (e) => { if (e.target.closest("a")) setOpen(false); });
    shell.header.onkeydown = (e) => {
      if (e.key === "Escape" && toggle.getAttribute("aria-expanded") === "true") {
        e.preventDefault();
        setOpen(false, { focusToggle: true });
      }
    };
  }
  mount(shell.header,
    h("div.header-inner",
      h("a.brand", { href: app.session ? "#/" : "#/" },
        h("span.brand-mark", { "aria-hidden": "true" }), h("span.brand-name", "Trace")),
      nav,
      app.session
        ? h("div.account",
          toggle,
          h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: async () => { await signOut(); } }, "Sign out"))
        : null));
}

async function route() {
  const token = ++app.renderToken;
  const root = shell.main;
  if (!app.session) {
    renderHeader(null);
    if (window.location.hash && window.location.hash !== "#/") history.replaceState(null, "", "#/");
    signin.render(root, { config: app.config, oauthError: app.oauthError });
    app.oauthError = null;
    return;
  }
  renderHeader(null);
  mount(root, loading("Loading your workspace"));
  let state;
  try {
    state = await journeyState();
  } catch (err) {
    if (token !== app.renderToken) return;
    if (err instanceof ApiError && err.status === 401) return;
    mount(root, errorState(err, route));
    return;
  }
  if (token !== app.renderToken) return;

  let { path, param, section } = parseHash();
  path = ALIASES[path] || path;
  if (param && path === "pathway") app.target = param;
  const ctx = { app, state, go, refresh: route };

  // An import waiting for review comes first: it is reviewed and confirmed in one place before anything else.
  if (state.pendingImport && !["import", "settings", "start"].includes(path)) path = "import";
  if (!state.pendingImport && path === "import") path = state.hasRecords ? "profile" : "start";
  if (!state.hasRecords && !state.pendingImport && !["start", "settings"].includes(path)) path = "start";
  // A returning user with a saved career plan lands on it; otherwise on their profile.
  if (state.hasRecords && path === "" && state.latestPlan && state.sharing.notice_acknowledged) {
    app.target = state.latestPlan.target_role_id;
    path = "pathway";
  }
  // Phase 5: after a reload the target page (and its saved goal and "where" preference) shows the latest plan's target.
  if (!app.target && state.hasRecords && state.latestPlan) app.target = state.latestPlan.target_role_id;
  if (state.hasRecords && !state.pendingImport && (path === "start" || path === "")) path = "profile";
  if (path === "replace" && !state.hasRecords) path = "start";   // nothing to replace yet: the ordinary upload
  if (path === "data-use" && (!state.hasRecords || state.sharing.notice_acknowledged)) path = state.hasRecords ? "target" : "start";
  // The data-use notice comes after profile review and before the target role, once.
  if (state.hasRecords && !state.sharing.notice_acknowledged && ["target", "pathway"].includes(path)) path = "data-use";
  const canonical = path === "pathway" && app.target
    ? `#/pathway/${encodeURIComponent(app.target)}${section ? `/${section}` : ""}` : `#/${path}`;
  rememberContext(app, canonical);
  renderHeader(path);

  switch (path) {
    case "start":
      return start.render(root, ctx);
    case "import":
      return importReview.render(root, ctx);
    case "data-use":
      return dataUse.render(root, ctx);
    case "target":
      return target.render(root, ctx);
    case "pathway":
      if (!app.target) return go("target");
      return report.render(root, { ...ctx, targetId: app.target, section: section || "overview" });
    case "settings":
      return settings.render(root, ctx);
    case "replace":
      return manage.renderReplace(root, ctx);
    default:
      return review.render(root, ctx);
  }
}

/** Where this account is in the journey. Creates the account's own (empty) profile on first visit. */
async function journeyState() {
  let me = await api.me();
  if (!me.has_profile) {
    await api.createProfile();                // owner-scoped: the API creates the caller's own profile only
    me = await api.me();
  }
  app.me = me;
  const [consent, records, sharing, latestPlan, resumeImport] = await Promise.all([api.consent(), api.records(), api.sharing(),
    api.latestAnyPlan().catch(() => null), api.resumeImport().catch(() => ({ status: "none" }))]);
  const pendingImport = resumeImport.status === "ready";
  // Only reviewed (confirmed or corrected) records count as a profile; drafts of an open import are reviewed first.
  const reviewed = (r) => r.review && ["user_confirmed", "user_corrected"].includes(r.review.status);
  const hasRecords = records.employments.some(reviewed) || records.other_records.some(reviewed)
    || records.unattached_evidence.some(reviewed);
  app.hasRecords = hasRecords;
  app.pendingImport = pendingImport;
  return { me, consent, records, sharing, hasRecords, latestPlan, pendingImport, resumeImport };
}

async function boot() {
  mount(shell.main, loading("Starting Trace"));
  try {
    app.config = await publicConfig();
    initAuth(app.config);
  } catch (err) {
    mount(shell.main, h("section.page.narrow",
      h("h1.page-title", "Trace isn't ready yet"),
      h("p.lead", err.message || "The sign-in configuration is missing."),
      h("p.muted", "The server needs SUPABASE_URL and SUPABASE_ANON_KEY. See backend/docs/FRONTEND.md.")));
    return;
  }
  app.oauthError = takeOAuthError();
  app.session = await currentSession();
  restoreContext(app);
  setUnauthorizedHandler(() => {
    app.session = null;
    route();
  });
  onAuthChange((event, session) => {
    const wasSignedIn = Boolean(app.session);
    app.session = session;
    if (event === "SIGNED_OUT" || (event === "SIGNED_IN" && !wasSignedIn)) {
      if (event === "SIGNED_OUT") {
        app.target = null;
        app.me = null;
        forgetContext();
      }
      route();
    }
  });
  window.addEventListener("hashchange", route);
  route();
}

boot();
