"""Slides from blocks: title slide, ids, visuals, diffs, code from disk, images and questions."""

from __future__ import annotations

import pytest


def by_id(w, sid):
    return next(s for s in w.slides if s.id == sid)


# -- slides and ids ---------------------------------------------------------------------------


def test_each_heading_starts_a_slide_whatever_its_level(build):
    w, _ = build("# One\n\nA.\n\n## Two\n\nB.\n\n### Three\n\nC.\n")
    assert [s.id for s in w.slides] == ["one", "two", "three"]
    assert [s.title for s in w.slides] == ["One", "Two", "Three"]


def test_first_slide_without_visual_is_a_title_slide_with_subtitle(build):
    w, _ = build("# How caching works\n> A read-through cache\n\nEvery request checks the cache.\n")
    (s,) = w.slides
    assert (s.kind, s.shown) == ("title", "A read-through cache")
    assert s.says[0].text == "Every request checks the cache."


def test_text_before_first_heading_gets_an_implicit_title_slide(build):
    w, _ = build("Opening words.\n\n## Next\n\nMore.\n", title="My Walk")
    assert [(s.id, s.kind, s.title) for s in w.slides] == [("my-walk", "title", "My Walk"), ("next", "text", "Next")]
    assert w.slides[0].says[0].text == "Opening words."


def test_later_slide_without_visual_is_text(build):
    w, _ = build("# T\n\nA.\n\n## Plain\n\nB.\n")
    assert w.slides[1].kind == "text"


def test_explicit_id_and_classes(build):
    w, _ = build("# T\n\nA.\n\n## The lookup {#lookup .build}\n\nB.\n")
    s = w.slides[1]
    assert (s.id, s.classes) == ("lookup", ["build"])


def test_heading_attributes_other_than_id_and_classes_are_kept(build):
    w, _ = build('# T\n\nA.\n\n## D {alt="Request flow"}\n\nB.\n')
    assert w.slides[1].attrs == {"alt": "Request flow"}


def test_generated_ids_are_unique(build):
    w, _ = build("# Intro\n\nA.\n\n## Intro\n\nB.\n\n## Intro\n\nC.\n\n## X {#intro}\n\nD.\n")
    assert [s.id for s in w.slides] == ["intro", "intro-2", "intro-3", "intro-4"]


def test_ids_stay_stable_as_slides_are_added(build):
    w, b = build("# T\n\nA.\n\n## The lookup\n\nB.\n")
    first = [s.id for s in w.slides]
    b.add("## Storage\n\nC.\n\n## The lookup\n\nD.\n")
    assert [s.id for s in w.slides][:2] == first
    assert [s.id for s in w.slides][2:] == ["storage", "the-lookup-2"]


def test_slug_of_symbols_only_title(build):
    w, _ = build("# T\n\nA.\n\n## ???\n\nB.\n")
    assert w.slides[1].id == "slide"


def test_heading_markup_is_stripped_from_title(build):
    w, _ = build("# The `get` **method**\n\nA.\n")
    assert w.slides[0].title == "The get method"


def test_narration_ids_and_pause(build):
    w, _ = build("# T\n\nOne.\n\n{pause=1.5}\n\nTwo.\n")
    says = w.slides[0].says
    assert [x.id for x in says] == ["t.1.1", "t.2.1", "t.3.1"]
    assert (says[1].pause, says[1].status) == (1.5, "ready")


def test_long_pause_is_capped(build):
    w, _ = build("# T\n\nOne.\n\n{pause=30}\n")
    assert w.slides[0].says[1].pause == 5.0


def test_quote_on_later_slide_is_shown_text_not_narration(build):
    w, _ = build("# T\n\nA.\n\n## S\n> on screen\n> second line\n\nSpoken.\n")
    s = w.slides[1]
    assert s.shown == "on screen\nsecond line" and [x.text for x in s.says] == ["Spoken."]


def test_front_matter_sets_walkthrough_settings(build):
    w, _ = build("---\ntitle: From front\nframe: portrait\nvoice: am_adam\nspeed: 1.1\n---\n# T\n\nA.\n")
    assert (w.title, w.frame, w.voice, w.speed) == ("From front", "portrait", "am_adam", 1.1)


def test_front_matter_voice_may_be_described(build):
    w, b = build("---\nvoice: British male\n---\n# T\n\nA.\n")
    assert w.voice == "bm_george" and not b.issues
    w, b = build("---\nvoice: Australian male\n---\n# T\n\nA.\n")
    assert w.voice == "af_heart" and any("no voice is australian" in i for i in b.issues)


def test_front_matter_values_are_validated(build):
    w, b = build("---\nframe: banana\nvoice: nobody\nspeed: 9\n---\n# T\n\nA.\n")
    assert w.frame == "landscape" and w.voice == "af_heart" and w.speed <= 1.4
    assert b.issues


def test_footnote_definitions_are_collected(build):
    w, _ = build("# T\n\nA fact[^src].\n\n[^src]: The paper.\n")
    assert w.notes == {"src": "The paper."} and w.slides[0].says[0].notes == ["src"]
    assert "cited" not in w.public()  # a walkthrough without quoted lines reads as before


def test_a_source_can_quote_project_lines(build, project):
    w, b = build("# T\n\nThe cache reads first[^get].\n\n[^get]: file=src/cache.py lines=3-5 The read path\n", root=project)
    assert w.notes["get"] == "The read path (src/cache.py, lines 3-5)" and not b.issues
    assert w.cited["get"]["file"] == "src/cache.py" and w.cited["get"]["start"] == 3
    assert len(w.cited["get"]["code"].split("\n")) == 3 and w.public()["cited"] == w.cited
    w, _ = build("# T\n\nA fact[^f].\n\n[^f]: file=src/cache.py\n", root=project)
    assert w.notes["f"] == "src/cache.py" and len(w.cited["f"]["code"].split("\n")) == 30  # held to what the transcript shows


def test_a_source_that_cannot_be_read_or_is_long_is_reported(build, project):
    _, b = build("# T\n\nA fact[^x].\n\n[^x]: file=src/missing.py lines=1-3\n", root=project)
    assert any(i.code == "source_error" and "[^x]" in i for i in b.issues)
    _, b = build("# T\n\nA fact[^x].\n\n[^x]: file=src/cache.py\n", root=project)
    assert any(i.code == "note_long" for i in b.issues)


def test_a_source_quoting_long_lines_cuts_them(build, project):
    (project / "src" / "min.js").write_text("x=" + "1" * 5000 + "\n")
    w, b = build("# T\n\nA fact[^m].\n\n[^m]: file=src/min.js lines=1\n", root=project)
    assert len(w.cited["m"]["code"]) == 201 and w.cited["m"]["code"].endswith("…")
    assert any(i.code == "note_long" and "200 characters" in i for i in b.issues)


def test_finish_reports_sources_missing_and_unused(build):
    w, b = build("# T\n\nOne[^a] and two[^b].\n\n[^a]: Here.\n[^c]: Nowhere.\n")
    b.issues = []
    b.check_all()
    codes = {i.code: i for i in b.issues}
    assert "[^b]" in codes["note_missing"] and "[^c]" in codes["note_unused"] and "[^a]" not in codes["note_unused"]


# -- visuals ----------------------------------------------------------------------------------


def test_inline_code_with_start(build):
    w, _ = build('# T\n\nA.\n\n## C\n\n```python start=10 title="Cache"\ndef get():\n    pass\n```\n\nB.\n')
    s = w.slides[1]
    assert s.kind == "code"
    assert s.visual == {"lang": "python", "code": "def get():\n    pass", "start": 10, "title": "Cache"}


def test_code_from_disk_keeps_real_line_numbers(build, project):
    w, _ = build("# T\n\nA.\n\n## C\n\n```python file=src/cache.py lines=10-14\n```\n\nB.\n", root=project)
    v = w.slides[1].visual
    assert v["start"] == 10 and v["file"] == "src/cache.py" and v["title"] == "cache.py"
    assert v["code"].split("\n")[0] == "class Cache:" and len(v["code"].split("\n")) == 5


def test_code_from_disk_with_body_reports_and_shows_file(build, project):
    w, b = build("# T\n\nA.\n\n## C\n\n```python file=src/cache.py lines=10\nretyped\n```\n\nB.\n", root=project)
    assert w.slides[1].visual["code"] == "class Cache:"
    assert any("had a body" in i for i in b.issues)


def test_missing_file_is_an_issue(build, project):
    w, b = build("# T\n\nA.\n\n## C\n\n```python file=src/nope.py\n```\n\nB.\n", root=project)
    assert any("not found" in i for i in b.issues)


def test_file_outside_root_is_an_issue(build, project):
    _, b = build("# T\n\nA.\n\n## C\n\n```python file=../../etc/passwd\n```\n\nB.\n", root=project)
    assert any("outside the project" in i for i in b.issues)


def test_math_fence_and_dollars(build):
    w, _ = build("# T\n\nA.\n\n## M\n\n```math\na^2\n```\n\nB.\n\n## N\n\n$$ b^2 $$\n\nC.\n")
    assert [(s.kind, s.visual) for s in w.slides[1:]] == [("math", {"latex": "a^2"}), ("math", {"latex": "b^2"})]


def test_table_rows_get_ids(build):
    w, _ = build("# T\n\nA.\n\n## Tab\n\n| Name | Speed |\n|---|---|\n| mem | fast {#mem} |\n| disk | slow |\n\nB.\n")
    v = w.slides[1].visual
    assert w.slides[1].kind == "table"
    assert v["ids"] == ["mem", "row-2"] and v["rows"][0] == ["mem", "fast"]


def test_list_items_get_titles_bodies_and_ids(build):
    w, _ = build("# T\n\nA.\n\n## L\n\n- **Memory**: fast {#memory}\n- **Disk**: survives\n- plain item\n\nB.\n")
    v = w.slides[1].visual
    assert v["style"] == "bullets"
    assert [(i["id"], i["title"], i["body"]) for i in v["items"]] == [
        ("memory", "Memory", "fast"),
        ("disk", "Disk", "survives"),
        ("plain-item", "", "plain item"),
    ]


@pytest.mark.parametrize(
    "md,style",
    [
        ("## L {.cards}\n\n- a\n- b\n", "cards"),
        ("## L {.steps}\n\n- a\n- b\n", "steps"),
        ("## L\n\n1. a\n2. b\n", "steps"),
    ],
)
def test_list_styles(build, md, style):
    w, _ = build("# T\n\nA.\n\n" + md + "\nB.\n")
    assert w.slides[1].visual["style"] == style


def test_two_lists_on_one_slide_merge(build):
    w, b = build("# T\n\nA.\n\n## L\n\n- a\n\n- b\n\nB.\n")
    assert [i["body"] for i in w.slides[1].visual["items"]] == ["a", "b"] and len(w.slides) == 2


def test_duplicate_item_ids_are_made_unique(build):
    w, _ = build("# T\n\nA.\n\n## L\n\n- same\n- same\n\nB.\n")
    assert [i["id"] for i in w.slides[1].visual["items"]] == ["same", "same-2"]


def test_image_from_project_is_copied(build, project, tmp_path):
    w, b = build("# T\n\nA.\n\n## I\n\n![Request timeline](docs/timeline.png)\n\nB.\n", root=project)
    v = w.slides[1].visual
    assert w.slides[1].kind == "image" and v["alt"] == "Request timeline"
    assert v["src"].startswith("assets/") and (tmp_path / v["src"]).read_bytes().startswith(b"\x89PNG")
    assert not b.issues


def test_image_url_is_shown_as_is(build):
    w, _ = build("# T\n\nA.\n\n## I\n\n![Plot](https://example.com/p.png)\n\nB.\n")
    assert w.slides[1].visual == {"src": "https://example.com/p.png", "alt": "Plot"}


def test_image_without_alt_is_an_issue(build, project):
    _, b = build("# T\n\nA.\n\n## I\n\n![](docs/timeline.png)\n\nB.\n", root=project)
    assert any("no alt text" in i for i in b.issues)


def test_non_image_file_is_refused(build, project):
    w, b = build("# T\n\nA.\n\n## I\n\n![Notes](docs/notes.txt)\n\nB.\n", root=project)
    assert any("not an image" in i for i in b.issues) and not w.slides[1].visual


def test_image_outside_root_is_refused(build, project, tmp_path):
    (tmp_path / "outside.png").write_bytes(b"x")
    _, b = build("# T\n\nA.\n\n## I\n\n![Out](../outside.png)\n\nB.\n", root=project)
    assert any("outside the project" in i for i in b.issues)


def test_second_visual_on_a_slide_gets_its_own_slide(build):
    w, b = build("# T\n\nA.\n\n## Two\n\n```python\nx = 1\n```\n\n```python\ny = 2\n```\n\nSpoken.\n")
    assert [(s.id, s.title, s.kind) for s in w.slides[1:]] == [("two", "Two", "code"), ("two-2", "Two", "code")]
    assert w.slides[2].visual["code"] == "y = 2"
    assert any("already shows a code" in i for i in b.issues)


def test_questions_slide_is_not_shown_and_fills_questions(build):
    w, _ = build("# T\n\nA.\n\n## Questions {.questions}\n\n- What about **eviction**?\n- Why not Redis?\n")
    assert [s.id for s in w.slides] == ["t"]
    assert w.questions == ["What about eviction?", "Why not Redis?"]


def test_heading_after_questions_starts_slides_again(build):
    w, _ = build("# T\n\nA.\n\n## Q {.questions}\n\n- One?\n\n## Back\n\nB.\n")
    assert [s.id for s in w.slides] == ["t", "back"] and w.questions == ["One?"]


# -- diffs ------------------------------------------------------------------------------------


OLD_NEW = "```python old\ntotal = 0\nfor x in xs: total += x\n```\n```python new\ntotal = sum(xs)\n```\n"


def test_old_and_new_blocks_make_a_diff(build):
    w, _ = build("# T\n\nA.\n\n## D\n\n" + OLD_NEW + "\nB.\n")
    s = w.slides[1]
    assert s.kind == "diff" and s.visual["lang"] == "python"
    assert (s.visual["old"]["code"], s.visual["old"]["start"]) == ("total = 0\nfor x in xs: total += x", 1)
    assert (s.visual["new"]["code"], s.visual["new"]["start"]) == ("total = sum(xs)", 1)


def test_old_block_may_arrive_in_an_earlier_call(build):
    w, b = build("# T\n\nA.\n\n## D\n\n```python old\na\n```\n")
    b.add("```python new\nb\n```\n\nB.\n")
    assert w.slides[1].kind == "diff" and w.slides[1].visual["new"]["code"] == "b"


def test_old_block_without_new_shows_as_code(build):
    w, b = build("# T\n\nA.\n\n## D\n\n```python old\na = 1\n```\n\nSpoken.\n")
    assert w.slides[1].kind == "code" and w.slides[1].visual["code"] == "a = 1"
    assert any("no `new` block" in i for i in b.issues)


def test_unified_diff_block_numbers_from_hunk_header(build):
    w, _ = build("# T\n\nA.\n\n## D\n\n```diff\n@@ -10,3 +20,2 @@\n keep\n-total = 0\n-x\n+total = sum(xs)\n```\n\nB.\n")
    v = w.slides[1].visual
    assert (v["old"]["start"], v["new"]["start"]) == (10, 20)
    assert v["new"]["code"] == "keep\ntotal = sum(xs)"


def test_git_diff_block(build, repo):
    repo.write("src/stats.py", "".join(f"line {i}\n" for i in range(1, 11)))
    repo.commit("one")
    repo.write("src/stats.py", "".join(f"line {i}\n" for i in range(1, 11)).replace("line 6", "line six"))
    w, b = build("# T\n\nA.\n\n## D\n\n```diff file=src/stats.py rev=HEAD\n```\n\nB.\n", root=repo.root, check_last=False)
    v = w.slides[1].visual
    assert w.slides[1].kind == "diff" and v["lang"] == "python" and v["rev"] == "HEAD"
    assert "line six" in v["new"]["code"] and "line 6" in v["old"]["code"]
    assert v["new"]["start"] == 3
    assert w.sha and not b.issues


def test_git_diff_block_error_is_an_issue(build, repo):
    repo.write("a.py", "x\n")
    repo.commit("one")
    _, b = build("# T\n\nA.\n\n## D\n\n```diff file=a.py rev=HEAD\n```\n\nB.\n", root=repo.root)
    assert any("no changes" in i for i in b.issues)
