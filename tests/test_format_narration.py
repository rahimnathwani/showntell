"""Inline narration: display text and exact display offsets for every construct."""

from __future__ import annotations

from showntell.format import inline_plain, parse_narration


def span(n, a_b) -> str:
    a, b = a_b
    return n.display[a:b]


def test_plain_text_is_unchanged():
    n = parse_narration("Every request checks the cache.")
    assert n.display == "Every request checks the cache." and not n.cues and not n.code


def test_whitespace_collapses_and_trailing_space_is_dropped():
    assert parse_narration("  a\n  b\t c  ").display == "a b c"


def test_cue_with_quoted_text():
    n = parse_narration('The method starts by [checking the dictionary](#L12 "if key in self._data").')
    assert n.display == "The method starts by checking the dictionary."
    (c,) = n.cues
    assert (c.at, c.end, c.label, c.target, c.text) == (21, 44, "checking the dictionary", "L12", "if key in self._data")
    assert n.display[c.at : c.end] == "checking the dictionary"


def test_cue_with_single_quoted_and_parenthesised_titles():
    a = parse_narration("[x](#L1 'one') [y](#L2 (two))")
    assert [(c.target, c.text) for c in a.cues] == [("L1", "one"), ("L2", "two")]


def test_cue_offsets_after_collapsed_whitespace():
    n = parse_narration("Look  at\n   [this](#L1) now")
    (c,) = n.cues
    assert n.display == "Look at this now" and (c.at, c.end) == (8, 12)


def test_cue_targets_of_every_shape():
    n = parse_narration("[a](#L12) [b](#L12-18) [c](#-L5) [d](#+L7) [e](#memory) [f](#)")
    assert [c.target for c in n.cues] == ["L12", "L12-18", "-L5", "+L7", "memory", ""]


def test_cue_with_hash_alone_and_text():
    (c,) = parse_narration('[the decision](# "In cache?")').cues
    assert (c.target, c.text, c.label) == ("", "In cache?", "the decision")


def test_reserved_cue_attributes_are_skipped():
    n = parse_narration("[x](#L1){.zoom} y")
    assert n.display == "x y" and n.cues[0].label == "x"


def test_code_span_range_and_display():
    n = parse_narration("Call `get_value` now")
    assert n.display == "Call get_value now" and n.code == [(5, 14)]
    assert span(n, n.code[0]) == "get_value"


def test_double_backtick_code_span_with_padding():
    n = parse_narration("Use `` a`b `` here")
    assert n.display == "Use a`b here" and span(n, n.code[0]) == "a`b"


def test_markup_inside_code_span_is_literal():
    n = parse_narration("Write `[x](#L1)` to cue")
    assert n.display == "Write [x](#L1) to cue" and not n.cues


def test_unclosed_backtick_is_literal():
    assert parse_narration("a ` b").display == "a ` b"


def test_say_override_range():
    n = parse_narration('We use [SQL]{say="sequel"} daily')
    assert n.display == "We use SQL daily" and n.say == [(7, 10, "sequel")]


def test_say_override_with_single_quotes():
    assert parse_narration("[k8s]{say='kubernetes'}").say == [(0, 3, "kubernetes")]


def test_emphasis_is_shown_without_markers():
    assert parse_narration("a **bold** and _italic_ and *star*").display == "a bold and italic and star"


def test_nested_emphasis_inside_link_keeps_offsets():
    n = parse_narration("See [the **fast** path](#L3) here")
    (c,) = n.cues
    assert n.display == "See the fast path here"
    assert (c.at, c.end, c.label) == (4, 17, "the fast path")


def test_link_inside_emphasis_keeps_offsets():
    n = parse_narration("Now **see [this](#L1)** please")
    (c,) = n.cues
    assert n.display == "Now see this please" and n.display[c.at : c.end] == "this"


def test_code_inside_link_is_both_cue_and_code():
    n = parse_narration("then [`get`](#L1) runs")
    assert n.display == "then get runs"
    assert (n.cues[0].at, n.cues[0].end) == (5, 8) and n.code == [(5, 8)]


def test_say_inside_link_is_offset_into_outer_text():
    n = parse_narration('ok [use [SQL]{say="sequel"} now](#L2)')
    assert n.display == "ok use SQL now"
    assert n.say == [(7, 10, "sequel")] and (n.cues[0].at, n.cues[0].end) == (3, 14)


def test_snake_case_words_are_not_emphasis():
    assert parse_narration("snake_case_word stays").display == "snake_case_word stays"


def test_lone_asterisks_are_literal():
    assert parse_narration("2 * 3 * 4").display == "2 * 3 * 4"


def test_escapes_show_the_character():
    assert parse_narration(r"a \*literal\* star and \`tick\`").display == "a *literal* star and `tick`"


def test_escaped_brackets_are_not_a_cue():
    n = parse_narration(r"\[not a link\](#L1)")
    assert n.display == "[not a link](#L1)" and not n.cues


def test_footnote_reference_is_recorded_and_not_shown():
    n = parse_narration("A fact[^src] stated.")
    assert n.display == "A fact stated." and n.notes == ["src"]


def test_non_cue_link_shows_its_label_only():
    n = parse_narration("See [the docs](https://example.com) now")
    assert n.display == "See the docs now" and not n.cues


def test_html_tags_are_dropped():
    assert parse_narration("a <b>bold</b> c").display == "a bold c"


def test_offsets_are_clamped_to_display_length():
    n = parse_narration("end [here](#L1)   ")
    c = n.cues[0]
    assert c.end <= len(n.display) and n.display[c.at : c.end] == "here"


def test_inline_plain_strips_markup():
    assert inline_plain("The **`get`** [method](#L1)") == "The get method"


def test_cue_styles_keep_known_names_only():
    n = parse_narration("[the answer](#total){.decode .highlight .sparkle} y")
    assert n.cues[0].style == "decode highlight"
