// reladraw diagrams (vendor/reladraw, Apache-2.0): boxes placed by stated relations, drawn by the
// library in the slide's own colors and faces, and loaded only when a slide needs it. Each node
// and line comes wrapped in a group naming it (data-node, data-edge): nodes are what cues point
// at, top-level nodes reveal in source order, a child with its parent and a line with its ends.
// The library's findings (a line through a box, texts on top of each other) are reported as
// problems with the slide, since it draws what the source says and never moves anything.

import { el, loadScript, READABLE_PX, STATIC } from "../draw.js";
import { norm } from "../util.js";

const LIB = `${STATIC}/vendor/reladraw/reladraw.min.js`;
const FONT_PX = 20; // the drawing's text size before it is fitted to the slide
const MAX_SCALE = 1.6; // a small diagram grows to fill the slide, up to this
const MAX_FINDINGS = 3; // per slide; the rest are counted
const SVG_NS = "http://www.w3.org/2000/svg";

export default {
  render(slide, body, parts, ctx) {
    const v = slide.visual || {};
    const host = el("div", "reladraw");
    host.setAttribute("role", "img");
    host.setAttribute("aria-label", `Diagram: ${(v.items || []).map((i) => i.label).filter(Boolean).slice(0, 12).join(", ")}`);
    parts.visual = host;
    parts.reladraw = v;
    body.append(host);
    return ready().then((lib) => {
      let out;
      try {
        out = lib.draw(v.source || "", { theme: theme(), measurer: measurer(), fontSize: FONT_PX, margin: 6 });
      } catch (e) {
        const where = e.line ? `line ${e.line} of the block: ` : "";
        host.innerHTML = `<div class="err">The diagram could not be drawn.</div>`;
        ctx.report(`reladraw diagram on slide ${slide.id} failed to draw: ${where}${String(e.message || e).split("\n")[0]}`, "render_error", slide.id);
        return;
      }
      const svg = clean(out.svg);
      if (!svg) {
        host.innerHTML = `<div class="err">The diagram could not be drawn.</div>`;
        ctx.report(`reladraw diagram on slide ${slide.id} failed to draw.`, "render_error", slide.id);
        return;
      }
      host.append(svg);
      parts.svg = svg;
      svg.querySelectorAll("g[data-node]").forEach((g) => parts.items.set(g.dataset.node, g));
      const found = out.findings || [];
      found.slice(0, MAX_FINDINGS).forEach((f) => {
        ctx.report(`Slide ${slide.id}: the reladraw diagram, line ${f.line} of the block: ${f.message} (${f.kind}).`
          + " It is drawn as written: move a box, give a line a `via:` or `from:`/`to:` side, or widen a gap.", "drawing_overlap", slide.id);
      });
      if (found.length > MAX_FINDINGS) {
        ctx.report(`Slide ${slide.id}: the reladraw diagram has ${found.length - MAX_FINDINGS} more findings like these.`, "drawing_overlap", slide.id);
      }
    }).catch((e) => {
      host.innerHTML = `<div class="err">The diagram could not be drawn.</div>`;
      ctx.report(`Diagram on slide ${slide.id}: the diagram library did not load (${e.message || e}).`, "render_error", slide.id);
    });
  },

  // Scale the drawing to the slide: down to fit, up to MAX_SCALE to fill it.
  layout(body, parts) {
    const svg = parts.svg;
    if (!svg) return;
    const vb = svg.viewBox.baseVal;
    if (!vb || !vb.width || !vb.height) return;
    const k = Math.min(body.clientWidth / vb.width, body.clientHeight / vb.height, MAX_SCALE) * 0.98;
    svg.setAttribute("width", `${Math.round(vb.width * k)}`);
    svg.setAttribute("height", `${Math.round(vb.height * k)}`);
    parts.scale = k;
  },

  // Top-level nodes in order; a node inside another with it; a line once both its ends show.
  reveal(reveal, parts, veil) {
    const svg = parts.svg, v = parts.reladraw;
    if (!svg || !("items" in reveal)) return;
    const top = (name) => v.part_of?.[name] ?? name;
    const shown = (name) => {
      const i = v.ids.indexOf(top(name));
      return i < 0 || i < reveal.items;
    };
    for (const g of svg.querySelectorAll("g[data-node]")) veil(g, !shown(g.dataset.node));
    const all = v.ids.length <= reveal.items;
    for (const g of svg.querySelectorAll("g[data-edge]")) {
      const ends = edgeEnds(g.dataset.name || "", v);
      veil(g, !(ends ? ends.every(shown) : all));
    }
  },

  target(cue, parts, help) {
    const svg = parts.svg;
    if (!svg) return undefined;
    if (cue.kind === "item") {
      const g = parts.items.get(cue.id);
      return g ? { rects: [g.getBoundingClientRect()] } : help.wholeVisual();
    }
    if (cue.kind === "text" && cue.text) {
      const want = norm(cue.text);
      const hit = (parts.reladraw.items || []).find((it) => norm(it.label).includes(want));
      const g = hit && parts.items.get(hit.id);
      return g ? { rects: [g.getBoundingClientRect()] } : undefined;
    }
    if (cue.kind === "region") {
      const b = svg.getBoundingClientRect(), [x, y, w, h] = cue.box;
      const left = b.left + (b.width * x) / 100, top = b.top + (b.height * y) / 100;
      const width = (b.width * w) / 100, height = (b.height * h) / 100;
      return { rects: [{ left, top, width, height, right: left + width, bottom: top + height }] };
    }
    return undefined;
  },

  // Text drawn smaller than the player reads comfortably, once the diagram fits the slide.
  measure(slide, body, parts, report) {
    if (!parts.svg) return true;
    const px = FONT_PX * (parts.scale || 1);
    if (px < READABLE_PX - 3) {
      report(`Slide ${slide.id}: the diagram had to shrink until its text is about ${Math.round(px)} px high, too small to read.`
        + " Use fewer boxes, tighter gaps, or split it across slides.", "text_too_small", slide.id);
    }
    return true;
  },
};

async function ready() {
  await loadScript(LIB);
  // Boxes are sized by measuring their text: the face must be loaded first.
  await Promise.all(["400 20px Inter", "700 20px Inter"].map((f) => document.fonts?.load(f).catch(() => null)));
  return window.reladraw;
}

// The two node names of a line's group name, "a -> b"; null for a named line or a bundle.
function edgeEnds(name, v) {
  const m = /^(\S+)\s+\S*-\S*\s+(\S+)$/.exec(name.trim());
  if (!m) return null;
  const known = new Set((v.items || []).map((i) => i.id));
  return known.has(m[1]) && known.has(m[2]) ? [m[1], m[2]] : null;
}

// Text is measured with the slide's own face on a canvas, so boxes fit what is drawn.
let canvas;
function measurer() {
  const family = getComputedStyle(document.body).getPropertyValue("--sans").trim() || "Inter, sans-serif";
  canvas ||= document.createElement("canvas").getContext("2d");
  const lh = (size) => Math.round(size * 1.3);
  return {
    fontFamily: family,
    lineHeight: lh,
    measure(text, size) {
      const lines = window.reladraw.splitLines(text);
      canvas.font = `400 ${size}px ${family}`;
      const width = Math.max(...lines.map((l) => canvas.measureText(l.replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")).width));
      return { width: Math.ceil(width), height: lines.length * lh(size), lines };
    },
  };
}

// The library's theme, from the slide's: every color resolved to hex, since it mixes them.
function theme() {
  const css = getComputedStyle(document.body);
  const v = (n, fb) => hex(css.getPropertyValue(n).trim() || fb);
  const page = v("--stage", "#111111"), fg = v("--fg", "#e6e6e6");
  const accent = (c) => ({ color: c, subtle: mix(c, page, 0.25), on: luminance(c) > 0.45 ? page : fg });
  return {
    background: page,
    boxFill: v("--node", "#1c1c28"),
    boxStroke: v("--node-line", "#4f5367"),
    containerFill: mix(fg, page, 0.04),
    containerStroke: v("--line", "#2a2a35"),
    text: fg,
    mutedText: v("--muted", "#8b8b8b"),
    edge: v("--muted", "#8b8b8b"),
    iconInk: v("--muted", "#8b8b8b"),
    iconShade: v("--panel-2", "#2a2a35"),
    primary: accent(v("--accent", "#ffc857")),
    secondary: accent(v("--c2", "#6fb6ff")),
  };
}

// Any CSS color to #rrggbb, drawn onto one pixel (color-mix() and named colors included).
let pixel;
function hex(color) {
  pixel ||= Object.assign(document.createElement("canvas"), { width: 1, height: 1 }).getContext("2d", { willReadFrequently: true });
  pixel.clearRect(0, 0, 1, 1);
  pixel.fillStyle = "#000";
  pixel.fillStyle = color;
  pixel.fillRect(0, 0, 1, 1);
  const [r, g, b] = pixel.getImageData(0, 0, 1, 1).data;
  return `#${[r, g, b].map((x) => x.toString(16).padStart(2, "0")).join("")}`;
}

const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
function mix(a, b, t) {
  const x = rgb(a), y = rgb(b);
  return `#${x.map((c, i) => Math.round(c * t + y[i] * (1 - t)).toString(16).padStart(2, "0")).join("")}`;
}
function luminance(h) {
  const [r, g, b] = rgb(h).map((c) => c / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

// The drawing as an element, with nothing that could leave the page: links unwrapped, outside
// pictures and embedded HTML removed, references only to the drawing's own ids. The full-page
// background rectangle goes too, so the slide shows through.
function clean(text) {
  const doc = new DOMParser().parseFromString(text, "image/svg+xml");
  const svg = doc.documentElement;
  if (!svg || svg.namespaceURI !== SVG_NS || svg.querySelector("parsererror")) return null;
  for (const a of [...svg.querySelectorAll("a")]) a.replaceWith(...a.childNodes);
  svg.querySelectorAll("script, foreignObject, image, iframe, style").forEach((n) => n.remove());
  for (const n of svg.querySelectorAll("*")) {
    for (const at of [...n.attributes]) {
      const name = at.name.toLowerCase(), val = at.value.trim();
      if (name.startsWith("on")) n.removeAttribute(at.name);
      else if ((name === "href" || name === "xlink:href") && !val.startsWith("#")) n.removeAttribute(at.name);
      else if (/url\(\s*['"]?(?!#)/i.test(val)) n.removeAttribute(at.name);
    }
  }
  const first = svg.querySelector(":scope > rect");
  const vb = svg.viewBox.baseVal;
  if (first && vb && +first.getAttribute("width") === vb.width && +first.getAttribute("height") === vb.height) first.remove();
  svg.removeAttribute("width");
  svg.removeAttribute("height");
  return document.importNode(svg, true);
}
