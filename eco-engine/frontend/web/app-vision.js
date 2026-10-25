/**
 * Project Eco: Vision AI tab (photo assistance).
 * Upload / drag-drop / camera -> POST /vision/analyze -> draw detections -> the user
 * confirms suggestions -> only then the planner's selected measures change.
 * Relies on globals from app.js: state, $, scheduleEvaluation, activateTab.
 */

(() => {
  const v = {
    file: null,
    url: null,
    mode: "auto",
    result: null,
    highlight: null,
    stream: null,
  };

  const img = () => $("vizImg");
  const canvas = () => $("vizCanvas");

  function redraw() {
    if (!v.result || !img().complete) return;
    EcoVision.draw(canvas(), img(), v.result, { highlight: v.highlight, labels: $("showLabels").checked });
  }

  function showStage(stage) {
    $("dzEmpty").hidden = stage !== "empty";
    $("cameraWrap").hidden = stage !== "camera";
    $("vizWrap").hidden = stage !== "viz";
    $("vizToolbar").hidden = stage !== "viz";
    $("dropzone").style.padding = stage === "empty" ? "" : "12px";
  }

  function reset() {
    stopCamera();
    if (v.url) URL.revokeObjectURL(v.url);
    Object.assign(v, { file: null, url: null, result: null, highlight: null });
    $("samNote").hidden = true;
    $("visionDetections").hidden = true;
    $("visionSuggestions").hidden = true;
    $("visionFile").value = "";
    showStage("empty");
  }

  async function loadFile(file) {
    if (!file) return;
    if (file.type && !/^image\/(jpeg|png|webp)$/.test(file.type)) { alert("Use a JPG, PNG or WebP image."); return; }
    if (file.size > 12 * 1024 * 1024) { alert("That image is larger than 12 MB."); return; }
    stopCamera();
    if (v.url) URL.revokeObjectURL(v.url);
    v.file = file;
    v.url = URL.createObjectURL(file);
    v.result = null;
    $("samNote").hidden = true;
    img().src = v.url;
    showStage("viz");
    await new Promise((r) => { if (img().complete) r(); else img().onload = r; });
    redraw();
    run();
  }

  async function run() {
    if (!v.file) return;
    $("vizScan").hidden = false;
    $("visionMeta").textContent = "";
    $("visionDetections").hidden = false;
    $("detList").innerHTML = `<li style="grid-template-columns:auto 1fr"><span class="spinner"></span>Analysing the photo…</li>`;
    $("thermalMeter").innerHTML = "";
    $("visionSuggestions").hidden = true;
    try {
      const file = v.file;
      v.result = await EcoVision.analyze(file, v.mode);
      v.highlight = null;
      redraw();
      renderDetections();
      renderSuggestions();
      refineWithSam(file, v.result);
    } catch (err) {
      $("detList").innerHTML = `<li style="grid-template-columns:1fr;color:var(--danger)">Couldn't analyse the photo: ${escapeHtml(err.message)}</li>`;
    } finally {
      $("vizScan").hidden = true;
    }
  }

  /* Progressive enhancement: boxes are shown at once, then Segment Anything turns them
     into pixel-accurate outlines (one encoder pass, a few seconds on CPU). */
  async function refineWithSam(file, result) {
    const ids = result.segmentable || [];
    if (!ids.length || !v.samReady) return;
    const note = $("samNote");
    note.hidden = false;
    note.innerHTML = `<span class="spinner" aria-hidden="true"></span>Refining ${ids.length} outline${ids.length > 1 ? "s" : ""} with Segment Anything…`;
    try {
      const res = await EcoVision.segment(file, ids.map((i) => result.detections[i].box));
      if (v.result !== result) return; // a newer photo or mode replaced this one
      if (!res.available) { note.textContent = `Precise outlines unavailable: ${res.reason}`; return; }
      let n = 0;
      res.masks.forEach((m, k) => {
        if (!m) return;
        const d = result.detections[ids[k]];
        d.polygon = m.polygon;
        d.sam = { area_pct: m.area_pct, score: m.score };
        n++;
      });
      redraw();
      renderDetections();
      const glazing = result.detections.filter((d) => d.sam && d.label === "Window").reduce((s, d) => s + d.sam.area_pct, 0);
      note.innerHTML = `${icon("check-circle", "icon icon-sm")}<span>${n} outline${n === 1 ? "" : "s"} refined by SAM in ${(res.processing_ms / 1000).toFixed(1)} s${glazing ? ` · glazing ≈ ${glazing.toFixed(1)}% of the photo` : ""}</span>`;
    } catch (err) {
      if (v.result === result) note.textContent = `Precise outlines failed: ${err.message}`;
    }
  }

  function renderDetections() {
    const r = v.result;
    const engines = r.engines_used.map((e) => ({
      "yolo-world": "YOLO-World", "opencv-geometry": "OpenCV geometry", "thermal-segmentation": "Thermal segmentation", "opencv-pv-texture": "PV texture detector",
      "facade-yolov8": "Facade YOLOv8", "solar-yolov8": "Solar YOLOv8 (satellite)", "solar-yolo26": "Solar YOLO26 (satellite)",
    }[e] || e)).join(" + ");
    const modeName = { thermal: "Thermal", aerial: "Aerial", exterior: "Exterior" }[r.mode] || "Exterior";
    $("visionMeta").innerHTML = `${modeName} · ${escapeHtml(engines)} · ${r.processing_ms} ms`;

    const warn = [...r.quality.warnings, ...r.notes].map((w) => `<div class="callout callout-warn" style="margin-bottom:10px">${icon("alert", "icon icon-sm")}<div>${escapeHtml(w)}</div></div>`).join("");
    const t = r.thermal, a = r.aerial;
    const aerialHtml = a ? `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px">
        <div class="stat"><div class="stat-label">PV arrays</div><div class="meter-big"><strong>${a.pv_arrays}</strong></div></div>
        <div class="stat"><div class="stat-label">PV area</div><div class="meter-big"><strong>${a.pv_area_pct.toFixed(1)}</strong><span class="muted">% of image</span></div></div>
      </div>` : "";
    $("thermalMeter").innerHTML = warn + aerialHtml + (t ? `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px">
        <div class="stat"><div class="stat-label">Heat-loss index</div><div class="meter-big"><strong>${t.heat_loss_index}</strong><span class="muted">/ 100</span></div></div>
        <div class="stat"><div class="stat-label">Hot area</div><div class="meter-big"><strong>${t.hot_area_pct.toFixed(1)}</strong><span class="muted">% of frame</span></div></div>
      </div>
      <p class="help" style="margin:-4px 0 12px">Relative index from the image palette, not a calibrated temperature.</p>` : "");

    $("detList").innerHTML = r.detections.length
      ? r.detections.map((d) => `
        <li data-id="${d.id}" tabindex="0">
          <i style="background:${EcoVision.colorFor(d.label)}"></i>
          <span>${escapeHtml(d.label)}${d.source === "opencv-geometry" ? ' <span class="help">(geometric)</span>' : ""}${d.extra?.area_pct ? ` <span class="help">${d.extra.area_pct}% of frame</span>` : ""}${d.sam ? ` <span class="badge badge-info" title="Segment Anything mask, quality ${d.sam.score}">SAM ${d.sam.area_pct.toFixed(1)}%</span>` : ""}</span>
          <span class="conf" title="Confidence"><i style="--v:${Math.round(d.confidence * 100)}%"></i>${Math.round(d.confidence * 100)}%</span>
        </li>`).join("")
      : `<li style="grid-template-columns:1fr">Nothing detected. Try a clearer, front-on photo.</li>`;

    $("detList").querySelectorAll("li[data-id]").forEach((li) => {
      const on = () => { v.highlight = Number(li.dataset.id); redraw(); };
      const off = () => { v.highlight = null; redraw(); };
      li.addEventListener("mouseenter", on);
      li.addEventListener("focus", on);
      li.addEventListener("mouseleave", off);
      li.addEventListener("blur", off);
    });
  }

  function measureName(code) {
    return state.catalogue.find((a) => a.code === code)?.name || code;
  }

  function renderSuggestions() {
    const list = v.result.suggestions;
    $("visionSuggestions").hidden = !list.length;
    $("applyMsg").textContent = "";
    $("suggList").innerHTML = list.map((s, i) => {
      const actionable = s.kind !== "observation" && s.measure_code;
      const tag = s.kind === "already_installed"
        ? `<span class="badge badge-ok">Looks installed · remove from plan</span>`
        : s.kind === "add_measure" ? `<span class="badge badge-info">Add: ${escapeHtml(measureName(s.measure_code))}</span>` : `<span class="badge badge-neutral">Observation</span>`;
      const checked = actionable && s.confidence >= 0.45 ? "checked" : "";
      return actionable
        ? `<label class="sugg"><input type="checkbox" data-i="${i}" ${checked}><b>${escapeHtml(s.title)}</b><p>${escapeHtml(s.reason)}</p><span class="meta">${tag}<span class="conf"><i style="--v:${Math.round(s.confidence * 100)}%"></i>${Math.round(s.confidence * 100)}%</span></span></label>`
        : `<div class="sugg obs">${icon("info", "icon icon-sm")}<b>${escapeHtml(s.title)}</b><p>${escapeHtml(s.reason)}</p></div>`;
    }).join("");
    $("suggList").querySelectorAll(".sugg").forEach((el, i) => {
      const ev = list[i].evidence || [];
      el.addEventListener("mouseenter", () => { if (ev.length) { v.highlight = ev[0]; redraw(); } });
      el.addEventListener("mouseleave", () => { v.highlight = null; redraw(); });
    });
  }

  function applySuggestions() {
    if (!state.currentId) {
      $("applyMsg").className = "form-msg error";
      $("applyMsg").innerHTML = `Add a home first, then apply these suggestions to its plan.`;
      return;
    }
    const picked = [...$("suggList").querySelectorAll("input:checked")].map((el) => v.result.suggestions[Number(el.dataset.i)]);
    if (!picked.length) { $("applyMsg").className = "form-msg error"; $("applyMsg").textContent = "Tick at least one suggestion first."; return; }
    const added = [], removed = [];
    picked.forEach((s) => {
      if (s.kind === "add_measure" && !state.selected.has(s.measure_code)) { state.selected.add(s.measure_code); added.push(measureName(s.measure_code)); }
      if (s.kind === "already_installed" && state.selected.has(s.measure_code)) { state.selected.delete(s.measure_code); removed.push(measureName(s.measure_code)); }
    });
    $("applyMsg").className = "form-msg ok";
    $("applyMsg").textContent = added.length || removed.length
      ? `Plan updated${added.length ? `: added ${added.join(", ")}` : ""}${removed.length ? `${added.length ? ";" : ":"} removed ${removed.join(", ")}` : ""}.`
      : "Your plan already reflects these suggestions.";
    renderMeasures();
    scheduleEvaluation(0);
    setTimeout(() => { activateTab("assessment"); window.scrollTo({ top: 0, behavior: "smooth" }); }, 700);
  }

  /* ---------- Live camera ----------
     About one frame a second goes to POST /vision/live (fast path: trained detectors only) and
     the boxes are drawn over the moving video. One frame in flight at a time; frames are
     shrunk to 960 px and never stored. "Capture & analyse" runs the full photo analysis. */
  const LIVE_MIN_GAP_MS = 400;     // pause between frames after a reply
  const LIVE_MAX_SIDE = 960;
  const live = { on: false, busy: false, mode: "exterior", facing: "environment", timer: null, abort: null, result: null, backoff: 0 };

  function setChip(text, state = "") {
    $("liveChipText").textContent = text;
    $("liveChip").dataset.state = state;
  }

  async function startCamera() {
    if (!navigator.mediaDevices?.getUserMedia) {
      alert(window.isSecureContext ? "This browser has no camera support. Upload a photo instead."
        : "The camera only works over https or on localhost. Upload a photo instead.");
      return;
    }
    stopCamera();
    try {
      v.stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: live.facing, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false,
      });
    } catch (err) {
      alert(err?.name === "NotAllowedError" ? "Camera permission was blocked. Allow it in the address bar, then try again."
        : "No camera found. Upload a photo instead.");
      return;
    }
    const video = $("cameraVideo");
    video.srcObject = v.stream;
    showStage("camera");
    live.result = null;
    $("liveSummary").textContent = "";
    await new Promise((r) => (video.readyState >= 2 ? r() : (video.onloadeddata = r)));
    live.on = $("liveToggle").getAttribute("aria-pressed") === "true";
    setChip(live.on ? "Live · looking…" : "Paused", live.on ? "live" : "paused");
    drawLive();
    scheduleFrame(0);
  }

  function stopCamera() {
    clearTimeout(live.timer);
    live.abort?.abort();
    Object.assign(live, { busy: false, timer: null, abort: null, result: null, backoff: 0 });
    v.stream?.getTracks().forEach((t) => t.stop());
    v.stream = null;
    if ($("cameraVideo")) $("cameraVideo").srcObject = null;
  }

  function scheduleFrame(delay) {
    clearTimeout(live.timer);
    live.timer = setTimeout(sendFrame, delay);
  }

  function grabFrame(maxSide) {
    const video = $("cameraVideo");
    const scale = Math.min(1, maxSide / Math.max(video.videoWidth, video.videoHeight));
    const c = document.createElement("canvas");
    c.width = Math.round(video.videoWidth * scale);
    c.height = Math.round(video.videoHeight * scale);
    c.getContext("2d").drawImage(video, 0, 0, c.width, c.height);
    return c;
  }

  async function sendFrame() {
    if (!v.stream || !live.on || live.busy) return;
    if (document.hidden || $("panel-vision").hidden) { scheduleFrame(1000); return; }  // tab not visible
    live.busy = true;
    live.abort = new AbortController();
    const t0 = performance.now();
    try {
      const blob = await new Promise((r) => grabFrame(LIVE_MAX_SIDE).toBlob(r, "image/jpeg", 0.72));
      const result = await EcoVision.live(blob, live.mode, live.abort.signal);
      if (!v.stream || !live.on) return;
      live.result = result;
      live.backoff = 0;
      const ms = Math.round(performance.now() - t0);
      const n = result.detections.length;
      setChip(`Live · ${n} found · ${ms} ms`, "live");
      $("liveSummary").textContent = n
        ? Object.entries(result.counts).map(([k, c]) => `${c} × ${k}`).join(" · ")
        : "Nothing detected yet. Hold steady and fit the whole wall or roof in view.";
      drawLive();
    } catch (err) {
      if (err.name === "AbortError") return;
      live.backoff = Math.min(8000, (live.backoff || 1000) * 2);
      setChip(err.status === 429 ? "Busy · slowing down" : "Connection problem · retrying", "warn");
    } finally {
      live.busy = false;
      live.abort = null;
      if (v.stream && live.on) scheduleFrame(LIVE_MIN_GAP_MS + live.backoff);
    }
  }

  function drawLive() {
    EcoVision.draw($("liveCanvas"), $("cameraVideo"), live.result, { labels: true });
  }

  function setLiveMode(mode) {
    live.mode = mode;
    live.result = null;
    drawLive();
    document.querySelectorAll("[data-live-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.liveMode === mode)));
    if (live.on) { live.abort?.abort(); live.busy = false; setChip("Live · looking…", "live"); scheduleFrame(0); }
  }

  function toggleLive() {
    live.on = !live.on;
    $("liveToggle").setAttribute("aria-pressed", String(live.on));
    $("liveToggle").textContent = live.on ? "Pause" : "Resume";
    if (live.on) { setChip("Live · looking…", "live"); scheduleFrame(0); }
    else { clearTimeout(live.timer); live.abort?.abort(); setChip("Paused", "paused"); }
  }

  function flipCamera() {
    live.facing = live.facing === "environment" ? "user" : "environment";
    startCamera();
  }

  function snap() {
    const mode = live.mode;
    grabFrame(1920).toBlob((b) => {
      setMode(mode);
      loadFile(new File([b], "camera.jpg", { type: "image/jpeg" }));
    }, "image/jpeg", 0.92);
  }

  async function loadSample(path, mode) {
    const blob = await (await fetch(path)).blob();
    setMode(mode);
    loadFile(new File([blob], path.split("/").pop(), { type: blob.type || "image/jpeg" }));
  }

  function setMode(mode) {
    v.mode = mode;
    document.querySelectorAll(".seg [data-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
  }

  async function loadStatus() {
    try {
      const s = await ecoFetch("/vision/status");
      const all = [s, ...Object.values(s.trained_detectors || {}), s.segmenter || {}];
      v.samReady = !!s.segmenter?.loaded;
      if (all.some((m) => m.loading || (!m.loaded && !m.error))) setTimeout(loadStatus, 4000);
      if (v.samReady && v.result && !v.result.detections.some((d) => d.sam)) refineWithSam(v.file, v.result);
    } catch (_) { /* offline: photos still work with the fallbacks */ }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const dz = $("dropzone");
    ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
    dz.addEventListener("drop", (e) => loadFile(e.dataTransfer.files[0]));
    document.addEventListener("paste", (e) => {
      if ($("panel-vision").hidden) return;
      const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith("image/"));
      if (item) loadFile(item.getAsFile());
    });
    $("visionFile").addEventListener("change", (e) => loadFile(e.target.files[0]));
    $("cameraBtn").addEventListener("click", startCamera);
    $("cameraCancel").addEventListener("click", () => { stopCamera(); showStage("empty"); });
    $("snapBtn").addEventListener("click", snap);
    $("liveToggle").addEventListener("click", toggleLive);
    $("cameraFlip").addEventListener("click", flipCamera);
    document.querySelectorAll("[data-live-mode]").forEach((b) => b.addEventListener("click", () => setLiveMode(b.dataset.liveMode)));
    window.addEventListener("resize", () => { if (v.stream) drawLive(); });
    document.addEventListener("visibilitychange", () => { if (!document.hidden && v.stream && live.on) scheduleFrame(0); });
    // Leaving the Vision tab turns the camera (and its light) off.
    new MutationObserver(() => {
      if ($("panel-vision").hidden && v.stream) { stopCamera(); showStage("empty"); }
    }).observe($("panel-vision"), { attributes: true, attributeFilter: ["hidden"] });
    $("sampleExterior").addEventListener("click", () => loadSample("assets/eco_house_3d.webp", "exterior"));
    $("sampleThermal").addEventListener("click", () => loadSample("assets/thermal_sample.jpg", "thermal"));
    $("visionReset").addEventListener("click", reset);
    $("showLabels").addEventListener("change", redraw);
    $("applySuggestions").addEventListener("click", applySuggestions);
    document.querySelectorAll(".seg [data-mode]").forEach((b) => b.addEventListener("click", () => { setMode(b.dataset.mode); run(); }));
    addEventListener("resize", redraw);
    new ResizeObserver(redraw).observe(img());
    loadStatus();
    // Demo deep links: app.html?sample=photo#vision or ?sample=thermal#vision
    const sample = new URLSearchParams(location.search).get("sample");
    if (sample === "photo") loadSample("assets/eco_house_3d.webp", "exterior");
    if (sample === "thermal") loadSample("assets/thermal_sample.jpg", "thermal");
  });
})();
