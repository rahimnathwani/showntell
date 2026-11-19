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


ROUTES = {
    "tree": (
        "memory TB",
        "root -> r\nr: {v: 5, left -> a, right -> b, parent -> null}\na: {v: 3, left -> c, right -> d, parent -> r}\nb: {v: 8, left -> null, right -> null, parent -> r}\nc: {v: 1, left -> null, right -> null, parent -> a}\nd: {v: 4, left -> null, right -> null, parent -> a}",
    ),
    "under": ("memory", "top -> r\nr: {left -> a, right -> b}\na: {next -> c}\nb: {v: 1}\nc: {back -> b}"),
    "buckets": (
        "memory",
        'owner -> buckets\nbuckets: [-> a, -> null, -> b, -> c]\na: Entry {k: "x", next -> null}\nb: Entry {k: "y", next -> null}\nc: Entry {k: "z", next -> a}',
    ),
    "reversed": (
        "memory",
        "head -> n6\nn1: Node {v: 1, next -> null}\nn2: Node {v: 2, next -> n1}\nn3: Node {v: 3, next -> n2}\nn4: Node {v: 4, next -> n3}\nn5: Node {v: 5, next -> n4}\nn6: Node {v: 6, next -> n5}",
    ),
    "friends": ("memory", 'alice -> a\nbob -> b\na: Person {name: "Alice", age: 30}\nb: Person {name: "Bob", friend -> a}'),
}


def test_arrows_cross_no_box_and_stay_in_the_picture(daemon, browser):  # noqa: F811
    md = "# Routes\n\nArrows.\n\n" + "".join(
        f"## {k} {{#{k}}}\n\n```{fence}\n{body}\n```\n\nThe [picture](#) has arrows.\n\n" for k, (fence, body) in ROUTES.items()
    )
    call = _rpc(daemon)
    r = call("start", title="routes", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    a = call("add", handle=r["handle"], markdown=md)
    assert not [i for i in a["issues"] if i["severity"] == "error"], a["issues"]
    call("playable", handle=r["handle"], timeout=10)
    url = r["url"].replace("localhost", "127.0.0.1") + "/"
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    page.goto(url)
    page.wait_for_function("window.__showntell !== undefined")
    for k in ROUTES:
        page.goto(f"{url}?at={_at(page, k, 0, 0.2):.2f}")
        page.wait_for_selector(".memory svg g.arrow", state="attached")
        page.wait_for_timeout(300)
        bad = page.evaluate("""() => {
          const svg = document.querySelector('.memory svg'), out = [];
          const pic = svg.getBoundingClientRect();
          const boxes = [...svg.querySelectorAll('g.obj')].map((g) => ({ id: g.dataset.id, r: g.querySelector('.obox').getBoundingClientRect() }))
            .concat([...svg.querySelectorAll('g.var')].map((g) => ({ id: g.dataset.id, r: g.querySelector('.vbox').getBoundingClientRect() })));
          for (const g of svg.querySelectorAll('g.arrow')) {
            const p = g.querySelector('path'), len = p.getTotalLength(), m = p.getScreenCTM();
            const [from] = g.dataset.key.split('->');
            const ends = new Set([g.dataset.to, from]);
            const own = svg.querySelector(`g.slot[data-id="${from}"] .vbox`)?.getBoundingClientRect();
            const holder = from.includes('.') ? from.split('.')[0] : null;
            for (let s = 0; s <= len; s += 3) {
              const q = new DOMPoint(p.getPointAtLength(s).x, p.getPointAtLength(s).y).matrixTransform(m);
              if (q.x < pic.left - 1 || q.x > pic.right + 1 || q.y < pic.top - 1 || q.y > pic.bottom + 1) { out.push(`${g.dataset.key} leaves the picture`); break; }
              const inCell = own && q.x >= own.left - 1 && q.x <= own.right + 1 && q.y >= own.top - 1 && q.y <= own.bottom + 1;
              const hit = boxes.find((b) => !ends.has(b.id) && !(b.id === holder && inCell) && q.x > b.r.left + 3 && q.x < b.r.right - 3 && q.y > b.r.top + 3 && q.y < b.r.bottom - 3);
              if (hit) { out.push(`${g.dataset.key} crosses ${hit.id}`); break; }
            }
          }
          return out; }""")
        assert not bad, (k, bad)


ROUTES.update(
    {
        "cells": ("memory", "xs: [-> n1, 1, _, 1]\nn1: Node {v: 1, next -> null}"),
        "tbcur": (
            "memory TB",
            "cur -> b\nroot -> r\nr: {v: 5, left -> a, right -> b, parent -> null}\na: {v: 3, left -> null, right -> null, parent -> r}\nb: {v: 8, left -> null, right -> null, parent -> r}",
        ),
        "rev3": ("memory", "prev -> c\ncur -> null\na: Node {v: 1, next -> null}\nb: Node {v: 2, next -> a}\nc: Node {v: 3, next -> b}"),
    }
)
