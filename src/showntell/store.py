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
from dataclasses import asdict
from pathlib import Path

from . import access, themes
from .build import SPEED_RANGE, Builder
from .checks import cues_bunched
from .format import FOOTNOTE_DEF_RE, Parser
from .issues import Issue, player_issue
from .model import FRAMES, STATE_VERSION, Say, Slide, Walkthrough, public_slide, slide_from_state
from .speech.base import resolve_voice
from .speech.narrator import Narrator, cue_time
from .themes import THEMES

log = logging.getLogger("showntell.store")

MAX_SOURCE_BYTES = 1_000_000
MAX_SLIDES = 80


HEADING_LINE = re.compile(r"^ {0,3}#{1,6}\s")


def _heading_for(s: Slide) -> str:
    """A heading line that builds slide `s` again: its title, id, classes and attributes."""
    attrs = [f"#{s.id}"] + [f".{c}" for c in s.classes]
    attrs += [f'{k}="{v}"' for k, v in s.attrs.items() if isinstance(v, str)]
    return f"## {s.title} {{{' '.join(attrs)}}}"


def _heading_with_id(line: str, sid: str) -> str:
    """A heading line carrying `{#sid}`: replacing the id it had, or added to its attributes."""
    m = re.search(r"\{([^{}]*)\}\s*$", line)
    if not m:
        return f"{line.rstrip()} {{#{sid}}}"
    attrs = [a for a in m.group(1).split() if not a.startswith("#")]
    return f"{line[: m.start()].rstrip()} {{{' '.join([f'#{sid}', *attrs])}}}"


PARAGRAPH_GAP = 0.3  # matches the player's timeline
STILL_S = 20.0  # longest stretch of narration with nothing changing on screen
CUE_GAP_S = 1.5  # two cues closer than this: the first is gone before it can be read
MAX_DESCRIPTION = 200  # characters
MAX_QUESTIONS, MAX_QUESTION = 4, 300  # end questions kept, characters each
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
        cited = dict(self.w.cited)
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
        self._changed(b.changed, meta=self.w.cited != cited)
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
        if new.id != old.id:  # the slide keeps its id; write it into the heading so the source builds it too
            first, _, rest = text.partition("\n")
            text = _heading_with_id(first, old.id) + ("\n" + rest if rest else "")
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
        self._take_notes(scratch)  # every check has passed: from here on the walkthrough changes
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
            self._changed({new.id} | self._renumber_chapters(), meta=True)
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
        walkthrough now is. Source lines the old Markdown held for citations that other slides
        still make stay, after it. Returns the line the slide starts on."""
        lines = self.source.split("\n")
        blocks = Parser().feed(self.source)
        start = old.line if old.line > 0 else next((x.line for x in blocks if x.kind != "front"), len(lines))
        after = [x.line for x in blocks if x.kind == "heading" and x.line > start]
        after += [sl.line for sl in self.w.slides if sl.line > start]
        end = min(after, default=len(lines))  # the next slide or section, or the end
        new = text.rstrip("\n").split("\n")
        new += [""] + self._still_cited(lines[start - 1 : end - 1], new, [*lines[: start - 1], *lines[end - 1 :]])
        if new[-1] != "":
            new.append("")
        lines[start - 1 : end - 1] = new
        delta = len(new) - (end - start)
        for sl in [*self.w.slides, *self.staged.values()]:
            if sl.line > start:
                sl.line += delta
        self.builder.parser.lines_seen += delta
        self.source = "\n".join(lines)
        self._sync_notes()
        return start

    # -- inserting, removing and moving slides -------------------------------------------

    def _check_settled(self, what: str) -> None:
        """Slides can move only between whole slides: not while a code block or a diff's old side
        waits for the next add_slides call to complete it."""
        if self.builder.parser.pending.strip() or self.builder.pending_old:
            raise ValueError(f"a code block is still open; complete it with add_slides before you {what}")

    def _pieces(self) -> list[tuple[str | None, list[str]]]:
        """The source cut into pieces, in source order: (slide id, its lines) for each slide, and
        (None, lines) for anything between or around them (front matter, a questions section).
        A slide that spilled from the one before (its second visual) has no heading of its own;
        it gets one here, so its piece stays its own wherever it moves."""
        lines = self.source.split("\n")
        blocks = Parser().feed(self.source)
        first = next((x.line for x in blocks if x.kind != "front"), len(lines))
        starts = [(sl.line if sl.line > 0 else first, sl) for sl in self.w.slides]
        if any(b[0] <= a[0] for a, b in zip(starts, starts[1:], strict=False)) or any(not 1 <= a <= len(lines) for a, _ in starts):
            raise ValueError("the saved Markdown no longer lines up with the slides, so slides cannot be moved; start a new walkthrough")
        bounds = sorted({x.line for x in blocks if x.kind == "heading"} | {a for a, _ in starts} | {len(lines)})
        out: list[tuple[str | None, list[str]]] = []
        at = 1
        for k, (a, sl) in enumerate(starts):
            if a > at:
                out.append((None, lines[at - 1 : a - 1]))
            end = next((b for b in bounds if b > a), len(lines) + 1)  # the next slide or section, or the end
            piece = lines[a - 1 : end - 1]
            if k > 0 and not (piece and HEADING_LINE.match(piece[0])):
                piece = [_heading_for(sl), "", *piece]
            out.append((sl.id, piece))
            at = end
        out.append((None, lines[at - 1 :]))
        return out

    def _set_source(self, pieces: list[tuple[str | None, list[str]]]) -> None:
        """Write the pieces back as the source, each slide's ending in a blank line, and move every
        slide's line to where it now starts."""
        before = self.source.count("\n")
        at, line_of, kept = 1, {}, []
        for sid, ls in pieces:
            if sid is not None:
                line_of[sid] = at
                if ls and ls[-1] != "":
                    ls = [*ls, ""]
            kept.append(ls)
            at += len(ls)
        self.source = "\n".join(ln for ls in kept for ln in ls)
        for sl in [*self.w.slides, *self.staged.values()]:
            if sl.id in line_of:
                sl.line = line_of[sl.id]
        self.builder.parser.lines_seen += self.source.count("\n") - before
        self._sync_notes()

    def _cited(self) -> set[str]:
        return {n for sl in [*self.w.slides, *self.staged.values()] for x in sl.says for n in x.notes}

    def _still_cited(self, lost: list[str], *kept: list[str]) -> list[str]:
        """Of the citation source lines in `lost`, those for citations a slide still makes and
        that the `kept` lines do not define again."""
        defined = {m.group(1) for ls in kept for ln in ls if (m := FOOTNOTE_DEF_RE.match(ln))}
        cited = self._cited()
        return [ln for ln in lost if (m := FOOTNOTE_DEF_RE.match(ln)) and m.group(1) in cited and m.group(1) not in defined]

    def _sync_notes(self) -> None:
        """Keep only the sources the source Markdown still defines, so state and Markdown agree."""
        defined = {b.attrs["id"] for b in Parser().feed(self.source) if b.kind == "footnote"}
        for nid in [n for n in self.w.notes if n not in defined]:
            del self.w.notes[nid]
        for nid in [n for n in self.w.cited if n not in defined]:
            del self.w.cited[nid]

    def _renumber_chapters(self) -> set[str]:
        """Chapters by their `.chapter` class and numbered in order, after slides moved, came or went.
        Returns the ids of slides whose kind or number changed."""
        changed, n = set(), 0
        for i, s in enumerate(self.w.slides):
            if i == 0 or "chapter" not in s.classes:
                continue
            n += 1
            if s.kind != "chapter" or s.visual.get("n") != n:
                s.kind, s.visual = "chapter", {**s.visual, "n": n}
                changed.add(s.id)
        return changed

    def insert(self, after: str, markdown: str) -> dict:
        """Put new slides right after slide `after`: built, checked and voiced as revise_slide
        builds a slide, with ids kept apart from every slide there is."""
        lim = self.store.hosted.limits if self.store.hosted else None
        if lim:
            self._check_size(markdown, lim)
        self._check_settled("insert slides")
        idx = next((i for i, s in enumerate(self.w.slides) if s.id == after), None)
        if idx is None:
            raise ValueError(f"no slide `{after}` to insert after. Slides: {', '.join(s.id for s in self.w.slides)}")
        text = markdown.strip()
        if not re.match(r"^#{1,6}\s", text):
            raise ValueError("inserted slides start with a heading: `## Title`")
        if len(self.source) + len(text) > MAX_SOURCE_BYTES:
            raise ValueError("this walkthrough is over 1 MB of Markdown; start a new one")
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
        if b.parser.pending.strip():
            raise ValueError("a code block in the inserted Markdown is never closed; send whole slides")
        if scratch.questions:
            raise ValueError("inserted Markdown holds slides only; give end questions to finish_walkthrough")
        new = list(scratch.slides)
        if not new:
            raise ValueError("the Markdown has no slide in it")
        if len(self.w.slides) + len(new) > MAX_SLIDES:
            raise ValueError(f"that would make {len(self.w.slides) + len(new)} slides; keep it under {MAX_SLIDES}")
        if lim:
            spoken = sum(x.duration or len(x.text.split()) / 2.7 for sl in [*self.w.slides, *new] for x in sl.says if x.text)
            over = self._over_limits(lim, len(self.w.slides) + len(new), spoken)
            if over:
                raise ValueError(over)
        taken = {s.id for s in self.w.slides} | set(self.staged)
        sources = text.split("\n")
        heads = [s.line for s in new] + [len(sources) + 1]
        chunks, renamed = [], {}
        for k, s in enumerate(new):
            chunk = sources[s.line - 1 : heads[k + 1] - 1]
            sid = s.id
            n = 2
            while sid in taken:  # an id in use: the next free `-2`, `-3`, written into the heading
                sid, n = f"{s.id}-{n}", n + 1
            if sid != s.id:
                renamed[s.id] = sid
            taken.add(sid)
            s.id, s.rev = sid, self.w.retired.get(sid, 0) + 1
            if not (chunk and HEADING_LINE.match(chunk[0])):  # spilled from the slide before: give it a heading
                chunk = [_heading_for(s), "", *chunk]
            elif sid in renamed.values():
                chunk[0] = _heading_with_id(chunk[0], sid)
            if s.kind == "title":
                s.kind = "text"
            for i, say in enumerate(s.says):
                say.id = f"{s.id}.{i + 1}.{s.rev}"
            b._check_finished_slide(s)
            chunks.append(chunk)
        # Every check has passed: from here on the walkthrough changes.
        self._take_notes(scratch)
        pieces = self._pieces()
        at = next(i for i, (sid, _) in enumerate(pieces) if sid == after) + 1
        pieces[at:at] = [(s.id, c) for s, c in zip(new, chunks, strict=True)]
        self.w.slides[idx + 1 : idx + 1] = new
        self._set_source(pieces)
        order = sum(len(x.says) for x in self.w.slides[: idx + 1])
        voiced = 0
        for s in new:
            for i, say in enumerate(s.says):
                if not say.pause:
                    voiced += self._voice(s, say, order=order + i)
            order += len(s.says)
        self._changed({s.id for s in new} | self._renumber_chapters(), meta=True)
        issues = list(dict.fromkeys(b.issues))
        return {
            "slide_ids": [s.id for s in new],
            "renamed": renamed,
            "says": sum(len(s.says) for s in new),
            "revoiced": voiced,
            "issues": issues,
        }

    def arrange(self, order: list[str] | None, remove: list[str] | None) -> dict:
        """Remove slides and put the rest in a new order. The title slide stays first, and every
        slide kept must be named in `order` when it is given, so nothing goes missing unasked."""
        self._check_settled("move or remove slides")
        ids = [s.id for s in self.w.slides]
        remove = list(dict.fromkeys(remove or []))
        unknown = [x for x in [*remove, *(order or [])] if x not in ids]
        if unknown:
            raise ValueError(f"no slide {', '.join(f'`{x}`' for x in unknown)}. Slides: {', '.join(ids)}")
        if not remove and order is None:
            raise ValueError("pass `order`, `remove`, or both")
        if ids and ids[0] in remove:
            raise ValueError(f"the title slide `{ids[0]}` cannot be removed; revise it instead")
        kept = [x for x in ids if x not in remove]
        if order is not None:
            if len(set(order)) != len(order):
                raise ValueError("`order` names a slide twice")
            both = [x for x in order if x in remove]
            missing = [x for x in kept if x not in order]
            if both:
                raise ValueError(f"{', '.join(f'`{x}`' for x in both)} is both in `order` and in `remove`")
            if missing:
                raise ValueError(
                    f"`order` leaves out {', '.join(f'`{x}`' for x in missing)}; list every slide you keep, or add it to `remove`"
                )
            if order and kept and order[0] != kept[0]:
                raise ValueError(f"the title slide `{kept[0]}` stays first")
            kept = list(order)
        if kept == ids:
            return {"removed": [], "moved": False, "order": ids}
        by_id = {s.id: s for s in self.w.slides}
        pieces = self._pieces()
        slots = iter(kept)  # each slide's place in the source takes the next slide of the new order
        text = {sid: ls for sid, ls in pieces if sid is not None}
        out = []
        for sid, ls in pieces:
            if sid is None:
                out.append((None, ls))
            elif sid not in remove:
                nxt = next(slots)
                out.append((nxt, list(text[nxt])))
        for sid in remove:
            staged = self.staged.pop(sid, None)
            self.w.retired[sid] = max(self.w.retired.get(sid, 0), by_id[sid].rev, staged.rev if staged else 0)
        self.w.slides = [by_id[x] for x in kept]
        # Source lines of removed slides for citations kept slides still make go on with the last slide.
        lost = [ln for sid in remove for ln in text[sid]]
        keep = self._still_cited(lost, *(ls for _, ls in out))
        if keep:
            last = max(i for i, (sid, _) in enumerate(out) if sid is not None)
            sid, ls = out[last]
            out[last] = (sid, [*ls, *([""] if ls and ls[-1] != "" else []), *keep, ""])
        self._set_source(out)
        self.builder.issues = []
        self._changed(self._renumber_chapters(), meta=True)  # the player takes the new order from the delta
        return {"removed": remove, "moved": [x for x in ids if x in kept] != kept, "order": kept}

    def _take_notes(self, scratch: Walkthrough) -> None:
        """Sources defined in a revised or inserted slide's Markdown join the walkthrough's."""
        for nid, text in scratch.notes.items():
            self.w.notes[nid] = text
            if nid in scratch.cited:
                self.w.cited[nid] = scratch.cited[nid]
            else:
                self.w.cited.pop(nid, None)

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
        ('', '') when it ran, or (finding, reason) when it could not run: 'no_browser' for a browser
        not ready, off or missing, 'not_checked' for a drawing that failed or timed out."""
        b = self.builder
        b.issues = []
        b.check_all()
        self._check_cue_spacing()
        if b.parser.pending.strip():
            b._issue("block_dropped", "A code block was never closed; it was dropped.", "Close it and send the slide with revise_slide.")
            b.parser.pending = ""
        code, why = unchecked or ("", "")
        if code == "no_browser":
            from .browser import PENDING, STATE

            meanwhile = "Open the link; what the player finds arrives with your next call to this walkthrough."
            b._issue(
                "no_browser",
                f"The slides were not drawn: {why}. This reply has no layout findings, no pictures of alike slides and no poster.",
                f"Once it is ready, preview_slides draws any slide. {meanwhile}"
                if STATE.state in PENDING
                else meanwhile
                if STATE.state == "off"
                else f"Tell the user that `showntell doctor` says why and how to fix it. {meanwhile}",
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
            self.w.questions = check_questions(questions)
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

    def _check_cue_spacing(self) -> None:
        """At finish, on voiced slides: cues that fire too close together for the first to be read,
        timed by the voice's words and compared across the slide's paragraphs. A step-reveal slide
        with every cue in its first paragraph already has `build_cues_bunched`."""
        for slide in self.w.slides:
            key = f"close:{slide.id}:{slide.rev}"
            if key in self.reported or cues_bunched(slide) or any(x.text and x.status != "ready" for x in slide.says):
                continue
            t, marks = 0.0, []
            for x in slide.says:
                if x.pause:
                    t += x.pause
                elif x.text:
                    marks += [(t + c["t"], c) for c in x.cues if c.get("t") is not None and not c.get("inferred")]
                    t += x.duration + PARAGRAPH_GAP
            marks.sort(key=lambda m: m[0])
            close = [
                (b - a, x, y)
                for (a, x), (b, y) in zip(marks, marks[1:], strict=False)
                if b - a < CUE_GAP_S and x.get("target") != y.get("target")
            ]
            if not close:
                continue
            self.reported.add(key)
            gap, x, y = min(close, key=lambda m: m[0])
            more = f" ({len(close)} such pairs on the slide)" if len(close) > 1 else ""
            self.builder._issue(
                "cues_too_close",
                f"Slide `{slide.id}`: [{x.get('label')}](#{x.get('target')}) and [{y.get('label')}](#{y.get('target')}) "
                f"fire {gap:.1f} s apart{more}, so the first is gone before it can be read.",
                f"Leave about {CUE_GAP_S:g} s of narration between cues: give each its own clause, or drop one. Apply it with revise_slide.",
                slide=slide.id,
                line=slide.line,
            )

    def take_late_issues(self) -> list[Issue]:
        out, self.late_issues = self.late_issues, []
        return out

    def recall_findings(self) -> None:
        """After loading from disk: what was reported lives only in memory, so check the slides
        already there again, quietly, and the next call reports only what is new."""
        b = self.builder
        for sl in self.w.slides:
            b._check_finished_slide(sl)
        b.check_runs()
        b.issues = []
        if narration_seconds(self.w) > TARGET_S:
            self.reported.add("too_long")

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

    def close_streams(self) -> None:
        """End every open event stream: the server is stopping, and an open stream would hold it."""
        for q in self.subscribers:
            q.put_nowait(None)

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
            **({"cited": self.w.cited} if meta else {}),  # quoted lines can be long: only when they may have changed
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
        state = self.w.to_state()
        if self.staged:  # source.md already has them: a restart voices them and swaps them in
            state["staged"] = [asdict(x) for x in self.staged.values()]
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(d / "state.json")
        (d / "source.md").write_text(self.source, encoding="utf-8")
        self.store._remember(self.w.id, state, (d / "state.json").stat().st_mtime_ns)

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


def check_questions(questions: list[str]) -> list[str]:
    """The end questions to keep: the first four that are not blank, each refused when too long."""
    kept = [q.strip() for q in questions if q and q.strip()][:MAX_QUESTIONS]
    for i, q in enumerate(kept, 1):
        if len(q) > MAX_QUESTION:
            raise ValueError(
                f"question {i} is {len(q)} characters; keep each under {MAX_QUESTION}. Nothing was finished; send them shorter."
            )
    return kept


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
        # What listings and limit checks need of each saved walkthrough, so they don't parse every
        # state.json each time: id -> (its mtime when read, summary). Saves here update it; a file
        # changed from outside (prune, another process) is read again when its mtime moves.
        self._index: dict[str, tuple[int, dict]] = {}
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
        voice = resolve_voice(voice or self.default_voice)
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
            d = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("unreadable state for %s", wid)
            return None
        if d.get("version", 0) > STATE_VERSION:
            log.warning("%s was saved by a newer showntell", wid)
        w = Walkthrough.from_state(d)
        s = Session(self, w)
        src = self.walk_dir(wid) / "source.md"
        s.source = src.read_text(encoding="utf-8") if src.is_file() else ""
        # Rebuild the parser position so later appends continue cleanly.
        s.builder.parser = Parser()
        s.builder.parser.started = True
        s.builder.parser.lines_seen = s.source.count("\n")
        s.recall_findings()
        self.sessions[wid] = s
        s.staged = {sl.id: sl for sl in map(slide_from_state, d.get("staged") or [])}  # revisions waiting for audio
        for slide in [*w.slides, *s.staged.values()]:
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

    def _remember(self, wid: str, data: dict, mtime: int) -> dict:
        from .export import timeline_total

        row = {
            "id": data["id"],
            "key": data.get("key", ""),
            "title": data.get("title", ""),
            "description": data.get("description", ""),
            "created": data.get("created", 0),
            "updated": data.get("updated", 0),
            "status": data.get("status", ""),
            "frame": data.get("frame", "landscape"),
            "slides": len(data.get("slides", [])),
            "duration": timeline_total(Walkthrough.from_state(data)),
            "owner": data.get("owner") or {},
            "shares": frozenset(data.get("shares") or ()),
            "company": bool(data.get("company")),
            "audio": frozenset(x["key"] for sl in data.get("slides", []) for x in sl.get("says", []) if x.get("key")),
        }
        self._index[wid] = (mtime, row)
        return row

    def summary(self, wid: str) -> dict | None:
        """A saved walkthrough's listing fields, owner, shares and audio keys, or None."""
        st = self.walk_dir(wid) / "state.json"
        try:
            mtime = st.stat().st_mtime_ns
        except OSError:
            self._index.pop(wid, None)
            return None
        hit = self._index.get(wid)
        if hit and hit[0] == mtime:
            return hit[1]
        try:
            return self._remember(wid, json.loads(st.read_text(encoding="utf-8")), mtime)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def list(self, limit: int = 50, user=None) -> list[dict]:
        """Recent walkthroughs, newest change first. In hosted mode only those `user` may see,
        each with its `role` ("yours" or "shared"); shared ones carry no handle."""
        out = []
        for d in self.walk_dir("x").parent.iterdir():
            data = self.summary(d.name)
            if data is None:
                continue
            role = access.role(data, user) if self.hosted else ""
            if self.hosted and not role:
                continue
            out.append(
                {
                    "id": data["id"],
                    "title": data["title"],
                    "description": data["description"],
                    "poster": (d / "poster.png").is_file(),
                    "handle": f"{data['id']}.{data['key']}",
                    "created": data["created"],
                    "updated": data["updated"],
                    "status": data["status"],
                    "frame": data["frame"],
                    "slides": data["slides"],
                    "duration": data["duration"],
                    "url": f"{self.base_url}/w/{data['id']}{self.link_suffix}",
                }
            )
            if self.hosted:
                out[-1]["role"] = role
                out[-1]["owner"] = data["owner"].get("email", "")
                if role != "yours":
                    del out[-1]["handle"]
        out.sort(key=lambda r: r["updated"], reverse=True)
        return out[:limit]

    def size(self, wid: str) -> int:
        """Bytes a walkthrough keeps: its folder (state, exports) and the audio it plays."""
        d = self.walk_dir(wid)
        total = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) if d.is_dir() else 0
        data = self.summary(wid)
        for key in data["audio"] if data else ():
            f = self.narrator.audio_path(key)
            total += f.stat().st_size if f.is_file() else 0
        return total

    def forget(self, wid: str) -> None:
        """Drop a deleted walkthrough's session for good, wherever it is still held."""
        s = self.sessions.pop(wid, None)
        self._index.pop(wid, None)
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
