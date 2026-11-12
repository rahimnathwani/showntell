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
description store """
node fake "Not a node"
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


def test_pasted_icons_are_refused_even_on_one_line():
    one = 'icon c """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M2 2h20v20H2z"/></svg>"""\nnode a "A"  badge: c\n'
    many = 'node a "A"\nicon c """\n<svg viewBox="0 0 1 1"/>\n"""\n'
    for src in (one, many):
        vis, problems = parse_reladraw(src, {})
        assert vis == {} and len(problems) == 1 and "pastes a picture" in problems[0], problems
    vis, problems = parse_reladraw('icon gh  letters: "GH"\nnode a "A"  icon: gh\nnode b "B"  icon: database\n', {})
    assert not problems and [i["label"] for i in vis["items"]] == ["A", "B"]


def test_a_description_string_hides_its_lines_and_markup_reads_as_text():
    src = 'node a "A"\ndescription a """\nnode fake "x"  // http://not.a.comment\n"""\nnode b "shared / [dim]synced[/dim]"  below a\nnode c  icon: database\n'
    vis, problems = parse_reladraw(src, {})
    assert not problems and [(i["id"], i["label"]) for i in vis["items"]] == [("a", "A"), ("b", "shared synced"), ("c", "")]


def test_named_lines_carry_their_ends_and_too_big_is_refused():
    vis, _ = parse_reladraw('node a\nnode b  right of a\nedge req a -> b "named"\nedge a -> b\nedge ghost a -> z\n', {})
    assert vis["edges"] == {"req": ["a", "b"]}
    many = "\n".join(f"node n{i}" for i in range(81))
    assert "at most 80 boxes" in parse_reladraw(many, {})[1][0]
    lines = "node a\nnode b  right of a\n" + "\n".join("edge a -> b" for _ in range(161))
    assert "161 lines" in parse_reladraw(lines, {})[1][0]


def test_find_prefers_text_over_a_name_in_another_case():
    vis, _ = parse_reladraw('node a "Cache"\nnode cache "Store"\n', {})
    assert find(vis, "Cache") == "a" and find(vis, "cache") == "cache" and find(vis, "Store") == "cache"


def test_leading_blank_lines_are_kept_so_line_numbers_match():
    vis, _ = parse_reladraw('\n\nnode a "A"\n', {})
    assert vis["source"].split("\n")[2] == 'node a "A"'


def test_texts_before_strings_hyphenated_names_commas_and_backgrounds():
    # "x""" is a text and an empty text, not a string: nothing after it is skipped
    vis, problems = parse_reladraw('node b "B"\nnode c "C"  right of b\nedge b -> c "x"""\nicon p """<svg/>"""\n', {})
    assert problems and "pastes a picture" in problems[0]
    vis, _ = parse_reladraw('node web-app "Web"\nnode db  right of web-app\nedge hy web-app -> db\nedge x o--|> y\n', {})
    assert vis["edges"] == {"hy": ["web-app", "db"]}
    lines = "node a\nnode b  right of a\n" + "\n".join('edge a -> b "x, y, z, w"' for _ in range(41))
    assert parse_reladraw(lines, {})[1] == []
    assert "diagram background:" in parse_reladraw("diagram  background: white\nnode a\n", {})[1][0]
