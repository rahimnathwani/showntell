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
    assert next(o for o in c["objects"] if o["id"] == "n3")["dim"]
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
    assert any("`.side`" in i for i in b.issues)
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
