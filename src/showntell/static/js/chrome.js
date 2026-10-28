// Everything around the stage: captions, the transcript, chapters, the progress bar and clock,
// the play button, the start overlay and the end card.

import { mmss, slideSpans, slideStart, wordAt } from "./timeline.js";
import { $, body, now, reducedMotion, S, sayOf, store } from "./state.js";
import { seek } from "./playback.js";
import { captionPages, esc } from "./util.js";

// ------------------------------------------------------------------- captions and transcript

// The caption shows one line at a time: the paragraph is cut into pages of five to seven words,
// and a page is replaced whole when speech reaches its first word. Words already spoken are
// light, the word being spoken brightest, the rest of the line dim.
export function applyCaption(st, slide, force) {
  const seg = S.TL.segs[st.seg];
  const say = sayOf(seg);
  const key = say ? say.id : "";
  const cap = $("#caption");
  if (force || key !== S.lastSay) {
    S.lastSay = key;
    S.lastWord = -2;
    cap.innerHTML = say && say.text ? `<div class="inner">${captionHtml(say)}</div>` : "";
    markTranscript(key);
  }
  if (!say) return;
  // Before the first word, the first line waits, dim; after the last, the last line stays.
  const n = cap.querySelectorAll(".w").length;
  const i = st.inAudio ? wordAt(say, st.local) : st.local > 0 ? n : -1;
  if (i === S.lastWord) return;
  S.lastWord = i;
  const words = [...cap.querySelectorAll(".w")];
  const page = words[Math.max(0, Math.min(i, words.length - 1))]?.closest(".page");
  cap.querySelectorAll(".page.on").forEach((p) => { if (p !== page) p.classList.remove("on"); });
  page?.classList.add("on");
  for (const w of page ? page.querySelectorAll(".w") : []) {
    const k = +w.dataset.i;
    w.classList.toggle("said", k < i);
    w.classList.toggle("now", k === i);
  }
}

function captionHtml(say) {
  const text = say.text;
  const code = say.code || [];
  const isCode = (a, b) => code.some(([x, y]) => a >= x && b <= y);
  const words = (say.words || []).map((w, i) => ({ ...w, i })).filter((w, k, all) => w.c0 >= 0 && w.c1 <= text.length && (k === 0 || w.c0 >= all[k - 1].c1));
  if (!words.length) return `<span class="page on">${esc(text)}</span>`;
  return captionPages(text, words).map((page) => {
    let html = "";
    page.forEach((w, k) => {
      html += `<span class="w${isCode(w.c0, w.c1) ? " code" : ""}" data-i="${w.i}">${esc(text.slice(w.c0, w.c1))}</span>`;
      const next = page[k + 1];
      // Text between words, and a page's closing punctuation; a page's leading text is dropped.
      html += esc(next ? text.slice(w.c1, next.c0) : text.slice(w.c1, words[words.indexOf(w) + 1]?.c0 ?? text.length).trimEnd());
    });
    return `<span class="page">${html}</span>`;
  }).join("");
}

function renderTranscript() {
  const box = $("#transcript-body");
  const parts = [];
  S.W.slides.forEach((s, i) => {
    const paras = s.says.filter((x) => x.text).map((x) => `<p data-say="${esc(x.id)}" tabindex="-1">${esc(x.text)}</p>`).join("");
    parts.push(`<section data-slide="${i}"><h3><button data-slide="${i}">${i + 1}. ${esc(stripMd(s.title))}</button></h3>${paras}</section>`);
  });
  const notes = Object.entries(S.W.notes || {});
  if (notes.length) parts.push(`<section class="notes"><h3>Sources</h3>${notes.map(([k, v]) => `<p><sup>${esc(k)}</sup> ${linkify(v)}</p>`).join("")}</section>`);
  box.innerHTML = parts.join("");
  markTranscript(S.lastSay);
  txNav.refresh();
}

// One tab stop per list: the current entry is focusable, the arrow keys move between entries.
function roving(box, selector) {
  const items = () => [...box.querySelectorAll(selector)];
  const current = () => box.querySelector(`${selector}[tabindex="0"]`) || items()[0];
  box.addEventListener("keydown", (e) => {
    const list = items();
    const i = list.indexOf(e.target);
    if (i < 0) return;
    const j = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: list.length - 1 }[e.key];
    if (j === undefined) return;
    e.preventDefault();
    e.stopPropagation();
    const next = list[Math.max(0, Math.min(list.length - 1, j))];
    list.forEach((el) => el.setAttribute("tabindex", el === next ? "0" : "-1"));
    next.focus();
  });
  return { refresh(active) {
    const list = items();
    const keep = active || current();
    list.forEach((el) => el.setAttribute("tabindex", el === keep ? "0" : "-1"));
  } };
}
const txNav = roving($("#transcript-body"), "p[data-say], h3 button");
const chapterNav = $("#chapters") ? roving($("#chapters"), "button") : null;

function linkify(s) {
  return esc(s).replace(/https?:\/\/[^\s<]+/g, (u) => `<a href="${u}" target="_blank" rel="noopener">${u}</a>`);
}

export function stripMd(s) { return String(s || "").replace(/[`*_]/g, ""); }

function markTranscript(sayId) {
  const box = $("#transcript-body");
  box.querySelectorAll("p.now").forEach((p) => p.classList.remove("now"));
  if (!sayId) return;
  const p = box.querySelector(`p[data-say="${CSS.escape(sayId)}"]`);
  if (p) {
    p.classList.add("now");
    if (!$("#transcript-body").contains(document.activeElement)) txNav.refresh(p);
    if (!$("#transcript").hidden) p.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
  }
}

$("#transcript-body").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-slide]");
  if (b) { const t = slideStart(S.TL, +b.dataset.slide); if (t !== null) seek(t); return; }
  const p = e.target.closest("p[data-say]");
  if (p) { const s = S.TL.segs.find((x) => x.key === p.dataset.say); if (s) seek(s.start); }
});
$("#transcript-body").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.matches("p[data-say]")) e.target.click();
});

export function setCaptions(on) {
  S.captionsOn = on;
  $("#caption").classList.toggle("off", !on);
  $("#cc").setAttribute("aria-pressed", on);
  store.set("cc", on);
}

export function setTranscript(on) {
  $("#transcript").hidden = !on;
  $("#tx").setAttribute("aria-pressed", on);
  body.classList.toggle("with-transcript", on);
  store.set("transcript", on);
  if (on) markTranscript(S.lastSay);
}

// ------------------------------------------------------------------- progress, time, overlay, end

export function renderChrome() {
  const { W, TL } = S;
  const bar = $("#progress");
  const spans = slideSpans(TL, W.slides.length);
  bar.replaceChildren(...W.slides.map((s, i) => {
    const sp = spans[i] || { start: 0, end: 0, ready: false };
    const seg = document.createElement("div");
    seg.className = `seg${sp.ready ? "" : " pending"}`;
    seg.style.flex = `${Math.max(0.4, sp.end - sp.start)} 1 0`;
    seg.title = `${i + 1}. ${stripMd(s.title)}`;
    seg.dataset.slide = i;
    seg.innerHTML = '<div class="fill"></div>';
    return seg;
  }));
  bar.setAttribute("aria-valuemax", Math.round(TL.total));
  const writing = W.status !== "finished";
  const voicing = TL.segs.filter((s) => !s.ready).length;
  $("#status").textContent = writing ? `Writing · ${W.slides.length} slides` : voicing ? `Voicing ${voicing}…` : "";
  renderTranscript();
  renderChapters();
}

function renderChapters() {
  const box = $("#chapters");
  if (!box) return;
  box.innerHTML = S.W.slides.map((s, i) => `<li><button data-slide="${i}" tabindex="-1">${esc(stripMd(s.title))}</button></li>`).join("");
  chapterNav?.refresh(box.querySelector(`button[data-slide="${Math.max(0, S.shown.slide)}"]`));
}

export function markChapter(i) {
  $("#chapters")?.querySelectorAll("button").forEach((b) => b.classList.toggle("now", +b.dataset.slide === i));
  if (!$("#chapters")?.contains(document.activeElement)) chapterNav?.refresh($("#chapters")?.querySelector(`button[data-slide="${i}"]`));
}

export function updateTime(st) {
  const { W, TL } = S;
  const t = now();
  const more = W.status !== "finished" ? "+" : "";
  $("#time").textContent = `${mmss(t)} / ${mmss(TL.total)}${more}`;
  const spans = slideSpans(TL, W.slides.length);
  const segs = $("#progress").children;
  for (let k = 0; k < segs.length; k++) {
    const sp = spans[k];
    const f = sp ? Math.max(0, Math.min(1, (t - sp.start) / Math.max(0.01, sp.end - sp.start))) : 0;
    segs[k].firstChild.style.width = `${f * 100}%`;
  }
  const bar = $("#progress");
  bar.setAttribute("aria-valuenow", Math.round(t));
  bar.setAttribute("aria-valuetext", `${mmss(t)} of ${mmss(TL.total)}, slide ${st.slide + 1} of ${W.slides.length}`);
}

function progressSeek(e) {
  const bar = $("#progress");
  const seg = e.target.closest(".seg");
  if (!seg) return;
  const r = seg.getBoundingClientRect();
  const sp = slideSpans(S.TL, S.W.slides.length)[+seg.dataset.slide];
  if (!sp) return;
  const f = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
  seek(sp.start + f * (sp.end - sp.start));
  bar.focus({ preventScroll: true });
}
$("#progress").addEventListener("pointerdown", (e) => { progressSeek(e); $("#progress").setPointerCapture(e.pointerId); });
$("#progress").addEventListener("pointermove", (e) => { if (e.buttons) progressSeek(e); });

export function setPlayIcon() {
  $("#play-icon").setAttribute("d", S.playing ? "M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" : "M8 5v14l11-7z");
  $("#play").setAttribute("aria-label", S.playing ? "Pause" : "Play");
}

export function showOverlay() {
  const { W, TL } = S;
  const o = $("#overlay");
  o.hidden = false;
  const atTitle = (TL.segs[S.pos.seg]?.slide ?? 0) === 0;
  const titleSlide = W.slides[0]?.kind === "title";
  o.classList.toggle("over-title", titleSlide && atTitle);
  $("#overlay-title").textContent = titleSlide ? "" : stripMd(W.title);
  const ready = TL.segs[0]?.ready;
  $("#big-play").disabled = !ready;
  o.classList.toggle("loading", !ready);
  const sub = titleSlide ? "" : W.slides[0]?.shown || "";
  $("#overlay-sub").textContent = stripMd(sub);
  $("#overlay-meta").textContent = !ready
    ? "Preparing the opening…"
    : W.status === "finished"
      ? `${W.slides.length} slides · ${mmss(TL.total)}`
      : "Still being written. Press play; it keeps up.";
}

export function showEnd() {
  const box = $("#questions");
  box.innerHTML = "";
  for (const q of S.W.questions || []) {
    const b = document.createElement("button");
    b.className = "q";
    b.textContent = q;
    b.onclick = () => navigator.clipboard?.writeText(q).then(() => { b.classList.add("copied"); b.dataset.label = "Copied"; setTimeout(() => b.classList.remove("copied"), 1500); }).catch(() => {});
    box.append(b);
  }
  $("#end-questions").hidden = !(S.W.questions || []).length;
  $("#end").hidden = false;
  $("#replay").focus({ preventScroll: true });
}

export function announce(text) { $("#announce").textContent = text; }
