// The Trace API client. Every protected call carries the Supabase access token as a Bearer token. Errors become
// ApiError with a short user-facing message; raw server details are never shown. A 401 triggers one session refresh
// and retry; if that fails the app returns to sign-in.

import { accessToken, refreshSession } from "./auth.js";

export class ApiError extends Error {
  constructor(status, code, message, extra = {}) {
    super(message);
    this.status = status;
    this.code = code;
    Object.assign(this, extra);
  }
}

const FRIENDLY = {
  0: "Can't reach Trace right now. Check your connection and try again.",
  401: "Your session has ended. Please sign in again.",
  403: "That action isn't allowed.",
  404: "We couldn't find that.",
  409: "That change conflicts with the current state. Refresh and try again.",
  413: "That file is too large.",
  415: "Upload a PDF file.",
  422: "Something in that request isn't valid.",
  429: "You're going a little fast. Wait a moment and try again.",
  500: "Something went wrong on our side. Please try again.",
  503: "Trace is temporarily unavailable. Please try again shortly.",
};

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

export async function publicConfig() {
  const resp = await fetch("/api/v1/config", { headers: { Accept: "application/json" } });
  if (!resp.ok) throw new ApiError(resp.status, "config_unavailable", "Trace isn't configured for sign-in yet.");
  return resp.json();
}

async function send(method, path, { body, raw, contentType, headers = {}, accept = "application/json" } = {}, retried = false) {
  const token = await accessToken();
  if (!token) {
    onUnauthorized();
    throw new ApiError(401, "unauthorized", FRIENDLY[401]);
  }
  const init = { method, headers: { Accept: accept, Authorization: `Bearer ${token}`, ...headers } };
  if (raw !== undefined) {
    init.body = raw;
    init.headers["Content-Type"] = contentType;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers["Content-Type"] = "application/json";
  }
  let resp;
  try {
    resp = await fetch(path, init);
  } catch {
    throw new ApiError(0, "network", FRIENDLY[0]);
  }
  if (resp.status === 401 && !retried) {
    const session = await refreshSession();
    if (session) return send(method, path, { body, raw, contentType, headers, accept }, true);
    onUnauthorized();
  }
  if (!resp.ok) throw await toError(resp);
  if (accept !== "application/json") return resp;
  return resp.status === 204 ? null : resp.json();
}

async function toError(resp) {
  let payload = {};
  try {
    payload = await resp.json();
  } catch {
    /* non-JSON error body: use the generic message */
  }
  const code = payload.error || "error";
  // The API's own messages are already sanitised and written for people; fall back to a fixed message otherwise.
  const serverMessage = typeof payload.message === "string" && payload.message.length < 240 ? payload.message : "";
  const message = resp.status >= 500 || !serverMessage ? FRIENDLY[resp.status] || FRIENDLY[500] : sentence(serverMessage);
  const extra = { fields: payload.fields || [] };
  if (typeof payload.reference === "string" && /^[0-9a-f]{6,16}$/.test(payload.reference)) extra.reference = payload.reference;
  if (resp.status === 429) extra.retryAfter = Number(resp.headers.get("Retry-After")) || 30;
  return new ApiError(resp.status, code, message, extra);
}

function sentence(text) {
  const t = text.trim();
  return t ? t[0].toUpperCase() + t.slice(1) + (/[.!?]$/.test(t) ? "" : ".") : t;
}

const q = (v) => encodeURIComponent(v);
const ifMatch = (version) => (version ? { "If-Match": version } : {});

export const api = {
  // account and profile
  me: () => send("GET", "/api/v1/me"),
  createProfile: () => send("POST", "/api/v1/profile", { body: {} }),
  records: () => send("GET", "/api/v1/profile/records"),

  // personal analysis consent (purpose resume_processing)
  consent: () => send("GET", "/api/v1/consent/resume-processing"),
  decideConsent: (decision, version) =>
    send("POST", "/api/v1/consent/resume-processing", { body: { decision, consent_version: version } }),

  // how the career information is used: the onboarding data-use notice, the sharing preference (Profile settings)
  // and, separately, the actual state of the profile in the shared pool. A preference is never a consent decision.
  sharing: () => send("GET", "/api/v1/sharing"),
  acknowledgeNotice: (version) => send("POST", "/api/v1/sharing/notice", { body: { notice_version: version } }),
  chooseSharing: (mode, confirmVersion = null) =>
    send("PUT", "/api/v1/sharing", { body: confirmVersion ? { mode, confirm_version: confirmVersion } : { mode } }),

  // a person's own resume: only when the server offers it (config.features.real_resume_uploads). No filename is sent.
  uploadRealResume: (bytes) => send("POST", "/api/v1/real-resumes", { raw: bytes, contentType: "application/pdf" }),
  // Resume-first onboarding (parser v2): one import, one consolidated review, one confirmation.
  resumeImport: () => send("GET", "/api/v1/resume-import"),
  importResume: (bytes, contentType, replace = false, replaceProfile = false) => {
    const qs = [replace ? "replace=true" : null, replaceProfile ? "replace_profile=true" : null].filter(Boolean).join("&");
    return send("POST", `/api/v1/resume-import${qs ? `?${qs}` : ""}`, { raw: bytes, contentType });
  },
  clearPreview: () => send("GET", "/api/v1/profile/clear"),
  clearProfile: (confirm) => send("POST", "/api/v1/profile/clear", { body: { confirm } }),
  confirmImport: (id, edits) => send("POST", `/api/v1/resume-import/${q(id)}/confirm`, { body: edits }),
  // read-only: which role sets the starting point (with its choices) and every field still to fix, for these edits
  checkImport: (id, edits) => send("POST", `/api/v1/resume-import/${q(id)}/check`, { body: edits }),
  discardImport: () => send("DELETE", "/api/v1/resume-import"),
  realResumeStatus: () => send("GET", "/api/v1/real-resumes/status"),

  // review, corrections, role matching and evidence tagging. ``version`` is the record's version as last read
  // (review.version): sent as If-Match, so a change made meanwhile elsewhere is refused (409 record_changed), never
  // silently overwritten.
  review: (action, kind, id, version) => send("POST", `/api/v1/records/${kind}/${q(id)}/${action}`, { headers: ifMatch(version) }),
  correct: (kind, id, changes, version) =>
    send("POST", `/api/v1/records/${kind}/${q(id)}/corrections`, { body: changes, headers: ifMatch(version) }),
  editDraft: (kind, id, changes, version) =>
    send("PATCH", `/api/v1/records/${kind}/${q(id)}`, { body: changes, headers: ifMatch(version) }),

  // adding to the profile by hand, and suggested updates (pasted text, a later resume). Nothing is added until the user
  // accepts each suggestion; "confirm" is the user's explicit confirmation.
  addEntry: (kind, body) => send("POST", `/api/v1/entries/${kind}`, { body }),
  proposeFromText: (text) => send("POST", "/api/v1/proposals/from-text", { body: { text } }),
  proposals: () => send("GET", "/api/v1/proposals"),
  acceptProposal: (id, confirm, classifications = []) =>
    send("POST", `/api/v1/proposals/${q(id)}/accept`, { body: { confirm: Boolean(confirm), classifications } }),
  dismissProposal: (id) => send("POST", `/api/v1/proposals/${q(id)}/dismiss`),
  mapRole: (employmentId) => send("POST", `/api/v1/employments/${q(employmentId)}/map`),
  roleOptions: (employmentId) => send("GET", `/api/v1/employments/${q(employmentId)}/role-options`),
  chooseRole: (employmentId, roleId, ownershipAreas) =>
    send("POST", `/api/v1/employments/${q(employmentId)}/role`, {
      body: ownershipAreas && ownershipAreas.length ? { role_id: roleId, ownership_areas: ownershipAreas } : { role_id: roleId },
    }),
  // Phase 3.1: choice = {areas, none, qualifiers}; the user's own explicit classification (never inferred)
  classify: (evidenceId, choice, version) =>
    send("PUT", `/api/v1/evidence/${q(evidenceId)}/classification`, { body: choice, headers: ifMatch(version) }),
  clearClassification: (evidenceId, version) =>
    send("DELETE", `/api/v1/evidence/${q(evidenceId)}/classification`, { headers: ifMatch(version) }),

  // taxonomy, position, pathway and career insights. The reference source is chosen by the server (its data mode);
  // the browser never sends or chooses one, and synthetic and real profiles are never combined.
  targetRoles: () => send("GET", "/api/v1/taxonomy/target-roles"),
  areas: () => send("GET", "/api/v1/taxonomy/responsibility-areas"),
  currentPosition: () => send("GET", "/api/v1/current-position"),
  pathway: (target) => send("GET", `/api/v1/pathway?target=${q(target)}`),
  insights: (target) => send("GET", `/api/v1/insights?target=${q(target)}`),

  // Career Intelligence Report (product Phase 5): the saved transition plan. A POST saves a new append-only run from the
  // current confirmed profile; GET returns the latest saved run for a target (404 plan_not_found when none). The only
  // input the user gives is the stated portfolio scope of a Senior PM target (D31); the server decides everything else.
  latestPlan: (target) => send("GET", `/api/v1/transition-plan?target=${q(target)}`),
  // contexts: only those already stored on the previous plan are carried forward (there is no context selector).
  createPlan: (target, { portfolioScope = false, contexts = [] } = {}) =>
    send("POST", `/api/v1/transition-plan?target=${q(target)}${contexts.map((c) => `&context=${q(c)}`).join("")}`
      + `${portfolioScope ? "&scope=portfolio" : ""}`),

  // Career transition roadmap (redesign): the user's planning goal per target (an ambition, never a forecast and never
  // a plan input) and the roadmap projection of a saved plan. Setting a goal never creates a plan run.
  goal: (target) => send("GET", `/api/v1/transition-goal?target=${q(target)}`),
  // Where the user wants to make the move (Phase 5, D51): specific companies, kinds of company, or open. A preference.
  movePreference: (target) => send("GET", `/api/v1/move-preference?target=${q(target)}`),
  setMovePreference: (target, body) => send("PUT", `/api/v1/move-preference?target=${q(target)}`, { body }),
  setGoal: (target, body) => send("PUT", `/api/v1/transition-goal?target=${q(target)}`, { body }),
  roadmap: (runId) => send("GET", `/api/v1/transition-plan/${q(runId)}/roadmap`),
  // Career Origins & Transitions (internal preview, off unless the server enables it): research-cohort aggregates, with the
  // research group matching the confirmed current role of this saved plan. 404 means the feature is off.
  careerOrigins: (runId) => send("GET", `/api/v1/career-origins?plan=${q(runId)}`),
  // where a returning user lands: their most recent saved plan for any target (404 when there is none)
  latestAnyPlan: () => send("GET", "/api/v1/transition-plans/latest"),
  exportData: () => send("GET", "/api/v1/account/export"),
  deleteAccount: (confirm) => send("POST", "/api/v1/account", { body: { confirm } }),

  // DORMANT (redesign): target companies and company research are no longer part of the product flow. The endpoints
  // and data are preserved for possible future use; no view calls these.
  // Target companies (POC): optional aspirations per target role. They only choose which employer research the report
  // shows; they never change the plan and never create a plan run. Search covers Trace's researched employers only.
  targetCompanies: (target) => send("GET", `/api/v1/target-companies?target=${q(target)}`),
  setTargetCompanies: (target, names) => send("PUT", `/api/v1/target-companies?target=${q(target)}`, { body: { companies: names } }),
  searchCompanies: (target, query) => send("GET", `/api/v1/target-companies/search?target=${q(target)}&q=${q(query)}`),
  companyResearch: (target) => send("GET", `/api/v1/company-research?target=${q(target)}`),
};
