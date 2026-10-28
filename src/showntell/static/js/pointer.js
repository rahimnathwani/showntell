// The pointer: where a cue points, the mark drawn around it, and scrolling long code to the cued
// lines. The camera, which zooms onto a target, is in camera.js.

import { textRange } from "./draw.js";
import { kindOf } from "./render.js";
import { body, FRAMES, pointer, reducedMotion, report, S, scale, stage } from "./state.js";
import { render } from "./view.js";
import { norm } from "./util.js";

// Long code (`scroll`) scrolls so the latest cued lines sit in the middle; other lines dim.
// Returns true while it is moving; the pointer is placed when it lands.
export function scrollCode(st, cueKey) {
  const { view } = S;
  const box = view.parts.visual;
  if (!box?.classList?.contains("scroll")) return false;
  const code = box.querySelector(".code");
  // The latest line cue since the code last changed version: a step cue clears the focus.
  const latest = [...st.cues].reverse().find((c) => c.kind === "lines" || c.kind === "step");
  const last = latest?.kind === "lines" ? latest : null;
  box.classList.toggle("focusing", !!last);
  let y = 0;
  if (last) {
    const lines = [];
    for (let n = last.l0; n <= last.l1; n++) if (view.parts.lines.new.get(n)) lines.push(view.parts.lines.new.get(n));
    if (lines.length) {
      const top = lines[0].offsetTop, bottom = lines[lines.length - 1].offsetTop + lines[lines.length - 1].offsetHeight;
      const room = box.clientHeight - (code.offsetTop || 0);
      y = Math.max(0, Math.min(code.scrollHeight - room, (top + bottom) / 2 - room / 2));
    }
  }
  return move(code, `translateY(${-Math.round(y)}px)`, cueKey);
}

// Apply a transform; animate it while playing, jump when seeking or capturing.
function move(el, transform, cueKey) {
  if (el.style.transform === transform) return false;
  const animate = S.playing && !body.classList.contains("capture") && !reducedMotion();
  if (!animate) {
    el.style.transition = "none";
    el.style.transform = transform;
    void el.offsetWidth;
    el.style.transition = "";
    return false;
  }
  el.style.transform = transform;
  pointer.hidden = true;
  const done = (e) => {
    if (e.target !== el) return;
    el.removeEventListener("transitionend", done);
    if (S.lastCueKey === cueKey) { S.lastCueKey = ""; render(true); }
  };
  el.addEventListener("transitionend", done);
  return true;
}

// Where a cue points: a list of DOMRect-like boxes, plus side effects (active classes, highlights).
// The slide's kind answers first (image regions, diagram nodes); then lines, items, rows and text.
export function cueTarget(cue, slide, strict = false) {
  if (!cue) return null;
  // On a `.pair` slide a cue points into the pane it names; quoted text is looked for in both.
  if (cue.kind === "text" && cue.pane === undefined && S.view.panes?.length > 1) {
    for (const k of [0, 1]) {
      const r = cueTarget({ ...cue, pane: k }, slide, true);
      if (r) return r;
    }
    return wholeVisual(S.view.panes[0].parts);
  }
  const pane = S.view.panes?.[cue.pane || 0];
  const p = pane?.parts || S.view.parts;
  if (pane) slide = pane.slide;
  const whole = () => (strict ? null : wholeVisual(p));
  if (cue.kind === "step" || cue.kind === "edge") return null; // the code's change, or the moving dot, is the pointer
  const own = kindOf(slide).target?.(cue, p, { wholeVisual: whole });
  if (own !== undefined) return own;
  const range = (r) => { if (window.CSS?.highlights) CSS.highlights.set("ptr", new Highlight(r)); };
  if (cue.kind === "lines") {
    const map = p.lines[cue.side || "new"] || p.lines.new;
    const els = [];
    for (let n = cue.l0; n <= cue.l1; n++) if (map.get(n)) els.push(map.get(n));
    if (!els.length) return null;
    if (cue.text) {
      for (const ln of els) {
        const r = findText(ln.querySelector(".tx"), cue.text);
        if (r) { ln.classList.add("hl"); range(r); return { rects: [...r.getClientRects()] }; }
      }
    }
    els.forEach((e) => e.classList.add("hl"));
    return { rects: els.map((e) => e.querySelector(".tx").getBoundingClientRect()) };
  }
  if (cue.kind === "item" || cue.kind === "row") {
    const e = p.items.get(cue.id) || p.rows.get(cue.id);
    if (!e) return null;
    e.classList.add("active");
    e.parentElement.classList.add("has-active");
    if (cue.kind === "item") return { rects: e instanceof SVGElement ? [e.getBoundingClientRect()] : [] }; // a card's own highlight is the pointer
    return { rects: [e.getBoundingClientRect()] };
  }
  if (cue.kind === "text" && cue.text) {
    for (const side of ["new", "old"]) {
      for (const ln of p.lines[side].values()) {
        const r = findText(ln.querySelector(".tx"), cue.text);
        if (r) { ln.classList.add("hl"); range(r); return { rects: [...r.getClientRects()] }; }
      }
    }
    for (const e of [...p.items.values(), ...p.rows.values()]) {
      if (norm(e.textContent).includes(norm(cue.text))) {
        e.classList.add("active");
        e.parentElement.classList.add("has-active");
        return { rects: [e.getBoundingClientRect()] };
      }
    }
    // KaTeX keeps an invisible MathML copy first; search only what is drawn.
    const drawn = p.visual?.querySelector?.(".katex-html") || p.visual;
    const r = drawn && findText(drawn, cue.text);
    if (r) { range(r); return { rects: [...r.getClientRects()] }; }
  }
  return whole();
}

function wholeVisual(parts = S.view.parts) {
  const v = parts.visual;
  if (!v || S.shown.slide < 0 || kindOf(S.W.slides[S.shown.slide]).bare) return null;
  const t = v.querySelector?.(".katex-display") || parts.svg || v;
  return { rects: [t.getBoundingClientRect()], wide: true };
}

function findText(container, text) {
  if (!container || !text) return null;
  const hay = container.textContent;
  const i = hay.indexOf(text);
  if (i >= 0) return textRange(container, i, i + text.length);
  // Tolerate differences in spacing: search a collapsed copy, keeping a map back to offsets.
  let flat = "";
  const map = [];
  for (let k = 0; k < hay.length; k++) {
    const space = /\s/.test(hay[k]);
    if (space && (flat.endsWith(" ") || !flat)) continue;
    flat += space ? " " : hay[k];
    map.push(k);
  }
  const want = text.replace(/\s+/g, " ").trim();
  const j = flat.indexOf(want);
  if (j < 0 || !want) return null;
  return textRange(container, map[j], map[j + want.length - 1] + 1);
}

// Pointer styles from a cue's {.circle}, {.underline}, {.arrow} or {.highlight}; drawn as SVG
// that inks itself in, so a new cue draws a new mark.
const MARKS = {
  circle: '<svg viewBox="0 0 100 100" preserveAspectRatio="none"><path pathLength="1" d="M52 4 C80 3 97 22 96 50 C95 78 74 97 48 96 C22 95 3 76 4 49 C5 24 26 5 56 7"/></svg>',
  underline: '<svg viewBox="0 0 100 10" preserveAspectRatio="none"><path pathLength="1" d="M2 6 C20 2 34 9 52 5 S84 3 98 6"/></svg>',
  arrow: '<svg viewBox="0 0 100 60"><path pathLength="1" d="M4 8 C30 10 58 22 84 44 M70 46 L86 46 L84 30"/></svg>',
};
// `jump` places it without gliding: after a camera move, it appears on the new subject.
export function placePointer(rects, wide, style = "", jump = false) {
  rects = rects.filter((r) => r && (r.width || r.height));
  if (!rects.length) { pointer.hidden = true; return; }
  if (!style.includes("highlight")) pointer.dataset.swept = "";
  if (jump) pointer.style.transition = "none";
  const kind = ["circle", "underline", "arrow", "highlight"].find((s) => style.includes(s)) || "box";
  if (pointer.dataset.kind !== kind || kind !== "box") {
    pointer.dataset.kind = kind;
    pointer.innerHTML = MARKS[kind] || "";
  }
  const l = Math.min(...rects.map((r) => r.left)), t = Math.min(...rects.map((r) => r.top));
  const r = Math.max(...rects.map((r) => r.right)), b = Math.max(...rects.map((r) => r.bottom));
  const sr = stage.getBoundingClientRect(), k = scale(), pad = wide ? 12 : 6;
  const box = [(l - sr.left) / k - pad, (t - sr.top) / k - pad, (r - l) / k + pad * 2, (b - t) / k + pad * 2];
  const was = ["left", "top", "width", "height"].map((p) => parseFloat(pointer.style[p]) || 0);
  if (kind === "highlight" && box.some((x, i) => Math.abs(x - was[i]) > 8)) sweep(was, !pointer.hidden && pointer.dataset.swept === "1");
  [pointer.style.left, pointer.style.top, pointer.style.width, pointer.style.height] = box.map((x) => `${x}px`);
  pointer.classList.toggle("wide", !!wide && kind === "box");
  pointer.hidden = false;
  if (jump) { void pointer.offsetWidth; pointer.style.transition = ""; }
  if (S.zoomed) return; // zoomed in: the target may be cropped on purpose
  const [w, h] = FRAMES[S.W.frame] || FRAMES.landscape;
  const x = (l - sr.left) / k, y = (t - sr.top) / k;
  if (x < -4 || y < -4 || x + (r - l) / k > w + 4 || y + (b - t) / k > h + 4) {
    const id = S.W.slides[S.shown.slide]?.id || "";
    report(`Slide ${id}: a cue points at something partly outside the visible slide.`, "pointer_offstage", id);
  }
}

// A highlight does not glide: it sweeps across its new target left to right, and the one it
// leaves stays a moment, fading about a second later. Frame capture shows it in place, unswept.
function sweep(was, leaving) {
  const still = body.classList.contains("capture") || reducedMotion();
  if (leaving && !still) {
    const trail = document.createElement("div");
    trail.className = "pointer-trail";
    [trail.style.left, trail.style.top, trail.style.width, trail.style.height] = was.map((x) => `${x}px`);
    trail.addEventListener("animationend", () => trail.remove());
    stage.append(trail);
  }
  pointer.style.transition = "none";
  pointer.classList.remove("sweep");
  void pointer.offsetWidth;
  pointer.style.transition = "";
  if (!still) pointer.classList.add("sweep");
  pointer.dataset.swept = "1";
}
