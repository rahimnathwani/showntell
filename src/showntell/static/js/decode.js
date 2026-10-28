// Withheld labels: a cue styled `decode` keeps its target's text scrambled from the start of the
// slide until the cue fires, then resolves it left to right in a fifth of a second. For the
// answer the narration builds up to: the last node of a chain, a result value. On the narration
// clock, so seeking and frame capture show the same letters.

import { CUE_LEAD, slideCues } from "./timeline.js";
import { kindOf } from "./render.js";
import { reducedMotion, S } from "./state.js";

const RESOLVE_S = 0.2;
const GLYPHS = "#%&@$*+?<>=/\\0123456789ABCDEFGHJKLMNPQRSTUVWXYZ";

// Find each withheld label on a freshly drawn slide and scramble it at once, before it is seen.
// Called again once the slide has finished drawing, for kinds that draw later (diagrams).
export function prepareDecode(view, slide) {
  const cues = slideCues(S.W, S.TL, S.W.slides.findIndex((x) => x.id === slide.id)).filter((c) => (c.style || "").split(/\s+/).includes("decode"));
  if (!cues.length) { view.decode = null; return; }
  const done = new Set((view.decode?.labels || []).map((l) => l.node));
  const labels = view.decode?.labels || [];
  for (const cue of cues) {
    const pane = view.panes?.[cue.pane || 0];
    const parts = pane?.parts || view.parts;
    const el = labelOf(cue, parts, pane?.slide || slide);
    if (!el) continue;
    for (const node of textNodes(el)) {
      if (done.has(node)) continue;
      done.add(node);
      // Cues that find a drawing's text by its words still find it while it is scrambled.
      const text = node.parentElement?.closest("text");
      if (text && text.dataset.plain === undefined) text.dataset.plain = text.textContent;
      labels.push({ node, text: node.data, from: cue._at - CUE_LEAD, shown: null });
    }
  }
  view.decode = { labels };
  applyDecode(view, -Infinity);
}

// The letters for time t. Returns a key that changes when any label does, for frame capture.
export function applyDecode(view, t) {
  const d = view?.decode;
  if (!d) return "";
  const still = reducedMotion();
  let key = "";
  d.labels.forEach((l, i) => {
    const p = still ? (t >= l.from ? 1 : 0) : Math.max(0, Math.min(1, (t - l.from) / RESOLVE_S));
    // The letters still hidden change a few times while it resolves, then hold.
    const text = p >= 1 ? l.text : scramble(l.text, p, i * 7919 + Math.floor(p * 5));
    if (text !== l.shown) { l.node.data = text; l.shown = text; }
    if (p < 1) key += `${i}:${Math.round(p * 20)}/`;
  });
  return key;
}

// The element holding a cue's label: the slide's kind finds it (a drawing's group, a diagram
// node), else a list item or table row.
function labelOf(cue, parts, slide) {
  const own = kindOf(slide).element?.(cue, parts);
  if (own !== undefined) return own;
  if (cue.kind === "item" || cue.kind === "row") return parts.items.get(cue.id) || parts.rows.get(cue.id) || null;
  return null;
}

function textNodes(el) {
  const out = [];
  const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.data.trim() && !n.parentElement?.closest("title, desc, style, script") ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
  });
  while (walk.nextNode()) out.push(walk.currentNode);
  return out;
}

// The first p of the letters are the real ones; the rest are stand-ins, symbols, digits and capitals
// mixed so it reads as hidden rather than misspelt. Spaces and punctuation stay, keeping its shape.
function scramble(text, p, seed) {
  const real = Math.floor(text.length * p);
  let out = "";
  for (let k = 0; k < text.length; k++) {
    const c = text[k];
    if (k < real) { out += c; continue; }
    out += /[\p{L}\p{N}]/u.test(c) ? GLYPHS[hash(seed, k) % GLYPHS.length] : c;
  }
  return out;
}

function hash(a, b) {
  let h = Math.imul(a ^ 0x9e3779b9, 0x85ebca6b) ^ Math.imul(b + 0x632be5ab, 0xc2b2ae35);
  h ^= h >>> 15;
  return (Math.imul(h, 0x27d4eb2f) >>> 0);
}
