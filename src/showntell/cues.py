"""Cues: links in narration resolved against the slide's visual, and cues inferred when there are none.

A cue becomes a dict the player acts on: lines of code, a list item or row, a diagram node, a
path along edges, an image region, a code step, a part of a bundled kind, or the whole visual.
Targets that do not exist are reported with the ids that do.
"""

from __future__ import annotations

import re

from . import kinds, paths
from .format import Cue, inline_plain
from .mermaid import has_edge, sequence_messages
from .model import Say, Slide
from .trace import find_moment

# Words too common to take for an identifier when inferring cues from narration.
STOPWORDS = set(
    "the a an and or but if then else for from with into onto over under this that these those it its "
    "is are was were be been being to of in on at by as so not no yes we you they he she our your their "
    "here there when where what which who how why all any each every some more most less one two three "
    "first next last also just only very can will would should could may might must do does did done "
    "has have had get gets got set sets new old value values line lines code call calls return returns".split()
)


class CueResolver:
    """Builder methods that resolve a paragraph's cues; expects `_issue` from the builder."""

    _step = 1  # the code step shown when the cue being resolved fires
    _moment = 0  # the trace moment shown when the cue being resolved fires

    def _resolve_cues(self, s: Slide, say: Say, reresolve: bool = False) -> None:
        raw: list[Cue] = getattr(say, "_cues", None) or [
            Cue(c["at"], c["end"], c.get("label", ""), c.get("target", ""), c.get("text", ""), c.get("inferred", False), c.get("style", ""))
            for c in say.cues
            if not c.get("inferred")
        ]
        say._cues = raw
        out = []
        # Code that evolves: line cues refer to the step shown when they fire.
        self._step, self._moment = 1, 0
        for x in s.says:
            if x is say:
                break
            for c in x.cues:
                if c.get("kind") == "step":
                    self._step = c["n"]
                elif c.get("kind") == "trace":
                    self._moment = c["n"]
        for c in raw:
            r = self._resolve_pair(s, c, quiet=not s.visual and not reresolve)
            if r:
                if c.style:
                    r["style"] = c.style
                if r.get("kind") == "step":
                    self._step = r["n"]
                elif r.get("kind") == "trace":
                    self._moment = r["n"]
                elif s.visual.get("steps") and r.get("kind") == "lines":
                    r["step"] = self._step
                out.append(r)
        if not raw and s.visual:
            out = infer_cues(s, say)
        times = {(c["at"], c.get("target")): c.get("t") for c in say.cues}
        for c in out:
            if (c["at"], c.get("target")) in times and times[(c["at"], c.get("target"))] is not None:
                c["t"] = times[(c["at"], c.get("target"))]
        say.cues = sorted(out, key=lambda c: c["at"])

    def _resolve_pair(self, s: Slide, c: Cue, quiet: bool = False) -> dict | None:
        """On a `.pair` slide a cue points into the first visual, or else into the second, whose
        cues carry `pane: 1`; problems are reported only when neither has the target."""
        if not s.pair or c.target.strip() in ("", "visual", "slide"):
            return self._resolve(s, c, quiet=quiet)
        mark = len(self.issues)
        r = self._resolve(s, c, quiet=quiet)
        if r and len(self.issues) == mark and r.get("kind") != "visual":
            return r
        first = self.issues[mark:]
        del self.issues[mark:]
        kind, visual = s.kind, s.visual
        s.kind, s.visual = s.pair["kind"], s.pair["visual"]
        try:
            r2 = self._resolve(s, c, quiet=quiet)
        finally:
            s.kind, s.visual = kind, visual
        if r2 and len(self.issues) == mark and r2.get("kind") != "visual":
            return {**r2, "pane": 1}
        del self.issues[mark:]
        self.issues += first
        return r

    def _resolve(self, s: Slide, c: Cue, quiet: bool = False) -> dict | None:
        base = {"at": c.at, "end": c.end, "label": c.label, "target": c.target, "text": c.text}
        t = c.target.strip()
        if not s.visual:
            if t in ("", s.id, "visual"):
                return {**base, "kind": "visual"}
            if not quiet:
                self._issue("cue_no_visual", f"Slide `{s.id}` has no visual for the cue [{c.label}](#{t}).", slide=s.id)
            return {**base, "kind": "visual"}
        # The slide's own parts and keys come first, so a node `l1`, an object `step1` or a key named
        # like the slide's id is reached rather than read as a line, a step or the whole visual.
        own = self._own_part(s, c, t, base) if t not in ("", "visual", "slide") else None
        if own:
            return own
        if t in ("", s.id, "visual", "slide"):
            if c.text:
                return {**base, "kind": "text"}
            return {**base, "kind": "visual"}
        steps = s.visual.get("steps") if s.kind == "code" or (kinds.get(s.kind) and kinds.get(s.kind).steps) else None
        trace = s.visual.get("trace") if s.kind == "code" else None
        tm = re.fullmatch(r"trace(?:-?(\d+))?", t, re.I)
        if tm and trace:
            return self._resolve_moment(s, c, t, tm.group(1), trace, base)
        sm = re.fullmatch(r"step-?(\d+)", t, re.I)
        if sm and trace:
            self._issue(
                "cue_no_target",
                f"[{c.label}](#{t}): slide `{s.id}` has a trace; its moments are `#trace-1` to `#trace-{len(trace)}`.",
                slide=s.id,
            )
            return None
        if tm:
            self._issue(
                "cue_no_target",
                f"[{c.label}](#{t}): slide `{s.id}` has no trace.",
                "Put a ```trace block after the code: one line per moment, `L3  x = 3`.",
                slide=s.id,
            )
            return None
        if sm:
            n = int(sm.group(1))
            if steps and 1 <= n <= len(steps):
                return {**base, "kind": "step", "n": n}
            have = f"{len(steps)} steps" if steps else "no steps"
            self._issue(
                "cue_no_target",
                f"[{c.label}](#{t}): slide `{s.id}` has {have}.",
                "Mark each version of the code with `step` in its info string.",
                slide=s.id,
            )
            return None
        if t.startswith("@"):
            box = _region(t[1:])
            regions = s.kind == "image" or bool(kinds.get(s.kind) and kinds.get(s.kind).regions)
            if regions and box:
                return {**base, "kind": "region", "box": box}
            if not regions:
                self._issue(
                    "cue_wrong_kind",
                    f"[{c.label}](#{t}): regions point into images and drawings; slide `{s.id}` shows a {s.kind}.",
                    slide=s.id,
                )
            else:
                self._issue(
                    "cue_no_target",
                    f"[{c.label}](#{t}): a region is `@left,top,width,height` in percent of the {s.kind}, each from 0 to 100.",
                    slide=s.id,
                )
            return {**base, "kind": "visual"}
        seq = re.fullmatch(r"([\w-]+?)\s*(?:<<)?-{1,2}(?:>>|>|x|\))\s*([\w-]+)", t)
        if seq and s.kind == "diagram" and s.visual.get("type") == "sequencediagram":
            # A message in a sequence diagram, named by its sender and receiver: point at its text.
            msgs = [m for m in sequence_messages(s.visual["source"]) if m[:2] == seq.groups()]
            if c.text:
                msgs = [m for m in msgs if _norm(c.text) in _norm(m[2])] or msgs
            if msgs:
                return {**base, "kind": "text", "text": c.text or msgs[0][2]}
            pairs = ", ".join(sorted({f"{a}->>{b}" for a, b, _ in sequence_messages(s.visual["source"])})[:12])
            self._issue(
                "cue_no_target",
                f"[{c.label}](#{t}): no message from `{seq.group(1)}` to `{seq.group(2)}` in the diagram on slide `{s.id}`. "
                f"Messages: {pairs}.",
                slide=s.id,
            )
            return None
        if "->" in t:
            path = [p.strip() for p in t.split("->")]
            nodes = s.visual.get("nodes", {}) if s.kind == "diagram" else {}
            if s.kind != "diagram" or s.visual.get("type") not in ("flowchart", "graph"):
                kind = s.visual.get("type", "") if s.kind == "diagram" else ""
                what = DIAGRAM_NAMES.get(kind, "diagram") if s.kind == "diagram" else s.kind
                self._issue(
                    "cue_wrong_kind",
                    f"[{c.label}](#{t}): a path along edges needs a flowchart; slide `{s.id}` shows a {what}.",
                    "Name one message as `#Sender->>Receiver`." if kind == "sequencediagram" else "",
                    slide=s.id,
                )
                return {**base, "kind": "visual"}
            missing = [p for p in path if p not in nodes]
            if missing or len(path) < 2:
                self._issue(
                    "cue_no_target",
                    f"[{c.label}](#{t}): no node {', '.join(f'`{x}`' for x in missing) or 'pair'} in the diagram on slide `{s.id}`. "
                    f"Nodes: {', '.join(list(nodes)[:12])}.",
                    slide=s.id,
                )
                return None
            gaps = [f"{a}->{b}" for a, b in zip(path, path[1:], strict=False) if not has_edge(s.visual["source"], a, b)]
            if gaps:
                self._issue(
                    "cue_no_target",
                    f"[{c.label}](#{t}): the diagram on slide `{s.id}` has no edge {', '.join(gaps)}.",
                    "Follow edges in the direction they are drawn.",
                    slide=s.id,
                )
                return None
            return {**base, "kind": "edge", "path": path}
        m = re.fullmatch(r"([+-])?L(\d+)(?:-L?(\d+))?", t, re.I)
        if m and (s.kind in ("code", "diff") or kinds.line_kind(s.kind)):
            side = "old" if m.group(1) == "-" else "new"
            a = int(m.group(2))
            b = int(m.group(3)) if m.group(3) else a
            if b < a:
                a, b = b, a
            vis = s.visual[side] if s.kind == "diff" else s.visual
            if steps:
                vis = steps[min(len(steps), max(1, self._step)) - 1]
            lines = vis["code"].split("\n")
            first, last = vis["start"], vis["start"] + len(lines) - 1
            where = f"slide `{s.id}`" + (f" ({side} side)" if s.kind == "diff" else "")
            if a > last or b < first:
                if c.text:
                    hit = _find_line(lines, c.text, first, a)
                    if hit:
                        self._issue(
                            "cue_moved",
                            f"[{c.label}](#{t}): line {a} is outside lines {first}-{last} on {where}; "
                            f"pointed at line {hit}, where the quoted text is.",
                            slide=s.id,
                        )
                        return {**base, "kind": "lines", "side": side, "l0": hit, "l1": hit}
                self._issue(
                    "cue_off_code",
                    f"[{c.label}](#{t}) points past the code on {where}, which shows lines {first}-{last}.",
                    "Use the gutter numbers.",
                    slide=s.id,
                )
                return None
            a, b = max(a, first), min(b, last)
            if c.text:
                span = "\n".join(lines[a - first : b - first + 1])
                if _norm(c.text) not in _norm(span):
                    hit = _find_line(lines, c.text, first, a)
                    if hit:
                        self._issue(
                            "cue_moved",
                            f"[{c.label}](#{t}): the quoted text is not on line {a}; pointed at line {hit}, where it is.",
                            "Count lines with the gutter numbers.",
                            slide=s.id,
                        )
                        a = b = hit
                    else:
                        self._issue(
                            "cue_text_missing",
                            f'[{c.label}](#{t}): "{c.text}" does not occur in the code on {where}; pointed at the whole line.',
                            "Quote the code exactly, spaces included.",
                            slide=s.id,
                        )
                        return {**base, "kind": "lines", "side": side, "l0": a, "l1": b, "text": ""}
            return {**base, "kind": "lines", "side": side, "l0": a, "l1": b}
        if m:
            self._issue("cue_wrong_kind", f"[{c.label}](#{t}) points at lines, but slide `{s.id}` shows a {s.kind}, not code.", slide=s.id)
            return {**base, "kind": "visual"}
        if s.kind == "code" and paths.data_lang(s.visual.get("lang")):
            return self._resolve_key(s, c, t, steps, base)
        if s.kind == "list":
            ids = {it["id"] for it in s.visual["items"]}
            if t in ids:
                return {**base, "kind": "item", "id": t}
            hit = _match_title(t, s.visual["items"])
            if hit:
                return {**base, "kind": "item", "id": hit}
            self._issue(
                "cue_no_target", f"[{c.label}](#{t}): no item `{t}` on slide `{s.id}`. Items: {', '.join(sorted(ids))}.", slide=s.id
            )
            return None
        if kinds.part_kind(s.kind):
            pid = kinds.get(s.kind).find(s.visual, t)
            if pid:
                outer = s.visual.get("part_of", {}).get(pid)  # a part inside another reveals with it
                return {**base, "kind": "item", "id": pid, **({"part": outer} if outer else {})}
            what = kinds.get(s.kind).part(s.visual)
            self._issue(
                "cue_no_target",
                f"[{c.label}](#{t}): no {what} `{t}` on slide `{s.id}`. Ids: {', '.join(s.visual.get('ids', [])[:12])}.",
                slide=s.id,
            )
            return None
        if s.kind == "table":
            ids = s.visual.get("ids", [])
            row = _table_row(t, ids)
            if row:
                return {**base, "kind": "row", "id": row}
            cols = s.visual.get("cols") or [""] * len(s.visual.get("head", []))
            if "/" in t:  # one cell: `#row-2/col-3`, `#postgres/col-latency` or `#postgres/latency`
                r, _, k = t.partition("/")
                row, col = _table_row(r, ids), _table_col(k, cols, bare=True)
                if row and col is not None:
                    return {**base, "kind": "cell", "id": row, "col": col}
                missing = f"no row `{r}`" if not row else f"no column `{k}`"
                self._issue(
                    "cue_no_target", f"[{c.label}](#{t}): {missing} in the table on slide `{s.id}`. {_table_names(ids, cols)}", slide=s.id
                )
                return None
            col = _table_col(t, cols)
            if col is not None:
                return {**base, "kind": "col", "col": col}
            self._issue(
                "cue_no_target", f"[{c.label}](#{t}): no row or column `{t}` on slide `{s.id}`. {_table_names(ids, cols)}", slide=s.id
            )
            return None
        if s.kind == "diagram":
            nodes = s.visual.get("nodes", {})
            if t in nodes:
                return {**base, "kind": "node", "id": t, "text": c.text or nodes[t]}
            if nodes and s.visual.get("type") in ("flowchart", "graph"):
                self._issue(
                    "cue_no_target",
                    f"[{c.label}](#{t}): no node `{t}` in the diagram on slide `{s.id}`. Nodes: {', '.join(list(nodes)[:12])}.",
                    slide=s.id,
                )
            return {**base, "kind": "node", "id": t, "text": c.text}
        self._issue("cue_wrong_kind", f"[{c.label}](#{t}): slide `{s.id}` shows a {s.kind}.", "Point at it with `#`.", slide=s.id)
        return {**base, "kind": "visual"}

    def _resolve_moment(self, s: Slide, c: Cue, t: str, num: str | None, trace: list[dict], base: dict) -> dict | None:
        """`#trace-N` moves the panel to moment N; with quoted text, checked against that moment's
        line of the trace and moved to the moment that has it. `#trace "t = 8"` alone finds the
        next moment with that text."""
        k = len(trace)
        names = f"its moments are `#trace-1` to `#trace-{k}`"
        if num is None:
            n = find_moment(trace, c.text, self._moment, _norm) if c.text else None
            if n is None:
                self._issue(
                    "cue_no_target",
                    f"[{c.label}](#{t}"
                    + (f' "{c.text}"' if c.text else "")
                    + f"): no moment of the trace on slide `{s.id}` has that text; {names}.",
                    "Number the moment, `#trace-3`, or quote text from its trace line.",
                    slide=s.id,
                )
                return None
            return {**base, "kind": "trace", "n": n, "line": trace[n - 1]["line"], "text": ""}
        n = int(num)
        if not 1 <= n <= k:
            self._issue("cue_no_target", f"[{c.label}](#{t}): the trace on slide `{s.id}` has {k} moments; {names}.", slide=s.id)
            return None
        if c.text and _norm(c.text) not in _norm(trace[n - 1]["text"]):
            hit = find_moment(trace, c.text, self._moment, _norm)
            if hit:
                self._issue(
                    "cue_moved",
                    f'[{c.label}](#{t} "{c.text}"): moment {n} is `{trace[n - 1]["text"]}`; pointed at moment {hit}, which has the quoted text.',
                    "Count moments from 1, one per line of the trace.",
                    slide=s.id,
                )
                n = hit
            else:
                self._issue(
                    "cue_text_missing",
                    f'[{c.label}](#{t} "{c.text}"): no moment of the trace has that text; kept moment {n}, `{trace[n - 1]["text"]}`.',
                    "Quote the trace line as written, such as `t = 8`.",
                    slide=s.id,
                )
        return {**base, "kind": "trace", "n": n, "line": trace[n - 1]["line"], "text": ""}

    def _own_part(self, s: Slide, c: Cue, t: str, base: dict) -> dict | None:
        """A part, list item or JSON/YAML key of this slide's visual with exactly that name; None
        (and no problem reported) when there is none."""
        if kinds.part_kind(s.kind):
            pid = kinds.get(s.kind).find(s.visual, t)
            if pid:
                outer = s.visual.get("part_of", {}).get(pid)
                return {**base, "kind": "item", "id": pid, **({"part": outer} if outer else {})}
        elif s.kind == "list" and t in {it["id"] for it in s.visual.get("items", [])}:
            return {**base, "kind": "item", "id": t}
        elif s.kind == "code" and paths.data_lang(s.visual.get("lang")) and not re.fullmatch(r"[+-]?L\d+(?:-L?\d+)?", t, re.I):
            steps = s.visual.get("steps")
            vis = steps[min(len(steps), max(1, self._step)) - 1] if steps else s.visual
            want = paths.parse_path(t)
            if want and paths.find(paths.key_lines(vis["code"], s.visual["lang"]), want):
                return self._resolve_key(s, c, t, steps, base)
        return None

    def _resolve_key(self, s: Slide, c: Cue, t: str, steps: list | None, base: dict) -> dict | None:
        """A key of a JSON, YAML or TOML code slide, by its path (`#params.name`, `#items[0].id`):
        the lines from the key through the end of its value, as a line cue. A key sharing its line
        with others (one-line JSON) is narrowed to its own text."""
        vis = steps[min(len(steps), max(1, self._step)) - 1] if steps else s.visual
        found = paths.key_lines(vis["code"], s.visual["lang"])
        want = paths.parse_path(t)
        hit = paths.find(found, want) if want else None
        if hit is None:
            tails = sorted({paths.show(k) for k in found if want and k[-len(want) :] == want})
            if len(tails) > 1:
                names = f"Paths ending that way: {', '.join(tails[:8])}."
            else:
                have, under = paths.near(found, want or ())
                keys = ", ".join(paths.show(k[-1:]) for k in under[:12]) or "none"
                names = f"`{paths.show(have)}` has: {keys}." if have else f"Top-level keys: {keys}."
                if want and any(p.lstrip("-").isdigit() and int(p) < 0 for p in want):
                    names += " List positions count from 0."
            self._issue(
                "cue_no_target",
                f"[{c.label}](#{t}): no key `{t}` in the {s.visual['lang']} on slide `{s.id}`. {names}",
                'Name a key by its path from the top, such as `#params.name` or `#items[0].id` (`#deps["socket.io"]` for a key '
                'with a dot), a line as `#L3`, or text as [label](# "text").',
                slide=s.id,
            )
            return None
        span = found[hit]
        a, b = vis["start"] + span.l0, vis["start"] + span.l1
        if c.text:
            lines = vis["code"].split("\n")
            within = "\n".join(lines[a - vis["start"] : b - vis["start"] + 1])
            if _norm(c.text) not in _norm(within):
                self._issue(
                    "cue_text_missing",
                    f'[{c.label}](#{t}): "{c.text}" does not occur in `{paths.show(hit)}` on slide `{s.id}`; pointed at the whole key.',
                    "Quote the code exactly, spaces included.",
                    slide=s.id,
                )
                return {**base, "kind": "lines", "side": "new", "l0": a, "l1": b, "text": ""}
        elif span.text:
            return {**base, "kind": "lines", "side": "new", "l0": a, "l1": b, "text": span.text}
        return {**base, "kind": "lines", "side": "new", "l0": a, "l1": b}


def _table_row(t: str, ids: list[str]) -> str | None:
    """A row's id, by its own id or by position (`row-2`, also when rows have their own ids)."""
    if t in ids:
        return t
    n = re.fullmatch(r"row-(\d+)", t)
    return ids[int(n.group(1)) - 1] if n and 1 <= int(n.group(1)) <= len(ids) else None


def _table_col(t: str, cols: list[str], bare: bool = False) -> int | None:
    """A column's index, by its heading's slug (`col-latency`, `col-2023`; inside a cell target the
    slug alone, `latency`, works too) or by position (`col-3`). A heading wins over a position, so
    `col-2` is the column headed `2` when there is one."""
    name = t[4:] if t.startswith("col-") else t if bare else None
    if name and name in cols:
        return cols.index(name)
    n = re.fullmatch(r"col-(\d+)", t)
    return int(n.group(1)) - 1 if n and 1 <= int(n.group(1)) <= len(cols) else None


def _table_names(ids: list[str], cols: list[str]) -> str:
    own = [r for r in ids if not re.fullmatch(r"row-\d+", r)]
    rows = f"Rows: row-1 to row-{len(ids)}" + (f" or {', '.join(own[:8])}" if own else "")
    named = [f"col-{k}" for k in cols if k]
    clash = any(k.isdigit() and 1 <= int(k) <= len(cols) and cols.index(k) != int(k) - 1 for k in cols if k)
    return (
        f"{rows}. Columns: col-1 to col-{len(cols)}"
        + (f" or {', '.join(named[:8])}" if named else "")
        + (" (a number that is also a heading names that heading's column)" if clash else "")
        + ". A cell is `#row/column`."
    )


def _region(spec: str) -> list[float] | None:
    """`left,top,width,height` in percent of the image."""
    try:
        box = [float(x) for x in spec.split(",")]
    except ValueError:
        return None
    if len(box) != 4 or any(not 0 <= v <= 100 for v in box) or box[2] <= 0 or box[3] <= 0:
        return None
    if box[0] + box[2] > 100.5 or box[1] + box[3] > 100.5:
        return None
    return box


DIAGRAM_NAMES = {
    "sequencediagram": "sequence diagram",
    "statediagram": "state diagram",
    "statediagram-v2": "state diagram",
    "classdiagram": "class diagram",
    "erdiagram": "entity-relationship diagram",
    "gantt": "Gantt chart",
    "pie": "pie chart",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _find_line(lines: list[str], text: str, first: int, near: int) -> int | None:
    t = _norm(text)
    hits = [first + i for i, ln in enumerate(lines) if t in _norm(ln)]
    if not hits:
        return None
    return min(hits, key=lambda h: abs(h - near))


def _match_title(target: str, items: list[dict]) -> str | None:
    t = target.lower().replace("-", " ").replace("_", " ")
    for it in items:
        if it["title"] and it["title"].lower() == t:
            return it["id"]
    return None


IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def infer_cues(s: Slide, say: Say) -> list[dict]:
    """Cues for narration that has none: code spans and identifiers that appear in the code,
    list titles, table first cells and diagram labels mentioned by name."""
    text = say.text
    low = text.lower()
    out: list[dict] = []
    seen: set = set()

    def add(at: int, end: int, **kw) -> None:
        key = (kw.get("kind"), kw.get("l0"), kw.get("id"), kw.get("text"))
        if key in seen or len(out) >= 4:
            return
        seen.add(key)
        out.append({"at": at, "end": end, "label": text[at:end], "target": "", "inferred": True, **kw})

    if s.kind in ("code", "diff") or kinds.line_kind(s.kind):
        sides = [("new", s.visual["new"]), ("old", s.visual["old"])] if s.kind == "diff" else [("new", s.visual)]
        for a, b in say.code:
            needle = text[a:b]
            for side, vis in sides:
                lines = vis["code"].split("\n")
                hit = _find_line(lines, needle, vis["start"], vis["start"])
                if hit:
                    add(a, b, kind="lines", side=side, l0=hit, l1=hit, text=needle)
                    break
        if not out:
            idents = set()
            for _, vis in sides:
                idents |= {m.group(0) for m in IDENT_RE.finditer(vis["code"]) if len(m.group(0)) >= 4}
            for m in IDENT_RE.finditer(text):
                w = m.group(0)
                if w in idents and w.lower() not in STOPWORDS and (not w.islower() or "_" in w or len(w) >= 6):
                    for side, vis in sides:
                        hit = _find_line(vis["code"].split("\n"), w, vis["start"], vis["start"])
                        if hit:
                            add(m.start(), m.end(), kind="lines", side=side, l0=hit, l1=hit, text=w)
                            break
    elif s.kind == "list":
        for it in s.visual["items"]:
            title = it["title"] or it["body"]
            if len(title) < 3:
                continue
            i = _find_word(low, title.lower())
            if i is not None:
                add(i, i + len(title), kind="item", id=it["id"])
    elif kinds.part_kind(s.kind) and kinds.get(s.kind).infer:
        for pid, name in kinds.get(s.kind).labels(s.visual):
            i = _find_word(low, name.lower()) if len(name) >= 3 else None
            if i is not None:
                add(i, i + len(name), kind="item", id=pid)
    elif s.kind == "table":
        for rid, row in zip(s.visual.get("ids", []), s.visual.get("rows", []), strict=False):
            cell = inline_plain(row[0]) if row else ""
            i = _find_word(low, cell.lower()) if len(cell) >= 3 else None
            if i is not None:
                add(i, i + len(cell), kind="row", id=rid)
    elif s.kind == "diagram":
        for nid, label in s.visual.get("nodes", {}).items():
            if len(label) < 3:
                continue
            i = _find_word(low, label.lower())
            if i is not None:
                add(i, i + len(label), kind="node", id=nid, text=label)
    return sorted(out, key=lambda c: c["at"])


def _find_word(hay: str, needle: str) -> int | None:
    m = re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", hay)
    return m.start() if m else None
