"""MP4 export levels the narration to one loudness under a true-peak ceiling. Needs ffmpeg."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from showntell.export import timeline_total
from showntell.model import Walkthrough
from showntell.video import LOUDNESS, _mux, soundtrack

GALLERY = Path(__file__).resolve().parents[1] / "gallery"
pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def measured(path: Path) -> tuple[float, float]:
    """(integrated LUFS, true peak dBTP) of a file's audio."""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    summary = r.stderr[r.stderr.rfind("Summary:") :]
    return float(re.search(r"I:\s+(-?[\d.]+) LUFS", summary).group(1)), float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary).group(1))


def test_the_exported_narration_is_levelled(tmp_path):
    # The gallery's real voice comes out of Kokoro near -25.5 LUFS; the video plays at -16.
    folder = GALLERY / "csrf"
    w = Walkthrough.from_state({**json.loads((folder / "data.json").read_text()), "key": "", "report_key": ""})
    total = timeline_total(w)
    wav = tmp_path / "narration.wav"
    soundtrack(folder, total, wav)
    seg = tmp_path / "seg.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=64x64:r=1:d={total:.2f}",
            "-c:v",
            "libx264",
            str(seg),
        ],
        check=True,
    )
    out = tmp_path / "walk.mp4"
    _mux([seg], wav, tmp_path / "none.vtt", out, tmp_path)
    before, _ = measured(wav)
    loud, peak = measured(out)
    assert before < LOUDNESS - 5
    assert abs(loud - LOUDNESS) <= 1.0 and peak <= -1.5, (loud, peak)
