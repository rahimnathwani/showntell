"""The headless browser that draws slides: Playwright's Chromium headless shell.

Playwright comes with showntell; its browser, about 100 MB, does not. The daemon fetches it once,
in the background: after the voice model is ready, when the narrator is idle, at low priority.
So it never competes with the voice model's download or a first link. Until it is ready, finish
skips the layout check, and previews, posters and MP4 export say why.

    SHOWNTELL_BROWSER=off               never fetch or start a browser (locked-down machines)
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1  use one already installed; never fetch
    PLAYWRIGHT_BROWSERS_PATH=<folder>   where Playwright keeps its browsers, as Playwright reads it

    STATE.unavailable()   # '' when a launch may be tried, else why not, in plain words
"""

from __future__ import annotations

import collections
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("showntell.browser")

# Playwright's installer, for the headless shell only: `install chromium` also fetches the full
# Chromium (about 195 MB more), which headless capture never starts.
INSTALL = [sys.executable, "-m", "playwright", "install", "--only-shell", "chromium"]
PENDING = ("waiting", "downloading")  # coming: features say so and skip


def mode() -> str:
    """'off' when SHOWNTELL_BROWSER turns the browser off, else 'auto'."""
    raw = os.environ.get("SHOWNTELL_BROWSER", "").strip().lower()
    return "off" if raw in ("off", "0", "no", "false", "none") else "auto"


def skip_download() -> bool:
    """Playwright's own switch for machines whose browsers are provisioned some other way."""
    return os.environ.get("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "").strip().lower() not in ("", "0", "false")


class Browser:
    """What the browser is doing, for /health, doctor and tool replies.

    unknown      nobody looked yet (a command-line run: a launch is simply tried)
    off          SHOWNTELL_BROWSER=off
    missing      not installed, and PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD is set
    waiting      not installed; fetched once the voice is ready and idle
    downloading  being fetched (`fraction` of `detail`)
    ready        installed and starts
    error        the fetch or the first start failed (`detail` says why and what to do)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state = "unknown"
        self.detail = ""
        self.fraction = 0.0
        self.since = time.time()

    def set(self, state: str, detail: str = "", fraction: float = 0.0) -> None:
        with self._lock:
            if state != self.state:
                self.since = time.time()
            self.state, self.detail, self.fraction = state, detail, fraction

    def describe(self) -> str:
        s = self.state
        if s == "downloading":
            return f"downloading ({self.fraction:.0%} of {self.detail})" if self.detail else "downloading"
        if s == "waiting":
            return "not downloaded yet; it downloads in the background once the voice model is ready"
        if s == "off":
            return "turned off with SHOWNTELL_BROWSER=off"
        if s == "missing":
            return "not installed, and PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD keeps showntell from fetching it"
        if s == "error":
            return self.detail
        return s

    def unavailable(self) -> str:
        """Why a launch must not be tried now, or '' when it may. A browser on its way is not tried:
        Playwright would start a half-unpacked one, or fail with its own install advice."""
        if self.state == "off" or mode() == "off":
            return "the browser is turned off on this machine (SHOWNTELL_BROWSER=off)"
        if self.state in PENDING:
            return f"the browser that draws slides is {self.describe()}; try again in a minute or two"
        if self.state == "missing":
            return f"the browser is {self.describe()}"
        return ""

    def as_dict(self) -> dict:
        return {"state": self.state, "detail": self.describe(), "fraction": round(self.fraction, 2)}


STATE = Browser()


# -- where Playwright keeps it -------------------------------------------------------------------


def _registry() -> Path | None:
    """Playwright's browser folder, as its own installer works it out."""
    import playwright

    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if env == "0":
        return Path(playwright.__file__).parent / "driver" / "package" / ".local-browsers"
    if env:
        return Path(env).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "ms-playwright"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ms-playwright"


def installed() -> bool:
    """Whether this Playwright's headless shell is fully installed: a file check, no browser
    started and nothing downloaded, so it is cheap enough for the start of every daemon."""
    try:
        import playwright

        meta = json.loads((Path(playwright.__file__).parent / "driver" / "package" / "browsers.json").read_text(encoding="utf-8"))
        rev = next(b["revision"] for b in meta["browsers"] if b["name"] == "chromium-headless-shell")
        root = _registry()
    except Exception:  # Playwright missing, or laid out differently: let a launch decide
        return False
    return root is not None and (root / f"chromium_headless_shell-{rev}" / "INSTALLATION_COMPLETE").is_file()


# -- fetching it ---------------------------------------------------------------------------------

_PROGRESS = re.compile(r"(\d+)% of ([\d.]+ ?[KMGT]i?B)")


def _low_priority(cmd: list[str]) -> tuple[list[str], dict]:
    """The installer at background priority: on macOS `taskpolicy -b` (CPU, disk and network
    throttled), elsewhere a nice value of 10."""
    if sys.platform == "darwin" and shutil.which("taskpolicy"):
        return ["taskpolicy", "-b", *cmd], {}
    if os.name == "posix":
        return cmd, {"preexec_fn": lambda: os.nice(10)}
    return cmd, {}


def install(state: Browser = STATE, echo=None, low: bool = True) -> None:
    """Run Playwright's installer for the headless shell, updating `state` as it downloads. Already
    installed, it returns in about a second without downloading. `echo` gets every output line.
    Raises RuntimeError with the installer's last lines when it fails."""
    cmd, kw = _low_priority(INSTALL) if low else (INSTALL, {})
    state.set("downloading")  # at once: at background priority the first progress line can take a while
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", **kw)
    tail: collections.deque[str] = collections.deque(maxlen=6)
    for raw in p.stdout:
        line = raw.strip()
        if not line:
            continue
        if echo:
            echo(line)
        m = _PROGRESS.search(line)
        if m:
            state.set("downloading", m.group(2), int(m.group(1)) / 100)
        elif line.startswith("Downloading"):
            state.set("downloading", state.detail, 0.0)
        else:
            tail.append(line)
    if p.wait():
        raise RuntimeError(" / ".join(tail) or f"the installer exited with {p.returncode}")


def deps_hint() -> str:
    """Playwright's own fix; it asks for sudo itself to run the package manager."""
    return f"{sys.executable} -m playwright install-deps chromium"


def explain_launch_error(e: BaseException) -> str:
    """A browser that will not start, in plain words, with the fix when there is one."""
    msg = str(e)
    if "missing dependencies" in msg or "install-deps" in msg:
        return f"Chromium is missing system libraries; install them once with `{deps_hint()}`"
    first = msg.strip().splitlines()[0][:200] if msg.strip() else type(e).__name__
    return f"Chromium did not start: {first}"


def verify() -> str:
    """Start the browser once and close it: '' when it works, else why not."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            pw.chromium.launch().close()
    except Exception as e:  # noqa: BLE001 - any failure is the answer
        return explain_launch_error(e)
    return ""


def start(narrator, state: Browser = STATE, idle_s: float = 2.0, idle_cap_s: float = 60.0) -> threading.Thread | None:
    """At daemon start: settle what the browser is, and fetch it in the background if it is
    missing. The fetch waits for the voice model to load, then for the narrator to stay idle for
    `idle_s` (so the opening is voiced first) or at most `idle_cap_s`, then runs at low priority,
    once. Returns the fetching thread, or None when nothing needs fetching."""
    if mode() == "off":
        state.set("off")
        return None
    if installed():
        state.set("ready")
        return None
    if skip_download():
        state.set("missing")
        return None
    state.set("waiting")

    def run() -> None:
        narrator.wait_ready()
        end = time.monotonic() + idle_cap_s
        quiet_since = time.monotonic()
        while time.monotonic() < end:
            if narrator.backlog():
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= idle_s:
                break
            time.sleep(0.25)
        t0 = time.monotonic()
        log.info("fetching the browser that draws slides (Chromium headless shell, about 100 MB)")
        try:
            install(state)
        except Exception as e:  # noqa: BLE001 - offline, a proxy, a full disk
            log.warning("the browser download failed: %s", e)
            state.set("error", f"its download failed ({str(e)[:300]}); showntell tries again when it next starts")
            return
        why = verify()
        if why:
            log.warning("the browser does not start: %s", why)
            state.set("error", why)
            return
        log.info("browser ready in %.1fs", time.monotonic() - t0)
        state.set("ready")

    t = threading.Thread(target=run, name="browser", daemon=True)
    t.start()
    return t
