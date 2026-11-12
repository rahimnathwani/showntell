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
               fill: store.querySelector('path, rect, ellipse').getAttribute('fill'), node: css.getPropertyValue('--node').trim().toLowerCase(),
               svg: box(svg), body: box(document.querySelector('.slide-body')) }; }""")
    w, p = state["web"], state["pointer"]
    assert p and p["left"] <= w["left"] + 2 and p["right"] >= w["right"] - 2 and p["top"] <= w["top"] + 2 and p["bottom"] >= w["bottom"] - 2
    assert "store" in state["veiled"] and "docs" in state["veiled"] and "web" not in state["veiled"]
    assert "0" in state["veiled"]  # the line web.api -> store waits for its far end
    assert state["links"] == 0 and state["page"] is None  # url: is dropped; the slide shows through
    assert state["font"].replace("'", '"') == state["sans"] and state["fill"] == state["node"]  # the slide's theme, not the library's
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


HOSTILE = """<svg xmlns="http://www.w3.org/2000/svg" xmlns:x="http://www.w3.org/1999/xlink" viewBox="0 0 100 100">
<rect width="100" height="100" fill="#123456"/>
<meta xmlns="http://www.w3.org/1999/xhtml" http-equiv="refresh" content="0;url=https://evil.example/navigated"/>
<img xmlns="http://www.w3.org/1999/xhtml" src="https://evil.example/img"/>
<video xmlns="http://www.w3.org/1999/xhtml" poster="https://evil.example/poster"/>
<audio xmlns="http://www.w3.org/1999/xhtml" src="https://evil.example/a.mp3" autoplay="autoplay"/>
<style>rect { fill: url(https://evil.example/style) }</style>
<filter id="f"><feImage x:href="https://evil.example/feimage"/></filter>
<a href="https://evil.example/link"><g data-node="kept"><rect x="1" y="1" width="9" height="9" fill="url(#g)" onclick="window.pwned=1"/>
<text x="1" y="20" style="cursor:\\75rl(https://evil.example/esc)">safe text</text></g></a>
<use x:href="https://evil.example/use.svg#a"/><use href="#g"/>
<rect mask="image-set('https://evil.example/set' 1x)" width="5" height="5" fill="url(https://evil.example/fill)"/>
<image href="https://evil.example/image"/><foreignObject><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject>
</svg>"""


def test_the_sanitiser_keeps_only_drawing(daemon, browser):  # noqa: F811
    """The allowlist rebuild, fed what the library could pass through: nothing that navigates,
    fetches or runs survives, and the drawing does."""
    call = _rpc(daemon)
    r = call("start", title="reladraw", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    call("add", handle=r["handle"], markdown=MD)
    call("playable", handle=r["handle"], timeout=10)
    url = r["url"].replace("localhost", "127.0.0.1") + "/"
    page = browser.new_page()
    outside = []
    page.route(
        "**/*",
        lambda route: (outside.append(route.request.url), route.abort()) if "127.0.0.1" not in route.request.url else route.continue_(),
    )
    page.goto(url)
    page.wait_for_function("window.__showntell !== undefined")
    out = page.evaluate(
        """async (src) => {
          const m = await import('./' + document.body.dataset.static + '/js/kinds/reladraw.js');
          const svg = m.clean(src);
          document.body.append(svg);
          await new Promise((r) => setTimeout(r, 1500));
          return { html: svg.outerHTML, names: [...svg.querySelectorAll('*')].map((e) => e.localName),
                   pwned: !!window.pwned, at: location.href, page: svg.dataset.page || null };
        }""",
        HOSTILE,
    )
    assert "evil" not in out["html"] and "\\75" not in out["html"] and "image-set" not in out["html"], out["html"]
    assert set(out["names"]) <= {"rect", "g", "text", "use"}, out["names"]  # filter is not drawn by the library
    assert 'fill="url(#g)"' in out["html"] and 'href="#g"' in out["html"] and "safe text" in out["html"]
    assert not out["pwned"] and out["at"].startswith(url) and out["page"] == "#123456"
    assert not outside, outside
    page.close()


def test_pasted_icons_are_refused_by_the_builder(daemon):  # noqa: F811
    call = _rpc(daemon)
    r = call("start", title="Icons", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    md = '# Icons\n\nOpening.\n\n## I {#i}\n\n```reladraw\nicon p """<svg viewBox="0 0 1 1"><path d="M0 0h1v1z"/></svg>"""\nnode a "A"  badge: p\n```\n\nA box.\n'
    a = call("add", handle=r["handle"], markdown=md)
    assert any(i["code"] == "block_error" and "pastes a picture" in i["message"] for i in a["issues"]), a["issues"]


@pytest.mark.parametrize("theme", ["light", "paper", "studio"])
def test_accent_text_reads_on_light_themes(daemon, browser, theme):  # noqa: F811
    call = _rpc(daemon)
    r = call("start", title="Accent", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT), theme=theme)
    md = '# Accent\n\nOpening.\n\n## Hot {#hot}\n\n```reladraw\nstyle hot  fill: theme-primary\nnode a "Hot box"  style: hot\n```\n\nA box.\n'
    call("add", handle=r["handle"], markdown=md)
    call("playable", handle=r["handle"], timeout=10)
    page = browser.new_page()
    page.goto(r["url"].replace("localhost", "127.0.0.1") + "/?from=hot")
    page.wait_for_selector('.reladraw g[data-node="a"] text')
    fill, text = page.evaluate("""() => { const g = document.querySelector('.reladraw g[data-node="a"]');
      return [g.querySelector('path, rect').getAttribute('fill'), g.querySelector('text').getAttribute('fill')]; }""")

    def lum(h):
        c = [int(h[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    hi, lo = sorted([lum(fill), lum(text)], reverse=True)
    assert (hi + 0.05) / (lo + 0.05) >= 4.5, (theme, fill, text)
    page.close()
