"""The evaluation script's own arithmetic, without agents or a browser."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _eval():
    spec = importlib.util.spec_from_file_location("showntell_eval", ROOT / "scripts" / "eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_watch_speed_reports_first_sound_and_the_longest_wait():
    m = {"stalls": 3, "stallSeconds": 7.04, "longestStall": 5.27, "starts": [{"key": "a", "at": 1_000_012_340.0, "local": 0}]}
    w = _eval().watch_speed(m, run_start=1_000_000.0)
    assert w == {"first_sound_s": 12.3, "stalls": 3, "stall_s": 7.0, "longest_stall_s": 5.3}


def test_watch_speed_without_sound():
    assert _eval().watch_speed({}, run_start=0.0)["first_sound_s"] is None


def test_start_metrics_time_the_start_and_split_the_gap_to_slide_two():
    ev = _eval()
    msgs: list[dict] = []
    # first request: ToolSearch; second: a Read; third: start_walkthrough; fourth: add_slides
    for e, now, sent in [
        ({"type": "message_start", "message": {"usage": {"input_tokens": 3, "cache_read_input_tokens": 9000}}}, 2.0, 0.5),
        ({"type": "message_delta", "usage": {"output_tokens": 40}}, 3.0, 0.5),
        ({"type": "message_stop"}, 3.0, 0.5),
        ({"type": "message_start", "message": {"usage": {"input_tokens": 9100}}}, 5.0, 4.0),
        ({"type": "message_delta", "usage": {"output_tokens": 60}}, 6.0, 4.0),
        ({"type": "message_stop"}, 6.0, 4.0),
        ({"type": "message_start", "message": {"usage": {"input_tokens": 15160}}}, 12.0, 10.0),
        ({"type": "content_block_delta"}, 12.5, 10.0),
        ({"type": "message_delta", "usage": {"output_tokens": 300}}, 17.0, 10.0),
        ({"type": "message_stop"}, 17.0, 10.0),
    ]:
        ev.note_stream(msgs, e, now, sent)
    assert msgs[0] == {"sent": 0.5, "first": 2.0, "in": 9003, "out": 40, "end": 3.0}
    marks = {
        "tool_calls": [(3.0, "ToolSearch"), (3.5, "Read"), (6.0, "start_walkthrough"), (16.5, "add_slides")],
        "start_call": 6.0,
        "playable_at": 10.0,
        "opening_in_start": True,
        "messages": msgs,
    }
    m = ev.start_metrics(marks)
    assert m["start_s"] == 6.0 and m["tools_before_start"] == ["Read"]
    assert m["gap2_s"] == 6.5
    assert (m["gap2_wait_s"], m["gap2_write_s"], m["gap2_other_s"]) == (2.0, 4.5, 0.0)
    assert m["gap2_out_tokens"] == 300 and m["gap2_tools"] == []
    assert m["start_reply_tokens"] == 15160 - 9100 - 60


def test_start_metrics_skip_the_add_slides_that_carried_the_opening():
    marks = {
        "tool_calls": [(5.0, "start_walkthrough"), (8.0, "add_slides"), (15.0, "add_slides")],
        "start_call": 5.0,
        "playable_at": 9.0,
        "opening_in_start": False,
    }
    assert _eval().start_metrics(marks)["gap2_s"] == 6.0


def test_start_metrics_without_a_start_call():
    assert _eval().start_metrics({"tool_calls": [(1.0, "Read")]}) == {}
