// The showntell player. One clock drives everything (playback.js); each frame computes the state
// for the current time (timeline.js) and brings the screen in line with it (view.js). This module
// loads the walkthrough, follows live updates, and wires up the controls.
//
//   state.js     shared flags, DOM handles, mutable state, reporting and embed events
//   playback.js  the clock: play, pause, seek, stalls, ranges
//   view.js      the slide on screen: reveals, code versions, per-frame animation
//   pointer.js   where cues point, pointer marks, scrolling and zooming
//   chrome.js    captions, transcript, progress bar, overlay and end card
//   present.js   presenter and audience windows, commands from an embedding page
//   render.js    drawing a slide, through the slide kinds in kinds/

import { build, mmss, rangeEnd, revealedAt, slideStart } from "./timeline.js";
import { $, AUDIENCE, audio, body, currentSeg, emit, LIVE, now, params, PRESENT, report, S, sayOf, store } from "./state.js";
import { play, seek, seekSlide, setRange, toggle } from "./playback.js";
import { layoutStage, render } from "./view.js";
import { renderChrome, setCaptions, setTranscript, showOverlay, stripMd } from "./chrome.js";
import { followPresenter, presenterSync, startPresenting, stepBy } from "./present.js";

new ResizeObserver(() => { layoutStage(); render(true); }).observe($("#viewport"));

// ------------------------------------------------------------------- data

function setData(w) {
  S.failedAudio.clear();
  const { TL, pos } = S;
  const keepKey = TL.segs[pos.seg]?.key;
  const keepSlideId = S.W && TL.segs[pos.seg] ? S.W.slides[TL.segs[pos.seg].slide]?.id : null;
  const first = !S.W;
  const W = S.W = w;
  S.TL = build(W);
  if (first) {
    body.classList.add(W.frame);
    document.title = W.title;
    layoutStage();
  }
  // Keep the playback position on the same paragraph, or the same slide if it was revised.
  let i = keepKey ? S.TL.segs.findIndex((s) => s.key === keepKey) : 0;
  if (i < 0 && keepSlideId) {
    i = S.TL.segs.findIndex((s) => W.slides[s.slide]?.id === keepSlideId);
    S.pos.local = 0;
  }
  S.pos.seg = Math.max(0, i);
  const cur = W.slides[S.TL.segs[S.pos.seg]?.slide];
  if (cur && cur.id === S.shown.id && cur.rev !== S.shown.rev) S.shown.slide = -1; // revised: redraw
  renderChrome();
  if (!S.started) showOverlay();
  render(true);
  if (S.range) S.range.end = rangeEnd(S.TL, S.range.to);
  if (PRESENT) presenterSync();
  emit("durationchange", { duration: S.TL.total, slides: W.slides.length, finished: W.status === "finished" });
}

function mergeDelta(d) {
  const byId = new Map(S.W.slides.map((s) => [s.id, s]));
  for (const s of d.slides) byId.set(s.id, s);
  setData({ ...S.W, status: d.status, title: d.title, questions: d.questions, notes: d.notes, slides: d.order.map((id) => byId.get(id)).filter(Boolean) });
}

async function load() {
  // A single-file export carries its walkthrough in the page; everything else reads data.json.
  setData(window.__showntellData || await (await fetch("data.json", { cache: "no-store" })).json());
  if (LIVE) connect();
  if (params.get("from") || params.get("to")) setRange(params.get("from"), params.get("to"));
  // `at` is where to start, in seconds. (`t` is the access token on links served beyond this machine.)
  const at = parseFloat(params.get("at"));
  if (at > 0) seek(at);
  if (PRESENT) startPresenting();
  if (AUDIENCE) followPresenter();
  emit("ready", { duration: S.TL.total, slides: S.W.slides.length, title: S.W.title, frame: S.W.frame });
  if (params.get("autoplay") === "1") play();
}

function connect() {
  const es = new EventSource("events");
  es.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "full") setData(msg.walkthrough);
    else if (msg.type === "delta") mergeDelta(msg);
    $("#status").classList.remove("offline");
  };
  es.onerror = () => { $("#status").textContent = "Reconnecting…"; $("#status").classList.add("offline"); };
}

// ------------------------------------------------------------------- controls

$("#range-all").onclick = () => { $("#range-note").hidden = true; play(); };
$("#big-play").onclick = play;
$("#play").onclick = toggle;
$("#prev").onclick = () => seekSlide(-1);
$("#next").onclick = () => seekSlide(1);
$("#replay").onclick = () => { $("#end").hidden = true; S.ended = false; seek(0); play(); };
$("#end-transcript").onclick = () => setTranscript(true);
$("#speed").value = String(S.rate);
$("#speed").onchange = (e) => { S.rate = parseFloat(e.target.value) || 1; audio.playbackRate = S.rate; store.set("rate", S.rate); };
$("#pause-after").checked = S.pauseAfterSlide;
$("#pause-after").onchange = (e) => { S.pauseAfterSlide = e.target.checked; store.set("pauseAfterSlide", S.pauseAfterSlide); };
$("#theme").value = store.get("theme", "") || "";
$("#theme").onchange = (e) => {
  // Diagrams and charts take their colours when drawn, so reload, at the same moment.
  store.set("theme", e.target.value || null);
  const u = new URL(location.href);
  u.searchParams.set("at", now().toFixed(2));
  location.replace(u.toString());
};
$("#pause-steps").checked = S.pauseSteps;
$("#pause-steps").onchange = (e) => { S.pauseSteps = e.target.checked; store.set("pauseSteps", S.pauseSteps); };
$("#present").onclick = () => { const u = new URL(location.href); u.searchParams.set("present", "1"); location.href = u.toString(); };
$("#ask").onclick = () => {
  // A ready-made request for the agent: which slide, where, and what it currently says.
  const seg = currentSeg();
  if (!S.W || !seg) return;
  const slide = S.W.slides[seg.slide];
  const said = (sayOf(seg)?.text || "").slice(0, 160);
  const text = `In the showntell walkthrough "${stripMd(S.W.title)}" (id ${S.W.id}), revise slide "${slide.id}" (${stripMd(slide.title)}), `
    + `at ${mmss(now())}${said ? `, where the narration says: "${said}${said.length >= 160 ? "…" : ""}"` : ""}. Change: `;
  navigator.clipboard?.writeText(text).then(() => {
    $("#ask").classList.add("done");
    $("#ask").textContent = "Copied. Paste it to your agent";
    setTimeout(() => { $("#ask").classList.remove("done"); $("#ask").textContent = "Copy a change request for this slide"; }, 2500);
  }).catch(() => {});
};
$("#chapters")?.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-slide]");
  if (b) { const t = slideStart(S.TL, +b.dataset.slide); if (t !== null) seek(t); $("#menu").hidden = true; }
});

$("#cc").onclick = () => setCaptions(!S.captionsOn);
setCaptions(S.captionsOn);
$("#tx").onclick = () => setTranscript($("#transcript").hidden);
$("#transcript-close").onclick = () => setTranscript(false);
setTranscript(store.get("transcript", false) && innerWidth > 900);

$("#more").onclick = () => { const m = $("#menu"); m.hidden = !m.hidden; $("#more").setAttribute("aria-expanded", !m.hidden); };
document.addEventListener("click", (e) => { if (!e.target.closest("#menu, #more")) { $("#menu").hidden = true; $("#more").setAttribute("aria-expanded", "false"); } });

document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = e.target.tagName;
  if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
  const onButton = tag === "BUTTON" && (e.key === " " || e.key === "Enter");
  if (onButton) return;
  const k = e.key;
  if (PRESENT && [" ", "ArrowRight", "PageDown", "n", "ArrowDown"].includes(k)) { e.preventDefault(); return stepBy(1); }
  if (PRESENT && ["ArrowLeft", "PageUp", "p", "ArrowUp", "Backspace"].includes(k)) { e.preventDefault(); return stepBy(-1); }
  if (k === " " || k === "k") { e.preventDefault(); toggle(); }
  else if (k === "ArrowRight" && e.shiftKey) seekSlide(1);
  else if (k === "ArrowLeft" && e.shiftKey) seekSlide(-1);
  else if (k === "ArrowRight" || k === "l") { e.preventDefault(); seek(now() + 5); }
  else if (k === "ArrowLeft" || k === "j") { e.preventDefault(); seek(now() - 5); }
  else if (k === "PageDown" || k === ".") seekSlide(1);
  else if (k === "PageUp" || k === ",") seekSlide(-1);
  else if (k === "Home") seek(0);
  else if (k === "c") setCaptions(!S.captionsOn);
  else if (k === "t") setTranscript($("#transcript").hidden);
  else if (k === "?") { $("#menu").hidden = false; }

  else if (k === "Escape") { $("#menu").hidden = true; }
});

load().catch((e) => {
  $("#overlay-meta").textContent = "This walkthrough could not be loaded.";
  report(`Load failed: ${e.message}`);
  emit("error", { message: e.message });
});

// ------------------------------------------------------------------- capture mode (?capture=1)
// Frame-by-frame capture (capture.py: MP4 export, posters, thumbnails) drives the player by
// seeking, never by playing: no audio, no wall-clock animation (player.css turns transitions off
// under body.capture), only the stage on screen. ?captions=1 burns the caption into the frame.
// seek(t) resolves once the frame for t is painted, with a key that changes whenever the picture
// can change, so the caller can reuse the previous frame while the key repeats. With
// { measure: true } it also waits for the slide's layout checks (preview, finish-time check).
if (params.get("capture") === "1") {
  body.classList.add("capture");
  const burn = params.get("captions") === "1";
  if (burn) body.classList.add("burn-captions");
  const painted = () => new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
  const stateKey = () => [S.shown.id, S.shown.rev, S.view?.done, S.lastReveal, S.lastCueKey, S.frameKey,
    burn ? `${S.lastSay}:${S.lastWord}` : ""].join("|");
  let paintedKey = "";
  window.__showntell = Object.assign(window.__showntell || {}, {
    duration: () => (S.W && S.TL.segs.length ? S.TL.total : 0),
    frame: () => (S.W ? S.W.frame : ""),
    async seek(t, { measure = false } = {}) {
      seek(t);
      // Nothing changed since the last painted frame: skip the wait (most frames of a video).
      if (S.view?.done && stateKey() === paintedKey && (!measure || S.view.measured)) return paintedKey;
      // Mermaid, KaTeX, images and fonts settle in view.ready; showSlide sets view.done after them.
      for (let i = 0; i < 600 && !S.view?.done; i++) await painted();
      await document.fonts?.ready;
      await painted();
      for (let i = 0; measure && i < 60 && !S.view?.measured; i++) await painted();
      return (paintedKey = stateKey());
    },
    // A slide alone and fully revealed, timed on this page's own timeline: audio voiced since
    // the caller looked moves slides, so a time worked out there can land in a crossfade.
    // [key, t], or null for an unknown slide.
    async seekSlide(id, opts = {}) {
      const si = S.W ? S.W.slides.findIndex((s) => s.id === id) : -1;
      const t = si < 0 ? null : revealedAt(S.TL, si);
      return t === null ? null : [await this.seek(t, opts), t];
    },
  });
}
