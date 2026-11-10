"""Icons: Lucide's set (ISC), bundled as data/icons.json and copied into the slides that use them.

An icon is named like `:database:` at the start of a list item, a stat label or a `>` line,
`{icon=rocket}` on a heading, or `<use href="icon:cloud"/>` in a drawing. The builder copies each
one's SVG into the slide, so the player and exports need no icon file.
"""

from __future__ import annotations

import difflib
import json
import re
from functools import lru_cache
from pathlib import Path

FILE = Path(__file__).parent / "data" / "icons.json"
LEADING = re.compile(r"^:([a-z0-9]+(?:-[a-z0-9]+)*):\s*")


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads(FILE.read_text(encoding="utf-8"))


def get(name: str) -> str | None:
    """The icon's inner SVG (24 by 24, stroked), or None."""
    return _data()["icons"].get(name)


def suggest(name: str, n: int = 4) -> list[str]:
    """Icon names close to `name`: near spellings first, then icons tagged with its words."""
    icons, tags = _data()["icons"], _data()["tags"]
    out = difflib.get_close_matches(name, icons.keys(), n=n, cutoff=0.8)
    words = set(name.replace("-", " ").split())
    for k, t in tags.items():
        if len(out) >= n:
            break
        if k not in out and words & ({w for x in t for w in x.split()} | set(k.split("-"))):
            out.append(k)
    for k in difflib.get_close_matches(name, icons.keys(), n=n, cutoff=0.6):
        if len(out) < n and k not in out:
            out.append(k)
    return out


def split_leading(text: str) -> tuple[str, str]:
    """`:name: rest` -> (name, rest); ("", text) when the text does not start with an icon."""
    m = LEADING.match(text or "")
    return (m.group(1), text[m.end() :]) if m else ("", text)
