"""```memory: variables, objects and the pointers between them, as a data-structure diagram;
`memory step` blocks are versions where objects stay put, arrows move and changed values glow.

    head -> a                       a variable holding a pointer (`head = -> a` is the same)
    size = 3                        a variable holding a value (`size: 3` too)
    frame fact                      a call's variables, indented below it; first written is outermost
      n = 3
    a: Node {value: 1, next -> b}   an object: an optional type, then fields
    b: {value: 2, next: -> c}       `-> id` is a pointer wherever a value goes
    arr: [10, 20, -> c, _]          an array (indexes drawn under it); `_` is an empty cell
    lo -> arr.0                     a pointer to a cell, drawn as a marker under it
    c: Node {value: 3, next -> null}

Values are shown as written. A bare word that names an object (`next: b`) is refused: it would
show the text "b" where an arrow was meant. Layout is computed here, with the player's monospace
metrics, so every version shares one picture: objects are placed from the first version (columns
by distance from the variables, or rows with `memory TB`), a new object beside the first thing
that points at it, and an object nothing points at any more stays where it was, dimmed.
"""

from __future__ import annotations

import re

from .base import Kind

FONT = 22
CH = 13.2  # JetBrains Mono's advance at FONT px
ROW = 44
PAD = 12
GAP_X = 70  # between columns of objects (LR) or between siblings (TB)
GAP_Y = 34
CAPTION = 24  # room above an object for its type
INDEX = 22  # room under an array for its indexes
MARK = 34  # room under a cell for each pointer marker
MAX_OBJECTS = 16
MAX_CELLS = 40
MAX_VALUE = 24
NULLS = {"null", "none", "nil", "nullptr", "undefined"}
ID = r"[A-Za-z_]\w*"
FRAME = re.compile(rf"^frame\s+({ID})\s*:?\s*$")
PTR = re.compile(rf"^({ID})\s*->\s*(\S+)$")
VAR = re.compile(rf"^({ID})\s*(?:=|:)\s*(.*)$")
RESERVED = {"visual", "slide"}


def _split(text: str, sep: str = ",") -> list[str] | None:
    """Pieces at top-level `sep`, outside quotes and brackets; None if something is left open."""
    out, cur, depth, quote = [], "", 0, ""
    for ch in text:
        if quote:
            cur += ch
            quote = "" if ch == quote else quote
            continue
        if ch in "\"'`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append(cur.strip())
            cur = ""
            continue
        cur += ch
    if quote or depth:
        return None
    out.append(cur.strip())
    return out


def _body(rest: str) -> tuple[str, str, str] | None:
    """(type caption, opening bracket, inside) when `rest` ends in a bracket group: `Node {…}`."""
    rest = rest.strip()
    if not rest or rest[-1] not in "]})":
        return None
    close = rest[-1]
    open_ = {"]": "[", "}": "{", ")": "("}[close]
    depth, quote = 0, ""
    for i in range(len(rest) - 1, -1, -1):
        ch = rest[i]
        if quote:
            quote = "" if ch == quote else quote
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "]})":
            depth += 1
        elif ch in "[{(":
            depth -= 1
            if depth == 0:
                if ch != open_:
                    return None
                caption = rest[:i].strip()
                if caption and not re.fullmatch(r"[\w<>\[\]., ]+", caption):
                    return None
                return caption, ch, rest[i + 1 : -1]
    return None


def _value(text: str) -> dict:
    """A value: `-> id`, `-> arr.2`, `-> null`, or text shown as written."""
    t = text.strip()
    if t.startswith("->"):
        target = t[2:].strip()
        if not target:
            return {"text": "", "empty_ptr": True}
        if target.lower() in NULLS:
            return {"text": target, "null": True}
        m = re.fullmatch(rf"({ID})(?:\.(\w+)|\[\s*(\w+)\s*\])", target)
        if m:
            return {"text": target, "cell": [m.group(1), m.group(2) or m.group(3)]}
        return {"text": target, "ptr": target}
    return {"text": "" if t == "_" else t}


def parse_memory(body: str, attrs: dict) -> tuple[dict, list[str]]:
    problems: list[str] = []
    flags = [f.upper() for f in attrs.get("flags", [])]
    direction = "TB" if "TB" in flags or "TD" in flags or str(attrs.get("dir", "")).upper() in ("TB", "TD") else "LR"
    frames: list[dict] = []
    variables: list[dict] = []
    objects: list[dict] = []
    frame: dict | None = None
    for raw in body.split("\n"):
        line = re.sub(r"(^|\s)//.*$", "", raw).rstrip()
        if not line.strip():
            continue
        indented = line[:1].isspace()
        text = line.strip()
        if m := FRAME.match(text):
            name = m.group(1)
            k = sum(1 for f in frames if f["name"] == name)
            frame = {"id": name if not k else f"{name}-{k + 1}", "name": name}
            frames.append(frame)
            continue
        if not indented:
            frame = None
        elif frame is not None and (m := re.match(rf"^({ID})\s*:\s*(.*)$", text)) and _body(m.group(2)):
            problems.append(
                f"`{m.group(1)}` is written as an object inside frame `{frame['name']}`; objects go unindented, after the frames, and a frame's variable points at one (`{m.group(1)}_ref -> {m.group(1)}`)"
            )
            continue
        if frame is None and (m := re.match(rf"^({ID}(?:\.\w+)*)\s*:\s*(.*)$", text)) and (b := _body(m.group(2))):
            oid = m.group(1)
            if "." in oid:
                problems.append(
                    f"`{oid}`: an object's name is letters, digits and `_`; the dot separates an object from its field (`#a.next`)"
                )
                continue
            caption, bracket, inside = b
            obj = _object(oid, caption, bracket, inside, problems)
            if obj:
                objects.append(obj)
            continue
        if m := PTR.match(text):
            name, val = m.group(1), _value("-> " + m.group(2))
        elif m := VAR.match(text):
            name, val = m.group(1), _value(m.group(2))
        else:
            problems.append(
                f"`{text[:50]}` is not a variable (`size = 3`, `head -> a`), an object (`a: Node {{value: 1, next -> b}}`), an array (`arr: [1, 2, 3]`) or `frame name`"
            )
            continue
        if name in RESERVED:
            problems.append(f"`{name}` is a reserved name; call the variable something else")
            continue
        if val.get("empty_ptr"):
            problems.append(f"`{name}` points at nothing; write `{name} -> null` or name the object")
            continue
        if len(val["text"]) > MAX_VALUE and not (val.get("ptr") or val.get("cell")):
            problems.append(f"`{name}` holds {len(val['text'])} characters; a box shows up to {MAX_VALUE}: shorten it")
            val["text"] = val["text"][: MAX_VALUE - 1] + "…"
        vid = f"{frame['id']}.{name}" if frame else name
        if any(v["id"] == vid for v in variables):
            problems.append(f"`{name}` is set twice{' in frame ' + frame['name'] if frame else ''}; each version shows one value")
            continue
        variables.append({"id": vid, "name": name, "frame": frame["id"] if frame else None, **val})
    _check_refs(variables, objects, frames, problems)
    cells = sum(len(o["slots"]) for o in objects)
    if len(objects) > MAX_OBJECTS or cells > MAX_CELLS:
        problems.append(
            f"it has {len(objects)} objects and {cells} fields and cells; a slide reads well up to {MAX_OBJECTS} and {MAX_CELLS}: show the part the narration is about"
        )
    version = {"dir": direction, "frames": frames, "vars": variables, "objects": objects}
    _layout([version])
    vis = {**version, "ids": _ids(version), "items": _items(version), "part_of": _part_of(version)}
    return vis, problems


def _object(oid: str, caption: str, bracket: str, inside: str, problems: list[str]) -> dict | None:
    pieces = _split(inside) if inside.strip() else []
    if pieces is None:
        problems.append(f"`{oid}` leaves a quote or bracket open")
        return None
    slots = []
    if bracket == "{":
        for p in pieces:
            if not p:
                continue
            if m := PTR.match(p):
                name, val = m.group(1), _value("-> " + m.group(2))
            elif m := re.match(r"^(\"[^\"]*\"|'[^']*'|[\w$-]+)\s*[:=]\s*(.*)$", p):
                name, val = m.group(1), _value(m.group(2))
            else:
                problems.append(
                    f"`{oid}`: field `{p[:30]}` has no value; write `name: value` or `name -> id`, and quote text that has a comma"
                )
                continue
            key = name.strip("\"'")
            if any(x["id"] == f"{oid}.{key}" for x in slots):
                problems.append(f"`{oid}` has the field `{key}` twice")
                continue
            if val.get("empty_ptr"):
                problems.append(f"`{oid}.{key}` points at nothing; write `{key} -> null` or name the object")
                val = {"text": ""}
            slots.append({"id": f"{oid}.{key}", "label": name, **val})
        kind = "obj"
    else:
        for i, p in enumerate(pieces):
            val = _value(p)
            if val.pop("empty_ptr", None):
                problems.append(f"`{oid}.{i}` points at nothing; write `-> null` or name the object")
            slots.append({"id": f"{oid}.{i}", "label": str(i), **val})
        kind = "arr"
        if bracket == "(" and not caption:
            caption = "tuple"
    for s in slots:
        if len(s["text"]) > MAX_VALUE and not (s.get("ptr") or s.get("cell")):
            problems.append(f"`{s['id']}` holds {len(s['text'])} characters; a box shows up to {MAX_VALUE}: shorten it")
            s["text"] = s["text"][: MAX_VALUE - 1] + "…"
    # An array that mostly holds pointers (hash buckets) stands as a column, so its arrows run side by side.
    column = kind == "arr" and len(slots) > 1 and sum(1 for s in slots if s.get("ptr") or s.get("null")) * 2 > len(slots)
    return {"id": oid, "type": caption, "kind": kind, "column": column, "slots": slots}


def _check_refs(variables: list[dict], objects: list[dict], frames: list[dict], problems: list[str]) -> None:
    ids = {}
    for o in objects:
        if o["id"] in ids or o["id"] in RESERVED:
            problems.append(f"`{o['id']}` is defined twice" if o["id"] in ids else f"`{o['id']}` is a reserved name")
        ids[o["id"]] = o
    names = {v["name"] for v in variables if not v["frame"]} | {f["id"] for f in frames}
    for n in sorted(names & set(ids)):
        problems.append(f"`{n}` is both a variable or frame and an object; both would be `#{n}`")
    holders = [(v["id"], v) for v in variables] + [(s["id"], s) for o in objects for s in o["slots"]]
    cells = {s["id"] for o in objects if o["kind"] == "arr" for s in o["slots"]}
    for where, val in holders:
        if val.get("ptr") and val["ptr"] not in ids:
            problems.append(f"`{where}` points at `{val['ptr']}`, which is not an object here; objects: {', '.join(ids) or 'none'}")
            val.pop("ptr")
        elif val.get("cell") and val["cell"][0] in ids and (ids[val["cell"][0]]["kind"] != "arr" or ids[val["cell"][0]]["column"]):
            obj = val["cell"][0]
            what = "an object's field" if ids[obj]["kind"] == "obj" else "an array drawn as a column"
            problems.append(f"`{where}` points into {what} (`{obj}.{val['cell'][1]}`); point at the object instead: `-> {obj}`")
            val["ptr"] = obj
            val.pop("cell")
        elif val.get("cell"):
            obj, sub = val["cell"]
            if obj not in ids or not any(s["id"] == f"{obj}.{sub}" for s in ids[obj]["slots"]):
                have = ", ".join(s["label"] for s in ids[obj]["slots"]) if obj in ids else ""
                problems.append(f"`{where}` points at `{obj}.{sub}`, which is not here" + (f"; `{obj}` has {have}" if have else ""))
                val.pop("cell")
        elif not (val.get("ptr") or val.get("cell") or val.get("null")) and val["text"] in ids:
            if where in cells:
                problems.append(f"`{where}` shows the text `{val['text']}`; a cell pointing at that object is written `-> {val['text']}`")
            else:
                arrow = f"{where.rsplit('.', 1)[-1]} -> {val['text']}"
                problems.append(f"`{where}` shows the text `{val['text']}`; a pointer to that object is `{arrow}`")


def _ids(v: dict) -> list[str]:
    """The parts a step reveal shows in order: variables (frames with theirs), then objects."""
    out = []
    for var in v["vars"]:
        if var["frame"] and var["frame"] not in out:
            out.append(var["frame"])
        if not var["frame"]:
            out.append(var["id"])
    for f in v["frames"]:
        if f["id"] not in out:
            out.append(f["id"])
    return out + [o["id"] for o in v["objects"]]


def _items(v: dict) -> list[dict]:
    """Every part a cue can name, with what it is called."""
    out = [{"id": f["id"], "label": f"frame {f['name']}"} for f in v["frames"]]
    out += [{"id": var["id"], "label": var["name"]} for var in v["vars"]]
    for o in v["objects"]:
        out.append({"id": o["id"], "label": o["id"]})
        out += [{"id": s["id"], "label": s["id"]} for s in o["slots"]]
    return out


# -- layout -----------------------------------------------------------------------------------------


def _w(text: str) -> float:
    return len(text) * CH


def _size(o: dict, vw_min: float = 0, lw_min: float = 0) -> None:
    """An object's box and its slots, relative to the box's top left; columns at least as wide as
    given, so an object keeps one size across versions."""
    slots = o["slots"]
    vw = max([_w(s["text"]) if not (s.get("ptr") or s.get("cell")) else 2 * CH for s in slots] + [2 * CH]) + 2 * PAD
    vw = max(vw, vw_min)
    o["vw"] = vw
    if o["kind"] == "obj":
        lw = max([_w(s["label"]) for s in slots] + [CH, lw_min - 2 * PAD]) + 2 * PAD
        for i, s in enumerate(slots):
            s.update(x=lw, y=i * ROW, w=vw, h=ROW, lw=lw)
        o.update(w=lw + vw, h=max(1, len(slots)) * ROW, lw=lw)
    elif o["column"]:
        lw = max(_w(str(len(slots) - 1)), CH, lw_min - 2 * PAD) + 2 * PAD
        for i, s in enumerate(slots):
            s.update(x=lw, y=i * ROW, w=vw, h=ROW, lw=lw)
        o.update(w=lw + vw, h=max(1, len(slots)) * ROW, lw=lw)
    else:
        for i, s in enumerate(slots):
            s.update(x=i * vw, y=0, w=vw, h=ROW)
        o.update(w=max(1, len(slots)) * vw, h=ROW)
    o["top"] = CAPTION if o["type"] else 0  # room above for the caption
    o["bottom"] = INDEX if o["kind"] == "arr" and not o["column"] else 0


def _var_w(var: dict) -> float:
    """A variable's value box: a dot for a pointer, `arr[2]` for a pointer into an array."""
    if var.get("ptr"):
        return 2 * CH + 2 * PAD
    if var.get("cell"):
        return _w(f"{var['cell'][0]}[{var['cell'][1]}]") + 2 * PAD
    return max(_w(var["text"]), 2 * CH) + 2 * PAD


def _stack(v: dict, width: float) -> float:
    """Variables and frames in a column at the left; returns its height."""
    y = 0.0
    rows = []
    by_frame: dict[str | None, list[dict]] = {}
    for var in v["vars"]:
        by_frame.setdefault(var["frame"], []).append(var)
    nw = max([_w(var["name"]) for var in v["vars"]] + [CH]) + PAD
    for key in [None, *[f["id"] for f in v["frames"]]]:
        group = by_frame.get(key, [])
        frame = next((f for f in v["frames"] if f["id"] == key), None)
        if frame:
            frame.update(x=0, y=y, w=width, h=ROW + max(1, len(group)) * (ROW + 8) + PAD)
            y += ROW
        for var in group:
            vw = _var_w(var)
            var.update(x=PAD if frame else 0, y=y, nw=nw, w=vw, h=ROW)
            rows.append(var)
            y += ROW + 8
        if frame:
            y = frame["y"] + frame["h"] + GAP_Y / 2
    return y


def _stack_width(v: dict) -> float:
    nw = max([_w(var["name"]) for var in v["vars"]] + [CH]) + PAD
    vw = max([_var_w(var) for var in v["vars"]] + [0])
    fw = max([_w(f"frame {f['name']}") + 2 * PAD for f in v["frames"]] + [0])
    return max(nw + vw + (2 * PAD if v["frames"] else 0), fw)


def _children(o: dict, objs: dict, tb: bool = True) -> list[str | None]:
    """Objects an object points at, in field order; a null pointer keeps a slot (None) when the
    object has more than one pointer field, so a lone right child stays right."""
    ptrs = [s for s in o["slots"] if s.get("ptr") or s.get("null")]
    out: list[str | None] = []
    for s in ptrs:
        if s.get("ptr") in objs:
            out.append(s["ptr"])
        elif len(ptrs) > 1 and tb:
            out.append(None)
    return out


def _place_tree(v: dict, x0: float, placed: dict[str, tuple[float, float]]) -> None:
    """First version: a forest from what the variables point at, in order, then anything left."""
    objs = {o["id"]: o for o in v["objects"]}
    roots = [var["ptr"] for var in v["vars"] if var.get("ptr") in objs] + [
        var["cell"][0] for var in v["vars"] if var.get("cell") and var["cell"][0] in objs
    ]
    roots += [o["id"] for o in v["objects"]]
    seen: set[str] = set()
    tb = v["dir"] == "TB"
    hole = 3 * CH + 2 * PAD

    def extent(o: dict) -> tuple[float, float]:
        """(along, across): the box along the depth axis and across it, with caption and indexes."""
        return (o["fh"], o["fw"]) if tb else (o["fw"], o["fh"])

    tree: dict[str, list[str | None]] = {}

    def grow(oid: str) -> None:
        kids = []
        for c in _children(objs[oid], objs, tb):
            if c is None:
                kids.append(None)
            elif c not in seen:
                seen.add(c)
                kids.append(c)
        tree[oid] = kids
        for c in kids:
            if c:
                grow(c)

    order = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            order.append(r)
            grow(r)
    span: dict[str, float] = {}

    def measure(oid: str | None) -> float:
        if oid is None:
            return hole
        kids = tree[oid]
        own = extent(objs[oid])[1]
        inner = sum(measure(k) for k in kids) + GAP_Y * max(0, len(kids) - 1) if kids else 0
        if tb:
            inner = sum(measure(k) for k in kids) + GAP_X * max(0, len(kids) - 1) if kids else 0
        span[oid] = max(own, inner)
        return span[oid]

    depth_size: dict[int, float] = {}

    def depths(oid: str | None, d: int) -> None:
        if oid is None:
            return
        depth_size[d] = max(depth_size.get(d, 0), extent(objs[oid])[0])
        for k in tree[oid]:
            depths(k, d + 1)

    for r in order:
        measure(r)
        depths(r, 0)
    gap_d = GAP_Y + 30 if tb else GAP_X
    offset = {0: 0.0}
    for d in range(1, len(depth_size) + 1):
        offset[d] = offset[d - 1] + depth_size.get(d - 1, 0) + gap_d

    def put(oid: str | None, d: int, across: float) -> None:
        if oid is None:
            return
        o = objs[oid]
        own = extent(o)[1]
        start = across + (span[oid] - own) / 2 if tb else across
        if tb:
            placed[oid] = (x0 + start, offset[d] + o["top"])
        else:
            placed[oid] = (x0 + offset[d], start + o["top"])
        kids = tree[oid]
        gap = GAP_X if tb else GAP_Y
        inner = sum(span.get(k, hole) if k else hole for k in kids) + gap * max(0, len(kids) - 1)
        at = across + (span[oid] - inner) / 2 if tb else across
        for k in kids:
            put(k, d + 1, at)
            at += (span[k] if k else hole) + gap

    at = 0.0
    for r in order:
        put(r, 0, at)
        at += span[r] + (GAP_X if tb else GAP_Y)


def _listlike(v: dict) -> bool:
    """Two or more objects, each with at most one pointer field, none an array: a linked list."""
    objs = v["objects"]
    links = sum(1 for o in objs for s in o["slots"] if s.get("ptr"))
    return (
        len(objs) > 1
        and links > 0
        and all(o["kind"] == "obj" and sum(1 for s in o["slots"] if s.get("ptr") or s.get("null")) <= 1 for o in objs)
    )


def _marks(v: dict, oid: str) -> int:
    """How many cell markers stack under the busiest cell of an object."""
    per: dict[str, int] = {}
    for var in v["vars"]:
        if var.get("cell") and var["cell"][0] == oid:
            key = var["cell"][1]
            per[key] = per.get(key, 0) + 1
    return max(per.values(), default=0)


def _free(placed: dict, objs: dict, oid: str, x: float, y: float, tb: bool) -> tuple[float, float]:
    """Where a new object goes near (x, y), moved along until it overlaps nothing placed."""
    o = objs[oid]
    w, h = o["fw"], o["fh"]

    def hits(px: float, py: float) -> bool:
        for other, (qx, qy) in placed.items():
            q = objs.get(other)
            if q is None:
                continue
            if (
                px < qx + q["fw"] + 20
                and qx < px + w + 20
                and py - o["top"] < qy - q["top"] + q["fh"] + 20
                and qy - q["top"] < py - o["top"] + h + 20
            ):
                return True
        return False

    for _ in range(60):
        if not hits(x, y):
            return x, y
        if tb:
            x += GAP_X
        else:
            y += ROW
    return x, y


def _layout(versions: list[dict]) -> None:
    """Positions for every version: the stack at the left, objects placed once and kept."""
    stack_w = max(_stack_width(v) for v in versions)
    x0 = stack_w + GAP_X + 30
    placed: dict[str, tuple[float, float]] = {}
    sized: dict[str, dict] = {}
    # Each object keeps one size across versions (its widest column), and is placed by the room it
    # takes in its largest version, so a value that grows never runs into a neighbour.
    widest: dict[str, tuple[float, float]] = {}
    for v in versions:
        for o in v["objects"]:
            _size(o)
            a, b = widest.get(o["id"], (0.0, 0.0))
            widest[o["id"]] = (max(a, o["vw"]), max(b, o.get("lw", 0)))
    foot: dict[str, tuple[float, float]] = {}
    for v in versions:
        for o in v["objects"]:
            _size(o, *widest[o["id"]])
            a, b = foot.get(o["id"], (0.0, 0.0))
            foot[o["id"]] = (max(a, o["w"]), max(b, o["h"] + o["top"] + o["bottom"] + _marks(v, o["id"]) * MARK))
    for v in versions:
        for o in v["objects"]:
            o["fw"], o["fh"] = foot[o["id"]]
    seen_reach: set[str] = set()
    for k, v in enumerate(versions):
        objs = {o["id"]: o for o in v["objects"]}
        sized.update(objs)
        if k == 0 and v["dir"] == "LR" and _listlike(v):
            # Nodes of a list stay in a row in the order written, whatever points where right now
            # (halfway through a reversal, variables point into the middle and links run back).
            x = x0
            for o in v["objects"]:
                placed[o["id"]] = (x, CAPTION)
                x += o["fw"] + GAP_X
        elif k == 0:
            _place_tree(v, x0, placed)
        else:
            tb = v["dir"] == "TB"
            for o in v["objects"]:
                if o["id"] in placed:
                    continue
                # Beside the first thing that points at it, else after everything.
                src = next((h for h in v["objects"] if any(s.get("ptr") == o["id"] for s in h["slots"]) and h["id"] in placed), None)
                if src:
                    sx, sy = placed[src["id"]]
                    near = (sx, sy - src["top"] + src["fh"] + GAP_Y + 30 + o["top"]) if tb else (sx + src["fw"] + GAP_X, sy)
                else:
                    near = (x0, 0.0 + o["top"])
                placed[o["id"]] = _free(placed, sized, o["id"], *near, tb)
        for o in v["objects"]:
            o["x"], o["y"] = placed[o["id"]]
        _stack(v, stack_w)
        # Reachable from a variable: an object nothing reaches is drawn dimmed.
        reach: set[str] = set()
        todo = [var["ptr"] for var in v["vars"] if var.get("ptr")] + [var["cell"][0] for var in v["vars"] if var.get("cell")]
        while todo:
            oid = todo.pop()
            if oid in reach or oid not in objs:
                continue
            reach.add(oid)
            todo += [s["ptr"] for s in objs[oid]["slots"] if s.get("ptr")] + [s["cell"][0] for s in objs[oid]["slots"] if s.get("cell")]
        # Dimmed: an object something reached in an earlier version that nothing reaches now
        # (a node unlinked, a frame's object left behind), not one that was never pointed at.
        for o in v["objects"]:
            o["dim"] = o["id"] in seen_reach and o["id"] not in reach
        seen_reach |= reach
    # One drawing size for all versions, so the scale holds still between them.
    right = max([x0] + [o["x"] + o["w"] for v in versions for o in v["objects"]] + [stack_w])
    bottom = max(
        [ROW]
        + [o["y"] + o["h"] + o["bottom"] + _marks(v, o["id"]) * MARK for v in versions for o in v["objects"]]
        + [var["y"] + ROW for v in versions for var in v["vars"]]
        + [f["y"] + f["h"] for v in versions for f in v["frames"]]
    )
    top = min([0.0] + [o["y"] - o["top"] for v in versions for o in v["objects"]])
    for v in versions:
        v["box"] = [0, round(top - 10, 1), round(right + 20, 1), round(bottom - top + 20, 1)]


def join_steps(vis: dict) -> list[str]:
    """Versions of one memory picture: laid out together, so objects keep their places, and each
    version marks the values that changed since the one before."""
    steps = vis.get("steps", [])
    if len(steps) < 2:
        return []
    problems = []
    if steps[-1]["dir"] != steps[0]["dir"]:
        problems.append("every `memory step` version uses the first one's direction (LR or TB)")
        steps[-1]["dir"] = steps[0]["dir"]
    _layout(steps)
    for prev, cur in zip(steps, steps[1:], strict=False):
        before = {
            p["id"]: (p["text"], p.get("ptr"), p.get("cell")) for p in prev["vars"] + [s for o in prev["objects"] for s in o["slots"]]
        }
        cur["changed"] = [
            p["id"]
            for p in cur["vars"] + [s for o in cur["objects"] for s in o["slots"]]
            if p["id"] in before and before[p["id"]] != (p["text"], p.get("ptr"), p.get("cell"))
        ]
    for key in ("frames", "vars", "objects", "box"):
        vis[key] = steps[0][key]
    vis["ids"] = list(dict.fromkeys(i for st in steps for i in st["ids"]))
    vis["part_of"] = {k: v for st in steps for k, v in _part_of(st).items()}
    vis["items"] = list({it["id"]: it for st in steps for it in st["items"]}.values())
    return problems


def find(vis: dict, target: str) -> str | None:
    """A part by its id (`head`, `a`, `a.next`, `arr.2`, `fact-2.n`), `arr[2]` for `arr.2`, or a
    variable's bare name when only one frame has it."""
    t = re.sub(r"\[\s*(\w+)\s*\]", r".\1", target.strip())
    every = vis.get("items", []) + [it for st in vis.get("steps", []) for it in st.get("items", [])]
    ids = {it["id"] for it in every}
    if t in ids:
        return t
    tails = {
        i
        for i in ids
        if i.endswith("." + t) and i.split(".", 1)[0] in {f["id"] for st in [vis, *vis.get("steps", [])] for f in st.get("frames", [])}
    }
    return tails.pop() if len(tails) == 1 else None


def labels(vis: dict) -> list[tuple[str, str]]:
    return [(it["id"], it["label"]) for it in vis.get("items", []) if it["label"] and not it["label"].startswith("frame ")]


def _part_of(v: dict) -> dict:
    """A field or cell shows with its object, and a frame's variable with its frame."""
    out = {s["id"]: o["id"] for o in v["objects"] for s in o["slots"]}
    out.update({var["id"]: var["frame"] for var in v["vars"] if var["frame"]})
    return out


KIND = Kind(
    "memory",
    ("memory",),
    parse_memory,
    part=lambda vis: "variable, object, field or cell",
    find=find,
    labels=labels,
    hint="Write variables as `head -> a` or `size = 3`, and objects as `a: Node {value: 1, next -> b}` or `arr: [1, 2, 3]`.",
    steps=("dir", "frames", "vars", "objects", "box", "ids", "items", "changed"),
    join_steps=join_steps,
)
