/**
 * Project Eco: browser authentication.
 *
 * GET /auth/config tells the page which provider the API uses:
 *   local     email + password accounts on this server (bcrypt + signed JWT). Default.
 *   supabase  Supabase Auth via supabase-js (project URL + publishable key).
 *   none      sign-in turned off; everything except saved scenarios works as a guest.
 *
 * window.EcoAuth:
 *   ready            Promise<{ enabled, provider }>
 *   user()           current user ({ id, email, name }) or null
 *   signIn / signUp / signInWithGoogle / resetPassword / signOut
 *   apiFetch(path)   ecoFetch with the session's bearer token
 *   onChange(cb)     called with the user (or null) whenever auth state changes
 */

const SUPABASE_ESM = "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.117.3/+esm";
const LOCAL_SESSION_KEY = "eco-session";

const EcoAuth = (() => {
  let provider = "none";
  let client = null;          // supabase client
  let localSession = null;    // { access_token, expires_at, user }
  let currentUser = null;
  const listeners = new Set();
  const emit = () => {
    // Cached API data belongs to the previous user; drop it before anyone re-renders.
    if (typeof invalidateEcoQuery === "function") invalidateEcoQuery();
    listeners.forEach((cb) => { try { cb(currentUser); } catch (_) { /* listener error */ } });
  };

  function readLocal() {
    try {
      const s = JSON.parse(localStorage.getItem(LOCAL_SESSION_KEY) || "null");
      if (s && s.access_token && s.expires_at > Date.now()) return s;
    } catch (_) { /* storage blocked */ }
    return null;
  }
  function writeLocal(s) {
    localSession = s;
    currentUser = s ? s.user : null;
    try {
      if (s) localStorage.setItem(LOCAL_SESSION_KEY, JSON.stringify(s));
      else localStorage.removeItem(LOCAL_SESSION_KEY);
    } catch (_) { /* storage blocked: session lasts for this page only */ }
    emit();
  }
  function storeAuthSession(res) {
    writeLocal({ access_token: res.access_token, expires_at: Date.now() + res.expires_in * 1000, user: res.user });
  }

  const ready = (async () => {
    try {
      const cfg = await ecoFetch("/auth/config");
      if (!cfg.enabled) return { enabled: false, provider: "none" };
      provider = cfg.provider;
      if (provider === "local") {
        localSession = readLocal();
        currentUser = localSession ? localSession.user : null;
        if (localSession) {
          // Drop sessions the server no longer accepts (for example a rotated secret).
          ecoFetch("/auth/me", { headers: { Authorization: `Bearer ${localSession.access_token}` } })
            .catch(() => writeLocal(null));
        }
        return { enabled: true, provider };
      }
      const { createClient } = await import(SUPABASE_ESM);
      client = createClient(cfg.supabase_url, cfg.supabase_publishable_key || cfg.supabase_anon_key, {
        auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
      });
      const { data } = await client.auth.getSession();
      currentUser = data.session?.user ?? null;
      client.auth.onAuthStateChange((_event, session) => { currentUser = session?.user ?? null; emit(); });
      return { enabled: true, provider };
    } catch (err) {
      console.warn("Sign-in unavailable:", err);
      return { enabled: false, provider: "none", error: err.message };
    }
  })();

  function requireSupabase() {
    if (!client) throw new Error("This option needs Supabase Auth, which isn't configured on this server.");
    return client;
  }

  async function token() {
    if (provider === "local") return localSession?.access_token ?? null;
    if (!client) return null;
    const { data } = await client.auth.getSession();
    return data.session?.access_token ?? null;
  }

  /* Non-blocking: usable while `ready` is still resolving (ecoFetch calls it for /auth/config). */
  async function getToken() {
    if (provider === "local" || !client) {
      const s = localSession || readLocal();
      return s ? s.access_token : null;
    }
    const { data } = await client.auth.getSession();
    return data.session?.access_token ?? null;
  }

  return {
    ready,
    getToken,
    onUnauthorized() {
      if (!currentUser && !readLocal()) return;
      writeLocal(null);
      currentUser = null;
    },
    async deleteAccount() {
      await ecoFetch("/auth/me", { method: "DELETE" });
      if (provider === "local") writeLocal(null);
      else if (client) await client.auth.signOut();
      currentUser = null;
      emit();
    },
    provider: () => provider,
    user: () => currentUser,
    onChange(cb) { listeners.add(cb); ready.then(() => cb(currentUser)); return () => listeners.delete(cb); },

    async signIn(email, password) {
      await ready;
      if (provider === "local") {
        storeAuthSession(await ecoFetch("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }));
        return;
      }
      const { error } = await requireSupabase().auth.signInWithPassword({ email, password });
      if (error) throw error;
    },
    async signUp(email, password, name) {
      await ready;
      if (provider === "local") {
        storeAuthSession(await ecoFetch("/auth/register", {
          method: "POST", body: JSON.stringify({ email, password, name: name || null }),
        }));
        return { needsConfirmation: false };
      }
      const { data, error } = await requireSupabase().auth.signUp({
        email, password, options: { data: { name }, emailRedirectTo: `${location.origin}${location.pathname}` },
      });
      if (error) throw error;
      return { needsConfirmation: !data.session };
    },
    async signInWithGoogle(next) {
      const { error } = await requireSupabase().auth.signInWithOAuth({
        provider: "google",
        options: { redirectTo: new URL(next || "app.html", location.href).href },
      });
      if (error) throw error;
    },
    async resetPassword(email) {
      const { error } = await requireSupabase().auth.resetPasswordForEmail(email, {
        redirectTo: new URL("login.html", location.href).href,
      });
      if (error) throw error;
    },
    async signOut() {
      if (provider === "local") { writeLocal(null); return; }
      if (client) await client.auth.signOut();
      currentUser = null;
      emit();
    },
    async apiFetch(path, options = {}) {
      const t = await token();
      try {
        return await ecoFetch(path, {
          ...options,
          headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) },
        });
      } catch (err) {
        if (provider === "local" && /session (expired|invalid)/i.test(err.message)) writeLocal(null);
        throw err;
      }
    },
  };
})();

window.EcoAuth = EcoAuth;

function displayName(user) {
  return user?.name || user?.user_metadata?.name || (user?.email || "Account").split("@")[0];
}

/* ---------- Account UI: header slot (marketing pages) and rail card (app) ---------- */
function renderAccountSlot(user) {
  const here = location.pathname.split("/").pop() || "index.html";
  const next = encodeURIComponent(here + location.search + location.hash);

  const rail = document.getElementById("railUser");
  if (rail) {
    rail.innerHTML = user
      ? `<div class="rail-user">
           <span class="avatar" aria-hidden="true">${escapeHtml(displayName(user)[0].toUpperCase())}</span>
           <span class="who"><b>${escapeHtml(displayName(user))}</b><span>${escapeHtml(user.email || "")}</span></span>
           <button class="icon-btn plain" type="button" data-signout aria-label="Sign out">${icon("logout", "icon icon-sm")}</button>
         </div>`
      : `<a class="btn btn-primary btn-sm" style="width:100%" href="login.html?next=${next}">${icon("user", "icon icon-sm")}<span>Sign in</span></a>`;
    rail.querySelector("[data-signout]")?.addEventListener("click", () => EcoAuth.signOut());
  }

  const slot = document.getElementById("accountSlot");
  if (!slot) return;
  if (!user) {
    slot.innerHTML = `<a class="btn btn-outline-light btn-sm" href="login.html?next=${next}">Sign in</a>`;
    return;
  }
  const label = user.email || "Account";
  slot.innerHTML = `
    <div class="account">
      <button class="avatar-btn" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="Account menu for ${escapeHtml(label)}">${escapeHtml(displayName(user)[0].toUpperCase())}</button>
      <div class="account-menu" role="menu">
        <div class="who">Signed in as<strong>${escapeHtml(label)}</strong></div>
        <a role="menuitem" href="app.html">${icon("grid", "icon icon-sm")}Open dashboard</a>
        <a role="menuitem" href="app.html#saved">${icon("bookmark", "icon icon-sm")}My saved scenarios</a>
        <button role="menuitem" type="button" data-signout>${icon("logout", "icon icon-sm")}Sign out</button>
      </div>
    </div>`;
  const btn = slot.querySelector(".avatar-btn");
  const menu = slot.querySelector(".account-menu");
  const setOpen = (open) => { menu.classList.toggle("open", open); btn.setAttribute("aria-expanded", String(open)); };
  btn.addEventListener("click", (e) => { e.stopPropagation(); setOpen(!menu.classList.contains("open")); });
  document.addEventListener("click", (e) => { if (!slot.contains(e.target)) setOpen(false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });
  slot.querySelector("[data-signout]").addEventListener("click", () => EcoAuth.signOut());
}

EcoAuth.onChange(renderAccountSlot);
