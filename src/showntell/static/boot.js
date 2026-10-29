// Runs in the page head before the stylesheets, as a classic script, so a page served with a
// Content-Security-Policy that forbids inline script still gets both.

// The viewer's own theme choice (?theme= or the menu) wins over the walkthrough's, before first paint.
(function () {
  var t = new URLSearchParams(location.search).get("theme");
  try { t = t || JSON.parse(localStorage.getItem("showntell.theme") || "null"); } catch (e) {}
  if (t && /^(dark|light|paper|contrast|night|ocean|studio)$/.test(t)) document.documentElement.dataset.theme = t;
})();

// Errors before the player's modules load (a missing vendor file, a syntax error) would otherwise
// go unreported: keep them, and send them ourselves if the player never starts.
(function () {
  var early = window.__showntellEarly = [];
  addEventListener("error", function (e) {
    var t = e.target, src = t && (t.src || t.href);
    early.push(src && t !== window ? "could not load " + src : String(e.message || "script error"));
  }, true);
  addEventListener("unhandledrejection", function (e) { early.push(String((e.reason && e.reason.message) || e.reason)); });
  setTimeout(function () {
    var key = document.body && document.body.dataset.reportKey;
    if (window.__showntellStarted || !early.length || !key) return;
    var headers = { "Content-Type": "application/json", "X-Report-Key": key };
    if (document.body.dataset.csrf) headers["X-CSRF-Token"] = document.body.dataset.csrf;
    fetch("report", { method: "POST", headers: headers,
      body: JSON.stringify({ code: "player_error", message: "The player did not start: " + early.join("; ") }) });
  }, 8000);
})();
