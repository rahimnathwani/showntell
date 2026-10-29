"""Turn parsed blocks into slides: visuals, code from disk, diffs, cues and checks.

The builder appends to a walkthrough as blocks arrive. Every problem it finds becomes a
plain-language issue returned to the agent on its next call. Cue resolution lives in cues.py,
the checks in checks.py, and the bundled kinds (charts, trees, …) in the kinds package.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import icons, kinds, sources
from .checks import Checks, generated
from .cues import CueResolver
from .format import Block, Parser, inline_plain, parse_item, parse_narration, parse_table
from .issues import Issue
from .mermaid import parse_mermaid
from .model import Say, Slide, Walkthrough

# Comfortable sizes, measured on the player at each frame (scripts/measure_limits.py).
# "drawing" is a viewBox the shape of the slide body, so a drawing made to it fills the slide.
LIMITS = {
    "landscape": {"lines": 18, "width": 90, "diff_lines": 18, "diff_width": 46, "nodes": 12, "drawing": "0 0 1150 560"},
    "square": {"lines": 26, "width": 68, "diff_lines": 22, "diff_width": 32, "nodes": 10, "drawing": "0 0 860 800"},
    "portrait": {"lines": 34, "width": 48, "diff_lines": 18, "diff_width": 52, "nodes": 8, "drawing": "0 0 640 1080"},
}
IMAGE_FRAMES = ("browser", "window", "phone")
SPEED_RANGE = (0.8, 1.3)
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif"}


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40].strip("-") or "slide"


class Builder(CueResolver, Checks):
    def __init__(self, w: Walkthrough, assets_dir: Path, hosted: bool = False) -> None:
        self.w = w
        self.assets_dir = assets_dir
        self.hosted = hosted  # hosted mode: nothing is read from the server's disk or fetched from elsewhere
        self.parser = Parser()
        self.questions_mode = False
        self.pending_old: tuple[Block, sources.Excerpt] | None = None
        self.issues: list[Issue] = []
        self.new_says: list[tuple[Slide, Say]] = []
        self.changed: set[str] = set()
        self.visual_line = 0  # where the visual being added starts, for a slide it spills onto
        self.into_pair = False  # the visual being added is the second one on a `.pair` slide

    @property
    def root(self) -> Path | None:
        return Path(self.w.root) if self.w.root else None

    @property
    def limits(self) -> dict:
        return LIMITS.get(self.w.frame, LIMITS["landscape"])

    def _no_disk(self, b: Block, what: str) -> bool:
        """In hosted mode, refuse a block that would read the server's disk or git; True if refused."""
        if not self.hosted:
            return False
        from .hosted import say

        self._issue(
            "source_error",
            say("no_disk_block", "Line {line}: {what} cannot be read on this server.", line=b.line, what=what),
            say("no_disk_fix", ""),
            line=b.line,
        )
        return True

    def _issue(self, code: str, message: str, fix: str = "", *, slide: str | None = None, line: int | None = None) -> None:
        if slide is None and self.w.slides:
            slide = self.w.slides[-1].id
        self.issues.append(Issue(code, message, fix, slide=slide, line=line or None))

    # -- appending ----------------------------------------------------------------------

    def add(self, markdown: str, check_last: bool = True) -> None:
        for block in self.parser.feed(markdown):
            self._apply(block)
        if self.parser.pending.strip():
            first = self.parser.pending.strip().split("\n", 1)[0][:40]
            self._issue(
                "block_open",
                f"`{first}` is still open at the end of this call; your next add_slides call completes it.",
                "Close every code block within one call.",
            )
        elif check_last and self.w.slides and not self.pending_old:
            # Slides arrive whole, so the last one is done: check it now, not a call later when
            # the next heading arrives. Checking it again then reports only what is new.
            self._check_finished_slide(self.w.slides[-1])
            self.check_runs()

    def _current(self) -> Slide:
        if not self.w.slides:
            self._new_slide(Block("heading", 0, self.w.title, attrs={"classes": []}), implicit=True)
        return self.w.slides[-1]

    def _new_slide(self, b: Block, implicit: bool = False) -> Slide:
        if self.w.slides and not implicit:
            self._check_finished_slide(self.w.slides[-1])
        attrs = dict(b.attrs)
        classes = attrs.pop("classes", [])
        sid = attrs.pop("id", "") or slugify(b.text)
        sid = self._unique_id(sid)
        icon = attrs.pop("icon", "")
        s = Slide(id=sid, title=inline_plain(b.text), classes=classes, attrs=attrs, line=b.line)
        s.kind = "title" if not self.w.slides else "text"
        if "chapter" in classes and self.w.slides:
            s.kind = "chapter"
            s.visual = {"n": 1 + sum(1 for x in self.w.slides if x.kind == "chapter")}
        self.w.slides.append(s)
        if icon and self._use_icon(s, icon, b.line):
            s.icon = icon
        self.changed.add(s.id)
        return s

    def _use_icon(self, s: Slide, name: str, line: int) -> bool:
        """Copy a named icon into the slide; an unknown name is reported with names close to it."""
        if not self._known_icon(name, line):
            return False
        s.icons[name] = icons.get(name)
        return True

    def _known_icon(self, name: str, line: int) -> bool:
        if icons.get(name) is not None:
            return True
        close = icons.suggest(name)
        self._issue(
            "icon_unknown",
            f"Line {line}: there is no icon `{name}`" + (f"; close: {', '.join(close)}." if close else "."),
            "Use a Lucide icon name, such as database, server, cloud, lock, zap, clock or users.",
            line=line,
        )
        return False

    def _unique_id(self, sid: str) -> str:
        taken = {s.id for s in self.w.slides}
        if sid not in taken:
            return sid
        n = 2
        while f"{sid}-{n}" in taken:
            n += 1
        return f"{sid}-{n}"

    def _apply(self, b: Block) -> None:
        if b.kind == "front":
            from .model import FRAMES
            from .speech.base import VOICES

            a = b.attrs
            if a.get("title"):
                self.w.title = a["title"][:120]
            if a.get("description"):
                from .store import MAX_DESCRIPTION

                self.w.description = " ".join(a["description"].split())[:MAX_DESCRIPTION]
            if a.get("frame"):
                if a["frame"] in FRAMES:
                    self.w.frame = a["frame"]
                else:
                    self._issue(
                        "front_matter",
                        f"Front matter frame `{a['frame']}` is not one of {', '.join(FRAMES)}; kept {self.w.frame}.",
                        line=b.line,
                    )
            if a.get("voice"):
                if a["voice"] in VOICES:
                    self.w.voice = a["voice"]
                else:
                    self._issue("front_matter", f"Front matter voice `{a['voice']}` is unknown; kept {self.w.voice}.", line=b.line)
            if a.get("theme"):
                from .themes import THEMES

                if a["theme"] in THEMES:
                    self.w.theme = a["theme"]
                else:
                    self._issue(
                        "front_matter",
                        f"Front matter theme `{a['theme']}` is not one of {', '.join(THEMES)}; kept {self.w.theme}.",
                        line=b.line,
                    )
            if a.get("speed"):
                try:
                    self.w.speed = max(SPEED_RANGE[0], min(SPEED_RANGE[1], float(a["speed"])))
                except ValueError:
                    self._issue("front_matter", f"Front matter speed `{a['speed']}` is not a number; kept {self.w.speed}.", line=b.line)
            return
        if b.kind == "heading":
            self._flush_old()
            if "questions" in b.attrs.get("classes", []):
                self.questions_mode = True
                return
            self.questions_mode = False
            self._new_slide(b)
            return
        if self.questions_mode:
            if b.kind == "list":
                self.w.questions += [inline_plain(t) for t in b.items if t.strip()]
            elif b.kind == "para":
                self.w.questions.append(inline_plain(b.text))
            return
        if b.kind == "footnote":
            self.w.notes[b.attrs["id"]] = b.text
            return
        if b.kind == "para":
            self._flush_old()
            self._add_say(self._current(), b.text)
            return
        if b.kind == "pause":
            s = self._current()
            say = Say(id=self._say_id(s), pause=min(5.0, b.attrs["seconds"]), status="ready")
            s.says.append(say)
            self.changed.add(s.id)
            return
        if b.kind == "quote":
            s = self._current()
            s.shown = (s.shown + "\n" + b.text).strip() if s.shown else b.text
            for ln in b.text.split("\n"):  # `> :zap: takeaway` leads with an icon
                name, _ = icons.split_leading(ln.strip())
                if name and not self._use_icon(s, name, b.line):
                    s.shown = s.shown.replace(f":{name}:", "", 1)
            self.changed.add(s.id)
            return
        if b.kind == "list" and self._spoken_item(b):
            self._flush_old()
            self._add_say(self._current(), b.items[0])
            return
        self.visual_line = b.line
        self._add_visual(b)

    def _spoken_item(self, b: Block) -> bool:
        """A paragraph that happens to start with `1. ` or `- ` after the slide's visual: as a
        list it would be a second visual, so it is narration."""
        s = self.w.slides[-1] if self.w.slides else None
        return bool(s and s.visual and s.kind != "list" and len(b.items) == 1 and len(b.items[0].split()) >= 4)

    # -- visuals ------------------------------------------------------------------------

    def _slide_for_visual(self, kind: str) -> Slide:
        s = self._current()
        if (
            s.visual
            and "pair" in s.classes
            and not s.pair
            and s.kind not in ("title", "chapter")
            and not (kind == "list" and s.kind == "list")
        ):
            self.into_pair = True  # the second visual of a pair: `_set` puts it beside the first
            return s
        if s.visual and not (kind == "list" and s.kind == "list"):
            self._issue(
                "extra_visual",
                f"Slide `{s.id}` already shows a {s.kind}; the next visual got its own slide.",
                "Give each visual its own heading.",
                slide=s.id,
            )
            s = self._new_slide(Block("heading", self.visual_line, s.title, attrs={"classes": list(s.classes)}))
        return s

    def _add_visual(self, b: Block) -> None:
        self.into_pair = False
        k = kinds.for_lang(b.lang) if b.kind == "code" else None
        if k:
            return self._add_kind(k, b)
        if b.kind == "code":
            flags = b.attrs.get("flags", [])
            if b.lang == "mermaid":
                return self._set(self._slide_for_visual("diagram"), "diagram", self._diagram(b))
            if b.lang in ("math", "latex", "tex"):
                return self._set(self._slide_for_visual("math"), "math", {"latex": b.text.strip()})
            if "old" in flags or "before" in flags:
                ex = self._code_excerpt(b)
                if ex is not None:
                    self.pending_old = (b, ex)
                return
            if ("new" in flags or "after" in flags) and self.pending_old:
                ob, oex = self.pending_old
                self.pending_old = None
                nex = self._code_excerpt(b)
                if nex is None:
                    return
                lang = b.lang or ob.lang
                vis = {
                    "lang": lang,
                    "old": {"code": oex.code, "start": oex.start, "title": ob.attrs.get("title") or self._name(ob)},
                    "new": {"code": nex.code, "start": nex.start, "title": b.attrs.get("title") or self._name(b)},
                }
                self._check_diff(vis)
                return self._set(self._slide_for_visual("diff"), "diff", vis)
            if b.lang == "diff" or (b.attrs.get("rev") and b.attrs.get("file")):
                return self._diff_block(b)
            ex = self._code_excerpt(b)
            if ex is None:
                return
            vis = {"lang": b.lang, "code": ex.code, "start": ex.start, "title": b.attrs.get("title") or self._name(b)}
            if b.attrs.get("file"):
                vis["file"] = b.attrs["file"]
            if "scroll" in flags:
                vis["scroll"] = True
            cur = self.w.slides[-1] if self.w.slides else None
            if "step" in flags:
                step = {k: vis[k] for k in ("code", "start", "title")}
                if cur is not None and cur.kind == "code" and cur.visual.get("steps"):
                    cur.visual["steps"].append(step)
                    self._check_code(vis)
                    return self._set(cur, "code", cur.visual)
                vis["steps"] = [step]
            self._check_code(vis)
            return self._set(self._slide_for_visual("code"), "code", vis)
        if b.kind == "math":
            return self._set(self._slide_for_visual("math"), "math", {"latex": b.text.strip()})
        if b.kind == "table":
            t = parse_table(b.text)
            ids = []
            for i, row in enumerate(t["rows"]):
                # A row's {#id} belongs at the end of its last cell, but is taken from any cell.
                rid = None
                for k, cell in enumerate(row):
                    m = re.search(r"\s*\{#([\w-]+)\}\s*$", cell)
                    if m:
                        row[k] = cell[: m.start()]
                        rid = rid or m.group(1)
                ids.append(rid or f"row-{i + 1}")
            t["ids"] = ids
            return self._set(self._slide_for_visual("table"), "table", t)
        if b.kind == "list":
            s = self._slide_for_visual("list")
            items = []
            for t in b.items:
                if not t.strip():
                    continue
                name, rest = icons.split_leading(t.strip())  # `- :database: **Postgres**: ...`
                it = parse_item(rest)
                if name and self._known_icon(name, b.line):
                    it["icon"] = name
                items.append(it)
            if s.kind == "list" and not self.into_pair:
                items = s.visual["items"] + items
            used = set()
            quiz = "quiz" in s.classes
            for i, it in enumerate(items):
                it["title"], it["body"] = inline_plain(it["title"]), inline_plain(it["body"])
                if not it["id"]:
                    # A quiz's choices are lettered on screen, and pointed at by letter: #a, #b.
                    it["id"] = chr(ord("a") + i) if quiz and i < 26 else slugify(it["title"] or it["body"])[:24] or f"item-{i + 1}"
                while it["id"] in used:
                    it["id"] += "-2"
                used.add(it["id"])
            style = (
                "quiz"
                if quiz
                else "timeline"
                if "timeline" in s.classes
                else "cards"
                if "cards" in s.classes
                else "stack"
                if "stack" in s.classes
                else "checklist"
                if ("checklist" in s.classes or any("check" in it for it in items))
                else "steps"
                if ("steps" in s.classes or b.attrs.get("ordered"))
                else "bullets"
            )
            return self._set(s, "list", {"style": style, "items": items})
        if b.kind == "image":
            return self._image(b)

    def _add_kind(self, k: kinds.Kind, b: Block) -> None:
        body = b.text
        lead: dict[str, str] = {}
        if k.name == "stat":  # `:clock: p95 latency: 420 ms`: the icon leads the label
            lines = []
            for ln in body.split("\n"):
                name, rest = icons.split_leading(ln.strip())
                if name:
                    lead[rest.split(":", 1)[0].strip()] = name
                lines.append(rest if name else ln)
            body = "\n".join(lines)
        if k.reads_file and b.attrs.get("file"):
            if self._no_disk(b, f"`{k.name} file={b.attrs['file']}`"):
                return
            try:
                body = sources.read_text(sources.resolve(self.root, b.attrs["file"]))
            except sources.SourceError as e:
                self._issue("source_error", f"Line {b.line}: {e}", line=b.line)
                return
            if b.text.strip():
                self._issue(
                    "code_body_ignored",
                    f"The {k.name} block for {b.attrs['file']} had a body; showed the file instead.",
                    "Leave the body empty with file=.",
                    line=b.line,
                )
        vis, problems = k.parse(body, b.attrs)
        for it in vis.get("items", []) if lead else []:
            if it.get("label") in lead and self._known_icon(lead[it["label"]], b.line):
                it["icon"] = lead[it["label"]]
        for i, p in enumerate(problems):  # the format once per block
            self._issue(k.error_code, f"Line {b.line}: {p}.", k.fix() if i == 0 else k.hint, line=b.line)
        if k.lines:
            self._check_code(vis)
        for code, message, fix in k.check(vis, b.line, self.limits):
            self._issue(code, message, fix, line=b.line)
        if k.steps and "step" in b.attrs.get("flags", []):
            # Versions of one visual: each step block after the first joins the slide's list.
            version = {key: vis[key] for key in k.steps if key in vis}
            cur = self.w.slides[-1] if self.w.slides else None
            if cur is not None and cur.kind == k.name and cur.visual.get("steps"):
                cur.visual["steps"].append(version)
                return self._set(cur, k.name, cur.visual)
            vis["steps"] = [version]
        self._set(self._slide_for_visual(k.name), k.name, vis)

    def _set(self, s: Slide, kind: str, visual: dict) -> None:
        if self.into_pair and s.visual and not s.pair:
            self.into_pair = False
            s.pair = {"kind": kind, "visual": visual}
        else:
            s.kind, s.visual = kind, visual
        for it in visual.get("items", []):  # icons leading list items and numbers
            if isinstance(it, dict) and it.get("icon"):
                s.icons[it["icon"]] = icons.get(it["icon"])
        self.changed.add(s.id)
        for say in s.says:  # cues written before the visual arrived
            self._resolve_cues(s, say, reresolve=True)

    def _name(self, b: Block) -> str:
        f = b.attrs.get("file", "")
        return Path(f).name if f else ""

    def _code_excerpt(self, b: Block) -> sources.Excerpt | None:
        if b.attrs.get("file"):
            if self._no_disk(b, f"`file={b.attrs['file']}`"):
                return None
            try:
                ex, _ = sources.read_excerpt(self.root, b.attrs["file"], b.attrs.get("lines"))
                if b.text.strip():
                    self._issue(
                        "code_body_ignored",
                        f"Code block for {b.attrs['file']} had a body; showed the file instead.",
                        "Leave the body empty with file=.",
                        line=b.line,
                    )
                if not self.w.sha:
                    self.w.sha = sources.head_sha(self.root)
                return ex
            except sources.SourceError as e:
                self._issue("source_error", f"Line {b.line}: {e}", line=b.line)
                return sources.Excerpt(f"# {e}", 1, 1)
        start = 1
        if b.attrs.get("start", "").isdigit():
            start = int(b.attrs["start"])
        text = b.text
        return sources.Excerpt(text, start, len(text.split("\n")))

    def _diff_block(self, b: Block) -> None:
        if b.attrs.get("file") and b.attrs.get("rev"):
            if self._no_disk(b, f"`diff file={b.attrs['file']} rev={b.attrs['rev']}`"):
                return
            try:
                d = sources.git_diff(self.root, b.attrs["file"], b.attrs["rev"], b.attrs.get("lines"))
                if not self.w.sha:
                    self.w.sha = sources.head_sha(self.root)
            except sources.SourceError as e:
                self._issue("source_error", f"Line {b.line}: {e}", line=b.line)
                return
            if generated(b.attrs["file"]):
                self._issue(
                    "generated_file",
                    f"Line {b.line}: {b.attrs['file']} is a lockfile or generated file.",
                    "Skip it unless it is the point of the change.",
                    line=b.line,
                )
            lang = b.attrs.get("lang") or _lang_from_name(b.attrs["file"])
            vis = {
                "lang": lang,
                "old": {"code": d.old.code, "start": d.old.start, "title": d.old_title},
                "new": {"code": d.new.code, "start": d.new.start, "title": d.new_title},
                "file": b.attrs["file"],
                "rev": b.attrs["rev"],
            }
        else:
            old, new = sources.split_unified(b.text)
            hunks = sources.hunk_count(b.text)
            if hunks > 1:
                self._issue(
                    "diff_hunks",
                    f"Line {b.line}: this diff has {hunks} hunks; line numbers follow the first one.",
                    "Show one hunk per slide, or use file= with rev=.",
                    line=b.line,
                )
            lang = b.attrs.get("lang", "")
            vis = {
                "lang": lang,
                "old": {"code": old.code, "start": old.start, "title": b.attrs.get("title", "")},
                "new": {"code": new.code, "start": new.start, "title": b.attrs.get("title", "")},
            }
        self._check_diff(vis)
        self._set(self._slide_for_visual("diff"), "diff", vis)

    def _flush_old(self) -> None:
        if self.pending_old:
            ob, ex = self.pending_old
            self.pending_old = None
            self._issue(
                "old_without_new",
                f"Line {ob.line}: an `old` code block had no `new` block after it; showed it as plain code.",
                line=ob.line,
            )
            vis = {"lang": ob.lang, "code": ex.code, "start": ex.start, "title": ob.attrs.get("title") or self._name(ob)}
            self._set(self._slide_for_visual("code"), "code", vis)

    def _diagram(self, b: Block) -> dict:
        src = b.text.strip("\n")
        first = next((ln.strip() for ln in src.split("\n") if ln.strip() and not ln.strip().startswith("%%")), "")
        kind = first.split()[0].lower() if first else ""
        nodes, labels, order = parse_mermaid(src)
        vis = {"source": src, "type": kind, "nodes": nodes, "labels": labels, "order": order}
        alt = self.w.slides[-1].attrs.get("alt") if self.w.slides else None
        if alt:
            vis["alt"] = alt
        if len(nodes) > self.limits["nodes"]:
            self._issue(
                "diagram_too_big",
                f"The diagram on line {b.line} has {len(nodes)} nodes; this frame reads well up to {self.limits['nodes']}.",
                "Split it across slides.",
                line=b.line,
            )
        return vis

    def _image(self, b: Block) -> None:
        src, alt = b.attrs["src"], b.attrs.get("alt", "")
        if self.hosted:  # no files from disk, and nothing fetched from elsewhere (img-src blocks it too)
            from .hosted import say

            self._issue(
                "image_error",
                say("no_images", "Line {line}: images are off on this server.", line=b.line),
                say("no_images_fix", ""),
                line=b.line,
            )
            return
        s = self._slide_for_visual("image")
        if re.match(r"^https?://", src):
            vis = {"src": src, "alt": alt}
            if b.attrs.get("frame") in IMAGE_FRAMES:
                vis["frame"] = b.attrs["frame"]
                if b.attrs.get("url"):
                    vis["url"] = b.attrs["url"][:120]
            return self._set(s, "image", vis)
        try:
            path = sources.resolve(self.root, src)
        except sources.SourceError as e:
            self._issue("image_error", f"Line {b.line}: image {e}", line=b.line)
            return
        if path.suffix.lower() not in IMAGE_TYPES:
            self._issue("image_error", f"Line {b.line}: {path.name} is not an image type the player shows.", line=b.line)
            return
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        name = f"{len(list(self.assets_dir.iterdir())) + 1}-{re.sub(r'[^\w.-]', '_', path.name)}"
        shutil.copyfile(path, self.assets_dir / name)
        if not alt:
            self._issue("image_no_alt", f"Image {path.name} has no alt text.", "Add a short description in the brackets.", line=b.line)
        vis = {"src": f"assets/{name}", "alt": alt}
        frame = b.attrs.get("frame", "")
        if frame:
            if frame in IMAGE_FRAMES:
                vis["frame"] = frame
                if b.attrs.get("url"):
                    vis["url"] = b.attrs["url"][:120]
            else:
                self._issue(
                    "image_error",
                    f"Line {b.line}: frame `{frame}` is not one of {', '.join(IMAGE_FRAMES)}; showed the image unframed.",
                    line=b.line,
                )
        self._set(s, "image", vis)

    # -- narration ----------------------------------------------------------------------

    def _say_id(self, s: Slide) -> str:
        return f"{s.id}.{len(s.says) + 1}.{s.rev}"

    def _add_say(self, s: Slide, src: str) -> None:
        n = parse_narration(src)
        if not n.display:
            return
        say = Say(id=self._say_id(s), text=n.display, code=n.code, overrides=[list(x) for x in n.say], notes=n.notes)
        say._cues = n.cues  # raw cues, resolved below
        self._resolve_cues(s, say)
        s.says.append(say)
        self.changed.add(s.id)
        self.new_says.append((s, say))


# -- helpers ------------------------------------------------------------------------------


def _lang_from_name(name: str) -> str:
    ext = Path(name).suffix.lower().lstrip(".")
    return {
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "tsx": "typescript",
        "jsx": "javascript",
        "rs": "rust",
        "rb": "ruby",
        "md": "markdown",
        "yml": "yaml",
        "sh": "bash",
        "zsh": "bash",
        "kt": "kotlin",
        "cs": "csharp",
        "h": "c",
        "hpp": "cpp",
        "cc": "cpp",
    }.get(ext, ext)
