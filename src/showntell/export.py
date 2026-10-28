"""Static export: a folder that plays from any web server, with WebVTT captions.

    out/index.html      the player, reading data.json instead of the live stream
    out/data.json       the walkthrough
    out/audio/          one file per narration paragraph (WAV, or MP3 with --mp3)
    out/assets/         images used by slides
    out/static/         player code and the bundled libraries
    out/captions.vtt    captions on the walkthrough's single timeline
    out/source.md       the Markdown the walkthrough was written in
    out/poster.png      the title slide, for link previews (drawn here with Playwright, else the one finish kept)

An output path ending in `.mp4` renders a video instead (video.py), and one ending in `.html`
writes a single self-contained page (export_html) that plays offline from a double-click.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from .config import Config
from .model import Walkthrough

GAP, SLIDE_GAP, EMPTY = 0.3, 0.6, 2.5  # keep in step with static/js/timeline.js
STATIC = Path(__file__).parent / "static"


def _estimate(x) -> float:
    """A paragraph's length on the timeline, as `estimate` in timeline.js computes it."""
    if x.pause:
        return x.pause
    if x.status == "ready" and x.duration:
        return x.duration
    return max(1.0, len(x.text.split()) / 2.7)


def _walk(w: Walkthrough) -> tuple[list[tuple[float, object, object]], dict[str, float], float]:
    out, starts, t = [], {}, 0.0
    for slide in w.slides:
        starts[slide.id] = t
        says = [x for x in slide.says if x.pause or x.text]
        if not says:
            t += EMPTY + SLIDE_GAP
            continue
        for k, x in enumerate(says):
            out.append((t, slide, x))
            t += _estimate(x) + (0 if x.pause else SLIDE_GAP if k == len(says) - 1 else GAP)
    return out, starts, t


def timeline(w: Walkthrough) -> list[tuple[float, object, object]]:
    """(start, slide, say) for every paragraph, on the same clock as the player."""
    return _walk(w)[0]


def slide_starts(w: Walkthrough) -> dict[str, float]:
    """Where each slide starts on the player's clock, by slide id."""
    return _walk(w)[1]


def timeline_total(w: Walkthrough) -> float:
    """The walkthrough's length in seconds on the player's clock, trailing gap included."""
    return _walk(w)[2]


def _ts(t: float) -> str:
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


# Caption grouping: break where the voice pauses or a sentence ends, keep groups short enough
# to read at a glance, show each a moment before its first word and let it linger briefly.
MAX_WORDS, MAX_SECONDS = 8, 3.0
PAUSE_BREAK, COMMA_PAUSE = 0.45, 0.2
LEAD, LINGER, MIN_SHOW = 0.08, 0.6, 0.7


def vtt(w: Walkthrough) -> str:
    """WebVTT captions on the player's timeline, one short phrase per cue."""
    cues: list[tuple[float, float, str]] = []
    for start, _, say in timeline(w):
        if say.pause or not say.text or not say.words:
            continue
        for group in phrases(say):
            a, b = group[0]["c0"], group[-1]["c1"]
            while b < len(say.text) and say.text[b] in ".,;:!?)\"'":  # keep trailing punctuation
                b += 1
            cues.append((start + group[0]["s"], start + group[-1]["e"], say.text[a:b].strip()))
    lines = ["WEBVTT", ""]
    for i, (s0, e0, text) in enumerate(cues):
        nxt = cues[i + 1][0] - LEAD if i + 1 < len(cues) else e0 + LINGER
        prev_end = cues[i - 1][1] if i else 0.0
        a = max(prev_end, s0 - LEAD) if i else max(0.0, s0 - LEAD)
        b = min(max(e0 + LINGER, a + MIN_SHOW), max(nxt, e0))
        lines += [str(i + 1), f"{_ts(a)} --> {_ts(b)}", text, ""]
    return "\n".join(lines)


def phrases(say) -> list[list[dict]]:
    """Group a paragraph's words into caption phrases using the voice's own timing."""
    out: list[list[dict]] = []
    group: list[dict] = []
    words = say.words
    for i, w_ in enumerate(words):
        group.append(w_)
        after = say.text[w_["c1"] : w_["c1"] + 1]
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["s"] - w_["e"]) if nxt else 0.0
        span = w_["e"] - group[0]["s"]
        if (
            nxt is None
            or after in (".", "?", "!")
            or gap >= PAUSE_BREAK
            or (after in (",", ";", ":") and (gap >= COMMA_PAUSE or len(group) >= 4))
            or len(group) >= MAX_WORDS
            or span >= MAX_SECONDS
        ):
            out.append(group)
            group = []
    if group:
        out.append(group)
    return out


def export(
    data_dir: Path, wid: str, out: Path, mp3: bool = False, static_href: str = "static", copy_static: bool = True, poster: bool = False
) -> Path:
    """Write the walkthrough to `out`. A gallery of several exports can share one copy of the
    player: pass `static_href="../static"` and `copy_static=False`, and copy `STATIC` once.
    `poster` also writes poster.png when Playwright is installed (needs `copy_static`)."""
    state = data_dir / "walkthroughs" / wid / "state.json"
    if not state.is_file():
        raise SystemExit(f"no walkthrough {wid} in {data_dir}")
    w = Walkthrough.from_state(json.loads(state.read_text()))
    pending = [x for s in w.slides for x in s.says if x.text and x.status != "ready"]
    if pending:
        print(f"warning: {len(pending)} paragraph(s) have no audio yet and will be shown without sound", file=sys.stderr)
    if out.exists() and any(out.iterdir()):
        if not (out / "data.json").is_file():
            raise SystemExit(f"{out} exists and is not a showntell export; choose an empty folder")
        shutil.rmtree(out)
    (out / "audio").mkdir(parents=True)
    from .server import render_player

    if copy_static:
        copy_player(out / "static")
    assets = data_dir / "walkthroughs" / wid / "assets"
    if assets.is_dir():
        shutil.copytree(assets, out / "assets")
    audio_dir = data_dir / "audio"
    data = w.public()
    for slide in data["slides"]:
        for say in slide["says"]:
            if "audio" not in say:
                continue
            key = Path(say["audio"]).stem
            src = audio_dir / f"{key}.wav"
            if not src.is_file():
                say.pop("audio")
                continue
            if mp3:
                import soundfile as sf

                audio, sr = sf.read(src, dtype="float32")
                sf.write(out / "audio" / f"{key}.mp3", audio, sr, format="MP3")
                say["audio"] = f"audio/{key}.mp3"
            else:
                shutil.copyfile(src, out / "audio" / f"{key}.wav")
    data["status"] = "finished"
    (out / "data.json").write_text(json.dumps(data))
    (out / "index.html").write_text(
        render_player(w.title, w.frame, "", static_href, live=False, theme=w.theme, tokens=w.tokens, description=w.description)
    )
    (out / "captions.vtt").write_text(vtt(w))
    src_md = data_dir / "walkthroughs" / wid / "source.md"
    if src_md.is_file():
        (out / "source.md").write_text(src_md.read_text())
    if poster and copy_static and (write_poster(out) or _copy_poster(data_dir / "walkthroughs" / wid, out)):
        page = (out / "index.html").read_text()
        meta = (
            '<meta property="og:image" content="poster.png">\n'
            '<meta name="twitter:card" content="summary_large_image">\n'
            '<meta name="twitter:image" content="poster.png">\n'
        )
        (out / "index.html").write_text(page.replace("</head>", meta + "</head>", 1))
    return out


# -- one self-contained page ----------------------------------------------------------------------

# Libraries a walkthrough loads only when a slide needs them, by slide kind (paths under static/).
LIBRARIES = {
    "diagram": ["vendor/mermaid.min.js"],
    "math": ["vendor/katex/katex.min.js", "vendor/katex/katex.min.css"],
    "code": ["vendor/hljs/highlight.min.js"],
    "diff": ["vendor/hljs/highlight.min.js"],
    "terminal": ["vendor/hljs/highlight.min.js"],
    "drawio": ["vendor/drawio/viewer-static.min.js"],
}
MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".avif": "image/avif",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".woff2": "font/woff2",
}
GZIP_OVER = 100_000  # characters; smaller libraries are not worth unpacking
_IMPORT_RE = re.compile(r"""(\bfrom\s*|\bimport\s*)(["'])(\.{1,2}/[^"']+)\2""")


def _data_uri(path: Path) -> str:
    import base64

    return f"data:{MIME.get(path.suffix.lower(), 'application/octet-stream')};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _script_json(value) -> str:
    """JSON safe inside a <script> element: no `</script`, no `<!--`."""
    return json.dumps(value, separators=(",", ":")).replace("<", "\\u003c")


def _modules() -> dict[str, str]:
    """The player's ES modules as data: URLs under bare specifiers (`showntell/js/...`), their
    relative imports rewritten to match, for an import map: a page opened from disk cannot
    fetch module files, but it can import data: URLs."""
    import base64
    import posixpath

    root = STATIC / "js"
    out = {}
    for f in sorted(root.rglob("*.js")):
        here = posixpath.dirname(f.relative_to(root).as_posix())

        def bare(m: re.Match, here: str = here) -> str:
            target = posixpath.normpath(posixpath.join(here, m.group(3)))
            return f"{m.group(1)}{m.group(2)}showntell/js/{target}{m.group(2)}"

        code = _IMPORT_RE.sub(bare, f.read_text())
        out[f"showntell/js/{f.relative_to(root).as_posix()}"] = "data:text/javascript;base64," + base64.b64encode(code.encode()).decode()
    return out


def _katex_css() -> str:
    """KaTeX's stylesheet with its fonts inside it (woff2 only, which every current browser reads)."""
    css = (STATIC / "vendor" / "katex" / "katex.min.css").read_text()
    fonts = STATIC / "vendor" / "katex" / "fonts"

    def src(m: re.Match) -> str:
        woff2 = re.search(r"url\((fonts/[^)]+\.woff2)\)", m.group(0))
        return f'src:url({_data_uri(fonts / Path(woff2.group(1)).name)}) format("woff2")' if woff2 else m.group(0)

    return re.sub(r"src:[^;}]*", src, css)


def _library(rel: str) -> str | dict:
    """A library's text, or {"gzip": base64} for the ones loaded on demand (Mermaid shrinks from
    2.6 MB to 1 MB). highlight.js starts with the page, so it stays plain text."""
    import base64
    import gzip

    text = (STATIC / rel).read_text()
    if rel.endswith("highlight.min.js") or len(text) < GZIP_OVER:
        return text
    return {"gzip": base64.b64encode(gzip.compress(text.encode(), 9, mtime=0)).decode()}


def _swap(page: str, old: str, new: str) -> str:
    if page.count(old) != 1:
        raise RuntimeError(f"player.html changed: expected {old!r} once")
    return page.replace(old, new)


# Runs at the end of the page, before the player's modules (which wait for the whole page): the
# walkthrough's narration becomes blob: URLs so the audio elements hold short addresses, and
# highlight.js, which the code slides call as they are drawn, is started.
_BOOT = """<script>
(function () {
  var read = function (id) { var e = document.getElementById(id); return e ? JSON.parse(e.textContent) : {}; };
  var blob = function (uri) {
    var i = uri.indexOf(","), bytes = atob(uri.slice(i + 1)), a = new Uint8Array(bytes.length);
    for (var k = 0; k < bytes.length; k++) a[k] = bytes.charCodeAt(k);
    return URL.createObjectURL(new Blob([a], { type: uri.slice(5, uri.indexOf(";")) }));
  };
  var data = read("showntell-data");
  data.slides.forEach(function (s) { s.says.forEach(function (x) { if (x.audio) x.audio = blob(x.audio); }); });
  window.__showntellData = data;
  var files = window.__showntellFiles = read("showntell-files");
  var hl = files["vendor/hljs/highlight.min.js"];
  if (hl) { var s = document.createElement("script"); s.textContent = hl; document.head.append(s); }
})();
</script>"""


def export_html(data_dir: Path, wid: str, out: Path) -> Path:
    """One HTML file holding the player, the walkthrough, its narration (MP3) and images, and only
    the libraries its slides use. It plays offline and makes no network requests."""
    import tempfile

    from .server import render_player

    with tempfile.TemporaryDirectory() as tmp:
        folder = export(data_dir, wid, Path(tmp) / "w", mp3=True, copy_static=False)
        data = json.loads((folder / "data.json").read_text())
        for slide in data["slides"]:
            for say in slide["says"]:
                if say.get("audio"):
                    say["audio"] = _data_uri(folder / say["audio"])
            v = slide.get("visual") or {}
            if slide["kind"] == "image" and str(v.get("src", "")).startswith("assets/"):
                v["src"] = _data_uri(folder / v["src"])
    w = Walkthrough.from_state(json.loads((data_dir / "walkthroughs" / wid / "state.json").read_text()))
    files: dict[str, str | dict] = {}
    for kind in sorted({s["kind"] for s in data["slides"]}):
        for rel in LIBRARIES.get(kind, []):
            files[rel] = _katex_css() if rel.endswith("katex.min.css") else _library(rel)
    if any((s.get("visual") or {}).get("sketch") for s in data["slides"]):  # hand-drawn drawings
        files["vendor/rough/rough.js"] = _library("vendor/rough/rough.js")
        files["vendor/fonts/Virgil.woff2"] = _data_uri(STATIC / "vendor/fonts/Virgil.woff2")

    page = render_player(w.title, w.frame, "", "__STATIC__", live=False, theme=w.theme, tokens=w.tokens, description=w.description)
    css = "\n".join((STATIC / n).read_text() for n in ("themes.css", "player.css"))
    # The player's own faces travel inside the stylesheet, as KaTeX's do.
    css = re.sub(r'url\("(vendor/fonts/[^"]+\.woff2)"\)', lambda m: f'url("{_data_uri(STATIC / m.group(1))}")', css)
    if "</style" in css:
        raise RuntimeError("the player's CSS cannot be inlined")
    page = _swap(
        page,
        '<link rel="icon" href="__STATIC__/icon.svg" type="image/svg+xml">',
        f'<link rel="icon" href="{_data_uri(STATIC / "icon.svg")}" type="image/svg+xml">',
    )
    page = _swap(
        page,
        '<link rel="stylesheet" href="__STATIC__/themes.css">\n<link rel="stylesheet" href="__STATIC__/player.css">',
        f"<style>\n{css}\n</style>",
    )
    page = _swap(page, '<script src="__STATIC__/vendor/hljs/highlight.min.js"></script>\n', "")
    page = _swap(
        page,
        '<script type="module" src="__STATIC__/js/main.js"></script>',
        f'<script type="importmap">{_script_json({"imports": _modules()})}</script>\n'
        '<script type="module">import "showntell/js/main.js";</script>',
    )
    page = _swap(page, 'data-static="__STATIC__"', 'data-static="static"')
    # The walkthrough and the libraries come last, so the page draws before they are parsed.
    tail = (
        f'<script type="application/json" id="showntell-data">{_script_json(data)}</script>\n'
        f'<script type="application/json" id="showntell-files">{_script_json(files)}</script>\n{_BOOT}\n'
    )
    page = _swap(page, "</body>", tail + "</body>")
    if "__STATIC__" in page:
        raise RuntimeError("player.html references a file the single page does not carry")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    return out


def write_poster(folder: Path) -> bool:
    """poster.png: the title slide's settled frame. False, silently, without Playwright or its
    browser; a warning for any other failure."""
    from .capture import CaptureUnavailable, capture_frames

    try:
        png = capture_frames(folder, [0.0])[0]
    except CaptureUnavailable:
        return False
    except Exception as e:  # a poster is a nicety; never fail the export over it
        print(f"warning: no poster.png: {e}", file=sys.stderr)
        return False
    (folder / "poster.png").write_bytes(png)
    return True


def _copy_poster(folder: Path, out: Path) -> bool:
    """The poster finish_walkthrough kept (640 wide), when no browser is here to draw a larger one."""
    src = folder / "poster.png"
    if not src.is_file():
        return False
    shutil.copyfile(src, out / "poster.png")
    return True


def copy_player(dest: Path) -> None:
    shutil.copytree(STATIC, dest, ignore=shutil.ignore_patterns("index.html", "player.html", "*.map"), dirs_exist_ok=True)


def _resolve_id(source: str) -> str | None:
    m = re.search(r"/w/([A-Za-z0-9_-]{16,64})", source)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{16,64}", source):
        return source
    return None


def export_cli(cfg: Config, args) -> None:
    wid = _resolve_id(args.source)
    if wid is None:
        path = Path(args.source).expanduser()
        if not path.is_file():
            raise SystemExit(f"{args.source} is neither a walkthrough id, a link nor a Markdown file")
        wid = _play_and_wait(cfg, path)
    dest = Path(args.out).expanduser()
    if dest.suffix.lower() == ".mp4":
        return _export_mp4(cfg, wid, dest, args)
    if dest.suffix.lower() in (".html", ".htm"):
        if args.mp3:
            raise SystemExit("--mp3 applies to folder exports; a single page always carries MP3 audio")
        export_html(cfg.data_dir, wid, dest)
        print(f"Exported to {dest} ({dest.stat().st_size / 1e6:.1f} MB): one file that plays offline in a browser.")
        return
    out = export(cfg.data_dir, wid, dest, mp3=args.mp3, poster=True)
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"Exported to {out} ({size / 1e6:.1f} MB). Serve the folder with any web server, for example:")
    print(f"  python3 -m http.server --directory {out} 8000")


def _export_mp4(cfg: Config, wid: str, dest: Path, args) -> None:
    from .video import render_mp4

    if args.mp3:
        raise SystemExit("--mp3 applies to folder exports; an MP4 always carries AAC audio")
    max_bytes = int(getattr(args, "max_mb", 0) * 1e6)
    r = render_mp4(
        cfg.data_dir,
        wid,
        dest,
        fps=args.fps,
        workers=args.workers,
        captions=args.burn_captions,
        motion=getattr(args, "motion", False),
        max_bytes=max_bytes,
    )
    for e in r["errors"]:
        print(f"- player: {e}", file=sys.stderr)
    print(
        f"Exported to {dest} ({dest.stat().st_size / 1e6:.1f} MB, {r['duration']:.1f} s, {r['frames']} frames at "
        f"{r['fps']} fps) in {r['seconds']:.1f} s: {r['speed']:.1f} s of video per second"
        + (f"; shrunk to fit with {r['shrunk']}" if r["shrunk"] else "")
    )


def _play_and_wait(cfg: Config, path: Path) -> str:
    """Voice a Markdown file through the daemon and wait until every paragraph has audio."""
    import asyncio

    from . import daemon
    from .cli import _project_for, file_meta
    from .service import RemoteService

    lock = daemon.ensure(cfg, log=lambda m: print(m, file=sys.stderr))
    text = path.read_text()
    meta = file_meta(path, text)

    async def go() -> str:
        svc = RemoteService(f"http://127.0.0.1:{lock['port']}", lock["token"])
        r = await svc.start(meta["title"], meta["frame"], meta["voice"], meta["speed"], str(_project_for(path)))
        await svc.add(r["handle"], text)
        await svc.playable(r["handle"], 60)
        for _ in range(600):
            f = await svc.finish(r["handle"], None, check=False)
            if not f["pending"]:
                break
            await asyncio.sleep(0.5)
        return r["handle"].split(".")[0]

    return asyncio.run(go())
