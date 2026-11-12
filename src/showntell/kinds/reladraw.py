"""```reladraw: a diagram whose boxes are placed by saying where they go relative to each other
(`node api "API" below web`), drawn by the reladraw library in the player, vendored and loaded
only when a slide needs it. Written inline, or read with `file=` from a .reladraw file.

The library lays out and draws; this module only reads what cues and reveals need, best effort:
each `node`'s name and text, in source order. A node is a part cues point at by its name, its
text or the text's slug. The top-level nodes are the parts a step reveal shows in order; a node
inside another (`server.db`) shows with it, and a line with the later of its two ends. Syntax
errors and the library's own findings (a line through a box, texts on top of each other) come
back from the player as problems when the slide is drawn.
"""

from __future__ import annotations

import re

from .base import Finding, Kind, slug

MAX_BYTES = 200_000
MAX_NODES = 60
NAME = re.compile(r"[^\s:,;\"()\[\]]+")


def _statements(body: str) -> list[tuple[int, str]]:
    """(line, text) of each statement: comments removed, indented lines joined to the one above,
    and `\"\"\"` blocks (an icon's SVG, a description) skipped."""
    out: list[tuple[int, str]] = []
    in_block = False
    for n, raw in enumerate(body.split("\n"), 1):
        if in_block:
            if raw.count('"""') % 2:
                in_block = False
            continue
        line = _uncomment(raw)
        if line.count('"""') % 2:
            in_block = True
            line = line[: line.index('"""')]
        if not line.strip():
            continue
        if line[:1].isspace() and out:
            out[-1] = (out[-1][0], out[-1][1] + " " + line.strip())
        else:
            out.append((n, line.strip()))
    return out


def _uncomment(line: str) -> str:
    """The line up to a `//` outside quotes."""
    quoted = False
    i = 0
    while i < len(line):
        c = line[i]
        if c == "\\" and quoted:
            i += 2
            continue
        if c == '"':
            quoted = not quoted
        elif not quoted and line.startswith("//", i):
            return line[:i]
        i += 1
    return line


def _text(rest: str) -> str | None:
    """The quoted text at the start of `rest`, unescaped, with ` / ` line breaks as spaces."""
    m = re.match(r'"((?:[^"\\]|\\.)*)"', rest)
    if not m:
        return None
    t = re.sub(r"\s+/\s+", " ", m.group(1))
    t = re.sub(r"\\(.)", r"\1", t)
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t).strip()  # markup: [text](style) reads as text


def parse_reladraw(body: str, attrs: dict) -> tuple[dict, list[str]]:
    if len(body.encode("utf-8")) > MAX_BYTES:
        return {}, [f"the diagram is over {MAX_BYTES // 1000} KB"]
    if not body.strip():
        return {}, ["the block is empty"]
    items: list[dict] = []
    seen: set[str] = set()
    for _, st in _statements(body):
        m = re.match(r"(?:guide\s+)?node\s+(\S+)\s*(.*)$", st)
        if not m or st.startswith("guide"):
            continue
        name = NAME.match(m.group(1))
        if not name or name.group(0) in seen:
            continue
        nid = name.group(0)
        seen.add(nid)
        text = _text(m.group(2))
        label = text if text else nid.rsplit(".", 1)[-1]
        items.append({"id": nid, "label": label})
    if not items:
        return {}, ['it has no `node` statement, so there is nothing to draw: start with `node a "First"`']
    names = [i["id"] for i in items]
    part_of = {}
    for nid in names:
        top = nid.split(".", 1)[0]
        if top != nid and top in seen:
            part_of[nid] = top
    vis = {
        "source": body.strip("\n"),
        "items": items,
        "ids": [n for n in names if n not in part_of],
        "part_of": part_of,
        "nodes": len(items),
    }
    return vis, []


def find(vis: dict, target: str) -> str | None:
    """Name of the node a cue names: its name, or a unique text or slug of one."""
    t = target.strip()
    names = [i["id"] for i in vis.get("items", [])]
    if t in names:
        return t
    low = t.lower()
    hits = [i["id"] for i in vis.get("items", []) if i["label"].lower() == low or slug(i["label"]) == low or i["id"].lower() == low]
    return hits[0] if len(set(hits)) == 1 else None


def labels(vis: dict) -> list[tuple[str, str]]:
    return [(i["id"], i["label"]) for i in vis.get("items", []) if i["label"]]


def _check(vis: dict, line: int, limits: dict) -> list[Finding]:
    n = vis.pop("nodes", 0)
    if n > MAX_NODES:
        return [
            (
                "block_too_big",
                f"The diagram on line {line} has {n} boxes; a slide reads well with far fewer.",
                "Show one area of it, or split it across slides.",
            )
        ]
    return []


KIND = Kind(
    "reladraw",
    ("reladraw",),
    parse_reladraw,
    check=_check,
    part=lambda vis: "node",
    find=find,
    labels=labels,
    reads_file=True,
    regions=True,
    hint='Write `node name "Text"` statements placed with `below a`, `right of a (gap: wide)` or `even with a`, and `edge a -> b "label"` lines.',
)
