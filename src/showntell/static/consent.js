// The consent page's two buttons: tell the server, then follow it back to the app. A script
// navigates, not a form, so the CSP's form-action 'self' does not stop the redirect to the app.
for (const [id, approve] of [["allow", true], ["deny", false]]) {
  document.getElementById(id).addEventListener("click", async () => {
    for (const b of document.querySelectorAll("button")) b.disabled = true;
    const r = await fetch("/oauth/consent", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": document.body.dataset.csrf },
      body: JSON.stringify({ request: document.body.dataset.request, approve }),
    });
    const data = await r.json().catch(() => ({}));
    if (data.redirect) location.href = data.redirect;
    else document.querySelector("main").textContent = data.error || "Something went wrong; start again from your app.";
  });
}
