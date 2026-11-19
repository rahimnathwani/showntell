"""Where each key of a JSON, YAML or TOML block is, so a cue can name `#params.name` or
`#items[0].id` instead of counting lines. The readers are forgiving, since a slide shows an
excerpt: comments, trailing commas, unquoted keys, `...` placeholders and a JSON fragment without
its outer braces are read past, not refused.

`key_lines(code, lang)` maps each path (a tuple of keys, list positions as their digits) to a
`Span`: the lines, counted from 0, from its key (or list item) through the end of its value, and,
when the key shares its line with others (one-line JSON, `[a, b]` in YAML), its own text there.
A YAML file with several documents puts `doc-1`, `doc-2` ahead of each document's paths. The
first of two equal keys wins.
"""

from __future__ import annotations

import re
from typing import NamedTuple

DATA_LANGS = {"json": "json", "jsonc": "json", "json5": "json", "yaml": "yaml", "yml": "yaml", "toml": "toml"}

Path = tuple[str, ...]
MAX_LINE = 1000
MAX_NEST = 64  # keys deeper than this are not named (and the reader never recurses further)


class Span(NamedTuple):
    l0: int
    l1: int
    text: str = ""  # the key and its value, when the line holds more than this key


def data_lang(lang: str | None) -> str | None:
    """'json', 'yaml' or 'toml' for a code block whose keys cues can name, else None."""
    return DATA_LANGS.get((lang or "").lower())


def key_lines(code: str, lang: str) -> dict[Path, Span]:
    kind = data_lang(lang)
    try:
        return _json(code) if kind == "json" else _toml(code) if kind == "toml" else _yaml(code)
    except RecursionError:  # the readers bound their depth; this is the last guard
        return {}


def parse_path(target: str) -> Path | None:
    """`$.a.b[0].c`, `a.b.0.c` or `a["x.y"]` as ('a', 'b', '0', 'c'); None if it is not one."""
    t = target.strip()
    t = t[2:] if t.startswith("$.") else t[1:] if t.startswith("$") else t
    out: list[str] = []
    for m in re.finditer(r"""\[\s*(?:"([^"]*)"|'([^']*)'|(-?\d+))\s*\]|\.?([^.\[\]]+)""", t):
        out.append(next(g for g in m.groups() if g is not None))
    return tuple(p.strip() for p in out) if out else None


def find(paths: dict[Path, Span], want: Path, exact: bool = False) -> Path | None:
    """The path a cue names: exactly, else (unless `exact`) the one path ending with those keys.
    Keys that hold a dot (`socket.io`) match when written with dots too."""
    for cand in _joins(want, _prefixes(paths)):
        if cand in paths:
            return cand
    if exact:
        return None
    tails = {p for cand in _joins(want, None) for p in paths if p[-len(cand) :] == cand}
    return tails.pop() if len(tails) == 1 else None


def near(paths: dict[Path, Span], want: Path) -> tuple[Path, list[Path]]:
    """The longest prefix of `want` that exists, and the keys directly under it."""
    have: Path = ()
    for i in range(len(want), 0, -1):
        if want[:i] in paths:
            have = want[:i]
            break
    under = [p for p in paths if len(p) == len(have) + 1 and p[: len(have)] == have]
    return have, under


def show(path: Path) -> str:
    out = ""
    for p in path:
        out += f"[{p}]" if p.isdigit() else f'["{p}"]' if "." in p or " " in p else f".{p}"
    return out.lstrip(".")


def _prefixes(paths: dict[Path, Span]) -> set[Path]:
    return {p[:i] for p in paths for i in range(1, len(p) + 1)}


def _joins(want: Path, prefixes: set[Path] | None, limit: int = 64) -> list[Path]:
    """`want` as written, then with neighbouring segments joined by dots, kept to prefixes that
    exist when `prefixes` is given."""
    out: list[Path] = []

    def walk(i: int, acc: Path) -> None:
        if len(out) >= limit:
            return
        if i == len(want):
            out.append(acc)
            return
        for j in range(i + 1, len(want) + 1):
            nxt = (*acc, ".".join(want[i:j]))
            if prefixes is None or nxt in prefixes:
                walk(j, nxt)

    walk(0, ())
    return out or [want]


# -- JSON -----------------------------------------------------------------------------------------

_TOKEN = re.compile(
    r"""(?P<ws>\s+)|(?P<line>//[^\n]*|\#[^\n]*)|(?P<block>/\*.*?\*/)|(?P<str>"(?:[^"\\\n]|\\.)*"?|'(?:[^'\\\n]|\\.)*'?)"""
    r"""|(?P<punct>[{}\[\]:,])|(?P<word>[^\s{}\[\]:,"'/#]+|/)""",
    re.S,
)


class _Tok(NamedTuple):
    kind: str
    text: str
    line: int
    start: int  # offset in the whole text
    end: int


def _tokens(code: str, line0: int = 0) -> list[_Tok]:
    out, line = [], line0
    for m in _TOKEN.finditer(code):
        kind, text = m.lastgroup, m.group(0)
        if kind in ("str", "punct", "word"):
            out.append(_Tok(kind, text, line, m.start(), m.end()))
        line += text.count("\n")
    return out


def _json(code: str, line0: int = 0) -> dict[Path, Span]:
    toks = _tokens(code, line0)
    if toks and toks[0].text not in ("{", "[") and any(t.text == ":" for t in toks[:3]):
        # A fragment: `"scripts": {...}` pasted without the object around it.
        first, last = toks[0], toks[-1]
        toks = [_Tok("punct", "{", first.line, first.start, first.start), *toks, _Tok("punct", "}", last.line, last.end, last.end)]
    rows = code.split("\n")
    out: dict[Path, Span] = {}
    i = 0

    def put(path: Path, first: _Tok, last: _Tok) -> None:
        if path in out:
            return  # the first of two equal keys wins
        text = ""
        if first.line == last.line:
            row = rows[first.line - line0]
            piece = code[first.start : last.end].strip()
            if piece and piece != row.strip().rstrip(","):
                text = piece
        out[path] = Span(first.line, last.line, text)

    def key_text(tok: _Tok) -> str:
        if tok.kind == "str":
            return tok.text[1:-1] if len(tok.text) > 1 and tok.text[-1] == tok.text[0] else tok.text[1:]
        return tok.text

    def skip() -> _Tok:
        """Past a container nested too deep to name keys in, to its matching close."""
        nonlocal i
        depth = 0
        while i < len(toks):
            t = toks[i]
            i += 1
            depth += (t.text in ("{", "[")) - (t.text in ("}", "]"))
            if depth <= 0:
                return t
        return toks[-1]

    def value(path: Path) -> _Tok:
        """Read the value at toks[i]; return its last token."""
        nonlocal i
        if i >= len(toks):
            return toks[-1]
        tok = toks[i]
        if tok.text in ("{", "[") and nest[0] >= MAX_NEST:
            return skip()
        if tok.text in ("{", "["):
            nest[0] += 1
            try:
                return container(path, tok)
            finally:
                nest[0] -= 1
        i += 1
        return tok

    def container(path: Path, tok: _Tok) -> _Tok:
        nonlocal i
        close = "}" if tok.text == "{" else "]"
        i += 1
        n = 0
        while i < len(toks) and toks[i].text != close:
            t = toks[i]
            if t.text in (",", "}", "]", "...", "…"):
                i += 1
                continue
            if close == "}":
                if t.text in ("{", "["):  # out of place: read it as a value and go on
                    value(path)
                    continue
                k = key_text(t)
                i += 1
                if i < len(toks) and toks[i].text == ":":
                    i += 1
                    put((*path, k), t, value((*path, k)))
                else:
                    put((*path, k), t, t)
            else:
                put((*path, str(n)), t, value((*path, str(n))))
                n += 1
        end = toks[i] if i < len(toks) else toks[-1]
        i += 1
        return end

    nest = [0]  # containers open now, however they are reached
    while i < len(toks):
        before = i
        value(())
        if i == before:
            i += 1
    return out


# -- YAML -----------------------------------------------------------------------------------------

_KEY = re.compile(r"""^(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|([^\s#'"\-?:,\[\]{}&*!|>%@`][^:#]*?|-[^\s:][^:#]*?))\s*:(?:\s+|$)(.*)$""")


def _yaml(code: str) -> dict[Path, Span]:
    docs: list[list[tuple[int, str]]] = [[]]
    for n, raw in enumerate(code.split("\n")):
        if re.match(r"^---(\s|$)", raw):
            docs.append([])
        elif not re.match(r"^\.\.\.\s*$", raw):
            docs[-1].append((n, raw))
    docs = [d for d in docs if any(r.strip() and not r.strip().startswith("#") for _, r in d)]
    if len(docs) <= 1:
        return _yaml_doc(docs[0] if docs else [])
    out: dict[Path, Span] = {}
    for k, d in enumerate(docs, 1):
        lines = [n for n, r in d if r.strip() and not r.strip().startswith("#")]
        out[(f"doc-{k}",)] = Span(lines[0], lines[-1])
        out.update({(f"doc-{k}", *p): s for p, s in _yaml_doc(d).items()})
    return out


def _yaml_doc(rows: list[tuple[int, str]]) -> dict[Path, Span]:
    out: dict[Path, Span] = {}
    stack: list[tuple[int, Path, int]] = []  # (indent, path, first line) of each open key or item
    bare: set[Path] = set()  # keys with nothing after the colon: their value is on the lines below
    counts: dict[Path, int] = {}
    last = -1  # the last line with content
    block = -1  # inside a block scalar (| or >) opened at this indent

    def pop() -> None:
        _, path, start = stack.pop()
        out.setdefault(path, Span(start, max(start, last)))

    for n, raw in rows:
        text = raw.rstrip()
        body = text.lstrip(" ")
        if not body or body.startswith("#"):
            continue
        indent = len(text) - len(body)
        if block >= 0:
            if indent > block:
                last = n
                continue
            block = -1
        item = body == "-" or body.startswith("- ")
        while stack and stack[-1][0] > indent:
            pop()
        # `key:` then `- a` at the key's own indent: the list is the key's value, so an item closes
        # the item before it but not the key.
        while stack and stack[-1][0] == indent and not (item and stack[-1][1] in bare):
            pop()
        parent = stack[-1][1] if stack else ()
        while body == "-" or body.startswith("- "):  # a list item, perhaps holding a key
            idx = counts.get(parent, 0)
            counts[parent] = idx + 1
            it = (*parent, str(idx))
            stack.append((indent, it, n))
            rest = body[1:].lstrip(" ")
            indent += len(body) - len(rest)
            body, parent = rest, it
            if not body:
                break
        m = _KEY.match(body) if len(body) <= MAX_LINE else None  # a long line is no key (and keeps the match linear)
        if m:
            key = next(g for g in m.groups()[:3] if g is not None).strip()
            path = (*parent, key)
            stack.append((indent, path, n))
            val = m.group(4).strip()
            if not val or val.startswith("#"):
                bare.add(path)
            if re.match(r"^[|>][+-]?\d*\s*(#.*)?$", val):
                block = indent
            if val[:1] in ("[", "{"):  # a flow collection on one line: its items, by the JSON reader
                at = len(raw.rstrip()) - len(m.group(4).rstrip())
                for sub, sp in _json(raw[at:], n).items():
                    out.setdefault((*path, *sub), Span(n, n, sp.text or raw[at:].strip()))
        elif body.startswith(("[", "{")) and parent:  # `- [a, b]`
            at = len(raw) - len(body)
            for sub, sp in _json(raw[at:], n).items():
                out.setdefault((*parent, *sub), Span(n, n, sp.text))
        last = n
    while stack:
        pop()
    return out


# -- TOML -----------------------------------------------------------------------------------------

_TABLE = re.compile(r"^\s*(\[\[?)\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")
_TKEY = re.compile(r"""^\s*((?:"[^"]*"|'[^']*'|[\w-]+)(?:\s*\.\s*(?:"[^"]*"|'[^']*'|[\w-]+))*)\s*=\s*(.*)$""")


def _toml_key(text: str) -> Path:
    return tuple(p.strip().strip("\"'") for p in re.findall(r"""("[^"]*"|'[^']*'|[^.\s]+)""", text))


def _toml(code: str) -> dict[Path, Span]:
    rows = code.split("\n")
    out: dict[Path, Span] = {}
    table: Path = ()
    table_at = -1
    arrays: dict[Path, int] = {}
    last = -1
    n = 0

    def end_table() -> None:
        if table and table_at >= 0:
            out.setdefault(table, Span(table_at, max(table_at, last)))

    while n < len(rows):
        raw = rows[n]
        body = raw.strip()
        if not body or body.startswith("#"):
            n += 1
            continue
        m = _TABLE.match(raw)
        if m and not _TKEY.match(raw):
            end_table()
            path = _toml_key(m.group(2))
            if m.group(1) == "[[":
                idx = arrays.get(path, 0)
                arrays[path] = idx + 1
                out.setdefault(path, Span(n, n))
                table = (*path, str(idx))
            else:
                table = path
            table_at = last = n
            n += 1
            continue
        m = _TKEY.match(raw)
        if m:
            path = (*table, *_toml_key(m.group(1)))
            start, value = n, m.group(2)
            # A value that runs on: an array or inline table until its brackets close, a """ string.
            depth = value.count("[") + value.count("{") - value.count("]") - value.count("}")
            triple = value.count('"""') == 1 or value.count("'''") == 1
            while (depth > 0 or triple) and n + 1 < len(rows):
                n += 1
                nxt = rows[n]
                depth += nxt.count("[") + nxt.count("{") - nxt.count("]") - nxt.count("}")
                if triple and ('"""' in nxt or "'''" in nxt):
                    triple = False
            out.setdefault(path, Span(start, n))
            if start == n and value.strip()[:1] in ("[", "{"):
                at = len(raw) - len(value)
                for sub, sp in _json(raw[at:], start).items():
                    out.setdefault((*path, *sub), Span(start, start, sp.text))
            last = n
        n += 1
    end_table()
    # An array of tables spans to the last key of its last entry.
    for path in arrays:
        items = [s for p, s in out.items() if len(p) == len(path) + 1 and p[: len(path)] == path]
        if items:
            out[path] = Span(out[path].l0, max(s.l1 for s in items))
    return out
