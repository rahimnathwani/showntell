"""Charts, terminal sessions and file trees: parsing, cues, reveals, checks, and the catalogue."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from showntell.kinds import find_part, parse_chart, parse_terminal, parse_tree

KINDS = json.loads((Path(__file__).parent.parent / "src" / "showntell" / "text" / "kinds.json").read_text())["kinds"]


def slide(build, visual: str, narration: str):
    w, b = build(f"# T\n\nOpening.\n\n## S {{#s}}\n\n{visual}\n\n{narration}\n")
    return w.slides[-1], b


# -- parsers ---------------------------------------------------------------------------------------


def test_bar_chart_with_series():
    v, problems = parse_chart("series: before, after\nread: 420, 180\nwrite: 610 ms, 390", {"flags": ["bar"]})
    assert not problems and v["ids"] == ["read", "write"] and v["categories"][1]["values"] == [610, 390]
    assert [s["name"] for s in v["series"]] == ["before", "after"]


def test_line_chart_needs_x_and_matching_lengths():
    v, problems = parse_chart("x: 1, 2, 3\nfast: 1, 2, 3\nslow: 1, 2", {"flags": ["line"]})
    assert v["ids"] == ["fast", "slow"] and any("2 values for 3" in p for p in problems)
    _, problems = parse_chart("fast: 1, 2", {"flags": ["line"]})
    assert any("starts with an `x:" in p for p in problems)


def test_chart_values_must_be_numbers():
    _, problems = parse_chart("read: lots", {"flags": ["bar"]})
    assert any("needs numbers" in p for p in problems)


def test_terminal_splits_commands_from_output():
    v = parse_terminal("$ ls\nREADME.md\n% make\n> npm test", {})
    assert [x["cmd"] for x in v["lines"]] == [True, False, True, True] and v["lines"][0]["text"] == "ls"


@pytest.mark.parametrize(
    "body",
    [
        "src/\n  app.py\n  cache/\n    lru.py\ntests/",
        "src\n    app.py\n    cache\n        lru.py\ntests/",
        ".\n├── src\n│   ├── app.py\n│   └── cache\n│       └── lru.py\n└── tests/",
    ],
)
def test_tree_paths_from_indentation_or_tree_output(body):
    v, problems = parse_tree(body, {})
    assert not problems
    assert v["ids"] == ["src", "src/app.py", "src/cache", "src/cache/lru.py", "tests"]
    assert [i["folder"] for i in v["items"]] == [True, False, True, False, True]


def test_tree_parts_are_found_by_path_or_unique_name():
    v, _ = parse_tree("a/\n  x.py\nb/\n  x.py\n  y.py # note", {})
    assert find_part(v, "a/x.py") == "a/x.py" and find_part(v, "y.py") == "b/y.py"
    assert find_part(v, "x.py") is None  # ambiguous
    assert v["items"][-1]["note"] == "note"


# -- in slides ------------------------------------------------------------------------------------


CHART = "```chart bar\nread: 4\nwrite: 6\nlist: 2\n```"
TREE = "```tree\nsrc/\n  app.py\n  cache/\n    lru.py\n```"
TERM = "```terminal\n$ uv run pytest\n12 passed\n$ uv run ruff check\n```"


def test_chart_cues_point_at_bars(build):
    s, b = slide(build, CHART, "The [write path](#write) is slowest; [Read](#read) is next.")
    assert s.kind == "chart" and [(c["kind"], c["id"]) for c in s.says[0].cues] == [("item", "write"), ("item", "read")]
    assert not b.issues


def test_unknown_chart_target_lists_the_ids(build):
    _, b = slide(build, CHART, "The [delete path](#delete).")
    assert any(i.code == "cue_no_target" and "read, write, list" in i for i in b.issues)


def test_tree_cues_and_inferred_cues(build):
    s, _ = slide(build, TREE, "Start with [the cache](#lru.py).")
    assert s.says[0].cues[0]["id"] == "src/cache/lru.py"
    s, _ = slide(build, TREE, "The entry point is app.py and eviction lives in lru.py.")
    assert [c["id"] for c in s.says[0].cues] == ["src/app.py", "src/cache/lru.py"] and s.says[0].cues[0]["inferred"]


def test_terminal_cues_are_lines(build):
    s, b = slide(build, TERM, "First [the tests](#L1), then [the linter](#L3).")
    assert [(c["kind"], c["l0"]) for c in s.says[0].cues] == [("lines", 1), ("lines", 3)] and not b.issues
    _, b = slide(build, TERM, "Then [nothing](#L9).")
    assert any(i.code == "cue_off_code" for i in b.issues)


def test_too_many_bars_and_bad_charts_are_reported(build):
    bars = "\n".join(f"b{i}: {i}" for i in range(15))
    _, b = slide(build, f"```chart bar\n{bars}\n```", "Many bars.")
    assert any(i.code == "block_too_big" for i in b.issues)
    _, b = slide(build, "```chart line\nfast: 1, 2\n```", "A line.")
    assert any(i.code == "block_error" for i in b.issues)


def test_block_problems_carry_the_catalogue_format_once(build):
    fmt = next(k["format"] for k in KINDS if k["kind"] == "stat")
    _, b = slide(build, "```stat\nf nothing\ng: lots\n```", "Two bad lines.")
    errors = [i for i in b.issues if i.code == "block_error"]
    assert len(errors) == 2 and errors[0].fix == f"Format: {fmt}" and not errors[1].fix


def test_build_order_check_covers_new_kinds(build):
    w, b = build(f"# T\n\nOpening.\n\n## S {{#s .build}}\n\n{CHART}\n\nFirst [list](#list).\n\nThen [read](#read).\n")
    b.check_all()
    assert any(i.code == "build_cue_order" for i in b.issues)


# -- the catalogue ----------------------------------------------------------------------------------


@pytest.mark.parametrize("k", KINDS, ids=[k["kind"] for k in KINDS])
def test_every_catalogue_example_builds_cleanly(build, project, k):
    md = k["example"].replace("src/cache.py", "src/cache.py").replace("docs/timeline.png", "docs/timeline.png")
    if k["kind"] == "diff":
        pytest.skip("needs a git repository")
    if k["kind"] == "drawio":  # its example reads a file, as draw.io slides do
        (project / "docs" / "architecture.drawio").write_text(
            (Path(__file__).parent / "fixtures" / "drawio" / "architecture.drawio").read_text()
        )
    w, b = build(f"# T\n\nOpening.\n\n## Example {{#ex}}\n\n{md}\n\nA sentence about it.\n", root=project)
    kind = w.slides[-1].kind
    if k["kind"] == "trace":  # a trace joins its code slide
        assert kind == "code" and w.slides[-1].visual.get("trace"), w.slides[-1].visual
    else:
        assert kind == k["kind"], (kind, k["kind"])
    assert not [i for i in b.issues if i.severity == "error"], b.issues


def test_catalogue_is_a_resource(tmp_path):
    from showntell.server import TEXT

    assert json.loads((TEXT / "kinds.json").read_text())["kinds"]
