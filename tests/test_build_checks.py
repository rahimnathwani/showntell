"""Checks returned to the agent: narration, pacing, reading the screen, sameness and size per frame."""

from __future__ import annotations

import pytest

from showntell.build import LIMITS


def code_block(lines: int, width: int = 10) -> str:
    return "```python\n" + "\n".join(("x" * width) for _ in range(lines)) + "\n```\n"


def has(issues: list[str], *words: str) -> bool:
    return any(all(w in i for w in words) for i in issues)


# -- narration -------------------------------------------------------------------------------------


def test_slide_without_narration_is_reported_when_the_next_slide_starts(build):
    _, b = build("# T\n\nA.\n\n## Silent {#silent}\n\n```python\nx = 1\n```\n\n## Next\n\nB.\n")
    assert has(b.issues, "`silent`", "no narration")


def test_the_last_slide_of_a_call_is_checked_in_that_call_and_reported_once(build):
    _, b = build("# T\n\nA.\n\n## Silent {#silent}\n\n```python\nx = 1\n```\n")
    assert has(b.issues, "`silent`", "no narration")
    b.issues = []
    b.add("## Next\n\nB.\n")  # the next heading checks `silent` again
    b.check_all()
    assert not has(b.issues, "`silent`")


def test_a_slide_still_open_at_the_end_of_a_call_waits_for_its_block(build):
    _, b = build("# T\n\nA.\n\n## Open {#open}\n\n```python\nx = 1\n")
    assert not has(b.issues, "no narration")
    b.add("```\n\nNow the narration.\n")
    assert not has(b.issues, "`open`", "no narration")


def test_a_paragraph_that_starts_like_a_list_item_after_the_visual_is_spoken(build):
    w, b = build(
        "# T\n\nA.\n\n## Steps {#steps}\n\n```python\nx = 1\n```\n\n"
        "1. First the loader reads the file.\n\n- Then it parses every line in turn.\n"
    )
    s = w.slides[-1]
    assert len(w.slides) == 2 and s.kind == "code" and not has(b.issues, "already shows")
    assert [x.text for x in s.says] == ["First the loader reads the file.", "Then it parses every line in turn."]


def test_shown_quote_alone_is_not_narration(build):
    _, b = build("# T\n\nA.\n\n## Q {#q}\n> only shown\n\n## Next\n\nB.\n")
    assert has(b.issues, "`q`", "no narration")


def test_pause_alone_is_not_narration(build):
    _, b = build("# T\n\nA.\n\n## P {#p}\n\n{pause=1}\n\n## Next\n\nB.\n")
    assert has(b.issues, "`p`", "no narration")


# -- pacing ------------------------------------------------------------------------------------------


def test_many_lines_with_little_narration_is_a_pacing_issue(build):
    _, b = build("# T\n\nA.\n\n## Big {#big}\n\n" + code_block(16) + "\nShort.\n\n## Next\n\nB.\n")
    assert has(b.issues, "`big`", "shows 16 lines")


def test_enough_narration_passes_pacing(build):
    words = " ".join(["word"] * 40)
    _, b = build("# T\n\nA.\n\n## Big {#big}\n\n" + code_block(16) + "\n" + words + ".\n\n## Next\n\nB.\n")
    assert not has(b.issues, "`big`", "lines with about")


def test_pacing_applies_to_diffs(build):
    old = "```python old\n" + "\n".join(f"a{i}" for i in range(14)) + "\n```\n"
    new = "```python new\n" + "\n".join(f"b{i}" for i in range(14)) + "\n```\n"
    _, b = build("# T\n\nA.\n\n## D {#d}\n\n" + old + new + "\nTiny.\n\n## Next\n\nB.\n")
    assert has(b.issues, "`d`", "shows 14 lines")


# -- reading the screen ----------------------------------------------------------------------------


def test_narration_repeating_shown_text_is_reported(build):
    line = "Every request checks a small cache then the database before returning"
    _, b = build(f"# T\n\nA.\n\n## S {{#s}}\n> {line}\n\n{line}.\n\n## Next\n\nB.\n")
    assert has(b.issues, "`s`", "reads the on-screen text")


def test_narration_repeating_a_list_body_is_reported(build):
    body = "values live in process memory and vanish on every restart"
    _, b = build(f"# T\n\nA.\n\n## L {{#l}}\n\n- **Memory**: {body}\n\nSo, {body}.\n\n## Next\n\nB.\n")
    assert has(b.issues, "`l`", "reads the on-screen text")


def test_narration_that_differs_from_the_screen_passes(build):
    _, b = build(
        "# T\n\nA.\n\n## S {#s}\n> Cache first, database second\n\nWe look in memory before paying for a round trip.\n\n## Next\n\nB.\n"
    )
    assert not has(b.issues, "on-screen")


def test_narration_repeating_shown_text_with_commas_is_reported(build):
    line = "Every request checks a small cache, then the database, before returning"
    _, b = build(f"# T\n\nA.\n\n## S {{#s}}\n> {line}\n\n{line}.\n\n## Next\n\nB.\n")
    assert has(b.issues, "`s`", "reads the on-screen text")


# -- sameness --------------------------------------------------------------------------------------------


def test_three_slides_opening_alike_are_reported(build):
    md = "# T\n\nA.\n\n" + "".join(f"## S{i}\n\nNext we look at part {i}.\n\n" for i in range(3))
    _, b = build(md)
    b.check_all()
    assert has(b.issues, '3 slides open with "next we"')


def test_two_slides_opening_alike_pass(build):
    md = "# T\n\nA.\n\n" + "".join(f"## S{i}\n\nNext we look at part {i}.\n\n" for i in range(2))
    _, b = build(md)
    b.check_all()
    assert not has(b.issues, "open with")


# -- size per frame --------------------------------------------------------------------------------------


@pytest.mark.parametrize("frame", ["landscape", "square", "portrait"])
@pytest.mark.parametrize("extra,flagged", [(0, False), (1, True)])
def test_code_line_limit_per_frame(build, frame, extra, flagged):
    lines = LIMITS[frame]["lines"] + extra
    _, b = build("# T\n\nA.\n\n## C\n\n" + code_block(lines) + "\n" + " ".join(["w"] * 80) + "\n", frame=frame)
    assert has(b.issues, f"shows {lines} lines; this frame reads well up to") is flagged


@pytest.mark.parametrize("frame", ["landscape", "square", "portrait"])
@pytest.mark.parametrize("extra,flagged", [(0, False), (1, True)])
def test_code_width_limit_per_frame(build, frame, extra, flagged):
    width = LIMITS[frame]["width"] + extra
    _, b = build("# T\n\nA.\n\n## C\n\n" + code_block(3, width) + "\nWords.\n", frame=frame)
    assert has(b.issues, f"lines of {width} characters") is flagged


def test_tabs_count_as_four_columns(build):
    tabs = LIMITS["landscape"]["width"] // 4 + 1
    _, b = build("# T\n\nA.\n\n## C\n\n```go\n" + "\t" * tabs + "x\n```\n\nWords.\n")
    assert has(b.issues, f"lines of {tabs * 4 + 1} characters")


def test_diff_side_line_limit(build):
    n = LIMITS["landscape"]["diff_lines"] + 1
    old = "```python old\n" + "\n".join(f"a{i}" for i in range(n)) + "\n```\n"
    new = "```python new\nb\n```\n"
    _, b = build("# T\n\nA.\n\n## D\n\n" + old + new + "\nWords.\n")
    assert has(b.issues, f"old side of a diff shows {n} lines")


def test_diff_side_width_limit(build):
    old = "```python old\nx = '" + "a" * 60 + "'\n```\n"
    new = "```python new\ny = 1\n```\n"
    _, b = build("# T\n\nA.\n\n## D\n\n" + old + new + "\nWords.\n")
    assert has(b.issues, "characters")


def test_diagram_node_limit(build):
    nodes = "\n".join(f"  n{i}[Node {i}] --> n{i + 1}[Node {i + 1}]" for i in range(12))
    _, b = build("# T\n\nA.\n\n## G\n\n```mermaid\nflowchart LR\n" + nodes + "\n```\n\nWords.\n")
    assert has(b.issues, "13 nodes")


def test_open_fence_at_end_of_call_is_reported(build):
    _, b = build("# T\n\nA.\n\n## C\n\n```python\nx = 1\n")
    assert has(b.issues, "still open")


# -- the camera and withheld answers ------------------------------------------------------------

DRAWING = """## Sky {#sky}

```svg
<svg viewBox="0 0 1150 560"><g id="sun"><circle cx="100" cy="100" r="40"/></g><g id="earth"><circle cx="400" cy="100" r="40"/></g><g id="moon"><circle cx="700" cy="100" r="40"/></g></svg>
```

Light leaves [the sun](#sun){CUE} and travels a long way through empty space before anything stops it.

It reaches [the Earth](#earth){CUE}, which turns all day so that one half is lit and the other half is dark.

And it reaches [the moon](#moon){CUE}, which sends some of that light back to us at night.
"""


@pytest.mark.parametrize("cue, found", [("", True), ("{.zoom}", False)])
def test_naming_several_parts_of_one_drawing_suggests_close_ups(build, cue, found):
    _, b = build("# T\n\nA.\n\n" + DRAWING.replace("{CUE}", cue))
    assert any(i.code == "zoom_unused" for i in b.issues) is found


CHAIN = """## Lookup {#lookup}

```mermaid
graph LR
  b[Browser] --> r[Resolver] --> root[Root] --> ip[Address]
```

The [browser](#b) asks the [resolver](#r), which asks [the root](#root), and back comes [the address](#ip){STYLE}.
"""


@pytest.mark.parametrize("style, found", [("", True), ("{.decode}", False)])
def test_a_chain_cued_in_order_to_its_end_suggests_decode_once(build, style, found):
    _, b = build("# T\n\nA.\n\n" + CHAIN.replace("{STYLE}", style) + CHAIN.replace("lookup", "again").replace("{STYLE}", ""))
    assert [i.code for i in b.issues].count("decode_unused") == (1 if found else 0)


def test_decode_on_a_target_without_a_label_is_reported(build):
    md = "## Fast {#fast}\n\n```stat\np95 latency: 400 ms -> 100 ms\n```\n\nThe [p95 latency](#p95-latency){.decode} fell by three quarters.\n"
    _, b = build("# T\n\nA.\n\n" + md)
    assert has(b.issues, "{.decode}", "does nothing")
    _, b = build("# T\n\nA.\n\n" + CHAIN.replace("{STYLE}", "{.decode}"))
    assert not has(b.issues, "does nothing")
