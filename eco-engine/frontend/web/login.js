/* Sign-in / create-account page. */
(function () {
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const next = params.get("next") || "app.html";
  const safeNext = /^[a-z0-9_.-]+\.html([?#].*)?$/i.test(next) ? next : "app.html";
  let mode = "signin";
  let provider = "local";

  function setMode(m) {
    mode = m;
    const up = m === "signup";
    $("modeSignIn").setAttribute("aria-pressed", String(!up));
    $("modeSignUp").setAttribute("aria-pressed", String(up));
    $("authEyebrow").textContent = up ? "New here" : "Welcome back";
    $("authTitle").innerHTML = up ? "Create your <mark>account.</mark>" : "Sign in to <span class='brand-word brand-inline'>Eco</span>";
    $("authSub").textContent = up
      ? "Free, takes ten seconds. Save plans for any home and compare them later."
      : "Save retrofit scenarios and pick up where you left off.";
    $("submitLabel").textContent = up ? "Create account" : "Sign in";
    $("password").autocomplete = up ? "new-password" : "current-password";
    $("nameField").hidden = !up;
    $("pwMeter").hidden = !up;
    $("forgotBtn").hidden = up || provider !== "supabase";
    say("");
  }

  function say(text, kind) {
    $("formMsg").textContent = text;
    $("formMsg").className = `form-msg ${kind || ""}`;
  }

  function friendly(err) {
    const m = (err && err.message) || String(err);
    if (/invalid login credentials|don't match/i.test(m)) return "That email and password don't match. Check them and try again.";
    if (/already (registered|exists)/i.test(m)) return "An account with this email already exists. Sign in instead.";
    if (/provider is not enabled/i.test(m)) return "Google sign-in isn't enabled for this project yet.";
    if (/Failed to fetch|NetworkError/i.test(m)) return "Can't reach the server. Check the API is running.";
    return m;
  }

  function strength(pw) {
    let s = 0;
    if (pw.length >= 8) s++;
    if (pw.length >= 12) s++;
    if (/[A-Z]/.test(pw) && /[a-z]/.test(pw)) s++;
    if (/\d/.test(pw)) s++;
    if (/[^A-Za-z0-9]/.test(pw)) s++;
    return s;
  }

  $("password").addEventListener("input", () => {
    const s = strength($("password").value);
    const bar = $("pwMeter").firstElementChild;
    bar.style.width = `${(s / 5) * 100}%`;
    bar.style.background = s <= 1 ? "var(--danger)" : s <= 3 ? "var(--warn)" : "var(--ok)";
    if (mode === "signup") $("pwHelp").textContent = ["Too short", "Weak", "Okay", "Good", "Strong", "Very strong"][s];
  });

  $("pwToggle").addEventListener("click", () => {
    const show = $("password").type === "password";
    $("password").type = show ? "text" : "password";
    $("pwToggle").setAttribute("aria-label", show ? "Hide password" : "Show password");
    $("pwToggle").innerHTML = icon(show ? "eye-off" : "eye", "icon icon-sm");
  });

  $("modeSignIn").addEventListener("click", () => setMode("signin"));
  $("modeSignUp").addEventListener("click", () => setMode("signup"));

  $("authForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = $("email").value.trim();
    const password = $("password").value;
    if (!/^\S+@\S+\.\S+$/.test(email)) { say("Enter a valid email address.", "error"); $("email").focus(); return; }
    if (password.length < 8) { say("Use a password of at least 8 characters.", "error"); $("password").focus(); return; }

    const btn = $("submitBtn");
    btn.disabled = true;
    say(mode === "signup" ? "Creating your account…" : "Signing in…");
    try {
      if (mode === "signup") {
        const { needsConfirmation } = await EcoAuth.signUp(email, password, $("name").value.trim());
        if (needsConfirmation) { say("Check your inbox to confirm your email, then sign in.", "ok"); return; }
      } else {
        await EcoAuth.signIn(email, password);
      }
      say("Done. Taking you to the app…", "ok");
      location.href = safeNext;
    } catch (err) {
      say(friendly(err), "error");
    } finally {
      btn.disabled = false;
    }
  });

  $("forgotBtn").addEventListener("click", async () => {
    const email = $("email").value.trim();
    if (!/^\S+@\S+\.\S+$/.test(email)) { say("Enter your email above first.", "error"); $("email").focus(); return; }
    try { await EcoAuth.resetPassword(email); say("If that account exists, a reset link is on its way.", "ok"); }
    catch (err) { say(friendly(err), "error"); }
  });

  $("googleBtn").addEventListener("click", async () => {
    try { await EcoAuth.signInWithGoogle(safeNext); } catch (err) { say(friendly(err), "error"); }
  });

  EcoAuth.ready.then(({ enabled, provider: p }) => {
    provider = p;
    if (!enabled) {
      $("notConfigured").hidden = false;
      document.querySelectorAll("#authForm input, #authForm button, .auth-switch button").forEach((el) => { el.disabled = true; });
      return;
    }
    $("googleWrap").hidden = true;
    if (p === "supabase") {
      $("providerNote").lastElementChild.textContent = "Accounts are handled by Supabase Auth. Eco stores only your homes and saved scenarios.";
      // Only offer Google when it is switched on in the Supabase project.
      ecoFetch("/auth/config")
        .then((cfg) => fetch(`${cfg.supabase_url}/auth/v1/settings`, { headers: { apikey: cfg.supabase_publishable_key || cfg.supabase_anon_key } }))
        .then((r) => (r.ok ? r.json() : null))
        .then((s) => { if (s?.external?.google) $("googleWrap").hidden = false; })
        .catch(() => { /* keep it hidden */ });
    }
    setMode(params.get("mode") === "signup" ? "signup" : "signin");
  });

  EcoAuth.onChange((user) => {
    $("signedIn").hidden = !user;
    if (user) $("signedInAs").innerHTML = ` as ${escapeHtml(user.email || "")}. <a href="${safeNext}">Continue to the app</a>`;
  });

  setMode(params.get("mode") === "signup" ? "signup" : "signin");
})();
