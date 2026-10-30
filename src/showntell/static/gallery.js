// The gallery's Delete buttons: a first click arms, a second within three seconds deletes.
document.getElementById("list").addEventListener("click", async (e) => {
  const btn = e.target.closest(".del");
  if (!btn) return;
  const li = btn.closest("li");
  if (btn.dataset.armed !== "1") { btn.dataset.armed = "1"; btn.textContent = "Confirm"; setTimeout(() => { btn.dataset.armed = ""; btn.textContent = "Delete"; }, 3000); return; }
  const r = await fetch(`/w/${li.dataset.id}/delete`, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": document.body.dataset.csrf || "" }, body: "{}" });
  if (r.ok) li.remove();
});

// Hosted mode with provider sign-in: the footer's Sign out button. It stays on this page: going on
// to /login would send the browser to the provider, which signs it straight back in while the
// provider's own session lasts.
document.querySelector(".signout")?.addEventListener("click", async () => {
  const r = await fetch("/logout", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": document.body.dataset.csrf || "" }, body: "{}" });
  const main = document.querySelector("main");
  const note = document.createElement("p");
  note.className = "lede";
  if (!r.ok) {
    note.textContent = "Signing out failed. Reload the page and try again.";
    main.append(note);
    return;
  }
  const again = document.createElement("a");
  again.href = "/login";
  again.textContent = "Sign in again";
  note.append("Signed out of showntell. Your company sign-in may still be open in this browser, so signing in again may not ask for a password. ", again);
  main.replaceChildren(main.querySelector("header"), note);
});
