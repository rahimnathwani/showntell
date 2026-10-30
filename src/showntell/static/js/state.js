// What the player modules share: page flags, DOM handles, the audio elements, mutable playback
// state (one object, since importers cannot reassign a module's `let`), reporting and events.
// Imports nothing from the other player modules, so it is always ready first.

window.__showntellStarted = true;

export const $ = (s) => document.querySelector(s);
export const body = document.body;
export const LIVE = body.dataset.live === "1";
const REPORT_KEY = body.dataset.reportKey || "";
export const params = new URLSearchParams(location.search);
export const PRESENT = params.get("present") === "1"; // no voice; step through with notes
export const AUDIENCE = params.get("audience") === "1"; // follows a presenter window
// Frame capture (MP4 export) holds still between moves, so unchanged frames are reused; ?motion=1 keeps
// the idle motion: a drawing's breathing and a held close-up's creep.
export const IDLE_MOTION = params.get("capture") !== "1" || params.get("motion") === "1";
export const EMBEDDED = window.parent !== window;
export const FRAMES = { landscape: [1280, 720], square: [960, 960], portrait: [720, 1280] };

export const stage = $("#stage"), host = $("#slide-host"), pointer = $("#pointer");
export const audio = new Audio();
audio.preload = "auto";
export const prefetch = new Audio();
prefetch.preload = "auto";

// Read by the evaluation harness and the video recorder.
export const metrics = { stalls: 0, stallSeconds: 0, longestStall: 0, renderErrors: [], ended: false, starts: [], startDelay: null, holds: 0 };
window.__showntell = metrics;

export const store = {
  get(k, d) { try { const v = localStorage.getItem(`showntell.${k}`); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(`showntell.${k}`, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};

export const S = {
  W: null, // walkthrough data
  TL: { segs: [], total: 0 },
  pos: { seg: 0, local: 0 },
  playing: false, started: false, waitingSince: 0, lastFrame: 0, ended: false,
  shown: { slide: -1, rev: -1, id: "" }, // what is on screen
  view: null, // {root, ready, parts, body, done}
  lastCueKey: "", lastReveal: "", lastSay: "", lastWord: -2, lastAnnounced: "", frameKey: "",
  rate: store.get("rate", 1),
  captionsOn: store.get("cc", true),
  pauseAfterSlide: store.get("pauseAfterSlide", false),
  pauseSteps: store.get("pauseSteps", false),
  range: null, // {start, end} in seconds while playing a range of slides
  holdSince: 0, // waiting for a slide to finish drawing before its narration starts
  playPressed: 0,
  seekingAudio: false,
  cam: null, camMoving: false, zoomed: false, camShot: null, // the camera (camera.js): its plan for the slide, and where it is
  failedAudio: new Set(), // audio URLs that failed to load; played silently instead
};

export function scale() { return parseFloat(stage.dataset.scale) || 1; }
export function currentSeg() { return S.TL.segs[S.pos.seg]; }
export function now() { const s = currentSeg(); return s ? s.start + S.pos.local : 0; }
export function sayOf(seg) { return seg && seg.say >= 0 ? S.W.slides[seg.slide].says[seg.say] : null; }
export function reducedMotion() { return matchMedia("(prefers-reduced-motion: reduce)").matches; }

const reported = new Set();
// Send a finding to the server, which passes it to the agent on its next call.
export function report(message, code = "player_error", slide = "") {
  if (code === "render_error" || code === "player_error" || code === "audio_error") metrics.renderErrors.push(message);
  (metrics.findings ||= []).push({ code, slide, message });
  if (!LIVE || reported.has(message)) return;
  reported.add(message);
  const headers = { "Content-Type": "application/json", "X-Report-Key": REPORT_KEY };
  if (body.dataset.csrf) headers["X-CSRF-Token"] = body.dataset.csrf; // hosted mode
  fetch("report", { method: "POST", headers, body: JSON.stringify({ message, code, slide }) }).catch(() => {});
}
window.addEventListener("error", (e) => report(`Player error: ${e.message}`));
for (const m of window.__showntellEarly || []) report(`Before the player started: ${m}`);

// Events for a page that embeds the player in an iframe (see embed.js).
export function emit(type, detail = {}) {
  if (EMBEDDED) window.parent.postMessage({ source: "showntell", type, ...detail }, "*");
}
