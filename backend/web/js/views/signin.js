// Sign in: Google first (when the Auth server has it enabled), email and password second, account creation.
import { enabledProviders, signInWithGoogle, signInWithPassword, signUp } from "../auth.js";
import { h, mount } from "../dom.js";
import { busy, button, field, notice } from "../ui.js";

export function render(root, { oauthError, config }) {
  const research = Boolean(config && config.features && config.features.career_research);
  const status = h("div.form-status", { "aria-live": "polite" });
  if (oauthError) mount(status, notice(`Google sign-in didn't complete: ${oauthError}`, { tone: "error" }));

  const googleBtn = button(h("span.google-label", h("span.google-mark", { "aria-hidden": "true" }, "G"), "Continue with Google"),
    { variant: "primary", attrs: { class: "btn-block btn-google" } });
  const googleNote = h("p.hint.center", "Trace asks Google only for your name and email address. Nothing else.");
  googleBtn.addEventListener("click", () => busy(googleBtn, "Opening Google…", async () => {
    try {
      await signInWithGoogle();
    } catch (err) {
      mount(status, notice(err.message, { tone: "error" }));
    }
  }));

  // Google is shown only when the Auth server reports it enabled; otherwise it is not shown at all (never an
  // active-looking button that does nothing). Email and password always work.
  const googleBlock = h("div.google-block", { hidden: true }, googleBtn, googleNote, h("div.divider", h("span", "or continue with email")));
  googleBtn.disabled = true;
  enabledProviders().then((p) => {
    if (p.google) {
      googleBtn.disabled = false;
      googleBlock.hidden = false;
    }
  });

  let mode = "signin";
  const email = h("input", { type: "email", name: "email", autocomplete: "email", required: true, maxlength: "254" });
  const password = h("input", { type: "password", name: "password", autocomplete: "current-password", required: true, minlength: "8", maxlength: "128" });
  const submit = button("Continue with email", { variant: "secondary", type: "submit", attrs: { class: "btn-block" } });
  const toggle = h("button.link-button", { type: "button" }, "New to Trace? Create an account");
  const submitLabel = () => (mode === "signin" ? "Continue with email" : "Create account");
  const form = h("form.auth-form", { novalidate: true },
    field("Email", email),
    field("Password", password, "At least 8 characters."),
    submit, toggle);

  toggle.addEventListener("click", () => {
    mode = mode === "signin" ? "signup" : "signin";
    submit.textContent = submitLabel();
    const title = form.closest(".landing") ? form.closest(".landing").querySelector("#auth-card-title") : null;
    if (title) title.textContent = mode === "signin" ? "Sign in to Trace" : "Create your Trace account";
    toggle.textContent = mode === "signin" ? "New to Trace? Create an account" : "Already have an account? Sign in";
    password.setAttribute("autocomplete", mode === "signin" ? "current-password" : "new-password");
    mount(status);
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!email.value.trim() || !email.checkValidity()) {
      mount(status, notice("Enter a valid email address.", { tone: "error" }));
      email.focus();
      return;
    }
    if (password.value.length < 8) {
      mount(status, notice("Your password needs at least 8 characters.", { tone: "error" }));
      password.focus();
      return;
    }
    busy(submit, mode === "signin" ? "Signing in…" : "Creating account…", async () => {
      try {
        if (mode === "signin") {
          await signInWithPassword(email.value.trim(), password.value);
        } else {
          const result = await signUp(email.value.trim(), password.value);
          if (result.needsConfirmation) {
            mount(status, notice("Check your email to confirm your account, then sign in.", { tone: "info" }));
          }
        }
      } catch (err) {
        mount(status, notice(err.message, { tone: "error" }));
      }
    });
  });

  // Order in the DOM (and on mobile): intro, sign-in card, then the three steps. Desktop places the steps under the
  // intro in the left column (CSS grid areas). Below: who Trace is for, what makes it different, and (only where the
  // research view is switched on) the research methodology note. Where the research is off (its release gates are open,
  // D46), the page describes it as a capability under review and makes no claim about career-history data.
  mount(root, h("div.landing",
    h("section.auth",
      h("div.auth-intro",
        h("p.eyebrow", "Career pathways for product roles"),
        h("h1.page-title.display", { tabindex: "-1" }, "Find your path to your target role."),
        h("p.lead", research
          ? "Understand where your experience stands, explore how Product Managers with different backgrounds have made "
            + "career moves, and see what you could build next to reach your goal."
          : "See what your experience proves for your next product role, what is missing, and how to close the gap, "
            + "and keep that case growing as your career does."),
        h("p.tagline", "Upload your resume. Choose your target. See your path.")),
      h("div.auth-card",
        h("h2.card-title", { id: "auth-card-title" }, "Sign in to Trace"),
        googleBlock,
        status, form),
      h("ol.auth-points", { "aria-label": "How Trace works" },
        h("li", h("span.auth-point-num", { "aria-hidden": "true" }, "01"),
          h("div", h("strong", "Upload your resume"), h("span", "Check and correct what Trace found, then confirm it once."))),
        h("li", h("span.auth-point-num", { "aria-hidden": "true" }, "02"),
          h("div", h("strong", "Choose your target"), h("span", "Pick the role you want to reach, including the role you have now at another company."))),
        h("li", h("span.auth-point-num", { "aria-hidden": "true" }, "03"),
          h("div", h("strong", "See your path"), h("span", "The career moves that lead there, and the experience behind them."))))),
    audience(),
    differentiator(research)));
  root.querySelector(".page-title").focus({ preventScroll: true });
}

function audience() {
  return h("section.landing-band", { "aria-labelledby": "who-title" },
    h("div.landing-inner.landing-split",
      h("div",
        h("h2.landing-title", { id: "who-title" }, "Who Trace is for"),
        h("p.landing-lead", "A career move is hard to plan from the inside. It's hard to tell whether your experience is enough, "
          + "what's missing, how soon you could move, or what to do next. Job descriptions, other people's profiles and "
          + "scattered advice rarely add up to a clear view of your own position.")),
      h("ul.landing-audience",
        h("li", h("strong", "Moving into product management"), h("span", "From engineering, QA, analysis, consulting, design or another field.")),
        h("li", h("strong", "Product Managers aiming higher"), h("span", "Working towards a senior or lead product role.")),
        h("li", h("strong", "Making a lateral move"), h("span", "The same kind of role, at another company or in another kind of product.")))));
}

function differentiator(research) {
  const points = [
    ["Understand where you stand", "See how your existing experience relates to your target role, with the lines from your "
      + "own profile behind every judgement."],
    research
      ? ["Explore real career paths", "Understand the different routes Product Managers with relevant starting backgrounds have taken."]
      : ["See the routes to your target", "A direct move or a step through a related role, with the reasons Trace recommends one."],
    ["Know what to do next", "Identify relevant experience to build, practical actions at work, and ways to strengthen your transition."],
  ];
  return h("section.landing-band.is-tint", { "aria-labelledby": "diff-title" },
    h("div.landing-inner",
      h("h2.landing-title", { id: "diff-title" }, research
        ? "Your career decisions, grounded in real career journeys."
        : "Your career decisions, grounded in evidence you can check."),
      h("p.landing-lead", research
        ? "Trace compares your confirmed experience with observed career patterns from real Product Managers, to help you "
          + "explore relevant career paths, understand where you stand and identify what to build next."
        : "Trace compares your confirmed experience with sourced descriptions of what each product role involves, and shows "
          + "which of your own experience supports each part of your plan. It isn't a resume builder or a job board."),
      h("ol.landing-points", points.map(([t, d], i) => h("li",
        h("span.auth-point-num", { "aria-hidden": "true" }, String(i + 1).padStart(2, "0")),
        h("div", h("strong", t), h("span", d))))),
      h("div.landing-separation",
        h("p", h("strong", "Three things kept apart. "), "Your private profile is yours: only you can see it. "
          + (research ? "The career research describes groups of people, never individuals. " : "")
          + "Trace's recommendations are labelled as recommendations, with the reasons behind them.")),
      research ? methodology() : upcoming()));
}

function methodology() {
  return h("details.disclosure.subtle.landing-method",
    h("summary", h("span.disclosure-label", "About the career research")),
    h("div.disclosure-body",
      h("p", "The research summarises the career histories of 439 Product Managers in India, grouped by where they started and "
        + "how they moved. It's a non-representative sample, so it shows routes people took, not how common each route is "
        + "everywhere."),
      h("p", "It describes what happened. It doesn't predict your chances of making a move. No individual's history is ever "
        + "shown, and groups too small to describe safely are hidden."),
      h("p.muted", "Internal preview: shown in this preview while its review is completed.")));
}

function upcoming() {
  return h("div.landing-upcoming",
    h("p", h("strong", "Coming next: career research. "), "We're reviewing aggregated research on how Product Managers with "
      + "different starting backgrounds have moved into and through product roles. It isn't available yet, while its "
      + "data-use and privacy review is completed."));
}
