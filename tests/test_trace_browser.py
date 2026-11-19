"""A traced code slide in the player: the panel shows each moment's variables as #trace cues
fire, marks what changed and the line that just ran, and plays through moments a cue skips."""

from __future__ import annotations

import os

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.slow]
pytest.importorskip("playwright.sync_api")

from test_player_browser import ROOT, _at, _rpc, browser, daemon  # noqa: E402,F401  (fixtures)

MD = """# Trace

Summing a list.

## The loop {#loop}

```python
def total(xs):
    t = 0
    for x in xs:
        t += x
    return t
```
```trace
L1  xs = [3, 5]
L2  t = 0
L3  x = 3
L4  t = 3
L3  x = 5
L4  t = 8
L5  return: 8
```

It starts [at zero](#trace-2), before the loop has looked at a single number in the list it was given.

Then, once both numbers have been added in turn, [t becomes 8](#trace-6 "t = 8"), which is the whole sum of the list.

Finally [it returns](#trace-7) that sum to whoever called it, and the function is done.
"""


def test_trace_panel_follows_the_moments(daemon, browser):  # noqa: F811
    call = _rpc(daemon)
    r = call("start", title="trace", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    a = call("add", handle=r["handle"], markdown=MD)
    assert not [i for i in a["issues"] if i["severity"] == "error"], a["issues"]
    call("playable", handle=r["handle"], timeout=10)
    url = r["url"].replace("localhost", "127.0.0.1") + "/"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_function("window.__showntell !== undefined")

    def state(t: float) -> dict:
        page.goto(f"{url}?at={t:.2f}")
        page.wait_for_selector(".trace-panel")
        page.wait_for_timeout(300)
        return page.evaluate("""() => ({
          vars: [...document.querySelectorAll('.trace-panel .tp-row')].map((r) => [r.querySelector('.tp-name').textContent, r.querySelector('.tp-val').textContent, r.classList.contains('changed')]),
          ret: document.querySelector('.trace-panel .tp-ret .tp-val')?.textContent || null,
          run: [...document.querySelectorAll('.codebox .ln.run')].map((l) => +l.dataset.n),
          side: document.querySelector('.trace-panel').getBoundingClientRect().left > document.querySelector('.codebox').getBoundingClientRect().right }) """)

    first = state(_at(page, "loop", 0, 0.5))  # moment 1 plays for 0.3 s on the way to 2
    assert first["vars"] == [["xs", "[3, 5]", False], ["t", "0", True]] and first["run"] == [2] and first["side"]
    skipping = state(_at(page, "loop", 1, 0.45))  # 2 -> 6 plays 3, 4, 5 on the way
    assert skipping["run"] in ([3], [4]) and skipping["vars"][1][1] in ("0", "3"), skipping
    landed = state(_at(page, "loop", 1, 2.0))
    assert landed["vars"] == [["xs", "[3, 5]", False], ["t", "8", True], ["x", "5", False]] and landed["run"] == [4]
    done = state(_at(page, "loop", 2, 0.5))
    assert done["ret"] == "8" and done["run"] == [5]
    if os.environ.get("SHOWNTELL_SHOTS"):
        page.goto(url + "?capture=1")
        page.wait_for_function("window.__showntell?.duration?.() > 0")
        page.evaluate("(t) => window.__showntell.seek(t)", _at(page, "loop", 1, 2.0))
        page.wait_for_timeout(500)
        page.screenshot(path=os.path.join(os.environ["SHOWNTELL_SHOTS"], "trace.png"))
    assert not errors, errors
