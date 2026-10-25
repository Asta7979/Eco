/**
 * Project Eco: account security UI.
 *   - Change password (POST /auth/password): other sessions are signed out by the server.
 *   - Admin console (superusers only, GET/PATCH/DELETE /admin/users): activate, deactivate,
 *     promote, demote and delete accounts. The tab only appears for admins, and every call
 *     is authorised again on the server, so hiding it is convenience, not the protection.
 * Relies on globals from app.js: $, activateTab.
 */

async function revealAdminIfAllowed() {
  try {
    const me = await ecoFetch("/auth/me");
    const isAdmin = !!me.is_superuser;
    $("tab-admin").hidden = !isAdmin;
    if (!isAdmin && document.body.dataset.view === "admin") activateTab("overview");
  } catch (_) { /* signed out: the auth gate handles it */ }
}

async function loadAdmin() {
  const panel = $("adminPanel");
  panel.innerHTML = `<div class="skeleton" style="height:120px;width:100%"></div>`;
  try {
    const [stats, users] = await Promise.all([ecoFetch("/admin/stats"), ecoFetch("/admin/users")]);
    const me = EcoAuth.user();
    panel.innerHTML = `
      <div class="stat-grid" style="margin-bottom:16px">
        ${[["Accounts", stats.users], ["Active", stats.active_users], ["Homes", stats.homes], ["Saved scenarios", stats.saved_scenarios]]
          .map(([k, v]) => `<div class="stat-card"><div class="sc-head"><span>${escapeHtml(k)}</span></div><div class="sc-value">${Number(v)}</div></div>`).join("")}
      </div>
      <div class="table-wrap"><table>
        <thead><tr><th scope="col">Account</th><th scope="col">Role</th><th scope="col">Status</th><th scope="col" class="num">Homes</th><th scope="col">Last sign-in</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
        <tbody>${users.map((u) => {
          const self = me && me.id === u.id;
          return `<tr data-id="${escapeHtml(u.id)}">
            <td><strong>${escapeHtml(u.name || "—")}</strong><div class="help">${escapeHtml(u.email)}</div></td>
            <td>${u.is_superuser ? `<span class="badge badge-info">Admin</span>` : `<span class="badge badge-neutral">User</span>`}</td>
            <td>${u.is_active ? `<span class="badge badge-ok">Active</span>` : `<span class="badge badge-danger">Deactivated</span>`}</td>
            <td class="num">${Number(u.homes)}</td>
            <td class="help">${u.last_login_at ? escapeHtml(new Date(u.last_login_at + "Z").toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })) : "Never"}</td>
            <td class="admin-actions">${self ? `<span class="help">You</span>` : `
              <button class="btn btn-ghost btn-sm" type="button" data-act="${u.is_active ? "deactivate" : "activate"}">${u.is_active ? "Deactivate" : "Activate"}</button>
              <button class="btn btn-ghost btn-sm" type="button" data-act="${u.is_superuser ? "demote" : "promote"}">${u.is_superuser ? "Remove admin" : "Make admin"}</button>
              <button class="btn btn-ghost btn-sm danger" type="button" data-act="delete" aria-label="Delete ${escapeHtml(u.email)}">${icon("trash", "icon icon-sm")}</button>`}
            </td></tr>`;
        }).join("")}</tbody>
      </table></div>`;
    panel.querySelectorAll("[data-act]").forEach((btn) => btn.addEventListener("click", () => adminAction(btn)));
  } catch (err) {
    panel.innerHTML = `<div class="callout callout-danger">${icon("alert")}<div><strong>Admin data unavailable</strong>${escapeHtml(err.message)}</div></div>`;
  }
}

async function adminAction(btn) {
  const id = btn.closest("tr").dataset.id;
  const email = btn.closest("tr").querySelector(".help").textContent;
  const act = btn.dataset.act;
  try {
    if (act === "delete") {
      if (!confirm(`Delete ${email} and all of their homes and scenarios? This can't be undone.`)) return;
      await ecoFetch(`/admin/users/${encodeURIComponent(id)}`, { method: "DELETE" });
    } else {
      const body = { activate: { is_active: true }, deactivate: { is_active: false }, promote: { is_superuser: true }, demote: { is_superuser: false } }[act];
      await ecoFetch(`/admin/users/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) });
    }
    loadAdmin();
  } catch (err) {
    alert(`Couldn't update the account: ${err.message}`);
  }
}

async function changePassword(e) {
  e.preventDefault();
  const form = e.target;
  const msg = $("pwMsg");
  const current = form.current_password.value, next = form.new_password.value;
  msg.className = "form-msg";
  if (next !== form.confirm_password.value) { msg.className = "form-msg error"; msg.textContent = "The new passwords don't match."; return; }
  if (next.length < 8) { msg.className = "form-msg error"; msg.textContent = "Use at least 8 characters."; return; }
  const btn = form.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    await ecoFetch("/auth/password", { method: "POST", body: JSON.stringify({ current_password: current, new_password: next }) });
    msg.className = "form-msg ok";
    msg.textContent = "Password changed. Signing you in again…";
    await EcoAuth.signIn(EcoAuth.user().email, next); // the old session was revoked server-side
    form.reset();
    msg.textContent = "Password changed. Other devices have been signed out.";
  } catch (err) {
    let text = err.message;
    try { text = JSON.parse(err.message).map((d) => d.msg.replace(/^Value error, /, "")).join(" "); } catch (_) { /* plain message */ }
    msg.className = "form-msg error";
    msg.textContent = text;
  } finally {
    btn.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("passwordForm")?.addEventListener("submit", changePassword);
  EcoAuth.ready.then(({ provider }) => {
    if (provider !== "local") $("passwordCard")?.setAttribute("hidden", ""); // Supabase manages its own passwords
    if (EcoAuth.user()) revealAdminIfAllowed();
  });
});
