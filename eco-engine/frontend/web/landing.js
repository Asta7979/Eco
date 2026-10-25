/**
 * Landing page: a live computer vision demo. Portfolio data is private to each account,
 * so the public page never reads it.
 */

function setupVisionDemo() {
  const btn = document.getElementById("runVisionDemo");
  const img = document.getElementById("visionDemoImg");
  const canvas = document.getElementById("visionDemoCanvas");
  const out = document.getElementById("visionDemoResult");
  let last = null;

  btn.addEventListener("click", async () => {
    btn.disabled = true;
    out.innerHTML = `<span class="spinner" aria-hidden="true"></span>Detecting…`;
    try {
      const blob = await (await fetch(img.currentSrc || img.src)).blob();
      const file = new File([blob], blob.type === "image/webp" ? "sample.webp" : "sample.jpg", { type: blob.type || "image/jpeg" });
      last = await EcoVision.analyze(file, "exterior");
      EcoVision.draw(canvas, img, last);
      const counts = Object.entries(last.counts).map(([k, v]) => `<span class="badge">${v} × ${escapeHtml(k)}</span>`).join("");
      out.innerHTML = `${counts || "No objects found"}<span class="mono">${last.processing_ms} ms</span>`;
    } catch (err) {
      out.textContent = `Vision service unavailable: ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  });
  addEventListener("resize", () => last && EcoVision.draw(canvas, img, last));
}

document.addEventListener("DOMContentLoaded", () => {
  setupVisionDemo();
});
