// Pipe tables; cues point at rows, columns (`col`, k) and single cells (`cell`, row id and k).

import { el } from "../draw.js";
import { inline } from "../util.js";

// The smallest box around several, for a column drawn as one target.
function around(rects) {
  const left = Math.min(...rects.map((r) => r.left)), top = Math.min(...rects.map((r) => r.top));
  const right = Math.max(...rects.map((r) => r.right)), bottom = Math.max(...rects.map((r) => r.bottom));
  return { left, top, right, bottom, width: right - left, height: bottom - top };
}

function mark(els, table) {
  els.forEach((e) => e.classList.add("active"));
  table.classList.add("has-active");
}

export default {
  render(slide, body, parts) {
    const v = slide.visual || {};
    const t = el("table", "data");
    const align = v.align || [];
    t.innerHTML = `<thead><tr>${v.head.map((h, i) => `<th style="text-align:${align[i] || "left"}">${inline(h)}</th>`).join("")}</tr></thead>`;
    parts.cols = [...t.querySelectorAll("th")].map((th) => [th]); // column k: its heading, then its cells
    parts.cells = new Map(); // "rowid/k" -> td
    const tb = el("tbody");
    v.rows.forEach((r, i) => {
      const tr = el("tr", "entry", r.map((c, k) => `<td style="text-align:${align[k] || "left"}">${inline(c)}</td>`).join(""));
      const id = (v.ids || [])[i] || `row-${i + 1}`;
      tr.dataset.id = id;
      tr.dataset.idx = i;
      parts.rows.set(id, tr);
      [...tr.children].forEach((td, k) => {
        parts.cells.set(`${id}/${k}`, td);
        parts.cols[k]?.push(td);
      });
      tb.append(tr);
    });
    t.append(tb);
    parts.visual = t;
    body.append(t);
  },

  target(cue, parts) {
    if (cue.kind === "cell") {
      const td = parts.cells?.get(`${cue.id}/${cue.col}`);
      if (!td) return null;
      mark([td], parts.visual);
      return { rects: [td.getBoundingClientRect()] };
    }
    if (cue.kind === "col") {
      // Only the rows on screen: on a `.build` slide the rest are still veiled.
      const els = (parts.cols?.[cue.col] || []).filter((e) => !e.closest("tr.veiled"));
      if (!els.length) return null;
      mark(els, parts.visual);
      return { rects: [around(els.map((e) => e.getBoundingClientRect()))] };
    }
    return undefined;
  },

  // The label `{.decode}` scrambles: a cell's own text.
  element(cue, parts) {
    if (cue.kind === "cell") return parts.cells?.get(`${cue.id}/${cue.col}`) || null;
    return undefined;
  },
};
