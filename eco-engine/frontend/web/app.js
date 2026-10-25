/**
 * Project Eco: assessment app controller.
 * All figures come from the FastAPI deterministic engines; this file only renders them.
 */

const PRESETS = {
  fabric: ["loft_insulation", "wall_insulation", "floor_insulation", "window_upgrade", "draught_proofing"],
  heatpump: ["heat_pump"],
  solar: ["solar_pv", "battery_storage"],
  whole: [
    "wall_insulation", "loft_insulation", "floor_insulation", "window_upgrade",
    "draught_proofing", "smart_controls", "heat_pump", "solar_pv", "battery_storage",
  ],
};

const state = {
  properties: [],
  catalogue: [],
  currentId: null,
  selected: new Set(PRESETS.heatpump),
  result: null,
  ranking: null,
  evalController: null,
  evalTimer: null,
};

const $ = (id) => document.getElementById(id);

/* ======================= Boot ======================= */
document.addEventListener("DOMContentLoaded", async () => {
  setupTabs();
  setupSidebar();
  setupPlanner();
  setupChat();
  $("printBtn").addEventListener("click", () => window.print());
  $("saveScenarioBtn").addEventListener("click", saveScenario);

  // The workspace holds the user's own homes, so it needs an account.
  const { enabled } = await EcoAuth.ready;
  if (!enabled) {
    showError(new Error("Sign-in is turned off on this server, so personal workspaces are unavailable."));
    document.documentElement.classList.remove("auth-pending");
    return;
  }
  if (!EcoAuth.user()) { location.replace(loginHref()); return; }
  document.documentElement.classList.remove("auth-pending");
  EcoAuth.onChange((user) => {
    if (!user) { location.replace(loginHref()); return; }
    if (!$("panel-saved").hidden) loadSaved();
  });

  try {
    const [properties, catalogue] = await Promise.all([ecoQuery("/properties"), ecoQuery("/catalogue", { staleMs: 600_000 })]);
    state.properties = properties;
    state.catalogue = catalogue;
  } catch (err) {
    showError(err);
    return;
  }
  applyEmptyState();
  if (!state.properties.length) {
    renderPropertyList();
    greetAdvisor();
    return;
  }

  const params = new URLSearchParams(location.search);
  if (params.get("q")) $("propSearch").value = params.get("q");
  if (PRESETS[params.get("preset")]) state.selected = new Set(PRESETS[params.get("preset")]);
  const fromUrl = params.get("property");
  const q = $("propSearch").value.trim().toLowerCase();
  const searchHit = q && state.properties.find((p) =>
    [p.address, p.postcode, p.region, p.property_type, p.property_id].join(" ").toLowerCase().includes(q));
  const initial = state.properties.find((p) => p.property_id === fromUrl) || searchHit || state.properties[0];
  renderPropertyList();
  selectProperty(initial.property_id, { focusList: false });
  greetAdvisor();
});

function showError(err) {
  $("errorBanner").classList.add("show");
  if (err && err.message && !/Failed to fetch|NetworkError|Load failed/i.test(err.message)) {
    $("errorText").textContent = err.message;
  }
  $("propTitle").textContent = "Engine offline";
}

function clearError() { $("errorBanner").classList.remove("show"); }

/* ======================= Tabs (ARIA, hash deep links) ======================= */
const TABS = ["overview", "assessment", "vision", "advisor", "portfolio", "saved", "admin"];

function activateTab(name, { focus = false, updateHash = true } = {}) {
  if (!TABS.includes(name)) name = "overview";
  document.body.dataset.view = name;
  TABS.forEach((t) => {
    const tab = $(`tab-${t}`);
    const on = t === name;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
    $(`panel-${t}`).hidden = !on;
    document.querySelectorAll(`[data-tab-link="${t}"]`).forEach((a) => {
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  });
  if (focus) $(`tab-${name}`).focus();
  if (updateHash && location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  if (name === "overview") renderOverview();
  if (name === "portfolio") renderPortfolioTable();
  if (name === "admin") { if ($("tab-admin").hidden) { activateTab("overview"); return; } loadAdmin(); }
  if (name === "saved") loadSaved();
}

function setupTabs() {
  TABS.forEach((t, i) => {
    const tab = $(`tab-${t}`);
    tab.addEventListener("click", () => activateTab(t));
    tab.addEventListener("keydown", (e) => {
      const fwd = e.key === "ArrowDown" || e.key === "ArrowRight";
      const back = e.key === "ArrowUp" || e.key === "ArrowLeft";
      if (fwd || back) {
        const next = TABS[(i + (fwd ? 1 : TABS.length - 1)) % TABS.length];
        activateTab(next, { focus: true });
        e.preventDefault();
      }
    });
  });
  window.addEventListener("hashchange", () => activateTab(location.hash.slice(1), { updateHash: false }));
  const params = new URLSearchParams(location.search);
  const fallback = params.has("property") || params.has("preset") || params.has("q") ? "assessment" : "overview";
  activateTab(location.hash.slice(1) || fallback, { updateHash: false });
}

/* ======================= Sidebar ======================= */
function setupSidebar() {
  $("propSearch").addEventListener("input", renderPropertyList);
  $("flaggedOnly").addEventListener("change", renderPropertyList);
}

function renderPropertyList() {
  const q = $("propSearch").value.trim().toLowerCase();
  const flaggedOnly = $("flaggedOnly").checked;
  const items = state.properties.filter((p) => {
    if (flaggedOnly && !p.flagged_for_intervention) return false;
    if (!q) return true;
    return [p.address, p.postcode, p.region, p.property_type, p.property_id].join(" ").toLowerCase().includes(q);
  });

  $("propCount").textContent = state.properties.length ? `${items.length} of ${state.properties.length}` : "";
  $("propertyList").innerHTML = items.length
    ? items.map((p) => `
      <li>
        <button type="button" class="property-option" data-id="${escapeHtml(p.property_id)}" ${p.property_id === state.currentId ? 'aria-current="true"' : ""}>
          <span class="p-addr">${escapeHtml(p.address)}</span>
          <span class="p-meta">${escapeHtml(p.property_type)} · ${escapeHtml(p.region)} · EPC ${escapeHtml(p.current_epc_rating)}</span>
          <span class="p-score ${bandClass(p.band)}" title="Green Home Score: ${p.green_home_score}, ${escapeHtml(p.band)}">${p.green_home_score}</span>
        </button>
      </li>`).join("")
    : state.properties.length
      ? `<li class="empty">No homes match.</li>`
      : `<li class="empty">No homes yet.<br><button class="btn btn-primary btn-sm" type="button" data-add-home style="margin-top:10px">${icon("plus", "icon icon-sm")}Add your first home</button></li>`;
  $("propertyList").querySelector("[data-add-home]")?.addEventListener("click", () => openHomeDialog());

  $("propertyList").querySelectorAll(".property-option").forEach((btn) =>
    btn.addEventListener("click", () => selectProperty(btn.dataset.id)));
}

async function selectProperty(id) {
  state.currentId = id;
  renderPropertyList();
  const url = new URL(location.href);
  url.searchParams.set("property", id);
  history.replaceState(null, "", url);

  const summary = state.properties.find((p) => p.property_id === id);
  $("propEyebrow").textContent = `${id} · ${summary.region}`;
  $("propTitle").textContent = summary.address;
  $("propSub").textContent = `${summary.property_type} · ${summary.postcode} · EPC ${summary.current_epc_rating} (${summary.current_epc_score}) · ${fmt.gbp(summary.baseline_value_gbp)}`;

  try {
    const detail = await ecoQuery(`/properties/${id}`);
    if (state.currentId !== id) return;
    renderSpecs(detail);
  } catch (err) {
    if (state.currentId !== id) return;
    showError(err);
  }
  loadEpcPrediction(id);

  scheduleEvaluation(0);
}

async function loadEpcPrediction(id) {
  const panel = $("epcPanel");
  panel.innerHTML = `<div class="skeleton" style="height:96px;width:100%"></div>`;
  try {
    const r = await ecoQuery(`/properties/${id}/epc-prediction`, { staleMs: 600_000 });
    if (state.currentId !== id) return;
    if (!r.available) { panel.innerHTML = `<div class="empty">EPC model unavailable: ${escapeHtml(r.reason)}</div>`; return; }
    const bands = Object.entries(r.band_probabilities);
    const gained = r.bands_gained;
    panel.innerHTML = `
      <div class="epc-grid">
        <div class="epc-headline">
          <span class="epc-chip epc-${r.current_band}">${r.current_band}</span>
          ${icon("arrow", "icon")}
          <span class="epc-chip epc-${r.potential_band}">${r.potential_band}</span>
          <div><strong>${gained > 0 ? `+${gained} band${gained > 1 ? "s" : ""} potential` : "Already at its potential"}</strong>
          <div class="help">${Math.round(r.confidence * 100)}% model confidence · reported accuracy ${Math.round(r.model.reported_accuracy * 100)}%</div></div>
        </div>
        <div class="epc-bars" role="img" aria-label="Probability of each potential band: ${bands.map(([b, p]) => `${b} ${Math.round(p * 100)}%`).join(", ")}">
          ${bands.map(([b, p]) => `<div class="epc-bar ${b === r.potential_band ? "on" : ""}" title="${b}: ${(p * 100).toFixed(1)}%"><i style="height:${Math.max(2, p * 100)}%"></i><span>${b}</span></div>`).join("")}
        </div>
      </div>
      <details class="epc-inputs"><summary>${r.assumed_inputs.length} of 15 inputs were estimated or assumed: see all inputs</summary>
        <dl class="spec-list">${Object.entries(r.inputs).map(([k, val]) => `<dt>${escapeHtml(k.replaceAll("_", " ").toLowerCase())}</dt><dd>${escapeHtml(val)} <span class="help">· ${escapeHtml(r.input_sources[k])}</span></dd>`).join("")}</dl>
        <p class="help" style="margin-top:8px">Source: ${escapeHtml(r.model.source)}. A second opinion, not an official EPC.</p>
      </details>`;
  } catch (err) {
    panel.innerHTML = `<div class="empty">EPC prediction unavailable: ${escapeHtml(err.message)}</div>`;
  }
}

function renderSpecs(p) {
  const rows = [
    ["Built", p.built_year],
    ["Floor area", `${p.total_floor_area_m2} m²`],
    ["Walls", `${p.wall_type} (U ${p.wall_u_value})`],
    ["Loft", `${p.roof_insulation_depth_mm} mm`],
    ["Floor", p.floor_insulation_status],
    ["Glazing", p.glazing_type],
    ["Heating", p.heating_type],
    ["Gas / yr", fmt.kwh(p.annual_gas_kwh)],
    ["Electricity / yr", fmt.kwh(p.annual_electricity_kwh)],
    ["Solar · battery", `${p.solar_pv_kwp} kWp · ${p.battery_storage_kwh} kWh`],
    ["Smart controls", p.smart_controls ? "Yes" : "No"],
  ];
  $("propertySpecs").innerHTML = `
    <div class="spec-actions">
      <button class="btn btn-secondary btn-sm" type="button" data-edit-home>${icon("doc", "icon icon-sm")}Edit</button>
      <button class="btn btn-ghost btn-sm danger" type="button" data-delete-home>${icon("trash", "icon icon-sm")}Delete</button>
    </div>
    <details open>
      <summary style="cursor:pointer;font-weight:600;font-size:.875rem;min-height:32px">Building details</summary>
      <dl class="spec-list" style="margin-top:8px">
        ${rows.map(([k, v]) => `<dt>${k}</dt><dd>${escapeHtml(v)}</dd>`).join("")}
      </dl>
    </details>`;
  $("propertySpecs").querySelector("[data-edit-home]").addEventListener("click", editCurrentHome);
  $("propertySpecs").querySelector("[data-delete-home]").addEventListener("click", deleteCurrentHome);
}

/* ======================= Planner ======================= */
function setupPlanner() {
  document.querySelectorAll(".preset").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.selected = new Set(PRESETS[btn.dataset.preset]);
      renderMeasures();
      scheduleEvaluation();
    }));
  $("clearMeasures").addEventListener("click", () => {
    state.selected.clear();
    renderMeasures();
    scheduleEvaluation();
  });
}

function syncPresetButtons() {
  document.querySelectorAll(".preset").forEach((btn) => {
    const preset = PRESETS[btn.dataset.preset];
    const match = preset.length === state.selected.size && preset.every((c) => state.selected.has(c));
    btn.setAttribute("aria-pressed", String(match));
  });
}

function renderMeasures() {
  const installed = new Set(state.result?.already_installed || []);
  const injected = new Set(state.result?.dag_plan.injected_prerequisites || []);

  $("measureList").innerHTML = state.catalogue.map((a) => {
    const isInstalled = installed.has(a.code);
    const tag = isInstalled
      ? `<span class="badge badge-ok">Installed</span>`
      : injected.has(a.code) ? `<span class="badge badge-info">Added by planner</span>` : "";
    return `
      <li>
        <label class="measure ${isInstalled ? "installed" : ""}">
          <input type="checkbox" value="${a.code}" ${state.selected.has(a.code) && !isInstalled ? "checked" : ""} ${isInstalled ? "disabled" : ""}>
          <span class="m-name">${escapeHtml(a.name)}</span>
          <span class="m-cost num">${fmt.gbp(a.estimated_cost_gbp)}</span>
          <span class="m-cat">${escapeHtml(a.category)} · ${a.estimated_time_days} day${a.estimated_time_days === 1 ? "" : "s"} ${tag}</span>
        </label>
      </li>`;
  }).join("");

  $("measureList").querySelectorAll("input").forEach((input) =>
    input.addEventListener("change", () => {
      if (input.checked) state.selected.add(input.value); else state.selected.delete(input.value);
      syncPresetButtons();
      scheduleEvaluation();
    }));
  syncPresetButtons();
}

function scheduleEvaluation(delay = 220) {
  clearTimeout(state.evalTimer);
  state.evalTimer = setTimeout(runEvaluation, delay);
}

async function runEvaluation() {
  if (!state.currentId) return;
  state.evalController?.abort();
  const controller = new AbortController();
  state.evalController = controller;
  document.querySelector("#panel-assessment").setAttribute("aria-busy", "true");

  try {
    const res = await ecoFetch("/evaluate/full-retrofit?persist=false", {
      method: "POST",
      signal: controller.signal,
      body: JSON.stringify({ property_id: state.currentId, selected_interventions: [...state.selected] }),
    });
    if (controller.signal.aborted) return;
    clearError();
    state.result = res;
    renderResult(res);
  } catch (err) {
    if (err.name !== "AbortError") showError(err);
  } finally {
    if (state.evalController === controller) {
      document.querySelector("#panel-assessment").removeAttribute("aria-busy");
    }
  }
}

/* ======================= Render: assessment ======================= */
function renderResult(r) {
  renderMeasures();
  renderScore(r);
  renderComponents(r);
  renderRoadmap(r);
  renderKpis(r);
  renderFuel(r.carbon_impact);
  renderValue(r.property_uplift);
}

function renderScore(r) {
  const ring = $("baseRing");
  ring.className = `score-ring ${bandClass(r.baseline_band)}`;
  ring.style.setProperty("--val", r.baseline_score);
  ring.setAttribute("role", "img");
  ring.setAttribute("aria-label", `Green Home Score ${r.baseline_score} out of 100, ${r.baseline_band}`);
  $("baseScore").textContent = r.baseline_score;
  $("baseBand").textContent = r.baseline_band;
  $("baseBand").className = `band-name ${bandClass(r.baseline_band)}`;
  $("projScoreInline").textContent = r.projected_score;
  $("projBandInline").innerHTML = `<span class="badge ${r.score_delta > 0 ? "badge-info" : "badge-neutral"}">${escapeHtml(r.projected_band)}</span>`;

  $("flagBadge").innerHTML = r.flagged_for_intervention
    ? `<span class="badge badge-danger">${icon("alert", "icon icon-sm")}Flagged for intervention</span>`
    : `<span class="badge badge-ok">${icon("check", "icon icon-sm")}Not flagged</span>`;

  $("driversWrap").innerHTML = r.baseline_drivers.length
    ? `<h3 style="font-size:.875rem;margin-top:20px">Main drivers of the score</h3>
       <ul class="drivers">${r.baseline_drivers.map((d) => `<li>${icon("arrow", "icon icon-sm")}${escapeHtml(d)}</li>`).join("")}</ul>`
    : `<p class="muted" style="margin-top:20px;font-size:.875rem">No weak components: every part scores 60 or above.</p>`;
}

function renderComponents(r) {
  const after = Object.fromEntries(r.projected_components.map((c) => [c.name, c]));
  $("componentList").innerHTML = r.baseline_components.map((c) => {
    const proj = after[c.name];
    const delta = proj ? Math.round(proj.sub_score - c.sub_score) : 0;
    return `
      <div class="component-row">
        <div class="label"><span>${escapeHtml(c.name)}</span><small>${Math.round(c.weight * 100)}%</small></div>
        <div class="value">${Math.round(c.sub_score)}${delta > 0 ? `<span class="delta">+${delta}</span>` : ""}</div>
        <div class="bar" role="img" aria-label="${escapeHtml(c.name)}: ${Math.round(c.sub_score)} of 100${delta > 0 ? `, rising to ${Math.round(proj.sub_score)}` : ""}">
          <span style="width:${c.sub_score}%"></span>
        </div>
      </div>`;
  }).join("");
}

function renderRoadmap(r) {
  const plan = r.dag_plan;
  const injected = new Set(plan.injected_prerequisites);
  $("injectedBadge").innerHTML = injected.size
    ? `<span class="badge badge-info">${icon("graph", "icon icon-sm")}${injected.size} prerequisite${injected.size > 1 ? "s" : ""} added</span>`
    : "";

  if (!plan.ordered_plan.length) {
    $("roadmap").innerHTML = `<div class="empty">${state.selected.size ? "Everything selected is already installed." : "Choose one or more upgrades to build a roadmap."}</div>`;
    return;
  }

  const days = plan.ordered_plan.flatMap((s) => s.actions).reduce((n, a) => n + a.estimated_time_days, 0);
  $("roadmap").innerHTML = `
    <ol class="roadmap">
      ${plan.ordered_plan.map((stage) => `
        <li class="roadmap-stage">
          <header>
            <span class="phase-num">${stage.stage_number}</span>
            <div><h4>${escapeHtml(stage.stage_title.replace(/^Phase \d+:\s*/, ""))}</h4><p class="rationale">${escapeHtml(stage.stage_rationale)}</p></div>
          </header>
          <ul class="roadmap-actions">
            ${stage.actions.map((a) => `
              <li>
                <span class="a-name">${escapeHtml(a.name)}${injected.has(a.code) ? ` <span class="badge badge-info">Prerequisite</span>` : ""}</span>
                <span class="a-cost">${fmt.gbp(a.estimated_cost_gbp)}<small>${a.estimated_time_days} day${a.estimated_time_days === 1 ? "" : "s"}</small></span>
                <span class="a-desc">${escapeHtml(a.description)}</span>
              </li>`).join("")}
          </ul>
        </li>`).join("")}
    </ol>
    <div class="roadmap-total" style="margin-top:14px"><span>Total · about ${days} working days</span><span class="num">${fmt.gbp(plan.total_estimated_cost_gbp)}</span></div>`;
}

function renderKpis(r) {
  const c = r.carbon_impact;
  $("kpiScore").innerHTML = `${r.baseline_score} → ${r.projected_score}`;
  $("kpiScoreSub").innerHTML = `<span class="${r.score_delta > 0 ? "pos" : ""}">${fmt.signed(r.score_delta, " points")}</span> · ${escapeHtml(r.band_transition.replace("->", "→"))}`;

  $("kpiCo2").textContent = `${c.net_co2e_reduction_tonnes.toFixed(2)} t`;
  $("kpiCo2Sub").textContent = `${c.net_co2e_reduction_pct.toFixed(1)}% less CO₂e than today`;

  const saving = c.net_bill_savings_gbp;
  $("kpiBill").innerHTML = `<span class="${saving >= 0 ? "pos" : "neg"}">${saving >= 0 ? "−" : "+"}${fmt.gbp(Math.abs(saving))}</span>`;
  $("kpiBillSub").textContent = `${fmt.gbp(c.baseline_annual_bill_gbp)} → ${fmt.gbp(c.projected_annual_bill_gbp)}`;

  $("kpiCost").textContent = fmt.gbp(r.total_retrofit_cost_gbp);
  $("kpiCostSub").textContent = r.net_payback_years != null
    ? `Pays back in about ${r.net_payback_years} years from bill savings`
    : r.total_retrofit_cost_gbp > 0 ? "No bill-based payback" : "No measures selected";
}

function renderFuel(c) {
  const max = Math.max(c.baseline_gas_kwh, c.projected_gas_kwh, c.baseline_electricity_kwh, c.projected_electricity_kwh, 1);
  const row = (name, ic, color, before, after, delta) => `
    <div class="fuel-row">
      <div class="fuel-name">${icon(ic, "icon icon-sm")}${name}</div>
      <div class="fuel-bars">
        <div class="fuel-bar"><span>Today</span><div class="track"><div class="fill" style="width:${(before / max) * 100}%;background:${color}"></div></div><span class="v">${fmt.int(before)}</span></div>
        <div class="fuel-bar"><span>After</span><div class="track"><div class="fill after" style="width:${(after / max) * 100}%;background:${color}"></div></div><span class="v">${fmt.int(after)}</span></div>
      </div>
      <div class="fuel-delta ${delta <= 0 ? "pos" : "neg"}">${fmt.signed(delta, " kWh / year")}</div>
    </div>`;
  $("fuelChart").innerHTML =
    row("Gas", "flame", "var(--gas)", c.baseline_gas_kwh, c.projected_gas_kwh, c.delta_gas_kwh) +
    row("Electricity", "zap", "var(--elec)", c.baseline_electricity_kwh, c.projected_electricity_kwh, c.delta_electricity_kwh);

  let kind = "info", title = "Dual-fuel accounting";
  if (c.heat_pump_paradox_detected && !c.heat_pump_paradox_mitigated) { kind = "warn"; title = "EPC heat pump paradox"; }
  else if (c.heat_pump_paradox_mitigated) { kind = "ok"; title = "Paradox avoided by fabric-first"; }
  $("paradoxCallout").innerHTML = `
    <div class="callout callout-${kind}">
      ${icon(kind === "warn" ? "alert" : kind === "ok" ? "check-circle" : "info")}
      <div><strong>${title}</strong>${escapeHtml(c.paradox_explanation)}</div>
    </div>`;
}

function renderValue(u) {
  if (u.score_delta <= 0) {
    $("valuePanel").innerHTML = `<div class="empty">No score improvement, so no value uplift is estimated.</div>`;
    return;
  }
  $("valuePanel").innerHTML = `
    <div class="stat-label">Indicative value uplift</div>
    <div class="kpi-value pos" style="font-size:1.75rem">${fmt.gbp(u.predicted_uplift_gbp_min)} – ${fmt.gbp(u.predicted_uplift_gbp_max)}</div>
    <div class="kpi-sub">+${u.predicted_uplift_pct_min}% to +${u.predicted_uplift_pct_max}% on ${fmt.gbp(u.baseline_value_gbp)} · midpoint ${fmt.gbp(u.indicative_new_value_gbp_mid)}</div>
    <div class="grid grid-2" style="margin-top:18px;gap:12px">
      <div class="stat"><div class="stat-label">Green Living Reward</div><div class="stat-value">${u.lloyds_green_mortgage_cashback_gbp > 0 ? fmt.gbp(u.lloyds_green_mortgage_cashback_gbp) : "Not eligible"}</div></div>
      <div class="stat"><div class="stat-label">Indicative rate discount</div><div class="stat-value">${u.lloyds_preferential_rate_discount_bps > 0 ? `${u.lloyds_preferential_rate_discount_bps} bps` : "None"}</div></div>
    </div>
    <p class="help" style="margin-top:14px">Hedonic gradient-boosted model, confidence ${Math.round(u.confidence_score * 100)}%. A range, not a valuation. Incentives are illustrative.</p>`;
}

/* ======================= Portfolio ======================= */
/* Portfolio ranking, overview charts and the data table live in dashboard.js. */
function openHome(id) {
  selectProperty(id);
  activateTab("assessment");
  window.scrollTo({ top: 0 });
}

/* ======================= Advisor ======================= */
function setupChat() {
  $("chatForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("chatInput").value.trim();
    if (q) askAdvisor(q);
  });
  document.querySelectorAll(".quick").forEach((b) => b.addEventListener("click", () => askAdvisor(b.dataset.q)));
}

function greetAdvisor() {
  appendMessage("ai", `<p>Ask a what-if question about the selected home, for example <em>"What if I replace my boiler with a heat pump?"</em>. I rerun the calculation engines and explain only the numbers they return.</p>`);
}

function appendMessage(role, html) {
  const el = document.createElement("div");
  el.className = `msg msg-${role}`;
  el.innerHTML = html;
  $("chatLog").appendChild(el);
  $("chatLog").scrollTop = $("chatLog").scrollHeight;
  return el;
}

/* Minimal, safe Markdown: escape first, then **bold**, *em*, and "- " bullet lists. */
function renderMarkdown(md) {
  const inline = (s) => escapeHtml(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/\*(.+?)\*/g, "<em>$1</em>");
  return md.split(/\n{2,}/).map((block) => {
    const lines = block.split("\n");
    if (lines.every((l) => /^\s*[-•]\s+/.test(l))) {
      return `<ul>${lines.map((l) => `<li>${inline(l.replace(/^\s*[-•]\s+/, ""))}</li>`).join("")}</ul>`;
    }
    return `<p>${lines.map(inline).join("<br>")}</p>`;
  }).join("");
}

async function askAdvisor(query) {
  $("chatInput").value = "";
  appendMessage("user", escapeHtml(query));
  const pending = appendMessage("ai", `<span style="display:inline-flex;gap:8px;align-items:center"><span class="spinner" aria-hidden="true"></span>Rerunning the engines…</span>`);
  $("chatSend").disabled = true;

  try {
    const res = await ecoFetch("/scenario/chat", {
      method: "POST",
      body: JSON.stringify({ user_query: query, property_id: state.currentId }),
    });
    const names = res.extracted_intent.requested_interventions
      .map((code) => state.catalogue.find((a) => a.code === code)?.name || code);
    const sourceBadge = res.explanation_source === "llm"
      ? `<span class="badge badge-ok">${icon("shield", "icon icon-sm")}AI wording · every number verified</span>`
      : `<span class="badge badge-neutral">${icon("shield", "icon icon-sm")}Template answer${res.numeric_grounding_passed ? "" : " · AI reply failed number check"}</span>`;
    const budgetBadge = res.within_budget == null ? ""
      : res.within_budget ? `<span class="badge badge-ok">Within budget</span>` : `<span class="badge badge-warn">Over budget</span>`;

    pending.innerHTML = `
      ${renderMarkdown(res.natural_language_explanation)}
      <div class="msg-note"><strong>Green finance:</strong> ${escapeHtml(res.lloyds_bank_advisory_note)}</div>
      <div class="msg-meta">
        ${sourceBadge}
        ${res.guardrail_validation_passed ? `<span class="badge badge-ok">Guardrails passed</span>` : `<span class="badge badge-danger">Guardrails failed</span>`}
        ${budgetBadge}
        <span class="badge badge-neutral">Measures: ${escapeHtml(names.join(", "))}</span>
      </div>
      <div style="margin-top:10px"><button class="btn btn-secondary btn-sm apply-plan" type="button">Open this plan in the planner</button></div>`;

    pending.querySelector(".apply-plan").addEventListener("click", () => {
      state.selected = new Set(res.extracted_intent.requested_interventions);
      if (res.property_id !== state.currentId) selectProperty(res.property_id); else scheduleEvaluation(0);
      activateTab("assessment");
      window.scrollTo({ top: 0 });
    });
  } catch (err) {
    pending.innerHTML = `<p>The calculation engine didn't respond (${escapeHtml(err.message)}). Check the API is running and try again.</p>`;
  } finally {
    $("chatSend").disabled = false;
    $("chatInput").focus();
  }
}

/* ======================= Saved scenarios (Supabase sign-in) ======================= */
function loginHref() {
  const here = `app.html${location.search}${location.hash}`;
  return `login.html?next=${encodeURIComponent(here)}`;
}

async function saveScenario() {
  const msg = $("saveMsg");
  msg.className = "form-msg";
  const { enabled } = await EcoAuth.ready;
  if (!enabled) {
    msg.className = "form-msg error";
    msg.innerHTML = "Saving needs sign-in, which is turned off on this server.";
    return;
  }
  if (!EcoAuth.user()) { location.href = loginHref(); return; }
  if (!state.currentId) {
    msg.className = "form-msg error";
    msg.textContent = "Add a home first.";
    return;
  }
  if (!state.selected.size) {
    msg.className = "form-msg error";
    msg.textContent = "Choose at least one upgrade before saving.";
    return;
  }

  const summary = state.properties.find((p) => p.property_id === state.currentId);
  const name = `${summary.address} · ${state.selected.size} measure${state.selected.size > 1 ? "s" : ""}`;
  const btn = $("saveScenarioBtn");
  btn.disabled = true;
  try {
    await EcoAuth.apiFetch("/scenarios", {
      method: "POST",
      body: JSON.stringify({ property_id: state.currentId, name, selected_interventions: [...state.selected] }),
    });
    msg.className = "form-msg ok";
    msg.innerHTML = `Saved. <a href="#saved">View saved scenarios</a>`;
  } catch (err) {
    msg.className = "form-msg error";
    msg.textContent = `Couldn't save: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
}

async function loadSaved() {
  const panel = $("savedPanel");
  const { enabled } = await EcoAuth.ready;
  if (!enabled) {
    panel.innerHTML = `
      <div class="callout callout-info">${icon("info")}
        <div><strong>Sign-in is turned off on this server</strong>Everything else works without an account.</div>
      </div>`;
    return;
  }
  if (!EcoAuth.user()) {
    panel.innerHTML = `
      <div class="empty">
        <div class="dz-icon" style="margin:0 auto 14px">${icon("bookmark", "icon icon-lg")}</div>
        <p style="margin-bottom:16px;color:var(--ink-2)">Sign in to save scenarios and come back to them later.</p>
        <div class="dz-actions"><a class="btn btn-primary" href="${loginHref()}">Sign in</a><a class="btn btn-secondary" href="${loginHref()}&mode=signup">Create account</a></div>
      </div>`;
    return;
  }

  panel.innerHTML = `<div class="skeleton" style="height:72px"></div>`;
  try {
    const rows = await EcoAuth.apiFetch("/scenarios");
    if (!rows.length) {
      panel.innerHTML = `<div class="empty">No saved scenarios yet. Build a plan in <a href="#assessment">Assessment</a> and press <strong>Save scenario</strong>.</div>`;
      return;
    }
    panel.innerHTML = `<div class="scenario-list">${rows.map((r) => `
      <article class="scenario" data-id="${escapeHtml(r.scenario_id)}">
        <h3>${escapeHtml(r.name)}</h3>
        <div class="meta">${escapeHtml(r.property_id)} · saved ${new Date(r.created_at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}</div>
        <div class="actions">
          <button class="btn btn-secondary btn-sm" type="button" data-open>Open</button>
          <button class="icon-btn" type="button" data-delete aria-label="Delete ${escapeHtml(r.name)}">${icon("trash", "icon icon-sm")}</button>
        </div>
        <div class="stats">
          <span class="badge badge-neutral num">${r.baseline_score} → ${r.projected_score}</span>
          <span class="badge badge-ok">${r.net_co2e_reduction_tonnes.toFixed(2)} t CO₂e / yr</span>
          <span class="badge badge-neutral">${fmt.gbp(r.total_retrofit_cost_gbp)}</span>
          <span class="badge badge-neutral">${r.measures.length} measure${r.measures.length === 1 ? "" : "s"}</span>
        </div>
      </article>`).join("")}</div>`;

    panel.querySelectorAll(".scenario").forEach((el) => {
      const row = rows.find((r) => r.scenario_id === el.dataset.id);
      el.querySelector("[data-open]").addEventListener("click", () => {
        state.selected = new Set(row.measures);
        if (row.property_id !== state.currentId) selectProperty(row.property_id); else scheduleEvaluation(0);
        activateTab("assessment");
        window.scrollTo({ top: 0 });
      });
      el.querySelector("[data-delete]").addEventListener("click", async () => {
        if (!confirm(`Delete "${row.name}"?`)) return;
        try {
          await EcoAuth.apiFetch(`/scenarios/${row.scenario_id}`, { method: "DELETE" });
          loadSaved();
        } catch (err) { alert(`Couldn't delete: ${err.message}`); }
      });
    });
  } catch (err) {
    panel.innerHTML = `<div class="callout callout-danger">${icon("alert")}<div><strong>Couldn't load saved scenarios</strong>${escapeHtml(err.message)}</div></div>`;
  }
}
