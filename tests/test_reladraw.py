"""reladraw diagrams: what the builder reads from the source (node names and texts for cues,
top-level nodes for reveals), statements over several lines, comments and pasted blocks."""

from __future__ import annotations

from showntell.kinds.reladraw import find, parse_reladraw

OPEN = "# T\n\nOpening.\n\n"
SRC = '''// a comment line
style hot  fill: theme-primary
node web "Web app"  layout: column
node web.ui "Interface"   // a trailing comment, with a "quote"
node web.api
  // an indented comment continues the statement
node store "Data / base"  right of web (gap: wide)
  shape: cylinder
icon crate """
<svg viewBox="0 0 24 24"><text>node fake "Not a node"</text></svg>
"""
guide node ruler  below web
node esc "Say \\"hi\\""  below store
edge web.api -> store "queries"
'''


def last(build, md: str, **kw):
    w, b = build(OPEN + md, **kw)
    b.check_all()
    return w.slides[-1], [i.code for i in b.issues], b


def test_nodes_children_and_texts():
    v, problems = parse_reladraw(SRC, {})
    assert not problems
    labels = {i["id"]: i["label"] for i in v["items"]}
    assert list(labels) == ["web", "web.ui", "web.api", "store", "esc"]  # no icon text, no guide
    assert labels["web.api"] == "api" and labels["store"] == "Data base" and labels["esc"] == 'Say "hi"'
    assert v["ids"] == ["web", "store", "esc"] and v["part_of"] == {"web.ui": "web", "web.api": "web"}
    assert v["source"].startswith("// a comment line")


def test_find_by_name_text_or_slug():
    v, _ = parse_reladraw(SRC, {})
    assert [find(v, t) for t in ("web.ui", "Interface", "data-base", "Web app", "nothing")] == ["web.ui", "web.ui", "store", "web", None]


def test_empty_and_nodeless_blocks_are_refused():
    assert parse_reladraw("  \n", {})[1] == ["the block is empty"]
    assert "no `node` statement" in parse_reladraw("style a  fill: red\n", {})[1][0]


def test_cues_reveal_and_file(build, project):
    (project / "arch.reladraw").write_text(SRC)
    s, codes, _ = last(
        build,
        "## A {#a .build}\n\n```reladraw file=arch.reladraw\n```\n\n"
        "The [web app](#web) has [an interface](#Interface); [the API](#web.api) queries [the store](#store) "
        'in [this corner](#@60,0,40,50). The [label](# "Data") names it.\n',
        root=project,
    )
    cues = [c for say in s.says for c in say.cues]
    assert s.kind == "reladraw" and [c["kind"] for c in cues] == ["item", "item", "item", "item", "region", "text"]
    assert cues[1]["id"] == "web.ui" and cues[1]["part"] == "web" and cues[3]["id"] == "store"
    assert not [c for c in codes if c.startswith("cue") or c == "build_cue_order"], codes
    _, codes, b = last(build, '## B\n\n```reladraw\nnode a "A"\n```\n\nThe [cache](#cache).\n')
    assert "cue_no_target" in codes
    _, codes, _ = last(build, "## C\n\n```reladraw file=missing.reladraw\n```\n\nNothing.\n", root=project)
    assert "source_error" in codes
