// Lists: bullets, numbered steps, cards, a stack of layers, a timeline, a checklist whose ticks draw
// in, or a quiz whose answer shows when the narration points at it.

import { icon } from "../deco.js";
import { el } from "../draw.js";
import { inline } from "../util.js";

const TICK = '<svg viewBox="0 0 24 24" width="30" height="30"><rect x="2.5" y="2.5" width="19" height="19" rx="5" class="box"/><path d="M7 12.5l3.2 3.2L17 8.8" class="mark" pathLength="1"/></svg>';

export default {
  render(slide, body, parts, ctx) {
    const v = slide.visual || {};
    const style = v.style || "bullets";
    const list = el(style === "cards" || style === "stack" ? "div" : style === "steps" || style === "timeline" || style === "quiz" ? "ol" : "ul", `list ${style}`);
    // A timeline runs across a landscape slide, and down a tall one or when it has many dates.
    if (style === "timeline" && (ctx.frame !== "landscape" || v.items.length > 6)) list.classList.add("down");
    if (style === "cards") {
      const n = v.items.length;
      const cols = ctx.frame === "portrait" ? 1 : ctx.frame === "square" ? (n <= 2 ? n : 2) : n <= 3 ? n : n === 4 ? 2 : 3;
      list.style.gridTemplateColumns = `repeat(${Math.max(1, cols)}, minmax(0, 1fr))`;
    }
    v.items.forEach((it, i) => {
      const tick = style === "checklist" ? `<span class="tick${it.check ? " todo-done" : ""}" aria-hidden="true">${TICK}</span>` : "";
      const ic = style === "checklist" || style === "timeline" || style === "quiz" ? "" : icon(slide, it.icon);
      const text = `<div class="entry-text">${it.title ? `<h3>${inline(it.title)}</h3>` : ""}${it.body ? `<p>${inline(it.body)}</p>` : ""}</div>`;
      const item = el(style === "cards" || style === "stack" ? "div" : "li", `entry${ic ? " has-icon" : ""}`, `${tick}${ic}${text}`);
      if (style === "checklist") item.setAttribute("aria-label", `${it.check ? "Done" : "Not done"}: ${it.title || it.body}`);
      if (style === "stack") item.style.setProperty("--layer", i);
      if (style === "steps") item.dataset.n = i + 1;
      if (style === "quiz") quizChoice(item, it, i, parts);
      item.dataset.id = it.id;
      item.dataset.idx = i;
      parts.items.set(it.id, item);
      list.append(item);
    });
    parts.visual = list;
    body.append(list);
  },

  // Checklist ticks draw in as their item is pointed at, or all at once when none are.
  frame(st, slide, parts) {
    if (slide.visual.style === "quiz") return quizFrame(st, slide, parts);
    if (slide.visual.style !== "checklist") return;
    const cued = new Set(st.cues.filter((c) => c.kind === "item").map((c) => c.id));
    const any = slide.says.some((x) => (x.cues || []).some((c) => c.kind === "item"));
    const ticked = slide.visual.items.filter((it) => !!it.check && (cued.has(it.id) || !any || st.revealAll));
    for (const it of slide.visual.items) parts.items.get(it.id)?.classList.toggle("ticked", ticked.includes(it));
    return ticked.map((it) => it.id).join("|");
  },
};

// A quiz choice: lettered, and a viewer can pick one before the answer shows.
function quizChoice(item, it, i, parts) {
  item.dataset.letter = String.fromCharCode(65 + i);
  if (it.check) item.classList.add("correct");
  if (document.body.classList.contains("capture")) return;
  item.tabIndex = 0;
  item.setAttribute("role", "button");
  const pick = () => {
    if (item.parentElement.classList.contains("revealed")) return;
    for (const e of parts.items.values()) e.classList.toggle("picked", e === item && !e.classList.contains("picked"));
    item.setAttribute("aria-pressed", item.classList.contains("picked"));
  };
  item.addEventListener("click", pick);
  // Stopped here, so Space picks a choice without also playing or pausing.
  item.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); pick(); } });
}

// The answer shows once a cue points at a correct choice, or when the narration ends if none
// does. A pause paragraph before it is thinking time: a bar runs down under the choices. Cues
// inferred from reading the choices out highlight them without giving the answer away.
function quizFrame(st, slide, parts) {
  const list = parts.visual;
  const right = new Set(slide.visual.items.filter((it) => it.check).map((it) => it.id));
  const answers = (c) => c.kind === "item" && !c.inferred && right.has(c.id);
  const aimed = slide.says.some((x) => (x.cues || []).some(answers));
  const shown = st.cues.some(answers) || (!aimed && st.revealAll);
  list.classList.toggle("revealed", shown);
  const say = slide.says[st.say];
  const think = !shown && say?.pause ? Math.min(1, Math.max(0, st.local) / say.pause) : -1;
  list.classList.toggle("thinking", think >= 0);
  if (think >= 0) list.style.setProperty("--think", (1 - think).toFixed(3));
  return `${shown}|${think >= 0 ? Math.round(think * 30) : ""}`;
}
