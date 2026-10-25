/**
 * Project Eco: shadcn-style dashboard layer for app.html.
 *   - Overview: stat cards + three charts (inline SVG, hover tooltips, click-through)
 *   - Portfolio: a TanStack-style data table (global filter, sorting, column
 *     visibility, pagination, row click)
 *   - Command palette (Ctrl/⌘ K) to jump to any page or home
 * Every figure comes from GET /properties/ranking; this file only aggregates and draws.
 * Relies on globals from app.js: state, $, activateTab, openHome.
 */

const BAND_ORDER = ["Very Poor", "Poor", "Moderate", "Good", "Highly Efficient"];
let rankingPromise = null;

function resetPortfolioData() {
  state.ranking = null;
  rankingPromise = null;
  invalidateEcoQuery("/properties/ranking");
  const host = document.getElementById("portfolioTable");
  if (host?.dataset.ready && state.ranking === null && document.body.dataset.view === "portfolio") renderPortfolioTable();
}

function ensureRanking() {
  if (state.ranking) return Promise.resolve(state.ranking);
  rankingPromise ??= ecoQuery("/properties/ranking").then((rows) => (state.ranking = rows))
    .catch((err) => { rankingPromise = null; throw err; });
  return rankingPromise;
}

/* ======================= Tooltip ======================= */
const tip = (() => {
  const el = document.createElement("div");
  el.className = "viz-tip";
  el.setAttribute("role", "tooltip");
  el.hidden = true;
  document.addEventListener("DOMContentLoaded", () => document.body.appendChild(el));
  return {
    show(html, x, y) {
      el.innerHTML = html;
      el.hidden = false;
      const r = el.getBoundingClientRect();
      const left = Math.min(x + 14, innerWidth - r.width - 8);
      const top = y + 14 + r.height > innerHeight ? y - r.height - 10 : y + 14;
      el.style.transform = `translate(${Math.max(8, left)}px, ${top}px)`;
    },
    hide() { el.hidden = true; },
  };
})();

function bindTip(node, html) {
  node.addEventListener("pointermove", (e) => tip.show(html(), e.clientX, e.clientY));
  node.addEventListener("pointerleave", () => tip.hide());
  node.addEventListener("focus", () => {
    const r = node.getBoundingClientRect();
    tip.show(html(), r.left + r.width / 2, r.top);
  });
  node.addEventListener("blur", () => tip.hide());
}

const svgNS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs = {}, parent) {
  const el = document.createElementNS(svgNS, tag);
  Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
  if (parent) parent.appendChild(el);
  return el;
}

/* Bar with a 4px rounded data-end and a square baseline end. */
function barPath(x, y, w, h, dir) {
  const r = Math.min(4, dir === "right" ? w : h, dir === "right" ? h / 2 : w / 2);
  if (w <= 0 || h <= 0) return "";
  if (dir === "right") {
    return `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h - r}Q${x + w},${y + h} ${x + w - r},${y + h}H${x}Z`;
  }
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/* ======================= Overview ======================= */
async function renderOverview() {
  try {
    const rows = await ensureRanking();
    if (!rows.length) return; // the first-run empty state is shown instead
    renderStats(rows);
    drawAll(rows);
  } catch (err) {
    $("ovStats").innerHTML = `<div class="callout callout-danger" style="grid-column:1/-1">${icon("alert")}<div><strong>Overview unavailable</strong>${escapeHtml(err.message)}</div></div>`;
  }
}

function drawAll(rows) {
  drawDumbbell($("chartDumbbell"), rows);
  drawBands($("chartBands"), rows);
  drawPotential($("chartPotential"), rows);
}

function renderStats(rows) {
  const n = rows.length;
  const avg = (k) => rows.reduce((s, r) => s + r[k], 0) / Math.max(n, 1);
  const flagged = rows.filter((r) => r.flagged_for_intervention).length;
  const withBase = rows.filter((r) => r.baseline_energy_kwh != null && r.after_energy_kwh != null);
  const cut = withBase.reduce((s, r) => s + (r.baseline_energy_kwh - r.after_energy_kwh), 0);
  const base = withBase.reduce((s, r) => s + r.baseline_energy_kwh, 0);
  const today = avg("green_home_score"), after = avg("projected_score");
  const card = (label, ic, value, sub, tone = "") => `
    <div class="stat-card">
      <div class="sc-head"><span>${label}</span>${icon(ic, "icon icon-sm")}</div>
      <div class="sc-value">${value}</div>
      <div class="sc-sub ${tone}">${sub}</div>
    </div>`;
  $("ovStats").innerHTML =
    card("Homes assessed", "home", n, `${rows.filter((r) => r.improvement_potential_pct != null).length} with metered baselines`) +
    card("Average score", "trend", `${Math.round(today)} <small>→ ${Math.round(after)}</small>`, `+${Math.round(after - today)} points under the whole-home plan`, "pos") +
    card("Flagged for retrofit", "alert", flagged, `${Math.round((100 * flagged) / Math.max(n, 1))}% of homes are Poor or Very Poor`, flagged ? "neg" : "") +
    card("Purchased energy cut", "zap", `${fmt.int(cut / 1000)} <small>MWh / yr</small>`, base ? `${((100 * cut) / base).toFixed(1)}% of the portfolio baseline` : "No metered baselines", "pos");
}

function drawDumbbell(host, rows) {
  const data = [...rows].sort((a, b) => a.green_home_score - b.green_home_score);
  const W = Math.max(host.clientWidth, 320);
  const labelW = W < 520 ? 110 : 190;
  const rowH = 26, top = 8, bottom = 26;
  const H = top + data.length * rowH + bottom;
  const plotW = W - labelW - 16;
  const x = (v) => labelW + (v / 100) * plotW;

  host.innerHTML = "";
  const svg = svgEl("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": `Green Home Score today and after the whole-home plan for ${data.length} homes` }, host);

  [0, 25, 50, 75, 100].forEach((t) => {
    svgEl("line", { x1: x(t), x2: x(t), y1: top, y2: H - bottom, class: "grid" }, svg);
    const tl = svgEl("text", { x: x(t), y: H - 8, class: "tick", "text-anchor": "middle" }, svg);
    tl.textContent = t;
  });

  data.forEach((r, i) => {
    const cy = top + i * rowH + rowH / 2;
    const g = svgEl("g", { class: "row", tabindex: 0, role: "button", "aria-label": `${r.address}: ${r.green_home_score} today, ${r.projected_score} after the plan` }, svg);
    svgEl("rect", { x: 0, y: cy - rowH / 2, width: W, height: rowH, class: "hit" }, g);
    const lab = svgEl("text", { x: labelW - 12, y: cy + 4, class: "label", "text-anchor": "end" }, g);
    const maxChars = labelW < 150 ? 14 : 26;
    lab.textContent = r.address.length > maxChars ? `${r.address.slice(0, maxChars - 1)}…` : r.address;
    svgEl("line", { x1: x(r.green_home_score), x2: x(r.projected_score), y1: cy, y2: cy, class: "link" }, g);
    svgEl("circle", { cx: x(r.green_home_score), cy, r: 5, class: "dot today" }, g);
    svgEl("circle", { cx: x(r.projected_score), cy, r: 5, class: "dot after" }, g);
    bindTip(g, () => `
      <b>${escapeHtml(r.address)}</b>
      <div class="tip-row"><i style="background:var(--series-today)"></i>Today<span>${r.green_home_score} · ${escapeHtml(r.band)}</span></div>
      <div class="tip-row"><i style="background:var(--series-after)"></i>After plan<span>${r.projected_score}</span></div>
      <div class="tip-foot">+${r.projected_score - r.green_home_score} points · click to open</div>`);
    g.addEventListener("click", () => openHome(r.property_id));
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openHome(r.property_id); } });
  });
}

function drawBands(host, rows) {
  const counts = BAND_ORDER.map((b) => ({ band: b, n: rows.filter((r) => r.band === b).length }));
  const W = Math.max(host.clientWidth, 260), H = 230;
  const top = 22, bottom = 40, left = 8;
  const max = Math.max(...counts.map((c) => c.n), 1);
  const slot = (W - left * 2) / counts.length;
  const bw = Math.min(46, slot - 12);
  const y = (v) => top + (1 - v / max) * (H - top - bottom);

  host.innerHTML = "";
  const svg = svgEl("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Number of homes in each band today" }, host);
  svgEl("line", { x1: left, x2: W - left, y1: H - bottom, y2: H - bottom, class: "axis" }, svg);

  counts.forEach((c, i) => {
    const cx = left + slot * i + slot / 2;
    const g = svgEl("g", { class: "row", tabindex: 0, "aria-label": `${c.band}: ${c.n} homes` }, svg);
    svgEl("rect", { x: cx - slot / 2, y: top, width: slot, height: H - top - bottom, class: "hit" }, g);
    const flagged = c.band === "Poor" || c.band === "Very Poor";
    svgEl("path", { d: barPath(cx - bw / 2, y(c.n), bw, H - bottom - y(c.n), "up"), class: `bar${flagged ? " flagged" : ""}` }, g);
    const v = svgEl("text", { x: cx, y: y(c.n) - 6, class: "value", "text-anchor": "middle" }, g);
    v.textContent = c.n;
    const l = svgEl("text", { x: cx, y: H - bottom + 16, class: "tick", "text-anchor": "middle" }, g);
    const short = slot < 72 ? { "Very Poor": "V. Poor", "Moderate": "Mod.", "Highly Efficient": "High" } : { "Highly Efficient": "High" };
    l.textContent = `${flagged && slot >= 72 ? "⚠ " : ""}${short[c.band] || c.band}`;
    bindTip(g, () => `<b>${escapeHtml(c.band)}</b><div class="tip-row">Homes<span>${c.n}</span></div>${flagged ? '<div class="tip-foot">⚠ Flagged for intervention</div>' : ""}`);
  });
  const note = svgEl("text", { x: left, y: H - 6, class: "tick" }, svg);
  note.textContent = "⚠ Flagged bands shown in red";
}

function drawPotential(host, rows) {
  const data = rows.filter((r) => r.improvement_potential_pct != null)
    .sort((a, b) => b.improvement_potential_pct - a.improvement_potential_pct).slice(0, 8);
  const W = Math.max(host.clientWidth, 260);
  const labelW = W < 420 ? 100 : 150, rowH = 26, top = 4, right = 52;
  const H = top + data.length * rowH + 6;
  const max = Math.max(...data.map((r) => r.improvement_potential_pct), 1);
  const plotW = W - labelW - right;

  host.innerHTML = "";
  const svg = svgEl("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Top homes by improvement potential" }, host);
  data.forEach((r, i) => {
    const y = top + i * rowH;
    const g = svgEl("g", { class: "row", tabindex: 0, role: "button", "aria-label": `${r.address}: ${r.improvement_potential_pct.toFixed(1)}%` }, svg);
    svgEl("rect", { x: 0, y, width: W, height: rowH, class: "hit" }, g);
    const lab = svgEl("text", { x: labelW - 10, y: y + rowH / 2 + 4, class: "label", "text-anchor": "end" }, g);
    const maxChars = labelW < 120 ? 12 : 20;
    lab.textContent = r.address.length > maxChars ? `${r.address.slice(0, maxChars - 1)}…` : r.address;
    const w = (r.improvement_potential_pct / max) * plotW;
    svgEl("path", { d: barPath(labelW, y + 6, w, rowH - 12, "right"), class: "bar" }, g);
    const v = svgEl("text", { x: labelW + w + 6, y: y + rowH / 2 + 4, class: "value" }, g);
    v.textContent = `${r.improvement_potential_pct.toFixed(0)}%`;
    bindTip(g, () => `
      <b>${escapeHtml(r.address)}</b>
      <div class="tip-row">Energy cut<span>${r.improvement_potential_pct.toFixed(1)}%</span></div>
      <div class="tip-row">Gas<span>${fmt.signed(r.delta_gas_kwh, " kWh")}</span></div>
      <div class="tip-row">Electricity<span>${fmt.signed(r.delta_electricity_kwh, " kWh")}</span></div>
      <div class="tip-foot">Click to open</div>`);
    g.addEventListener("click", () => openHome(r.property_id));
    g.addEventListener("keydown", (e) => { if (e.key === "Enter") openHome(r.property_id); });
  });
}

/* ======================= Data table ======================= */
const table = {
  query: "",
  flaggedOnly: false,
  sort: { key: "rank", dir: "asc" },
  page: 0,
  pageSize: 10,
  hidden: new Set(["drivers"]),
};

/* Table state lives in the URL (nuqs pattern), so filters, sort and page survive a
   reload and can be shared as a link. replaceState keeps history clean. */
function readTableUrl() {
  const p = new URLSearchParams(location.search);
  table.query = (p.get("tq") || "").toLowerCase();
  table.flaggedOnly = p.get("tflag") === "1";
  const [key, dir] = (p.get("tsort") || "rank.asc").split(".");
  if (COLUMNS_KEYS().includes(key)) table.sort = { key, dir: dir === "desc" ? "desc" : "asc" };
  table.page = Math.max(0, (parseInt(p.get("tpage"), 10) || 1) - 1);
}

function writeTableUrl() {
  const url = new URL(location.href);
  const set = (k, v, dflt) => (v === dflt ? url.searchParams.delete(k) : url.searchParams.set(k, v));
  set("tq", table.query, "");
  set("tflag", table.flaggedOnly ? "1" : "0", "0");
  set("tsort", `${table.sort.key}.${table.sort.dir}`, "rank.asc");
  set("tpage", String(table.page + 1), "1");
  history.replaceState(null, "", url);
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

const COLUMNS_KEYS = () => COLUMNS.map((c) => c.key);
const COLUMNS = [
  { key: "rank", label: "#", num: true, value: (r) => r.rank, cell: (r) => r.rank, hideable: false },
  { key: "address", label: "Home", value: (r) => r.address, hideable: false,
    cell: (r) => `<strong>${escapeHtml(r.address)}</strong><div class="help">${escapeHtml(r.property_type)} · ${escapeHtml(r.region)}</div>` },
  { key: "green_home_score", label: "Score today", num: true, value: (r) => r.green_home_score,
    cell: (r) => `<span class="score-pill ${bandClass(r.band)}">${r.green_home_score}</span>` },
  { key: "band", label: "Band", value: (r) => BAND_ORDER.indexOf(r.band),
    cell: (r) => r.flagged_for_intervention ? `<span class="badge badge-danger">${icon("alert", "icon icon-sm")}${escapeHtml(r.band)}</span>` : `<span class="badge badge-neutral">${escapeHtml(r.band)}</span>` },
  { key: "drivers", label: "Main drivers", value: (r) => r.drivers.length,
    cell: (r) => `<span class="help">${r.drivers.length ? r.drivers.map(escapeHtml).join(", ") : "None"}</span>` },
  { key: "improvement_potential_pct", label: "Potential", num: true, value: (r) => r.improvement_potential_pct ?? -1,
    cell: (r) => r.improvement_potential_pct == null ? '<span class="help">Unavailable</span>'
      : `<span class="mini-bar"><i style="width:${Math.min(100, r.improvement_potential_pct)}%"></i></span><strong>${r.improvement_potential_pct.toFixed(1)}%</strong>` },
  { key: "delta_gas_kwh", label: "Gas Δ kWh", num: true, value: (r) => r.delta_gas_kwh,
    cell: (r) => `<span class="${r.delta_gas_kwh < 0 ? "pos" : ""}">${fmt.signed(r.delta_gas_kwh)}</span>` },
  { key: "delta_electricity_kwh", label: "Elec Δ kWh", num: true, value: (r) => r.delta_electricity_kwh,
    cell: (r) => `<span class="${r.delta_electricity_kwh > 0 ? "neg" : r.delta_electricity_kwh < 0 ? "pos" : ""}">${fmt.signed(r.delta_electricity_kwh)}</span>` },
  { key: "projected_score", label: "Score after", num: true, value: (r) => r.projected_score,
    cell: (r) => `<span class="score-pill ${bandClass(bandForScore(r.projected_score))}">${r.projected_score}</span>` },
];

async function renderPortfolioTable() {
  const host = $("portfolioTable");
  if (!host.dataset.ready) {
    host.dataset.ready = "1";
    readTableUrl();
    host.innerHTML = `
      <div class="dt-toolbar">
        <div class="search-box" style="flex:1;max-width:340px">
          <label for="dtSearch" class="sr-only">Filter homes</label>
          <input class="input" id="dtSearch" type="search" placeholder="Filter homes, regions, drivers…" autocomplete="off">
          ${icon("search", "icon icon-sm")}
        </div>
        <label class="toggle"><input type="checkbox" id="dtFlagged"> Flagged only</label>
        <div class="dt-cols">
          <button class="btn btn-secondary btn-sm" type="button" id="dtColsBtn" aria-haspopup="true" aria-expanded="false">${icon("layers", "icon icon-sm")}Columns</button>
          <div class="dt-menu" id="dtColsMenu" hidden>
            ${COLUMNS.filter((c) => c.hideable !== false).map((c) => `<label><input type="checkbox" data-col="${c.key}" ${table.hidden.has(c.key) ? "" : "checked"}> ${c.label}</label>`).join("")}
          </div>
        </div>
      </div>
      <div class="table-wrap"><table class="dt"><thead><tr id="dtHead"></tr></thead><tbody id="dtBody"></tbody></table></div>
      <div class="dt-footer"><span class="help" id="dtCount"></span>
        <div class="dt-pager">
          <span class="help" id="dtPage"></span>
          <button class="icon-btn" type="button" id="dtPrev" aria-label="Previous page">${icon("arrow", "icon icon-sm flip")}</button>
          <button class="icon-btn" type="button" id="dtNext" aria-label="Next page">${icon("arrow", "icon icon-sm")}</button>
        </div>
      </div>`;
    $("dtSearch").value = table.query;
    $("dtFlagged").checked = table.flaggedOnly;
    $("dtSearch").addEventListener("input", debounce((e) => { table.query = e.target.value.trim().toLowerCase(); table.page = 0; paintTable(); }, 200));
    $("dtFlagged").addEventListener("change", (e) => { table.flaggedOnly = e.target.checked; table.page = 0; paintTable(); });
    $("dtPrev").addEventListener("click", () => { table.page--; paintTable(); });
    $("dtNext").addEventListener("click", () => { table.page++; paintTable(); });
    const btn = $("dtColsBtn"), menu = $("dtColsMenu");
    btn.addEventListener("click", (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; btn.setAttribute("aria-expanded", String(!menu.hidden)); });
    document.addEventListener("click", (e) => { if (!menu.contains(e.target)) { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); } });
    menu.querySelectorAll("input").forEach((cb) => cb.addEventListener("change", () => {
      if (cb.checked) table.hidden.delete(cb.dataset.col); else table.hidden.add(cb.dataset.col);
      paintTable();
    }));
  }
  try {
    await ensureRanking();
    paintTable();
  } catch (err) {
    $("dtBody").innerHTML = `<tr><td colspan="9" class="muted">Ranking unavailable: ${escapeHtml(err.message)}</td></tr>`;
  }
}

function paintTable() {
  const cols = COLUMNS.filter((c) => !table.hidden.has(c.key));
  const q = table.query;
  let rows = state.ranking.filter((r) => (!table.flaggedOnly || r.flagged_for_intervention) &&
    (!q || [r.address, r.region, r.property_type, r.band, ...r.drivers].join(" ").toLowerCase().includes(q)));
  const col = COLUMNS.find((c) => c.key === table.sort.key);
  const dir = table.sort.dir === "asc" ? 1 : -1;
  rows = [...rows].sort((a, b) => {
    const va = col.value(a), vb = col.value(b);
    return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * dir;
  });
  const pages = Math.max(1, Math.ceil(rows.length / table.pageSize));
  table.page = Math.min(Math.max(0, table.page), pages - 1);
  const view = rows.slice(table.page * table.pageSize, (table.page + 1) * table.pageSize);

  $("dtHead").innerHTML = cols.map((c) => {
    const active = table.sort.key === c.key;
    const aria = active ? (table.sort.dir === "asc" ? "ascending" : "descending") : "none";
    return `<th scope="col" class="${c.num ? "num" : ""}" aria-sort="${aria}"><button type="button" class="th-sort" data-key="${c.key}">${c.label}<span class="sort-ind" aria-hidden="true">${active ? (table.sort.dir === "asc" ? "↑" : "↓") : "↕"}</span></button></th>`;
  }).join("");
  $("dtHead").querySelectorAll(".th-sort").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.key;
    table.sort = table.sort.key === k ? { key: k, dir: table.sort.dir === "asc" ? "desc" : "asc" } : { key: k, dir: COLUMNS.find((c) => c.key === k).num ? "desc" : "asc" };
    paintTable();
  }));

  $("dtBody").innerHTML = view.length
    ? view.map((r) => `<tr class="clickable" tabindex="0" data-id="${escapeHtml(r.property_id)}" ${r.property_id === state.currentId ? 'aria-selected="true"' : ""}>${cols.map((c) => `<td class="${c.num ? "num" : ""}">${c.cell(r)}</td>`).join("")}</tr>`).join("")
    : `<tr><td colspan="${cols.length}"><div class="empty" style="border:0">No homes match these filters.</div></td></tr>`;
  $("dtBody").querySelectorAll("tr[data-id]").forEach((tr) => {
    tr.addEventListener("click", () => openHome(tr.dataset.id));
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openHome(tr.dataset.id); } });
  });
  $("dtCount").textContent = `${rows.length} of ${state.ranking.length} homes`;
  $("dtPage").textContent = `Page ${table.page + 1} of ${pages}`;
  writeTableUrl();
  $("dtPrev").disabled = table.page === 0;
  $("dtNext").disabled = table.page >= pages - 1;
}

/* ======================= Command palette ======================= */
const PAGES = [
  ["overview", "Overview", "grid"], ["assessment", "Assessment", "home"], ["vision", "Vision AI", "scan"],
  ["advisor", "AI advisor", "chat"], ["portfolio", "Portfolio table", "list"], ["saved", "Saved scenarios", "bookmark"],
  ["admin", "Admin", "shield"],
];

function setupCommandPalette() {
  const dlg = document.createElement("dialog");
  dlg.className = "cmdk";
  dlg.setAttribute("aria-label", "Command palette");
  dlg.innerHTML = `
    <div class="cmdk-input">${icon("search", "icon icon-sm")}<input type="text" placeholder="Type a page or a home…" aria-label="Search" autocomplete="off"><kbd>Esc</kbd></div>
    <div class="cmdk-list" role="listbox"></div>`;
  document.body.appendChild(dlg);
  const input = dlg.querySelector("input"), list = dlg.querySelector(".cmdk-list");
  let items = [], active = 0;

  const paint = () => {
    const q = input.value.trim().toLowerCase();
    const pages = PAGES.filter(([, n]) => !q || n.toLowerCase().includes(q)).map(([id, n, ic]) => ({ group: "Pages", label: n, ic, run: () => activateTab(id) }));
    const homes = state.properties.filter((p) => !q || [p.address, p.postcode, p.region, p.property_type].join(" ").toLowerCase().includes(q))
      .slice(0, 8).map((p) => ({ group: "Homes", label: p.address, sub: `${p.region} · score ${p.green_home_score}`, ic: "home", run: () => openHome(p.property_id) }));
    items = [...pages, ...homes];
    active = Math.min(active, Math.max(0, items.length - 1));
    let last = "";
    list.innerHTML = items.length ? items.map((it, i) => {
      const head = it.group !== last ? `<div class="cmdk-group">${it.group}</div>` : "";
      last = it.group;
      return `${head}<div class="cmdk-item" role="option" data-i="${i}" aria-selected="${i === active}">${icon(it.ic, "icon icon-sm")}<span>${escapeHtml(it.label)}</span>${it.sub ? `<small>${escapeHtml(it.sub)}</small>` : ""}</div>`;
    }).join("") : `<div class="cmdk-empty">No results.</div>`;
    list.querySelectorAll(".cmdk-item").forEach((el) => {
      el.addEventListener("mousemove", () => { if (active !== +el.dataset.i) { active = +el.dataset.i; paint(); } });
      el.addEventListener("click", () => choose(+el.dataset.i));
    });
    list.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  };
  const choose = (i) => { const it = items[i]; if (!it) return; dlg.close(); it.run(); };
  const open = () => { input.value = ""; active = 0; paint(); dlg.showModal(); input.focus(); };

  input.addEventListener("input", () => { active = 0; paint(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { active = (active + 1) % Math.max(items.length, 1); paint(); e.preventDefault(); }
    if (e.key === "ArrowUp") { active = (active - 1 + items.length) % Math.max(items.length, 1); paint(); e.preventDefault(); }
    if (e.key === "Enter") { choose(active); e.preventDefault(); }
  });
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); dlg.open ? dlg.close() : open(); }
  });
  $("cmdkOpen")?.addEventListener("click", open);
  if (/Mac|iPhone|iPad/.test(navigator.platform)) document.querySelectorAll(".cmdk-trigger kbd").forEach((k) => { k.textContent = "⌘K"; });
}

/* ======================= Boot ======================= */
document.addEventListener("DOMContentLoaded", () => {
  setupCommandPalette();
  document.querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => activateTab(b.dataset.goto)));

  const crumbNames = Object.fromEntries(PAGES.map(([id, n]) => [id, n]));
  const paintCrumb = () => { $("crumbHere").textContent = crumbNames[document.body.dataset.view] || "Overview"; };
  new MutationObserver(paintCrumb).observe(document.body, { attributes: true, attributeFilter: ["data-view"] });
  paintCrumb();

  let t;
  addEventListener("resize", () => {
    clearTimeout(t);
    t = setTimeout(() => { if (state.ranking && document.body.dataset.view === "overview") drawAll(state.ranking); }, 120);
  });
});
