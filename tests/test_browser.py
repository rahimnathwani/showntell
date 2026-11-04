"""The browser that draws slides: found, fetched once in the background after the voice, or off."""

from __future__ import annotations

import json
import sys
import threading
import time

import pytest

from showntell import browser


class Voice:
    """A narrator stand-in: `ready` and `busy` say what the fetch should wait for."""

    def __init__(self, ready: bool = True, busy: int = 0) -> None:
        self._ready = threading.Event()
        if ready:
            self._ready.set()
        self.busy = busy

    def wait_ready(self, timeout=None) -> bool:
        return self._ready.wait(timeout)

    def backlog(self) -> int:
        return self.busy


@pytest.fixture
def fetch(monkeypatch):
    """No real download: `install` records its calls, the browser starts, nothing is installed."""
    calls = []

    def fake_install(state=browser.STATE, echo=None, low=True):
        calls.append(time.monotonic())
        state.set("downloading", "99 MiB", 0.5)

    monkeypatch.delenv("SHOWNTELL_BROWSER", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", raising=False)
    monkeypatch.setattr(browser, "installed", lambda: False)
    monkeypatch.setattr(browser, "install", fake_install)
    monkeypatch.setattr(browser, "verify", lambda: "")
    return calls


def test_fetch_waits_for_the_voice_then_runs_once(fetch):
    state, voice = browser.Browser(), Voice(ready=False)
    t = browser.start(voice, state, idle_s=0.05)
    time.sleep(0.3)
    assert fetch == [] and state.state == "waiting"  # never alongside the voice model's download
    assert "once the voice model is ready" in state.describe()
    voice._ready.set()
    t.join(5)
    assert len(fetch) == 1 and state.state == "ready"


def test_fetch_waits_for_the_opening_to_be_voiced(fetch):
    """With the voice loaded, the opening is still being voiced: the first link goes first."""
    state, voice = browser.Browser(), Voice(busy=1)
    t = browser.start(voice, state, idle_s=0.05, idle_cap_s=10)
    time.sleep(0.3)
    assert fetch == []
    voice.busy = 0
    t.join(5)
    assert len(fetch) == 1


def test_a_busy_narrator_delays_the_fetch_only_so_long(fetch):
    t = browser.start(Voice(busy=3), browser.Browser(), idle_s=0.05, idle_cap_s=0.2)
    t.join(5)
    assert len(fetch) == 1


def test_an_installed_browser_is_ready_at_once_without_fetching(fetch, monkeypatch):
    monkeypatch.setattr(browser, "installed", lambda: True)
    state = browser.Browser()
    assert browser.start(Voice(ready=False), state) is None
    assert state.state == "ready" and fetch == []


def test_off_never_fetches(fetch, monkeypatch):
    monkeypatch.setenv("SHOWNTELL_BROWSER", "off")
    state = browser.Browser()
    assert browser.start(Voice(), state) is None
    assert state.state == "off" and fetch == []
    assert "SHOWNTELL_BROWSER=off" in state.unavailable()


def test_playwrights_skip_switch_never_fetches(fetch, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "1")
    state = browser.Browser()
    assert browser.start(Voice(), state) is None
    assert state.state == "missing" and fetch == []


def test_a_failed_download_is_an_error_that_says_what_happens_next(fetch, monkeypatch):
    def offline(state=browser.STATE, echo=None, low=True):
        fetch.append(1)
        raise RuntimeError("getaddrinfo ENOTFOUND cdn.playwright.dev")

    monkeypatch.setattr(browser, "install", offline)
    state = browser.Browser()
    browser.start(Voice(), state, idle_s=0).join(5)
    assert state.state == "error" and fetch == [1]
    assert "ENOTFOUND" in state.describe() and "tries again when it next starts" in state.describe()


def test_a_browser_that_will_not_start_says_how_to_fix_it(fetch, monkeypatch):
    monkeypatch.setattr(browser, "verify", lambda: browser.explain_launch_error(RuntimeError("Host system is missing dependencies")))
    state = browser.Browser()
    browser.start(Voice(), state, idle_s=0).join(5)
    assert state.state == "error" and "install-deps chromium" in state.describe()


def test_install_reports_progress_from_playwrights_output(monkeypatch):
    script = (
        "print('Downloading Chrome Headless Shell 156 (playwright chromium-headless-shell v1248) from https://x');"
        "print('|■■■■                 |  40% of 99 MiB', flush=True);"
        "import time; time.sleep(2)"
    )
    monkeypatch.setattr(browser, "INSTALL", [sys.executable, "-c", script])
    state, seen = browser.Browser(), []
    t = threading.Thread(target=browser.install, args=(state, seen.append))
    t.start()
    end = time.monotonic() + 1.8
    while len(seen) < 2 and time.monotonic() < end:
        time.sleep(0.02)
    assert state.state == "downloading" and state.describe() == "downloading (40% of 99 MiB)"
    t.join(5)
    assert len(seen) == 2


def test_install_says_downloading_before_any_output(monkeypatch):
    """At background priority Playwright's first line can take a while; it is not "waiting" meanwhile."""
    monkeypatch.setattr(browser, "INSTALL", [sys.executable, "-c", "import time; time.sleep(1.5)"])
    state = browser.Browser()
    state.set("waiting")
    t = threading.Thread(target=browser.install, args=(state,))
    t.start()
    end = time.monotonic() + 1.2
    while state.state != "downloading" and time.monotonic() < end:
        time.sleep(0.02)
    assert state.state == "downloading" and state.describe() == "downloading"
    t.join(5)


def test_install_failure_carries_the_installers_last_lines(monkeypatch):
    monkeypatch.setattr(browser, "INSTALL", [sys.executable, "-c", "print('Error: ENOSPC: no space left'); raise SystemExit(1)"])
    with pytest.raises(RuntimeError, match="ENOSPC"):
        browser.install(browser.Browser())


def test_installed_reads_playwrights_folder(tmp_path, monkeypatch):
    import playwright

    meta = json.loads((browser.Path(playwright.__file__).parent / "driver" / "package" / "browsers.json").read_text())
    rev = next(b["revision"] for b in meta["browsers"] if b["name"] == "chromium-headless-shell")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert not browser.installed()
    (tmp_path / f"chromium_headless_shell-{rev}").mkdir()
    assert not browser.installed()  # half unpacked
    (tmp_path / f"chromium_headless_shell-{rev}" / "INSTALLATION_COMPLETE").write_text("")
    assert browser.installed()
