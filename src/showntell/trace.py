"""```trace: what a piece of code does as it runs, one moment per line, shown as a panel of
variables beside the code. A moment names the line that has just run, then what that changed:

    L2  t = 0
    L3  x = 3
    L4  t = 3; print: 3
    L5  return: 8

`L3`, `L3:`, `3:` and `3` all name line 3 by its gutter number. Assignments are split on `;` and
on a `,` followed by another `name =`, never inside quotes or brackets. `print: text` adds a
line of output (the rest of the line), `return: value` shows what the call returns. Values are
shown as written; nothing is run, so the agent is asked to paste them from a real run.

`parse_trace` returns the moments, each with the full picture at that point (every variable in
the order first set, the ones this moment changed, the output so far, the return value), so a
seek or a video frame shows exactly that moment.
"""

from __future__ import annotations

import re

MAX_MOMENTS = 40
MAX_VARS = 10
MAX_VALUE = 40
LINE = re.compile(r"^L?(\d+)(?:\s*:\s*|\s+|$)(.*)$", re.I)
NAME = re.compile(r"^[A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)*$")
INDEXED = re.compile(r"^([A-Za-z_][\w.]*)\s*\[.*\]$")
COMPOUND = re.compile(r"^\s*([A-Za-z_][\w.\[\]]*)\s*(\+=|-=|\*=|/=|//=|%=|\*\*=|\+\+|--)")


def _split(text: str, sep: str) -> list[str] | None:
    """Pieces of `text` at `sep` outside quotes and brackets; None when a quote or bracket is
    left open."""
    out, cur, depth, quote = [], "", 0, ""
    for ch in text:
        if quote:
            cur += ch
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append(cur)
            cur = ""
            continue
        cur += ch
    out.append(cur)
    if quote or depth != 0:
        return None
    return [p.strip() for p in out]


def _assignments(text: str) -> list[str] | None:
    """`i = 0, j = 4` -> ['i = 0', 'j = 4']; a comma that does not start a `name =` stays."""
    pieces = _split(text, ",")
    if pieces is None:
        return None
    out: list[str] = []
    for p in pieces:
        if out and not re.match(r"^[A-Za-z_][\w.\[\]]*\s*=(?!=)", p):
            out[-1] += ", " + p
        else:
            out.append(p)
    return out


def _mentions(line: str, name: str) -> bool:
    """Whether a code line mentions a variable: its last attribute for `self.total`, its base for
    `xs[0]`."""
    word = name.split("[", 1)[0].rsplit(".", 1)[-1]
    return bool(re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", line))


def parse_trace(body: str, code: str, start: int) -> tuple[list[dict], list[str]]:
    lines = code.split("\n")
    last = start + len(lines) - 1
    problems: list[str] = []
    moments: list[dict] = []
    order: list[str] = []
    values: dict[str, str] = {}
    out: list[str] = []
    ret: str | None = None
    numbers: list[int] = []
    outside: list[int] = []  # moments naming a line the code does not show
    for raw in body.split("\n"):
        row = raw.strip()
        if not row:
            continue
        k = len(moments) + 1
        m = LINE.match(row)
        if not m:
            problems.append(
                f"`{row[:40]}` does not start with a line number: write `L3  x = 3` (the line that just ran, then what it changed)"
            )
            moments.append({**_snapshot(order, values, out, ret), "line": None, "changed": [], "printed": False, "text": row})
            continue
        n, rest = int(m.group(1)), m.group(2).strip()
        numbers.append(n)
        if not start <= n <= last:
            outside.append(k)
            moments.append({**_snapshot(order, values, out, ret), "line": None, "changed": [], "printed": False, "text": rest})
            continue
        pieces = _split(rest, ";") if rest else []
        if pieces is None:
            problems.append(f"moment {k} (`{rest[:40]}`) leaves a quote or bracket open")
            continue
        changed: list[str] = []
        printed: list[str] = []
        i = 0
        while i < len(pieces):
            p = pieces[i]
            i += 1
            if not p:
                continue
            if re.match(r"^print\s*:", p, re.I):
                # The rest of the line is the text, semicolons and all.
                printed.append("; ".join([p.split(":", 1)[1].strip(), *pieces[i:]]).strip())
                break
            if re.match(r"^return\s*:", p, re.I):
                ret = p.split(":", 1)[1].strip()
                continue
            for a in _assignments(p) or [p]:
                c = COMPOUND.match(a)
                if c or re.search(r"(?<![=!<>])==(?!=)", a):
                    problems.append(
                        f"moment {k} (`{a}`) copies the code; write the new value instead, such as `{(c.group(1) if c else 'x')} = 3`"
                    )
                    continue
                if "=" not in a:
                    problems.append(f"moment {k} (`{a}`) is not `name = value`, `print: text` or `return: value`")
                    continue
                name, _, value = (x.strip() for x in a.partition("="))
                idx = INDEXED.match(name)
                if idx and idx.group(1) in values:
                    problems.append(f"moment {k} sets `{name}`; write the whole value instead: `{idx.group(1)} = [...]`")
                    continue
                if not (NAME.match(name) or idx):
                    problems.append(f"moment {k}: `{name}` is not a variable name")
                    continue
                if len(value) > MAX_VALUE:
                    problems.append(
                        f"moment {k} sets `{name}` to a value of {len(value)} characters; the panel shows up to {MAX_VALUE}: shorten it (`[3, 5, ...]`)"
                    )
                    value = value[: MAX_VALUE - 1] + "…"
                if not _mentions(lines[n - start], name):
                    problems.append(
                        f"moment {k} sets `{name}` on line {n}, which does not mention `{name}`; a moment names the line that has just run"
                    )
                if name not in values:
                    order.append(name)
                values[name] = value
                changed.append(name)
        out.extend(printed)
        moments.append({**_snapshot(order, values, out, ret), "line": n, "changed": changed, "printed": bool(printed), "text": rest})
    if outside:
        which = ", ".join(str(k) for k in outside[:8]) + (" ..." if len(outside) > 8 else "")
        hint = (
            "; they look counted from 1"
            if start > 1 and len(outside) == len(numbers) and all(1 <= n <= len(lines) for n in numbers)
            else ""
        )
        problems.insert(
            0,
            f"moment{'s' if len(outside) > 1 else ''} {which} name{'' if len(outside) > 1 else 's'} lines outside {start}-{last}, the code's gutter numbers{hint}",
        )
    if not moments and not problems:
        problems.append("it has no moments: write one line per moment, `L3  x = 3`")
    if len(moments) > MAX_MOMENTS:
        problems.append(f"it has {len(moments)} moments; a trace reads well up to {MAX_MOMENTS}: trace one call, or a shorter input")
    if len(order) > MAX_VARS:
        problems.append(f"it has {len(order)} variables; the panel shows up to {MAX_VARS}: leave out the ones the narration never mentions")
    return moments, problems


def _snapshot(order: list[str], values: dict[str, str], out: list[str], ret: str | None) -> dict:
    return {"vars": [[name, values[name]] for name in order], "out": list(out), "ret": ret}


def find_moment(trace: list[dict], text: str, after: int, norm) -> int | None:
    """The first moment after `after` (1-based) whose line holds `text`, else the first anywhere."""
    want = norm(text)
    hits = [i + 1 for i, m in enumerate(trace) if want in norm(m["text"])]
    later = [k for k in hits if k > after]
    return (later or hits or [None])[0]
