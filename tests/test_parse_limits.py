"""Hostile Markdown at parse time: inputs of 64 KB (a hosted server's limit per call) that once
took seconds or failed the call build quickly, and a compressed draw.io page cannot expand without bound."""

from __future__ import annotations

import base64
import time
import zlib

import pytest

from showntell.build import Builder
from showntell.kinds.drawio import MAX_INFLATED, _decompress
from showntell.model import Walkthrough


@pytest.mark.parametrize(
    "md",
    [
        "_a " * 21000,
        "[a](" * 16000,
        "[" * 64000,
        "`a.b_c`" * 9000,
        "```svg\n<svg viewBox='0 0 10 10'>" + "<g>" * 7000 + "</g>" * 7000 + "</svg>\n```\n",
        "".join(f"{'  ' * (i % 40)}- x\n" for i in range(700)),
    ],
    ids=["underscores", "links", "brackets", "code", "deep-svg", "deep-list"],
)
def test_pathological_markdown_at_the_cap_builds_quickly(tmp_path, md):
    md = "## T\n\n" + md
    assert len(md.encode()) <= 64 * 1024 + 64
    w = Walkthrough(id="w" * 22, key="k" * 22, report_key="r" * 16, title="T")
    t0 = time.perf_counter()
    b = Builder(w, tmp_path / "assets")
    b.add(md)
    b.check_all()
    assert time.perf_counter() - t0 < 2.0


def test_a_drawio_decompression_bomb_is_refused():
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    bomb = base64.b64encode(c.compress(b"a" * (MAX_INFLATED + 1000)) + c.flush()).decode()
    with pytest.raises(ValueError, match="expands past"):
        _decompress(bomb)
