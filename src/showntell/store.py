"""Walkthrough sessions: creation, appending, revising, speech, persistence and change events.

All mutation happens on the asyncio loop. Speech results arrive on the speech thread and
are handed to the loop with `call_soon_threadsafe`.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import secrets
import shutil
import time
import webbrowser
from pathlib import Path

from . import access, themes
from .build import SPEED_RANGE, Builder
from .format import Parser
from .issues import Issue, player_issue
from .model import FRAMES, STATE_VERSION, Say, Slide, Walkthrough, public_slide
from .speech.base import VOICES
from .speech.narrator import Narrator, cue_time
from .themes import THEMES

log = logging.getLogger("showntell.store")

MAX_SOURCE_BYTES = 1_000_000
MAX_SLIDES = 80
PARAGRAPH_GAP = 0.3  # matches the player's timeline
STILL_S = 20.0  # longest stretch of narration with nothing changing on screen
MAX_DESCRIPTION = 200  # characters
TARGET_S = 240.0  # the guide aims for 2 to 4 minutes of narration
ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
UNKNOWN_HANDLE = "unknown handle; use the handle returned by start_walkthrough"


class Session:
    """A walkthrough loaded in memory, with its builder and live subscribers."""

    def __init__(self, store: Store, w: Walkthrough) -> None:
        self.store = store
        self.w = w
        self.builder = Builder(w, store.walk_dir(w.id) / "assets", hosted=store.hosted is not None)
        self.subscribers: set[asyncio.Queue] = set()
        self.player_issues: list[Issue] = []
        self.reported: set[str] = set()
        # Findings that need audio (timing), reported on the next call after the audio arrives.
        self.late_issues: list[Issue] = []
        self.viewed = False  # a player has connected at least once
        self.source = ""
        # Revised slides waiting for their audio, shown to viewers only once voiced (by slide id).
        self.staged: dict[str, Slide] = {}
        self._playable: asyncio.Event = asyncio.Event()
        self._save_handle: asyncio.TimerHandle | None = None
        # Set once deleted: speech callbacks, event streams and tool calls may still hold this
        # session, and none of them may write its folder back.
        self.deleted = False

    # -- writing ------------------------------------------------------------------------

    def add(self, markdown: str) -> dict:
        lim = self.store.hosted.limits if self.store.hosted else None
        if lim:
            self._check_size(markdown, lim)
        if self.w.status == "finished":
            self.w.status = "writing"  # adding after finishing reopens it
        if len(self.source) + len(markdown) > MAX_SOURCE_BYTES:
            raise ValueError("this walkthrough is over 1 MB of Markdown; start a new one")
        before = len(self.w.slides)
        # Hosted limits are checked on the result: keep the state to put back if it goes over.
        undo = copy.deepcopy({"w": self.w, "b": self.builder, "source": self.source}) if lim else None
        self.source += markdown if markdown.endswith("\n") else markdown + "\n"
        b = self.builder
        b.issues, b.new_says, b.changed = [], [], set()
        b.add(markdown)
        if len(self.w.slides) > MAX_SLIDES:
            b._issue(
                "too_many_slides",
                f"This walkthrough has {len(self.w.slides)} slides; keep it under {MAX_SLIDES}.",
                "Split it into a series.",
            )
        spoken = narration_seconds(self.w)
        if lim:
            over = self._over_limits(lim, len(self.w.slides), spoken)
            if over:
                self.w, self.builder, self.source = undo["w"], undo["b"], undo["source"]
                raise ValueError(over)
        if spoken > TARGET_S and "too_long" not in self.reported:
            self.reported.add("too_long")
            b._issue(
                "too_long",
                f"Narration is {int(spoken // 60)}:{int(spoken % 60):02d} so far; the guide aims for 1 to 2 minutes for a concept, 2 to 4 for a code change or review.",
                "Finish soon, or leave the rest for a second walkthrough.",
            )
        for slide, say in b.new_says:
            self._voice(slide, say)
        self._changed(b.changed)
        return {"slides": len(self.w.slides) - before, "says": len(b.new_says), "issues": list(b.issues)}

    def _check_size(self, markdown: str, lim) -> None:
        """Hosted: cap one call's Markdown before it is parsed."""
        size = len(markdown.encode())
        if size > lim.markdown_kb * 1024:
            from .hosted import say

            raise ValueError(
                say(
                    "markdown_too_big",
                    "This call carries {kb:.0f} KB of Markdown; the limit is {limit:g} KB.",
                    kb=size / 1024,
                    limit=lim.markdown_kb,
                )
            )

    def _over_limits(self, lim, slides: int, spoken: float) -> str:
        """Hosted: why the walkthrough would be past its slide or narration limit, or ''."""
        from .hosted import say

        if slides > lim.slides:
            return say("too_many_slides", "That would make {n} slides; the limit is {limit}.", n=slides, limit=lim.slides)
        if spoken > lim.narration_min * 60:
            return say(
                "narration_limit",
                "That would make about {minutes} of narration; the limit is {limit:g} minutes.",
                minutes=f"{int(spoken // 60)}:{int(spoken % 60):02d}",
                limit=lim.narration_min,
            )
        return ""

    def revise(self, slide_id: str, markdown: str) -> dict:
        lim = self.store.hosted.limits if self.store.hosted else None
        if lim:
            self._check_size(markdown, lim)
        idx = next((i for i, s in enumerate(self.w.slides) if s.id == slide_id), None)
        if idx is None:
            raise ValueError(f"no slide `{slide_id}`. Slides: {', '.join(s.id for s in self.w.slides)}")
        old = self.w.slides[idx]
        text = markdown.strip()
        if not re.match(r"^#{1,6}\s", text):
            attrs = [f"#{old.id}"] + [f".{c}" for c in old.classes]
            attrs += [f'{k}="{v}"' for k, v in old.attrs.items() if isinstance(v, str)]
            text = f"## {old.title} {{{' '.join(attrs)}}}\n\n{text}"
        scratch = Walkthrough(
            id=self.w.id,
            key="",
            report_key="",
            title=self.w.title,
            frame=self.w.frame,
            voice=self.w.voice,
            speed=self.w.speed,
            root=self.w.root,
            sha=self.w.sha,
        )
        b = Builder(scratch, self.store.walk_dir(self.w.id) / "assets", hosted=self.store.hosted is not None)
        b.add(text + "\n", check_last=False)
        issues = list(b.issues)
        if not scratch.slides:
            raise ValueError("the Markdown has no slide in it")
        if len(scratch.slides) > 1:
            issues.append(
                Issue(
                    "revise_kept_first",
                    f"revise_slide replaces one slide; kept the first of {len(scratch.slides)}.",
                    "Send one heading per call.",
                    slide=slide_id,
                )
            )
            text = "\n".join(text.split("\n")[: max(1, scratch.slides[1].line) - 1]).rstrip()
        new = scratch.slides[0]
        # Past any revision still staged too, so two revisions never share say ids and one's
        # audio can't fill in the other's.
        waiting = self.staged.get(old.id)
        new.id, new.rev, new.line = old.id, max(old.rev, waiting.rev if waiting else 0) + 1, old.line
        if new.kind == "title" and idx > 0:
            new.kind = "text"
        for i, say in enumerate(new.says):
            say.id = f"{new.id}.{i + 1}.{new.rev}"
        b._check_finished_slide(new)
        if lim:
            others = [x for x in self.w.slides if x is not old]
            spoken = sum(x.duration or len(x.text.split()) / 2.7 for sl in [*others, new] for x in sl.says if x.text)
            over = self._over_limits(lim, len(self.w.slides), spoken)
            if over:
                raise ValueError(over)
        issues = list(dict.fromkeys(issues + [x for x in b.issues if x not in issues]))
        # Someone may be watching: keep showing the voiced old version until the new one has
        # its audio, so a revision never makes the viewer wait.
        stage = bool(self.subscribers) and all(x.status == "ready" for x in old.says if x.text)
        if stage:
            self.staged[new.id] = new
        else:
            self.staged.pop(new.id, None)
            self.w.slides[idx] = new
        order = sum(len(x.says) for x in self.w.slides[:idx])
        voiced = 0
        for i, say in enumerate(new.says):
            if not say.pause:
                voiced += self._voice(new, say, order=order + i)
        new.line = self._rewrite_source(old, text)
        if not stage:
            self._changed({new.id})
        self._promote(new.id)
        return {
            "slide": new.id,
            "rev": new.rev,
            "says": len(new.says),
            "revoiced": voiced,
            "issues": issues,
            "staged": stage and new.id in self.staged,
        }

    def _rewrite_source(self, old: Slide, text: str) -> int:
        """Put a revised slide's Markdown where the old slide's was, so the source reads as the
        walkthrough now is. Returns the line the slide starts on."""
        lines = self.source.split("\n")
        blocks = Parser().feed(self.source)
        start = old.line if old.line > 0 else next((x.line for x in blocks if x.kind != "front"), len(lines))
        after = [x.line for x in blocks if x.kind == "heading" and x.line > start]
        after += [sl.line for sl in self.w.slides if sl.line > start]
        end = min(after, default=len(lines))  # the next slide or section, or the end
        new = text.rstrip("\n").split("\n") + [""]
        lines[start - 1 : end - 1] = new
        delta = len(new) - (end - start)
        for sl in [*self.w.slides, *self.staged.values()]:
            if sl.line > start:
                sl.line += delta
        self.builder.parser.lines_seen += delta
        self.source = "\n".join(lines)
        return start

    def _promote(self, slide_id: str) -> None:
        """Swap a staged revision in once all its narration has audio (or failed)."""
        new = self.staged.get(slide_id)
        if new is None or any(x.text and x.status == "pending" for x in new.says):
            return
        del self.staged[slide_id]
        idx = next((i for i, s in enumerate(self.w.slides) if s.id == slide_id), None)
        if idx is not None:
            self.w.slides[idx] = new
            self._changed({slide_id})

    def finish(self, questions: list[str] | None, unchecked: tuple[str, str] | None = None) -> list[str]:
        """Close the walkthrough. `unchecked` is None when no layout check was attempted at finish,
        ('', '') when it ran, or (finding, reason) when it could not run: 'no_browser' for a missing
        Playwright or Chromium, 'not_checked' for a drawing that failed or timed out."""
        b = self.builder
        b.issues = []
        b.check_all()
        if b.parser.pending.strip():
            b._issue("block_dropped", "A code block was never closed; it was dropped.", "Close it and send the slide with revise_slide.")
            b.parser.pending = ""
        code, why = unchecked or ("", "")
        if code == "no_browser":
            b._issue(
                "no_browser",
                f"The slides were not drawn: {why}. This reply has no layout findings, no pictures of alike slides and no poster.",
                "Tell the user that `showntell doctor` prints the two commands that add the browser. "
                "Meanwhile, open the link in a browser; what it finds arrives with your next call to this walkthrough.",
                slide="",
            )
        elif code:
            b._issue(
                "not_checked",
                f"Render and layout checks could not run: {why}.",
                "Open the link to run them in your browser; what it finds arrives with your next call to this walkthrough.",
                slide="",
            )
        elif unchecked is None and not self.viewed:
            b._issue(
                "not_checked",
                "No player has opened this walkthrough yet, so render and layout checks have not run.",
                "Open the link; problems the player finds arrive with your next call to this walkthrough.",
                slide="",
            )
        waiting = sum(1 for sl in self.w.slides for x in sl.says if x.text and x.status == "pending")
        if waiting:
            b._issue("not_checked", f"{waiting} paragraph(s) are still being voiced, so timing checks have not run on them.", slide="")
        if questions:
            self.w.questions = [q.strip() for q in questions if q and q.strip()][:4]
        self.w.status = "finished"
        self._changed(set(), meta=True)
        self.save_now()
        return list(b.issues)

    # -- speech -------------------------------------------------------------------------

    def _voice(self, slide: Slide, say: Say, order: int | None = None) -> int:
        """Request audio for a narration. Returns 1 if it needs voicing, 0 if cached."""
        if say.pause or not say.text:
            return 0
        narrator = self.store.narrator
        key, spoken = narrator.prepare(say.text, say.code, say.overrides, self.w.voice, self.w.speed)
        say.key = key
        say.status = "pending"
        if order is None:
            order = 0
            for s in self.w.slides:
                if s is slide:
                    order += s.says.index(say) if say in s.says else len(s.says)
                    break
                order += len(s.says)
        sid = say.id
        loop = self.store.loop
        hit = narrator.cached(key)

        def done(result: dict) -> None:
            if loop is None or (loop.is_running() and _on_loop(loop)):
                self._audio(slide.id, sid, result, key)
            else:
                loop.call_soon_threadsafe(self._audio, slide.id, sid, result, key)

        user = f"{self.w.owner.get('iss', '')} {self.w.owner.get('sub', '')}" if self.w.owner else ""
        narrator.request(key, spoken, self.w.voice, self.w.speed, order, done, user=user)
        return 0 if hit else 1

    def _audio(self, slide_id: str, say_id: str, result: dict, key: str = "") -> None:
        if self.deleted:
            return
        staged = self.staged.get(slide_id)
        slide = (
            staged if staged and any(x.id == say_id for x in staged.says) else next((s for s in self.w.slides if s.id == slide_id), None)
        )
        say = next((x for x in slide.says if x.id == say_id), None) if slide else None
        if say is None or (key and say.key != key):
            return  # revised away meanwhile
        if result.get("status") == "ready":
            say.status, say.duration, say.words, say.error = "ready", result["duration"], result["words"], ""
            for c in say.cues:
                c["t"] = round(cue_time(c["at"], say.words, say.duration, len(say.text)), 3)
        else:
            say.status, say.error = "error", result.get("error", "speech failed")
        if self.first_say() is say:
            self._playable.set()
        self._check_timing(slide)
        if slide is staged:
            self._promote(slide_id)
            return
        self._changed({slide_id})

    def _check_timing(self, slide: Slide) -> None:
        """Once a slide is voiced: flag long stretches where nothing on screen changes."""
        if slide.kind in ("title", "text") or any(x.text and x.status == "pending" for x in slide.says):
            return
        t, marks = 0.0, [0.0]
        for x in slide.says:
            if x.pause:
                t += x.pause
                continue
            if not x.text or x.status != "ready":
                continue
            marks += [t + c["t"] for c in x.cues if c.get("t") is not None]
            t += x.duration + PARAGRAPH_GAP
        marks = sorted(marks) + [t]
        gap, at = max(((b - a, a) for a, b in zip(marks, marks[1:], strict=False)), default=(0.0, 0.0))
        key = f"still:{slide.id}:{slide.rev}"
        if gap > STILL_S and key not in self.reported:
            self.reported.add(key)
            self.late_issues.append(
                Issue(
                    "still_slide",
                    f"Slide `{slide.id}` holds still for {gap:.0f} s (from {at:.0f} s into the slide) while the narration goes on.",
                    "Add a cue on what the narration is talking about, split the slide, or say less.",
                    slide=slide.id,
                    line=slide.line,
                )
            )

    def take_late_issues(self) -> list[Issue]:
        out, self.late_issues = self.late_issues, []
        return out

    def first_say(self) -> Say | None:
        for s in self.w.slides:
            for x in s.says:
                if x.text:
                    return x
        return None

    async def wait_playable(self, timeout: float) -> str:
        first = self.first_say()
        if first is None:
            return "empty"
        if first.status != "pending":
            return first.status
        try:
            await asyncio.wait_for(self._playable.wait(), timeout)
        except TimeoutError:
            return "pending"
        first = self.first_say()
        return first.status if first else "empty"

    # -- events and persistence -----------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(q)
        self.viewed = True
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def _changed(self, slide_ids: set[str], meta: bool = False) -> None:
        if self.deleted:
            return
        self.w.updated = time.time()
        for q in self.subscribers:
            q.put_nowait({"slides": set(slide_ids), "meta": meta})
        self._schedule_save()

    def delta(self, slide_ids: set[str], meta: bool) -> dict:
        """What changed, for the live player: changed slides plus the current order."""
        return {
            "type": "delta",
            "status": self.w.status,
            "title": self.w.title,
            "questions": self.w.questions,
            "notes": self.w.notes,
            "order": [s.id for s in self.w.slides],
            "slides": [public_slide(s) for s in self.w.slides if s.id in slide_ids],
        }

    def report(self, body: dict | str) -> None:
        issue = player_issue(body if isinstance(body, dict) else {"message": body})
        if issue is not None and issue not in self.reported:
            self.reported.add(issue)
            self.player_issues.append(issue)

    def take_player_issues(self) -> list[Issue]:
        out, self.player_issues = self.player_issues, []
        return out

    def _schedule_save(self) -> None:
        if self.deleted:
            return
        loop = self.store.loop
        if loop is None:
            self.save_now()
            return
        if self._save_handle is None:
            self._save_handle = loop.call_later(0.3, self.save_now)

    def save_now(self) -> None:
        self._save_handle = None
        if self.deleted:
            return
        d = self.store.walk_dir(self.w.id)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "state.json.part"
        tmp.write_text(json.dumps(self.w.to_state()))
        tmp.rename(d / "state.json")
        (d / "source.md").write_text(self.source)

    def close(self) -> None:
        """Deleted: cancel a pending save, write nothing more, and wake open event streams so they end."""
        self.deleted = True
        if self._save_handle is not None:
            self._save_handle.cancel()
            self._save_handle = None
        for q in self.subscribers:
            q.put_nowait({"slides": set(), "meta": True})

    @property
    def url(self) -> str:
        return f"{self.store.base_url}/w/{self.w.id}{self.store.link_suffix}"

    @property
    def handle(self) -> str:
        return f"{self.w.id}.{self.w.key}"


def narration_seconds(w: Walkthrough) -> float:
    """Spoken length so far: measured where voiced, else about 2.7 words a second."""
    return sum(x.duration or len(x.text.split()) / 2.7 for sl in w.slides for x in sl.says if x.text)


def _on_loop(loop: asyncio.AbstractEventLoop) -> bool:
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


class Store:
    def __init__(
        self,
        data_dir: Path,
        base_url: str,
        narrator: Narrator,
        default_voice: str = "af_heart",
        open_browser: bool = False,
        link_suffix: str = "",
        hosted=None,
    ) -> None:
        self.data_dir = data_dir
        self.base_url = base_url.rstrip("/")
        self.narrator = narrator
        self.default_voice = default_voice
        self.default_theme = themes.DEFAULT
        self.open_browser = open_browser
        self.link_suffix = link_suffix  # e.g. "?t=token" when serving beyond localhost
        self.hosted = hosted  # hosted.Hosted in hosted mode: owners, shares and limits apply
        self.loop: asyncio.AbstractEventLoop | None = None
        self.sessions: dict[str, Session] = {}
        (data_dir / "walkthroughs").mkdir(parents=True, exist_ok=True)

    def walk_dir(self, wid: str) -> Path:
        return self.data_dir / "walkthroughs" / wid

    def create(
        self,
        title: str,
        frame: str = "landscape",
        voice: str = "",
        speed: float = 1.0,
        root: str | None = None,
        theme: str = "",
        description: str = "",
        owner=None,
    ) -> Session:
        if frame not in FRAMES:
            raise ValueError(f"frame must be one of {', '.join(FRAMES)}")
        if theme and theme not in THEMES:
            raise ValueError(f"theme must be one of {', '.join(THEMES)}")
        project_theme, tokens, theme_problems = themes.project_theme(root)
        voice = voice or self.default_voice
        if voice not in VOICES:
            raise ValueError(f"unknown voice {voice!r}; choose one of {', '.join(VOICES)}")
        w = Walkthrough(
            id=secrets.token_urlsafe(16),
            key=secrets.token_urlsafe(16),
            report_key=secrets.token_urlsafe(12),
            title=(title or "Untitled walkthrough").strip()[:120],
            description=" ".join((description or "").split())[:MAX_DESCRIPTION],
            frame=frame,
            theme=theme or project_theme or self.default_theme,
            tokens=tokens,
            voice=voice,
            speed=max(SPEED_RANGE[0], min(SPEED_RANGE[1], float(speed or 1.0))),
            root=root,
            created=time.time(),
            updated=time.time(),
            owner=owner.as_owner() if owner is not None else {},
        )
        s = Session(self, w)
        s.late_issues += [Issue("theme_file", p, "Fix the file; the rest of it still applies.") for p in theme_problems]
        self.sessions[w.id] = s
        s.save_now()
        return s

    def get(self, wid: str) -> Session | None:
        if not ID_RE.match(wid or ""):
            return None
        if wid in self.sessions:
            return self.sessions[wid]
        path = self.walk_dir(wid) / "state.json"
        if not path.is_file():
            return None
        try:
            d = json.loads(path.read_text())
        except json.JSONDecodeError:
            log.warning("unreadable state for %s", wid)
            return None
        if d.get("version", 0) > STATE_VERSION:
            log.warning("%s was saved by a newer showntell", wid)
        w = Walkthrough.from_state(d)
        s = Session(self, w)
        src = self.walk_dir(wid) / "source.md"
        s.source = src.read_text() if src.is_file() else ""
        # Rebuild the parser position so later appends continue cleanly.
        s.builder.parser = Parser()
        s.builder.parser.started = True
        s.builder.parser.lines_seen = s.source.count("\n")
        self.sessions[wid] = s
        for slide in w.slides:
            for say in slide.says:
                if say.text and say.status != "ready":
                    s._voice(slide, say)
                elif say.text and say.status == "ready" and not self.narrator.audio_path(say.key).is_file():
                    s._voice(slide, say)
        if s.first_say() and s.first_say().status == "ready":
            s._playable.set()
        return s

    def by_handle(self, handle: str, user=None) -> Session:
        """The walkthrough a handle names. In hosted mode only its owner gets it; anyone else
        is told the handle is unknown, as for one that does not exist."""
        wid, _, key = (handle or "").strip().partition(".")
        s = self.get(wid)
        if s is None or not secrets.compare_digest(s.w.key, key) or (self.hosted and not access.owns(s.w, user)):
            raise ValueError(UNKNOWN_HANDLE)
        return s

    def viewable(self, wid: str, user=None) -> Session | None:
        """The walkthrough for a page or file request: in hosted mode only for its owner and
        those it is shared with."""
        s = self.get(wid)
        if s is None or (self.hosted and not access.can_view(s.w, user)):
            return None
        return s

    def owned(self, wid: str, user=None) -> Session | None:
        s = self.get(wid)
        if s is None or (self.hosted and not access.owns(s.w, user)):
            return None
        return s

    def list(self, limit: int = 50, user=None) -> list[dict]:
        """Recent walkthroughs, newest change first. In hosted mode only those `user` may see,
        each with its `role` ("yours" or "shared"); shared ones carry no handle."""
        from .export import timeline_total

        out = []
        for d in self.walk_dir("x").parent.iterdir():
            st = d / "state.json"
            if not st.is_file():
                continue
            try:
                data = json.loads(st.read_text())
            except json.JSONDecodeError:
                continue
            role = access.role(data, user) if self.hosted else ""
            if self.hosted and not role:
                continue
            out.append(
                {
                    "id": data["id"],
                    "title": data.get("title", ""),
                    "description": data.get("description", ""),
                    "poster": (d / "poster.png").is_file(),
                    "handle": f"{data['id']}.{data.get('key', '')}",
                    "created": data.get("created", 0),
                    "updated": data.get("updated", 0),
                    "status": data.get("status", ""),
                    "frame": data.get("frame", "landscape"),
                    "slides": len(data.get("slides", [])),
                    "duration": timeline_total(Walkthrough.from_state(data)),
                    "url": f"{self.base_url}/w/{data['id']}{self.link_suffix}",
                }
            )
            if self.hosted:
                out[-1]["role"] = role
                out[-1]["owner"] = (data.get("owner") or {}).get("email", "")
                if role != "yours":
                    del out[-1]["handle"]
        out.sort(key=lambda r: r["updated"], reverse=True)
        return out[:limit]

    def size(self, wid: str) -> int:
        """Bytes a walkthrough keeps: its folder (state, exports) and the audio it plays."""
        d = self.walk_dir(wid)
        total = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) if d.is_dir() else 0
        try:
            data = json.loads((d / "state.json").read_text())
        except (OSError, ValueError):
            return total
        for key in {x.get("key") for sl in data.get("slides", []) for x in sl.get("says", []) if x.get("key")}:
            f = self.narrator.audio_path(key)
            total += f.stat().st_size if f.is_file() else 0
        return total

    def forget(self, wid: str) -> None:
        """Drop a deleted walkthrough's session for good, wherever it is still held."""
        s = self.sessions.pop(wid, None)
        if s is not None:
            s.close()

    def delete(self, wid: str) -> bool:
        if not ID_RE.match(wid or ""):
            return False
        self.forget(wid)
        d = self.walk_dir(wid)
        if d.is_dir():
            shutil.rmtree(d)
            return True
        return False

    def on_playable(self, s: Session) -> None:
        if self.open_browser:
            try:
                webbrowser.open(s.url)
            except Exception:
                pass
