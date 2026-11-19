"""A plot in the player: curves drawn from the builder's samples with gaps where a function is
undefined, a point written as (a, f(a)) gliding along its curve between versions, and cues
pointing at functions and points."""

from __future__ import annotations

import os

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.slow]
pytest.importorskip("playwright.sync_api")

from test_player_browser import ROOT, _at, _rpc, browser, daemon  # noqa: E402,F401  (fixtures)

MD = """# Plots

Descent on a curve.

## Gradient descent {#gd}

```plot step x=-3..3 title="Loss"
f(x) = x^2  "loss"
point p = (2.5, f(2.5))  "start"
```
```plot step x=-3..3 title="Loss"
f(x) = x^2  "loss"
point p = (0.5, f(0.5))  "after a step"
t(x) = 1/x dashed "spike"
```

The [loss](#f) is a bowl. One [step](#step-2) moves [the point](#p) down it.
"""


def test_plot_draws_glides_along_the_curve_and_points(daemon, browser):  # noqa: F811
    call = _rpc(daemon)
    r = call("start", title="plot", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    a = call("add", handle=r["handle"], markdown=MD)
    assert not [i for i in a["issues"] if i["severity"] == "error"], a["issues"]
    call("playable", handle=r["handle"], timeout=10)
    url = r["url"].replace("localhost", "127.0.0.1") + "/"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_function("window.__showntell !== undefined")
    t_step = _at(page, "gd", 1, 0)

    def state(t: float) -> dict:
        page.goto(f"{url}?at={t:.2f}")
        page.wait_for_selector(".plot svg g.entry")
        page.wait_for_timeout(400)
        return page.evaluate("""() => {
          const svg = document.querySelector('.plot svg');
          const pt = svg.querySelector('g.entry.point circle');
          const curve = svg.querySelector('g.entry.fn[data-id="f"] path');
          const at = (x) => { const len = curve.getTotalLength(); let best = 1e9;
            for (let s = 0; s <= len; s += 2) { const q = curve.getPointAtLength(s); best = Math.min(best, Math.hypot(q.x - x[0], q.y - x[1])); }
            return best; };
          const c = [+pt.getAttribute('cx'), +pt.getAttribute('cy')];
          const spike = svg.querySelector('g.entry.fn[data-id="t"] path');
          const box = (e) => e.getBoundingClientRect().toJSON(), ptr = document.querySelector('#pointer');
          return { point: c, off: at(c), dot: box(pt), pointer: ptr.hidden ? null : box(ptr), moves: (spike?.getAttribute('d').match(/M/g) || []).length,
                   active: [...svg.querySelectorAll('g.entry.active')].map((g) => g.dataset.id),
                   label: document.querySelector('.plot').getAttribute('aria-label') }; }""")

    before = state(t_step - 0.3)
    mid = state(t_step + 0.35)
    after = state(t_step + 2.0)
    # The point glides along the curve, not across it.
    assert before["point"][0] > mid["point"][0] > after["point"][0]
    assert mid["off"] < 3 and after["off"] < 3, (mid, after)
    assert after["moves"] >= 2  # 1/x breaks at 0 instead of joining -inf to +inf
    assert "p" in after["active"]
    d, ptr = after["dot"], after["pointer"]  # the pointer followed the point to where it landed
    assert (
        ptr
        and ptr["left"] <= d["left"] + 2
        and ptr["right"] >= d["right"] - 2
        and ptr["top"] <= d["top"] + 2
        and ptr["bottom"] >= d["bottom"] - 2
    )
    assert "f(x) = x^2" in after["label"]
    if os.environ.get("SHOWNTELL_SHOTS"):
        page.goto(url + "?capture=1")
        page.wait_for_function("window.__showntell?.duration?.() > 0")
        page.evaluate("(t) => window.__showntell.seek(t)", t_step + 2.0)
        page.wait_for_timeout(500)
        page.screenshot(path=os.path.join(os.environ["SHOWNTELL_SHOTS"], "plot.png"))
    assert not errors, errors
