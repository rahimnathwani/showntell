"""How visuals read once drawn: a chart beside takeaways keeps its text size, parts the
narration has moved past stay readable, and a diagram shrunk too far is told how to fix it."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.slow]
pytest.importorskip("playwright.sync_api")

from test_player_browser import ROOT, _at, _rpc, browser, daemon  # noqa: E402,F401  (fixtures)

MD = """# Visuals

Opening words.

## Lines added {#bars .side}
> :file-plus: Two new modules carry most of it

```chart hbar
title: Lines added per file
export.py: 172
doctor.py: 89
mlx_backend.py: 4
```

Most lines are in [export.py](#export-py), and [the backend](#mlx-backend-py) barely moved.

## Three things {#cards .cards}

- **First**: the first point
- **Second**: the second point
- **Third**: the third point

Start with [the first](#first), then [the second](#second).
"""


def _open(daemon, browser, md):  # noqa: F811
    call = _rpc(daemon)
    r = call("start", title="Visuals", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    call("add", handle=r["handle"], markdown=md)
    call("playable", handle=r["handle"], timeout=10)
    url = r["url"].replace("localhost", "127.0.0.1") + "/"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(url)
    page.wait_for_function("window.__showntell !== undefined")
    return call, r, url, page


def test_a_chart_beside_takeaways_keeps_its_text_size_and_dims_only_bars(daemon, browser):  # noqa: F811
    _, _, url, page = _open(daemon, browser, MD)
    t = _at(page, "bars", 0, 1)  # the second cue: the backend's bar
    page.goto(f"{url}?at={t:.2f}")
    page.wait_for_selector(".chart svg g.entry.active")
    page.wait_for_timeout(500)
    s = page.evaluate("""() => {
      const k = parseFloat(document.querySelector('#stage').dataset.scale || '1');
      const g = [...document.querySelectorAll('.chart g.entry.bar')].find((e) => !e.classList.contains('active'));
      const op = (e) => { let o = 1; for (let x = e; x && x.tagName !== 'svg'; x = x.parentNode) o *= parseFloat(getComputedStyle(x).opacity); return o; };
      const tk = g.querySelector('.tick');
      return { tick: parseFloat(getComputedStyle(tk).fontSize) * tk.getScreenCTM().a / k, bar: op(g.querySelector('rect')),
               label: op(g.querySelector('.tick')), value: op(g.querySelector('.vlabel')) }; }""")
    assert s["tick"] >= 20, s  # was about 15 px when the drawing was made for the whole slide
    assert 0.3 <= s["bar"] <= 0.4 and s["label"] == 1 and s["value"] >= 0.65, s
    page.close()


def test_cards_passed_stay_readable(daemon, browser):  # noqa: F811
    _, _, url, page = _open(daemon, browser, MD)
    t = _at(page, "cards", 0, 1)
    page.goto(f"{url}?at={t:.2f}")
    page.wait_for_selector(".list.cards .entry.active")
    page.wait_for_timeout(500)
    o = page.evaluate("getComputedStyle(document.querySelector('.list.cards .entry:not(.active)')).opacity")
    assert float(o) >= 0.6, o
    page.close()


def test_diagram_advice_names_a_direction_only_for_a_flowchart_running_the_wrong_way(daemon):  # noqa: F811
    call = _rpc(daemon)
    r = call("start", title="Small", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    tall = " --> ".join(f"n{i}[Step number {i} here]" for i in range(14))
    msgs = "\n".join(f"  A->>B: message number {i} goes across" for i in range(40))
    md = (
        f"# Small\n\nOpening words.\n\n## Tall {{#tall}}\n\n```mermaid\nflowchart TB\n  {tall}\n```\n\nThe [first](#n0).\n\n"
        f"## Sequence {{#seq}}\n\n```mermaid\nsequenceDiagram\n{msgs}\n```\n\nA long exchange.\n"
    )
    call("add", handle=r["handle"], markdown=md)
    call("playable", handle=r["handle"], timeout=10)
    f = call("finish", handle=r["handle"], questions=None)
    small = {i["slide"]: i["message"] for i in f["player_issues"] if i["code"] == "text_too_small"}
    assert "left to right (flowchart LR)" in small.get("tall", ""), small
    assert "seq" in small and "flowchart" not in small["seq"], small
