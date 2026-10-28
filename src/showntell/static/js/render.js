// Build the DOM for one slide. Returns the element, a promise that resolves once it is laid
// out (diagrams and images load asynchronously), and handles the pointer and reveals use.
//
// Each slide kind is a module in kinds/ whose default export has
//   render(slide, body, parts, ctx)  fills the slide body and `parts`; may return a promise
//                                    that resolves once it is drawn
// and, optionally,
//   bare: true                       render(slide, root, parts) draws the whole slide, no header
//   layout(body, parts)              size the visual once drawn (default: fit)
//   reveal(reveal, parts, veil)      step reveals other than items, rows and lines
//   element(cue, parts)              the element holding a cue's label ({.decode}), null, or undefined
//                                    to leave it to lists and tables
//   target(cue, parts, help)         where a cue points ({rects, wide}), or undefined for the
//                                    generic pointer (lines, items, rows, text)
//   frame(st, slide, parts, t)       per-frame animation while playing or seeking
//   measure(slide, body, parts, report)  layout findings; true when it has checked the slide
// parts: visual (what the whole-slide pointer frames), lines {old, new} (line number -> row),
// items and rows (id -> element, with data-idx in source order), svg, wordRanges.

import { icon, leadingIcon } from "./deco.js";
import { el } from "./draw.js";
import { inline } from "./util.js";
import chart from "./kinds/chart.js";
import chat from "./kinds/chat.js";
import code from "./kinds/code.js";
import diagram from "./kinds/diagram.js";
import diff from "./kinds/diff.js";
import image from "./kinds/image.js";
import list from "./kinds/list.js";
import math from "./kinds/math.js";
import stat from "./kinds/stat.js";
import svg from "./kinds/svg.js";
import drawio from "./kinds/drawio.js";
import table from "./kinds/table.js";
import terminal from "./kinds/terminal.js";
import { chapter, title } from "./kinds/title.js";
import tree from "./kinds/tree.js";

export const KINDS = { title, chapter, code, diff, list, table, diagram, math, chart, stat, chat, terminal, tree, image, svg, drawio };

// Narration-only slides: a statement. The shown text is the sentence and the heading a kicker
// above it; without shown text the heading itself is the sentence.
const text = {
  bare: true,
  render(slide, root, parts) {
    const lines = (slide.shown || "").split(/\n+/).filter((x) => x.trim());
    const [say, ...more] = lines.length ? lines : [slide.title];
    const long = say.length > 120;
    root.innerHTML = `<div class="statement">${icon(slide, slide.icon, "big")}${lines.length ? `<div class="kick">${inline(slide.title)}</div>` : '<div class="bar"></div>'}`
      + `<p class="say${long ? " long" : ""}">${inline(say)}</p>${more.map((p) => `<p class="more">${inline(p)}</p>`).join("")}</div>`;
    parts.visual = root.querySelector(".say");
  },
};

export function kindOf(slide) {
  return KINDS[slide?.kind] || text;
}

const LAYOUTS = ["side", "pair", "full"];
export const layoutOf = (slide) => LAYOUTS.find((l) => slide.classes?.includes(l)) || "";

const emptyParts = () => ({ lines: { old: new Map(), new: new Map() }, items: new Map(), rows: new Map(), visual: null, svg: null, wordRanges: [] });

// The second visual of a `.pair` slide, as a slide of its own whose cues are those marked for it.
export function paneSlide(slide, pane) {
  if (!pane) return { ...slide, says: slide.says.map((x) => ({ ...x, cues: (x.cues || []).filter((c) => !c.pane) })) };
  return { ...slide, kind: slide.pair.kind, visual: slide.pair.visual, says: slide.says.map((x) => ({ ...x, cues: (x.cues || []).filter((c) => c.pane === 1) })) };
}

// ctx: frame, report, and where the slide sits: n (its number), chapter (the part it is in), prev.
export function renderSlide(slide, ctx) {
  const parts = emptyParts();
  const layout = layoutOf(slide);
  const root = el("section", `slide k-${slide.kind}${layout ? ` l-${layout}` : ""}`);
  root.setAttribute("aria-roledescription", "slide");
  if (slide.classes?.includes("build")) root.classList.add("build");
  const kind = kindOf(slide);
  if (kind.bare) {
    kind.render(slide, root, parts, ctx);
    return { root, ready: Promise.resolve(), parts };
  }
  // On a side slide the shown lines are takeaways beside the visual; otherwise a line under the title.
  const points = layout === "side" && slide.shown ? slide.shown.split(/\n+/).filter((x) => x.trim()) : [];
  const kicker = ctx?.n ? `<div class="kicker"><span class="n">${String(ctx.n).padStart(2, "0")}</span>${ctx.chapter ? `<span>${inline(ctx.chapter)}</span>` : ""}</div>` : "";
  const head = el("header", "slide-head", `${kicker}<h2>${inline(slide.title)}</h2>${slide.shown && !points.length ? `<p>${shownLine(slide, slide.shown)}</p>` : ""}`);
  const row = el("div", "slide-row");
  const body = el("div", "slide-body");
  row.append(body);
  root.append(head, row);
  const ready = [kind.render(paneSlide(slide, 0), body, parts, ctx)];
  const out = { root, parts, panes: [{ slide: paneSlide(slide, 0), parts, body, kind }] };
  if (slide.pair && KINDS[slide.pair.kind]) {
    const p2 = emptyParts(), b2 = el("div", "slide-body pane-2"), s2 = paneSlide(slide, 1);
    row.append(b2);
    ready.push(KINDS[s2.kind].render(s2, b2, p2, ctx));
    out.panes.push({ slide: s2, parts: p2, body: b2, kind: KINDS[s2.kind] });
  }
  if (points.length) {
    row.append(el("aside", "points", points.map((p, i) => {
      const [name, rest] = leadingIcon(p);
      const mark = icon(slide, name) || `<span class="num">${i + 1}</span>`;
      return `<div class="point">${mark}<div>${inline(rest)}</div></div>`;
    }).join("")));
  }
  out.ready = Promise.all(ready.map((r) => r || Promise.resolve()));
  return out;
}

// The takeaway under a title: each `>` line on its own line, led by its icon if it names one.
function shownLine(slide, text) {
  return text.split(/\n+/).filter((x) => x.trim()).map((ln) => {
    const [name, rest] = leadingIcon(ln);
    return `${icon(slide, name)}${name && slide.icons?.[name] ? " " : ""}${inline(rest)}`;
  }).join("<br>");
}
