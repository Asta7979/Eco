/* Applies the saved (or ?theme=) colour theme before first paint. Loaded synchronously in <head>. */
try {
  var t = new URLSearchParams(location.search).get("theme") || localStorage.getItem("eco-theme");
  if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
} catch (e) { /* storage blocked: default theme */ }
