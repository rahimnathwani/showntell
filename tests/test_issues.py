"""Structured findings: codes, severities, folding, player reports, and the checks added with them."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from showntell.issues import CODES, Issue, as_dict, line_text, player_issue, summarize

OPENING = "# How caching works\n\nA cache keeps answers close.\n"


def codes(issues) -> list[str]:
    return [i.code if isinstance(i, Issue) else i["code"] for i in issues]


# -- the Issue type --------------------------------------------------------------------------------


def test_issue_is_its_text_and_carries_fields():
    i = Issue("code_too_tall", "Code shows 30 lines.", "Show fewer.", slide="s", line=4)
    assert i == "Code shows 30 lines. Show fewer." and "30 lines" in i
    assert i.to_dict() == {
        "code": "code_too_tall",
        "severity": "warning",
        "message": "Code shows 30 lines.",
        "fix": "Show fewer.",
        "slide": "s",
        "line": 4,
    }


def test_issue_survives_deepcopy_and_pickle():
    import copy
    import pickle

    i = Issue("code_too_tall", "Code shows 30 lines.", "Show fewer.", slide="s", line=4, severity="error", source="player")
    for j in (copy.deepcopy(i), pickle.loads(pickle.dumps(i))):
        assert j == i and j.to_dict() == i.to_dict()


def test_unknown_code_is_refused():
    with pytest.raises(KeyError):
        Issue("made_up", "x")


def test_plain_strings_become_code_other():
    assert as_dict("old style") == {"code": "other", "severity": "warning", "message": "old style"}


def test_summarize_orders_by_severity_and_folds_repeats():
    items = [Issue("no_cues", f"n{i}").to_dict() for i in range(5)] + [Issue("cue_off_code", "bad").to_dict()]
    kept, notes = summarize(items, limit=12, per_code=3)
    assert kept[0]["code"] == "cue_off_code" and codes(kept).count("no_cues") == 3
    assert notes == ["…and 2 more `no_cues` findings"]
    kept, notes = summarize(items, limit=2, per_code=3)
    assert len(kept) == 2 and any("no_cues" in n for n in notes)


def test_line_text_marks_errors_and_names_the_code():
    assert (
        line_text(Issue("cue_off_code", "Past the end.", "Use gutter numbers.").to_dict())
        == "- Error: Past the end. Use gutter numbers. (cue_off_code)"
    )
    assert line_text(Issue("no_cues", "No cues.").to_dict()) == "- No cues. (no_cues)"


def test_player_issue_keeps_known_codes_and_trims():
    i = player_issue({"code": "text_too_small", "slide": "lookup", "message": "  shrunk   to 40% "})
    assert (i.code, i.slide, i.source, str(i)) == ("text_too_small", "lookup", "player", "shrunk to 40%")
    assert player_issue({"code": "cue_off_code", "message": "x"}).code == "player_error"  # server codes are not the player's to use
    assert player_issue({"message": ""}) is None
    assert len(player_issue({"message": "y" * 1000})) == 300


def test_every_code_is_documented_in_the_spec():
    spec = (Path(__file__).parent.parent / "docs" / "spec.md").read_text()
    missing = [c for c in CODES if f"`{c}`" not in spec]
    assert not missing, f"document these codes in docs/spec.md: {missing}"


def test_every_code_used_in_the_source_exists():
    src = Path(__file__).parent.parent / "src" / "showntell"
    used = set()
    for f in src.glob("*.py"):
        used |= set(re.findall(r"""(?:_issue|Issue)\(\s*["']([a-z_]+)["']""", f.read_text()))
    assert used and used <= set(CODES), used - set(CODES)


# -- builder checks -------------------------------------------------------------------------------


def test_builder_issues_name_slide_and_line(build):
    _, b = build("# T\n\nA.\n\n## Big {#big}\n\n" + "```python\n" + "x\n" * 30 + "```\n\nSay it.\n")
    (i,) = [i for i in b.issues if i.code == "code_too_tall"]
    assert i.slide == "big" and i.fix


@pytest.mark.parametrize(
    "text, what",
    [
        ("Thanks to @octocat for this.", "a handle"),
        ("See https://example.com/x for more.", "a URL"),
        ("Commit 3f9a2c1b fixed it.", "a commit hash"),
    ],
)
def test_spoken_handles_urls_and_hashes_are_flagged(build, text, what):
    _, b = build(f"# T\n\nA.\n\n## S {{#s}}\n\n- **One**: a\n\n{text}\n")
    b.check_all()
    assert any(i.code == "spoken_handle" and what in i for i in b.issues)


@pytest.mark.parametrize(
    "text", ["Email me at me@example.com.", "The 1024 byte limit.", "Use `3f9a2c1b` here.", "A decade of facade work."]
)
def test_ordinary_text_is_not_a_handle(build, text):
    _, b = build(f"# T\n\nA.\n\n## S {{#s}}\n\n- **One**: a\n\n{text}\n")
    b.check_all()
    assert "spoken_handle" not in codes(b.issues)


LIST3 = "- **Parse**: a\n- **Plan**: b\n- **Run**: c\n"


def test_build_cues_all_in_first_paragraph_are_flagged(build):
    md = f"# T\n\nA.\n\n## Steps {{#steps .build}}\n\n{LIST3}\nWe [parse](#parse), [plan](#plan) and [run](#run).\n\nThen a lot more words follow here.\n"
    _, b = build(md)
    b.check_all()
    assert "build_cues_bunched" in codes(b.issues)


def test_build_cues_out_of_order_are_flagged(build):
    md = f"# T\n\nA.\n\n## Steps {{#steps .build}}\n\n{LIST3}\nFirst [run](#run).\n\nThen [parse](#parse).\n\nThen [plan](#plan).\n"
    _, b = build(md)
    b.check_all()
    assert "build_cue_order" in codes(b.issues)


def test_build_cues_in_order_pass(build):
    md = f"# T\n\nA.\n\n## Steps {{#steps .build}}\n\n{LIST3}\nFirst [parse](#parse).\n\nThen [plan](#plan).\n\nThen [run](#run).\n"
    _, b = build(md)
    b.check_all()
    assert not {"build_cue_order", "build_cues_bunched"} & set(codes(b.issues))


def test_a_run_of_code_slides_suggests_a_diagram(build):
    slides = "".join(f"## C{i} {{#c{i}}}\n\n```python\nx = {i}\n```\n\nThe value is {i}.\n\n" for i in range(4))
    _, b = build("# T\n\nA.\n\n" + slides)
    b.check_all()
    assert "code_run" in codes(b.issues)
    _, b = build(
        "# T\n\nA.\n\n" + slides.replace("## C2 {#c2}\n\n```python\nx = 2\n```", "## C2 {#c2}\n\n```mermaid\nflowchart LR\n  a --> b\n```")
    )
    b.check_all()
    assert "code_run" not in codes(b.issues)


def test_a_run_of_code_slides_is_reported_while_writing_and_once(build):
    slides = "".join(f"## C{i} {{#c{i}}}\n\n```python\nx = {i}\n```\n\nThe value is {i}.\n\n" for i in range(5))
    _, b = build("# T\n\nA.\n\n" + slides)
    assert codes(b.issues).count("code_run") == 1  # in the reply to the call, not only at finish
    b.issues = []
    b.check_all()
    assert "code_run" not in codes(b.issues)


WORDS = {
    "list": "- **Parse**: a\n- **Plan**: b\n",
    "table": "| A | B |\n|---|---|\n| one | two |\n",
    "text": "> A quote on screen\n",
    "diagram": "```mermaid\nflowchart LR\n  a --> b\n```\n",
}


def _slides(*kinds: str) -> str:
    return "# T\n\nA.\n\n" + "".join(f"## S{i} {{#s{i}}}\n\n{WORDS[k]}\nAbout part {i} of it.\n\n" for i, k in enumerate(kinds))


def test_three_wordy_slides_in_a_row_ask_for_a_picture(build):
    _, b = build(_slides("list", "table", "text"))
    found = [i for i in b.issues if i.code == "plain_run"]
    assert len(found) == 1 and "s0, s1, s2" in found[0] and "diagram" in found[0]
    b.issues = []
    b.add("## S9 {#s9}\n\n- **More**: c\n\nAnd more.\n")  # the same run grows: not reported again
    assert "plain_run" not in codes(b.issues)


def test_a_picture_breaks_a_run_of_words(build):
    _, b = build(_slides("list", "table", "diagram", "text", "list"))
    b.check_all()
    assert "plain_run" not in codes(b.issues)


@pytest.mark.parametrize(
    ("said", "flagged"),
    [
        ("This change replaces the fixed sleep. The old code used to wait a whole second.", True),
        ("The retry loop was removed, and the client no longer blocks.", True),
        ("The loader reads the file. Each entry is added to the cache and is removed when it expires.", False),
        ("We use a dict instead of a list here, so lookups stay fast.", False),
    ],
)
def test_a_change_told_over_plain_code_suggests_a_diff(build, said, flagged):
    _, b = build(f"# T\n\nA.\n\n## Retry {{#retry}}\n\n```python\nx = 1\n```\n\n{said}\n")
    assert ("change_as_code" in codes(b.issues)) == flagged


@pytest.mark.parametrize(
    ("title", "opening", "flagged"),
    [
        ("What changed between 8d0619a and f2a4a93", "Two fixes landed.", True),
        ("Retry review", "This pull request makes retries safe.", True),
        ("How retries work", "Failed requests now recover on their own.", False),
    ],
)
def test_code_from_disk_in_a_walkthrough_of_a_change_suggests_a_diff(build, repo, title, opening, flagged):
    repo.write("app.py", "x = 1\n")
    repo.commit()
    md = f"# {title}\n\n{opening}\n\n## App {{#app}}\n\n```python file=app.py\n```\n\nThe [value](#L1) is set once.\n"
    _, b = build(md, root=repo.root, title=title)
    assert ("change_as_code" in codes(b.issues)) == flagged


def test_a_change_already_shown_as_a_diff_does_not_ask_again(build, repo):
    repo.write("app.py", "x = 1\n")
    repo.commit()
    repo.write("app.py", "x = 2\n")
    md = (
        "# Commit review\n\nOne fix.\n\n## Fix {#fix}\n\n```diff file=app.py rev=HEAD\n```\n\nThe [value](#L1) changed.\n\n"
        "## Context {#ctx}\n\n```python file=app.py\n```\n\nThe [value](#L1) is read here.\n"
    )
    _, b = build(md, root=repo.root, title="Commit review")
    assert "change_as_code" not in codes(b.issues)


def test_a_change_shown_as_steps_is_fine_and_the_hint_comes_once(build):
    said = "This change replaces the fixed sleep. The old code used to wait a whole second."
    steps = f"## R {{#r}}\n\n```python step\nx = 1\n```\n\n```python step\nx = 2\n```\n\n{said}\n\n"
    _, b = build("# T\n\nA.\n\n" + steps)
    assert "change_as_code" not in codes(b.issues)
    plain = "".join(f"## P{i} {{#p{i}}}\n\n```python\nx = {i}\n```\n\n{said}\n\n" for i in range(2))
    _, b = build("# T\n\nA.\n\n" + plain)
    assert codes(b.issues).count("change_as_code") == 1


@pytest.mark.parametrize(
    ("rows", "flagged"),
    [
        ("| Violet | 400 nm |\n| Blue | 470 nm |\n| Green | 530 nm |\n| Red | 700 nm |", True),
        ("| p50 | 12 ms | 9 ms |\n| p95 | 80 ms | 31 ms |\n| Errors | **2.1%** | 0.4% |", True),
        ("| Turing test | 1950 |\n| Dartmouth | 1956 |\n| ELIZA | 1966 |", False),  # years are not quantities
        ("| Asset | Loan to you | +100,000 |\n| Liability | Your deposit | +100,000 |\n| Equity | None | 0 |", False),
        ("| Violet | 400 nm |\n| Blue | 470 nm |", False),  # two rows read fine
    ],
)
def test_a_table_of_numbers_suggests_a_chart(build, rows, flagged):
    width = rows.split("\n")[0].count("|") - 1
    head = "|" + " x |" * width + "\n|" + "---|" * width + "\n"
    _, b = build(f"# T\n\nA.\n\n## Light {{#light}}\n\n{head}{rows}\n\nShort waves scatter most.\n")
    assert ("table_numbers" in codes(b.issues)) == flagged


@pytest.mark.parametrize(
    ("visual", "flagged"),
    [
        ("- **1989**: a memo\n- **1990**: a server\n- **1991**: public\n", True),
        ("- **March 1989**: a memo\n- **1990s**: growth\n- **Q3 2021**: a fork\n- **Today**: here\n", True),
        ("| 1950 | Turing test |\n| 1956 | Dartmouth |\n| 1966 | ELIZA |\n", True),
        ("- **Parse**: a\n- **Plan**: b\n- **Run**: c\n", False),
        ("- **1989**: a memo\n- **Later**: more\n- **Then**: more\n", False),
    ],
)
def test_dated_events_suggest_a_timeline(build, visual, flagged):
    if visual.startswith("|"):
        visual = "| Year | Event |\n|---|---|\n" + visual
    _, b = build(f"# T\n\nA.\n\n## History {{#history}}\n\n{visual}\nIt grew.\n")
    assert ("list_dates" in codes(b.issues)) == flagged


def test_a_timeline_is_built_and_not_flagged(build):
    w, b = build(
        "# T\n\nA.\n\n## History {#history .timeline}\n\n- **1989**: a memo\n- **1990**: a server\n- **1991**: public\n\nIt [began](#1989).\n"
    )
    s = w.slides[-1]
    assert s.kind == "list" and s.visual["style"] == "timeline" and [i["id"] for i in s.visual["items"]] == ["1989", "1990", "1991"]
    assert "list_dates" not in codes(b.issues) and "cue_no_target" not in codes(b.issues)


@pytest.mark.parametrize(
    ("body", "flagged"),
    [
        ("Win conditions: 0 -> 0", True),
        ("Copies sold: 0 -> 300 million\nPlayers: 0 -> 170 million", True),
        ("Errors: 0 -> 12\nLatency: 420 ms -> 90 ms", False),
        ("Copies sold: 300 million", False),
    ],
)
def test_a_stat_without_a_real_change_is_flagged(build, body, flagged):
    _, b = build(f"# T\n\nA.\n\n## Numbers {{#numbers}}\n\n```stat\n{body}\n```\n\nThe [numbers](#numbers) say it.\n")
    assert ("stat_no_change" in codes(b.issues)) == flagged


@pytest.mark.parametrize(
    ("para", "flagged"),
    [
        ("Narration: Say you ask for a mortgage.", True),
        ("**Voiceover:** The bank approves it.", True),
        ("Title slide: How debt creates money.", True),
        ("Note: this only runs on Linux.", False),
        ("The script: it runs nightly.", False),
    ],
)
def test_a_script_label_in_narration_is_flagged(build, para, flagged):
    _, b = build(f"# T\n\nA.\n\n## Loan {{#loan}}\n\n- **Loan**: a\n\n{para}\n")
    assert ("narration_label" in codes(b.issues)) == flagged


def test_opening_that_names_code_is_flagged(build):
    _, b = build("# T\n\nThe `RetryPolicy` class wraps `send`.\n\n## Next\n\nMore.\n")
    b.check_all()
    assert "opening_code_names" in codes(b.issues)
    _, b = build("# T\n\nFailed requests now recover on their own.\n\n## Next\n\nMore.\n")
    b.check_all()
    assert "opening_code_names" not in codes(b.issues)


def test_diff_of_a_lockfile_is_flagged(build, repo):
    repo.write("uv.lock", "a = 1\n")
    repo.commit()
    repo.write("uv.lock", "a = 2\n")
    _, b = build("# T\n\nA.\n\n## Lock\n\n```diff file=uv.lock rev=HEAD\n```\n\nIt changed.\n", root=repo.root)
    assert "generated_file" in codes(b.issues)


# -- store: timing, coverage, revisions --------------------------------------------------------------


async def started(service):
    return await service.start("How caching works", "landscape", "", 1.0, None)


async def test_a_long_still_slide_is_reported_once_voiced(service, store, voiced):
    r = await started(service)
    words = " ".join(["word"] * 90) + "."
    await service.add(r["handle"], OPENING + f"\n## Still {{#still}}\n\n```python\nx = 1\n```\n\n{words}\n")
    await voiced(store.by_handle(r["handle"]))
    a = await service.add(r["handle"], "## Next\n\nMore.\n")
    assert any(i["code"] == "still_slide" and i["slide"] == "still" for i in a["issues"])
    a = await service.add(r["handle"], "## Later\n\nMore.\n")
    assert "still_slide" not in codes(a["issues"])  # reported once


async def test_cues_break_up_a_long_slide(service, store, voiced):
    r = await started(service)
    half = " ".join(["word"] * 45)
    md = f"\n## Busy {{#busy}}\n\n```python\nx = 1\ny = 2\n```\n\n{half} [x](#L1) {half} [y](#L2) {half}.\n"
    await service.add(r["handle"], OPENING + md)
    await voiced(store.by_handle(r["handle"]))
    a = await service.add(r["handle"], "## Next\n\nMore.\n")
    assert "still_slide" not in codes(a["issues"])


async def test_finish_says_what_was_not_checked(service, store):
    r = await started(service)
    await service.add(r["handle"], OPENING)
    f = await service.finish(r["handle"], None)
    assert any(i["code"] == "not_checked" and "No player" in i["message"] for i in f["issues"])
    assert f["outline"][0] == {"id": "how-caching-works", "title": "How caching works", "start_s": 0.0, "look": "title"}
    s = store.by_handle(r["handle"])
    q = s.subscribe()
    f = await service.finish(r["handle"], None)
    s.unsubscribe(q)
    assert not any("No player" in i["message"] for i in f["issues"])


CODE2 = "\n```python\nx = 1\ny = 2\nz = 3\n```\n\n"


async def test_cues_too_close_across_paragraphs_are_reported_at_finish(service, store, voiced):
    # The cue that ends one paragraph and the one that opens the next fire under a second apart.
    r = await started(service)
    md = "The loader starts here and reads the [config](#L1).\n\n[Then](#L2) it parses each line in turn and keeps going.\n"
    await service.add(r["handle"], OPENING + "\n## Close {#close}\n" + CODE2 + md)
    await voiced(store.by_handle(r["handle"]))
    f = await service.finish(r["handle"], None, check=False)
    found = [i for i in f["issues"] if i["code"] == "cues_too_close"]
    assert len(found) == 1 and found[0]["slide"] == "close" and "[config](#L1) and [Then](#L2)" in found[0]["message"]
    f = await service.finish(r["handle"], None, check=False)
    assert "cues_too_close" not in codes(f["issues"])  # once a slide


async def test_cues_with_room_between_them_are_not_reported(service, store, voiced):
    r = await started(service)
    md = "The loader starts by reading the [config](#L1) from disk.\n\nOnce that is done, it moves on and [parses](#L2) each line.\n"
    await service.add(r["handle"], OPENING + "\n## Spaced {#spaced}\n" + CODE2 + md)
    await voiced(store.by_handle(r["handle"]))
    f = await service.finish(r["handle"], None, check=False)
    assert "cues_too_close" not in codes(f["issues"])


async def test_bunched_build_cues_are_not_reported_twice(service, store, voiced):
    r = await started(service)
    md = "[One](#L1) [two](#L2) [three](#L3) appear.\n\nThen we talk for a while about all of them together.\n"
    a = await service.add(r["handle"], OPENING + "\n## Bunch {#bunch .build}\n" + CODE2 + md)
    assert "build_cues_bunched" in codes(a["issues"])
    await voiced(store.by_handle(r["handle"]))
    f = await service.finish(r["handle"], None, check=False)
    assert "cues_too_close" not in codes(f["issues"])
