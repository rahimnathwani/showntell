// The gallery's Delete buttons: a first click arms, a second within three seconds deletes.
document.getElementById("list").addEventListener("click", async (e) => {
  const btn = e.target.closest(".del");
  if (!btn) return;
  const li = btn.closest("li");
  if (btn.dataset.armed !== "1") { btn.dataset.armed = "1"; btn.textContent = "Confirm"; setTimeout(() => { btn.dataset.armed = ""; btn.textContent = "Delete"; }, 3000); return; }
  const r = await fetch(`/w/${li.dataset.id}/delete`, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": document.body.dataset.csrf || "" }, body: "{}" });
  if (r.ok) li.remove();
});
