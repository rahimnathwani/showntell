// The timeline: every narration paragraph gets a start time on one clock, and the visible
// state of the player (slide, pointer, step reveals, caption word) is a pure function of time.
// Nothing here touches the DOM, so it is shared by playback, seeking and tests.

export const GAP = 0.3; // silence after a paragraph
export const SLIDE_GAP = 0.6; // silence after a slide's last paragraph
export const WORDS_PER_SECOND = 2.7;
// Cues fire this long before their word is spoken: the pointer takes about as long to glide
// over, so it lands as the word is heard instead of after it.
export const CUE_LEAD = 0.3;
// A new slide starts coming in this long before its first word, so the change has landed by the
// time the word is heard.
export const SCENE_LEAD = 0.45;

export function estimate(say) {
  if (say.pause) return say.pause;
  if (say.status === "ready" && say.duration) return say.duration;
  return Math.max(1, (say.text || "").split(/\s+/).filter(Boolean).length / WORDS_PER_SECOND);
}

// Build segments for every paragraph: {key, slide, say, start, dur, gap, ready}.
export function build(w) {
  const segs = [];
  let t = 0;
  w.slides.forEach((slide, si) => {
    const says = slide.says.filter((x) => x.pause || x.text);
    if (!says.length) {
      // A slide without narration still gets a moment on screen.
      segs.push({ key: `${slide.id}#empty`, slide: si, say: -1, start: t, dur: 2.5, gap: SLIDE_GAP, ready: true, empty: true });
      t += 2.5 + SLIDE_GAP;
      return;
    }
    says.forEach((x, k) => {
      const j = slide.says.indexOf(x);
      const dur = estimate(x);
      const gap = x.pause ? 0 : k === says.length - 1 ? SLIDE_GAP : GAP;
      const ready = !!x.pause || x.status === "ready" || x.status === "error";
      segs.push({ key: x.id, slide: si, say: j, start: t, dur, gap, ready, pause: !!x.pause, error: x.status === "error" });
      t += dur + gap;
    });
  });
  return { segs, total: t };
}

// Index of the segment playing at global time t (the last one starting at or before t).
export function segmentAt(tl, t) {
  const { segs } = tl;
  if (!segs.length) return -1;
  let lo = 0, hi = segs.length - 1;
  if (t <= 0) return 0;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (segs[mid].start <= t) lo = mid; else hi = mid - 1;
  }
  return lo;
}

// Cues that have fired on a slide by time t: earlier paragraphs' cues all, the current
// paragraph's up to its local time. Returns them in firing order.
export function firedCues(w, tl, segIndex, local) {
  const seg = tl.segs[segIndex];
  if (!seg) return [];
  const slide = w.slides[seg.slide];
  let first = segIndex;
  while (first > 0 && tl.segs[first - 1].slide === seg.slide) first--;
  const out = [];
  for (let k = first; k <= segIndex; k++) {
    const s = tl.segs[k];
    if (s.say < 0) continue;
    for (const c of slide.says[s.say].cues || []) {
      if (k < segIndex || (c.t ?? 0) <= local + CUE_LEAD) out.push({ ...c, _say: s.say, _at: s.start + (c.t ?? 0) });
    }
  }
  return out;
}

// Everything the screen should show at global time t.
export function stateAt(w, tl, t) {
  let i = segmentAt(tl, t);
  if (i < 0) return { seg: -1, slide: -1 };
  // In the silence after a slide, the next slide comes in early (SCENE_LEAD): its state, with a
  // local time below zero, so nothing in it has fired yet.
  const after = tl.segs[i + 1];
  if (after && after.slide !== tl.segs[i].slide && t >= after.start - SCENE_LEAD && t >= tl.segs[i].start + tl.segs[i].dur) i += 1;
  const seg = tl.segs[i];
  const local = t <= 0 ? 0 : t - seg.start;
  const inAudio = local < seg.dur;
  const cues = firedCues(w, tl, i, local);
  const slide = w.slides[seg.slide];
  const lastOfSlide = i === tl.segs.length - 1 || tl.segs[i + 1].slide !== seg.slide;
  // Step reveals finish once the slide's narration is over.
  const revealAll = lastOfSlide && !inAudio;
  let step = 1;
  for (const c of cues) if (c.kind === "step") step = c.n;
  const slideStartT = tl.segs.find((s) => s.slide === seg.slide)?.start ?? 0;
  return {
    seg: i,
    slide: seg.slide,
    say: seg.say,
    local,
    inAudio,
    cue: cues.length ? cues[cues.length - 1] : null,
    cues,
    revealAll,
    reveal: revealLevel(slide, cues, revealAll),
    step, // code that evolves: the version on screen
    sinceSlide: t - slideStartT, // seconds since the slide began
  };
}

// For `.build` slides: how much is revealed, by the kind of part the slide's cues point at.
// {items: n} for parts with ids (list items, chart bars, tree entries, numbers, messages),
// {rows: n} for table rows, {line: n} for one pane of numbered lines (code, terminal),
// {nodes: n} for flowcharts; counted in source order. null when everything shows.
export function revealLevel(slide, cues, all) {
  if (!slide || !(slide.classes || []).includes("build") || all || slide.pair) return null; // a pair shows whole
  const v = slide.visual || {};
  const every = slide.says.flatMap((x) => x.cues || []);
  const upTo = (kind, ids) => {
    let n = 0;
    for (const c of cues) if (c.kind === kind) n = Math.max(n, ids.indexOf(c.part ?? c.id) + 1); // a nested part reveals its outer one
    return n;
  };
  if (every.some((c) => c.kind === "item")) return { items: upTo("item", v.ids || (v.items || []).map((it) => it.id)) };
  if (every.some((c) => c.kind === "row")) return { rows: upTo("row", v.ids || []) };
  const lineCues = every.filter((c) => c.kind === "lines");
  if (lineCues.length && !v.old) { // a diff's two panes always show whole
    // Show the lines leading up to the first one the narration points at.
    let line = Math.min(...lineCues.map((c) => c.l0)) - 1;
    for (const c of cues) if (c.kind === "lines") line = Math.max(line, c.l1);
    return { line };
  }
  if ((v.type === "flowchart" || v.type === "graph") && every.some((c) => c.kind === "node" || c.kind === "edge")) {
    const order = v.order || [];
    let n = 0;
    for (const c of cues) {
      if (c.kind === "node") n = Math.max(n, order.indexOf(c.id) + 1);
      if (c.kind === "edge") for (const id of c.path) n = Math.max(n, order.indexOf(id) + 1);
    }
    return { nodes: n, order };
  }
  return null;
}

// The word being spoken: index into say.words at local time, or -1.
export function wordAt(say, local) {
  const words = say?.words || [];
  let idx = -1;
  for (let i = 0; i < words.length; i++) {
    if (words[i].s <= local + 0.02) idx = i; else break;
  }
  return idx;
}

// Start time of a slide (its first segment).
export function slideStart(tl, slideIndex) {
  const s = tl.segs.find((x) => x.slide === slideIndex);
  return s ? s.start : null;
}

// End time of a slide (after its last segment and gap).
export function slideEnd(tl, slideIndex) {
  let end = null;
  for (const s of tl.segs) if (s.slide === slideIndex) end = s.start + s.dur + s.gap;
  return end;
}

// Where playing a range of slides stops: before the slide after it starts coming in.
export function rangeEnd(tl, slideIndex) {
  const end = slideEnd(tl, slideIndex);
  if (end === null) return tl.total;
  return tl.segs.some((s) => s.slide > slideIndex) ? end - SCENE_LEAD : end;
}

// When a slide shows alone with everything on it revealed, for pictures of it: after its last
// paragraph (everything reveals once the narration is over) and before the next slide starts
// coming in. A slide ending on a pause has no silence after it: just before the next one.
// Kept in step with _end_times in service.py.
export function revealedAt(tl, slideIndex) {
  const own = tl.segs.filter((s) => s.slide === slideIndex);
  if (!own.length) return null;
  const last = own[own.length - 1];
  const lo = last.start + last.dur;
  const next = tl.segs.find((s) => s.slide > slideIndex);
  const end = next ? next.start : tl.total;
  const hi = next ? end - SCENE_LEAD : end;
  return hi > lo ? lo + Math.min(0.1, (hi - lo) / 2) : Math.max(own[0].start, end - 0.05);
}

// Steps for presenting by hand: the start of every slide, plus, on step-reveal slides, the
// moment each cue reveals another part, and the end where everything shows. [{t, slide, say}]
export function steps(w, tl) {
  const out = [];
  const seen = new Set();
  const add = (t, slide, say) => {
    const key = Math.round(t * 100);
    if (!seen.has(key)) { seen.add(key); out.push({ t, slide, say }); }
  };
  w.slides.forEach((slide, si) => {
    const segs = tl.segs.filter((s) => s.slide === si);
    if (!segs.length) return;
    add(segs[0].start, si, segs[0].say);
    if (!(slide.classes || []).includes("build")) return;
    let last = JSON.stringify(revealLevel(slide, [], false));
    for (let i = 0; i < segs.length; i++) {
      const seg = segs[i];
      if (seg.say < 0) continue;
      for (const c of slide.says[seg.say].cues || []) {
        const t = seg.start + (c.t ?? 0) + 0.06;
        const st = stateAt(w, tl, t);
        const level = JSON.stringify(st.reveal);
        if (level !== last) { last = level; add(t, si, seg.say); }
      }
    }
    // Everything shows once the narration ends, if that happens before the next slide.
    const tail = segs[segs.length - 1];
    const t = tail.start + tail.dur + 0.01;
    if (last !== "null" && tail.gap > 0.05 && stateAt(w, tl, t).slide === si) add(t, si, tail.say);
  });
  return out.sort((a, b) => a.t - b.t);
}

// Per-slide [start, end] spans for the progress bar.
export function slideSpans(tl, nSlides) {
  const spans = Array.from({ length: nSlides }, () => null);
  for (const s of tl.segs) {
    const end = s.start + s.dur + s.gap;
    if (!spans[s.slide]) spans[s.slide] = { start: s.start, end, ready: s.ready };
    else {
      spans[s.slide].end = end;
      spans[s.slide].ready = spans[s.slide].ready && s.ready;
    }
  }
  return spans;
}

export function mmss(s) {
  s = Math.max(0, Math.round(s || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// Every cue on a slide, fired or not, in firing order, with `_at` (when its word is heard) and
// `_sentence` (when the sentence holding it starts) on the walkthrough's clock.
export function slideCues(w, tl, slideIndex) {
  const slide = w.slides[slideIndex];
  const out = [];
  for (const s of tl.segs) {
    if (s.slide !== slideIndex || s.say < 0) continue;
    const say = slide.says[s.say];
    for (const c of say.cues || []) {
      const t = c.t ?? 0;
      out.push({ ...c, _say: s.say, _at: s.start + t, _sentence: s.start + sentenceStart(say, t) });
    }
  }
  return out.sort((a, b) => a._at - b._at);
}

// Local time of the first word of the sentence that is being spoken at local time t.
function sentenceStart(say, t) {
  const words = say.words || [], text = say.text || "";
  let j = wordAt(say, t);
  while (j > 0 && !/[.!?:;]["'”’)\]]*\s*$/.test(text.slice(words[j - 1].c0, words[j].c0))) j--;
  return j > 0 ? words[j].s : 0;
}

// The camera on one slide: a cue styled `zoom` moves it onto its target, any other cue after
// one pulls it back to the whole visual. Each move is placed against the speech: a move in starts
// soon after its sentence does and settles as the cue's word is heard; a move out is quicker and
// starts just before its word. Moves never overlap, and each leaves the last shot a moment.
// Returns [{start, dur, kind: "in"|"out", shot, cue}], `shot` a key for the target (null: whole).
export const CAMERA = { IN_S: 1.6, OUT_S: 1.0, MIN_IN_S: 0.9, AFTER_SENTENCE: 0.4, SETTLE: 0.15, LATE: 0.6, HOLD: 0.4 };

export function shotOf(cue) {
  if (!(cue.style || "").split(/\s+/).includes("zoom")) return null;
  return [cue.kind, cue.id ?? "", cue.l0 ?? "", cue.l1 ?? "", cue.side ?? "", cue.text ?? "", (cue.box || []).join(","), cue.pane ?? ""].join(":");
}

export function cameraPlan(w, tl, slideIndex) {
  const C = CAMERA;
  const moves = [];
  let shot = null, free = slideStart(tl, slideIndex) ?? 0, lastCue = -Infinity;
  for (const c of slideCues(w, tl, slideIndex)) {
    const to = shotOf(c);
    if (to === shot) { lastCue = c._at; continue; }
    const after = Math.max(free + C.HOLD, lastCue + CUE_LEAD);
    let start, dur;
    if (to) {
      start = Math.max(c._at + C.SETTLE - C.IN_S, c._sentence + C.AFTER_SENTENCE, after);
      dur = Math.max(C.MIN_IN_S, Math.min(C.IN_S, c._at + C.LATE - start));
    } else {
      start = Math.max(c._at - 0.35, after);
      dur = C.OUT_S;
    }
    moves.push({ start, dur, kind: to ? "in" : "out", shot: to, cue: c });
    shot = to;
    free = start + dur;
    lastCue = c._at;
  }
  return moves.some((m) => m.kind === "in") ? moves : [];
}
