"""Shared fixtures: fake-voiced narrators, stores and services, project folders, git repos and builders.

Nothing here is autouse, so test files written by others (pronunciation, ONNX) are unaffected.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import time
from pathlib import Path

import pytest

from showntell.build import Builder
from showntell.model import Walkthrough
from showntell.service import LocalService
from showntell.speech.fake import FakeBackend
from showntell.speech.narrator import Narrator
from showntell.store import Store

# A daemon a test starts must never download Chromium (CI has none); one already installed is still used.
os.environ.setdefault("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "1")

BASE_URL = "http://localhost:4799"

# Forty numbered lines with a few recognisable ones, used as src/cache.py in the project fixture.
CACHE_PY = [f"# line {i}" for i in range(1, 41)]
CACHE_PY[9] = "class Cache:"  # line 10
CACHE_PY[11] = "    if key in self._data:"  # line 12
CACHE_PY[13] = "        return self._data[key]"  # line 14
CACHE_PY[17] = "    value = self.loader(key)"  # line 18


# -- speech and store ---------------------------------------------------------------------


@pytest.fixture
def narrator(tmp_path: Path) -> Narrator:
    """A started narrator on the fake backend: audio in milliseconds, cached under tmp_path/audio."""
    n = Narrator(FakeBackend(), tmp_path / "audio")
    n.start()
    assert n.wait_ready(5)
    return n


@pytest.fixture
def idle_narrator(tmp_path: Path) -> Narrator:
    """A narrator whose worker never starts, so every request stays pending."""
    return Narrator(FakeBackend(), tmp_path / "audio")


@pytest.fixture
async def store(tmp_path: Path, narrator: Narrator) -> Store:
    s = Store(tmp_path / "data", BASE_URL, narrator)
    s.loop = asyncio.get_running_loop()
    return s


@pytest.fixture
def service(store: Store) -> LocalService:
    return LocalService(store)


@pytest.fixture
def wait_until():
    """`await wait_until(lambda: cond)` polls the condition, yielding to the loop, for up to `timeout` s."""

    async def _wait(cond, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while not cond():
            if time.monotonic() > deadline:
                raise AssertionError("condition not met in time")
            await asyncio.sleep(0.005)

    return _wait


def all_says(session) -> list:
    return [x for s in session.w.slides for x in s.says if x.text]


@pytest.fixture
def voiced(wait_until):
    """`await voiced(session)` waits until every narration of the session has audio."""

    async def _voiced(session, timeout: float = 5.0) -> None:
        await wait_until(lambda: all(x.status == "ready" for x in all_says(session)), timeout)

    return _voiced


# -- project folders and git ----------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project root holding src/cache.py (40 lines), a PNG, a binary file and a text file."""
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "src" / "cache.py").write_text("\n".join(CACHE_PY) + "\n")
    (root / "docs" / "timeline.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    (root / "docs" / "notes.txt").write_text("plain text\n")
    (root / "blob.bin").write_bytes(b"abc\0def")
    return root


GIT_ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


class Repo:
    """A throwaway git repository driven with the user's global config switched off."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str) -> str:
        r = subprocess.run(["git", *args], cwd=self.root, env=GIT_ENV, capture_output=True, text=True, check=True)
        return r.stdout

    def write(self, rel: str, text: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def commit(self, message: str = "change") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path / "repo")


# -- building slides ------------------------------------------------------------------------


@pytest.fixture
def build(tmp_path: Path):
    """`w, b = build(markdown, frame=..., root=...)`: a fresh walkthrough built from Markdown."""

    def _build(markdown: str, frame: str = "landscape", root: Path | None = None, title: str = "Demo walk", check_last: bool = True):
        w = Walkthrough(id="w" * 22, key="k" * 22, report_key="r" * 16, title=title, frame=frame, root=str(root) if root else None)
        b = Builder(w, tmp_path / "assets")
        b.add(markdown, check_last=check_last)
        return w, b

    return _build


# -- network --------------------------------------------------------------------------------


@pytest.fixture
def free_port() -> int:
    """A free TCP port on 127.0.0.1 that is never 4747, the default daemon port."""
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port != 4747:
            return port
