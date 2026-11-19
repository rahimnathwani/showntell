"""A memory picture in the player: variables, objects and arrows drawn from the builder's layout,
objects holding still between `memory step` versions while arrows change, markers under array
cells, and cues on variables, objects and fields."""

from __future__ import annotations

import os

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.slow]
pytest.importorskip("playwright.sync_api")

from test_player_browser import ROOT, _at, _rpc, browser, daemon  # noqa: E402,F401  (fixtures)

MD = """# Memory

Reversing a list.

## Reverse in place {#rev}

```memory step
head -> n1
prev -> null
n1: Node {val: 1, next -> n2}
n2: Node {val: 2, next -> n3}
n3: Node {val: 3, next -> null}
```
```memory step
head -> n3
prev -> n2
n1: Node {val: 1, next -> null}
n2: Node {val: 2, next -> n1}
n3: Node {val: 3, next -> n2}
```

The list starts at [the head](#head), and [the first node](#n1) points on to the second.

After [the reversal](#step-2), [the third node's next](#n3.next) points back, and nothing moved.

## Binary search {#bs}

```memory
lo -> arr.0
mid -> arr[3]
hi -> arr.6
target = 9
arr: [1, 3, 4, 7, 9, 11, 15]
```

The [middle](#mid) is seven, so [lo](#lo) moves past it.
"""


def test_memory_draws_keeps_objects_still_and_points(daemon, browser):  # noqa: F811
    call = _rpc(daemon)
    r = call("start", title="memory", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
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
        page.wait_for_selector(".memory svg [data-id]")
        page.wait_for_timeout(300)
        return page.evaluate("""() => {
          const svg = document.querySelector('.memory svg'), box = (e) => e.getBoundingClientRect().toJSON();
          const pos = {}; for (const g of svg.querySelectorAll('g.obj')) pos[g.dataset.id] = box(g.querySelector('.obox'));
          const ptr = document.querySelector('#pointer');
          return { pos, arrows: [...svg.querySelectorAll('g.arrow')].filter((g) => !g.style.opacity || +g.style.opacity > 0.5).map((g) => g.dataset.key).sort(),
                   active: [...svg.querySelectorAll('[data-id].active')].map((g) => g.dataset.id),
                   markers: [...svg.querySelectorAll('g.marker')].map((g) => g.dataset.key),
                   changed: [...svg.querySelectorAll('.vbox.changed')].length,
                   pointer: ptr.hidden ? null : box(ptr) }; }""")

    one = state(_at(page, "rev", 1, 0.3))
    assert one["arrows"] == ["head->n1", "n1.next->n2", "n2.next->n3"] and "n1" in one["active"]
    two = state(_at(page, "rev", 3, 1.2))
    assert two["arrows"] == ["head->n3", "n2.next->n1", "n3.next->n2", "prev->n2"], two["arrows"]
    for k in ("n1", "n2", "n3"):  # nothing moved
        assert abs(one["pos"][k]["left"] - two["pos"][k]["left"]) < 1 and abs(one["pos"][k]["top"] - two["pos"][k]["top"]) < 1
    assert "n3.next" in two["active"] and two["changed"] >= 3
    bs = state(_at(page, "bs", 1, 0.3))
    assert sorted(bs["markers"]) == ["hi", "lo", "mid"] and "lo" in bs["active"]
    if os.environ.get("SHOWNTELL_SHOTS"):
        page.goto(url + "?capture=1")
        page.wait_for_function("window.__showntell?.duration?.() > 0")
        for name, t in (("memory-rev", _at(page, "rev", 3, 1.2)), ("memory-bs", _at(page, "bs", 1, 0.3))):
            page.evaluate("(t) => window.__showntell.seek(t)", t)
            page.wait_for_timeout(500)
            page.screenshot(path=os.path.join(os.environ["SHOWNTELL_SHOTS"], f"{name}.png"))
    assert not errors, errors


GROW = """# Grow

Appending.

## Append {#app}

```memory step
head -> n1
n1: Node {val: 1, next -> null}
```
```memory step
head -> n1
n1: Node {val: 1, next -> n2}
n2: Node {val: 2, next -> null}
```

We [append](#step-2) a node to the end of the list, and then [the new node](#n2) holds the value two, which is where the list now ends.

## Curve {#cv}

```plot x=-3..3
f(x) = 1/x  "spike"
```

The [curve](#f) runs off the chart.
"""


def test_a_direct_seek_lands_on_the_picture_playing_through_gives(daemon, browser):  # noqa: F811
    call = _rpc(daemon)
    r = call("start", title="grow", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    a = call("add", handle=r["handle"], markdown=GROW)
    assert not [i for i in a["issues"] if i["severity"] == "error"], a["issues"]
    call("playable", handle=r["handle"], timeout=10)
    url = r["url"].replace("localhost", "127.0.0.1") + "/"
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    page.goto(url)
    page.wait_for_function("window.__showntell !== undefined")
    t = _at(page, "app", 1, 0.4)
    look = """() => { const p = document.querySelector('#pointer'), s = document.querySelector('#stage').getBoundingClientRect();
      return { active: [...document.querySelectorAll('[data-id].active')].map((g) => g.dataset.id),
               pointer: p.hidden ? null : p.getBoundingClientRect().toJSON(), stage: s.toJSON() }; }"""
    for how in ("at", "seek"):  # a link to the moment, and a seek under capture
        if how == "at":
            page.goto(f"{url}?at={t:.2f}")
        else:
            page.goto(url + "?capture=1")
            page.wait_for_function("window.__showntell?.duration?.() > 0")
            page.evaluate("(t) => window.__showntell.seek(t)", t)
        page.wait_for_selector(".memory svg [data-id='n2']")
        page.wait_for_timeout(500)
        st = page.evaluate(look)
        assert st["active"] == ["n2"] and st["pointer"], (how, st)
    page.goto(f"{url}?at={_at(page, 'cv', 0, 0.6):.2f}")
    page.wait_for_selector(".plot svg g.entry")
    page.wait_for_timeout(500)
    st = page.evaluate(look)
    p, s = st["pointer"], st["stage"]
    assert p and p["top"] >= s["top"] - 2 and p["bottom"] <= s["bottom"] + 2, st  # the box stays on the plot
