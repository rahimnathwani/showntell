// Function plots: curves the builder has already sampled, points and shaded areas on axes, drawn
// as SVG at the slide body's own size. Functions, points and shades are the parts cues point at.
// Curves draw left to right as the slide arrives; `plot step` versions glide into each other on a
// #step-N cue (curves by their samples, points by their coordinates, the axes by their ranges).

import { el, reducedMotion } from "../draw.js";
import { esc, spread } from "../util.js";

const SIZES = { landscape: [1100, 600], square: [900, 820], portrait: [660, 940] };
const MIN_W = 480, MIN_H = 320;
const PALETTE = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)"];
const DRAW_S = 1.1; // a curve drawing itself in
const DELAY_S = 0.3;
const MORPH_S = 0.9; // one version gliding into the next
const ease = (x) => 1 - (1 - x) ** 3;
const clamp = (x) => Math.max(0, Math.min(1, x));
let clipSeq = 0;

export default {
  render(slide, body, parts, ctx) {
    const v = slide.visual || {};
    const c = el("div", "chart plot");
    c.setAttribute("role", "img");
    c.setAttribute("aria-label", plotLabel(v));
    parts.visual = c;
    parts.plot = v;
    parts.size = SIZES[ctx?.frame] || SIZES.landscape;
    parts.drawn = { v: v.steps?.length ? v.steps[0] : v, key: "" };
    draw(parts, parts.drawn.v, 1);
    body.append(c);
  },

  layout(body, parts) {
    const bw = Math.floor(body.clientWidth), bh = Math.floor(body.clientHeight);
    if (bw < 200 || bh < 160) return;
    const size = [Math.max(MIN_W, bw), Math.max(MIN_H, bh)];
    if (size.join("x") === parts.size.join("x")) return;
    parts.size = size;
    draw(parts, parts.drawn.v, 1);
  },

  // A part pointed at: its box within the plot's frame, since curves run on beyond it.
  target(cue, parts) {
    if (cue.kind !== "item") return undefined;
    const g = parts.items.get(cue.id), frame = parts.visual?.querySelector("rect.frame");
    if (!g || !frame) return undefined;
    g.classList.add("active");
    g.parentElement?.classList.add("has-active");
    const a = g.getBoundingClientRect(), f = frame.getBoundingClientRect();
    const left = Math.max(a.left, f.left), top = Math.max(a.top, f.top);
    const right = Math.min(a.right, f.right), bottom = Math.min(a.bottom, f.bottom);
    if (right <= left || bottom <= top) return null;
    return { rects: [{ left, top, right, bottom, width: right - left, height: bottom - top }] };
  },

  frame(st, slide, parts, t) {
    const v = slide.visual || {};
    let key = "";
    if (v.steps?.length > 1) {
      const fired = st.cues.filter((c) => c.kind === "step");
      const last = fired[fired.length - 1];
      const n = last?.n || 1, from = fired.length > 1 ? fired[fired.length - 2].n : 1;
      const p = !last || reducedMotion() ? 1 : clamp((t - last._at) / MORPH_S);
      key = `${n}:${p.toFixed(2)}`;
      parts.moving = key !== parts.drawn.key; // redrawn: the pointer follows what moved
      if (key !== parts.drawn.key) {
        const shown = p >= 1 ? v.steps[n - 1] : between(v.steps[from - 1], v.steps[n - 1], ease(p));
        draw(parts, shown, p >= 1 ? 1 : ease(p));
        parts.drawn = { v: shown, key };
      }
    }
    // Curves draw themselves in as the slide arrives, unless everything shows at once.
    const still = st.revealAll || reducedMotion() || slide.classes?.includes("build");
    const k = still ? 1 : ease(clamp((st.sinceSlide - DELAY_S) / DRAW_S));
    for (const path of parts.visual.querySelectorAll(".curve:not(.dashed)")) path.style.strokeDashoffset = k < 1 ? `${1 - k}` : "";
    for (const path of parts.visual.querySelectorAll(".curve.dashed, .vline")) path.style.opacity = k < 1 ? `${k}` : "";
    for (const lab of parts.visual.querySelectorAll(".flabel")) lab.style.opacity = k < 1 ? clamp((k - 0.8) / 0.2) : "";
    return `${key}|${k.toFixed(3)}`;
  },
};

function plotLabel(v) {
  const fns = (v.items || []).filter((i) => i.kind === "fn").map((i) => `${i.name}(x) = ${i.src}`);
  return `${v.title ? `${v.title}. ` : ""}Plot of ${fns.join("; ")} for x from ${fmt(v.x?.[0])} to ${fmt(v.x?.[1])}.`;
}

// Draw a version into the slide, keeping which parts are pointed at or veiled.
function draw(parts, v, fade) {
  const c = parts.visual;
  const keep = new Map([...c.querySelectorAll("g.entry")].map((g) => [g.dataset.id, g.className.baseVal]));
  c.innerHTML = drawPlot(v, parts.size, parts.plot, fade);
  parts.items.clear();
  c.querySelectorAll("g.entry").forEach((g) => {
    parts.items.set(g.dataset.id, g);
    const cls = keep.get(g.dataset.id);
    if (cls) for (const x of ["active", "veiled"]) g.classList.toggle(x, cls.split(/\s+/).includes(x));
  });
  c.querySelector("g.entry.active")?.parentElement.classList.add("has-active");
}

// A version part of the way (p) from a to b: shared parts move, new ones fade in, gone ones out.
export function between(a, b, p) {
  const lerp = (x, y) => (x == null || y == null ? (p < 0.5 ? x : y) : x + (y - x) * p);
  const arr = (xs, ys) => (xs && ys && xs.length === ys.length ? xs.map((x, i) => lerp(x, ys[i])) : p < 0.5 ? xs : ys);
  const was = new Map((a.items || []).map((it) => [it.id, it]));
  const items = (b.items || []).map((it) => {
    const o = was.get(it.id);
    if (!o || o.kind !== it.kind) return { ...it, fade: p };
    return { ...it, x: lerp(o.x, it.x), y: lerp(o.y, it.y), from: lerp(o.from, it.from), to: lerp(o.to, it.to), ys: arr(o.ys, it.ys), xs: arr(o.xs, it.xs) };
  });
  const now = new Set(items.map((it) => it.id));
  for (const it of a.items || []) if (!now.has(it.id)) items.push({ ...it, fade: 1 - p });
  return { ...b, items, x: [lerp(a.x[0], b.x[0]), lerp(a.x[1], b.x[1])], y: [lerp(a.y[0], b.y[0]), lerp(a.y[1], b.y[1])] };
}

// A curve's value at x, read off its samples as the drawn line runs between them.
function sampleAt(ys, x0, x1, x) {
  if (!ys?.length || x < x0 || x > x1) return null;
  const f = ((x - x0) / (x1 - x0)) * (ys.length - 1), i = Math.min(ys.length - 2, Math.floor(f));
  const a = ys[i], b = ys[i + 1];
  if (a == null || b == null) return a ?? b ?? null;
  return a + (b - a) * (f - i);
}

// Ticks at 1, 2 or 5 times a power of ten, about `n` of them across [lo, hi], counted by index so
// a range too fine or too far out for floating point still ends (and never with more than 40).
export function ticks(lo, hi, n) {
  const raw = (hi - lo) / n;
  if (!(raw > 0) || !Number.isFinite(raw)) return [];
  const p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw);
  const first = Math.ceil(lo / step - 1e-9);
  const out = [];
  for (let i = 0; i <= 40; i++) {
    const t = (first + i) * step;
    if (t > hi + step * 1e-6) break;
    out.push(Math.abs(t) < step * 1e-6 ? 0 : t);
  }
  return out;
}

function fmt(n) {
  if (n == null || !Number.isFinite(n)) return "";
  const abs = Math.abs(n);
  if (abs >= 1e5 || (abs > 0 && abs < 1e-3)) return n.toExponential(1);
  return `${+n.toFixed(abs < 1 ? 3 : abs < 10 ? 2 : abs < 100 ? 1 : 0)}`;
}

export function drawPlot(v, [W, H], whole, fade = 1) {
  const t = (x, y, text, cls, anchor = "middle") => `<text x="${x}" y="${y}" class="${cls}" text-anchor="${anchor}">${esc(text)}</text>`;
  const m = { l: 86, r: 36, t: v.title ? 70 : 28, b: v.xlabel ? 84 : 58 };
  if (v.ylabel) m.l += 34;
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const [x0, x1] = v.x, [y0, y1] = v.y;
  const X = (x) => m.l + ((x - x0) / (x1 - x0)) * iw;
  const Y = (y) => m.t + ih - ((y - y0) / (y1 - y0)) * ih;
  const clip = `plot-clip-${++clipSeq}`;
  let grid = "";
  for (const tx of ticks(x0, x1, Math.max(4, Math.round(iw / 150)))) {
    grid += `<line x1="${X(tx)}" x2="${X(tx)}" y1="${m.t}" y2="${m.t + ih}" class="grid"/>` + t(X(tx), m.t + ih + 32, fmt(tx), "tick");
  }
  for (const ty of ticks(y0, y1, Math.max(3, Math.round(ih / 80)))) {
    grid += `<line x1="${m.l}" x2="${m.l + iw}" y1="${Y(ty)}" y2="${Y(ty)}" class="grid"/>` + t(m.l - 12, Y(ty) + 7, fmt(ty), "tick", "end");
  }
  // The axes through zero where zero is in view; the frame's edge otherwise.
  const ax = y0 <= 0 && y1 >= 0 ? Y(0) : null, ay = x0 <= 0 && x1 >= 0 ? X(0) : null;
  const axes = (ax != null ? `<line x1="${m.l}" x2="${m.l + iw}" y1="${ax}" y2="${ax}" class="axis"/>` : "")
    + (ay != null ? `<line x1="${ay}" x2="${ay}" y1="${m.t}" y2="${m.t + ih}" class="axis"/>` : "")
    + `<rect x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" class="frame"/>`
    + (v.xlabel ? t(m.l + iw / 2, H - 16, v.xlabel, "alabel") : "")
    + (v.ylabel ? `<text class="alabel" text-anchor="middle" transform="translate(26 ${m.t + ih / 2}) rotate(-90)">${esc(v.ylabel)}</text>` : "");
  const head = v.title ? t(W / 2, 36, v.title, "ctitle") : "";
  const ids = whole?.ids || v.ids || [];
  const color = new Map();
  // Each function keeps one color across versions, in the order functions first appear.
  const every = [...(whole?.items || v.items || []), ...(whole?.steps || []).flatMap((st) => st.items || [])];
  for (const it of every) if (it.kind === "fn" && !color.has(it.id)) color.set(it.id, PALETTE[color.size % PALETTE.length]);
  const fns = (v.items || []).filter((i) => i.kind === "fn");
  const xsOf = (k) => Array.from({ length: k }, (_, i) => x0 + ((x1 - x0) * i) / Math.max(1, k - 1));
  // Function labels sit at the curve's last point in view, pushed apart where they would overlap.
  const ends = fns.map((it) => {
    const xs = xsOf(it.ys.length);
    for (let i = it.ys.length - 1; i >= 0; i--) {
      const y = it.ys[i];
      if (y != null && y >= y0 && y <= y1) return { x: X(xs[i]), y: Y(y) };
    }
    return null;
  });
  const labelY = spread(ends.map((e) => (e ? e.y - 16 : m.t + 20)), 30, m.t + 22, m.t + ih - 6);
  let marks = "";
  const group = (it, inner) => {
    const op = it.fade != null && it.fade < 1 ? ` style="opacity:${it.fade.toFixed(3)}"` : "";
    return `<g class="entry ${it.kind}" data-id="${esc(it.id)}" data-idx="${ids.indexOf(it.id)}"${op}>${inner}</g>`;
  };
  for (const it of v.items || []) {
    if (it.kind !== "shade") continue;
    const ys = it.ys || [], xs = it.xs || [];
    const pts = xs.map((x, i) => (ys[i] == null ? null : `${X(x).toFixed(1)},${Y(Math.max(y0 - (y1 - y0), Math.min(y1 + (y1 - y0), ys[i]))).toFixed(1)}`)).filter(Boolean);
    const base = Y(Math.max(y0, Math.min(y1, 0)));
    marks += group(it, `<polygon points="${X(it.from).toFixed(1)},${base} ${pts.join(" ")} ${X(it.to).toFixed(1)},${base}" class="shade" style="--c:${color.get(it.of) || PALETTE[0]}"/>`
      + (it.label ? t(X((it.from + it.to) / 2), base - 14, it.label, "plabel") : ""));
  }
  fns.forEach((it, fi) => {
    const xs = xsOf(it.ys.length);
    let d = "", pen = false;
    const span = y1 - y0;
    it.ys.forEach((y, i) => {
      if (y == null) { pen = false; return; }
      const prev = it.ys[i - 1];
      // A jump from far above the view to far below it (tan, 1/x) is a gap, not a line.
      if (pen && prev != null && Math.abs(y - prev) > span && (Math.min(y, prev) < y0 || Math.max(y, prev) > y1)) pen = false;
      const cy = Math.max(y0 - 2 * span, Math.min(y1 + 2 * span, y));
      d += `${pen ? "L" : "M"}${X(xs[i]).toFixed(1)},${Y(cy).toFixed(1)}`;
      pen = true;
    });
    const end = ends[fi];
    const label = it.label || `${it.name}(x)`;
    marks += group(it, `<path d="${d}" class="curve${it.dashed ? " dashed" : ""}"${it.dashed ? "" : ' pathLength="1"'} style="--c:${color.get(it.id)}"/>`
      + (end ? `<text x="${Math.min(end.x, m.l + iw) - 10}" y="${labelY[fi]}" class="flabel" text-anchor="end" style="--c:${color.get(it.id)}">${esc(label)}</text>` : ""));
  });
  for (const it of v.items || []) {
    if (it.kind !== "vline" || it.x < x0 || it.x > x1) continue;
    marks += group(it, `<line x1="${X(it.x).toFixed(1)}" x2="${X(it.x).toFixed(1)}" y1="${m.t}" y2="${m.t + ih}" class="vline${it.dashed ? " dashed" : ""}"/>`
      + (it.label ? t(X(it.x) + 10, m.t + 26, it.label, "plabel", "start") : ""));
  }
  const curve = new Map(fns.map((f) => [f.id, f]));
  for (const it of v.items || []) {
    if (it.kind !== "point") continue;
    // A point written as (a, f(a)) stays on f's drawn curve while it glides.
    const y = it.on && curve.has(it.on) ? sampleAt(curve.get(it.on).ys, x0, x1, it.x) ?? it.y : it.y;
    const px = X(it.x), py = Y(y);
    const inView = it.x >= x0 && it.x <= x1 && y >= y0 && y <= y1;
    if (!inView) continue;
    const right = px < m.l + iw * 0.75;
    marks += group(it, `<circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="10" class="pt"/>`
      + (it.label ? t(px + (right ? 16 : -16), py - 16, it.label, "plabel", right ? "start" : "end") : ""));
  }
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet"><defs><clipPath id="${clip}"><rect x="${m.l}" y="${m.t}" width="${iw}" height="${ih}"/></clipPath></defs>`
    + `${head}${grid}${axes}<g clip-path="url(#${clip})">${marks}</g></svg>`;
}
