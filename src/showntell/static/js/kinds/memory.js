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
  parts.box = v.box;
  host.innerHTML = svgOf(v, was, p);
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
    const [, , w, h] = v.box;
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

function svgOf(v, was, p) {
  const [bx, by, bw, bh] = v.box;
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

// Each pointer: from the middle of the box that holds it to its object's nearest side, around
// any box in the way.
function arrows(v, geo) {
  const out = [];
  const objs = new Map((v.objects || []).map((o) => [o.id, o]));
  const tb = v.dir === "TB";
  const boxes = (v.objects || []).map((o) => ({ id: o.id, ...geo.get(o.id), top: o.type ? 26 : 0, bottom: o.kind === "arr" && !o.column ? 24 : 0 }));
  // Boxes other than the two ends that a straight run from (x0, y0) to (x1, y1) would cross.
  const blocking = (x0, y0, x1, y1, skip) => boxes.filter((b) => {
    if (skip.includes(b.id) || !b.w) return false;
    for (let i = 1; i < 20; i++) {
      const x = x0 + ((x1 - x0) * i) / 20, y = y0 + ((y1 - y0) * i) / 20;
      if (x > b.x - 6 && x < b.x + b.w + 6 && y > b.y - b.top - 6 && y < b.y + b.h + b.bottom + 6) return true;
    }
    return false;
  });
  let lane = 0;
  const add = (fromId, holder, partOf, target) => {
    const s = geo.get(fromId), d = geo.get(target), o = objs.get(target);
    if (!s || !d || !o) return;
    const sx = s.x + s.w / 2, sy = s.y + s.h / 2;
    let path;
    if (!tb && d.x > sx + 20) {
      const tx = d.x, ty = Math.min(d.y + d.h - 10, Math.max(d.y + 10, sy));
      const dx = Math.max(40, (tx - sx) / 2);
      const way = blocking(sx, sy, tx, ty, [holder, target]);
      if (way.length) {
        // Under the boxes in the way, then up into the target's left side.
        const low = Math.max(...way.map((b) => b.y + b.h + b.bottom)) + 26 + 16 * lane++; // each detour its own lane
        const x1 = Math.min(...way.map((b) => b.x)) - 20, x2 = Math.max(...way.map((b) => b.x + b.w)) + 20;
        path = `M${sx},${sy} C${sx + 40},${sy} ${x1 - 40},${low} ${x1},${low} L${x2},${low} C${x2 + 30},${low} ${tx - 30},${ty} ${tx},${ty}`;
      } else path = `M${sx},${sy} C${sx + dx},${sy} ${tx - dx},${ty} ${tx},${ty}`;
    } else if (tb && d.y > sy + 20) {
      const tx = Math.min(d.x + d.w - 10, Math.max(d.x + 10, sx)), ty = d.y - (o.type ? 26 : 0);
      const dy = Math.max(30, (ty - sy) / 2);
      const way = blocking(sx, sy, tx, ty, [holder, target]);
      if (way.length) {
        const side = Math.max(...way.map((b) => b.x + b.w)) + 26 + 16 * lane++;
        const y1 = Math.min(...way.map((b) => b.y - b.top)) - 20, y2 = Math.max(...way.map((b) => b.y + b.h + b.bottom)) + 20;
        path = `M${sx},${sy} C${sx},${sy + 40} ${side},${y1 - 40} ${side},${y1} L${side},${y2} C${side},${y2 + 30} ${tx},${ty - 30} ${tx},${ty}`;
      } else path = `M${sx},${sy} C${sx},${sy + dy} ${tx},${ty - dy} ${tx},${ty}`;
    } else {
      // Back or sideways: out of the holder's right edge and around, never through a box.
      const h = (holder && geo.get(holder)) || s;
      const x1 = h.x + h.w;
      const besides = d.x < h.x + h.w && h.x < d.x + d.w; // stacked in one column
      if (besides || tb) {
        // A loop on the right, into the target's right side.
        const ty = d.y + d.h / 2;
        const lx = Math.max(x1, d.x + d.w) + 36 + 14 * lane++;
        path = `M${sx},${sy} L${x1},${sy} C${lx},${sy} ${lx},${ty} ${d.x + d.w},${ty}`;
      } else {
        // Over the top of everything between, down onto the target's top.
        const tx = d.x + d.w / 2, tTop = d.y - (o.type ? 26 : 2);
        const span = boxes.filter((b) => b.w && b.x < x1 + 20 && b.x + b.w > tx - 20);
        const ytop = Math.min(sy, tTop, ...span.map((b) => b.y - b.top)) - 28 - 14 * lane++;
        path = `M${sx},${sy} L${x1},${sy} C${x1 + 44},${sy} ${x1 + 44},${ytop} ${x1},${ytop} L${tx + 30},${ytop} C${tx},${ytop} ${tx},${ytop} ${tx},${tTop}`;
      }
    }
    out.push({ key: `${fromId}->${target}`, from: partOf, to: target, path });
  };
  for (const x of v.vars || []) if (x.ptr) add(x.id, null, x.frame || x.id, x.ptr);
  for (const o of v.objects || []) for (const s of o.slots) if (s.ptr) add(s.id, o.id, o.id, s.ptr);
  return out;
}

function arrowSvg(a, marker, opacity) {
  const op = opacity < 1 ? ` style="opacity:${opacity.toFixed(3)}"` : "";
  return `<g class="arrow" data-key="${esc(a.key)}" data-from="${esc(a.from)}" data-to="${esc(a.to)}"${op}><path d="${a.path}" marker-end="url(#${marker})"/></g>`;
}
