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
