"""```drawio: a draw.io diagram, read with `file=` (a .drawio file, or a .drawio.svg export that
carries its diagram) or written inline as mxfile XML. The player draws it with the official
draw.io viewer, vendored and loaded only when a slide needs it.

The diagram is rebuilt from what draw.io needs to draw it: cells, styles, geometry and labels.
Labels keep simple formatting only; links, tooltips, placeholders, web fonts, outside images and
maths are dropped, so the viewer has nothing to open or fetch. Cells with labels or ids are what
cues point at; the cells directly on a layer are the parts a step reveal shows in source order,
and an edge between two parts appears with the later of them.
"""

from __future__ import annotations

import base64
import html
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from html.parser import HTMLParser
from xml.sax.saxutils import escape, quoteattr

from .base import Finding, Kind, slug

MAX_BYTES = 2_000_000
MAX_CELLS = 400
WRAPPERS = ("UserObject", "object")
CELL_ATTRS = ("id", "value", "style", "vertex", "edge", "parent", "source", "target", "connectable", "collapsed", "visible")
MODEL_ATTRS = (
    "dx",
    "dy",
    "grid",
    "gridSize",
    "guides",
    "tooltips",
    "connect",
    "arrows",
    "fold",
    "page",
    "pageScale",
    "pageWidth",
    "pageHeight",
    "background",
    "shadow",
    "adaptiveColors",
)
GEOMETRY = {
    "mxGeometry": ("x", "y", "width", "height", "relative", "as"),
    "mxPoint": ("x", "y", "as"),
    "Array": ("as",),
    "mxRectangle": ("x", "y", "width", "height", "as"),
}
# Style keys that point outside the drawing or make it interactive.
STYLE_DROP = {"link", "fontSource", "placeholders", "tooltip", "clickable", "editable", "movable", "resizable"}
LABEL_TAGS = {
    "b",
    "i",
    "u",
    "s",
    "strike",
    "strong",
    "em",
    "code",
    "pre",
    "br",
    "hr",
    "span",
    "div",
    "p",
    "font",
    "sub",
    "sup",
    "ul",
    "ol",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "table",
    "thead",
    "tbody",
    "tr",
    "td",
    "th",
    "blockquote",
}
LABEL_ATTRS = {
    "style",
    "color",
    "face",
    "size",
    "align",
    "colspan",
    "rowspan",
    "width",
    "height",
    "border",
    "cellpadding",
    "cellspacing",
    "valign",
}
VOID = {"br", "hr"}
UNSAFE = re.compile(r"url\(|expression\(|javascript:|@import|behavior\s*:", re.I)


class _Label(HTMLParser):
    """Rebuilds an HTML label from an allowlist of formatting tags; everything else keeps its text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str] = []
        self.skip = 0  # inside <script>, <style> and the like

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style", "iframe", "object", "embed", "template", "svg", "math"):
            self.skip += 1
            return
        if tag not in LABEL_TAGS or self.skip:
            return
        kept = "".join(f" {k}={quoteattr(v)}" for k, v in attrs if k in LABEL_ATTRS and v is not None and not UNSAFE.search(v))
        self.out.append(f"<{tag}{kept}>")
        if tag not in VOID:
            self.open.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "iframe", "object", "embed", "template", "svg", "math"):
            self.skip = max(0, self.skip - 1)
            return
        if tag in self.open and not self.skip:
            while self.open:
                t = self.open.pop()
                self.out.append(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(escape(data))

    def text(self) -> str:
        return "".join(self.out) + "".join(f"</{t}>" for t in reversed(self.open))


def clean_label(value: str, is_html: bool) -> str:
    if not is_html:
        return value
    p = _Label()
    p.feed(value)
    p.close()
    return p.text()


def plain(value: str, is_html: bool) -> str:
    """A label as words: tags and line breaks become spaces."""
    if is_html:
        value = re.sub(r"<br\s*/?>|</(div|p|li|tr|h\d)>", " ", value, flags=re.I)
        value = html.unescape(re.sub(r"<[^>]+>", "", value))
    return " ".join(value.split())


def clean_style(style: str) -> str:
    out = []
    for part in style.split(";"):
        if not part:
            continue
        key, eq, value = part.partition("=")
        if key in STYLE_DROP or "javascript:" in value.lower():
            continue
        if key == "image" and not value.startswith("data:image/"):
            continue
        out.append(part if eq else key)
    return ";".join(out)


MAX_INFLATED = 20_000_000  # bytes a compressed page may expand to; a real one is far smaller


def _decompress(text: str) -> str:
    data = base64.b64decode(text.strip())
    d = zlib.decompressobj(-15)
    raw = d.decompress(data, MAX_INFLATED)
    if d.unconsumed_tail:
        raise ValueError(f"it expands past {MAX_INFLATED // 1_000_000} MB")
    return urllib.parse.unquote(raw.decode("utf-8"))


def _xml(text: str) -> ET.Element:
    if re.search(r"<!ENTITY", text, re.I):
        raise ValueError("the file declares XML entities, which are not allowed")
    text = re.sub(r"<!DOCTYPE[^>]*>", "", re.sub(r"<\?xml[^>]*\?>", "", text.strip()), flags=re.I)
    return ET.fromstring(text)


def _pages(root: ET.Element) -> list[tuple[str, ET.Element | str]]:
    """(name, model) for each page: an <mxGraphModel> element, or its compressed text."""
    if root.tag.rsplit("}", 1)[-1] == "svg":  # a .drawio.svg export carries the diagram in `content`
        content = root.get("content", "")
        if "<mxfile" not in content and "<mxGraphModel" not in content:
            raise ValueError("this SVG has no draw.io diagram in it; show it with ```svg instead")
        root = _xml(content)
    if root.tag == "mxGraphModel":
        return [("Page-1", root)]
    if root.tag != "mxfile":
        raise ValueError("this is not a draw.io file (no <mxfile> or <mxGraphModel>)")
    pages = []
    for i, d in enumerate(root.findall("diagram")):
        model = d.find("mxGraphModel")
        pages.append((d.get("name") or f"Page-{i + 1}", model if model is not None else (d.text or "")))
    return pages


def _cells(model: ET.Element) -> list[tuple[ET.Element, ET.Element]]:
    """(cell, carrier) pairs: the <mxCell> and the element that holds its id and label (a wrapper
    such as <UserObject>, or the cell itself)."""
    root = model.find("root")
    out = []
    for e in root if root is not None else []:
        if e.tag == "mxCell":
            out.append((e, e))
        elif e.tag in WRAPPERS:
            inner = e.find("mxCell")
            if inner is not None:
                out.append((inner, e))
    return out


def _geometry(e: ET.Element) -> str:
    if e.tag not in GEOMETRY:
        return ""
    attrs = "".join(f" {k}={quoteattr(e.get(k))}" for k in GEOMETRY[e.tag] if e.get(k) is not None)
    inner = "".join(_geometry(c) for c in e)
    return f"<{e.tag}{attrs}>{inner}</{e.tag}>"


def parse_drawio(body: str, attrs: dict) -> tuple[dict, list[str]]:
    """```drawio file=docs/arch.drawio [page="Name" or page=2]: one page of a draw.io file."""
    vis: dict = {"xml": "", "ids": [], "items": [], "part_of": {}, "edges": {}, "title": attrs.get("title", "")}
    background = attrs.get("background", "white")
    if background != "none" and re.match(r"^(#[0-9a-fA-F]{3,8}|[a-z]{3,20}|var\(--[a-z0-9-]+\))$", background):
        vis["background"] = background
    if len(body.encode()) > MAX_BYTES:
        return vis, [f"the diagram is {len(body.encode()) // 1000} KB; the limit is {MAX_BYTES // 1000} KB"]
    try:
        pages = _pages(_xml(body))
    except ET.ParseError as e:
        return vis, [f"the diagram is not well-formed XML ({e})"]
    except ValueError as e:
        return vis, [str(e)]
    if not pages:
        return vis, ["the file has no pages"]
    want = str(attrs.get("page", "")).strip()
    names = [n for n, _ in pages]
    if not want:
        pick = 0
    elif want.isdigit() and 1 <= int(want) <= len(pages):
        pick = int(want) - 1
    elif want in names:
        pick = names.index(want)
    else:
        return vis, [f"no page `{want}`; the pages are {', '.join(f'{i + 1} `{n}`' for i, n in enumerate(names))}"]
    name, model = pages[pick]
    if isinstance(model, str):
        try:
            model = _xml(_decompress(model))
        except Exception as e:  # noqa: BLE001 - any failure to unpack is the same problem for the author
            return vis, [f"page `{name}` could not be decompressed ({type(e).__name__})"]
    cells = _cells(model)
    if not cells:
        return vis, [f"page `{name}` has no cells"]

    # Rebuild the page from cells, styles, geometry and cleaned labels.
    out, info = [], {}
    for cell, carrier in cells:
        cid = carrier.get("id") or cell.get("id")
        style = clean_style(cell.get("style", ""))
        is_html = "html=1" in style
        raw = carrier.get("label") if carrier is not cell else cell.get("value")
        value = clean_label(raw or "", is_html)
        a = {k: cell.get(k) for k in CELL_ATTRS if cell.get(k) is not None}
        a.update(id=cid, style=style, value=value)
        if not style:
            a.pop("style")
        if cid is None:
            a.pop("id")
        geo = "".join(_geometry(c) for c in cell)
        out.append("<mxCell" + "".join(f" {k}={quoteattr(v)}" for k, v in a.items()) + f">{geo}</mxCell>")
        if cid is not None:
            info[cid] = {
                "parent": cell.get("parent"),
                "vertex": cell.get("vertex") == "1",
                "edge": cell.get("edge") == "1",
                "source": cell.get("source"),
                "target": cell.get("target"),
                "label": plain(raw or "", is_html),
            }
    mattrs = "".join(f" {k}={quoteattr(model.get(k))}" for k in MODEL_ATTRS if model.get(k) is not None)
    vis["xml"] = (
        f'<mxfile><diagram id="page" name={quoteattr(name)}><mxGraphModel{mattrs} math="0"><root>'
        + "".join(out)
        + "</root></mxGraphModel></diagram></mxfile>"
    )

    # Parts: the cells directly on a layer (a child of the root cell), in source order.
    roots = {cid for cid, c in info.items() if not c["parent"]}
    layers = {cid for cid, c in info.items() if c["parent"] in roots}
    shown = [cid for cid, c in info.items() if c["vertex"] or c["edge"]]

    def top(cid: str) -> str | None:
        seen = set()
        while cid in info and info[cid]["parent"] not in layers and cid not in seen:
            seen.add(cid)
            cid = info[cid]["parent"]
        return cid if cid in info and info[cid]["parent"] in layers else None

    order = [cid for cid in shown if info[cid]["parent"] in layers and info[cid]["vertex"]]
    edges, part_of = {}, {}
    for cid in shown:
        c = info[cid]
        if c["edge"]:
            ends = [top(c["source"]) if c["source"] else None, top(c["target"]) if c["target"] else None]
            if all(e in order for e in ends):
                edges[cid] = ends
                part_of[cid] = max(ends, key=order.index)  # it appears with the later of its two ends
                continue
            if c["parent"] in layers:
                order.append(cid)
                continue
        t = top(cid)
        if t and t != cid:
            part_of[cid] = t
    vis.update(
        ids=order,
        part_of=part_of,
        edges=edges,
        page=name,
        items=[{"id": cid, "label": info[cid]["label"]} for cid in shown],
        cells=len(shown),
    )
    return vis, []


def find(vis: dict, target: str) -> str | None:
    """Id of the cell a cue names: its id, or a unique label or slug of one."""
    t = target.strip()
    if t in [i["id"] for i in vis.get("items", [])]:
        return t
    low = t.lower()
    hits = [i["id"] for i in vis.get("items", []) if i["label"] and (i["label"].lower() == low or slug(i["label"]) == low)]
    return hits[0] if len(set(hits)) == 1 else None


def labels(vis: dict) -> list[tuple[str, str]]:
    return [(i["id"], i["label"]) for i in vis.get("items", []) if i["label"]]


def _check(vis: dict, line: int, limits: dict) -> list[Finding]:
    n = vis.pop("cells", 0)
    if n > MAX_CELLS:
        return [
            (
                "block_too_big",
                f"The diagram on line {line} has {n} shapes and connectors; a slide reads well with far fewer.",
                "Show one page or area of it, or split it across slides.",
            )
        ]
    return []


KIND = Kind(
    "drawio",
    ("drawio",),
    parse_drawio,
    check=_check,
    part=lambda vis: "cell",
    find=find,
    labels=labels,
    reads_file=True,
    regions=True,
    hint="Point file= at a .drawio file, or a .drawio.svg export, and pick a page with page=.",
)
