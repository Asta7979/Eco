/**
 * Project Eco: computer vision client (shared by the landing page and the app).
 *   EcoVision.analyze(fileOrBlob, mode)  -> POST /vision/analyze (multipart), returns the API result
 *   EcoVision.draw(canvas, img, result, { highlight })  draws boxes / hotspot outlines over the image
 *   EcoVision.colorFor(label)
 */

const EcoVision = (() => {
  const COLORS = {
    "Window": "#2450FF",
    "Door": "#7C5CFF",
    "Solar panel": "#00B3FF",
    "Chimney": "#FF7A1A",
    "Outdoor heat-pump / AC unit": "#22C55E",
    "Satellite dish": "#A3A3A3",
    "Garage door": "#9B8CFF",
    "Roof": "#64748B",
    "Basement opening": "#0EA5A4",
  };

  function colorFor(label) {
    if (label.startsWith("Hotspot")) return "#FF3B5C";
    return COLORS[label] || "#2450FF";
  }

  async function analyze(file, mode = "auto") {
    const body = new FormData();
    body.append("file", file, file.name || "photo.jpg");
    body.append("mode", mode);
    const res = await fetch(`${ECO_API_BASE}/vision/analyze`, { method: "POST", body });
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try { detail = (await res.json()).detail || detail; } catch (_) { /* keep status */ }
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return res.json();
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function draw(canvas, img, result, { highlight = null, hidden = new Set(), labels = true } = {}) {
    const rect = img.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);
    if (!result) return;

    const W = rect.width, H = rect.height;
    const dets = result.detections.filter((d) => !hidden.has(d.id));
    const focus = highlight != null;

    // Dim everything outside the highlighted detection.
    if (focus) {
      const d = dets.find((x) => x.id === highlight);
      if (d) {
        ctx.save();
        ctx.fillStyle = "rgba(6,7,9,.45)";
        ctx.fillRect(0, 0, W, H);
        ctx.globalCompositeOperation = "destination-out";
        const [x1, y1, x2, y2] = d.box;
        ctx.fillRect(x1 * W, y1 * H, (x2 - x1) * W, (y2 - y1) * H);
        ctx.restore();
      }
    }

    const placed = [];
    dets.forEach((d) => {
      const color = colorFor(d.label);
      const active = !focus || d.id === highlight;
      ctx.globalAlpha = active ? 1 : 0.25;
      ctx.lineWidth = d.id === highlight ? 3 : 2;
      ctx.strokeStyle = color;

      if (d.polygon && d.polygon.length > 2) {
        ctx.beginPath();
        d.polygon.forEach(([px, py], i) => (i ? ctx.lineTo(px * W, py * H) : ctx.moveTo(px * W, py * H)));
        ctx.closePath();
        ctx.fillStyle = `${color}40`;
        ctx.fill();
        ctx.setLineDash([]);
        ctx.stroke();
      } else {
        const [x1, y1, x2, y2] = d.box;
        const x = x1 * W, y = y1 * H, w = (x2 - x1) * W, h = (y2 - y1) * H;
        ctx.setLineDash(d.source === "opencv-geometry" ? [5, 4] : []);
        roundRect(ctx, x, y, w, h, 4);
        ctx.fillStyle = `${color}14`;
        ctx.fill();
        ctx.stroke();
        ctx.setLineDash([]);
        // Corner ticks for a "scanner" feel.
        const t = Math.min(12, w / 4, h / 4);
        ctx.lineWidth = 3.5;
        [[x, y, 1, 1], [x + w, y, -1, 1], [x, y + h, 1, -1], [x + w, y + h, -1, -1]].forEach(([cx, cy, sx, sy]) => {
          ctx.beginPath(); ctx.moveTo(cx, cy + sy * t); ctx.lineTo(cx, cy); ctx.lineTo(cx + sx * t, cy); ctx.stroke();
        });
      }

      if (labels && active) {
        const text = `${d.label.replace("Hotspot · ", "")} ${Math.round(d.confidence * 100)}%`;
        ctx.font = "500 11px Geist, system-ui, sans-serif";
        const tw = ctx.measureText(text).width + 12;
        const bx = Math.min(Math.max(0, d.box[0] * W), W - tw);
        let by = Math.max(0, d.box[1] * H - 20);
        while (placed.some((p) => bx < p.x + p.w && p.x < bx + tw && Math.abs(by - p.y) < 19) && by < H - 18) by += 20;
        placed.push({ x: bx, y: by, w: tw });
        ctx.fillStyle = color;
        roundRect(ctx, bx, by, tw, 18, 5);
        ctx.fill();
        ctx.fillStyle = "#fff";
        ctx.fillText(text, bx + 6, by + 12.5);
      }
    });
    ctx.globalAlpha = 1;
  }

  async function segment(file, boxes) {
    const body = new FormData();
    body.append("file", file, file.name || "photo.jpg");
    body.append("boxes", JSON.stringify(boxes));
    const res = await fetch(`${ECO_API_BASE}/vision/segment`, { method: "POST", body });
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try { detail = (await res.json()).detail || detail; } catch (_) { /* keep status */ }
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return res.json();
  }

  /** One camera frame on the fast path. `signal` lets the caller abort when the camera closes. */
  async function live(blob, mode, signal) {
    const body = new FormData();
    body.append("file", blob, "frame.jpg");
    body.append("mode", mode);
    const headers = {};
    const token = await window.EcoAuth?.getToken?.();
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`${ECO_API_BASE}/vision/live`, { method: "POST", body, headers, signal });
    if (!res.ok) {
      const err = new Error(res.status === 429 ? "Too many frames; slowing down." : `${res.status} ${res.statusText}`);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  return { analyze, segment, live, draw, colorFor };
})();

window.EcoVision = EcoVision;
