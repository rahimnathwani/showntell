// The owner's Share control (hosted mode only): a "Share…" row in the settings menu opens a dialog
// listing who may watch, with Remove buttons, an address to add and the company-wide switch. Every
// change goes through /w/<id>/shares, which applies the same checks as share_walkthrough.

const csrf = document.body.dataset.csrf || "";
const menu = document.getElementById("menu");

const el = (tag, props = {}, ...kids) => {
  const e = Object.assign(document.createElement(tag), props);
  e.append(...kids);
  return e;
};

const row = el("button", { className: "row link", id: "share-open", textContent: "Share…" });
menu.insertBefore(row, document.getElementById("present"));

const list = el("ul", { className: "share-list" });
const email = el("input", { type: "email", placeholder: "name@company.com", autocomplete: "off", ariaLabel: "Email address to share with" });
const add = el("button", { type: "submit", textContent: "Add" });
const company = el("input", { type: "checkbox", id: "share-company" });
const status = el("p", { className: "share-status", role: "status" });
const dialog = el(
  "div",
  { id: "share", className: "share", hidden: true, role: "dialog", ariaModal: "true", ariaLabel: "Share this walkthrough" },
  el(
    "div",
    { className: "share-card" },
    el("h2", { textContent: "Share this walkthrough" }),
    el("p", { className: "share-hint", textContent: "Only you can open it until you share it. People you add can watch, not change it." }),
    list,
    el("form", { className: "share-add" }, email, add),
    el("label", { className: "share-company" }, company, " Everyone who can sign in to this server"),
    status,
    el("div", { className: "share-actions" }, el("button", { type: "button", className: "share-done", textContent: "Done" })),
  ),
);
document.body.append(dialog);

function draw(state) {
  list.replaceChildren(
    ...(state.shares.length ? state.shares : [null]).map((who) => {
      if (who === null) return el("li", { className: "share-none", textContent: "Not shared with anyone by name." });
      const remove = el("button", { type: "button", textContent: "Remove", ariaLabel: `Stop sharing with ${who}` });
      remove.onclick = () => change({ remove: [who] });
      return el("li", {}, el("span", { textContent: who }), remove);
    }),
  );
  company.checked = state.company;
  email.placeholder = state.domains.length ? `name@${state.domains[0]}` : "name@company.com";
}

async function call(body) {
  const r = await fetch("shares", {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({ error: "Something went wrong." }));
  if (!r.ok) throw new Error(data.error || "Something went wrong.");
  return data;
}

async function change(body) {
  status.textContent = "Saving…";
  try {
    const state = await call(body);
    draw(state);
    // Shares match the address people sign in with, which may not be the one the owner knows.
    status.textContent = state.unknown?.length
      ? `Saved. Nobody has signed in here as ${state.unknown.join(", ")} yet. Shares match the address people sign in with; if they still can't open it after signing in, add that address instead.`
      : "Saved.";
    return true;
  } catch (e) {
    status.textContent = e.message;
    return false;
  }
}

row.onclick = async () => {
  menu.hidden = true;
  document.getElementById("more")?.setAttribute("aria-expanded", "false");
  dialog.hidden = false;
  status.textContent = "";
  try {
    draw(await call());
  } catch (e) {
    status.textContent = e.message;
  }
  email.focus();
};
dialog.querySelector("form").onsubmit = async (e) => {
  e.preventDefault();
  if (email.value.trim() && (await change({ add: [email.value.trim()] }))) email.value = "";
};
company.onchange = () => change({ company: company.checked });
const close = () => { dialog.hidden = true; row.focus(); };
dialog.querySelector(".share-done").onclick = close;
dialog.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } });
dialog.addEventListener("click", (e) => { if (e.target === dialog) close(); });
