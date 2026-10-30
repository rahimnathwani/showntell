// The clock. One clock drives everything: the audio of the current paragraph while it plays,
// wall time during pauses between paragraphs. Also play, pause, seek, stalls while the writer or
// the voice catches up, the wait for a slide to finish drawing, and playing a range of slides.

import { rangeEnd, segmentAt, slideStart } from "./timeline.js";
import { $, audio, AUDIENCE, currentSeg, emit, EMBEDDED, metrics, now, prefetch, PRESENT, report, S, sayOf } from "./state.js";
import { render } from "./view.js";
import { setPlayIcon, showEnd, showOverlay } from "./chrome.js";
import { presenterSync } from "./present.js";

const HOLD_MAX_MS = 1500, HOLD_SHOW_MS = 150;

// The loop runs only while playing, so a paused player (or many on one page) costs nothing.
let looping = false, lastTimeEvent = 0;
function frame(ts) {
  const dt = S.lastFrame ? Math.min(0.25, (ts - S.lastFrame) / 1000) : 0;
  S.lastFrame = ts;
  if (!S.W || !S.playing) { looping = false; return; }
  requestAnimationFrame(frame);
  const segBefore = S.pos.seg;
  advance(dt);
  if (S.pauseSteps && S.pos.seg !== segBefore && revealedIn(segBefore) && S.TL.segs[S.pos.seg]?.slide === S.TL.segs[segBefore].slide) pause();
  render(false);
  if (EMBEDDED && ts - lastTimeEvent > 250) { lastTimeEvent = ts; emit("timeupdate", { currentTime: now(), duration: S.TL.total }); }
}
function kick() {
  if (looping) return;
  looping = true;
  S.lastFrame = 0;
  requestAnimationFrame(frame);
}

// "Pause after each step": on a step-reveal slide, a paragraph whose cues revealed a part.
function revealedIn(i) {
  const seg = S.TL.segs[i];
  const slide = seg && S.W.slides[seg.slide];
  if (!slide || !(slide.classes || []).includes("build") || seg.say < 0) return false;
  return (slide.says[seg.say].cues || []).some((c) => ["item", "row", "lines", "node"].includes(c.kind));
}

function advance(dt) {
  const { W, TL } = S;
  const seg = currentSeg();
  if (!seg) return stall(W.status === "finished" ? "end" : "writing");
  if (!seg.ready) {
    audio.pause();
    return stall("voicing");
  }
  if (S.range && now() >= S.range.end - 0.02) return endRange();
  if (holdForSlide(seg)) return;
  const atEnd = S.pos.seg === TL.segs.length - 1 && S.pos.local >= seg.dur + seg.gap;
  if (atEnd && W.status !== "finished") return stall("writing"); // caught up with the writer
  unstall();
  const say = sayOf(seg);
  const hasAudio = say && say.audio && !seg.pause && !seg.error && !S.failedAudio.has(say.audio);
  if (hasAudio && S.pos.local < seg.dur) {
    const url = say.audio;
    if (!audio.src.endsWith(url)) {
      audio.src = url;
      audio.playbackRate = S.rate;
      S.seekingAudio = true;
      audio.addEventListener("loadedmetadata", () => { audio.currentTime = S.pos.local; S.seekingAudio = false; }, { once: true });
      audio.play().catch(onBlocked);
      prefetchNext();
      return;
    }
    if (S.seekingAudio) return;
    if (audio.paused && !audio.ended) audio.play().catch(onBlocked);
    S.pos.local = audio.ended ? seg.dur : audio.currentTime;
    return;
  }
  if (seg.error && say) report(`Narration on slide ${W.slides[seg.slide].id} could not be voiced: ${say.error || "unknown error"}`, "audio_error", W.slides[seg.slide].id);
  S.pos.local += dt * S.rate;
  if (S.pos.local >= seg.dur + seg.gap) {
    const next = TL.segs[S.pos.seg + 1];
    if (S.pauseAfterSlide && next && next.slide !== seg.slide) {
      S.pos = { seg: S.pos.seg + 1, local: 0 };
      pause();
      return;
    }
    if (!next) {
      S.pos.local = seg.dur + seg.gap;
      if (W.status === "finished") return finish();
      return stall("writing");
    }
    S.pos = { seg: S.pos.seg + 1, local: 0 };
  }
}

// At the start of a slide, wait (briefly) until it has finished drawing, so the first cue
// lands on a laid-out diagram or formula rather than on an empty box.
function holdForSlide(seg) {
  const firstOfSlide = S.pos.local === 0 && S.TL.segs[S.pos.seg - 1]?.slide !== seg.slide;
  const drawing = firstOfSlide && S.shown.slide === seg.slide && S.view && !S.view.done;
  if (!drawing) {
    if (S.holdSince) { S.holdSince = 0; if (!S.waitingSince) $("#waiting").hidden = true; }
    return false;
  }
  const t = performance.now();
  if (!S.holdSince) { S.holdSince = t; metrics.holds += 1; }
  if (t - S.holdSince > HOLD_SHOW_MS && !S.waitingSince) {
    $("#waiting").hidden = false;
    $("#waiting-text").textContent = "Drawing the slide…";
  }
  if (t - S.holdSince < HOLD_MAX_MS) return true;
  const id = S.W.slides[seg.slide].id;
  report(`Slide ${id} took more than ${HOLD_MAX_MS / 1000} s to draw; its narration started before it was ready.`, "slow_render", id);
  S.view.done = true; // stop waiting for this one
  S.holdSince = 0;
  if (!S.waitingSince) $("#waiting").hidden = true;
  return false;
}

function prefetchNext() {
  for (let i = S.pos.seg + 1; i < Math.min(S.TL.segs.length, S.pos.seg + 3); i++) {
    const s = sayOf(S.TL.segs[i]);
    if (s?.audio) { if (!prefetch.src.endsWith(s.audio)) prefetch.src = s.audio; return; }
  }
}

function onBlocked() {
  // Autoplay blocked: wait for a click.
  emit("playbackerror", { reason: "blocked" });
  S.playing = false;
  setPlayIcon();
  showOverlay();
}

let stallWhy = "";
function stall(why) {
  if (why === "end") return finish();
  if (!S.waitingSince) {
    S.waitingSince = performance.now();
    stallWhy = why;
    if (S.started) metrics.stalls += 1;
    emit("waiting", { reason: why });
  }
  $("#waiting").hidden = false;
  $("#waiting-text").textContent = why === "voicing" ? "Voicing the next part…" : "Writing the next slide…";
}

function unstall() {
  if (!S.waitingSince) return;
  const waited = (performance.now() - S.waitingSince) / 1000;
  metrics.stallSeconds += waited;
  if (S.started) metrics.longestStall = Math.max(metrics.longestStall, waited);
  S.waitingSince = 0;
  $("#waiting").hidden = true;
  const seg = currentSeg();
  if (S.started && waited >= 1.5 && seg) {
    const id = S.W.slides[seg.slide].id;
    report(`The viewer caught up and waited ${waited.toFixed(1)} s for slide ${id} to be ${stallWhy === "voicing" ? "voiced" : "written"}.`
      + (stallWhy === "voicing" ? " Send shorter slides early on." : " Send the next slides sooner, or fewer per call."), "viewer_waited", id);
  }
}

function finish() {
  if (S.ended) return;
  S.ended = true;
  metrics.ended = true;
  emit("ended");
  pause();
  unstall();
  showEnd();
}

export function play() {
  const { W, TL } = S;
  if (!W || !TL.segs.length) return;
  if (!TL.segs[0].ready && !S.started) return;
  if (S.ended) { S.ended = false; S.pos = { seg: 0, local: 0 }; $("#end").hidden = true; }
  if (PRESENT || AUDIENCE) return; // stepped by hand, or by the presenter window
  if (S.range && now() >= S.range.end - 0.05) seek(S.range.start);
  if (!S.started) S.playPressed = performance.now();
  S.started = true;
  S.playing = true;
  $("#overlay").hidden = true;
  $("#range-note").hidden = true;
  setPlayIcon();
  render(true);
  kick();
  emit("play");
}

export function pause() {
  const was = S.playing;
  S.playing = false;
  audio.pause();
  setPlayIcon();
  if (was) emit("pause");
}

export function toggle() { S.playing ? pause() : play(); }

// ------------------------------------------------------------------- playing a range of slides

export function setRange(fromId, toId) {
  const { W, TL } = S;
  const idx = (id, d) => { const i = id ? W.slides.findIndex((s) => s.id === id) : -1; return i >= 0 ? i : d; };
  const a = idx(fromId, 0), b = Math.max(a, idx(toId, W.slides.length - 1));
  const start = slideStart(TL, a);
  if (start === null) return;
  S.range = { start, end: rangeEnd(TL, b), from: a, to: b };
  seek(start);
}

function endRange() {
  const end = S.range.end;
  S.range = null;
  pause();
  seek(Math.max(0, end - 0.05));
  $("#range-note").hidden = false;
  emit("ended", { range: true });
}

// ------------------------------------------------------------------- seeking

export function seek(t) {
  const { TL } = S;
  if (!TL.segs.length) return;
  t = Math.max(0, Math.min(t, TL.total - 0.01));
  const i = segmentAt(TL, t);
  S.pos = { seg: i, local: t - TL.segs[i].start };
  S.ended = false;
  $("#end").hidden = true;
  const say = sayOf(TL.segs[i]);
  if (say?.audio && audio.src.endsWith(say.audio) && S.pos.local < TL.segs[i].dur) {
    audio.currentTime = S.pos.local;
  } else {
    audio.pause();
    audio.removeAttribute("src");
  }
  render(true);
  if (PRESENT) presenterSync();
}

export function seekSlide(delta) {
  const s = currentSeg();
  if (!s) return;
  let target = s.slide + delta;
  if (delta < 0 && S.pos.local > 1.5 && S.TL.segs.findIndex((x) => x.slide === s.slide) === S.pos.seg) target = s.slide;
  target = Math.max(0, Math.min(S.W.slides.length - 1, target));
  const t = slideStart(S.TL, target);
  if (t !== null) seek(t);
}

// ------------------------------------------------------------------- audio events

audio.addEventListener("playing", () => {
  // Wall-clock start of each paragraph's audio, so recordings can line sound up with pictures.
  const seg = currentSeg();
  if (seg) metrics.starts.push({ key: seg.key, at: performance.timeOrigin + performance.now(), local: audio.currentTime });
  if (S.playPressed && metrics.startDelay === null) {
    metrics.startDelay = (performance.now() - S.playPressed) / 1000;
    if (metrics.startDelay > 2) report(`The first narration took ${metrics.startDelay.toFixed(1)} s to start after play was pressed.`, "viewer_waited");
  }
});
audio.addEventListener("error", () => {
  // A missing or broken file: carry on silently with captions rather than stopping.
  if (!audio.src) return;
  const name = audio.src.split("/").pop();
  const say = sayOf(currentSeg());
  if (say?.audio) S.failedAudio.add(say.audio);
  S.seekingAudio = false;
  audio.removeAttribute("src");
  report(`Audio failed to load: ${name}`, "audio_error", S.W.slides[currentSeg()?.slide]?.id || "");
});
