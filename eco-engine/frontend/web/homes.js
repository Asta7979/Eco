/**
 * Project Eco: the signed-in user's homes.
 *   - "Add home" / "Edit home" dialog -> POST /properties, PATCH /properties/{id}
 *   - Delete home -> DELETE /properties/{id} (removes its scenarios and logs too)
 *   - Delete account -> DELETE /auth/me
 * Server-side validation errors are shown next to the field they belong to.
 * Relies on globals from app.js: state, $, selectProperty, renderPropertyList, activateTab.
 */

const HOME_OPTIONS = {
  region: ["London", "South East", "South West", "East of England", "East Midlands", "West Midlands",
    "Yorkshire and The Humber", "North West", "North East", "Wales", "Scotland", "Northern Ireland"],
  property_type: ["Victorian Mid-Terrace", "Victorian End-Terrace", "Edwardian Semi-Detached", "1930s Semi-Detached",
    "1950s Semi-Detached", "1960s Detached House", "1970s Mid-Terrace", "1970s Detached Bungalow", "Purpose-Built Flat",
    "1990s Detached", "2000s Detached", "Modern Apartment", "New-Build Detached", "Pre-1900 Stone Cottage"],
  wall_type: ["Solid Brick 228mm", "Solid Brick Rendered", "Solid Stone", "Cavity Wall Unfilled", "Cavity Wall Filled",
    "Cavity Wall Insulated", "Timber Frame Cavity Insulated", "SIPs High Performance Fabric"],
  floor_type: ["Suspended Timber", "Suspended Timber Insulated", "Solid Concrete", "Solid Concrete Insulated",
    "Solid Stone", "Concrete Intermediate", "Slab Super-Insulated"],
  glazing_type: ["Single Glazed", "Single Glazed with Secondary", "Older Double Glazed (pre-2002)",
    "Modern Double Glazed (post-2002)", "High Performance Double Glazed", "Triple Glazed Acoustic Low-E"],
  heating_type: ["Gas Boiler (Condensing)", "Gas Boiler (Non-condensing)", "Oil Boiler (Standard)",
    "LPG / Electric Direct Heaters", "Electric Storage Heaters (Old)", "Electric Panel Heaters",
    "Air Source Heat Pump (ASHP)", "Ground Source Heat Pump (GSHP)", "Communal CHP District Heating"],
  roof_type: ["Pitched Tile", "Pitched Slate", "Flat Roof", "Flat Roof Insulated", "Warm Roof High Insulation"],
};

/* Typical values used to pre-fill a field when the user picks a type (they can overwrite). */
const WALL_U = { "Solid Brick 228mm": 2.1, "Solid Brick Rendered": 2.0, "Solid Stone": 2.4, "Cavity Wall Unfilled": 1.5,
  "Cavity Wall Filled": 0.55, "Cavity Wall Insulated": 0.45, "Timber Frame Cavity Insulated": 0.3, "SIPs High Performance Fabric": 0.15 };
const HEAT_EFF = { "Gas Boiler (Condensing)": 88, "Gas Boiler (Non-condensing)": 70, "Oil Boiler (Standard)": 80,
  "LPG / Electric Direct Heaters": 100, "Electric Storage Heaters (Old)": 100, "Electric Panel Heaters": 100,
  "Air Source Heat Pump (ASHP)": 300, "Ground Source Heat Pump (GSHP)": 380, "Communal CHP District Heating": 85 };

const FIELDS = [
  { group: "The home", items: [
    { k: "address", label: "Address", type: "text", wide: true, ph: "12 Elm Road, Leeds" },
    { k: "postcode", label: "Postcode", type: "text", ph: "LS1 4AB" },
    { k: "region", label: "Region", type: "select" },
    { k: "property_type", label: "Property type", type: "text", list: true, ph: "1930s Semi-Detached" },
    { k: "built_year", label: "Year built", type: "number", min: 1500, max: 2030, step: 1, ph: "1934" },
    { k: "total_floor_area_m2", label: "Floor area (m²)", type: "number", min: 10, max: 2000, step: 0.1, ph: "88" },
    { k: "baseline_value_gbp", label: "Current value (£)", type: "number", min: 10000, step: 1000, ph: "260000" },
  ] },
  { group: "Energy", hint: "From your EPC and the last 12 months of bills.", items: [
    { k: "current_epc_score", label: "EPC score (1–100)", type: "number", min: 1, max: 100, step: 1, ph: "52", help: "The band (A–G) is worked out for you." },
    { k: "annual_gas_kwh", label: "Gas per year (kWh)", type: "number", min: 0, step: 1, ph: "13500", help: "0 if the home has no gas." },
    { k: "annual_electricity_kwh", label: "Electricity per year (kWh)", type: "number", min: 0, step: 1, ph: "3100" },
  ] },
  { group: "Fabric", items: [
    { k: "wall_type", label: "Walls", type: "select" },
    { k: "wall_insulation_status", label: "Wall insulation", type: "select", options: ["Uninsulated", "Partial", "Insulated"] },
    { k: "wall_u_value", label: "Wall U-value (W/m²K)", type: "number", min: 0.05, max: 5, step: 0.01, help: "Pre-filled from the wall type." },
    { k: "roof_type", label: "Roof", type: "select" },
    { k: "roof_insulation_depth_mm", label: "Loft insulation (mm)", type: "number", min: 0, max: 600, step: 10, ph: "100" },
    { k: "floor_type", label: "Floor", type: "select" },
    { k: "floor_insulation_status", label: "Floor insulation", type: "select", options: ["Uninsulated", "Partial", "Insulated"] },
    { k: "glazing_type", label: "Windows", type: "select", wide: true },
  ] },
  { group: "Heating and extras", items: [
    { k: "heating_type", label: "Main heating", type: "select", wide: true },
    { k: "heating_efficiency_pct", label: "Efficiency (%)", type: "number", min: 30, max: 500, step: 1, help: "Heat pumps: SCOP × 100, e.g. 300." },
    { k: "solar_pv_kwp", label: "Solar PV (kWp)", type: "number", min: 0, max: 50, step: 0.1, ph: "0" },
    { k: "battery_storage_kwh", label: "Battery (kWh)", type: "number", min: 0, max: 100, step: 0.1, ph: "0" },
    { k: "smart_controls", label: "Smart thermostat or TRVs", type: "checkbox" },
  ] },
];

const DEFAULTS = {
  region: "London", wall_type: "Cavity Wall Unfilled", wall_insulation_status: "Uninsulated", wall_u_value: 1.5,
  roof_type: "Pitched Tile", roof_insulation_depth_mm: 100, floor_type: "Suspended Timber",
  floor_insulation_status: "Uninsulated", glazing_type: "Older Double Glazed (pre-2002)",
  heating_type: "Gas Boiler (Condensing)", heating_efficiency_pct: 88, solar_pv_kwp: 0, battery_storage_kwh: 0, smart_controls: false,
};

let editingId = null;

function fieldHtml(f) {
  const id = `hf-${f.k}`;
  const help = f.help ? `<span class="help">${f.help}</span>` : "";
  if (f.type === "checkbox") {
    return `<label class="toggle hf-wide" style="margin-top:6px"><input type="checkbox" id="${id}" name="${f.k}"> ${f.label}</label>`;
  }
  let control;
  if (f.type === "select") {
    const opts = f.options || HOME_OPTIONS[f.k] || [];
    control = `<select class="input" id="${id}" name="${f.k}" required>${opts.map((o) => `<option>${escapeHtml(o)}</option>`).join("")}</select>`;
  } else {
    const attrs = [f.min != null ? `min="${f.min}"` : "", f.max != null ? `max="${f.max}"` : "", f.step != null ? `step="${f.step}"` : "",
      f.ph ? `placeholder="${escapeHtml(f.ph)}"` : "", f.list ? `list="dl-${f.k}"` : ""].join(" ");
    control = `<input class="input" id="${id}" name="${f.k}" type="${f.type}" ${attrs} required autocomplete="off">`;
    if (f.list) control += `<datalist id="dl-${f.k}">${HOME_OPTIONS[f.k].map((o) => `<option value="${escapeHtml(o)}">`).join("")}</datalist>`;
  }
  return `<div class="field ${f.wide ? "hf-wide" : ""}" data-field="${f.k}"><label for="${id}">${f.label}</label>${control}${help}<span class="field-error" role="alert"></span></div>`;
}

function buildHomeDialog() {
  const dlg = document.createElement("dialog");
  dlg.className = "home-dialog";
  dlg.id = "homeDialog";
  dlg.setAttribute("aria-labelledby", "homeDialogTitle");
  dlg.innerHTML = `
    <form id="homeForm" novalidate>
      <header class="hd-head">
        <div><h2 id="homeDialogTitle">Add a home</h2><p class="help">Saved to your account only. You can edit it any time. Eco currently assesses homes in the UK; India is coming soon.</p></div>
        <button class="icon-btn plain" type="button" data-close aria-label="Close">${icon("close", "icon icon-sm")}</button>
      </header>
      <div class="hd-body">
        ${FIELDS.map((g) => `
          <fieldset class="hd-group">
            <legend>${g.group}${g.hint ? ` <span class="help">${g.hint}</span>` : ""}</legend>
            <div class="hd-grid">${g.items.map(fieldHtml).join("")}</div>
          </fieldset>`).join("")}
      </div>
      <footer class="hd-foot">
        <p class="form-msg" id="homeFormMsg" role="status"></p>
        <button class="btn btn-ghost" type="button" data-close>Cancel</button>
        <button class="btn btn-primary" type="submit" id="homeSubmit">Save home</button>
      </footer>
    </form>`;
  document.body.appendChild(dlg);

  dlg.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => dlg.close()));
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  const form = dlg.querySelector("form");
  form.wall_type.addEventListener("change", () => {
    if (WALL_U[form.wall_type.value]) form.wall_u_value.value = WALL_U[form.wall_type.value];
    if (/insulated|filled|sips/i.test(form.wall_type.value) && !/unfilled/i.test(form.wall_type.value)) form.wall_insulation_status.value = "Insulated";
    else form.wall_insulation_status.value = "Uninsulated";
  });
  form.heating_type.addEventListener("change", () => {
    if (HEAT_EFF[form.heating_type.value]) form.heating_efficiency_pct.value = HEAT_EFF[form.heating_type.value];
  });
  form.addEventListener("submit", submitHome);
  return dlg;
}

function fillForm(form, values) {
  FIELDS.flatMap((g) => g.items).forEach((f) => {
    const el = form.elements[f.k];
    if (!el) return;
    const v = values[f.k];
    if (f.type === "checkbox") el.checked = !!v;
    else if (f.type === "select" && v != null && ![...el.options].some((o) => o.value === v)) {
      el.add(new Option(v, v)); // keep a value that isn't in our list
      el.value = v;
    } else el.value = v ?? "";
  });
}

function clearErrors(form) {
  form.querySelectorAll(".field-error").forEach((e) => { e.textContent = ""; });
  form.querySelectorAll("[aria-invalid]").forEach((e) => e.removeAttribute("aria-invalid"));
  $("homeFormMsg").textContent = "";
  $("homeFormMsg").className = "form-msg";
}

function showFieldError(form, field, message) {
  const wrap = form.querySelector(`[data-field="${field}"]`);
  if (!wrap) return false;
  wrap.querySelector(".field-error").textContent = message;
  wrap.querySelector("input, select")?.setAttribute("aria-invalid", "true");
  return true;
}

function readForm(form) {
  const out = {};
  FIELDS.flatMap((g) => g.items).forEach((f) => {
    const el = form.elements[f.k];
    if (f.type === "checkbox") out[f.k] = el.checked;
    else if (f.type === "number") out[f.k] = el.value === "" ? null : Number(el.value);
    else out[f.k] = el.value.trim();
  });
  return out;
}

function openHomeDialog(home = null) {
  const dlg = $("homeDialog") || buildHomeDialog();
  const form = dlg.querySelector("form");
  editingId = home ? home.property_id : null;
  form.reset();
  clearErrors(form);
  fillForm(form, home || DEFAULTS);
  $("homeDialogTitle").textContent = home ? "Edit home" : "Add a home";
  $("homeSubmit").textContent = home ? "Save changes" : "Add home";
  dlg.showModal();
  form.elements[home ? "address" : "address"].focus();
}

async function submitHome(e) {
  e.preventDefault();
  const form = e.target;
  clearErrors(form);
  const body = readForm(form);

  // Client-side checks mirror the API's rules so most mistakes are caught instantly.
  let firstBad = null;
  FIELDS.flatMap((g) => g.items).forEach((f) => {
    const el = form.elements[f.k];
    if (f.type === "checkbox") return;
    if (body[f.k] === null || body[f.k] === "") { showFieldError(form, f.k, "Required."); firstBad ??= el; return; }
    if (!el.checkValidity()) { showFieldError(form, f.k, el.validationMessage); firstBad ??= el; }
  });
  if (firstBad) { firstBad.focus(); return; }

  const btn = $("homeSubmit");
  btn.disabled = true;
  try {
    const saved = editingId
      ? await ecoFetch(`/properties/${editingId}`, { method: "PATCH", body: JSON.stringify(body) })
      : await ecoFetch("/properties", { method: "POST", body: JSON.stringify(body) });
    $("homeDialog").close();
    await reloadHomes(saved.property_id);
  } catch (err) {
    let shown = false;
    try {
      JSON.parse(err.message).forEach((d) => { shown = showFieldError(form, d.loc?.[d.loc.length - 1], d.msg) || shown; });
    } catch (_) { /* not a validation list */ }
    $("homeFormMsg").className = "form-msg error";
    $("homeFormMsg").textContent = shown ? "Please fix the highlighted fields." : `Couldn't save: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
}

/* Refresh everything that depends on the user's homes, then select one. */
async function reloadHomes(selectId) {
  invalidateEcoQuery("/properties");
  if (typeof resetPortfolioData === "function") resetPortfolioData();
  state.properties = await ecoQuery("/properties");
  applyEmptyState();
  renderPropertyList();
  if (selectId && state.properties.some((p) => p.property_id === selectId)) {
    selectProperty(selectId);
    activateTab("assessment");
  } else if (state.properties.length) {
    selectProperty(state.properties[0].property_id);
  } else {
    state.currentId = null;
  }
  if (document.body.dataset.view === "overview") renderOverview();
}

async function editCurrentHome() {
  if (!state.currentId) return;
  openHomeDialog(await ecoQuery(`/properties/${state.currentId}`));
}

async function deleteCurrentHome() {
  const home = state.properties.find((p) => p.property_id === state.currentId);
  if (!home) return;
  if (!confirm(`Delete "${home.address}"?\n\nIts saved scenarios and advisor history are deleted too. This can't be undone.`)) return;
  try {
    await ecoFetch(`/properties/${home.property_id}`, { method: "DELETE" });
    invalidateEcoQuery(`/properties/${home.property_id}`);
    await reloadHomes(null);
  } catch (err) {
    alert(`Couldn't delete: ${err.message}`);
  }
}

async function deleteAccount() {
  const typed = prompt("This permanently deletes your account, every home and every saved scenario.\n\nType DELETE to confirm.");
  if (typed !== "DELETE") return;
  try {
    await EcoAuth.deleteAccount();
    location.replace("./");
  } catch (err) {
    alert(`Couldn't delete the account: ${err.message}`);
  }
}

/* First-run: no homes yet. Data views show one clear next step instead of empty charts. */
function applyEmptyState() {
  const empty = state.properties.length === 0;
  document.body.classList.toggle("no-homes", empty);
  $("propCount").textContent = `${state.properties.length} home${state.properties.length === 1 ? "" : "s"}`;
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-add-home]").forEach((b) => b.addEventListener("click", () => openHomeDialog()));
  $("deleteAccountBtn")?.addEventListener("click", deleteAccount);
});
