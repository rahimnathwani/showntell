// Drawings: SVG the builder has already cleaned, drawn inside a shadow root so the drawing's own
// <style> rules stay inside it. Elements with ids are what cues point at; the outermost ones are
// the parts a step reveal shows in source order, strokes drawing themselves in.

import { assetUrl, el, loadScript, READABLE_PX, reducedMotion, STATIC } from "../draw.js";
import { norm } from "../util.js";

const SHADOW_CSS = `
:host { display: block; width: 100%; height: 100%; }
svg { display: block; width: 100%; height: 100%; overflow: visible; }
.part { transition: opacity 0.45s ease; }
.part.veiled { opacity: 0; }
.ink { stroke-dasharray: 1; stroke-dashoffset: 0; transition: stroke-dashoffset 0.9s ease; }
.part.veiled .ink, .part.veiled.ink { stroke-dashoffset: 1; }
:host(.still) *, :host(.still) *::before { transition: none !important; animation: none !important; }
.sketched { visibility: hidden; }
.rough { pointer-events: none; transition: opacity 0.45s ease; }
.part.veiled + .rough { opacity: 0; }
.part.veiled + .rough .ink { stroke-dashoffset: 1; }
:host(.sketch) text { font-family: Virgil, "Segoe Print", sans-serif; }
`;
const STROKES = "path, line, polyline, polygon, circle, ellipse, rect";

export default {
  render(slide, body, parts, ctx) {
    const v = slide.visual || {};
    const host = el("div", "drawing");
    if (v.background) { host.classList.add("page"); host.style.setProperty("--page", v.background); }
    host.setAttribute("role", "img");
    host.setAttribute("aria-label", v.title || slide.title || "Drawing");
    const root = host.attachShadow({ mode: "open" });
    root.innerHTML = `<style>${SHADOW_CSS}</style>${v.svg || ""}`;
    hoistFonts(root);
    host.classList.toggle("still", document.body.classList.contains("capture") || reducedMotion());
    (v.ids || []).forEach((id, i) => {
      const e = root.getElementById(id);
      if (!e) return;
      e.classList.add("part");
      e.dataset.idx = i;
      e.dataset.id = id;
      parts.items.set(id, e);
    });
    parts.visual = host;
    parts.drawing = root.querySelector("svg");
    parts.shadow = root;
    parts.build = (slide.classes || []).includes("build");
    parts.shown = 1;
    // `.morph`: the drawing starts as the previous slide's and glides into this one.
    const prev = ctx?.prev;
    if ((slide.classes || []).includes("morph") && prev?.kind === "svg") {
      const pv = prev.visual || {};
      parts.fromSvg = pv.steps?.length ? pv.steps[pv.steps.length - 1].svg : pv.svg;
    }
    body.append(host);
    if (v.sketch) {
      host.classList.add("sketch");
      return sketchReady().then((rough) => { parts.rough = rough; sketch(parts.drawing, rough); });
    }
  },

  // A drawing in steps: after a #step-N cue, elements that keep their id glide from the last
  // version to this one over MORPH_S seconds, new ones fade in and old ones fade out. Driven by
  // the narration's clock, so seeking and frame capture land on the same in-between picture.
  frame(st, slide, parts, t) {
    const v = slide.visual || {};
    const fired = st.cues.filter((c) => c.kind === "step");
    if (parts.fromSvg && !fired.length) {
      // Across slides: from the previous drawing to this one as the slide begins.
      const p = reducedMotion() ? 1 : Math.max(0, Math.min(1, st.sinceSlide / MORPH_S));
      if (!parts.morph?.cross || (parts.morph.done && p < 1)) {
        startMorph(parts, parts.fromSvg, v.steps ? v.steps[0] : v, 1);
        if (parts.morph) parts.morph.cross = true;
      }
      applyMorph(parts.morph, ease(p));
      return `x:${p.toFixed(2)}`;
    }
    if (!(v.steps?.length > 1)) return "";
    const last = fired[fired.length - 1];
    const n = last?.n || 1, from = fired.length > 1 ? fired[fired.length - 2].n : 1;
    const p = !last || reducedMotion() ? 1 : Math.max(0, Math.min(1, (t - last._at) / MORPH_S));
    if (parts.shown !== n || parts.morph?.cross || (parts.morph?.done && p < 1)) startMorph(parts, v.steps[from - 1].svg, v.steps[n - 1], n);
    applyMorph(parts.morph, ease(p));
    return `${n}:${p.toFixed(2)}`;
  },

  // Once laid out: a drawing on its own page hugs the drawing's shape; on a step-reveal slide,
  // outlines without a fill draw themselves in.
  layout(body, parts) {
    const vb = parts.drawing?.viewBox.baseVal, host = parts.visual;
    if (host.classList.contains("page") && vb?.width && vb.height) {
      const pad = 36, room = [body.clientWidth - pad, body.clientHeight - pad], k = Math.min(room[0] / vb.width, room[1] / vb.height);
      host.style.width = `${Math.round(vb.width * k + pad)}px`;
      host.style.height = `${Math.round(vb.height * k + pad)}px`;
      host.style.margin = "auto";
    }
    if (!parts.build) return;
    for (const part of parts.items.values()) {
      // A sketched shape is drawn by the rough copy beside it.
      const shapes = [part, ...part.querySelectorAll(STROKES), ...(part.nextElementSibling?.matches(".rough") ? [part.nextElementSibling] : [])];
      for (const e of shapes.flatMap((x) => (x.matches(".rough") ? [...x.querySelectorAll("path")] : [x]))) {
        if (!e.matches(STROKES) || e.matches(".sketched")) continue;
        const cs = getComputedStyle(e);
        if (cs.fill !== "none" || cs.stroke === "none" || cs.strokeDasharray !== "none") continue;
        e.setAttribute("pathLength", "1");
        e.classList.add("ink");
      }
    }
  },

  target(cue, parts, help) {
    const root = parts.shadow;
    if (!root) return undefined;
    if (cue.kind === "item") {
      const e = root.getElementById(cue.id);
      return e ? { rects: [e.getBoundingClientRect()] } : help.wholeVisual();
    }
    if (cue.kind === "text" && cue.text) {
      const want = norm(cue.text);
      const t = textEl(root, cue.text);
      return t ? { rects: [t.getBoundingClientRect()] } : help.wholeVisual();
    }
    if (cue.kind === "region") {
      const r = drawn(parts.drawing), [x, y, w, h] = cue.box;
      const left = r.left + (r.width * x) / 100, top = r.top + (r.height * y) / 100;
      const width = (r.width * w) / 100, height = (r.height * h) / 100;
      return { rects: [{ left, top, width, height, right: left + width, bottom: top + height }] };
    }
    return undefined;
  },

  // The element holding a cue's label, for `{.decode}`.
  element(cue, parts) {
    const root = parts.shadow;
    if (!root) return undefined;
    if (cue.kind === "item") return root.getElementById(cue.id);
    if (cue.kind === "text" && cue.text) return textEl(root, cue.text);
    return null;
  },

  // Text drawn smaller than the player reads comfortably, once the drawing is scaled to the slide.
  measure(slide, body, parts, report) {
    const svg = parts.drawing, vb = svg?.viewBox.baseVal;
    if (!vb || !vb.width) return true;
    const stage = parseFloat(document.getElementById("stage")?.dataset.scale) || 1;
    const k = drawn(svg).width / vb.width / stage;
    const sizes = [...parts.shadow.querySelectorAll("text")].map((t) => parseFloat(getComputedStyle(t).fontSize)).filter((x) => x > 0);
    const px = sizes.length ? Math.min(...sizes) * k : 0;
    if (px && px < READABLE_PX - 3) {
      report(`Slide ${slide.id}: the smallest text in the drawing is about ${Math.round(px)} px high on the slide, too small to read.`
        + " Use larger text, a viewBox closer to the slide's size, or `{.zoom}` onto each part as the narration names it.", "text_too_small", slide.id);
    }
    for (const msg of overlaps(parts.shadow).slice(0, 2)) report(`Slide ${slide.id}: ${msg}`, "drawing_overlap", slide.id);
    // Drawn past the viewBox: on the slide it spills over the title or off the edge.
    let all = null;
    try { all = svg.getBBox(); } catch { /* nothing drawn */ }
    const slack = Math.max(vb.width, vb.height) * 0.03;
    if (all && (all.x < vb.x - slack || all.y < vb.y - slack || all.x + all.width > vb.x + vb.width + slack
      || all.y + all.height > vb.y + vb.height + slack)) {
      report(`Slide ${slide.id}: part of the drawing lies outside its viewBox (${[vb.x, vb.y, vb.width, vb.height].map(Math.round).join(" ")}), `
        + "so it spills past the slide. Keep everything inside the viewBox.", "overflow", slide.id);
    }
    return true;
  },
};

const MORPH_S = 1.2;
const ease = (p) => (p < 0.5 ? 4 * p * p * p : 1 - (-2 * p + 2) ** 3 / 2);
const NUMBER = /-?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?/gi;
const SKIP = new Set(["id", "class", "style"]);

// A <text> whose words include `want`; a scrambled label (decode.js) is matched by its real words.
function textEl(root, want) {
  want = norm(want);
  return [...root.querySelectorAll("text")].find((x) => norm(x.dataset.plain ?? x.textContent).includes(want)) || null;
}

function parse(svgText) {
  const t = document.createElement("template");
  t.innerHTML = svgText;
  return t.content.querySelector("svg");
}

// Show `version` (step `n`) and plan the glide from the drawing `prevSvg`: pairs of elements by
// id with their attributes before and after, plus elements that appear or go.
function startMorph(parts, prevSvg, version, n) {
  const next = parse(version.svg), prev = parse(prevSvg);
  if (!next || !prev) return;
  parts.drawing.replaceWith(next);
  parts.drawing = next;
  parts.shown = n;
  if (parts.rough) {
    // Sketch the new version before planning the glide; the old version is read from its own
    // clean copy, so pairs compare the shapes' real geometry.
    sketch(next, parts.rough);
  }
  parts.items.clear();
  (version.ids || []).forEach((id, i) => {
    const e = next.querySelector(`[id="${CSS.escape(id)}"]`);
    if (e) { e.classList.add("part"); e.dataset.idx = i; parts.items.set(id, e); }
  });
  const morph = { pairs: [], fades: [], done: false };
  const before = new Map([...prev.querySelectorAll("[id]")].map((e) => [e.id, e]));
  for (const el of next.querySelectorAll("[id]")) {
    const old = before.get(el.id);
    before.delete(el.id);
    if (!old) { morph.fades.push({ el, dir: 1 }); continue; }
    const names = new Set([...old.getAttributeNames(), ...el.getAttributeNames()].filter((a) => !SKIP.has(a)));
    const attrs = [...names].map((a) => [a, old.getAttribute(a), el.getAttribute(a)]).filter(([, a, b]) => a !== b);
    const text = el.children.length === 0 && old.children.length === 0 && old.textContent !== el.textContent
      ? [old.textContent, el.textContent] : null;
    if (attrs.length || text) morph.pairs.push({ el, attrs, text });
  }
  // What only the old version had leaves in place, fading, unless its parent left too.
  for (const old of before.values()) {
    if (old.parentElement?.closest("[id]") && before.has(old.parentElement.closest("[id]").id)) continue;
    const ghost = old.cloneNode(true);
    next.append(ghost);
    if (parts.rough) sketch(ghost, parts.rough);
    morph.fades.push({ el: ghost, dir: -1 });
  }
  morph.rough = parts.rough;
  parts.morph = morph;
}

function applyMorph(m, p) {
  if (!m || (m.done && p >= 1)) return;
  for (const { el, attrs, text } of m.pairs) {
    for (const [name, a, b] of attrs) {
      const value = between(a, b, p);
      if (value === null) el.removeAttribute(name);
      else el.setAttribute(name, value);
    }
    if (text) el.textContent = p < 0.5 ? text[0] : text[1];
    if (m.rough && el.matches(SHAPES)) roughen(m.rough, el); // a hand-drawn shape is drawn again where it now is
  }
  for (const f of m.fades) {
    const rough = f.el.nextElementSibling?.matches(".rough") ? f.el.nextElementSibling : null;
    if (p >= 1) {
      if (f.dir < 0) { f.el.remove(); rough?.remove(); } else { f.el.style.removeProperty("opacity"); rough?.style.removeProperty("opacity"); }
    } else {
      for (const e of [f.el, rough]) if (e) e.style.opacity = String(f.dir > 0 ? p : 1 - p);
    }
  }
  m.done = p >= 1;
}

// A value between two attribute values: numbers move (also inside a transform, path or point
// list written the same way in both), colours blend, anything else switches halfway.
function between(a, b, p) {
  if (p >= 1) return b;
  if (a === null || b === null) return p < 0.5 ? a : b;
  const ca = colour(a), cb = colour(b);
  if (ca && cb) return `rgba(${ca.map((x, i) => (i < 3 ? Math.round(x + (cb[i] - x) * p) : +(x + (cb[i] - x) * p).toFixed(3))).join(", ")})`;
  const na = a.match(NUMBER), nb = b.match(NUMBER);
  if (na && nb && na.length === nb.length && a.replace(NUMBER, "#") === b.replace(NUMBER, "#")) {
    let i = 0;
    return b.replace(NUMBER, () => { const x = +na[i], y = +nb[i]; i++; return String(+(x + (y - x) * p).toFixed(3)); });
  }
  return p < 0.5 ? a : b;
}

function colour(s) {
  const h = s.trim().match(/^#([0-9a-f]{3,8})$/i);
  if (h) {
    const x = h[1].length <= 4 ? [...h[1]].map((c) => c + c).join("") : h[1];
    const n = x.match(/../g).map((c) => parseInt(c, 16));
    return [n[0], n[1], n[2], n.length > 3 ? n[3] / 255 : 1];
  }
  const r = s.match(/^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:[\s,/]+([\d.]+))?\s*\)$/i);
  return r ? [+r[1], +r[2], +r[3], r[4] === undefined ? 1 : +r[4]] : null;
}

// Labels a line runs through, or that sit on each other: what an agent drawing blind gets wrong.
function overlaps(root) {
  const inner = (r) => { const dx = r.width * 0.06 + 2, dy = r.height * 0.22 + 1; // glyph boxes include space around the letters
    return { left: r.left + dx, right: r.right - dx, top: r.top + dy, bottom: r.bottom - dy }; };
  const labels = [...root.querySelectorAll("text")].filter((t) => !t.closest("defs, .rough") && t.textContent.trim())
    .map((t) => ({ t, box: inner(t.getBoundingClientRect()), name: t.textContent.trim().slice(0, 40) }))
    .filter((l) => l.box.right > l.box.left && l.box.bottom > l.box.top);
  const out = [];
  for (let i = 0; i < labels.length; i++) {
    for (let j = i + 1; j < labels.length; j++) {
      const a = labels[i].box, b = labels[j].box;
      const w = Math.min(a.right, b.right) - Math.max(a.left, b.left), h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      if (w > 0 && h > 0) out.push(`in the drawing, the labels "${labels[i].name}" and "${labels[j].name}" overlap.`);
    }
  }
  const lines = [...root.querySelectorAll("line, polyline, path, polygon, rect, circle, ellipse")]
    .filter((e) => !e.closest("defs, marker, clipPath, mask, pattern, symbol, .rough") && getComputedStyle(e).stroke !== "none");
  const crossed = new Set();
  for (const e of lines) {
    let len = 0;
    try { len = e.getTotalLength(); } catch { continue; }
    const m = e.getScreenCTM();
    if (!m || !len) continue;
    for (let d = 0; d <= len; d += Math.max(2, len / 400)) {
      const q = new DOMPoint(e.getPointAtLength(d).x, e.getPointAtLength(d).y).matrixTransform(m);
      const hit = labels.find((l) => !crossed.has(l) && q.x > l.box.left && q.x < l.box.right && q.y > l.box.top && q.y < l.box.bottom);
      if (hit) crossed.add(hit);
    }
  }
  for (const l of crossed) out.push(`in the drawing, a line runs through the label "${l.name}". Move the label or the line.`);
  return out;
}

// -- sketch: shapes redrawn by hand (Rough.js) and text in a hand-drawn face --------------------

const SHAPES = "rect, circle, ellipse, line, polyline, polygon, path";
let roughLoaded = null;

function sketchReady() {
  if (!roughLoaded) {
    const face = document.createElement("style");
    face.textContent = `@font-face { font-family: "Virgil"; src: url("${assetUrl("vendor/fonts/Virgil.woff2")}") format("woff2"); font-display: block; }`;
    document.head.append(face);
    roughLoaded = loadScript(`${STATIC}/vendor/rough/rough.js`)
      .then(() => document.fonts?.load('24px "Virgil"'))
      .then(() => window.rough);
  }
  return roughLoaded;
}

// Every shape outside definitions and text gets a hand-drawn copy beside it; the shape itself
// stays, hidden, so cues, reveals and glides keep working on it.
function sketch(root, rough) {
  const shapes = root.matches?.(SHAPES) ? [root] : [...root.querySelectorAll(SHAPES)];
  for (const e of shapes) {
    if (e.closest("defs, marker, clipPath, mask, pattern, symbol, text, .rough")) continue;
    roughen(rough, e);
  }
}

function roughen(rough, e) {
  const svg = e.ownerSVGElement;
  if (!svg) return;
  const old = e.nextElementSibling?.matches(".rough") ? e.nextElementSibling : null;
  const cs = getComputedStyle(e);
  const fill = cs.fill && cs.fill !== "none" && !cs.fill.startsWith("url") ? cs.fill : undefined;
  const stroke = cs.stroke && cs.stroke !== "none" ? cs.stroke : undefined;
  if (!fill && !stroke) return; // a gradient fill or nothing to draw: keep the shape as it is
  let box = { width: 0, height: 0 };
  try { box = e.getBBox(); } catch { /* not laid out */ }
  const dash = cs.strokeDasharray !== "none" ? cs.strokeDasharray.split(/[\s,]+/).map(parseFloat).filter((x) => x > 0) : undefined;
  // As on a whiteboard: every filled shape gets an outline in its own colour, and hatching
  // unless it is small enough that hatching would read as a scribble.
  const o = { stroke: stroke || fill, strokeWidth: stroke ? parseFloat(cs.strokeWidth) || 1 : 1.6, roughness: 1.1, bowing: 1,
    seed: seedOf(e), fill, fillStyle: box.width * box.height < 1600 ? "solid" : "hachure", hachureGap: 6, fillWeight: 1.3,
    hachureAngle: -41, strokeLineDash: dash?.length ? dash : undefined };
  const n = (a) => parseFloat(e.getAttribute(a)) || 0;
  const pts = () => (e.getAttribute("points") || "").trim().split(/[\s,]+/).map(Number).reduce((a, x, i) => (i % 2 ? a[a.length - 1].push(x) : a.push([x]), a), []);
  const rc = rough.svg(svg);
  let node;
  switch (e.tagName.toLowerCase()) {
    case "rect": node = rc.rectangle(n("x"), n("y"), n("width"), n("height"), o); break;
    case "circle": node = rc.circle(n("cx"), n("cy"), 2 * n("r"), o); break;
    case "ellipse": node = rc.ellipse(n("cx"), n("cy"), 2 * n("rx"), 2 * n("ry"), o); break;
    case "line": node = rc.line(n("x1"), n("y1"), n("x2"), n("y2"), o); break;
    case "polyline": node = rc.linearPath(pts(), o); break;
    case "polygon": node = rc.polygon(pts(), o); break;
    default: node = rc.path(e.getAttribute("d") || "", o);
  }
  if (e.getAttribute("transform")) node.setAttribute("transform", e.getAttribute("transform"));
  node.classList.add("rough");
  node.setAttribute("aria-hidden", "true");
  if (cs.opacity !== "1") node.style.opacity = cs.opacity;
  e.classList.add("sketched");
  if (old) old.replaceWith(node);
  else e.after(node);
}

// The same hand-drawn wobble every time a shape is drawn: a seed from its id or place.
function seedOf(e) {
  let key = e.id || "";
  for (let x = e; !key && x && x.parentNode; x = x.parentElement) key = `${[...x.parentNode.children].indexOf(x)}/${key}`;
  let h = 7;
  for (const c of key) h = (h * 31 + c.charCodeAt(0)) % 2147483647;
  return (h % 2147483646) + 1;
}

// The picture as drawn: the viewBox fitted inside the element, centred (preserveAspectRatio meet).
function drawn(svg) {
  const box = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
  if (!vb || !vb.width || !vb.height) return box;
  const k = Math.min(box.width / vb.width, box.height / vb.height);
  return { left: box.left + (box.width - vb.width * k) / 2, top: box.top + (box.height - vb.height * k) / 2,
    width: vb.width * k, height: vb.height * k };
}

// Browsers ignore @font-face inside a shadow root, so embedded fonts (as in Excalidraw exports)
// are declared on the page, once each.
function hoistFonts(root) {
  const faces = [...root.querySelectorAll("svg style")].flatMap((s) => s.textContent.match(/@font-face\s*\{[^}]*\}/g) || []);
  if (!faces.length) return;
  let sheet = document.getElementById("drawing-fonts");
  if (!sheet) {
    sheet = document.createElement("style");
    sheet.id = "drawing-fonts";
    document.head.append(sheet);
  }
  for (const f of faces) if (!sheet.textContent.includes(f)) sheet.textContent += `${f}\n`;
}
