"""What the MCP tools do, independent of transport.

`LocalService` works on the store inside the daemon. `RemoteService` forwards the same calls
to a running daemon over its token-protected RPC route; the stdio shim uses it, so every
MCP client shares one daemon and one loaded voice model.
"""

from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .issues import as_dict
from .store import UNKNOWN_HANDLE, Store, check_questions, narration_seconds

log = logging.getLogger("showntell")
_browser_warned = False


def _dicts(items) -> list[dict]:
    return [as_dict(i) for i in items]


class LocalService:
    def __init__(self, store: Store, layout_check: bool = False) -> None:
        self.store = store
        self.layout_check = layout_check  # draw every slide headless at finish (the daemon turns this on)

    def speech_status(self) -> dict:
        n = self.store.narrator
        return {"ready": n.ready, "state": n.status.state, "describe": n.status.describe()}

    async def start(
        self,
        title: str,
        frame: str,
        voice: str,
        speed: float,
        project_dir: str | None,
        theme: str = "",
        description: str = "",
        user=None,
    ) -> dict:
        root = None
        if self.store.hosted:
            from .hosted import say

            if project_dir:
                raise ValueError(say("no_project_dir", "This server cannot read project_dir {given}.", given=project_dir))
            self._check_room(user)
        elif project_dir:
            p = Path(project_dir).expanduser()
            if not p.is_dir():
                raise ValueError(f"project_dir {project_dir} is not a folder")
            root = str(p.resolve())
        s = self.store.create(title, frame, voice, speed, root, theme, description, owner=user)
        return {"handle": s.handle, "url": s.url, "frame": s.w.frame, "root": root, "speech": self.speech_status()}

    def _check_room(self, user) -> None:
        """Hosted: room for one more walkthrough under the user's limits."""
        from .hosted import WRITING_STALE_S, say

        lim = self.store.hosted.limits
        mine = [r for r in self.store.list(10**9, user) if r["role"] == "yours"]
        recent = time.time() - WRITING_STALE_S
        writing = [r for r in mine if r["status"] != "finished" and r["updated"] > recent]
        if len(writing) >= lim.writing:
            handles = ", ".join(f"{r['title']} ({r['handle']})" for r in writing)
            raise ValueError(
                say("writing_limit", "You have {n} walkthroughs in progress, the limit: {handles}.", n=len(writing), handles=handles)
            )
        if len(mine) >= lim.walkthroughs:
            raise ValueError(say("stored_limit", "You have {n} walkthroughs, the limit.", n=len(mine)))
        used = sum(self.store.size(r["id"]) for r in mine)
        if used >= lim.storage_gb * 1e9:
            raise ValueError(
                say("storage_limit", "Your walkthroughs use {gb:.1f} GB, the limit is {limit:g} GB.", gb=used / 1e9, limit=lim.storage_gb)
            )

    async def add(self, handle: str, markdown: str, user=None) -> dict:
        s = self.store.by_handle(handle, user)
        r = s.add(markdown)
        r["issues"] = _dicts(r["issues"] + s.take_late_issues())
        says = [x for sl in s.w.slides for x in sl.says if x.text]
        return {
            **r,
            "url": s.url,
            "frame": s.w.frame,
            "link_shared": s.w.link_shared,
            "total_slides": len(s.w.slides),
            "total_says": len(says),
            "voiced": sum(1 for x in says if x.status == "ready"),
            "narration_s": round(narration_seconds(s.w), 1),
            "player_issues": _dicts(s.take_player_issues()),
            "slide_ids": [sl.id for sl in s.w.slides],
            "speech": self.speech_status(),
        }

    async def playable(self, handle: str, timeout: float, give_up: bool = False, user=None) -> dict:
        """Wait up to `timeout` for the opening narration. Marks the link shared once it is,
        or at once with `give_up`, so later calls stop waiting."""
        s = self.store.by_handle(handle, user)
        status = await s.wait_playable(timeout) if timeout > 0 else (s.first_say().status if s.first_say() else "empty")
        if give_up and status == "pending":
            s.w.link_shared = True
        out: dict[str, Any] = {"status": status, "url": s.url, "speech": self.speech_status(), "frame": s.w.frame}
        if status == "error":
            first = s.first_say()
            out["error"] = first.error if first else ""
        if status in ("ready", "error"):
            out["guide"] = not s.w.guide_sent
            first_time = not s.w.link_shared or not s.w.guide_sent
            s.w.link_shared = True
            s.w.guide_sent = True
            if first_time:
                s._changed(set(), meta=True)
                if status == "ready":
                    self.store.on_playable(s)
        return out

    async def revise(self, handle: str, slide: str, markdown: str, user=None) -> dict:
        s = self.store.by_handle(handle, user)
        r = s.revise(slide, markdown)
        if s.w.slides and s.w.slides[0].id == slide:  # the poster shows the title slide; finish draws a new one
            (self.store.walk_dir(s.w.id) / "poster.png").unlink(missing_ok=True)
        r["issues"] = _dicts(r["issues"] + s.take_late_issues())
        return {**r, "url": s.url, "player_issues": _dicts(s.take_player_issues())}

    async def finish(self, handle: str, questions: list[str] | None, check: bool = True, user=None) -> dict:
        """Close the walkthrough. `check=False` skips drawing the slides headless, for callers
        that only wait for the audio (exporting a Markdown file)."""
        import base64

        from .checks import alike_runs, look
        from .export import slide_starts, timeline_total

        s = self.store.by_handle(handle, user)
        check_questions(questions or [])  # before drawing, so a refusal is quick
        layout = self.layout_check and check
        runs = [[x.id for x in run] for run in alike_runs(s.w.slides)]
        shots: dict[str, bytes] = {}
        want = [sid for run in runs for sid in run][:ALIKE_SHOTS_MAX]
        unchecked = await self._check_layout(s, want, shots) if layout else None
        if s.deleted:  # while the slides were drawn
            raise ValueError(UNKNOWN_HANDLE)
        issues = s.finish(questions, unchecked=unchecked) + s.take_late_issues()
        says = [x for sl in s.w.slides for x in sl.says if x.text]
        starts = slide_starts(s.w)
        return {
            "outline": [{"id": sl.id, "title": sl.title, "start_s": round(starts[sl.id], 1), "look": look(sl)} for sl in s.w.slides],
            "url": s.url,
            "slides": len(s.w.slides),
            "duration_s": round(timeline_total(s.w), 1),
            "pending": sum(1 for x in says if x.status == "pending"),
            "issues": _dicts(issues),
            "player_issues": _dicts(s.take_player_issues()),
            "questions": s.w.questions,
            # Runs of slides that look alike, with a small picture of each when the check drew them.
            "alike": [{"slides": run, "look": look(next(x for x in s.w.slides if x.id == run[0]))} for run in runs],
            "thumbs": [{"id": sid, "png": base64.b64encode(shots[sid]).decode()} for sid in want if sid in shots],
        }

    async def list(self, user=None) -> list[dict]:
        return self.store.list(20, user)

    async def share(
        self, handle: str, add: list[str] | None = None, remove: list[str] | None = None, company: bool | None = None, user=None
    ) -> dict:
        """Hosted: change who else may view a walkthrough. Takes effect on their next request,
        and ends their open live updates when access goes."""
        from .hosted import say

        if not self.store.hosted:
            raise ValueError(
                say("share_local", "Sharing is for hosted servers; on this machine the link works for anyone who can reach it.")
            )
        return self.share_session(self.store.by_handle(handle, user), add, remove, company)

    def share_session(self, s, add: list[str] | None = None, remove: list[str] | None = None, company: bool | None = None) -> dict:
        """The checks and the change behind both `share_walkthrough` and the page's Share control;
        the caller has already made sure the user owns `s`. All or nothing."""
        from .hosted import say

        allowed = self.store.hosted.allowed_domains
        shares, seen = list(s.w.shares), set(s.w.shares)
        for raw in add or []:
            email = (raw or "").strip().lower()
            shown = raw if len(raw or "") <= 80 else raw[:77] + "..."
            if len(email) > MAX_EMAIL or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                raise ValueError(say("share_bad_email", "{email} is not an email address.", email=shown))
            if allowed and email.rsplit("@", 1)[1] not in allowed:
                raise ValueError(say("share_domain", "{email} is outside {domains}.", email=shown, domains=", ".join(allowed)))
            if email not in seen and email != (s.w.owner.get("email") or "").lower():
                shares.append(email)
                seen.add(email)
        gone = {(x or "").strip().lower() for x in remove or []}
        shares = [x for x in shares if x not in gone]
        if len(shares) > MAX_SHARES and len(shares) > len(s.w.shares):
            raise ValueError(
                say("share_too_many", "That would share it with {n} people; the limit is {limit}.", n=len(shares), limit=MAX_SHARES)
            )
        s.w.shares = shares
        if company is not None:
            s.w.company = bool(company)
        s._changed(set(), meta=True)  # wakes open event streams, which check access again
        s.save_now()
        return {"url": s.url, "shares": list(s.w.shares), "company": s.w.company}

    async def _check_layout(self, s, shoot: list[str] = (), shots: dict | None = None) -> tuple[str, str]:
        """Draw every slide fully revealed in headless Chromium, so the player's render and layout
        checks report before the agent stops; pictures of the slides in `shoot` go into `shots`.
        Returns ('', '') when it ran, or the finding to raise and why: ('no_browser', reason) when
        Playwright or its Chromium is missing, ('not_checked', reason) when drawing failed."""
        import asyncio

        from .capture import Capturer, CaptureUnavailable

        global _browser_warned
        ends = _end_times(s.w)
        if not ends:
            return "", ""
        limit = self.store.hosted.limits.check_s if self.store.hosted else LAYOUT_CHECK_S

        async def run() -> None:
            async with self._browser(), Capturer(**self._capture_target(s, limit), short=360) as c:
                for i, (sid, t) in enumerate(list(ends.items())[:LAYOUT_CHECK_MAX]):
                    await c.seek(t, measure=True)
                    if i == 0:  # the settled title slide: the poster for the gallery and link previews
                        (self.store.walk_dir(s.w.id) / "poster.png").write_bytes(await c.shot())
                    if sid in shoot and shots is not None:
                        shots[sid] = await c.shot()
                for f in await c.page.evaluate("window.__showntell.findings || []"):
                    s.report(f)

        try:
            await asyncio.wait_for(run(), limit)
        except CaptureUnavailable as e:
            if not _browser_warned:  # once per daemon, so the log says why finish never draws
                _browser_warned = True
                log.warning("finish_walkthrough does not draw the slides: %s", e)
            return "no_browser", e.reason
        except TimeoutError:
            return "not_checked", f"drawing the slides took over {limit:g} s"
        except Exception as e:  # a broken page or browser must not fail finish
            return "not_checked", f"the headless player failed: {str(e).splitlines()[0][:200]}"
        return "", ""

    def _browser(self, timeout: float | None = None):
        """Hosted: one of the server's few Chromium slots, held for the whole job; waits at most `timeout` s for it."""
        import contextlib

        return self.store.hosted.chromium.short(timeout) if self.store.hosted else contextlib.nullcontext()

    def _capture_target(self, s, ttl: float) -> dict:
        """Where headless Chromium opens a walkthrough. Hosted: this process's own address with a
        short session for the owner, and nothing loaded from any other origin."""
        h = self.store.hosted
        if not h:
            return {"url": s.url}
        from .identity import SESSION_COOKIE, User

        session = h.capture_session(User.from_owner(s.w.owner), ttl + 30)
        return {
            "url": f"{h.internal_url}/w/{s.w.id}/",
            "cookie": (SESSION_COOKIE, session.id),
            "only_origin": True,
            "sandbox": h.chromium_sandbox,
        }

    async def preview(self, handle: str, slides: list[str] | None = None, short: int = 360, user=None) -> dict:
        """Pictures of slides as the viewer sees them once fully revealed, for the agent to check."""
        import asyncio
        import base64

        from .capture import CaptureUnavailable, capture_frames_async

        s = self.store.by_handle(handle, user)
        ends = _end_times(s.w)
        order = list(ends)
        want = [x for x in (slides or order) if x in ends]
        unknown = [x for x in (slides or []) if x not in ends]
        if len(want) > PREVIEW_MAX:
            want = want[-PREVIEW_MAX:] if not slides else want[:PREVIEW_MAX]
        times = [ends[sid] for sid in want]
        limit = self.store.hosted.limits.check_s if self.store.hosted else None
        try:
            if not times:
                pngs = []
            else:
                async with self._browser(limit):
                    t = self._capture_target(s, limit or 0)
                    shoot = capture_frames_async(t.pop("url"), times, short=short, measure=True, **t)
                    pngs = await (asyncio.wait_for(shoot, limit) if limit else shoot)
        except CaptureUnavailable as e:
            raise ValueError(str(e)) from e
        except TimeoutError:
            raise ValueError(f"drawing the slides took over {limit:g} s") from None
        return {
            "url": s.url,
            "slides": [
                {
                    "id": sid,
                    "title": next(x.title for x in s.w.slides if x.id == sid),
                    "at_s": round(t, 1),
                    "png": base64.b64encode(png).decode(),
                }
                for sid, t, png in zip(want, times, pngs, strict=True)
            ],
            "unknown": unknown,
            "player_issues": _dicts(s.take_player_issues()),
        }

    async def export(
        self, handle: str, path: str = "", format: str = "mp4", max_mb: float = 0, wait_s: float | None = None, user=None
    ) -> dict:
        """Write the walkthrough to a file the agent can attach: an MP4, or one HTML page that plays
        offline. A relative or empty `path` is in the project folder. Waits for the narration first.
        Hosted: the file stays on the server, in the walkthrough's folder, for its owner to download,
        and each user and walkthrough has one export under way at a time."""
        h = self.store.hosted
        if not h:
            return await self._export(handle, path, format, max_mb, wait_s, user)
        s = self.store.by_handle(handle, user)
        async with h.chromium.exporting(((s.w.owner.get("iss"), s.w.owner.get("sub")), s.w.id)) as job:
            return await self._export(handle, path, format, max_mb, wait_s, user, job=job)

    async def _export(self, handle: str, path: str, format: str, max_mb: float, wait_s: float | None, user, job=None) -> dict:
        import asyncio
        import functools

        from .export import export_html, timeline_total
        from .video import render_mp4

        wait_s = EXPORT_WAIT_S if wait_s is None else wait_s
        s = self.store.by_handle(handle, user)
        if format not in ("mp4", "html"):
            raise ValueError("format must be mp4 or html")
        h = self.store.hosted
        if h:
            from .hosted import say

            if path:
                raise ValueError(say("no_export_path", "This server cannot save to {given}.", given=path))
            out = self.store.walk_dir(s.w.id) / "exports" / f"{_slug(s.w.title)}.{format}"
            out.parent.mkdir(parents=True, exist_ok=True)
        else:
            out = export_path(s.w.title, s.w.root, path, format)
        says = [x for sl in s.w.slides for x in sl.says if x.text]
        if not says:
            raise ValueError("nothing to export yet: the walkthrough has no narration")
        t0 = time.monotonic()
        while not s.deleted and any(x.status == "pending" for x in says) and time.monotonic() - t0 < wait_s:
            await asyncio.sleep(0.5)
        if s.deleted:  # while waiting for the voice
            raise ValueError(UNKNOWN_HANDLE)
        pending = sum(1 for x in says if x.status == "pending")
        if pending:
            raise ValueError(f"{pending} paragraph(s) are still being voiced after {wait_s:.0f} s; try again shortly")
        silent = sum(1 for x in says if x.status != "ready")
        s.save_now()  # the exporters read the saved state
        data_dir = self.store.data_dir
        # Hosted: one browser, no requests beyond the export's own server, and a time limit.
        hosted_kw = {"workers": 1, "only_origin": True, "sandbox": h.chromium_sandbox} if h else {}
        try:
            if format == "mp4":
                render = functools.partial(render_mp4, data_dir, s.w.id, out, quiet=True, max_bytes=int(max_mb * 1e6), **hosted_kw)
                r = await (job.run(render, h.limits.export_min * 60) if h else asyncio.to_thread(render))
                shrunk, errors = r["shrunk"], r["errors"]
            else:
                await asyncio.to_thread(export_html, data_dir, s.w.id, out)
                shrunk, errors = "", []
                size = out.stat().st_size
                if max_mb and size > max_mb * 1e6:
                    out.unlink()
                    raise SystemExit(f"the page is {size / 1e6:.1f} MB, over the {max_mb:g} MB limit; export an MP4 instead")
        except SystemExit as e:  # the exporters stop the command line this way
            raise ValueError(str(e)) from None
        except TimeoutError:
            raise ValueError(f"the export took over {h.limits.export_min:g} minutes") from None
        finally:
            if s.deleted and h:  # deleted while rendering: leave nothing of it behind
                shutil.rmtree(self.store.walk_dir(s.w.id), ignore_errors=True)
        if s.deleted:
            raise ValueError(UNKNOWN_HANDLE)
        extra = {"download": f"{s.store.base_url}/w/{s.w.id}/exports/{out.name}"} if h else {}
        return {
            **extra,
            "path": str(out),
            "format": format,
            "bytes": out.stat().st_size,
            "duration_s": round(timeline_total(s.w), 1),
            "shrunk": shrunk,
            "silent": silent,
            "errors": errors,
            "seconds": round(time.monotonic() - t0, 1),
        }

    async def source(self, wid: str, user=None) -> str:
        s = self.store.owned(wid, user)
        if s is None:
            raise ValueError(f"no walkthrough {wid}")
        return s.source


class RemoteService:
    """Forwards calls to the daemon's `/api/rpc/{method}` route."""

    def __init__(self, base: str, token: str) -> None:
        import httpx

        self.base = base.rstrip("/")
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(70.0, connect=5.0), headers={"Authorization": f"Bearer {token}"})

    async def _call(self, method: str, http_timeout: float | None = None, **kw) -> Any:
        extra = {"timeout": http_timeout} if http_timeout else {}
        r = await self.client.post(f"{self.base}/api/rpc/{method}", json=kw, **extra)
        try:
            data = r.json()
        except ValueError:  # a plain-text refusal, or not the daemon at all
            data = {"error": f"daemon error {r.status_code}: {r.text.strip()[:200]}"}
        if r.status_code != 200:
            raise ValueError(data.get("error", f"daemon error {r.status_code}"))
        return data["result"]

    async def start(self, title, frame, voice, speed, project_dir, theme="", description=""):
        return await self._call(
            "start", title=title, frame=frame, voice=voice, speed=speed, project_dir=project_dir, theme=theme, description=description
        )

    async def add(self, handle, markdown):
        return await self._call("add", handle=handle, markdown=markdown)

    async def playable(self, handle, timeout, give_up=False):
        return await self._call("playable", handle=handle, timeout=timeout, give_up=give_up)

    async def revise(self, handle, slide, markdown):
        return await self._call("revise", handle=handle, slide=slide, markdown=markdown)

    async def finish(self, handle, questions, check=True):
        if check:  # a daemon from before `check` still takes the call
            return await self._call("finish", handle=handle, questions=questions)
        return await self._call("finish", handle=handle, questions=questions, check=False)

    async def list(self):
        return await self._call("list")

    async def source(self, wid):
        return await self._call("source", wid=wid)

    async def preview(self, handle, slides=None, short=360):
        return await self._call("preview", handle=handle, slides=slides, short=short)

    async def export(self, handle, path="", format="mp4", max_mb=0):
        # Rendering takes a while: about a tenth of the video's length, plus waiting for the voice.
        return await self._call("export", http_timeout=EXPORT_WAIT_S + 900, handle=handle, path=path, format=format, max_mb=max_mb)


RPC_METHODS = {"start", "add", "playable", "revise", "finish", "list", "source", "preview", "export"}
EXPORT_WAIT_S = 300.0  # for the narration to finish voicing before an export
MAX_SHARES = 100  # people one walkthrough is shared with, by address; beyond that, the whole company
MAX_EMAIL = 254  # characters in an address (RFC 5321)
PREVIEW_MAX = 12
LAYOUT_CHECK_MAX = 60  # slides drawn by the finish-time layout check
LAYOUT_CHECK_S = 25.0
ALIKE_SHOTS_MAX = 6  # pictures of alike slides in the finish reply


def _end_times(w) -> dict[str, float]:
    """Slide id -> a moment just before the slide ends, when everything on it is revealed."""
    from .export import timeline, timeline_total

    starts: dict[str, float] = {}
    for t, slide, _ in timeline(w):
        starts.setdefault(slide.id, t)
    order = [sl.id for sl in w.slides if sl.id in starts]
    total = timeline_total(w)
    out = {}
    for i, sid in enumerate(order):
        end = starts[order[i + 1]] if i + 1 < len(order) else total
        out[sid] = max(starts[sid], end - 0.35)
    return out


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60].strip("-") or "walkthrough"


def export_path(title: str, root: str | None, path: str, format: str) -> Path:
    """Where an export goes: `path` (relative ones inside the project folder), else a file named
    after the title in the project folder. The suffix must match the format."""
    if path:
        p = Path(path).expanduser()
        if not p.is_absolute():
            if not root:
                raise ValueError("a relative path needs a project folder; pass an absolute path")
            p = Path(root) / p
    else:
        if not root:
            raise ValueError("no project folder to save in; pass an absolute path")
        p = Path(root) / f"{_slug(title)}.{format}"
    p = p.resolve()
    want = (".mp4",) if format == "mp4" else (".html", ".htm")
    if p.suffix.lower() not in want:
        raise ValueError(f"path must end in {want[0]} for format {format}")
    if p.is_dir():
        raise ValueError(f"{p} is a folder")
    if not p.parent.is_dir():
        raise ValueError(f"folder {p.parent} does not exist")
    return p


def mmss(seconds: float | None) -> str:
    s = int(round(seconds or 0))
    return f"{s // 60}:{s % 60:02d}"


def now() -> float:
    return time.time()
