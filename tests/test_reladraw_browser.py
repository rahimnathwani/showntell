"""A reladraw diagram in the player: drawn by the vendored library with the network blocked, in
the slide's colors, the pointer on its nodes, parts revealed in order; its findings and syntax
errors come back from finish with the block's line."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.slow]
pytest.importorskip("playwright.sync_api")

from test_player_browser import ROOT, _at, _rpc, browser, daemon  # noqa: E402,F401  (fixtures)

MD = """# reladraw

A diagram placed by relations.

## The system {#arch .build}

```reladraw
node web "Web app"  layout: column
node web.ui "Interface"
node web.api "API"
node store "Database"  right of web (gap: wide)  shape: cylinder
edge web.api -> store "queries"
node docs "Docs"  below store  url: "https://example.com/docs"
```

The [web app](#web) calls [the database](#store).

Then [the docs](#docs).
"""


def test_reladraw_renders_offline_in_theme_points_and_reveals(daemon, browser):  # noqa: F811
    call = _rpc(daemon)
    r = call("start", title="reladraw", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    a = call("add", handle=r["handle"], markdown=MD)
    assert not [i for i in a["issues"] if i["severity"] == "error"], a["issues"]
    call("playable", handle=r["handle"], timeout=10)
    url = r["url"].replace("localhost", "127.0.0.1") + "/"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    outside, errors = [], []
    page.route(
        "**/*",
        lambda route: (outside.append(route.request.url), route.abort()) if "127.0.0.1" not in route.request.url else route.continue_(),
    )
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_function("window.__showntell !== undefined")
    t = _at(page, "arch", 0, 0.3)
    page.goto(f"{url}?at={t:.2f}")
    page.wait_for_selector(".reladraw svg g[data-node]")
    page.wait_for_timeout(800)
    state = page.evaluate("""() => {
      const svg = document.querySelector('.reladraw svg'), box = (e) => e.getBoundingClientRect().toJSON();
      const store = svg.querySelector('g[data-node="store"]'), p = document.querySelector('#pointer');
      const css = getComputedStyle(document.body);
      return { web: box(svg.querySelector('g[data-node="web"]')), pointer: p.hidden ? null : box(p),
               veiled: [...svg.querySelectorAll('.veiled')].map((g) => g.dataset.node || g.dataset.edge),
               links: svg.querySelectorAll('a, [href^="http"]').length, page: svg.querySelector(':scope > rect[fill]')?.getAttribute('width'),
               font: svg.getAttribute('font-family'), sans: css.getPropertyValue('--sans').trim(),
               fill: store.querySelector('path, rect, ellipse').getAttribute('fill'),
               svg: box(svg), body: box(document.querySelector('.slide-body')) }; }""")
    w, p = state["web"], state["pointer"]
    assert p and p["left"] <= w["left"] + 2 and p["right"] >= w["right"] - 2 and p["top"] <= w["top"] + 2 and p["bottom"] >= w["bottom"] - 2
    assert "store" in state["veiled"] and "docs" in state["veiled"] and "web" not in state["veiled"]  # the line waits for its far end too
    assert state["links"] == 0 and state["page"] is None  # url: is dropped; the slide shows through
    assert state["font"].replace("'", '"') == state["sans"] and state["fill"].startswith("#")
    assert state["svg"]["right"] <= state["body"]["right"] + 1 and state["svg"]["bottom"] <= state["body"]["bottom"] + 1
    page.goto(url + "?capture=1")
    page.wait_for_function("window.__showntell?.duration?.() > 0")
    page.evaluate("(t) => window.__showntell.seek(t)", page.evaluate("window.__showntell.duration()") - 0.1)
    assert page.evaluate("document.querySelectorAll('.reladraw .veiled').length") == 0
    assert not outside, outside
    assert not errors, errors
    page.close()


def test_reladraw_findings_and_errors_come_back_with_the_line(daemon):  # noqa: F811
    call = _rpc(daemon)
    r = call("start", title="Findings", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    md = """# Findings

Opening words.

## Crossed {#crossed}

```reladraw
node a "Left"
node mid "In the way"  right of a
node b "Right"  right of mid
edge a -> b
```

A line runs through a box.

## Broken {#broken}

```reladraw
node a "A"
node b "B"  rite of a
```

This one does not parse.
"""
    call("add", handle=r["handle"], markdown=md)
    call("playable", handle=r["handle"], timeout=10)
    f = call("finish", handle=r["handle"], questions=None)
    crossed = [i["message"] for i in f["player_issues"] if i["code"] == "drawing_overlap" and i["slide"] == "crossed"]
    assert crossed and "line 4 of the block" in crossed[0], f["player_issues"]
    broken = [i["message"] for i in f["player_issues"] if i["code"] == "render_error" and i["slide"] == "broken"]
    assert broken and "line 2 of the block" in broken[0], f["player_issues"]
