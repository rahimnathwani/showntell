"""Agents start the walkthrough before researching, so the opening plays while they read. The
advice has to be where an agent reads before its first call: the server instructions, the
start_walkthrough description and the plugin skill. Advice placed later in the guide was ignored."""

from __future__ import annotations

from pathlib import Path

from showntell.server import arg_text, texts, tool_text

ROOT = Path(__file__).resolve().parents[1]


def test_instructions_say_start_first_before_the_flow():
    for hosted in (False, True):
        ins = texts("link", hosted)["instructions"]
        assert "Start first" in ins and ins.index("Start first") < ins.index("Flow:"), hosted
        assert "before reading code or researching" in ins


def test_start_description_and_opening_say_start_before_research():
    assert "Call it first, before reading files or researching" in tool_text("start_walkthrough")
    assert "hold without research" in arg_text("start_walkthrough", "opening")


def test_file_delivery_keeps_its_no_rush_flow():
    # Nothing plays before export there, so starting early buys nothing.
    assert "Start first" not in texts("file", False)["instructions"]


def test_skill_starts_first_and_no_longer_reads_first():
    skill = (ROOT / "claude-plugin" / "skills" / "showntell" / "SKILL.md").read_text()
    assert "## Start first" in skill and "## Read first" not in skill
    assert "before `start_walkthrough`" not in skill
    guide = texts()["guide"]
    assert "**Read first:**" not in guide and "**Read in steps:**" in guide


def test_reads_after_the_start_go_in_one_turn_of_parallel_calls():
    # Each read in a turn of its own cost the viewer a model round trip of 3 to 8 s after the opening.
    assert "in one turn of parallel calls" in texts()["replies"]["playable"]
    for hosted in (False, True):
        assert "in one turn of parallel calls" in texts("link", hosted)["instructions"]
    skill = (ROOT / "claude-plugin" / "skills" / "showntell" / "SKILL.md").read_text()
    assert "in one turn of parallel calls" in skill
