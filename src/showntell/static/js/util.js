// Small pure helpers shared by the renderer and tests.

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// Inline Markdown for on-screen text: code, bold, italic, links shown as text. Escapes first.
export function inline(md) {
  const parts = String(md ?? "").split(/(`+[^`]*?`+)/g);
  return parts
    .map((p) => {
      const m = p.match(/^(`+)([^`]*?)\1$/);
      if (m) return `<code class="inline">${esc(m[2])}</code>`;
      return esc(p)
        .replace(/\[([^\]]+)\]\((?:[^)]*)\)/g, "$1")
        .replace(/\[([^\]]+)\]\{[^}]*\}/g, "$1")
        .replace(/\*\*(.+?)\*\*|__(.+?)__/g, (_, a, b) => `<strong>${a ?? b}</strong>`)
        .replace(/(^|[^\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)/g, "$1<em>$2</em>")
        .replace(/(^|[^\w])_(?!\s)(.+?)(?<!\s)_(?!\w)/g, "$1<em>$2</em>");
    })
    .join("");
}

// Split highlighted HTML into lines, closing and reopening spans that cross line breaks.
export function splitLines(html) {
  const lines = [];
  const stack = [];
  let cur = "";
  const re = /(<span[^>]*>)|(<\/span>)|(\n)|([^<\n]+|<)/g;
  let m;
  while ((m = re.exec(html))) {
    if (m[1]) { stack.push(m[1]); cur += m[1]; }
    else if (m[2]) { stack.pop(); cur += m[2]; }
    else if (m[3]) { cur += "</span>".repeat(stack.length); lines.push(cur); cur = stack.join(""); }
    else cur += m[4];
  }
  lines.push(cur);
  return lines;
}

export function norm(s) {
  return String(s ?? "").replace(/\s+/g, " ").trim().toLowerCase();
}

// Captions show one line at a time (chrome.js).
const PAGE_MIN = 5, PAGE_MAX = 7, PAGE_CHARS = 46;

// Pages of words: break after a sentence or clause once a page has five words, always by seven
// (or about 46 characters), and never leave one or two words alone on the last page.
export function captionPages(text, words) {
  const pages = [];
  let cur = [];
  const chars = (ws) => (ws.length ? ws[ws.length - 1].c1 - ws[0].c0 : 0);
  words.forEach((w, i) => {
    if (cur.length && (cur.length >= PAGE_MAX || chars([...cur, w]) > PAGE_CHARS)) { pages.push(cur); cur = []; }
    cur.push(w);
    const left = words.length - i - 1;
    const pause = /[.,;:!?…—–)]["'”’]?\s*$/.test(text.slice(w.c0, words[i + 1]?.c0 ?? text.length));
    if (left > 2 && cur.length >= PAGE_MIN && pause) { pages.push(cur); cur = []; }
  });
  if (cur.length) {
    const last = pages[pages.length - 1];
    if (last && cur.length <= 2 && last.length + cur.length <= PAGE_MAX + 2) last.push(...cur);
    else pages.push(cur);
  }
  return pages;
}
