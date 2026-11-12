// DOM helpers shared by the slide kinds (kinds/*.js) and the renderer.

import { esc } from "./util.js";

export const STATIC = document.body.dataset.static || "static";
export const READABLE_PX = 15; // smallest comfortable text on the 1280-wide stage
export const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const loaded = {};

// A single-file export carries the libraries it needs in the page (export_html in export.py),
// keyed by their path under static/, the large ones gzipped; elsewhere they load from the server.
function inline(src) {
  const files = window.__showntellFiles;
  return files && src.startsWith(`${STATIC}/`) ? files[src.slice(STATIC.length + 1)] : undefined;
}

// A file under static/ as a URL: the copy a single-file export carries (fonts as data: URLs), or
// the server's.
export function assetUrl(rel) {
  const entry = inline(`${STATIC}/${rel}`);
  return typeof entry === "string" ? entry : `${STATIC}/${rel}`;
}

async function unpack(entry) {
  if (typeof entry === "string") return entry;
  const bytes = Uint8Array.from(atob(entry.gzip), (c) => c.charCodeAt(0));
  return new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"))).text();
}

export function loadScript(src) {
  if (!loaded[src]) {
    const entry = inline(src);
    loaded[src] = entry !== undefined ? unpack(entry).then((code) => {
      const s = document.createElement("script");
      s.textContent = code; // runs as it is added
      document.head.append(s);
    }) : new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = res;
      s.onerror = () => rej(new Error(`could not load ${src}`));
      document.head.append(s);
    });
  }
  return loaded[src];
}

export function loadCss(href) {
  if (!loaded[href]) {
    const entry = inline(href);
    loaded[href] = entry !== undefined ? unpack(entry).then((css) => {
      const st = document.createElement("style");
      st.textContent = css;
      document.head.append(st);
    }) : new Promise((res) => {
      const l = document.createElement("link");
      l.rel = "stylesheet";
      l.href = href;
      l.onload = l.onerror = res; // a missing stylesheet should not stop the slide
      document.head.append(l);
    });
  }
  return loaded[href];
}

export function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}

export function highlight(code, lang) {
  const hljs = window.hljs;
  try {
    if (hljs && lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
    if (hljs && code.length < 4000) return hljs.highlightAuto(code).value;
  } catch (e) {
    /* fall through to plain text */
  }
  return esc(code);
}

// DOM Range covering characters [start, end) of an element's text.
export function textRange(container, start, end) {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  let pos = 0, node, started = false;
  while ((node = walker.nextNode())) {
    const len = node.nodeValue.length;
    if (!started && start < pos + len) { range.setStart(node, start - pos); started = true; }
    if (started && end <= pos + len) { range.setEnd(node, end - pos); return range; }
    pos += len;
  }
  return started ? range : null;
}

// Fit the visual to the slide body: shrink what is too big, and grow what is small until it fills
// the space, up to a size per kind that keeps text from looking shouted. A transform rather than
// CSS zoom: bounding boxes of text ranges inside a transform are reported exactly, which keeps
// the pointer on the words it points at.
const GROW = [
  [".codebox, .diff", 1.6],
  ["table.data, .tree", 1.5],
  [".math", 1.3],
  [".list", 1.35],
  [".chat", 1.3],
  [".stats", 1.25],
];
export function fit(body) {
  const content = body.firstElementChild;
  if (!content || content.matches(".diagram, .image, .chart")) return;
  content.style.transform = "";
  content.style.width = "";
  content.classList.remove("wrapped", "stacked");
  const bw = body.clientWidth, bh = body.clientHeight;
  if (content.matches(".codebox.scroll")) {
    // Long code keeps its size and scrolls: fit the width only, and the box to the slide's height.
    // Lines too long for the reading size wrap.
    content.style.maxHeight = "";
    let k = Math.min(1.15, (bw / Math.max(content.offsetWidth, content.scrollWidth)) * 0.98);
    const floor = CODE_PX / codeFont(content);
    if (k < floor) {
      content.classList.add("wrapped");
      content.style.width = `${Math.floor((bw / floor) * 0.98)}px`;
      k = floor;
    }
    content.dataset.scale = k.toFixed(3);
    content.style.maxHeight = `${Math.floor(bh / k)}px`;
    content.style.transform = `scale(${k})`;
    return;
  }
  const grow = GROW.find(([sel]) => content.matches(sel))?.[1] || 1;
  let k = fitScale(content, bw, bh, grow);
  if (content.matches(".codebox, .diff")) k = fitCode(content, bw, bh, grow, k);
  // A list spans the body's width, so it can only grow by laying out narrower and scaling up:
  // try sizes from the largest down until its height fits.
  if (content.matches(".list:not(.timeline)") && grow > 1 && k >= 0.97) {
    for (let g = grow; g > 1.001; g -= 0.05) {
      content.style.width = `${Math.floor((bw / g) * 0.98)}px`;
      if (content.scrollWidth <= (bw / g) * 0.98 + 1 && Math.max(content.offsetHeight, content.scrollHeight) * g <= bh * 0.98) { k = g; break; }
      content.style.width = "";
    }
  }
  content.dataset.scale = k.toFixed(3);
  if (k !== 1) content.style.transform = `scale(${k})`;
}

// Code keeps a reading size. Shrinking a whole block to fit its longest line leaves every line
// too small to read, so first a side-by-side diff stacks its panes, then long lines wrap at the
// largest size whose wrapped height still fits. Only code with too many lines ends up small.
export const CODE_PX = 17; // smallest code text on the stage before long lines wrap
const codeFont = (content) => parseFloat(getComputedStyle(content.querySelector(".code") || content).fontSize) || 22;

const WRAP_BELOW = 0.85; // code shrunk further than this for one long line wraps instead, if that lets it grow

function fitCode(content, bw, bh, grow, k) {
  const floor = CODE_PX / codeFont(content);
  if (k >= Math.max(floor, WRAP_BELOW)) return k;
  // Readable as it is: change the layout only for a real gain.
  let need = k >= floor ? k * 1.1 : k + 0.01;
  // Too many lines, not too long ones: stacking and wrapping would only make it taller.
  const cw = Math.max(content.offsetWidth, content.scrollWidth), ch = Math.max(content.offsetHeight, content.scrollHeight);
  if (bh / ch <= bw / cw) return k;
  if (content.matches(".diff")) {
    content.classList.add("stacked");
    const stacked = fitScale(content, bw, bh, grow);
    if (stacked >= need) k = stacked;
    else content.classList.remove("stacked");
    need = Math.max(need, k + 0.01); // wrapping must also beat the stacked layout
    if (k >= Math.max(floor, WRAP_BELOW)) return k;
  }
  // Wrapping trades width for height: try the reading size first, then smaller ones.
  // Code that changes in steps is held at its widest step's width; wrapping replaces that.
  const minWidth = content.style.minWidth;
  content.style.minWidth = "";
  content.classList.add("wrapped");
  for (let g = Math.min(grow, Math.max(floor, 1)); g >= need; g -= 0.04) {
    content.style.width = `${Math.floor((bw / g) * 0.98)}px`;
    const h = Math.max(content.offsetHeight, content.scrollHeight);
    if (content.scrollWidth <= (bw / g) * 0.98 + 1 && h * g <= bh * 0.98) return g;
  }
  content.classList.remove("wrapped");
  content.style.width = "";
  content.style.minWidth = minWidth;
  return k;
}

function fitScale(content, bw, bh, grow) {
  const cw = Math.max(content.offsetWidth, content.scrollWidth), ch = Math.max(content.offsetHeight, content.scrollHeight);
  if (!cw || !ch) return 1;
  let k = Math.min(grow, bw / cw, bh / ch) * 0.98;
  if (Math.abs(k - 1) <= 0.02) k = 1;
  return Math.max(0.3, k);
}
