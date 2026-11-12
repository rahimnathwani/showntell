"""Bundled slide kinds beyond the core set: charts, numbers, exchanges, terminal sessions, file trees,
drawings, draw.io diagrams and reladraw diagrams.

Each module defines `KIND`, a `base.Kind` with its fence languages, parser, size check and how
cues find its parts; the builder looks kinds up here instead of naming them. Every part a cue can
point at has an id, and the parts come in source order, so cues, step reveals and checks work as
they do for lists. The catalogue the agent can read is text/kinds.json (resource
showntell://slide-kinds); a block's problems quote that kind's `format` entry as the fix.
"""

from __future__ import annotations

from . import chart, chat, drawio, reladraw, stat, svg, terminal, tree
from .base import Kind, find_part, labels
from .chart import parse_chart
from .chat import parse_chat
from .stat import parse_stat
from .terminal import parse_terminal
from .tree import parse_tree

KINDS: dict[str, Kind] = {
    k.name: k for k in (chart.KIND, stat.KIND, chat.KIND, terminal.KIND, tree.KIND, svg.KIND, drawio.KIND, reladraw.KIND)
}
BY_LANG: dict[str, Kind] = {lang: k for k in KINDS.values() for lang in k.langs}

__all__ = [
    "BY_LANG",
    "KINDS",
    "Kind",
    "find_part",
    "for_lang",
    "get",
    "labels",
    "line_kind",
    "parse_chart",
    "parse_chat",
    "parse_stat",
    "parse_terminal",
    "parse_tree",
    "part_kind",
]


def for_lang(lang: str) -> Kind | None:
    """The kind a fenced block makes, from its language."""
    return BY_LANG.get(lang)


def get(name: str) -> Kind | None:
    """The bundled kind of a slide, from its `kind`; None for core kinds such as code or list."""
    return KINDS.get(name)


def line_kind(name: str) -> bool:
    """Whether a bundled kind's parts are code lines, cued and checked as code is (terminal)."""
    k = KINDS.get(name)
    return bool(k and k.lines)


def part_kind(name: str) -> bool:
    """Whether a bundled kind's parts are named items: bars, series, entries, numbers, messages."""
    k = KINDS.get(name)
    return bool(k and not k.lines)
