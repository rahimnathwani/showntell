"""Frames of the player, captured by seeking: the base of MP4 export, posters and thumbnails.

The player opens with `?capture=1`, which hides everything but the stage, turns animation off and
exposes `window.__showntell.seek(t)`; each frame is a screenshot taken after that promise
resolves. Playwright is an optional dependency (the `video` extra), imported only here.

    frames = capture_frames("out/folder", [0, 12.5])          # PNG bytes per time
    async with Capturer("http://127.0.0.1:4747/w/<id>/") as c: # or step through by hand
        key, png = await c.frame(3.0)
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import http.server
import json
import logging
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .model import FRAMES

log = logging.getLogger("showntell.capture")


def install_hint() -> str:
    """The two commands that add the browser, pinned to this release by `showntell doctor`."""
    from .doctor import BROWSER_HINT

    return BROWSER_HINT


class CaptureUnavailable(RuntimeError):
    """Playwright or its Chromium is not installed. `reason` is the short form, without the hint."""

    def __init__(self, reason: str, hint: str = ""):
        self.reason = reason
        super().__init__(f"{reason}: {hint}" if hint else reason)


def require_playwright():
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise CaptureUnavailable("Playwright is not installed", install_hint()) from e
    return async_playwright


def output_size(frame: str, short: int = 1080) -> tuple[int, int]:
    """Pixel size for a frame with the given short side: 1920x1080 for landscape at 1080."""
    w, h = FRAMES.get(frame, FRAMES["landscape"])
    k = short / min(w, h)
    return round(w * k) // 2 * 2, round(h * k) // 2 * 2  # even, as H.264 needs


# -- serving an exported folder -----------------------------------------------------------------


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


class _Server(http.server.ThreadingHTTPServer):
    request_queue_size = 128  # several browsers open the player at once; the default of 5 drops some
    daemon_threads = True


@contextlib.contextmanager
def serve_folder(folder: Path) -> Iterator[str]:
    """Serve a static export on a free loopback port for as long as the block runs; yields its URL.
    Module scripts do not load from file:// URLs, so the browser needs HTTP."""
    handler = functools.partial(_Quiet, directory=str(folder))
    httpd = _Server(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    finally:
        httpd.shutdown()
        httpd.server_close()


@contextlib.contextmanager
def player_url(source: str | Path) -> Iterator[str]:
    """A player URL for a link or an exported folder (served while the block runs)."""
    s = str(source)
    if s.startswith(("http://", "https://")):
        yield s
        return
    folder = Path(source).expanduser()
    if not (folder / "data.json").is_file():
        raise ValueError(f"{folder} is not a showntell export (no data.json)")
    with serve_folder(folder) as url:
        yield url


def _with_params(url: str, **params: str) -> str:
    u = urlsplit(url)
    path = u.path if u.path.endswith("/") or u.path.endswith(".html") else u.path + "/"
    q = [(k, v) for k, v in parse_qsl(u.query) if k not in params] + list(params.items())
    return urlunsplit((u.scheme, u.netloc, path, urlencode(q), ""))


# -- the capturer --------------------------------------------------------------------------------


class Capturer:
    """One headless Chromium page on the player in capture mode.

    `short` sets the output's short side in pixels (1080 gives 1920x1080 landscape). The page is
    laid out at the frame's own size (1280x720 for landscape) and scaled with the device pixel
    ratio, so text stays sharp at any output size.
    """

    def __init__(
        self,
        url: str,
        *,
        short: int = 1080,
        captions: bool = False,
        motion: bool = False,
        image: str = "png",
        browser=None,
        cookie: tuple[str, str] | None = None,
        only_origin: bool = False,
        sandbox: bool = False,
    ) -> None:
        self.url = url
        self.sandbox = sandbox  # Chromium's own sandbox, which Playwright turns off unless asked (hosted mode asks)
        self.cookie = cookie  # (name, value) for the page's origin: hosted mode's session for the owner
        self.only_origin = only_origin  # abort every request to another origin (hosted mode)
        self.short = short
        self.captions = captions
        self.motion = motion  # keep the idle motion (breathing, creep), so held frames differ
        self.image = image
        self._browser = browser  # shared by the caller, or launched here
        self._own: list = []
        self.page = None
        self.frame_name = "landscape"
        self.size = output_size("landscape", short)
        self.duration = 0.0

    async def __aenter__(self) -> Capturer:
        if self._browser is None:
            pw = await require_playwright()().start()
            self._own.append(pw.stop)
            self._browser = await launch(pw, self.sandbox)
            self._own.append(self._browser.close)
        self.frame_name = await _frame_of(self.url, self.cookie)
        native = FRAMES.get(self.frame_name, FRAMES["landscape"])
        self.size = output_size(self.frame_name, self.short)
        ctx = await self._browser.new_context(
            viewport={"width": native[0], "height": native[1]}, device_scale_factor=self.size[0] / native[0]
        )
        self._own.append(ctx.close)
        if self.cookie:
            await ctx.add_cookies([{"name": self.cookie[0], "value": self.cookie[1], "url": _origin(self.url)}])
        if self.only_origin:
            await ctx.route("**/*", _same_origin_only(_origin(self.url)))
        self.page = await ctx.new_page()
        problems: list[str] = []
        self.page.on("pageerror", lambda e: problems.append(str(e)))
        self.page.on("requestfailed", lambda r: problems.append(f"{r.url}: {r.failure}"))
        params = {"capture": "1"}
        if self.captions:
            params["captions"] = "1"
        if self.motion:
            params["motion"] = "1"
        await self.page.goto(_with_params(self.url, **params))
        try:
            await self.page.wait_for_function("window.__showntell?.duration?.() > 0", timeout=30_000)
        except Exception as e:
            raise RuntimeError(f"the player at {self.url} did not load in capture mode: {'; '.join(problems) or e}") from e
        self.duration = float(await self.page.evaluate("window.__showntell.duration()"))
        return self

    async def __aexit__(self, *exc) -> None:
        for close in reversed(self._own):
            with contextlib.suppress(Exception):
                await close()

    async def seek(self, t: float, measure: bool = False) -> str:
        """Bring the player to time t; returns a key that repeats while the picture stays the same.
        `measure` also waits for the player's layout checks on the slide at t."""
        return await self.page.evaluate("([t, m]) => window.__showntell.seek(t, { measure: m })", [t, measure])

    async def seek_slide(self, slide_id: str, measure: bool = False) -> tuple[str, float] | None:
        """Bring the player to the moment a slide shows alone with everything revealed, worked out
        on the page's own timeline; (key, time), or None for a slide it does not have."""
        r = await self.page.evaluate("([id, m]) => window.__showntell.seekSlide(id, { measure: m })", [slide_id, measure])
        return (r[0], float(r[1])) if r else None

    async def shot(self) -> bytes:
        if self.image == "jpeg":
            return await self.page.screenshot(type="jpeg", quality=92)
        return await self.page.screenshot(type="png")

    async def frame(self, t: float) -> tuple[str, bytes]:
        key = await self.seek(t)
        return key, await self.shot()

    async def errors(self) -> list[str]:
        return await self.page.evaluate("window.__showntell.renderErrors || []")


async def launch(pw, sandbox: bool = False):
    """Headless Chromium, with a clear message when the browser is not installed. `sandbox` keeps
    Chromium's sandbox on (Playwright passes --no-sandbox otherwise)."""
    try:
        return await pw.chromium.launch(chromium_sandbox=sandbox)
    except Exception as e:
        if "Executable doesn't exist" in str(e) or "playwright install" in str(e):
            raise CaptureUnavailable("Playwright's Chromium is not installed", install_hint()) from e
        raise


def _origin(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}"


def _same_origin_only(origin: str):
    """A Playwright route handler that lets through only requests to `origin` (and data: URLs,
    which never reach the network); everything else is aborted before it leaves the browser."""

    async def handle(route) -> None:
        url = route.request.url
        if url.startswith("data:") or _origin(url) == origin:
            await route.continue_()
        else:
            log.warning("headless player: blocked a request to %s", url[:200])
            await route.abort("blockedbyclient")

    return handle


async def _frame_of(url: str, cookie: tuple[str, str] | None = None) -> str:
    """The walkthrough's frame, read from data.json before the page is sized."""
    import httpx

    u = urlsplit(_with_params(url))
    path = u.path.rsplit("/", 1)[0] + "/data.json"
    async with httpx.AsyncClient(timeout=30, cookies=dict([cookie]) if cookie else None) as client:
        r = await client.get(urlunsplit((u.scheme, u.netloc, path, u.query, "")))
        r.raise_for_status()
        return json.loads(r.content).get("frame", "landscape")


async def capture_frames_async(
    source: str | Path,
    times: Sequence[float],
    *,
    short: int = 1080,
    captions: bool = False,
    measure: bool = False,
    cookie: tuple[str, str] | None = None,
    only_origin: bool = False,
    sandbox: bool = False,
) -> list[bytes]:
    """PNG bytes of the player at each time (seconds), for a player link or an exported folder.
    `measure` waits for the player's layout checks on each slide, which report to a live link."""
    with player_url(source) as url:
        async with Capturer(url, short=short, captions=captions, cookie=cookie, only_origin=only_origin, sandbox=sandbox) as c:
            out, last_key, last_png = [], None, b""
            for t in times:
                key = await c.seek(t, measure)
                if key != last_key:
                    last_key, last_png = key, await c.shot()
                out.append(last_png)
            return out


async def capture_slides_async(
    source: str | Path,
    slide_ids: Sequence[str],
    *,
    short: int = 1080,
    measure: bool = False,
    cookie: tuple[str, str] | None = None,
    only_origin: bool = False,
    sandbox: bool = False,
) -> list[tuple[float, bytes] | None]:
    """(time, PNG bytes) of each slide alone with everything revealed, None for a slide the
    player does not have. `measure` as for `capture_frames_async`."""
    with player_url(source) as url:
        async with Capturer(url, short=short, cookie=cookie, only_origin=only_origin, sandbox=sandbox) as c:
            out: list[tuple[float, bytes] | None] = []
            for sid in slide_ids:
                r = await c.seek_slide(sid, measure)
                out.append((r[1], await c.shot()) if r else None)
            return out


def capture_frames(source: str | Path, times: Sequence[float], **kw) -> list[bytes]:
    """Blocking form of `capture_frames_async`; not for use inside a running event loop."""
    return asyncio.run(capture_frames_async(source, times, **kw))
