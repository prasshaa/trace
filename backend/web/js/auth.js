// Supabase Auth only. The browser receives the public URL and anon key from /api/v1/config and nothing else; it never
// sees a service-role key, database credential or OAuth client secret. The Supabase client is used for sign-in,
// session restoration, refresh and sign-out ONLY: every profile, pathway and insight request goes to the Trace API
// (api.js), never to Supabase tables, storage or functions.
//
// Session storage follows the SDK's supported mechanism (its own storage key); this app never copies tokens elsewhere.
// Google sign-in uses the PKCE flow and requests only the provider's default identity scopes (openid, email, profile).

let client = null;
let supabaseUrl = "";
let anonKey = "";

export function initAuth(config) {
  if (!window.supabase || typeof window.supabase.createClient !== "function") {
    throw new Error("The sign-in library did not load.");
  }
  supabaseUrl = config.supabase_url;
  anonKey = config.supabase_anon_key;
  client = window.supabase.createClient(supabaseUrl, anonKey, {
    auth: {
      flowType: "pkce",
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
    },
  });
  return client;
}

export async function currentSession() {
  const { data, error } = await client.auth.getSession();
  if (error) return null;
  return data.session || null;
}

/** A fresh access token for the Trace API, or null when signed out. The SDK refreshes it when needed. */
export async function accessToken() {
  const session = await currentSession();
  return session ? session.access_token : null;
}

export async function refreshSession() {
  const { data, error } = await client.auth.refreshSession();
  if (error) return null;
  return data.session || null;
}

export function onAuthChange(callback) {
  return client.auth.onAuthStateChange((event, session) => callback(event, session));
}

export async function signInWithPassword(email, password) {
  const { error } = await client.auth.signInWithPassword({ email, password });
  if (error) throw friendlyAuthError(error);
}

export async function signUp(email, password) {
  const { data, error } = await client.auth.signUp({ email, password, options: { emailRedirectTo: appUrl() } });
  if (error) throw friendlyAuthError(error);
  return { needsConfirmation: !data.session };
}

export async function signInWithGoogle() {
  // No custom scopes: Supabase requests the provider's identity scopes only. No Gmail, Drive, Calendar or Contacts.
  const { error } = await client.auth.signInWithOAuth({ provider: "google", options: { redirectTo: appUrl() } });
  if (error) throw friendlyAuthError(error);
}

export async function signOut() {
  await client.auth.signOut();
}

/** Which providers the Supabase Auth server has enabled (its public settings endpoint; anon key only). */
export async function enabledProviders() {
  try {
    const resp = await fetch(`${supabaseUrl.replace(/\/$/, "")}/auth/v1/settings`, { headers: { apikey: anonKey } });
    if (!resp.ok) return { google: false, email: true };
    const body = await resp.json();
    const ext = body.external || {};
    return { google: Boolean(ext.google), email: ext.email !== false };
  } catch {
    return { google: false, email: true };
  }
}

/** Error text from the URL after a failed OAuth redirect (e.g. provider disabled), then removed from the address bar. */
export function takeOAuthError() {
  const params = new URLSearchParams(window.location.search);
  const hash = new URLSearchParams(window.location.hash.replace(/^#\/?[^?]*\??/, ""));
  const message = params.get("error_description") || hash.get("error_description");
  if (message) window.history.replaceState(null, "", appUrl());
  return message ? message.replace(/\+/g, " ") : null;
}

function appUrl() {
  return `${window.location.origin}/app/`;
}

function friendlyAuthError(error) {
  const msg = (error && error.message) || "";
  if (/invalid login credentials/i.test(msg)) return new Error("That email and password don't match an account.");
  if (/provider is not enabled|unsupported provider/i.test(msg)) {
    return new Error("Google sign-in isn't configured on this server yet. Use email and password instead.");
  }
  if (/already registered/i.test(msg)) return new Error("An account with this email already exists. Sign in instead.");
  if (/password/i.test(msg) && /characters|short|weak/i.test(msg)) return new Error("Choose a longer password (at least 8 characters).");
  if (/rate limit|too many/i.test(msg)) return new Error("Too many attempts. Wait a minute and try again.");
  return new Error("Sign-in didn't work. Check your details and try again.");
}
