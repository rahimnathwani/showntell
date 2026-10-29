"""Hosted mode in headless Chromium: hostile Markdown runs no script under the CSP, diagrams and
formulas still draw without breaking it, and the server's own Chromium (finish check, preview,
MP4 export) reaches nothing beyond showntell's origin."""

from __future__ import annotations

import http.server
import shutil
import threading

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.slow]
pytest.importorskip("playwright.async_api")

from test_hosted import OPENING, mcp_as, running, text, wid_of  # noqa: E402

from showntell.identity import SESSION_COOKIE, User  # noqa: E402
from showntell.service import _end_times  # noqa: E402

ALICE = User("showntell-dev-login", "alice@example.com", "alice@example.com")

HOSTILE = r"""
## <script>window.__pwned = "title"</script>Title <img src=x onerror="window.__pwned='title-img'"> {#t1}

> <svg onload="window.__pwned='shown'"></svg> shown <iframe srcdoc="<script>parent.__pwned='srcdoc'</script>"></iframe>

Narration with <img src=x onerror="window.__pwned='narration'"> and [a link](javascript:window.__pwned='link').

## Lists {#t2}

- [click me](javascript:window.__pwned='item')
- <a href="javascript:window.__pwned='a'">anchor</a> <b onmouseover="window.__pwned='b'">bold</b>

Each item is hostile.

## Table {#t3}

| Name | Value |
|---|---|
| <img src=x onerror="window.__pwned='cell'"> | <script>window.__pwned='cell2'</script> |

A hostile table.

## Drawing {#t4}

```svg
<svg viewBox="0 0 400 300" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" onload="window.__pwned='svg-onload'">
  <script>window.__pwned = 'svg-script'</script>
  <foreignObject width="100" height="100"><body xmlns="http://www.w3.org/1999/xhtml"><script>window.__pwned='fo'</script><img src="x" onerror="window.__pwned='fo-img'"/></body></foreignObject>
  <use href="data:image/svg+xml;base64,PHN2ZyBpZD0neCcgeG1sbnM9J2h0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnJz48aW1hZ2UgaHJlZj0neCcgb25lcnJvcj0nd2luZG93Ll9fcHduZWQ9MScvPjwvc3ZnPg==#x"/>
  <use xlink:href="https://evil.example/sprite.svg#x"/>
  <a href="javascript:window.__pwned='svg-a'"><rect id="box" x="10" y="10" width="100" height="60" fill="#4a9"/></a>
  <animate attributeName="href" to="javascript:window.__pwned='animate'"/>
  <set attributeName="onload" to="window.__pwned='set'"/>
  <image href="https://evil.example/x.png" onerror="window.__pwned='image'" width="10" height="10"/>
  <style>@import url(https://evil.example/x.css); rect { fill: url(https://evil.example/f) }</style>
</svg>
```

The [box](#box) is the only thing here.

## Diagram {#t5}

```mermaid
%%{init: {"securityLevel": "loose"}}%%
graph TB
  A["<img src=x onerror=window.__pwned='mermaid'>"] --> B[Plain]
  click A call window.alert("mermaid-click")
  click B href "javascript:window.__pwned='mermaid-href'"
```

The [first node](#A) leads to [the second](#B).

## Chart {#t6}

```chart bar
<script>window.__pwned='chart'</script>: 3
<img src=x onerror="window.__pwned='chart-img'">: 5
```

Two hostile bars.

## draw.io {#t7}

```drawio
<mxfile><diagram id="d" name="D"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="c1" value="&lt;img src=x onerror=&quot;window.__pwned='drawio'&quot;&gt;&lt;a href=&quot;javascript:window.__pwned='drawio-a'&quot;&gt;x&lt;/a&gt;" style="html=1;" vertex="1" parent="1"><mxGeometry x="10" y="10" width="120" height="60" as="geometry"/></mxCell>
<UserObject label="L" link="javascript:window.__pwned='drawio-link'" id="c2"><mxCell style="html=1;" vertex="1" parent="1"><mxGeometry x="200" y="10" width="80" height="40" as="geometry"/></mxCell></UserObject>
</root></mxGraphModel></diagram></mxfile>
```

A hostile [cell](#c1).
"""

BENIGN = r"""
## Flow {#b1}

```mermaid
graph TB
  subgraph edge [Edge]
    A[Client] --> B[Cache]
  end
  B --> C[(Store)]
```

The [client](#A) asks [the cache](#B) first.

## Formula {#b2}

```math
\frac{a}{b} = \sum_{i=1}^{n} x_i^2
```

A formula drawn by KaTeX.

## Code {#b3}

```python start=4
def get(self, key):
    return self._data[key]
```

The [return](#L5) is the point.

## Boxes {#b4}

```drawio
<mxfile><diagram id="d" name="D"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="api" value="API" style="rounded=1;html=1;" vertex="1" parent="1"><mxGeometry x="10" y="10" width="120" height="60" as="geometry"/></mxCell>
</root></mxGraphModel></diagram></mxfile>
```

The [API](#api) box.
"""

WATCH = """
window.__csp = [];
document.addEventListener("securitypolicyviolation", (e) => window.__csp.push(e.violatedDirective + " " + e.blockedURI + " " + e.sourceFile + ":" + e.lineNumber));
"""


class Listener:
    """An HTTP server elsewhere on this machine that counts every request it gets."""

    def __init__(self) -> None:
        hits = self.hits = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


async def written(s, markdown: str) -> str:
    r = await s.service.start("Corpus", "landscape", "", 1.0, None, user=ALICE)
    await s.service.add(r["handle"], OPENING + markdown, user=ALICE)
    return r["handle"]


async def open_page(s, wid: str, extra: str = ""):
    """Alice's player in Chromium, signed in, in capture mode; returns (playwright, browser, page, dialogs)."""
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.launch()
    ctx = await browser.new_context(viewport={"width": 1280, "height": 720})
    session = s.hosted.identity.sessions.create(ALICE)
    await ctx.add_cookies([{"name": SESSION_COOKIE, "value": session.id, "url": s.url}])
    await ctx.add_init_script(WATCH)
    page = await ctx.new_page()
    dialogs: list[str] = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    await page.goto(f"{s.url}/w/{wid}/?capture=1{extra}")
    await page.wait_for_function("window.__showntell?.duration?.() > 0", timeout=30_000)
    return pw, browser, page, dialogs


async def visit_every_slide(s, wid: str, page) -> None:
    for t in _end_times(s.store.get(wid).w).values():
        await page.evaluate("(t) => window.__showntell.seek(t)", t)
    await page.wait_for_timeout(300)


async def test_hostile_markdown_runs_no_script(tmp_path, narrator, free_port, voiced):
    async with running(tmp_path, free_port, narrator) as s:
        handle = await written(s, HOSTILE)
        wid = wid_of(handle)
        await voiced(s.store.get(wid))
        pw, browser, page, dialogs = await open_page(s, wid)
        try:
            await visit_every_slide(s, wid, page)
            for sel in ("a", "b", ".diagram svg g.node", ".drawio svg g"):  # what a viewer might click or hover
                for el in await page.query_selector_all(sel):
                    try:
                        await el.click(timeout=200, force=True)
                        await el.hover(timeout=200, force=True)
                    except Exception:
                        pass
            kinds = {sl.id: sl.kind for sl in s.store.get(wid).w.slides}
            assert kinds == {**kinds, "t2": "list", "t3": "table", "t4": "svg", "t5": "diagram", "t6": "chart", "t7": "drawio"}, kinds
            pwned = await page.evaluate("window.__pwned")
            assert pwned is None, pwned
            assert dialogs == []
            assert "javascript:" not in page.url
        finally:
            await browser.close()
            await pw.stop()


async def test_diagrams_formulas_and_code_draw_under_the_csp(tmp_path, narrator, free_port, voiced):
    async with running(tmp_path, free_port, narrator) as s:
        handle = await written(s, BENIGN)
        wid = wid_of(handle)
        await voiced(s.store.get(wid))
        pw, browser, page, _ = await open_page(s, wid)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        try:
            ends = _end_times(s.store.get(wid).w)
            drawn = {}
            for sid, sel in (("b1", ".diagram svg g.node"), ("b2", ".katex"), ("b3", ".hljs, code"), ("b4", ".drawio svg")):
                await page.evaluate("(t) => window.__showntell.seek(t)", ends[sid])
                drawn[sid] = await page.evaluate("(sel) => document.querySelectorAll(sel).length", sel)
            assert all(drawn.values()), drawn
            assert await page.evaluate("window.__csp") == []
            assert await page.evaluate("window.__showntell.renderErrors || []") == []
            assert errors == []
        finally:
            await browser.close()
            await pw.stop()


async def pointed_off_server(s, voiced, listener) -> tuple[str, object]:
    """A voiced walkthrough whose `pic` slide shows an image on `listener`, set past the builder's refusal."""
    handle = await written(s, "## Picture {#pic}\n\nA [picture](#pic) of it.\n")
    sess = s.store.get(wid_of(handle))
    await voiced(sess)
    slide = next(x for x in sess.w.slides if x.id == "pic")
    slide.kind, slide.visual = "image", {"src": f"{listener.url}/metadata.png", "alt": "x"}
    sess._changed({"pic"})
    sess.save_now()
    return handle, sess


async def test_the_servers_chromium_reaches_only_showntell(tmp_path, narrator, free_port, voiced, caplog):
    """The finish check, preview and MP4 export draw the slides signed in as the owner, through the
    guard, and fetch nothing addressed elsewhere: here a slide whose image points off the server.
    The finish check and preview open pages with the CSP, which stops the image in the browser;
    the MP4 export's page has none, so there the request allowlist stops it (next test)."""
    listener = Listener()
    try:
        async with running(tmp_path, free_port, narrator, layout_check=True) as s:
            handle, sess = await pointed_off_server(s, voiced, listener)
            async with mcp_as(s, "alice@example.com") as alice:
                r = await alice.call_tool("finish_walkthrough", {"handle": handle})
                assert not r.is_error, text(r)
                body = text(r)
                assert "no_browser" not in body and "could not run" not in body, body
                assert (s.store.walk_dir(sess.w.id) / "poster.png").is_file()  # drawn, so the page loaded signed in
                r = await alice.call_tool("preview_slides", {"handle": handle, "slides": ["pic"]})
                assert not r.is_error and any(c.type == "image" for c in r.content), text(r)
                if shutil.which("ffmpeg"):
                    r = await alice.call_tool("export_walkthrough", {"handle": handle})
                    assert not r.is_error, text(r)
                    assert r.structured_content["download"].endswith(".mp4")
            assert listener.hits == []
            blocked = [r.getMessage() for r in caplog.records if "blocked a request" in r.getMessage()]
            assert all(listener.url in m for m in blocked)
    finally:
        listener.close()


async def test_the_request_allowlist_stops_what_no_csp_covers(tmp_path, narrator, free_port, voiced, caplog):
    """The page MP4 export draws is a plain export folder with no CSP. Without the allowlist the
    browser fetches the off-server image; with it the request is aborted before it leaves the
    browser, and logged. Needs no ffmpeg: it draws one frame the way the exporter does."""
    from showntell.capture import capture_frames_async
    from showntell.export import export

    listener = Listener()
    try:
        async with running(tmp_path, free_port, narrator) as s:
            _, sess = await pointed_off_server(s, voiced, listener)
            folder = export(s.store.data_dir, sess.w.id, tmp_path / "site")
            at = [_end_times(sess.w)["pic"]]
            await capture_frames_async(folder, at, only_origin=True)
            assert listener.hits == []
            blocked = [r.getMessage() for r in caplog.records if "blocked a request" in r.getMessage()]
            assert blocked and all(listener.url in m for m in blocked)  # asked for, and stopped in the browser
            await capture_frames_async(folder, at)  # the control: without the allowlist the image is fetched
            assert listener.hits == ["/metadata.png"]
    finally:
        listener.close()


async def saved(page) -> None:
    """Wait for the Share dialog to report a save (a selector, not a script: the page's CSP forbids eval)."""
    await page.wait_for_selector("#share .share-status:text-is('Saved.')")
    await page.evaluate("() => { document.querySelector('#share .share-status').textContent = ''; }")


async def test_the_owner_shares_from_the_page(tmp_path, narrator, free_port, voiced):
    """The settings menu's Share… row opens the dialog; adding, removing and the company switch
    reach the walkthrough, with no CSP violation on the way."""
    from playwright.async_api import async_playwright

    async with running(tmp_path, free_port, narrator) as s:
        handle = await written(s, "")
        wid = wid_of(handle)
        await voiced(s.store.get(wid))
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            ctx = await browser.new_context(viewport={"width": 1280, "height": 720})
            session = s.hosted.identity.sessions.create(ALICE)
            await ctx.add_cookies([{"name": SESSION_COOKIE, "value": session.id, "url": s.url}])
            await ctx.add_init_script(WATCH)
            page = await ctx.new_page()
            await page.goto(f"{s.url}/w/{wid}/")
            await page.wait_for_selector("#share-open", state="attached")
            await page.click("#more")
            await page.click("#share-open")
            await page.fill("#share input[type=email]", "bob@example.com")
            await page.click("#share .share-add button")
            await saved(page)
            assert s.store.get(wid).w.shares == ["bob@example.com"]
            await page.fill("#share input[type=email]", "eve@elsewhere.org")
            await page.click("#share .share-add button")
            await page.wait_for_selector("#share .share-status:text('outside')")
            await page.check("#share-company")
            await saved(page)
            assert s.store.get(wid).w.company is True
            await page.click("#share .share-list button")
            await page.wait_for_selector("#share .share-none")
            assert s.store.get(wid).w.shares == []
            await page.click("#share .share-done")
            assert await page.is_hidden("#share")
            assert await page.evaluate("window.__csp") == []
            await browser.close()
