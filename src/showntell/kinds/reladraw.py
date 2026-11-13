"""```reladraw: a diagram whose boxes are placed by saying where they go relative to each other
(`node api "API" below web`), drawn by the reladraw library in the player, vendored and loaded
only when a slide needs it. Written inline, or read with `file=` from a .reladraw file.

The library lays out and draws; this module only reads what cues and reveals need, best effort:
each `node`'s name and text, in source order, and the two ends of each named line. A node is a
part cues point at by its name, its text or the text's slug. The top-level nodes are the parts a
step reveal shows in order; a node inside another (`server.db`) shows with it, and a line with
the later of its two ends. Syntax errors and the library's own findings (a line through a box,
texts on top of each other) come back from the player as problems when the slide is drawn.

Pasted pictures (`icon name \"\"\"<svg>…\"\"\"`, or any `icon` statement but a letter icon) are refused: their markup would reach the page as
written. Built-in icons and letter icons are drawn by the library and stay. Over MAX_NODES boxes
or MAX_EDGES lines (counted by their ends: `a, b and c -> d` is three, `each child of x`
one per child) the block is refused, since the library lays out on the page's main thread.
"""

from __future__ import annotations

import re

from .base import Kind, slug

MAX_BYTES = 100_000
MAX_NODES = 80
MAX_EDGES = 160
NAME = re.compile(r"[^\s:,;\"()\[\]]+")
# A line's arrow: a mark, a shaft of - or --, a mark (->, <->, --, o--|>, odot--arrow, (dot)--[bar]).
_MARK = r"(?:[<>|*o]{0,2}|\(?(?:o?arrow|o?dot|o?diamond|bar|none)\)?|\[(?:o?arrow|o?dot|o?diamond|bar|none)\])"
ARROW = re.compile(rf"^{_MARK}--?{_MARK}$")


def _statements(body: str) -> tuple[list[tuple[int, str]], list[int]]:
    """(line, text) of each statement, with comments removed and indented lines joined to the one
    above; and the line of each statement that opens a triple-quoted string (an icon's SVG or a
    description), whose content is skipped. One scanner reads quotes, strings and comments, so a
    `//` inside a string is not a comment, and a text followed by an empty text is not a string."""
    out: list[tuple[int, str]] = []
    pasted: list[int] = []
    lines = body.split("\n")
    n = 0
    while n < len(lines):
        raw = lines[n]
        n += 1
        start = n  # this line's number, before a string's lines are skipped
        line, at = _scan(raw)
        if at >= 0:
            # An indented line, or one that opens with the marks, continues the statement above.
            pasted.append(out[-1][0] if (line[:1].isspace() or not line.strip()) and out else start)
            rest = raw[at + 3 :]
            # Skip to the closing marks, on this line or a later one.
            while '"""' not in rest and n < len(lines):
                rest = lines[n]
                n += 1
        if not line.strip():
            continue
        if line[:1].isspace() and out:
            out[-1] = (out[-1][0], out[-1][1] + " " + line.strip())
        else:
            out.append((start, line.strip()))
    return out, pasted


def _scan(line: str) -> tuple[str, int]:
    """The line up to a `//` comment or a triple-quoted string outside quotes, and where that
    string opens (-1 for none)."""
    quoted = False
    i = 0
    while i < len(line):
        c = line[i]
        if quoted:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                quoted = False
        elif line.startswith('"""', i):
            return line[:i], i
        elif c == '"':
            quoted = True
        elif line.startswith("//", i):
            return line[:i], -1
        i += 1
    return line, -1


def _text(rest: str) -> str | None:
    """The quoted text at the start of `rest`, unescaped, with ` / ` line breaks as spaces and
    markup (`[dim]synced[/dim]`) as its text."""
    m = re.match(r'"((?:[^"\\]|\\.)*)"', rest)
    if not m:
        return None
    t = re.sub(r"\s+/\s+", " ", m.group(1))
    t = re.sub(r"(?<!\\)\[/?[A-Za-z][\w-]*\]", "", t)
    t = re.sub(r"\\(.)", r"\1", t)
    return " ".join(t.split())


QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"')
# A mark written as its own word beside the shaft: `a -- [arrow] b`, `a (dot)-- (bar) b`.
LONE_MARK = re.compile(r"^(?:\[(?:o?arrow|o?dot|o?diamond|bar|none)\]|\((?:o?arrow|o?dot|o?diamond|bar|none)\))$")
LETTER_KEYS = re.compile(r"^(?:(?:letters|fill|text):\s*(?:\S+)\s*)+$")


def _unquoted(st: str) -> str:
    """The statement with each quoted text emptied, so words inside texts are not read."""
    return QUOTED.sub('""', st)


def _letter_icon(st: str) -> bool:
    """`icon gh  letters: "GH"  fill: red`: a letter icon, the only `icon` statement slides draw."""
    words = _unquoted(st).split(None, 2)
    return len(words) == 3 and "letters:" in words[2] and bool(LETTER_KEYS.match(words[2]))


def _ends(words: list[str]) -> list[str]:
    """The ends on one side of a line's arrow: `a, b and c` is three; `each child of x` is one."""
    return [e for e in re.split(r",|\band\b", " ".join(words)) if e.strip()]


def _line_count(st: str, children: dict[str, int]) -> int:
    """How many lines an `edge` statement draws: ends before the arrow times ends after it, with
    `each child of x` as one per child of x. Texts and attributes (`line: (…)`, `via: a, b`) are not
    read."""
    words = []
    for w in _unquoted(st).split()[1:]:
        if w.startswith('"') or w.startswith("(") or re.match(r"^[A-Za-z][\w-]*:", w):
            if not (ARROW.match(w) or LONE_MARK.match(w)):
                break
        words.append(w)
    at = next((i for i, w in enumerate(words) if ARROW.match(w) and not LONE_MARK.match(w)), None)
    if at is None:
        return 1
    count = 1
    for side in (words[:at], words[at + 1 :]):
        n = 0
        for end in _ends([w for w in side if not LONE_MARK.match(w)]):
            m = re.match(r"\s*(?:\S+\s+)?each\s+child\s+of\s+(\S+)", end)
            n += max(1, children.get(m.group(1), 0)) if m else 1
        count *= max(1, n)
    return count


def _edge(st: str) -> tuple[str, str, str] | None:
    """(name, from, to) of a named line between two nodes: `edge req a -> b`. None otherwise."""
    words = [w for w in st.split() if not LONE_MARK.match(w)]
    if len(words) >= 5 and words[0] == "edge" and ARROW.match(words[3]) and not ARROW.match(words[2]):
        name, a, b = words[1], words[2], words[4]
        if all(NAME.fullmatch(x) for x in (name, a, b)):
            return name, a, b
    return None


def parse_reladraw(body: str, attrs: dict) -> tuple[dict, list[str]]:
    if len(body.encode("utf-8")) > MAX_BYTES:
        return {}, [f"the diagram is over {MAX_BYTES // 1000} KB; a slide reads well with far less"]
    if not body.strip():
        return {}, ["the block is empty"]
    statements, pasted = _statements(body)
    problems = []
    if any(re.match(r"icon\b", st) and (ln in pasted or not _letter_icon(st)) for ln, st in statements):
        problems.append(
            'it pastes a picture with an `icon` statement (`icon name """<svg>…"""`), which slides do not draw: '
            'use a built-in icon (`icon: database`) or a letter icon (`icon gh  letters: "GH"`)'
        )
    items: list[dict] = []
    edges: dict[str, list[str]] = {}
    seen: set[str] = set()
    line_statements = []
    for _, st in statements:
        if re.match(r"diagram\b.*\bbackground:", _unquoted(st)):
            problems.append("it sets `diagram background:`; the slide's theme sets the background, so leave it out")
        if st.startswith("edge"):
            line_statements.append(st)
            named = _edge(st)
            if named:
                edges[named[0]] = [named[1], named[2]]
            continue
        m = re.match(r"node\s+(\S+)\s*(.*)$", st)
        if not m:
            continue
        name = NAME.match(m.group(1))
        if not name or name.group(0) in seen:
            continue
        nid = name.group(0)
        seen.add(nid)
        text = _text(m.group(2))
        if text is None:  # no text: the library shows the name's last part, unless it is a picture
            text = "" if re.search(r"(^|\s)icon:", m.group(2)) else nid.rsplit(".", 1)[-1]
        items.append({"id": nid, "label": text})
    children: dict[str, int] = {}
    for nid in seen:
        if "." in nid:
            parent = nid.rsplit(".", 1)[0]
            children[parent] = children.get(parent, 0) + 1
    lines = sum(_line_count(st, children) for st in line_statements)
    if not items:
        problems.append('it has no `node` statement, so there is nothing to draw: start with `node a "First"`')
    if len(items) > MAX_NODES or lines > MAX_EDGES:
        problems.append(
            f"it has {len(items)} boxes and {lines} lines; a slide holds at most {MAX_NODES} boxes and {MAX_EDGES} lines: "
            "show one area of it, or split it across slides"
        )
    if problems:
        return {}, problems
    names = [i["id"] for i in items]
    part_of = {}
    for nid in names:
        top = nid.split(".", 1)[0]
        if top != nid and top in seen:
            part_of[nid] = top
    vis = {
        "source": body.rstrip(),
        "items": items,
        "ids": [n for n in names if n not in part_of],
        "part_of": part_of,
        "edges": {k: v for k, v in edges.items() if all(e in seen for e in v)},
    }
    return vis, []


def find(vis: dict, target: str) -> str | None:
    """Name of the node a cue names: its name, then a unique text or slug of one, then a unique
    name in any case."""
    t = target.strip()
    items = vis.get("items", [])
    if t in [i["id"] for i in items]:
        return t
    low = t.lower()
    hits = {i["id"] for i in items if i["label"] and (i["label"].lower() == low or slug(i["label"]) == low)}
    if len(hits) == 1:
        return hits.pop()
    if hits:
        return None
    ids = {i["id"] for i in items if i["id"].lower() == low}
    return ids.pop() if len(ids) == 1 else None


def labels(vis: dict) -> list[tuple[str, str]]:
    return [(i["id"], i["label"]) for i in vis.get("items", []) if i["label"]]


KIND = Kind(
    "reladraw",
    ("reladraw",),
    parse_reladraw,
    part=lambda vis: "node",
    find=find,
    labels=labels,
    reads_file=True,
    regions=True,
    hint='Write `node name "Text"` statements placed with `below a`, `right of a (gap: wide)` or `even with a`, and `edge a -> b "label"` lines.',
)
