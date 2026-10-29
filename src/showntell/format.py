"""Incremental, lenient parser for the showntell Markdown format (docs/format.md).

`Parser.feed(chunk)` returns the blocks completed by that chunk. The end of a chunk closes
every open block except a fenced block, a `$$` formula or front matter, which wait for
the next chunk. Inline narration markup is handled by `parse_narration`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*(.*)$")
HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*$")
ATTRS_TAIL_RE = re.compile(r"\s*\{([^{}]*)\}\s*$")
QUOTE_RE = re.compile(r"^ {0,3}>\s?(.*)$")
ITEM_RE = re.compile(r"^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
IMAGE_RE = re.compile(r"^\s*!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)\s*(?:\{([^{}]*)\})?\s*$")
FOOTNOTE_DEF_RE = re.compile(r"^\s*\[\^([^\]\s]+)\]:\s*(.*)$")
PAUSE_RE = re.compile(r"^\s*\{\s*pause\s*=\s*([\d.]+)\s*s?\s*\}\s*$")
RULE_RE = re.compile(r"^ {0,3}([-*_])(\s*\1){2,}\s*$")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


@dataclass
class Block:
    kind: str  # front, heading, code, math, quote, list, table, image, para, pause, footnote
    line: int  # 1-based line in the accumulated source, for messages
    text: str = ""
    level: int = 0
    attrs: dict = field(default_factory=dict)
    items: list = field(default_factory=list)
    lang: str = ""


def parse_attrs(s: str) -> dict:
    """`#id .a .b key=value key="two words"` -> {"id": ..., "classes": [...], "key": "value"}."""
    out: dict = {"classes": []}
    for m in re.finditer(r'(#[\w-]+)|(\.[\w-]+)|([\w-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(\S+))|(\S+)', s):
        if m.group(1):
            out["id"] = m.group(1)[1:]
        elif m.group(2):
            out["classes"].append(m.group(2)[1:])
        elif m.group(3):
            val = next((g for g in (m.group(4), m.group(5), m.group(6)) if g is not None), "")
            out[m.group(3).lower()] = val
        elif m.group(7):
            out.setdefault("flags", []).append(m.group(7))
    return out


def parse_info(info: str) -> tuple[str, dict]:
    """Fence info string: first bare word is the language, the rest are options and flags."""
    info = info.strip()
    if info.startswith("{") and info.endswith("}"):
        info = info[1:-1]
    lang = ""
    m = re.match(r"^([\w+#.-]+)(?=\s|$)", info)
    if m and "=" not in m.group(1):
        lang = m.group(1)
        info = info[m.end() :]
    attrs = parse_attrs(info)
    flags = [f.lower() for f in attrs.pop("flags", [])]
    attrs["flags"] = flags
    return lang.lower(), attrs


def split_heading(text: str) -> tuple[str, dict]:
    m = ATTRS_TAIL_RE.search(text)
    attrs: dict = {"classes": []}
    if m:
        attrs = parse_attrs(m.group(1))
        attrs.pop("flags", None)
        text = text[: m.start()]
    text = re.sub(r"\s+#+\s*$", "", text).strip()  # closing hashes
    return text, attrs


class Parser:
    def __init__(self) -> None:
        self.pending = ""  # unfinished fence, formula or front matter
        self.lines_seen = 0  # lines of source fully consumed
        self.started = False  # past the point where front matter may appear
        self.source = ""

    def feed(self, chunk: str) -> list[Block]:
        chunk = chunk.replace("\r\n", "\n").replace("\r", "\n")
        self.source += chunk if chunk.endswith("\n") or not chunk else chunk + "\n"
        text = self.pending + chunk
        self.pending = ""
        text = _strip_comments(text)
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        blocks: list[Block] = []
        base = self.lines_seen
        i = 0
        para: list[str] = []
        para_line = 0
        quote: list[str] = []
        quote_line = 0
        items: list[str] = []
        ordered = False
        list_line = 0

        def flush() -> None:
            nonlocal para, quote, items
            if para:
                blocks.append(Block("para", para_line, " ".join(s.strip() for s in para)))
                para = []
            if quote:
                blocks.append(Block("quote", quote_line, "\n".join(quote).strip()))
                quote = []
            if items:
                blocks.append(Block("list", list_line, items=items, attrs={"ordered": ordered}))
                items = []

        while i < len(lines):
            line = lines[i]
            lineno = base + i + 1

            if not self.started:
                if not line.strip():
                    i += 1
                    continue
                self.started = True
                if line.strip() == "---":
                    end = next((j for j in range(i + 1, len(lines)) if lines[j].strip() in ("---", "...")), None)
                    if end is None:
                        self.started = False
                        self.pending = "\n".join(lines[i:]) + "\n"
                        break
                    blocks.append(Block("front", lineno, attrs=_front_matter(lines[i + 1 : end])))
                    i = end + 1
                    continue

            fm = FENCE_RE.match(line)
            if fm:
                flush()
                mark = fm.group(1)
                end = None
                for j in range(i + 1, len(lines)):
                    s = lines[j].strip()
                    if s and s[0] == mark[0] and set(s) == {mark[0]} and len(s) >= len(mark):
                        end = j
                        break
                if end is None:
                    self.pending = "\n".join(lines[i:]) + "\n"
                    break
                lang, attrs = parse_info(fm.group(2))
                body = "\n".join(lines[i + 1 : end])
                kind = "math" if lang in ("math", "latex", "tex", "katex") else "code"
                blocks.append(Block(kind, lineno, body, attrs=attrs, lang=lang))
                i = end + 1
                continue

            s = line.strip()
            if s.startswith("$$"):
                flush()
                rest = s[2:]
                if rest.rstrip().endswith("$$") and len(rest.strip()) >= 2:
                    blocks.append(Block("math", lineno, rest.rstrip()[:-2].strip()))
                    i += 1
                    continue
                end = next((j for j in range(i + 1, len(lines)) if lines[j].strip().endswith("$$")), None)
                if end is None:
                    self.pending = "\n".join(lines[i:]) + "\n"
                    break
                body = "\n".join([rest] + lines[i + 1 : end] + [lines[end].strip()[:-2]]).strip()
                blocks.append(Block("math", lineno, body))
                i = end + 1
                continue

            if not s:
                flush()
                i += 1
                continue

            hm = HEADING_RE.match(line)
            if hm:
                flush()
                title, attrs = split_heading(hm.group(2))
                blocks.append(Block("heading", lineno, title, level=len(hm.group(1)), attrs=attrs))
                i += 1
                continue

            if RULE_RE.match(line) and not para:
                flush()
                i += 1
                continue

            pm = PAUSE_RE.match(line)
            if pm and not para:
                flush()
                blocks.append(Block("pause", lineno, attrs={"seconds": float(pm.group(1))}))
                i += 1
                continue

            fd = FOOTNOTE_DEF_RE.match(line)
            if fd:
                flush()
                blocks.append(Block("footnote", lineno, fd.group(2).strip(), attrs={"id": fd.group(1)}))
                i += 1
                continue

            im = IMAGE_RE.match(line)
            if im and not para:
                flush()
                attrs = parse_attrs(im.group(3) or "")
                attrs.pop("flags", None)
                attrs.update(alt=im.group(1).strip(), src=im.group(2))
                blocks.append(Block("image", lineno, attrs=attrs))
                i += 1
                continue

            qm = QUOTE_RE.match(line)
            if qm:
                if para or items:
                    flush()
                if not quote:
                    quote_line = lineno
                quote.append(qm.group(1))
                i += 1
                continue

            if "|" in line and i + 1 < len(lines) and TABLE_SEP_RE.match(lines[i + 1]) and not para:
                flush()
                rows = [line, lines[i + 1]]
                j = i + 2
                while j < len(lines) and lines[j].strip() and "|" in lines[j]:
                    rows.append(lines[j])
                    j += 1
                blocks.append(Block("table", lineno, "\n".join(rows)))
                i = j
                continue

            lm = ITEM_RE.match(line)
            if lm and (items or not para):
                if quote or para:
                    flush()
                if not items:
                    list_line = lineno
                    ordered = lm.group(2)[0].isdigit()
                items.append(lm.group(3).strip())
                i += 1
                continue

            if items and (line.startswith((" ", "\t"))):
                items[-1] += " " + s  # continuation of the last item
                i += 1
                continue

            if quote:
                flush()
            if items:
                flush()
            if not para:
                para_line = lineno
            para.append(line)
            i += 1

        flush()
        consumed = len(lines) if not self.pending else len(lines) - self.pending.count("\n")
        self.lines_seen = base + max(0, consumed)
        return blocks


def _strip_comments(text: str) -> str:
    """Remove HTML comments outside fenced blocks, keeping line numbers."""
    out: list[str] = []
    buf: list[str] = []
    fence = ""

    def flush() -> None:
        if buf:
            out.append(COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), "".join(buf)))
            buf.clear()

    for line in text.splitlines(keepends=True):
        m = FENCE_RE.match(line.rstrip("\n"))
        if fence:
            out.append(line)
            st = line.strip()
            if st and st[0] == fence[0] and set(st) == {fence[0]} and len(st) >= len(fence):
                fence = ""
            continue
        if m:
            flush()
            fence = m.group(1)
            out.append(line)
            continue
        buf.append(line)
    flush()
    return "".join(out)


def _front_matter(lines: list[str]) -> dict:
    out: dict = {}
    for line in lines:
        m = re.match(r"^\s*([\w-]+)\s*:\s*(.*?)\s*$", line)
        if m:
            val = m.group(2)
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            out[m.group(1).lower()] = val
    return out


# -- inline narration ---------------------------------------------------------------------


@dataclass
class Cue:
    at: int  # offset in the display text where the label starts
    end: int  # offset where the label ends
    label: str
    target: str  # without the leading '#'
    text: str = ""  # optional exact text to narrow the target
    inferred: bool = False
    style: str = ""  # pointer style from {.circle} and the like after the link


@dataclass
class Narration:
    display: str  # what captions show
    cues: list[Cue] = field(default_factory=list)
    code: list[tuple[int, int]] = field(default_factory=list)  # display ranges written as `code`
    say: list[tuple[int, int, str]] = field(default_factory=list)  # display range spoken as given text
    notes: list[str] = field(default_factory=list)  # footnote ids referenced
    problems: list[str] = field(default_factory=list)


CUE_STYLES = ("box", "circle", "underline", "arrow", "highlight", "zoom", "decode")
LINK_RE = re.compile(r"\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\(\s*(#[^\s)]*)?\s*(?:\"([^\"]*)\"|'([^']*)'|\(([^)]*)\))?\s*\)")
SPAN_RE = re.compile(r"\[([^\[\]]+)\]\{\s*say\s*=\s*(?:\"([^\"]*)\"|'([^']*)')\s*\}")
OTHER_LINK_RE = re.compile(r"\[([^\[\]]+)\]\((?!\s*#)[^)]*\)")
FOOTREF_RE = re.compile(r"\[\^([^\]\s]+)\]")
# Emphasis spans stop at 400 characters: an unclosed `_` would otherwise scan to the end of the
# paragraph at every underscore, quadratic in its length.
EMPH_RE = re.compile(
    r"(\*\*|__)(?=\S)(.{1,400}?)(?<=\S)\1|(?<![\w*])\*(?=\S)(.{1,400}?)(?<=\S)\*(?![\w*])|(?<![\w_])_(?=\S)(.{1,400}?)(?<=\S)_(?![\w_])"
)


def parse_narration(src: str) -> Narration:
    """Turn a narration paragraph into display text plus cues, code ranges and overrides.

    Works left to right over the source, emitting display characters and recording the
    display offsets of each construct, so offsets stay exact however markup nests.
    """
    n = Narration("")
    out: list[str] = []  # display characters, whitespace already collapsed

    def emit(s: str) -> None:
        for c in s:
            if c in " \t\n\r":
                if out and out[-1] != " ":
                    out.append(" ")
            else:
                out.append(c)

    i = 0
    L = len(src)
    while i < L:
        ch = src[i]
        if ch == "\\" and i + 1 < L and src[i + 1] in "\\`*_[](){}#+-.!|>~\"'":
            emit(src[i + 1])
            i += 2
            continue
        if ch == "`":
            m = re.match(r"(`+)(.+?)\1", src[i:], re.S)
            if m:
                body = m.group(2)
                if body.startswith(" ") and body.endswith(" ") and body.strip():
                    body = body[1:-1]
                start = len(out)
                emit(body)
                n.code.append((start, len(out)))
                i += m.end()
                continue
        if ch == "[":
            m = FOOTREF_RE.match(src, i)
            if m:
                n.notes.append(m.group(1))
                i = m.end()
                continue
            m = SPAN_RE.match(src, i)
            if m:
                start = len(out)
                inner = parse_narration(m.group(1))
                _absorb(n, inner, start)
                emit(inner.display)
                spoken = m.group(2) if m.group(2) is not None else m.group(3)
                n.say.append((start, len(out), spoken))
                i = m.end()
                continue
            m = LINK_RE.match(src, i)
            if m and m.group(2) is not None:
                start = len(out)
                inner = parse_narration(m.group(1))
                _absorb(n, inner, start)
                emit(inner.display)
                quoted = next((g for g in (m.group(3), m.group(4), m.group(5)) if g is not None), "")
                cue = Cue(start, len(out), inner.display, m.group(2)[1:], quoted)
                n.cues.append(cue)
                i = m.end()
                if i < L and src[i] == "{":  # cue attributes such as {.circle} or {.zoom}
                    close = src.find("}", i)
                    if close != -1:
                        styles = [c for c in parse_attrs(src[i + 1 : close])["classes"] if c in CUE_STYLES]
                        cue.style = " ".join(dict.fromkeys(styles))
                        i = close + 1
                continue
            m = OTHER_LINK_RE.match(src, i)
            if m:
                inner = parse_narration(m.group(1))
                _absorb(n, inner, len(out))
                emit(inner.display)
                i = m.end()
                continue
        if ch in "*_":
            m = EMPH_RE.match(src, i)
            if m:
                body = next(g for g in (m.group(2), m.group(3), m.group(4)) if g is not None)
                inner = parse_narration(body)
                _absorb(n, inner, len(out))
                emit(inner.display)
                i = m.end()
                continue
        if ch == "<":
            m = re.match(r"</?[a-zA-Z][^>]*>", src[i:])
            if m:  # stray HTML tags are dropped from narration
                i += m.end()
                continue
        emit(ch)
        i += 1

    n.display = "".join(out).rstrip()
    size = len(n.display)
    for c in n.cues:
        c.at, c.end = min(c.at, size), min(c.end, size)
    n.code = [(min(a, size), min(b, size)) for a, b in n.code]
    n.say = [(min(a, size), min(b, size), t) for a, b, t in n.say]
    return n


def _absorb(outer: Narration, inner: Narration, offset: int) -> None:
    for c in inner.cues:
        outer.cues.append(Cue(c.at + offset, c.end + offset, c.label, c.target, c.text, style=c.style))
    outer.code += [(a + offset, b + offset) for a, b in inner.code]
    outer.say += [(a + offset, b + offset, s) for a, b, s in inner.say]
    outer.notes += inner.notes


# -- other inline helpers -----------------------------------------------------------------


def inline_plain(src: str) -> str:
    """Strip inline markup for on-screen text that is not narration (titles, cells)."""
    return parse_narration(src).display


def parse_item(text: str) -> dict:
    """List item: `**Title**: description {#id}` or plain text; `[ ]` or `[x]` first makes it a
    checklist item."""
    attrs: dict = {}
    check = None
    cm = re.match(r"^\[([ xX])\]\s+", text)
    if cm:
        check = cm.group(1).lower() == "x"
        text = text[cm.end() :]
    m = ATTRS_TAIL_RE.search(text)
    if m and "#" in m.group(1):
        attrs = parse_attrs(m.group(1))
        text = text[: m.start()].rstrip()
    title, body = "", text
    bm = re.match(r"^\s*(\*\*|__)(.+?)\1\s*(?:[:\u2013\u2014-]\s*)?(.*)$", text)
    if bm:
        title, body = bm.group(2).strip().rstrip(":"), bm.group(3).strip()
    item = {"id": attrs.get("id", ""), "title": title, "body": body}
    if check is not None:
        item["check"] = check
    return item


def parse_table(text: str) -> dict:
    rows = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|") and not line.endswith("\\|"):
            line = line[:-1]
        cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)]
        rows.append(cells)
    if len(rows) < 2:
        return {"head": [], "rows": []}
    align = []
    for c in rows[1]:
        c = c.strip()
        align.append("center" if c.startswith(":") and c.endswith(":") else "right" if c.endswith(":") else "left")
    width = len(rows[0])
    body = [(r + [""] * width)[:width] for r in rows[2:]]
    return {"head": rows[0], "align": align[:width], "rows": body}
