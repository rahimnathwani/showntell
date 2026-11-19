// Memory pictures: variables (in call frames) at the left, objects and arrays to the right, arrows
// for pointers and markers under cells for pointers into arrays. The builder lays everything out;
// this draws it in the slide's colors and fits it to the slide. Variables, frames, objects,
// fields and cells are the parts cues point at. `memory step` versions keep objects in place:
// on a #step-N cue, arrows that change fade across, new objects fade in, changed values glow.

import { el, READABLE_PX, reducedMotion } from "../draw.js";
import { esc } from "../util.js";

const FONT = 22, ROW = 44, PAD = 12, MARK = 34;
const MAX_SCALE = 1.5;
const MORPH_S = 0.9;
const ease = (x) => 1 - (1 - x) ** 3;
const clamp = (x) => Math.max(0, Math.min(1, x));
let markerSeq = 0;

export default {
  render(slide, body, parts) {
    const v = slide.visual || {};
    const host = el("div", "memory");
    host.setAttribute("role", "img");
    host.setAttribute("aria-label", describe(v.steps?.[0] || v));
    parts.visual = host;
    parts.memory = v;
    parts.drawn = { key: "" };
    parts.reach = reach(v.steps?.length ? v.steps : [v]);
    body.append(host);
    draw(parts, v.steps?.[0] || v, null, 1);
  },

  layout(body, parts) {
    const svg = parts.svg;
    if (!svg) return;
    const [, , w, h] = parts.box;
    const k = Math.min(body.clientWidth / w, body.clientHeight / h, MAX_SCALE) * 0.98;
    parts.scale = k;
    svg.setAttribute("width", `${Math.round(w * k)}`);
    svg.setAttribute("height", `${Math.round(h * k)}`);
  },

  frame(st, slide, parts, t) {
    const v = slide.visual || {};
    if (!(v.steps?.length > 1)) return "";
    const fired = st.cues.filter((c) => c.kind === "step");
    const last = fired[fired.length - 1];
    const n = last?.n || 1, from = fired.length > 1 ? fired[fired.length - 2].n : 1;
    const p = !last || reducedMotion() ? 1 : clamp((t - last._at) / MORPH_S);
    const key = `${n}:${p.toFixed(2)}`;
    parts.moving = key !== parts.drawn.key; // redrawn: the pointer follows what moved
    if (parts.moving) {
      draw(parts, v.steps[n - 1], p < 1 && from !== n ? v.steps[from - 1] : null, ease(p));
      parts.drawn.key = key;
    }
    return key;
  },

  // Variables and objects in order; a field or cell with its object; an arrow once both ends show.
  reveal(reveal, parts, veil) {
    const v = parts.memory;
    if (!parts.svg || !("items" in reveal)) return;
    const shown = (id) => {
      const top = v.part_of?.[id] || id;
      const i = (v.ids || []).indexOf(top);
      return i < 0 ? reveal.items >= (v.ids || []).length : i < reveal.items;
    };
    for (const g of parts.svg.querySelectorAll("g.arrow, g.marker")) veil(g, !(shown(g.dataset.from) && shown(g.dataset.to)));
  },

  measure(slide, body, parts, report) {
    if (!parts.svg) return true;
    const px = FONT * (parts.scale || 1);
    if (px < READABLE_PX - 3) {
      report(`Slide ${slide.id}: the memory picture had to shrink until its text is about ${Math.round(px)} px high, too small to read.`
        + " Show fewer objects or shorter values, or split it across slides.", "text_too_small", slide.id);
    }
    return true;
  },
};

function describe(v) {
  const vars = (v.vars || []).map((x) => `${x.name} ${x.ptr ? `points to ${x.ptr}` : x.cell ? `points to ${x.cell.join(" ")}` : `= ${x.text}`}`);
  const objs = (v.objects || []).map((o) => `${o.id}${o.type ? ` (${o.type})` : ""}: ${o.slots.map((s) => `${s.label} ${s.ptr ? `-> ${s.ptr}` : s.text}`).join(", ")}`);
  return `Memory. ${[...vars, ...objs].join(". ")}.`;
}

// Draw version `v`, gliding from `was` (null: no glide) at progress p, keeping which parts are
// pointed at or veiled.
function draw(parts, v, was, p) {
  const host = parts.visual;
  const keep = new Map([...host.querySelectorAll("[data-id]")].map((g) => [g.dataset.id, g.classList]));
  const arrowsVeiled = new Set([...host.querySelectorAll("g.arrow.veiled, g.marker.veiled")].map((g) => g.dataset.key));
  parts.box = parts.reach || v.box;
  host.innerHTML = svgOf(v, was, p, parts.box);
  const svg = host.querySelector("svg");
  parts.svg = svg;
  parts.items.clear();
  let idx = 0;
  const order = new Map((parts.memory.ids || []).map((id, i) => [id, i]));
  svg.querySelectorAll("[data-id]").forEach((g) => {
    const id = g.dataset.id;
    parts.items.set(id, g);
    const top = parts.memory.part_of?.[id] || id;
    g.dataset.idx = order.has(top) ? order.get(top) : idx++;
    const cls = keep.get(id);
    if (cls) for (const c of ["active", "veiled"]) g.classList.toggle(c, cls.contains(c));
  });
  svg.querySelectorAll("g.arrow, g.marker").forEach((g) => g.classList.toggle("veiled", arrowsVeiled.has(g.dataset.key)));
  svg.querySelector("[data-id].active")?.parentElement.classList.add("has-active");
  const body = host.closest(".slide-body");
  if (body && parts.scale) {
    const [, , w, h] = parts.box;
    svg.setAttribute("width", `${Math.round(w * parts.scale)}`);
    svg.setAttribute("height", `${Math.round(h * parts.scale)}`);
  }
}

// Where each part is in a version: objects and their slots, variables and frames, by id.
function geometry(v) {
  const g = new Map();
  for (const f of v.frames || []) g.set(f.id, { x: f.x, y: f.y, w: f.w, h: f.h });
  for (const x of v.vars || []) g.set(x.id, { x: x.x + x.nw, y: x.y, w: x.w, h: x.h });
  for (const o of v.objects || []) {
    g.set(o.id, { x: o.x, y: o.y, w: o.w, h: o.h });
    for (const s of o.slots) g.set(s.id, { x: o.x + s.x, y: o.y + s.y, w: s.w, h: s.h });
  }
  return g;
}

const lerp = (a, b, p) => a + (b - a) * p;
const mix = (a, b, p) => (a && b ? { x: lerp(a.x, b.x, p), y: lerp(a.y, b.y, p), w: lerp(a.w, b.w, p), h: lerp(a.h, b.h, p) } : b || a);

function svgOf(v, was, p, box) {
  const [bx, by, bw, bh] = box;
  const t = (x, y, text, cls, anchor = "start") => `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" class="${cls}" text-anchor="${anchor}">${esc(text)}</text>`;
  const changed = new Set(v.changed || []);
  const old = was ? geometry(was) : null;
  const now = geometry(v);
  const at = (id) => (old && old.has(id) ? mix(old.get(id), now.get(id), p) : now.get(id));
  const fade = (id) => (old && !old.has(id) ? ` style="opacity:${p.toFixed(3)}"` : "");
  const marker = `mem-arrow-${++markerSeq}`;
  let out = `<svg viewBox="${bx} ${by} ${bw} ${bh}" preserveAspectRatio="xMidYMid meet"><defs><marker id="${marker}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="head"/></marker></defs>`;
  for (const f of v.frames || []) {
    const g = at(f.id);
    out += `<g class="frame" data-id="${esc(f.id)}"${fade(f.id)}><rect x="${g.x}" y="${g.y}" width="${g.w}" height="${g.h}" rx="12"/>${t(g.x + PAD, g.y + ROW * 0.68, f.name, "fname")}</g>`;
  }
  for (const x of v.vars || []) {
    const g = at(x.id);
    out += `<g class="var" data-id="${esc(x.id)}"${fade(x.id)}>${t(g.x - 10, g.y + ROW * 0.66, x.name, "vname", "end")}`
      + valueBox(x, g, changed.has(x.id), t) + "</g>";
  }
  for (const o of v.objects || []) {
    const g = at(o.id);
    let inner = o.type ? t(g.x + 2, g.y - 7, o.type, "otype") : "";
    inner += `<rect x="${g.x}" y="${g.y}" width="${g.w}" height="${g.h}" rx="8" class="obox"/>`;
    for (const s of o.slots) {
      const sg = { x: g.x + s.x, y: g.y + s.y, w: s.w, h: s.h };
      let cell = valueBox(s, sg, changed.has(s.id), t, true);
      if (o.kind === "obj" || o.column) {
        cell = t(g.x + s.lw / 2, sg.y + ROW * 0.66, s.label, "flabel", "middle") + cell;
        if (s.y > 0) cell = `<line x1="${g.x}" x2="${g.x + g.w}" y1="${sg.y}" y2="${sg.y}" class="sep"/>` + cell;
      } else {
        cell += t(sg.x + sg.w / 2, g.y + g.h + 18, s.label, "idx", "middle");
        if (s.x > 0) cell = `<line x1="${sg.x}" x2="${sg.x}" y1="${g.y}" y2="${g.y + g.h}" class="sep"/>` + cell;
      }
      inner += `<g class="slot" data-id="${esc(s.id)}">${cell}</g>`;
    }
    if (o.kind === "obj" || o.column) inner += `<line x1="${g.x + o.lw}" x2="${g.x + o.lw}" y1="${g.y}" y2="${g.y + g.h}" class="sep"/>`;
    out += `<g class="obj${o.dim ? " dim" : ""}" data-id="${esc(o.id)}"${fade(o.id)}>${inner}</g>`;
  }
  // Arrows: those in both versions move with their ends; the rest fade across.
  const keyed = (ver, geo) => arrows(ver, geo);
  const nowArrows = keyed(v, { get: (id) => at(id) });
  const oldArrows = was ? keyed(was, { get: (id) => old.get(id) }) : [];
  const nowKeys = new Set(nowArrows.map((a) => a.key)), oldKeys = new Set(oldArrows.map((a) => a.key));
  for (const a of nowArrows) out += arrowSvg(a, marker, was && !oldKeys.has(a.key) ? p : 1);
  for (const a of oldArrows) if (!nowKeys.has(a.key)) out += arrowSvg(a, marker, 1 - p);
  // Markers under cells for pointers into arrays, stacked when several share a cell.
  const per = new Map();
  for (const x of v.vars || []) {
    if (!x.cell) continue;
    const cid = `${x.cell[0]}.${x.cell[1]}`, cg = at(cid);
    if (!cg) continue;
    const k = per.get(cid) || 0;
    per.set(cid, k + 1);
    const o = (v.objects || []).find((q) => q.id === x.cell[0]);
    const base = cg.y + cg.h + (o && !o.column ? 22 : 4) + k * MARK, cx = cg.x + cg.w / 2;
    out += `<g class="marker" data-key="${esc(x.id)}" data-from="${esc(x.id)}" data-to="${esc(x.cell[0])}"><path d="M${cx},${base} l-8,13 h16 z" class="tri"/>${t(cx, base + 31, x.name, "mname", "middle")}</g>`;
  }
  return `${out}</svg>`;
}

function valueBox(x, g, changed, t, flush = false) {
  const cls = `vbox${changed ? " changed" : ""}`;
  const r = flush ? "" : ' rx="8"';
  let inside;
  if (x.ptr) inside = `<circle cx="${g.x + g.w / 2}" cy="${g.y + g.h / 2}" r="6" class="dot"/>`;
  else if (x.cell) inside = t(g.x + g.w / 2, g.y + ROW * 0.66, `${x.cell[0]}[${x.cell[1]}]`, "vnull", "middle");
  else if (x.null) inside = t(g.x + g.w / 2, g.y + ROW * 0.66, x.text, "vnull", "middle");
  else inside = t(g.x + g.w / 2, g.y + ROW * 0.66, x.text, "vtext", "middle");
  return `<rect x="${g.x}" y="${g.y}" width="${g.w}" height="${g.h}"${r} class="${cls}"/>${inside}`;
}

// Each pointer, from the dot in the box that holds it to its object, by the first of several
// shapes that crosses no box: straight on into the target's side or top, under or over what is
// in the way, or a loop round the right. Lanes keep detours apart.
export function arrows(v, geo) {
  const out = [];
  const objs = new Map((v.objects || []).map((o) => [o.id, o]));
  const boxes = [
    ...(v.objects || []).map((o) => ({ id: o.id, ...geo.get(o.id), top: o.type ? 26 : 0, bottom: o.kind === "arr" && !o.column ? 24 : 0 })),
    ...(v.vars || []).map((x) => ({ id: x.id, ...geo.get(x.id), top: 0, bottom: 0 })),
  ].filter((b) => b.w);
  const outer = (b) => ({ l: b.x, t: b.y - b.top, r: b.x + b.w, b: b.y + b.h + b.bottom });
  let lane = 0;
  const add = (fromId, holder, partOf, target) => {
    const s = geo.get(fromId), d = geo.get(target), o = objs.get(target);
    if (!s || !d || !o) return;
    const h = (holder && geo.get(holder)) || s;
    const sx = s.x + s.w / 2, sy = s.y + s.h / 2, x1 = h.x + h.w;
    const dt = d.y - (o.type ? 26 : 0), db = d.y + d.h + (o.kind === "arr" && !o.column ? 24 : 0);
    const others = boxes.filter((b) => b.id !== target && b.id !== (holder || fromId)).map(outer);
    // The holder is in the way too, all but the cell the arrow starts in.
    const own = holder ? outer(boxes.find((b) => b.id === holder) || { ...h, top: 0, bottom: 0 }) : null;
    const cell = { l: s.x, t: s.y, r: s.x + s.w, b: s.y + s.h };
    const near = (b, l, r, t, btm) => b.r > l && b.l < r && b.b > t && b.t < btm;
    const gap = 26 + 14 * lane;
    const uniq = (xs) => [...new Set(xs.map((n) => Math.round(n)))];
    const txs = uniq([Math.min(d.x + d.w - 12, Math.max(d.x + 12, sx)), d.x + d.w / 2, d.x + 14, d.x + d.w - 14]);
    const tys = uniq([Math.min(d.y + d.h - 10, Math.max(d.y + 10, sy)), d.y + d.h / 2]);
    const hb = Math.max(h.y + h.h, own ? own.b : 0); // the holder's bottom
    const cands = [];
    if (d.x > x1 + 10) {
      for (const ty of tys) { // straight on, into the left side
        const dx = Math.max(40, (d.x - sx) / 2);
        cands.push({ segs: [["M", sx, sy], ["C", sx + dx, sy, d.x - dx, ty, d.x, ty]] });
      }
    }
    for (const tx of txs) {
      if (dt > h.y + h.h + 10) { // straight down, onto the top
        const dy = Math.max(30, (dt - sy) / 2);
        cands.push({ segs: [["M", sx, sy], ["C", sx, sy + dy, tx, dt - dy, tx, dt]] });
      }
      if (dt > sy + 20) { // out of the holder's right edge, then down onto the top
        cands.push({ segs: [["M", sx, sy], ["L", x1, sy], ["C", x1 + 50, sy, tx, dt - 50, tx, dt]] });
      }
      if (dt > hb + 30) { // out to the right, down past the holder, across, down onto the top
        const yb = hb + 16 + 14 * lane;
        cands.push({ lane: true, segs: [["M", sx, sy], ["L", x1, sy], ["C", x1 + 30, sy, x1 + 30, yb, x1, yb], ["L", tx, yb], ["L", tx, dt]] });
      }
    }
    if (d.x > x1 + 10) {
      for (const ty of tys) { // under what is in the way, up into the left side
        const lo = Math.max(sy, ty, ...others.filter((b) => near(b, sx, d.x, -1e9, 1e9)).map((b) => b.b)) + gap;
        cands.push({ lane: true, segs: [["M", sx, sy], ["L", x1, sy], ["C", x1 + 30, sy, x1 + 30, lo, x1 + 60, lo], ["L", d.x - 60, lo], ["C", d.x - 30, lo, d.x - 30, ty, d.x, ty]] });
      }
    }
    for (const tx of txs) { // over the top, down onto the target's top
      const l = Math.min(x1, tx) - 20, r = Math.max(x1, tx) + 20;
      const hi = Math.min(sy, dt, ...others.filter((b) => near(b, l, r, -1e9, 1e9)).map((b) => b.t)) - gap;
      cands.push({ lane: true, segs: [["M", sx, sy], ["L", x1, sy], ["C", x1 + 40, sy, x1 + 40, hi, x1, hi], ["L", tx, hi], ["L", tx, dt]] });
    }
    for (const ry of tys) { // a loop round the right, into the target's right side
      const t0 = Math.min(sy, ry) - 10, t1 = Math.max(sy, ry) + 10;
      const lx = Math.max(x1, d.x + d.w, ...others.filter((b) => near(b, Math.min(x1, d.x), 1e9, t0, t1)).map((b) => b.r)) + gap;
      cands.push({ lane: true, segs: [["M", sx, sy], ["L", x1, sy], ["C", lx, sy, lx, ry, d.x + d.w, ry]] });
    }
    for (const tx of txs) { // under everything, up into the target's bottom
      const l = Math.min(x1, tx) - 20, r = Math.max(x1, tx) + 20;
      const lo = Math.max(sy, db, ...others.filter((b) => near(b, l, r, -1e9, 1e9)).map((b) => b.b)) + gap;
      cands.push({ lane: true, segs: [["M", sx, sy], ["L", x1, sy], ["C", x1 + 40, sy, x1 + 40, lo, x1, lo], ["L", tx, lo], ["L", tx, db]] });
    }
    const target_ = outer({ ...d, top: o.type ? 26 : 0, bottom: o.kind === "arr" && !o.column ? 24 : 0 });
    let best = null;
    for (const c of cands) {
      const pts = sample(c.segs);
      const hit = others.filter((b) => pts.some((q) => inside(q, b))).length + (pts.slice(0, -4).some((q) => inside(q, target_)) ? 1 : 0)
        + (own && pts.some((q) => inside(q, own) && !inside(q, cell, -3)) ? 1 : 0);
      if (!best || hit < best.hit) best = { ...c, hit, pts };
      if (!hit) break;
    }
    if (best.lane) lane++;
    out.push({ key: `${fromId}->${target}`, from: partOf, to: target, path: svgPath(best.segs), pts: best.pts });
  };
  for (const x of v.vars || []) if (x.ptr) add(x.id, null, x.frame || x.id, x.ptr);
  for (const o of v.objects || []) for (const sl of o.slots) if (sl.ptr) add(sl.id, o.id, o.id, sl.ptr);
  return out;
}

const inside = (q, b, m = 3) => q[0] > b.l + m && q[0] < b.r - m && q[1] > b.t + m && q[1] < b.b - m;

// Points along a path of M, L and C segments, to test it against boxes and measure its reach.
function sample(segs) {
  const pts = [];
  let cx = 0, cy = 0;
  for (const [op, ...a] of segs) {
    if (op === "M") { cx = a[0]; cy = a[1]; pts.push([cx, cy]); continue; }
    const n = op === "L" ? 8 : 20;
    for (let i = 1; i <= n; i++) {
      const t = i / n, u = 1 - t;
      pts.push(op === "L" ? [cx + (a[0] - cx) * t, cy + (a[1] - cy) * t]
        : [u * u * u * cx + 3 * u * u * t * a[0] + 3 * u * t * t * a[2] + t * t * t * a[4], u * u * u * cy + 3 * u * u * t * a[1] + 3 * u * t * t * a[3] + t * t * t * a[5]]);
    }
    [cx, cy] = op === "L" ? a : [a[4], a[5]];
  }
  return pts;
}

const svgPath = (segs) => segs.map(([op, ...a]) => `${op}${a.map((n) => n.toFixed(1)).join(",")}`).join(" ");

// The drawing's box widened to hold every version's arrows, so detours are never cut off.
export function reach(versions) {
  let [x, y, w, h] = versions[0].box;
  let r = x + w, b = y + h;
  for (const v of versions) {
    [x, y] = [Math.min(x, v.box[0]), Math.min(y, v.box[1])];
    [r, b] = [Math.max(r, v.box[0] + v.box[2]), Math.max(b, v.box[1] + v.box[3])];
    for (const a of arrows(v, geometry(v))) {
      for (const [px, py] of a.pts) { x = Math.min(x, px - 16); y = Math.min(y, py - 16); r = Math.max(r, px + 16); b = Math.max(b, py + 16); }
    }
  }
  return [x, y, r - x, b - y];
}

function arrowSvg(a, marker, opacity) {
  const op = opacity < 1 ? ` style="opacity:${opacity.toFixed(3)}"` : "";
  return `<g class="arrow" data-key="${esc(a.key)}" data-from="${esc(a.from)}" data-to="${esc(a.to)}"${op}><path d="${a.path}" marker-end="url(#${marker})"/></g>`;
}
