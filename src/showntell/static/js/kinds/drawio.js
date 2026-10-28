// draw.io diagrams, drawn by the official draw.io viewer (vendor/drawio, Apache-2.0), which loads
// only when a slide needs it. The builder has already rebuilt the page without links, outside
// images or web fonts; the viewer is pointed at local paths, so it never reaches the network.
// Cells are what cues point at; the cells on a layer reveal in source order, edges with their ends.

import { el, loadScript, READABLE_PX, STATIC } from "../draw.js";
import { norm } from "../util.js";

const VIEWER = `${STATIC}/vendor/drawio/viewer-static.min.js`;
const PAD = 14;

async function viewerReady() {
  if (!window.__drawioPaths) {
    window.__drawioPaths = true;
    // Everything the viewer could fetch stays on this server: stencils for icon libraries
    // (AWS, Azure, Cisco...) are not bundled, so those shapes keep their plain fallback.
    const local = `${STATIC}/vendor/drawio`;
    Object.assign(window, {
      DRAWIO_BASE_URL: local, DRAWIO_VIEWER_URL: local, STENCIL_PATH: `${local}/stencils`, IMAGE_PATH: `${local}/images`,
      STYLE_PATH: `${local}/styles`, CSS_PATH: `${local}/styles`, SHAPES_PATH: `${local}/shapes`, GRAPH_IMAGE_PATH: `${local}/img`,
      DRAW_MATH_URL: `${local}/math`, mxBasePath: `${local}/mxgraph`, mxLoadResources: false, mxLoadStylesheets: false,
    });
  }
  await loadScript(VIEWER);
  if (window.mxStencilRegistry && !window.__drawioNoStencils) {
    window.__drawioNoStencils = true;
    window.mxStencilRegistry.loadStencil = () => null; // no request for a stencil library that is not here
  }
  return window.GraphViewer;
}

export default {
  render(slide, body, parts, ctx) {
    const v = slide.visual || {};
    const host = el("div", "drawio");
    host.setAttribute("role", "img");
    host.setAttribute("aria-label", v.title || `Diagram: ${(v.items || []).map((i) => i.label).filter(Boolean).slice(0, 12).join(", ")}`);
    if (v.background) { host.classList.add("page"); host.style.setProperty("--page", v.background); }
    const inner = el("div", "drawio-graph");
    host.append(inner);
    parts.visual = host;
    parts.drawio = v;
    body.append(host);
    return viewerReady().then((GraphViewer) => {
      const doc = window.mxUtils.parseXml(v.xml);
      const viewer = new GraphViewer(inner, doc.documentElement,
        { highlight: "none", nav: false, lightbox: false, toolbar: null, resize: false, border: 0, "dark-mode": false });
      const g = viewer.graph;
      g.setEnabled(false);
      g.setTooltips(false);
      g.linkPolicy = "blank";
      parts.graph = g;
      inner.style.cssText = "";
    }).catch((e) => {
      inner.innerHTML = `<div class="err">The diagram could not be drawn.</div>`;
      ctx.report(`draw.io diagram on slide ${slide.id} failed to render: ${e.message || e}`, "render_error", slide.id);
    });
  },

  // Scale the diagram to the slide, centred, inside its page.
  layout(body, parts) {
    const g = parts.graph, host = parts.visual;
    if (!g) return;
    const inner = g.container;
    const svg = inner.querySelector("svg");
    if (svg) { svg.style.minWidth = ""; svg.style.minHeight = ""; }
    // Bounds in diagram units, from the cells' geometry: the same however the viewer zooms.
    const m = g.getModel(), cells = Object.values(m.cells).filter((c) => m.isVertex(c) || m.isEdge(c));
    const b = g.getBoundingBoxFromGeometry(cells, true);
    if (!b || !b.width || !b.height) return;
    const pad = host.classList.contains("page") ? PAD * 2 : 0, room = 6; // room for strokes and arrowheads
    const k = Math.min((body.clientWidth - pad - room) / b.width, (body.clientHeight - pad - room) / b.height, 2.5);
    host.style.width = `${Math.round(b.width * k + pad + room)}px`;
    host.style.height = `${Math.round(b.height * k + pad + room)}px`;
    g.view.scaleAndTranslate(k, (inner.clientWidth / k - b.width) / 2 - b.x, (inner.clientHeight / k - b.height) / 2 - b.y);
  },

  // Parts on a layer appear in order; cells inside a part with it; an edge once both its ends show.
  reveal(reveal, parts, veil) {
    const g = parts.graph, v = parts.drawio;
    if (!g || !("items" in reveal)) return;
    const shown = (pid) => v.ids.indexOf(pid) < reveal.items;
    for (const it of v.items) {
      const ends = v.edges[it.id];
      const part = v.part_of[it.id] ?? (v.ids.includes(it.id) ? it.id : null);
      const visible = ends ? ends.every(shown) : part ? shown(part) : true;
      for (const n of nodes(g, it.id)) veil(n, !visible);
    }
  },

  target(cue, parts, help) {
    const g = parts.graph;
    if (!g) return undefined;
    if (cue.kind === "item") {
      const r = cellRect(g, cue.id);
      return r ? { rects: [r] } : help.wholeVisual();
    }
    if (cue.kind === "text" && cue.text) {
      const want = norm(cue.text);
      const hit = parts.drawio.items.find((it) => norm(it.label).includes(want));
      const r = hit && cellRect(g, hit.id);
      return r ? { rects: [r] } : help.wholeVisual();
    }
    if (cue.kind === "region") {
      const b = toClient(g, g.getGraphBounds()), [x, y, w, h] = cue.box;
      const left = b.left + (b.width * x) / 100, top = b.top + (b.height * y) / 100;
      const width = (b.width * w) / 100, height = (b.height * h) / 100;
      return { rects: [{ left, top, width, height, right: left + width, bottom: top + height }] };
    }
    return undefined;
  },

  // Labels drawn smaller than the player reads comfortably, once the diagram fits the slide.
  measure(slide, body, parts, report) {
    const g = parts.graph;
    if (!g) return true;
    let small = Infinity;
    for (const it of parts.drawio.items) {
      const cell = g.getModel().getCell(it.id);
      if (!cell || !it.label) continue;
      small = Math.min(small, parseFloat(g.getCellStyle(cell).fontSize) || 12);
    }
    const px = small * g.view.scale;
    if (Number.isFinite(px) && px < READABLE_PX - 3) {
      report(`Slide ${slide.id}: the diagram's smallest labels are about ${Math.round(px)} px high on the slide, too small to read.`
        + " Show one page or area of it, split it, or `{.zoom}` onto each part as the narration names it.", "text_too_small", slide.id);
    }
    return true;
  },
};

// The SVG nodes drawn for a cell: its shape and its label.
function nodes(g, id) {
  const st = g.view.getState(g.getModel().getCell(id));
  return st ? [st.shape?.node, st.text?.node].filter(Boolean) : [];
}

// Where a cell is on screen: its shape and label boxes, in client coordinates.
function cellRect(g, id) {
  const st = g.view.getState(g.getModel().getCell(id));
  if (!st) return null;
  const box = new window.mxRectangle(st.x, st.y, st.width, st.height);
  if (st.text?.boundingBox) box.add(st.text.boundingBox);
  return toClient(g, box);
}

// Cell coordinates to client coordinates. The viewer zooms with a transform on its drawing
// layer, so map through that layer's screen matrix (which also carries the stage's and the
// camera's scaling).
function toClient(g, r) {
  const m = g.view.getDrawPane().getScreenCTM();
  const a = new DOMPoint(r.x, r.y).matrixTransform(m), b = new DOMPoint(r.x + r.width, r.y + r.height).matrixTransform(m);
  const left = Math.min(a.x, b.x), top = Math.min(a.y, b.y), width = Math.abs(b.x - a.x), height = Math.abs(b.y - a.y);
  return { left, top, width, height, right: left + width, bottom: top + height };
}
