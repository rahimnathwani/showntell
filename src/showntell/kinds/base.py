"""What the bundled kinds share: the `Kind` record the builder dispatches on, part ids, and cue lookup."""

from __future__ import annotations

import functools
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

CATALOGUE = Path(__file__).resolve().parent.parent / "text" / "kinds.json"

# A size finding: (code, message, fix).
Finding = tuple[str, str, str]


def _no_check(vis: dict, line: int, limits: dict) -> list[Finding]:
    return []


@dataclass(frozen=True)
class Kind:
    """A slide kind made from a fenced block.

    `parse(body, attrs)` returns the visual the player draws and problems with the block, each
    reported as `error_code`. `check(visual, line, limits)` returns size findings. A kind whose
    parts are code lines (`lines`) takes #L cues and the code size checks; otherwise cues name
    parts through `find` (by default `find_part`), and `part(visual)` is what a missing target
    is called. A part nested in another names its outer part in the visual's `part_of`."""

    name: str
    langs: tuple[str, ...]
    parse: Callable[[str, dict], tuple[dict, list[str]]]
    check: Callable[[dict, int, dict], list[Finding]] = _no_check
    lines: bool = False
    part: Callable[[dict], str] = lambda vis: "part"
    error_code: str = "block_error"
    hint: str = ""  # said before the format when a block has problems
    infer: bool = True  # cues may be inferred from narration that names a part
    find: Callable[[dict, str], str | None] = lambda vis, target: find_part(vis, target)
    labels: Callable[[dict], list[tuple[str, str]]] = lambda vis: labels(vis)
    reads_file: bool = False  # `file=` reads the block's body from the project
    regions: bool = False  # cues may point at `#@left,top,width,height` in percent of the visual
    # Consecutive blocks marked `step` are versions of one visual, each kept as these keys of its
    # visual; #step-N moves between them. Empty: the kind has no steps.
    steps: tuple[str, ...] = ()

    def fix(self) -> str:
        """The fix for a problem with the block: the hint, then the format from the catalogue."""
        fmt = catalogue_format(self.name)
        return " ".join(x for x in (self.hint, f"Format: {fmt}" if fmt else "") if x)


@functools.cache
def catalogue_format(name: str) -> str:
    """A kind's `format` entry in text/kinds.json, the same text the slide-kinds resource gives."""
    for k in json.loads(CATALOGUE.read_text(encoding="utf-8"))["kinds"]:
        if k["kind"] == name:
            return k.get("format", "")
    return ""


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "item"


def find_part(vis: dict, target: str) -> str | None:
    """Id of the chart bar, series or tree entry a cue names: its id, label, path or file name."""
    t = target.strip().lstrip("/")
    ids = vis.get("ids", [])
    if t in ids or t in [n["id"] for n in vis.get("notes", [])]:
        return t
    low = t.lower()
    hits = []
    for part in vis.get("categories", []) + vis.get("series", []) + vis.get("items", []):
        names = {part.get("label", ""), part.get("name", ""), part.get("name", "").rstrip("/")}
        if (low in {n.lower() for n in names if n} or slug(t) == part.get("id")) and part.get("id") in ids:
            hits.append(part["id"])
    if len(set(hits)) == 1:
        return hits[0]
    tails = [i for i in ids if i.endswith("/" + t)]
    return tails[0] if len(tails) == 1 else None


def labels(vis: dict) -> list[tuple[str, str]]:
    """(id, spoken name) for every part a cue can point at, used to infer cues from narration."""
    out = []
    for part in vis.get("categories", []) + vis.get("series", []) + vis.get("items", []):
        if part.get("id") not in vis.get("ids", []):
            continue
        name = part.get("label") or part.get("name", "").rstrip("/")
        out.append((part["id"], name))
    return out
