"""Plots, memory pictures, traces and ELK: what the builder reads, refuses and resolves."""

from __future__ import annotations

import math
import time

import pytest

from showntell import expr
from showntell.kinds.memory import parse_memory
from showntell.kinds.plot import parse_plot
from showntell.trace import parse_trace

# -- expressions ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "x", "want"),
    [
        ("x^2", 3, 9),
        ("2x - 1", 3, 5),
        ("-x^2", 3, -9),
        ("2^3^2", 0, 512),
        ("3(x+1)", 1, 6),
        ("(x+1)(x-1)", 2, 3),
        ("2 sin(pi x/2)", 1, 2),
        ("x**2", 4, 16),
    ],
)
def test_expressions_read_as_written(src, x, want):
    assert expr.evaluate(expr.parse(src), {"x": float(x)}) == pytest.approx(want)


@pytest.mark.parametrize(
    ("src", "says"),
    [
        ("1/2x", "reads two ways"),
        ("e^2x", "reads two ways"),
        ("log(x)", "`ln`"),
        ("|x|", "abs(x)"),
        ("sin x", "sin(x)"),
        ("y*2", "unknown name"),
        ("x +", "ends too early"),
    ],
)
def test_expressions_that_read_two_ways_or_not_at_all_are_refused(src, says):
    with pytest.raises(expr.ExprError, match=None) as e:
        expr.parse(src)
    assert says in str(e.value)


def test_undefined_values_are_gaps_and_huge_ones_end_quickly():
    for src, x in (("1/x", 0), ("sqrt(-1)", 1), ("x^(1/3)", -8), ("10^1000", 0), ("exp(1000)", 0)):
        assert math.isnan(expr.evaluate(expr.parse(src), {"x": float(x)}))
    t = time.monotonic()
    assert math.isnan(expr.evaluate(expr.parse("9^9^9^9"), {"x": 0.0}))
    assert time.monotonic() - t < 0.5
    with pytest.raises(expr.ExprError):
        expr.parse("(" * 100 + "x" + ")" * 100)


# -- plot -------------------------------------------------------------------------------------------


def test_a_plot_reads_functions_constants_points_shades_and_lines():
    v, problems = parse_plot(
        'k = 0.5\nf(x) = k x^2  "half square"\nf\'(x) = x dashed\npoint a = (1, f(1)) "here"\n(2, 3)\nshade f from 0 to 2\nshade f from -1 to 0\nvline k "k"\nh(t) = 2t',
        {"x": "-3..3"},
    )
    assert not problems
    assert [(i["id"], i["kind"]) for i in v["items"]] == [
        ("f", "fn"),
        ("f-prime", "fn"),
        ("a", "point"),
        ("point-2", "point"),
        ("shade-f", "shade"),
        ("shade-f-2", "shade"),
        ("vline-1", "vline"),
        ("h", "fn"),
    ]
    a = v["items"][2]
    assert (a["x"], a["y"], a["on"]) == (1, 0.5, "f")
    assert v["items"][0]["ys"][0] == pytest.approx(4.5) and v["items"][1]["dashed"]
    assert v["x"] == [-3, 3] and v["fit"]


@pytest.mark.parametrize(
    ("body", "attrs", "says"),
    [
        ("f(x) = x", {}, "no x range"),
        ("f(x) = x", {"x": "time"}, 'xlabel="..."'),
        ("f(x) = x\nx: -3..3", {"x": "-1..1"}, "in the info string"),
        ("y = x^2", {"x": "-1..1"}, "name the function"),
        ("read: 3, 4", {"x": "-1..1"}, "```chart"),
        ("f(x) = x\nf(x) = 2x", {"x": "-1..1"}, "already a name"),
        ("sin(x) = 2", {"x": "-1..1"}, "reserved"),
        ("shade g from 0 to 1", {"x": "-1..1"}, "no function `g`"),
    ],
)
def test_plot_mistakes_come_back_with_the_fix(body, attrs, says):
    _, problems = parse_plot(body, attrs)
    assert any(says in p for p in problems), problems


def test_plot_steps_share_one_y_range_and_need_one_x_range(build):
    md = "## P {#p}\n\n```plot step x=-3..3\nf(x) = x\n```\n```plot step x=-3..3\nf(x) = 3x\n```\n\nThe [line](#f) gets [steeper](#step-2).\n"
    w, b = build(f"# T\n\nOpening.\n\n{md}")
    s = w.slides[-1]
    a, c = s.visual["steps"]
    assert a["y"] == c["y"] and a["y"][1] > 8 and not [i for i in b.issues if i.severity == "error"]
    _, b = build(f"# T\n\nOpening.\n\n{md.replace('x=-3..3', 'x=-1..1', 1)}")
    assert any("first version's x range" in i for i in b.issues)


# -- memory -----------------------------------------------------------------------------------------


def test_memory_reads_variables_frames_objects_arrays_and_cell_pointers():
    v, problems = parse_memory(
        "head -> a\nsize: 2\nlo -> arr[1]\nframe f\n  n = 3\nframe f\n  n = 2\na: Node {val: 1, next -> b}\nb: {val: 2, next: -> null}\narr: [1, -> a, _]\nt: (1, 2)",
        {"flags": []},
    )
    assert not problems
    assert [x["id"] for x in v["vars"]] == ["head", "size", "lo", "f.n", "f-2.n"]
    lo = next(x for x in v["vars"] if x["name"] == "lo")
    assert lo["cell"] == ["arr", "1"]
    objs = {o["id"]: o for o in v["objects"]}
    assert objs["b"]["slots"][1]["null"] and objs["arr"]["slots"][1]["ptr"] == "a" and objs["arr"]["slots"][2]["text"] == ""
    assert objs["t"]["type"] == "tuple" and objs["t"]["kind"] == "arr"
    assert v["part_of"]["a.next"] == "a" and v["part_of"]["f-2.n"] == "f-2"
    assert objs["b"]["x"] > objs["a"]["x"]  # a list reads left to right


@pytest.mark.parametrize(
    ("body", "says"),
    [
        ("a: {next: b}\nb: {v: 1}", "a pointer to that object is `next -> b`"),
        ("head = a\na: {v: 1}", "`head -> a`"),
        ("buckets: [_, a]\na: {v: 1}", "a cell pointing at that object is written `-> a`"),
        ("p -> zz\na: {v: 1}", "not an object here"),
        ("p -> a.9\na: [1, 2]", "`a` has 0, 1"),
        ("a: {name: Smith, John}", "quote text that has a comma"),
        ("a: {v: 1}\na: {v: 2}", "defined twice"),
        ("a = 1\na: {v: 1}", "both a variable"),
        ("x.y: {v: 1}", "the dot separates"),
    ],
)
def test_memory_mistakes_come_back_with_the_fix(body, says):
    _, problems = parse_memory(body, {"flags": []})
    assert any(says in p for p in problems), problems


def test_a_tree_stacks_down_and_a_lone_right_child_stays_right():
    v, _ = parse_memory("root -> r\nr: {v: 5, left -> null, right -> c}\nc: {v: 8, left -> null, right -> null}", {"flags": ["TB"]})
    r, c = (next(o for o in v["objects"] if o["id"] == k) for k in ("r", "c"))
    assert c["y"] > r["y"] and c["x"] > r["x"]


def test_memory_versions_keep_objects_still_and_mark_changes(build):
    one = "head -> n1\nn1: {v: 1, next -> n2}\nn2: {v: 2, next -> null}"
    two = "head -> n2\nn1: {v: 1, next -> null}\nn2: {v: 2, next -> n1}\nn3: {v: 3, next -> null}"
    md = f"## M {{#m}}\n\n```memory step\n{one}\n```\n```memory step\n{two}\n```\n\nIt starts at [the head](#head); [reversed](#step-2), [n2](#n2.next) points back.\n"
    w, b = build(f"# T\n\nOpening.\n\n{md}")
    a, c = w.slides[-1].visual["steps"]
    pa, pc = ({o["id"]: (o["x"], o["y"]) for o in st["objects"]} for st in (a, c))
    assert pa["n1"] == pc["n1"] and pa["n2"] == pc["n2"] and "n3" in pc
    assert set(c["changed"]) == {"head", "n1.next", "n2.next"} and a["box"] == c["box"]
    assert not next(o for o in c["objects"] if o["id"] == "n3")["dim"]  # never reached, so not left behind
    three = "head -> n2\nn1: {v: 1, next -> null}\nn2: {v: 2, next -> null}"
    md3 = md.replace("\n\nIt starts", f"\n```memory step\n{three}\n```\n\nIt starts")
    w, _ = build(f"# T\n\nOpening.\n\n{md3}")
    last = w.slides[-1].visual["steps"][-1]
    assert next(o for o in last["objects"] if o["id"] == "n1")["dim"]  # unlinked: reached before, not now
    assert not [i for i in b.issues if i.severity == "error"], b.issues


def test_code_beside_a_memory_picture_in_steps(build):
    md = (
        "## Pair {#p .pair}\n\n```python\nhead = head.next\n```\n\n"
        "```memory step\nhead -> a\na: {v: 1, next -> b}\nb: {v: 2, next -> null}\n```\n"
        "```memory step\nhead -> b\na: {v: 1, next -> b}\nb: {v: 2, next -> null}\n```\n\n"
        "The [line](#L1) moves [the head](#head) [along](#step-2).\n"
    )
    w, b = build(f"# T\n\nOpening.\n\n{md}")
    s = w.slides[-1]
    assert s.kind == "code" and s.pair["kind"] == "memory" and len(s.pair["visual"]["steps"]) == 2
    cues = s.says[-1].cues
    assert [(c["kind"], c.get("pane")) for c in cues] == [("lines", None), ("item", 1), ("step", 1)], cues
    assert not [i for i in b.issues if i.severity == "error"], b.issues


def test_memory_cues_by_cell_and_bare_frame_variable(build):
    md = "## M {#m}\n\n```memory\nframe f\n  n = 3\nframe g\n  k = 1\narr: [1, 2]\n```\n\nThe [cell](#arr[1]), [n](#n) and [k](#g.k).\n"
    w, b = build(f"# T\n\nOpening.\n\n{md}")
    assert [c.get("id") for c in w.slides[-1].says[-1].cues] == ["arr.1", "f.n", "g.k"]
    assert not [i for i in b.issues if i.severity == "error"], b.issues


# -- trace ------------------------------------------------------------------------------------------

CODE = "```python\ndef total(xs):\n    t = 0\n    for x in xs:\n        t += x\n    return t\n```\n"
TRACE = "```trace\nL1  xs = [3, 5]\nL2  t = 0\nL3  x = 3\nL4  t = 3\nL3  x = 5\nL4  t = 8\nL5  return: 8\n```\n"


def test_a_trace_snapshots_every_moment():
    moments, problems = parse_trace(
        "L1 xs = [3, 5]\n2: t = 0\n4 t = 3; print: 3; done\nL5 return: 3",
        "def f(xs):\n    t = 0\n    for x in xs:\n        t += x\n    return t",
        1,
    )
    assert not problems
    assert [m["line"] for m in moments] == [1, 2, 4, 5]
    assert moments[2]["vars"] == [["xs", "[3, 5]"], ["t", "3"]] and moments[2]["out"] == ["3; done"] and moments[2]["changed"] == ["t"]
    assert moments[3]["ret"] == "3" and moments[3]["changed"] == []


@pytest.mark.parametrize(
    ("body", "says"),
    [
        ("L4  t += 3", "write the new value"),
        ("L4  y = 2", "does not mention `y`"),
        ("L9  t = 1", "outside 1-5"),
        ("t = 1", "does not start with a line number"),
        ('L2  t = "open', "open"),
        ("L1  xs = [3, 5]\nL1  xs[0] = 9", "write the whole value"),
    ],
)
def test_trace_mistakes_come_back_with_the_fix(body, says):
    _, problems = parse_trace(body, "def total(xs):\n    t = 0\n    for x in xs:\n        t += x\n    return t", 1)
    assert any(says in p for p in problems), problems


def test_trace_cues_check_their_moment_by_text(build):
    md = f'## L {{#l}}\n\n{CODE}{TRACE}\nIt starts [at zero](#trace-2), then [t is 8](#trace-5 "t = 8"), and [returns](#trace "return").\n'
    w, b = build(f"# T\n\nOpening.\n\n{md}")
    s = w.slides[-1]
    assert s.kind == "code" and len(s.visual["trace"]) == 7
    assert [(c["kind"], c["n"], c["line"]) for c in s.says[-1].cues] == [("trace", 2, 2), ("trace", 6, 4), ("trace", 7, 5)]
    assert any("pointed at moment 6" in i for i in b.issues)


def test_trace_placement_and_cue_problems(build):
    w, b = build(f"# T\n\nOpening.\n\n## L {{#l}}\n\n{TRACE}\nNothing traced.\n")
    assert any("goes with the code it traces" in i for i in b.issues)
    w, b = build(f"# T\n\nOpening.\n\n## L {{#l .side}}\n\n> point\n\n{CODE}{TRACE}\nSide.\n")
    assert any(i.code == "trace_layout" for i in b.issues) and w.slides[-1].visual.get("trace") and "side" not in w.slides[-1].classes
    w, b = build(f"# T\n\nOpening.\n\n## L {{#l .pair}}\n\n{CODE}{TRACE}\nPair.\n")
    assert any("`.pair`" in i for i in b.issues)
    w, b = build(f"# T\n\nOpening.\n\n## L {{#l}}\n\n{CODE}{TRACE}\nA [step](#step-2) and [too far](#trace-9).\n")
    assert any("its moments are `#trace-1` to `#trace-7`" in i for i in b.issues)
    assert any("has 7 moments" in i for i in b.issues)
    w, b = build(f"# T\n\nOpening.\n\n## L {{#l}}\n\n{CODE}{TRACE}\nNo trace cue here, only [a line](#L2).\n")
    b.check_all()
    assert any(i.code == "trace_unused" for i in b.issues)


# -- ELK --------------------------------------------------------------------------------------------


def test_elk_on_a_flowchart_and_ignored_elsewhere(build):
    w, b = build("# T\n\nOpening.\n\n## F {#f}\n\n```mermaid elk\nflowchart LR\n  a --> b\n```\n\nThe [start](#a).\n")
    assert w.slides[-1].visual["layout"] == "elk" and not b.issues
    w, b = build("# T\n\nOpening.\n\n## S {#s}\n\n```mermaid elk\nsequenceDiagram\n  a->>b: hi\n```\n\nThe [call](#a).\n")
    assert "layout" not in w.slides[-1].visual and any(i.code == "diagram_option" for i in b.issues)


def test_a_list_halfway_through_a_reversal_stays_in_a_row():
    v, _ = parse_memory(
        "prev -> n1\ncurr -> n2\nnxt -> n3\nn1: {v: 1, next -> null}\nn2: {v: 2, next -> n1}\nn3: {v: 3, next -> null}", {"flags": []}
    )
    ys = {o["y"] for o in v["objects"]}
    xs = [o["x"] for o in v["objects"]]
    assert len(ys) == 1 and xs == sorted(xs)


# -- bounds and geometry (from the implementation review) -------------------------------------------


def test_functions_calling_each_other_stay_cheap_or_are_refused():
    same = ["f0(x) = x"] + [f"f{i}(x) = " + "+".join([f"f{i - 1}(x)"] * 20) for i in range(1, 6)]
    t = time.monotonic()
    _, problems = parse_plot("\n".join(same), {"x": "0..1"})
    assert not problems and time.monotonic() - t < 1  # remembered by argument
    spread = ["f0(x) = x"] + [f"f{i}(x) = " + "+".join(f"f{i - 1}(x+{k})" for k in range(20)) for i in range(1, 6)]
    t = time.monotonic()
    _, problems = parse_plot("\n".join(spread), {"x": "0..1"})
    assert time.monotonic() - t < 3 and any("call each other too often" in p for p in problems)
    _, problems = parse_plot("g(x) = " + "+".join(["x"] * 190), {"x": "0..1"})
    assert any("too long to work out" in p for p in problems)


def test_ranges_and_values_a_plot_cannot_show_are_reported():
    for x in ("0..1e-12", "1e16..10000000000000004"):
        assert any("too large or too narrow" in p for p in parse_plot("f(x) = x", {"x": x})[1])
    v, problems = parse_plot("f(x) = 0.0000001 x\nh(x) = ln(x)\nvline 99\nk(x) = 2 3", {"x": "1..3"})
    assert v["items"][0]["ys"][0] == pytest.approx(1e-7)
    assert any("`h` has no value" in p for p in problems) is False  # ln is defined on 1..3
    assert any("outside the x range" in p for p in problems) and any("put `*` between" in p for p in problems)
    assert any("`h` has no value" in p for p in parse_plot("h(x) = ln(x)", {"x": "-3..-1"})[1])


def test_deeply_nested_json_is_read_without_recursing_forever():
    from showntell.paths import key_lines

    assert len(key_lines("[" * 1200 + "1" + "]" * 1200, "json")) <= 64
    t = time.monotonic()
    key_lines("a" + " " * 60000 + "b: 1", "yaml")
    assert time.monotonic() - t < 0.5


def _boxes(v: dict) -> list[tuple[str, float, float, float, float]]:
    out = [(o["id"], o["x"], o["y"] - o["top"], o["x"] + o["w"], o["y"] + o["h"] + o["bottom"]) for o in v["objects"]]
    out += [(f["id"], f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"]) for f in v["frames"]]
    return out


def _overlaps(v: dict) -> list[tuple[str, str]]:
    bs = _boxes(v)
    return [(a[0], b[0]) for i, a in enumerate(bs) for b in bs[i + 1 :] if a[1] < b[3] and b[1] < a[3] and a[2] < b[4] and b[2] < a[4]]


@pytest.mark.parametrize(
    "body",
    [
        "head -> a\na: {v: 1, prev -> null, next -> b}\nb: {v: 2, prev -> a, next -> c}\nc: {v: 3, prev -> b, next -> null}",
        "root -> r\nr: {v: 5, left -> a, right -> b}\na: {v: 3, left -> null, right -> c}\nb: {v: 8, left -> null, right -> null}\nc: {v: 4, left -> null, right -> null}",
        'buckets: [-> a, -> null, -> b, -> null]\na: Entry {k: "x", next -> c}\nb: Entry {k: "y", next -> null}\nc: Entry {k: "z", next -> null}',
        "lo -> arr.0\nmid -> arr.0\nhi -> arr.3\narr: [1, 2, 3, 4]\nother: [5, 6]",
        "frame outer\n  a = 1\n  b = 2\n  c = 3\n  d = 4\n  e = 5\n  f = 6\nframe inner\n  g = 7\nn: {v: 1}",
    ],
)
def test_memory_pictures_never_overlap(body):
    for flags in ([], ["TB"]):
        v, problems = parse_memory(body, {"flags": flags})
        assert not problems and not _overlaps(v), (flags, _overlaps(v))
        for f in v["frames"]:  # a frame holds its variables
            inside = [x for x in v["vars"] if x["frame"] == f["id"]]
            assert all(f["y"] < x["y"] and x["y"] + x["h"] <= f["y"] + f["h"] for x in inside)


def test_memory_versions_that_grow_never_overlap(build):
    one = "head -> n1\nn1: {v: 1, next -> n2}\nn2: {v: 2, next -> null}"
    two = 'head -> n1\nn1: {v: "hello, world", next -> n2}\nn2: {v: 2, next -> n3}\nn3: {v: [1, 2, 3], next -> null}'
    w, _ = build(f"# T\n\nOpening.\n\n## M {{#m}}\n\n```memory step\n{one}\n```\n```memory step\n{two}\n```\n\nA [step](#step-2).\n")
    for st in w.slides[-1].visual["steps"]:
        assert not _overlaps(st), _overlaps(st)
    a, b = w.slides[-1].visual["steps"]
    assert a["objects"][0]["w"] == b["objects"][0]["w"]  # one size across versions


def test_trace_quoted_text_matches_whole_values():
    moments, problems = parse_trace('L1 t = 0\nL1 t = 30\nL1 t = 3\nL1 msg = "a == b"', 't = 0; msg = ""', 1)
    from showntell.trace import find_moment

    assert find_moment(moments, "t = 3", 0, str.lower) == 3 and not [p for p in problems if "copies the code" in p]


def test_cell_pointers_go_only_into_a_row_of_cells():
    _, problems = parse_memory("cur -> a.val\na: {key: 7, val: 1}", {"flags": []})
    assert any("point at the object instead" in p for p in problems)


def test_steps_on_both_sides_of_a_pair_are_reported(build):
    md = (
        "## P {#p .pair}\n\n```python step\na = 1\n```\n```python step\na = 2\n```\n\n"
        "```memory step\na = 1\n```\n```memory step\na = 2\n```\n\nIt [changes](#step-2).\n"
    )
    _, b = build(f"# T\n\nOpening.\n\n{md}")
    assert any("both visuals" in i for i in b.issues)


def test_elk_asked_for_by_the_older_renderer_setting():
    from showntell.mermaid import asks_elk

    assert asks_elk('%%{init: {"flowchart": {"defaultRenderer": "elk"}}}%%\nflowchart LR\n  a --> b')


@pytest.mark.parametrize(
    ("body", "says"),
    [("a: [->, 1]", "points at nothing"), ("frame f\n  o: {v: 1}", "inside frame"), ("visual = 2", "reserved")],
)
def test_more_memory_slips_are_reported(body, says):
    assert any(says in p for p in parse_memory(body, {"flags": []})[1])


def test_a_tiny_fitted_range_keeps_its_height():
    v, _ = parse_plot("f(x) = 0.0000001 x", {"x": "-3..3"})
    assert v["y"][1] - v["y"][0] > 5e-7


def test_one_budget_covers_constants_and_every_block_in_a_call():
    lines = ["f0(x) = x"] + [f"f{i}(x) = " + "+".join(f"f{i - 1}({k}x+1)" for k in range(3, 33)) for i in range(1, 5)]
    lines += [f"k{j} = f4(1.{j})" for j in range(12)]
    token = expr.start_call()
    try:
        t = time.monotonic()
        for _ in range(5):
            parse_plot("\n".join(lines), {"x": "0..1"})
        assert time.monotonic() - t < 3
    finally:
        expr.end_call(token)


def test_objects_nested_in_objects_are_read_without_recursing():
    from showntell.paths import key_lines

    for code, lang in (
        ("{" * 1200 + "1" + "}" * 1200, "json"),
        ("[" + "{" * 1200, "json"),
        ("a = " + "{b = " * 3000 + "1" + "}" * 3000, "toml"),
    ):
        t = time.monotonic()
        key_lines(code, lang)
        assert time.monotonic() - t < 0.5


def test_plain_records_stand_in_a_column_and_flat_curves_keep_a_range():
    v, _ = parse_memory("x = 1\n" + "\n".join(f"o{i}: {{v: {i}}}" for i in range(8)), {"flags": []})
    assert len({o["x"] for o in v["objects"]}) == 1
    for f in ("f(x) = 100000000", "f(x) = 3", "f(x) = 0.0000001 x"):
        y = parse_plot(f, {"x": "0..10"})[0]["y"]
        assert y[1] > y[0], (f, y)
