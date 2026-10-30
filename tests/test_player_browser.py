"""Every slide kind in every frame, rendered in headless Chromium against a fake-speech daemon.

Checks that each slide renders without errors, its visual fits inside the stage, and the
pointer lands inside the stage when a cue is active. Screenshots go to tests/artifacts/ for
review. Needs Playwright's Chromium: `uv run playwright install chromium`.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = [pytest.mark.browser, pytest.mark.slow]
pw = pytest.importorskip("playwright.sync_api")

ART = Path(__file__).parent / "artifacts"
ROOT = Path(__file__).resolve().parents[1]

DOC = """# Every slide kind
> A tour of what the player draws

This opening sentence is narrated while the title shows.

## Code from disk {#code .build}

```python file=tests/js/golden/files/short.py lines=1-14
```

The file opens with [a docstring](#L1) and then [imports](#L8 "import re").

## A change {#diff}

```python old
total = 0
for x in xs:
    total += x
```
```python new
total = sum(xs)
```

The loop [goes away](#-L2) and [one call](#+L1 "sum") replaces it.

## Flow {#flow .build}

```mermaid
flowchart LR
  a[Request] --> b{Cached?}
  b -->|yes| c[Return]
  b -->|no| d[Load]
```

A [request](#a) checks [the cache](#b) and either [returns](#c) or [loads](#d).

## Steps {#steps .build}

1. **Parse**: text to blocks {#parse}
2. **Build**: blocks to slides {#build}
3. **Voice**: slides to audio {#voice}

First we [parse](#parse), then [build](#build), then [voice](#voice).

## Options {#cards .cards}

- **Memory**: fast, lost on restart {#mem}
- **Disk**: slower, survives restarts {#disk}
- **Network**: shared between machines {#net}

[Memory](#mem) is fastest and [the network](#net) is shared.

## Checks {#table}

| Check | When |
|---|---|
| Cue target | every call |
| Pacing | slide end |

[Cue targets](#row-1) are checked on every call.

## Softmax {#math}

```math
\\mathrm{softmax}(z)_i = \\frac{e^{z_i}}{\\sum_j e^{z_j}}
```

Each output is [an exponential](#) divided by the sum of all of them.

## Plain words {#text}

> Shown text, not spoken.

A slide with only words on it.

## Newer kinds {#part .chapter}
> Charts, numbers, exchanges and more

## Latency {#chart}

```chart bar title="p95 latency" unit="ms"
series: before, after
read: 420, 180
write: 610, 390
note write: the slow path
```

[Reads](#read) improved most, and [the note](#note-1) marks the slow path.

## Headline {#stat}

```stat
p95 latency: 420 ms -> 180 ms
error rate: 2.1% -> 0.3%
```

[Latency](#p95-latency) fell by more than half, and so did [errors](#error-rate).

## Exchange {#chat}

```chat
user: How do I undo my last commit?
assistant: Run `git reset --soft HEAD~1`. Your changes stay staged.
```

The [question](#m1) gets [a short answer](#m2).

## Session {#terminal}

```terminal
$ uv run pytest -q
783 passed in 82.9s
$ git status --short
 M README.md
```

[The tests](#L2) pass, and [one file](#L4) changed.

## Layout {#tree}

```tree
src/
  app.py   # entry point
  cache/
    lru.py
tests/
  test_cache.py
```

The [cache](#lru.py) has its own folder, next to [the entry point](#app.py).

## Screen {#image}

![A settings page](tests/js/golden/files/screen.png){frame=browser url="example.com/settings"}

The [search box](#@4,16,55,9) sits above [the first result](#@4,31,92,14){.circle}.

## Drawing {#svg .build}

```svg
<svg viewBox="0 0 1150 560">
  <g id="sun"><title>Sun</title><circle cx="130" cy="130" r="64" fill="#ffd166"/>
    <text x="130" y="235" text-anchor="middle" font-size="28" fill="var(--fg)">Sun</text></g>
  <path id="beam" d="M200 150 L560 260" stroke="var(--fg)" stroke-width="6" fill="none"/>
  <g id="blue"><path d="M575 250 L680 90" stroke="#4f8cff" stroke-width="5" fill="none"/>
    <path d="M560 280 L430 430" stroke="#4f8cff" stroke-width="5" fill="none"/></g>
  <path id="red" d="M590 265 L1060 400" stroke="#ff5a4e" stroke-width="5" fill="none"/>
  <text id="eye" x="400" y="490" font-size="28" fill="var(--fg)">your eye</text>
</svg>
```

[Sunlight](#beam) meets the air. [Blue](#blue) scatters toward [your eye](#eye) and [red](#red) passes on.

## Five years {#timeline .timeline .build}

- **1989**: A memo proposes linked documents
- **1990**: The first server runs
- **1991**: The Web opens to the public
- **1993**: Released into the public domain

It began with [a memo](#1989), and [two years later](#1991) anyone could use it.
"""

KINDS = [
    "title",
    "code",
    "diff",
    "diagram",
    "list",
    "list",
    "table",
    "math",
    "text",
    "chapter",
    "chart",
    "stat",
    "chat",
    "terminal",
    "tree",
    "image",
    "svg",
    "list",
]


BASELINES = Path(__file__).parent / "baselines" / "linux"
MAX_CHANGED = 0.005  # fraction of pixels allowed to differ noticeably from the baseline


def compare_baseline(shot: Path) -> str | None:
    """Compare a screenshot with its Linux baseline and return the mismatch, if any. Fonts and
    Chromium builds differ between systems, so baselines exist only for Linux CI; elsewhere, and
    before baselines are added (scripts/update_baselines.sh), this does nothing."""
    base = BASELINES / shot.name
    if sys.platform != "linux" or not base.is_file():
        return None
    import numpy as np
    from PIL import Image

    a = np.asarray(Image.open(shot).convert("RGB"), dtype=np.int16)
    b = np.asarray(Image.open(base).convert("RGB"), dtype=np.int16)
    if a.shape != b.shape:
        return f"{shot.name}: size {a.shape} differs from the baseline {b.shape}"
    changed = float((np.abs(a - b).max(axis=2) > 24).mean())
    if changed > MAX_CHANGED:
        return f"{shot.name}: {changed:.2%} of pixels differ from tests/baselines/linux"
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def daemon(tmp_path_factory):
    data = tmp_path_factory.mktemp("browser-data")
    port = _free_port()
    env = {**os.environ, "SHOWNTELL_DATA": str(data), "SHOWNTELL_SPEECH": "fake"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "showntell", "serve", "--port", str(port), "--speech", "fake"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 20
    lock = None
    while time.time() < deadline:
        if (data / "daemon.json").is_file():
            lock = json.loads((data / "daemon.json").read_text())
            try:
                if httpx.get(f"http://127.0.0.1:{lock['port']}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
        time.sleep(0.2)
    assert lock, "daemon did not start"
    yield lock
    proc.terminate()
    proc.wait(10)


def _make(lock: dict, frame: str, doc: str = DOC, title: str = "Every slide kind") -> str:
    h = {"Authorization": f"Bearer {lock['token']}"}
    base = f"http://127.0.0.1:{lock['port']}/api/rpc"

    def call(m, **kw):
        r = httpx.post(f"{base}/{m}", json=kw, headers=h, timeout=30)
        r.raise_for_status()
        return r.json()["result"]

    r = call("start", title=title, frame=frame, voice="", speed=1.0, project_dir=str(ROOT))
    call("add", handle=r["handle"], markdown=doc)
    call("playable", handle=r["handle"], timeout=10)
    for _ in range(50):
        if not call("finish", handle=r["handle"], questions=["What next?"])["pending"]:
            break
        time.sleep(0.2)
    return r["url"].replace("localhost", "127.0.0.1") + "/"


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # browsers not installed
            pytest.skip(f"Chromium unavailable: {e}")
        yield b
        b.close()


@pytest.mark.parametrize("frame", ["landscape", "square", "portrait"])
def test_every_kind_renders(daemon, browser, frame):
    url = _make(daemon, frame)
    data = httpx.get(url + "data.json").json()
    assert [s["kind"] for s in data["slides"]] == KINDS
    page = browser.new_page(viewport={"width": 1440, "height": 900} if frame != "portrait" else {"width": 900, "height": 1400})
    ART.mkdir(exist_ok=True)
    # Every slide is shot before a baseline mismatch fails the test, so one CI run carries the
    # full set for scripts/update_baselines.sh.
    mismatches = []
    for i, slide in enumerate(data["slides"]):
        # a moment after the first cue of the slide, or a second in
        page.goto(url)
        page.wait_for_function("window.__showntell !== undefined")
        t = page.evaluate(
            """(i) => new Promise((res) => {
                 import('./' + document.body.dataset.static + '/js/timeline.js').then((m) => {
                   fetch('data.json').then((r) => r.json()).then((w) => {
                     const tl = m.build(w); const seg = tl.segs.find((s) => s.slide === i);
                     const cues = w.slides[i].says.flatMap((x) => x.cues || []);
                     const first = cues.length ? Math.max(0, cues[cues.length - 1].t || 0) : 0.5;
                     res(seg.start + Math.min(first + 0.2, seg.dur));
                   });
                 });
               })""",
            i,
        )
        page.goto(f"{url}?at={t:.2f}")
        page.wait_for_timeout(900 if slide["kind"] in ("diagram", "math") else 400)
        page.evaluate("document.querySelector('#overlay').hidden = true")
        state = page.evaluate(
            """() => {
              const stage = document.querySelector('#stage').getBoundingClientRect();
              const slide = document.querySelector('.slide:not(.leaving)');
              const ptr = document.querySelector('#pointer');
              const p = ptr.hidden ? null : ptr.getBoundingClientRect();
              const body = document.querySelector('.slide:not(.leaving) .slide-body');
              const vis = body && body.firstElementChild ? body.firstElementChild.getBoundingClientRect() : null;
              return {cls: slide && slide.className, errors: window.__showntell.renderErrors,
                      stage: stage.toJSON(), pointer: p && p.toJSON(), visual: vis && vis.toJSON()};
            }"""
        )
        shot = ART / f"{frame}-{i:02d}-{slide['kind']}.png"
        # Let the slide finish easing in (the player then re-places the pointer) and every
        # transition settle, so the same frame comes out on every run.
        page.wait_for_function("document.getAnimations().every((a) => a.playState !== 'running')", timeout=5000)
        page.wait_for_timeout(150)
        page.screenshot(path=str(shot), animations="disabled")
        if miss := compare_baseline(shot):
            mismatches.append(miss)
        assert f"k-{slide['kind']}" in state["cls"]
        assert not state["errors"], state["errors"]
        st = state["stage"]
        if state["visual"]:
            v = state["visual"]
            assert v["left"] >= st["left"] - 2 and v["right"] <= st["right"] + 2, f"{frame} slide {i} overflows sideways"
            assert v["bottom"] <= st["bottom"] + 2, f"{frame} slide {i} overflows downwards"
        if state["pointer"]:
            p = state["pointer"]
            assert p["left"] >= st["left"] - 20 and p["right"] <= st["right"] + 20
            assert p["top"] >= st["top"] - 20 and p["bottom"] <= st["bottom"] + 20
    page.close()
    assert not mismatches, "\n".join(mismatches)


# -- ranges, presenting, embedding, layout findings -------------------------------------------------


def _rpc(lock: dict):
    h = {"Authorization": f"Bearer {lock['token']}"}
    base = f"http://127.0.0.1:{lock['port']}/api/rpc"

    def call(m, **kw):
        r = httpx.post(f"{base}/{m}", json=kw, headers=h, timeout=30)
        r.raise_for_status()
        return r.json()["result"]

    return call


def test_a_range_plays_its_slides_and_stops(daemon, browser):
    url = _make(daemon, "landscape")
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(url + "?from=flow&to=flow")
    page.wait_for_function("window.__showntell !== undefined && !document.querySelector('#big-play').disabled")
    page.click("#big-play")
    page.wait_for_selector("#range-note:not([hidden])", timeout=20000)
    shown = page.evaluate("document.querySelector('.slide:not(.leaving)').className")
    assert "k-diagram" in shown and not page.evaluate("window.__showntell.ended")
    page.close()


def test_a_token_in_the_link_is_not_a_start_time(daemon, browser):
    # On links served beyond this machine `t` carries the access token; one that started with
    # digits used to send the player part-way in. The start time is `at`.
    url = _make(daemon, "landscape")
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    kind = "[...document.querySelector('.slide:not(.leaving)').classList].find((c) => c.startsWith('k-'))"
    page.goto(url + "?t=9e3Xk2")
    page.wait_for_selector(".slide")
    page.wait_for_timeout(400)
    assert page.evaluate(kind) == "k-title"
    page.goto(url + "?at=9000")
    page.wait_for_selector(".slide")
    page.wait_for_timeout(400)
    assert page.evaluate(kind) != "k-title"
    page.close()


def test_presenting_steps_by_hand_without_voice(daemon, browser):
    url = _make(daemon, "landscape")
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(url + "?present=1")
    page.wait_for_selector("#notes:not([hidden])")
    first = page.inner_text("#notes-step")
    for _ in range(4):
        page.keyboard.press("Space")
    page.wait_for_timeout(300)
    assert page.inner_text("#notes-step") != first and "step 5 of" in page.inner_text("#notes-step")
    assert page.evaluate("window.__showntell.starts.length") == 0  # nothing was voiced
    page.close()


def test_embedded_player_sends_events_and_takes_commands(daemon, browser):
    url = _make(daemon, "landscape")
    embed = f"http://127.0.0.1:{daemon['port']}/static/embed.js"
    page = browser.new_page(viewport={"width": 1200, "height": 900})
    host = f"""<!doctype html><script src="{embed}"></script>
        <showntell-player id="p" src="{url}"></showntell-player>
        <script>window.got = []; for (const t of ["ready", "slidechange", "durationchange"])
          document.getElementById("p").addEventListener(t, (e) => got.push([t, e.detail]));</script>"""
    # A page on another origin (another local port), as a docs site embedding a walkthrough.
    import http.server
    import threading

    class Host(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(host.encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Host)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    page.goto(f"http://127.0.0.1:{srv.server_port}/")
    page.wait_for_function("window.got.some(([t]) => t === 'ready')", timeout=15000)
    page.evaluate("document.getElementById('p').seek(document.getElementById('p').duration - 0.5)")
    page.wait_for_function("window.got.some(([t, d]) => t === 'slidechange' && d.id === 'timeline')", timeout=10000)
    ready = page.evaluate("window.got.find(([t]) => t === 'ready')[1]")
    assert ready["slides"] == len(KINDS) and ready["duration"] > 0
    page.close()
    srv.shutdown()


def test_the_player_reports_text_shrunk_too_far(daemon, browser):
    call = _rpc(daemon)
    r = call("start", title="Too big", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    long = "\n".join(f"value_{i} = compute_something_long(alpha, beta, gamma, delta, epsilon_{i})" for i in range(70))
    call(
        "add",
        handle=r["handle"],
        markdown=f"# Too big\n\nOpening words.\n\n## Wall {{#wall}}\n\n```python\n{long}\n```\n\nA wall of code.\n",
    )
    call("playable", handle=r["handle"], timeout=10)
    url = r["url"].replace("localhost", "127.0.0.1") + "/"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(url + "?from=wall")
    page.wait_for_function("(window.__showntell.findings || []).some((f) => f.code === 'text_too_small')", timeout=10000)
    page.wait_for_timeout(300)
    f = call("finish", handle=r["handle"], questions=None)
    assert any(i["code"] == "text_too_small" and i["slide"] == "wall" and i["source"] == "player" for i in f["player_issues"])
    page.close()


def test_finish_draws_every_slide_and_reports_layout(daemon):
    call = _rpc(daemon)
    r = call("start", title="Unopened", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    long = "\n".join(f"value_{i} = compute_something_long(alpha, beta, gamma, delta, epsilon_{i})" for i in range(70))
    call(
        "add",
        handle=r["handle"],
        markdown=f"# Unopened\n\nOpening words.\n\n## Wall {{#wall}}\n\n```python\n{long}\n```\n\nA wall of code.\n",
    )
    call("playable", handle=r["handle"], timeout=10)
    f = call("finish", handle=r["handle"], questions=None)
    assert any(i["code"] == "text_too_small" and i["slide"] == "wall" for i in f["player_issues"])
    assert not any(i["code"] == "not_checked" and "player" in i["message"] for i in f["issues"])


def test_finish_shows_slides_that_look_alike(daemon):
    call = _rpc(daemon)
    r = call("start", title="Alike", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    md = "# Alike\n\nOpening words.\n\n" + "".join(
        f"## Part {i} {{#p{i}}}\n\n```python\nx = {i}\n```\n\nThe [line](#L1) sets it.\n\n" for i in range(3)
    )
    call("add", handle=r["handle"], markdown=md)
    call("playable", handle=r["handle"], timeout=10)
    f = call("finish", handle=r["handle"], questions=None)
    import base64

    assert f["alike"] == [{"slides": ["p0", "p1", "p2"], "look": "code"}]
    assert [x["id"] for x in f["thumbs"]] == ["p0", "p1", "p2"]
    assert all(base64.b64decode(x["png"])[:8] == b"\x89PNG\r\n\x1a\n" for x in f["thumbs"])
    # Finishing also kept the title slide as the poster, served next to the player and shown in the gallery.
    import httpx

    poster = httpx.get(f"{r['url']}/poster.png")
    assert poster.status_code == 200 and poster.content[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = int.from_bytes(poster.content[16:20], "big"), int.from_bytes(poster.content[20:24], "big")
    assert (w, h) == (640, 360)
    base = r["url"].rsplit("/w/", 1)[0]
    assert f'<img class="thumb" src="/w/{r["url"].rsplit("/", 1)[1]}/poster.png" alt="">' in httpx.get(f"{base}/").text
    assert f'<meta property="og:image" content="{r["url"]}/poster.png">' in httpx.get(f"{r['url']}/").text


def test_long_code_steps_that_scroll_fit_the_slide(daemon):
    call = _rpc(daemon)
    r = call("start", title="Long steps", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    one = "\n".join(f"step_{i} = compute(step_{i - 1})" for i in range(1, 45))
    two = one.replace("compute(", "compute_faster(")
    md = f"# Long steps\n\nOpening words.\n\n## Faster {{#faster}}\n\n```python step scroll\n{one}\n```\n```python step scroll\n{two}\n```\n\nIt [gets faster](#step-2) at [the end](#L40).\n"
    call("add", handle=r["handle"], markdown=md)
    call("playable", handle=r["handle"], timeout=10)
    f = call("finish", handle=r["handle"], questions=None)
    assert not [i for i in f["player_issues"] if i["code"] == "overflow"], f["player_issues"]


def test_a_new_code_version_clears_the_scroll_focus(daemon, browser):
    call = _rpc(daemon)
    r = call("start", title="Focus", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    one = "\n".join(f"step_{i} = compute(step_{i - 1})" for i in range(1, 45))
    md = (
        f"# Focus\n\nOpening words.\n\n## Faster {{#faster}}\n\n```python step scroll\n{one}\n```\n```python step scroll\n{one.replace('compute(', 'fast(')}\n```\n\n"
        "First [this line](#L30). Then it [gets faster](#step-2), all of it at once, everywhere. And [this one](#L40).\n"
    )
    call("add", handle=r["handle"], markdown=md)
    call("playable", handle=r["handle"], timeout=10)
    url = r["url"].replace("localhost", "127.0.0.1") + "/"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    focusing = []
    for cue in (0, 1, 2):
        page.goto(url)
        page.wait_for_function("window.__showntell !== undefined")
        t = _at(page, "faster", cue, 0.3)
        page.goto(f"{url}?at={t:.2f}")
        page.wait_for_selector(".codebox.scroll")
        page.wait_for_timeout(500)
        focusing.append(page.evaluate("document.querySelector('.codebox.scroll').classList.contains('focusing')"))
    assert focusing == [True, False, True]
    page.close()


def test_preview_returns_pictures_of_slides(daemon, browser):
    import base64

    call = _rpc(daemon)
    r = call("start", title="Preview", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    call("add", handle=r["handle"], markdown=DOC)
    call("playable", handle=r["handle"], timeout=10)
    p = call("preview", handle=r["handle"], slides=["flow", "math", "nope"], short=360)
    assert [s["id"] for s in p["slides"]] == ["flow", "math"] and p["unknown"] == ["nope"]
    png = base64.b64decode(p["slides"][0]["png"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000
    assert p["slides"][0]["png"] != p["slides"][1]["png"]


def test_preview_times_show_each_slide_alone(daemon, browser):
    # The next slide starts coming in 0.45 s before its first word; a picture taken then shows two.
    call = _rpc(daemon)
    r = call("start", title="Alone", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    call("add", handle=r["handle"], markdown=DOC)
    call("playable", handle=r["handle"], timeout=10)
    p = call("preview", handle=r["handle"], short=360)
    page = browser.new_page()
    page.goto(r["url"].replace("localhost", "127.0.0.1") + "/?capture=1")
    page.wait_for_function("window.__showntell?.duration?.() > 0")
    for s in p["slides"]:
        key = page.evaluate("(t) => window.__showntell.seek(t)", s["at_s"])
        shown = page.evaluate("[...document.querySelectorAll('#stage .slide')].map((e) => e.className)")
        assert key.split("|")[0] == s["id"] and len(shown) == 1 and "leaving" not in shown[0], (s["id"], s["at_s"], shown)
    page.close()


def test_slide_pictures_follow_the_page_timeline(daemon, browser):
    # Narration voiced after the caller worked out its times moves every later slide; pictures
    # timed on the page's own timeline still show each slide alone.
    call = _rpc(daemon)
    doc = "# Moving\n\nOpening line.\n\n" + "".join(f"## Slide {i} {{#s{i}}}\n\nSlide {i} says a short line.\n\n" for i in range(3))
    r = call("start", title="Moving", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    call("add", handle=r["handle"], markdown=doc)
    call("playable", handle=r["handle"], timeout=10)
    page = browser.new_page()
    page.goto(r["url"].replace("localhost", "127.0.0.1") + "/?capture=1")
    page.wait_for_function("window.__showntell?.duration?.() > 0")
    before = page.evaluate("window.__showntell.duration()")
    call("revise", handle=r["handle"], slide="s0", markdown="This line is now a good deal longer, so every slide after it moves later.")
    page.wait_for_function(f"window.__showntell.duration() > {before} + 1", timeout=15_000)
    for sid in ("s0", "s1", "s2"):
        key, _ = page.evaluate("(id) => window.__showntell.seekSlide(id)", sid)
        shown = page.evaluate("[...document.querySelectorAll('#stage .slide')].map((e) => e.className)")
        assert key.split("|")[0] == sid and len(shown) == 1 and "leaving" not in shown[0], (sid, shown)
    assert page.evaluate("window.__showntell.seekSlide('nope')") is None
    page.close()


# -- code that evolves, scrolling code, packets, numbers, regions and zoom, checklists ---------------

MORE = """# More kinds

The newer visuals.

## Part one {#part .chapter}
> Code

## Evolving {#evolve}

```python step
def total(xs):
    t = 0
    for x in xs:
        t += x
    return t
```
```python step
def total(xs):
    return sum(xs)
```

The loop [adds each value](#L4). Then it [becomes one call](#step-2) and [sum does the work](#L2 "sum").

## Long {#long}

```python file=tests/js/golden/files/long.py lines=1-150 scroll
```

First [the imports](#L9). Much later, [this line](#L140).

## Path {#path}

```mermaid
flowchart LR
  c[Client] --> g[Gateway] --> s[Service]
```

A request goes [all the way through](#c->g->s) and the [gateway](#g){.circle} checks it.

## Numbers {#numbers}

```stat
p95 latency: 400 ms -> 100 ms
```

The [p95 latency](#p95-latency) fell by three quarters, which took a lot of patient work over many weeks.

## Screen {#screen}

![The demo](docs/media/demo.gif){frame=browser url="example.com"}

The [top left](#@0,0,50,40){.zoom} is where it starts, and the camera stays on it while the rest of the page waits its turn.

## Ship {#ship}

- [x] **Tests**: pass {#tests}
- [ ] **Announce**: later {#announce}

The [tests](#tests) pass, and the [announcement](#announce) waits.
"""


def _at(page, slide_id: str, cue: int, after: float) -> float:
    """Global time `after` seconds past cue number `cue` of a slide."""
    return page.evaluate(
        """([sid, ci, off]) => import('./' + document.body.dataset.static + '/js/timeline.js').then((m) =>
             fetch('data.json').then((r) => r.json()).then((w) => {
               const tl = m.build(w); const si = w.slides.findIndex((s) => s.id === sid); const at = [];
               for (const s of tl.segs.filter((s) => s.slide === si)) if (s.say >= 0)
                 for (const c of w.slides[si].says[s.say].cues || []) at.push(s.start + (c.t || 0));
               return at[ci] + off; }))""",
        [slide_id, cue, after],
    )


def test_newer_visuals_follow_the_narration(daemon, browser):
    call = _rpc(daemon)
    r = call("start", title="More kinds", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    a = call("add", handle=r["handle"], markdown=MORE)
    assert not [i for i in a["issues"] if i["severity"] == "error"], a["issues"]
    call("playable", handle=r["handle"], timeout=10)
    for _ in range(50):
        if not call("finish", handle=r["handle"], questions=None)["pending"]:
            break
        time.sleep(0.2)
    url = r["url"].replace("localhost", "127.0.0.1") + "/"
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def at(slide_id, cue, after, js):
        page.goto(url)
        page.wait_for_function("window.__showntell !== undefined")
        t = _at(page, slide_id, cue, after)
        page.goto(f"{url}?at={t:.2f}")
        page.wait_for_timeout(700)
        return page.evaluate(js)

    page.goto(url + "?from=part&to=part")
    page.wait_for_selector(".k-chapter")
    assert page.text_content(".k-chapter .chapter-n") == "Part 1"
    assert at("evolve", 0, 0.3, "document.querySelector('.codebox .tag').textContent") == "Step 1 of 2"
    tag, line = at(
        "evolve", 2, 0.3, "[document.querySelector('.codebox .tag').textContent, document.querySelector('.ln.hl .tx').textContent]"
    )
    assert tag == "Step 2 of 2" and "sum" in line
    shift, focusing = at(
        "long",
        1,
        0.3,
        "[getComputedStyle(document.querySelector('.codebox.scroll .code')).transform, document.querySelector('.codebox.scroll').classList.contains('focusing')]",
    )
    assert shift not in ("none", "") and focusing
    x1 = at("path", 0, 0.2, "+document.querySelector('circle.packet').getAttribute('cx')")
    # before the gateway cue takes over, which fires CUE_LEAD (0.3 s) ahead of its word
    x2 = at("path", 0, 0.7, "+document.querySelector('circle.packet').getAttribute('cx')")
    assert x2 > x1 > 0  # the packet travels left to right
    assert at("path", 1, 0.3, "document.querySelector('#pointer').dataset.kind") == "circle"
    early = at("numbers", 0, 0.2, "document.querySelector('.stat .num').textContent")
    late = at("numbers", 0, 3.0, "document.querySelector('.stat .num').textContent")
    assert late == "100 ms" and early not in ("100 ms", "400 ms")
    # The camera moves in over about a second, then frames the region: a zoom onto a large part
    # needs no pointer.
    framed, zoomed = at(
        "screen",
        0,
        1.6,
        """[document.querySelector('#pointer').hidden,
        parseFloat(document.querySelector('.slide:not(.leaving) .slide-body').style.transform.split('scale(')[1]) > 1.2]""",
    )
    assert framed and zoomed
    # Frame capture holds a close-up still, so held frames repeat; ?motion=1 keeps the creep.
    t = _at(page, "screen", 0, 1.4)
    for extra, alike in (("", True), ("&motion=1", False)):
        page.goto(f"{url}?capture=1{extra}")
        page.wait_for_function("window.__showntell?.duration?.() > 0")
        keys = [page.evaluate("(t) => window.__showntell.seek(t)", t + d) for d in (0.0, 0.4)]
        assert (keys[0] == keys[1]) == alike and "scale(" in keys[0], keys
    ticked = at("ship", 1, 0.3, "[...document.querySelectorAll('.entry.ticked')].map((e) => e.dataset.id)")
    assert ticked == ["tests"]
    assert not errors, errors
    page.close()


def test_reduced_motion_holds_still_and_moving_cues_are_announced(daemon, browser):
    call = _rpc(daemon)
    r = call("start", title="Calm", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    call("add", handle=r["handle"], markdown=MORE)
    call("playable", handle=r["handle"], timeout=10)
    for _ in range(50):
        if not call("finish", handle=r["handle"], questions=None)["pending"]:
            break
        time.sleep(0.2)
    url = r["url"].replace("localhost", "127.0.0.1") + "/"
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = ctx.new_page()

    def at(slide_id, cue, after, js, ready="body"):
        page.goto(url)
        page.wait_for_function("window.__showntell !== undefined")
        t = _at(page, slide_id, cue, after)
        page.goto(f"{url}?at={t:.2f}")
        page.wait_for_selector(ready, state="attached")
        page.wait_for_timeout(700)
        return page.evaluate(js)

    # Numbers show their value at once, and the dot waits at the end of its route.
    assert at("numbers", 0, 0.2, "document.querySelector('.stat .num').textContent") == "100 ms"
    x1 = at("path", 0, 0.2, "+document.querySelector('circle.packet').getAttribute('cx')", "circle.packet")
    x2 = at("path", 0, 1.0, "+document.querySelector('circle.packet').getAttribute('cx')", "circle.packet")
    assert x1 == x2 > 0
    # While playing, the route of a travelling dot is announced to screen readers.
    page.goto(url)
    page.wait_for_function("window.__showntell !== undefined")
    t = _at(page, "path", 0, -0.8)
    page.goto(f"{url}?at={t:.2f}")
    page.wait_for_function("!document.querySelector('#big-play').disabled")
    page.click("#big-play")
    page.wait_for_function("document.querySelector('#announce').textContent.startsWith('Path:')", timeout=8000)
    assert page.text_content("#announce") == "Path: Client to Gateway to Service"
    ctx.close()


DRAWING_MD = """# Drawing

Opening words.

## Sky {#sky .build}

```svg
<svg viewBox="0 0 1150 560" onload="window.pwned = 1">
  <script>window.pwned = 2</script>
  <style>.ray { stroke-width: 6 }</style>
  <g id="sun"><title>Sun</title><circle cx="120" cy="140" r="60" fill="#ffd166"/><circle id="core" cx="120" cy="140" r="14" fill="#fff"/></g>
  <path id="beam" class="ray" d="M190 160 L560 270" stroke="#f4f4f4" fill="none"/>
  <text id="eye" x="420" y="480" font-size="30" fill="var(--fg)">your eye</text>
</svg>
```

First [the sun](#sun), bright and far away.

Then [its light](#beam) crosses the sky.

Some of it reaches [you](#eye).
"""


def test_drawings_reveal_point_and_never_run_script(daemon, browser):
    call = _rpc(daemon)
    r = call("start", title="Drawing", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    a = call("add", handle=r["handle"], markdown=DRAWING_MD)
    assert "svg_removed" in [i["code"] for i in a["issues"]]
    call("playable", handle=r["handle"], timeout=10)
    url = r["url"].replace("localhost", "127.0.0.1") + "/"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(url)
    page.wait_for_function("window.__showntell !== undefined")
    t = _at(page, "sky", 0, 0.3)
    page.goto(f"{url}?at={t:.2f}")
    page.wait_for_selector(".drawing")
    page.wait_for_timeout(600)
    state = page.evaluate("""() => {
      const root = document.querySelector('.drawing').shadowRoot, box = (id) => root.getElementById(id).getBoundingClientRect().toJSON();
      const p = document.querySelector('#pointer');
      return { veiled: ['sun', 'beam', 'eye'].filter((id) => root.getElementById(id).classList.contains('veiled')),
               sun: box('sun'), pointer: p.hidden ? null : p.getBoundingClientRect().toJSON(), pwned: window.pwned ?? null }; }""")
    assert state["veiled"] == ["beam", "eye"] and state["pwned"] is None
    p, s = state["pointer"], state["sun"]
    assert p and p["left"] <= s["left"] + 2 and p["right"] >= s["right"] - 2 and p["top"] <= s["top"] + 2
    # Seeking forward in capture mode shows every part once the narration has ended.
    page.goto(url + "?capture=1")
    page.wait_for_function("window.__showntell?.duration?.() > 0")
    page.evaluate("(t) => window.__showntell.seek(t)", t)
    end = page.evaluate("window.__showntell.duration()") - 0.1
    page.evaluate("(t) => window.__showntell.seek(t)", end)
    assert page.evaluate("document.querySelector('.drawing').shadowRoot.querySelectorAll('.veiled').length") == 0
    page.close()


ORBIT_MD = """# Orbit

Opening words.

## The moon moves {#phase}

```svg step sketch
<svg viewBox="0 0 1150 560">
  <circle id="earth" cx="575" cy="280" r="70" fill="#2f7de1"/>
  <g id="moon" transform="rotate(0 575 280)"><circle cx="765" cy="280" r="24" fill="#d9d9d9"/></g>
  <text id="label" x="575" y="520" text-anchor="middle" font-size="30" fill="var(--fg)">New moon</text>
</svg>
```
```svg step sketch
<svg viewBox="0 0 1150 560">
  <circle id="earth" cx="575" cy="280" r="70" fill="#2f7de1"/>
  <g id="moon" transform="rotate(-90 575 280)"><circle cx="765" cy="280" r="24" fill="#d9d9d9"/></g>
  <text id="label" x="575" y="520" text-anchor="middle" font-size="30" fill="var(--fg)">First quarter</text>
  <line id="cross" x1="400" y1="510" x2="760" y2="510" stroke="#ff5a4e" stroke-width="4"/>
</svg>
```

The [moon](#moon) starts on the right, and a week later it has [moved a quarter of the way round](#step-2).
"""


def test_drawing_steps_glide_on_the_clock_and_frames_capture_the_motion(daemon, browser):
    call = _rpc(daemon)
    r = call("start", title="Orbit", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    call("add", handle=r["handle"], markdown=ORBIT_MD)
    call("playable", handle=r["handle"], timeout=10)
    for _ in range(50):
        if not call("finish", handle=r["handle"], questions=None)["pending"]:
            break
        time.sleep(0.2)
    url = r["url"].replace("localhost", "127.0.0.1") + "/"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(url)
    page.wait_for_function("window.__showntell !== undefined")
    at = _at(page, "phase", 1, 0.0)
    page.goto(url + "?capture=1")
    page.wait_for_function("window.__showntell?.duration?.() > 0")
    angle = (
        "(() => { const g = document.querySelector('.drawing').shadowRoot.getElementById('moon'); return g.getAttribute('transform'); })()"
    )
    seen = []
    for d in (-0.2, 0.6, 2.0):
        page.evaluate("(t) => window.__showntell.seek(t)", at + d)
        seen.append(page.evaluate(angle))
    page.evaluate("(t) => window.__showntell.seek(t)", at + 0.3)  # back into the glide after it ended
    back = page.evaluate(angle)
    sketched = page.evaluate("document.querySelector('.drawing').shadowRoot.querySelectorAll('.rough').length")
    # Frame capture keys each in-between picture apart, so video export does not repeat a frame.
    keys = [page.evaluate("(t) => window.__showntell.seek(t)", at + d) for d in (0.2, 0.6, 1.0)]
    page.close()
    first, middle, last = (float(s.split("(")[1].split()[0]) for s in seen)
    assert first == 0 and -90 < middle < 0 and last == -90 and -90 < float(back.split("(")[1].split()[0]) < 0
    assert sketched >= 2 and len(set(keys)) == 3
    # The finish check draws every slide: no render errors, and the line through the label is noticed
    # (the check measures the drawing as first shown, so this slide's line only shows in version 2).
    f = call("finish", handle=r["handle"], questions=None)
    assert not [i for i in f["player_issues"] if i["code"] == "render_error"]


def test_a_line_through_a_label_is_reported(daemon):
    call = _rpc(daemon)
    r = call("start", title="Crossed", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    md = """# Crossed

Opening words.

## Rays {#rays}

```svg
<svg viewBox="0 0 1150 560">
  <text id="label" x="575" y="300" text-anchor="middle" font-size="36" fill="var(--fg)">Air molecule</text>
  <line id="ray" x1="300" y1="200" x2="850" y2="400" stroke="#4f8cff" stroke-width="5"/>
  <text id="clear" x="200" y="500" font-size="30" fill="var(--fg)">Far from the line</text>
</svg>
```

The [ray](#ray) crosses [the label](#label).
"""
    call("add", handle=r["handle"], markdown=md)
    call("playable", handle=r["handle"], timeout=10)
    f = call("finish", handle=r["handle"], questions=None)
    found = [i["message"] for i in f["player_issues"] if i["code"] == "drawing_overlap"]
    assert len(found) == 1 and "Air molecule" in found[0]


def test_a_tall_flowchart_on_a_wide_slide_is_reported(daemon):
    call = _rpc(daemon)
    r = call("start", title="Tall", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    chain = " --> ".join(f"n{i}[Step {i}]" for i in range(7))
    md = f"# Tall\n\nOpening words.\n\n## Chain {{#chain}}\n\n```mermaid\nflowchart TB\n  {chain}\n```\n\nThe [first step](#n0) starts it.\n\n"
    md += (
        f"## Wide {{#wide .side}}\n> The chain beside a point\n\n```mermaid\nflowchart TB\n  {chain}\n```\n\nThe [first step](#n0) again.\n"
    )
    call("add", handle=r["handle"], markdown=md)
    call("playable", handle=r["handle"], timeout=10)
    f = call("finish", handle=r["handle"], questions=None)
    found = [i for i in f["player_issues"] if i["code"] == "diagram_shape"]
    assert [i["slide"] for i in found] == ["chain"] and "flowchart LR" in found[0]["message"], found


def test_moving_dots_and_counting_numbers_change_the_capture_key(daemon, browser):
    call = _rpc(daemon)
    r = call("start", title="Motion", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    call("add", handle=r["handle"], markdown=MORE)
    call("playable", handle=r["handle"], timeout=10)
    for _ in range(50):
        if not call("finish", handle=r["handle"], questions=None)["pending"]:
            break
        time.sleep(0.2)
    url = r["url"].replace("localhost", "127.0.0.1") + "/"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(url)
    page.wait_for_function("window.__showntell !== undefined")
    dot, count = _at(page, "path", 0, 0.0), _at(page, "numbers", 0, 0.0)
    page.goto(url + "?capture=1")
    page.wait_for_function("window.__showntell?.duration?.() > 0")
    for at in (dot, count):  # MP4 export reuses a frame only while its key repeats
        keys = [page.evaluate("(t) => window.__showntell.seek(t)", at + d) for d in (0.2, 0.5, 0.8)]
        assert len(set(keys)) == 3, keys
    page.close()


def test_a_drawing_past_its_viewbox_is_reported(daemon):
    call = _rpc(daemon)
    r = call("start", title="Spill", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    md = (
        '# Spill\n\nOpening words.\n\n## Off the edge {#edge}\n\n```svg\n<svg viewBox="0 0 1150 560">'
        '<circle id="a" cx="575" cy="280" r="100" fill="#2f7de1"/>'
        '<text id="b" x="575" y="700" font-size="30" fill="var(--fg)">below the slide</text></svg>\n```\n\nA [circle](#a).\n'
    )
    call("add", handle=r["handle"], markdown=md)
    call("playable", handle=r["handle"], timeout=10)
    f = call("finish", handle=r["handle"], questions=None)
    assert any(i["code"] == "overflow" and "viewBox" in i["message"] for i in f["player_issues"]), f["player_issues"]


LAYOUTS_DOC = """# Layouts {icon=layout-grid}
> Side, pair, full and statement

Opening.

## Takeaways beside code {#side .side}
> :search: Check first
> :download: Load on a miss

```python
def get(key):
    if key in cache:
        return cache[key]
    return load(key)
```

The [check](#L2) comes first.

## Code and its flow {#pair .pair}

```python
def get(key):
    return cache.get(key) or load(key)
```

```mermaid
flowchart TB
  req[Request] --> hit{In cache?}
  hit --> db[(Database)]
```

The [lookup](#L2) is one line; [the check](#hit) decides, and [the database](# "Database") answers a miss.

## Edge to edge {#full .full}

```svg
<svg viewBox="0 0 1150 560"><g id="box"><rect x="100" y="100" width="300" height="200" fill="var(--panel)" stroke="var(--c2)"/>
<use href="icon:database" x="200" y="140" width="96"/></g></svg>
```

The [box](#box) holds a database.

## Moved {#moved .full .morph}

```svg
<svg viewBox="0 0 1150 560"><g id="box"><rect x="600" y="200" width="400" height="260" fill="var(--panel)" stroke="var(--c2)"/>
<use href="icon:database" x="760" y="260" width="96"/></g></svg>
```

The [box](#box) moved.

## The point {#statement}
> A cache trades **memory** for time.

Said aloud.

## Parts {#cards .cards}

- :server: **App**: asks
- :zap: **Cache**: answers
- :database: **Store**: keeps

The [app](#app) asks.
"""


@pytest.mark.parametrize("frame", ["landscape", "portrait"])
def test_layouts_render_inside_the_slide(daemon, browser, frame):
    url = _make(daemon, frame, LAYOUTS_DOC, "Layouts")
    data = httpx.get(url + "data.json").json()
    ids = [s["id"] for s in data["slides"]]
    assert ids[1:] == ["side", "pair", "full", "moved", "statement", "cards"]
    assert data["slides"][2]["pair"]["kind"] == "diagram" and "database" in data["slides"][-1]["icons"]
    page = browser.new_page(viewport={"width": 1440, "height": 900} if frame != "portrait" else {"width": 900, "height": 1400})
    page.goto(url)
    page.wait_for_function("window.__showntell !== undefined")
    starts = page.evaluate(
        """() => import('./' + document.body.dataset.static + '/js/timeline.js').then((m) => fetch('data.json')
             .then((r) => r.json()).then((w) => { const tl = m.build(w); return w.slides.map((_, i) => [m.slideStart(tl, i), m.slideEnd(tl, i)]); }))"""
    )
    for i, sid in enumerate(ids):
        # settled, before the next slide starts coming in (SCENE_LEAD, 0.45 s, ahead of its first word)
        page.goto(f"{url}?at={min(starts[i][0] + 2.5, starts[i][1] - 0.6):.2f}")
        page.wait_for_timeout(1000 if sid in ("pair", "title") else 500)
        state = page.evaluate(
            """() => {
              const slide = document.querySelector('.slide:not(.leaving)');
              const stage = document.querySelector('#stage').getBoundingClientRect();
              const out = (e) => { const r = e.getBoundingClientRect(); return r.left < stage.left - 2 || r.right > stage.right + 2 || r.bottom > stage.bottom + 2; };
              const bodies = [...slide.querySelectorAll('.slide-body')];
              return {cls: slide.className, errors: window.__showntell.renderErrors, style: slide.getAttribute('style') || '',
                      bodies: bodies.map((b) => !!b.firstElementChild && b.firstElementChild.getBoundingClientRect().width > 50),
                      overflow: [...slide.querySelectorAll('.slide-body > *, .points, .statement, .slide-head')].some(out),
                      points: slide.querySelectorAll('.points .point').length, icons: slide.querySelectorAll('svg.ic').length,
                      say: slide.querySelector('.statement .say')?.textContent || '', kicker: slide.querySelector('.kicker .n')?.textContent || ''};
            }"""
        )
        page.screenshot(path=str(ART / f"layout-{frame}-{i:02d}-{sid}.png"), animations="disabled")
        assert not state["errors"], state["errors"]
        assert not state["overflow"], f"{frame} {sid} spills off the stage"
        assert state["style"] in ("", 'style=""') or "opacity" not in state["style"], f"{sid} is still coming in: {state['style']}"
        if sid == "side":
            assert "l-side" in state["cls"] and state["points"] == 2 and state["icons"] == 2 and state["kicker"] == "01"
        if sid == "pair":
            assert "l-pair" in state["cls"] and state["bodies"] == [True, True]
        if sid in ("full", "moved"):
            assert "l-full" in state["cls"] and state["bodies"] == [True]
        if sid == "statement":
            assert "k-text" in state["cls"] and state["say"] == "A cache trades memory for time."
        if sid == "cards":
            assert state["icons"] == 3
    # A moment into a slide it is still arriving, by the clock, so a paused frame shows it too.
    # It starts coming in SCENE_LEAD (0.45 s) before its first word.
    page.goto(f"{url}?at={starts[6][0] - 0.25:.2f}")
    page.wait_for_timeout(400)
    moving = page.evaluate("() => [...document.querySelectorAll('.slide:not(.leaving) .entering')].length")
    assert moving == 3
    # Quoted text that only the second pane has is found there.
    page.goto(f"{url}?at={_at(page, 'pair', 2, 0.3):.2f}")
    page.wait_for_timeout(1200)
    assert page.evaluate("() => !!document.querySelector('.slide:not(.leaving) .pane-2 .active')")
    page.goto(f"{url}?at={starts[6][0] - 0.25:.2f}")
    page.wait_for_timeout(400)
    # Landing there by a jump still shows the slide before on its way out, as playing through would.
    leaving = page.evaluate("() => [...document.querySelectorAll('.slide.leaving')].map((s) => s.className)")
    assert len(leaving) == 1 and "k-text" in leaving[0], leaving
    page.close()


CHARTS_DOC = """# Charts

Opening words.

## Bars {#bars}

```chart bar unit="ms"
series: before, after
read: 420, 180
write: 610, 390
```

The bars grow from the axis while this sentence plays out over a few seconds of narration.

## Line {#line}

```chart line
x: 2021, 2022, 2023, 2024
users: 10, 20, 45, 80
```

The line draws itself from left to right while this sentence plays for a few seconds.

## Picture {#picture}

![A settings page](tests/js/golden/files/screen.png)

The picture pushes in slowly while this sentence plays for a few seconds of narration.
"""


def test_charts_and_pictures_move_by_the_clock(daemon, browser):
    url = _make(daemon, "landscape", CHARTS_DOC, "Charts")
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(url + "?capture=1")
    page.wait_for_function("window.__showntell !== undefined")
    starts = page.evaluate(
        """() => import('./' + document.body.dataset.static + '/js/timeline.js').then((m) => fetch('data.json')
             .then((r) => r.json()).then((w) => { const tl = m.build(w); return w.slides.map((_, i) => [m.slideStart(tl, i), m.slideEnd(tl, i)]); }))"""
    )

    def look(i, dt):
        page.evaluate("(t) => window.__showntell.seek(t)", min(starts[i][0] + dt, starts[i][1] - 0.1))
        return page.evaluate(
            """() => { const s = document.querySelector('.slide:not(.leaving) .chart svg');
              return {bars: [...s.querySelectorAll('g.bar rect')].map((r) => r.style.transform),
                      dash: s.querySelector('.line')?.style.strokeDashoffset || ''}; }"""
        )

    early = look(1, 0.6)
    page.screenshot(path=str(ART / "chart-growing.png"))
    late = look(1, 3.0)
    assert all(t.startswith("scaleY(") for t in early["bars"]) and len(set(early["bars"])) > 1, early
    assert late["bars"] == ["", "", "", ""], late
    early, late = look(2, 0.7), look(2, 3.0)
    assert float(early["dash"]) > 0 and late["dash"] == "", (early, late)
    # The picture pushes in by the clock, clipped to its own box.
    scales = []
    for dt in (0.2, 3.0):
        page.evaluate("(t) => window.__showntell.seek(t)", min(starts[3][0] + dt, starts[3][1] - 0.1))
        scales.append(page.evaluate("() => document.querySelector('.slide:not(.leaving) .kb img').style.transform"))
    a, b = (float(s[6:-1]) for s in scales)
    assert 1 <= a < b <= 1.06, scales
    page.close()


def test_the_longest_wait_is_measured(daemon, browser):
    # The viewer plays the opening, then waits for a slide the writer has not sent yet: the
    # evaluation reads that single wait, which the total hides among short ones.
    call = _rpc(daemon)
    r = call("start", title="Waiting", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    call("add", handle=r["handle"], markdown="# Waiting\n\nA short opening.\n")
    call("playable", handle=r["handle"], timeout=10)
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(r["url"].replace("localhost", "127.0.0.1") + "/")
    page.wait_for_function("window.__showntell !== undefined && !document.querySelector('#big-play').disabled")
    page.click("#big-play")
    page.wait_for_selector("#waiting:not([hidden])", timeout=20000)
    page.wait_for_timeout(2000)
    call("add", handle=r["handle"], markdown="## Next {#next}\n\nThe slide that was waited for.\n")
    page.wait_for_function("window.__showntell.longestStall > 0", timeout=20000)
    m = page.evaluate("window.__showntell")
    assert 1.8 <= m["longestStall"] <= m["stallSeconds"] + 0.01
    page.close()
