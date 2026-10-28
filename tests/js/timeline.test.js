import assert from "node:assert/strict";
import { test } from "node:test";
import { build, CAMERA, cameraPlan, CUE_LEAD, firedCues, revealLevel, SCENE_LEAD, segmentAt, slideCues, slideEnd, slideSpans, slideStart, stateAt, steps, wordAt } from "../../src/showntell/static/js/timeline.js";

const say = (id, dur, cues = [], extra = {}) => ({ id, status: "ready", text: "one two three", duration: dur, cues, words: [], ...extra });
const W = {
  slides: [
    { id: "intro", kind: "title", classes: [], visual: {}, says: [say("intro.1", 2)] },
    {
      id: "list", kind: "list", classes: ["build"], visual: { items: [{ id: "a" }, { id: "b" }, { id: "c" }] },
      says: [say("list.1", 3, [{ at: 0, kind: "item", id: "a", t: 0.5 }, { at: 5, kind: "item", id: "b", t: 2 }]), say("list.2", 2, [{ at: 0, kind: "item", id: "c", t: 1 }])],
    },
    { id: "pause", kind: "text", classes: [], visual: {}, says: [{ id: "p.1", status: "ready", pause: 1.5 }] },
  ],
};

test("segments are laid end to end with gaps", () => {
  const tl = build(W);
  assert.equal(tl.segs.length, 4);
  assert.deepEqual(tl.segs.map((s) => s.start), [0, 2.6, 5.9, 8.5]);
  assert.equal(tl.segs[3].gap, 0); // a pause has no extra gap
  assert.equal(tl.total, 10);
});

test("segmentAt finds the paragraph playing at a time", () => {
  const tl = build(W);
  assert.equal(segmentAt(tl, 0), 0);
  assert.equal(segmentAt(tl, 2.59), 0);
  assert.equal(segmentAt(tl, 2.6), 1);
  assert.equal(segmentAt(tl, 99), 3);
});

test("cues fire by local time and persist across paragraphs of a slide", () => {
  const tl = build(W);
  assert.deepEqual(firedCues(W, tl, 1, 0.1).map((c) => c.id), []);
  assert.deepEqual(firedCues(W, tl, 1, 1).map((c) => c.id), ["a"]);
  assert.deepEqual(firedCues(W, tl, 2, 0).map((c) => c.id), ["a", "b"]);
  assert.deepEqual(firedCues(W, tl, 2, 1.2).map((c) => c.id), ["a", "b", "c"]);
});

test("cues fire a moment before their word, so the pointer lands as it is spoken", () => {
  const tl = build(W);
  assert.deepEqual(firedCues(W, tl, 1, 0.5 - CUE_LEAD - 0.01).map((c) => c.id), []);
  assert.deepEqual(firedCues(W, tl, 1, 0.5 - CUE_LEAD).map((c) => c.id), ["a"]);
});

test("build slides reveal up to the latest item pointed at, then everything", () => {
  const tl = build(W);
  assert.deepEqual(stateAt(W, tl, 2.7).reveal, { items: 0 });
  assert.deepEqual(stateAt(W, tl, 3.2).reveal, { items: 1 });
  assert.deepEqual(stateAt(W, tl, 5.0).reveal, { items: 2 });
  assert.equal(stateAt(W, tl, 8.3).reveal, null); // after the slide's last paragraph
});

test("code reveal starts just before the first line pointed at", () => {
  const slide = { kind: "code", classes: ["build"], visual: { start: 10 }, says: [{ cues: [{ kind: "lines", l0: 14, l1: 15 }, { kind: "lines", l0: 20, l1: 20 }] }] };
  assert.deepEqual(revealLevel(slide, [], false), { line: 13 });
  assert.deepEqual(revealLevel(slide, [{ kind: "lines", l0: 14, l1: 15 }], false), { line: 15 });
  assert.equal(revealLevel({ ...slide, classes: [] }, [], false), null);
});

test("pending narration is estimated and not ready", () => {
  const w = { slides: [{ id: "x", kind: "text", classes: [], visual: {}, says: [{ id: "x.1", status: "pending", text: "a b c d e f g h i j k l m n o p q r s t u v w x y z a" }] }] };
  const tl = build(w);
  assert.equal(tl.segs[0].ready, false);
  assert.ok(tl.segs[0].dur > 9 && tl.segs[0].dur < 11);
});

test("slide spans and starts", () => {
  const tl = build(W);
  assert.equal(slideStart(tl, 1), 2.6);
  const spans = slideSpans(tl, 3);
  assert.equal(spans[1].start, 2.6);
  assert.ok(Math.abs(spans[1].end - 8.5) < 1e-9);
});

test("wordAt", () => {
  const s = { words: [{ s: 0.1 }, { s: 0.5 }, { s: 0.9 }] };
  assert.equal(wordAt(s, 0), -1);
  assert.equal(wordAt(s, 0.5), 1);
  assert.equal(wordAt(s, 5), 2);
});

test("slideEnd is the end of a slide's last segment and gap", () => {
  const tl = build(W);
  assert.equal(slideEnd(tl, 0), 2.6);
  assert.equal(slideEnd(tl, 1), 8.5);
  assert.equal(slideEnd(tl, 9), null);
});

test("steps: every slide start, each reveal on a step-reveal slide, then everything", () => {
  const tl = build(W);
  const s = steps(W, tl);
  assert.deepEqual(s.map((x) => x.slide), [0, 1, 1, 1, 1, 1, 2]);
  assert.equal(s[0].t, 0);
  assert.equal(s[1].t, 2.6); // the list slide starts with nothing revealed
  assert.ok(Math.abs(s[2].t - (2.6 + 0.5 + 0.06)) < 1e-9); // first item
  const levels = s.slice(1, 6).map((x) => JSON.stringify(stateAt(W, tl, x.t).reveal));
  assert.deepEqual(levels, ['{"items":0}', '{"items":1}', '{"items":2}', '{"items":3}', "null"]);
});

test("the code step on screen is the latest step cue fired", () => {
  const w = { slides: [{ id: "e", kind: "code", classes: [], visual: { steps: [{}, {}, {}] }, says: [
    say("e.1", 4, [{ at: 0, kind: "step", n: 2, t: 1 }, { at: 9, kind: "step", n: 3, t: 3 }]) ] }] };
  const tl = build(w);
  assert.deepEqual([0.5, 1.2, 3.5].map((t) => stateAt(w, tl, t).step), [1, 2, 3]);
});

test("an edge path reveals both its ends in a step-reveal flowchart", () => {
  const w = { slides: [{ id: "f", kind: "diagram", classes: ["build"], visual: { type: "flowchart", order: ["a", "b", "c"] }, says: [
    say("f.1", 4, [{ at: 0, kind: "edge", path: ["a", "b"], t: 1 }]) ] }] };
  const tl = build(w);
  assert.deepEqual(stateAt(w, tl, 0.5).reveal, { nodes: 0, order: ["a", "b", "c"] });
  assert.deepEqual(stateAt(w, tl, 1.5).reveal, { nodes: 2, order: ["a", "b", "c"] });
});

test("fired cues carry the time they fired on the walkthrough's clock", () => {
  const tl = build(W);
  const st = stateAt(W, tl, 2.6 + 2.5);
  assert.deepEqual(st.cues.map((c) => +c._at.toFixed(2)), [3.1, 4.6]);
  assert.ok(Math.abs(st.sinceSlide - 2.5) < 1e-9);
});

test("the next slide comes in during the silence before its first word", () => {
  const tl = build(W); // the list slide starts at 2.6, after the title's 2 s and its 0.6 s gap
  assert.equal(stateAt(W, tl, 2.6 - SCENE_LEAD - 0.01).slide, 0);
  const early = stateAt(W, tl, 2.6 - SCENE_LEAD + 0.01);
  assert.equal(early.slide, 1);
  assert.ok(early.local < 0 && early.sinceSlide < 0);
  assert.deepEqual(early.cues, []);
  assert.deepEqual(early.reveal, { items: 0 });
  assert.equal(stateAt(W, tl, 1.9).slide, 0); // never during speech
});

// A slide whose words are timed, one every half second.
function camSlide(styles) {
  const text = "We start here. Then over on the far left panel, and the right panel. Back out.";
  let pos = 0;
  const words = text.split(" ").map((w, i) => { const c0 = text.indexOf(w, pos); pos = c0 + w.length; return { s: i * 0.5, c0, c1: pos }; });
  const at = (word) => words.find((w) => text.slice(w.c0, w.c1) === word).s;
  const cues = [
    { at: 0, kind: "item", id: "left", t: at("left"), style: styles[0] },
    { at: 0, kind: "item", id: "right", t: at("right"), style: styles[1] },
    { at: 0, kind: "whole", t: at("Back"), style: "" },
  ];
  return { slides: [{ id: "s", kind: "svg", classes: [], visual: {}, says: [say("s.1", 8, cues, { text, words })] }] };
}

test("slide cues know when their sentence starts", () => {
  const w = camSlide(["zoom", "zoom"]);
  const cues = slideCues(w, build(w), 0);
  assert.deepEqual(cues.map((c) => c._at), [4, 6, 7]);
  assert.deepEqual(cues.map((c) => c._sentence), [1.5, 1.5, 7]);
});

test("the camera moves in before its word, holds, moves across and pulls back out quickly", () => {
  const w = camSlide(["zoom", "zoom"]);
  const plan = cameraPlan(w, build(w), 0);
  assert.deepEqual(plan.map((m) => m.kind), ["in", "in", "out"]);
  const [a, b, c] = plan;
  // In: starts at least 0.4 s into its sentence, settles as "left" is heard.
  assert.ok(a.start >= 1.5 + CAMERA.AFTER_SENTENCE - 1e-9);
  assert.ok(Math.abs(a.start + a.dur - (4 + CAMERA.SETTLE)) < 1e-9);
  // Across: never overlaps the last move, and gives the last cue its moment.
  assert.ok(b.start >= a.start + a.dur + CAMERA.HOLD - 1e-9 && b.start >= 4 + CUE_LEAD - 1e-9);
  assert.ok(b.start + b.dur <= 6 + CAMERA.LATE + 1e-9);
  // Out: quicker, starting just before "Back".
  assert.equal(c.dur, CAMERA.OUT_S);
  assert.ok(c.start < 7 && c.start > 6.5);
  assert.equal(c.shot, null);
});

test("no zoom cue, no camera plan; a repeated shot does not move", () => {
  let w = camSlide(["", "circle"]);
  assert.deepEqual(cameraPlan(w, build(w), 0), []);
  w = camSlide(["zoom", "zoom"]);
  w.slides[0].says[0].cues[1].id = "left";
  assert.deepEqual(cameraPlan(w, build(w), 0).map((m) => m.kind), ["in", "out"]);
});
