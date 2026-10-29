"""```svg: a drawing, written inline or read with `file=` (an export from draw.io, Excalidraw,
Inkscape and the like works too).

The drawing is rebuilt from an allowlist of SVG elements and attributes, so nothing in it can
run or fetch: no scripts, event handlers, external links, embedded HTML or wall-clock animation.
Elements with an `id` are what cues point at; the outermost ones are the parts a step reveal
shows in source order.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from .. import icons
from .base import Finding, Kind, slug

MAX_BYTES = 600_000
MAX_ELEMENTS = 3000
SVG_NS = "http://www.w3.org/2000/svg"
XLINK = "{http://www.w3.org/1999/xlink}"

ELEMENTS = {
    "svg",
    "g",
    "defs",
    "symbol",
    "use",
    "title",
    "desc",
    "switch",
    "path",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "image",
    "text",
    "tspan",
    "textPath",
    "linearGradient",
    "radialGradient",
    "stop",
    "pattern",
    "clipPath",
    "mask",
    "marker",
    "filter",
    "feGaussianBlur",
    "feOffset",
    "feFlood",
    "feComposite",
    "feMerge",
    "feMergeNode",
    "feBlend",
    "feColorMatrix",
    "feDropShadow",
    "feMorphology",
    "style",
}
UNWRAP = {"a"}  # links lose their target but keep their drawing
# Containers whose children are definitions, not drawing: never parts of a reveal.
DEFINITIONS = {
    "defs",
    "symbol",
    "linearGradient",
    "radialGradient",
    "pattern",
    "clipPath",
    "mask",
    "marker",
    "filter",
    "title",
    "desc",
    "style",
}

PRESENTATION = {
    "fill",
    "fill-opacity",
    "fill-rule",
    "stroke",
    "stroke-width",
    "stroke-opacity",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-dasharray",
    "stroke-dashoffset",
    "stroke-miterlimit",
    "opacity",
    "color",
    "display",
    "visibility",
    "font-family",
    "font-size",
    "font-weight",
    "font-style",
    "font-variant",
    "text-anchor",
    "dominant-baseline",
    "alignment-baseline",
    "baseline-shift",
    "letter-spacing",
    "word-spacing",
    "text-decoration",
    "writing-mode",
    "marker-start",
    "marker-mid",
    "marker-end",
    "clip-path",
    "clip-rule",
    "mask",
    "filter",
    "paint-order",
    "vector-effect",
    "shape-rendering",
    "text-rendering",
    "stop-color",
    "stop-opacity",
    "flood-color",
    "flood-opacity",
    "mix-blend-mode",
    "white-space",
    "transform-origin",
    "transform-box",
    "pointer-events",
    "direction",
    "unicode-bidi",
    "overflow",
}
GENERIC_FONTS = ("serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-sans-serif", "ui-monospace")
ATTRIBUTES = PRESENTATION | {
    "id",
    "class",
    "style",
    "transform",
    "viewBox",
    "preserveAspectRatio",
    "x",
    "y",
    "x1",
    "y1",
    "x2",
    "y2",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "fx",
    "fy",
    "fr",
    "width",
    "height",
    "d",
    "points",
    "pathLength",
    "offset",
    "gradientUnits",
    "gradientTransform",
    "spreadMethod",
    "patternUnits",
    "patternContentUnits",
    "patternTransform",
    "markerWidth",
    "markerHeight",
    "markerUnits",
    "refX",
    "refY",
    "orient",
    "clipPathUnits",
    "maskUnits",
    "maskContentUnits",
    "filterUnits",
    "primitiveUnits",
    "in",
    "in2",
    "result",
    "stdDeviation",
    "dx",
    "dy",
    "operator",
    "k1",
    "k2",
    "k3",
    "k4",
    "mode",
    "values",
    "type",
    "radius",
    "href",
    "startOffset",
    "method",
    "spacing",
    "textLength",
    "lengthAdjust",
    "rotate",
    "side",
    "role",
    "aria-label",
    "aria-hidden",
    "xml:space",
    "systemLanguage",
}
BACKGROUND_RE = re.compile(r"^(#[0-9a-fA-F]{3,8}|[a-z]{3,20}|var\(--[a-z0-9-]+\))$")
URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)
MAX_DEPTH = 100  # levels of nesting; real drawings use a handful
DATA_IMAGE = re.compile(r"^data:image/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=\s]+$")
DATA_FONT = re.compile(r"^data:(font/[\w.+-]+|application/(x-)?font-[\w.+-]+);base64,[A-Za-z0-9+/=\s]+$")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _safe_value(value: str) -> bool:
    """Attribute and style values may point only inside the drawing: url(#id)."""
    low = value.lower()
    if "javascript:" in low or "expression(" in low or "@import" in low or "<" in value:
        return False
    return all(m.group(2).startswith("#") for m in URL_RE.finditer(value))


def _with_fallback(families: str) -> str:
    """A font list that ends in a generic family, so a font the drawing named but did not embed
    (Excalidraw's Virgil, say) falls back to the player's sans-serif rather than the browser's serif."""
    if any(f.strip().strip("'\"").lower() in GENERIC_FONTS for f in families.split(",")):
        return families
    return f"{families}, sans-serif"


def _clean_style(style: str) -> str:
    out = []
    for decl in style.split(";"):
        name, _, value = decl.partition(":")
        name, value = name.strip().lower(), value.strip()
        if name in PRESENTATION and value and _safe_value(value):
            out.append(f"{name}: {_with_fallback(value) if name == 'font-family' else value}")
    return "; ".join(out)


def _clean_css(css: str) -> str:
    """A <style> element keeps only its rules whose URLs stay inside the drawing, and @font-face
    rules whose fonts are embedded (as Excalidraw exports carry them)."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    if re.search(r"@import|expression\(|javascript:|behavior\s*:|-moz-binding", css, re.I):
        return ""
    kept = []
    for rule in re.findall(r"[^{}]+\{[^{}]*\}", css):
        head = rule.split("{", 1)[0].strip().lower()
        urls = [m.group(2).strip() for m in URL_RE.finditer(rule)]
        if head.startswith("@font-face"):
            if urls and all(DATA_FONT.match(u) for u in urls):
                kept.append(rule.strip())
        elif not head.startswith("@") and all(u.startswith("#") for u in urls):
            kept.append(rule.strip())
    return "\n".join(kept)


XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


class _Cleaner:
    def __init__(self) -> None:
        self.removed: dict[str, int] = {}
        self.count = 0

    def drop(self, what: str) -> None:
        self.removed[what] = self.removed.get(what, 0) + 1

    def element(self, e: ET.Element, root: bool = False) -> list[str]:
        """The cleaned element as SVG text (a list of pieces), or only its tail when it is dropped."""
        name = _local(e.tag)
        tail = [escape(e.tail)] if e.tail else []
        if name in UNWRAP:
            self.drop(name)
            return [piece for c in e for piece in self.element(c)] + tail
        if name not in ELEMENTS:
            self.drop(name or "comment")
            return tail
        self.count += 1
        if name == "style":
            css = _clean_css("".join(e.itertext()))
            return ([f"<style>{escape(css)}</style>"] if css else []) + tail
        attrs = [f' xmlns="{SVG_NS}"'] if root else []
        for key, value in e.attrib.items():
            k = "href" if key == f"{XLINK}href" else "xml:space" if key == XML_SPACE else _local(key)
            if k not in ATTRIBUTES:
                # Editor bookkeeping (Inkscape, draw.io's embedded diagram, data-*) goes quietly.
                if not (key.startswith("{") or k.startswith("data-") or k in ("version", "baseProfile", "content")):
                    self.drop(f"{k}=")
                continue
            if k == "href":
                if not (value.startswith("#") or (name == "image" and DATA_IMAGE.match(value))):
                    self.drop("href=")
                    continue
            elif k == "style":
                value = _clean_style(value)
                if not value:
                    continue
            elif not _safe_value(value):
                self.drop(f"{k}=")
                continue
            if k == "font-family":
                value = _with_fallback(value)
            attrs.append(f" {k}={quoteattr(value)}")
        inner = [escape(e.text)] if e.text else []
        for c in e:
            inner += self.element(c)
        return [f"<{name}{''.join(attrs)}>", *inner, f"</{name}>", *tail]


def _number(v: str | None) -> float | None:
    m = re.match(r"^\s*([-+]?\d*\.?\d+)\s*(px)?\s*$", v or "")
    return float(m.group(1)) if m else None


def _name_of(e: ET.Element) -> str:
    """What a part is called: its <title>, else its own text."""
    for c in e:
        if _local(c.tag) == "title" and (c.text or "").strip():
            return " ".join(c.text.split())
    text = " ".join("".join(e.itertext()).split())
    return text if len(text) <= 60 else ""


def _depth(root: ET.Element) -> int:
    deepest, stack = 0, [(root, 1)]
    while stack:
        e, d = stack.pop()
        deepest = max(deepest, d)
        stack.extend((c, d + 1) for c in e)
    return deepest


def parse_svg(body: str, attrs: dict) -> tuple[dict, list[str]]:
    """```svg [sketch] [step] [title="..."] [background="white"]: an <svg> element with a viewBox (or
    a width and height). Read with file=, it sits on white unless `background` says another colour
    or none. `sketch` redraws its shapes by hand; `step` blocks in a row are versions of one drawing."""
    text = body.strip()
    vis: dict = {"svg": "", "ids": [], "items": [], "part_of": {}, "box": [0, 0, 1, 1], "title": attrs.get("title", "")}
    # Drawings from other tools usually assume a white page; they get one unless they say otherwise.
    background = attrs.get("background", "white" if attrs.get("file") else "")
    if background and background != "none" and BACKGROUND_RE.match(background):
        vis["background"] = background
    if "sketch" in attrs.get("flags", []):
        vis["sketch"] = True  # redrawn by hand in the player, as on a whiteboard
    if len(text.encode()) > MAX_BYTES:
        return vis, [f"the drawing is {len(text.encode()) // 1000} KB; the limit is {MAX_BYTES // 1000} KB"]
    if re.search(r"<!DOCTYPE|<!ENTITY", text, re.I):
        text = re.sub(r"<\?xml[^>]*\?>", "", text)
        if re.search(r"<!ENTITY", text, re.I):
            return vis, ["the drawing declares XML entities, which are not allowed"]
        text = re.sub(r"<!DOCTYPE[^>]*>", "", text, flags=re.I)
    if "xmlns=" not in text.split(">", 1)[0]:
        text = re.sub(r"^(\s*(?:<\?xml[^>]*\?>\s*)?<svg)\b", rf'\1 xmlns="{SVG_NS}"', text, count=1)
    if "xlink:" in text and "xmlns:xlink" not in text:
        text = re.sub(r"<svg\b", '<svg xmlns:xlink="http://www.w3.org/1999/xlink"', text, count=1)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        return vis, [f"the drawing is not well-formed SVG ({e})"]
    if _local(root.tag) != "svg":
        return vis, ["the block must hold one <svg> element"]
    if _depth(root) > MAX_DEPTH:  # the walks below recurse once per level
        return vis, [f"the drawing nests elements more than {MAX_DEPTH} deep"]
    box = [_number(x) for x in re.split(r"[\s,]+", root.get("viewBox", "").strip())] if root.get("viewBox") else []
    if len(box) != 4 or any(x is None for x in box):
        w, h = _number(root.get("width")), _number(root.get("height"))
        if not (w and h):
            return vis, ['the <svg> needs a viewBox, such as viewBox="0 0 1100 560"']
        box = [0.0, 0.0, w, h]
        root.set("viewBox", f"0 0 {w:g} {h:g}")
    for k in ("width", "height", "x", "y"):
        root.attrib.pop(k, None)
    root.set("preserveAspectRatio", "xMidYMid meet")
    icon_problems = _expand_icons(root)

    ids, items, part_of = [], [], {}

    def walk(e: ET.Element, top: str | None, in_defs: bool) -> None:
        name = _local(e.tag)
        if name not in ELEMENTS and name not in UNWRAP:
            return  # removed with everything inside it
        defs = in_defs or name in DEFINITIONS
        eid = e.get("id")
        here = top
        if eid and not defs and name in ELEMENTS:
            if top is None:
                ids.append(eid)
                here = eid
            else:
                part_of[eid] = top
            items.append({"id": eid, "label": _name_of(e)})
        for c in e:
            walk(c, here, defs)

    for c in root:
        walk(c, None, False)
    cleaner = _Cleaner()
    svg = "".join(cleaner.element(root, root=True))
    first: dict[str, dict] = {}
    for it in items:
        first.setdefault(it["id"], it)
    dupes = sorted({it["id"] for it in items if first[it["id"]] is not it})
    problems = icon_problems + [f"id `{d}` is used more than once; cues reach the first" for d in dupes]
    vis.update(
        svg=svg,
        ids=list(dict.fromkeys(ids)),
        items=list(first.values()),
        part_of=part_of,
        box=box,
        removed=cleaner.removed,
        elements=cleaner.count,
        from_file=bool(attrs.get("file")),
    )
    return vis, problems


STEP_KEYS = ("svg", "ids", "items", "part_of", "box")


def _expand_icons(root: ET.Element) -> list[str]:
    """`<use href="icon:name" x y width height/>` becomes a group drawing that Lucide icon, 24 units
    square scaled to `width` (or `height`), stroked with `currentColor` unless it sets `stroke`.
    Its id, class, colour and title stay, so cues and reveals reach it like any other part."""
    problems = []
    for e in root.iter():
        if _local(e.tag) != "use":
            continue
        href = e.get("href") or e.get(f"{XLINK}href") or ""
        if not href.startswith("icon:"):
            continue
        name = href[5:].strip()
        inner = icons.get(name)
        if inner is None:
            close = icons.suggest(name)
            problems.append(f"there is no icon `{name}`" + (f" (close: {', '.join(close)})" if close else ""))
            e.attrib.pop("href", None)
            e.attrib.pop(f"{XLINK}href", None)
            continue
        x, y = _number(e.get("x")) or 0.0, _number(e.get("y")) or 0.0
        size = _number(e.get("width")) or _number(e.get("height")) or 24.0
        outer = e.get("transform", "")
        keep = {k: v for k, v in e.attrib.items() if k not in ("href", f"{XLINK}href", "x", "y", "width", "height", "transform")}
        e.tag = f"{{{SVG_NS}}}g"
        e.attrib.clear()
        e.attrib.update(
            {"fill": "none", "stroke": "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round"}
        )
        e.attrib.update(keep)
        e.set("transform", f"{outer} translate({x:g} {y:g}) scale({size / 24:g})".strip())
        for c in ET.fromstring(f'<g xmlns="{SVG_NS}">{inner}</g>'):
            e.append(c)
    return problems


def _items(vis: dict) -> list[dict]:
    """Every named element, across all versions of a drawing that changes in steps."""
    seen, out = set(), []
    for it in vis.get("items", []) + [it for st in vis.get("steps", []) for it in st.get("items", [])]:
        if it["id"] not in seen:
            seen.add(it["id"])
            out.append(it)
    return out


def find(vis: dict, target: str) -> str | None:
    """Id of the element a cue names: its id, or a unique name (its <title> or text) or slug of one."""
    t = target.strip()
    items = _items(vis)
    if t in {i["id"] for i in items}:
        return t
    low = t.lower()
    hits = [i["id"] for i in items if i["label"] and (i["label"].lower() == low or slug(i["label"]) == low)]
    return hits[0] if len(set(hits)) == 1 else None


def labels(vis: dict) -> list[tuple[str, str]]:
    return [(i["id"], i["label"]) for i in _items(vis) if i["label"]]


def _check(vis: dict, line: int, limits: dict) -> list[Finding]:
    out: list[Finding] = []
    removed = vis.pop("removed", {}) or {}
    n = vis.pop("elements", 0)
    from_file = vis.pop("from_file", False)
    if removed:
        what = ", ".join(f"{k} ({v})" if v > 1 else k for k, v in sorted(removed.items(), key=lambda kv: -kv[1])[:8])
        out.append(
            (
                "svg_removed",
                f"The drawing on line {line} lost parts the player does not allow: {what}.",
                "An exported file usually keeps plain-text copies of its labels; check the slide with preview_slides."
                if from_file
                else "Draw with shapes, paths and text only; no scripts, links, embedded HTML or animation elements.",
            )
        )
    if n > MAX_ELEMENTS:
        out.append(
            (
                "block_too_big",
                f"The drawing on line {line} has {n} elements; drawings read well with far fewer.",
                "Simplify it, or split it across slides.",
            )
        )
    return out


KIND = Kind(
    "svg",
    ("svg",),
    parse_svg,
    check=_check,
    part=lambda vis: "element",
    hint="",
    find=find,
    labels=labels,
    reads_file=True,
    regions=True,
    steps=STEP_KEYS,
)
