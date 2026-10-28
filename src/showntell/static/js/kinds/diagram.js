// Mermaid diagrams: nodes are the parts cues point at, flowcharts reveal node by node, and edge
// paths send a dot along the edges.

import { el, loadScript, READABLE_PX, reducedMotion, STATIC } from "../draw.js";
import { norm } from "../util.js";

const HOP_S = 0.9; // seconds per edge for the travelling dot

export default {
  render(slide, body, parts, ctx) {
    const v = slide.visual || {};
    const d = el("div", "diagram");
    d.setAttribute("role", "img");
    d.setAttribute("aria-label", v.alt || `Diagram: ${Object.values(v.nodes || {}).join(", ")}`);
    parts.visual = d;
    body.append(d);
    return renderDiagram(d, v, slide, ctx).then((svg) => { parts.svg = svg; });
  },

  layout(body, parts) { sizeDiagram(parts.svg, body); },

  // Flowchart nodes in source order; an edge shows once both its ends do.
  reveal(reveal, parts, veil) {
    if (!("nodes" in reveal) || !parts.svg) return;
    const visible = new Set(reveal.order.slice(0, reveal.nodes));
    diagramNodes(parts.svg).forEach((g, id) => veil(g, !visible.has(id)));
    for (const e of diagramEdges(parts.svg, reveal.order)) {
      if (!e.el.dataset.len) {
        // Edges draw themselves in when revealed: dash the path to its own length.
        const len = Math.ceil(e.el.getTotalLength());
        e.el.dataset.len = len;
        e.el.style.setProperty("--len", len);
        e.el.style.strokeDasharray = len;
        e.el.classList.add("draws");
      }
      const hide = !(visible.has(e.from) && visible.has(e.to));
      veil(e.el, hide);
      veil(e.label, hide);
    }
  },

  // A node by id, or text inside the diagram; undefined hands other cues to the generic pointer.
  target(cue, parts, help) {
    if (cue.kind === "node") {
      const g = diagramNodes(parts.svg).get(cue.id) || findInDiagram(parts.svg, cue.text || cue.id);
      if (!g) return help.wholeVisual();
      g.classList.add("active");
      return { rects: [g.getBoundingClientRect()] };
    }
    if (cue.kind === "text" && cue.text && parts.svg) {
      const g = findInDiagram(parts.svg, cue.text);
      if (g) { g.classList.add("active"); return { rects: [g.getBoundingClientRect()] }; }
    }
    return undefined;
  },

  // The element holding a cue's label, for `{.decode}`: a node's label.
  element(cue, parts) {
    if (!parts.svg) return undefined;
    const g = cue.kind === "node" ? diagramNodes(parts.svg).get(cue.id) || findInDiagram(parts.svg, cue.text || cue.id)
      : cue.kind === "text" ? findInDiagram(parts.svg, cue.text) : null;
    return g?.querySelector(".nodeLabel, .label, text") || g || null;
  },

  frame(st, slide, parts, t) {
    return parts.svg ? travel(st, parts.svg, t, slide.visual?.order) : "";
  },

  // Labels shrunk below a readable size; diagrams are scaled as a whole, not by fit().
  measure(slide, body, parts, report) {
    if (!parts.svg) return false;
    const vb = parts.svg.viewBox.baseVal;
    const k = vb && vb.width ? parseFloat(parts.svg.getAttribute("width")) / vb.width : 1;
    const label = parts.svg.querySelector(".nodeLabel, text");
    const px = (label ? parseFloat(getComputedStyle(label).fontSize) : 20) * k;
    if (px && px < READABLE_PX - 3) {
      report(`Slide ${slide.id}: the diagram had to shrink until its labels are about ${Math.round(px)} px high, too small to read.`
        + " Split it, or use fewer nodes or a top-to-bottom layout.", "text_too_small", slide.id);
    }
    // Beside code or takeaways, a narrow diagram is the point of the layout.
    const beside = ["side", "pair"].some((c) => slide.classes?.includes(c));
    const shape = !beside && shapeMismatch(slide.visual?.source, parts.svg, body);
    if (shape) report(`Slide ${slide.id}: ${shape}`, "diagram_shape", slide.id);
    return true;
  },
};

async function mermaidReady() {
  await loadScript(`${STATIC}/vendor/mermaid.min.js`);
  // Mermaid sizes each box by measuring its label: the face must be loaded first, or labels
  // measured in a fallback font are clipped when drawn in the real one.
  await Promise.all(["400 20px Inter", "700 20px Inter"].map((f) => document.fonts?.load(f).catch(() => null)));
  if (!window.__mermaidInit) {
    window.__mermaidInit = true;
    const css = getComputedStyle(document.body);
    const v = (n) => css.getPropertyValue(n).trim();
    window.mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "base",
      fontFamily: v("--sans"),
      themeVariables: {
        darkMode: v("--scheme") !== "light",
        background: v("--stage"),
        fontSize: "20px",
        primaryColor: v("--node"),
        primaryBorderColor: v("--node-line"),
        primaryTextColor: v("--fg"),
        secondaryColor: v("--panel-2"),
        tertiaryColor: v("--panel"),
        lineColor: v("--muted"),
        textColor: v("--fg"),
        mainBkg: v("--node"),
        nodeBorder: v("--node-line"),
        clusterBkg: v("--panel"),
        clusterBorder: v("--line"),
        edgeLabelBackground: v("--stage"),
        actorBkg: v("--node"),
        actorBorder: v("--node-line"),
        actorTextColor: v("--fg"),
        signalColor: v("--muted"),
        signalTextColor: v("--fg"),
        labelBoxBkgColor: v("--node"),
        labelTextColor: v("--fg"),
        loopTextColor: v("--fg"),
        noteBkgColor: v("--note"),
        noteTextColor: v("--fg"),
        noteBorderColor: v("--accent"),
      },
      flowchart: { htmlLabels: true, curve: "basis", padding: 14, subGraphTitleMargin: { top: 6, bottom: 10 } },
      sequence: { actorMargin: 60, messageFontSize: 18, actorFontSize: 18, noteFontSize: 16 },
    });
  }
  return window.mermaid;
}

let seq = 0;
async function renderDiagram(container, v, slide, ctx) {
  let mermaid;
  try {
    mermaid = await mermaidReady();
  } catch (e) {
    container.innerHTML = `<div class="err">Diagrams are unavailable.</div>`;
    ctx.report(`Diagram on slide ${slide.id}: the diagram library did not load (${e.message || e}).`, "render_error", slide.id);
    return null;
  }
  try {
    const { svg } = await mermaid.render(`mmd-${++seq}`, v.source);
    container.innerHTML = svg;
    const s = container.querySelector("svg");
    s.removeAttribute("style");
    s.setAttribute("aria-hidden", "true");
    return s;
  } catch (e) {
    container.innerHTML = `<div class="err">This diagram could not be drawn.</div>`;
    ctx.report(`Diagram on slide ${slide.id} failed to render: ${String(e.message || e).split("\n")[0]}`, "render_error", slide.id);
    document.querySelectorAll('[id^="dmmd-"]').forEach((n) => n.remove());
    return null;
  }
}

// Scale the diagram to the space it has (in stage pixels, not screen pixels).
export function sizeDiagram(svg, body) {
  if (!svg) return;
  const vb = svg.viewBox.baseVal;
  if (!vb || !vb.width) return;
  const bw = body.clientWidth, bh = body.clientHeight;
  const k = Math.min(bw / vb.width, bh / vb.height, 2.2);
  svg.setAttribute("width", Math.floor(vb.width * k));
  svg.setAttribute("height", Math.floor(vb.height * k));
}

// A flowchart whose direction fights the frame: a top-to-bottom chain that fills the height of a
// wide slide but less than half its width, or the reverse. Other diagrams have no direction to change.
export function shapeMismatch(source, svg, body) {
  const dir = /^\s*(?:flowchart|graph)\s+(TB|TD|BT|LR|RL)\b/m.exec(source || "")?.[1];
  if (!dir || !svg) return "";
  const w = parseFloat(svg.getAttribute("width")), h = parseFloat(svg.getAttribute("height"));
  const bw = body.clientWidth, bh = body.clientHeight;
  if (!w || !h || !bw || !bh) return "";
  const down = dir === "TB" || dir === "TD" || dir === "BT";
  if (down && h >= bh - 2 && w < bw * 0.5) {
    return `the top-to-bottom diagram fills the slide's height but only ${Math.round((100 * w) / bw)}% of its width, so it is drawn small.`
      + " Use `flowchart LR`, or put it beside the code or takeaways with `.pair` or `.side`.";
  }
  if (!down && w >= bw - 2 && h < bh * 0.5) {
    return `the left-to-right diagram fills the slide's width but only ${Math.round((100 * h) / bh)}% of its height, so it is drawn small.`
      + " Use `flowchart TB`, or wrap the chain into rows with subgraphs.";
  }
  return "";
}

// Mermaid flowchart nodes: map node id -> <g> element.
export function diagramNodes(svg) {
  const out = new Map();
  if (!svg) return out;
  for (const g of svg.querySelectorAll("g.node, g.actor, g.stateGroup, g[id^='state-']")) {
    const m = (g.id || "").match(/^(?:flowchart|state)-(.+?)-\d+$/);
    if (m) out.set(m[1], g);
    else if (g.dataset?.id) out.set(g.dataset.id, g);
  }
  // Sequence diagrams: actor boxes carry the participant name.
  // Mermaid draws each participant twice; prefer the box at the top.
  for (const t of [...svg.querySelectorAll("rect.actor-top"), ...svg.querySelectorAll("rect.actor")]) {
    const name = t.getAttribute("name");
    if (name && !out.has(name)) out.set(name, t.parentElement);
  }
  return out;
}

// Edges with their endpoints, for step reveals: [{el, from, to, label}]. Mermaid names edge
// paths `L_<from>_<to>_<n>` and draws edge labels in the same order as the edges.
export function diagramEdges(svg, nodeIds) {
  const edges = [];
  if (!svg) return edges;
  const ids = new Set(nodeIds || diagramNodes(svg).keys());
  const labels = [...svg.querySelectorAll("g.edgeLabels > g.edgeLabel, g.edgeLabel")];
  const paths = [...svg.querySelectorAll("path.flowchart-link")];
  paths.forEach((p, i) => {
    let from = null, to = null;
    const m = (p.id || "").match(/^L_(.+)_\d+$/);
    if (m) {
      const body = m[1];
      for (let k = body.indexOf("_"); k > 0; k = body.indexOf("_", k + 1)) {
        const a = body.slice(0, k), b = body.slice(k + 1);
        if (ids.has(a) && ids.has(b)) { from = a; to = b; break; }
      }
    }
    edges.push({ el: p, from, to, label: labels.length === paths.length ? labels[i] : null });
  });
  return edges;
}

export function findInDiagram(svg, label) {
  if (!svg || !label) return null;
  const want = norm(label);
  const groups = [...svg.querySelectorAll("g.node, g.edgeLabel, g.cluster, g.actor, text.actor, text.messageText, g.stateGroup")];
  const exact = groups.find((g) => norm(g.textContent) === want);
  if (exact) return exact.closest("g.node, g.edgeLabel, g.cluster, g.stateGroup") || exact;
  const part = groups.find((g) => norm(g.textContent).includes(want));
  return part ? part.closest("g.node, g.edgeLabel, g.cluster, g.stateGroup") || part : null;
}

// A dot travelling along an edge-path cue's edges, lighting them as it goes. Returns where it is.
function travel(st, svg, t, order) {
  let dot = svg.querySelector("circle.packet");
  const cue = st.cue?.kind === "edge" ? st.cue : null;
  svg.querySelectorAll("path.lit").forEach((e) => e.classList.remove("lit"));
  if (!cue) { if (dot) dot.style.display = "none"; return ""; }
  const edges = diagramEdges(svg, order);
  const legs = [];
  for (let i = 0; i + 1 < cue.path.length; i++) {
    const e = edges.find((x) => x.from === cue.path[i] && x.to === cue.path[i + 1]);
    if (e) { e.el.classList.add("lit"); legs.push(e.el); }
  }
  if (!legs.length) { if (dot) dot.style.display = "none"; return ""; }
  if (!dot) {
    dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    dot.setAttribute("class", "packet");
    dot.setAttribute("r", "9");
    svg.append(dot);
  }
  // Position along the joined legs: one hop per HOP_S seconds after the cue fires, then rest at the end.
  // With reduced motion the dot waits at the end of the route; the lit edges show the way.
  const f = reducedMotion() ? 1 : Math.max(0, Math.min(1, (t - cue._at) / (HOP_S * legs.length)));
  const lens = legs.map((e) => e.getTotalLength());
  let d = f * lens.reduce((a, b) => a + b, 0), i = 0;
  while (i < legs.length - 1 && d > lens[i]) { d -= lens[i]; i++; }
  const pt = legs[i].getPointAtLength(Math.min(d, lens[i]));
  // Edge paths may sit inside transformed groups: map the point into the SVG's own space.
  const m = legs[i].getCTM(), root = svg.getCTM();
  const q = m && root ? new DOMPoint(pt.x, pt.y).matrixTransform(root.inverse().multiply(m)) : pt;
  dot.setAttribute("cx", q.x);
  dot.setAttribute("cy", q.y);
  dot.style.display = "";
  return `${Math.round(q.x)},${Math.round(q.y)}`;
}
