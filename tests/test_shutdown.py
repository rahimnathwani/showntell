"""A running daemon stops promptly with a walkthrough page open, and saves on the way out."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

import httpx
import pytest

from showntell import daemon


def _start(data, port: int) -> tuple[subprocess.Popen, dict]:
    env = {**os.environ, "SHOWNTELL_DATA": str(data), "SHOWNTELL_SPEECH": "fake", "SHOWNTELL_FINISH_CHECK": "0"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "showntell", "serve", "--port", str(port), "--speech", "fake"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            lock = json.loads((data / "daemon.json").read_text())
            if httpx.get(f"http://127.0.0.1:{lock['port']}/health", timeout=1).status_code == 200:
                return proc, lock
        except (OSError, ValueError, httpx.HTTPError):
            pass
        time.sleep(0.2)
    proc.kill()
    raise AssertionError("daemon did not start")


def _open_page_stream(lock: dict) -> tuple[str, list[str]]:
    """Start a walkthrough and hold its events stream open, as a page left open in a browser does."""
    h = {"Authorization": f"Bearer {lock['token']}"}
    r = httpx.post(
        f"http://127.0.0.1:{lock['port']}/api/rpc/start",
        json={"title": "Left open", "frame": "landscape", "voice": "", "speed": 1.0, "project_dir": None},
        headers=h,
        timeout=30,
    )
    r.raise_for_status()
    res = r.json()["result"]
    u = urlsplit(res["url"])
    wid = u.path.split("/")[2]
    events = f"http://127.0.0.1:{lock['port']}/w/{wid}/events" + (f"?{u.query}" if u.query else "")
    got: list[str] = []

    def hold() -> None:
        try:
            with httpx.stream("GET", events, timeout=None) as s:
                for chunk in s.iter_text():
                    got.append(chunk)
        except httpx.HTTPError:
            pass

    threading.Thread(target=hold, daemon=True).start()
    for _ in range(50):
        if got:
            break
        time.sleep(0.1)
    assert got, "the events stream did not open"
    return wid, got


@pytest.mark.parametrize("how", ["signal", "stop command"])
def test_an_open_page_does_not_hold_the_daemon(tmp_path, free_port, how):
    proc, lock = _start(tmp_path, free_port)
    try:
        wid, _ = _open_page_stream(lock)
        t0 = time.time()
        if how == "signal":
            proc.send_signal(signal.SIGTERM)
        else:  # what `showntell stop` sends; daemon.stop itself would wait on this test's unreaped child
            daemon._shutdown(lock)
        proc.wait(timeout=10)
        assert time.time() - t0 < 3, "an open events stream held the daemon"
        assert (tmp_path / "walkthroughs" / wid / "state.json").is_file()  # cleanup ran
    finally:
        if proc.poll() is None:
            proc.kill()
