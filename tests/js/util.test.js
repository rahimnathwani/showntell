import assert from "node:assert/strict";
import { test } from "node:test";
import { captionPages, esc, inline, splitLines } from "../../src/showntell/static/js/util.js";

test("escapes before formatting", () => {
  assert.equal(esc(`<a href="x">`), "&lt;a href=&quot;x&quot;&gt;");
  assert.equal(inline("**<b>** and `x < y`"), "<strong>&lt;b&gt;</strong> and <code class=\"inline\">x &lt; y</code>");
});

test("inline drops link targets and say overrides", () => {
  assert.equal(inline("[the loop](#L4) and [SQL]{say=\"sequel\"}"), "the loop and SQL");
});

test("splitLines reopens spans across line breaks", () => {
  const lines = splitLines('<span class="s">"a\nb"</span>\nc');
  assert.deepEqual(lines, ['<span class="s">"a</span>', '<span class="s">b"</span>', "c"]);
});

test("captions page five to seven words at a time, breaking at punctuation", () => {
  const text = "The browser looks up the name, then opens a connection to the server, sends the request and waits for the answer.";
  let pos = 0;
  const words = text.split(" ").map((w) => { const c0 = text.indexOf(w, pos); pos = c0 + w.length; return { c0, c1: pos }; });
  const pages = captionPages(text, words).map((p) => text.slice(p[0].c0, p[p.length - 1].c1));
  assert.deepEqual(pages, ["The browser looks up the name,", "then opens a connection to the server,", "sends the request and waits for the answer."]);
  assert.ok(captionPages(text, words).every((p) => p.length <= 9));
});

test("a caption never ends on a page of one or two words", () => {
  const text = "One two three four five six seven eight.";
  let pos = 0;
  const words = text.split(" ").map((w) => { const c0 = text.indexOf(w, pos); pos = c0 + w.length; return { c0, c1: pos }; });
  assert.deepEqual(captionPages(text, words).map((p) => p.length), [8]);
});
