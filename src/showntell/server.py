"""MCP tools and the web side: player pages, live updates, audio, health and the shim's RPC."""

# No `from __future__ import annotations`: the tools' `Field(description=arg_text(...))` must be
# evaluated inside `create_mcp`, where `arg_text` reads the client's delivery mode and hosted text.

import asyncio
import html
import json
import logging
import secrets
import time
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError
from mcp.server.mcpserver.resolve import ListRoots, Resolve
from mcp_types import CallToolResult, ImageContent, ListRootsResult, TextContent, ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from . import __version__, access
from .build import LIMITS
from .hosted import user_of
from .issues import as_dict, line_text, summarize
from .service import RPC_METHODS, LocalService, mmss
from .speech.base import VOICES

log = logging.getLogger("showntell")
PKG = Path(__file__).parent
STATIC = PKG / "static"
TEXT = PKG / "text"
FIRST_WAIT_S = 45.0


DELIVER = ("link", "file")
FILE_MAX_MB = 16.0  # WhatsApp sends videos up to 16 MB; larger ones go as documents or not at all


@lru_cache(maxsize=4)
def texts(deliver: str = "link", hosted: bool = False) -> dict:
    """The agent-facing text. With `deliver="file"` the user cannot open links on this machine
    (the agent runs in a sandbox, or talks to the user through a chat app), so the files in
    text/file/ replace what tells the agent to share the link with what tells it to send a file.
    A hosted server reads nothing from disk, so text/hosted/ replaces what tells the agent to
    point at files, git and images."""

    def load(name: str, default, base: Path = TEXT):
        p = base / name
        if not p.is_file():
            return default
        return json.loads(p.read_text()) if name.endswith(".json") else p.read_text()

    out = {
        "instructions": load("instructions.md", "Narrated slide walkthroughs voiced locally."),
        "tools": load("tools.json", {}),
        "guide": load("guide.md", ""),
        "replies": load("replies.json", {}),
        "prompts": load("prompts.json", {}),
    }
    for folder in (["file"] if deliver == "file" else []) + (["hosted"] if hosted else []):
        over = TEXT / folder
        out["instructions"] = load("instructions.md", out["instructions"], over)
        out["replies"] = {**out["replies"], **load("replies.json", {}, over)}
        tools = {k: dict(v) for k, v in out["tools"].items()}
        for name, t in load("tools.json", {}, over).items():
            merged = tools.setdefault(name, {})
            merged.update({k: v for k, v in t.items() if k != "args"})
            merged["args"] = {**merged.get("args", {}), **t.get("args", {})}
        out["tools"] = tools
    return out


def tool_text(name: str, key: str = "description", default: str = "", deliver: str = "link", hosted: bool = False) -> str:
    return texts(deliver, hosted)["tools"].get(name, {}).get(key, default)


def arg_text(name: str, arg: str, deliver: str = "link", hosted: bool = False) -> str:
    return texts(deliver, hosted)["tools"].get(name, {}).get("args", {}).get(arg, "")


def reply(name: str, default: str, deliver: str = "link", hosted: bool = False, **kw: Any) -> str:
    template = texts(deliver, hosted)["replies"].get(name, default)
    try:
        return template.format(**kw)
    except (KeyError, IndexError):
        return default.format(**kw)


def guide(frame: str, deliver: str = "link", hosted: bool = False) -> str:
    lim = LIMITS.get(frame, LIMITS["landscape"])
    g = texts(deliver, hosted)["guide"]
    if hosted:  # what this server cannot do comes before the parts of the guide it overrides
        g = reply("guide_hosted", "", deliver, hosted) + "\n\n" + g
    for k, v in {
        "SEND_LINK": reply("guide_send_link", "", deliver, hosted),
        "TELL_USER": reply("guide_tell_user", "", deliver, hosted),
        "FRAME": frame,
        "LINES": lim["lines"],
        "WIDTH": lim["width"],
        "DIFF_LINES": lim["diff_lines"],
        "DIFF_WIDTH": lim["diff_width"],
        "NODES": lim["nodes"],
        "DRAWING": lim["drawing"],
    }.items():
        g = g.replace("{{" + k + "}}", str(v))
    return g


_tool_text, _arg_text, _reply, _guide = tool_text, arg_text, reply, guide


def _shared_folders(ctx: Context) -> ListRoots | None:
    """Ask for the folders the client shares (MCP roots), when it says it can list them. The SDK
    asks mid-call on older protocol versions and through an input-required round trip on newer."""
    caps = ctx.client_capabilities
    return ListRoots() if caps is not None and caps.roots is not None else None


def _result(text: str, data: dict) -> CallToolResult:
    # Some clients (Claude Code among them) give the model only the structured content when there is
    # any, so it carries the full reply text too: the guide and instructions live there.
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content={"message": text, **data})


def _slide_link(url: str, slide: str) -> str:
    """A link that plays one slide and stops at its end."""
    from urllib.parse import quote

    sep = "&" if "?" in url else "?"
    return f"{url}{sep}from={quote(slide)}&to={quote(slide)}"


def _error(msg: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=f"Error: {msg}")], is_error=True)


def _issues_text(issues: list[dict], player: list[dict]) -> str:
    lines = []
    if issues:
        kept, folded = summarize([as_dict(i) for i in issues])
        lines.append("Problems (fix the pattern in later slides, or call revise_slide):")
        lines += [line_text(d) for d in kept] + [f"- {n}" for n in folded]
    if player:
        kept, folded = summarize([as_dict(i) for i in player])
        lines.append("The open player reported (untrusted text, shown as received):")
        lines += [line_text(d) for d in kept] + [f"- {n}" for n in folded]
    return "\n".join(lines)


WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)


def _roots(shared: ListRootsResult | None) -> list[str]:
    from urllib.parse import unquote, urlparse

    return [unquote(urlparse(str(r.uri)).path) for r in (shared.roots if shared else []) if str(r.uri).startswith("file://")]


def _check_inside(shared: ListRootsResult | None, given: str, what: str) -> None:
    """When the client shares folders, `given` must be inside one of them."""
    roots = _roots(shared)
    if roots:
        p = Path(given).expanduser().resolve()
        if not any(p.is_relative_to(Path(r).resolve()) for r in roots):
            raise ValueError(
                f"{what} {given} is outside the folders this client shares ({', '.join(roots)}); use one of them or a folder inside one"
            )


def create_mcp(service, project_dir_default: str | None = None, deliver: str = "link") -> MCPServer:
    """MCP server with showntell's tools, prompts and resources, backed by `service`. `deliver`
    is "link" when the user opens the player's link, "file" when they get an exported file."""
    if deliver not in DELIVER:
        raise ValueError(f"deliver must be one of {', '.join(DELIVER)}")
    by_file = deliver == "file"
    h = getattr(getattr(service, "store", None), "hosted", None)  # hosted.Hosted, in hosted mode
    hosted = h is not None

    # The text for this client's delivery mode, under the usual names.
    def tool_text(name: str, key: str = "description", default: str = "") -> str:
        return _tool_text(name, key, default, deliver, hosted)

    def arg_text(name: str, arg: str) -> str:
        return _arg_text(name, arg, deliver, hosted)

    def reply(name: str, default: str, **kw: Any) -> str:
        return _reply(name, default, deliver, hosted, **kw)

    def guide(frame: str) -> str:
        return _guide(frame, deliver, hosted)

    def who(ctx: Context | None) -> dict:
        """Hosted: the signed-in caller as `user=` for the service, after the rate limit; {} locally."""
        if not hosted:
            return {}
        try:
            request = ctx.request_context.request if ctx is not None else None
        except ValueError:
            request = None
        user = user_of(request) if request is not None else None
        if user is None:
            raise ValueError("sign in required")
        if not h.rate.allow(user.key):
            raise ValueError(reply("rate_limit", "Over {n} tool calls a minute; wait a moment.", n=h.limits.calls_per_min))
        return {"user": user}

    mcp = MCPServer(
        "showntell",
        instructions=texts(deliver, hosted)["instructions"],
        version=__version__,
        website_url="https://github.com/rahimnathwani/showntell",
    )

    def _root(shared: ListRootsResult | None, given: str) -> str | None:
        """The project folder: `project_dir`, else the client's first root, else the default. When
        the client shares folders, `project_dir` must be inside one, so an agent cannot put files
        from elsewhere on the machine (keys, other projects) on a slide. Hosted: the client's
        folders are on another machine, so only `project_dir` passes, for the service to refuse."""
        if hosted:
            return given or None
        if not given:
            roots = _roots(shared)
            return roots[0] if roots else project_dir_default
        _check_inside(shared, given, "project_dir")
        return given

    async def _append(handle: str, markdown: str, ctx: Context | None, u: dict | None = None) -> CallToolResult:
        """Add Markdown; until the link is shared, wait for the opening audio and return the link."""
        try:
            u = who(ctx) if u is None else u
            r = await service.add(handle, markdown, **u)
        except ValueError as e:
            return _error(str(e))
        parts: list[str] = []
        status = "added"
        if not r["link_shared"]:
            t0 = time.time()
            p = None
            while time.time() - t0 < FIRST_WAIT_S:
                p = await service.playable(handle, 2.0, **u)
                if p["status"] != "pending":
                    break
                if ctx is not None:
                    try:
                        await ctx.report_progress(time.time() - t0, FIRST_WAIT_S, message=p["speech"]["describe"])
                    except Exception:
                        pass
            assert p is not None
            # Voiced while this call waited: report the counts as they are now. (An older daemon
            # behind the stdio shim leaves them out.)
            r.update({k: p[k] for k in ("voiced", "total_says", "narration_s") if k in p})
            if p["status"] == "ready":
                status = "playable"
                parts.append(
                    reply(
                        "playable",
                        "PLAYABLE NOW ({seconds}s). Send the user this link in a message before your next tool call, then keep adding slides:\n{url}",
                        seconds=f"{time.time() - t0:.1f}",
                        url=r["url"],
                    )
                )
            elif p["status"] == "error":
                status = "error"
                parts.append(
                    reply("voice_error", "The narration could not be voiced: {error}. Link: {url}", error=p.get("error", ""), url=r["url"])
                )
            elif p["status"] == "empty":
                status = "waiting"
                parts.append(reply("need_opening", "Nothing to play yet: send the title slide with a spoken sentence."))
            else:
                status = "pending"
                await service.playable(handle, 0, give_up=True, **u)  # later calls return at once
                parts.append(reply("pending", "Audio is still being made; share this link anyway: {url}", url=r["url"]))
            if p.get("guide"):
                g = guide(p.get("frame", r["frame"]))
                if g:
                    parts.append("\n" + g)
        else:
            p = await service.playable(handle, 0, **u)  # the guide, if the first reply went out before the audio
            if p.get("guide"):
                g = guide(p.get("frame", r["frame"]))
                if g:
                    parts.append(g)
        parts.append(
            f"Added {r['slides']} slide(s) and {r['says']} narration paragraph(s). Totals: {r['total_slides']} slides, "
            f"{r['voiced']}/{r['total_says']} paragraphs voiced, about {mmss(r['narration_s'])} of narration. "
            f"Slide ids: {', '.join(r['slide_ids'][-12:])}."
        )
        issues = _issues_text(r["issues"], r["player_issues"])
        if issues:
            parts.append(issues)
        data = {"url": r["url"], "status": status, "issues": r["issues"], "player_issues": r["player_issues"], "slides": r["total_slides"]}
        return _result("\n".join(parts), data)

    @mcp.tool(
        title=tool_text("start_walkthrough", "title", "Start walkthrough"), description=tool_text("start_walkthrough"), annotations=WRITE
    )
    async def start_walkthrough(
        title: Annotated[str, Field(description=arg_text("start_walkthrough", "title"))],
        description: Annotated[str, Field(description=arg_text("start_walkthrough", "description"))] = "",
        frame: Annotated[
            Literal["landscape", "square", "portrait"], Field(description=arg_text("start_walkthrough", "frame"))
        ] = "landscape",
        voice: Annotated[str, Field(description=arg_text("start_walkthrough", "voice") + " " + ", ".join(VOICES) + ".")] = "",
        speed: Annotated[float, Field(description=arg_text("start_walkthrough", "speed"))] = 1.0,
        project_dir: Annotated[str, Field(description=arg_text("start_walkthrough", "project_dir"))] = "",
        opening: Annotated[str, Field(description=arg_text("start_walkthrough", "opening"))] = "",
        theme: Annotated[
            Literal["", "dark", "light", "paper", "contrast", "night", "ocean", "studio"],
            Field(description=arg_text("start_walkthrough", "theme")),
        ] = "",
        shared: Annotated[ListRootsResult | None, Resolve(_shared_folders)] = None,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> CallToolResult:
        try:
            u = who(ctx)
            r = await service.start(title, frame, voice, speed, _root(shared, project_dir), theme, description, **u)
        except ValueError as e:
            return _error(str(e))
        head = reply(
            "started",
            "Handle: {handle}\nLink (not playable yet): {url}\nNext: call add_slides now with only the title slide and one or two spoken sentences.",
            handle=r["handle"],
            url=r["url"],
        )
        # Which folder file= and rev= read from, so a wrong guess shows before the first source_error.
        project = (
            reply("project", "Project folder: {root}", root=r["root"])
            if r.get("root")
            else reply("no_project", "No project folder: pass project_dir to show files from disk.")
        )
        if opening.strip():
            # The opening came with the call: voice it now and return the link, saving a round trip.
            res = await _append(r["handle"], opening, ctx, u)
            text = f"Handle: {r['handle']}\n{project}\n" + res.content[0].text
            data = {**(res.structured_content or {}), "message": text, "handle": r["handle"]}
            return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=data, is_error=res.is_error)
        text = head + "\n" + project
        if not r["speech"]["ready"]:
            text += "\n" + reply(
                "loading", "The voice model is {status}; the first add_slides call may take longer.", status=r["speech"]["describe"]
            )
        return _result(text, {"handle": r["handle"], "url": r["url"], "status": "started", "issues": []})

    @mcp.tool(title=tool_text("add_slides", "title", "Add slides"), description=tool_text("add_slides"), annotations=WRITE)
    async def add_slides(
        handle: Annotated[str, Field(description=arg_text("add_slides", "handle"))],
        markdown: Annotated[str, Field(description=arg_text("add_slides", "markdown"))],
        ctx: Context = None,  # type: ignore[assignment]
    ) -> CallToolResult:
        return await _append(handle, markdown, ctx)

    @mcp.tool(title=tool_text("revise_slide", "title", "Revise slide"), description=tool_text("revise_slide"), annotations=WRITE)
    async def revise_slide(
        handle: Annotated[str, Field(description=arg_text("revise_slide", "handle"))],
        slide: Annotated[str, Field(description=arg_text("revise_slide", "slide"))],
        markdown: Annotated[str, Field(description=arg_text("revise_slide", "markdown"))],
        ctx: Context = None,  # type: ignore[assignment]
    ) -> CallToolResult:
        try:
            r = await service.revise(handle, slide, markdown, **who(ctx))
        except ValueError as e:
            return _error(str(e))
        text = f"Replaced slide `{r['slide']}` (revision {r['rev']}); {r['revoiced']} paragraph(s) need new audio."
        if r.get("staged"):
            text += " The open player keeps showing the old version until the new narration is voiced, then switches."
        elif r["revoiced"]:
            text += " The player shows the new version now and fills in its audio as it is voiced."
        if not by_file:
            text += f"\nWatch only this slide: {_slide_link(r['url'], r['slide'])}"
        issues = _issues_text(r["issues"], r["player_issues"])
        return _result(
            text + ("\n" + issues if issues else ""),
            {
                "url": r["url"],
                "status": "revised",
                "issues": r["issues"],
                "player_issues": r["player_issues"],
                "slide": r["slide"],
                "staged": bool(r.get("staged")),
                "slide_url": _slide_link(r["url"], r["slide"]),
            },
        )

    @mcp.tool(
        title=tool_text("finish_walkthrough", "title", "Finish walkthrough"), description=tool_text("finish_walkthrough"), annotations=WRITE
    )
    async def finish_walkthrough(
        handle: Annotated[str, Field(description=arg_text("finish_walkthrough", "handle"))],
        questions: Annotated[list[str] | None, Field(description=arg_text("finish_walkthrough", "questions"))] = None,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> CallToolResult:
        try:
            r = await service.finish(handle, questions, **who(ctx))
        except ValueError as e:
            return _error(str(e))
        # Slides with errors or warnings still standing: the reply asks for fixes before the
        # agent reports done, since a list of findings after "tell the user" gets skipped.
        fix = list(
            dict.fromkeys(
                d["slide"] for d in r["issues"] + r["player_issues"] if d.get("slide") and d.get("severity") in ("error", "warning")
            )
        )
        text = reply(
            "finished_fix" if fix else "finished",
            "Finished: {url} ({slides} slides, {duration}). Fix slides {fix} with revise_slide, then tell the user it is done, with the link."
            if fix
            else "Finished: {url} ({slides} slides, {duration}). Tell the user it is done, with the link.",
            url=r["url"],
            slides=r["slides"],
            duration=mmss(r["duration_s"]),
            fix=", ".join(f"`{x}`" for x in fix),
        )
        if r["pending"]:
            text += f"\n{r['pending']} paragraph(s) are still being voiced; the player fills them in as they finish."
        if r.get("outline"):
            text += "\nSlides (id, start time, look):\n" + "\n".join(
                f"- {o['id']} {mmss(o['start_s'])}" + (f" {o['look']}" if o.get("look") else "") for o in r["outline"]
            )
        for run in r.get("alike", []):
            text += "\n" + reply(
                "alike",
                "Slides {ids} in a row look alike ({look}). Before you tell the user, give one a different look with revise_slide.",
                ids=", ".join(f"`{x}`" for x in run["slides"]),
                look=run["look"],
            )
        issues = _issues_text(r["issues"], r["player_issues"])
        text += "\n" + issues if issues else ""
        data = {
            "url": r["url"],
            "status": "finished",
            "issues": r["issues"],
            "player_issues": r["player_issues"],
            "slides": r["slides"],
            "outline": r.get("outline", []),
            "alike": r.get("alike", []),
        }
        if not r.get("thumbs"):
            return _result(text, data)
        # Pictures of the alike slides; no structured content, which some clients prefer over pictures.
        content: list = [TextContent(type="text", text=text)]
        for th in r["thumbs"]:
            content.append(TextContent(type="text", text=f"Slide `{th['id']}`:"))
            content.append(ImageContent(type="image", data=th["png"], mime_type="image/png"))
        return CallToolResult(content=content)

    @mcp.tool(
        title=tool_text("export_walkthrough", "title", "Export walkthrough"), description=tool_text("export_walkthrough"), annotations=WRITE
    )
    async def export_walkthrough(
        handle: Annotated[str, Field(description=arg_text("export_walkthrough", "handle"))],
        format: Annotated[Literal["mp4", "html"], Field(description=arg_text("export_walkthrough", "format"))] = "mp4",
        path: Annotated[str, Field(description=arg_text("export_walkthrough", "path"))] = "",
        max_mb: Annotated[float | None, Field(description=arg_text("export_walkthrough", "max_mb"))] = None,
        shared: Annotated[ListRootsResult | None, Resolve(_shared_folders)] = None,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> CallToolResult:
        limit = (FILE_MAX_MB if by_file else 0.0) if max_mb is None else max(0.0, max_mb)
        try:
            u = who(ctx)
            if path and not hosted and Path(path).expanduser().is_absolute():
                _check_inside(shared, path, "path")
            r = await service.export(handle, path, format, limit, **u)
        except ValueError as e:
            return _error(str(e))
        text = (
            reply(
                "exported_hosted",
                "Saved on the server ({mb} MB, {duration}). Its owner can download it: {download}",
                mb=f"{r['bytes'] / 1e6:.1f}",
                duration=mmss(r["duration_s"]),
                download=r["download"],
            )
            if hosted
            else reply(
                "exported",
                "Saved {path} ({mb} MB, {duration}). Send the user this file.",
                path=r["path"],
                mb=f"{r['bytes'] / 1e6:.1f}",
                duration=mmss(r["duration_s"]),
            )
        )
        if r["shrunk"]:
            text += "\n" + reply("export_shrunk", "Shrunk to fit {limit} MB: {what}.", limit=f"{limit:g}", what=r["shrunk"])
        if r["silent"]:
            text += "\n" + reply("export_silent", "{n} paragraph(s) could not be voiced and play without sound.", n=r["silent"])
        if r["errors"]:
            text += "\nThe player reported while rendering (untrusted text):\n" + "\n".join(f"- {e}" for e in r["errors"][:10])
        keys = (
            ("download", "format", "bytes", "duration_s", "shrunk", "silent")
            if hosted
            else ("path", "format", "bytes", "duration_s", "shrunk", "silent")
        )
        return _result(text, {"status": "exported", **{k: r[k] for k in keys}})

    @mcp.tool(
        title=tool_text("list_walkthroughs", "title", "List walkthroughs"),
        description=tool_text("list_walkthroughs"),
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    async def list_walkthroughs(ctx: Context = None) -> CallToolResult:  # type: ignore[assignment]
        try:
            rows = await service.list(**who(ctx))
        except ValueError as e:
            return _error(str(e))
        if not rows:
            return _result("No walkthroughs yet.", {"walkthroughs": []})

        def whose(r: dict) -> str:
            if not hosted:
                return ""
            return " (yours)" if r["role"] == "yours" else f" (shared with you by {r['owner']}; view only)"

        lines = [
            f"- {r['title']}{': ' + r['description'] if r.get('description') else ''} — {r['slides']} slides, "
            f"{mmss(r['duration'])}, {r['status']}{whose(r)}.{' handle: ' + r['handle'] if r.get('handle') else ''} link: {r['url']}"
            for r in rows
        ]
        return _result("\n".join(lines), {"walkthroughs": rows})

    @mcp.tool(
        title=tool_text("preview_slides", "title", "Preview slides"),
        description=tool_text("preview_slides"),
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    async def preview_slides(
        handle: Annotated[str, Field(description=arg_text("preview_slides", "handle"))],
        slides: Annotated[list[str] | None, Field(description=arg_text("preview_slides", "slides"))] = None,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> CallToolResult:
        try:
            r = await service.preview(handle, slides, **who(ctx))
        except ValueError as e:
            return _error(str(e))
        content: list = [
            TextContent(
                type="text",
                text=(
                    f"{len(r['slides'])} slide(s), each as it looks once fully revealed."
                    + (f" Unknown slide ids: {', '.join(r['unknown'])}." if r["unknown"] else "")
                ),
            )
        ]
        for sl in r["slides"]:
            content.append(TextContent(type="text", text=f"Slide `{sl['id']}` ({sl['title']}), at {mmss(sl['at_s'])}:"))
            content.append(ImageContent(type="image", data=sl["png"], mime_type="image/png"))
        issues = _issues_text([], r["player_issues"])
        if issues:
            content.append(TextContent(type="text", text=issues))
        # No structured content here: clients that prefer it would drop the pictures.
        return CallToolResult(content=content)

    if hosted:

        @mcp.tool(
            title=tool_text("share_walkthrough", "title", "Share walkthrough"),
            description=tool_text("share_walkthrough"),
            annotations=WRITE,
        )
        async def share_walkthrough(
            handle: Annotated[str, Field(description=arg_text("share_walkthrough", "handle"))],
            add: Annotated[list[str] | None, Field(description=arg_text("share_walkthrough", "add"))] = None,
            remove: Annotated[list[str] | None, Field(description=arg_text("share_walkthrough", "remove"))] = None,
            company: Annotated[bool | None, Field(description=arg_text("share_walkthrough", "company"))] = None,
            ctx: Context = None,  # type: ignore[assignment]
        ) -> CallToolResult:
            try:
                r = await service.share(handle, add, remove, company, **who(ctx))
            except ValueError as e:
                return _error(str(e))
            people = ", ".join(r["shares"]) or reply("shared_nobody", "nobody")
            text = reply(
                "shared",
                "Shared with: {people}. Whole company: {company}. Link: {url}",
                people=people,
                company="yes" if r["company"] else "no",
                url=r["url"],
            )
            if r.get("unknown"):
                text += "\n" + reply(
                    "share_unknown",
                    "Nobody has signed in here as {emails} yet: added anyway.",
                    emails=", ".join(r["unknown"]),
                )
            return _result(text, {"status": "shared", **r})

    for name, p in texts(deliver, hosted)["prompts"].items():
        _register_prompt(mcp, name, p)

    if not hosted:  # a static resource gets no caller, so a hosted server lists through list_walkthroughs

        @mcp.resource(
            "showntell://walkthroughs",
            name="walkthroughs",
            title="Recent walkthroughs",
            description="Recent walkthroughs with titles, links and handles",
            mime_type="application/json",
        )
        async def walkthroughs_resource() -> str:
            return json.dumps(await service.list(), indent=1)

    @mcp.resource(
        "showntell://slide-kinds",
        name="slide-kinds",
        title="Slide kinds",
        description="Every kind of visual a slide can show: how to write it, what cues point at, how it reveals, with an example",
        mime_type="application/json",
    )
    async def kinds_resource() -> str:
        return (TEXT / "kinds.json").read_text()

    @mcp.resource(
        "showntell://walkthroughs/{wid}",
        name="walkthrough",
        title="Walkthrough source",
        description="The Markdown a walkthrough was written in, to read or revise",
        mime_type="text/markdown",
    )
    async def walkthrough_resource(wid: str, ctx: Context = None) -> str:  # type: ignore[assignment]
        try:
            return await service.source(wid, **who(ctx))
        except ValueError:
            # Anything but ResourceError reaches the client as "Error creating resource from template".
            raise ResourceNotFoundError(
                reply(
                    "no_walkthrough",
                    "No walkthrough {wid}. list_walkthroughs lists yours; the id is the part of the handle before the dot.",
                    wid=wid,
                )
            ) from None

    return mcp


def _register_prompt(mcp: MCPServer, name: str, p: dict) -> None:
    (arg,) = list(p.get("args", {"target": ""}).keys())[:1] or ["target"]
    template = p.get("template", "{" + arg + "}")

    async def fn(**kw: str) -> str:
        return template.format(**{arg: kw.get(arg, "")})

    import inspect

    fn.__signature__ = inspect.Signature(
        [inspect.Parameter(arg, inspect.Parameter.KEYWORD_ONLY, annotation=str, default=inspect.Parameter.empty)]
    )
    fn.__annotations__ = {arg: str, "return": str}
    fn.__name__ = f"prompt_{name}"
    mcp.prompt(name=name, title=p.get("title"), description=p.get("description"))(fn)


# -- web ----------------------------------------------------------------------------------


def add_routes(mcp: MCPServer, service: LocalService, rpc_token: str, started: float, stop=None) -> None:
    store = service.store
    hosted = store.hosted is not None

    def session_or_404(request: Request):
        """The walkthrough for a viewer's request: in hosted mode only for its owner and those it
        is shared with, so anyone else gets the same 404 as for an id that does not exist."""
        return store.viewable(request.path_params["wid"], user_of(request))

    def seen(s) -> None:
        if hosted:  # retention counts from the last view
            s.w.viewed = time.time()
            s._schedule_save()

    @mcp.custom_route("/", methods=["GET"])
    async def index(request: Request) -> Response:
        user = user_of(request)
        signout = hosted and store.hosted.identity.login_url("/") is not None  # provider sign-in, not the dev login
        page = render_index(
            store.list(200, user), store.link_suffix, csrf=request.scope.get("showntell.csrf", ""), hosted=hosted, signout=signout
        )
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/w/{wid}", methods=["GET"])
    async def player_redirect(request: Request) -> Response:
        q = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/w/{request.path_params['wid']}/{q}", status_code=308)

    @mcp.custom_route("/w/{wid}/", methods=["GET"])
    async def player(request: Request) -> Response:
        s = session_or_404(request)
        if not s:
            return HTMLResponse("Walkthrough not found", status_code=404)
        seen(s)
        return HTMLResponse(
            render_player(
                s.w.title,
                s.w.frame,
                s.w.report_key,
                "../../static",
                live=True,
                theme=s.w.theme,
                tokens=s.w.tokens,
                description=s.w.description,
                poster=f"{s.url}/poster.png" if (store.walk_dir(s.w.id) / "poster.png").is_file() else "",
                csrf=request.scope.get("showntell.csrf", ""),
                share=hosted and access.owns(s.w, user_of(request)),
            ),
            headers={"Cache-Control": "no-store"},
        )

    @mcp.custom_route("/w/{wid}/data.json", methods=["GET"])
    async def data(request: Request) -> Response:
        s = session_or_404(request)
        if not s:
            return JSONResponse({"error": "not found"}, status_code=404)
        seen(s)
        return JSONResponse(s.w.public(), headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/w/{wid}/events", methods=["GET"])
    async def events(request: Request) -> Response:
        s = session_or_404(request)
        if not s:
            return JSONResponse({"error": "not found"}, status_code=404)
        wid, user = s.w.id, user_of(request)

        def allowed() -> bool:
            # Hosted: access is checked again on every event, so a removed share or a deleted
            # walkthrough ends the stream.
            return not hosted or store.viewable(wid, user) is s

        async def stream():
            q = s.subscribe()
            try:
                yield f"data: {json.dumps({'type': 'full', 'walkthrough': s.w.public()})}\n\n"
                while True:
                    try:
                        item = await asyncio.wait_for(q.get(), timeout=15)
                    except TimeoutError:
                        if not allowed():
                            return
                        yield ": keepalive\n\n"
                        continue
                    if not allowed():
                        return
                    await asyncio.sleep(0.05)  # coalesce bursts
                    ids, meta = set(item["slides"]), item["meta"]
                    while not q.empty():
                        more = q.get_nowait()
                        ids |= more["slides"]
                        meta = meta or more["meta"]
                    yield f"data: {json.dumps(s.delta(ids, meta))}\n\n"
            finally:
                s.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @mcp.custom_route("/w/{wid}/report", methods=["POST"])
    async def report(request: Request) -> Response:
        s = session_or_404(request)
        if not s:
            return JSONResponse({"error": "not found"}, status_code=404)
        if not secrets.compare_digest(request.headers.get("x-report-key", ""), s.w.report_key):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError
        except Exception:
            return JSONResponse({"error": "bad request"}, status_code=400)
        s.report(body)
        log.warning("player report for %s: %s", s.w.id, str(body.get("message", ""))[:200])
        return JSONResponse({"ok": True})

    @mcp.custom_route("/w/{wid}/audio/{name}", methods=["GET"])
    async def audio(request: Request) -> Response:
        s = session_or_404(request)
        name = request.path_params["name"]
        key = name.removesuffix(".wav")
        hit = next(((sl, x) for sl in s.w.slides for x in sl.says if x.key == key), None) if s else None
        if not hit:
            return Response("Not found", status_code=404)
        if not store.narrator.audio_path(key).is_file():
            # The audio cache was cleared: voice it again; the player gets an update when it is back.
            hit[1].status = "pending"
            s._voice(*hit)
            s._changed({hit[0].id})
            return Response("Being voiced again", status_code=404)
        return _file(store.narrator.cache_dir, f"{key}.wav", immutable=True, private=hosted)

    @mcp.custom_route("/w/{wid}/poster.png", methods=["GET"])
    async def poster(request: Request) -> Response:
        if not session_or_404(request):
            return Response("Not found", status_code=404)
        return _file(store.walk_dir(request.path_params["wid"]), "poster.png")

    @mcp.custom_route("/w/{wid}/assets/{name}", methods=["GET"])
    async def assets(request: Request) -> Response:
        if not session_or_404(request):
            return Response("Not found", status_code=404)
        return _file(store.walk_dir(request.path_params["wid"]) / "assets", request.path_params["name"])

    @mcp.custom_route("/w/{wid}/delete", methods=["POST"])
    async def delete(request: Request) -> Response:
        wid = request.path_params["wid"]
        if hosted and store.owned(wid, user_of(request)) is None:
            return JSONResponse({"ok": False}, status_code=404)
        ok = store.delete(wid)
        return JSONResponse({"ok": ok}, status_code=200 if ok else 404)

    if hosted:

        @mcp.custom_route("/w/{wid}/shares", methods=["GET", "POST"])
        async def shares(request: Request) -> Response:
            """The page's Share control, for the owner only: who it is shared with, and changes to that
            through the same checks as share_walkthrough."""
            s = store.owned(request.path_params["wid"], user_of(request))
            if s is None:
                return JSONResponse({"error": "not found"}, status_code=404)
            if request.method == "POST":
                try:
                    body = await request.json()
                    add, remove, company = body.get("add") or [], body.get("remove") or [], body.get("company")
                    if not all(isinstance(x, str) for x in [*add, *remove]) or company not in (None, True, False):
                        raise ValueError("bad request")
                    unknown = service.share_session(s, add, remove, company)["unknown"]
                except ValueError as e:
                    return JSONResponse({"error": str(e)}, status_code=400)
                except Exception:
                    return JSONResponse({"error": "bad request"}, status_code=400)
            else:
                unknown = []
            domains = store.hosted.allowed_domains
            return JSONResponse(
                {"shares": s.w.shares, "company": s.w.company, "owner": s.w.owner.get("email", ""), "domains": domains, "unknown": unknown}
            )

        @mcp.custom_route("/w/{wid}/exports/{name}", methods=["GET"])
        async def exported(request: Request) -> Response:
            """An MP4 or page made by export_walkthrough, for the owner only."""
            s = store.owned(request.path_params["wid"], user_of(request))
            if s is None:
                return Response("Not found", status_code=404)
            r = _file(store.walk_dir(s.w.id) / "exports", request.path_params["name"])
            r.headers["Cache-Control"] = "private, no-store"
            r.headers["Content-Disposition"] = "attachment"
            return r

    @mcp.custom_route("/static/{path:path}", methods=["GET"])
    async def static(request: Request) -> Response:
        r = _file(STATIC, request.path_params["path"])
        if request.path_params["path"] == "embed.js" and not hosted:
            r.headers["Access-Control-Allow-Origin"] = "*"  # for pages on other sites that embed a player
        return r

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        n = store.narrator
        return JSONResponse(
            {
                "app": "showntell",
                "version": __version__,
                "speech": {
                    "backend": n.backend.name,
                    "fallback": getattr(n.backend, "fell_back", ""),  # why MLX was given up, if it was
                    "state": n.status.state,
                    "detail": n.status.describe(),
                    "queue": n.backlog(),
                    "speed": n.speed_factor(),
                    "stats": n.stats,
                },
                "walkthroughs_open": len(store.sessions),
                "uptime_s": round(time.time() - started, 1),
                "busy": n.backlog() > 0
                or any(s.w.status == "writing" and time.time() - s.w.updated < 120 for s in store.sessions.values()),
            }
        )

    @mcp.custom_route("/api/rpc/{method}", methods=["POST"])
    async def rpc(request: Request) -> Response:
        if hosted:  # the stdio shim's route; HostedGuard refuses it before it gets here
            return JSONResponse({"error": "not found"}, status_code=404)
        auth = request.headers.get("authorization", "")
        if not secrets.compare_digest(auth, f"Bearer {rpc_token}"):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        method = request.path_params["method"]
        if method == "shutdown":
            asyncio.get_running_loop().call_later(0.2, stop or _shutdown)
            return JSONResponse({"result": "bye"})
        if method not in RPC_METHODS:
            return JSONResponse({"error": "unknown method"}, status_code=404)
        try:
            kw = await request.json()
            kw.pop("user", None)  # a caller is never named over RPC
            result = await getattr(service, method)(**kw)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except TypeError as e:
            return JSONResponse({"error": f"bad arguments: {e}"}, status_code=400)
        return JSONResponse({"result": result})


def _shutdown() -> None:
    import os
    import signal

    os.kill(os.getpid(), signal.SIGTERM)


def _file(base: Path, rel: str, immutable: bool = False, private: bool = False) -> Response:
    path = (base / rel).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file():
        return Response("Not found", status_code=404)
    scope = "private" if private else "public"
    headers = {"Cache-Control": f"{scope}, max-age=31536000, immutable"} if immutable else {"Cache-Control": "no-cache"}
    return FileResponse(path, headers=headers)


def render_player(
    title: str,
    frame: str,
    report_key: str,
    static: str,
    live: bool,
    theme: str = "dark",
    tokens: dict | None = None,
    description: str = "",
    poster: str = "",
    csrf: str = "",
    share: bool = False,
) -> str:
    from .themes import THEMES, clean_tokens, style_attr

    page = (STATIC / "player.html").read_text()
    theme = theme if theme in THEMES else "dark"
    style = html.escape(style_attr(clean_tokens(tokens or {})[0]))
    desc = html.escape(" ".join(description.split()))
    meta = f'<meta name="description" content="{desc}">\n<meta property="og:description" content="{desc}">\n' if desc else ""
    if poster:
        meta += f'<meta property="og:image" content="{html.escape(poster)}">\n'
    return (
        page.replace("{{TITLE}}", html.escape(title))
        .replace("{{DESCRIPTION}}\n", meta)
        .replace("{{FRAME}}", frame)
        .replace("{{THEME}}", theme)
        .replace("{{TOKENS}}", style)
        .replace("{{REPORT_KEY}}", report_key if live else "")
        .replace("{{CSRF}}", html.escape(csrf))
        .replace("</head>", SHARE_HEAD + "</head>" if share else "</head>")
        .replace("{{STATIC}}", static)
        .replace("{{LIVE}}", "1" if live else "0")
        .replace("{{VERSION}}", __version__)
    )


# The owner's Share control in hosted mode: a row in the settings menu and its dialog.
SHARE_HEAD = '<link rel="stylesheet" href="{{STATIC}}/share.css">\n<script type="module" src="{{STATIC}}/share.js"></script>\n'


def render_index(rows: list[dict], suffix: str, csrf: str = "", hosted: bool = False, signout: bool = False) -> str:
    """The gallery. Hosted: the viewer's own walkthroughs, then those shared with them, which
    have no Delete button."""
    items = []
    if hosted:
        rows = [r for r in rows if r.get("role") == "yours"] + [r for r in rows if r.get("role") == "shared"]
    for r in rows:
        when = time.strftime("%d %b %Y, %H:%M", time.localtime(r["created"]))
        badge = '<span class="badge">writing</span>' if r["status"] != "finished" else ""
        desc = f'<span class="desc">{html.escape(r["description"])}</span>' if r.get("description") else ""
        thumb = f'<img class="thumb" src="/w/{r["id"]}/poster.png" alt="">' if r.get("poster") else '<span class="thumb"></span>'
        shared = hosted and r.get("role") == "shared"
        by = f" · shared by {html.escape(r.get('owner', ''))}" if shared else ""
        button = "" if shared else f'<button class="del" title="Delete" aria-label="Delete {html.escape(r["title"])}">Delete</button>'
        items.append(
            f'<li data-id="{r["id"]}">{thumb}<a href="/w/{r["id"]}/{suffix}">{html.escape(r["title"] or "Untitled")}</a>{badge}{desc}'
            f'<span class="meta">{when} · {r["slides"]} slides · {mmss(r["duration"])} · {r["frame"]}{by}</span>'
            f"{button}</li>"
        )
    body = "\n".join(items) or '<li class="empty">No walkthroughs yet. Ask your coding agent for one.</li>'
    page = (STATIC / "index.html").read_text().replace("{{ROWS}}", body).replace("{{VERSION}}", __version__)
    # A hosted server voices them itself, and its users have no showntell to run.
    where, hint = (
        ("this server", "Ask your agent for a walkthrough.")
        if hosted
        else ("this machine", "Ask your agent for a walkthrough, or run <code>showntell play file.md</code>.")
    )
    page = page.replace("{{WHERE}}", where).replace("{{HINT}}", hint)
    if signout:
        page = page.replace("</footer>", ' · <button class="signout">Sign out</button></footer>')
    return page.replace("{{CSRF}}", html.escape(csrf))
