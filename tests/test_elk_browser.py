"""A flowchart marked ```mermaid elk: laid out by the vendored ELK layout with the network
blocked, its nodes pointed at and its edges followed by a path cue as with Mermaid's own layout."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.slow]
pytest.importorskip("playwright.sync_api")

from test_player_browser import ROOT, _at, _rpc, browser, daemon  # noqa: E402,F401  (fixtures)

MD = """# ELK

A flowchart laid out by ELK.

## The request {#flow}

```mermaid elk
flowchart LR
  web_1[Web] --> api[API]
  api --> db[(Database)]
  api --> cache[Cache]
```

The [web tier](#web_1) calls [the API](#api), and a request goes [all the way down](#web_1->api->db).
"""


def test_elk_draws_offline_and_cues_follow_its_edges(daemon, browser):  # noqa: F811
    call = _rpc(daemon)
    r = call("start", title="elk", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    a = call("add", handle=r["handle"], markdown=MD)
    assert not [i for i in a["issues"] if i["severity"] == "error"], a["issues"]
    call("playable", handle=r["handle"], timeout=10)
    url = r["url"].replace("localhost", "127.0.0.1") + "/"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    outside, errors, loaded = [], [], []
    page.route(
        "**/*",
        lambda route: (outside.append(route.request.url), route.abort()) if "127.0.0.1" not in route.request.url else route.continue_(),
    )
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda req: loaded.append(req.url))
    page.goto(url)
    page.wait_for_function("window.__showntell !== undefined")
    t = _at(page, "flow", 2, 0.6)
    page.goto(f"{url}?at={t:.2f}")
    page.wait_for_selector(".diagram[data-layout='elk'] svg g.node")
    page.wait_for_timeout(600)
    state = page.evaluate("""() => {
      const svg = document.querySelector('.diagram svg');
      return { ids: [...svg.querySelectorAll('path.flowchart-link')].map((p) => p.id),
               lit: [...svg.querySelectorAll('path.lit')].map((p) => p.id),
               dot: svg.querySelector('circle.packet')?.style.display }; }""")
    # ELK names edges L_a_b_0_0; the path cue still finds and lights both legs.
    assert any(i.endswith("_0") and i.count("_") >= 4 for i in state["ids"]), state["ids"]
    assert sorted(state["lit"]) == sorted(i for i in state["ids"] if i.startswith(("L_web_1_api", "L_api_db"))), state
    assert state["dot"] == ""
    assert any("vendor/elk/mermaid-elk.min.js" in u for u in loaded)
    assert not outside, outside
    assert not errors, errors


def test_a_plain_flowchart_does_not_load_elk(daemon, browser):  # noqa: F811
    call = _rpc(daemon)
    r = call("start", title="dagre", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    call("add", handle=r["handle"], markdown=MD.replace("```mermaid elk", "```mermaid"))
    call("playable", handle=r["handle"], timeout=10)
    url = r["url"].replace("localhost", "127.0.0.1") + "/"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    loaded = []
    page.on("request", lambda req: loaded.append(req.url))
    page.goto(f"{url}?at=999")
    page.wait_for_selector(".diagram svg g.node")
    assert page.evaluate("document.querySelector('.diagram').dataset.layout") is None
    assert not any("elk" in u for u in loaded)
