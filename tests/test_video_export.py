"""MP4 export and posters: frames captured by seeking the player in capture mode.

Runs a fake-speech daemon, writes a three-slide walkthrough, and renders it at a low frame rate.
Needs Playwright's Chromium (`uv run playwright install chromium`) and ffmpeg on PATH.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from showntell.capture import _with_params, output_size

DOC = """# Video export
> Frames by seeking

A short opening line for the title.

## Code {#code}

```python
def add(a, b):
    return a + b
```

The function [returns the sum](#L2).

## Steps {#steps .build}

1. **Parse** {#parse}
2. **Render** {#render}

First we [parse](#parse), then we [render](#render).
"""


def test_output_size_keeps_frame_aspect():
    assert output_size("landscape") == (1920, 1080)
    assert output_size("square") == (1080, 1080)
    assert output_size("portrait") == (1080, 1920)
    assert output_size("landscape", 360) == (640, 360)


def test_capture_params_keep_the_token():
    url = _with_params("http://127.0.0.1:4747/w/abc?token=x", capture="1")
    assert url == "http://127.0.0.1:4747/w/abc/?token=x&capture=1"


# -- browser ------------------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


handles: dict = {}  # walkthrough id -> (handle, export route, auth header), for tests through the daemon


@pytest.fixture(scope="module")
def walkthrough(tmp_path_factory):
    """(data folder, walkthrough id) for DOC, voiced by a fake-speech daemon."""
    pytest.importorskip("playwright.async_api")
    data = tmp_path_factory.mktemp("video-data")
    env = {**os.environ, "SHOWNTELL_DATA": str(data), "SHOWNTELL_SPEECH": "fake"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "showntell", "serve", "--port", str(_free_port()), "--speech", "fake"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        lock = None
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                lock = json.loads((data / "daemon.json").read_text())
                if httpx.get(f"http://127.0.0.1:{lock['port']}/health", timeout=1).status_code == 200:
                    break
            except (OSError, ValueError, httpx.HTTPError):
                pass
            time.sleep(0.2)
        assert lock, "daemon did not start"
        h = {"Authorization": f"Bearer {lock['token']}"}

        def call(m, **kw):
            r = httpx.post(f"http://127.0.0.1:{lock['port']}/api/rpc/{m}", json=kw, headers=h, timeout=30)
            r.raise_for_status()
            return r.json()["result"]

        r = call("start", title="Video export", frame="landscape", voice="", speed=1.0, project_dir=str(data))
        call("add", handle=r["handle"], markdown=DOC)
        for _ in range(100):
            if not call("finish", handle=r["handle"], questions=[])["pending"]:
                break
            time.sleep(0.1)
        wid = r["handle"].split(".")[0]
        handles[wid] = (r["handle"], f"http://127.0.0.1:{lock['port']}/api/rpc/export", h)
        state = data / "walkthroughs" / wid / "state.json"
        for _ in range(100):  # state is saved a moment after the last change
            if state.is_file():
                st = json.loads(state.read_text())
                if st["status"] == "finished" and all(x["status"] == "ready" for s in st["slides"] for x in s["says"] if x.get("text")):
                    break
            time.sleep(0.1)
        yield data, wid
    finally:
        proc.terminate()
        proc.wait(10)


def _frame(video: Path, t: float) -> bytes:
    return subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{t:.2f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-s",
            "320x180",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout


@pytest.mark.browser
@pytest.mark.slow
def test_mp4_export(walkthrough, tmp_path):
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg not installed")
    from showntell.capture import CaptureUnavailable
    from showntell.export import timeline, timeline_total
    from showntell.model import Walkthrough
    from showntell.video import probe, render_mp4

    data, wid = walkthrough
    out = tmp_path / "walk.mp4"
    try:
        r = render_mp4(data, wid, out, fps=4, workers=2, quiet=True)
    except SystemExit as e:
        if "not installed" in str(e):
            pytest.skip(str(e))
        raise
    except CaptureUnavailable as e:
        pytest.skip(str(e))
    assert not r["errors"], r["errors"]

    w = Walkthrough.from_state(json.loads((data / "walkthroughs" / wid / "state.json").read_text()))
    total = timeline_total(w)
    info = probe(out)
    assert abs(float(info["format"]["duration"]) - total) <= 0.2
    streams = {s["codec_type"]: s for s in info["streams"]}
    assert set(streams) == {"video", "audio", "subtitle"}
    assert (streams["video"]["codec_name"], streams["audio"]["codec_name"], streams["subtitle"]["codec_name"]) == (
        "h264",
        "aac",
        "mov_text",
    )
    assert (streams["video"]["width"], streams["video"]["height"]) == (1920, 1080)
    assert info["format"]["tags"]["encoder"].startswith("showntell ") and info["format"]["tags"]["title"] == w.title

    # Frames from the middle of the code slide and of the steps slide differ.
    starts = {}
    for start, slide, _ in timeline(w):
        starts.setdefault(slide.id, start)
    assert _frame(out, starts["code"] + 0.5) != _frame(out, starts["steps"] + 0.5)
    assert r["shots"] < r["frames"]  # repeated states reuse the previous screenshot


@pytest.mark.browser
@pytest.mark.slow
def test_folder_export_writes_a_poster(walkthrough, tmp_path):
    from showntell.export import export

    data, wid = walkthrough
    out = export(data, wid, tmp_path / "site", poster=True)
    if not (out / "poster.png").is_file():
        pytest.skip("Chromium unavailable")
    png = (out / "poster.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
    assert (w, h) == (1920, 1080)
    page = (out / "index.html").read_text()
    assert '<meta property="og:image" content="poster.png">' in page
    assert '<meta name="twitter:image" content="poster.png">' in page


@pytest.mark.browser
@pytest.mark.slow
def test_mp4_export_through_the_daemon_fits_a_size_limit(walkthrough, tmp_path):
    """export_walkthrough's path: the daemon renders, then shrinks the file to fit `max_mb`."""
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg not installed")
    from showntell.video import probe

    data, wid = walkthrough
    handle, route, auth = handles[wid]

    def export(name: str, max_mb: float) -> httpx.Response:
        body = {"handle": handle, "path": str(tmp_path / name), "format": "mp4", "max_mb": max_mb}
        return httpx.post(route, json=body, headers=auth, timeout=180)

    r = export("full.mp4", 0)
    if r.status_code == 400 and ("not installed" in r.text or "Chromium" in r.text):
        pytest.skip(r.text)
    full = r.json()["result"]
    assert full["shrunk"] == "" and full["bytes"] == (tmp_path / "full.mp4").stat().st_size
    limit = full["bytes"] * 0.95 / 1e6
    small = export("small.mp4", limit).json()["result"]
    assert small["shrunk"] and small["bytes"] <= limit * 1e6
    streams = {s["codec_type"] for s in probe(tmp_path / "small.mp4")["streams"]}
    assert streams == {"video", "audio", "subtitle"}
    r = export("tiny.mp4", 0.001)
    assert r.status_code == 400 and "split the walkthrough" in r.json()["error"]
    assert not (tmp_path / "tiny.mp4").exists()


# -- the browser, on first need from the command line --------------------------------------------


def _mp4_args():
    from argparse import Namespace

    return Namespace(mp3=False, fps=4, workers=1, burn_captions=False, motion=False, max_mb=0)


@pytest.mark.parametrize(
    ("env", "fetches"), [({}, True), ({"SHOWNTELL_BROWSER": "off"}, False), ({"PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1"}, False)]
)
def test_command_line_mp4_export_fetches_a_missing_browser_first(tmp_path, monkeypatch, env, fetches):
    from showntell import browser, export, video
    from showntell.config import Config

    monkeypatch.delenv("SHOWNTELL_BROWSER", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    order = []
    monkeypatch.setattr(browser, "installed", lambda: False)
    monkeypatch.setattr(browser, "install", lambda state=None, echo=None, low=True: order.append(("install", low)))

    def render(*a, **kw):
        order.append("render")
        raise SystemExit("stop here")

    monkeypatch.setattr(video, "render_mp4", render)
    with pytest.raises(SystemExit, match="stop here"):
        export._export_mp4(Config(data_dir=tmp_path), "w1", tmp_path / "x.mp4", _mp4_args())
    assert order == ([("install", False), "render"] if fetches else ["render"])  # the user waits: full speed


def test_command_line_mp4_export_says_when_the_download_fails(tmp_path, monkeypatch):
    from showntell import browser, export
    from showntell.config import Config

    monkeypatch.delenv("SHOWNTELL_BROWSER", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", raising=False)
    monkeypatch.setattr(browser, "installed", lambda: False)

    def offline(**kw):
        raise RuntimeError("getaddrinfo ENOTFOUND cdn.playwright.dev")

    monkeypatch.setattr(browser, "install", offline)
    with pytest.raises(SystemExit, match="browser download failed: getaddrinfo"):
        export._export_mp4(Config(data_dir=tmp_path), "w1", tmp_path / "x.mp4", _mp4_args())
