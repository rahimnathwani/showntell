"""```plot x=-3..3: curves of formulas on axes, with points, shaded areas and vertical lines;
`plot step` blocks are versions that glide into each other (a point walking down a curve, a
tangent turning, a parameter changing).

The builder reads and evaluates every expression itself (expr.py, no `eval`) and sends sampled
values; the player only draws. A value that is undefined (1/0, ln of a negative) is a gap. Numbers
someone measured belong in ```chart; this kind is for formulas.
"""

from __future__ import annotations

import math
import re

from .. import expr
from .base import Finding, Kind

SAMPLES = 241  # per function across the x range; the same in every version, so curves can glide
SHADE_SAMPLES = 81
MAX_FUNCS = 8
MAX_MARKS = 16
NAME = r"[A-Za-z_][A-Za-z_0-9]*'*"
LABEL = re.compile(r'\s+"([^"]*)"\s*$')
DASHED = re.compile(r"\s+dashed\s*$")
FUNC = re.compile(rf"^({NAME})\s*\(\s*([A-Za-z_]\w*)\s*\)\s*=\s*(.+)$")
CONST = re.compile(rf"^({NAME})\s*=\s*(.+)$")
POINT = re.compile(rf"^(?:point\b\s*)?(?:({NAME})\s*(?:=|at)?\s*)?\((.*)\)\s*$")
SHADE = re.compile(rf"^shade\s+({NAME})\s+from\s+(.+?)\s+to\s+(.+)$")
VLINE = re.compile(r"^vline\s+(?:x\s*=\s*)?(.+)$")
RANGE = re.compile(r"^\s*(\S+?)\s*\.\.\s*(\S+?)\s*$")
RESERVED = {"x", "visual", "slide"}
FORMAT_HINT = "the range is `x=-3..3` in the info string, no spaces: ```plot x=-3..3 y=0..10"


def _const(src: str, funcs: dict, consts: dict, what: str, problems: list[str]) -> float | None:
    """A number written as an expression (`-pi`, `2^10`, `f(1)`, `k/2`)."""
    try:
        v = expr.evaluate(expr.parse(src, set(funcs), params=tuple(consts)), dict(consts), funcs)
    except expr.ExprError as e:
        problems.append(f"{what} `{src}`: {e}")
        return None
    if not math.isfinite(v):
        problems.append(f"{what} `{src}` has no value")
        return None
    return v


def _split_args(text: str) -> list[str]:
    """`1, f(1)` -> ['1', 'f(1)']: commas outside parentheses."""
    out, depth, cur = [], 0, ""
    for ch in text:
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
            continue
        depth += (ch == "(") - (ch == ")")
        cur += ch
    out.append(cur)
    return [p.strip() for p in out]


def _range(text: str | None, consts: dict, axis: str, problems: list[str]) -> list[float] | None:
    if not text:
        return None
    m = RANGE.match(text)
    if not m:
        label = f'; label the axis with {axis}label="..."' if not re.search(r"\d", text) else ""
        problems.append(f"`{axis}={text}`: {FORMAT_HINT}{label}")
        return None
    a = _const(m.group(1), {}, consts, f"the {axis} range's start", problems)
    b = _const(m.group(2), {}, consts, f"the {axis} range's end", problems)
    if a is None or b is None:
        return None
    if not a < b:
        problems.append(f"the {axis} range `{text}` must run from smaller to larger")
        return None
    return [a, b]


def _id(name: str) -> str:
    return name.replace("'", "-prime")


def parse_plot(body: str, attrs: dict) -> tuple[dict, list[str]]:
    problems: list[str] = []
    funcs: dict[str, tuple[str, expr.Node]] = {}
    consts: dict[str, float] = {}
    items: list[dict] = []
    lines = [ln.strip() for ln in body.split("\n") if ln.strip() and not ln.strip().startswith(("#", "//"))]
    for line in lines:
        label, dashed = "", False
        if lm := LABEL.search(line):
            label, line = lm.group(1), line[: lm.start()].rstrip()
        if dm := DASHED.search(line):
            dashed, line = True, line[: dm.start()].rstrip()
        if re.match(r"^(x|y)\s*:\s*\S+\s*\.\.", line):
            problems.append(f"`{line}`: {FORMAT_HINT}")
        elif m := FUNC.match(line):
            name, param, src = m.groups()
            if not _fresh(name, funcs, consts, items, problems):
                continue
            try:
                funcs[name] = (param, expr.parse(src, set(funcs), params=(param, *consts)))
            except expr.ExprError as e:
                problems.append(f"`{name}({param}) = {src}`: {e}")
                continue
            items.append({"id": _id(name), "kind": "fn", "name": name, "label": label, "src": src, "dashed": dashed})
        elif (m := POINT.match(line)) and (line.startswith("point") or len(_split_args(m.group(2))) == 2):
            parts = _split_args(m.group(2))
            if len(parts) != 2:
                problems.append(f"`{line}`: a point is `point a = (x, y)`")
                continue
            name = m.group(1) or ""
            if name and not _fresh(name, funcs, consts, items, problems):
                continue
            px = _const(parts[0], funcs, consts, "the point's x", problems)
            py = _const(parts[1], funcs, consts, "the point's y", problems)
            if px is None or py is None:
                continue
            n = sum(1 for it in items if it["kind"] == "point") + 1
            point = {"id": _id(name) if name else f"point-{n}", "kind": "point", "name": name, "label": label, "x": px, "y": py}
            on = _on_curve(parts, funcs, consts)
            if on:
                point["on"] = _id(on)  # between versions it slides along that curve, not across
            items.append(point)
        elif m := SHADE.match(line):
            name = m.group(1)
            if name not in funcs:
                problems.append(f"`shade {name}`: no function `{name}` above; define it first as `{name}(x) = ...`")
                continue
            a = _const(m.group(2), funcs, consts, "the shade's start", problems)
            b = _const(m.group(3), funcs, consts, "the shade's end", problems)
            if a is None or b is None:
                continue
            k = sum(1 for it in items if it.get("of") == _id(name))
            sid = f"shade-{_id(name)}" + (f"-{k + 1}" if k else "")
            items.append(
                {"id": sid, "kind": "shade", "of": _id(name), "fn": name, "name": sid, "label": label, "from": min(a, b), "to": max(a, b)}
            )
        elif m := VLINE.match(line):
            at = _const(m.group(1), funcs, consts, "the vertical line's x", problems)
            if at is None:
                continue
            n = sum(1 for it in items if it["kind"] == "vline") + 1
            items.append({"id": f"vline-{n}", "kind": "vline", "name": "", "label": label, "x": at, "dashed": dashed})
        elif m := CONST.match(line):
            name, src = m.groups()
            if name == "y":
                problems.append(f"`{line}`: name the function: `f(x) = {src}`")
                continue
            if not _fresh(name, funcs, consts, items, problems):
                continue
            v = _const(src, funcs, consts, f"the constant `{name}`", problems)
            if v is not None:
                consts[name] = v
        elif re.match(r"^[^=()]+:\s*-?[\d.]+(\s*,\s*-?[\d.]+)*\s*$", line):
            problems.append(f"`{line}` looks like measured numbers: those go in a ```chart; a plot draws formulas such as `f(x) = x^2`")
        else:
            problems.append(
                f"`{line[:50]}` is not a function (`f(x) = x^2`), a constant (`k = 0.5`), a point (`point a = (1, f(1))`), "
                "a shaded area (`shade f from 0 to 2`) or a vertical line (`vline 2`)"
            )
    xr = _range(attrs.get("x"), consts, "x", problems)
    if xr is None and not attrs.get("x"):
        problems.append(f"it has no x range: {FORMAT_HINT}")
    xr = xr or [-5.0, 5.0]
    for it in items:
        if it["kind"] == "fn":
            param, node = funcs[it["name"]]
            it["ys"] = [_num(expr.evaluate(node, {**consts, param: x}, funcs)) for x in _xs(xr, SAMPLES)]
        elif it["kind"] == "shade":
            param, node = funcs[it.pop("fn")]
            xs = _xs([it["from"], it["to"]], SHADE_SAMPLES)
            it["xs"] = [round(x, 6) for x in xs]
            it["ys"] = [_num(expr.evaluate(node, {**consts, param: x}, funcs)) for x in xs]
    yr = _range(attrs.get("y"), consts, "y", problems) if attrs.get("y") else None
    if not any(it["kind"] == "fn" for it in items) and not problems:
        problems.append("it has no function to draw: add a line like `f(x) = x^2`")
    nf = sum(it["kind"] == "fn" for it in items)
    nm = len(items) - nf
    if nf > MAX_FUNCS or nm > MAX_MARKS:
        problems.append(f"it has {nf} functions and {nm} points, shades and lines; a plot reads well up to {MAX_FUNCS} and {MAX_MARKS}")
    vis = {
        "title": attrs.get("title", ""),
        "xlabel": attrs.get("xlabel", ""),
        "ylabel": attrs.get("ylabel", ""),
        "x": xr,
        "y": yr or fit_y(items),
        "fit": yr is None,  # y fitted to the curves: across all versions once there are steps
        "items": items,
        "ids": [it["id"] for it in items],
    }
    return vis, problems


def _fresh(name: str, funcs: dict, consts: dict, items: list[dict], problems: list[str]) -> bool:
    """Whether a name is free for a new function, point or constant."""
    if name in funcs or name in consts or _id(name) in {it["id"] for it in items}:
        problems.append(f"`{name}` is already a name; give each function, point and constant its own")
        return False
    if name in expr.FUNCS or name in expr.CONSTS or name in RESERVED or name == "log" or re.fullmatch(r"(?i)L\d+|step-?\d+", name):
        problems.append(f"`{name}` is a reserved name; choose another")
        return False
    return True


def _on_curve(parts: list[str], funcs: dict, consts: dict) -> str | None:
    """The function a point sits on when written `(a, f(a))`."""
    try:
        px = expr.parse(parts[0], set(funcs), params=tuple(consts))
        py = expr.parse(parts[1], set(funcs), params=tuple(consts))
    except expr.ExprError:
        return None
    if py[0] == "call" and py[1] in funcs and len(py[2]) == 1 and py[2][0] == px:
        return py[1]
    return None


def _xs(r: list[float], n: int) -> list[float]:
    return [r[0] + (r[1] - r[0]) * i / (n - 1) for i in range(n)]


def _num(v: float) -> float | None:
    return round(v, 6) if math.isfinite(v) and abs(v) < 1e12 else None


def fit_y(items: list[dict]) -> list[float]:
    """A y range holding the curves and points, ignoring spikes (tan, 1/x near 0)."""
    vals = [v for it in items if it["kind"] == "fn" for v in it["ys"] if v is not None]
    pts = [it["y"] for it in items if it["kind"] == "point"]
    vals = sorted(vals + pts)
    if not vals:
        return [-1.0, 1.0]
    lo, hi = vals[0], vals[-1]
    k = len(vals)
    p5, p95 = vals[int(0.05 * (k - 1))], vals[int(0.95 * (k - 1))]
    if p95 > p5 and hi - lo > 3 * (p95 - p5):
        lo, hi = max(lo, p5 - 0.5 * (p95 - p5)), min(hi, p95 + 0.5 * (p95 - p5))
    lo, hi = min([lo, *pts]), max([hi, *pts])
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    pad = 0.08 * (hi - lo)
    return [round(lo - pad, 6), round(hi + pad, 6)]


def join_steps(vis: dict) -> list[str]:
    """Versions of one plot share their axes: the same x range, and a y range fitted once over
    every version (unless a version sets its own), so the axes hold still while things move."""
    steps = vis.get("steps", [])
    if len(steps) < 2:
        return []
    if steps[-1]["x"] != steps[0]["x"]:
        return ["each `plot step` version must use the first version's x range, so the curves can glide; set the same `x=` on every block"]
    fitted = [st for st in steps if st.get("fit")]
    if fitted:
        y = fit_y([it for st in fitted for it in st["items"]])
        for st in fitted:
            st["y"] = y
        if vis.get("fit"):
            vis["y"] = y
    return []


def find(vis: dict, target: str) -> str | None:
    t = target.strip()
    every = vis.get("items", []) + [it for st in vis.get("steps", []) for it in st.get("items", [])]
    ids = {it["id"] for it in every}
    for cand in (t, _id(t)):
        if cand in ids:
            return cand
    low = t.lower()
    hits = {it["id"] for it in every if it.get("label") and it["label"].lower() == low}
    return hits.pop() if len(hits) == 1 else None


def labels(vis: dict) -> list[tuple[str, str]]:
    return [(it["id"], it["label"]) for it in vis.get("items", []) if it.get("label")]


def _check(vis: dict, line: int, limits: dict) -> list[Finding]:
    return []


KIND = Kind(
    "plot",
    ("plot",),
    parse_plot,
    check=_check,
    part=lambda vis: "function, point, shade or line",
    find=find,
    labels=labels,
    hint="Write ```plot x=-3..3, then one `f(x) = ...` line per function and `point a = (1, f(1))` for points.",
    steps=("items", "ids", "x", "y", "fit"),
    join_steps=join_steps,
)
