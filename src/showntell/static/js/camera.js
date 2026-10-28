// The camera: zooming the slide body onto a cue's target and back, as a pure function of the
// narration clock, so playback, seeking and frame capture (MP4 export) show the same move.
//
// The plan (timeline.js cameraPlan) says when each move starts and how long it takes; here each
// shot is measured once and the picture for a moment is worked out. Moves in ease in and out,
// with half the change in the middle quarter; a move between two close-ups at a similar zoom
// pulls back a little on the way, an arc rather than a flat slide; moves out are quicker and
// front-loaded. While a close-up holds, it creeps in about 2% towards the picture's centre; a
// whole drawing breathes about 1%. Reduced motion cuts instead and holds still.

import { cameraPlan, CUE_LEAD, slideEnd, slideStart } from "./timeline.js";
import { kindOf } from "./render.js";
import { host, now, reducedMotion, S, scale } from "./state.js";
import { cueTarget } from "./pointer.js";

const ARC = 0.065; // how far a move between close-ups pulls back at its midpoint
const CREEP = 0.02, DRIFT = 0.03, CREEP_AFTER = 0.5; // a held close-up: zoom gained, share of the way to the centre
const BREATH = 0.01, BREATH_S = 9; // a whole drawing: zoom amplitude, seconds per breath
const MAX_ZOOM = 2.6;

// Half the change in the middle quarter of the move: an ease in and out with exponent 2.4.
const inOut = (p) => (p < 0.5 ? 0.5 * (2 * p) ** 2.4 : 1 - 0.5 * (2 - 2 * p) ** 2.4);
const outCubic = (p) => 1 - (1 - p) ** 3;
const smooth = (p) => 0.5 - 0.5 * Math.cos(Math.PI * p);
const clamp = (x) => Math.max(0, Math.min(1, x));

// Still pictures breathe; code, lists and charts hold still unless a cue moves the camera.
function breathes(slide) {
  if (slide.kind === "svg" || slide.kind === "drawio") return !(slide.visual?.steps?.length > 1);
  return slide.kind === "image" && slide.says.some((x) => (x.cues || []).some((c) => c.kind === "region"));
}

// Set the camera for this moment. Returns {key, moving, zoomed, changed}: `key` changes whenever
// the picture does (for frame capture), `moving` is true during a move (the pointer waits).
export function applyCamera(st, slide) {
  const view = S.view, bodyEl = view?.body;
  const still = { key: "", moving: false, zoomed: false, changed: false };
  if (!bodyEl || !view.done) return still;
  if (S.enter && S.enter.root === view.root && !S.enter.done) return still; // measure once it has landed
  let cam = S.cam;
  if (!cam || cam.root !== view.root) {
    const plan = cameraPlan(S.W, S.TL, st.slide);
    cam = S.cam = { root: view.root, plan, breathe: breathes(slide) && !kindOf(slide).bare, shots: new Map(), transform: "" };
    if (!plan.length && !cam.breathe) return still;
    measure(cam, slide, bodyEl);
  }
  if (!cam.plan.length && !cam.breathe) return still;
  const t = now();
  const v = reducedMotion() ? cutAt(cam, t) : at(cam, st.slide, t);
  const transform = Math.abs(v.z - 1) < 1e-4 && Math.abs(v.cx - cam.whole.cx) < 0.01 && Math.abs(v.cy - cam.whole.cy) < 0.01 ? ""
    : `translate(${(cam.whole.cx - v.cx * v.z).toFixed(2)}px, ${(cam.whole.cy - v.cy * v.z).toFixed(2)}px) scale(${v.z.toFixed(5)})`;
  const changed = transform !== cam.transform;
  if (changed) {
    bodyEl.style.transform = transform;
    cam.transform = transform;
  }
  return { key: transform, moving: v.moving, zoomed: v.z > 1.05, changed, shot: v.shot ? cam.shots.get(v.shot) : null };
}

// Measure every close-up on the slide with the camera at rest, in stage pixels relative to the
// slide body. Finding a target marks it (active classes, highlights): undo that afterwards, and
// have the current cue marked again.
function measure(cam, slide, bodyEl) {
  const was = bodyEl.style.transform;
  bodyEl.style.transform = "";
  const k = scale(), b = bodyEl.getBoundingClientRect();
  const bw = b.width / k, bh = b.height / k;
  cam.whole = { cx: bw / 2, cy: bh / 2, z: 1 };
  for (const m of cam.plan) {
    if (!m.shot || cam.shots.has(m.shot)) continue;
    const target = cueTarget(m.cue, slide);
    const rs = (target?.rects || []).filter((r) => r && (r.width || r.height));
    if (!rs.length) { cam.shots.set(m.shot, null); continue; }
    const l = (Math.min(...rs.map((r) => r.left)) - b.left) / k, t = (Math.min(...rs.map((r) => r.top)) - b.top) / k;
    const w = (Math.max(...rs.map((r) => r.right)) - b.left) / k - l, h = (Math.max(...rs.map((r) => r.bottom)) - b.top) / k - t;
    // Small targets fill about 60% of the frame; large ones, such as one panel of a grid, nearly all of it.
    const size = Math.max(w / bw, h / bh);
    const fill = Math.min(0.9, 0.6 + 0.6 * size);
    const z = Math.max(1, Math.min(MAX_ZOOM, (fill * bw) / Math.max(w, 1), (fill * bh) / Math.max(h, 1)));
    cam.shots.set(m.shot, { cx: l + w / 2, cy: t + h / 2, z, large: size >= 0.3 });
  }
  bodyEl.style.transform = was;
  host.querySelectorAll(".active").forEach((e) => e.classList.remove("active"));
  host.querySelectorAll(".has-active").forEach((e) => e.classList.remove("has-active"));
  host.querySelectorAll(".ln.hl").forEach((e) => e.classList.remove("hl"));
  if (window.CSS?.highlights) CSS.highlights.delete("ptr");
  S.lastCueKey = "";
}

const base = (cam, shot) => (shot && cam.shots.get(shot)) || cam.whole;

// The camera at time t: walk the plan, holding between moves.
function at(cam, si, t) {
  let shot = null, land = slideStart(S.TL, si) ?? 0;
  for (let i = 0; i < cam.plan.length; i++) {
    const m = cam.plan[i];
    if (t < m.start) return { ...hold(cam, shot, land, m.start, t), shot };
    const from = hold(cam, shot, land, m.start, m.start);
    const to = base(cam, m.shot);
    if (t < m.start + m.dur) return { ...between(from, to, (t - m.start) / m.dur, m.kind), moving: true, shot: m.shot };
    shot = m.shot;
    land = m.start + m.dur;
  }
  return { ...hold(cam, shot, land, slideEnd(S.TL, si) ?? land + 10, t), shot };
}

// Holding a shot from `land` until `until`: a close-up creeps in, a whole drawing breathes.
function hold(cam, shot, land, until, t) {
  const b = base(cam, shot), w = cam.whole;
  if (shot && cam.shots.get(shot)) {
    const q = smooth(clamp((t - land - CREEP_AFTER) / Math.max(1.5, until - land - CREEP_AFTER)));
    return { cx: b.cx + (w.cx - b.cx) * DRIFT * q, cy: b.cy + (w.cy - b.cy) * DRIFT * q, z: b.z * (1 + CREEP * q), moving: false };
  }
  const z = cam.breathe ? 1 + BREATH * Math.sin((2 * Math.PI * Math.max(0, t - land)) / BREATH_S) : 1;
  return { cx: w.cx, cy: w.cy, z, moving: false };
}

function between(a, b, p, kind) {
  const e = kind === "in" ? inOut(clamp(p)) : outCubic(clamp(p));
  let z = Math.exp(Math.log(a.z) + (Math.log(b.z) - Math.log(a.z)) * e);
  if (a.z > 1.05 && b.z > 1.05 && Math.abs(Math.log(a.z / b.z)) < Math.log(1.35)) z *= 1 - ARC * Math.sin(Math.PI * clamp(p));
  return { cx: a.cx + (b.cx - a.cx) * e, cy: a.cy + (b.cy - a.cy) * e, z };
}

// Reduced motion: each move is a cut at the moment its cue fires, and nothing drifts.
function cutAt(cam, t) {
  let shot = null;
  for (const m of cam.plan) if (t >= m.cue._at - CUE_LEAD) shot = m.shot;
  return { ...base(cam, shot), moving: false, shot };
}
